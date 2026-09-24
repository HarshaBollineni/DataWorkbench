"""Deterministic governed feature-state handling for RCA analyses."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd


SNAPSHOT_VERSION = 1


def snapshot_from_inventory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze confirmed and proposed special-value governance per column."""
    columns: dict[str, dict[str, Any]] = {}
    for row in rows:
        column = str(row.get("column_name") or "")
        if not column:
            continue
        proposed = list(row.get("missing_value_codes_json")
                        or row.get("missing_value_codes") or [])
        confirmed = bool(row.get("missing_codes_confirmed"))
        columns[column] = {
            "special_values_confirmed": confirmed,
            "confirmed_special_values": proposed if confirmed else [],
            "proposed_special_values": [] if confirmed else proposed,
        }
    return {"schema_version": SNAPSHOT_VERSION, "columns": columns}


def column_governance(snapshot: dict[str, Any] | None, column: str) -> dict[str, Any]:
    """Return a compatibility-safe governance record for one column."""
    raw = ((snapshot or {}).get("columns") or {}).get(column) or {}
    confirmed = bool(raw.get("special_values_confirmed"))
    confirmed_values = list(raw.get("confirmed_special_values") or []) if confirmed else []
    proposed = list(raw.get("proposed_special_values") or [])
    return {
        "special_values_confirmed": confirmed,
        "confirmed_special_values": confirmed_values,
        "proposed_special_values": proposed,
        "governance_status": (
            "confirmed" if confirmed else
            "proposed_unconfirmed" if proposed else
            "not_declared"
        ),
    }


def _value_mask(series: pd.Series, raw: Any) -> pd.Series:
    text = series.astype("string").str.strip()
    mask = text.eq(str(raw).strip()).fillna(False)
    try:
        mask |= pd.to_numeric(series, errors="coerce").eq(float(raw)).fillna(False)
    except (TypeError, ValueError):
        pass
    return mask & ~series.isna()


def classify(series: pd.Series, governance: dict[str, Any]) -> dict[str, Any]:
    """Partition every row into regular, physical missing, or one special category."""
    physical = series.isna()
    assigned = physical.copy()
    specials: list[tuple[Any, pd.Series]] = []
    for raw in governance.get("confirmed_special_values") or []:
        mask = _value_mask(series, raw) & ~assigned
        specials.append((raw, mask))
        assigned |= mask
    return {"regular": ~assigned, "physical_missing": physical, "specials": specials}


def reconciliation(frame: pd.DataFrame, snapshot: dict[str, Any] | None,
                   columns: list[str] | None = None) -> dict[str, Any]:
    """Describe mutually exclusive feature states and prove row reconciliation."""
    result: dict[str, Any] = {}
    for column in columns or list(frame.columns):
        if column not in frame.columns:
            continue
        governance = column_governance(snapshot, column)
        states = classify(frame[column], governance)
        special_counts = {
            str(raw): int(mask.sum()) for raw, mask in states["specials"]
        }
        total = int(len(frame))
        accounted = (int(states["regular"].sum())
                     + int(states["physical_missing"].sum())
                     + sum(special_counts.values()))
        result[column] = {
            **governance,
            "total_rows": total,
            "regular_rows": int(states["regular"].sum()),
            "physical_missing_rows": int(states["physical_missing"].sum()),
            "special_rows": special_counts,
            "accounted_rows": accounted,
            "reconciles": accounted == total,
        }
    return result


def _indicator_name(column: str, state: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", state).strip("_") or "value"
    return f"__rca_state__{column}__{safe}"


def analysis_frame(frame: pd.DataFrame, snapshot: dict[str, Any] | None
                   ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return a copy where confirmed specials cannot enter regular calculations.

    Original columns retain regular values and physical nulls, while confirmed
    special values are replaced with ``NA``. Explicit boolean indicators keep
    physical missingness and every special category analytically available.
    """
    governed = frame.copy()
    indicators: dict[str, dict[str, str]] = {}
    for column in list(frame.columns):
        governance = column_governance(snapshot, str(column))
        states = classify(frame[column], governance)
        physical_name = _indicator_name(str(column), "physical_missing")
        governed[physical_name] = states["physical_missing"].astype(int)
        special_names: dict[str, str] = {}
        special_mask = pd.Series(False, index=frame.index)
        for raw, mask in states["specials"]:
            name = _indicator_name(str(column), f"special_{raw}")
            governed[name] = mask.astype(int)
            special_names[str(raw)] = name
            special_mask |= mask
        if special_mask.any():
            governed.loc[special_mask, column] = pd.NA
        indicators[str(column)] = {
            "physical_missing": physical_name,
            **{f"special: {raw}": name for raw, name in special_names.items()},
        }
    context = {
        "schema_version": SNAPSHOT_VERSION,
        "columns": reconciliation(frame, snapshot),
        "indicator_columns": indicators,
        "regular_value_policy": (
            "Confirmed special values are excluded from original columns; physical missing and "
            "each confirmed special category are supplied as explicit indicators."
        ),
    }
    return governed, context


__all__ = ["analysis_frame", "classify", "column_governance", "reconciliation",
           "snapshot_from_inventory"]
