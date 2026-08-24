"""Shared, deterministic implementation of the five Test Lab selection rules.

The planner calls this before a test's own selection logic.  It deliberately
uses the raw frame: inventory labels are useful hints, not a substitute for
the behavioural distinction between a calendar axis and a duration measure.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

_PERIOD_WORDS = ("date", "period", "quarter", "month", "year", "vintage", "snapshot", "as_of", "asof", "reporting")
_DURATION_WORDS = ("age", "tenure", "time_on_book", "months_on_book", "days_since", "duration", "remaining_term", "time_to")
_GRADE_WORDS = ("grade", "rating", "score", "band", "bucket", "scale", "stage")
_OUTCOME_WORDS = ("default", "event", "outcome", "status", "flag", "dpd", "delinq", "arrears", "bad")


def _words(name: str) -> str:
    return str(name or "").casefold().replace("-", "_").replace(" ", "_")


def is_constant(frame: pd.DataFrame, column: str) -> bool:
    return column in frame and frame[column].dropna().nunique() <= 1


def is_period_axis(frame: pd.DataFrame, row: dict[str, Any]) -> bool:
    """Identify calendar axes without treating durations as dates.

    A repeated / ordered calendar label is strong behavioural evidence.  A
    datetime/Date tag is only a weak hint, so a row-varying duration remains a
    feature even when it has an unfortunate type annotation.
    """
    column = row.get("column_name", "")
    if column not in frame:
        return False
    name = _words(column)
    description = _words(row.get("description", ""))
    text = f"{name}_{description}"
    if any(token in text for token in _DURATION_WORDS):
        return False
    series = frame[column].dropna()
    if series.empty:
        return False
    name_hint = any(token in text for token in _PERIOD_WORDS)
    tag_hint = row.get("classification") == "datetime" or row.get("role") == "Date"
    # Calendar axes repeat (or are monotone snapshots) and have substantially
    # fewer distinct values than rows.  This is intentionally conservative.
    repeated = series.nunique() < len(series)
    try:
        ordered = bool(series.is_monotonic_increasing or series.is_monotonic_decreasing)
    except TypeError:
        ordered = False
    return bool((name_hint or tag_hint) and (repeated or ordered))


def is_discrete_numeric(row: dict[str, Any]) -> bool:
    if row.get("classification") == "ordinal":
        return True
    text = _words(f"{row.get('column_name', '')}_{row.get('description', '')}")
    return any(token in text for token in _GRADE_WORDS)


def is_outcome_sibling(row: dict[str, Any], target: str | None) -> bool:
    column = str(row.get("column_name") or "")
    if target and column == target:
        return True
    if row.get("role") == "Target":
        return True
    text = _words(f"{column}_{row.get('description', '')}")
    return any(token in text for token in _OUTCOME_WORDS)


def filter_columns(test_name: str, frame: pd.DataFrame, inventory: list[dict], target: str | None = None) -> tuple[list[str], list[dict], list[str]]:
    """Return eligible columns, structured exclusions, and calendar references."""
    selected: list[str] = []
    excluded: list[dict] = []
    periods: list[str] = []
    target_referenced = test_name in {"Missingness-vs-target (MNAR) test", "Leakage detection (single-feature AUC)"}
    feature_level = test_name in {
        "PSI (Population Stability Index)", "MCAR test (Little's)",
        "Missingness-vs-target (MNAR) test", "Robust z-score outlier test (MAD)",
        "Tail analysis", "Leakage detection (single-feature AUC)",
        "Correlation stability analysis", "Drift decomposition",
    }
    for row in inventory:
        col = row.get("column_name")
        if col not in frame:
            continue
        if is_constant(frame, col):
            excluded.append({"column": col, "reason": "constant column - no variation to assess", "status": "not_runnable"})
            continue
        period = is_period_axis(frame, row)
        if period:
            periods.append(col)
        if feature_level and period:
            excluded.append({"column": col, "reason": "period column - offered as date/period reference", "status": "not_runnable"})
            continue
        if test_name in {"Robust z-score outlier test (MAD)", "Tail analysis"} and is_discrete_numeric(row):
            excluded.append({"column": col, "reason": "coded or ordinal-grade numeric - continuous-distribution test excluded", "status": "not_runnable"})
            continue
        if target_referenced and is_outcome_sibling(row, target):
            excluded.append({"column": col, "reason": "definitional sibling of the outcome", "status": "not_runnable"})
            continue
        selected.append(col)
    return selected, excluded, periods
