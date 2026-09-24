"""Generation-scoped progress checkpoints; no scheduler or execution-policy changes."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from time import perf_counter
import logging
import sqlite3
import uuid

import system_db as s

_active = ContextVar("rca_progress", default=None)
_log = logging.getLogger(__name__)


def _publish(state, status="running"):
    from domains.rca import evidence
    current = s.query_one("rca_cases", case_id=state["case"]["case_id"])
    if not current or current.get("workflow_generation", 1) != state["case"].get("workflow_generation", 1):
        return  # An old request must not repopulate a reset generation.
    evidence.record_event(
        state["case"], evidence_kind="operation_progress", stage="system",
        status="started" if status == "running" else status, actor=state["actor"], details={
            "operation_id": state["id"], "operation": state["operation"],
            "started_at": state["started_at"], "phase": " · ".join(state["stack"]) or "Preparing",
            "elapsed_seconds": round(perf_counter() - state["start"], 3),
            "phase_timings": state["timings"],
            "evidence_persistence_seconds": round(state["persistence"], 3),
        },
    )


def checkpoint(state, status="running"):
    if not state.get("retain", True):
        return
    # Telemetry failure must not fail or retry a successful analysis. Make it observable.
    try:
        _publish(state, status)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        _log.exception("RCA progress checkpoint could not be retained")


def persistence_elapsed(seconds):
    state = _active.get()
    if state is not None:
        state["persistence"] += seconds


def observe_event(kind, status):
    state = _active.get()
    if state is not None and kind != "operation_progress" and status in {"failed", "timed_out", "rejected"}:
        state["failed"] = True


@contextmanager
def phase(label):
    state = _active.get()
    if state is None:
        yield
        return
    state["stack"].append(label)
    checkpoint(state)
    start = perf_counter()
    status = "completed"
    try:
        yield
    except BaseException:
        status = "failed"
        raise
    finally:
        state["timings"].append({"phase": label, "seconds": round(perf_counter() - start, 3),
                                 "status": status, "depth": len(state["stack"]), "inclusive": True})
        state["stack"].pop()


def action(label, *, retain=True):
    """Wrap case/ look operations, reusing one context for nested service calls."""
    def decorate(fn):
        sig = signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _active.get() is not None:
                return fn(*args, **kwargs)
            from domains.rca.service import require_case, DEFAULT_TENANT
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            case_id = values.get("case_id")
            if not case_id:
                look = s.query_one("rca_looks", look_id=values.get("look_id"))
                if not look:
                    return fn(*args, **kwargs)  # Preserve the original validation error.
                case_id = look["case_id"]
            case = require_case(case_id, values.get("tenant_id", DEFAULT_TENANT))
            state = {"case": case, "actor": values.get("actor", "system"),
                     "id": uuid.uuid4().hex, "operation": label, "start": perf_counter(),
                     "started_at": s.now_ist(), "stack": [], "timings": [],
                     "persistence": 0.0, "failed": False, "retain": retain}
            token = _active.set(state)
            checkpoint(state)
            try:
                return fn(*args, **kwargs)
            except BaseException:
                state["failed"] = True
                raise
            finally:
                checkpoint(state, "failed" if state["failed"] else "completed")
                _log.info("RCA operation=%s status=%s elapsed_seconds=%.3f", label,
                          "failed" if state["failed"] else "completed", perf_counter() - state["start"])
                _active.reset(token)
        return wrapped
    return decorate


def read(case):
    """Read two bounded projections, never the whole case or evidence history."""
    from domains.aar.repository import AnalysisArtifactRepository
    generation = int(case.get("workflow_generation") or 1)
    result = {"case_id": case["case_id"], "workflow_generation": generation, "operation": None,
              "opening_result": None}
    with s.get_conn() as conn:
        for kind, key in (("operation_progress", "operation"), ("static_initial_review", "opening_result")):
            row = conn.execute(
                "SELECT artifact_id, status, sequence_no FROM rca_aar_links "
                "WHERE case_id=? AND workflow_generation=? AND evidence_kind=? "
                "ORDER BY sequence_no DESC LIMIT 1", (case["case_id"], generation, kind),
            ).fetchone()
            if row:
                _, payload = AnalysisArtifactRepository().get(row[0])
                details = payload.get("details") or {}
                result[key] = ({**details, "status": "running" if row[1] == "started" else row[1], "sequence_no": row[2]} if key == "operation"
                               else details.get("result") if row[1] == "completed" else None)
    return result
