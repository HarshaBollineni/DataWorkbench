"""Execution, AAR reuse, persistence and reporting for Diagnostic 6."""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import system_db as db
from analysis_runtime.artifacts import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader

from . import manifest_row_completeness as manifest_mod
from .engines.row_completeness.engine import (
    ENGINE_VERSION,
    METHODOLOGY_VERSION,
    evaluate_row_completeness,
)
from .engines.row_completeness.models import (
    ReconciliationPayload,
    ReportPayload,
    build_external_report_payload,
)
from .inference_audit import inference_disclosure
from .manifest import DONE, DRAFT
from .result import DiagnosticResult


DIAGNOSTIC_ID = 6
RECONCILIATION_TYPE = "row_completeness_reconciliation"
REPORT_TYPE = "row_completeness_report"
REPORT_RENDERER_VERSION = "row-completeness-pdf-v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    scope = manifest["scope"]
    population = stable_fingerprint({"snapshot_id": scope["snapshot_id"], "table": scope["table"],
                                     "population": "all_snapshot_rows"})
    inputs = {"scope": scope, "rule_ids": manifest["rule_ids"],
              "kb_version_id": manifest["kb"].get("version_id"),
              "kb_package_hash": manifest["kb"].get("package_hash"),
              "engine_version": ENGINE_VERSION, "methodology_version": METHODOLOGY_VERSION}
    methodology = stable_fingerprint(inputs)
    return {"artifact_type": RECONCILIATION_TYPE, "asset_id": scope["asset_id"],
        "snapshot_id": scope["snapshot_id"], "comparison_snapshot_id": None,
        "population_fingerprint": population, "target_fingerprint": None, "feature": None,
        "methodology_fingerprint": methodology, "scope": "diagnostic_local",
        "workflow_id": None, "owner_id": manifest_mod.OWNER_ID, "table": scope["table"],
        "features": tuple(binding["column"] for binding in (
            scope["facility_id"], scope["period"], scope.get("segment")) if binding),
        "source_artifacts": tuple(manifest["source_artifact_references"]),
        "identity_inputs": inputs}


def _reconcile(manifest: dict[str, Any], actor: str) -> tuple[Any, ReconciliationPayload, bool]:
    repo, identity = AnalysisArtifactRepository(), _identity(manifest)
    existing = repo.find_exact(**identity)
    if existing is not None:
        metadata, payload = repo.get(existing.artifact_id)
        return metadata, ReconciliationPayload.model_validate(payload), True
    scope = manifest["scope"]
    # Duplicate classification compares the complete delivered records, while
    # findings/report projections retain only governed key/segment evidence.
    columns = list(manifest["available_columns"])
    frame = SnapshotLoader().load_table(scope["snapshot_id"], scope["table"], columns=columns)
    payload = evaluate_row_completeness(frame, scope=scope, origin_run_id=manifest["run_id"],
        calculation_inference_disclosure=manifest["inference_disclosure"],
        manifest_fingerprint=manifest["manifest_fingerprint"],
        source_artifact_references=manifest["source_artifact_references"],
        rule_specs=manifest["kb"]["rules"])
    outcome = repo.save(payload.model_dump(mode="json"), **identity,
                        run_id=manifest["run_id"], created_by=actor)
    return outcome.artifact, payload, outcome.outcome == "reused"


def _rollup(payload: ReconciliationPayload) -> dict[str, int]:
    values = {key: 0 for key in ("PASS", "VIOLATION", "NO-VERDICT",
                                  "NOT-APPLICABLE", "NOT-ASSESSABLE")}
    for rule in payload.rules:
        values[rule.outcome] += 1
    return values


