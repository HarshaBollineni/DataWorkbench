"""D06's deliberately non-authoritative DSC cadence shadow observation.

Nothing returned here is fed into D06 reconciliation, persistence, reporting,
or identity construction.  Keeping this boundary in a small consumer module
makes that invariant mechanically reviewable.
"""
from __future__ import annotations

import logging
import time
import uuid
import threading
from copy import deepcopy
from typing import Any, Callable, Mapping

import system_db as db
import tenancy
from analysis_runtime.dataset_structure_context import project_materialized_dataset_structure, request_fingerprint


MAX_D06_DSC_CADENCE_SHADOW_WALL_MS = 2_000
_FLAG = "D06_DSC_CADENCE_SHADOW_ENABLED"
_LOGGER = logging.getLogger(__name__)
_VERSION = "v1"
_IN_FLIGHT: dict[tuple[str, str, str], float] = {}
_IN_FLIGHT_LOCK = threading.Lock()
_FLAG_DB_BUDGET_SECONDS = 0.1
_CLOSED_REASONS = frozenset({"dependency_changed", "dependency_limit_exceeded", "source_missing",
    "source_integrity_failed", "payload_limit_exceeded", "evidence_limit_exceeded", "invalid_response",
    "deadline_exceeded", "db_busy", "projection_error", "adapter_exception", "telemetry_error", "malformed_projection",
    "completed_scan_unknown", "advisory_non_comparable", "equivalent_observed_interval",
    "different_observed_interval"})
_EQUIVALENT = {
    "monthly": ({"unit": "month", "step": 1},),
    "quarterly": ({"unit": "quarter", "step": 1}, {"unit": "month", "step": 3}),
    "semiannual": ({"unit": "quarter", "step": 2}, {"unit": "month", "step": 6}),
    "annual": ({"unit": "year", "step": 1}, {"unit": "quarter", "step": 4}, {"unit": "month", "step": 12}),
}


def _bucket(elapsed_ms: int) -> str:
    if elapsed_ms < 50:
        return "lt_50"
    if elapsed_ms < 250:
        return "lt_250"
    if elapsed_ms < 1_000:
        return "lt_1000"
    return "lt_2000" if elapsed_ms <= MAX_D06_DSC_CADENCE_SHADOW_WALL_MS else "gt_2000"


def _metric(*, outcome: str, reason: str, elapsed_ms: int, enabled: bool) -> None:
    # Intentionally closed, identifier-free operational event.  Do not add
    # exception text, locator data, interval values, counts, or pins here.
    _LOGGER.info("d06_dsc_cadence_shadow_metric_v1", extra={
        "adapter_version": _VERSION, "outcome": outcome, "reason_category": reason,
        "elapsed_bucket": _bucket(elapsed_ms), "flag_enabled": enabled,
    })


def _deadline_result(deadline: float) -> dict[str, str] | None:
    """Return the closed local timeout outcome once the advisory budget ends."""
    if time.monotonic() >= deadline:
        return {"outcome": "error", "reason": "timeout"}
    return None


def append_audit_event(tenant_id: str, payload: Mapping[str, Any], *,
                       deadline: float | None = None) -> None:
    """Consumer-owned append-only, non-public reproducibility transport.

    There is no general AAR/D06 audit transport.  The table is intentionally
    not projected by any API; access is therefore limited to the governed
    system-state audit boundary.  Its caller supplies an already-sanitized
    closed payload.
    """
    remaining = _FLAG_DB_BUDGET_SECONDS if deadline is None else min(
        _FLAG_DB_BUDGET_SECONDS, deadline - time.monotonic(),
    )
    if remaining <= 0:
        raise TimeoutError("D06 advisory database budget expired")
    with db.get_conn(timeout=remaining, configure_journal=False) as conn:
        conn.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
        conn.execute("INSERT OR IGNORE INTO d06_dsc_cadence_shadow_audit "
                     "(event_id, tenant_id, run_id, adapter_version, event_type, payload_json, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (f"d06shadow_{uuid.uuid4().hex[:16]}", tenant_id,
                     payload["run_id"], _VERSION, "d06_dsc_cadence_shadow_audit_v1",
                     db._encode("d06_dsc_cadence_shadow_audit", {"payload_json": dict(payload)})["payload_json"], db.now_ist()))


