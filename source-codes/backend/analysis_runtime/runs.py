"""Persistent lifecycle for supporting-analysis manifests and review observations."""
from __future__ import annotations

import uuid
from typing import Any

import system_db as db
from ai.v2 import service as item_service
from .capabilities import get_capability
from .contracts import FrozenAnalysisManifest


TERMINAL_STATUSES = {"complete", "partial", "action_required", "not_applicable", "failed"}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_manifest(snapshot_id: str, capability_id: str, context: dict[str, Any],
                    actor: str = "system") -> dict[str, Any]:
    capability = get_capability(capability_id)
    readiness = capability.readiness(snapshot_id, context)
    status = readiness.get("status")
    if status == "action_required":
        raise ValueError(readiness.get("reason") or "Analysis inputs require attention.")
    if status not in {"ready", "not_applicable"}:
        raise ValueError(f"Unsupported readiness status: {status!r}")
    now = db.now_ist()
    run_id = _id("anrun")
    if status == "ready":
        manifest = capability.build_manifest(snapshot_id, context)
        manifest_json = manifest.to_dict()
        fingerprint = manifest.fingerprint
        run_status = "draft"
    else:
        # Preserve the attempted scope and honest N/A reason without pretending
        # a runnable frozen manifest exists.
        manifest_json = {"capability_id": capability_id, "snapshot_id": snapshot_id,
                         "requested_context": context}
        from .contracts import stable_fingerprint
        fingerprint = stable_fingerprint(manifest_json)
        run_status = "not_applicable"
    db.insert("analysis_manifests", {
        "run_id": run_id, "capability_id": capability_id,
        "capability_version": capability.version, "snapshot_id": snapshot_id,
        "manifest_json": manifest_json, "manifest_fingerprint": fingerprint,
        "readiness_json": readiness, "status": run_status,
        "result_json": None, "artifact_ids_json": [], "error_message": None,
        "created_by": actor, "created_at": now, "updated_at": now,
        "started_at": None, "finished_at": now if run_status == "not_applicable" else None,
    })
    return result(run_id)


def get_run(run_id: str) -> dict[str, Any]:
    row = db.query_one("analysis_manifests", run_id=run_id)
    if row is None:
        raise KeyError(f"Unknown supporting-analysis run: {run_id}")
    return row


def execute(run_id: str, actor: str = "system", emit=None) -> dict[str, Any]:
    row = get_run(run_id)
    if row["status"] in TERMINAL_STATUSES:
        return result(run_id)
    if row["status"] != "draft":
        raise ValueError(f"Run {run_id!r} cannot start from status {row['status']!r}")
    capability = get_capability(row["capability_id"])
    manifest = FrozenAnalysisManifest.from_dict(row["manifest_json"])
    now = db.now_ist()
    db.update("analysis_manifests", {"run_id": run_id}, {
        "status": "running", "started_at": now, "updated_at": now,
    })
    try:
        outcome = capability.execute(manifest, {"run_id": run_id, "actor": actor}, emit=emit)
        stored = outcome.to_dict()
        observations = []
        for sequence, payload in enumerate(outcome.observations, start=1):
            observation_id = f"obs_{uuid.uuid5(uuid.NAMESPACE_URL, f'{run_id}:{sequence}:{payload.get("column", "")}').hex[:12]}"
            review_state = payload.get("review_state") or "open"
            db.insert("analysis_observations", {
                "observation_id": observation_id, "run_id": run_id,
                "artifact_id": outcome.artifact.artifact_id,
                "snapshot_id": row["snapshot_id"], "capability_id": row["capability_id"],
                "feature": payload.get("column"),
                "classification": payload.get("classification"),
                "review_state": review_state, "payload_json": payload,
                "issue_row_id": None, "created_at": db.now_ist(), "updated_at": db.now_ist(),
            })
            observations.append({**payload, "observation_id": observation_id,
                                 "review_state": review_state})
        stored["observations"] = observations
        finished = db.now_ist()
        db.update("analysis_manifests", {"run_id": run_id}, {
            "status": outcome.status, "result_json": stored,
            "artifact_ids_json": [outcome.artifact.artifact_id],
            "updated_at": finished, "finished_at": finished,
        })
    except Exception as exc:
        finished = db.now_ist()
        db.update("analysis_manifests", {"run_id": run_id}, {
            "status": "failed", "error_message": str(exc),
            "updated_at": finished, "finished_at": finished,
        })
        raise
    return result(run_id)


def result(run_id: str) -> dict[str, Any]:
    row = get_run(run_id)
    observations = db.query("analysis_observations", run_id=run_id, order_by="created_at")
    live = [{**(item.get("payload_json") or {}),
             "observation_id": item["observation_id"],
             "review_state": item["review_state"],
             "issue_row_id": item.get("issue_row_id")}
            for item in observations]
    stored = dict(row.get("result_json") or {})
    if live:
        stored["observations"] = live
    return {
        "run": {key: row.get(key) for key in (
            "run_id", "capability_id", "capability_version", "snapshot_id", "status",
            "error_message", "created_by", "created_at", "started_at", "finished_at",
        )},
        "readiness": row.get("readiness_json"),
        "manifest": row.get("manifest_json"),
        "result": stored or None,
    }


