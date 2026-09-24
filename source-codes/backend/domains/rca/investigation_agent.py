"""Structured model boundary for hypothesis-driven RCA investigations."""
from __future__ import annotations
from domains.rca import progress

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai.model_registry import load_model_policy
from ai.model_runtime import execute_with_fallback
from ai.sandbox_capabilities import public_contract


PLANNER_WORKLOAD = "rca_investigation_planner"
CODEGEN_WORKLOAD = "rca_code_generator"
READER_WORKLOAD = "rca_investigation_reader"
TARGET_WORKLOAD = "rca_driver_target"
DRIVER_READER_WORKLOAD = "rca_driver_reader"
PLANNER_PROMPT_VERSION = "rca_investigation_planner_v0_2"
CODEGEN_PROMPT_VERSION = "rca_code_generator_v0_3"
TARGET_PROMPT_VERSION = "rca_driver_target_v0_1"


class HelperParameters(BaseModel):
    """Closed parameter surface accepted by the governed helper catalogue."""

    model_config = ConfigDict(extra="forbid")

    column: str | None = Field(default=None, max_length=200)
    columns: list[str] = Field(default_factory=list, max_length=10)
    segment_col: str | None = Field(default=None, max_length=200)
    date_col: str | None = Field(default=None, max_length=200)
    key_col: str | None = Field(default=None, max_length=200)
    target_col: str | None = Field(default=None, max_length=200)
    bins: int | None = Field(default=None, ge=2, le=100)
    split_quantile: float | None = Field(default=None, gt=0, lt=1)


class InvestigationPlan(BaseModel):
    confirmation_possible: bool = True
    question: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1000)
    analysis_kind: str = Field(min_length=1, max_length=100)
    preferred_helper_ids: list[str] = Field(default_factory=list, max_length=3)
    helper_params: HelperParameters = Field(default_factory=HelperParameters)
    expected_output: list[str] = Field(default_factory=list, max_length=12)
    supports_hypothesis_when: str = Field(min_length=1, max_length=500)
    rejects_hypothesis_when: str = Field(min_length=1, max_length=500)


class GeneratedInvestigationCode(BaseModel):
    rationale: str = Field(min_length=1, max_length=800)
    python_code: str = Field(min_length=1, max_length=12000)
    expected_result_keys: list[str] = Field(default_factory=list, max_length=20)


class AlternativeExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = True
    statement: str = Field(min_length=1, max_length=700)
    evidence_basis: str = Field(min_length=1, max_length=1000)
    proposed_test: str = Field(min_length=1, max_length=700)
    distinction: str = Field(min_length=1, max_length=700)


@progress.phase("Developing another explanation")
def propose_alternative(payload: dict[str, Any]) -> dict[str, Any]:
    result = _structured(
        PLANNER_WORKLOAD, AlternativeExplanation,
        "The user explicitly requests another explanation, not a follow-up to the active "
        "hypothesis. Propose exactly one distinct, testable explanation for this RCA using "
        "only the frozen schema, original diagnostic, retained evidence and user context. "
        "Explain how its mechanism differs from prior hypotheses. Do not merely reword "
        "the active hypothesis, repeat disproven explanations, invent fields or claim causality. "
        "If no distinct testable explanation is supported by the supplied data, set available "
        "false and explain the missing information in distinction; no hypothesis will be created. "
        "The proposed test must be one bounded read-only analysis; no automatic follow-up loop.",
        payload,
    )
    return {**result, "prompt_version": "rca_alternative_explanation_v0_1"}


class InvestigationInterpretation(BaseModel):
    assessment: str = Field(pattern="^(supported|rejected|inconclusive)$")
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_points: list[str] = Field(default_factory=list, max_length=8)
    next_question: str | None = Field(default=None, max_length=500)


class DriverTargetProblem(BaseModel):
    problem_statement: str = Field(min_length=1, max_length=700)
    target_mode: str = Field(pattern="^(population_membership|missingness|psi_bin_membership|unavailable)$")
    affected_column: str = Field(min_length=1, max_length=200)
    positive_class_definition: str = Field(min_length=1, max_length=500)
    target_bin_id: str | None = Field(default=None, max_length=200)
    candidate_columns: list[str] = Field(default_factory=list, max_length=20)
    excluded_columns: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=1000)
    unavailable_reason: str | None = Field(default=None, max_length=500)