def _audit_payload(frozen: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any] | None:
    cadence = result.get("cadence")
    if not isinstance(cadence, Mapping) or not result.get("resolved_as_of"):
        return None
    scope = frozen["scope"]
    pin = cadence.get("assertion_pin")
    if not isinstance(pin, Mapping):
        return None
    return {
        "run_id": frozen["run_id"], "manifest_fingerprint": frozen["manifest_fingerprint"],
        "snapshot": {"asset_id": scope["asset_id"], "snapshot_id": scope["snapshot_id"]},
        "table": scope["table"],
        "axis_column": {"table": scope["table"], "column": scope["period"]["column"]},
        "grouping": [{"table": scope["table"], "column": scope["facility_id"]["column"]}],
        "resolved_as_of": result["resolved_as_of"], "outcome": result["outcome"],
        "reason": result.get("reason"), "assertion_pin": dict(pin),
    }


def _comparison(result: Mapping[str, Any], grain: str) -> tuple[str, str]:
    outcome = result.get("outcome")
    if outcome in {"selected_pair_absent", "unavailable", "ambiguous", "error"}:
        reason = result.get("reason")
        return str(outcome), str(reason if reason in _CLOSED_REASONS else outcome)
    if outcome != "fulfilled":
        return "error", "malformed_projection"
    cadence = result.get("cadence")
    if not isinstance(cadence, Mapping):
        return "error", "malformed_projection"
    value = cadence.get("value")
    if not isinstance(value, Mapping):
        return "error", "malformed_projection"
    state = value.get("state")
    if state == "unknown":
        return "unknown", "completed_scan_unknown"
    if state in {"mixed", "irregular"}:
        return "non_regular", "advisory_non_comparable"
    interval = value.get("observed_interval_class")
    if state != "regular" or not isinstance(interval, Mapping):
        return "error", "malformed_projection"
    if dict(interval) in _EQUIVALENT.get(grain, ()):
        return "supports", "equivalent_observed_interval"
    known = {tuple(sorted(item.items())) for intervals in _EQUIVALENT.values() for item in intervals}
    # Day/week and any future interval class are structurally valid but are not
    # part of D06's closed reporting-grain comparison vocabulary.
    if tuple(sorted(interval.items())) not in known:
        return "non_regular", "advisory_non_comparable"
    return "different_observed_interval", "different_observed_interval"


