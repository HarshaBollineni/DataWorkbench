from __future__ import annotations

import pandas as pd
import pytest

from domains.test_lab.diagnostics.t2_d08_value_semantics import knowledge
from domains.test_lab.diagnostics.t2_d08_value_semantics.adjudication import RoleAdjudicationCall
from domains.test_lab.diagnostics.t2_d08_value_semantics.adjudication_contract import (
    RoleAdjudicationOutput, build_adjudication_input,
)
from domains.test_lab.diagnostics.t2_d08_value_semantics.manifest import (
    _adjudicate_with_expansion, _validate_declarations,
)
from domains.test_lab.diagnostics.t2_d08_value_semantics.matching import match_variable_to_roles
from domains.test_lab.shared.run_state import ManifestError
from domains.test_lab.diagnostics.t2_d08_value_semantics.engine import (
    CLAIM_COLUMNS,
    execute_value_semantics,
    resolve_precedence,
)


def _dictionary(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {"column_name": column, "description": column, "data_type": "object", "role": "feature"}
        for column in columns
    ])


def test_active_resources_preserve_contract_and_enhanced_terminology():
    value_kb, terminology, _matcher = knowledge.resources()
    assert str(value_kb["metadata"]["version"]) == "0.2"
    assert str(terminology["metadata"]["version"]) == "0.3"
    assert len(value_kb["semantic_roles"]) == 36
    assert {rule["tag_assigned"] for rule in value_kb["rules"]} == {
        "CENSORED", "STALE_FROZEN", "NOT_APPLICABLE",
    }
    assert terminology["standard_abbreviations"]["rvl"] == "revolving"


def test_declared_sentinel_executes_when_variance_prerequisites_are_unscoped():
    kb = knowledge.resources()[0]
    data = pd.DataFrame({"ROW_ID": ["r1", "r2"], "INCOME": [-999, 1250]})
    result = execute_value_semantics(
        data,
        _dictionary(list(data.columns)),
        {"INCOME": ["income_measure"]},
        {
            "variance_window": 3,
            "minimum_row_count": 10,
            "field_specific_sentinel_definitions": {"INCOME": -999},
        },
        kb,
    )
    assert result.execution_plan["routing_status"].tolist() == [
        "UNSCOPED", "READY_FOR_EVALUATION",
    ]
    assert result.resolved_cell_tags[["row_reference", "input_variable", "tag"]].to_dict("records") == [{
        "row_reference": "r1", "input_variable": "INCOME", "tag": "STALE_FROZEN",
    }]
    assert result.assessment_ledger["status"].eq("UNSCOPED").sum() == 1


def test_unfinished_lgd_outcome_is_censored_without_treating_finished_rows_as_valid():
    kb = knowledge.resources()[0]
    data = pd.DataFrame({
        "ROW_ID": ["r1", "r2"], "RECOVERY": [100, 250],
        "WORKOUT_STATE": ["OPEN", "FINISHED"],
    })
    result = execute_value_semantics(
        data, _dictionary(list(data.columns)),
        {"RECOVERY": ["outcome_values"], "WORKOUT_STATE": ["outcome_state"]},
        {
            "finished_outcome_states": ["FINISHED"],
            "outcome_state_domain": ["OPEN", "FINISHED"],
            "interim_outcome_value_policy": "retain_and_tag",
        },
        kb,
    )
    assert result.resolved_cell_tags[["row_reference", "tag"]].to_dict("records") == [
        {"row_reference": "r1", "tag": "CENSORED"},
    ]
    assert result.assessment_ledger.loc[
        result.assessment_ledger["row_reference"].eq("r2"), "status"
    ].item() == "NO_TAG"


def test_fixed_rate_spread_is_not_applicable_but_floating_rate_spread_is_no_tag():
    kb = knowledge.resources()[0]
    data = pd.DataFrame({
        "ROW_ID": ["r1", "r2"], "SPREAD": [175, 180],
        "RATE_BASIS": ["FIXED", "FLOATING"],
    })
    result = execute_value_semantics(
        data, _dictionary(list(data.columns)),
        {"SPREAD": ["spread_over_benchmark"], "RATE_BASIS": ["rate_basis"]},
        {}, kb,
    )
    assert result.resolved_cell_tags[["row_reference", "tag"]].to_dict("records") == [
        {"row_reference": "r1", "tag": "NOT_APPLICABLE"},
    ]
    assert result.assessment_ledger.loc[
        result.assessment_ledger["row_reference"].eq("r2"), "status"
    ].item() == "NO_TAG"


def test_ambiguous_indicator_binding_is_unscoped_instead_of_arbitrarily_selected():
    kb = knowledge.resources()[0]
    data = pd.DataFrame({
        "ROW_ID": ["r1"], "LABEL": [None], "PERIOD_A": ["2024-Q4"],
        "PERIOD_B": ["2024-Q4"], "EXIT": ["ACTIVE"],
    })
    result = execute_value_semantics(
        data,
        _dictionary(list(data.columns)),
        {
            "LABEL": ["forward_labels"], "PERIOD_A": ["period"],
            "PERIOD_B": ["period"], "EXIT": ["exit_indicators"],
        },
        {
            "panel_end_period": "2024-Q4",
            "forward_horizon_by_label": {"LABEL": "4 quarters"},
        },
        kb,
    )
    plan = result.execution_plan.iloc[0]
    assert plan["routing_status"] == "UNSCOPED"
    assert "period" in plan["missing_roles"]
    assert result.resolved_cell_tags.empty


