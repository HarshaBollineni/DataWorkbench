"""Durable, fenced DSC-v1 materialization queue."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from .dataset_structure_producer import DatasetStructureObservationError, SUPPORTED_OBSERVER_PREDICATES, observe_dataset_structure
from .repository import AnalysisArtifactRepository

LEASE_SECONDS, HEARTBEAT_SECONDS, JOB_DEADLINE_SECONDS, MAX_ATTEMPTS = 90, 30, 600, 3
RETRY_DELAYS_SECONDS = (30, 300, 1800)
WORKER_OWNER = f"dsc-worker-{os.getpid()}-{uuid.uuid4().hex[:10]}"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    try:
        result["progress"] = json.loads(result.pop("progress_json") or "{}")
    except (TypeError, ValueError):
        result["progress"] = {}
    return result


def begin_profile_publication_attempt(snapshot_id: str) -> str:
    """Fence an old worker before the first refreshed profile replacement."""
    token = f"dscpa_{uuid.uuid4().hex}"
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE dq_items SET profile_publication_attempt_token=? WHERE item_id=?", (token, snapshot_id))
        conn.commit()
    return token


def _profile_fingerprint(snapshot_id: str, asset_id: str, *, conn: Any = None) -> str | None:
    """Exact active/verified profile+inventory set; partial sets never qualify."""
    inventory = db.query("variable_inventory", conn=conn, item_id=snapshot_id, order_by="table_name, column_name")
    tables = db.query("dq_item_tables", conn=conn, item_id=snapshot_id, order_by="table_name")
    expected = {(r["table_name"], r["column_name"]) for r in inventory}
    table_names = {r["table_name"] for r in tables}
    if not expected or not table_names or {t for t, _ in expected} != table_names:
        return None
    repo = AnalysisArtifactRepository(); pins: list[dict[str, Any]] = []
    def all_metadata(kind: str) -> list[Any]:
        rows = db.query("analysis_artifacts", conn=conn, snapshot_id=snapshot_id,
                        artifact_type=kind, status="active")
        return [repo._metadata(row) for row in rows if row.get("asset_id") == asset_id]

    def verify(metadata: Any) -> bool:
        """Read-only integrity check suitable while the scheduling UoW holds a lock."""
        try:
            payload = repo._payload_path(metadata).read_bytes()
            if hashlib.sha256(payload).hexdigest() != metadata.payload_hash:
                return False
            if metadata.payload_media_type == "application/json":
                json.loads(payload)
            return True
        except Exception:
            return False

    def source_pins(metadata: Any) -> list[dict[str, str]]:
        details = []
        for ref in metadata.source_artifacts:
            source_row = db.query_one("analysis_artifacts", conn=conn, artifact_id=ref["artifact_id"])
            if source_row is None:
                raise ValueError("profile source artifact is missing")
            source = repo._metadata(source_row)
            details.append({"role": ref["role"], "artifact_id": source.artifact_id,
                            "payload_hash": source.payload_hash})
        return sorted(details, key=lambda item: (item["role"], item["artifact_id"]))
    for table, column in sorted(expected):
        matches = [m for m in all_metadata("column_profile")
                   if m.identity.get("table") == table and m.feature == column]
        if len(matches) != 1:
            return None
        try:
            if not verify(matches[0]):
                return None
            source_pins(matches[0])
        except Exception:
            return None
        pins.append({"type": "column_profile", "table": table, "feature": column,
                     "artifact_id": matches[0].artifact_id,
                     "identity_fingerprint": matches[0].identity_fingerprint,
                     "status": matches[0].status, "hash": matches[0].payload_hash,
                     "source_pins": source_pins(matches[0])})
    for table in sorted(table_names):
        for kind in ("table_profile", "table_inventory_profile"):
            matches = [m for m in all_metadata(kind) if m.identity.get("table") == table]
            if len(matches) != 1:
                return None
            try:
                if not verify(matches[0]):
                    return None
                source_pins(matches[0])
            except Exception:
                return None
            pins.append({"type": kind, "table": table, "feature": "",
                         "artifact_id": matches[0].artifact_id,
                         "identity_fingerprint": matches[0].identity_fingerprint,
                         "status": matches[0].status, "hash": matches[0].payload_hash,
                         "source_pins": source_pins(matches[0])})
    inventory_pins = [{"table": row["table_name"], "column": row["column_name"],
                       "data_type": row.get("data_type"), "role": row.get("role"),
                       "role_reviewed": row.get("role_reviewed"),
                       "profile": row.get("profile_json"),
                       "description": row.get("description") or ""}
                      for row in inventory]
    return stable_fingerprint({"snapshot_id": snapshot_id, "asset_id": asset_id,
                               "inventory": inventory_pins, "profiles": pins})


def record_profile_completion(snapshot_id: str) -> dict[str, Any] | None:
    """Durably prove that this invocation completed profile publication."""
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item_row = conn.execute("SELECT * FROM dq_items WHERE item_id=?", (snapshot_id,)).fetchone()
        if item_row is None or not item_row["sourcing_tenant_id"] or not item_row["dataset_family_id"]:
            conn.rollback(); return None
        item = dict(item_row)
        # Elevation is a real publication operation: validate the complete
        # governed set while its proof update is fenced by this same UoW.
        fingerprint = _profile_fingerprint(snapshot_id, item["dataset_family_id"], conn=conn)
        if fingerprint is None:
            conn.rollback(); return None
        current = conn.execute("SELECT * FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if current and current["fingerprint"] == fingerprint:
            # A later genuine publication is authoritative over a migration
            # proof even when its retained profile set is byte-for-byte equal.
            if current["proof_source"] != "publication" or current["proof_migration_version"] is not None:
                conn.execute("UPDATE dataset_structure_profile_publication_completions SET proof_source='publication',proof_migration_version=NULL,completed_at=? WHERE snapshot_id=?", (_utc(), snapshot_id))
                current = conn.execute("SELECT * FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            conn.commit(); return dict(current)
        generation = 1 if current is None else int(current["generation"]) + 1
        now = _utc()
        if current:
            conn.execute("UPDATE dataset_structure_profile_publication_completions SET asset_id=?,tenant_id=?,generation=?,completion_token=?,fingerprint=?,proof_source='publication',proof_migration_version=NULL,state='completed',completed_at=? WHERE snapshot_id=?", (item["dataset_family_id"], item["sourcing_tenant_id"], generation, f"dscpc_{uuid.uuid4().hex}", fingerprint, now, snapshot_id))
        else:
            conn.execute("INSERT INTO dataset_structure_profile_publication_completions (snapshot_id,asset_id,tenant_id,generation,completion_token,fingerprint,proof_source,proof_migration_version,state,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (snapshot_id, item["dataset_family_id"], item["sourcing_tenant_id"], generation, f"dscpc_{uuid.uuid4().hex}", fingerprint, "publication", None, "completed", now))
        conn.commit()
    return db.query_one("dataset_structure_profile_publication_completions", snapshot_id=snapshot_id)


def record_backfill_profile_completion(snapshot_id: str, *, tenant_id: str,
                                       migration_version: str) -> dict[str, Any] | None:
    """Create the narrowly authorized migration proof from retained profiles.

    Normal reconciliation must never infer a publication completion from
    artifacts it happens to see.  Slice 4 is the sole explicit migration path
    allowed to establish that proof, and records its distinct provenance.
    """
    item = db.query_one("dq_items", item_id=snapshot_id)
    if (not item or item.get("snapshot_status") != "active" or item.get("ingest_status") != "ready"
            or item.get("sourcing_tenant_id") != tenant_id or not item.get("dataset_family_id")):
        return None
    fingerprint = _profile_fingerprint(snapshot_id, item["dataset_family_id"])
    if fingerprint is None:
        return None
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Re-read the item inside the transaction before making the proof.
        current_item = conn.execute("SELECT * FROM dq_items WHERE item_id=?", (snapshot_id,)).fetchone()
        if (current_item is None or current_item["snapshot_status"] != "active"
                or current_item["ingest_status"] != "ready"
                or current_item["sourcing_tenant_id"] != tenant_id
                or current_item["dataset_family_id"] != item["dataset_family_id"]):
            conn.rollback(); return None
        current = conn.execute("SELECT * FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if current and current["fingerprint"] == fingerprint and current["asset_id"] == item["dataset_family_id"] and current["tenant_id"] == tenant_id:
            conn.commit(); return dict(current)
        generation = 1 if current is None else int(current["generation"]) + 1
        now = _utc()
        values = (item["dataset_family_id"], tenant_id, generation, f"dscpc_{uuid.uuid4().hex}",
                  fingerprint, "backfill", migration_version, now, snapshot_id)
        if current:
            conn.execute("UPDATE dataset_structure_profile_publication_completions SET asset_id=?,tenant_id=?,generation=?,completion_token=?,fingerprint=?,proof_source=?,proof_migration_version=?,state='completed',completed_at=? WHERE snapshot_id=?", values)
        else:
            conn.execute("INSERT INTO dataset_structure_profile_publication_completions (snapshot_id,asset_id,tenant_id,generation,completion_token,fingerprint,proof_source,proof_migration_version,state,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (snapshot_id, item["dataset_family_id"], tenant_id, generation, f"dscpc_{uuid.uuid4().hex}", fingerprint, "backfill", migration_version, "completed", now))
        conn.commit()
    return db.query_one("dataset_structure_profile_publication_completions", snapshot_id=snapshot_id)


def schedule_backfill(snapshot_id: str, *, tenant_id: str, migration_version: str) -> tuple[dict[str, Any] | None, bool]:
    """Atomically validate and schedule one explicitly authorized migration item.

    This is deliberately separate from normal reconciliation: it is the only
    path that may derive a proof from retained governed artifacts.  Proof,
    marker, fenced job, initial review state and migration run share one
    ``BEGIN IMMEDIATE`` unit so a crash cannot leave a usable orphan marker.
    """
    now = _utc()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM dq_items WHERE item_id=?", (snapshot_id,)).fetchone()
        if (row is None or row["snapshot_status"] != "active" or row["ingest_status"] != "ready"
                or row["sourcing_tenant_id"] != tenant_id or not row["dataset_family_id"]):
            conn.rollback(); return None, False
        item = dict(row)
        fingerprint = _profile_fingerprint(snapshot_id, item["dataset_family_id"], conn=conn)
        if fingerprint is None:
            conn.rollback(); return None, False
        completion = conn.execute("SELECT * FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if completion and completion["fingerprint"] == fingerprint and completion["asset_id"] == item["dataset_family_id"] and completion["tenant_id"] == tenant_id:
            completion = dict(completion)
        else:
            generation = 1 if completion is None else int(completion["generation"]) + 1
            completion = {"snapshot_id": snapshot_id, "asset_id": item["dataset_family_id"], "tenant_id": tenant_id,
                          "generation": generation, "completion_token": f"dscpc_{uuid.uuid4().hex}",
                          "fingerprint": fingerprint, "proof_source": "backfill",
                          "proof_migration_version": migration_version}
            if conn.execute("SELECT 1 FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (snapshot_id,)).fetchone():
                conn.execute("UPDATE dataset_structure_profile_publication_completions SET asset_id=?,tenant_id=?,generation=?,completion_token=?,fingerprint=?,proof_source=?,proof_migration_version=?,state='completed',completed_at=? WHERE snapshot_id=?", (completion["asset_id"], tenant_id, generation, completion["completion_token"], fingerprint, "backfill", migration_version, now, snapshot_id))
            else:
                conn.execute("INSERT INTO dataset_structure_profile_publication_completions (snapshot_id,asset_id,tenant_id,generation,completion_token,fingerprint,proof_source,proof_migration_version,state,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (snapshot_id, completion["asset_id"], tenant_id, generation, completion["completion_token"], fingerprint, "backfill", migration_version, "completed", now))
        marker = conn.execute("SELECT * FROM dataset_structure_profile_publications WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if marker is None:
            conn.execute("INSERT INTO dataset_structure_profile_publications (snapshot_id,asset_id,tenant_id,generation,fingerprint,published_at) VALUES (?,?,?,?,?,?)", (snapshot_id, completion["asset_id"], tenant_id, completion["generation"], completion["fingerprint"], now))
        elif marker["generation"] != completion["generation"] or marker["fingerprint"] != completion["fingerprint"] or marker["asset_id"] != completion["asset_id"] or marker["tenant_id"] != tenant_id:
            conn.execute("UPDATE dataset_structure_profile_publications SET asset_id=?,tenant_id=?,generation=?,fingerprint=?,published_at=? WHERE snapshot_id=?", (completion["asset_id"], tenant_id, completion["generation"], completion["fingerprint"], now, snapshot_id))
        attempt = item.get("profile_publication_attempt_token") or ""
        conn.execute("UPDATE dataset_structure_materialization_jobs SET status='revoked',closed_error_code='DSC_R_PUBLICATION_REPLACED',lease_expires_at=?,updated_at=? WHERE snapshot_id=? AND status IN ('queued','running','retry_wait') AND (publication_generation<>? OR publication_fingerprint<>? OR publication_completion_token<>? OR publication_attempt_token<>?)", (now, now, snapshot_id, completion["generation"], completion["fingerprint"], completion["completion_token"], attempt))
        equivalent = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE snapshot_id=? AND publication_generation=? AND publication_fingerprint=? AND publication_completion_token=? AND publication_attempt_token=? AND status IN ('queued','running','retry_wait','succeeded') ORDER BY created_at DESC LIMIT 1", (snapshot_id, completion["generation"], completion["fingerprint"], completion["completion_token"], attempt)).fetchone()
        created = equivalent is None
        if equivalent is None:
            job = {"job_id": f"dscj_{uuid.uuid4().hex}", "snapshot_id": snapshot_id, "asset_id": item["dataset_family_id"], "tenant_id": tenant_id,
                   "reason": "backfill", "dsc_context_version": "1", "publication_generation": completion["generation"], "publication_fingerprint": completion["fingerprint"],
                   "publication_completion_token": completion["completion_token"], "publication_attempt_token": attempt, "fencing_token": 0,
                   "status": "queued", "attempt_count": 0, "max_attempts": MAX_ATTEMPTS, "available_at": now, "lease_owner": None, "lease_expires_at": None,
                   "heartbeat_at": None, "started_at": None, "finished_at": None, "closed_error_code": None, "progress_json": "{}", "created_at": now, "updated_at": now}
            conn.execute(f"INSERT INTO dataset_structure_materialization_jobs ({','.join(job)}) VALUES ({','.join('?' for _ in job)})", tuple(job.values()))
        else:
            job = dict(equivalent)
        conn.execute("INSERT OR IGNORE INTO dataset_structure_review_states (snapshot_id,tenant_id,asset_id,state,current_generation,current_evidence_fingerprint,current_draft_id,review_contract_version,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (snapshot_id, tenant_id, item["dataset_family_id"], "materializing", completion["generation"], None, None, "1", now))
        conn.execute("INSERT INTO dataset_structure_backfill_runs (run_id,migration_version,tenant_id,snapshot_id,status,publication_generation,job_id,source_fingerprint,proof_source,closed_error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(migration_version,tenant_id,snapshot_id) DO UPDATE SET status=CASE WHEN dataset_structure_backfill_runs.status='materialized' THEN 'materialized' ELSE 'scheduled' END,publication_generation=excluded.publication_generation,job_id=excluded.job_id,source_fingerprint=excluded.source_fingerprint,proof_source=excluded.proof_source,closed_error_code=NULL,updated_at=excluded.updated_at", (f"dscbf_{uuid.uuid4().hex}", migration_version, tenant_id, snapshot_id, "scheduled", completion["generation"], job["job_id"], completion["fingerprint"], completion["proof_source"], None, now, now))
        conn.commit()
    return _decode(job), created


def record_profile_publication(snapshot_id: str) -> dict[str, Any] | None:
    """Project a previously completed publication proof into the queue marker."""
    completion = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=snapshot_id)
    if not completion or completion.get("state") != "completed":
        return None
    item = db.query_one("dq_items", item_id=snapshot_id)
    if not item or _profile_fingerprint(snapshot_id, item.get("dataset_family_id") or "") != completion["fingerprint"]:
        return None
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM dataset_structure_profile_publications WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if current and current["generation"] == completion["generation"] and current["fingerprint"] == completion["fingerprint"]:
            conn.commit(); return dict(current)
        if current:
            conn.execute("UPDATE dataset_structure_profile_publications SET asset_id=?,tenant_id=?,generation=?,fingerprint=?,published_at=? WHERE snapshot_id=?", (completion["asset_id"], completion["tenant_id"], completion["generation"], completion["fingerprint"], _utc(), snapshot_id))
        else:
            conn.execute("INSERT INTO dataset_structure_profile_publications (snapshot_id,asset_id,tenant_id,generation,fingerprint,published_at) VALUES (?,?,?,?,?,?)", (snapshot_id, completion["asset_id"], completion["tenant_id"], completion["generation"], completion["fingerprint"], _utc()))
        conn.commit()
    return db.query_one("dataset_structure_profile_publications", snapshot_id=snapshot_id)


def eligible_snapshot(snapshot_id: str, *, tenant_id: str | None = None, repair_marker: bool = False) -> tuple[dict[str, Any], dict[str, Any]] | None:
    item = db.query_one("dq_items", item_id=snapshot_id)
    if (not item or item.get("snapshot_status") != "active" or item.get("ingest_status") != "ready"
            or not item.get("sourcing_tenant_id") or not item.get("dataset_family_id")
            or (tenant_id is not None and item["sourcing_tenant_id"] != tenant_id)):
        return None
    marker = db.query_one("dataset_structure_profile_publications", snapshot_id=snapshot_id)
    current_fingerprint = _profile_fingerprint(snapshot_id, item["dataset_family_id"])
    if repair_marker and current_fingerprint is not None and (marker is None or marker.get("fingerprint") != current_fingerprint):
        marker = record_profile_publication(snapshot_id)
    if not marker or marker["tenant_id"] != item["sourcing_tenant_id"] or marker["asset_id"] != item["dataset_family_id"]:
        return None
    if current_fingerprint != marker["fingerprint"]:
        return None
    return item, marker


def enqueue(snapshot_id: str, *, reason: str = "post_ready", tenant_id: str | None = None, repair_marker: bool = False,
            on_enqueued: Callable[[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], bool], None] | None = None) -> tuple[dict[str, Any] | None, bool]:
    if reason not in {"post_ready", "retry", "backfill"}:
        raise ValueError("unsupported DSC materialization reason")
    eligible = eligible_snapshot(snapshot_id, tenant_id=tenant_id, repair_marker=repair_marker)
    if eligible is None:
        return None, False
    item, marker = eligible; now = _utc()
    completion = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=snapshot_id)
    if (not completion or completion["state"] != "completed" or completion["generation"] != marker["generation"]
            or completion["fingerprint"] != marker["fingerprint"]):
        return None, False
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        attempt = item.get("profile_publication_attempt_token") or ""
        conn.execute("UPDATE dataset_structure_materialization_jobs SET status='revoked',closed_error_code='DSC_R_PUBLICATION_REPLACED',lease_expires_at=?,updated_at=? WHERE snapshot_id=? AND status IN ('queued','running','retry_wait') AND (publication_generation<>? OR publication_fingerprint<>? OR publication_completion_token<>? OR publication_attempt_token<>?)", (now, now, snapshot_id, marker["generation"], marker["fingerprint"], completion["completion_token"], attempt))
        equivalent = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE snapshot_id=? AND publication_generation=? AND publication_fingerprint=? AND publication_completion_token=? AND publication_attempt_token=? AND status IN ('queued','running','retry_wait','succeeded') ORDER BY created_at DESC LIMIT 1", (snapshot_id, marker["generation"], marker["fingerprint"], completion["completion_token"], attempt)).fetchone()
        if equivalent:
            if on_enqueued:
                on_enqueued(conn, item, marker, completion, dict(equivalent), False)
            conn.commit(); return _decode(dict(equivalent)), False
        row = {"job_id": f"dscj_{uuid.uuid4().hex}", "snapshot_id": snapshot_id, "asset_id": item["dataset_family_id"], "tenant_id": item["sourcing_tenant_id"], "reason": reason, "dsc_context_version": "1", "publication_generation": marker["generation"], "publication_fingerprint": marker["fingerprint"], "publication_completion_token": completion["completion_token"], "publication_attempt_token": attempt, "fencing_token": 0, "status": "queued", "attempt_count": 0, "max_attempts": MAX_ATTEMPTS, "available_at": now, "lease_owner": None, "lease_expires_at": None, "heartbeat_at": None, "started_at": None, "finished_at": None, "closed_error_code": None, "progress_json": "{}", "created_at": now, "updated_at": now}
        cols = ",".join(row)
        try:
            conn.execute(f"INSERT INTO dataset_structure_materialization_jobs ({cols}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        except sqlite3.IntegrityError:
            winner = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE snapshot_id=? AND publication_generation=? AND publication_fingerprint=? AND status IN ('queued','running','retry_wait') LIMIT 1", (snapshot_id, marker["generation"], marker["fingerprint"])).fetchone()
            if not winner:
                conn.rollback(); raise
            if on_enqueued:
                on_enqueued(conn, item, marker, completion, dict(winner), False)
            conn.commit(); return _decode(dict(winner)), False
        if on_enqueued:
            on_enqueued(conn, item, marker, completion, row, True)
        conn.commit()
    return _decode(row), True


def _expire_leases(snapshot_id: str | None = None, *, limit: int = 50) -> int:
    now = _utc(); recovered = 0
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        sql = "SELECT job_id,attempt_count,max_attempts FROM dataset_structure_materialization_jobs WHERE status='running' AND lease_expires_at<=?"
        params: list[Any] = [now]
        if snapshot_id is not None:
            sql += " AND snapshot_id=?"; params.append(snapshot_id)
        sql += " ORDER BY lease_expires_at,job_id LIMIT ?"; params.append(max(1, min(int(limit), 100)))
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            attempts = int(row["attempt_count"]); terminal = attempts >= int(row["max_attempts"])
            conn.execute("UPDATE dataset_structure_materialization_jobs SET status=?,available_at=?,finished_at=?,closed_error_code='DSC_R_WORKER_LEASE_EXPIRED',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE job_id=?", ("failed" if terminal else "retry_wait", now if terminal else _after(RETRY_DELAYS_SECONDS[min(attempts - 1, 2)]), now if terminal else None, now, row["job_id"])); recovered += 1
        conn.commit()
    return recovered


def reconcile(snapshot_id: str | None = None, *, ensure_job: bool = True) -> dict[str, int]:
    # Item polling must never mutate another snapshot's lease state. Startup
    # and the background sweep use fixed-size global batches.
    recovered = _expire_leases(snapshot_id) if snapshot_id is not None else _expire_leases(limit=50)
    if snapshot_id is not None:
        candidates = [snapshot_id]
    else:
        cursor = db.query_one("dataset_structure_materialization_reconcile_cursor", cursor_name="global")
        after = (cursor or {}).get("last_snapshot_id") or ""
        candidates = [r["item_id"] for r in db.execute(
            "SELECT item_id FROM dq_items WHERE snapshot_status='active' AND ingest_status='ready' "
            "AND sourcing_tenant_id IS NOT NULL AND item_id>? ORDER BY item_id LIMIT 50", (after,))]
        if not candidates and after:
            candidates = [r["item_id"] for r in db.execute(
                "SELECT item_id FROM dq_items WHERE snapshot_status='active' AND ingest_status='ready' "
                "AND sourcing_tenant_id IS NOT NULL ORDER BY item_id LIMIT 50")]
        if candidates:
            db.upsert("dataset_structure_materialization_reconcile_cursor", {
                "cursor_name": "global", "last_snapshot_id": candidates[-1], "updated_at": _utc()})
    queued = 0
    if ensure_job:
        for candidate in candidates:
            _job, created = enqueue(candidate, reason="post_ready", repair_marker=True); queued += int(created)
    return {"recovered": recovered, "queued": queued}


def _claim_one(owner: str) -> dict[str, Any] | None:
    now = _utc()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE status IN ('queued','retry_wait') AND available_at<=? ORDER BY available_at,created_at LIMIT 1", (now,)).fetchone()
        if not row:
            conn.commit(); return None
        cur = conn.execute("UPDATE dataset_structure_materialization_jobs SET status='running',attempt_count=attempt_count+1,fencing_token=fencing_token+1,lease_owner=?,lease_expires_at=?,heartbeat_at=?,started_at=COALESCE(started_at,?),closed_error_code=NULL,updated_at=? WHERE job_id=? AND status IN ('queued','retry_wait')", (owner, _after(LEASE_SECONDS), now, now, now, row["job_id"]))
        if cur.rowcount != 1:
            conn.rollback(); return None
        claimed = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE job_id=?", (row["job_id"],)).fetchone(); conn.commit()
    return _decode(dict(claimed))


def _lease_guard(job: dict[str, Any], owner: str, conn: Any = None) -> None:
    """One exact binding predicate for heartbeat, AAR writes, and finish."""
    row = db.query_one("dataset_structure_materialization_jobs", conn=conn, job_id=job["job_id"])
    item = db.query_one("dq_items", conn=conn, item_id=job["snapshot_id"])
    marker = db.query_one("dataset_structure_profile_publications", conn=conn, snapshot_id=job["snapshot_id"])
    completion = db.query_one("dataset_structure_profile_publication_completions", conn=conn, snapshot_id=job["snapshot_id"])
    if (not row or not item or not marker or not completion or row["status"] != "running"
            or row.get("lease_owner") != owner or int(row.get("fencing_token") or 0) != int(job["fencing_token"])
            or row.get("lease_expires_at", "") <= _utc() or item.get("snapshot_status") != "active"
            or item.get("ingest_status") != "ready" or item.get("dataset_family_id") != job.get("asset_id")
            or item.get("sourcing_tenant_id") != job.get("tenant_id")
            or item.get("profile_publication_attempt_token") != job.get("publication_attempt_token")
            or marker.get("asset_id") != job.get("asset_id") or marker.get("tenant_id") != job.get("tenant_id")
            or marker.get("generation") != job.get("publication_generation")
            or marker.get("fingerprint") != job.get("publication_fingerprint")
            or completion.get("state") != "completed" or completion.get("generation") != job.get("publication_generation")
            or completion.get("fingerprint") != job.get("publication_fingerprint")
            or completion.get("asset_id") != job.get("asset_id") or completion.get("tenant_id") != job.get("tenant_id")
            or completion.get("completion_token") != job.get("publication_completion_token")):
        raise DatasetStructureObservationError("DSC_R_WORKER_LEASE_LOST")


def _heartbeat(job: dict[str, Any], owner: str) -> bool:
    now = _utc()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _lease_guard(job, owner, conn)
        except DatasetStructureObservationError:
            conn.rollback(); return False
        cur = conn.execute("UPDATE dataset_structure_materialization_jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE job_id=? AND status='running' AND lease_owner=? AND fencing_token=?", (now, _after(LEASE_SECONDS), now, job["job_id"], owner, job["fencing_token"])); conn.commit()
    return cur.rowcount == 1


def _finish(job: dict[str, Any], owner: str, error: str | None, totals: dict[str, int],
            on_success: Callable[[Any], None] | None = None) -> bool:
    now = _utc(); attempts = int(job["attempt_count"]); terminal = error is None or attempts >= int(job["max_attempts"])
    status = "succeeded" if error is None else ("failed" if terminal else "retry_wait")
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _lease_guard(job, owner, conn)
        except DatasetStructureObservationError:
            conn.rollback(); return False
        cur = conn.execute("UPDATE dataset_structure_materialization_jobs SET status=?,available_at=?,finished_at=?,closed_error_code=?,progress_json=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE job_id=? AND status='running' AND lease_owner=? AND fencing_token=?", (status, now if terminal else _after(RETRY_DELAYS_SECONDS[min(attempts - 1, 2)]), now if terminal else None, error, json.dumps(totals, separators=(",", ":")), now, job["job_id"], owner, job["fencing_token"]))
        if cur.rowcount == 1 and error is None and on_success is not None:
            on_success(conn)
        conn.commit()
    return cur.rowcount == 1


def _record_backfill_outcome(job: dict[str, Any], error: str | None) -> None:
    """Mirror only terminal Slice-4 queue outcome into migration observability."""
    if not db.query_one("dataset_structure_backfill_runs", job_id=job["job_id"]):
        return
    row = db.query_one("dataset_structure_materialization_jobs", job_id=job["job_id"])
    if not row:
        return
    if row["status"] in {"failed", "revoked"}:
        db.update("dataset_structure_backfill_runs", {"job_id": job["job_id"]},
                  {"status": "failed", "closed_error_code": error or row.get("closed_error_code"), "updated_at": _utc()})


def process_one(*, owner: str = WORKER_OWNER) -> dict[str, Any] | None:
    job = _claim_one(owner)
    if not job:
        return None
    stop = threading.Event(); lost = threading.Event()
    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            if not _heartbeat(job, owner):
                lost.set(); return
    thread = threading.Thread(target=beat, name="dsc-job-heartbeat", daemon=True); thread.start()
    totals = {"tables_total": 0, "predicates_total": 0, "predicates_succeeded": 0, "predicates_failed": 0}; error: str | None = None; began = datetime.now(timezone.utc)
    try:
        eligible = eligible_snapshot(job["snapshot_id"], tenant_id=job["tenant_id"])
        if not eligible or eligible[1]["generation"] != job["publication_generation"] or eligible[1]["fingerprint"] != job["publication_fingerprint"]:
            error = "DSC_R_PUBLICATION_REPLACED"
        else:
            repo = AnalysisArtifactRepository(); tables = sorted({r["table_name"] for r in db.query("dq_item_tables", item_id=job["snapshot_id"])}); totals["tables_total"] = len(tables)
            for table in tables:
                for predicate in sorted(SUPPORTED_OBSERVER_PREDICATES):
                    totals["predicates_total"] += 1
                    if lost.is_set() or (datetime.now(timezone.utc) - began).total_seconds() >= JOB_DEADLINE_SECONDS:
                        error = "DSC_R_WORKER_LEASE_LOST" if lost.is_set() else "DSC_R_JOB_DEADLINE_EXCEEDED"; break
                    _lease_guard(job, owner)
                    try:
                        observe_dataset_structure(repo, job["snapshot_id"], tables=[table], predicates=(predicate,), created_by="system:dsc-materializer", precommit_guard=lambda conn=None: _lease_guard(job, owner, conn)); totals["predicates_succeeded"] += 1
                    except DatasetStructureObservationError as exc:
                        error = exc.reason_code; totals["predicates_failed"] += 1
                    except Exception:
                        error = "DSC_R_MATERIALIZATION_FAILED"; totals["predicates_failed"] += 1
                    if error: break
                if error: break
    except DatasetStructureObservationError as exc:
        error = exc.reason_code
    except Exception:
        error = "DSC_R_MATERIALIZATION_FAILED"
    stop.set(); thread.join(timeout=1)
    success_callback = None
    if error is None and db.query_one("dataset_structure_backfill_runs", job_id=job["job_id"]):
        try:
            from . import dataset_structure_review
            item = db.query_one("dq_items", item_id=job["snapshot_id"])
            if item is None:
                raise DatasetStructureObservationError("DSC_R_REVIEW_PROJECTION_FAILED")
            projection = dataset_structure_review.prepare_backfill_projection(item, generation=job["publication_generation"])
            success_callback = lambda conn: dataset_structure_review.commit_backfill_projection(conn, item, projection, job_id=job["job_id"])
        except Exception:
            error = "DSC_R_REVIEW_PROJECTION_FAILED"
    try:
        finished = _finish(job, owner, error, totals, on_success=success_callback)
    except Exception:
        # The callback transaction rolled back the tentative success, leaving
        # the fenced claim running. Persist a normal retryable closed error.
        error = "DSC_R_REVIEW_PROJECTION_FAILED"
        finished = _finish(job, owner, error, totals)
    if not finished:
        error = "DSC_R_WORKER_LEASE_LOST"
    _record_backfill_outcome(job, error)
    return {"job_id": job["job_id"], "status": "succeeded" if error is None else "failed", "error_code": error}


def revoke_owned(owner: str = WORKER_OWNER) -> int:
    """Shutdown fence: running workers cannot publish after backup/shutdown."""
    now = _utc()
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE dataset_structure_materialization_jobs SET status='revoked',closed_error_code='DSC_R_WORKER_REVOKED',lease_expires_at=?,updated_at=? WHERE status='running' AND lease_owner=?", (now, now, owner))
        conn.commit()
    return cur.rowcount


def status(job_id: str, *, snapshot_id: str, tenant_id: str) -> dict[str, Any] | None:
    row = db.query_one("dataset_structure_materialization_jobs", job_id=job_id, snapshot_id=snapshot_id, tenant_id=tenant_id)
    return _decode(row) if row else None


def public_status(row: dict[str, Any]) -> dict[str, Any]:
    progress = row.get("progress") or {}
    revoked = row["status"] == "revoked"
    return {"job_id": row["job_id"], "status": "failed" if revoked else row["status"],
            "attempt_count": row["attempt_count"], "max_attempts": row["max_attempts"],
            "progress": {key: int(progress.get(key, 0)) for key in ("tables_total", "predicates_total", "predicates_succeeded", "predicates_failed")},
            "retry_after_ms": 1000 if row["status"] in {"queued", "retry_wait"} else None,
            # Do not expose shutdown/fencing ownership detail to callers.
            "error_code": "DSC_R_PUBLICATION_REPLACED" if revoked else row.get("closed_error_code")}
