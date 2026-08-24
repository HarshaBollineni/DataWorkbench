"""Missing indicators, co-missingness blocks, and lineage-break checks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from .metadata import normalize_category_values


class _UnionFind:
    """Small disjoint-set helper used to form transitive column blocks."""

    def __init__(self, size: int) -> None:
        """Create ``size`` initially independent sets."""
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        """Return the representative index for ``item`` with path compression."""
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        """Join the sets containing the two supplied column indexes."""
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def build_missing_masks(
    dataset: pd.DataFrame,
    missing_value_codes: dict[str, set[Any]],
) -> pd.DataFrame:
    """Build one dictionary-aware boolean missing indicator per column.

    Args:
        dataset: Source table whose index and columns are preserved.
        missing_value_codes: Approved special values treated as missing, keyed by
            column name.

    Returns:
        Boolean DataFrame where ``True`` means missing. Nulls, empty strings,
        whitespace-only strings, and approved missing-value codes are counted as
        missing.
    """
    masks = pd.DataFrame(False, index=dataset.index, columns=dataset.columns)
    for column in dataset.columns:
        series = dataset[column]
        mask = series.isna()
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            normalized = normalize_category_values(series)
            mask |= normalized.isna()
        else:
            normalized = None
        values = missing_value_codes.get(column, set())
        if values:
            mask |= series.isin(values)
            normalized_values = normalize_category_values(
                pd.Series([str(value) for value in values], dtype="string")
            ).dropna()
            comparable = normalized if normalized is not None else normalize_category_values(series)
            mask |= comparable.isin(set(normalized_values))
        masks[column] = mask
    return masks.astype(bool)


def find_comissingness_blocks(
    masks: pd.DataFrame,
    threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    """Group columns whose missing rows have high Jaccard similarity.

    Args:
        masks: Boolean missing-indicator matrix from :func:`build_missing_masks`.
        threshold: Minimum pairwise Jaccard score that joins two columns. The
            Stage-1 specification uses the strict value ``0.95``.

    Returns:
        A report-friendly block list and a lookup from every column to the set of
        members in its block. Singleton blocks are retained for uniform handling.
    """
    columns = masks.columns.tolist()
    values = masks.to_numpy(dtype=np.int64)
    totals = values.sum(axis=0)
    intersections = values.T @ values
    unions = totals[:, None] + totals[None, :] - intersections
    similarities = np.divide(
        intersections,
        unions,
        out=np.zeros_like(intersections, dtype=float),
        where=unions > 0,
    )

    union_find = _UnionFind(len(columns))
    for left in range(len(columns)):
        for right in range(left + 1, len(columns)):
            if similarities[left, right] >= threshold:
                union_find.union(left, right)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(columns)):
        groups[union_find.find(index)].append(index)

    blocks: list[dict[str, Any]] = []
    block_for: dict[str, set[str]] = {}
    for number, members in enumerate(groups.values(), 1):
        names = [columns[index] for index in members]
        member_set = set(names)
        for name in names:
            block_for[name] = member_set
        pair_scores = [
            similarities[left, right]
            for position, left in enumerate(members)
            for right in members[position + 1 :]
        ]
        blocks.append(
            {
                "block_id": f"B{number:03d}",
                "representative": names[0],
                "columns": names,
                "size": len(names),
                "minimum_pairwise_jaccard": round(min(pair_scores), 4)
                if pair_scores
                else 1.0,
            }
        )
    return blocks, block_for


def detect_structural_break(
    target_mask: pd.Series,
    period: pd.Series,
    extreme: float,
) -> dict[str, Any] | None:
    """Detect an approximately all-to-none missingness flip across time.

    Args:
        target_mask: Missing indicator for the column being assessed.
        period: Ordered period/date values aligned to ``target_mask``.
        extreme: Required extreme share. At ``0.95``, one side must be at least
            95% missing and the other no more than 5% missing.

    Returns:
        A serializable break rule with before/after shares, or ``None``.
    """
    frame = pd.DataFrame({"period": period, "missing": target_mask}).dropna(subset=["period"])
    if frame.empty:
        return None

    # String ordering handles ISO periods and timestamps consistently in reports.
    ordered = sorted(frame["period"].unique(), key=lambda value: str(value))
    period_text = frame["period"].map(str)
    for boundary in ordered[1:]:
        before = frame.loc[period_text < str(boundary), "missing"]
        after = frame.loc[period_text >= str(boundary), "missing"]
        if before.empty or after.empty:
            continue
        before_share, after_share = float(before.mean()), float(after.mean())
        flipped = (before_share >= extreme and after_share <= 1 - extreme) or (
            after_share >= extreme and before_share <= 1 - extreme
        )
        if flipped:
            return {
                "rule": f"structural break at {boundary}",
                "break_period": str(boundary),
                "before_share": round(before_share, 6),
                "after_share": round(after_share, 6),
            }
    return None