class FocusedDriverHypothesis(BaseModel):
    assessment: str = Field(pattern="^inconclusive$")
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_points: list[str] = Field(default_factory=list, max_length=8)
    focused_hypothesis: str = Field(min_length=1, max_length=700)
    evidence_basis: str = Field(min_length=1, max_length=1000)
    proposed_test: str = Field(min_length=1, max_length=700)
    next_question: str = Field(min_length=1, max_length=500)


def _structured(workload: str, output_type: type[BaseModel], instructions: str,
                payload: dict[str, Any]) -> dict[str, Any]:
    policy = load_model_policy(workload)

    def invoke(client: Any, deployment: Any) -> Any:
        return client.responses.parse(
            model=deployment.deployment,
            instructions=instructions,
            input=[{"role": "user", "content": [{
                "type": "input_text", "text": json.dumps(payload, sort_keys=True, default=str),
            }]}],
            text_format=output_type,
            store=False,
        )

    execution = execute_with_fallback(workload, invoke, policy=policy)
    parsed = getattr(execution.response, "output_parsed", None)
    if parsed is None:
        raise ValueError("The model returned no structured output")
    output = (parsed.model_dump() if hasattr(parsed, "model_dump")
              else output_type.model_validate(parsed).model_dump())
    return {
        "output": output,
        "selected_model": execution.deployment.public_metadata(),
        "response_id": getattr(execution.response, "id", None),
        "response_model": getattr(execution.response, "model", None),
        "attempts": execution.attempts,
    }


@progress.phase("Planning analysis")
def plan(payload: dict[str, Any]) -> dict[str, Any]:
    result = _structured(
        PLANNER_WORKLOAD, InvestigationPlan,
        "You are the investigation planner in a governed data-quality RCA. "
        "When discovery_confirmation is supplied, use its separator and proposed test to plan "
        "one confirmation of the ORIGINAL selected hypothesis. Do not repeat discovery, replace "
        "the hypothesis with the association, or plan further automatic analyses. "
        "Set confirmation_possible false and explain the missing information in rationale if "
        "the supplied data cannot support a confirmation. Plan exactly one "
        "bounded, read-only analysis of the user-selected hypothesis. Use only the supplied "
        "case evidence. Directly test the hypothesis's discriminating clause: when it names "
        "a segment such as reporting quarter and frozen population context is supplied, use "
        "both rather than repeating aggregate evidence. Apply progressive disclosure: begin "
        "with the coarsest aggregation that can discriminate the hypothesis, then request a "
        "separate narrower follow-up only where the aggregate result shows a material pattern. "
        "For temporal data, prefer year before quarter and quarter before month or date. Do not "
        "cross multiple high-cardinality dimensions in the first look when a lower-grain summary "
        "can establish where to investigate. Treat the latest inconclusive "
        "next_question in investigation_history as a required follow-up only when it tests "
        "the selected hypothesis; a different selected explanation takes precedence. Never repeat an "
        "already completed helper with the same parameters. Prefer one compatible helper ID "
        "from the supplied catalogue. Do not "
        "claim a root cause or invent columns, populations, or evidence. When retained PSI "
        "bins exist, use them for aggregate claims, but use the supplied frozen population "
        "definition when a segment-level hypothesis requires row-level allocation. State explicit support "
        "and rejection conditions. Code is generated later only if no listed helper fits.",
        payload,
    )
    return {**result, "prompt_version": PLANNER_PROMPT_VERSION}


