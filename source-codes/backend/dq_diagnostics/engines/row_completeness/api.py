"""API-facing contracts for the Row Completeness Test Lab journey.

Routes are wired in the execution-integration phase. Defining these models now
keeps the request, frozen manifest, status, result, and report metadata stable
before the engine or UI depends on them.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import (
    DIAGNOSTIC_ID,
    RULE_IDS,
    InferenceDisclosure,
    ReconciliationPayload,
    ReportingGrain,
    RunScope,
    StrictModel,
)


class RowCompletenessRunRequest(StrictModel):
    """Minimum user inputs required to create a draft Diagnostic 6 run."""

    item_id: str = Field(min_length=1, description="Immutable DataWorkbench snapshot ID")
    table: str = Field(min_length=1)
    facility_id_column: str = Field(min_length=1)
    period_column: str = Field(min_length=1)
    reporting_grain: ReportingGrain
    continuity_floor: float = Field(default=0.95, ge=0.0, le=1.0)
    segment_column: str | None = None
    llm_role_verification: bool = False

    @field_validator("item_id", "table", "facility_id_column", "period_column", mode="before")
    @classmethod
    def required_strings_are_trimmed(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()

    @field_validator("segment_column", mode="before")
    @classmethod
    def optional_segment_is_trimmed(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("segment_column cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def role_columns_are_distinct(self) -> "RowCompletenessRunRequest":
        columns = [self.facility_id_column, self.period_column]
        if self.segment_column is not None:
            columns.append(self.segment_column)
        if len(columns) != len(set(columns)):
            raise ValueError("facility, period and segment roles must use distinct columns")
        return self


class FrozenRowCompletenessManifest(StrictModel):
    schema_version: Literal[1] = 1
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    run_id: str = Field(min_length=1)
    status: Literal["frozen"] = "frozen"
    scope: RunScope
    rule_ids: tuple[str, ...] = RULE_IDS
    kb: dict[str, Any]
    source_artifact_references: list[dict[str, str]]
    inference_disclosure: InferenceDisclosure
    engine_version: str = Field(min_length=1)
    methodology_version: str = Field(min_length=1)
    actor_decisions: list[dict[str, Any]] = Field(default_factory=list)
    manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("rule_ids")
    @classmethod
    def only_canonical_rules_in_display_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != RULE_IDS:
            raise ValueError("rule_ids must be T2D6-01 through T2D6-06 in display order")
        return value


class RowCompletenessRunAccepted(StrictModel):
    run_id: str = Field(min_length=1)
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    status: Literal["draft"] = "draft"
    manifest_url: str = Field(min_length=1)


class RowCompletenessRunStatus(StrictModel):
    run_id: str = Field(min_length=1)
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    status: Literal["draft", "queued", "running", "complete", "failed"]
    phase: str = Field(min_length=1)
    progress_percent: float = Field(ge=0.0, le=100.0)
    message: str = Field(min_length=1)
    retryable: bool = False
    error_code: str | None = None


class ArtifactReference(StrictModel):
    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reused: bool = False


class RowCompletenessResultResponse(StrictModel):
    run_id: str = Field(min_length=1)
    diagnostic_id: Literal[6] = DIAGNOSTIC_ID
    status: Literal["complete"] = "complete"
    reconciliation_artifact: ArtifactReference
    result: ReconciliationPayload
    current_journey_inference_disclosure: InferenceDisclosure

    @model_validator(mode="after")
    def artifact_type_is_correct(self) -> "RowCompletenessResultResponse":
        if self.reconciliation_artifact.artifact_type != "row_completeness_reconciliation":
            raise ValueError("result must reference a row-completeness reconciliation artifact")
        return self


class RowCompletenessReportMetadata(StrictModel):
    run_id: str = Field(min_length=1)
    report_artifact: ArtifactReference
    audience: Literal["external"] = "external"
    media_type: Literal["application/pdf"] = "application/pdf"
    filename: str = Field(pattern=r"^row-completeness-[A-Za-z0-9_-]+\.pdf$")

    @model_validator(mode="after")
    def report_artifact_type_is_correct(self) -> "RowCompletenessReportMetadata":
        if self.report_artifact.artifact_type != "row_completeness_report":
            raise ValueError("download must reference a row-completeness report artifact")
        return self
