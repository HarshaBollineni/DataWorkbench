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
    from domains.test_lab.diagnostics.t4_d14_population_stability.bins import validate_bin_definition
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


def _psi_report_validator(payload: Any) -> None:
    _mapping(payload)
    required = {"artifact_kind", "run_id", "summary", "features",
                "finding_actions", "source_artifact_ids"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"PSI report is missing fields: {missing}")
    if payload.get("artifact_kind") != "population_stability_report":
        raise ValueError("invalid PSI report artifact kind")
    if not isinstance(payload.get("features"), list):
        raise ValueError("PSI report features must be a list")


def _psi_report_summary(payload: Any) -> dict[str, Any]:
    _psi_report_validator(payload)
    summary = payload["summary"]
    return {"metrics": [
        _metric("features_completed", summary.get("features_completed")),
        _metric("drift_candidates", summary.get("drift_candidates")),
        _metric("awaiting_review", summary.get("awaiting_review")),
        _metric("issues_promoted", summary.get("issues_promoted")),
    ], "fields": {
        "run_id": payload["run_id"],
        "table": (payload.get("scope") or {}).get("table"),
        "methodology": payload.get("methodology"),
    }}


def _row_completeness_reconciliation_validator(payload: Any) -> None:
    from domains.test_lab.diagnostics.t2_d06_row_completeness.models import ReconciliationPayload
    ReconciliationPayload.model_validate(payload)


def _row_completeness_report_validator(payload: Any) -> None:
    from domains.test_lab.diagnostics.t2_d06_row_completeness.models import ReportPayload
    ReportPayload.model_validate(payload)


def _row_completeness_summary(payload: Any) -> dict[str, Any]:
    from domains.test_lab.diagnostics.t2_d06_row_completeness.models import ReconciliationPayload
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
    from domains.test_lab.diagnostics.t2_d06_row_completeness.models import ReportPayload
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


def _directionality_validator(payload: Any) -> None:
    _mapping(payload)
    required = {"artifact_kind", "feature", "reference", "expected", "overall",
                "comparison", "manifest_fingerprint"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"directionality evidence is missing fields: {missing}")
    if payload.get("artifact_kind") != "directionality_evidence":
        raise ValueError("invalid directionality artifact kind")


def _directionality_summary(payload: Any) -> dict[str, Any]:
    _directionality_validator(payload)
    evidence = payload["overall"]
    comparison = payload.get("comparison") or {}
    return {"metrics": [
        _metric("spearman", (evidence.get("spearman") or {}).get("value")),
        _metric("regression_coefficient", (evidence.get("regression") or {}).get("value")),
        _metric("pearson", (evidence.get("pearson") or {}).get("value")),
        _metric("paired_observations", evidence.get("n_paired")),
    ], "fields": {"feature": payload["feature"],
                    "observed_direction": evidence.get("observed_direction"),
                    "evidence_strength": evidence.get("evidence_strength"),
                    "expected_reference_direction": comparison.get(
                        "expected_reference_direction"),
                    "conclusion": comparison.get("conclusion"),
                    "reference_column": (payload.get("reference") or {}).get("column"),
                    "bin_count": len((evidence.get("binned") or {}).get("bins") or []),
                    "segment_count": len(payload.get("segments") or []),
                    "methodology": payload.get("methodology")}}


def _directionality_report_validator(payload: Any) -> None:
    _mapping(payload)
    required = {"artifact_kind", "run_id", "summary", "features",
                "inference_disclosure", "decision_actions", "source_artifact_ids"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"directionality report is missing fields: {missing}")
    if payload.get("artifact_kind") != "directionality_report":
        raise ValueError("invalid directionality report artifact kind")
    if not isinstance(payload.get("features"), list):
        raise ValueError("directionality report features must be a list")


def _directionality_report_summary(payload: Any) -> dict[str, Any]:
    _directionality_report_validator(payload)
    summary = payload["summary"]
    disclosure = payload["inference_disclosure"]
    return {"metrics": [
        _metric("features_completed", summary.get("features_completed")),
        _metric("awaiting_review", summary.get("awaiting_review")),
        _metric("issues_promoted", summary.get("issues_promoted")),
        _metric("llm_calls", disclosure.get("llm_call_count")),
    ], "fields": {
        "run_id": payload["run_id"],
        "reference_column": (payload.get("scope") or {}).get("reference_column"),
        "llm_used": disclosure.get("llm_used"),
        "verdict_influenced_by_llm": disclosure.get("verdict_influenced_by_llm"),
        "methodology": payload.get("methodology"),
    }}


def _feature_target_report_validator(payload: Any) -> None:
    _mapping(payload)
    required = {"artifact_kind", "run_id", "summary", "features",
                "decision_actions", "source_artifact_ids"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"feature-target report is missing fields: {missing}")
    if payload.get("artifact_kind") != "feature_target_separation_report":
        raise ValueError("invalid feature-target report artifact kind")
    if not isinstance(payload.get("features"), list):
        raise ValueError("feature-target report features must be a list")


