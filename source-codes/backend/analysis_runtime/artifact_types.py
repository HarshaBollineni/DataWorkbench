"""Extension registry for governed analytical artifact types.

The repository only understands immutable identity, storage and lineage.  This
module owns type-specific validation and bounded catalogue projections, keeping
new evidence types out of repository conditionals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


PayloadValidator = Callable[[Any], None]
SummaryAdapter = Callable[[Any], dict[str, Any]]


def _mapping(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("artifact payload must be an object")


def _summary(payload: Any) -> dict[str, Any]:
    """Safe generic projection for known and future types."""
    if not isinstance(payload, dict):
        return {"metrics": [], "fields": {"payload_kind": type(payload).__name__}}
    metrics = []
    for key, value in payload.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics.append({"metric_key": key, "label": key.replace("_", " ").title(),
                            "value": value, "value_type": "number", "direction": "neutral"})
        if len(metrics) >= 3:
            break
    return {"metrics": metrics, "fields": {"keys": sorted(payload)[:20]}}


def _metric(key: str, value: Any, *, unit: str | None = None) -> dict[str, Any]:
    return {"metric_key": key, "label": key.replace("_", " ").title(), "value": value,
            "value_type": "number" if isinstance(value, (int, float)) else "text",
            "unit": unit, "direction": "neutral"}


def _column_profile_summary(payload: Any) -> dict[str, Any]:
    _mapping(payload)
    keys = ("total_count", "non_null_count", "null_count", "physical_null_share",
            "special_value_row_count", "special_value_share", "regular_value_count",
            "regular_value_share", "effective_missing_count", "effective_missing_share",
            "distinct_count", "min", "max", "mean", "variance", "stddev", "median",
            "q1", "q3", "iqr", "mad", "skewness", "excess_kurtosis")
    share_keys = {key for key in keys if key.endswith("_share")}
    return {"metrics": [_metric(key, payload[key], unit="percent" if key in share_keys else None)
                         for key in keys if payload.get(key) is not None],
            "fields": {"classification": payload.get("classification"), "data_type": payload.get("data_type"),
                       "role": payload.get("role"), "description": payload.get("description"),
                       "special_value_count": payload.get("special_value_count", 0),
                       "special_values": payload.get("special_values") or [],
                       "special_values_confirmed": payload.get("special_values_confirmed", False),
                       "profile_basis": payload.get("profile_basis"),
                       "calculation_method": payload.get("calculation_method"),
                       "has_distribution": bool(payload.get("histogram") or payload.get("top_k"))}}


def _table_profile_summary(payload: Any) -> dict[str, Any]:
    _mapping(payload)
    return {"metrics": [_metric(key, payload[key]) for key in ("row_count", "column_count") if payload.get(key) is not None],
            "fields": {"table": payload.get("table"), "source": payload.get("source")}}


def _psi_bins_validator(payload: Any) -> None:
    from dq_diagnostics.engines.population_stability.bins import validate_bin_definition
    validate_bin_definition(payload, require_frozen=False)


def _psi_validator(payload: Any) -> None:
    _mapping(payload)
    required = {"feature", "psi", "classification", "bins", "epsilon", "thresholds",
                "population_fingerprint", "bin_artifact_id", "bin_payload_hash"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"PSI payload is missing fields: {missing}")
    if payload.get("decision_type") != "contextual" or payload.get("is_violation") is not False:
        raise ValueError("PSI artifacts must preserve contextual, non-violation semantics")
    if not isinstance(payload.get("bins"), list):
        raise ValueError("PSI bin contributions must be a list")


def _psi_summary(payload: Any) -> dict[str, Any]:
    _psi_validator(payload)
    return {"metrics": [_metric("psi", payload["psi"])],
            "fields": {"feature": payload["feature"], "classification": payload["classification"],
                       "decision_type": "contextual", "review_state": payload.get("review_state", "open"),
                       "bin_count": len(payload["bins"])}}


def _row_completeness_reconciliation_validator(payload: Any) -> None:
    from dq_diagnostics.engines.row_completeness.models import ReconciliationPayload
    ReconciliationPayload.model_validate(payload)


def _row_completeness_report_validator(payload: Any) -> None:
    from dq_diagnostics.engines.row_completeness.models import ReportPayload
    ReportPayload.model_validate(payload)


def _row_completeness_summary(payload: Any) -> dict[str, Any]:
    from dq_diagnostics.engines.row_completeness.models import ReconciliationPayload
    value = ReconciliationPayload.model_validate(payload)
    violated = sum(rule.outcome == "VIOLATION" for rule in value.rules)
    metrics = [
        _metric("primary_issue_instances", value.issue_summary.primary_issue_instances),
        _metric("violated_rules", violated),
    ]
    if value.continuity_coverage is not None:
        metrics.insert(0, _metric("continuity_coverage", value.continuity_coverage, unit="percent"))
    return {"metrics": metrics, "fields": {
        "overall_verdict": value.overall_verdict,
        "table": value.scope.table,
        "reporting_grain": value.scope.reporting_grain,
        "segment_bound": value.scope.segment is not None,
        "llm_used": value.calculation_inference_disclosure.llm_used,
    }}


def _row_completeness_report_summary(payload: Any) -> dict[str, Any]:
    from dq_diagnostics.engines.row_completeness.models import ReportPayload
    value = ReportPayload.model_validate(payload)
    return {"metrics": [
        _metric("primary_issue_instances", value.issue_summary.primary_issue_instances),
        _metric("violated_rules", value.rule_rollup["VIOLATION"]),
    ], "fields": {
        "overall_verdict": value.overall_verdict,
        "audience": value.audience,
        "source_reconciliation_artifact_id": value.source_reconciliation_artifact_id,
        "source_calculation_llm_used": value.source_calculation_inference_disclosure.llm_used,
        "current_journey_llm_used": value.current_journey_inference_disclosure.llm_used,
        "safe_sharing_profile": value.safe_sharing.profile_id,
    }}


@dataclass(frozen=True)
class ArtifactTypeDescriptor:
    artifact_type: str
    display_name: str
    description: str
    owner: str
    payload_schema_version: int = 1
    supported_scopes: tuple[str, ...] = ("universal", "diagnostic_local", "workflow_local")
    granularity: str = "feature"
    target_applicability: str = "optional"
    comparison_snapshot_applicability: str = "not_applicable"
    payload_validator: PayloadValidator = _mapping
    summary_adapter: SummaryAdapter = _summary
    metric_definitions: tuple[dict[str, Any], ...] = ()
    allowed_source_types: tuple[str, ...] = ()
    sensitivity: str = "internal"
    status: str = "active"
    summary_adapter_version: str = "1"

    def catalog(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type, "display_name": self.display_name,
            "description": self.description, "owner": self.owner,
            "payload_schema_version": self.payload_schema_version,
            "supported_scopes": list(self.supported_scopes), "granularity": self.granularity,
            "target_applicability": self.target_applicability,
            "comparison_snapshot_applicability": self.comparison_snapshot_applicability,
            "metric_definitions": list(self.metric_definitions), "sensitivity": self.sensitivity,
            "status": self.status, "summary_adapter_version": self.summary_adapter_version,
        }


_TYPES: dict[str, ArtifactTypeDescriptor] = {}


def register_artifact_type(descriptor: ArtifactTypeDescriptor) -> ArtifactTypeDescriptor:
    if not descriptor.artifact_type or descriptor.artifact_type in _TYPES:
        raise ValueError(f"artifact type already registered or blank: {descriptor.artifact_type!r}")
    if not descriptor.supported_scopes:
        raise ValueError("supported_scopes must not be empty")
    valid_applicability = {"required", "optional", "not_applicable"}
    if descriptor.target_applicability not in valid_applicability:
        raise ValueError("invalid target_applicability")
    if descriptor.comparison_snapshot_applicability not in valid_applicability:
        raise ValueError("invalid comparison_snapshot_applicability")
    _TYPES[descriptor.artifact_type] = descriptor
    return descriptor


def get_artifact_type(artifact_type: str) -> ArtifactTypeDescriptor | None:
    return _TYPES.get(artifact_type)


def list_artifact_types() -> list[dict[str, Any]]:
    return [descriptor.catalog() for descriptor in sorted(_TYPES.values(), key=lambda item: item.artifact_type)]


def validate_payload(artifact_type: str, payload: Any, schema_version: int) -> None:
    descriptor = get_artifact_type(artifact_type)
    if descriptor is not None:
        if schema_version != descriptor.payload_schema_version:
            raise ValueError(f"unsupported {artifact_type!r} payload schema version: {schema_version}")
        descriptor.payload_validator(payload)


def validate_identity_contract(artifact_type: str, *, scope: str,
                               target_fingerprint: str | None,
                               comparison_snapshot_id: str | None,
                               source_types: tuple[str, ...]) -> None:
    """Enforce declared write constraints without blocking unknown legacy types."""
    descriptor = get_artifact_type(artifact_type)
    if descriptor is None:
        return
    if descriptor.status != "active":
        raise ValueError(f"artifact type {artifact_type!r} is not active")
    if scope not in descriptor.supported_scopes:
        raise ValueError(f"scope {scope!r} is not supported by {artifact_type!r}")
    for label, applicability, value in (
        ("target_fingerprint", descriptor.target_applicability, target_fingerprint),
        ("comparison_snapshot_id", descriptor.comparison_snapshot_applicability, comparison_snapshot_id),
    ):
        if applicability == "required" and not value:
            raise ValueError(f"{label} is required for {artifact_type!r}")
        if applicability == "not_applicable" and value is not None:
            raise ValueError(f"{label} is not applicable to {artifact_type!r}")
    if descriptor.allowed_source_types:
        disallowed = sorted(set(source_types) - set(descriptor.allowed_source_types))
        if disallowed:
            raise ValueError(
                f"unsupported source artifact types for {artifact_type!r}: {disallowed}"
            )


def summarize(artifact_type: str, payload: Any) -> tuple[dict[str, Any], str]:
    descriptor = get_artifact_type(artifact_type)
    if descriptor is None:
        return _summary(payload), "generic-1"
    return descriptor.summary_adapter(payload), descriptor.summary_adapter_version


def _register_defaults() -> None:
    # Producer-owned types use descriptors; the repository remains generic.
    definitions = (
        ("snapshot_profile", "Snapshot profile", "Data Sourcing", "snapshot"),
        ("table_profile", "Table profile", "Data Sourcing", "table"),
        ("schema_profile", "Schema profile", "Data Sourcing", "table"),
        ("column_profile", "Column profile", "Data Sourcing", "feature"),
        ("value_distribution", "Value distribution", "Data Sourcing", "feature"),
        ("special_value_observation", "Special-value observation", "Data Sourcing", "feature"),
        ("governance_reference", "Governance reference", "Data Sourcing", "feature"),
        ("missingness_report", "Missingness report", "Supporting analyses", "table"),
        ("feature_profile", "Feature profile", "Test Lab", "feature"),
        ("fine_bins", "Fine-bin definition", "Feature Target Separation", "feature"),
        ("coarse_bins", "Coarse-bin definition", "Feature Target Separation", "feature"),
        ("roc_feature", "ROC evidence", "Feature Target Separation", "feature"),
        ("gini", "Gini evidence", "Feature Target Separation", "feature"),
        ("iv", "IV evidence", "Feature Target Separation", "feature"),
        ("reusable_diagnostic_evidence", "Reusable diagnostic evidence", "Test Lab", "feature"),
    )
    for artifact_type, name, owner, granularity in definitions:
        adapter = _column_profile_summary if artifact_type == "column_profile" else (_table_profile_summary if artifact_type == "table_profile" else _summary)
        source_contracts = {
            "table_profile": ("column_profile",),
            "roc_feature": ("column_profile",),
            "fine_bins": ("column_profile",),
            "coarse_bins": ("fine_bins",),
            "iv": ("fine_bins", "coarse_bins"),
        }
        register_artifact_type(ArtifactTypeDescriptor(
            artifact_type=artifact_type, display_name=name,
            description=f"Governed immutable {name.lower()}.", owner=owner,
            supported_scopes=(("universal", "diagnostic_local", "workflow_local", "local")
                              if artifact_type in {"coarse_bins", "iv"}
                              else ("universal", "diagnostic_local", "workflow_local")),
            granularity=granularity, summary_adapter=adapter,
            target_applicability=("required" if artifact_type in {
                "roc_feature", "fine_bins", "coarse_bins", "iv", "gini"
            } else "optional"),
            allowed_source_types=source_contracts.get(artifact_type, ()),
        ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="psi_bins", display_name="PSI frozen-bin definition",
        description="Reviewed immutable bins used by Population Stability Index.",
        owner="Population Stability Index", supported_scopes=("universal", "diagnostic_local"),
        granularity="feature", target_applicability="optional",
        comparison_snapshot_applicability="not_applicable", payload_validator=_psi_bins_validator,
        allowed_source_types=("column_profile", "coarse_bins"),
    ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="psi", display_name="Population Stability Index",
        description="Feature-level contextual PSI result with per-bin contributions.",
        owner="Population Stability Index", supported_scopes=("diagnostic_local",),
        granularity="feature", target_applicability="not_applicable",
        comparison_snapshot_applicability="required", payload_validator=_psi_validator,
        summary_adapter=_psi_summary, allowed_source_types=("psi_bins", "coarse_bins"),
        metric_definitions=({"metric_key": "psi", "label": "PSI", "direction": "higher_is_more_drift"},),
    ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="row_completeness_reconciliation",
        display_name="Row completeness reconciliation",
        description="Table-level required-grid reconciliation and six governed rule results.",
        owner="Row Completeness",
        supported_scopes=("diagnostic_local",),
        granularity="table",
        target_applicability="not_applicable",
        comparison_snapshot_applicability="not_applicable",
        payload_validator=_row_completeness_reconciliation_validator,
        summary_adapter=_row_completeness_summary,
        allowed_source_types=("table_profile", "column_profile", "governance_reference"),
        sensitivity="confidential",
        metric_definitions=(
            {"metric_key": "continuity_coverage", "label": "Continuity coverage", "direction": "higher_is_better"},
            {"metric_key": "primary_issue_instances", "label": "Primary issue instances", "direction": "lower_is_better"},
        ),
    ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="row_completeness_report",
        display_name="Row completeness external report",
        description="Safe structured source for the externally shareable PDF report.",
        owner="Row Completeness",
        supported_scopes=("diagnostic_local",),
        granularity="table",
        target_applicability="not_applicable",
        comparison_snapshot_applicability="not_applicable",
        payload_validator=_row_completeness_report_validator,
        summary_adapter=_row_completeness_report_summary,
        allowed_source_types=("row_completeness_reconciliation",),
        sensitivity="external_safe",
        metric_definitions=(
            {"metric_key": "primary_issue_instances", "label": "Primary issue instances", "direction": "lower_is_better"},
        ),
    ))


_register_defaults()