def _persist(manifest: dict[str, Any], payload: ReconciliationPayload,
             artifact: Any, reused: bool) -> dict[str, Any]:
    verdict = payload.overall_verdict
    na_reason = ("No row-completeness rule was applicable to the selected data."
                 if verdict == "not_applicable" else None)
    envelope = DiagnosticResult(decision_type="verdict", diagnostic_id=DIAGNOSTIC_ID,
                                verdict=verdict, na_reason=na_reason,
                                metric=payload.continuity_coverage,
                                evidence={"rollup": _rollup(payload)})
    result_id, now = _id("dres"), db.now_ist()
    current_disclosure = inference_disclosure(manifest["run_id"])
    artifact_ref = {"artifact_id": artifact.artifact_id, "artifact_type": artifact.artifact_type,
                    "payload_hash": artifact.payload_hash, "reused": reused}
    total_rows = int(payload.rules[0].affected_population.get("rows_evaluated") or 0)
    db.insert("diag_results", {"result_id": result_id, "run_id": manifest["run_id"],
        "diagnostic_id": DIAGNOSTIC_ID, "entity_or_table": payload.scope.table,
        "decision_type": envelope.decision_type, "verdict": envelope.verdict,
        "review_state": envelope.review_state,
        "metrics_json": {"result_kind": "run_summary", "metric": envelope.metric,
            "rollup": _rollup(payload), "issue_summary": payload.issue_summary.model_dump(mode="json"),
            "knowledge_provenance": {key: manifest["kb"].get(key) for key in (
                "document_id", "version_id", "document_sha256", "package_hash",
                "retrieval_manifest_id", "contract_version"
            )},
            "reconciliation_artifact": artifact_ref,
            "structured_result": payload.model_dump(mode="json"),
            "current_journey_inference_disclosure": current_disclosure,
            "source_calculation_inference_disclosure": payload.calculation_inference_disclosure.model_dump(mode="json")},
        "thresholds_used_json": {"continuity_floor": payload.scope.continuity_floor,
            "source": manifest["configuration"]["continuity_floor"]["source"]},
        "scope_counts_json": {"rows_evaluated": total_rows,
            "facilities_assessed": payload.facilities_assessed,
            "periods_assessed": payload.periods_assessed,
            "required_facility_period_pairs": payload.required_facility_period_pairs},
        "na_reason": envelope.na_reason, "created_at": now})
    specs = {rule["rule_id"]: rule for rule in manifest["kb"]["rules"]}
    roles = {name: (binding or {}).get("column") for name, binding in manifest["roles"].items()}
    for seq, rule in enumerate(payload.rules):
        db.insert("diag_findings", {"finding_id": _id("dfind"), "result_id": result_id,
            "run_id": manifest["run_id"], "rule_id": rule.rule_id,
            "kb_rule_id": specs[rule.rule_id].get("kb_rule_id", rule.rule_id),
            "severity": specs[rule.rule_id].get("severity", "MATERIAL"), "outcome": rule.outcome,
            "violation_count": rule.issue_count, "rate": rule.measure,
            "tolerance": rule.continuity_floor, "exceptions_json": {"count": 0, "notes": []},
            "evidence_json": [entry.model_dump(mode="json") for entry in rule.evidence],
            "pattern": "row_completeness", "pattern_detail": rule.explanation,
            "regulatory_ref": None, "rule_text": rule.explanation,
            "rule_type": "deterministic_reconciliation", "framework": "T2",
            "entity": payload.scope.table, "resolved_roles_json": roles,
            "tables_used": payload.scope.table, "scope_rows_evaluated": total_rows,
            "scope_rows_skipped": 0, "na_reason": rule.na_reason,
            "review_state": "open" if rule.outcome == "VIOLATION" else "not_required",
            "seq": seq, "created_at": now})
    return {"result_id": result_id, "verdict": verdict, "rollup": _rollup(payload),
            "findings": len(payload.rules), "artifact": artifact_ref}


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    run_row = manifest_mod.get_run(run_id)
    if run_row["status"] == DONE:
        results = db.query("diag_results", run_id=run_id)
        summary = results[0] if results else None
        metrics = (summary or {}).get("metrics_json") or {}
        findings = db.query("diag_findings", run_id=run_id)
        review_required = sum(1 for finding in findings
                              if finding.get("review_state") == "open")
        issues_created = len(db.query("issues_v2", run_id=run_id, diagnostic_id=6))
        yield {"phase": "start", "agent": "row_completeness_engine", "run_id": run_id,
               "thought": "Replaying a completed run; persisted results are unchanged."}
        yield {"phase": "done", "agent": "row_completeness_engine", "run_id": run_id,
               "result_id": (summary or {}).get("result_id"), "verdict": (summary or {}).get("verdict"),
               "rollup": metrics.get("rollup") or {}, "findings": len(findings),
               "review_required": review_required, "issues_created": issues_created,
               "reconciliation_artifact": metrics.get("reconciliation_artifact"),
               "thought": "Run already complete."}
        return
    manifest = manifest_mod.freeze(run_id, actor) if run_row["status"] == DRAFT else run_row["manifest_json"]
    yield {"phase": "start", "agent": "row_completeness_engine", "run_id": run_id,
           "total": len(manifest["rule_ids"]),
           "thought": f"Reconciling facility rows across {manifest['table']} using the frozen observed-span scope."}
    artifact, payload, reused = _reconcile(manifest, actor)
    for done, rule in enumerate(payload.rules, start=1):
        yield {"phase": "progress", "agent": "row_completeness_engine", "done": done,
               "total": len(payload.rules), "rule_id": rule.rule_id, "outcome": rule.outcome,
               "thought": f"[{done}/{len(payload.rules)}] {rule.rule_id} -> {rule.outcome}"}
    summary = _persist(manifest, payload, artifact, reused)
    db.update("diag_runs", {"run_id": run_id}, {"status": DONE, "finished_at": db.now_ist()})
    review_required = summary["rollup"].get("VIOLATION", 0)
    yield {"phase": "done", "agent": "row_completeness_engine", "run_id": run_id,
           "result_id": summary["result_id"], "verdict": summary["verdict"],
           "rollup": summary["rollup"], "findings": summary["findings"],
           "review_required": review_required, "issues_created": 0,
           "reconciliation_artifact": summary["artifact"],
           "thought": (("Run complete using an exact reusable reconciliation artifact. "
                        if reused else "Run complete; a governed reconciliation artifact was created. ")
                       + f"{review_required} failed rule finding(s) await user review; no issue was created automatically.")}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last: dict[str, Any] = {}
    for event in run(run_id, actor):
        last = event
    return last


