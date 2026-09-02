"""OpenAI integration boundary for semantic feature adjudication.

The fixed prompt supplies all semantic instructions.  Case messages contain
only the already-validated feature and candidate contract fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from pydantic import ValidationError

from .semantic_adjudication_contract import (
    AdjudicationInput,
    AdjudicationOutput,
    Candidate,
    InputFeature,
)


DEFAULT_MODEL = "gpt-5.4-mini"
PROMPT_VERSION_V0_1 = "semantic_feature_adjudication_v0_1"
PROMPT_VERSION_V0_2 = "semantic_feature_adjudication_v0_2"
CONTRACT_VERSION_V0_1 = "semantic_feature_adjudication_contract_v0_1"
CONTRACT_VERSION_V0_2 = "semantic_feature_adjudication_contract_v0_2"
PROMPT_VERSION = PROMPT_VERSION_V0_2
ADJUDICATION_CONTRACT_VERSION = CONTRACT_VERSION_V0_2


class StructuredAdjudicationResponseError(ValueError):
    """Raised when an API response does not contain a parseable contract output."""


@dataclass(frozen=True)
class AdjudicationCall:
    """Parsed API result without chain-of-thought or credentials."""

    output: AdjudicationOutput
    response_id: str | None = None


class SemanticAdjudicator(Protocol):
    """Model-independent callable seam used by the experiment harness."""

    model: str
    prompt_version: str
    contract_version: str

    def adjudicate(self, adjudication_input: AdjudicationInput) -> AdjudicationCall: ...


def load_adjudication_prompt(path: str | Path) -> str:
    """Load the fixed prompt verbatim from its versioned resource file."""

    prompt_path = Path(path)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Semantic adjudication prompt not found: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError(f"Semantic adjudication prompt is empty: {prompt_path}")
    return prompt


def _rule_index(kb: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rules = kb.get("feature_rules")
    if not isinstance(rules, list):
        raise ValueError("Knowledge Base must contain a feature_rules list")
    return {str(rule["feature"]): rule for rule in rules}


def build_adjudication_input(
    feature_name: str,
    feature_description: str | None,
    matcher_result: Mapping[str, Any],
    kb: Mapping[str, Any],
) -> AdjudicationInput:
    """Project matcher Top-3 names to the leakage-limited semantic contract."""

    ranked_candidates = list(matcher_result.get("top_candidates") or [])[:3]
    if not ranked_candidates:
        raise ValueError("A non-exact matcher result must supply at least one candidate")
    rules = _rule_index(kb)
    candidates: list[Candidate] = []
    for ranked in ranked_candidates:
        canonical_feature = str(ranked["canonical_feature"])
        if canonical_feature not in rules:
            raise ValueError(
                f"Matcher candidate {canonical_feature!r} is absent from the Knowledge Base"
            )
        rule = rules[canonical_feature]
        candidates.append(
            Candidate(
                canonical_feature=canonical_feature,
                definition=rule["definition"],
                representations=list(rule.get("representations") or []),
                inverse_representations=list(rule.get("inverse_representations") or []),
            )
        )
    description = feature_description if feature_description and feature_description.strip() else None
    return AdjudicationInput(
        input_feature=InputFeature(name=feature_name, description=description),
        candidates=candidates,
    )


def _format_values(values: list[str]) -> list[str]:
    return [f"  - {value}" for value in values] or ["  - (none supplied)"]


def build_case_message(adjudication_input: AdjudicationInput) -> str:
    """Render case data without adding semantic instructions or matcher scores."""

    description = adjudication_input.input_feature.description or "(not supplied)"
    lines = [
        "INPUT FEATURE",
        f"name: {adjudication_input.input_feature.name}",
        f"description: {description}",
    ]
    for index, candidate in enumerate(adjudication_input.candidates, start=1):
        lines.extend(
            [
                "",
                f"CANDIDATE {index}",
                f"canonical feature: {candidate.canonical_feature}",
                f"definition: {candidate.definition}",
                "representations:",
                *_format_values(candidate.representations),
                "inverse representations:",
                *_format_values(candidate.inverse_representations),
            ]
        )
    return "\n".join(lines)


def create_openai_client(env_path: str | Path | None = None) -> Any:
    """Create the project's configured OpenAI/Azure OpenAI client lazily."""

    if env_path is not None:
        load_dotenv(Path(env_path), override=False)
    timeout = float(os.getenv("AI_REQUEST_TIMEOUT", "90"))
    max_retries = int(os.getenv("AI_MAX_RETRIES", "1"))
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        from openai import OpenAI

        api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is required for semantic adjudication")
        # Foundry may provide either a resource URL or the complete
        # /openai/v1/responses endpoint.  The SDK needs the host-level v1 base
        # because it appends the resource path itself.
        parsed_endpoint = urlsplit(endpoint)
        base_url = urlunsplit(
            (parsed_endpoint.scheme, parsed_endpoint.netloc, "/openai/v1/", "", "")
        )
        return OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for semantic adjudication")
    return OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)


def configured_model(env_path: str | Path | None = None) -> str:
    """Resolve a deployment name while preserving the fixed baseline default."""

    if env_path is not None:
        load_dotenv(Path(env_path), override=False)
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


class OpenAISemanticAdjudicator:
    """Responses API Structured Outputs implementation of the adjudicator seam."""

    def __init__(
        self,
        *,
        client: Any,
        prompt: str,
        model: str = DEFAULT_MODEL,
        prompt_version: str = PROMPT_VERSION,
        contract_version: str = ADJUDICATION_CONTRACT_VERSION,
    ):
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        self.client = client
        self.prompt = prompt
        self.model = model
        self.prompt_version = prompt_version
        self.contract_version = contract_version

    def adjudicate(self, adjudication_input: AdjudicationInput) -> AdjudicationCall:
        case_message = build_case_message(adjudication_input)
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.prompt,
                input=[{"role": "user", "content": case_message}],
                text_format=AdjudicationOutput,
                store=False,
            )
        except ValidationError as exc:
            raise StructuredAdjudicationResponseError(
                f"Structured output failed Pydantic parsing: {exc}"
            ) from exc
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise StructuredAdjudicationResponseError(
                "Responses API returned no parsed semantic adjudication output"
            )
        try:
            output = (
                parsed
                if isinstance(parsed, AdjudicationOutput)
                else AdjudicationOutput.model_validate(parsed)
            )
        except ValidationError as exc:
            raise StructuredAdjudicationResponseError(
                f"Structured output failed Pydantic parsing: {exc}"
            ) from exc
        return AdjudicationCall(output=output, response_id=getattr(response, "id", None))
