"""ANL-06 read model for the inventory catalogue (capture only)."""
from __future__ import annotations

import json
from typing import Any

import system_db as s

from assets import reads
from . import measures


_STATE_LABELS = {"yes": "Dictionary bound", "thin": "Partially documented", "absent": "No dictionary bound"}
_STATUS_LABELS = {
    "sourcing": "In progress", "profiled": "Profiled", "complete": "Complete",
    "archived": "Archived", "requires_reupload": "Needs re-upload", "issues": "Needs review",
}


def human_state(value: str | None) -> str:
    return _STATE_LABELS.get(value or "absent", "Dictionary state unavailable")


def human_status(value: str | None) -> str:
    return _STATUS_LABELS.get(value or "", "Status unavailable")


def _completion(snapshot: dict[str, Any]) -> dict[str, Any]:
    item_id = snapshot["item_id"]
    asset = s.query_one("dq_assets", asset_id=snapshot.get("dataset_family_id")) or {}
    rows = s.query("variable_inventory", item_id=item_id, order_by="table_name, column_name")
    columns = [{"table": row.get("table_name"), "column": row.get("column_name"),
                "confirmed_type": row.get("classification") or "text"} for row in rows]
    overrides = s.query("dq_asset_events", snapshot_id=item_id, event_type="schema_override", order_by="at")
    processed = s.query("dq_asset_events", snapshot_id=item_id, event_type="snapshot_processed", order_by="at")
    schema_events = s.query("dq_asset_events", snapshot_id=item_id, event_type="schema_checked", order_by="at")
    schema_detail = (schema_events[-1].get("detail_json") or {}) if schema_events else {}
    return {
        "asset_name": asset.get("display_name") or snapshot.get("name"),
        "snapshot_label": snapshot.get("snapshot_label"),
        "period_covered": ({"start": snapshot.get("start_date"), "end": snapshot.get("end_date")}
                            if snapshot.get("has_time_period") else "not applicable"),
        "rows_loaded": snapshot.get("row_count"), "columns": columns,
        "schema_change_applied": ((schema_detail.get("messages") or [])
                                   if snapshot.get("intent") != "full_replacement" else
                                   (["reference schema replaced"] + (schema_detail.get("messages") or []))
                                   if processed else "none"),
        "snapshots_superseded": "retained snapshots are listed in the catalogue",
        "warnings_overridden": [
            (row.get("detail_json") or {}).get("overridden_warnings") or [] for row in overrides
        ],
    }


def _usage_summary(system_id: str) -> dict[str, Any]:
    rows = [row for row in s.query("usage_events", order_by="at")
            if row.get("workflow_context") == system_id or row.get("object_id") == system_id]
    return {
        "times_selected": sum(row["event_type"] == "asset_selected" for row in rows),
        "snapshots_added": sum(row["event_type"] == "snapshot_added" for row in rows),
        "last_activity": max((row.get("at") for row in rows), default=None),
        "restore_count": sum(row["event_type"] == "version_restored" for row in rows),
    }


def _asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
    all_snapshots = s.query("dq_items", dataset_family_id=asset["asset_id"], order_by="created_at")
    active = [row for row in all_snapshots if row.get("snapshot_status") == "active"]
    dictionary_state = next((row.get("dictionary_state") for row in reversed(all_snapshots)
                             if row.get("dictionary_state")), "absent")
    versions = []
    for version in s.query("dq_asset_versions", asset_id=asset["asset_id"], order_by="version_no"):
        snapshots = [row for row in all_snapshots if row.get("version_no") == version.get("version_no")]
        versions.append({
            **version, "status_label": "Current version" if version.get("status") == "current" else "Superseded version",
            "snapshots": [{**row,
                           "snapshot_status_label": "Active" if row.get("snapshot_status") == "active" else "Superseded (retained for audit)",
                           "completion_summary": _completion(row)} for row in snapshots],
        })
    return {
        **asset,
        "kind_label": "Database" if asset.get("kind") == "database" else "Dataset",
        "current_version": asset.get("current_version_no"),
        "active_snapshot_count": len(active),
        "superseded_snapshot_count": len(all_snapshots) - len(active),
        "dictionary_state": dictionary_state,
        "dictionary_state_label": human_state(dictionary_state),
        "lifecycle_status_label": human_status(asset.get("lifecycle_status")),
        "last_upload": max((row.get("created_at") for row in all_snapshots if row.get("created_at")), default=None),
        "versions": versions,
        "usage_summary": _usage_summary(asset.get("system_id")),
        "diff_mount": {"asset_id": asset["asset_id"], "versions": [v["version_no"] for v in versions]},
    }


def list_catalogue() -> list[dict[str, Any]]:
    # The catalogue is intentionally one row per asset, not one row per
    # snapshot; its expanded model is the one place retained snapshots show.
    return [_asset_payload(asset) for asset in s.query("dq_assets", order_by="created_at")]


def get_catalogue(asset_id: str) -> dict[str, Any]:
    asset = s.query_one("dq_assets", asset_id=asset_id)
    if not asset:
        raise ValueError(f"No such asset: {asset_id!r}")
    return _asset_payload(asset)
