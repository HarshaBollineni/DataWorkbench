"""Canonical target-route resolution shared by governed analyses."""
from __future__ import annotations

from typing import Any

import pandas as pd


def resolve_target_route(raw: pd.Series, requested_type: str = "auto",
                         positive_class: Any = None) -> tuple[str, str | None]:
    """Return the effective target type and canonical binary event label."""
    values = raw.dropna()
    unique_count = int(values.nunique(dropna=True))
    if unique_count < 2:
        raise ValueError("Target must contain at least two distinct values.")
    target_type = requested_type
    if target_type == "auto":
        target_type = (
            "binary" if unique_count == 2 else
            "continuous" if pd.api.types.is_numeric_dtype(values) else
            "multinomial"
        )
    if target_type != "binary":
        return target_type, None
    labels = sorted(values.astype("string").unique().tolist())
    if len(labels) != 2:
        raise ValueError("Binary target must contain exactly two classes.")
    positive = str(positive_class) if positive_class is not None else labels[1]
    if positive not in labels and positive_class is not None:
        numeric_positive = pd.to_numeric(pd.Series([positive_class]), errors="coerce").iloc[0]
        matches = [
            label for label in labels
            if pd.notna(numeric_positive)
            and pd.to_numeric(pd.Series([label]), errors="coerce").iloc[0] == numeric_positive
        ]
        if len(matches) == 1:
            positive = matches[0]
    if positive not in labels:
        raise ValueError(f"Positive class {positive!r} is not present in {labels}.")
    return "binary", positive
