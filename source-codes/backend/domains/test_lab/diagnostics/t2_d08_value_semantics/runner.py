"""Deterministic execution, AAR persistence, findings, and reports for T2-D08."""
from __future__ import annotations

import io
import json
import uuid
from typing import Any, Generator

import pandas as pd

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader
from domains.aar.repository import AnalysisArtifactRepository
from domains.test_lab.shared.run_state import DONE, DRAFT, RUNNING
from dq_diagnostics.inference_audit import inference_disclosure

from . import knowledge, manifest as manifest_mod
from .actions import build_action_focused_summary
from .engine import execute_value_semantics
from .summaries import summarize_value_semantics

DIAGNOSTIC_ID = 8
AGENT = "value_semantics_engine"
METHODOLOGY = "role_routed_cell_semantics_v1"
REPORT_RENDERER_VERSION = "1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _artifact_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    identity = {
        "table": manifest["table"],
        "selected_fields": manifest["selected_fields"],
        "bindings_hash": stable_fingerprint(manifest["confirmed_role_bindings"]),
        "declarations_hash": stable_fingerprint(manifest["runtime_declarations"]),
        "value_semantics_kb": manifest["knowledge"]["value_semantics_version"],
        "terminology": manifest["knowledge"]["terminology_version"],
        "engine_version": manifest["engine_version"],
    }
    if manifest.get("field_decisions_version"):
        identity["field_decisions_hash"] = stable_fingerprint(manifest_mod.field_scope_decisions(manifest))
    return identity


def _artifact_args(manifest: dict[str, Any], actor: str,
                   artifact_type: str) -> dict[str, Any]:
    snapshot = SnapshotLoader().reference(manifest["item_id"])
    identity = _artifact_identity(manifest)
    features = manifest["selected_fields"]
    if manifest.get("field_decisions_version") and artifact_type in {
        "value_semantics_bindings", "value_semantics_report",
    }:
        features = [row["column"] for row in manifest["fields"]]
    return {
        "artifact_type": artifact_type, "asset_id": snapshot.asset_id,
        "snapshot_id": manifest["item_id"], "comparison_snapshot_id": None,
        "population_fingerprint": stable_fingerprint({
            "snapshot": manifest["item_id"], "table": manifest["table"],
            "selected_fields": manifest["selected_fields"],
        }),
        "target_fingerprint": None,
        "methodology_fingerprint": stable_fingerprint({
            "methodology": METHODOLOGY, **identity,
        }),
        "scope": "diagnostic_local", "workflow_id": None,
        "owner_id": "t2_d08_value_semantics", "table": manifest["table"],
        "features": tuple(features),
        "identity_inputs": identity, "run_id": manifest["run_id"],
        "created_by": actor,
    }


def _save_bindings(manifest: dict[str, Any], actor: str) -> tuple[Any, bool]:
    payload = {
        "artifact_kind": "value_semantics_bindings",
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "table": manifest["table"], "selected_fields": manifest["selected_fields"],
        "confirmed_role_bindings": manifest["confirmed_role_bindings"],
        "binding_decisions": [
            {key: row.get(key) for key in (
                "column", "confirmed_roles", "binding_source", "confirmed_by",
                "match_status", "match_method",
            )}
            for row in manifest["fields"] if row.get("selected")
        ],
        "context": manifest["context"],
        "runtime_declarations": manifest["runtime_declarations"],
        "coverage": manifest["coverage"], "knowledge": manifest["knowledge"],
        "inference_disclosure": inference_disclosure(manifest["run_id"]),
    }
    if manifest.get("field_decisions_version"):
        payload["field_scope_decisions"] = manifest_mod.field_scope_decisions(manifest)
        payload["field_decisions_version"] = manifest["field_decisions_version"]
    outcome = AnalysisArtifactRepository().save(
        payload, source_artifact_ids=tuple(
            value["artifact_id"] for value in manifest.get("source_artifact_references") or []
            if value.get("artifact_id")
        ), **_artifact_args(manifest, actor, "value_semantics_bindings"),
    )
    return outcome.artifact, outcome.outcome == "reused"


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False, engine="pyarrow", compression="zstd")
    return buffer.getvalue()


