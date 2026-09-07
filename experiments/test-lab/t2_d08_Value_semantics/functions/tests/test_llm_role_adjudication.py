import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from functions.llm_role_adjudication import (
    CONTRACT_VERSION, PROMPT_VERSION, OpenAIRoleAdjudicator, RoleAdjudicationCall,
    build_case_message, load_adjudication_prompt,
)
from functions.role_adjudication_contract import (
    RoleAdjudicationOutput, RoleDecision, build_adjudication_input,
)
from functions.role_adjudication_validation import (
    adjudication_metrics, attach_ground_truth, benchmark_role_coverage, run_benchmark_predictions,
    validate_benchmark_ground_truth,
)
from functions.role_matching import match_variable_to_roles


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="resp_test", output_parsed=self.output)


def test_openai_boundary_uses_structured_output(prepared):
    match = match_variable_to_roles(
        {"column_name": "STATUS_CD", "description": "Operational processing status", "data_type": "string", "role": "ignore"},
        prepared,
    )
    input_ = build_adjudication_input(match, dict(prepared.kb))
    output = RoleAdjudicationOutput(decision=RoleDecision.NO_CANDIDATE_MATCH, reason="Operational only")
    responses = FakeResponses(output)
    adjudicator = OpenAIRoleAdjudicator(
        client=SimpleNamespace(responses=responses), prompt="fixed prompt", model="test-model",
    )
    call = adjudicator.adjudicate(input_)
    assert call.output == output
    assert responses.kwargs["store"] is False
    assert responses.kwargs["text_format"] is RoleAdjudicationOutput
    assert json.loads(build_case_message(input_))["full_catalog"]


def test_versioned_prompt_is_present():
    prompt = load_adjudication_prompt(
        EXPERIMENT_ROOT / "prompts" / "value_semantics_role_adjudication_v0_2.txt"
    )
    assert "MULTI_ROLE_MATCH" in prompt
    assert PROMPT_VERSION.endswith("v0_2")
    assert CONTRACT_VERSION.endswith("v0_2")


class TruthAdjudicator:
    model = "fake"
    prompt_version = PROMPT_VERSION
    contract_version = CONTRACT_VERSION

    def __init__(self, truth):
        self.truth = truth

    def adjudicate(self, input_):
        row = self.truth[input_.input_variable.name]
        return RoleAdjudicationCall(output=RoleAdjudicationOutput(
            decision=row["expected_decision"],
            primary_role=row["expected_primary_role"] or None,
            secondary_roles=[value for value in row["expected_secondary_roles"].split(";") if value],
            reason="fixture truth fake",
        ))


def test_coverage_and_fake_end_to_end(kb, terminology):
    benchmark = pd.read_csv(
        EXPERIMENT_ROOT / "inputs" / "test_fixtures" / "role_matching_benchmark_v0_2.csv",
        keep_default_na=False,
    )
    coverage = benchmark_role_coverage(benchmark, kb)
    assert len(coverage) == 36
    assert coverage["total_case_count"].gt(0).all()
    sample = benchmark.iloc[[21, 22, 23]].copy()
    truth = sample.set_index("column_name").to_dict("index")
    predictions = run_benchmark_predictions(sample, kb, terminology, TruthAdjudicator(truth))
    evaluation = attach_ground_truth(predictions, sample)
    metrics = adjudication_metrics(evaluation).set_index("metric")
    assert metrics.loc["overall_case_accuracy", "rate"] == 1.0


def test_ground_truth_contract_rejects_match_with_secondary(kb):
    invalid = pd.DataFrame([{
        "case_id": "bad_01", "expected_decision": "MATCH", "expected_primary_role": "realisation_fields",
        "expected_secondary_roles": "outcome_values",
    }])
    import pytest
    with pytest.raises(ValueError, match="MATCH requires exactly one"):
        validate_benchmark_ground_truth(invalid, kb)
