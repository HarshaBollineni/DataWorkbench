"""backend/assets/reads.py — read-only query helpers over the 0.5.0 asset
model (task 3.6). No writes beyond what ``system_db._backfill_asset_model``
already does at migration time.

These are NEW read models beside ``ai/v2/service.py``'s existing
``list_items``, not a replacement for it (plan operating rule 1 / rule 7's
"list_items itself keeps working for every 0.4.0 caller"): both coexist.
"""
from __future__ import annotations

from typing import Any

import system_db as s


def list_sourcing_drafts(owner: str, tenant_id: str) -> list[dict[str, Any]]:
    """Return every unresolved draft the principal must resume or discard."""
    drafts = [
        row for row in s.query("dq_items", order_by="updated_at DESC, created_at DESC")
        if row.get("sourcing_draft_state") in {"active", "recovery"}
        and row.get("sourcing_tenant_id") == tenant_id
        and row.get("sourcing_owner") in {owner, "__legacy__"}
    ]
    result: list[dict[str, Any]] = []
    for draft in drafts:
        asset = s.query_one("dq_assets", asset_id=draft.get("dataset_family_id")) or {}
        files = s.query("dq_item_files", item_id=draft["item_id"])
        data_files = sorted(
            (row for row in files if row.get("role") == "data"),
            key=lambda row: row.get("completed_at") or "",
        )
        dictionary_files = sorted(
            (row for row in files if row.get("role") == "dictionary"),
            key=lambda row: row.get("completed_at") or "",
        )
        result.append({
            **asset,
            "kind": draft.get("kind") or asset.get("kind"),
            "resumable": True,
            "resume_snapshot_id": draft["item_id"],
            "resume_ingest_status": draft.get("ingest_status"),
            "resume_intent": draft.get("intent"),
            "resume_file_name": data_files[-1].get("filename") if data_files else None,
            "resume_dictionary_file_name": (
                dictionary_files[-1].get("filename") if dictionary_files else None
            ),
            "resume_has_data": bool(data_files),
            "draft_state": draft.get("sourcing_draft_state"),
            "draft_updated_at": draft.get("updated_at") or draft.get("created_at"),
        })
    return result


def list_assets(kind: str | None = None,
                exclude_lifecycle: list[str] | None = None,
                sourcing_owner: str | None = None,
                sourcing_tenant_id: str | None = None) -> list[dict[str, Any]]:
    """SRC-05's basic data source — one row per ``dq_assets`` row, enriched
    with its active-snapshot count and its most recent upload timestamp.

    ``kind``: filter to 'database' or 'dataset'; ``None`` returns both.
    ``exclude_lifecycle``: asset rows whose ``lifecycle_status`` is in this
    list are omitted entirely (the caller decides which statuses to exclude
    — e.g. SRC-04's landing-page view excludes ``complete``/``archived``/
    ``requires_reupload``, while SRC-13's upload-flow picker does not; that
    parameterisation is exactly why this function takes the list as an
    argument rather than hardcoding one exclusion set — P-12's "no default"
    discipline for the dropdown this feeds).
    """
    rows = s.query("dq_assets", kind=kind) if kind else s.query("dq_assets")
    exclude = set(exclude_lifecycle or [])
    out: list[dict[str, Any]] = []
    for asset in rows:
        if asset.get("lifecycle_status") in exclude:
            continue
        snapshots = s.query("dq_items", dataset_family_id=asset["asset_id"])
        active = [r for r in snapshots if r.get("snapshot_status") == "active"]
        superseded = [r for r in snapshots if r.get("snapshot_status") == "superseded"]
        upload_times = [r["created_at"] for r in snapshots if r.get("created_at")]
        current_active = [r for r in active if r.get("version_no") == asset.get("current_version_no")] or active
        table_columns: dict[str, int] = {}
        for snapshot in current_active:
            for table in s.query("dq_item_tables", item_id=snapshot["item_id"]):
                table_columns[table["table_name"]] = max(
                    table_columns.get(table["table_name"], 0), int(table.get("col_count") or 0)
                )
        has_ready_snapshot = any(
            r.get("ingest_status") == "ready" for r in active
        )
        unfinished = [r for r in active if (
            r.get("ingest_status") != "ready"
            or str(r.get("snapshot_label") or "").startswith("__staged_")
        ) and (
            sourcing_owner is None
            or (
                r.get("sourcing_draft_state") in {"active", "recovery"}
                and r.get("sourcing_tenant_id") == sourcing_tenant_id
                and r.get("sourcing_owner") in {sourcing_owner, "__legacy__"}
            )
        )]
        unfinished.sort(key=lambda row: row.get("updated_at") or row.get("created_at") or "")
        resumable = unfinished[-1] if unfinished else None
        resume_files = (s.query("dq_item_files", item_id=resumable["item_id"])
                        if resumable else [])
        resume_data_files = [row for row in resume_files if row.get("role") == "data"]
        resume_data_files.sort(key=lambda row: row.get("completed_at") or "")
        resume_dictionary_files = [row for row in resume_files if row.get("role") == "dictionary"]
        resume_dictionary_files.sort(key=lambda row: row.get("completed_at") or "")
        table_count = len(table_columns)
        column_count = sum(table_columns.values())
        if asset["kind"] == "database":
            count_label = f"{table_count} tables · {column_count} columns"
        else:
            count_label = f"{column_count} columns"
        out.append({
            **asset,
            "snapshot_count": len(active),
            "superseded_snapshot_count": len(superseded),
            "superseded_versions": sorted({r.get("version_no") for r in superseded if r.get("version_no") is not None}),
            "active_snapshot_count": len(active),
            "last_upload_at": max(upload_times) if upload_times else None,
            "last_upload_date": max(upload_times) if upload_times else None,
            "table_count": table_count,
            "column_count": column_count,
            "kind_count_label": count_label,
            "selectable": has_ready_snapshot,
            "has_eligible_snapshot": has_ready_snapshot,
            "resumable": bool(resumable),
            "resume_snapshot_id": resumable.get("item_id") if resumable else None,
            "resume_ingest_status": resumable.get("ingest_status") if resumable else None,
            "resume_intent": resumable.get("intent") if resumable else None,
            "resume_file_name": (resume_data_files[-1].get("filename")
                                 if resume_data_files else None),
            "resume_dictionary_file_name": (resume_dictionary_files[-1].get("filename")
                                            if resume_dictionary_files else None),
            "resume_has_data": bool(resume_data_files),
        })
    return out


