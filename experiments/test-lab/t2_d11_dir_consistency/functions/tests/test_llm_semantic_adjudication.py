from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid

import pandas as pd
import pytest

from functions.feature_matching import (
    load_kb,
    load_terminology,
    match_feature_to_kb,
)
from functions.llm_semantic_adjudication import (
    ADJUDICATION_CONTRACT_VERSION,
    CONTRACT_VERSION_V0_1,
    CONTRACT_VERSION_V0_2,
    PROMPT_VERSION,
    PROMPT_VERSION_V0_1,
    PROMPT_VERSION_V0_2,
    AdjudicationCall,
    OpenAISemanticAdjudicator,
    build_adjudication_input,
    build_case_message,
    create_openai_client,
    load_adjudication_prompt,
)
from functions.llm_semantic_adjudication_validation import (
    adjudication_metrics,
    attach_adjudication_ground_truth,
    compare_adjudication_versions,
    run_adjudication_predictions,
)
from functions.semantic_adjudication_contract import (
    AdjudicationInput,
    AdjudicationOutput,
    Candidate,
    InputFeature,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = EXPERIMENT_ROOT / "prompts" / "semantic_feature_adjudication_v0_1.txt"
PROMPT_V0_2_PATH = EXPERIMENT_ROOT / "prompts" / "semantic_feature_adjudication_v0_2.txt"


@pytest.fixture(scope="module")
def resources():
    kb = load_kb(EXPERIMENT_ROOT / "kb" / "pd_directionality_kb_v0_3.yaml")
    terminology = load_terminology(
        EXPERIMENT_ROOT / "kb" / "credit_risk_abbreviations_v0_2.yaml"
    )
    return kb, terminology


@pytest.fixture
def checkpoint_path():
    path = Path(__file__).parent / f".adjudication-checkpoint-{uuid.uuid4().hex}.jsonl"
    yield path
    path.unlink(missing_ok=True)


def simple_input() -> AdjudicationInput:
    return AdjudicationInput(
        input_feature=InputFeature(name="DTI_CURR", description="Current debt to income"),
        candidates=[
            Candidate(
                canonical_feature="debt_service_burden",
                definition="Debt payment burden relative to income.",
                representations=["debt-to-income ratio"],
            )
        ],
    )


def test_prompt_loader_returns_fixed_file_verbatim():
    assert load_adjudication_prompt(PROMPT_PATH) == PROMPT_PATH.read_text(encoding="utf-8")


def test_v02_prompt_has_ordered_precedence_without_case_specific_names():
    prompt = load_adjudication_prompt(PROMPT_V0_2_PATH)
    step_1 = prompt.index("STEP 1 — CAN THE FEATURE BE UNDERSTOOD?")
    step_2 = prompt.index("STEP 2 — IS NUMERIC DIRECTIONALITY MEANINGFUL?")
    step_3 = prompt.index("STEP 3 — DOES A SUPPLIED CANDIDATE REPRESENT THE FEATURE?")
    assert step_1 < step_2 < step_3
    assert "If NO, return INSUFFICIENT_CONTEXT" in prompt
    assert "If NO, return NOT_DIRECTIONAL" in prompt
    assert "If NO, return NO_CANDIDATE_MATCH" in prompt
    for case_name in (
        "RR_METRIC",
        "LOAN_PURPOSE",
        "INDUSTRY_CODE",
        "CURRENCY_CODE",
        "ORIGINATION_CHANNEL",
        "RANDOM_VAR_X",
    ):
        assert case_name not in prompt


def test_prompt_loader_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        load_adjudication_prompt(EXPERIMENT_ROOT / "inputs" / "missing_prompt.txt")


def test_azure_responses_client_uses_v1_route(monkeypatch):
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT",
        "https://example.services.ai.azure.com/openai/v1/responses",
    )
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    client = create_openai_client()
    assert client.base_url.path == "/openai/v1/"


def test_dynamic_message_contains_only_case_contract_fields():
    message = build_case_message(simple_input())
    assert "INPUT FEATURE" in message
    assert "CANDIDATE 1" in message
    assert "debt_service_burden" in message
    for prohibited in (
        "rapidfuzz_score",
        "tfidf_cosine_score",
        "combined_similarity_score",
        "expected_direction",
        "knowledge_strength",
        "target_rate",
    ):
        assert prohibited not in message.casefold()


def test_adjudication_input_uses_kb_semantics_but_not_matcher_scores(resources):
    kb, terminology = resources
    matcher_result = match_feature_to_kb(
        "DTI_CURR", "Current debt divided by income", kb, terminology, top_n=3
    )
    adjudication_input = build_adjudication_input(
        "DTI_CURR", "Current debt divided by income", matcher_result, kb
    )
    payload = adjudication_input.model_dump()
    assert len(payload["candidates"]) == 3
    assert set(payload["candidates"][0]) == {
        "canonical_feature",
        "definition",
        "representations",
        "inverse_representations",
    }


