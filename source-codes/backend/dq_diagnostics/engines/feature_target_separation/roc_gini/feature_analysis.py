"""Efficient in-sample association and leakage diagnostics for mixed data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from sklearn.metrics import roc_auc_score
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor, export_text

from .models import (
    AnalysisConstraints,
    AnalysisMessage,
    AnalysisResponse,
    BenchmarkResult,
    ConfigurationMetric,
    FeatureResult,
    FeatureSpec,
    LeafSummary,
    LocalizedLeakageCandidate,
    LocalizedLeakageScan,
    MissingTargetAction,
    TargetResult,
    TargetSpec,
)

TREE_CONFIGURATIONS = {
    "conservative": {"max_depth": 3, "max_leaf_nodes": 4, "min_leaf_fraction": 0.020},
    "primary": {"max_depth": 4, "max_leaf_nodes": 8, "min_leaf_fraction": 0.010},
    "permissive": {"max_depth": 5, "max_leaf_nodes": 12, "min_leaf_fraction": 0.005},
}
@dataclass
class _PreparedFeature:
    matrix: pd.DataFrame
    feature_type: str
    population: pd.Series
    regular_rows: int
    missing_rows: int
    special_rows: dict[str, int]
    transformation: str
    numeric: pd.Series | None = None
    scan_values: pd.Series | None = None
    observed_information_states: int = 1
    regular_unique_values: int = 1
    profile_source: str = "analysis_fallback"


@dataclass
class _Fit:
    metric: ConfigurationMetric
    estimator: DecisionTreeClassifier | DecisionTreeRegressor
    score: np.ndarray
    leaf_ids: np.ndarray


def analyze_features(
    data: pd.DataFrame,
    targets: list[TargetSpec],
    features: list[FeatureSpec],
    *,
    missing_target_action: MissingTargetAction = "prompt",
    percentile_bins: int = 10,
    analysis_constraints: AnalysisConstraints | None = None,
    feature_profiles: dict[str, dict[str, Any]] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cached_feature_results: dict[str, dict[str, dict[str, Any]]] | None = None,
    feature_result_callback: Callable[[TargetSpec, FeatureSpec, dict[str, Any]], None]
    | None = None,
) -> AnalysisResponse:
    """Analyze every requested target-feature pair without dropping feature-missing rows."""
    if not targets:
        raise ValueError("Select at least one target column.")
    if not features:
        raise ValueError("Select at least one independent variable.")
    requested = {item.column for item in [*targets, *features]}
    absent = sorted(requested - set(map(str, data.columns)))
    if absent:
        raise ValueError(f"Selected columns are absent from the dataset: {absent}")
    overlap = sorted({item.column for item in targets} & {item.column for item in features})
    if overlap:
        raise ValueError(f"Target columns cannot also be independent variables: {overlap}")
    if not 2 <= percentile_bins <= 100:
        raise ValueError("percentile_bins must be between 2 and 100.")
    constraints = analysis_constraints or AnalysisConstraints()

    target_results = []
    total_pairs = len(targets) * len(features)
    completed_pairs = 0

    def feature_completed(feature_name: str) -> None:
        nonlocal completed_pairs
        completed_pairs += 1
        if progress_callback:
            progress_callback(completed_pairs, total_pairs, feature_name)

    for target in targets:
        target_results.append(
            _analyze_target(
                data,
                target,
                features,
                missing_target_action,
                percentile_bins,
                constraints,
                feature_profiles or {},
                feature_completed,
                (cached_feature_results or {}).get(target.column, {}),
                feature_result_callback,
            )
        )
    pairs = sum(len(item.features) for item in target_results)
    trees_run = sum(
        len(feature.configurations) for target in target_results for feature in target.features
    )
    if any(item.status == "action_required" for item in target_results):
        status = "action_required"
    elif any(item.status == "invalid" for item in target_results):
        status = "invalid"
    else:
        status = "complete"
    return AnalysisResponse(
        status=status,
        methodology={
            "purpose": "in-sample association and potential feature-leakage diagnostic",
            "feature_timing_assumption": (
                "Independent variables are available as of the observation date; "
                "target variables represent future outcomes."
            ),
            "feature_missing_rows_dropped": False,
            "special_values_are_generic_missing": False,
            "risk_thresholds": {
                "medium": constraints.global_medium_auc,
                "high": constraints.global_high_auc,
                "stability_tolerance": constraints.stability_tolerance,
            },
            "tree_configurations": TREE_CONFIGURATIONS,
            "cardinality_adaptation": (
                "Configured tree depth, leaf count, and percentile bins are upper bounds; "
                "low-cardinality features use smaller effective limits."
            ),
            "risk_routing": {
                "low": ["primary"],
                "medium": ["primary", "conservative"],
                "high": ["primary", "conservative", "permissive"],
            },
            "multinomial_routing_metric": "maximum one-vs-rest class AUC",
            "percentile_benchmark_bins": percentile_bins,
            "localized_leakage_policy": {
                "target_rate_threshold": constraints.localized_target_rate_threshold,
                "minimum_rate_increase": constraints.localized_minimum_rate_increase,
                "minimum_target_count": constraints.localized_minimum_target_count,
                "minimum_population_fraction": constraints.localized_minimum_population_fraction,
                "minimum_rows_floor": constraints.localized_minimum_rows_floor,
                "adjusted_p_value_threshold": constraints.localized_adjusted_p_value_threshold,
            },
        },
        targets=target_results,
        total_feature_target_pairs=pairs,
        trees_run=trees_run,
        full_sensitivity_trees=pairs * 3,
        trees_avoided=pairs * 3 - trees_run,
    )


def _analyze_target(
    data: pd.DataFrame,
    target_spec: TargetSpec,
    features: list[FeatureSpec],
    missing_action: MissingTargetAction,
    percentile_bins: int,
    constraints: AnalysisConstraints,
    feature_profiles: dict[str, dict[str, Any]],
    progress_callback: Callable[[str], None] | None = None,
    cached_feature_results: dict[str, dict[str, Any]] | None = None,
    feature_result_callback: Callable[[TargetSpec, FeatureSpec, dict[str, Any]], None]
    | None = None,
) -> TargetResult:
    raw = data[target_spec.column]
    missing_mask = raw.isna()
    missing_count = int(missing_mask.sum())
    if missing_count and missing_action == "prompt":
        return TargetResult(
            target=target_spec.column,
            status="action_required",
            input_rows=len(data),
            missing_target_rows=missing_count,
            messages=[
                _message(
                    "warning",
                    "target_missing_action_required",
                    f"Target '{target_spec.column}' contains {missing_count:,} missing rows.",
                    choices=["drop_rows", "upload_new_file"],
                )
            ],
        )
    working = data.loc[~missing_mask].copy() if missing_count else data.copy()
    try:
        target, target_type, mapping, messages = _prepare_target(
            working[target_spec.column], target_spec
        )
    except (TypeError, ValueError) as exc:
        return TargetResult(
            target=target_spec.column,
            status="invalid",
            input_rows=len(data),
            evaluated_rows=len(working),
            missing_target_rows=missing_count,
            dropped_target_rows=missing_count,
            messages=[_message("error", "invalid_target", str(exc))],
        )

    results = []
    localized_candidates: list[dict[str, Any]] = []
    localized_tests = 0
    for feature_spec in features:
        cached = (cached_feature_results or {}).get(feature_spec.column)
        if cached is not None:
            feature_result = FeatureResult.model_validate(cached["result"])
            if feature_result.feature != feature_spec.column:
                raise ValueError("Cached ROC result does not match the requested feature.")
            results.append(feature_result)
            localized_candidates.extend(cached.get("localized_candidates", []))
            localized_tests += int(cached.get("localized_tests", 0))
            if progress_callback:
                progress_callback(feature_spec.column)
            continue
        prepared = _prepare_feature(
            working[feature_spec.column],
            feature_spec,
            feature_profiles.get(feature_spec.column),
        )
        primary_fit = _fit_configuration(target, target_type, prepared, "primary", constraints)
        fits = [primary_fit]
        if primary_fit.metric.risk_tier in {"medium", "high"}:
            fits.append(
                _fit_configuration(target, target_type, prepared, "conservative", constraints)
            )
        if primary_fit.metric.risk_tier == "high":
            fits.append(
                _fit_configuration(target, target_type, prepared, "permissive", constraints)
            )
        feature_result = _feature_result(
            target,
            target_type,
            feature_spec,
            prepared,
            fits,
            percentile_bins,
            constraints,
        )
        results.append(feature_result)
        candidates, tests_evaluated = _localized_feature_scan(
            target,
            target_type,
            feature_spec.column,
            prepared,
            constraints,
        )
        localized_tests += tests_evaluated
        for candidate in candidates:
            candidate.update(
                primary_auc=feature_result.primary_auc,
                primary_gini=feature_result.primary_gini,
            )
            localized_candidates.append(candidate)
        if feature_result_callback:
            feature_result_callback(
                target_spec,
                feature_spec,
                {
                    "result": feature_result.model_dump(mode="json"),
                    "localized_candidates": candidates,
                    "localized_tests": tests_evaluated,
                },
            )
        if progress_callback:
            progress_callback(feature_spec.column)
    return TargetResult(
        target=target_spec.column,
        status="ready",
        target_type=target_type,
        input_rows=len(data),
        evaluated_rows=len(working),
        missing_target_rows=missing_count,
        dropped_target_rows=missing_count if missing_count else 0,
        target_mapping=mapping,
        messages=(
            [
                _message(
                    "warning",
                    "target_missing_rows_dropped",
                    f"Dropped {missing_count:,} rows with missing target values after "
                    "explicit approval.",
                )
            ]
            + messages
            if missing_count
            else messages
        ),
        features=sorted(results, key=lambda item: item.primary_auc, reverse=True),
        localized_leakage_scan=_finalize_localized_scan(
            localized_candidates,
            localized_tests,
            len(working),
            constraints,
        ),
    )


def _prepare_target(
    raw: pd.Series, spec: TargetSpec
) -> tuple[np.ndarray, str, dict[str, int], list[AnalysisMessage]]:
    unique_count = int(raw.nunique(dropna=True))
    if unique_count < 2:
        raise ValueError("Target must contain at least two distinct values.")
    target_type = spec.target_type
    messages: list[AnalysisMessage] = []
    if target_type == "auto":
        if unique_count == 2:
            target_type = "binary"
        elif pd.api.types.is_numeric_dtype(raw):
            target_type = "continuous"
            messages.append(
                _message(
                    "info",
                    "numeric_target_assumed_continuous",
                    f"Numeric target '{spec.column}' was treated as continuous.",
                    unique_values=unique_count,
                )
            )
        else:
            target_type = "multinomial"
    if target_type == "continuous":
        numeric = pd.to_numeric(raw, errors="coerce")
        if numeric.isna().any():
            raise ValueError(
                "Continuous target contains values that cannot be converted to numeric."
            )
        return numeric.to_numpy(dtype=float), "continuous", {}, messages

    labels = raw.astype("string")
    ordered = (
        [str(value) for value in spec.class_order] if spec.class_order else sorted(labels.unique())
    )
    if set(labels.unique()) - set(ordered):
        raise ValueError("class_order omits one or more observed target classes.")
    if target_type == "binary":
        if len(ordered) != 2:
            raise ValueError("Binary target must contain exactly two classes.")
        positive = str(spec.positive_class) if spec.positive_class is not None else ordered[1]
        if positive not in ordered and spec.positive_class is not None:
            numeric_positive = pd.to_numeric(
                pd.Series([spec.positive_class]), errors="coerce"
            ).iloc[0]
            matches = [
                label
                for label in ordered
                if pd.notna(numeric_positive)
                and pd.to_numeric(pd.Series([label]), errors="coerce").iloc[0] == numeric_positive
            ]
            if len(matches) == 1:
                positive = matches[0]
        if positive not in ordered:
            raise ValueError(f"Positive class '{positive}' is not present in {ordered}.")
        negative = next(label for label in ordered if label != positive)
        mapping = {negative: 0, positive: 1}
        messages.append(
            _message(
                "info",
                "binary_target_encoded",
                f"Binary target encoded as 0/1 with '{positive}' as the positive class.",
                mapping=mapping,
            )
        )
        return labels.map(mapping).to_numpy(dtype=np.int8), "binary", mapping, messages
    mapping = {label: index for index, label in enumerate(ordered)}
    messages.append(
        _message(
            "info",
            "multinomial_target_encoded",
            f"Multinomial target encoded into {len(mapping)} classes for one-vs-rest AUC.",
            mapping=mapping,
        )
    )
    return labels.map(mapping).to_numpy(dtype=np.int32), "multinomial", mapping, messages


def _prepare_feature(
    raw: pd.Series,
    spec: FeatureSpec,
    retained_profile: dict[str, Any] | None = None,
) -> _PreparedFeature:
    observed_levels = (
        int(retained_profile["n_levels"])
        if retained_profile and retained_profile.get("n_levels") is not None
        else int(raw.nunique(dropna=True))
    )
    information_states = max(1, observed_levels + int(raw.isna().any()))
    profile_source = "initial_load_profile" if retained_profile else "analysis_fallback"
    feature_type = spec.feature_type
    if feature_type == "auto":
        feature_type = "numeric" if pd.api.types.is_numeric_dtype(raw) else "categorical"
    if feature_type == "categorical":
        values = raw.astype("string")
        missing = values.isna()
        values = values.fillna("__MISSING__")
        matrix = pd.get_dummies(values, prefix=spec.column, dtype=np.int8)
        population = pd.Series("regular", index=raw.index, dtype="string")
        population.loc[missing] = "generic_missing"
        return _PreparedFeature(
            matrix=matrix,
            feature_type="categorical",
            population=population,
            regular_rows=int((~missing).sum()),
            missing_rows=int(missing.sum()),
            special_rows={},
            transformation=(
                "one-hot encoded every observed category; generic missing retained as a category"
            ),
            scan_values=values,
            observed_information_states=information_states,
            regular_unique_values=max(1, observed_levels),
            profile_source=profile_source,
        )

    numeric = pd.to_numeric(raw, errors="coerce").astype(float)
    missing = numeric.isna()
    special_masks: dict[str, pd.Series] = {}
    for raw_special in spec.special_values:
        try:
            numeric_special = float(raw_special)
        except (TypeError, ValueError):
            continue
        mask = numeric.eq(numeric_special)
        if mask.any():
            special_masks[str(raw_special)] = mask
    any_special = pd.Series(False, index=raw.index)
    for mask in special_masks.values():
        any_special |= mask
    regular = ~missing & ~any_special
    fill = float(numeric.loc[regular].median()) if regular.any() else 0.0
    matrix = pd.DataFrame({f"{spec.column}__regular_value": numeric.mask(~regular, fill)})
    matrix[f"{spec.column}__generic_missing"] = missing.astype(np.int8)
    population = pd.Series("regular", index=raw.index, dtype="string")
    population.loc[missing] = "generic_missing"
    for label, mask in special_masks.items():
        matrix[f"{spec.column}__special_{label}"] = mask.astype(np.int8)
        population.loc[mask] = f"special:{label}"
    return _PreparedFeature(
        matrix=matrix,
        feature_type="numeric",
        population=population,
        regular_rows=int(regular.sum()),
        missing_rows=int(missing.sum()),
        special_rows={label: int(mask.sum()) for label, mask in special_masks.items()},
        transformation=(
            "regular numeric value plus separate generic-missing and observed "
            "special-value indicators; "
            "no independent-variable rows dropped"
        ),
        numeric=numeric,
        scan_values=numeric,
        observed_information_states=information_states,
        regular_unique_values=max(1, observed_levels - len(special_masks)),
        profile_source=profile_source,
    )


def _localized_outcomes(
    target: np.ndarray,
    target_type: str,
) -> list[tuple[str, str, np.ndarray]]:
    if target_type == "binary":
        return [("1", "direct_target_link", target == 1)]
    if target_type == "multinomial":
        return [
            (str(class_code), "class_concentration", target == class_code)
            for class_code in np.unique(target)
        ]
    lower = float(np.quantile(target, 0.10))
    upper = float(np.quantile(target, 0.90))
    return [
        ("lower target decile", "target_tail", target <= lower),
        ("upper target decile", "target_tail", target >= upper),
    ]


def _localized_feature_scan(
    target: np.ndarray,
    target_type: str,
    feature_name: str,
    feature: _PreparedFeature,
    constraints: AnalysisConstraints,
) -> tuple[list[dict[str, Any]], int]:
    """Find concentrated target outcomes that can be hidden by population-wide AUC."""
    assert feature.scan_values is not None
    total_rows = len(target)
    minimum_rows = max(
        constraints.localized_minimum_rows_floor,
        int(np.ceil(constraints.localized_minimum_population_fraction * total_rows)),
    )
    candidates: list[dict[str, Any]] = []
    tests_evaluated = 0

    for linked_outcome, signal_type, outcome_mask in _localized_outcomes(target, target_type):
        outcome = outcome_mask.astype(np.int8)
        total_target = int(outcome.sum())
        baseline_rate = total_target / total_rows

        def evaluate(
            rule: str,
            family: str,
            rows: int,
            target_count: int,
            baseline_rate: float = baseline_rate,
            total_target: int = total_target,
            linked_outcome: str = linked_outcome,
            signal_type: str = signal_type,
        ) -> None:
            nonlocal tests_evaluated
            if rows < minimum_rows or rows >= total_rows:
                return
            tests_evaluated += 1
            target_rate = target_count / rows
            if (
                target_count < constraints.localized_minimum_target_count
                or target_rate < constraints.localized_target_rate_threshold
                or target_rate - baseline_rate
                < constraints.localized_minimum_rate_increase
            ):
                return
            other_target = total_target - target_count
            other_rows = total_rows - rows
            p_value = float(
                fisher_exact(
                    [
                        [target_count, rows - target_count],
                        [other_target, other_rows - other_target],
                    ],
                    alternative="greater",
                ).pvalue
            )
            candidates.append(
                {
                    "feature": feature_name,
                    "rule": rule,
                    "rule_family": family,
                    "linked_outcome": linked_outcome,
                    "signal_type": signal_type,
                    "rows": rows,
                    "population_share": rows / total_rows,
                    "target_count": target_count,
                    "target_rate": target_rate,
                    "baseline_rate": baseline_rate,
                    "lift": target_rate / baseline_rate if baseline_rate else float("inf"),
                    "target_capture": target_count / total_target if total_target else 0.0,
                    "p_value": p_value,
                }
            )

        values = feature.scan_values
        if feature.feature_type == "categorical":
            grouped = pd.DataFrame({"value": values.to_numpy(), "target": outcome}).groupby(
                "value", dropna=False
            )["target"]
            for value, group in grouped:
                label = str(value)
                rule = (
                    f"{feature_name} is missing"
                    if label == "__MISSING__"
                    else f"{feature_name} = {label}"
                )
                evaluate(rule, f"category:{label}", len(group), int(group.sum()))
            continue

        population_groups = feature.population.groupby(feature.population).groups
        for population_value, indexes in population_groups.items():
            if population_value == "regular":
                continue
            row_indexes = feature.population.index.get_indexer(indexes)
            label = str(population_value)
            rule = (
                f"{feature_name} is missing"
                if label == "generic_missing"
                else f"{feature_name} = special value {label.removeprefix('special:')}"
            )
            evaluate(rule, label, len(row_indexes), int(outcome[row_indexes].sum()))

        regular = feature.population.eq("regular").to_numpy()
        grouped = (
            pd.DataFrame(
                {
                    "value": values.to_numpy()[regular],
                    "target": outcome[regular],
                }
            )
            .groupby("value", as_index=False)["target"]
            .agg(rows="size", target_count="sum")
            .sort_values("value")
        )
        if grouped.empty:
            continue
        grouped["left_rows"] = grouped["rows"].cumsum()
        grouped["left_target"] = grouped["target_count"].cumsum()
        regular_rows = int(grouped["rows"].sum())
        regular_target = int(grouped["target_count"].sum())
        for item in grouped.iloc[:-1].itertuples(index=False):
            threshold = f"{float(item.value):.6g}"
            left_rows = int(item.left_rows)
            left_target = int(item.left_target)
            evaluate(f"{feature_name} ≤ {threshold}", "lower_tail", left_rows, left_target)
            evaluate(
                f"{feature_name} > {threshold}",
                "upper_tail",
                regular_rows - left_rows,
                regular_target - left_target,
            )
        if len(grouped) <= 20:
            for item in grouped.itertuples(index=False):
                value = f"{float(item.value):.6g}"
                evaluate(
                    f"{feature_name} = {value}",
                    f"exact:{value}",
                    int(item.rows),
                    int(item.target_count),
                )
    return candidates, tests_evaluated


def _finalize_localized_scan(
    candidates: list[dict[str, Any]],
    tests_evaluated: int,
    rows: int,
    constraints: AnalysisConstraints,
) -> LocalizedLeakageScan:
    minimum_rows = max(
        constraints.localized_minimum_rows_floor,
        int(np.ceil(constraints.localized_minimum_population_fraction * rows)),
    )
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        adjusted = min(1.0, candidate["p_value"] * max(tests_evaluated, 1))
        if adjusted <= constraints.localized_adjusted_p_value_threshold:
            accepted.append({**candidate, "adjusted_p_value": adjusted})

    accepted.sort(
        key=lambda item: (
            item["feature"],
            item["linked_outcome"],
            -item["target_capture"],
            item["adjusted_p_value"],
        )
    )
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in accepted:
        best.setdefault((candidate["feature"], candidate["linked_outcome"]), candidate)
    final = sorted(
        best.values(),
        key=lambda item: (-item["target_capture"], -item["target_rate"], item["feature"]),
    )
    public_candidates = []
    for candidate in final:
        public = {
            key: value
            for key, value in candidate.items()
            if key not in {"p_value", "rule_family"}
        }
        public_candidates.append(LocalizedLeakageCandidate(**public))
    description = (
        "Rare categories, missing or special states, and numeric tails were scanned for "
        "a statistically supported target concentration meeting the configured threshold."
    )
    return LocalizedLeakageScan(
        status="candidates_found" if public_candidates else "no_candidates",
        description=description,
        candidates=public_candidates,
        tests_evaluated=tests_evaluated,
        minimum_rows=minimum_rows,
        minimum_target_count=constraints.localized_minimum_target_count,
        target_rate_threshold=constraints.localized_target_rate_threshold,
        adjusted_p_value_threshold=constraints.localized_adjusted_p_value_threshold,
    )


def _fit_configuration(
    target: np.ndarray,
    target_type: str,
    feature: _PreparedFeature,
    name: str,
    constraints: AnalysisConstraints,
) -> _Fit:
    policy = TREE_CONFIGURATIONS[name]
    effective_maximum_leaves = max(
        2, min(policy["max_leaf_nodes"], feature.observed_information_states)
    )
    effective_maximum_depth = max(
        1, min(policy["max_depth"], feature.observed_information_states - 1)
    )
    minimum_leaf = max(50, int(np.ceil(policy["min_leaf_fraction"] * len(target))))
    parameters = {
        "max_depth": effective_maximum_depth,
        "max_leaf_nodes": effective_maximum_leaves,
        "min_samples_leaf": minimum_leaf,
        "splitter": "best",
        "random_state": 42,
    }
    if target_type == "continuous":
        estimator = DecisionTreeRegressor(criterion="squared_error", **parameters)
        estimator.fit(feature.matrix, target)
        score = estimator.predict(feature.matrix)
        auc = _continuous_concordance_auc(target, score)
        class_auc: dict[str, float] = {}
        macro_auc = weighted_auc = max(auc, 1.0 - auc)
        risk_auc = macro_auc
    else:
        estimator = DecisionTreeClassifier(criterion="gini", class_weight=None, **parameters)
        estimator.fit(feature.matrix, target)
        probabilities = estimator.predict_proba(feature.matrix)
        class_auc = {}
        class_counts = pd.Series(target).value_counts().to_dict()
        for index, class_code in enumerate(estimator.classes_):
            indicator = (target == class_code).astype(np.int8)
            raw_auc = float(roc_auc_score(indicator, probabilities[:, index]))
            class_auc[str(class_code)] = max(raw_auc, 1.0 - raw_auc)
        if target_type == "binary":
            risk_auc = macro_auc = weighted_auc = class_auc["1"]
            score = probabilities[:, list(estimator.classes_).index(1)]
        else:
            risk_auc = max(class_auc.values())
            macro_auc = float(np.mean(list(class_auc.values())))
            weighted_auc = float(
                sum(class_auc[str(code)] * count for code, count in class_counts.items())
                / len(target)
            )
            score = probabilities.max(axis=1)
    risk_auc = max(float(risk_auc), 1.0 - float(risk_auc))
    leaf_ids = estimator.apply(feature.matrix)
    leaves = int(np.unique(leaf_ids).size)
    metric = ConfigurationMetric(
        configuration=name,
        auc=risk_auc,
        gini=2.0 * risk_auc - 1.0,
        macro_auc=macro_auc,
        weighted_auc=weighted_auc,
        class_auc=class_auc,
        risk_tier=_risk_tier(risk_auc, constraints),
        leaves=leaves,
        maximum_leaves=effective_maximum_leaves,
        minimum_leaf_rows=minimum_leaf,
        leaf_cap_reached=leaves >= effective_maximum_leaves,
    )
    return _Fit(metric=metric, estimator=estimator, score=score, leaf_ids=leaf_ids)


def _feature_result(
    target: np.ndarray,
    target_type: str,
    spec: FeatureSpec,
    prepared: _PreparedFeature,
    fits: list[_Fit],
    percentile_bins: int,
    constraints: AnalysisConstraints,
) -> FeatureResult:
    primary = fits[0]
    aucs = [fit.metric.auc for fit in fits]
    tiers = {fit.metric.risk_tier for fit in fits}
    spread = max(aucs) - min(aucs)
    if len(fits) == 1:
        robustness = "not sensitivity-tested (low initial risk)"
    elif spread > constraints.stability_tolerance or len(tiers) > 1:
        robustness = "configuration-sensitive"
    elif primary.metric.risk_tier == "high":
        robustness = "robust high association"
    else:
        robustness = "stable within tested configurations"
    leaves = _leaf_summaries(
        target,
        target_type,
        prepared.population,
        primary,
        spec.column,
        (
            prepared.numeric.where(prepared.population.eq("regular"))
            if prepared.numeric is not None
            else None
        ),
    )
    benchmark = None
    effective_benchmark_bins = min(percentile_bins, prepared.regular_unique_values)
    if prepared.feature_type == "numeric" and primary.metric.risk_tier == "high":
        benchmark = _percentile_benchmark(
            prepared,
            target,
            target_type,
            max(2, effective_benchmark_bins),
        )
    messages = []
    if primary.metric.leaf_cap_reached:
        messages.append(
            _message(
                "warning",
                "primary_leaf_cap_reached",
                "The primary tree reached its leaf cap; sensitivity results show whether "
                "the metric remains stable.",
            )
        )
    return FeatureResult(
        feature=spec.column,
        target_type=target_type,
        feature_type=prepared.feature_type,
        rows_evaluated=len(target),
        regular_rows=prepared.regular_rows,
        generic_missing_rows=prepared.missing_rows,
        special_value_rows=prepared.special_rows,
        primary_auc=primary.metric.auc,
        primary_gini=primary.metric.gini,
        primary_risk=primary.metric.risk_tier,
        macro_auc=primary.metric.macro_auc,
        weighted_auc=primary.metric.weighted_auc,
        class_auc=primary.metric.class_auc,
        configurations=[fit.metric for fit in fits],
        auc_min=min(aucs),
        auc_max=max(aucs),
        auc_spread=spread,
        robustness=robustness,
        transformation=prepared.transformation,
        optimization_profile={
            "profile_source": prepared.profile_source,
            "observed_information_states": prepared.observed_information_states,
            "regular_unique_values": prepared.regular_unique_values,
            "requested_percentile_bins": percentile_bins,
            "effective_percentile_bins": (
                max(2, effective_benchmark_bins) if benchmark is not None else None
            ),
            "adaptive_tree_limits": {
                fit.metric.configuration: {
                    "maximum_leaves": fit.metric.maximum_leaves,
                    "maximum_depth": min(
                        TREE_CONFIGURATIONS[fit.metric.configuration]["max_depth"],
                        max(1, prepared.observed_information_states - 1),
                    ),
                }
                for fit in fits
            },
        },
        tree_rules=export_text(
            primary.estimator,
            feature_names=list(prepared.matrix.columns),
            decimals=4,
        ),
        primary_tree=_tree_structure(
            primary,
            spec.column,
            target,
            target_type,
            prepared.matrix,
            (
                prepared.numeric.where(prepared.population.eq("regular"))
                if prepared.numeric is not None
                else None
            ),
        ),
        leaves=leaves,
        percentile_benchmark=benchmark,
        messages=messages,
    )


def _leaf_summaries(
    target: np.ndarray,
    target_type: str,
    population: pd.Series,
    fit: _Fit,
    feature_name: str,
    numeric_values: pd.Series | None,
) -> list[LeafSummary]:
    audit = pd.DataFrame(
        {
            "leaf": fit.leaf_ids,
            "target": target,
            "score": fit.score,
            "population": population.to_numpy(),
        }
    )
    summaries = []
    rule_paths = _leaf_rule_paths(fit, feature_name, numeric_values)
    for leaf_id, group in audit.groupby("leaf"):
        special = group.loc[group["population"].str.startswith("special:"), "population"]
        special_counts = special.str.removeprefix("special:").value_counts().to_dict()
        summaries.append(
            LeafSummary(
                leaf_id=int(leaf_id),
                rule_conditions=rule_paths[int(leaf_id)],
                rows=len(group),
                target_events=int(group["target"].sum()) if target_type == "binary" else None,
                target_mean=float(group["target"].mean()),
                score=float(group["score"].mean()),
                class_distribution={
                    str(key): int(value)
                    for key, value in group["target"].value_counts().sort_index().items()
                }
                if target_type == "multinomial"
                else {},
                regular_rows=int(group["population"].eq("regular").sum()),
                generic_missing_rows=int(group["population"].eq("generic_missing").sum()),
                special_rows={str(key): int(value) for key, value in special_counts.items()},
            )
        )
    return sorted(summaries, key=lambda item: item.score, reverse=True)


def _observed_split_endpoints(
    numeric_values: pd.Series | None, threshold: float
) -> tuple[float, float] | None:
    """Return adjacent observed values around an exact tree threshold."""
    if numeric_values is None:
        return None
    observed = pd.to_numeric(numeric_values, errors="coerce").dropna().to_numpy(dtype=float)
    if not len(observed):
        return None
    lower = observed[observed <= threshold]
    upper = observed[observed > threshold]
    if not len(lower) or not len(upper):
        return None
    return float(np.max(lower)), float(np.min(upper))


def _format_observed_endpoint(value: float) -> str:
    if np.isclose(value, round(value)):
        return str(int(round(value)))
    return f"{value:.6g}"


def _leaf_rule_paths(
    fit: _Fit, feature_name: str, numeric_values: pd.Series | None
) -> dict[int, list[str]]:
    """Return readable root-to-leaf conditions keyed by sklearn's leaf node ID."""
    tree = fit.estimator.tree_
    matrix_columns = list(fit.estimator.feature_names_in_)
    paths: dict[int, list[str]] = {}

    def readable_condition(column: str, threshold: float, goes_left: bool) -> str:
        if column == f"{feature_name}__generic_missing":
            return f"{feature_name} is {'not missing' if goes_left else 'missing'}"
        special_prefix = f"{feature_name}__special_"
        if column.startswith(special_prefix):
            special_value = column.removeprefix(special_prefix)
            return f"{feature_name} is {'not ' if goes_left else ''}special value {special_value}"
        if column == f"{feature_name}__regular_value":
            endpoints = _observed_split_endpoints(numeric_values, threshold)
            if endpoints:
                lower, upper = endpoints
                displayed = _format_observed_endpoint(lower if goes_left else upper)
                return f"{feature_name} {'≤' if goes_left else '≥'} {displayed}"
            operator = "≤" if goes_left else ">"
            return f"{feature_name} {operator} {threshold:.4g}"
        category_prefix = f"{feature_name}_"
        if column.startswith(category_prefix):
            category = column.removeprefix(category_prefix)
            return f"{feature_name} is {'not ' if goes_left else ''}{category}"
        operator = "≤" if goes_left else ">"
        return f"{column} {operator} {threshold:.4g}"

    def walk(node_id: int, conditions: list[str]) -> None:
        left = int(tree.children_left[node_id])
        right = int(tree.children_right[node_id])
        if left == right:
            paths[node_id] = conditions or ["All evaluated records"]
            return
        column = matrix_columns[int(tree.feature[node_id])]
        threshold = float(tree.threshold[node_id])
        walk(left, [*conditions, readable_condition(column, threshold, True)])
        walk(right, [*conditions, readable_condition(column, threshold, False)])

    walk(0, [])
    return paths