def ordered_snapshots(asset_id: str, only_active: bool = True) -> list[dict[str, Any]]:
    """AST-11 — order a family's snapshots by ``start_date`` when the
    asset's ``time_basis='period'``, by ``created_at`` when it is 'none'.

    AST-22 guarantees a single, fixed time basis per asset for its whole
    life, which is supposed to make a set mixing dated and undated snapshots
    impossible BY CONSTRUCTION. That guarantee is treated as an invariant to
    PROVE, not merely trust (plan operating rule 6): if this ever sees a
    snapshot whose ``start_date`` is inconsistent with the asset's declared
    basis — a 'period' asset with an undated row, or a 'none' asset with a
    dated one — it raises rather than silently interleaving or guessing a
    position. See ``backend/tests/test_asset_reads.py`` for the forced-state
    bites-check (both directions: the guard firing, and the guard NOT firing
    on a well-formed set).
    """
    asset = s.query_one("dq_assets", asset_id=asset_id)
    if asset is None:
        raise ValueError(f"No such asset: {asset_id!r}")

    rows = s.query("dq_items", dataset_family_id=asset_id)
    if only_active:
        rows = [r for r in rows if r.get("snapshot_status") == "active"]

    time_basis = asset["time_basis"]
    if time_basis == "period":
        undated = [r["item_id"] for r in rows if not r.get("start_date")]
        if undated:
            raise ValueError(
                f"Asset {asset_id!r} is time_basis='period' but snapshot(s) {undated} have no "
                "start_date. AST-22 guarantees every snapshot of a period-basis asset carries "
                "one; refusing to order a set that mixes dated and undated snapshots rather than "
                "guessing a position for the undated one(s)."
            )
        rows.sort(key=lambda r: (r["start_date"], r.get("created_at") or ""))
    elif time_basis == "none":
        dated = [r["item_id"] for r in rows if r.get("start_date")]
        if dated:
            raise ValueError(
                f"Asset {asset_id!r} is time_basis='none' but snapshot(s) {dated} carry a "
                "start_date. AST-22 guarantees a single fixed time basis per asset; refusing to "
                "order a set that mixes dated and undated snapshots rather than silently "
                "interleaving them."
            )
        rows.sort(key=lambda r: r.get("created_at") or "")
    else:
        raise ValueError(f"Asset {asset_id!r} has an unrecognised time_basis: {time_basis!r}")
    return rows


# AST-13's stored-record field list, typed out literally so a reviewer can
# diff this function's output against the requirement one name at a time.
AST_13_FIELDS = (
    "asset_id", "asset_name", "version_no", "snapshot_id", "snapshot_label",
    "file_name", "start_date", "end_date", "has_time_period", "period_column",
    "column_type_map", "row_count", "column_count", "status", "intent",
    "schema_override_flag", "uploaded_by", "uploaded_at",
)


def snapshot_read_model(item_id: str) -> dict[str, Any]:
    """AST-13's stored record for one snapshot, exposed one-to-one under
    AST-13's OWN field names (P-06) — not the internal column names.

    The two deliberate renames: ``snapshot_id`` <- ``item_id`` and
    ``uploaded_at`` <- ``created_at``. There is no duplicate column for
    either (P-06) — ``item_id`` IS the snapshot id, ``created_at`` IS the
    upload timestamp. ``status`` here is AST-13's selectability axis
    (internally ``dq_items.snapshot_status`` — P-07's new axis, distinct
    from the item's lifecycle ``status`` column, which this function does
    not expose under this name at all, precisely to avoid that collision).
    """
    row = s.query_one("dq_items", item_id=item_id)
    if row is None:
        raise ValueError(f"No such snapshot: {item_id!r}")
    family_id = row.get("dataset_family_id")
    asset = s.query_one("dq_assets", asset_id=family_id) if family_id else None
    return {
        "asset_id": family_id,
        "asset_name": asset["display_name"] if asset else row.get("name"),
        "version_no": row.get("version_no"),
        "snapshot_id": row["item_id"],
        "snapshot_label": row.get("snapshot_label"),
        "file_name": row.get("file_name"),
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "has_time_period": bool(row.get("has_time_period")),
        "period_column": row.get("period_column"),
        "column_type_map": row.get("column_type_map_json"),
        "row_count": row.get("row_count"),
        "column_count": row.get("column_count"),
        "status": row.get("snapshot_status"),
        "intent": row.get("intent"),
        "schema_override_flag": bool(row.get("schema_override_flag")),
        "uploaded_by": row.get("uploaded_by"),
        "uploaded_at": row.get("created_at"),
    }