def test_structurally_invalid_panel_predicate_fails_closed_as_unscoped():
    kb = knowledge.resources()[0]
    data = pd.DataFrame({"ROW_ID": ["r1"], "DEFAULT_FLAG": [0]})
    result = execute_value_semantics(
        data,
        _dictionary(list(data.columns)),
        {"DEFAULT_FLAG": ["construction_constants"]},
        {"panel_scope_exclusions": ["default observations removed"]},
        kb,
    )
    assert result.resolved_cell_tags.empty
    assert result.execution_plan.iloc[0]["routing_status"] == "UNSCOPED"
    assert result.assessment_ledger.iloc[0]["status"] == "UNSCOPED"


def test_kb_precedence_selects_not_applicable_and_retains_suppressed_claim():
    claims = pd.DataFrame([
        {
            "row_reference": "r1", "input_variable": "FIELD", "matched_role": "role",
            "rule": "censor", "entry": "censor-entry", "tag": "CENSORED",
            "reason_code": "window", "input_value": None, "is_populated": False,
        },
        {
            "row_reference": "r1", "input_variable": "FIELD", "matched_role": "role",
            "rule": "na", "entry": "na-entry", "tag": "NOT_APPLICABLE",
            "reason_code": "not-applicable", "input_value": None, "is_populated": False,
        },
    ], columns=CLAIM_COLUMNS)
    resolved = resolve_precedence(claims)
    assert resolved.iloc[0]["tag"] == "NOT_APPLICABLE"
    assert resolved.iloc[0]["suppressed_tags"] == "CENSORED"
    assert resolved.iloc[0]["claim_count"] == 2


def test_runtime_declaration_shapes_are_validated_before_freeze():
    _validate_declarations({
        "variance_window": 3, "minimum_row_count": 10,
        "not_applicable_share_threshold": 0.8,
        "field_specific_sentinel_definitions": {"INCOME": -999},
    })
    with pytest.raises(ManifestError, match="minimum_row_count"):
        _validate_declarations({"minimum_row_count": "ten"})
    with pytest.raises(ManifestError, match="forward_horizon_by_label"):
        _validate_declarations({"forward_horizon_by_label": ["4 quarters"]})


def test_ai_adjudication_validates_and_expands_the_bounded_catalog():
    kb, _terminology, prepared = knowledge.resources()
    match = match_variable_to_roles({
        "name": "STATUS_CD", "description": "Operational processing status",
        "data_type": "string", "role": "ignore", "business_name": "",
        "allowed_values": "", "model_type": "", "use_cases": [], "product": "",
    }, prepared)
    input_ = build_adjudication_input(match, kb)
    detailed = {row.role for row in input_.detailed_candidates}
    requested = next(row.role for row in input_.full_catalog if row.role not in detailed)

    class ExpandingAdjudicator:
        def __init__(self):
            self.inputs = []

        def adjudicate(self, current):
            self.inputs.append(current)
            if len(self.inputs) == 1:
                output = RoleAdjudicationOutput(
                    decision="CANDIDATE_SET_INCOMPLETE",
                    requested_expansion_roles=[requested],
                    reason="The full catalog identifies a plausible role requiring details.",
                )
            else:
                output = RoleAdjudicationOutput(
                    decision="MATCH", primary_role=requested,
                    reason="The expanded definition establishes the role.",
                )
            return RoleAdjudicationCall(output=output)

    adjudicator = ExpandingAdjudicator()
    _call, output, expanded_input, attempts, calls = _adjudicate_with_expansion(
        adjudicator, input_, kb,
    )
    assert output.primary_role == requested
    assert requested in {row.role for row in expanded_input.detailed_candidates}
    assert [row["decision"] for row in attempts] == ["CANDIDATE_SET_INCOMPLETE", "MATCH"]
    assert len(calls) == 2


def test_ai_adjudication_rejects_a_role_outside_the_detailed_candidates():
    kb, _terminology, prepared = knowledge.resources()
    match = match_variable_to_roles({
        "name": "STATUS_CD", "description": "Operational processing status",
        "data_type": "string", "role": "ignore", "business_name": "",
        "allowed_values": "", "model_type": "", "use_cases": [], "product": "",
    }, prepared)
    input_ = build_adjudication_input(match, kb)
    detailed = {row.role for row in input_.detailed_candidates}
    invalid = next(row.role for row in input_.full_catalog if row.role not in detailed)

    class InvalidAdjudicator:
        def adjudicate(self, _current):
            return RoleAdjudicationCall(output=RoleAdjudicationOutput(
                decision="MATCH", primary_role=invalid, reason="Invalid unsupported selection.",
            ))

    with pytest.raises(ValueError, match="not supplied with details"):
        _adjudicate_with_expansion(InvalidAdjudicator(), input_, kb)
