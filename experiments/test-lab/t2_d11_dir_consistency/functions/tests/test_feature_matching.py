from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from functions import feature_matching as matching_module
from functions.feature_matching import (
    load_kb,
    load_terminology,
    match_feature_to_kb,
    match_features_to_kb,
    normalize_text,
    prepare_feature_matcher,
    process_terminology,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
KB_DIR = EXPERIMENT_ROOT / "kb"
INPUTS_DIR = EXPERIMENT_ROOT / "inputs"


@pytest.fixture(scope="module")
def resources():
    return (
        load_kb(KB_DIR / "pd_directionality_kb_v0_3.yaml"),
        load_terminology(KB_DIR / "credit_risk_abbreviations_v0_2.yaml"),
    )


def test_v03_kb_additions_are_active(resources):
    kb, terminology = resources
    expected = {
        "NOI": ("net_operating_income", "no_clear_direction"),
        "outstanding_balance": ("loan_balance", "no_clear_direction"),
        "loan_limit": ("facility_limit", "no_clear_direction"),
        "months_on_book": ("loan_seasoning", "non_monotonic"),
        "interest_rate": ("contractual_interest_rate", "no_clear_direction"),
        "origination_rating": ("credit_rating", "decreasing"),
        "property_value": ("property_value", "no_clear_direction"),
    }
    for feature_name, (canonical, direction) in expected.items():
        result = match_feature_to_kb(feature_name, "", kb, terminology)
        deterministic = result["deterministic_match"]
        assert deterministic["canonical_feature"] == canonical
        assert deterministic["expected_direction"] == direction


def test_v03_preserves_v02_and_has_valid_unique_mapping_contract(resources):
    kb, terminology = resources
    prior = load_kb(KB_DIR / "pd_directionality_kb_v0_2.yaml")
    prior_rules = {rule["feature"]: rule for rule in prior["feature_rules"]}
    current_rules = {rule["feature"]: rule for rule in kb["feature_rules"]}
    assert set(prior_rules) <= set(current_rules)
    for name, prior_rule in prior_rules.items():
        assert current_rules[name]["expected_direction"] == prior_rule["expected_direction"]
        assert current_rules[name]["knowledge_strength"] == prior_rule["knowledge_strength"]
    assert set(current_rules) - set(prior_rules) == {
        "net_operating_income",
        "loan_balance",
        "original_loan_amount",
        "facility_limit",
        "loan_seasoning",
        "contractual_interest_rate",
        "credit_rating",
        "probability_of_default",
        "credit_spread",
        "property_value",
    }
    assert len(current_rules) == len(kb["feature_rules"])
    assert {rule["expected_direction"] for rule in kb["feature_rules"]} <= set(
        kb["conventions"]["expected_direction"]
    )
    assert {rule["knowledge_strength"] for rule in kb["feature_rules"]} <= set(
        kb["conventions"]["knowledge_strength"]
    )
    prepared = prepare_feature_matcher(kb, terminology)
    assert all(
        len({entry["canonical_feature"] for entry in entries}) == 1
        for entries in prepared.exact_index.values()
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CURRENT_LTV", "current ltv"),
        ("Debt-to-Income", "debt to income"),
        ("mthSinceDlq", "mth since dlq"),
        ("maxDpd24m", "max dpd 24m"),
        ("PERF_4Q", "perf 4q"),
    ],
)
def test_normalize_text_preserves_semantic_tokens(raw, expected):
    assert normalize_text(raw) == expected


def test_terminology_expansion_sequence(resources):
    _, terminology = resources
    assert process_terminology("MTH_SINCE_DLQ", terminology)["expanded_text"] == (
        "months since delinquency"
    )
    assert process_terminology("REV_UTIL_PCT", terminology)["expanded_text"] == (
        "revolving utilization percentage"
    )
    assert process_terminology("MAX_DPD_24M", terminology)["expanded_text"] == (
        "maximum days past due 24 months"
    )


@pytest.mark.parametrize(
    ("feature_name", "canonical", "source"),
    [
        ("CURRENT_LTV", "loan_to_value", "representation"),
        ("credit_utilization", "utilization", "representation"),
        ("DSCR", "debt_service_coverage", "representation"),
        ("VACANCY_RATE", "property_occupancy", "inverse_representation"),
    ],
)
def test_deterministic_matches(resources, feature_name, canonical, source):
    kb, terminology = resources
    result = match_feature_to_kb(feature_name, None, kb, terminology)
    assert result["match_status"] == "exact_match"
    assert result["deterministic_match"]["canonical_feature"] == canonical
    assert result["deterministic_match"]["match_source"] == source


