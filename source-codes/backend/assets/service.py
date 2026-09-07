"""backend/assets/service.py — 0.5.0 Step 3b: the asset service layer.

``create_asset`` / ``add_snapshot`` / ``supersede_version_set`` /
``restore_version_set`` / ``rename_alias`` — AST-01..09, AST-13, AST-22,
C-40, D-25, P-05, P-08, P-11.

This is the module ``ai/v2/service.py``'s rewritten ``create_item`` /
``reupload_item`` delegate into (P-14): 0.4.0's ``reupload_item`` created a
SIBLING ``dq_items`` row — its own ``item_id``, its own name, forked off
into what LOOKED like the same family but behaved like an independent
dataset. That is the defect ``Data_Sourcing_3`` reports and C-40/D-25
correct: a re-upload is now a SNAPSHOT of the SAME asset. The asset's
identity (``system_id`` / ``display_name`` / ``asset_id``) never forks,
never changes, and every existing reference to it keeps resolving.

Physical seam (AST-10, P-04, unchanged from S3a): ``dq_assets.asset_id`` IS
``dq_items.dataset_family_id`` — a value THIS module mints fresh for every
new asset (never equal to any snapshot's own ``item_id`` — see
``test_asset_reads.py``'s own fixtures for the same convention). PLT-02's
``dq_diagnostics/delivery.py`` is never modified (R-01) and is called here
exactly as any other caller would.

Superseding/restoring are SET operations (P-11): every function here that
mutates more than one snapshot's ``snapshot_status`` does so as ONE UPDATE
statement per table inside ONE transaction (a single ``with
system_db.get_conn()`` block, one ``commit()``) — never a per-snapshot
Python loop, and never split across two caller-visible steps that could
leave a half-superseded set observable in between.
"""
from __future__ import annotations

import uuid
from typing import Any

import system_db as s

from . import identity

# UPL-17/AST-06 — the only three snapshot intents the model recognises.
# 'fresh' is the asset's very first snapshot (no version bump — there is
# nothing yet to bump from); 'add_period' adds alongside the existing set
# with NO version bump (AST-06); 'full_replacement' bumps the version and
# supersedes every previously-active snapshot of the old version as one set
# (AST-08/AST-14).
INTENTS: tuple[str, ...] = ("fresh", "add_period", "full_replacement")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def require_asset(asset_id: str) -> dict[str, Any]:
    """Same idiom as ``assets.reads.ordered_snapshots``'s own guard — raises
    ``ValueError`` (not ``KeyError``) for an unknown asset, matching the
    established convention in this package."""
    asset = s.query_one("dq_assets", asset_id=asset_id)
    if asset is None:
        raise ValueError(f"No such asset: {asset_id!r}")
    return asset


def _write_event(asset_id: str, event_type: str, summary: str, *,
                 actor: str | None = None, version_no: int | None = None,
                 snapshot_id: str | None = None, detail: dict | None = None) -> None:
    """ADM-06/07's audit-of-record (P-08) — ``summary`` is written in human
    language HERE, at write time, never composed from enum values later at
    read time (M-2)."""
    s.insert("dq_asset_events", {
        "event_id": _id("evt"), "asset_id": asset_id, "version_no": version_no,
        "snapshot_id": snapshot_id, "event_type": event_type, "actor": actor,
        "at": s.now_ist(), "summary": summary, "detail_json": detail,
    })


def _usage(event_type: str, asset_id: str, object_type: str, internal_id: str,
           actor: str | None = None, detail: dict | None = None, **kwargs: Any) -> None:
    """Dual-stream helper: audit history plus measurement log stay aligned."""
    from analytics.events import event_object_id, record_event  # noqa: PLC0415
    asset = s.query_one("dq_assets", asset_id=asset_id) or {}
    record_event(event_type=event_type, actor=actor or "system", at=s.now_ist(),
                 object_type=object_type,
                 object_id=event_object_id(object_type, internal_id, **kwargs),
                 workflow_context=asset.get("system_id"), detail=detail)


