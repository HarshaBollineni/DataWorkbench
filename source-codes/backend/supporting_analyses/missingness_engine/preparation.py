"""Run-wide preparation shared by every column classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import AnalysisConfig
from .feature_catalog import FeatureCatalog
from .metadata import ColumnMetadata, prepare_metadata, select_period_column
from .missingness import build_missing_masks, find_comissingness_blocks


@dataclass(frozen=True)
class PreparedAssessment:
    """Immutable reference to the state prepared once for an assessment run.

    The contained pandas objects and dictionaries are treated as read-only by
    classifiers. Freezing the container prevents accidental replacement of
    run-wide dependencies while avoiding expensive dataframe copies per column.
    """

    dataset: pd.DataFrame
    metadata: ColumnMetadata
    masks: pd.DataFrame
    params: dict[str, Any]
    blocks: list[dict[str, Any]]
    block_for: dict[str, set[str]]
    period_column: str | None
    feature_catalog: FeatureCatalog


class AssessmentPreparer:
    """Build metadata, missing masks, blocks, and resolved run parameters once."""

    def __init__(self, config: AnalysisConfig) -> None:
        """Store the already validated analysis policy for one preparation run."""
        self.config = config

    def prepare(
        self,
        dataset: pd.DataFrame,
        data_dictionary: pd.DataFrame | None = None,
        *,
        period_column: str | None = None,
        dictionary_mapping: Mapping[str, str] | None = None,
        dictionary_value_mapping: Mapping[str, Mapping[str, str]] | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> PreparedAssessment:
        """Prepare all state that is independent of the current target column.

        Args:
            dataset: Validated source dataframe copied by the orchestrator.
            data_dictionary: Optional user metadata table.
            period_column: Optional explicit period/date column.
            dictionary_mapping: Optional canonical-to-source dictionary headers.
            dictionary_value_mapping: Optional confirmed type and role values.
            progress_callback: Optional operational progress receiver.

        Returns:
            Prepared run state reused by every column classifier invocation.
        """
        params = self.config.resolved(len(dataset))
        if progress_callback:
            progress_callback(9, "Preparing dictionary metadata")
        metadata = prepare_metadata(
            dataset,
            data_dictionary,
            params["categorical_raw_level_limit"],
            dictionary_mapping,
            dictionary_value_mapping,
        )
        if progress_callback:
            progress_callback(12, "Building missing-value indicators")
        masks = build_missing_masks(dataset, metadata.missing_value_codes)
        if progress_callback:
            progress_callback(16, "Finding co-missingness blocks")
        blocks, block_for = find_comissingness_blocks(
            masks,
            params["block_threshold"],
        )
        selected_period = select_period_column(
            period_column,
            metadata.types,
            dataset.columns,
        )
        feature_catalog = FeatureCatalog(
            dataset,
            masks,
            metadata.types,
            categorical_raw_level_limit=params["categorical_raw_level_limit"],
            categorical_max_levels=params["categorical_max_levels"],
            min_category_rows=params["min_category_rows"],
        )
        return PreparedAssessment(
            dataset=dataset,
            metadata=metadata,
            masks=masks,
            params=params,
            blocks=blocks,
            block_for=block_for,
            period_column=selected_period,
            feature_catalog=feature_catalog,
        )
