from __future__ import annotations

import pytest
from pydantic import ValidationError

from functions.semantic_adjudication_contract import (
    AdjudicationDecision,
    AdjudicationInput,
    AdjudicationOutput,
    AdjudicationResultValidationError,
    Candidate,
    InputFeature,
    RepresentationOrientation,
    determine_review_required,
    validate_adjudication_result,
)


def candidate(name: str) -> Candidate:
    return Candidate(
        canonical_feature=name,
        definition=f"Definition of {name}",
        representations=[name, name.replace("_", " ")],
    )


@pytest.fixture
def dti_input() -> AdjudicationInput:
    return AdjudicationInput(
        input_feature=InputFeature(
            name="DTI_CURR", description="Current debt-to-income burden"
        ),
        candidates=[
            candidate("income_capacity"),
            candidate("debt_service_burden"),
            candidate("financial_leverage"),
        ],
    )


def match_output(selected: str, orientation: str = "SAME") -> AdjudicationOutput:
    return AdjudicationOutput(
        decision="MATCH",
        selected_candidate=selected,
        representation_orientation=orientation,
        reason="The feature describes the selected economic concept.",
    )


def non_match_output(decision: str) -> AdjudicationOutput:
    return AdjudicationOutput(
        decision=decision,
        selected_candidate=None,
        representation_orientation=None,
        reason="The supplied metadata supports this outcome.",
    )


def test_valid_match(dti_input):
    output = match_output("debt_service_burden")
    assert validate_adjudication_result(dti_input, output) is output
    assert output.representation_orientation is RepresentationOrientation.SAME


def test_invented_candidate_is_rejected(dti_input):
    with pytest.raises(AdjudicationResultValidationError, match="was not supplied"):
        validate_adjudication_result(dti_input, match_output("debt_to_income_ratio"))


def test_valid_inverse_match():
    adjudication_input = AdjudicationInput(
        input_feature=InputFeature(name="VACANCY_RATE"),
        candidates=[candidate("property_occupancy")],
    )
    output = match_output("property_occupancy", "INVERSE")
    assert validate_adjudication_result(adjudication_input, output) is output


def test_valid_undetermined_orientation():
    adjudication_input = AdjudicationInput(
        input_feature=InputFeature(name="RISK_SCORE", description="Internal risk score"),
        candidates=[candidate("credit_quality_score")],
    )
    output = match_output("credit_quality_score", "UNDETERMINED")
    assert validate_adjudication_result(adjudication_input, output) is output


@pytest.mark.parametrize(
    "decision",
    ["NO_CANDIDATE_MATCH", "NOT_DIRECTIONAL", "INSUFFICIENT_CONTEXT"],
)
def test_valid_non_match_decisions(decision, dti_input):
    output = non_match_output(decision)
    assert validate_adjudication_result(dti_input, output) is output


def test_not_directional_property_type():
    adjudication_input = AdjudicationInput(
        input_feature=InputFeature(name="PROPERTY_TYPE"),
        candidates=[candidate("property_occupancy")],
    )
    assert validate_adjudication_result(
        adjudication_input, non_match_output("NOT_DIRECTIONAL")
    ).decision is AdjudicationDecision.NOT_DIRECTIONAL


def test_insufficient_context_without_description():
    adjudication_input = AdjudicationInput(
        input_feature=InputFeature(name="X_VAR_017"),
        candidates=[candidate("income_capacity")],
    )
    assert validate_adjudication_result(
        adjudication_input, non_match_output("INSUFFICIENT_CONTEXT")
    ).decision is AdjudicationDecision.INSUFFICIENT_CONTEXT


def test_non_match_with_candidate_is_rejected():
    with pytest.raises(ValidationError, match="must be null"):
        AdjudicationOutput(
            decision="NOT_DIRECTIONAL",
            selected_candidate="property_occupancy",
            representation_orientation=None,
            reason="Property type is categorical.",
        )


def test_match_without_orientation_is_rejected():
    with pytest.raises(ValidationError, match="required when decision is MATCH"):
        AdjudicationOutput(
            decision="MATCH",
            selected_candidate="loan_to_value",
            representation_orientation=None,
            reason="It represents loan to value.",
        )


@pytest.mark.parametrize("count", [0, 4])
def test_candidate_count_must_be_one_to_three(count):
    with pytest.raises(ValidationError):
        AdjudicationInput(
            input_feature=InputFeature(name="FEATURE"),
            candidates=[candidate(f"candidate_{index}") for index in range(count)],
        )


def test_duplicate_canonical_features_are_rejected():
    with pytest.raises(ValidationError, match="must be unique"):
        AdjudicationInput(
            input_feature=InputFeature(name="FEATURE"),
            candidates=[candidate("duplicate"), candidate("duplicate")],
        )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "expected_direction",
        "knowledge_strength",
        "kb_rationale",
        "similarity_score",
        "pearson_correlation",
        "target",
        "anchor",
    ],
)
def test_forbidden_input_fields_are_rejected(forbidden_field):
    payload = {
        "input_feature": {"name": "FEATURE"},
        "candidates": [candidate("candidate").model_dump()],
        forbidden_field: "leaked",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AdjudicationInput.model_validate(payload)


@pytest.mark.parametrize("extra_field", ["confidence", "review_required"])
def test_output_contains_no_extra_fields(extra_field):
    payload = {
        "decision": "NO_CANDIDATE_MATCH",
        "selected_candidate": None,
        "representation_orientation": None,
        "reason": "No supplied candidate matches.",
        extra_field: True,
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AdjudicationOutput.model_validate(payload)


def test_reason_must_be_nonempty():
    with pytest.raises(ValidationError):
        AdjudicationOutput(
            decision="NO_CANDIDATE_MATCH",
            selected_candidate=None,
            representation_orientation=None,
            reason="   ",
        )


def test_review_policy_is_system_controlled():
    assert determine_review_required("deterministic_exact", False) is False
    assert determine_review_required("canonical", False) is False
    assert determine_review_required("semantic_candidate", True) is True
    assert determine_review_required("future_source", False) is True