def _save_parquet(manifest: dict[str, Any], actor: str, artifact_type: str,
                  frame: pd.DataFrame, binding_artifact_id: str,
                  summary: dict[str, Any]) -> tuple[Any, bool]:
    outcome = AnalysisArtifactRepository().save_blob(
        _parquet_bytes(frame), {
            "artifact_kind": artifact_type,
            "producer_run_id": manifest["run_id"],
            "manifest_fingerprint": manifest["manifest_fingerprint"],
            "table": manifest["table"], "format": "parquet",
            "record_count": len(frame), **summary,
        }, payload_media_type="application/vnd.apache.parquet",
        payload_extension="parquet",
        payload_filename=f"{artifact_type}-{manifest['item_id']}-{manifest['table']}.parquet",
        source_artifact_ids=(binding_artifact_id,),
        **_artifact_args(manifest, actor, artifact_type),
    )
    return outcome.artifact, outcome.outcome == "reused"


def _generated_row_references(manifest: dict[str, Any], count: int) -> list[str]:
    return [
        stable_fingerprint({"snapshot": manifest["item_id"],
                            "table": manifest["table"], "ordinal": index})[:20]
        for index in range(count)
    ]


def _dictionary(manifest: dict[str, Any], columns: list[str]) -> pd.DataFrame:
    manifest_fields = {row["column"]: row for row in manifest["fields"]}
    rows = []
    for column in columns:
        field = manifest_fields.get(column) or {}
        rows.append({"column_name": column, "description": field.get("description") or "",
                     "data_type": field.get("data_type") or "generated",
                     "role": field.get("profile_role") or "ignore"})
    return pd.DataFrame(rows)


