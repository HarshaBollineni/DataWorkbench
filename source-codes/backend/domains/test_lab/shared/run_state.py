"""Shared Test Lab run-state and append-only decision access."""
from __future__ import annotations

import os
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import system_db as db

_LOGGER = logging.getLogger(__name__)

DRAFT, RUNNING, DONE, FAILED, DISCARDED = "draft", "running", "done", "failed", "discarded"

RUN_LEASE_SECONDS = max(30, int(os.environ.get("DIAGNOSTIC_RUN_LEASE_SECONDS", "120")))
RUN_HEARTBEAT_SECONDS = max(
    5, min(RUN_LEASE_SECONDS // 3, int(os.environ.get("DIAGNOSTIC_RUN_HEARTBEAT_SECONDS", "15")))
)
UNCLAIMED_RUN_GRACE_SECONDS = max(
    RUN_LEASE_SECONDS,
    int(os.environ.get("DIAGNOSTIC_RUN_START_GRACE_SECONDS", "180")),
)
PROCESS_EXECUTION_OWNER = f"process-{os.getpid()}-{uuid.uuid4().hex[:10]}"
# The browser's native EventSource cannot carry an Authorization header.  D06
# therefore has a deliberately private, short-lived launch-to-stream handoff.
# It lives in system state (rather than this worker's memory) so a stream can
# land on another worker after a restart.
EXECUTION_CONTEXT_SECONDS = max(
    RUN_LEASE_SECONDS,
    int(os.environ.get("DIAGNOSTIC_EXECUTION_CONTEXT_SECONDS", "900")),
)
# Keep the physical lock wait well below the advisory 100ms policy ceiling:
# SQLite can otherwise spend a second busy interval in connection setup and a
# second one acquiring BEGIN IMMEDIATE under an exclusive writer.
EXECUTION_CONTEXT_DB_BUDGET_SECONDS = 0.0

_FAILURE_MESSAGES = {
    "execution_failed": "Execution failed safely. Review available evidence and start a new run.",
    "execution_stopped_before_completion": (
        "Execution stopped before reaching a terminal result. Start a new run."
    ),
    "worker_lease_expired": (
        "Execution was interrupted after its worker stopped responding. Start a new run."
    ),
}


class ManifestError(RuntimeError):
    """A manifest operation that cannot proceed because its contract is invalid."""


class RunAlreadyExecuting(ManifestError):
    """Raised when another live worker owns the persisted run lease."""


@contextmanager
def _execution_context_connection():
    """Open a minimal bounded connection for private D06 handoff state.

    Do not use ``system_db.get_conn`` here: its general-purpose setup may run
    additional SQLite pragmas before an operation's ``BEGIN IMMEDIATE`` and
    therefore accumulate lock waits.  This table has no foreign keys and
    needs neither journal configuration nor any public DB helper behaviour.
    """
    conn = sqlite3.connect(str(db.SYS_DB_PATH), timeout=EXECUTION_CONTEXT_DB_BUDGET_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(EXECUTION_CONTEXT_DB_BUDGET_SECONDS * 1000)}")
        yield conn
    except Exception:
        # ``close`` rolls back an uncommitted SQLite transaction.  Calling an
        # explicit rollback while another connection owns an exclusive lock
        # can itself add another lock wait to this best-effort path.
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def bind_trusted_execution_tenant(run_id: str, tenant_id: str) -> bool:
    """Atomically bind D06's authenticated launch tenant exactly once.

    The private context is intentionally outside the immutable manifest.  A
    repeated authenticated launch from the same tenant is harmless; a tenant
    switch is refused.  Expired contexts are disposable crash recovery state.
    """
    if not isinstance(run_id, str) or not run_id or not isinstance(tenant_id, str) or not tenant_id:
        raise ManifestError("trusted execution tenant is required")
    bound_at, expires_at = _lease_times(EXECUTION_CONTEXT_SECONDS)
    try:
        with _execution_context_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM diagnostic_execution_context WHERE expires_at <= ?",
                (bound_at,),
            )
            row = conn.execute(
                "SELECT tenant_id FROM diagnostic_execution_context WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO diagnostic_execution_context "
                    "(run_id, tenant_id, bound_at, expires_at) VALUES (?,?,?,?)",
                    (run_id, tenant_id, bound_at, expires_at),
                )
            elif row["tenant_id"] != tenant_id:
                conn.rollback()
                raise ManifestError("diagnostic execution context is already bound to another tenant")
            else:
                # A genuine retry by the same authenticated tenant extends the
                # short handoff without permitting any tenant switch.
                conn.execute(
                    "UPDATE diagnostic_execution_context SET expires_at=? WHERE run_id=?",
                    (expires_at, run_id),
                )
            conn.commit()
        return True
    except ManifestError:
        raise
    except Exception:
        _LOGGER.debug("diagnostic_execution_context_bind_unavailable")
        return False