@pytest.mark.parametrize(
    ("feature_name", "description", "canonical"),
    [
        (
            "MTH_SINCE_DLQ",
            "Number of months since most recent delinquency",
            "delinquency_recency",
        ),
        (
            "REV_CR_BAL_PCT_AVAIL",
            "Percentage of available revolving credit currently drawn",
            "utilization",
        ),
        (
            "MAX_DPD_24M",
            "Maximum days past due observed during the previous 24 months",
            "delinquency_severity",
        ),
        (
            "CNT_DLQ_12M",
            "Number of delinquency events during the previous 12 months",
            "delinquency_frequency",
        ),
        (
            "DEBT_TO_ASSETS",
            "Total debt divided by total assets",
            "financial_leverage",
        ),
    ],
)
def test_abbreviation_and_nlp_cases_rank_expected_first(
    resources, feature_name, description, canonical
):
    kb, terminology = resources
    result = match_feature_to_kb(feature_name, description, kb, terminology)
    resolved = result["deterministic_match"]
    actual = resolved["canonical_feature"] if resolved else result["top_candidates"][0][
        "canonical_feature"
    ]
    assert actual == canonical
    if feature_name == "DEBT_TO_ASSETS":
        assert actual != "debt_service_burden"


@pytest.mark.parametrize(
    "description",
    [
        "Current outstanding mortgage balance divided by current property value",
        "Combined first and second lien balances divided by current property value",
    ],
)
def test_cltv_remains_ambiguous_while_loan_to_value_ranks_first(resources, description):
    kb, terminology = resources
    result = match_feature_to_kb("CLTV", description, kb, terminology)
    assert result["match_status"] == "candidate_match"
    assert result["top_candidates"][0]["canonical_feature"] == "loan_to_value"
    ambiguity = result["unresolved_ambiguous_terminology"][0]
    assert ambiguity["abbreviation"] == "cltv"
    assert ambiguity["candidates"] == [
        "current loan to value",
        "combined loan to value",
    ]


def test_unknown_feature_has_no_match_without_semantic_evidence(resources):
    kb, terminology = resources
    result = match_feature_to_kb("RANDOM_VAR_X", None, kb, terminology)
    assert result["match_status"] == "no_match"
    assert result["match_method"] == "no_lexical_evidence"
    assert len(result["top_candidates"]) == 3


def test_batch_returns_inspectable_dataframe_and_details(resources):
    kb, terminology = resources
    metadata = pd.DataFrame(
        [
            {"feature_name": "CURRENT_LTV", "description": ""},
            {"feature_name": "RANDOM_VAR_X", "description": float("nan")},
        ]
    )
    summary, details = match_features_to_kb(
        metadata, kb, terminology, include_details=True
    )
    assert list(summary["match_status"]) == ["exact_match", "no_match"]
    assert summary.loc[0, "top_candidate"] == "loan_to_value"
    assert details[1]["original_description"] == ""
    assert len(details) == 2


def test_batch_prepares_terminology_once(resources, monkeypatch):
    kb, terminology = resources
    original = matching_module._terminology_tables
    calls = 0

    def counted(values):
        nonlocal calls
        calls += 1
        return original(values)

    monkeypatch.setattr(matching_module, "_terminology_tables", counted)
    match_features_to_kb(
        [
            {"feature_name": "CURRENT_LTV", "description": ""},
            {"feature_name": "MAX_DPD_24M", "description": "Maximum days past due"},
        ],
        kb,
        terminology,
    )
    assert calls == 1


def test_loader_errors_are_clear():
    invalid_kb = INPUTS_DIR / "test_fixtures" / "invalid_kb.yaml"
    with pytest.raises(ValueError, match="feature_rules"):
        load_kb(invalid_kb)

    invalid_terminology = INPUTS_DIR / "test_fixtures" / "invalid_terminology.yaml"
    with pytest.raises(ValueError, match="domain_acronyms"):
        load_terminology(invalid_terminology)
