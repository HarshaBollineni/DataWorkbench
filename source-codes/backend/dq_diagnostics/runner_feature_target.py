"""Execution, persistence, and reviewed issue hand-off for diagnostic #2."""
from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any, Generator

import system_db as db
from ai.v2 import service as item_service
from analysis_runtime.artifacts import AnalysisArtifactRepository

from .engines.feature_target_separation import assess_snapshot
from .manifest import DONE, FAILED, RUNNING
from . import manifest_feature_target as manifest_mod

DIAGNOSTIC_ID = 2
AGENT = "feature_target_separation_engine"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _threshold_values(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: spec["value"] for key, spec in manifest["thresholds"].items()}


def _threshold_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {key: spec["source"] for key, spec in manifest["thresholds"].items()}


def _artifact_map(outcome: Any) -> dict[str, dict[str, str]]:
    repo = AnalysisArtifactRepository()
    mapped: dict[str, dict[str, str]] = {}
    for artifact_type, ids in outcome.artifact_ids.items():
        for artifact_id in ids:
            metadata = repo.get_metadata(artifact_id)
            if metadata.feature:
                mapped.setdefault(metadata.feature, {})[artifact_type] = artifact_id
    return mapped


def _candidate_text(candidate: dict[str, Any]) -> str:
    feature = candidate["feature"]
    if candidate["candidate_type"] == "target_leakage":
        return f"{feature} exceeds one or more target-leakage review thresholds."
    return f"{feature} falls below one or more discrimination review thresholds."