# ---------------------------------------------------------------------------
#  create_asset
# ---------------------------------------------------------------------------

def create_asset(kind: str, alias: str, time_basis: str, actor: str | None = None) -> dict[str, Any]:
    """AST-01/02/03/22 — a brand-new asset. Allocates a fresh ``system_id``
    (``assets.identity.allocate``), validates ``alias`` INLINE-STRICT
    (``assets.identity.validate_alias`` — the AST-02 "as typed" rule, never
    relaxed here), and inserts:

    1. one ``dq_assets`` row (``current_version_no=1``);
    2. one ``dq_asset_versions`` v1 row with ``reference_schema_json=NULL``
       — populated once the first snapshot's confirmed type map exists,
       which is Step 4's job, not this one's;
    3. one ``asset_created`` ``dq_asset_events`` row.

    Creates NO snapshot (no ``dq_items`` row) — that is ``add_snapshot``'s
    job, called separately with ``intent='fresh'``. Two assets may share an
    alias (AST-03: the SYSTEM ID carries the uniqueness burden, not the
    alias) — ``ux_dq_assets_system_id`` is the last-line-of-defence unique
    index on the one thing that must never collide.

    ``time_basis`` is REQUIRED and written exactly once, here: no other
    function in this module ever writes ``dq_assets.time_basis`` again
    (AST-22/D-30 — immutable, including across a Full replacement).
    """
    if kind not in {"database", "dataset"}:
        raise ValueError("kind must be 'database' or 'dataset'")
    if time_basis not in {"period", "none"}:
        raise ValueError("time_basis must be 'period' or 'none'")
    alias = identity.validate_alias(alias)

    asset_id = _id("asset")
    scope = identity.scope_for_kind(kind)
    system_id = identity.allocate(scope)
    display_name = identity.compose_display_name(system_id, alias)
    now = s.now_ist()

    s.insert("dq_assets", {
        "asset_id": asset_id, "system_id": system_id, "alias": alias,
        "display_name": display_name, "kind": kind, "time_basis": time_basis,
        "current_version_no": 1, "lifecycle_status": "sourcing",
        "target_variable": None, "use_case": None,
        "current_dictionary_version_id": None,
        "artifact_origin": s.current_artifact_origin(),
        "created_by": actor, "created_at": now, "updated_at": now,
    })
    version_id = identity.allocate("version")
    s.insert("dq_asset_versions", {
        "version_id": version_id, "asset_id": asset_id, "version_no": 1,
        "status": "current", "reference_schema_json": None,
        "created_from_snapshot_id": None, "supersedes_version_no": None,
        "restored_from_version_no": None, "created_by": actor,
        "created_at": now, "superseded_at": None,
    })
    _write_event(asset_id, "asset_created",
                f"Asset {display_name} created as a new {kind}.",
                actor=actor, version_no=1)
    _usage("asset_created", asset_id, "asset", asset_id, actor)
    return require_asset(asset_id)


# ---------------------------------------------------------------------------
#  add_snapshot
# ---------------------------------------------------------------------------