def trusted_execution_tenant(run_id: str) -> str | None:
    if not isinstance(run_id, str) or not run_id:
        return None
    now = db.now_ist()
    try:
        with _execution_context_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM diagnostic_execution_context WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT tenant_id FROM diagnostic_execution_context WHERE run_id=?", (run_id,),
            ).fetchone()
            conn.commit()
        return str(row["tenant_id"]) if row is not None else None
    except Exception:
        _LOGGER.debug("diagnostic_execution_context_read_unavailable")
        return None


def clear_trusted_execution_tenant(run_id: str, tenant_id: str | None = None) -> bool:
    """Drop a consumed D06 handoff, never deleting a replacement binding."""
    if not isinstance(run_id, str) or not run_id:
        return False
    try:
        with _execution_context_connection() as conn:
            if tenant_id:
                conn.execute(
                    "DELETE FROM diagnostic_execution_context WHERE run_id=? AND tenant_id=?",
                    (run_id, tenant_id),
                )
            else:
                conn.execute("DELETE FROM diagnostic_execution_context WHERE run_id=?", (run_id,))
        return True
    except Exception:
        _LOGGER.debug("diagnostic_execution_context_clear_unavailable")
        return False


def cleanup_expired_execution_contexts() -> int:
    """Remove abandoned handoffs after a crash without touching run history."""
    try:
        with _execution_context_connection() as conn:
            return conn.execute(
                "DELETE FROM diagnostic_execution_context WHERE expires_at <= ?", (db.now_ist(),),
            ).rowcount
    except Exception:
        _LOGGER.debug("diagnostic_execution_context_cleanup_unavailable")
        return 0


