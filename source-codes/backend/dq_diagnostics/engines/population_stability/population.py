"""Deterministic, auditable one-snapshot population predicates."""
from __future__ import annotations

from typing import Any

import pandas as pd

from analysis_runtime.contracts import stable_fingerprint


def _predicate(series: pd.Series, spec: dict[str, Any]) -> pd.Series:
    op = spec.get("operator")
    if op == "in":
        values = {str(v) for v in spec.get("values") or []}
        if not values:
            raise ValueError("categorical predicate requires at least one value")
        return series.astype("string").isin(values)
    if op in {"<", "<=", "=", ">=", ">"}:
        if "value" not in spec:
            raise ValueError("comparison predicate requires value")
        comparable, rhs = _ordered(series, spec["value"])
        if op == "<": return comparable < rhs
        if op == "<=": return comparable <= rhs
        if op == "=": return comparable == rhs
        if op == ">=": return comparable >= rhs
        return comparable > rhs
    if op == "range":
        low, high = spec.get("lower"), spec.get("upper")
        comparable, low = _ordered(series, low)
        _ignored, high = _ordered(series, high)
        if low is None or high is None or low > high:
            raise ValueError("range predicate requires ordered lower and upper values")
        left = comparable >= low if spec.get("lower_inclusive", True) else comparable > low
        right = comparable <= high if spec.get("upper_inclusive", True) else comparable < high
        return left & right
    raise ValueError("unsupported population predicate")


def _ordered(series: pd.Series, value: Any) -> tuple[pd.Series, Any]:
    if value is None:
        return series, value
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce"), pd.Timestamp(value)
    if pd.api.types.is_numeric_dtype(series):
        number = pd.to_numeric(pd.Series([value]), errors="raise").iloc[0]
        return series, number
    parsed = pd.to_datetime(series, errors="coerce")
    if int(series.notna().sum()) and float(parsed.notna().sum() / series.notna().sum()) >= 0.9:
        return parsed, pd.Timestamp(value)
    return series.astype("string"), str(value)


def split_population(frame: pd.DataFrame, split_feature: str, predicate: dict[str, Any], *,
                     null_policy: str = "baseline", special_values: list[Any] | None = None,
                     special_policy: str = "exclude") -> dict[str, Any]:
    if split_feature not in frame:
        raise ValueError(f"unknown split feature: {split_feature}")
    if null_policy not in {"exclude", "baseline", "current"}:
        raise ValueError("null_policy must be exclude, baseline, or current")
    if special_policy not in {"exclude", "baseline", "current"}:
        raise ValueError("special_policy must be exclude, baseline, or current")
    series = frame[split_feature]
    nulls = series.isna()
    specials = series.astype("string").isin({str(value) for value in (special_values or [])}) & ~nulls
    baseline_mask = _predicate(series, predicate).fillna(False) & ~nulls & ~specials
    current_mask = ~baseline_mask & ~nulls & ~specials
    if null_policy == "baseline": baseline_mask |= nulls
    elif null_policy == "current": current_mask |= nulls
    if special_policy == "baseline": baseline_mask |= specials
    elif special_policy == "current": current_mask |= specials
    if bool((baseline_mask & current_mask).any()):
        raise ValueError("baseline and current populations overlap")
    if not bool(baseline_mask.any()) or not bool(current_mask.any()):
        raise ValueError("baseline and current populations must both be non-empty")
    identity = {"split_feature": split_feature, "predicate": predicate, "null_policy": null_policy,
                "special_values": sorted(str(value) for value in (special_values or [])),
                "special_policy": special_policy,
                "baseline_rows": frame.index[baseline_mask].tolist(),
                "current_rows": frame.index[current_mask].tolist()}
    return {"baseline": frame.loc[baseline_mask].copy(), "current": frame.loc[current_mask].copy(),
            "baseline_count": int(baseline_mask.sum()), "current_count": int(current_mask.sum()),
            "excluded_count": int((~baseline_mask & ~current_mask).sum()), "null_count": int(nulls.sum()),
            "special_count": int(specials.sum()),
            "population_fingerprint": stable_fingerprint(identity)}