def add_snapshot(asset_id: str, intent: str, actor: str | None = None,
                 **period_fields: Any) -> dict[str, Any]:
    """FNC-01's single most-affected function (C-40/D-25/AST-05/06/08/10/13).

    Inserts ONE new ``dq_items`` row that is a snapshot of the SAME asset:
    ``dataset_family_id = asset_id`` (the seam, unchanged), ``delivery_seq``
    from the EXISTING ``dq_diagnostics.delivery.register_delivery`` (never
    modified — R-01), ``name = asset.display_name`` (P-05),
    ``snapshot_status='active'``. ``version_no`` is the asset's CURRENT
    version for ``intent in ('fresh', 'add_period')`` — AST-06: neither
    bumps the version — or ``current + 1`` for ``'full_replacement'``, which
    ALSO inserts a new ``dq_asset_versions`` row and supersedes every
    previously-active snapshot of the OLD version as one set
    (``supersede_version_set``, AST-08/AST-14). The prior snapshot's files,
    inventory, mappings and warnings are UNTOUCHED (rule 8/9 — nothing here
    ever writes to another snapshot's own row).

    ``period_fields`` (all optional): ``start_date``, ``end_date``,
    ``snapshot_label``, ``period_column``, ``file_name``, ``row_count``,
    ``column_count``, ``column_type_map_json``, ``schema_override_flag``,
    ``dictionary_version_id``.

    AST-22/D-30 — TIME BASIS IS IMMUTABLE. ``time_basis`` is deliberately
    NOT a recognised period field: this function refuses outright if one is
    supplied (including for ``'full_replacement'``, which changes the
    reference schema but NEVER the basis) rather than silently ignoring or
    applying it. A caller that wants the asset's own basis reads it from
    ``require_asset(asset_id)['time_basis']`` — it is never an input here.
    """
    if intent not in INTENTS:
        raise ValueError(f"intent must be one of {INTENTS}, got {intent!r}")
    if "time_basis" in period_fields:
        raise ValueError(
            "time_basis is fixed for the whole life of an asset (AST-22) and cannot be set "
            "or changed via add_snapshot — including for a full_replacement, which changes "
            "the reference schema but never the time basis."
        )
    asset = require_asset(asset_id)

    existing_snapshots = s.query("dq_items", dataset_family_id=asset_id)
    active_existing = [r for r in existing_snapshots if r.get("snapshot_status") == "active"]
    if intent == "fresh" and existing_snapshots:
        raise ValueError(
            f"Asset {asset_id!r} already has a snapshot; use 'add_period' or "
            "'full_replacement' instead of 'fresh'."
        )
    if intent != "fresh" and not existing_snapshots:
        raise ValueError(
            f"Asset {asset_id!r} has no snapshot yet; its first snapshot must use intent='fresh'."
        )

    start_date = period_fields.get("start_date")
    end_date = period_fields.get("end_date")
    snapshot_label = period_fields.get("snapshot_label")
    period_column = period_fields.get("period_column")

    if start_date and end_date and str(start_date) > str(end_date):
        raise ValueError("Start date must be on or before end date; equal dates are allowed.")
    if snapshot_label:
        duplicate = s.execute(
            "SELECT item_id FROM dq_items WHERE dataset_family_id=? AND snapshot_label=?",
            (asset_id, snapshot_label),
        )
        if duplicate:
            raise ValueError(f"Snapshot label {snapshot_label!r} is already used within this asset.")

    if asset["time_basis"] == "period" and not start_date and not period_fields.get("_staged"):
        raise ValueError(
            f"Asset {asset_id!r} has a period time basis; every snapshot requires a "
            "start_date (AST-22/UPL-14)."
        )

    item_id = _id("item")
    if asset["time_basis"] == "none" and not snapshot_label:
        # UPL-15: free text, defaulting to the period range when dates are
        # given — for a 'none'-basis asset there is no period, so fall back
        # to something unique-by-construction (ux_dq_items_label_in_family)
        # rather than blocking here; Step 4 owns the full UPL-14/15 label UX.
        snapshot_label = f"__staged_{item_id}" if period_fields.get("_staged") else item_id
    if period_fields.get("_staged") and not snapshot_label:
        snapshot_label = f"__staged_{item_id}"

    now = s.now_ist()
    version_no = asset["current_version_no"]

    if intent == "full_replacement":
        old_version_no = asset["current_version_no"]
        version_no = old_version_no + 1
        version_id = identity.allocate("version")
        s.insert("dq_asset_versions", {
            "version_id": version_id, "asset_id": asset_id, "version_no": version_no,
            "status": "current",
            "reference_schema_json": period_fields.get("column_type_map_json"),
            "created_from_snapshot_id": item_id, "supersedes_version_no": old_version_no,
            "restored_from_version_no": None, "created_by": actor,
            "created_at": now, "superseded_at": None,
        })
        s.update("dq_assets", {"asset_id": asset_id},
                {"current_version_no": version_no, "target_variable": None,
                 "use_case": None, "updated_at": now})
        _write_event(asset_id, "version_created",
                    f"Version {version_no} created by full replacement "
                    f"(supersedes version {old_version_no}).",
                    actor=actor, version_no=version_no, snapshot_id=item_id)
        _usage("version_created", asset_id, "version", version_id, actor,
               {"version_no": version_no, "after_replacement": True})

    from dq_diagnostics import delivery  # noqa: PLC0415 — R-01: delivery.py is never modified
    delivery_result = delivery.register_delivery(item_id, asset_id, as_of_date=start_date)

    s.insert("dq_items", {
        "item_id": item_id, "kind": asset["kind"], "name": asset["display_name"],
        "status": "sourcing", "module_tag": "Data Sourcing", "ingest_status": "uploading",
        "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": delivery_result["delivery_seq"],
        "artifact_origin": asset.get("artifact_origin") or "unclassified",
        "as_of_date": start_date,
        "version_no": version_no, "snapshot_status": "active",
        "snapshot_label": snapshot_label, "start_date": start_date, "end_date": end_date,
        "has_time_period": 1 if asset["time_basis"] == "period" else 0,
        "period_column": period_column,
        "column_type_map_json": period_fields.get("column_type_map_json"),
        "row_count": period_fields.get("row_count"),
        "column_count": period_fields.get("column_count"),
        "file_name": period_fields.get("file_name"),
        "intent": intent,
        # CTX-04/06: the asset is canonical. A fresh/replacement starts with
        # blank intent; an add-period snapshot inherits the live asset fields.
        "target_variable": None if intent in {"fresh", "full_replacement"} else asset.get("target_variable"),
        "use_case": None if intent in {"fresh", "full_replacement"} else asset.get("use_case"),
        "schema_override_flag": int(bool(period_fields.get("schema_override_flag"))),
        "uploaded_by": actor,
        "sourcing_draft_state": period_fields.get("sourcing_draft_state"),
        "sourcing_owner": period_fields.get("sourcing_owner"),
        "sourcing_tenant_id": period_fields.get("sourcing_tenant_id"),
        "dictionary_version_id": period_fields.get("dictionary_version_id"),
        "superseded_at": None, "superseded_by_version_no": None,
    })

    if intent == "full_replacement":
        supersede_version_set(asset_id, old_version_no, actor)

    _write_event(asset_id, "snapshot_added",
                f"Snapshot added to {asset['display_name']} ({intent}), version {version_no}.",
                actor=actor, version_no=version_no, snapshot_id=item_id)
    _usage("snapshot_added", asset_id, "snapshot", item_id, actor,
           {"version_no": version_no, "intent": intent})

    from .refresh import refresh_derived
    refresh_derived(asset_id, actor=actor, reason=f"snapshot added ({intent})")
    row = s.query_one("dq_items", item_id=item_id)
    return {**row, "delivery_seq": delivery_result["delivery_seq"]}