def test_responses_api_structured_output_is_parsed_and_store_is_disabled():
    output = AdjudicationOutput(
        decision="MATCH",
        selected_candidate="debt_service_burden",
        representation_orientation="SAME",
        reason="The feature is a debt-to-income burden ratio.",
    )

    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(output_parsed=output, id="resp_test")

    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    adjudicator = OpenAISemanticAdjudicator(
        client=client, prompt="fixed prompt", model="gpt-5.4-mini"
    )
    call = adjudicator.adjudicate(simple_input())
    assert call.output is output
    assert call.response_id == "resp_test"
    assert responses.kwargs["text_format"] is AdjudicationOutput
    assert responses.kwargs["instructions"] == "fixed prompt"
    assert responses.kwargs["store"] is False


def test_v01_metadata_remains_explicitly_reproducible():
    client = SimpleNamespace(responses=SimpleNamespace())
    adjudicator = OpenAISemanticAdjudicator(
        client=client,
        prompt="v0.1 prompt",
        prompt_version=PROMPT_VERSION_V0_1,
        contract_version=CONTRACT_VERSION_V0_1,
    )
    assert adjudicator.prompt_version == PROMPT_VERSION_V0_1
    assert adjudicator.contract_version == CONTRACT_VERSION_V0_1
    assert PROMPT_VERSION == PROMPT_VERSION_V0_2
    assert ADJUDICATION_CONTRACT_VERSION == CONTRACT_VERSION_V0_2


class FakeAdjudicator:
    model = "gpt-5.4-mini"
    prompt_version = PROMPT_VERSION
    contract_version = ADJUDICATION_CONTRACT_VERSION

    def __init__(self, selected: str | None = None):
        self.calls: list[AdjudicationInput] = []
        self.selected = selected

    def adjudicate(self, adjudication_input: AdjudicationInput) -> AdjudicationCall:
        self.calls.append(adjudication_input)
        selected = self.selected or adjudication_input.candidates[0].canonical_feature
        return AdjudicationCall(
            output=AdjudicationOutput(
                decision="MATCH",
                selected_candidate=selected,
                representation_orientation="SAME",
                reason="Mock semantic selection.",
            ),
            response_id="resp_mock",
        )


def test_exact_match_bypasses_llm_and_candidate_match_routes_to_llm(resources):
    kb, terminology = resources
    adjudicator = FakeAdjudicator()
    features = pd.DataFrame(
        [
            {"feature_name": "CURRENT_LTV", "description": ""},
            {"feature_name": "DTI_CURR", "description": "Current debt divided by income"},
        ]
    )
    predictions = run_adjudication_predictions(
        features, kb, terminology, adjudicator
    )
    assert predictions["adjudication_used"].tolist() == [False, True]
    assert predictions["user_review_required"].tolist() == [False, True]
    assert len(adjudicator.calls) == 1
    assert set(adjudicator.calls[0].model_dump()) == {"input_feature", "candidates"}
    assert predictions["knowledge_base_version"].tolist() == ["0.3", "0.3"]


def test_adjudication_calls_use_bounded_concurrency_and_preserve_order(resources):
    kb, terminology = resources

    class TrackingAdjudicator(FakeAdjudicator):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def adjudicate(self, adjudication_input):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return super().adjudicate(adjudication_input)
            finally:
                with self.lock:
                    self.active -= 1

    features = pd.DataFrame(
        [
            {"feature_name": "DTI_CURR", "description": "Debt divided by income"},
            {"feature_name": "RR_METRIC", "description": "Recovery-related metric"},
            {"feature_name": "BORROWER_ZIP_CODE", "description": "Postal code"},
            {"feature_name": "MONTHLY_DEBT_INC", "description": "Monthly debt and income"},
        ]
    )
    adjudicator = TrackingAdjudicator()

    predictions = run_adjudication_predictions(
        features, kb, terminology, adjudicator, max_concurrency=2
    )

    assert adjudicator.max_active == 2
    assert predictions["feature_name"].tolist() == features["feature_name"].tolist()


def test_valid_adjudications_are_checkpointed_and_reused(resources, checkpoint_path):
    kb, terminology = resources
    features = pd.DataFrame(
        [
            {"feature_name": "DTI_CURR", "description": "Debt divided by income"},
            {"feature_name": "RR_METRIC", "description": "Recovery-related metric"},
        ]
    )
    first_adjudicator = FakeAdjudicator()

    first = run_adjudication_predictions(
        features,
        kb,
        terminology,
        first_adjudicator,
        max_concurrency=2,
        checkpoint_path=checkpoint_path,
    )
    second_adjudicator = FakeAdjudicator()
    second = run_adjudication_predictions(
        features,
        kb,
        terminology,
        second_adjudicator,
        max_concurrency=2,
        checkpoint_path=checkpoint_path,
    )

    assert len(first_adjudicator.calls) == 2
    assert len(second_adjudicator.calls) == 0
    assert first["checkpoint_hit"].tolist() == [False, False]
    assert second["checkpoint_hit"].tolist() == [True, True]
    assert (first["api_call_seconds"] >= 0).all()
    assert second["api_call_seconds"].tolist() == [0.0, 0.0]
    assert second["final_canonical_feature"].tolist() == first[
        "final_canonical_feature"
    ].tolist()
    assert len(checkpoint_path.read_text(encoding="utf-8").splitlines()) == 2


