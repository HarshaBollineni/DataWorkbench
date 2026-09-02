"""Exploratory expected/observed review for a proof-of-concept reference run.

This is deliberately not the formal Step 3 diagnostic contract.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from functions.reference_configuration import ReferenceOrientation


_DIRECTIONAL = {"increasing", "decreasing"}


def expected_reference_direction(
    feature_risk_direction: str, orientation: ReferenceOrientation
) -> str:
    if orientation is ReferenceOrientation.HIGHER_IS_BETTER:
        return {
            "increasing": "decreasing",
            "decreasing": "increasing",
        }.get(feature_risk_direction, feature_risk_direction)
    return feature_risk_direction


def conclusion(expected: str, observed: str) -> str:
    if expected == "not_applicable":
        return "not_applicable"
    if expected == "no_clear_direction":
        return "no_economic_prior"
    if observed in {"weak_no_relationship", "insufficient_evidence"}:
        return "weak_or_insufficient_empirical_evidence"
    if observed == "conflicting_evidence":
        return "conflicting_empirical_evidence"
    if expected == "non_monotonic":
        return (
            "expected_non_monotonic_confirmed"
            if observed == "non_monotonic"
            else "expected_non_monotonic_not_confirmed"
        )
    if expected in _DIRECTIONAL and observed in _DIRECTIONAL:
        return "agreement" if expected == observed else "disagreement"
    return "review"


def build_review(
    mapping_path: Path,
    evidence_path: Path,
    *,
    orientation: ReferenceOrientation,
) -> pd.DataFrame:
    mappings = pd.read_csv(mapping_path)
    evidence = pd.read_csv(evidence_path)
    review = mappings[
        [
            "feature_name",
            "final_canonical_feature",
            "column_expected_direction",
            "kb_knowledge_strength",
            "user_review_required",
        ]
    ].merge(evidence, on="feature_name", how="inner", validate="one_to_one")
    review["expected_reference_direction"] = review["column_expected_direction"].map(
        lambda value: expected_reference_direction(value, orientation)
    )
    review["exploratory_conclusion"] = [
        conclusion(expected, observed)
        for expected, observed in zip(
            review["expected_reference_direction"], review["observed_direction"]
        )
    ]
    review["formal_diagnostic"] = False
    review["review_caveat"] = (
        "Proof-of-concept rating anchor; not the formal Step 3 diagnostic."
    )
    return review


__all__ = ["build_review", "conclusion", "expected_reference_direction"]