def finalize_staged_snapshot(asset_id: str, snapshot_id: str, intent: str,
                             actor: str | None = None, **fields: Any) -> dict[str, Any]:
    """Commit the metadata decisions made after S4a profiling.

    S4a creates a staged ``dq_items`` row so the file can be uploaded and
    profiled immediately.  This small extension commits Step 4/5 decisions
    onto that staged row.  Full replacement still uses the existing
    ``supersede_version_set`` operation for the atomic set transition.
    """
    if intent not in ("fresh", "add_period", "full_replacement"):
        raise ValueError(f"intent must be one of {INTENTS}, got {intent!r}")
    asset = require_asset(asset_id)
    snapshot = s.query_one("dq_items", item_id=snapshot_id)
    if not snapshot or snapshot.get("dataset_family_id") != asset_id:
        raise ValueError("The staged snapshot does not belong to the selected asset.")
    if s.query_one("dq_asset_events", snapshot_id=snapshot_id,
                   event_type="snapshot_processed"):
        raise ValueError("This staged snapshot has already been saved and processed.")
    start_date, end_date = fields.get("start_date"), fields.get("end_date")
    if asset["time_basis"] == "period":
        if bool(start_date) != bool(end_date):
            raise ValueError("Start date and end date are mandatory together.")
        if not start_date:
            raise ValueError("A period-basis snapshot requires start and end dates.")
        if str(start_date) > str(end_date):
            raise ValueError("Start date must be on or before end date; equal dates are allowed.")
        label = fields.get("snapshot_label") or f"{start_date} → {end_date}"
    else:
        if start_date or end_date:
            raise ValueError("This asset has no time period; date fields are not applicable.")
        label = (fields.get("snapshot_label") or "").strip()
        if not label:
            raise ValueError("A snapshot label is required for a no-time-period asset.")
    duplicate = s.execute(
        "SELECT item_id FROM dq_items WHERE dataset_family_id=? AND snapshot_label=? AND item_id<>?",
        (asset_id, label, snapshot_id),
    )
    if duplicate:
        raise ValueError(f"Snapshot label {label!r} is already used within this asset.")

    old_version = asset["current_version_no"]
    version_no = snapshot.get("version_no") or old_version
    now = s.now_ist()
    if intent == "full_replacement":
        if snapshot.get("intent") == "full_replacement" and version_no > old_version:
            raise ValueError("This staged snapshot has already been committed.")
        version_no = old_version + 1
        version_id = identity.allocate("version")
        s.insert("dq_asset_versions", {
            "version_id": version_id, "asset_id": asset_id, "version_no": version_no,
            "status": "current", "reference_schema_json": fields.get("column_type_map_json"),
            "created_from_snapshot_id": snapshot_id, "supersedes_version_no": old_version,
            "restored_from_version_no": None, "created_by": actor, "created_at": now,
            "superseded_at": None,
        })
        s.update("dq_assets", {"asset_id": asset_id},
                 {"current_version_no": version_no, "updated_at": now})
        s.update("dq_items", {"item_id": snapshot_id}, {"version_no": version_no})
        superseded = supersede_version_set(asset_id, old_version, actor)
        _usage("version_created", asset_id, "version", version_id, actor,
               {"version_no": version_no, "after_replacement": True})
    else:
        superseded = {"snapshot_count": 0, "snapshot_ids": [], "description": "not applicable"}

    # CTX-04/06 (Data_Sourcing_11): an explicitly supplied value for this
    # commit always wins — that's how a decision made ON THIS upload
    # actually survives it. Only the ABSENCE of a supplied value falls back
    # to the original rule: blank on fresh/full_replacement (never carry a
    # stale prior decision into a new reference schema), carry the asset's
    # existing decision forward on add_period.
    supplied_target_variable = fields.get("target_variable")
    supplied_use_case = fields.get("use_case")
    supplied_product = fields.get("product")
    target_variable = (supplied_target_variable if supplied_target_variable is not None
                       else (None if intent in {"fresh", "full_replacement"} else asset.get("target_variable")))
    use_case = (supplied_use_case if supplied_use_case is not None
               else (None if intent in {"fresh", "full_replacement"} else asset.get("use_case")))
    product = (supplied_product if supplied_product is not None
              else (None if intent in {"fresh", "full_replacement"} else asset.get("product")))
    if intent in {"fresh", "full_replacement"}:
        s.update("dq_assets", {"asset_id": asset_id},
                 {"target_variable": target_variable, "use_case": use_case,
                  "product": product, "updated_at": now})
    changes = {
        "intent": intent, "version_no": version_no, "snapshot_label": label,
        "start_date": start_date, "end_date": end_date,
        "as_of_date": start_date, "period_column": fields.get("period_column"),
        "column_type_map_json": fields.get("column_type_map_json"),
        "schema_override_flag": int(bool(fields.get("schema_override_flag"))),
        "uploaded_by": actor or fields.get("uploaded_by"), "updated_at": now,
        "target_variable": target_variable, "use_case": use_case, "product": product,
        "sourcing_draft_state": None,
    }
    s.update("dq_items", {"item_id": snapshot_id}, changes)
    if snapshot.get("sourcing_draft_state") in {"active", "recovery"}:
        recovery = s.execute(
            "SELECT item_id FROM dq_items WHERE sourcing_tenant_id=? "
            "AND sourcing_owner=? AND sourcing_draft_state='recovery' "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            (snapshot.get("sourcing_tenant_id"), snapshot.get("sourcing_owner")),
        )
        if recovery:
            s.update("dq_items", {"item_id": recovery[0]["item_id"]}, {
                "sourcing_draft_state": "active", "updated_at": now,
            })
    if intent == "full_replacement":
        s.update("dq_asset_versions", {"asset_id": asset_id, "version_no": version_no},
                 {"reference_schema_json": fields.get("column_type_map_json"),
                  "created_from_snapshot_id": snapshot_id})
    elif intent == "fresh":
        s.update("dq_asset_versions", {"asset_id": asset_id, "version_no": version_no},
                 {"reference_schema_json": fields.get("column_type_map_json"),
                  "created_from_snapshot_id": snapshot_id})
    _write_event(asset_id, "snapshot_processed",
                 f"Snapshot {label} processed as {intent.replace('_', ' ')}.",
                 actor=actor, version_no=version_no, snapshot_id=snapshot_id,
                 detail={"intent": intent, "superseded": superseded,
                         "period_column": fields.get("period_column")})
    from .refresh import refresh_derived
    refresh_derived(asset_id, actor=actor, reason=f"snapshot processed ({intent})")
    return {**s.query_one("dq_items", item_id=snapshot_id), "superseded": superseded}