def persist(manifest: dict[str, Any], outcome: Any) -> dict[str, Any]:
    run_id = manifest["run_id"]
    now = db.now_ist()
    artifacts = _artifact_map(outcome)
    candidates = {row["feature"]: row for row in outcome.candidate_findings}
    results_by_feature = {}
    category_counts = {"suspicious": 0, "strong": 0, "medium": 0, "weak": 0}
    candidate_counts = {"target_leakage": 0, "poor_discrimination": 0}

    for feature_result in outcome.response.results:
        feature = feature_result.feature
        category_counts[feature_result.category] += 1
        candidate = candidates.get(feature)
        if candidate:
            candidate_counts[candidate["candidate_type"]] += 1
        result_id = _id("dres")
        results_by_feature[feature] = result_id
        roc = feature_result.roc
        binning = feature_result.binning
        db.insert("diag_results", {
            "result_id": result_id, "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID,
            "entity_or_table": feature, "decision_type": "candidate_flag",
            "verdict": None, "review_state": "open",
            "metrics_json": {
                "result_kind": "feature",
                "analysis_fingerprint": manifest_mod.analysis_fingerprint(manifest, feature),
                "feature": feature,
                "target": outcome.response.target.target,
                "target_type": outcome.response.target.target_type,
                "auc": feature_result.auc,
                "gini": roc.primary_gini if roc else None,
                "iv": feature_result.iv,
                "category": feature_result.category,
                "leakage_status": feature_result.leakage_status,
                "candidate": candidate,
                "rows_evaluated": (roc.rows_evaluated if roc else
                                   binning.rows_evaluated if binning else None),
                "missing_rows": roc.generic_missing_rows if roc else None,
                "errors": list(feature_result.errors),
                "warnings": list(binning.warnings) if binning else [],
                "coarse_bins": ([row.model_dump(mode="json") for row in binning.coarse_bins]
                                if binning else []),
                "localized_candidates": [
                    row.model_dump(mode="json")
                    for row in (outcome.response.localized_leakage_scan.candidates
                                if outcome.response.localized_leakage_scan else [])
                    if row.feature == feature
                ],
                "artifact_ids": artifacts.get(feature, {}),
            },
            "thresholds_used_json": {
                "values": _threshold_values(manifest),
                "sources": _threshold_sources(manifest),
            },
            "scope_counts_json": {
                "rows_evaluated": (roc.rows_evaluated if roc else
                                   binning.rows_evaluated if binning else 0),
                "metrics_available": int(feature_result.auc is not None) + int(feature_result.iv is not None),
            },
            "na_reason": None, "created_at": now,
        })

    finding_ids = []
    for seq, candidate in enumerate(outcome.candidate_findings):
        finding_id = _id("dfind")
        finding_ids.append(finding_id)
        thresholds = candidate["thresholds"]
        db.insert("diag_findings", {
            "finding_id": finding_id,
            "result_id": results_by_feature[candidate["feature"]],
            "run_id": run_id,
            "rule_id": f"feature_target:{candidate['feature']}:{candidate['candidate_type']}",
            "kb_rule_id": None,
            "severity": "HIGH" if candidate["candidate_type"] == "target_leakage" else "MATERIAL",
            "outcome": "CANDIDATE",
            "violation_count": 0,
            "rate": candidate.get("auc"),
            "tolerance": (thresholds["leakage_auc"] if candidate["candidate_type"] == "target_leakage"
                          else thresholds["poor_auc"]),
            "exceptions_json": {"count": 0, "notes": []},
            "evidence_json": [{
                "feature": candidate["feature"], "auc": candidate.get("auc"),
                "gini": candidate.get("gini"), "iv": candidate.get("iv"),
                "category": candidate.get("category"),
                "candidate_type": candidate["candidate_type"],
                "reasons": ", ".join(candidate["candidate_reasons"]),
            }],
            "pattern": candidate["candidate_type"],
            "pattern_detail": ", ".join(candidate["candidate_reasons"]),
            "regulatory_ref": None,
            "rule_text": _candidate_text(candidate),
            "rule_type": "statistical",
            "framework": None,
            "entity": candidate["feature"],
            "resolved_roles_json": {
                "target": outcome.response.target.target,
                "feature": candidate["feature"],
            },
            "tables_used": manifest["scope"]["table"],
            "scope_rows_evaluated": outcome.response.target.evaluated_rows,
            "scope_rows_skipped": outcome.response.target.dropped_target_rows,
            "na_reason": None,
            "review_state": "open", "seq": seq, "created_at": now,
        })

    summary_id = _id("dres")
    rollup = {
        "features": len(outcome.response.results),
        "candidates": len(finding_ids),
        "target_leakage": candidate_counts["target_leakage"],
        "poor_discrimination": candidate_counts["poor_discrimination"],
        "partial_features": sum(1 for row in outcome.response.results if row.errors),
        "categories": category_counts,
    }
    db.insert("diag_results", {
        "result_id": summary_id, "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID,
        "entity_or_table": manifest["scope"]["table"],
        "decision_type": "candidate_flag", "verdict": None, "review_state": "open",
        "metrics_json": {
            "result_kind": "run_summary", "status": outcome.response.status,
            "target": outcome.response.target.model_dump(mode="json"),
            "rollup": rollup,
            "methodology": outcome.response.methodology,
            "artifact_ids": outcome.artifact_ids,
        },
        "thresholds_used_json": {
            "values": _threshold_values(manifest), "sources": _threshold_sources(manifest),
        },
        "scope_counts_json": {
            "features_selected": len(manifest["scope"]["selected_features"]),
            "features_completed": len(outcome.response.results),
        },
        "na_reason": None, "created_at": now,
    })
    return {"result_id": summary_id, "findings": len(finding_ids), "rollup": rollup,
            "status": outcome.response.status}


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    run_row = manifest_mod._run(run_id)
    if run_row["status"] == DONE:
        results = db.query("diag_results", run_id=run_id)
        summary = next((row for row in results
                        if (row.get("metrics_json") or {}).get("result_kind") == "run_summary"), None)
        rollup = (summary.get("metrics_json") or {}).get("rollup", {}) if summary else {}
        yield {"phase": "start", "agent": AGENT, "run_id": run_id,
               "thought": "Replaying a completed run."}
        yield {"phase": "done", "agent": AGENT, "run_id": run_id,
               "result_id": summary.get("result_id") if summary else None,
               "rollup": rollup, "thought": "Run already complete."}
        return
    if run_row["status"] == manifest_mod.DRAFT:
        # Re-read the persisted Step 3 inventory immediately before freezing
        # the workflow. The scope gate may have been opened before the final
        # schema-role save completed; saved roles are authoritative for the
        # executable feature set.
        manifest_mod.refresh_draft_scope(run_id, actor)
        manifest = manifest_mod.freeze(run_id, actor)
    elif run_row["status"] == RUNNING:
        manifest = run_row["manifest_json"]
    else:
        raise RuntimeError(f"run {run_id} cannot execute from status {run_row['status']}")

    selected = manifest["scope"].get("execution_features") or manifest["scope"]["selected_features"]
    total = len(selected) * 2
    yield {"phase": "start", "agent": AGENT, "run_id": run_id, "total": total,
           "thought": f"Assessing {len(selected)} feature(s) against confirmed target "
                      f"{manifest['target']['column']}."}

    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def progress(done: int, count: int, feature: str, stage: str) -> None:
        events.put(("progress", {"done": done, "total": count, "feature": feature, "stage": stage}))

    def feature_preview(preview: dict[str, Any]) -> None:
        events.put(("feature_result", preview))

    def worker() -> None:
        try:
            values = _threshold_values(manifest)
            separation_keys = set(manifest_mod.SeparationThresholds.model_fields)
            outcome = assess_snapshot(
                manifest["item_id"], table=manifest["scope"]["table"],
                feature_columns=selected,
                target_type=manifest["target"]["target_type"],
                positive_class=manifest["target"].get("positive_class"),
                missing_target_action=manifest["target"]["missing_target_action"],
                percentile_bins=manifest["parameters"]["percentile_bins"],
                analysis_constraints=manifest["parameters"]["analysis_constraints"],
                binning_constraints=manifest["parameters"]["binning_constraints"],
                separation_thresholds={key: values[key] for key in separation_keys},
                finding_thresholds={key: values[key] for key in manifest_mod.FINDING_DEFAULTS},
                actor=actor, run_id=run_id, progress_callback=progress,
                feature_preview_callback=feature_preview,
            )
            events.put(("outcome", outcome))
        except Exception as exc:  # carried internally; router sanitizes it
            events.put(("error", exc))

    thread = threading.Thread(target=worker, name=f"diagnostic-2-{run_id}", daemon=True)
    thread.start()
    outcome = None
    while outcome is None:
        kind, payload = events.get()
        if kind == "progress":
            stage_label = "ROC/AUC/Gini" if payload["stage"] == "roc" else "IV/WOE binning"
            yield {"phase": "progress", "agent": AGENT, **payload,
                   "thought": f"[{payload['done']}/{payload['total']}] {payload['feature']} - {stage_label}"}
        elif kind == "feature_result":
            stage = payload.get("stage")
            yield {"phase": "feature_result", "agent": AGENT, "run_id": run_id,
                   "preview": payload,
                   "thought": (f"{payload['feature']} ROC complete; IV pending."
                               if stage == "roc" else
                               f"{payload['feature']} completed; preliminary results are ready.")}
        elif kind == "error":
            raise payload
        else:
            outcome = payload
    thread.join()

    if outcome.response.status in {"invalid", "action_required"}:
        messages = "; ".join(message.message for message in outcome.response.target.messages)
        raise ValueError(messages or "target configuration requires review")
    summary = persist(manifest, outcome)
    db.update("diag_runs", {"run_id": run_id}, {"status": DONE, "finished_at": db.now_ist()})
    yield {"phase": "done", "agent": AGENT, "run_id": run_id,
           "result_id": summary["result_id"], "rollup": summary["rollup"],
           "findings": summary["findings"], "issues": {"created": []},
           "thought": f"Run complete - {summary['rollup']['features']} feature(s), "
                      f"{summary['findings']} candidate(s) for review."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last = {}
    for event in run(run_id, actor):
        last = event
    return last


def run_results(run_id: str) -> dict[str, Any]:
    """Use the shared persisted-result projection, then router-level #2 hydration."""
    from .runner_cross_field import run_results as shared
    return shared(run_id)


def report_text(run_id: str) -> str:
    from .runner_cross_field import report_text as shared
    return shared(run_id)


def report_pdf(run_id: str) -> bytes:
    from .runner_cross_field import report_pdf as shared
    return shared(run_id)


class CandidateIssueConflict(RuntimeError):
    def __init__(self, issue_row_id: str):
        self.issue_row_id = issue_row_id
        super().__init__(f"Feature already has promoted issue {issue_row_id}; explicit overwrite is required.")


def _feature_issue(item_id: str, table_name: str, feature: str) -> dict[str, Any] | None:
    rows = db.execute(
        "SELECT * FROM issues_v2 WHERE item_id=? AND diagnostic_id=? AND table_name=? "
        "AND COALESCE(status, '') <> 'Superseded' ORDER BY created_at, issue_row_id",
        [item_id, DIAGNOSTIC_ID, table_name],
    )
    matches = [row for row in rows if feature in (row.get("columns_json") or [])]
    if not matches:
        return None
    with_rca = [row for row in matches if db.query_one("rca_cases", issue_row_id=row["issue_row_id"])]
    canonical = (with_rca or matches)[0]
    for duplicate in matches:
        if duplicate["issue_row_id"] != canonical["issue_row_id"]:
            db.update("issues_v2", {"issue_row_id": duplicate["issue_row_id"]}, {
                "status": "Superseded", "superseded_by_issue_row_id": canonical["issue_row_id"],
                "updated_at": db.now_ist(),
            })
    return canonical


def existing_candidate_issue(finding: dict[str, Any]) -> dict[str, Any] | None:
    feature = finding.get("entity")
    if not feature:
        return None
    return _feature_issue(finding["item_id"], finding.get("tables_used") or "", feature)


def ensure_candidate_issue(finding_id: str, actor: str = "system",
                           *, overwrite: bool = False) -> str:
    """Create exactly one Issue Management row after explicit confirmation."""
    rows = db.execute(
        "SELECT f.*, r.metrics_json, dr.item_id, dr.diagnostic_id "
        "FROM diag_findings f JOIN diag_results r ON r.result_id=f.result_id "
        "JOIN diag_runs dr ON dr.run_id=f.run_id WHERE f.finding_id=?",
        [finding_id],
    )
    if not rows:
        raise KeyError(f"Unknown finding: {finding_id}")
    finding = rows[0]
    if finding["diagnostic_id"] != DIAGNOSTIC_ID:
        raise ValueError("finding does not belong to diagnostic #2")
    metrics = finding.get("metrics_json") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    candidate = metrics.get("candidate") or {
        "candidate_type": "operator_override", "candidate_reasons": [finding.get("pattern_detail")],
        "auc": metrics.get("auc"), "gini": metrics.get("gini"), "iv": metrics.get("iv"),
        "category": metrics.get("category"),
        "thresholds": (finding.get("thresholds_used_json") or {}).get("values") or {},
    }
    issue_row_id = item_service._id("iss")
    now = db.now_ist()
    try:
        import tenancy
        enabled = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_ENABLED")
        retired = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_LEGACY_CREATION_RETIRED")
        workflow_version = "rca" if (enabled or retired) else "legacy-v1"
    except (KeyError, ValueError):
        workflow_version = "rca"
    feature = metrics.get("feature") or finding.get("entity")
    thresholds = candidate.get("thresholds") or {}
    issue_values = {
        "issue_row_id": issue_row_id, "item_id": finding["item_id"],
        "table_name": finding.get("tables_used") or "",
        "test_name": "Single-feature target separation review",
        "area_id": "T1",
        "criticality": "High" if candidate.get("candidate_type") == "target_leakage" else "Medium",
        "columns_json": [feature], "violation_count": 0,
        "threshold_json": thresholds,
        "column_details_json": [{
            "columns": [feature], "metric": candidate.get("auc"),
            "auc": candidate.get("auc"), "gini": candidate.get("gini"),
            "iv": candidate.get("iv"), "category": candidate.get("category"),
            "candidate_type": candidate.get("candidate_type"),
            "candidate_reasons": candidate.get("candidate_reasons") or [],
            "artifact_ids": metrics.get("artifact_ids") or {},
            "target": metrics.get("target"),
        }],
        "metric": candidate.get("auc"), "status": "Open",
        "workflow_version": workflow_version, "run_id": finding["run_id"],
        "finding_id": finding_id, "diagnostic_id": DIAGNOSTIC_ID,
        "rule_id": finding["rule_id"], "created_at": now, "updated_at": now,
    }
    existing = _feature_issue(finding["item_id"], issue_values["table_name"], feature)
    if existing:
        if existing.get("finding_id") == finding_id:
            return existing["issue_row_id"]
        if not overwrite:
            raise CandidateIssueConflict(existing["issue_row_id"])
        issue_row_id = existing["issue_row_id"]
        issue_values.pop("issue_row_id")
        issue_values.pop("created_at")
        issue_values.pop("status")
        db.update("issues_v2", {"issue_row_id": issue_row_id}, issue_values)
        return issue_row_id
    db.insert("issues_v2", issue_values)
    try:
        import taxonomy
        import tenancy
        taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "dq_item", finding["item_id"],
                              "issues_v2", issue_row_id, actor)
    except (KeyError, ValueError):
        pass
    return issue_row_id


