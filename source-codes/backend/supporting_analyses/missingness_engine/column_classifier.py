"""Ordered decision gates for classifying one prepared dataset column."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .metadata import ColumnMetadata, storage_family
from .missingness import detect_structural_break
from .preparation import PreparedAssessment
from .tree_analysis import TreeResult, fit_missingness_tree


class ColumnClassifier:
    """Classify individual targets against immutable run-wide preparation state."""

    def __init__(self, prepared: PreparedAssessment) -> None:
        """Bind the dataset, metadata, masks, blocks, and parameters for one run."""
        self.prepared = prepared

    def classify(self, column: str) -> dict[str, Any]:
        """Apply the ordered Stage-1 decision gates to one column.

        Args:
            column: Target column name present in the prepared dataset.

        Returns:
            One JSON-compatible column finding with exactly one verdict.
        """
        dataset = self.prepared.dataset
        masks = self.prepared.masks
        metadata = self.prepared.metadata
        params = self.prepared.params
        missing_count = int(masks[column].sum())
        missing_share = missing_count / len(dataset)
        role = metadata.roles[column]
        tolerance = params["role_tolerances"].get(role, params["tolerance_default"])
        target_block = self.prepared.block_for[column]
        block = next(
            item for item in self.prepared.blocks if column in item["columns"]
        )
        result = self._base_result(
            column,
            missing_count,
            missing_share,
            tolerance,
            role,
            metadata,
            block["block_id"],
            target_block,
            dataset[column],
            params["min_explained_missing_share"],
            params["min_leaf_enrichment"],
            params["min_leaf_lift"],
        )

        early_result = self._apply_pre_tree_gates(
            result=result,
            column=column,
            missing_count=missing_count,
            missing_share=missing_share,
            tolerance=tolerance,
        )
        if early_result is not None:
            return early_result

        features = self._screen_features(column, target_block, result)
        if not features:
            block_only = len(target_block) > 1
            reason = (
                "every column tracking this gap is in its own co-missingness block and was excluded"
                if block_only
                else "no independent column available to test against"
            )
            return self._untestable(result, reason)

        encoded, feature_map = self._encode_features(features)
        result["predictor_screening"]["encoded_feature_count"] = encoded.shape[1]
        if encoded.shape[1] == 0:
            return self._untestable(
                result,
                "eligible columns produced no usable encoded features",
            )

        tree = fit_missingness_tree(
            encoded,
            masks[column].astype(int).to_numpy(),
            feature_map,
            params,
        )
        self._attach_tree_evidence(result, tree)
        return self._classify_tree_result(result, tree)

    def _screen_features(
        self,
        column: str,
        target_block: set[str],
        result: dict[str, Any],
    ) -> list[str]:
        """Screen predictors once and attach auditable inclusion diagnostics."""
        features, screening = self.prepared.feature_catalog.screen(
            column,
            target_block,
        )
        result["predictor_screening"] = {
            "included_count": len(features),
            "excluded_count": len(screening) - len(features),
            "included": [item for item in screening if item["status"] == "included"],
            "excluded": [item for item in screening if item["status"] == "excluded"],
        }
        return features

    def _encode_features(
        self,
        features: list[str],
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        """Encode already screened predictors using the run-wide type policy."""
        return self.prepared.feature_catalog.encode(features)

    @staticmethod
    def _attach_tree_evidence(
        result: dict[str, Any],
        tree: TreeResult,
    ) -> None:
        """Project tree metrics and readable rules onto the stable finding schema."""
        result["correlates_with"] = tree.path_features
        result["rules"] = tree.rules
        result["rule_captured_missing_count"] = tree.rule_captured_missing_count
        result["rule_missing_coverage"] = round(tree.rule_missing_coverage, 6)
        result["rule_selected_count"] = tree.rule_selected_count
        result["rule_precision"] = round(tree.rule_precision, 6)
        result["unexplained_missing_count"] = tree.unexplained_missing_count
        result["unexplained_missing_share"] = round(
            tree.unexplained_missing_share,
            6,
        )
        result["max_rule_enrichment"] = round(tree.max_rule_enrichment, 6)
        result["max_rule_lift"] = round(tree.max_rule_lift, 6)

    def _apply_pre_tree_gates(
        self,
        *,
        result: dict[str, Any],
        column: str,
        missing_count: int,
        missing_share: float,
        tolerance: float,
    ) -> dict[str, Any] | None:
        """Apply rate, empty, type, break, and minority guards before modelling."""
        dataset = self.prepared.dataset
        masks = self.prepared.masks
        metadata = self.prepared.metadata
        params = self.prepared.params
        if missing_share <= tolerance:
            return self._finish(
                result,
                "a",
                "below_threshold",
                "Below threshold",
                "Missing share is within the role-based tolerance.",
                "Proceed; continue routine monitoring.",
            )
        if missing_share == 1.0:
            return self._finish(
                result,
                "b",
                "entirely_empty",
                "Above threshold with strong explainable pattern",
                "The column is entirely empty; no tree can or should be fitted.",
                "Escalate data lineage and remove from downstream modelling until restored.",
            )
        if metadata.types[column] == "other":
            return self._untestable(
                result,
                "type unresolved; only the flat-rate check is valid",
            )

        if self.prepared.period_column and self.prepared.period_column != column:
            break_rule = detect_structural_break(
                masks[column],
                dataset[self.prepared.period_column],
                params["break_extreme"],
            )
            if break_rule:
                result["rules"] = [break_rule]
                result["correlates_with"] = [self.prepared.period_column]
                return self._finish(
                    result,
                    "b",
                    "lineage_break",
                    "Above threshold with strong explainable pattern",
                    "Lineage break: document and segment on the break, not a missingness mechanism.",
                    "Confirm the feed or schema migration and define valid before/after populations.",
                )

        minority = min(missing_count, len(dataset) - missing_count)
        if minority < 2 * params["min_leaf_rows"]:
            return self._untestable(
                result,
                "too few minority rows to fit a tree at this data volume",
            )
        return None

    @staticmethod
    def _base_result(
        column: str,
        missing_count: int,
        missing_share: float,
        tolerance: float,
        role: str,
        metadata: ColumnMetadata,
        block_id: str,
        block_members: set[str],
        series: pd.Series,
        min_explained_missing_share: float,
        min_leaf_enrichment: float,
        min_leaf_lift: float,
    ) -> dict[str, Any]:
        """Create the stable result schema before applying decision gates."""
        provisional = column in metadata.provisional
        return {
            "column": column,
            "type": metadata.types[column],
            "field_family": storage_family(series),
            "storage_dtype": str(series.dtype),
            "role": role,
            "description": metadata.descriptions[column],
            "business_context": metadata.business_contexts[column],
            "missing_count": missing_count,
            "missing_share": round(missing_share, 6),
            "tolerance": tolerance,
            "exceeds_tolerance": missing_share > tolerance,
            "classification": "",
            "verdict": "",
            "subtype": "",
            "confidence": "low" if provisional else "high",
            "rule_captured_missing_count": None,
            "rule_missing_coverage": None,
            "rule_selected_count": None,
            "rule_precision": None,
            "unexplained_missing_count": None,
            "unexplained_missing_share": None,
            "max_rule_enrichment": None,
            "max_rule_lift": None,
            "min_explained_missing_share": min_explained_missing_share,
            "min_leaf_enrichment": min_leaf_enrichment,
            "min_leaf_lift": min_leaf_lift,
            "correlates_with": [],
            "rules": [],
            "predictor_screening": None,
            "block_id": block_id,
            "block_members": sorted(block_members),
            "provisional": provisional,
            "rationale": "",
            "recommended_action": "",
        }

    def _classify_tree_result(
        self,
        result: dict[str, Any],
        tree: TreeResult,
    ) -> dict[str, Any]:
        """Translate full-data tree evidence into the final classification."""
        minimum_coverage = self.prepared.params["min_explained_missing_share"]
        broad = tree.rule_missing_coverage >= minimum_coverage
        if tree.has_pattern_leaf:
            if broad and tree.path_importance_agrees:
                return self._finish(
                    result,
                    "b",
                    "tree_strong",
                    "Above threshold with strong explainable pattern",
                    "Supported, pure, enriched shallow-tree paths capture a broad share of all missing records.",
                    "Investigate the named segment/feed and preserve a missing indicator downstream.",
                )
            result["confidence"] = "low"
            limitations: list[str] = []
            if not broad:
                limitations.append(
                    f"full-tree missing-case coverage {tree.rule_missing_coverage:.1%} is below "
                    f"{minimum_coverage:.1%}"
                )
            if not tree.path_importance_agrees:
                limitations.append("split-path and aggregate importance disagree")
            return self._finish(
                result,
                "b",
                "tree_weak",
                "Above threshold with weak explainable pattern",
                "Localized high-missingness paths exist, but "
                + "; ".join(limitations)
                + ".",
                "Treat the relationship as partial; investigate the named segment and the unexplained missing majority separately.",
            )
        return self._finish(
            result,
            "c",
            "no_explainable_pattern",
            "Above threshold with no explainable pattern found",
            "The full-data shallow search found no leaf meeting the configured support, purity, enrichment, and lift guards.",
            "Treat the mechanism as unexplained by this search; review additional variables or run optional sensitivity diagnostics.",
        )

    @staticmethod
    def _finish(
        result: dict[str, Any],
        verdict: str,
        subtype: str,
        classification: str,
        rationale: str,
        action: str,
    ) -> dict[str, Any]:
        """Populate common terminal fields for a column finding."""
        result.update(
            verdict=verdict,
            subtype=subtype,
            classification=classification,
            rationale=rationale,
            recommended_action=action,
        )
        return result

    def _untestable(self, result: dict[str, Any], reason: str) -> dict[str, Any]:
        """Return the safety verdict used when no honest tree can be fitted."""
        return self._finish(
            result,
            "d",
            "untestable",
            "Above threshold but untestable",
            reason,
            "Add independent predictors or observations; do not infer a missingness mechanism.",
        )