# ---------------------------------------------------------------------------
#  supersede_version_set / restore_version_set (P-11)
# ---------------------------------------------------------------------------

def _describe_snapshot_set(rows: list[dict[str, Any]]) -> str:
    """Human-language description of a snapshot set — e.g. '2 snapshots
    (2026-01-31, 2026-02-28)'. A single reusable computation: this is what
    ``supersede_version_set``'s own event summary uses, and is EXACTLY the
    computation UPL-18's confirmation preview ("show how many snapshots and
    which periods or labels will be superseded") needs later — kept as one
    function so a future confirmation-preview surface calls this, rather
    than duplicating the string-building inline."""
    if not rows:
        return "no active snapshots"
    labels = [r.get("snapshot_label") or r.get("start_date") or r["item_id"] for r in rows]
    n = len(rows)
    noun = "snapshot" if n == 1 else "snapshots"
    return f"{n} {noun} ({', '.join(str(label) for label in labels)})"


def preview_supersede(asset_id: str, version_no: int) -> dict[str, Any]:
    """The reusable preview UPL-18 needs BEFORE a full replacement is
    confirmed, and what ``supersede_version_set`` itself uses to write its
    own event summary — one computation, not duplicated inline at each call
    site."""
    rows = [r for r in s.query("dq_items", dataset_family_id=asset_id, version_no=version_no)
           if r.get("snapshot_status") == "active"]
    return {
        "asset_id": asset_id, "version_no": version_no,
        "snapshot_count": len(rows),
        "snapshot_ids": [r["item_id"] for r in rows],
        "description": _describe_snapshot_set(rows),
    }


