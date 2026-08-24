"""Target-aware fine/coarse binning with portable, auditable results."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from math import ceil, log2, sqrt
from typing import Any

import numpy as np
import pandas as pd
from optbinning import ContinuousOptimalBinning, MulticlassOptimalBinning, OptimalBinning
from workload_governor import governed_optimizer

from .models import (
    BinDefinition,
    BinningConstraints,
    BinningFeatureSpec,
    BinningMetric,
    BinningResponse,
    BinningReviewResult,
    BinningTargetSpec,
    BinRow,
    FeatureBinningFailure,
    FeatureBinningResult,
    FeatureKind,
    ProgressCallback,
    TargetKind,
)


def fit_dataset_binning(
    data: pd.DataFrame,
    target_spec: BinningTargetSpec,
    features: list[BinningFeatureSpec],
    *,
    constraints: BinningConstraints | None = None,
    feature_profiles: dict[str, dict[str, Any]] | None = None,
    progress_callback: ProgressCallback | None = None,
    cached_results: dict[str, FeatureBinningResult | dict[str, Any]] | None = None,
    result_callback: Callable[[BinningFeatureSpec, FeatureBinningResult], None] | None = None,
    completion_callback: Callable[[BinningFeatureSpec, FeatureBinningResult | None, str | None], None] | None = None,
) -> BinningResponse:
    """Fit auditable bins for several features against one non-missing target."""
    if not features:
        raise ValueError("Select at least one feature for binning.")
    requested = {target_spec.column, *(feature.column for feature in features)}
    absent = sorted(requested - set(map(str, data.columns)))
    if absent:
        raise ValueError(f"Selected columns are absent from the dataset: {absent}")
    if any(feature.column == target_spec.column for feature in features):
        raise ValueError("The target cannot also be selected as a feature.")
    if data[target_spec.column].isna().any():
        raise ValueError("Target must be non-missing for supervised binning.")
    defaults = constraints or BinningConstraints()
    results_by_index: dict[int, FeatureBinningResult] = {}
    failures_by_index: dict[int, FeatureBinningFailure] = {}
    pending_features: list[tuple[int, BinningFeatureSpec]] = []
    completed = 0
    for index, feature in enumerate(features):
        cached = (cached_results or {}).get(feature.column)
        if cached is None:
            pending_features.append((index, feature))
            continue
        result = (
            cached
            if isinstance(cached, FeatureBinningResult)
            else FeatureBinningResult.model_validate(cached)
        )
        if result.feature != feature.column or result.target != target_spec.column:
            raise ValueError("Cached binning result does not match the requested identity.")
        results_by_index[index] = result
        completed += 1
        if progress_callback:
            progress_callback(completed, len(features), feature.column)
        if completion_callback:
            completion_callback(feature, result, None)

    def fit_one(feature: BinningFeatureSpec) -> FeatureBinningResult:
        kind = None if feature.feature_type == "auto" else feature.feature_type
        return fit_feature_binning(
            data[feature.column],
            data[target_spec.column],
            feature_name=feature.column,
            target_name=target_spec.column,
            target_type=target_spec.target_type,
            feature_type=kind,
            positive_class=target_spec.positive_class,
            special_values=feature.special_values,
            constraints=feature.constraints or defaults,
            feature_profile=(feature_profiles or {}).get(feature.column),
        )

    worker_count = min(defaults.max_workers, len(pending_features))
    with ThreadPoolExecutor(
        max_workers=max(worker_count, 1), thread_name_prefix="iv-feature"
    ) as pool:
        pending: dict[Future[FeatureBinningResult], tuple[int, BinningFeatureSpec]] = {
            pool.submit(fit_one, feature): (index, feature)
            for index, feature in pending_features
        }
        for future in as_completed(pending):
            index, feature = pending[future]
            completed += 1
            try:
                results_by_index[index] = future.result()
                if result_callback:
                    result_callback(feature, results_by_index[index])
                if completion_callback:
                    completion_callback(feature, results_by_index[index], None)
            except (TypeError, ValueError) as exc:
                failures_by_index[index] = FeatureBinningFailure(
                    feature=feature.column, error=str(exc)
                )
                if completion_callback:
                    completion_callback(feature, None, str(exc))
            if progress_callback:
                progress_callback(completed, len(features), feature.column)

    results = [results_by_index[index] for index in sorted(results_by_index)]
    failures = [failures_by_index[index] for index in sorted(failures_by_index)]
    return BinningResponse(
        status="partial" if failures else "complete",
        target=target_spec,
        rows_evaluated=len(data),
        results=results,
        failures=failures,
        methodology={
            "fine_binning": (
                "target-independent population-balanced prebins over ordered unique values"
            ),
            "categorical_fine_binning": "one reusable fine bin per observed category",
            "coarse_binning": "constraint-programming optimization over the fine-bin foundation",
            "coarse_revision": (
                "cached sufficient-statistic aggregation without baseline row rescans"
            ),
            "initial_coarse_statistics": (
                "aggregated from the target-independent fine-bin sufficient statistics"
            ),
            "binary_metric": "classical Weight of Evidence and Information Value",
            "continuous_metric": "between-bin variance ratio; not labelled IV",
            "continuous_search_optimization": (
                "heuristic automatic trend selection above 20 effective prebins; Quick mode caps "
                "continuous searches at 16 prebins, 5 coarse bins, and 5 seconds"
            ),
            "full_search_optimization": (
                "retains configured solver time and all available fine-bin boundaries while "
                "removing ceilings that exceed the actual foundation"
            ),
            "multinomial_metric": "one-vs-rest IV with macro, weighted, and maximum summaries",
            "missing_and_special_values_retained": True,
            "zero_event_and_zero_non_event_fine_bins_retained": True,
            "portable_definition_schema_version": 1,
            "feature_parallelism": {
                "strategy": "bounded_thread_pool",
                "workers": worker_count,
                "result_order": "requested_feature_order",
            },
            "artifact_reuse": {
                "reused_features": len(features) - len(pending_features),
                "calculated_features": len(pending_features),
            },
            "execution_mode": defaults.execution_mode,
            "initial_load_profile_reuse": {
                "available_features": len(feature_profiles or {}),
                "reused_features": sum(
                    result.binning_profile.get("profile_source") == "initial_load_profile"
                    for result in results
                ),
                "authority": "exact post-filter cardinality overrides profile hints",
            },
        },
    )


@governed_optimizer
def fit_feature_binning(
    feature: pd.Series | Iterable[Any],
    target: pd.Series | Iterable[Any],
    *,
    feature_name: str,
    target_name: str,
    target_type: TargetKind,
    feature_type: FeatureKind | None = None,
    positive_class: Any = None,
    special_values: dict[str, list[Any]] | None = None,
    constraints: BinningConstraints | None = None,
    feature_profile: dict[str, Any] | None = None,
    numeric_fine_splits: list[float] | None = None,
) -> FeatureBinningResult:
    """Fit supervised fine and constrained coarse bins for one feature."""
    x = _series(feature)
    y = _series(target).reset_index(drop=True)
    x = x.reset_index(drop=True)
    if len(x) != len(y) or x.empty:
        raise ValueError("feature and target must have the same non-zero length.")
    if y.isna().any():
        raise ValueError("Target must be non-missing for supervised binning.")
    requested_rules = constraints or BinningConstraints()
    kind = feature_type or ("numeric" if pd.api.types.is_numeric_dtype(x) else "categorical")
    prepared_target, classes = _prepare_target(y, target_type, positive_class)
    configured_specials = special_values or {}
    special_mask = _special_mask(x, configured_specials)
    missing_mask = x.isna()
    regular = ~(missing_mask | special_mask)
    if not regular.any():
        raise ValueError("Feature has no regular values after missing and special values.")
    profile_unique_count = _matching_profile_unique_count(
        feature_profile, len(x), bool(configured_specials)
    )
    unique_count = (
        profile_unique_count
        if profile_unique_count is not None
        else int(x.loc[regular].nunique(dropna=True))
    )
    profile_source = (
        "initial_load_profile" if profile_unique_count is not None else "post_filter_scan"
    )
    if unique_count < 2:
        raise ValueError("Feature has fewer than two distinct regular values after exclusions.")
    cardinality_rules = _effective_binning_constraints(
        requested_rules, unique_count, target_type
    )
    cardinality_adaptations: list[str] = []
    if cardinality_rules.max_prebins < requested_rules.max_prebins:
        cardinality_adaptations.append("Quick mode reduced fine-bin search by cardinality")
    if cardinality_rules.max_bins < requested_rules.max_bins:
        cardinality_adaptations.append("Quick mode reduced coarse-bin search by cardinality")
    if (
        cardinality_rules.solver_time_limit_seconds
        < requested_rules.solver_time_limit_seconds
    ):
        cardinality_adaptations.append("Quick mode reduced the per-feature solver time limit")

    if kind == "numeric":
        numeric, numeric_transform = _numeric_feature(x, regular)
        specials = _numeric_special_values(configured_specials)
        invalid = regular & numeric.isna()
        if invalid.any():
            raise ValueError(
                f"Numeric feature contains {int(invalid.sum())} non-numeric regular values."
            )
        if numeric_fine_splits is None:
            fine_splits = _numeric_fine_splits(numeric.loc[regular], cardinality_rules)
            fine_splits = sorted(set([*fine_splits, *cardinality_rules.user_splits]))
            fine_method = "population_balanced_prebinning"
        else:
            fine_splits = sorted(set(float(value) for value in numeric_fine_splits))
            if fine_splits != list(numeric_fine_splits):
                raise ValueError("Numeric override cuts must be unique and strictly increasing.")
            observed_min = float(numeric.loc[regular].min())
            observed_max = float(numeric.loc[regular].max())
            outside = [value for value in fine_splits
                       if value <= observed_min or value >= observed_max]
            if outside:
                raise ValueError(
                    "Numeric override cuts must fall strictly inside the observed regular "
                    f"range ({observed_min}, {observed_max}); invalid cuts: {outside}"
                )
            fine_method = "operator_numeric_cut_prebinning"
        rules, foundation_adaptations = _foundation_binning_constraints(
            cardinality_rules, len(fine_splits) + 1
        )
        observed_min = float(numeric.loc[regular].min())
        observed_max = float(numeric.loc[regular].max())
        fine = _definition(
            feature_name,
            target_name,
            kind,
            target_type,
            rules,
            specials,
            numeric_splits=fine_splits,
            numeric_transform=numeric_transform,
            observed_min=observed_min,
            observed_max=observed_max,
            out_of_range_guard_bins=rules.create_out_of_range_guard_bins,
            method=fine_method,
            status="GENERATED",
        )
        coarse, warnings = _optimal_numeric(
            numeric, prepared_target, feature_name, target_name, target_type, rules, specials, fine
        )
    else:
        specials = {
            label: [str(value) for value in values] for label, values in configured_specials.items()
        }
        text = x.astype("string")
        fine_groups = _categorical_fine_groups(text.loc[regular], cardinality_rules)
        fine_labels = _categorical_fine_labels(fine_groups)
        rules, foundation_adaptations = _foundation_binning_constraints(
            cardinality_rules, len(fine_groups)
        )
        fine = _definition(
            feature_name,
            target_name,
            kind,
            target_type,
            rules,
            specials,
            categorical_groups=fine_groups,
            categorical_group_labels=fine_labels,
            method="category_prebinning",
            status="GENERATED",
        )
        coarse, warnings = _optimal_categorical(
            text, prepared_target, feature_name, target_name, target_type, rules, specials, fine
        )

    fine_rows, fine_metrics = _summarize(x, prepared_target, target_type, fine, classes)
    cached_coarse = review_cached_fine_binning(fine, fine_rows, coarse, target_type)
    coarse_rows = cached_coarse.bins
    coarse_metrics = cached_coarse.metrics
    warnings.extend(cached_coarse.warnings)
    coarse_groups = cached_coarse.coarse_groups
    return FeatureBinningResult(
        feature=feature_name,
        target=target_name,
        feature_type=kind,
        target_type=target_type,
        rows_evaluated=len(x),
        fine_definition=fine,
        coarse_definition=coarse,
        fine_bins=fine_rows,
        coarse_bins=coarse_rows,
        fine_metrics=fine_metrics,
        coarse_metrics=coarse_metrics,
        warnings=warnings,
        coarse_groups=coarse_groups,
        binning_profile={
            "execution_mode": rules.execution_mode,
            "profile_source": profile_source,
            "initial_profile_n_levels": profile_unique_count,
            "unique_regular_values": unique_count,
            "fine_bin_foundation_count": (
                len(fine.numeric_splits) + 1
                if kind == "numeric"
                else len(fine.categorical_groups)
            ),
            "requested_max_prebins": requested_rules.max_prebins,
            "effective_max_prebins": rules.max_prebins,
            "requested_max_bins": requested_rules.max_bins,
            "effective_max_bins": rules.max_bins,
            "adaptation_reasons": [
                *cardinality_adaptations,
                *foundation_adaptations,
            ],
            "effective_solver_time_limit_seconds": rules.solver_time_limit_seconds,
            "coarse_summary_source": "cached_fine_bin_statistics",
            "continuous_trend_strategy": (
                _continuous_trend(rules.monotonic_trend, rules.max_prebins)
                if target_type == "continuous"
                else None
            ),
        },
    )


@governed_optimizer
def fit_coarse_from_fine_binning(
    feature: pd.Series | Iterable[Any],
    target: pd.Series | Iterable[Any],
    fine_definition: BinDefinition,
    *,
    target_name: str,
    target_type: TargetKind,
    positive_class: Any = None,
    constraints: BinningConstraints | None = None,
) -> FeatureBinningResult:
    """Run Diagnostic 2's coarse optimizer over an existing exact fine foundation.

    The supplied fine definition is preserved. Only the coarse partition is fitted,
    which lets downstream workflows reuse governed fine-bin lineage without silently
    regenerating or presenting fine bins as a coarse proposal.
    """
    x = _series(feature).reset_index(drop=True)
    y = _series(target).reset_index(drop=True)
    if len(x) != len(y) or x.empty:
        raise ValueError("feature and target must have the same non-zero length.")
    if y.isna().any():
        raise ValueError("Target must be non-missing for supervised binning.")
    if fine_definition.feature != (x.name or fine_definition.feature):
        raise ValueError("Fine definition feature does not match the supplied feature.")
    if fine_definition.target and fine_definition.target != target_name:
        raise ValueError("Fine definition target does not match the supplied target.")
    if fine_definition.target_type and fine_definition.target_type != target_type:
        raise ValueError("Fine definition and target interpretation do not match.")

    prepared_target, classes = _prepare_target(y, target_type, positive_class)
    regular_count = (len(fine_definition.numeric_splits) + 1
                     if fine_definition.feature_type == "numeric"
                     else len(fine_definition.categorical_groups))
    requested_rules = constraints or fine_definition.constraints
    rules, foundation_adaptations = _foundation_binning_constraints(
        requested_rules, regular_count
    )
    fine = fine_definition.model_copy(update={
        "target": target_name,
        "target_type": target_type,
        "constraints": rules,
    })
    specials = fine.special_values
    special_mask = _special_mask(x, specials)
    regular = ~(x.isna() | special_mask)
    if not regular.any():
        raise ValueError("Feature has no regular values after missing and special values.")

    if fine.feature_type == "numeric":
        numeric, numeric_transform = _numeric_feature(x, regular)
        if numeric_transform != fine.numeric_transform:
            raise ValueError("Fine definition numeric transform does not match the supplied feature.")
        coarse, warnings = _optimal_numeric(
            numeric, prepared_target, fine.feature, target_name, target_type,
            rules, _numeric_special_values(specials), fine,
        )
    else:
        text = x.astype("string")
        coarse, warnings = _optimal_categorical(
            text, prepared_target, fine.feature, target_name, target_type,
            rules, {label: [str(value) for value in values]
                    for label, values in specials.items()}, fine,
        )

    proposed_count = (len(coarse.numeric_splits) + 1
                      if fine.feature_type == "numeric"
                      else len(coarse.categorical_groups))
    if proposed_count < rules.min_bins or proposed_count > rules.max_bins:
        desired = min(max(rules.min_bins, proposed_count), rules.max_bins, regular_count)
        if fine.feature_type == "numeric":
            retained = [fine.numeric_splits[ceil(index * regular_count / desired) - 1]
                        for index in range(1, desired)]
            coarse = fine.model_copy(update={
                "numeric_splits": retained,
                "method": "fine_foundation_bounded_fallback",
                "solver_status": "FEASIBLE",
                "constraints": rules,
            })
        else:
            groups = [[] for _ in range(desired)]
            for index, fine_group in enumerate(fine.categorical_groups):
                bucket = min(desired - 1, index * desired // regular_count)
                groups[bucket].extend(fine_group)
            coarse = fine.model_copy(update={
                "categorical_groups": groups,
                "method": "fine_foundation_bounded_fallback",
                "solver_status": "FEASIBLE",
                "constraints": rules,
            })
        warnings.append(
            f"Automatic solver proposed {proposed_count} regular bins; a deterministic "
            f"{desired}-bin partition was created to satisfy review bounds."
        )

    fine_rows, fine_metrics = _summarize(x, prepared_target, target_type, fine, classes)
    cached_coarse = review_cached_fine_binning(fine, fine_rows, coarse, target_type)
    warnings.extend(cached_coarse.warnings)
    return FeatureBinningResult(
        feature=fine.feature,
        target=target_name,
        feature_type=fine.feature_type,
        target_type=target_type,
        rows_evaluated=len(x),
        fine_definition=fine,
        coarse_definition=coarse,
        fine_bins=fine_rows,
        coarse_bins=cached_coarse.bins,
        fine_metrics=fine_metrics,
        coarse_metrics=cached_coarse.metrics,
        warnings=warnings,
        coarse_groups=cached_coarse.coarse_groups,
        binning_profile={
            "coarse_summary_source": "cached_exact_fine_bin_statistics",
            "fine_foundation_source": "governed_repository_artifact",
            "adaptation_reasons": foundation_adaptations,
        },
    )


def _matching_profile_unique_count(
    profile: dict[str, Any] | None,
    row_count: int,
    has_configured_specials: bool,
) -> int | None:
    """Use a retained level count only when it describes the exact regular population."""
    if not profile or has_configured_specials or profile.get("n_levels") is None:
        return None
    summary = profile.get("summary") or {}
    profiled_rows = summary.get("count", 0) + summary.get("missing", 0)
    if profiled_rows != row_count:
        return None
    return int(profile["n_levels"])


def _effective_binning_constraints(
    rules: BinningConstraints,
    unique_count: int,
    target_type: TargetKind | None = None,
) -> BinningConstraints:
    """Reduce Quick-mode search space according to observed feature cardinality."""
    if rules.execution_mode != "quick":
        return rules
    continuous = target_type == "continuous"
    prebin_cap = 16 if continuous else 25
    bin_cap = 5 if continuous else 6
    multiplier = 1.5 if continuous else 2
    adaptive_prebins = max(
        4,
        min(prebin_cap, ceil(multiplier * sqrt(max(unique_count, 1)))),
    )
    adaptive_bins = max(2, min(bin_cap, ceil(log2(max(unique_count, 1) + 1))))
    max_prebins = min(
        rules.max_prebins,
        max(adaptive_prebins, len(rules.user_splits) + 1),
    )
    max_bins = min(rules.max_bins, adaptive_bins)
    min_bins = min(rules.min_bins, max_bins)
    return rules.model_copy(
        update={
            "max_prebins": max_prebins,
            "max_bins": max_bins,
            "min_bins": min_bins,
            "solver_time_limit_seconds": min(
                rules.solver_time_limit_seconds, 5 if continuous else 8
            ),
        }
    )


def _foundation_binning_constraints(
    rules: BinningConstraints,
    foundation_bin_count: int,
) -> tuple[BinningConstraints, list[str]]:
    """Remove impossible optimizer states without reducing solver time or fine detail."""
    effective_prebins = max(
        4,
        min(
            rules.max_prebins,
            max(foundation_bin_count, len(rules.user_splits) + 1),
        ),
    )
    effective_bins = max(2, min(rules.max_bins, foundation_bin_count))
    effective_minimum = min(rules.min_bins, effective_bins)
    reasons: list[str] = []
    if effective_prebins < rules.max_prebins:
        reasons.append("maximum fine bins capped to available fine-bin foundation")
    if effective_bins < rules.max_bins:
        reasons.append("maximum coarse bins capped to available fine-bin foundation")
    if effective_minimum < rules.min_bins:
        reasons.append("minimum coarse bins capped to available fine-bin foundation")
    return (
        rules.model_copy(
            update={
                "max_prebins": effective_prebins,
                "max_bins": effective_bins,
                "min_bins": effective_minimum,
            }
        ),
        reasons,
    )


def apply_bin_definition(
    values: pd.Series | Iterable[Any], definition: BinDefinition
) -> pd.DataFrame:
    """Apply a frozen definition and return stable bin IDs, labels, and kinds."""
    series = _series(values)
    result = pd.DataFrame(index=series.index, columns=["bin_id", "label", "kind"])
    missing = series.isna()
    result.loc[missing] = ["MISSING", definition.missing_label, "missing"]
    special = pd.Series(False, index=series.index)
    for position, (label, codes) in enumerate(definition.special_values.items()):
        mask = pd.Series(False, index=series.index)
        for code in codes:
            mask |= _value_mask(series, code)
        result.loc[mask] = [f"S{position}", label, "special"]
        special |= mask
    regular = ~(missing | special)

    if definition.feature_type == "numeric":
        numeric = _apply_numeric_transform(series.loc[regular], definition.numeric_transform)
        invalid = numeric.isna()
        result.loc[numeric.index[invalid]] = ["UNSEEN", definition.unseen_label, "unseen"]
        valid = numeric.loc[~invalid]
        if (
            definition.out_of_range_guard_bins
            and definition.observed_min is not None
            and definition.observed_max is not None
        ):
            low = valid.lt(definition.observed_min)
            high = valid.gt(definition.observed_max)
            result.loc[valid.index[low]] = ["GUARD_LOW", "Below observed minimum", "guard"]
            result.loc[valid.index[high]] = ["GUARD_HIGH", "Above observed maximum", "guard"]
            valid = valid.loc[~(low | high)]
        edges = [-np.inf, *definition.numeric_splits, np.inf]
        indexes = pd.cut(valid, bins=edges, labels=False, include_lowest=True)
        for index, left, right in zip(range(len(edges) - 1), edges[:-1], edges[1:], strict=True):
            mask = indexes.eq(index)
            result.loc[indexes.index[mask]] = [
                f"R{index}",
                _interval_label(left, right, definition.numeric_transform),
                "regular",
            ]
    else:
        text = series.loc[regular].astype("string")
        assigned = pd.Series(False, index=text.index)
        for index, group in enumerate(definition.categorical_groups):
            mask = text.isin(group)
            label = (definition.categorical_group_labels[index]
                     if index < len(definition.categorical_group_labels)
                     else " | ".join(group))
            result.loc[text.index[mask]] = [f"R{index}", label, "regular"]
            assigned |= mask
        result.loc[text.index[~assigned]] = ["UNSEEN", definition.unseen_label, "unseen"]
    if result.isna().any(axis=None):
        raise AssertionError("Bin assignment did not reconcile every input row.")
    return result.astype({"bin_id": "string", "label": "string", "kind": "string"})


def review_bin_definition(
    feature: pd.Series | Iterable[Any],
    target: pd.Series | Iterable[Any],
    target_spec: BinningTargetSpec,
    definition: BinDefinition,
) -> BinningReviewResult:
    """Reapply an edited definition and recalculate its metrics and warnings."""
    x = _series(feature).reset_index(drop=True)
    y = _series(target).reset_index(drop=True)
    if len(x) != len(y) or x.empty:
        raise ValueError("feature and target must have the same non-zero length.")
    if y.isna().any():
        raise ValueError("Target must be non-missing for supervised bin review.")
    if definition.feature != x.name and x.name is not None:
        raise ValueError("Definition feature does not match the supplied feature.")
    if definition.target_type and definition.target_type != target_spec.target_type:
        raise ValueError("Definition and target interpretation do not match.")
    prepared, classes = _prepare_target(y, target_spec.target_type, target_spec.positive_class)
    rows, metrics = _summarize(x, prepared, target_spec.target_type, definition, classes)
    warnings = _constraint_warnings(rows, definition.constraints, target_spec.target_type)
    return BinningReviewResult(
        definition=definition,
        bins=rows,
        metrics=metrics,
        warnings=warnings,
        coarse_groups=[],
    )


def review_cached_fine_binning(
    fine_definition: BinDefinition,
    fine_bins: list[BinRow],
    edited_definition: BinDefinition,
    target_type: TargetKind,
) -> BinningReviewResult:
    """Recalculate a coarse revision solely from cached fine-bin statistics."""
    if fine_definition.feature != edited_definition.feature:
        raise ValueError("Fine and coarse definitions refer to different features.")
    if fine_definition.feature_type != edited_definition.feature_type:
        raise ValueError("A coarse revision cannot change the feature type.")
    if edited_definition.feature_type == "categorical":
        edited_definition = edited_definition.model_copy(update={
            "categorical_group_labels": _categorical_coarse_labels(
                edited_definition.categorical_groups, fine_definition,
            ),
        })
    groups = _coarse_groups(fine_definition, edited_definition, require_partition=True)
    by_id = {row.bin_id: row for row in fine_bins}
    merged: list[BinRow] = []
    edges = [-np.inf, *edited_definition.numeric_splits, np.inf]
    for index, group in enumerate(groups):
        members = [
            by_id.get(
                bin_id,
                BinRow(
                    bin_id=bin_id,
                    label=bin_id,
                    kind="regular",
                    rows=0,
                    population_share=0.0,
                ),
            )
            for bin_id in group
        ]
        rows = sum(item.rows for item in members)
        if edited_definition.feature_type == "numeric":
            label = _interval_label(
                edges[index], edges[index + 1], edited_definition.numeric_transform
            )
        else:
            label = (edited_definition.categorical_group_labels[index]
                     if index < len(edited_definition.categorical_group_labels)
                     else " | ".join(edited_definition.categorical_groups[index]))
        merged.append(_combine_rows(f"R{index}", label, members, rows, target_type))

    for row in fine_bins:
        if row.kind != "regular":
            merged.append(row.model_copy(deep=True))
    populated = [row for row in merged if row.rows > 0]
    total_rows = sum(row.rows for row in populated)
    for row in merged:
        row.population_share = row.rows / total_rows if total_rows else 0.0

    metrics = _metrics_from_aggregates(merged, target_type)
    warnings = _constraint_warnings(merged, edited_definition.constraints, target_type)
    return BinningReviewResult(
        definition=edited_definition.model_copy(
            update={"method": "cached_fine_bin_revision", "solver_status": "USER_EDITED"}
        ),
        bins=merged,
        metrics=metrics,
        warnings=warnings,
        coarse_groups=groups,
    )


def _combine_rows(
    bin_id: str,
    label: str,
    members: list[BinRow],
    rows: int,
    target_type: TargetKind,
) -> BinRow:
    combined = BinRow(bin_id=bin_id, label=label, kind="regular", rows=rows, population_share=0)
    populated_members = [item for item in members if item.rows and item.feature_mean is not None]
    if populated_members:
        combined.feature_min = min(
            item.feature_min for item in populated_members if item.feature_min is not None
        )
        combined.feature_max = max(
            item.feature_max for item in populated_members if item.feature_max is not None
        )
        combined.feature_mean = sum(
            (item.feature_mean or 0) * item.rows for item in populated_members
        ) / sum(item.rows for item in populated_members)
    if target_type == "binary":
        combined.events = sum(item.events or 0 for item in members)
        combined.non_events = rows - combined.events
        combined.event_rate = combined.events / rows if rows else None
    elif target_type == "continuous":
        if rows:
            target_sum = sum((item.target_mean or 0) * item.rows for item in members)
            sum_squares = sum(
                item.rows * ((item.target_std or 0) ** 2 + (item.target_mean or 0) ** 2)
                for item in members
            )
            combined.target_mean = target_sum / rows
            combined.target_std = float(
                np.sqrt(max(0.0, sum_squares / rows - combined.target_mean**2))
            )
    else:
        classes = sorted({key for item in members for key in item.class_counts})
        combined.class_counts = {
            label: sum(item.class_counts.get(label, 0) for item in members) for label in classes
        }
        combined.class_rates = {
            label: count / rows if rows else 0.0 for label, count in combined.class_counts.items()
        }
    return combined


def _metrics_from_aggregates(rows: list[BinRow], target_type: TargetKind) -> list[BinningMetric]:
    populated = [row for row in rows if row.rows > 0]
    if target_type == "binary":
        total_events = sum(row.events or 0 for row in populated)
        total_non_events = sum(row.non_events or 0 for row in populated)
        smoothing = 0.5
        for row in populated:
            row.event_distribution = ((row.events or 0) + smoothing) / (
                total_events + smoothing * len(populated)
            )
            row.non_event_distribution = ((row.non_events or 0) + smoothing) / (
                total_non_events + smoothing * len(populated)
            )
            row.woe = float(np.log(row.non_event_distribution / row.event_distribution))
            row.iv = float((row.non_event_distribution - row.event_distribution) * row.woe)
        return [
            BinningMetric(
                name="information_value",
                value=sum(row.iv or 0 for row in populated),
                interpretation="classical_binary_iv",
            )
        ]
    if target_type == "continuous":
        total = sum(row.rows for row in populated)
        overall = sum((row.target_mean or 0) * row.rows for row in populated) / max(total, 1)
        between = sum(row.rows * ((row.target_mean or 0) - overall) ** 2 for row in populated)
        total_variance = sum(
            row.rows * ((row.target_std or 0) ** 2 + ((row.target_mean or 0) - overall) ** 2)
            for row in populated
        )
        return [
            BinningMetric(
                name="between_bin_variance_ratio",
                value=between / total_variance if total_variance else 0,
                interpretation="continuous_target_separation_not_iv",
            )
        ]

    classes = sorted({key for row in populated for key in row.class_counts})
    totals = {label: sum(row.class_counts.get(label, 0) for row in populated) for label in classes}
    total = sum(row.rows for row in populated)
    class_totals = {label: 0.0 for label in classes}
    smoothing = 0.5
    for row in populated:
        row.class_iv = {}
        for label in classes:
            events = row.class_counts.get(label, 0)
            non_events = row.rows - events
            event_distribution = (events + smoothing) / (totals[label] + smoothing * len(populated))
            non_event_distribution = (non_events + smoothing) / (
                total - totals[label] + smoothing * len(populated)
            )
            contribution = float(
                (non_event_distribution - event_distribution)
                * np.log(non_event_distribution / event_distribution)
            )
            row.class_iv[label] = contribution
            class_totals[label] += contribution
    values = list(class_totals.values())
    return [
        *[
            BinningMetric(
                name=f"one_vs_rest_iv:{label}", value=value, interpretation="multinomial_class_iv"
            )
            for label, value in class_totals.items()
        ],
        BinningMetric(
            name="macro_one_vs_rest_iv",
            value=float(np.mean(values)) if values else 0,
            interpretation="multinomial_summary",
        ),
        BinningMetric(
            name="weighted_one_vs_rest_iv",
            value=sum(class_totals[label] * totals[label] for label in classes) / max(total, 1),
            interpretation="multinomial_summary",
        ),
        BinningMetric(
            name="maximum_one_vs_rest_iv",
            value=max(values) if values else 0,
            interpretation="multinomial_summary",
        ),
    ]


def _coarse_groups(
    fine: BinDefinition, coarse: BinDefinition, *, require_partition: bool = False
) -> list[list[str]]:
    if fine.feature_type == "numeric":
        if require_partition:
            unmatched = [
                split
                for split in coarse.numeric_splits
                if not any(np.isclose(split, candidate) for candidate in fine.numeric_splits)
            ]
            if unmatched:
                raise ValueError(
                    "Coarse boundaries must use cached fine-bin edges. "
                    f"Unsupported boundaries: {unmatched}"
                )
        groups: list[list[str]] = [[] for _ in range(len(coarse.numeric_splits) + 1)]
        for index in range(len(fine.numeric_splits) + 1):
            upper = fine.numeric_splits[index] if index < len(fine.numeric_splits) else np.inf
            groups[bisect_left(coarse.numeric_splits, upper)].append(f"R{index}")
        return groups

    lookup = {
        value: coarse_index
        for coarse_index, group in enumerate(coarse.categorical_groups)
        for value in group
    }
    groups = [[] for _ in coarse.categorical_groups]
    missing = []
    for fine_index, fine_group in enumerate(fine.categorical_groups):
        destinations = {lookup.get(value) for value in fine_group}
        if len(destinations) != 1 or None in destinations:
            missing.extend(fine_group)
            continue
        groups[destinations.pop()].append(f"R{fine_index}")
    if require_partition and (missing or any(not group for group in groups)):
        raise ValueError(
            "Every cached fine category must belong to exactly one non-empty coarse group."
        )
    return groups


def _prepare_target(
    target: pd.Series, target_type: TargetKind, positive_class: Any
) -> tuple[pd.Series, list[str]]:
    unique = list(pd.unique(target))
    if target_type == "binary":
        if len(unique) != 2:
            raise ValueError("Binary target must contain exactly two distinct values.")
        if positive_class is None:
            positive_class = sorted(unique, key=str)[1]
        elif positive_class not in unique:
            matches = [value for value in unique if str(value) == str(positive_class)]
            if len(matches) == 1:
                positive_class = matches[0]
        encoded = target.eq(positive_class).astype(np.int8)
        if not encoded.any():
            raise ValueError(f"Positive class {positive_class!r} was not found in the target.")
        return encoded, ["non_event", "event"]
    if target_type == "continuous":
        numeric = pd.to_numeric(target, errors="coerce")
        if numeric.isna().any() or numeric.nunique() < 2:
            raise ValueError("Continuous target must contain at least two numeric values.")
        return numeric.astype(float), []
    if len(unique) < 2:
        raise ValueError("Multinomial target must contain at least two classes.")
    text = target.astype("string")
    return text, sorted(text.unique().tolist())


def _numeric_fine_splits(x: pd.Series, rules: BinningConstraints) -> list[float]:
    """Create deterministic, population-balanced boundaries without target filtering."""
    counts = x.value_counts().sort_index()
    if len(counts) <= 1:
        return []
    minimum = max(2, int(np.ceil(rules.min_prebin_size * len(x))))
    desired = max(1, min(rules.max_prebins, len(counts), len(x) // minimum))
    if desired <= 1:
        return []

    values = counts.index.to_numpy(dtype=float)
    cumulative = counts.cumsum().to_numpy()
    splits: list[float] = []
    for position in range(1, desired):
        target_count = position * len(x) / desired
        left_index = int(np.searchsorted(cumulative, target_count, side="left"))
        if left_index >= len(values) - 1:
            continue
        left = float(values[left_index])
        right = float(values[left_index + 1])
        splits.append(left + (right - left) / 2)

    splits = sorted(set(splits))
    if rules.suggest_zero_boundary and float(values[0]) < 0 < float(values[-1]):
        proposed = sorted(set([*splits, 0.0]))
        populations = pd.cut(x, [-np.inf, *proposed, np.inf], include_lowest=True).value_counts()
        if len(proposed) < rules.max_prebins and int(populations.min()) >= minimum:
            splits = proposed
    return sorted(set(splits))


def _categorical_fine_groups(x: pd.Series, rules: BinningConstraints) -> list[list[str]]:
    del rules
    counts = x.dropna().astype("string").value_counts()
    ordered = sorted(
        ((str(value), int(count)) for value, count in counts.items()),
        key=lambda item: (-item[1], item[0]),
    )
    if len(ordered) <= 49:
        return [[value] for value, _count in ordered]
    top = [[value] for value, _count in ordered[:49]]
    other = sorted(value for value, _count in ordered[49:])
    return [*top, other]


def _categorical_fine_labels(groups: list[list[str]]) -> list[str]:
    return [
        "Other" if len(groups) == 50 and index == 49 else " | ".join(group)
        for index, group in enumerate(groups)
    ]


def _categorical_coarse_labels(groups: list[list[str]], fine: BinDefinition) -> list[str]:
    other = next((set(group) for group, label in zip(
        fine.categorical_groups, fine.categorical_group_labels, strict=False
    ) if label == "Other"), set())
    labels: list[str] = []
    for group in groups:
        values = [value for value in group if value not in other]
        if other and other.issubset(set(group)):
            values.append("Other")
        labels.append(" | ".join(values))
    return labels


def _optimal_numeric(
    x: pd.Series,
    y: pd.Series,
    feature: str,
    target: str,
    target_type: TargetKind,
    rules: BinningConstraints,
    specials: dict[str, list[Any]],
    fine: BinDefinition,
) -> tuple[BinDefinition, list[str]]:
    common = _optimizer_parameters(
        feature, rules, specials, fine.numeric_splits, target_type=target_type
    )
    if target_type == "binary":
        model = OptimalBinning(
            **common,
            dtype="numerical",
            solver="cp",
            divergence="iv",
            min_bin_n_event=rules.min_event_count,
            min_bin_n_nonevent=rules.min_non_event_count,
            min_event_rate_diff=rules.min_target_difference,
        )
    elif target_type == "continuous":
        model = ContinuousOptimalBinning(
            **common,
            dtype="numerical",
            min_mean_diff=rules.min_target_difference,
        )
    else:
        model = MulticlassOptimalBinning(
            **common,
            min_event_rate_diff=rules.min_target_difference,
        )
    model.fit(x.to_numpy(), y.to_numpy())
    status = str(model.status)
    warnings = [] if status in {"OPTIMAL", "FEASIBLE"} else [f"Optimizer status: {status}."]
    splits = [float(value) for value in np.asarray(model.splits, dtype=float)]
    return (
        _definition(
            feature,
            target,
            "numeric",
            target_type,
            rules,
            specials,
            numeric_splits=splits,
            numeric_transform=fine.numeric_transform,
            observed_min=fine.observed_min,
            observed_max=fine.observed_max,
            out_of_range_guard_bins=fine.out_of_range_guard_bins,
            method="optbinning_cp",
            status=status,
        ),
        warnings,
    )


def _optimal_categorical(
    x: pd.Series,
    y: pd.Series,
    feature: str,
    target: str,
    target_type: TargetKind,
    rules: BinningConstraints,
    specials: dict[str, list[Any]],
    fine: BinDefinition,
) -> tuple[BinDefinition, list[str]]:
    if target_type == "multinomial":
        groups = _merge_multiclass_categories(x, y, fine.categorical_groups, rules)
        return (
            _definition(
                feature,
                target,
                "categorical",
                target_type,
                rules,
                specials,
                categorical_groups=groups,
                categorical_group_labels=_categorical_coarse_labels(groups, fine),
                method="minimum_information_loss_category_merging",
                status="HEURISTIC",
            ),
            [
                "Multinomial categorical bins use deterministic minimum-information-loss "
                "merging because OptBinning 0.21 does not support this combination."
            ],
        )
    special_codes = [code for codes in specials.values() for code in codes]
    common: dict[str, Any] = {
        "name": feature,
        "dtype": "categorical",
        "max_n_prebins": rules.max_prebins,
        "min_prebin_size": rules.min_prebin_size,
        "min_n_bins": rules.min_bins,
        "max_n_bins": rules.max_bins,
        "min_bin_size": rules.min_bin_size,
        "max_bin_size": rules.max_bin_size,
        "monotonic_trend": (
            _continuous_trend(rules.monotonic_trend, rules.max_prebins)
            if target_type == "continuous"
            else _trend(rules.monotonic_trend)
        ),
        "max_pvalue": rules.max_pvalue,
        # Preserve category identity in the reusable fine-bin artifact. Population
        # constraints belong to the coarse optimizer, not destructive pre-grouping.
        "cat_cutoff": None,
        "special_codes": special_codes or None,
        "time_limit": rules.solver_time_limit_seconds,
    }
    if target_type == "binary":
        model = OptimalBinning(
            **common,
            solver="cp",
            divergence="iv",
            min_bin_n_event=rules.min_event_count,
            min_bin_n_nonevent=rules.min_non_event_count,
            min_event_rate_diff=rules.min_target_difference,
        )
    else:
        model = ContinuousOptimalBinning(**common, min_mean_diff=rules.min_target_difference)
    # Optimize over the governed fine foundation, not the raw categories. This
    # keeps the high-cardinality Other fine bin atomic during coarse fitting.
    occupied = set(x.dropna().astype("string").unique().tolist())
    tokens: list[str] = []
    mapped = x.astype("string").copy()
    for fine_index, fine_group in enumerate(fine.categorical_groups):
        token = f"__DWB_FINE_{fine_index}__"
        while token in occupied:
            token = f"_{token}"
        occupied.add(token)
        tokens.append(token)
        mapped.loc[mapped.isin(fine_group)] = token
    model.fit(mapped.to_numpy(dtype=object), y.to_numpy())
    indices = model.transform(np.asarray(tokens, dtype=object), metric="indices")
    groups: dict[int, list[str]] = {}
    for fine_group, index in zip(fine.categorical_groups, indices, strict=True):
        groups.setdefault(int(index), []).extend(fine_group)
    ordered = [sorted(groups[index]) for index in sorted(groups)]
    status = str(model.status)
    return (
        _definition(
            feature,
            target,
            "categorical",
            target_type,
            rules,
            specials,
            categorical_groups=ordered,
            categorical_group_labels=_categorical_coarse_labels(ordered, fine),
            method="optbinning_cp",
            status=status,
        ),
        [] if status in {"OPTIMAL", "FEASIBLE"} else [f"Optimizer status: {status}."],
    )


def _merge_multiclass_categories(
    x: pd.Series,
    y: pd.Series,
    initial: list[list[str]],
    rules: BinningConstraints,
) -> list[list[str]]:
    frame = pd.DataFrame({"x": x.astype("string"), "y": y})
    groups = [list(group) for group in initial]
    minimum = rules.min_bin_size * len(frame)

    def profile(group: list[str]) -> tuple[int, np.ndarray]:
        selected = frame.loc[frame["x"].isin(group), "y"]
        counts = selected.value_counts().sort_index()
        classes = sorted(frame["y"].unique())
        vector = counts.reindex(classes, fill_value=0).to_numpy(dtype=float)
        return len(selected), vector

    def entropy(counts: np.ndarray) -> float:
        total = counts.sum()
        if total == 0:
            return 0.0
        probabilities = counts[counts > 0] / total
        return float(-total * np.sum(probabilities * np.log(probabilities)))

    while len(groups) > rules.max_bins or (
        len(groups) > rules.min_bins and any(profile(group)[0] < minimum for group in groups)
    ):
        profiles = [profile(group) for group in groups]
        undersized = {index for index, (size, _) in enumerate(profiles) if size < minimum}
        candidates = []
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                if undersized and left not in undersized and right not in undersized:
                    continue
                _, left_counts = profiles[left]
                _, right_counts = profiles[right]
                loss = (
                    entropy(left_counts + right_counts)
                    - entropy(left_counts)
                    - entropy(right_counts)
                )
                candidates.append((loss, left, right))
        if not candidates:
            break
        _, left, right = min(candidates)
        groups[left] = sorted([*groups[left], *groups[right]])
        groups.pop(right)
    return groups


def _optimizer_parameters(
    feature: str,
    rules: BinningConstraints,
    specials: dict[str, list[Any]],
    fine_splits: list[float],
    *,
    target_type: TargetKind | None = None,
) -> dict[str, Any]:
    fixed_lookup = dict(zip(rules.user_splits, rules.fixed_user_splits, strict=False))
    special_codes = [code for codes in specials.values() for code in codes]
    return {
        "name": feature,
        "max_n_prebins": rules.max_prebins,
        "min_prebin_size": rules.min_prebin_size,
        "min_n_bins": rules.min_bins,
        "max_n_bins": rules.max_bins,
        "min_bin_size": rules.min_bin_size,
        "max_bin_size": rules.max_bin_size,
        "monotonic_trend": (
            _continuous_trend(rules.monotonic_trend, rules.max_prebins)
            if target_type == "continuous"
            else _trend(rules.monotonic_trend)
        ),
        "max_pvalue": rules.max_pvalue,
        "user_splits": fine_splits or None,
        "user_splits_fixed": [fixed_lookup.get(value, False) for value in fine_splits]
        if any(fixed_lookup.values())
        else None,
        "special_codes": special_codes or None,
        "time_limit": rules.solver_time_limit_seconds,
    }


def _definition(
    feature: str,
    target: str,
    feature_type: FeatureKind,
    target_type: TargetKind,
    rules: BinningConstraints,
    specials: dict[str, list[Any]],
    *,
    numeric_splits: list[float] | None = None,
    numeric_transform: str = "identity",
    observed_min: float | None = None,
    observed_max: float | None = None,
    out_of_range_guard_bins: bool = False,
    categorical_groups: list[list[str]] | None = None,
    categorical_group_labels: list[str] | None = None,
    method: str,
    status: str,
) -> BinDefinition:
    return BinDefinition(
        feature=feature,
        feature_type=feature_type,
        target=target,
        target_type=target_type,
        numeric_splits=numeric_splits or [],
        numeric_transform=numeric_transform,
        observed_min=observed_min,
        observed_max=observed_max,
        out_of_range_guard_bins=out_of_range_guard_bins,
        categorical_groups=categorical_groups or [],
        categorical_group_labels=categorical_group_labels or [],
        special_values=specials,
        method=method,
        solver_status=status,
        constraints=rules,
    )


def _summarize(
    raw: pd.Series,
    target: pd.Series,
    target_type: TargetKind,
    definition: BinDefinition,
    classes: list[str],
) -> tuple[list[BinRow], list[BinningMetric]]:
    assigned = apply_bin_definition(raw, definition).reset_index(drop=True)
    frame = assigned.assign(target=target.reset_index(drop=True))
    groups = list(frame.groupby(["bin_id", "label", "kind"], sort=False, observed=True))
    rows: list[BinRow] = []
    metrics: list[BinningMetric] = []
    if target_type == "binary":
        total_events = int(target.sum())
        total_non_events = len(target) - total_events
        smoothing = 0.5
        denominator_events = total_events + smoothing * len(groups)
        denominator_non_events = total_non_events + smoothing * len(groups)
        for (bin_id, label, kind), group in groups:
            events = int(group["target"].sum())
            non_events = len(group) - events
            event_distribution = (events + smoothing) / denominator_events
            non_event_distribution = (non_events + smoothing) / denominator_non_events
            woe = float(np.log(non_event_distribution / event_distribution))
            iv = float((non_event_distribution - event_distribution) * woe)
            rows.append(
                BinRow(
                    bin_id=str(bin_id),
                    label=str(label),
                    kind=str(kind),
                    rows=len(group),
                    population_share=len(group) / len(frame),
                    events=events,
                    non_events=non_events,
                    event_rate=events / len(group),
                    event_distribution=event_distribution,
                    non_event_distribution=non_event_distribution,
                    woe=woe,
                    iv=iv,
                )
            )
        metrics.append(
            BinningMetric(
                name="information_value",
                value=sum(row.iv or 0 for row in rows),
                interpretation="classical_binary_iv",
            )
        )
    elif target_type == "continuous":
        overall = float(target.mean())
        total_variance = float(np.square(target - overall).sum())
        between = 0.0
        for (bin_id, label, kind), group in groups:
            mean = float(group["target"].mean())
            between += len(group) * (mean - overall) ** 2
            rows.append(
                BinRow(
                    bin_id=str(bin_id),
                    label=str(label),
                    kind=str(kind),
                    rows=len(group),
                    population_share=len(group) / len(frame),
                    target_mean=mean,
                    target_std=float(group["target"].std(ddof=0)),
                )
            )
        metrics.append(
            BinningMetric(
                name="between_bin_variance_ratio",
                value=between / total_variance if total_variance else 0,
                interpretation="continuous_target_separation_not_iv",
            )
        )
    else:
        smoothing = 0.5
        totals = target.value_counts().reindex(classes, fill_value=0)
        class_totals = {label: 0.0 for label in classes}
        for (bin_id, label, kind), group in groups:
            counts = group["target"].value_counts().reindex(classes, fill_value=0)
            class_iv: dict[str, float] = {}
            for class_label in classes:
                events = int(counts[class_label])
                non_events = len(group) - events
                event_distribution = (events + smoothing) / (
                    int(totals[class_label]) + smoothing * len(groups)
                )
                non_event_distribution = (non_events + smoothing) / (
                    len(target) - int(totals[class_label]) + smoothing * len(groups)
                )
                contribution = float(
                    (non_event_distribution - event_distribution)
                    * np.log(non_event_distribution / event_distribution)
                )
                class_iv[class_label] = contribution
                class_totals[class_label] += contribution
            rows.append(
                BinRow(
                    bin_id=str(bin_id),
                    label=str(label),
                    kind=str(kind),
                    rows=len(group),
                    population_share=len(group) / len(frame),
                    class_counts={key: int(value) for key, value in counts.items()},
                    class_rates={key: int(value) / len(group) for key, value in counts.items()},
                    class_iv=class_iv,
                )
            )
        values = list(class_totals.values())
        weighted = sum(class_totals[label] * int(totals[label]) for label in classes) / len(target)
        metrics.extend(
            [
                *[
                    BinningMetric(
                        name=f"one_vs_rest_iv:{label}",
                        value=value,
                        interpretation="multinomial_class_iv",
                    )
                    for label, value in class_totals.items()
                ],
                BinningMetric(
                    name="macro_one_vs_rest_iv",
                    value=float(np.mean(values)),
                    interpretation="multinomial_summary",
                ),
                BinningMetric(
                    name="weighted_one_vs_rest_iv",
                    value=weighted,
                    interpretation="multinomial_summary",
                ),
                BinningMetric(
                    name="maximum_one_vs_rest_iv",
                    value=max(values),
                    interpretation="multinomial_summary",
                ),
            ]
        )
    if definition.feature_type == "numeric":
        values = _apply_numeric_transform(
            _series(raw).reset_index(drop=True), definition.numeric_transform
        )
        profile = pd.DataFrame({"bin_id": assigned["bin_id"].astype(str), "value": values}).dropna(
            subset=["value"]
        )
        statistics = profile.groupby("bin_id", sort=False)["value"].agg(["min", "max", "mean"])
        for row in rows:
            if row.bin_id in statistics.index:
                row.feature_min = float(statistics.loc[row.bin_id, "min"])
                row.feature_max = float(statistics.loc[row.bin_id, "max"])
                row.feature_mean = float(statistics.loc[row.bin_id, "mean"])
    if definition.feature_type == "numeric" and definition.out_of_range_guard_bins:
        present = {row.bin_id for row in rows}
        for bin_id, label in (
            ("GUARD_LOW", "Below observed minimum"),
            ("GUARD_HIGH", "Above observed maximum"),
        ):
            if bin_id not in present:
                rows.append(
                    BinRow(
                        bin_id=bin_id,
                        label=label,
                        kind="guard",
                        rows=0,
                        population_share=0.0,
                        events=0 if target_type == "binary" else None,
                        non_events=0 if target_type == "binary" else None,
                        event_rate=None,
                    )
                )
    rows.sort(key=lambda row: _bin_order(row.bin_id))
    return rows, metrics


def _bin_order(bin_id: str) -> tuple[int, int]:
    if bin_id.startswith("R") and bin_id[1:].isdigit():
        return 0, int(bin_id[1:])
    if bin_id.startswith("S") and bin_id[1:].isdigit():
        return 1, int(bin_id[1:])
    order = {"MISSING": 2, "GUARD_LOW": 3, "GUARD_HIGH": 4, "UNSEEN": 5}
    return order.get(bin_id, 6), 0


def _special_mask(series: pd.Series, specials: dict[str, list[Any]]) -> pd.Series:
    mask = pd.Series(False, index=series.index)
    for codes in specials.values():
        for code in codes:
            mask |= _value_mask(series, code)
    return mask


def _value_mask(series: pd.Series, value: Any) -> pd.Series:
    direct = series.eq(value).fillna(False)
    if direct.any():
        return direct
    if pd.api.types.is_numeric_dtype(series):
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return direct
        return pd.to_numeric(series, errors="coerce").eq(numeric_value).fillna(False)
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(value, errors="coerce")
        if not pd.isna(parsed):
            return pd.to_datetime(series, errors="coerce").eq(parsed).fillna(False)
    return series.astype("string").eq(str(value)).fillna(False)


def _numeric_special_values(specials: dict[str, list[Any]]) -> dict[str, list[Any]]:
    normalized: dict[str, list[Any]] = {}
    for label, values in specials.items():
        normalized[label] = []
        for value in values:
            try:
                parsed = float(value)
                normalized[label].append(int(parsed) if parsed.is_integer() else parsed)
            except (TypeError, ValueError):
                normalized[label].append(value)
    return normalized


def _constraint_warnings(
    rows: list[BinRow], rules: BinningConstraints, target_type: TargetKind
) -> list[str]:
    all_regular = [row for row in rows if row.kind == "regular"]
    regular = [row for row in all_regular if row.rows > 0]
    warnings = []
    if not rules.min_bins <= len(all_regular) <= rules.max_bins:
        warnings.append(
            f"Regular-bin count {len(all_regular)} is outside the requested "
            f"range {rules.min_bins}–{rules.max_bins}."
        )
    for row in all_regular:
        if row.population_share + 1e-12 < rules.min_bin_size:
            warnings.append(
                f"Bin {row.label} has {row.population_share:.2%} of rows, below "
                f"the requested {rules.min_bin_size:.2%}."
            )
        if rules.max_bin_size is not None and row.population_share > rules.max_bin_size + 1e-12:
            warnings.append(
                f"Bin {row.label} has {row.population_share:.2%} of rows, above "
                f"the requested {rules.max_bin_size:.2%}."
            )
        if target_type == "binary":
            if (row.events or 0) < rules.min_event_count:
                warnings.append(f"Bin {row.label} has fewer than {rules.min_event_count} events.")
            if (row.non_events or 0) < rules.min_non_event_count:
                warnings.append(
                    f"Bin {row.label} has fewer than {rules.min_non_event_count} non-events."
                )
    outcome = [
        row.target_mean if target_type == "continuous" else row.event_rate for row in regular
    ]
    if target_type in {"binary", "continuous"} and len(outcome) > 1:
        assert all(value is not None for value in outcome)
        numeric = [float(value) for value in outcome if value is not None]
        pairs = list(zip(numeric[:-1], numeric[1:], strict=True))
        ascending = all(left <= right for left, right in pairs)
        descending = all(left >= right for left, right in pairs)
        expected = rules.monotonic_trend
        if expected == "ascending" and not ascending:
            warnings.append("Regular-bin target outcomes are not monotonically ascending.")
        elif expected == "descending" and not descending:
            warnings.append("Regular-bin target outcomes are not monotonically descending.")
    return warnings


def _series(values: pd.Series | Iterable[Any]) -> pd.Series:
    return values.copy() if isinstance(values, pd.Series) else pd.Series(list(values))


def _trend(value: str) -> str | None:
    return None if value == "none" else value


def _continuous_trend(value: str, max_prebins: int) -> str | None:
    """Select OptBinning's faster equivalent for large continuous-target searches."""
    if value == "none":
        return None
    if max_prebins > 20 and value in {"auto", "peak", "valley"}:
        return f"{value}_heuristic"
    return value


