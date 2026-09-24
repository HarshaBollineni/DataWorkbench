"""Governed shallow-tree discovery for tabular RCA driver candidates."""
from __future__ import annotations
from domains.rca import progress

import math
import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier

from domains.rca import feature_states


SUPPORTED_TARGET_MODES = {"population_membership", "missingness", "psi_bin_membership"}
_IDENTIFIER_TOKENS = ("id", "uuid", "guid", "row_number", "row_num", "index")
_DIRECT_TARGET_SUFFIXES = ("missing", "missing_flag", "is_missing", "null_flag", "complete_flag")


def validate_target_spec(frame: pd.DataFrame, spec: dict[str, Any],
                         population_context: dict[str, Any] | None = None,
                         diagnostic: dict[str, Any] | None = None,
                         psi_bin_definition: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a target and build the broad deterministic predictor universe.

    The model may rank useful columns, but it cannot remove otherwise eligible
    evidence. Exclusions are owned by deterministic leakage and cardinality
    rules so the discovery search is not silently narrowed by prose judgment.
    """
    mode = str(spec.get("target_mode") or "")
    diagnostic = diagnostic or {}
    diagnostic_bins = diagnostic.get("bins") or []
    if diagnostic.get("kind") == "population_stability_index" and diagnostic_bins:
        dominant = max(
            diagnostic_bins,
            key=lambda row: abs(float(row.get("contribution") or 0)),
        )
        target_bin_id = str(dominant.get("bin") or "")
        mode = "missingness" if target_bin_id == "missing" else "psi_bin_membership"
        spec = {**spec, "target_mode": mode, "target_bin_id": target_bin_id}
    if mode not in SUPPORTED_TARGET_MODES:
        raise ValueError(f"Unsupported driver-search target mode: {mode or 'missing'}")
    affected = str(spec.get("affected_column") or "")
    if not affected or affected not in frame.columns:
        raise ValueError("The driver-search affected column is not available in the retained table.")
    definition = (population_context or {}).get("definition") or {}
    split_feature = str(definition.get("split_feature") or "")
    if mode == "population_membership" and definition.get("method") != "split_snapshot":
        raise ValueError("Population-membership search requires the frozen split_snapshot definition.")
    if mode == "psi_bin_membership":
        target_bin = str(spec.get("target_bin_id") or "")
        retained_bins = diagnostic_bins
        if not target_bin or not any(str(row.get("bin")) == target_bin for row in retained_bins):
            raise ValueError("PSI-bin search requires a bin ID from retained diagnostic evidence.")
        if not psi_bin_definition:
            raise ValueError("PSI-bin search requires the exact frozen bin definition.")

    requested = [str(value) for value in spec.get("candidate_columns") or []]
    requested.extend(str(column) for column in frame.columns if str(column) not in requested)
    excluded = {affected}
    if mode == "population_membership" and split_feature:
        excluded.add(split_feature)
    candidates, rejected = [], []
    for column in requested:
        lowered = column.lower()
        identifier = any(
            lowered == token or lowered.endswith(f"_{token}") or lowered.startswith(f"{token}_")
            for token in _IDENTIFIER_TOKENS
        )
        affected_indicator = any(
            lowered in {f"{affected.lower()}_{suffix}", f"{suffix}_{affected.lower()}"}
            for suffix in _DIRECT_TARGET_SUFFIXES
        )
        if column not in frame.columns or column in excluded or identifier or affected_indicator:
            rejected.append(column)
        elif column not in candidates:
            candidates.append(column)
    if not candidates:
        raise ValueError("No eligible non-leaking predictor columns remain for driver search.")
    retained = candidates[:50]
    return {**spec, "target_mode": mode, "affected_column": affected,
            "candidate_columns": retained, "rejected_columns": sorted(set(rejected)),
            "excluded_columns": sorted(value for value in excluded if value),
            "llm_proposed_exclusions": list(spec.get("excluded_columns") or []),
            "candidate_limit_omissions": candidates[50:]}


def _declared_special_mask(series: pd.Series, values: list[Any]) -> pd.Series:
    mask = pd.Series(False, index=series.index)
    text = series.astype("string").str.strip()
    numeric = pd.to_numeric(series, errors="coerce")
    for raw in values:
        mask |= text.eq(str(raw).strip()).fillna(False)
        try:
            mask |= numeric.eq(float(raw)).fillna(False)
        except (TypeError, ValueError):
            pass
    return mask & ~series.isna()


def _target(frame: pd.DataFrame, spec: dict[str, Any], population_context: dict[str, Any],
            declared_special_values: list[Any],
            psi_bin_definition: dict[str, Any] | None = None,
            diagnostic: dict[str, Any] | None = None,
            ) -> tuple[pd.DataFrame, pd.Series, pd.Series | None]:
    from domains.test_lab.diagnostics.t4_d14_population_stability.population import split_population

    populations = None
    if (population_context.get("definition") or {}).get("method") == "split_snapshot":
        definition = population_context["definition"]
        populations = split_population(
            frame, definition.get("split_feature"), definition.get("expression") or {},
            null_policy=definition.get("null_policy", "baseline"),
            special_values=definition.get("special_values") or [],
            special_policy=definition.get("special_policy", "exclude"),
        )
    if spec["target_mode"] in {"missingness", "psi_bin_membership"}:
        retained = (pd.concat([populations["baseline"], populations["current"]], axis=0)
                    if populations else frame.copy())
        affected = retained[spec["affected_column"]]
        if spec["target_mode"] == "missingness":
            # The PSI Missing bin is physical nullness. Completeness targets
            # retain the governed broader definition including declared codes.
            target = (affected.isna() if (diagnostic or {}).get("kind") == "population_stability_index"
                      else affected.isna() | _declared_special_mask(
                          affected, declared_special_values
                      ))
        else:
            from domains.test_lab.diagnostics.t4_d14_population_stability.engine import assign_feature_bins
            target = assign_feature_bins(affected, psi_bin_definition or {}).eq(spec["target_bin_id"])
        labels = None
        if populations:
            labels = pd.Series("baseline", index=populations["baseline"].index, dtype="string")
            labels = pd.concat([
                labels,
                pd.Series("current", index=populations["current"].index, dtype="string"),
            ])
            labels = labels.reindex(retained.index)
        return retained, target.astype(int), labels
    assert populations is not None
    retained = pd.concat([populations["baseline"], populations["current"]], axis=0)
    target = pd.Series(0, index=retained.index, dtype=int)
    target.loc[populations["current"].index] = 1
    return retained, target, None


def _encode(frame: pd.DataFrame, columns: list[str], feature_state_snapshot: dict[str, Any]
            ) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    encoded: dict[str, pd.Series] = {}
    feature_map: dict[str, str] = {}
    rejected: list[str] = []
    for column in columns:
        series = frame[column]
        governance = feature_states.column_governance(feature_state_snapshot, column)
        states = feature_states.classify(series, governance)
        regular = states["regular"]

        def add_state_indicators() -> None:
            physical_name = f"{column} physical missing"
            encoded[physical_name] = states["physical_missing"].astype(float)
            feature_map[physical_name] = column
            for raw, mask in states["specials"]:
                name = f"{column} special: {raw}"
                encoded[name] = mask.astype(float)
                feature_map[name] = column

        add_state_indicators()
        if "quarter" in column.lower():
            quarter_parts = series.where(regular).astype("string").str.extract(
                r"(?P<year>\d{4})\D*[Qq](?P<quarter>[1-4])"
            )
            regular_count = int(regular.sum())
            if regular_count and float(quarter_parts["year"].notna().sum() / regular_count) >= 0.8:
                for suffix in ("year", "quarter"):
                    name = f"{column} regular {suffix}"
                    encoded[name] = pd.to_numeric(
                        quarter_parts[suffix], errors="coerce"
                    ).fillna(float(pd.to_numeric(quarter_parts[suffix], errors="coerce").median())).astype(float)
                    feature_map[name] = column
                continue
        regular_series = series.where(regular)
        numeric = pd.to_numeric(regular_series, errors="coerce")
        regular_count = int(regular.sum())
        numeric_rate = float(numeric.notna().sum() / regular_count) if regular_count else 0.0
        date_named = any(token in column.lower() for token in ("date", "time", "quarter", "month"))
        parsed = pd.to_datetime(regular_series, errors="coerce") if date_named else None
        date_rate = (float(parsed.notna().sum() / regular_count)
                     if parsed is not None and regular_count else 0.0)
        if date_named and date_rate >= 0.8:
            for suffix, values in (("year", parsed.dt.year), ("quarter", parsed.dt.quarter)):
                name = f"{column} regular {suffix}"
                encoded[name] = values.fillna(float(values.median())).astype(float)
                feature_map[name] = column
        elif numeric_rate >= 0.9:
            name = f"{column} regular"
            median = float(numeric.median()) if numeric.notna().any() else 0.0
            encoded[name] = numeric.fillna(median).astype(float)
            feature_map[name] = column
        else:
            text = regular_series.astype("string")
            regular_text = text.loc[regular]
            distinct = int(regular_text.nunique(dropna=False))
            if distinct > min(50, max(12, math.ceil(len(text) * 0.2))):
                rejected.append(column)
                continue
            top = set(regular_text.value_counts().head(20).index.astype(str))
            bounded = regular_text.map(lambda value: str(value) if str(value) in top else "<other>")
            for value in sorted(bounded.dropna().unique(), key=lambda item: _natural_key(str(item))):
                name = f"{column} regular: {value}"
                encoded[name] = pd.Series(False, index=frame.index).where(
                    ~regular, bounded.eq(value)
                ).fillna(False).astype(float)
                feature_map[name] = column
    if not encoded:
        raise ValueError("Eligible predictors could not be encoded into bounded numerical features.")
    return pd.DataFrame(encoded, index=frame.index), feature_map, rejected


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.lower()
                 for part in re.split(r"(\d+)", value))


def _feature_segments(frame: pd.DataFrame, target: pd.Series, column: str | None,
                      snapshot: dict[str, Any], model: DecisionTreeClassifier,
                      encoded_names: list[str], feature_map: dict[str, str]
                      ) -> list[dict[str, Any]]:
    """Return mutually exclusive, human-readable states for the leading feature."""
    if not column or column not in frame.columns:
        return []
    governance = feature_states.column_governance(snapshot, column)
    states = feature_states.classify(frame[column], governance)
    numeric = pd.to_numeric(frame[column].where(states["regular"]), errors="coerce")
    threshold = None
    for node_feature, node_threshold in zip(model.tree_.feature, model.tree_.threshold):
        if node_feature < 0:
            continue
        name = encoded_names[node_feature]
        if feature_map.get(name) == column and name == f"{column} regular":
            threshold = float(node_threshold)
            break

    masks: list[tuple[str, pd.Series]] = []
    if threshold is not None:
        masks.extend([
            (f"{column} regular <= {threshold:.4g}", states["regular"] & numeric.le(threshold)),
            (f"{column} regular > {threshold:.4g}", states["regular"] & numeric.gt(threshold)),
        ])
    else:
        masks.append((f"{column} regular", states["regular"]))
    masks.append((f"{column} physical missing", states["physical_missing"]))
    masks.extend((f"{column} special: {raw}", mask) for raw, mask in states["specials"])
    rows = []
    for label, mask in masks:
        count = int(mask.sum())
        rows.append({
            "segment": label, "rows": count,
            "positive_rows": int(target.loc[mask].sum()),
            "positive_rate": float(target.loc[mask].mean()) if count else None,
        })
    return rows


def _positive_paths(model: DecisionTreeClassifier, names: list[str], feature_map: dict[str, str],
                    validation: pd.DataFrame, target: pd.Series,
                    baseline_rate: float) -> list[dict[str, Any]]:
    paths: dict[int, list[str]] = {}

    def walk(node: int, conditions: list[str]) -> None:
        tree = model.tree_
        if tree.children_left[node] == tree.children_right[node]:
            paths[node] = conditions
            return
        name = names[tree.feature[node]]
        threshold = float(tree.threshold[node])
        if (" physical missing" in name or " special: " in name) and 0 <= threshold <= 1:
            walk(tree.children_left[node], conditions + [f"not {name}"])
            walk(tree.children_right[node], conditions + [name])
        else:
            walk(tree.children_left[node], conditions + [f"{name} <= {threshold:.4g}"])
            walk(tree.children_right[node], conditions + [f"{name} > {threshold:.4g}"])

    walk(0, [])
    leaves = model.apply(validation)
    rows = []
    for node, conditions in paths.items():
        selected = leaves == node
        count = int(selected.sum())
        if not count:
            continue
        rate = float(target.iloc[np.flatnonzero(selected)].mean())
        if rate <= baseline_rate:
            continue
        rows.append({
            "_leaf_node": node,
            "rule": " AND ".join(conditions) or "all rows",
            "source_features": ", ".join(dict.fromkeys(
                feature_map.get(condition.split(" ", 1)[0], condition.split(" ", 1)[0])
                for condition in conditions
            )),
            "validation_rows": count,
            "positive_count": int(target.iloc[np.flatnonzero(selected)].sum()),
            "positive_rate": rate,
            "baseline_positive_rate": baseline_rate,
            "lift": rate / baseline_rate if baseline_rate else None,
        })
    rows.sort(key=lambda row: (float(row.get("lift") or 0), row["validation_rows"]), reverse=True)
    return rows[:8]


def _psi_attribution(*, model: DecisionTreeClassifier, encoded: pd.DataFrame,
                     target: pd.Series, population: pd.Series | None,
                     rules: list[dict[str, Any]], spec: dict[str, Any],
                     diagnostic: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Connect the leading separating rule to measured baseline/current PSI movement."""
    if population is None or not rules:
        return None, []
    selected_rule = rules[0]
    leaf = int(selected_rule["_leaf_node"])
    selected = model.apply(encoded) == leaf
    rows = []
    population_totals = population.value_counts().to_dict()
    for name in ("baseline", "current"):
        pop_mask = population.eq(name).to_numpy()
        segment_mask = pop_mask & selected
        segment_rows = int(segment_mask.sum())
        symptom_count = int(target.iloc[np.flatnonzero(segment_mask)].sum())
        rows.append({
            "population": name,
            "driver_segment": selected_rule["rule"],
            "population_rows": int(population_totals.get(name, 0)),
            "segment_rows": segment_rows,
            "symptom_count": symptom_count,
            "symptom_rate_within_segment": symptom_count / segment_rows if segment_rows else None,
            "symptom_population_share": (
                symptom_count / population_totals[name] if population_totals.get(name) else None
            ),
        })
    by_population = {row["population"]: row for row in rows}
    baseline = by_population["baseline"]
    current = by_population["current"]
    segment_delta = float(current["symptom_population_share"] or 0) - float(
        baseline["symptom_population_share"] or 0
    )
    overall = {}
    for name in ("baseline", "current"):
        mask = population.eq(name).to_numpy()
        overall[name] = float(target.iloc[np.flatnonzero(mask)].mean()) if mask.any() else 0.0
    overall_delta = overall["current"] - overall["baseline"]
    retained_bin = next((row for row in diagnostic.get("bins") or []
                         if str(row.get("bin")) == str(spec.get("target_bin_id"))), None)
    if spec["target_mode"] == "missingness" and retained_bin is None:
        retained_bin = next((row for row in diagnostic.get("bins") or []
                             if str(row.get("bin")).lower() == "missing"), None)
    total_abs_psi = sum(abs(float(row.get("contribution") or 0))
                        for row in diagnostic.get("bins") or [])
    psi_contribution = float((retained_bin or {}).get("contribution") or 0)
    impact = {
        "target_label": ((retained_bin or {}).get("bin_label")
                         or spec.get("positive_class_definition") or "diagnostic symptom"),
        "driver_segment": selected_rule["rule"],
        "source_features": selected_rule.get("source_features"),
        "baseline_symptom_rate": overall["baseline"],
        "current_symptom_rate": overall["current"],
        "observed_rate_change": overall_delta,
        "segment_rate_change_contribution": segment_delta,
        "segment_share_of_absolute_movement": (
            abs(segment_delta) / abs(overall_delta) if overall_delta else None
        ),
        "psi": diagnostic.get("psi"),
        "target_bin_psi_contribution": psi_contribution,
        "target_bin_share_of_absolute_psi": (
            abs(psi_contribution) / total_abs_psi if total_abs_psi else None
        ),
    }
    return impact, rows


@progress.phase("Discovering drivers")
def run_driver_search(frame: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Run a deterministic shallow tree and return candidate separating rules."""
    context = params.get("population_context") or {}
    diagnostic = params.get("diagnostic") or {}
    psi_bin_definition = params.get("psi_bin_definition") or None
    spec = validate_target_spec(
        frame, params.get("target_spec") or {}, context, diagnostic, psi_bin_definition
    )
    feature_state_snapshot = params.get("feature_state_snapshot") or {}
    affected_governance = feature_states.column_governance(
        feature_state_snapshot, spec["affected_column"]
    )
    affected_specials = affected_governance["confirmed_special_values"]
    if not (feature_state_snapshot.get("columns") or {}):
        # Compatibility for cases created before the per-column snapshot existed.
        affected_specials = list(params.get("declared_special_values") or [])
    retained, target, population = _target(
        frame, spec, context, affected_specials,
        psi_bin_definition, diagnostic,
    )
    counts = target.value_counts().to_dict()
    if len(counts) != 2 or min(counts.values()) < 20:
        raise ValueError("Driver-search target requires two classes with at least 20 rows each.")
    encoded, feature_map, encoding_rejected = _encode(
        retained, spec["candidate_columns"], feature_state_snapshot
    )
    indices = np.arange(len(encoded))
    train_idx, validation_idx = train_test_split(
        indices, test_size=0.3, random_state=41, stratify=target.to_numpy(),
    )
    min_leaf = max(20, math.ceil(len(train_idx) * 0.02))
    model = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=min_leaf, criterion="gini", random_state=41,
    )
    model.fit(encoded.iloc[train_idx], target.iloc[train_idx])
    probability = model.predict_proba(encoded.iloc[validation_idx])[:, 1]
    prediction = (probability >= 0.5).astype(int)
    validation_target = target.iloc[validation_idx].reset_index(drop=True)
    importances: dict[str, float] = {}
    for name, importance in zip(encoded.columns, model.feature_importances_):
        source = feature_map[name]
        importances[source] = importances.get(source, 0.0) + float(importance)
    ranked = sorted(importances.items(), key=lambda item: (-item[1], _natural_key(item[0])))
    baseline_rate = float(validation_target.mean())
    rules = _positive_paths(
        model, list(encoded.columns), feature_map, encoded.iloc[validation_idx],
        validation_target, baseline_rate,
    )
    auc = float(roc_auc_score(validation_target, probability))
    balanced = float(balanced_accuracy_score(validation_target, prediction))
    top_feature = ranked[0][0] if ranked and ranked[0][1] > 0 else None
    state_segments = _feature_segments(
        retained, target, top_feature, feature_state_snapshot, model,
        list(encoded.columns), feature_map,
    )
    importance_rows = [
        {"rank": rank, "feature": feature, "importance": importance}
        for rank, (feature, importance) in enumerate(ranked[:8], start=1)
        if importance > 0
    ]
    psi_impact, attribution_rows = _psi_attribution(
        model=model, encoded=encoded, target=target, population=population,
        rules=rules, spec=spec, diagnostic=diagnostic,
    )
    public_rules = [{key: value for key, value in rule.items() if key != "_leaf_node"}
                    for rule in rules]
    if psi_impact:
        movement_share = psi_impact["segment_share_of_absolute_movement"]
        psi_share = psi_impact["target_bin_share_of_absolute_psi"]
        summary = (
            f"{psi_impact['target_label']} changed from "
            f"{psi_impact['baseline_symptom_rate']:.2%} in baseline to "
            f"{psi_impact['current_symptom_rate']:.2%} in current and contributed "
            f"{psi_share:.1%} of absolute PSI. "
            f"The leading segment ({psi_impact['driver_segment']}) accounts for "
            f"{movement_share:.1%} of the absolute "
            "target-bin movement."
        ) if movement_share is not None and psi_share is not None else (
            f"{psi_impact['target_label']} changed from "
            f"{psi_impact['baseline_symptom_rate']:.2%} in baseline to "
            f"{psi_impact['current_symptom_rate']:.2%} in current. The leading segment "
            f"is {psi_impact['driver_segment']}; confirmatory testing is required."
        )
    else:
        summary = (
            f"The governed symptom search found {top_feature} as the strongest associated input. "
            "A confirmatory analysis is required before treating it as a root cause."
            if top_feature else
            "The governed symptom search found no material separating input."
        )
    return {
        "summary": summary,
        "metrics": {
            "target_mode": spec["target_mode"], "rows": int(len(encoded)),
            "positive_rows": int(target.sum()), "baseline_positive_rate": float(target.mean()),
            "validation_auc": auc, "validation_balanced_accuracy": balanced,
            "top_feature": top_feature, "tree_depth": int(model.get_depth()),
            "minimum_leaf_rows": min_leaf,
        },
        "psi_impact": psi_impact,
        "feature_state_segments": state_segments,
        "evidence_rows": attribution_rows[:12] if attribution_rows else public_rules[:12],
        "model_validation": {
            "validation_auc": auc, "validation_balanced_accuracy": balanced,
            "tree_depth": int(model.get_depth()), "minimum_leaf_rows": min_leaf,
            "feature_importance": importance_rows, "separating_rules": public_rules,
        },
        "interpretation_hints": [
            "Separation identifies an associated driver candidate, not a proven root cause.",
            "A focused hypothesis should confirm whether conditioning on the split attenuates the diagnostic symptom.",
        ],
        "recommended_followups": [
            f"Form a focused hypothesis around {top_feature} and confirm symptom attenuation within comparable groups."
            if top_feature else "Continue with the existing hypothesis-driven investigation."
        ],
        "target_spec": spec,
        "feature_state_reconciliation": feature_states.reconciliation(
            retained, feature_state_snapshot, spec["candidate_columns"]
        ),
        "rejected_predictors": sorted(set(spec.get("rejected_columns") or []) | set(encoding_rejected)),
    }


__all__ = ["SUPPORTED_TARGET_MODES", "run_driver_search", "validate_target_spec"]
