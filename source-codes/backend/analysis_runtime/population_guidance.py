"""Reusable, artifact-led controls for defining two complementary populations.

The profile artifact suggests an interaction and useful defaults. Exact values
and preview counts always come from the immutable snapshot selected by the
calling workflow (PSI today; RCA and other analyses can reuse this contract).
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value.item() if hasattr(value, "item") else value


def _histogram_suggestions(histogram: list[dict[str, Any]]) -> list[Any]:
    total = sum(int(row.get("count") or 0) for row in histogram)
    if not total:
        return []
    suggestions, cumulative = [], 0
    targets = iter((0.25, 0.5, 0.75)); target = next(targets, None)
    for row in histogram[:-1]:
        cumulative += int(row.get("count") or 0)
        while target is not None and cumulative / total >= target:
            suggestions.append(_json_value(row.get("end")))
            target = next(targets, None)
    return list(dict.fromkeys(value for value in suggestions if value is not None))


def guidance_from_profile(*, feature: str, feature_kind: str, distinct_count: int,
                          profile: dict[str, Any] | None,
                          artifact_id: str | None) -> dict[str, Any]:
    profile = dict(profile or {})
    top_k = profile.get("top_k") or {}
    if feature_kind == "date":
        strategy = "temporal_cutoff"
        suggestions = [value for value in (profile.get("min"), profile.get("max")) if value is not None]
        recommendation = "Use a chronological cutoff so baseline precedes target."
    elif feature_kind in {"categorical", "boolean"} or distinct_count <= 12:
        strategy = "category_groups"
        suggestions = list(top_k)
        recommendation = "Select explicit baseline values; current is the complement."
    else:
        strategy = "numeric_cutoff"
        suggestions = _histogram_suggestions(profile.get("histogram") or [])
        recommendation = "Choose a profile-informed cutoff or define a bounded baseline range."
    return {
        "feature": feature, "feature_kind": feature_kind, "strategy": strategy,
        "recommended_strategy": strategy, "recommendation": recommendation,
        "distinct_count": int(profile.get("distinct_count") or distinct_count or 0),
        "null_count": int(profile.get("null_count") or 0),
        "min": profile.get("min"), "max": profile.get("max"),
        "histogram": profile.get("histogram") or [],
        "profile_values": [{"value": str(value), "count": int(count)} for value, count in top_k.items()],
        "profile_values_complete": bool(top_k) and int(profile.get("distinct_count") or distinct_count or 0) <= len(top_k),
        "suggestions": suggestions,
        "source": {"kind": "column_profile_artifact" if artifact_id else "inventory_profile_fallback",
                   "artifact_id": artifact_id, "values_may_be_truncated": bool(top_k) and
                   int(profile.get("distinct_count") or distinct_count or 0) > len(top_k)},
    }


def exact_population_options(series: pd.Series, guidance: dict[str, Any], *,
                             special_values: list[Any] | None = None,
                             limit: int = 200) -> dict[str, Any]:
    """Bounded exact options from one immutable snapshot, never comparison data."""
    specials = {str(value) for value in (special_values or [])}
    clean = series.dropna()
    strategy = guidance["strategy"]
    result = {**guidance, "lookup_source": "immutable_snapshot_exact_scan",
              "lookup_limit": limit, "special_values": sorted(specials),
              "total_count": int(len(series)), "non_null_count": int(clean.size)}
    if strategy == "numeric_cutoff":
        numeric = pd.to_numeric(clean, errors="coerce").dropna()
        result["suggestions"] = list(dict.fromkeys(_json_value(value) for value in
            numeric.quantile([0.25, 0.5, 0.75], interpolation="linear").tolist())) if not numeric.empty else []
        result["exact_values"] = []
        result["exact_values_complete"] = False
        return result
    if strategy == "temporal_cutoff":
        parsed = pd.to_datetime(clean, errors="coerce")
        if not clean.empty and float(parsed.notna().mean()) >= 0.9:
            valid = parsed.dropna().sort_values()
            median = valid.iloc[len(valid) // 2]
            result["suggestions"] = [median.isoformat()]
            result["input_kind"] = "date"
        else:
            values = sorted({str(value) for value in clean})
            result["suggestions"] = [values[len(values) // 2]] if values else []
            result["input_kind"] = "period"
        result["exact_values"] = []
        result["exact_values_complete"] = False
        return result
    counts = clean.astype("string").value_counts(dropna=True)
    counts = counts[~counts.index.astype(str).isin(specials)]
    total_distinct = int(len(counts))
    shown = counts.head(limit)
    # Low-cardinality numeric values (notably year columns classified as
    # categorical) should read in their natural minimum-to-maximum order,
    # rather than value_counts' frequency order.
    numeric_index = pd.to_numeric(pd.Series(shown.index.astype(str)), errors="coerce")
    if not shown.empty and bool(numeric_index.notna().all()):
        ordered = sorted(shown.items(), key=lambda item: float(item[0]))
    else:
        ordered = list(shown.items())
    denominator = max(int(len(series)), 1)
    result["exact_values"] = [{"value": str(value), "count": int(count),
        "share": round(int(count) / denominator, 6)} for value, count in ordered]
    result["exact_values_complete"] = total_distinct <= limit
    result["distinct_count"] = total_distinct
    return result
