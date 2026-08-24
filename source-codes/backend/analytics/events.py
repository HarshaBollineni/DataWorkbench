"""ANL-03's single append-only usage-event writer."""
from __future__ import annotations

import json
import uuid
from typing import Any

import system_db as s

from .ids import FAMILIES, public_id, typed_id

# Each vocabulary member names the measure that consumes it.  Keeping this
# closed map beside the writer prevents ad-hoc event accretion (ANL-04).
EVENT_TYPES: dict[str, str] = {
    "asset_created": "overall usage, usage per user, usage over time, reuse depth denominator",
    "asset_selected": "reuse depth numerator and usage per user",
    "alias_renamed": "overall usage",
    "snapshot_added": "snapshot cadence and usage over time",
    "snapshot_ready": "time-to-ready paired with upload_started",
    "upload_started": "time-to-ready and abandonment denominator",
    "upload_step_reached": "abandonment points",
    "upload_abandoned": "abandonment points",
    "type_override": "inferred-type override rate",
    "schema_warning_overridden": "schema-warning override rate",
    "version_created": "replacement/rerun analysis",
    "version_superseded": "replacement/rerun analysis",
    "version_restored": "restore frequency",
    "dictionary_version_created": "dictionary coverage trend",
    "dictionary_bound": "dictionary coverage trend",
    "refresh_requested": "overall usage",
    "diagnostic_run_started": "test and diagnostic preferences, replacement rerun rate",
    "finding_disposed": "finding disposition split",
    "use_case_set": "use-case frequency",
    "catalogue_viewed": "overall usage",
    "version_diff_viewed": "overall usage",
}


def _actor(value: Any) -> str:
    if value is None or not str(value).strip():
        raise ValueError("usage event actor is required")
    return str(value).strip()


def record_event(*, event_type: str, actor: str, at: str, object_type: str,
                 object_id: str, workflow_context: str | None = None,
                 detail: dict[str, Any] | None = None,
                 conn=None) -> dict[str, Any]:
    """Insert exactly one complete event and nothing else.

    Required fields are validated before the INSERT, so a malformed call
    cannot leave a partial event.  The only deletion exception is the
    explicitly named ``delete_for_factory_reset`` path below.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown usage event type: {event_type!r}")
    actor = _actor(actor)
    for name, value in (("at", at), ("object_type", object_type), ("object_id", object_id)):
        if value is None or not str(value).strip():
            raise ValueError(f"usage event {name} is required")
    if object_type not in FAMILIES:
        raise ValueError(f"usage event object_type must be one of {FAMILIES}")
    typed = typed_id(object_type, str(object_id))
    row = {
        "event_id": f"UE:{uuid.uuid4().hex}", "event_type": event_type,
        "actor": actor, "at": str(at), "object_type": object_type,
        "object_id": typed, "workflow_context": workflow_context,
        "detail_json": detail or {},
    }
    if conn is not None:
        data = json.dumps(row["detail_json"], sort_keys=True)
        conn.execute(
            "INSERT INTO usage_events "
            "(event_id,event_type,actor,at,object_type,object_id,workflow_context,detail_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (row["event_id"], row["event_type"], row["actor"], row["at"],
             row["object_type"], row["object_id"], row["workflow_context"], data),
        )
    else:
        s.insert("usage_events", row)
    return row


def event_object_id(object_type: str, internal_id: str, **kwargs: Any) -> str:
    """Small call-site helper; it never allocates a sixth object family."""
    return public_id(object_type, internal_id, **kwargs)


def delete_for_factory_reset(conn) -> int:
    """The sole ANL-03 deletion exception.

    Factory reset removes the asset objects and legitimately removes the usage
    history describing those deleted objects.  Keeping this operation in a
    named function makes the exception auditable and prevents a normal flow
    from acquiring UPDATE/DELETE access to the event log.
    """
    cur = conn.execute("DELETE FROM usage_events")
    return cur.rowcount
