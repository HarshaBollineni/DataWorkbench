"""D-04 — profiling preconditions: cheap structural checks an engine can
run on a frame before anything analytical happens. Schema-agnostic: this
module knows nothing about dq_items, KB roles, or any platform table — it
only ever sees the frame and column names a caller hands it.
"""
from __future__ import annotations

import pandas as pd


def grain_uniqueness(frame: pd.DataFrame, key_columns: list[str]) -> dict:
    """Check whether `key_columns` uniquely identify rows in `frame`.

    Returns ``{"unique": bool, "duplicate_count": int, "checked_keys": [...]}``
    where `duplicate_count` counts every ROW participating in a duplicated
    key combination (not the number of distinct duplicated key values).

    Raises `ValueError` if `key_columns` is empty, or names a column not
    present in `frame` — a caller/wiring error, not a data-quality finding.
    """
    if not key_columns:
        raise ValueError("grain_uniqueness requires at least one key column")
    missing = [c for c in key_columns if c not in frame.columns]
    if missing:
        raise ValueError(f"key column(s) not present in frame: {missing}")
    duplicate_mask = frame.duplicated(subset=key_columns, keep=False)
    duplicate_count = int(duplicate_mask.sum())
    return {
        "unique": duplicate_count == 0,
        "duplicate_count": duplicate_count,
        "checked_keys": list(key_columns),
    }
