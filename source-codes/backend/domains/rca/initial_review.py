"""Structured LLM review of deterministic RCA opening evidence."""
from __future__ import annotations
from domains.rca import progress

import json
from typing import Any

from pydantic import BaseModel, Field

from ai.model_registry import load_model_policy, rca_llm_enabled
from ai.model_runtime import ModelExecutionError, execute_with_fallback


WORKLOAD = "rca_initial_review"
PROMPT_VERSION = "rca_initial_review_v0_3"
CONTRACT_VERSION = "rca_initial_review_contract_v0_2"


class CandidateHypothesis(BaseModel):
    statement: str = Field(min_length=1, max_length=500)
    evidence_basis: str = Field(min_length=1, max_length=1000)
    testable_next_step: str = Field(min_length=1, max_length=500)


class InitialReviewOutput(BaseModel):
    summary: str = Field(min_length=1, max_length=1200)
    observed_signals: list[str] = Field(default_factory=list, max_length=5)
    candidate_hypotheses: list[CandidateHypothesis] = Field(default_factory=list, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=5)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=5)


class StructuredInitialReviewError(ValueError):
    """The provider completed but did not return the governed contract."""


def enabled() -> bool:
    return rca_llm_enabled()


def public_policy() -> dict[str, object]:
    return load_model_policy(WORKLOAD).public_metadata()


@progress.phase("Interpreting initial evidence")
def review(case_context: dict[str, Any], deterministic_result: dict[str, Any]) -> dict[str, Any]:
    """Review bounded metadata and aggregates; never send source rows."""
    policy = load_model_policy(WORKLOAD)
    review_input = {
        "case": {
            "case_id": case_context.get("case_id"),
            "test_name": case_context.get("test_name"),
            "test_family": case_context.get("test_family"),
            "table_name": case_context.get("table_name"),
            "columns": case_context.get("columns") or [],
            "metric": case_context.get("metric"),
            "threshold": case_context.get("threshold"),
            "violation_count": case_context.get("violation_count"),
            "user_context": case_context.get("user_context") or [],
        },
        "deterministic_opening_result": deterministic_result,
    }

    def invoke(client: Any, deployment: Any) -> Any:
        return client.responses.parse(
            model=deployment.deployment,
            instructions=(
                "You are the initial-review analyst in a governed data-quality RCA. "
                "Use only the supplied aggregate evidence. Treat governed AAR diagnostic "
                "evidence as authoritative over raw fallback statistics. Respect confirmed "
                "special-value handling and the declared population scope. For PSI, inspect "
                "the supplied bin counts, shares, and contributions before proposing causes. "
                "Distinguish observations from hypotheses, disclose genuine limitations, and "
                "treat user context as attributed, unverified background, not measured evidence. "
                "propose testable next steps. Do not claim a root cause, invent unavailable "
                "evidence, expose drafting commentary, or recommend modifying production data."
            ),
            input=[{
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": json.dumps(review_input, sort_keys=True, default=str),
                }],
            }],
            text_format=InitialReviewOutput,
            store=False,
        )

    execution = execute_with_fallback(WORKLOAD, invoke, policy=policy)
    parsed = getattr(execution.response, "output_parsed", None)
    if parsed is None:
        raise StructuredInitialReviewError(
            "The model returned no structured initial-review output"
        )
    output = (parsed.model_dump() if hasattr(parsed, "model_dump")
              else InitialReviewOutput.model_validate(parsed).model_dump())
    return {
        "output": output,
        "selected_model": execution.deployment.public_metadata(),
        "response_id": getattr(execution.response, "id", None),
        "response_model": getattr(execution.response, "model", None),
        "attempts": execution.attempts,
        "prompt_version": PROMPT_VERSION,
        "contract_version": CONTRACT_VERSION,
    }


__all__ = [
    "CONTRACT_VERSION", "InitialReviewOutput", "ModelExecutionError",
    "PROMPT_VERSION", "StructuredInitialReviewError", "enabled", "public_policy", "review",
]
