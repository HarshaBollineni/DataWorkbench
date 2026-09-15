from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ai.model_registry import load_model_policy  # noqa: E402
from ai.model_runtime import ModelExecutionError, execute_with_fallback  # noqa: E402


def _values() -> dict[str, str]:
    common = {
        "AZURE_OPENAI_ENDPOINT": "https://unit-test.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": "not-a-real-key",
        "AI_FALLBACK_WORKLOADS": "all",
        "AI_MAX_MODEL_FALLBACKS": "1",
        "AI_FALLBACK_ON": "timeout,rate_limit,deployment_unavailable",
        "AI_REQUEST_TIMEOUT": "30",
        "AI_MAX_RETRIES": "1",
    }
    for prefix, model, version in (
        ("PRIMARY", "gpt-5.6-sol", "2026-07-09"),
        ("FALLBACK", "gpt-5.4-mini", "2026-03-17"),
    ):
        common.update({
            f"AI_{prefix}_PROVIDER_ID": "azure-primary",
            f"AI_{prefix}_MODEL_ID": model.replace(".", "-"),
            f"AI_{prefix}_DEPLOYMENT": model,
            f"AI_{prefix}_MODEL_NAME": model,
            f"AI_{prefix}_MODEL_VERSION": version,
            f"AI_{prefix}_TPM_LIMIT": "20000",
            f"AI_{prefix}_RPM_LIMIT": "20",
        })
    return common


def test_public_policy_has_primary_and_fallback_without_secrets():
    policy = load_model_policy("rca_initial_review", values=_values())

    public = policy.public_metadata()

    assert public["primary"]["deployment"] == "gpt-5.6-sol"
    assert public["fallback"]["deployment"] == "gpt-5.4-mini"
    assert public["fallback_on"] == ["timeout", "rate_limit", "deployment_unavailable"]
    assert "endpoint" not in str(public).lower()
    assert "not-a-real-key" not in str(public)


def test_retryable_primary_failure_uses_the_single_fallback():
    policy = load_model_policy("rca_initial_review", values=_values())
    called: list[str] = []

    class RateLimitError(Exception):
        status_code = 429

    def operation(_client, deployment):
        called.append(deployment.deployment)
        if len(called) == 1:
            raise RateLimitError("primary throttled")
        return SimpleNamespace(id="fallback-response", model="gpt-5.4-mini", usage=None)

    result = execute_with_fallback(
        "rca_initial_review", operation, policy=policy,
        client_factory=lambda _policy: object(),
    )

    assert called == ["gpt-5.6-sol", "gpt-5.4-mini"]
    assert result.deployment.deployment == "gpt-5.4-mini"
    assert [attempt["status"] for attempt in result.attempts] == ["failed", "completed"]
    assert result.attempts[0]["failure_category"] == "rate_limit"


def test_non_retryable_failure_never_switches_models():
    policy = load_model_policy("rca_initial_review", values=_values())
    called: list[str] = []

    def operation(_client, deployment):
        called.append(deployment.deployment)
        raise ValueError("invalid governed request")

    with pytest.raises(ModelExecutionError) as caught:
        execute_with_fallback(
            "rca_initial_review", operation, policy=policy,
            client_factory=lambda _policy: object(),
        )

    assert called == ["gpt-5.6-sol"]
    assert caught.value.attempts[0]["failure_category"] == "non_retryable"
