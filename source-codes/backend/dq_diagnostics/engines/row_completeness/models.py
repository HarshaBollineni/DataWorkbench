"""Versioned contracts for Test 2, Diagnostic 6: Row Completeness.

This module contains no dataframe or database access. It is the validation
boundary shared by the deterministic engine, AAR validators, API layer, and
external report projection.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = 1
DIAGNOSTIC_ID = 6
RULE_TITLES = {
    "T2D6-01": "Valid row identifiers",
    "T2D6-02": "Reporting-period sequence",
    "T2D6-03": "Rows received by period",
    "T2D6-04": "Repeated facility-period rows",
    "T2D6-05": "Gaps in facility timelines",
    "T2D6-06": "Gaps by segment",
}
RULE_IDS = tuple(RULE_TITLES)

RuleOutcome = Literal["PASS", "VIOLATION", "NO-VERDICT", "NOT-APPLICABLE", "NOT-ASSESSABLE"]
OverallVerdict = Literal["pass", "violation", "inconclusive", "not_applicable"]
ReportingGrain = Literal["monthly", "quarterly", "semiannual", "annual"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleBinding(StrictModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    source: Literal["governed_metadata", "deterministic", "manual", "llm_reviewed"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class RunScope(StrictModel):
    asset_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    facility_id: RoleBinding
    period: RoleBinding
    segment: RoleBinding | None = None
    reporting_grain: ReportingGrain
    continuity_floor: float = Field(default=0.95, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def bindings_belong_to_selected_table(self) -> "RunScope":
        bindings = [self.facility_id, self.period]
        if self.segment is not None:
            bindings.append(self.segment)
        if any(binding.table != self.table for binding in bindings):
            raise ValueError("every semantic-role binding must belong to the selected table")
        columns = [binding.column for binding in bindings]
        if len(columns) != len(set(columns)):
            raise ValueError("a column cannot be assigned to multiple semantic roles")
        return self


class InferenceDisclosure(StrictModel):
    llm_call_count: int = Field(ge=0)
    llm_used: bool
    verdict_influenced_by_llm: Literal[False] = False
    statement: str = Field(min_length=1)
    events: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_inferences: list[dict[str, Any]] = Field(default_factory=list)
    event_set_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def counts_are_honest(self) -> "InferenceDisclosure":
        invoked = sum(bool(event.get("invoked")) for event in self.events)
        if invoked != self.llm_call_count:
            raise ValueError("llm_call_count must equal the number of invoked LLM events")
        if self.llm_used != (self.llm_call_count > 0):
            raise ValueError("llm_used must reflect llm_call_count")
        if not self.llm_used and "No LLM calls" not in self.statement:
            raise ValueError("zero-call disclosure must state that no LLM calls were made")
        if any(event.get("verdict_influenced") is not False for event in self.events):
            raise ValueError("LLM events cannot influence row-completeness verdicts")
        return self


class IssueSummary(StrictModel):
    invalid_key_rows: int = Field(ge=0)
    missing_reporting_periods: int = Field(default=0, ge=0)
    calendar_gaps_without_required_pairs: int = Field(default=0, ge=0)
    missing_facility_period_pairs: int = Field(ge=0)
    duplicate_pairs: int = Field(ge=0)
    surplus_duplicate_rows: int = Field(ge=0)
    affected_facilities: int = Field(ge=0)
    affected_periods: int = Field(ge=0)
    affected_segments: int = Field(default=0, ge=0)
    primary_issue_instances: int = Field(ge=0)

    @model_validator(mode="after")
    def primary_count_does_not_double_count_aggregations(self) -> "IssueSummary":
        expected = (self.invalid_key_rows + self.calendar_gaps_without_required_pairs
                    + self.missing_facility_period_pairs
                    + self.surplus_duplicate_rows)
        if self.primary_issue_instances != expected:
            raise ValueError(
                "primary_issue_instances must equal invalid rows + uncovered calendar gaps + "
                "missing pairs + surplus duplicate rows"
            )
        return self


class FindingEvidence(StrictModel):
    finding_type: Literal["invalid_key", "missing_pair", "duplicate_pair", "segment_gap"]
    facility_id: str | int | float | None = None
    period: str | None = None
    segment: str | None = None
    row_reference: str | None = None
    reason: str = Field(min_length=1)


class RuleResult(StrictModel):
    rule_id: str
    title: str
    outcome: RuleOutcome
    issue_count: int = Field(ge=0)
    measure: float | None = Field(default=None, ge=0.0, le=1.0)
    continuity_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    affected_population: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)
    total_findings: int = Field(ge=0)
    evidence: list[FindingEvidence] = Field(default_factory=list)
    evidence_truncated: bool = False
    na_reason: str | None = None

    @model_validator(mode="after")
    def validate_rule_contract(self) -> "RuleResult":
        if self.rule_id not in RULE_TITLES:
            raise ValueError(f"unknown row-completeness rule ID: {self.rule_id}")
        if not self.title.strip():
            raise ValueError("rule title cannot be empty")
        if self.total_findings < len(self.evidence):
            raise ValueError("total_findings cannot be smaller than retained evidence")
        if self.evidence_truncated != (self.total_findings > len(self.evidence)):
            raise ValueError("evidence_truncated must reflect total versus retained evidence")
        if self.outcome == "PASS" and self.issue_count:
            raise ValueError("a passing rule cannot report issues")
        if self.outcome == "NOT-APPLICABLE" and not self.na_reason:
            raise ValueError("NOT-APPLICABLE requires na_reason")
        return self


class ReconciliationPayload(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    origin_run_id: str = Field(min_length=1)
    scope: RunScope
    overall_verdict: OverallVerdict
    facilities_assessed: int = Field(ge=0)
    periods_assessed: int = Field(ge=0)
    required_facility_period_pairs: int = Field(ge=0)
    received_required_pairs: int = Field(ge=0)
    continuity_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    issue_summary: IssueSummary
    rules: list[RuleResult]
    calculation_inference_disclosure: InferenceDisclosure
    manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    methodology_version: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    source_artifact_references: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reconciliation(self) -> "ReconciliationPayload":
        if tuple(rule.rule_id for rule in self.rules) != RULE_IDS:
            raise ValueError("rules must contain T2D6-01 through T2D6-06 in display order")
        if self.received_required_pairs > self.required_facility_period_pairs:
            raise ValueError("received required pairs cannot exceed required pairs")
        if self.required_facility_period_pairs:
            expected = self.received_required_pairs / self.required_facility_period_pairs
            if self.continuity_coverage is None or abs(self.continuity_coverage - expected) > 1e-12:
                raise ValueError("continuity_coverage must reconcile to received / required pairs")
        elif self.continuity_coverage is not None:
            raise ValueError("continuity_coverage must be null when no required pairs exist")
        return self


class ReportExample(StrictModel):
    finding_type: Literal["invalid_key", "missing_pair", "duplicate_pair", "segment_gap"]
    facility_reference: str | None = Field(default=None, pattern=r"^FAC-[A-F0-9]{12}$")
    period: str | None = None
    segment: str | None = None
    row_reference: str | None = Field(default=None, pattern=r"^ROW-[A-F0-9]{12}$")
    reason: str = Field(min_length=1)


class ReportRuleResult(StrictModel):
    rule_id: str
    title: str
    outcome: RuleOutcome
    issue_count: int = Field(ge=0)
    measure: float | None = Field(default=None, ge=0.0, le=1.0)
    continuity_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    affected_population: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)
    total_findings: int = Field(ge=0)
    examples: list[ReportExample] = Field(default_factory=list)
    evidence_truncated: bool


class SafeSharingProfile(StrictModel):
    profile_id: Literal["row_completeness_external_v1"] = "row_completeness_external_v1"
    facility_identifiers_masked: Literal[True] = True
    unrelated_columns_omitted: Literal[True] = True
    raw_rows_included: Literal[False] = False
    segment_label_policy: Literal["retain", "mask", "omit"]


class ReportPayload(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    audience: Literal["external"] = "external"
    source_reconciliation_artifact_id: str = Field(min_length=1)
    source_reconciliation_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_schema_version: Literal[1] = 1
    renderer_version: str = Field(min_length=1)
    scope: RunScope
    overall_verdict: OverallVerdict
    continuity_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    rule_rollup: dict[RuleOutcome, int]
    issue_summary: IssueSummary
    rules: list[ReportRuleResult]
    source_calculation_inference_disclosure: InferenceDisclosure
    current_journey_inference_disclosure: InferenceDisclosure
    recommended_next_steps: list[dict[str, str]]
    limitations: list[str]
    provenance: dict[str, Any]
    safe_sharing: SafeSharingProfile = Field(default_factory=SafeSharingProfile)

    @model_validator(mode="after")
    def validate_report(self) -> "ReportPayload":
        if tuple(rule.rule_id for rule in self.rules) != RULE_IDS:
            raise ValueError("report rules must preserve the six-rule display order")
        expected_rollup = {outcome: 0 for outcome in (
            "PASS", "VIOLATION", "NO-VERDICT", "NOT-APPLICABLE", "NOT-ASSESSABLE"
        )}
        for rule in self.rules:
            expected_rollup[rule.outcome] += 1
        if self.rule_rollup != expected_rollup:
            raise ValueError("rule_rollup must reconcile to report rules")
        return self


def mask_facility_identifier(value: Any, *, report_salt: str) -> str | None:
    if value is None:
        return None
    if not report_salt:
        raise ValueError("report_salt is required")
    digest = hashlib.sha256(f"{report_salt}\x00{value}".encode("utf-8")).hexdigest()[:12].upper()
    return f"FAC-{digest}"


def _mask_reference(value: Any, *, report_salt: str, prefix: str) -> str | None:
    if value is None:
        return None
    digest = hashlib.sha256(f"{report_salt}\x00{prefix}\x00{value}".encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def build_external_report_payload(
    reconciliation: ReconciliationPayload | dict[str, Any],
    *,
    report_id: str,
    source_artifact_id: str,
    source_payload_hash: str,
    renderer_version: str,
    report_salt: str,
    recommended_next_steps: list[dict[str, str]],
    limitations: list[str],
    current_run_id: str,
    segment_label_policy: Literal["retain", "mask", "omit"],
    current_journey_inference_disclosure: InferenceDisclosure | dict[str, Any] | None = None,
    knowledge_provenance: dict[str, Any] | None = None,
) -> ReportPayload:
    """Create the safe structured source from which PDF bytes are rendered."""
    if not report_salt:
        raise ValueError("report_salt is required")
    if not current_run_id.strip():
        raise ValueError("current_run_id is required")
    source = (reconciliation if isinstance(reconciliation, ReconciliationPayload)
              else ReconciliationPayload.model_validate(reconciliation))
    report_rules = []
    for rule in source.rules:
        examples = []
        for evidence in rule.evidence:
            if segment_label_policy == "retain":
                segment = evidence.segment
            elif segment_label_policy == "mask":
                segment = _mask_reference(evidence.segment, report_salt=report_salt, prefix="SEG")
            else:
                segment = None
            examples.append(ReportExample(
                finding_type=evidence.finding_type,
                facility_reference=mask_facility_identifier(evidence.facility_id, report_salt=report_salt),
                period=evidence.period,
                segment=segment,
                row_reference=_mask_reference(evidence.row_reference, report_salt=report_salt, prefix="ROW"),
                reason=evidence.reason,
            ))
        safe_metrics = rule.metrics
        if rule.rule_id == "T2D6-04" and "pairs_by_segment" in rule.metrics:
            safe_metrics = {**rule.metrics, "pairs_by_segment": []}
            for row in rule.metrics.get("pairs_by_segment") or []:
                safe_row = dict(row)
                if segment_label_policy == "mask":
                    safe_row["segment"] = _mask_reference(
                        safe_row.get("segment"), report_salt=report_salt, prefix="SEG"
                    )
                elif segment_label_policy == "omit":
                    safe_row.pop("segment", None)
                safe_metrics["pairs_by_segment"].append(safe_row)
        if rule.rule_id == "T2D6-06" and "cell_results" in rule.metrics:
            safe_metrics = {**safe_metrics, "cell_results": []}
            for cell in rule.metrics.get("cell_results") or []:
                safe_cell = dict(cell)
                if segment_label_policy == "mask":
                    safe_cell["segment"] = _mask_reference(
                        safe_cell.get("segment"), report_salt=report_salt, prefix="SEG"
                    )
                elif segment_label_policy == "omit":
                    safe_cell.pop("segment", None)
                safe_metrics["cell_results"].append(safe_cell)
        report_rules.append(ReportRuleResult(
            rule_id=rule.rule_id,
            title=rule.title,
            outcome=rule.outcome,
            issue_count=rule.issue_count,
            measure=rule.measure,
            continuity_floor=rule.continuity_floor,
            affected_population=rule.affected_population,
            metrics=safe_metrics,
            explanation=rule.explanation,
            total_findings=rule.total_findings,
            examples=examples,
            evidence_truncated=rule.evidence_truncated,
        ))
    rollup: dict[str, int] = {outcome: 0 for outcome in (
        "PASS", "VIOLATION", "NO-VERDICT", "NOT-APPLICABLE", "NOT-ASSESSABLE"
    )}
    for rule in report_rules:
        rollup[rule.outcome] += 1
    return ReportPayload(
        report_id=report_id,
        run_id=current_run_id,
        source_reconciliation_artifact_id=source_artifact_id,
        source_reconciliation_payload_hash=source_payload_hash,
        renderer_version=renderer_version,
        scope=source.scope,
        overall_verdict=source.overall_verdict,
        continuity_coverage=source.continuity_coverage,
        rule_rollup=rollup,
        issue_summary=source.issue_summary,
        rules=report_rules,
        source_calculation_inference_disclosure=source.calculation_inference_disclosure,
        current_journey_inference_disclosure=(
            source.calculation_inference_disclosure
            if current_journey_inference_disclosure is None
            else (current_journey_inference_disclosure
                  if isinstance(current_journey_inference_disclosure, InferenceDisclosure)
                  else InferenceDisclosure.model_validate(current_journey_inference_disclosure))
        ),
        recommended_next_steps=recommended_next_steps,
        limitations=limitations,
        provenance={
            "origin_run_id": source.origin_run_id,
            "manifest_fingerprint": source.manifest_fingerprint,
            "methodology_version": source.methodology_version,
            "engine_version": source.engine_version,
            "source_artifact_references": source.source_artifact_references,
            "knowledge": knowledge_provenance or {},
        },
        safe_sharing=SafeSharingProfile(segment_label_policy=segment_label_policy),
    )
