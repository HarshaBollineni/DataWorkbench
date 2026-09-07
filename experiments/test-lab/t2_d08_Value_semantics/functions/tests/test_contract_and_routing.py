import pytest
from pydantic import ValidationError

from functions.role_adjudication_contract import (
    RoleAdjudicationInput,
    RoleAdjudicationOutput,
    build_adjudication_input,
    expand_adjudication_input,
    validate_role_adjudication,
)
from functions.role_matching import match_variable_to_roles
from functions.rule_router import route_roles_to_rules
from functions.value_semantics_agent import process_schema


def _unresolved(prepared):
    return match_variable_to_roles(
        {"name": "BAL_AMT", "description": "Outstanding principal currently drawn under revolving line", "data_type": "float", "role": "feature"},
        prepared,
    )


def test_adjudication_contract_has_full_catalog_and_unbounded_details(prepared, kb):
    input_ = build_adjudication_input(_unresolved(prepared), kb)
    assert isinstance(input_, RoleAdjudicationInput)
    assert len(input_.full_catalog) == 36
    assert len(input_.detailed_candidates) >= 5


def test_match_must_select_detailed_candidate(prepared, kb):
    input_ = build_adjudication_input(_unresolved(prepared), kb)
    output = RoleAdjudicationOutput(decision="MATCH", primary_role="invented_role", reason="Invalid selection")
    with pytest.raises(ValueError, match="not supplied with details"):
        validate_role_adjudication(input_, output)


def test_candidate_expansion_must_reference_catalog_role_not_already_detailed(prepared, kb):
    input_ = build_adjudication_input(_unresolved(prepared), kb)
    detailed = {item.role for item in input_.detailed_candidates}
    expandable = next(item.role for item in input_.full_catalog if item.role not in detailed)
    output = RoleAdjudicationOutput(
        decision="CANDIDATE_SET_INCOMPLETE",
        requested_expansion_roles=[expandable],
        reason="The compact catalog shows another plausible role.",
    )
    assert validate_role_adjudication(input_, output) is output
    expanded = expand_adjudication_input(input_, [expandable], kb)
    assert expandable in {item.role for item in expanded.detailed_candidates}


def test_invalid_production_role_is_rejected(prepared):
    with pytest.raises(ValueError, match="Unsupported production role"):
        match_variable_to_roles({"name": "X", "role": "metadata"}, prepared)


def test_one_role_fans_out_to_multiple_rules_without_context_filter(kb):
    declarations = {"variance_window": 3, "minimum_row_count": 10, "non_arrears_trigger_domain": {}}
    routed = route_roles_to_rules(
        ["arrears_measure"], kb,
        available_roles=["arrears_measure", "segment", "period", "non_arrears_trigger"],
        declarations=declarations,
    )
    assert {row["entry"] for row in routed} == {
        "t2_d08_valsim_stale_frozen_arrears",
        "t2_d08_valsim_not_applicable_lgd_arrears",
    }
    assert all(row["routing_status"] == "READY_FOR_EVALUATION" for row in routed)


def test_missing_indicator_produces_unscoped_not_exclusion(kb):
    routed = route_roles_to_rules(["future_drawdown"], kb, available_roles=["future_drawdown"], declarations={})
    assert len(routed) == 2
    assert {row["routing_status"] for row in routed} == {"UNSCOPED"}


def test_agent_exact_bindings_and_rule_fanout(prepared):
    result = process_schema(
        [
            {"name": "CURR_DPD", "description": "Current days past due", "data_type": "integer", "role": "feature"},
            {"name": "RPT_QTR", "description": "Reporting quarter", "data_type": "string", "role": "period"},
            {"name": "PORTF_SEG", "description": "Portfolio segment", "data_type": "string", "role": "group"},
        ],
        prepared,
        declarations={"variance_window": 3, "minimum_row_count": 10},
    )
    assert result["bindings"][0]["roles"] == ["arrears_measure"]
    assert any(row["entry"] == "t2_d08_valsim_stale_frozen_arrears" for row in result["routing"])


def test_agent_expands_candidates_then_routes_adjudicated_role(prepared):
    calls = 0

    def fake_adjudicator(input_):
        nonlocal calls
        calls += 1
        detailed = {item.role for item in input_.detailed_candidates}
        if "construction_constants" not in detailed:
            return RoleAdjudicationOutput(
                decision="CANDIDATE_SET_INCOMPLETE",
                requested_expansion_roles=["construction_constants"],
                reason="The compact catalog contains a panel-construction role.",
            )
        return RoleAdjudicationOutput(
            decision="MATCH",
            primary_role="construction_constants",
            reason="The dictionary says the flag is constant because default rows were excluded.",
        )

    result = process_schema(
        [{"name": "FLAG_X", "description": "Fixed at zero because default rows are excluded", "data_type": "integer", "role": "target"}],
        prepared,
        declarations={"panel_scope_exclusions": ["default rows"]},
        adjudicator=fake_adjudicator,
    )
    assert calls in {1, 2}
    assert result["bindings"][0]["roles"] == ["construction_constants"]
    assert any(row["entry"] == "t2_d08_valsim_not_applicable_pd_construction_constant" for row in result["routing"])


def test_agent_candidate_expansion_is_a_real_second_pass(prepared):
    requested = None
    calls = 0

    def fake_adjudicator(input_):
        nonlocal requested, calls
        calls += 1
        detailed = {item.role for item in input_.detailed_candidates}
        if requested is None:
            requested = next(item.role for item in input_.full_catalog if item.role not in detailed)
            return RoleAdjudicationOutput(decision="CANDIDATE_SET_INCOMPLETE",
                                          requested_expansion_roles=[requested], reason="Need role details")
        return RoleAdjudicationOutput(decision="MATCH", primary_role=requested, reason="Expanded details support it")

    result = process_schema([{"name": "X_UNKNOWN", "description": "Opaque field", "role": "ignore"}],
                            prepared, adjudicator=fake_adjudicator)
    assert calls == 2
    assert result["bindings"][0]["direct_roles"] == [requested]


def test_agent_accepts_adjudicator_object_and_expands_implied_roles(prepared):
    from functions.llm_role_adjudication import RoleAdjudicationCall

    class FakeClient:
        def adjudicate(self, input_):
            return RoleAdjudicationCall(
                output=RoleAdjudicationOutput(decision="MATCH", primary_role="realisation_fields", reason="Sale proceeds"),
                response_id="resp_test", response_model="test-model",
            )

    result = process_schema(
        [{"name": "SALE_VALUE_X", "description": "Gross proceeds from collateral sale", "role": "target"}],
        prepared, adjudicator=FakeClient(),
    )
    assert result["bindings"][0]["direct_roles"] == ["realisation_fields"]
    assert result["bindings"][0]["roles"] == ["realisation_fields", "outcome_values"]
    assert result["bindings"][0]["response_id"] == "resp_test"


def test_schema_cardinality_violation_is_reported(prepared):
    result = process_schema([
        {"name": "FACILITY_ID", "description": "Unique facility identifier", "role": "identifier"},
        {"name": "ACCOUNT_NUMBER", "description": "Unique facility account number", "role": "identifier"},
    ], prepared)
    assert result["cardinality_violations"] == [{
        "role": "entity_id", "cardinality": "one", "input_variables": ["FACILITY_ID", "ACCOUNT_NUMBER"]
    }]


def test_output_contract_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RoleAdjudicationOutput.model_validate(
            {"decision": "NO_CANDIDATE_MATCH", "reason": "No role matches", "confidence": 0.9}
        )
