from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[5]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from domains.aar.types import get_artifact_type  # noqa: E402
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.engine import (  # noqa: E402
    DirectionalityThresholds,
    analyze_directionality,
    compare_expected_observed,
)
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.knowledge import (  # noqa: E402
    KB_PATH,
    PROMPT_PATH,
    TERMINOLOGY_PATH,
    prompt,
    resources,
    rule_index,
)
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.matching import (  # noqa: E402
    match_feature_to_kb,
)
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.manifest import (  # noqa: E402
    _feature_card,
)
from routers.diagnostics import ManifestPatch  # noqa: E402


def test_binary_consensus_is_increasing_and_pearson_is_display_evidence():
    rng = np.random.default_rng(4)
    feature = pd.Series(np.linspace(-3, 3, 500))
    probability = 1 / (1 + np.exp(-feature))
    target = pd.Series(rng.binomial(1, probability))
    result = analyze_directionality(feature, target, target_type="binary", positive_class="1")
    assert result["observed_direction"] == "INCREASING"
    assert result["evidence_strength"] == "STRONG"
    assert result["spearman"]["direction"] == "INCREASING"
    assert result["regression"]["direction"] == "INCREASING"
    assert result["binned"]["shape"] == "INCREASING"
    assert result["pearson"]["value"] is not None


def test_string_binary_target_uses_explicit_positive_class():
    feature = pd.Series(np.arange(120, dtype=float))
    target = pd.Series(["good"] * 60 + ["bad"] * 60)
    result = analyze_directionality(feature, target, target_type="binary", positive_class="bad")
    assert result["event_value"] == "bad"
    assert result["observed_direction"] == "INCREASING"


def test_binary_target_special_value_is_removed_before_class_validation():
    feature = pd.Series(np.arange(121, dtype=float))
    target = pd.Series([-999] + [0] * 60 + [1] * 60)
    result = analyze_directionality(
        feature, target, target_type="binary", positive_class=1,
        reference_special_values=[-999],
    )
    assert result["event_value"] == "1"
    assert result["n_special_value_dropped"] == 1
    assert result["observed_direction"] == "INCREASING"


def test_broad_u_shape_is_non_monotonic_without_adjacent_inversion_counting():
    rng = np.random.default_rng(8)
    feature = pd.Series(np.linspace(-2, 2, 500))
    target = feature.pow(2) + pd.Series(rng.normal(0, 0.08, len(feature)))
    result = analyze_directionality(feature, target, target_type="continuous")
    assert result["observed_direction"] == "NON_MONOTONIC"
    assert result["binned"]["shape"] == "NON_MONOTONIC"
    assert "inversion" not in result["binned"]


def test_small_population_is_insufficient():
    result = analyze_directionality(pd.Series(range(20), dtype=float),
                                    pd.Series(range(20), dtype=float),
                                    target_type="continuous")
    assert result["observed_direction"] == "INSUFFICIENT_DATA"


def test_expected_direction_is_compared_only_after_reference_orientation():
    assert compare_expected_observed("INCREASING", "HIGHER_IS_WORSE", "INCREASING") == "AGREEMENT"
    assert compare_expected_observed("INCREASING", "HIGHER_IS_BETTER", "DECREASING") == "AGREEMENT"
    assert compare_expected_observed("INCREASING", "HIGHER_IS_BETTER", "INCREASING") == "REVIEW_RECOMMENDED"


def test_kb_v03_uses_intentional_income_amount_rename_and_exact_matching():
    rules = rule_index()
    assert "income_amount" in rules
    assert "income_capacity" not in rules
    kb, terminology, prepared = resources()
    result = match_feature_to_kb("CURRENT_LTV", "", kb, terminology,
                                 prepared_matcher=prepared)
    assert result["deterministic_match"]["canonical_feature"] == "loan_to_value"


def test_feature_cards_retain_exact_kb_decision_as_immutable_baseline_evidence():
    exact = _feature_card({
        "column_name": "CURRENT_LTV", "description": "", "data_type": "float64",
        "role": "Feature", "special_values_confirmed": False,
    })
    assert exact["governed_exact_decision"] == {
        "kb_version": "0.3",
        "canonical_feature": "loan_to_value",
        "representation_orientation": "SAME",
        "expected_direction": "INCREASING",
    }

    unmatched = _feature_card({
        "column_name": "RANDOM_VAR_X", "description": "", "data_type": "float64",
        "role": "Feature", "special_values_confirmed": False,
    })
    assert unmatched["governed_exact_decision"] is None


def test_production_knowledge_and_agent_assets_use_central_ownership_folders():
    assert KB_PATH.parent.name == "knowledge_base"
    assert TERMINOLOGY_PATH.parent == KB_PATH.parent
    assert PROMPT_PATH.parent.name == "agents"
    assert PROMPT_PATH.parent.parent.name == "ai"
    assert KB_PATH.is_file() and TERMINOLOGY_PATH.is_file()
    assert prompt().startswith("You are a semantic feature adjudicator")


def test_unrelated_fuzzy_result_is_never_promoted_to_deterministic_match():
    kb, terminology, prepared = resources()
    result = match_feature_to_kb("RANDOM_VAR_X", "", kb, terminology,
                                 top_n=10, prepared_matcher=prepared)
    assert result["match_status"] == "no_match"
    assert result["deterministic_match"] is None
    assert len(result["top_candidates"]) == 10


def test_directionality_artifact_type_is_governed():
    descriptor = get_artifact_type("directionality_evidence")
    assert descriptor is not None
    assert descriptor.target_applicability == "required"
    assert descriptor.supported_scopes == ("diagnostic_local",)


def test_router_manifest_patch_preserves_directionality_contract_fields():
    payload = ManifestPatch(
        kind="feature_classification", feature="ltv",
        orientation="HIGHER_IS_WORSE", segment_column="portfolio",
        target_type="binary", positive_class="1",
        expected_direction="INCREASING", rationale="Reviewed",
        canonical_feature="loan_to_value", representation_orientation="SAME",
        include_in_kb=True, limit=6,
    ).model_dump(exclude_none=True)
    assert payload["orientation"] == "HIGHER_IS_WORSE"
    assert payload["segment_column"] == "portfolio"
    assert payload["expected_direction"] == "INCREASING"
    assert payload["include_in_kb"] is True