def _validate_projection(result: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    """Reject malformed/cross-locator projections before comparison or audit."""
    allowed = {"projection_version", "request_identity", "outcome", "resolved_as_of", "reason", "cadence"}
    if not isinstance(result, Mapping) or set(result) - allowed or result.get("outcome") not in {"fulfilled", "selected_pair_absent", "unavailable", "ambiguous", "error"}:
        return False
    common = {"projection_version", "request_identity", "outcome", "resolved_as_of", "reason"}
    expected = common | ({"cadence"} if result["outcome"] == "fulfilled" else set())
    if set(result) != expected:
        return False
    if result.get("projection_version") != 1 or result.get("request_identity") != request_fingerprint(dict(request)):
        return False
    resolved = result.get("resolved_as_of")
    if not isinstance(resolved, Mapping) or set(resolved) != {"timestamp", "read_boundary_fingerprint"}:
        return False
    if not isinstance(resolved.get("timestamp"), str) or not resolved["timestamp"]:
        return False
    boundary = resolved.get("read_boundary_fingerprint")
    if not (isinstance(boundary, str) and len(boundary) == 64
            and all(char in "0123456789abcdef" for char in boundary.lower())):
        return False
    outcome, reason = result["outcome"], result.get("reason")
    if outcome in {"selected_pair_absent", "ambiguous"}:
        return reason is None
    if outcome == "unavailable":
        return reason in {"dependency_changed", "dependency_limit_exceeded", "source_missing", "source_integrity_failed", "payload_limit_exceeded", "evidence_limit_exceeded"}
    if outcome == "error":
        return reason in {"invalid_response", "deadline_exceeded", "db_busy", "projection_error"}
    if reason is not None:
        return False
    cadence = result.get("cadence")
    if (not isinstance(cadence, Mapping) or set(cadence) != {"resolution_state", "value", "assertion_pin"}
            or cadence.get("resolution_state") not in {"observed", "confirmed", "unknown"}):
        return False
    pin = cadence.get("assertion_pin", {})
    if not (isinstance(pin, Mapping) and set(pin) == {"artifact_id", "payload_hash", "dependency_fingerprint"}
            and isinstance(pin.get("artifact_id"), str)
            and all(isinstance(pin.get(key), str) and len(pin[key]) == 64 and all(c in "0123456789abcdef" for c in pin[key].lower()) for key in ("payload_hash", "dependency_fingerprint"))):
        return False
    value = cadence.get("value")
    if not isinstance(value, Mapping) or set(value) - {"state", "observed_interval_class"}:
        return False
    state = value.get("state")
    if state not in {"regular", "mixed", "irregular", "unknown"}:
        return False
    interval = value.get("observed_interval_class")
    if (state == "regular") != isinstance(interval, Mapping):
        return False
    if set(value) != ({"state", "observed_interval_class"} if state == "regular" else {"state"}):
        return False
    if interval is not None and (set(interval) != {"unit", "step"}
                                 or interval.get("unit") not in {"day", "week", "month", "quarter", "year"}
                                 or not isinstance(interval.get("step"), int) or isinstance(interval.get("step"), bool)
                                 or interval["step"] <= 0):
        return False
    if state == "unknown" and cadence["resolution_state"] != "unknown":
        return False
    if state != "unknown" and cadence["resolution_state"] == "unknown":
        return False
    return True


def observe(frozen: Mapping[str, Any], *, tenant_id: str,
            repository: Any | None = None,
            projection: Callable[..., Mapping[str, Any]] = project_materialized_dataset_structure,
            metric: Callable[..., None] = _metric,
            audit: Callable[[str, Mapping[str, Any]], None] = append_audit_event,
            flag_checked: bool = False, deadline: float | None = None) -> dict[str, str] | None:
    """Best-effort one-shot observation after D06 RunScope freeze.

    A disabled flag produces no seam, telemetry, or audit call.  Enabled calls
    return a tiny ephemeral result solely for tests/operational callers; D06
    intentionally ignores it.
    """
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + MAX_D06_DSC_CADENCE_SHADOW_WALL_MS / 1000
    # Flag lookup and audit share a tiny DB admission budget.  The timer is
    # deliberately established before the flag read, so a locked system DB
    # can only disable this non-authoritative work, never delay D06.
    db_deadline = started + _FLAG_DB_BUDGET_SECONDS
    if not isinstance(tenant_id, str) or not tenant_id:
        return None
    tenant = tenant_id
    if not flag_checked:
        try:
            remaining = min(db_deadline, deadline) - time.monotonic()
            if remaining <= 0:
                try:
                    metric(outcome="error", reason="timeout", elapsed_ms=round(
                        (time.monotonic() - started) * 1000), enabled=True)
                except Exception:
                    pass
                return {"outcome": "error", "reason": "timeout"}
            enabled = tenancy.is_flag_enabled(tenant, _FLAG, timeout=remaining)
            if not enabled:
                return None
        except Exception:
            return None
    # The same 100ms DB budget covers the projection admission.  A slow or
    # locked flag lookup disables advisory work; it never spills into D06.
    if min(db_deadline, deadline) <= time.monotonic():
        return None
    if expired := _deadline_result(deadline):
        try:
            metric(outcome="error", reason="timeout", elapsed_ms=round((time.monotonic() - started) * 1000), enabled=True)
        except Exception:
            pass
        return expired
    outcome, reason = "error", "adapter_exception"
    result: Mapping[str, Any] = {}
    try:
        scope = frozen["scope"]
        request = {"asset_id": scope["asset_id"], "snapshot_id": scope["snapshot_id"], "table": scope["table"],
            "predicate": "table.temporal/observed_cadence", "axis_id": f"column:{scope['period']['column']}",
            "axis_column": {"table": scope["table"], "column": scope["period"]["column"]},
            "grouping": [{"table": scope["table"], "column": scope["facility_id"]["column"]}],
            "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
        result = projection(
            request, deadline=min(db_deadline, deadline),
            **({"repository": repository} if repository is not None else {}),
        )
        elapsed = round((time.monotonic() - started) * 1000)
        if elapsed > MAX_D06_DSC_CADENCE_SHADOW_WALL_MS:
            outcome, reason = "error", "timeout"
        else:
            outcome, reason = (_comparison(result, scope["reporting_grain"])
                               if _validate_projection(result, request) else ("error", "malformed_projection"))
    except Exception:
        outcome, reason = "error", "adapter_exception"
    elapsed = round((time.monotonic() - started) * 1000)
    # Every fault is contained, including telemetry transport faults.  Metric
    # precedes audit so an audit failure cannot leak identifiers to general logs.
    if expired := _deadline_result(deadline):
        return expired
    try:
        metric(outcome=outcome, reason=reason, elapsed_ms=elapsed, enabled=True)
    except Exception:
        outcome, reason = "error", "telemetry_error"
    # `metric` is caller-supplied transport and may block.  Re-check before
    # any identifier-bearing audit work can begin.
    if expired := _deadline_result(deadline):
        return expired
    if min(db_deadline, deadline) <= time.monotonic():
        return None
    try:
        payload = _audit_payload(frozen, result)
        if payload is not None:
            audit(tenant, payload, deadline=min(db_deadline, deadline))
    except Exception:
        # An audit failure stays fail-open and is represented only by the
        # already-sanitized operational outcome category.
        outcome, reason = "error", "telemetry_error"
    # Audit is likewise transport: a slow sink must not be reported as a
    # successful shadow observation after the wall deadline.
    if expired := _deadline_result(deadline):
        return expired
    return {"outcome": outcome, "reason": reason}


def schedule(frozen: Mapping[str, Any], *, tenant_id: str) -> None:
    """Launch a daemon, single-flight shadow worker without delaying D06."""
    if not isinstance(tenant_id, str) or not tenant_id:
        return
    try:
        run_id = frozen["run_id"]
    except (KeyError, TypeError):
        return
    if not isinstance(run_id, str) or not run_id:
        return
    key = (tenant_id, run_id, _VERSION)
    now = time.monotonic()
    with _IN_FLIGHT_LOCK:
        for stale, expiry in tuple(_IN_FLIGHT.items()):
            if expiry <= now:
                _IN_FLIGHT.pop(stale, None)
        if key in _IN_FLIGHT:
            return
        _IN_FLIGHT[key] = now + MAX_D06_DSC_CADENCE_SHADOW_WALL_MS / 1000
    try:
        copied = deepcopy({key: frozen[key] for key in ("run_id", "manifest_fingerprint", "scope")})
    except Exception:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.pop(key, None)
        return
    released = threading.Event()
    def release_once() -> None:
        if not released.is_set():
            released.set()
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.pop(key, None)
    # A permanently stalled flag/database provider must not disable future
    # advisory work globally. The worker remains daemon/fail-open.
    watchdog = threading.Timer(MAX_D06_DSC_CADENCE_SHADOW_WALL_MS / 1000, release_once)
    watchdog.daemon = True
    def worker() -> None:
        try:
            observe(copied, tenant_id=tenant_id)
        finally:
            watchdog.cancel()
            release_once()
    try:
        watchdog.start()
        threading.Thread(target=worker, name="d06-dsc-shadow", daemon=True).start()
    except Exception:
        watchdog.cancel()
        release_once()