@progress.phase("Defining discovery target")
def define_driver_target(payload: dict[str, Any]) -> dict[str, Any]:
    result = _structured(
        TARGET_WORKLOAD, DriverTargetProblem,
        "You define one deterministic row-level target for a governed tabular RCA driver search. "
        "Use the observed diagnostic, retained results, population definition, available schema, "
        "selected broad hypothesis, and any supplied user context. For PSI, target the measured "
        "dominant contributing bin rather than baseline/current membership: use missingness when "
        "the retained dominant bin is Missing, otherwise use psi_bin_membership and copy its exact "
        "retained bin ID into target_bin_id. Use population_membership only for a population-shift "
        "question that has no affected-bin symptom. For completeness or missingness, use missingness. "
        "Otherwise return unavailable. Candidate columns must come "
        "only from available_schema. Exclude the affected diagnostic column, population split "
        "feature only when target_mode is population_membership, identifiers, technical fields, and "
        "direct target indicators. Do not exclude temporal, balance, derived, performance, or other "
        "available evidence merely because its name suggests a relationship; the deterministic "
        "validator owns final exclusions. Prefer at most 20 plausible contextual inputs. Do not "
        "claim causality or write code.",
        payload,
    )
    return {**result, "prompt_version": TARGET_PROMPT_VERSION}


@progress.phase("Generating analysis code")
def generate_code(payload: dict[str, Any]) -> dict[str, Any]:
    payload = {**payload, "sandbox_capabilities": public_contract()}
    result = _structured(
        CODEGEN_WORKLOAD, GeneratedInvestigationCode,
        "Generate one small read-only Python analysis for the supplied governed RCA plan. "
        "Follow sandbox_capabilities: use only its builtins, provided names and approved "
        "explicit imports. Define every other name locally; do not assume all Python builtins exist. "
        "The runtime provides pandas as pd, numpy as np, a copied DataFrame named df, and a "
        "dict named params containing analysis_params, retained_evidence, and diagnostic_context. Assign a "
        "JSON-serializable dict to result using exactly these top-level keys: summary (plain-language "
        "string), metrics (a small dict), evidence_rows (at most 12 concise aggregate row dicts), "
        "interpretation_hints (list), and recommended_followups (list). Never return a full grouped "
        "dataset or unbounded row collection in evidence_rows. Start at the plan's coarsest useful "
        "grain; for temporal evidence aggregate by year before quarter unless prior investigation "
        "history already identified a specific year requiring drill-down. Order temporal rows "
        "chronologically and ordinal or numeric groups naturally. A recommended follow-up may name "
        "the narrower slice that should be tested next. Do not use files, "
        "network, subprocesses, environment variables, reflection, dynamic execution, or "
        "unbounded loops. Use only supplied columns and evidence. Keep code under 12,000 "
        "characters and do not include markdown fences.",
        payload,
    )
    return {**result, "prompt_version": CODEGEN_PROMPT_VERSION}


@progress.phase("Interpreting results")
def interpret(payload: dict[str, Any]) -> dict[str, Any]:
    return _structured(
        READER_WORKLOAD, InvestigationInterpretation,
        "You are the evidence reader in a governed data-quality RCA. Compare the completed "
        "analysis only with the selected hypothesis and the plan's precommitted support and "
        "rejection conditions. Return supported, rejected, or inconclusive. Do not claim a "
        "root cause, invent evidence, or use knowledge outside the supplied payload. Summarize the "
        "aggregate pattern in plain language. Recommend a more granular follow-up only when the "
        "completed aggregate evidence identifies a material area that needs isolation.",
        payload,
    )


@progress.phase("Interpreting discovery")
def interpret_driver_search(payload: dict[str, Any]) -> dict[str, Any]:
    return _structured(
        DRIVER_READER_WORKLOAD, FocusedDriverHypothesis,
        "You interpret a governed shallow decision-tree discovery result for tabular RCA. "
        "The tree identifies associated separators, not a root cause. Always return assessment "
        "inconclusive. Lead with how the supplied psi_impact connects the leading segment to the "
        "observed baseline-to-current PSI-bin movement; model validation metrics are secondary. "
        "Propose exactly one more focused hypothesis plus a confirmatory test that checks whether "
        "conditioning on its leading split materially attenuates the observed diagnostic symptom. "
        "The focused hypothesis is a candidate "
        "for human selection, not a conclusion. Use only supplied evidence and do not claim causality.",
        payload,
    )


__all__ = ["DriverTargetProblem", "FocusedDriverHypothesis", "GeneratedInvestigationCode", "InvestigationPlan",
           "define_driver_target", "generate_code", "interpret", "interpret_driver_search", "plan"]
