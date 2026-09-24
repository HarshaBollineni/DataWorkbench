"""Read-only presentation of retained results; never infers a cause or reruns analysis."""
from __future__ import annotations

import math

# Explicit metric semantics, shared across diagnostics and report/UI consumers.
METRICS = {
    "physical_null_rate": ("Physical missing rate", "ratio"),
    "null_rate": ("Missing rate", "ratio"),
    "missing_rate": ("Missing rate", "ratio"),
    "outlier_rate": ("Outlier rate", "ratio"),
    "outlier_count": ("Outlier records", "count"),
    "duplicate_rate": ("Duplicate rate", "ratio"),
    "duplicated_rows": ("Records with duplicated keys", "count"),
    "duplicated_values": ("Duplicated key values", "count"),
    "distinct_count": ("Distinct values", "count"),
    "psi": ("Population stability index", "number"),
    "ks_statistic": ("KS statistic", "ratio"),
    "corr": ("Correlation", "correlation"),
    "mean": ("Mean", "number"),
    "numeric_parse_rate": ("Numeric parse rate", "ratio"),
    "date_parse_rate": ("Date parse rate", "ratio"),
}


def _valid(value, unit):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value)
            and (unit != "ratio" or 0 <= value <= 1)
            and (unit != "correlation" or -1 <= value <= 1)
            and (unit != "count" or value >= 0 and value == int(value)))


def present_execution(execution: dict) -> dict:
    summary = execution.get("summary_json") or {}
    runtime = summary.get("runtime") or {}
    result = summary.get("result") or runtime.get("result") or {}
    metrics = result.get("metrics") or {}
    cards = []
    reference = execution.get("execution_id")
    if runtime.get("ok") is not False:
        for key, (label, unit) in METRICS.items():
            card = None
            for before, after in (("baseline", "current"), ("early", "recent")):
                old = (metrics.get(before) or {}).get(key) if isinstance(metrics.get(before), dict) else metrics.get(f"{before}_{key}")
                new = (metrics.get(after) or {}).get(key) if isinstance(metrics.get(after), dict) else metrics.get(f"{after}_{key}")
                if _valid(old, unit) and _valid(new, unit):
                    card = {"label": label, "unit": unit, "value": new,
                            "value_label": after.title(),
                            "comparison": {"label": before.title(), "value": old}}
                    break
            if card is None and _valid(metrics.get(key), unit):
                card = {"label": label, "unit": unit, "value": metrics[key]}
            if card:
                cards.append({**card, "evidence_reference": reference})
    return {"version": 1, "summary": result.get("summary"),
            "metrics": cards[:4], "available_metric_count": len(cards),
            "evidence_reference": reference}