def _persist_results(manifest: dict[str, Any], summaries: dict[str, pd.DataFrame],
                     actions: dict[str, pd.DataFrame], artifacts: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    result_id, now = _id("dres"), db.now_ist()
    dataset_summary = _records(summaries["dataset_summary"])[0]
    executive = _records(actions["executive_action_summary"])[0]
    threshold = float(manifest["runtime_declarations"].get(
        "not_applicable_share_threshold", 0.8
    ))
    assessed = max(1, int(dataset_summary.get("rule_assessments") or 0))
    na_share = float(dataset_summary.get("not_applicable_cells") or 0) / assessed
    rollup = {
        **dataset_summary,
        "not_applicable_share": na_share,
        "not_applicable_share_threshold": threshold,
        "not_applicable_share_review": na_share > threshold,
        "overall_action": executive["overall_user_decision"],
        "ready_routes": len(manifest["coverage"]["ready_routes"]),
        "unscoped_routes": len(manifest["coverage"]["unscoped_routes"]),
    }
    metrics = {
        "result_kind": "value_semantics_run_summary", "rollup": rollup,
        "executive_action_summary": executive,
        "field_summary": _records(summaries["field_summary"]),
        "rule_summary": _records(summaries["rule_summary"]),
        "group_summary": _records(summaries["group_summary"]),
        "assessment_outcomes": _records(summaries["assessment_outcomes"]),
        "precedence_summary": _records(summaries["precedence_summary"]),
        "unexamined_fields": _records(summaries["unexamined_fields"]),
        "action_queue": _records(actions["action_queue"]),
        "expected_path_summary": _records(actions["expected_path_summary"]),
        "artifacts": {key: value.to_dict() for key, value in artifacts.items()},
        "methodology": METHODOLOGY,
    }
    if manifest.get("field_decisions_version"):
        metrics["field_scope_decisions"] = manifest_mod.field_scope_decisions(manifest)
    db.insert("diag_results", {
        "result_id": result_id, "run_id": manifest["run_id"],
        "diagnostic_id": DIAGNOSTIC_ID, "entity_or_table": manifest["table"],
        "decision_type": "classification_output", "verdict": None,
        "review_state": "open", "metrics_json": metrics,
        "thresholds_used_json": {
            "not_applicable_share_threshold": threshold,
            "variance_window": manifest["runtime_declarations"].get("variance_window"),
            "minimum_row_count": manifest["runtime_declarations"].get("minimum_row_count"),
        },
        "scope_counts_json": rollup, "na_reason": None, "created_at": now,
    })
    finding_count = 0
    for index, action in enumerate(_records(actions["rca_evidence"])):
        finding_count += 1
        db.insert("diag_findings", {
            "finding_id": _id("dfind"), "result_id": result_id,
            "run_id": manifest["run_id"],
            "rule_id": f"value_semantics:{action['input_variable']}:{action['reason_code']}",
            "kb_rule_id": action.get("evidence_reference"),
            "severity": "MATERIAL" if action["priority"] == "HIGH" else "ADVISORY",
            "outcome": "CANDIDATE", "violation_count": int(action["affected_cells"]),
            "rate": action.get("affected_share"), "tolerance": None,
            "exceptions_json": {"count": 0, "notes": []},
            "evidence_json": [{key: action.get(key) for key in (
                "input_variable", "matched_role", "tag", "reason_code",
                "affected_cells", "affected_rows", "sample_row_references",
                "recommended_next_step",
            )}],
            "pattern": action["tag"] or action["scope"],
            "pattern_detail": action["issue_statement"], "regulatory_ref": None,
            "rule_text": "A grouped Value Semantics anomaly requires human review before issue promotion.",
            "rule_type": "deterministic", "framework": None,
            "entity": action["input_variable"],
            "resolved_roles_json": {"field": action["input_variable"],
                                    "role": action["matched_role"]},
            "tables_used": manifest["table"],
            "scope_rows_evaluated": int(action["affected_rows"]),
            "scope_rows_skipped": 0, "na_reason": None,
            "review_state": "open", "seq": index, "created_at": now,
        })
    return result_id, finding_count, rollup


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    row = manifest_mod.get_run(run_id)
    if row["status"] == DONE:
        yield {"phase": "start", "agent": AGENT, "run_id": run_id,
               "thought": "Replaying a completed Value Semantics run."}
        yield {"phase": "done", "agent": AGENT, "run_id": run_id,
               "thought": "Run already complete."}
        return
    manifest = manifest_mod.freeze(run_id, actor) if row["status"] == DRAFT else row["manifest_json"]
    if row["status"] not in {DRAFT, RUNNING}:
        raise RuntimeError(f"run cannot execute from status {row['status']}")
    predicate = (manifest["runtime_declarations"].get("panel_scope_exclusions") or {})
    predicate_column = ((predicate.get("row_predicate") or {}).get("column")
                        if isinstance(predicate, dict) else None)
    columns = list(dict.fromkeys([
        *manifest["selected_fields"], *([predicate_column] if predicate_column else []),
    ]))
    yield {"phase": "start", "agent": AGENT, "run_id": run_id,
           "total": len(manifest["coverage"]["ready_routes"]),
           "thought": "Loading the frozen scope and applying confirmed semantic bindings."}
    frame = SnapshotLoader().load_table(manifest["item_id"], manifest["table"], columns=columns)
    row_reference = manifest["row_reference"]["column"]
    frame[row_reference] = _generated_row_references(manifest, len(frame))
    dictionary = _dictionary(manifest, list(frame.columns))
    bindings = dict(manifest["confirmed_role_bindings"])
    declarations = dict(manifest["runtime_declarations"])
    declarations["confirmed_role_bindings"] = bindings
    yield {"phase": "progress", "agent": AGENT, "run_id": run_id,
           "done": 0, "total": len(manifest["coverage"]["ready_routes"]),
           "thought": "Evaluating safe, identifier-mapped KB primitives; no free-form rules are executed."}
    result = execute_value_semantics(
        frame, dictionary, bindings, declarations, knowledge.resources()[0],
        row_reference_column=row_reference,
    )
    summary_frame = frame[manifest["selected_fields"]].copy()
    summary_dictionary = _dictionary(manifest, manifest["selected_fields"])
    summaries = summarize_value_semantics(
        summary_frame, summary_dictionary, bindings,
        knowledge.resources()[0], result,
    )
    actions = build_action_focused_summary(
        frame, bindings, result, summaries,
        binding_results=pd.DataFrame([
            {"column_name": field["column"], "decision": field["binding_source"]}
            for field in manifest["fields"] if field.get("selected")
        ]),
    )
    binding_artifact, _ = _save_bindings(manifest, actor)
    tag_frame = result.resolved_cell_tags.drop(columns=["input_value"], errors="ignore")
    ledger_frame = result.assessment_ledger.drop(columns=["input_value"], errors="ignore")
    tag_counts = {str(key): int(value) for key, value in tag_frame["tag"].value_counts().items()}
    tags_artifact, _ = _save_parquet(
        manifest, actor, "value_semantics_tags", tag_frame,
        binding_artifact.artifact_id, {"tag_counts": tag_counts},
    )
    ledger_artifact, _ = _save_parquet(
        manifest, actor, "value_semantics_assessment_ledger", ledger_frame,
        binding_artifact.artifact_id,
        {"unclassified_assessments": int(ledger_frame["status"].eq("UNCLASSIFIED").sum()),
         "unscoped_routes": int(ledger_frame["status"].eq("UNSCOPED").sum())},
    )
    result_id, findings, rollup = _persist_results(
        manifest, summaries, actions,
        {"bindings": binding_artifact, "tags": tags_artifact, "ledger": ledger_artifact},
    )
    # The report is part of D08's promised result package. Persist it and link
    # it to the compact result before publishing DONE, so a report/storage
    # failure cannot advertise an incomplete successful run.
    completion_at = db.now_ist()
    db.update("diag_runs", {"run_id": run_id, "status": RUNNING}, {
        "finished_at": completion_at,
    })
    report_artifact, _ = _save_report_artifact(run_id, actor)
    stored_result = db.query_one("diag_results", result_id=result_id)
    stored_metrics = dict(stored_result["metrics_json"])
    stored_metrics["artifacts"] = {
        **stored_metrics.get("artifacts", {}), "report": report_artifact.to_dict(),
    }
    db.update("diag_results", {"result_id": result_id}, {"metrics_json": stored_metrics})
    db.update("diag_runs", {"run_id": run_id}, {
        "status": DONE, "finished_at": completion_at,
    })
    yield {"phase": "done", "agent": AGENT, "run_id": run_id,
           "result_id": result_id, "rollup": rollup, "findings": findings,
           "issues": {"created": []},
           "thought": f"Value Semantics complete: {len(tag_frame)} tagged cells and {findings} review candidate(s)."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last: dict[str, Any] = {}
    for event in run(run_id, actor):
        last = event
    return last


def run_results(run_id: str) -> dict[str, Any]:
    from domains.test_lab.shared.results import run_results as shared
    return shared(run_id)


def _report_source(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    projected = run_results(run_id)
    summary_row = next(
        row for row in projected["results"]
        if (row.get("metrics_json") or {}).get("result_kind") == "value_semantics_run_summary"
    )
    metrics = summary_row["metrics_json"]
    findings = [finding for row in projected["results"] for finding in row.get("findings") or []]
    workflow = {"awaiting_review": 0, "promoted": 0, "dismissed": 0, "closed": 0}
    actions = []
    for finding in findings:
        issue = finding.get("existing_issue") or {}
        if issue.get("status") == "Closed":
            state = "closed"
        elif issue or finding.get("review_state") == "confirmed":
            state = "promoted"
        elif finding.get("review_state") == "dismissed":
            state = "dismissed"
        else:
            state = "awaiting_review"
        workflow[state] += 1
        actions.append({"finding_id": finding["finding_id"], "rule_id": finding["rule_id"],
                        "state": state, "issue_row_id": issue.get("issue_row_id"),
                        "dispositions": finding.get("dispositions") or []})
    manifest = projected["manifest"]
    source_ids = [
        value["artifact_id"] for value in metrics["artifacts"].values()
        if value.get("artifact_type") != "value_semantics_report"
    ]
    payload = {
        "artifact_kind": "value_semantics_report", "schema_version": 1,
        "run_id": run_id, "manifest_fingerprint": manifest["manifest_fingerprint"],
        "generated_at": projected["run"].get("finished_at") or db.now_ist(),
        "table": manifest["table"],
        "scope": {"item_id": manifest["item_id"], "table": manifest["table"],
                  "selected_fields": manifest["selected_fields"],
                  "context": manifest["context"]["selected"]},
        "summary": metrics["rollup"],
        "overall_action": metrics["executive_action_summary"]["overall_user_decision"],
        "executive_action_summary": metrics["executive_action_summary"],
        "field_summary": metrics["field_summary"], "rule_summary": metrics["rule_summary"],
        "action_queue": metrics["action_queue"], "finding_workflow": workflow,
        "finding_actions": actions, "coverage": manifest["coverage"],
        "knowledge": manifest["knowledge"],
        "inference_disclosure": inference_disclosure(run_id),
        "source_artifact_ids": source_ids,
        "methodology": METHODOLOGY,
        "limitations": [
            "The diagnostic assigns three treatment tags; it does not certify every untagged value as valid.",
            "PD, LGD, and EAD context is descriptive and does not independently activate rules.",
            "UNSCOPED routes identify missing bindings or declarations and are not passes.",
            "Reports contain aggregate evidence and masked references; full tag and assessment records remain in governed Parquet artifacts.",
        ],
    }
    if manifest.get("field_decisions_version"):
        payload["field_scope_decisions"] = manifest_mod.field_scope_decisions(manifest)
        payload["field_decisions_version"] = manifest["field_decisions_version"]
        payload["limitations"].append(
            "Field-level Not applicable is a human-confirmed scope decision, not a cell tag or a data-quality pass. "
            "Excluded fields have no confirmed not-applicable decision."
        )
    return payload, metrics


def _save_report_artifact(run_id: str, actor: str) -> tuple[Any, bool]:
    payload, metrics = _report_source(run_id)
    manifest = manifest_mod.get_run(run_id)["manifest_json"]
    report_state = stable_fingerprint({
        "finding_workflow": payload["finding_workflow"],
        "finding_actions": payload["finding_actions"],
    })
    args = _artifact_args(manifest, actor, "value_semantics_report")
    args["identity_inputs"] = {**(args["identity_inputs"] or {}),
                               "report_state": report_state,
                               "renderer_version": REPORT_RENDERER_VERSION,
                               "run_id": run_id}
    outcome = AnalysisArtifactRepository().save(
        payload, source_artifact_ids=tuple(payload["source_artifact_ids"]), **args,
    )
    return outcome.artifact, outcome.outcome == "reused"


def report_payload(run_id: str) -> tuple[dict[str, Any], Any, bool]:
    payload, _metrics = _report_source(run_id)
    artifact, reused = _save_report_artifact(run_id, "system")
    return payload, artifact, reused


def _report_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "VALUE SEMANTICS ANALYSIS", "",
        f"Run: {payload['run_id']}", f"Table: {payload['table']}",
        f"Context: {', '.join(payload['scope']['context'])}", "",
        "1. INTENDED USE",
        "Classifies cells as CENSORED, STALE_FROZEN, or NOT_APPLICABLE using confirmed roles and declared rule prerequisites.",
        "Untagged does not mean universally valid; UNSCOPED does not mean pass.", "",
        "2. OUTCOME",
        f"Overall action: {payload['overall_action']}",
        f"Tagged cells: {summary.get('distinct_tagged_cells', 0)}",
        f"Censored: {summary.get('censored_cells', 0)}",
        f"Stale/frozen: {summary.get('stale_frozen_cells', 0)}",
        f"Not applicable: {summary.get('not_applicable_cells', 0)}",
        f"Ready / unscoped routes: {summary.get('ready_routes', 0)} / {summary.get('unscoped_routes', 0)}", "",
        "3. FIELD SUMMARY",
    ]
    for row in payload["field_summary"]:
        lines.append(
            f"- {row['input_variable']}: {row['examination_status']}; "
            f"tagged={row['tagged_cells']}; censored={row['censored_count']}; "
            f"stale/frozen={row['stale_frozen_count']}; not-applicable={row['not_applicable_count']}"
        )
    if "field_scope_decisions" in payload:
        lines.extend(["", "FIELD SCOPE AND APPLICABILITY DECISIONS",
                      "Not applicable below is a human scope decision, not the cell-level NOT_APPLICABLE tag.",
                      "Excluded means outside this run's scope, without a confirmed not-applicable decision."])
        for field in payload["field_scope_decisions"]:
            lines.append(f"- {field['column']}: {field['status'].replace('_', ' ').title()}")
            if field.get("reason"):
                lines.append(f"  Reason: {field['reason']}")
            if field.get("confirmed_roles"):
                lines.append(f"  Confirmed roles: {', '.join(field['confirmed_roles'])}")
            if field.get("confirmed_by"):
                lines.append(f"  Confirmed by: {field['confirmed_by']} at {field.get('confirmed_at') or 'not recorded'}")
    lines.extend(["", "4. ACTION QUEUE"])
    if payload["action_queue"]:
        for row in payload["action_queue"]:
            lines.append(f"- [{row['priority']}] {row['input_variable']} — {row['user_decision']}: {row['recommended_next_step']}")
    else:
        lines.append("- No action item was generated within the assessed scope.")
    lines.extend(["", "5. FINDING WORKFLOW"])
    lines.extend(f"- {key}: {value}" for key, value in payload["finding_workflow"].items())
    lines.extend(["", "6. KNOWLEDGE AND AI", f"- Value Semantics KB: {payload['knowledge']['value_semantics_version']}",
                  f"- Terminology dictionary: {payload['knowledge']['terminology_version']}",
                  f"- {payload['inference_disclosure']['statement']}", "",
                  "7. LIMITATIONS"])
    lines.extend(f"- {value}" for value in payload["limitations"])
    lines.extend(["", "8. AAR LINEAGE"])
    lines.extend(f"- {value}" for value in payload["source_artifact_ids"])
    return "\n".join(lines)


def report_text(run_id: str) -> str:
    payload, _artifact, _reused = report_payload(run_id)
    return _report_text(payload)


def _render_pdf(payload: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError:  # pragma: no cover
        return _report_text(payload).encode("utf-8")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Value Semantics Analysis", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    for line in _report_text(payload).splitlines()[1:]:
        safe = line.encode("latin-1", "replace").decode("latin-1")
        if line and line[0].isdigit() and ". " in line[:4]:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 5, safe, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 8.5)
        else:
            pdf.multi_cell(0, 4.2, safe or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    payload, artifact, reused = report_payload(run_id)
    return _render_pdf(payload), {
        "run_id": run_id,
        "report_artifact": {"artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type,
                            "payload_hash": artifact.payload_hash, "reused": reused},
        "filename": f"value-semantics-{run_id}.pdf",
    }


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]


__all__ = ["execute_now", "report_document", "report_payload", "report_pdf",
           "report_text", "run", "run_results"]
