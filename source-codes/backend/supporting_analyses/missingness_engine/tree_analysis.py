"""Full-data shallow decision-tree discovery and rule extraction."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier


@dataclass(frozen=True)
class TreeResult:
    """Explanation-focused outputs from one missingness tree.

    Attributes:
        has_pattern_leaf: Whether a supported, pure, enriched leaf exists.
        rules: Human-readable rules for reportable leaves.
        path_features: Source columns appearing in those rules.
        path_importance_agrees: Whether the most important source feature occurs
            in at least one reported strong path.
        rule_captured_missing_count: Missing targets inside qualifying leaves of
            the final tree fitted on all observations.
        rule_missing_coverage: Final-tree captured missing targets divided by all
            missing targets.
        rule_selected_count: All rows inside qualifying final-tree leaves.
        rule_precision: Missing share across those qualifying leaves.
        unexplained_missing_count: Missing targets outside qualifying leaves.
        unexplained_missing_share: Uncaptured missing targets divided by all
            missing targets.
        max_rule_enrichment: Largest leaf missing-share increase over baseline.
        max_rule_lift: Largest leaf missing-share ratio over baseline.
    """

    has_pattern_leaf: bool
    rules: list[dict[str, Any]]
    path_features: list[str]
    path_importance_agrees: bool
    rule_captured_missing_count: int
    rule_missing_coverage: float
    rule_selected_count: int
    rule_precision: float
    unexplained_missing_count: int
    unexplained_missing_share: float
    max_rule_enrichment: float
    max_rule_lift: float


def _new_tree(params: dict[str, Any]) -> DecisionTreeClassifier:
    """Create a deterministic tree from resolved analysis parameters.

    Args:
        params: Resolved configuration containing ``tree_depth``,
            ``min_leaf_rows``, and ``random_state``.

    Returns:
        An unfitted shallow Gini decision tree.
    """
    return DecisionTreeClassifier(
        max_depth=params["tree_depth"],
        min_samples_leaf=params["min_leaf_rows"],
        random_state=params["random_state"],
        criterion="gini",
    )


def _extract_leaves(
    model: DecisionTreeClassifier,
    encoded_names: list[str],
    feature_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Walk a fitted tree and return every leaf with its complete split path.

    Args:
        model: Fitted shallow decision tree.
        encoded_names: Encoded feature names in model-column order.
        feature_map: Mapping from encoded names back to source columns.

    Returns:
        Leaf dictionaries containing row count, missing share, text path, and
        source features used by the path.
    """
    leaves: list[dict[str, Any]] = []

    def walk(node: int, path: list[str], path_features: list[str]) -> None:
        """Recursively accumulate split conditions until a leaf is reached."""
        tree = model.tree_
        if tree.children_left[node] == tree.children_right[node]:
            class_values = tree.value[node][0]
            class_total = float(class_values.sum())
            # Normalization works whether sklearn stores counts or proportions.
            missing_share = (
                float(class_values[1]) / class_total
                if len(class_values) > 1 and class_total
                else 0.0
            )
            leaves.append(
                {
                    "node_id": node,
                    "n_rows": int(tree.n_node_samples[node]),
                    "missing_share": missing_share,
                    "path": path.copy(),
                    "features": path_features.copy(),
                }
            )
            return

        encoded_name = encoded_names[tree.feature[node]]
        source_name = feature_map[encoded_name]
        threshold = float(tree.threshold[node])
        walk(
            tree.children_left[node],
            path + [f"{encoded_name} <= {threshold:.6g}"],
            path_features + [source_name],
        )
        walk(
            tree.children_right[node],
            path + [f"{encoded_name} > {threshold:.6g}"],
            path_features + [source_name],
        )

    walk(0, [], [])
    return leaves