def list_results(snapshot_id: str, capability_id: str | None = None) -> list[dict[str, Any]]:
    filters = {"snapshot_id": snapshot_id}
    if capability_id:
        filters["capability_id"] = capability_id
    rows = db.query("analysis_manifests", order_by="created_at DESC", **filters)
    return [result(row["run_id"]) for row in rows]


def dispose_observation(observation_id: str, action: str, reason: str | None = None,
                        actor: str = "system") -> dict[str, Any]:
    if action not in {"confirm_issue", "dismiss"}:
        raise ValueError("action must be confirm_issue or dismiss")
    reason = (reason or "").strip() or None
    if action == "dismiss" and reason is None:
        raise ValueError("a reason is required to dismiss an observation")
    observation = db.query_one("analysis_observations", observation_id=observation_id)
    if observation is None:
        raise KeyError(f"Unknown supporting-analysis observation: {observation_id}")
    if observation["review_state"] == "not_required":
        raise ValueError("An within-tolerance observation does not require disposition")
    issue_row_id = observation.get("issue_row_id")
    review_state = "confirmed" if action == "confirm_issue" else "dismissed"
    if action == "confirm_issue":
        issue_row_id = _ensure_observation_issue(observation, issue_row_id, actor)
    db.insert("analysis_observation_dispositions", {
        "observation_id": observation_id, "action": action, "reason": reason,
        "actor": actor, "ts": db.now_ist(),
    })
    db.update("analysis_observations", {"observation_id": observation_id}, {
        "review_state": review_state, "issue_row_id": issue_row_id,
        "updated_at": db.now_ist(),
    })
    return {"observation_id": observation_id, "action": action,
            "review_state": review_state, "issue_row_id": issue_row_id,
            "reason": reason}


def _ensure_observation_issue(observation: dict[str, Any], issue_row_id: str | None,
                              actor: str = "system") -> str:
    """Create (or restore) the Issue Management row for a confirmed observation."""
    if issue_row_id and db.query_one("issues_v2", issue_row_id=issue_row_id):
        return issue_row_id

    issue_row_id = issue_row_id or item_service._id("iss")
    payload = observation.get("payload_json") or {}
    run = get_run(observation["run_id"])
    manifest = run.get("manifest_json") or {}
    methodology = manifest.get("methodology") or {}
    parameters = (methodology.get("resolved_parameters")
                  or methodology.get("parameters") or {})
    scope = manifest.get("scope") or {}
    threshold = {
        "missing_share": payload.get("missing_share"),
        "tolerance": payload.get("tolerance"),
        "tree_depth": parameters.get("tree_depth"),
    }
    if parameters.get("period_column"):
        threshold["period_column"] = parameters["period_column"]
    now = db.now_ist()
    db.insert("issues_v2", {
        "issue_row_id": issue_row_id, "item_id": observation["snapshot_id"],
        "table_name": scope.get("table", ""),
        "test_name": "Missingness Mechanism review",
        "area_id": "L2-05",
        "criticality": "High" if payload.get("role") in {"identifier", "target", "mandatory"} else "Medium",
        "columns_json": [payload.get("column")],
        "violation_count": payload.get("missing_count") or 0,
        "threshold_json": threshold,
        "column_details_json": [{
            "columns": [payload.get("column")],
            "metric": payload.get("missing_share"),
            "threshold": threshold,
            "violation_count": payload.get("missing_count") or 0,
            "supporting_observation_id": observation["observation_id"],
            "analysis_run_id": observation["run_id"],
            "artifact_id": observation.get("artifact_id"),
            "analysis_parameters": parameters,
            "classification": payload.get("classification"),
            "rationale": payload.get("rationale"),
            "recommended_action": payload.get("recommended_action"),
        }],
        "metric": payload.get("missing_share"), "status": "Open",
        "workflow_version": "rca", "run_id": observation["run_id"],
        "finding_id": observation["observation_id"],
        # NULL diagnostic_id keeps this outside the nine-row register.
        "diagnostic_id": None,
        "rule_id": f"supporting:missingness:{payload.get('column')}",
        "created_at": now, "updated_at": now,
    })
    try:
        import taxonomy
        import tenancy
        taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "dq_item",
                              observation["snapshot_id"], "issues_v2",
                              issue_row_id, actor)
    except (KeyError, ValueError):
        # A valid issue must not be lost merely because an old/local item has
        # no governed tag assignments to inherit.
        pass
    return issue_row_id


def repair_confirmed_observation_issues(actor: str = "system:migration-repair") -> dict[str, int]:
    """Restore issue rows orphaned by the former repeat-on-boot retirement bug."""
    repaired = 0
    for observation in db.query("analysis_observations", review_state="confirmed"):
        issue_row_id = observation.get("issue_row_id")
        if issue_row_id and db.query_one("issues_v2", issue_row_id=issue_row_id):
            continue
        restored_id = _ensure_observation_issue(observation, issue_row_id, actor)
        db.update("analysis_observations", {"observation_id": observation["observation_id"]}, {
            "issue_row_id": restored_id, "updated_at": db.now_ist(),
        })
        repaired += 1
    return {"repaired": repaired}
