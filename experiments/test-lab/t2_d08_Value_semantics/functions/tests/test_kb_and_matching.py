from pathlib import Path

import pandas as pd

from functions.role_matching import match_variable_to_roles, normalize_text, process_terminology


ROOT = Path(__file__).resolve().parents[2]


def test_kb_shape_and_non_enforcing_context(kb):
    entries = [entry for rule in kb["rules"] for entry in rule["entries"]]
    assert len(kb["semantic_roles"]) == 36
    assert len(kb["shared_guards"]) == 11
    assert len(kb["rules"]) == 3
    assert len(entries) == 25
    assert kb["rule_routing_output"]["routing_status_domain"] == ["READY_FOR_EVALUATION", "UNSCOPED"]
    assert "provenance" not in kb and "bindings" not in kb
    assert kb["matching_contract"]["candidate_generation"]["detailed_candidate_subset"]["hard_maximum_candidates"] is None


def test_production_role_enum_matches_kb(kb):
    assert kb["matching_contract"]["input_fields"]["role"]["allowed_values"] == [
        "identifier", "period", "target", "feature", "score", "weight", "date", "ignore", "group"
    ]


def test_normalization_and_abbreviation_expansion(terminology):
    assert normalize_text("CURR_DPD") == "curr dpd"
    assert process_terminology("CURR_DPD", terminology)["expanded_text"] == "current days past due"
    assert process_terminology("CCF", terminology)["expanded_text"] == "credit conversion factor"
    assert process_terminology("CURR_EXP", terminology)["expanded_text"] == "current exposure"
    assert process_terminology("REV", terminology)["unresolved_ambiguities"]


def test_unique_exact_match_bypasses_adjudication(prepared):
    result = match_variable_to_roles(
        {"name": "CURR_DPD", "description": "Current contractual days past due", "data_type": "integer", "role": "feature"},
        prepared,
    )
    assert result["match_status"] == "exact_match"
    assert result["exact_match"]["role"] == "arrears_measure"


def test_shared_default_flag_alias_requires_adjudication(prepared):
    result = match_variable_to_roles(
        {"name": "DEFAULT_FLAG", "description": "Fixed at zero because default rows are excluded", "data_type": "integer", "role": "target"},
        prepared,
    )
    assert result["match_status"] == "alias_collision"
    assert {"default_event", "construction_constants"} <= set(result["exact_collision_roles"])
    assert "construction_constants" in {item["role"] for item in result["detailed_candidates"]}


def test_adaptive_candidates_retain_full_catalog(prepared):
    result = match_variable_to_roles(
        {"name": "BAL_AMT", "description": "Outstanding principal currently drawn under revolving line", "data_type": "float", "role": "feature"},
        prepared,
    )
    assert len(result["full_catalog"]) == 36
    assert len(result["detailed_candidates"]) >= 5
    assert len(result["detailed_candidates"]) <= 12
    assert "drawn_balance" in {item["role"] for item in result["detailed_candidates"]}


def test_benchmark_expected_roles_have_detailed_candidate_recall(prepared):
    benchmark = pd.read_csv(ROOT / "inputs" / "test_fixtures" / "role_matching_benchmark_v0_2.csv", keep_default_na=False)
    expected_cases = benchmark.loc[benchmark["expected_primary_role"].ne("")]
    misses = []
    for row in expected_cases.to_dict(orient="records"):
        result = match_variable_to_roles(
            {"name": row["column_name"], "description": row["description"], "data_type": row["data_type"], "role": row["role"]},
            prepared,
        )
        candidates = (
            {result["exact_match"]["role"]}
            if result["exact_match"]
            else {item["role"] for item in result["detailed_candidates"]}
        )
        if row["expected_primary_role"] not in candidates:
            misses.append((row["case_id"], row["expected_primary_role"], sorted(candidates)))
    assert not misses