def _feature_target_report_summary(payload: Any) -> dict[str, Any]:
    _feature_target_report_validator(payload)
    summary = payload["summary"]
    return {"metrics": [
        _metric("features_completed", summary.get("features_completed")),
        _metric("review_findings", summary.get("review_findings")),
        _metric("awaiting_review", summary.get("awaiting_review")),
        _metric("issues_promoted", summary.get("issues_promoted")),
    ], "fields": {
        "run_id": payload["run_id"],
        "target": (payload.get("scope") or {}).get("target"),
        "target_type": (payload.get("scope") or {}).get("target_type"),
        "methodology": payload.get("methodology"),
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
        artifact_type="feature_target_separation_report",
        display_name="Single-feature target separation analysis report",
        description="Run-level governed report of AUC, Gini, IV, outcome rationale, and review actions.",
        owner="Feature Target Separation",
        supported_scopes=("diagnostic_local",), granularity="run",
        target_applicability="required",
        comparison_snapshot_applicability="not_applicable",
        payload_validator=_feature_target_report_validator,
        summary_adapter=_feature_target_report_summary,
        allowed_source_types=("roc_feature", "iv"), sensitivity="confidential",
        metric_definitions=(
            {"metric_key": "features_completed", "label": "Features completed", "direction": "neutral"},
            {"metric_key": "review_findings", "label": "Review findings", "direction": "neutral"},
            {"metric_key": "awaiting_review", "label": "Awaiting review", "direction": "lower_is_better"},
            {"metric_key": "issues_promoted", "label": "Issues promoted", "direction": "neutral"},
        ),
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
        summary_adapter=_psi_summary,
        allowed_source_types=("psi_bins", "coarse_bins", "column_profile"),
        metric_definitions=({"metric_key": "psi", "label": "PSI", "direction": "higher_is_more_drift"},),
    ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="population_stability_report",
        display_name="Population Stability Index analysis report",
        description="Run-level governed report of PSI classifications, feature profiles, bin contributions, and review actions.",
        owner="Population Stability Index",
        supported_scopes=("diagnostic_local",), granularity="run",
        target_applicability="not_applicable",
        comparison_snapshot_applicability="required",
        payload_validator=_psi_report_validator,
        summary_adapter=_psi_report_summary,
        allowed_source_types=("psi", "column_profile"), sensitivity="confidential",
        metric_definitions=(
            {"metric_key": "features_completed", "label": "Features completed", "direction": "neutral"},
            {"metric_key": "drift_candidates", "label": "Drift candidates", "direction": "neutral"},
            {"metric_key": "awaiting_review", "label": "Awaiting review", "direction": "lower_is_better"},
            {"metric_key": "issues_promoted", "label": "Issues promoted", "direction": "neutral"},
        ),
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
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="directionality_evidence",
        display_name="Directional consistency evidence",
        description="Feature-level expected-versus-observed directionality evidence with optional segments.",
        owner="Directional / Monotonic Consistency",
        supported_scopes=("diagnostic_local",), granularity="feature",
        target_applicability="required",
        comparison_snapshot_applicability="not_applicable",
        payload_validator=_directionality_validator,
        summary_adapter=_directionality_summary,
        allowed_source_types=("column_profile",), sensitivity="confidential",
        metric_definitions=(
            {"metric_key": "spearman", "label": "Spearman correlation", "direction": "contextual"},
            {"metric_key": "regression_coefficient", "label": "Regression coefficient", "direction": "contextual"},
            {"metric_key": "pearson", "label": "Pearson correlation", "direction": "display_only"},
            {"metric_key": "paired_observations", "label": "Paired observations", "direction": "neutral"},
        ),
    ))
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type="directionality_report",
        display_name="Directional consistency analysis report",
        description="Run-level governed report of directionality evidence, review actions, and AI disclosure.",
        owner="Directional / Monotonic Consistency",
        supported_scopes=("diagnostic_local",), granularity="run",
        target_applicability="required",
        comparison_snapshot_applicability="not_applicable",
        payload_validator=_directionality_report_validator,
        summary_adapter=_directionality_report_summary,
        allowed_source_types=("directionality_evidence",), sensitivity="confidential",
        metric_definitions=(
            {"metric_key": "features_completed", "label": "Features completed", "direction": "neutral"},
            {"metric_key": "awaiting_review", "label": "Awaiting review", "direction": "lower_is_better"},
            {"metric_key": "issues_promoted", "label": "Issues promoted", "direction": "neutral"},
            {"metric_key": "llm_calls", "label": "Advisory LLM calls", "direction": "neutral"},
        ),
    ))


_register_defaults()