def supersede_version_set(asset_id: str, version_no: int, actor: str | None = None) -> dict[str, Any]:
    """AST-08/AST-14/P-11 — supersede EVERY active ``dq_items`` row of
    ``version_no`` as ONE set, inside ONE transaction: one ``UPDATE``
    statement for ``dq_items``, one for ``dq_asset_versions`` — never a
    per-snapshot Python loop, and never exposed as two separate calls a
    caller could interleave with something else. Writes one
    ``version_superseded`` event whose ``summary`` names how many snapshots
    and which periods/labels were superseded, in human language
    (``_describe_snapshot_set`` — the same helper a future confirmation
    preview reuses).

    ``superseded_by_version_no`` is read from ``dq_assets.current_version_no``
    AT CALL TIME — the caller (``add_snapshot``'s full_replacement branch,
    or ``restore_version_set``) is responsible for having already pointed
    it at whichever version is becoming current before calling this.
    Calling this directly on the version that IS still current (nothing has
    been designated to replace it yet) is refused — superseding without a
    successor to point at is meaningless and would corrupt the version
    history.
    """
    asset = require_asset(asset_id)
    if version_no == asset["current_version_no"]:
        raise ValueError(
            f"Version {version_no} of asset {asset_id!r} is still current — designate a new "
            "current version before superseding it."
        )
    preview = preview_supersede(asset_id, version_no)
    new_current = asset["current_version_no"]
    now = s.now_ist()

    with s.get_conn() as conn:
        conn.execute(
            "UPDATE dq_items SET snapshot_status='superseded', superseded_at=?, "
            "superseded_by_version_no=? "
            "WHERE dataset_family_id=? AND version_no=? AND snapshot_status='active'",
            (now, new_current, asset_id, version_no),
        )
        conn.execute(
            "UPDATE dq_asset_versions SET status='superseded', superseded_at=? "
            "WHERE asset_id=? AND version_no=?",
            (now, asset_id, version_no),
        )
        conn.commit()

    _write_event(asset_id, "version_superseded",
                f"Version {version_no} superseded by version {new_current}: "
                f"{preview['description']}.",
                actor=actor, version_no=version_no,
                detail={"superseded_by_version_no": new_current, **preview})
    old_version = s.query_one("dq_asset_versions", asset_id=asset_id, version_no=version_no)
    if old_version:
        _usage("version_superseded", asset_id, "version", old_version["version_id"], actor,
               {"superseded_by_version_no": new_current})
    from .refresh import refresh_derived
    refresh_derived(asset_id, actor=actor, reason=f"version {version_no} superseded")
    return {"asset_id": asset_id, "version_no": version_no,
            "superseded_by_version_no": new_current, **preview}