def new_execution_owner(channel: str) -> str:
    """Return an opaque owner token for one synchronous or streamed execution."""
    return f"{PROCESS_EXECUTION_OWNER}:{channel}:{uuid.uuid4().hex[:10]}"


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _lease_times(seconds: int = RUN_LEASE_SECONDS) -> tuple[str, str]:
    now = db.now_ist()
    expires = (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()
    return now, expires


def _is_future(value: str | None, now: datetime) -> bool:
    parsed = _as_datetime(value)
    if parsed is None:
        return False
    try:
        return parsed > now
    except TypeError:
        # Legacy timestamps may be naive while newer ones carry a zone. A
        # malformed/mixed lease must fail closed instead of staying RUNNING.
        return False


def claim_run_lease(run_id: str, owner: str,
                    lease_seconds: int = RUN_LEASE_SECONDS) -> bool:
    """Atomically claim or renew one RUNNING diagnostic.

    A live lease owned by a different worker prevents duplicate execution.
    Expired ownership is not resumed because diagnostic runners are not
    transactionally restartable; the recovery sweep converts it to FAILED.
    """
    now_text, expires = _lease_times(lease_seconds)
    now = datetime.fromisoformat(now_text)
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = db.query_one("diag_runs", conn=conn, run_id=run_id)
        if not run or run["status"] != RUNNING:
            conn.commit()
            return False
        current_owner = run.get("execution_owner")
        if current_owner and current_owner != owner:
            if _is_future(run.get("lease_expires_at"), now):
                conn.commit()
                raise RunAlreadyExecuting("This diagnostic run is already executing.")
            _mark_failed_in_connection(
                conn, run, now_text, "worker_lease_expired", "system:run-recovery",
            )
            conn.commit()
            raise ManifestError(
                "The prior execution was interrupted; start a new diagnostic run."
            )
        db.update("diag_runs", {"run_id": run_id, "status": RUNNING}, {
            "heartbeat_at": now_text,
            "lease_expires_at": expires,
            "execution_owner": owner,
            "status_detail_json": {
                "code": "executing",
                "message": "Execution is active.",
                "at": now_text,
            },
        }, conn=conn)
        conn.commit()
    return True


def release_run_lease(run_id: str, owner: str) -> None:
    """Release only the caller's lease without altering the terminal status."""
    db.update("diag_runs", {"run_id": run_id, "execution_owner": owner}, {
        "heartbeat_at": db.now_ist(), "lease_expires_at": None,
        "execution_owner": None, "status_detail_json": None,
    })


def _mark_failed_in_connection(conn, run: dict[str, Any], now: str,
                               reason: str, actor: str) -> None:
    db.update("diag_runs", {"run_id": run["run_id"], "status": run["status"]}, {
        "status": FAILED, "finished_at": now, "heartbeat_at": now,
        "lease_expires_at": None, "execution_owner": None,
        "status_detail_json": {
            "code": reason,
            "message": _FAILURE_MESSAGES.get(reason, _FAILURE_MESSAGES["execution_failed"]),
            "actor": actor,
            "at": now,
        },
    }, conn=conn)


def mark_run_failed(run_id: str, reason: str = "execution_failed",
                    actor: str = "system", *, owner: str | None = None) -> bool:
    """Fail a launched DRAFT/RUNNING run without exposing exception details.

    DRAFT is accepted for the legacy stream-start path, where an adapter can
    fail before its first instruction freezes the manifest.  Callers must use
    this only after an execution request has actually started.
    """
    now = db.now_ist()
    now_dt = datetime.fromisoformat(now)
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = db.query_one("diag_runs", conn=conn, run_id=run_id)
        if not run or run["status"] not in {DRAFT, RUNNING}:
            conn.commit()
            return False
        current_owner = run.get("execution_owner")
        if (run["status"] == RUNNING and owner is not None
                and current_owner not in {None, owner}
                and _is_future(run.get("lease_expires_at"), now_dt)):
            conn.commit()
            return False
        _mark_failed_in_connection(conn, run, now, reason, actor)
        conn.commit()
    return True


def recover_expired_runs(*, actor: str = "system:run-recovery",
                         run_ids: set[str] | None = None) -> dict[str, Any]:
    """Convert expired or long-unclaimed RUNNING rows into auditable failures.

    This is safe to call at startup and periodically. Active leases are left
    untouched, including leases owned by another application worker.
    """
    now_text = db.now_ist()
    now = datetime.fromisoformat(now_text)
    recovered: list[str] = []
    candidates = db.query("diag_runs", status=RUNNING)
    for candidate in candidates:
        if run_ids is not None and candidate["run_id"] not in run_ids:
            continue
        with db.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = db.query_one("diag_runs", conn=conn, run_id=candidate["run_id"])
            if not run or run["status"] != RUNNING:
                conn.commit()
                continue
            expiry = run.get("lease_expires_at")
            if expiry:
                expired = not _is_future(expiry, now)
            else:
                started = _as_datetime(run.get("heartbeat_at") or run.get("started_at")
                                       or run.get("created_at"))
                try:
                    expired = started is None or (
                        now - started
                    ).total_seconds() >= UNCLAIMED_RUN_GRACE_SECONDS
                except TypeError:
                    expired = True
            if expired:
                _mark_failed_in_connection(
                    conn, run, now_text, "worker_lease_expired", actor,
                )
                recovered.append(run["run_id"])
            conn.commit()
    return {"recovered": len(recovered), "run_ids": recovered, "checked_at": now_text}


@dataclass
class RunLease:
    run_id: str
    owner: str
    claimed: bool = False

    def heartbeat(self) -> bool:
        self.claimed = claim_run_lease(self.run_id, self.owner) or self.claimed
        return self.claimed


@contextmanager
def managed_run_execution(run_id: str, *, channel: str,
                          actor: str = "system"):
    """Guard one runner with duplicate prevention, heartbeats, and finalization."""
    lease = RunLease(run_id, new_execution_owner(channel))
    run = get_run(run_id)
    if run["status"] == RUNNING:
        lease.heartbeat()
    stop = threading.Event()

    def heartbeat_loop() -> None:
        while not stop.wait(RUN_HEARTBEAT_SECONDS):
            try:
                if get_run(run_id)["status"] != RUNNING:
                    return
                lease.heartbeat()
            except Exception:
                # The foreground execution owns error handling. A heartbeat
                # failure must not expose details or kill the worker thread.
                return

    thread = threading.Thread(
        target=heartbeat_loop, name=f"diagnostic-heartbeat-{run_id}", daemon=True,
    )
    thread.start()
    try:
        yield lease
    except Exception:
        mark_run_failed(run_id, "execution_failed", actor, owner=lease.owner)
        raise
    finally:
        stop.set()
        current = get_run(run_id)
        if current["status"] in {DRAFT, RUNNING}:
            mark_run_failed(
                run_id, "execution_stopped_before_completion", actor,
                owner=lease.owner,
            )
        release_run_lease(run_id, lease.owner)


def get_run(run_id: str) -> dict[str, Any]:
    row = db.query_one("diag_runs", run_id=run_id)
    if not row:
        raise KeyError(f"Unknown run: {run_id}")
    return row


def get_manifest(run_id: str) -> dict[str, Any]:
    return get_run(run_id)["manifest_json"]


def record_decision(run_id: str, kind: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Append one attributable decision to a diagnostic run."""
    timestamp = db.now_ist()
    row_id = db.insert("diag_run_decisions", {
        "run_id": run_id,
        "kind": kind,
        "payload_json": payload,
        "actor": actor,
        "ts": timestamp,
    })
    return {
        "id": row_id,
        "run_id": run_id,
        "kind": kind,
        "payload": payload,
        "actor": actor,
        "ts": timestamp,
    }


def list_decisions(run_id: str) -> list[dict[str, Any]]:
    return db.query("diag_run_decisions", run_id=run_id, order_by="id")


def latest_draft(item_id: str, diagnostic_id: int,
                 actor: str = "system") -> dict[str, Any] | None:
    """Return one open setup and archive older duplicate drafts."""
    rows = db.query(
        "diag_runs", item_id=item_id, diagnostic_id=diagnostic_id,
        status=DRAFT, order_by="created_at DESC, run_id DESC",
    )
    if not rows:
        return None
    for duplicate in rows[1:]:
        discard_draft(
            duplicate["run_id"], actor=actor,
            reason="superseded_duplicate_draft",
        )
    run = rows[0]
    manifest = run.get("manifest_json") or {}
    selected = (
        manifest.get("selected_features")
        or (manifest.get("scope") or {}).get("selected_features")
        or []
    )
    return {
        "run_id": run["run_id"],
        "item_id": run["item_id"],
        "diagnostic_id": run["diagnostic_id"],
        "status": DRAFT,
        "created_at": run.get("created_at"),
        "last_saved_at": manifest.get("updated_at") or run.get("created_at"),
        "selected_feature_count": len(selected),
    }


def discard_drafts(item_id: str, diagnostic_id: int, actor: str = "system") -> int:
    """Archive open setups while preserving their manifests and decision audit."""
    rows = db.query(
        "diag_runs", item_id=item_id, diagnostic_id=diagnostic_id, status=DRAFT,
    )
    now = db.now_ist()
    for run in rows:
        manifest = dict(run.get("manifest_json") or {})
        manifest.update({
            "status": DISCARDED,
            "discarded_at": now,
            "discarded_by": actor,
            "discard_reason": "start_afresh",
            "updated_at": now,
        })
        db.update("diag_runs", {"run_id": run["run_id"]}, {
            "manifest_json": manifest,
            "status": DISCARDED,
            "finished_at": now,
        })
    return len(rows)


def discard_draft(run_id: str, actor: str = "system",
                  reason: str = "user_discarded") -> dict[str, Any]:
    """Archive one selected setup without deleting its governed audit record."""
    run = get_run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError("Only an open diagnostic draft can be discarded")
    now = db.now_ist()
    manifest = dict(run.get("manifest_json") or {})
    manifest.update({
        "status": DISCARDED,
        "discarded_at": now,
        "discarded_by": actor,
        "discard_reason": reason,
        "updated_at": now,
    })
    db.update("diag_runs", {"run_id": run_id}, {
        "manifest_json": manifest,
        "status": DISCARDED,
        "finished_at": now,
    })
    return {
        "run_id": run_id,
        "status": DISCARDED,
        "discarded_at": now,
        "discarded_by": actor,
    }
