"""Structured model boundary for the governed RCA data chat."""
from __future__ import annotations
from domains.rca import progress

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domains.rca.investigation_agent import HelperParameters, _structured


PLANNER_WORKLOAD = "rca_data_chat_planner"
ANSWER_WORKLOAD = "rca_data_chat_answer"
PLANNER_PROMPT_VERSION = "rca_data_chat_planner_v0_1"
ANSWER_PROMPT_VERSION = "rca_data_chat_answer_v0_1"


class ChatPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_decision: str = Field(pattern="^(in_scope|out_of_scope)$")
    scope_reason: str = Field(min_length=1, max_length=500)
    response_mode: str = Field(pattern="^(retained_evidence|analysis)$")
    question: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    analysis_kind: str | None = Field(default=None, max_length=100)
    preferred_helper_ids: list[str] = Field(default_factory=list, max_length=3)
    helper_params: HelperParameters = Field(default_factory=HelperParameters)
    expected_output: list[str] = Field(default_factory=list, max_length=12)


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=3000)
    evidence_references: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)


@progress.phase("Planning answer")
def plan(payload: dict[str, Any]) -> dict[str, Any]:
    result = _structured(
        PLANNER_WORKLOAD, ChatPlan,
        "You plan one governed answer inside an active data-quality RCA. First decide whether "
        "the question is strictly about the supplied RCA, its retained dataset snapshot, columns, "
        "diagnostic, hypotheses, or completed evidence. Mark every other dataset, operational "
        "system, general-knowledge request, remediation request, or unrelated question out_of_scope. "
        "Use retained_evidence whenever the supplied evidence is enough. Choose analysis only when "
        "one bounded read-only calculation over the supplied snapshot is necessary. For analysis, "
        "prefer compatible helper IDs from the supplied catalogue and use only supplied columns. "
        "Plan exactly one calculation, never a follow-up sequence. Do not modify hypotheses, "
        "assessments, conclusions, or investigation budgets.",
        payload,
    )
    return {**result, "prompt_version": PLANNER_PROMPT_VERSION}


@progress.phase("Writing answer")
def answer(payload: dict[str, Any]) -> dict[str, Any]:
    result = _structured(
        ANSWER_WORKLOAD, ChatAnswer,
        "Answer one question about the active RCA using only the supplied retained evidence and, "
        "when present, the single governed calculation result. Be concise and evidence-oriented. "
        "Do not claim causality beyond the evidence, change any hypothesis or assessment, recommend "
        "autonomous follow-up analysis, or use outside knowledge. Evidence references must be copied "
        "exactly from supplied_evidence_refs. State material limitations explicitly.",
        payload,
    )
    return {**result, "prompt_version": ANSWER_PROMPT_VERSION}


__all__ = ["ChatAnswer", "ChatPlan", "answer", "plan"]