def restore_version_set(asset_id: str, version_no: int, actor: str | None = None) -> dict[str, Any]:
    """AST-09 — reactivate ``version_no``'s snapshot set WHOLESALE, supersede
    whatever set is CURRENTLY active, and make ``version_no`` current again.
    Itself an audited version event (``version_restored``).

    Does NOT invent a new version number: restoring v1 makes v1 current
    again — the event log (not a renumbering) records that it happened, and
    records it AGAIN if called a second time.

    Double-restore is guarded explicitly (never a corrupted double-active
    state): if ``version_no`` is ALREADY current, there is nothing to
    supersede — the 'currently active' set IS this version already — so no
    ``dq_items``/``dq_asset_versions`` row is touched a second time; only
    the event is written, recording the repeat call honestly.

    Restoring an unknown version raises ``ValueError`` (the same convention
    every other function in this module uses for "no such X").
    """
    asset = require_asset(asset_id)
    target_version = s.query_one("dq_asset_versions", asset_id=asset_id, version_no=version_no)
    if target_version is None:
        raise ValueError(f"Asset {asset_id!r} has no version {version_no}.")

    current_version_no = asset["current_version_no"]
    if current_version_no == version_no:
        _write_event(asset_id, "version_restored",
                    f"Version {version_no} restore requested but it was already current — "
                    "no change made.",
                    actor=actor, version_no=version_no, detail={"noop": True})
        return {"asset_id": asset_id, "version_no": version_no, "noop": True}

    now = s.now_ist()
    with s.get_conn() as conn:
        # AST-09: reactivate the target version's own snapshot set wholesale.
        conn.execute(
            "UPDATE dq_items SET snapshot_status='active', superseded_at=NULL, "
            "superseded_by_version_no=NULL WHERE dataset_family_id=? AND version_no=?",
            (asset_id, version_no),
        )
        # Supersede whatever set is CURRENTLY active.
        conn.execute(
            "UPDATE dq_items SET snapshot_status='superseded', superseded_at=?, "
            "superseded_by_version_no=? "
            "WHERE dataset_family_id=? AND version_no=? AND snapshot_status='active'",
            (now, version_no, asset_id, current_version_no),
        )
        conn.execute(
            "UPDATE dq_asset_versions SET status='superseded', superseded_at=? "
            "WHERE asset_id=? AND version_no=?",
            (now, asset_id, current_version_no),
        )
        conn.execute(
            "UPDATE dq_asset_versions SET status='current', superseded_at=NULL "
            "WHERE asset_id=? AND version_no=?",
            (asset_id, version_no),
        )
        conn.execute(
            "UPDATE dq_assets SET current_version_no=?, updated_at=? WHERE asset_id=?",
            (version_no, now, asset_id),
        )
        conn.commit()

    _write_event(asset_id, "version_restored",
                f"Version {version_no} restored and made current again (was version "
                f"{current_version_no}).",
                actor=actor, version_no=version_no,
                detail={"previous_current_version_no": current_version_no})
    _usage("version_restored", asset_id, "version", target_version["version_id"], actor,
           {"previous_current_version_no": current_version_no})
    from .refresh import refresh_derived
    refresh_derived(asset_id, actor=actor, reason=f"version {version_no} restored")
    return {"asset_id": asset_id, "version_no": version_no,
            "previous_current_version_no": current_version_no, "noop": False}


