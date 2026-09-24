from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ai.model_registry import ModelDeployment, ModelPolicy  # noqa: E402
from ai.model_runtime import ModelExecution  # noqa: E402
from domains.rca import initial_review  # noqa: E402


def test_review_uses_structured_responses_and_returns_auditable_metadata(monkeypatch):
    deployment = ModelDeployment(
        model_id="gpt-5-6-sol", provider_id="azure-primary",
        deployment="gpt-5.6-sol", model_name="gpt-5.6-sol",
        model_version="2026-07-09", deployment_type="GlobalStandard",
        lifecycle_status="GenerallyAvailable", retirement_date="2028-01-11",
        version_upgrade_policy="OnceCurrentVersionExpired",
        tpm_limit=20000, rpm_limit=20,
    )
    policy = ModelPolicy(
        endpoint="https://unit-test.openai.azure.com/", api_key="not-a-real-key",
        primary=deployment, fallback=None, fallback_on=(), max_fallbacks=0,
        request_timeout=30, request_retries=1,
    )
    request: dict[str, object] = {}
    parsed = initial_review.InitialReviewOutput.model_validate({
        "summary": "The evidence shows material missingness.",
        "observed_signals": ["Null share is elevated."],
        "candidate_hypotheses": [{
            "statement": "A source segment may omit the field.",
            "evidence_basis": "The profile confirms null values.",
            "testable_next_step": "Compare null share by source segment.",
        }],
        "limitations": ["No source lineage was tested."],
        "recommended_next_steps": ["Run segment attribution."],
    })
    response = SimpleNamespace(
        id="resp-unit-1", model="gpt-5.6-sol", output_parsed=parsed,
    )

    class Responses:
        @staticmethod
        def parse(**kwargs):
            request.update(kwargs)
            return response

    def execute(workload, operation, *, policy):
        assert workload == "rca_initial_review"
        value = operation(SimpleNamespace(responses=Responses()), deployment)
        return ModelExecution(value, deployment, [{
            "attempt": 1, "status": "completed", **deployment.public_metadata(),
        }])

    monkeypatch.setattr(initial_review, "load_model_policy", lambda _workload: policy)
    monkeypatch.setattr(initial_review, "execute_with_fallback", execute)

    result = initial_review.review(
        {"case_id": "rca-unit", "test_name": "Completeness", "columns": ["amount"],
         "user_context": ["The feed changed last quarter."]},
        {"found": True, "null_share": 0.4},
    )

    assert request["model"] == "gpt-5.6-sol"
    assert request["text_format"] is initial_review.InitialReviewOutput
    assert request["store"] is False
    assert "The feed changed last quarter." in request["input"][0]["content"][0]["text"]
    assert "unverified background" in request["instructions"]
    assert "source rows" not in request["input"][0]["content"][0]["text"]
    assert result["selected_model"]["model_version"] == "2026-07-09"
    assert result["output"]["candidate_hypotheses"][0]["testable_next_step"]
