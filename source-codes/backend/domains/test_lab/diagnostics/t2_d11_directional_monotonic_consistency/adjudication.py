"""Bounded semantic adjudication using the proven T2-D11 Responses adapter."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values
from pydantic import ValidationError

from .adjudication_contract import (
    AdjudicationInput, AdjudicationOutput, Candidate, InputFeature,
    validate_adjudication_result,
)
from .knowledge import prompt, rule_index

ENV_PATH_VARIABLE = "T2_D11_ENV_FILE"
PROMPT_VERSION = "semantic_feature_adjudication_v0_2"
CONTRACT_VERSION = "semantic_feature_adjudication_contract_v0_1"


class StructuredAdjudicationResponseError(ValueError):
    """Raised when the provider does not return the governed output contract."""


def _experiment_env_path() -> Path:
    return (
        Path(__file__).resolve().parents[6]
        / "experiments"
        / "test-lab"
        / "t2_d11_dir_consistency"
        / ".env"
    )


def _backend_env_path() -> Path:
    return Path(__file__).resolve().parents[4] / ".env"


def _configuration() -> dict[str, Any]:
    """Resolve D11 settings without changing process-global AI configuration.

    In the source workspace the experiment ``.env`` is deliberately authoritative
    for D11. A deployed installation can point ``T2_D11_ENV_FILE`` at its secret
    mount, or supply the same values through the process environment.
    """
    explicit = str(os.getenv(ENV_PATH_VARIABLE) or "").strip()
    selected_path: Path | None = None
    source = "runtime_environment"
    file_overrides_runtime = False
    if explicit:
        selected_path = Path(explicit).expanduser()
        if not selected_path.is_file():
            raise RuntimeError(f"{ENV_PATH_VARIABLE} does not identify a readable file")
        source = "configured_dotenv"
        file_overrides_runtime = True
    elif _experiment_env_path().is_file():
        selected_path = _experiment_env_path()
        source = "t2_d11_experiment_dotenv"
        file_overrides_runtime = True
    elif _backend_env_path().is_file():
        selected_path = _backend_env_path()
        source = "application_dotenv"

    runtime: dict[str, str] = {key: value for key, value in os.environ.items()}
    file_values: dict[str, str] = {}
    if selected_path is not None:
        file_values = {
            key: str(value)
            for key, value in dotenv_values(selected_path).items()
            if value is not None
        }
    values = ({**runtime, **file_values} if file_overrides_runtime
              else {**file_values, **runtime})

    endpoint = str(values.get("AZURE_OPENAI_ENDPOINT") or "").strip()
    api_key = str(values.get("AZURE_OPENAI_API_KEY")
                  or values.get("OPENAI_API_KEY") or "").strip()
    model = str(values.get("AZURE_OPENAI_DEPLOYMENT")
                or values.get("OPENAI_MODEL") or "").strip()
    if not api_key:
        raise RuntimeError("AI credentials are not configured for semantic adjudication")
    if not model:
        raise RuntimeError("An AI deployment is not configured for semantic adjudication")
    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "model": model,
        "api_version": str(values.get("AZURE_OPENAI_API_VERSION") or "configured"),
        "timeout": float(values.get("AI_REQUEST_TIMEOUT") or 90),
        "max_retries": int(values.get("AI_MAX_RETRIES") or 1),
        "configuration_source": source,
    }


def _client(configuration: Mapping[str, Any]) -> Any:
    from openai import OpenAI

    endpoint = str(configuration.get("endpoint") or "")
    if not endpoint:
        return OpenAI(
            api_key=configuration["api_key"],
            timeout=configuration["timeout"],
            max_retries=configuration["max_retries"],
        )
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("The configured AI endpoint is invalid")
    # Foundry may supply either a resource URL or the complete Responses URL.
    # The SDK needs the host-level v1 base because it appends the resource path.
    base_url = urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))
    return OpenAI(
        base_url=base_url,
        api_key=configuration["api_key"],
        timeout=configuration["timeout"],
        max_retries=configuration["max_retries"],
    )


def configuration_metadata() -> dict[str, str]:
    """Return the non-secret provider metadata used for an audit record."""
    configuration = _configuration()
    return {
        "model": str(configuration["model"]),
        "provider": ("azure_openai_v1" if configuration["endpoint"] else "openai"),
        "provider_api_version": str(configuration["api_version"]),
        "configuration_source": str(configuration["configuration_source"]),
    }


def _input(feature_name: str, description: str,
           candidates: list[dict[str, Any]]) -> AdjudicationInput:
    rules = rule_index()
    projected = []
    for item in candidates[:3]:
        rule = rules[item["canonical_feature"]]
        projected.append(Candidate(
            canonical_feature=rule["feature"], definition=rule["definition"],
            representations=rule.get("representations") or [],
            inverse_representations=rule.get("inverse_representations") or [],
        ))
    return AdjudicationInput(
        input_feature=InputFeature(name=feature_name, description=description or None),
        candidates=projected,
    )


def _case_message(contract: AdjudicationInput) -> str:
    feature = contract.input_feature
    lines = [
        "INPUT FEATURE",
        f"name: {feature.name}",
        f"description: {feature.description or '(not supplied)'}",
    ]
    for index, candidate in enumerate(contract.candidates, start=1):
        representations = candidate.representations or ["(none supplied)"]
        inverse = candidate.inverse_representations or ["(none supplied)"]
        lines.extend([
            "",
            f"CANDIDATE {index}",
            f"Knowledge Base concept: {candidate.canonical_feature}",
            f"definition: {candidate.definition}",
            "representations:",
            *(f"  - {value}" for value in representations),
            "inverse representations:",
            *(f"  - {value}" for value in inverse),
        ])
    return "\n".join(lines)


def adjudicate(feature_name: str, description: str,
               candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a validated AI suggestion or raise for the manual fallback path."""
    if not candidates:
        raise ValueError("semantic adjudication requires at least one bounded candidate")
    contract = _input(feature_name, description, candidates)
    configuration = _configuration()
    response = _client(configuration).responses.parse(
        model=configuration["model"],
        instructions=prompt(),
        input=[{"role": "user", "content": _case_message(contract)}],
        text_format=AdjudicationOutput,
        store=False,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise StructuredAdjudicationResponseError(
            "Responses API returned no parsed semantic adjudication output"
        )
    try:
        output = (parsed if isinstance(parsed, AdjudicationOutput)
                  else AdjudicationOutput.model_validate(parsed))
    except ValidationError as exc:
        raise StructuredAdjudicationResponseError(
            "Structured output failed contract validation"
        ) from exc
    validate_adjudication_result(contract, output)
    usage = getattr(response, "usage", None)
    return {
        "input": contract.model_dump(mode="json"),
        "output": output.model_dump(mode="json"),
        "response_id": getattr(response, "id", None),
        "model": configuration["model"],
        "provider": "azure_openai_v1" if configuration["endpoint"] else "openai",
        "provider_api_version": configuration["api_version"],
        "configuration_source": configuration["configuration_source"],
        "prompt_version": PROMPT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "usage": {
            "prompt_tokens": getattr(usage, "input_tokens", None),
            "completion_tokens": getattr(usage, "output_tokens", None),
        },
    }


__all__ = [
    "CONTRACT_VERSION", "ENV_PATH_VARIABLE", "PROMPT_VERSION",
    "StructuredAdjudicationResponseError", "adjudicate", "configuration_metadata",
]
