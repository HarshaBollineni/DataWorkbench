"""Pure PSI arithmetic. No routing, persistence, inference, or mutable data access."""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from .bins import validate_bin_definition

ENGINE_VERSION = "1.0.0"
DEFAULT_EPSILON = 1e-6
DEFAULT_THRESHOLDS = {"watch": 0.10, "investigate": 0.25}
_NUMBERED_BIN = re.compile(r"^bin_(\d+)$")


def _bin_sort_key(bin_id: str) -> tuple[int, int | str]:
    """Keep protected bins explicit and order numbered intervals numerically."""
    if bin_id == "missing":
        return (0, 0)
    if bin_id == "unseen":
        return (0, 1)
    numbered = _NUMBERED_BIN.fullmatch(bin_id)
    if numbered:
        return (1, int(numbered.group(1)))
    return (2, bin_id)


def bin_display_labels(bins: dict[str, Any]) -> dict[str, str]:
    """Human-readable labels without changing the stable reconciliation IDs."""
    labels = {"missing": "Missing", "unseen": "Unseen values"}
    for special in bins.get("special_value_bins") or []:
        values = ", ".join(str(value) for value in special.get("values") or [])
        labels[f"special:{special['label']}"] = f"{special['label']}: {values}" if values else str(special["label"])
    if bins.get("kind") in {"numeric", "date"}:
        boundaries = bins.get("boundaries") or []
        for index in range(len(boundaries) + 1):
            lower = "−∞" if index == 0 else str(boundaries[index - 1])
            upper = "+∞" if index == len(boundaries) else str(boundaries[index])
            labels[f"bin_{index + 1}"] = f"({lower}, {upper}]"
    else:
        for index, group in enumerate(bins.get("groups") or []):
            bin_id = str(group.get("label") or f"bin_{index + 1}")
            values = ", ".join(str(value) for value in group.get("values") or [])
            labels[bin_id] = f"{bin_id}: {values}" if values else bin_id
    return labels


def _assign(series: pd.Series, bins: dict[str, Any]) -> pd.Series:
    result = pd.Series(index=series.index, dtype="string")
    result.loc[series.isna()] = "missing"
    for special in bins.get("special_value_bins") or []:
        mask = series.astype("string").isin({str(value) for value in special["values"]}) & result.isna()
        result.loc[mask] = f"special:{special['label']}"
    usable = series.notna() & result.isna()
    if bins["kind"] in {"numeric", "date"}:
        values = pd.to_datetime(series, errors="coerce").astype("int64") if bins["kind"] == "date" else pd.to_numeric(series, errors="coerce")
        labels = [f"bin_{i + 1}" for i in range(len(bins["boundaries"]) + 1)]
        result.loc[usable] = pd.cut(values.loc[usable], [-math.inf, *bins["boundaries"], math.inf],
                                    labels=labels, right=True, include_lowest=True).astype("string")
    else:
        lookup = {str(value): str(group.get("label") or f"bin_{idx + 1}")
                  for idx, group in enumerate(bins["groups"]) for value in group["values"]}
        mapped = series.loc[usable].astype("string").map(lookup)
        unseen = mapped.isna()
        if unseen.any() and bins["unseen_category_policy"] == "error":
            raise ValueError("current population contains unseen categories")
        mapped.loc[unseen] = "unseen"
        result.loc[usable] = mapped
    if result.isna().any():
        raise ValueError("every usable row must reconcile to exactly one PSI bin")
    return result


def classify(psi: float, thresholds: dict[str, float]) -> str:
    if psi >= thresholds["investigate"]: return "investigate"
    if psi >= thresholds["watch"]: return "watch"
    return "stable"


def calculate_feature_psi(baseline: pd.Series, current: pd.Series, bins: dict[str, Any], *,
                          epsilon: float = DEFAULT_EPSILON,
                          thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    validate_bin_definition(bins)
    thresholds = dict(DEFAULT_THRESHOLDS if thresholds is None else thresholds)
    if not 0 < epsilon < 1: raise ValueError("epsilon must be between zero and one")
    if not 0 <= thresholds.get("watch", -1) < thresholds.get("investigate", -1):
        raise ValueError("PSI thresholds must satisfy 0 <= watch < investigate")
    if len(baseline) == 0 or len(current) == 0:
        raise ValueError("baseline and current populations must both be non-empty")
    left, right = _assign(baseline, bins), _assign(current, bins)
    display_labels = bin_display_labels(bins)
    labels = sorted(set(left.tolist()) | set(right.tolist()), key=_bin_sort_key)
    rows, total = [], 0.0
    for label in labels:
        bc, cc = int((left == label).sum()), int((right == label).sum())
        bp, cp = bc / len(left), cc / len(right)
        if bp == 0 and cp == 0:
            contribution = 0.0
        else:
            smooth_b, smooth_c = max(bp, epsilon), max(cp, epsilon)
            contribution = (smooth_c - smooth_b) * math.log(smooth_c / smooth_b)
        if not math.isfinite(contribution): raise ValueError("non-finite PSI contribution")
        total += contribution
        rows.append({"bin": label, "bin_label": display_labels.get(label, label),
                     "baseline_count": bc, "current_count": cc,
                     "baseline_proportion": bp, "current_proportion": cp,
                     "contribution": contribution})
    if sum(row["baseline_count"] for row in rows) != len(baseline) or sum(row["current_count"] for row in rows) != len(current):
        raise ValueError("PSI bin counts do not reconcile to population counts")
    if not math.isfinite(total) or abs(total - sum(row["contribution"] for row in rows)) > 1e-12:
        raise ValueError("PSI contributions do not reconcile")
    return {"status": "complete", "psi": total, "classification": classify(total, thresholds),
            "decision_type": "contextual", "review_state": "open", "is_violation": False,
            "epsilon": epsilon, "thresholds": thresholds, "bins": rows,
            "baseline_count": len(baseline), "current_count": len(current),
            "engine_version": ENGINE_VERSION, "methodology": "frozen_bins_psi_v1"}
