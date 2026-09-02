"""Execution, AAR reuse, persistence and reporting for Diagnostic 6."""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import system_db as db
from domains.aar.repository import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader

from . import manifest as manifest_mod
from .engine import (
    ENGINE_VERSION,
    METHODOLOGY_VERSION,
    evaluate_row_completeness,
)
from .models import (
    ReconciliationPayload,
    ReportPayload,
    build_external_report_payload,
)
from dq_diagnostics.inference_audit import inference_disclosure
from domains.test_lab.shared.run_state import DONE, DRAFT
from dq_diagnostics.result import DiagnosticResult


DIAGNOSTIC_ID = 6
RECONCILIATION_TYPE = "row_completeness_reconciliation"
REPORT_TYPE = "row_completeness_report"
REPORT_RENDERER_VERSION = "row-completeness-pdf-v3"

_RULE_MEASURE_LABELS = {
    "T2D6-01": "Valid-key row share",
    "T2D6-02": "Reporting-period coverage",
    "T2D6-03": "Lowest period coverage",
    "T2D6-04": "Repeated facility-period pairs",
    "T2D6-05": "Portfolio continuity coverage",
    "T2D6-06": "Lowest segment-period coverage",
}

_RULE_DECISION_RATIONALES = {
    "T2D6-01": "Rows need both a usable facility identifier and a parseable reporting period before continuity can be assessed.",
    "T2D6-02": "A missing calendar period can indicate a whole-delivery gap even when no individual facility span requires that period.",
    "T2D6-03": "The lowest period coverage is compared with the minimum so a weak delivery is not hidden by a strong portfolio average.",
    "T2D6-04": "Repeated keys are counted directly because duplicate rows can overstate received population and downstream aggregates.",
    "T2D6-05": "Coverage reconciles distinct received facility-period pairs to pairs required inside each facility's observed span.",
    "T2D6-06": "The lowest assessable segment-period coverage exposes concentrated gaps that the portfolio result can mask.",
}

_RULE_ISSUE_UNITS = {
    "T2D6-01": "invalid rows",
    "T2D6-02": "missing reporting periods",
    "T2D6-03": "periods below the minimum",
    "T2D6-04": "repeated facility-period pairs",
    "T2D6-05": "missing facility-period rows",
    "T2D6-06": "segment-period cells below the minimum",
}


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
        from .api import RowCompletenessResultResponse
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


