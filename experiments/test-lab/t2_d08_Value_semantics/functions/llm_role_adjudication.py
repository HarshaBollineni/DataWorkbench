"""OpenAI Responses API boundary for value-semantics role adjudication."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import ValidationError

from .role_adjudication_contract import RoleAdjudicationInput, RoleAdjudicationOutput


DEFAULT_MODEL = "gpt-5.4-mini"
PROMPT_VERSION = "value_semantics_role_adjudication_v0_2"
CONTRACT_VERSION = "value_semantics_role_adjudication_contract_v0_2"


class StructuredRoleResponseError(ValueError):
    """Raised when a provider response cannot satisfy the output contract."""


@dataclass(frozen=True)
class RoleAdjudicationCall:
    output: RoleAdjudicationOutput
    response_id: str | None = None
    response_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class RoleAdjudicator(Protocol):
    model: str
    prompt_version: str
    contract_version: str

    def adjudicate(self, input_: RoleAdjudicationInput) -> RoleAdjudicationCall: ...


def load_adjudication_prompt(path: str | Path) -> str:
    prompt_path = Path(path)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Role-adjudication prompt not found: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError(f"Role-adjudication prompt is empty: {prompt_path}")
    return prompt


def build_case_message(input_: RoleAdjudicationInput) -> str:
    """Serialize only validated case data; semantic instructions stay in the prompt."""
    return json.dumps(input_.model_dump(mode="json"), indent=2, ensure_ascii=False)


def _load_environment(env_path: str | Path | None) -> None:
    if env_path is not None:
        load_dotenv(Path(env_path), override=False)


def configured_model(env_path: str | Path | None = None) -> str:
    _load_environment(env_path)
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


def create_openai_client(env_path: str | Path | None = None) -> Any:
    """Create the same OpenAI/Azure-compatible client configuration used by t2_d11."""
    _load_environment(env_path)
    from openai import OpenAI

    timeout = float(os.getenv("AI_REQUEST_TIMEOUT", "90"))
    max_retries = int(os.getenv("AI_MAX_RETRIES", "1"))
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is required for role adjudication")
        parsed = urlsplit(endpoint)
        base_url = urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))
        return OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for role adjudication")
    return OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)


class OpenAIRoleAdjudicator:
    def __init__(self, *, client: Any, prompt: str, model: str = DEFAULT_MODEL,
                 prompt_version: str = PROMPT_VERSION, contract_version: str = CONTRACT_VERSION):
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        self.client = client
        self.prompt = prompt
        self.model = model
        self.prompt_version = prompt_version
        self.contract_version = contract_version

    def adjudicate(self, input_: RoleAdjudicationInput) -> RoleAdjudicationCall:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.prompt,
                input=[{"role": "user", "content": build_case_message(input_)}],
                text_format=RoleAdjudicationOutput,
                store=False,
            )
        except ValidationError as exc:
            raise StructuredRoleResponseError(f"Structured output failed Pydantic parsing: {exc}") from exc
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise StructuredRoleResponseError("Responses API returned no parsed role-adjudication output")
        try:
            output = parsed if isinstance(parsed, RoleAdjudicationOutput) else RoleAdjudicationOutput.model_validate(parsed)
        except ValidationError as exc:
            raise StructuredRoleResponseError(f"Structured output failed Pydantic parsing: {exc}") from exc
        usage = getattr(response, "usage", None)
        return RoleAdjudicationCall(
            output=output,
            response_id=getattr(response, "id", None),
            response_model=getattr(response, "model", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