def run_results(run_id: str) -> dict[str, Any]:
    run_row = manifest_mod.get_run(run_id)
    results = []
    for result in db.query("diag_results", run_id=run_id):
        findings = db.query("diag_findings", result_id=result["result_id"], order_by="seq")
        for finding in findings:
            finding["dispositions"] = db.query("diag_dispositions", target_type="finding",
                                                target_id=finding["finding_id"], order_by="id")
            issue = db.query_one("issues_v2", finding_id=finding["finding_id"])
            if issue:
                finding["existing_issue"] = {"issue_row_id": issue["issue_row_id"],
                                             "status": issue.get("status"), "same_finding": True}
        results.append({**result, "findings": findings})
    contract = None
    if run_row["status"] == DONE and results:
        metrics = results[0].get("metrics_json") or {}
        from .engines.row_completeness.api import RowCompletenessResultResponse
        contract = RowCompletenessResultResponse(run_id=run_id,
            reconciliation_artifact=metrics["reconciliation_artifact"],
            result=metrics["structured_result"],
            current_journey_inference_disclosure=metrics["current_journey_inference_disclosure"]
        ).model_dump(mode="json")
    return {"run": {key: run_row[key] for key in ("run_id", "item_id", "diagnostic_id", "status",
                                                    "created_at", "started_at", "finished_at")},
            "manifest": run_row["manifest_json"],
            "decisions": manifest_mod.list_decisions(run_id), "results": results,
            "row_completeness_result": contract}