def _finding_workflow(run_id: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Project D6 finding review state without exposing source rows."""
    counts = {"awaiting_review": 0, "promoted": 0, "closed": 0, "dismissed": 0}
    actions = []
    for finding in db.query("diag_findings", run_id=run_id, outcome="VIOLATION", order_by="seq"):
        issue = db.query_one("issues_v2", finding_id=finding["finding_id"])
        if issue and issue.get("status") == "Closed":
            state = "closed"
        elif issue or finding.get("review_state") == "confirmed":
            state = "promoted"
        elif finding.get("review_state") == "dismissed":
            state = "dismissed"
        else:
            state = "awaiting_review"
        counts[state] += 1
        dispositions = db.query("diag_dispositions", target_type="finding",
                                target_id=finding["finding_id"], order_by="id")
        actions.append({
            "rule_id": finding.get("rule_id"),
            "finding_id": finding["finding_id"],
            "state": state,
            "issue_row_id": (issue or {}).get("issue_row_id"),
            "issue_status": (issue or {}).get("status"),
            "review_rationale": ((dispositions[-1].get("reason") if dispositions else None)
                                 or "Not yet recorded"),
        })
    return counts, actions


def report_payload(run_id: str) -> tuple[ReportPayload, Any, bool]:
    run_row = manifest_mod.get_run(run_id)
    _result, reconciliation, source = _result_source(run_id)
    current_disclosure = inference_disclosure(run_id)
    workflow_summary, finding_actions = _finding_workflow(run_id)
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
        generated_at=run_row.get("finished_at") or db.now_ist(),
        executor=(run_row["manifest_json"].get("frozen_by")
                  or run_row["manifest_json"].get("created_by") or "system"),
        finding_workflow_summary=workflow_summary,
        finding_actions=finding_actions,
        knowledge_provenance={key: run_row["manifest_json"].get("kb", {}).get(key) for key in (
            "document_id", "version_id", "document_sha256", "package_hash",
            "retrieval_manifest_id", "contract_version"
        )})
    repo = AnalysisArtifactRepository()
    identity_inputs = {"run_id": run_id, "audience": "external", "renderer_version": REPORT_RENDERER_VERSION,
                       "segment_label_policy": policy,
                       "kb_package_hash": run_row["manifest_json"].get("kb", {}).get("package_hash"),
                       "current_inference_event_set_hash": current_disclosure["event_set_hash"],
                       "finding_actions_hash": stable_fingerprint(finding_actions)}
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


def _percentage(value: float | None) -> str:
    return "Not assessable" if value is None else f"{value:.1%}"


def _rule_metric(rule: Any) -> str:
    label = _RULE_MEASURE_LABELS[rule.rule_id]
    if rule.rule_id == "T2D6-04":
        return f"{label}: {rule.issue_count:,}"
    return f"{label}: {_percentage(rule.measure)}"


def _rule_threshold(rule: Any) -> str:
    if rule.rule_id in {"T2D6-01", "T2D6-02", "T2D6-04"}:
        return "No invalid/missing/repeated rows"
    return _percentage(rule.continuity_floor)


def _mapping_text(mapping: dict[str, Any] | None) -> str:
    if not mapping:
        return "None"
    return "; ".join(f"{role}={column or 'unbound'}" for role, column in mapping.items())


def _report_text(payload: ReportPayload) -> str:
    knowledge = payload.provenance.get("knowledge") or {}
    scope, issues = payload.scope, payload.issue_summary
    workflow = payload.finding_workflow_summary
    lines = [
        "ROW COMPLETENESS ANALYSIS REPORT", "", "1. ANALYSIS OVERVIEW",
        "This diagnostic reconciles row existence at facility-period grain. It first validates "
        "keys and duplicate pairs, then infers required rows only inside each facility's observed "
        "first-to-last reporting span. Period, portfolio and optional segment coverage are assessed "
        "against the frozen minimum.", "", "Execution details",
        f"- Report timestamp: {payload.generated_at or 'Not recorded'}",
        f"- Executor: {payload.executor}", f"- Run ID: {payload.run_id}",
        f"- Snapshot: {scope.snapshot_id}", f"- Table: {scope.table}", "", "Analysis inputs",
        f"- Facility identifier: {scope.facility_id.column} ({scope.facility_id.source})",
        f"- Reporting period: {scope.period.column} ({scope.period.source})",
        f"- Segment: {scope.segment.column if scope.segment else 'Not selected'}",
        f"- Reporting grain: {scope.reporting_grain}",
        f"- Continuity minimum: {scope.continuity_floor:.1%}",
        f"- Knowledge version: {knowledge.get('version_id') or 'legacy manifest'}", "",
        "2. OUTCOME SUMMARY", f"- Overall verdict: {payload.overall_verdict.upper()}",
        f"- Portfolio continuity coverage: {_percentage(payload.continuity_coverage)} "
        f"({payload.provenance.get('methodology_version')})",
        f"- Primary issue instances: {issues.primary_issue_instances:,}",
        f"- Affected facilities / periods / segments: {issues.affected_facilities:,} / "
        f"{issues.affected_periods:,} / {issues.affected_segments:,}",
        f"- Invalid rows: {issues.invalid_key_rows:,}; missing pairs: "
        f"{issues.missing_facility_period_pairs:,}; surplus duplicate rows: "
        f"{issues.surplus_duplicate_rows:,}",
        f"- Finding workflow: {workflow.get('awaiting_review', 0)} awaiting review; "
        f"{workflow.get('promoted', 0)} promoted; {workflow.get('closed', 0)} closed; "
        f"{workflow.get('dismissed', 0)} dismissed", "",
        "Coverage is a principal continuity metric, not the sole rationale for the verdict. "
        "Invalid keys, absent calendar periods and repeated keys are independently decision-relevant.",
        "", "3. RULE RESULTS",
        "Rule | Outcome | Decision metric | Threshold | Decision rationale",
        "---|---|---|---|---",
    ]
    for rule in payload.rules:
        lines.append(f"{rule.rule_id} {rule.title} | {rule.outcome} | {_rule_metric(rule)} | "
                     f"{_rule_threshold(rule)} | {_RULE_DECISION_RATIONALES[rule.rule_id]}")
        lines.append(f"  Result interpretation: {rule.explanation}")

    lines.extend(["", "4. ROW-LEVEL FAILURE EVIDENCE",
                  "Examples are retained only for failed rules, use masked facility/row references, "
                  "and omit unrelated source columns. A rule's issue count is its decision unit; "
                  "the example count is underlying row-level evidence and can differ."])
    failed_rules = [rule for rule in payload.rules if rule.outcome == "VIOLATION"]
    if not failed_rules:
        lines.append("- No failed rule required row-level examples.")
    for rule in failed_rules:
        lines.append(f"- {rule.rule_id} {rule.title}: {rule.issue_count:,} "
                     f"{_RULE_ISSUE_UNITS[rule.rule_id]}; "
                     f"showing {len(rule.examples):,} of {rule.total_findings:,} retained evidence rows.")
        for example in rule.examples:
            lines.append("  Example: " + " | ".join([
                f"type={example.finding_type}",
                f"facility={example.facility_reference or 'not applicable'}",
                f"period={example.period or 'not available'}",
                f"segment={example.segment or 'not applicable'}",
                f"row={example.row_reference or 'not applicable'}",
                f"why={example.reason}",
            ]))
        if rule.evidence_truncated:
            lines.append("  Additional evidence exists in the governed reconciliation artifact.")

    lines.extend(["", "5. AI ASSISTANCE AND HUMAN REVIEW",
                  "AI is optional and is limited to advisory semantic-role verification before "
                  "the manifest is frozen. It receives bounded column metadata, not row-level data; "
                  "it does not infer missing rows, calculate coverage or determine the verdict.",
                  payload.current_journey_inference_disclosure.statement,
                  f"- Source calculation disclosure: {payload.source_calculation_inference_disclosure.statement}",
                  f"- LLM calls made: {payload.current_journey_inference_disclosure.llm_call_count}",
                  "- LLM influenced empirical verdict: No"])
    invoked = [event for event in payload.current_journey_inference_disclosure.events
               if event.get("invoked")]
    if invoked:
        for event in invoked:
            lines.extend([
                f"- Advisory review status: {event.get('status')}",
                f"  Why requested: optional verification of facility, period and segment column roles.",
                f"  AI suggestion: {_mapping_text(event.get('proposed_mapping'))}",
                f"  Deterministic mapping: {_mapping_text(event.get('deterministic_mapping'))}",
                f"  Final applied mapping: {_mapping_text(event.get('final_applied_mapping'))}",
                f"  Review rationale / failure: {event.get('sanitized_error') or 'Bounded suggestion recorded for user review; no automatic mapping was applied.'}",
                f"  Row-level data included: {'Yes' if event.get('row_level_data_included') else 'No'}",
            ])
    else:
        lines.append("- No semantic-role review was submitted to AI; frozen roles came from "
                     "deterministic inference and/or explicit user selection.")

    lines.extend(["", "6. FINDINGS AND ISSUES"])
    if payload.finding_actions:
        for action in payload.finding_actions:
            lines.append(f"- {action['rule_id']}: {action['state']}"
                         + (f" ({action['issue_row_id']})" if action.get("issue_row_id") else "")
                         + f"; review rationale: {action.get('review_rationale') or 'Not recorded'}")
    else:
        lines.append("- No failed-rule finding required a user decision.")
    lines.extend(["", "7. RECOMMENDED ACTIONS"])
    lines.extend(f"- [{step['priority']}] {step['action']}" for step in payload.recommended_next_steps)
    lines.extend(["", "8. INTERPRETATION AND SHARING LIMITS"])
    lines.extend(f"- {value}" for value in payload.limitations)
    lines.extend([
        "- Masked references are report-specific and are not source facility or row identifiers.",
        "- The report contains examples, not raw source rows or an exhaustive evidence extract.",
        f"- Source reconciliation artifact: {payload.source_reconciliation_artifact_id}",
        f"- Knowledge package hash: {knowledge.get('package_hash') or 'not recorded'}",
        f"- Knowledge retrieval record: {knowledge.get('retrieval_manifest_id') or 'not recorded'}",
    ])
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
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    def safe(value: Any) -> str:
        if value is None:
            return "-"
        return str(value).encode("latin-1", "replace").decode("latin-1")

    def section(title: str) -> None:
        pdf.ln(2)
        pdf.set_fill_color(239, 246, 255)
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 7, safe(title), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def paragraph(value: Any, *, bold: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 8.3)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 4.2, safe(value), new_x="LMARGIN", new_y="NEXT")

    def wraps(value: Any, width: float) -> list[str]:
        words = safe(value).split()
        if not words:
            return ["-"]
        output, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and pdf.get_string_width(candidate) > width - 3:
                output.append(current)
                current = word
            else:
                current = candidate
        output.append(current)
        return output

    def table(headers: list[str], rows: list[list[Any]], widths: list[float]) -> None:
        line_height = 4.0

        def draw(values: list[Any], *, header: bool = False) -> None:
            pdf.set_font("Helvetica", "B" if header else "", 7.1)
            cells = [wraps(value, width) for value, width in zip(values, widths)]
            height = max(len(value) for value in cells) * line_height + 1.4
            if pdf.get_y() + height > pdf.h - 12:
                pdf.add_page()
            start_x, start_y = pdf.get_x(), pdf.get_y()
            for index, (cell, width) in enumerate(zip(cells, widths)):
                x = start_x + sum(widths[:index])
                pdf.set_xy(x, start_y)
                pdf.set_fill_color(*((226, 232, 240) if header else (255, 255, 255)))
                pdf.rect(x, start_y, width, height, style="DF")
                # Lines are measured above. Draw them explicitly so FPDF cannot
                # re-wrap the final line and move only that fragment to a new page.
                for line_index, line in enumerate(cell):
                    baseline = start_y + .7 + (line_index + .8) * line_height
                    pdf.text(x + 1.2, baseline, line)
            pdf.set_xy(start_x, start_y + height)

        draw(headers, header=True)
        for row in rows:
            draw(row)
        pdf.ln(1.5)

    scope, issues = payload.scope, payload.issue_summary
    workflow = payload.finding_workflow_summary
    knowledge = payload.provenance.get("knowledge") or {}
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Row Completeness Analysis", new_x="LMARGIN", new_y="NEXT")
    paragraph("Governed row-level diagnostic report", bold=True)
    section("1. Analysis overview")
    paragraph("This diagnostic reconciles row existence at facility-period grain. Required rows "
              "are inferred only inside each facility's observed first-to-last reporting span. "
              "Key validity, calendar sequence, period, duplicate, portfolio and optional segment "
              "rules provide distinct evidence for the verdict.")
    table(["Execution detail", "Value"], [
        ["Report timestamp", payload.generated_at or "Not recorded"],
        ["Executor", payload.executor], ["Run ID", payload.run_id],
        ["Snapshot", scope.snapshot_id], ["Table", scope.table],
    ], [48, 138])
    table(["Analysis input", "Frozen selection / rationale"], [
        ["Facility identifier", f"{scope.facility_id.column} ({scope.facility_id.source}): {scope.facility_id.reason}"],
        ["Reporting period", f"{scope.period.column} ({scope.period.source}): {scope.period.reason}"],
        ["Segment", (f"{scope.segment.column} ({scope.segment.source}): {scope.segment.reason}"
                     if scope.segment else "Not selected; segment rule is not applicable")],
        ["Reporting grain", scope.reporting_grain],
        ["Continuity minimum", f"{scope.continuity_floor:.1%}"],
        ["Knowledge version", knowledge.get("version_id") or "legacy manifest"],
    ], [48, 138])

    section("2. Outcome summary")
    table(["Verdict", "Coverage", "Primary issues", "Facilities", "Periods", "Segments"], [[
        payload.overall_verdict.upper(), _percentage(payload.continuity_coverage),
        issues.primary_issue_instances, issues.affected_facilities,
        issues.affected_periods, issues.affected_segments,
    ]], [31, 31, 31, 31, 31, 31])
    table(["Invalid rows", "Missing pairs", "Duplicate surplus", "Awaiting", "Promoted", "Dismissed/closed"], [[
        issues.invalid_key_rows, issues.missing_facility_period_pairs,
        issues.surplus_duplicate_rows, workflow.get("awaiting_review", 0),
        workflow.get("promoted", 0),
        workflow.get("dismissed", 0) + workflow.get("closed", 0),
    ]], [31, 31, 31, 31, 31, 31])
    paragraph("Coverage is a principal continuity metric, not the sole rationale for the verdict. "
              "Invalid keys, absent calendar periods and repeated keys are independently decision-relevant.")

    section("3. Rule results")
    table(["Rule", "Outcome", "Decision metric", "Threshold", "Rationale"], [[
        f"{rule.rule_id} {rule.title}", rule.outcome, _rule_metric(rule),
        _rule_threshold(rule), f"{_RULE_DECISION_RATIONALES[rule.rule_id]} {rule.explanation}",
    ] for rule in payload.rules], [35, 23, 34, 28, 66])

    section("4. Row-level failure evidence")
    paragraph("Only failed rules are shown. Facility and row references are report-specific masks; "
              "unrelated source columns are omitted. Issue counts use each rule's decision unit, "
              "while examples are underlying row evidence and may therefore differ.")
    failed_rules = [rule for rule in payload.rules if rule.outcome == "VIOLATION"]
    if not failed_rules:
        paragraph("No failed rule required row-level examples.")
    for rule in failed_rules:
        paragraph(f"{rule.rule_id} {rule.title}: {rule.issue_count:,} "
                  f"{_RULE_ISSUE_UNITS[rule.rule_id]}; showing {len(rule.examples):,} of "
                  f"{rule.total_findings:,} retained evidence rows.", bold=True)
        if rule.examples:
            table(["Type", "Masked facility", "Period", "Segment", "Masked row", "Why it failed"], [[
                example.finding_type, example.facility_reference or "-", example.period or "-",
                example.segment or "-", example.row_reference or "-", example.reason,
            ] for example in rule.examples], [24, 31, 24, 26, 31, 50])
        else:
            paragraph("The failure is aggregate; no row-level example applies or was retained.")
        if rule.evidence_truncated:
            paragraph("Additional evidence exists in the governed reconciliation artifact.")

    section("5. AI assistance and human review")
    paragraph("AI is optional and limited to advisory semantic-role verification before manifest "
              "freeze. It receives bounded column metadata, not row-level data. It does not infer "
              "missing rows, calculate coverage or determine the verdict.")
    table(["Disclosure", "Recorded outcome"], [
        ["Current report journey", payload.current_journey_inference_disclosure.statement],
        ["Source calculation artifact", payload.source_calculation_inference_disclosure.statement],
        ["Empirical verdict influenced by AI", "No"],
    ], [55, 131])
    invoked = [event for event in payload.current_journey_inference_disclosure.events
               if event.get("invoked")]
    if invoked:
        for event in invoked:
            table(["Advisory review", event.get("status") or "unknown"], [
                ["Why requested", "Optional verification of facility, period and segment column roles."],
                ["AI suggestion", _mapping_text(event.get("proposed_mapping"))],
                ["Deterministic mapping", _mapping_text(event.get("deterministic_mapping"))],
                ["Final applied mapping", _mapping_text(event.get("final_applied_mapping"))],
                ["Review rationale / failure", event.get("sanitized_error") or
                 "Bounded suggestion recorded for user review; no automatic mapping was applied."],
                ["Row-level data included", "Yes" if event.get("row_level_data_included") else "No"],
            ], [48, 138])
    else:
        paragraph("No role review was submitted to AI; frozen roles came from deterministic "
                  "inference and/or explicit user selection.")

    section("6. Findings and issues")
    if payload.finding_actions:
        table(["Rule", "State", "Issue reference", "Review rationale"], [[
            action["rule_id"], action["state"], action.get("issue_row_id") or "-",
            action.get("review_rationale") or "Not recorded",
        ] for action in payload.finding_actions], [31, 33, 42, 80])
    else:
        paragraph("No failed-rule finding required a user decision.")

    section("7. Recommended actions")
    for step in payload.recommended_next_steps:
        paragraph(f"- [{step['priority']}] {step['action']}")
    section("8. Interpretation and sharing limits")
    for value in payload.limitations:
        paragraph(f"- {value}")
    paragraph("- Masked references are report-specific; the report contains examples, not raw "
              "source rows or an exhaustive evidence extract.")
    paragraph(f"- Source reconciliation artifact: {payload.source_reconciliation_artifact_id}")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    """Return the PDF and its governed AAR/API metadata in one operation."""
    payload, artifact, reused = report_payload(run_id)
    from .api import RowCompletenessReportMetadata
    metadata = RowCompletenessReportMetadata(run_id=run_id,
        report_artifact={"artifact_id": artifact.artifact_id,
                         "artifact_type": artifact.artifact_type,
                         "payload_hash": artifact.payload_hash, "reused": reused},
        filename=f"row-completeness-{run_id}.pdf")
    return _render_pdf(payload), metadata.model_dump(mode="json")


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]
