"""High-level orchestration for explainable missing-value classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd

from .column_classifier import ColumnClassifier
from .config import ANALYSIS_PARAMETER_SCHEMA_VERSION, AnalysisConfig
from .metadata import ColumnMetadata
from .models import MissingnessReport
from .preparation import AssessmentPreparer
from .summary import build_dataset_summary


class MissingnessAnalyzer:
    """Coordinate preparation, per-column classification, and report assembly.

    Args:
        config: Optional analysis configuration. Defaults are used when omitted.
    """

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        """Initialize and validate the configuration used for subsequent runs."""
        self.config = config or AnalysisConfig()
        self.config.validate()

    def analyze(
        self,
        dataset: pd.DataFrame,
        data_dictionary: pd.DataFrame | None = None,
        *,
        grain: str | None = None,
        period_column: str | None = None,
        analysis_context: str | None = None,
        dictionary_mapping: Mapping[str, str] | None = None,
        dictionary_value_mapping: Mapping[str, Mapping[str, str]] | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> MissingnessReport:
        """Assess missing-value behavior across every dataset column.

        Args:
            dataset: Source table to profile. Rows are observations and columns are
                candidate targets/features.
            data_dictionary: Optional user metadata normalized before analysis.
            grain: Optional description of what one input row represents.
            period_column: Optional date/period column for lineage-break detection.
            analysis_context: Optional advisory dataset-level business context.
            dictionary_mapping: Optional canonical-to-source dictionary headers.
            dictionary_value_mapping: Optional confirmed type and role values.
            progress_callback: Optional operational progress receiver.

        Returns:
            A serializable report with one classification per source column.

        Raises:
            ValueError: If the dataset is empty, has duplicate columns, or names an
                unavailable period column.
        """
        self._validate_dataset(dataset)
        frame = dataset.copy()
        prepared = AssessmentPreparer(self.config).prepare(
            frame,
            data_dictionary,
            period_column=period_column,
            dictionary_mapping=dictionary_mapping,
            dictionary_value_mapping=dictionary_value_mapping,
            progress_callback=progress_callback,
        )
        classifier = ColumnClassifier(prepared)

        findings: list[dict[str, Any]] = []
        total_columns = len(frame.columns)
        for index, column in enumerate(frame.columns, 1):
            findings.append(classifier.classify(column))
            if progress_callback:
                percent = 18 + round(80 * index / total_columns)
                progress_callback(
                    percent,
                    f"Assessing column {index} of {total_columns}",
                )

        report = MissingnessReport(
            preamble=self._build_preamble(
                frame,
                prepared.metadata,
                prepared.params,
                grain,
                prepared.period_column,
                analysis_context,
            ),
            columns=findings,
            blocks=prepared.blocks,
            summary=build_dataset_summary(findings, prepared.blocks, len(frame)),
        )
        if progress_callback:
            progress_callback(100, "Assessment complete")
        return report

    @staticmethod
    def _validate_dataset(dataset: pd.DataFrame) -> None:
        """Validate table-level assumptions before allocating indicator matrices.

        Args:
            dataset: Candidate input table.

        Raises:
            ValueError: If there are no rows or column names are duplicated.
        """
        if dataset.empty:
            raise ValueError("Dataset must contain at least one row")
        if dataset.columns.duplicated().any():
            duplicates = dataset.columns[dataset.columns.duplicated()].tolist()
            raise ValueError(f"Dataset contains duplicate columns: {duplicates}")

    @staticmethod
    def _build_preamble(
        dataset: pd.DataFrame,
        metadata: ColumnMetadata,
        params: dict[str, Any],
        grain: str | None,
        period_column: str | None,
        analysis_context: str | None,
    ) -> dict[str, Any]:
        """Build the audit preamble that makes a report reproducible."""
        return {
            "methodology_version": "stage1-descriptive-v2",
            "parameter_schema_version": ANALYSIS_PARAMETER_SCHEMA_VERSION,
            "analysis_objective": (
                "Full-data descriptive discovery of supported, enriched "
                "missingness patterns"
            ),
            "uses_holdout_sample": False,
            "n_rows": len(dataset),
            "n_columns": len(dataset.columns),
            "min_leaf_rows": params["min_leaf_rows"],
            "leaf_purity_min": params["leaf_purity_min"],
            "min_leaf_enrichment": params["min_leaf_enrichment"],
            "min_leaf_lift": params["min_leaf_lift"],
            "min_explained_missing_share": params["min_explained_missing_share"],
            "break_extreme": params["break_extreme"],
            "block_threshold": params["block_threshold"],
            "tolerance_default": params["tolerance_default"],
            "role_tolerances": params["role_tolerances"],
            "max_cat_levels": params["max_cat_levels"],
            "categorical_max_levels": params["categorical_max_levels"],
            "categorical_raw_level_limit": params["categorical_raw_level_limit"],
            "rare_category_min_share": params["rare_category_min_share"],
            "min_category_rows": params["min_category_rows"],
            "tree_depth": params["tree_depth"],
            "random_state": params["random_state"],
            "dictionary": metadata.dictionary_state,
            "dictionary_warning_count": len(metadata.warnings),
            "dictionary_warnings": metadata.warnings,
            "dictionary_schema_version": "data-dictionary-v1",
            "dictionary_mapping": metadata.dictionary_mapping,
            "dictionary_value_mapping": metadata.dictionary_value_mapping,
            "unused_dictionary_fields": metadata.unused_dictionary_fields,
            "grain": grain or "one input row (assumed; confirm for interpretation)",
            "period_column": period_column,
            "analysis_context": (analysis_context or "").strip(),
            "analysis_context_usage": (
                "Advisory context only; it does not silently override deterministic "
                "classifications or parameters."
            ),
            "mcar_caveat": (
                "No qualifying shallow-tree pattern means none was discovered under "
                "the configured descriptive search; it is not proof of MCAR or randomness."
            ),
        }