def _tree_structure(
    fit: _Fit,
    feature_name: str,
    target: np.ndarray,
    target_type: str,
    matrix: pd.DataFrame,
    numeric_values: pd.Series | None,
) -> list[dict[str, Any]]:
    """Expose the small primary tree as business-readable nodes and branches."""
    tree = fit.estimator.tree_
    matrix_columns = list(fit.estimator.feature_names_in_)
    decision_path = fit.estimator.decision_path(matrix).tocsc()
    nodes: list[dict[str, Any]] = []

    def outcome_summary(
        node_id: int,
        parent_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        row_indexes = decision_path[:, node_id].indices
        node_target = target[row_indexes]
        summary: dict[str, Any] = {
            "target_mean": None,
            "target_events": None,
            "leading_class": None,
            "leading_class_rate": None,
            "parent_delta": None,
            "class_rates": {},
        }
        if target_type in {"binary", "continuous"}:
            summary["target_mean"] = float(np.mean(node_target))
            if target_type == "binary":
                summary["target_events"] = int(np.sum(node_target))
            if parent_summary is not None:
                summary["parent_delta"] = summary["target_mean"] - parent_summary["target_mean"]
            return summary

        classes, counts = np.unique(node_target, return_counts=True)
        class_rates = {
            str(class_code): float(count / len(node_target))
            for class_code, count in zip(classes, counts, strict=True)
        }
        leading_index = int(np.argmax(counts))
        leading_class = str(classes[leading_index])
        leading_rate = class_rates[leading_class]
        summary.update(
            leading_class=leading_class,
            leading_class_rate=leading_rate,
            class_rates=class_rates,
        )
        if parent_summary is not None:
            summary["parent_delta"] = leading_rate - parent_summary["class_rates"].get(
                leading_class, 0.0
            )
        return summary

    def split_presentation(column: str, threshold: float) -> tuple[str, str, str]:
        if column == f"{feature_name}__generic_missing":
            return f"Is {feature_name} missing?", "No", "Yes"
        special_prefix = f"{feature_name}__special_"
        if column.startswith(special_prefix):
            value = column.removeprefix(special_prefix)
            return f"Is {feature_name} special value {value}?", "No", "Yes"
        if column == f"{feature_name}__regular_value":
            endpoints = _observed_split_endpoints(numeric_values, threshold)
            if endpoints:
                lower, upper = endpoints
                lower_display = _format_observed_endpoint(lower)
                upper_display = _format_observed_endpoint(upper)
                return (
                    f"Split {feature_name}: ≤ {lower_display} or ≥ {upper_display}",
                    f"≤ {lower_display}",
                    f"≥ {upper_display}",
                )
            return f"Is {feature_name} ≤ {threshold:.4g}?", "Yes", "No"
        category_prefix = f"{feature_name}_"
        if column.startswith(category_prefix):
            category = column.removeprefix(category_prefix)
            return f"Is {feature_name} {category}?", "No", "Yes"
        return f"Is {column} ≤ {threshold:.4g}?", "Yes", "No"

    def walk(node_id: int, depth: int, parent_summary: dict[str, Any] | None = None) -> None:
        left = int(tree.children_left[node_id])
        right = int(tree.children_right[node_id])
        summary = outcome_summary(node_id, parent_summary)
        public_summary = {key: value for key, value in summary.items() if key != "class_rates"}
        if left == right:
            nodes.append(
                {
                    "node_id": node_id,
                    "depth": depth,
                    "node_type": "leaf",
                    "rows": int(tree.n_node_samples[node_id]),
                    **public_summary,
                }
            )
            return
        column = matrix_columns[int(tree.feature[node_id])]
        prompt, left_label, right_label = split_presentation(column, float(tree.threshold[node_id]))
        nodes.append(
            {
                "node_id": node_id,
                "depth": depth,
                "node_type": "split",
                "prompt": prompt,
                "left_child": left,
                "right_child": right,
                "left_label": left_label,
                "right_label": right_label,
                "rows": int(tree.n_node_samples[node_id]),
                **public_summary,
            }
        )
        walk(left, depth + 1, summary)
        walk(right, depth + 1, summary)

    walk(0, 0)
    return nodes


def _percentile_benchmark(
    feature: _PreparedFeature,
    target: np.ndarray,
    target_type: str,
    bins: int,
) -> BenchmarkResult:
    assert feature.numeric is not None
    bucket = pd.Series(index=feature.numeric.index, dtype="string")
    regular = feature.population.eq("regular")
    if regular.any() and feature.regular_unique_values > 1:
        bucket.loc[regular] = pd.qcut(
            feature.numeric.loc[regular], q=bins, duplicates="drop"
        ).astype("string")
    else:
        bucket.loc[regular] = "REGULAR"
    bucket.loc[feature.population.eq("generic_missing")] = "GENERIC_MISSING"
    for label in feature.special_rows:
        bucket.loc[feature.population.eq(f"special:{label}")] = f"SPECIAL_{label}"
    if bucket.isna().any():
        raise AssertionError("Percentile benchmark did not assign every row to a group.")
    frame = pd.DataFrame({"bucket": bucket.to_numpy(), "target": target})
    bucket_rows: list[dict[str, Any]] = []
    class_auc: dict[str, float] = {}
    if target_type in {"binary", "continuous"}:
        summary = frame.groupby("bucket", as_index=False).agg(
            rows=("target", "size"), target_sum=("target", "sum"), target_mean=("target", "mean")
        )
        score_map = summary.set_index("bucket")["target_mean"]
        scores = bucket.map(score_map).to_numpy(dtype=float)
        if target_type == "binary":
            raw_auc = float(roc_auc_score(target, scores))
        else:
            raw_auc = _continuous_concordance_auc(target, scores)
        auc = macro_auc = weighted_auc = max(raw_auc, 1.0 - raw_auc)
        bucket_rows = summary.sort_values("target_mean", ascending=False).to_dict("records")
    else:
        classes = np.unique(target)
        summary = frame.groupby("bucket", as_index=False).agg(rows=("target", "size"))
        for class_code in classes:
            rates = (
                frame.assign(indicator=(target == class_code).astype(float))
                .groupby("bucket")["indicator"]
                .mean()
            )
            probabilities = bucket.map(rates).to_numpy(dtype=float)
            raw_auc = float(roc_auc_score((target == class_code).astype(np.int8), probabilities))
            class_auc[str(class_code)] = max(raw_auc, 1.0 - raw_auc)
            summary[f"class_{class_code}_rate"] = summary["bucket"].map(rates)
        counts = pd.Series(target).value_counts().to_dict()
        auc = max(class_auc.values())
        macro_auc = float(np.mean(list(class_auc.values())))
        weighted_auc = float(
            sum(class_auc[str(code)] * count for code, count in counts.items()) / len(target)
        )
        bucket_rows = summary.to_dict("records")
    return BenchmarkResult(
        method=f"{bins}-percentile target-ranked buckets",
        rows_evaluated=len(target),
        groups=len(bucket_rows),
        auc=auc,
        gini=2.0 * auc - 1.0,
        macro_auc=macro_auc,
        weighted_auc=weighted_auc,
        class_auc=class_auc,
        buckets=bucket_rows,
    )


def _continuous_concordance_auc(target: np.ndarray, score: np.ndarray) -> float:
    target = np.asarray(target, dtype=float)
    score = np.asarray(score, dtype=float)
    order = np.argsort(score, kind="mergesort")
    sorted_target = target[order]
    sorted_score = score[order]
    _, ranks = np.unique(sorted_target, return_inverse=True)
    fenwick = np.zeros(int(ranks.max()) + 2, dtype=np.int64)

    def prefix(boundary: int) -> int:
        total = 0
        while boundary > 0:
            total += int(fenwick[boundary])
            boundary -= boundary & -boundary
        return total

    def add(rank: int) -> None:
        position = rank + 1
        while position < len(fenwick):
            fenwick[position] += 1
            position += position & -position

    concordant = discordant = score_ties = prior = start = 0
    while start < len(score):
        end = start + 1
        while end < len(score) and sorted_score[end] == sorted_score[start]:
            end += 1
        group = ranks[start:end]
        for rank in group:
            lower = prefix(int(rank))
            equal = prefix(int(rank) + 1) - lower
            concordant += lower
            discordant += prior - lower - equal
        size = len(group)
        target_ties = sum(count * (count - 1) // 2 for count in np.bincount(group))
        score_ties += size * (size - 1) // 2 - target_ties
        for rank in group:
            add(int(rank))
        prior += size
        start = end
    comparable = concordant + discordant + score_ties
    if comparable == 0:
        raise ValueError("Continuous target must contain at least two distinct values.")
    return float((concordant + 0.5 * score_ties) / comparable)


def _risk_tier(auc: float, constraints: AnalysisConstraints) -> str:
    if auc >= constraints.global_high_auc:
        return "high"
    if auc >= constraints.global_medium_auc:
        return "medium"
    return "low"


def _message(severity: str, code: str, message: str, **details: Any) -> AnalysisMessage:
    return AnalysisMessage(severity=severity, code=code, message=message, details=details)