def _numeric_feature(x: pd.Series, regular: pd.Series) -> tuple[pd.Series, str]:
    if pd.api.types.is_datetime64_any_dtype(x):
        parsed = pd.to_datetime(x, errors="coerce")
        days = (parsed - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86_400
        return days.astype(float), "datetime_days"
    numeric = pd.to_numeric(x, errors="coerce")
    if not (regular & numeric.isna()).any():
        return numeric, "identity"
    parsed = pd.to_datetime(x, errors="coerce")
    if (regular & parsed.isna()).any():
        return numeric, "identity"
    days = (parsed - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86_400
    return days.astype(float), "datetime_days"


def _apply_numeric_transform(series: pd.Series, transform: str) -> pd.Series:
    if transform == "datetime_days":
        parsed = pd.to_datetime(series, errors="coerce")
        return ((parsed - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86_400).astype(float)
    return pd.to_numeric(series, errors="coerce")


def _interval_label(left: float, right: float, transform: str = "identity") -> str:
    if transform == "datetime_days":
        start = "-inf" if np.isneginf(left) else str(pd.Timestamp(left, unit="D").date())
        end = "inf" if np.isposinf(right) else str(pd.Timestamp(right, unit="D").date())
        return f"({start}, {end}]"
    start = "-inf" if np.isneginf(left) else f"{left:.12g}"
    end = "inf" if np.isposinf(right) else f"{right:.12g}"
    return f"({start}, {end}]"