def test_invalid_contract_output_is_not_checkpointed(resources, checkpoint_path):
    kb, terminology = resources
    features = pd.DataFrame(
        [{"feature_name": "DTI_CURR", "description": "Debt divided by income"}]
    )
    invalid = run_adjudication_predictions(
        features,
        kb,
        terminology,
        FakeAdjudicator(selected="invented_feature"),
        checkpoint_path=checkpoint_path,
    )
    assert not checkpoint_path.exists()
    replacement = FakeAdjudicator()
    valid = run_adjudication_predictions(
        features,
        kb,
        terminology,
        replacement,
        checkpoint_path=checkpoint_path,
    )

    assert bool(invalid.loc[0, "contract_valid"]) is False
    assert len(replacement.calls) == 1
    assert bool(valid.loc[0, "contract_valid"]) is True
    assert checkpoint_path.is_file()


@pytest.mark.parametrize("max_concurrency", [0, -1, True, 1.5])
def test_max_concurrency_must_be_a_positive_integer(resources, max_concurrency):
    kb, terminology = resources
    with pytest.raises(ValueError, match="max_concurrency"):
        run_adjudication_predictions(
            pd.DataFrame([{"feature_name": "DTI_CURR", "description": "Debt and income"}]),
            kb,
            terminology,
            FakeAdjudicator(),
            max_concurrency=max_concurrency,
        )


def test_invented_candidate_is_recorded_not_repaired(resources):
    kb, terminology = resources
    predictions = run_adjudication_predictions(
        pd.DataFrame(
            [{"feature_name": "DTI_CURR", "description": "Current debt divided by income"}]
        ),
        kb,
        terminology,
        FakeAdjudicator(selected="invented_feature"),
    )
    row = predictions.iloc[0]
    assert bool(row["invented_candidate"]) is True
    assert bool(row["contract_valid"]) is False
    assert pd.isna(row["final_canonical_feature"])
    assert "was not supplied" in row["validation_error"]


def test_labels_are_attached_only_after_predictions_and_metrics_are_computed(resources):
    kb, terminology = resources
    features = pd.DataFrame(
        [{"feature_name": "CURRENT_LTV", "description": "", "secret_label": "never passed"}]
    )
    adjudicator = FakeAdjudicator()
    predictions = run_adjudication_predictions(features, kb, terminology, adjudicator)
    assert "secret_label" not in predictions
    validation = pd.DataFrame(
        [
            {
                "feature_name": "CURRENT_LTV",
                "description": "",
                "expected_canonical_feature": "loan_to_value",
                "expected_match_type": "match",
                "expected_representation_type": "same_orientation",
                "test_category": "easy",
            }
        ]
    )
    evaluation = attach_adjudication_ground_truth(predictions, validation)
    metrics = adjudication_metrics(evaluation).set_index("metric")
    assert metrics.loc["deterministic_cases_bypassing_llm", "count"] == 1
    assert metrics.loc["overall_canonical_feature_accuracy", "rate"] == 1.0


def test_version_comparison_classifies_taxonomy_corrections():
    baseline_path = (
        EXPERIMENT_ROOT / "output" / "llm_semantic_adjudication_results_v0_1.csv"
    )
    baseline = pd.read_csv(baseline_path, keep_default_na=False)
    challenger = baseline.copy()
    taxonomy = challenger["evaluation_expected_decision"].eq("NOT_DIRECTIONAL")
    challenger.loc[taxonomy, "llm_decision"] = "NOT_DIRECTIONAL"
    challenger.loc[taxonomy, "final_decision"] = "NOT_DIRECTIONAL"
    challenger.loc[taxonomy, "decision_correct"] = True
    challenger.loc[taxonomy, "prompt_version"] = PROMPT_VERSION_V0_2
    challenger.loc[taxonomy, "adjudication_contract_version"] = CONTRACT_VERSION_V0_2

    comparison, changes = compare_adjudication_versions(baseline, challenger)
    comparison = comparison.set_index("metric")
    assert comparison.loc["decision_taxonomy_accuracy", "v0_1_count"] == 66
    assert comparison.loc["decision_taxonomy_accuracy", "v0_2_count"] == 75
    assert len(changes) == 9
    assert set(changes["change_classification"]) == {"intended taxonomy correction"}
