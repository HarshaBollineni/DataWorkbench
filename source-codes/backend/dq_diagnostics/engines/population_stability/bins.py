"""Reviewed-bin contracts and baseline-only deterministic draft generation."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

SCHEMA_VERSION = 1


def validate_bin_definition(value: dict[str, Any], *, require_frozen: bool = True) -> None:
    if not isinstance(value, dict) or value.get("payload_schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported PSI bin payload schema version")
    if require_frozen and value.get("governance_state") != "frozen":
        raise ValueError("PSI execution requires a reviewed frozen bin artifact")
    if value.get("kind") not in {"numeric", "date", "categorical", "boolean"}:
        raise ValueError("unsupported PSI bin feature type")
    if value.get("unseen_category_policy") not in {"unseen_bin", "error"}:
        raise ValueError("unseen_category_policy must be explicit")
    if value.get("missing_bin") is not True:
        raise ValueError("a missing-value bin is required")
    specials = value.get("special_value_bins")
    if not isinstance(specials, list):
        raise ValueError("special-value bins must be an explicit list")
    special_seen: set[str] = set()
    for special in specials:
        if not isinstance(special, dict) or not special.get("label") or not isinstance(special.get("values"), list):
            raise ValueError("special-value bins require a label and values")
        keys = {str(item) for item in special["values"]}
        if special_seen & keys: raise ValueError("special-value bins overlap")
        special_seen |= keys
    if value["kind"] in {"numeric", "date"}:
        boundaries = value.get("boundaries")
        if not isinstance(boundaries, list) or any(not isinstance(v, (int, float)) for v in boundaries):
            raise ValueError("numeric/date boundaries must be an ordered number list")
        if any(not math.isfinite(float(v)) for v in boundaries) or boundaries != sorted(set(boundaries)):
            raise ValueError("numeric/date boundaries must be finite, unique, and increasing")
        if value.get("boundary_semantics") != "right_closed":
            raise ValueError("numeric/date boundary semantics must be right_closed")
        if value.get("underflow_guard") is not True or value.get("overflow_guard") is not True:
            raise ValueError("numeric/date bins require underflow and overflow guards")
    else:
        groups = value.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("categorical bins require non-empty groups")
        seen: set[str] = set()
        for group in groups:
            values = group.get("values") if isinstance(group, dict) else None
            if not isinstance(values, list) or not values:
                raise ValueError("each categorical group requires values")
            keys = {str(v) for v in values}
            if seen & keys:
                raise ValueError("categorical bin groups overlap")
            seen |= keys


def create_draft_bins(series: pd.Series, feature: str, *, creator: str = "system",
                      source_population_fingerprint: str = "draft",
                      special_values: dict[str, list[Any]] | None = None) -> dict[str, Any]:
    configured_specials = special_values or {}
    special_mask = pd.Series(False, index=series.index)
    for values in configured_specials.values():
        special_mask |= series.isin(values) | series.astype("string").isin({str(value) for value in values})
    usable = series.loc[~special_mask].dropna()
    is_date = pd.api.types.is_datetime64_any_dtype(usable)
    is_numeric = pd.api.types.is_numeric_dtype(usable) and not pd.api.types.is_bool_dtype(usable)
    common = {
        "payload_schema_version": SCHEMA_VERSION, "governance_state": "draft",
        "feature": feature, "physical_type": str(series.dtype), "logical_type": None,
        "missing_bin": True, "special_value_bins": [
            {"label": label, "values": values} for label, values in sorted(configured_specials.items())
        ], "unseen_category_policy": "unseen_bin",
        "source_population_fingerprint": source_population_fingerprint,
        "creation_methodology": "baseline_only_deterministic_v1", "source_artifact_references": [],
        "creator": creator, "reviewer": None, "review_timestamp": None,
        "supersedes_artifact_id": None,
    }
    if is_numeric or is_date:
        comparable = usable.astype("int64") if is_date else pd.to_numeric(usable)
        distinct = sorted(set(comparable.tolist()))
        requested = min(10, len(distinct))
        if len(distinct) < 10:
            boundaries = [float(v) for v in distinct[:-1]]
            methodology = "baseline_unique_values"
        else:
            quantiles = comparable.quantile([i / 10 for i in range(1, 10)], interpolation="linear")
            boundaries = sorted({float(v) for v in quantiles.tolist() if pd.notna(v)})
            methodology = "baseline_quantiles"
        return {**common, "kind": "date" if is_date else "numeric", "boundaries": boundaries,
                "boundary_semantics": "right_closed", "underflow_guard": True, "overflow_guard": True,
                "requested_bin_count": requested, "actual_bin_count": len(boundaries) + 1,
                "creation_methodology": methodology}
    # The target-free PSI contract is deterministic: retain the 49 most
    # frequent Baseline values and place the remaining observed values in one
    # explicit Baseline OTHER bin.  Missing/special/unseen values are separate
    # guard bins and do not consume one of these 50 regular bins.
    counts = usable.astype("string").value_counts(dropna=False)
    ranked = sorted(((str(value), int(count)) for value, count in counts.items()),
                    key=lambda item: (-item[1], item[0]))
    values = [value for value, _count in ranked]
    groups = [{"label": value, "values": [value]} for value in values[:49]]
    if len(values) > 49:
        groups.append({"label": "OTHER_BASELINE", "values": values[49:]})
    return {**common, "kind": "boolean" if pd.api.types.is_bool_dtype(usable) else "categorical",
            "groups": groups,
            "requested_bin_count": min(len(values), 50), "actual_bin_count": len(groups),
            "boundary_semantics": "exact_match", "underflow_guard": False, "overflow_guard": False}