def fit_missingness_tree(
    encoded: pd.DataFrame,
    target: np.ndarray,
    feature_map: dict[str, str],
    params: dict[str, Any],
) -> TreeResult:
    """Fit all rows once and summarize descriptive missingness patterns.

    Args:
        encoded: Numeric feature matrix produced by ``encode_tree_features``.
        target: Binary missing indicator for the assessed column.
        feature_map: Mapping from encoded feature names to source columns.
        params: Resolved analysis configuration including depth, support, purity,
            enrichment, lift, and deterministic random state.

    Returns:
        A :class:`TreeResult` containing full-data descriptive rules and coverage.
    """
    model = _new_tree(params)
    model.fit(encoded, target)

    encoded_names = [str(name) for name in encoded.columns]
    leaves = _extract_leaves(model, encoded_names, feature_map)
    baseline_missing_share = float(target.mean())
    for leaf in leaves:
        leaf["enrichment"] = leaf["missing_share"] - baseline_missing_share
        leaf["lift"] = (
            leaf["missing_share"] / baseline_missing_share
            if baseline_missing_share
            else 0.0
        )
    pattern_leaves = [
        leaf
        for leaf in leaves
        if leaf["n_rows"] >= params["min_leaf_rows"]
        and leaf["missing_share"] >= params["leaf_purity_min"]
        and leaf["enrichment"] >= params["min_leaf_enrichment"]
        and leaf["lift"] >= params["min_leaf_lift"]
    ]
    pattern_leaves.sort(
        key=lambda item: (item["missing_share"], item["n_rows"]),
        reverse=True,
    )
    assigned_leaf = model.apply(encoded)
    for leaf in pattern_leaves:
        selected = assigned_leaf == leaf["node_id"]
        leaf["missing_count"] = int(target[selected].sum())
    rule_captured_missing_count = sum(leaf["missing_count"] for leaf in pattern_leaves)
    rule_selected_count = sum(leaf["n_rows"] for leaf in pattern_leaves)
    total_missing = int(target.sum())
    rule_missing_coverage = (
        rule_captured_missing_count / total_missing if total_missing else 0.0
    )
    rule_precision = (
        rule_captured_missing_count / rule_selected_count if rule_selected_count else 0.0
    )
    unexplained_missing_count = total_missing - rule_captured_missing_count
    unexplained_missing_share = (
        unexplained_missing_count / total_missing if total_missing else 0.0
    )
    rules = [
        {
            "rule": " AND ".join(leaf["path"]) or "all rows",
            "n_rows": leaf["n_rows"],
            "missing_count": leaf["missing_count"],
            "missing_share": round(leaf["missing_share"], 6),
            "baseline_missing_share": round(baseline_missing_share, 6),
            "enrichment": round(leaf["enrichment"], 6),
            "lift": round(leaf["lift"], 6),
        }
        for leaf in pattern_leaves
    ]
    path_features = list(
        dict.fromkeys(feature for leaf in pattern_leaves for feature in leaf["features"])
    )

    # One-hot columns are aggregated back to their original source feature.
    aggregate_importance: dict[str, float] = defaultdict(float)
    for encoded_name, importance in zip(encoded_names, model.feature_importances_):
        aggregate_importance[feature_map[encoded_name]] += float(importance)
    top_importance = (
        max(aggregate_importance, key=aggregate_importance.get)
        if aggregate_importance
        else None
    )
    agrees = not path_features or top_importance in path_features
    return TreeResult(
        has_pattern_leaf=bool(pattern_leaves),
        rules=rules,
        path_features=path_features,
        path_importance_agrees=agrees,
        rule_captured_missing_count=rule_captured_missing_count,
        rule_missing_coverage=rule_missing_coverage,
        rule_selected_count=rule_selected_count,
        rule_precision=rule_precision,
        unexplained_missing_count=unexplained_missing_count,
        unexplained_missing_share=unexplained_missing_share,
        max_rule_enrichment=max(
            (leaf["enrichment"] for leaf in pattern_leaves), default=0.0
        ),
        max_rule_lift=max((leaf["lift"] for leaf in pattern_leaves), default=0.0),
    )