def ensure_candidate_finding(result_id: str, reason: str | None = None,
                             actor: str = "system") -> str:
    """Create candidate evidence for an explicit operator override on any assessed feature."""
    rows = db.execute("SELECT r.*, dr.item_id, dr.manifest_json FROM diag_results r "
                      "JOIN diag_runs dr ON dr.run_id=r.run_id WHERE r.result_id=? AND dr.diagnostic_id=?",
                      [result_id, DIAGNOSTIC_ID])
    if not rows: raise KeyError(f"Unknown single-feature result: {result_id}")
    result = rows[0]; metrics = result.get("metrics_json") or {}
    if isinstance(metrics, str): metrics = json.loads(metrics)
    if metrics.get("result_kind") != "feature": raise ValueError("only assessed feature results can be promoted")
    existing = db.query_one("diag_findings", result_id=result_id)
    if existing: return existing["finding_id"]
    rationale = (reason or "").strip()
    if not rationale: raise ValueError("a rationale is required to override the product recommendation")
    manifest = result.get("manifest_json") or {}
    if isinstance(manifest, str): manifest = json.loads(manifest)
    threshold_payload = result.get("thresholds_used_json") or {}
    if isinstance(threshold_payload, str): threshold_payload = json.loads(threshold_payload)
    thresholds = threshold_payload.get("values") or {}
    finding_id = _id("dfind")
    db.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id,
        "run_id": result["run_id"], "rule_id": f"feature_target:{metrics.get('feature')}:operator_override",
        "kb_rule_id": None, "severity": "MATERIAL", "outcome": "CANDIDATE", "violation_count": 0,
        "rate": metrics.get("auc"), "tolerance": thresholds.get("leakage_auc"),
        "exceptions_json": {"count": 0, "notes": []}, "evidence_json": [{
            "feature": metrics.get("feature"), "auc": metrics.get("auc"), "gini": metrics.get("gini"),
            "iv": metrics.get("iv"), "category": metrics.get("category"),
            "candidate_type": "operator_override", "operator_override": True,
            "override_rationale": rationale}], "pattern": "operator_override",
        "pattern_detail": rationale, "regulatory_ref": None,
        "rule_text": "Operator promoted feature evidence despite no automatic candidate recommendation.",
        "rule_type": "statistical", "framework": None, "entity": metrics.get("feature"),
        "resolved_roles_json": {"target": metrics.get("target"), "feature": metrics.get("feature")},
        "tables_used": (manifest.get("scope") or {}).get("table") or result.get("entity_or_table"),
        "scope_rows_evaluated": metrics.get("rows_evaluated") or 0, "scope_rows_skipped": 0,
        "na_reason": None, "review_state": "open", "seq": 0, "created_at": db.now_ist()})
    return finding_id
