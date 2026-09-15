from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND = Path(__file__).resolve().parents[5]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import (  # noqa: E402
    adjudication,
)
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.adjudication_contract import (  # noqa: E402
    AdjudicationOutput,
)


def test_configuration_uses_application_dotenv_with_runtime_override(tmp_path, monkeypatch):
    backend_env = tmp_path / ".env"
    backend_env.touch()
    monkeypatch.setattr(adjudication, "_backend_env_path", lambda: backend_env)
    for key in (
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT", "OPENAI_MODEL", "AZURE_OPENAI_API_VERSION",
        "AI_MAX_RETRIES",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_REQUEST_TIMEOUT", "37")
    monkeypatch.setattr(adjudication, "dotenv_values", lambda path: {
        "AZURE_OPENAI_ENDPOINT": "https://unit-test.openai.azure.com/openai/v1/responses",
        "AZURE_OPENAI_API_KEY": "non-secret-unit-test-key",
        "AZURE_OPENAI_DEPLOYMENT": "experiment-deployment",
        "AZURE_OPENAI_API_VERSION": "v1",
        "AI_REQUEST_TIMEOUT": "31",
        "AI_MAX_RETRIES": "2",
    })

    configuration = adjudication._configuration()

    assert configuration == {
        "endpoint": "https://unit-test.openai.azure.com/openai/v1/responses",
        "api_key": "non-secret-unit-test-key",
        "model": "experiment-deployment",
        "api_version": "v1",
        "timeout": 37.0,
        "max_retries": 2,
        "configuration_source": "application_dotenv",
    }
    assert adjudication.configuration_metadata() == {
        "model": "experiment-deployment",
        "provider": "azure_openai_v1",
        "provider_api_version": "v1",
        "configuration_source": "application_dotenv",
    }


def test_adjudicate_uses_responses_structured_output(monkeypatch):
    configuration = {
        "endpoint": "https://unit-test.openai.azure.com/openai/v1/responses?api-version=preview",
        "api_key": "non-secret-unit-test-key",
        "model": "configured-experiment-deployment",
        "api_version": "v1",
        "timeout": 45.0,
        "max_retries": 1,
        "configuration_source": "application_dotenv",
    }
    constructor_args: dict[str, object] = {}
    request_args: dict[str, object] = {}
    parsed = AdjudicationOutput.model_validate({
        "decision": "MATCH",
        "selected_candidate": "loan_to_value",
        "representation_orientation": "SAME",
        "reason": "The input is a direct loan-to-value representation.",
    })

    class Responses:
        @staticmethod
        def parse(**kwargs):
            request_args.update(kwargs)
            return SimpleNamespace(
                id="response-unit-1",
                output_parsed=parsed,
                usage=SimpleNamespace(input_tokens=17, output_tokens=9),
            )

    client = SimpleNamespace(responses=Responses())

    def fake_openai(**kwargs):
        constructor_args.update(kwargs)
        return client

    monkeypatch.setattr(adjudication, "_configuration", lambda: configuration)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=fake_openai))

    result = adjudication.adjudicate(
        "CURRENT_LTV",
        "Current loan-to-value ratio",
        [{"canonical_feature": "loan_to_value"}],
    )

    assert constructor_args == {
        "base_url": "https://unit-test.openai.azure.com/openai/v1/",
        "api_key": "non-secret-unit-test-key",
        "timeout": 45.0,
        "max_retries": 1,
    }
    assert request_args["model"] == "configured-experiment-deployment"
    assert request_args["text_format"] is AdjudicationOutput
    assert request_args["store"] is False
    assert "instructions" in request_args
    assert request_args["input"][0]["role"] == "user"
    assert result["output"]["reason"] == (
        "The input is a direct loan-to-value representation."
    )
    assert result["model"] == "configured-experiment-deployment"
    assert result["provider"] == "azure_openai_v1"
    assert result["usage"] == {"prompt_tokens": 17, "completion_tokens": 9}
    assert "api_key" not in result
