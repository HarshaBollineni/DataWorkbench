"""backend/assets/history.py — 0.5.0 Step 3c (ADM-06/07): the read-only
aggregation behind Admin's version-history endpoint
(``GET /api/admin/assets/{asset_id}/history``).

ADM-06 — "for any asset, show its full version sequence and, within each
version, its snapshots (active AND superseded), each with timestamp, actor,
intent, and a one-line summary of what changed." This module aggregates
exactly that, from tables S3a/S3b already populate:

    dq_asset_versions + dq_items + dq_asset_events

The one-line ``change_summary`` on every snapshot and every version is read
VERBATIM from the corresponding ``dq_asset_events.summary`` string —
``assets.service._write_event`` already writes that summary in human
language at WRITE time (M-2). This module never composes prose from a
status/intent enum at READ time; it only aggregates and orders rows that
already carry human-readable text.

ADM-07 / R-08 — the two factory-reset actions (surgical / wipe) are read
straight off ``transaction_log WHERE event='factory_reset'`` (P-10's
payload: ``grade``, ``deleted`` counts, ``id_epoch``). This module returns
those counts UNTRANSLATED (raw table-name keys) — turning them into human
labels is the frontend's job, via the ONE existing map
(``ui/src/lib/resetLabels.js`` / ``resetLabels.json``, built in Step 2).
Re-labelling them here in Python would be a SECOND label map that the plan's
Task 3.4 explicitly forbids ("reuse Step 2's label map — do NOT build a
second one"). Resets are asset-agnostic (a reset is a whole-product event,
not scoped to one family) so every reset row is returned regardless of
which asset's history is being viewed — this is what lets ADM-07's epoch
boundary render "inline, in one chronological list with the version
events" for ANY asset, including one created after the boundary.
"""
from __future__ import annotations

from typing import Any

import system_db as s

from . import service as assets_service


def _events_for(asset_id: str, *, version_no: int | None = None,
                snapshot_id: str | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {"asset_id": asset_id}
    if version_no is not None:
        filters["version_no"] = version_no
    if snapshot_id is not None:
        filters["snapshot_id"] = snapshot_id
    if event_type is not None:
        filters["event_type"] = event_type
    return sorted(s.query("dq_asset_events", **filters), key=lambda e: e.get("at") or "")


def _snapshot_change_summary(asset_id: str, snapshot_id: str) -> str:
    """The snapshot's one-line 'what changed' (ADM-06) — verbatim from the
    ``snapshot_added`` event ``assets.service.add_snapshot`` wrote for this
    exact ``snapshot_id`` (at most one such event exists per snapshot)."""
    events = _events_for(asset_id, snapshot_id=snapshot_id, event_type="snapshot_added")
    if events:
        return events[-1]["summary"]
    return "Snapshot recorded."


def _version_change_summary(asset_id: str, version_no: int) -> str | None:
    """The version's one-line 'what changed' — the event that brought this
    generation into being: ``asset_created`` for v1, ``version_created`` for
    a full replacement, or (if it was reactivated after being superseded)
    the most recent ``version_restored`` naming it. Verbatim, same
    discipline as the snapshot-level summary; never composed here."""
    candidates: list[dict[str, Any]] = []
    for event_type in ("asset_created", "version_created", "version_restored"):
        candidates.extend(_events_for(asset_id, version_no=version_no, event_type=event_type))
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.get("at") or "")
    return candidates[-1]["summary"]


def _reference_schema_summary(reference_schema_json: Any) -> str:
    """A short, human-language description of a version's reference schema
    — never the raw JSON, never a bare column-count with no sentence around
    it. ``None``/empty is itself a legitimate state (a version created but
    not yet confirmed against an uploaded file — AST-07) and is reported
    honestly, not papered over."""
    tables = (reference_schema_json or {}).get("tables") if isinstance(reference_schema_json, dict) else None
    if not tables:
        return "No reference schema confirmed yet."
    table_count = len(tables)
    column_count = sum(
        (t.get("column_count") if isinstance(t, dict) and t.get("column_count") is not None
         else len((t or {}).get("columns") or []))
        for t in tables.values()
    )
    if table_count == 1:
        return f"{column_count} column(s) confirmed."
    return f"{table_count} tables, {column_count} column(s) total confirmed."


def asset_history(asset_id: str) -> dict[str, Any]:
    """ADM-06/07's endpoint payload. Raises ``ValueError`` for an unknown
    asset (``routers/admin.py`` maps that to HTTP 404) — the same
    "no such X" convention every other function in ``assets.service`` uses.

    Shape:
        {asset: {...},
         versions: [{version_no, status, reference_schema_summary,
                     created_at, created_by, change_summary,
                     snapshots: [{snapshot_id, snapshot_label, start_date,
                                  end_date, intent, snapshot_status,
                                  uploaded_at, uploaded_by,
                                  change_summary}]}],
         resets: [{at, actor, grade, deleted, id_epoch}]}
    """
    asset = assets_service.require_asset(asset_id)

    version_rows = s.query("dq_asset_versions", asset_id=asset_id, order_by="version_no")
    versions: list[dict[str, Any]] = []
    for v in version_rows:
        snapshot_rows = [r for r in s.query("dq_items", dataset_family_id=asset_id,
                                            version_no=v["version_no"])]
        # AST-11's ordering, applied WITHIN the version — deliberately over
        # active-and-superseded alike: this view is explicitly the one place
        # superseded snapshots are meant to stay visible (never routed
        # through reads.ordered_snapshots(only_active=True), which is the
        # AST-12 test-configuration picker guard, a different concern).
        if asset["time_basis"] == "period":
            snapshot_rows.sort(key=lambda r: (r.get("start_date") or "", r.get("created_at") or ""))
        else:
            snapshot_rows.sort(key=lambda r: r.get("created_at") or "")

        snapshots = [{
            "snapshot_id": row["item_id"],
            "snapshot_label": row.get("snapshot_label"),
            "start_date": row.get("start_date"),
            "end_date": row.get("end_date"),
            "intent": row.get("intent"),
            "snapshot_status": row.get("snapshot_status"),
            "uploaded_at": row.get("created_at"),
            "uploaded_by": row.get("uploaded_by"),
            "change_summary": _snapshot_change_summary(asset_id, row["item_id"]),
        } for row in snapshot_rows]

        versions.append({
            "version_no": v["version_no"],
            "status": v["status"],
            "reference_schema_summary": _reference_schema_summary(v.get("reference_schema_json")),
            "created_at": v.get("created_at"),
            "created_by": v.get("created_by"),
            "change_summary": _version_change_summary(asset_id, v["version_no"]),
            "snapshots": snapshots,
        })

    resets = [{
        "at": row.get("ts"),
        "actor": row.get("actor"),
        "grade": (row.get("payload") or {}).get("grade"),
        "deleted": (row.get("payload") or {}).get("deleted") or {},
        "id_epoch": (row.get("payload") or {}).get("id_epoch") or {},
    } for row in s.query("transaction_log", event="factory_reset", order_by="id")]

    return {
        "asset": {
            "asset_id": asset["asset_id"],
            "system_id": asset["system_id"],
            "alias": asset["alias"],
            "display_name": asset["display_name"],
            "kind": asset["kind"],
            "time_basis": asset["time_basis"],
            "current_version_no": asset["current_version_no"],
            "lifecycle_status": asset.get("lifecycle_status"),
            "created_at": asset.get("created_at"),
            "created_by": asset.get("created_by"),
        },
        "versions": versions,
        "resets": resets,
    }