def _result_source(run_id: str) -> tuple[dict[str, Any], ReconciliationPayload, Any]:
    rows = db.query("diag_results", run_id=run_id)
    if not rows:
        raise KeyError(f"No results for run: {run_id}")
    metrics = rows[0].get("metrics_json") or {}
    reference = metrics.get("reconciliation_artifact") or {}
    metadata, payload = AnalysisArtifactRepository().get(reference.get("artifact_id"))
    return rows[0], ReconciliationPayload.model_validate(payload), metadata


def _next_steps(payload: ReconciliationPayload,
                rule_specs: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    if rule_specs and all(spec.get("next_step") for spec in rule_specs):
        specs = {spec["rule_id"]: spec for spec in rule_specs}
        steps = [{"rule_id": rule.rule_id,
                  "priority": "high" if specs[rule.rule_id].get("severity") == "CRITICAL" else "medium",
                  "action": specs[rule.rule_id]["next_step"]}
                 for rule in payload.rules if rule.outcome == "VIOLATION"]
        if steps:
            return steps
        return [{"priority": "routine",
                 "action": "Retain this reconciliation as evidence and repeat it for the next delivery."}]
    # Frozen manifests from before KB versioning retain their original advice.
    steps = []
    if payload.issue_summary.invalid_key_rows:
        steps.append({"priority": "high", "action": "Correct or quarantine rows with blank/unparseable facility-period keys."})
    if payload.issue_summary.surplus_duplicate_rows:
        steps.append({"priority": "high", "action": "Reconcile repeated facility-period rows before downstream aggregation."})
    if payload.issue_summary.missing_facility_period_pairs:
        steps.append({"priority": "high", "action": "Trace missing rows within each facility's observed first-to-last reporting span."})
    if payload.issue_summary.missing_reporting_periods:
        steps.append({"priority": "medium", "action": "Confirm whether wholly absent reporting periods reflect a source delivery gap."})
    if not steps:
        steps.append({"priority": "routine", "action": "Retain this reconciliation as evidence and repeat it for the next delivery."})
    return steps


def report_payload(run_id: str) -> tuple[ReportPayload, Any, bool]:
    run_row = manifest_mod.get_run(run_id)
    _result, reconciliation, source = _result_source(run_id)
    current_disclosure = inference_disclosure(run_id)
    policy = run_row["manifest_json"]["configuration"]["segment_label_policy"]["value"]
    report_id = f"rpt_{run_id}"
    payload = build_external_report_payload(reconciliation, report_id=report_id,
        source_artifact_id=source.artifact_id, source_payload_hash=source.payload_hash,
        renderer_version=REPORT_RENDERER_VERSION,
        report_salt=stable_fingerprint({"run_id": run_id, "source": source.payload_hash}),
        recommended_next_steps=_next_steps(reconciliation, run_row["manifest_json"].get("kb", {}).get("rules")),
        limitations=["Required rows are inferred only between each facility's first and last observed periods.",
                     "A row before the first or after the last observed facility period is not inferred as missing.",
                     "The diagnostic does not determine why a row is absent."],
        current_run_id=run_id, segment_label_policy=policy,
        current_journey_inference_disclosure=current_disclosure,
        knowledge_provenance={key: run_row["manifest_json"].get("kb", {}).get(key) for key in (
            "document_id", "version_id", "document_sha256", "package_hash",
            "retrieval_manifest_id", "contract_version"
        )})
    repo = AnalysisArtifactRepository()
    identity_inputs = {"run_id": run_id, "audience": "external", "renderer_version": REPORT_RENDERER_VERSION,
                       "segment_label_policy": policy,
                       "kb_package_hash": run_row["manifest_json"].get("kb", {}).get("package_hash"),
                       "current_inference_event_set_hash": current_disclosure["event_set_hash"]}
    identity = {"artifact_type": REPORT_TYPE, "asset_id": reconciliation.scope.asset_id,
        "snapshot_id": reconciliation.scope.snapshot_id, "comparison_snapshot_id": None,
        "population_fingerprint": stable_fingerprint({"source_payload_hash": source.payload_hash}),
        "target_fingerprint": None, "feature": None,
        "methodology_fingerprint": stable_fingerprint(identity_inputs), "scope": "diagnostic_local",
        "workflow_id": None, "owner_id": manifest_mod.OWNER_ID, "table": reconciliation.scope.table,
        "features": (), "source_artifacts": ({"artifact_id": source.artifact_id, "role": "reconciliation"},),
        "identity_inputs": identity_inputs}
    outcome = repo.save(payload.model_dump(mode="json"), **identity, run_id=run_id, created_by="system")
    return payload, outcome.artifact, outcome.outcome == "reused"


def _report_text(payload: ReportPayload) -> str:
    knowledge = payload.provenance.get("knowledge") or {}
    lines = ["ROW COMPLETENESS DIAGNOSTIC", f"Run: {payload.run_id}",
             f"Overall verdict: {payload.overall_verdict.upper()}",
             f"Continuity coverage: {payload.continuity_coverage:.1%}" if payload.continuity_coverage is not None else "Continuity coverage: not assessable",
             f"Total primary issues: {payload.issue_summary.primary_issue_instances}",
             f"Knowledge version: {knowledge.get('version_id') or 'legacy manifest'}",
             f"Knowledge package hash: {knowledge.get('package_hash') or 'not recorded'}",
             f"Knowledge retrieval record: {knowledge.get('retrieval_manifest_id') or 'not recorded'}",
             "", "RULE RESULTS"]
    for rule in payload.rules:
        lines.extend([f"{rule.rule_id} | {rule.title} | {rule.outcome} | issues: {rule.issue_count}",
                      rule.explanation])
        for example in rule.examples:
            parts = [example.facility_reference, example.period, example.segment, example.row_reference]
            lines.append("  Example: " + " | ".join(value for value in parts if value) + f" - {example.reason}")
    lines.extend(["", "RECOMMENDED NEXT STEPS"])
    lines.extend(f"- [{step['priority']}] {step['action']}" for step in payload.recommended_next_steps)
    lines.extend(["", "LIMITATIONS"])
    lines.extend(f"- {value}" for value in payload.limitations)
    lines.extend(["", "AI / INFERENCE DISCLOSURE",
                  payload.source_calculation_inference_disclosure.statement,
                  payload.current_journey_inference_disclosure.statement])
    return "\n".join(lines)


def report_text(run_id: str) -> str:
    payload, _artifact, _reused = report_payload(run_id)
    return _report_text(payload)


def _render_pdf(payload: ReportPayload) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError:  # pragma: no cover
        return _report_text(payload).encode("utf-8")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(12, 12, 12); pdf.set_auto_page_break(auto=True, margin=12); pdf.add_page()
    pdf.set_font("Helvetica", "B", 16); pdf.cell(0, 9, "Row Completeness Diagnostic", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for line in _report_text(payload).splitlines()[1:]:
        safe = line.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 4.2, safe or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    """Return the PDF and its governed AAR/API metadata in one operation."""
    payload, artifact, reused = report_payload(run_id)
    from .engines.row_completeness.api import RowCompletenessReportMetadata
    metadata = RowCompletenessReportMetadata(run_id=run_id,
        report_artifact={"artifact_id": artifact.artifact_id,
                         "artifact_type": artifact.artifact_type,
                         "payload_hash": artifact.payload_hash, "reused": reused},
        filename=f"row-completeness-{run_id}.pdf")
    return _render_pdf(payload), metadata.model_dump(mode="json")


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]
