"""Lazy run-scoped reuse of predictor screening and encoded feature blocks."""

from __future__ import annotations

import pandas as pd

from .features import PreparedFeature, prepare_feature


class FeatureCatalog:
    """Cache target-independent feature preparation for one assessment run.

    Target and co-missingness exclusions are applied fresh on every ``screen``
    call. The first target-specific screen prepares and encodes its independent,
    eligible candidates in one pass; subsequent trees reuse those blocks. Datasets
    with no threshold breaches never screen predictors and therefore pay no feature
    preparation cost. A catalog belongs to one sequential assessment run; its
    mutable caches are not shared across runs or concurrent classifiers.
    """

    def __init__(
        self,
        dataset: pd.DataFrame,
        missing_masks: pd.DataFrame,
        types: dict[str, str],
        *,
        categorical_raw_level_limit: int,
        categorical_max_levels: int,
        min_category_rows: int,
    ) -> None:
        """Bind run-wide feature inputs and initialize empty lazy caches."""
        self.dataset = dataset
        self.missing_masks = missing_masks
        self.types = types
        self.categorical_raw_level_limit = categorical_raw_level_limit
        self.categorical_max_levels = categorical_max_levels
        self.min_category_rows = min_category_rows
        self._prepared: dict[str, PreparedFeature] = {}

    def screen(
        self,
        target: str,
        target_block: set[str],
    ) -> tuple[list[str], list[dict[str, object]]]:
        """Return target-specific eligibility with cached candidate profiles."""
        features: list[str] = []
        diagnostics: list[dict[str, object]] = []
        for column in self.dataset.columns:
            if column == target:
                diagnostic = self._target_exclusion(column, "target column")
            elif column in target_block:
                diagnostic = self._target_exclusion(
                    column,
                    "target co-missingness block mate",
                )
            else:
                diagnostic = dict(self._prepare(column).diagnostic)
                if diagnostic["status"] == "included":
                    features.append(column)
            diagnostics.append(diagnostic)
        return features, diagnostics

    def encode(
        self,
        features: list[str],
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        """Assemble a target matrix from lazily cached source-column blocks."""
        pieces: list[pd.DataFrame] = []
        feature_map: dict[str, str] = {}
        for column in features:
            prepared = self._prepare(column)
            if prepared.encoded is None:
                raise ValueError(
                    f"Feature '{column}' reached encoding without an eligible block"
                )
            pieces.append(prepared.encoded)
            feature_map.update(prepared.feature_map)
        matrix = (
            pd.concat(pieces, axis=1)
            if pieces
            else pd.DataFrame(index=self.dataset.index)
        )
        return matrix, feature_map

    def cache_info(self) -> dict[str, int]:
        """Return cache counts and retained dataframe bytes for diagnostics."""
        encoded_blocks = [
            item.encoded
            for item in self._prepared.values()
            if item.encoded is not None
        ]
        return {
            "profiled_columns": len(self._prepared),
            "encoded_columns": len(encoded_blocks),
            "encoded_bytes": (
                sum(
                    int(encoded.memory_usage(index=False, deep=True).sum())
                    for encoded in encoded_blocks
                )
                + (
                    int(self.dataset.index.memory_usage(deep=True))
                    if encoded_blocks
                    else 0
                )
            ),
        }

    def _prepare(self, column: str) -> PreparedFeature:
        """Build target-independent evidence and encoding once per source column."""
        if column not in self._prepared:
            self._prepared[column] = prepare_feature(
                column,
                self.dataset,
                self.types,
                self.missing_masks,
                self.categorical_raw_level_limit,
                self.categorical_max_levels,
                self.min_category_rows,
            )
        return self._prepared[column]

    def _target_exclusion(self, column: str, reason: str) -> dict[str, object]:
        """Build the minimal diagnostic used before candidate profiling."""
        return {
            "column": column,
            "logical_type": self.types[column],
            "status": "excluded",
            "reason": reason,
        }