# ---------------------------------------------------------------------------
#  rename_alias (AST-04)
# ---------------------------------------------------------------------------

def rename_alias(asset_id: str, new_alias: str, actor: str | None = None) -> dict[str, Any]:
    """AST-04 — the alias is editable; ``system_id``/``asset_id`` and every
    ``item_id`` are NEVER touched. Mirrors the new ``display_name`` onto
    EVERY ``dq_items.name`` row of the asset (P-05's single-writer
    discipline — the same mirror ``system_db._backfill_asset_model``'s own
    drift-repair re-asserts on every boot), so every existing reader of
    ``dq_items.name`` (Inventory, Test Lab, issues, reports) keeps working
    untouched. Writes an ``alias_renamed`` audit event.
    """
    asset = require_asset(asset_id)
    new_alias = identity.validate_alias(new_alias)
    new_display_name = identity.compose_display_name(asset["system_id"], new_alias)
    old_alias, old_display_name = asset["alias"], asset["display_name"]
    now = s.now_ist()

    with s.get_conn() as conn:
        conn.execute(
            "UPDATE dq_assets SET alias=?, display_name=?, updated_at=? WHERE asset_id=?",
            (new_alias, new_display_name, now, asset_id),
        )
        conn.execute(
            "UPDATE dq_items SET name=? WHERE dataset_family_id=?",
            (new_display_name, asset_id),
        )
        conn.commit()

    _write_event(asset_id, "alias_renamed",
                f"Renamed from {old_display_name!r} to {new_display_name!r}.",
                actor=actor,
                detail={"old_alias": old_alias, "new_alias": new_alias,
                        "old_display_name": old_display_name,
                        "new_display_name": new_display_name})
    _usage("alias_renamed", asset_id, "asset", asset_id, actor,
           {"old_display_name": old_display_name, "new_display_name": new_display_name})
    return require_asset(asset_id)
