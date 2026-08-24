"""RCA Stage 1 — governed taxonomy and tag propagation service.

See docs/rca/00-contracts.md §7. Dimensions/values are data (seeded by
seeds/taxonomy_seed.py), never hardcoded frontend constants. Every assignment
records its taxonomy version + origin; downstream snapshots never rewrite
history when a source object's tags later change — inherit_tags() copies the
source's CURRENT active tags once, at the moment of the call, as new rows.
"""
from __future__ import annotations

import uuid

import system_db as s

ORIGIN_VALUES = {"inherited", "user_added", "system_derived", "migrated"}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def current_taxonomy_version(tenant_id: str) -> dict:
    versions = s.query("tag_taxonomy_versions", order_by="seq DESC", tenant_id=tenant_id)
    if not versions:
        raise RuntimeError(f"No taxonomy version seeded for tenant {tenant_id!r}")
    return versions[0]


def list_dimensions(tenant_id: str) -> list[dict]:
    """All dimensions with their non-deprecated values, for tag pickers."""
    dims = s.query("tag_dimensions", order_by="key", tenant_id=tenant_id)
    out = []
    for d in dims:
        values = [v for v in s.query("tag_values", order_by="key", dimension_id=d["dimension_id"])
                 if not v.get("deprecated")]
        out.append({**d, "values": values})
    return out


def _resolve_value(tenant_id: str, value_key: str) -> dict:
    """value_key is "dimension_key:value_key", e.g. "risk_type:credit"."""
    if ":" not in value_key:
        raise ValueError(f"Malformed tag value key: {value_key!r}")
    dim_key, val_key = value_key.split(":", 1)
    dim = s.query_one("tag_dimensions", tenant_id=tenant_id, key=dim_key)
    if not dim:
        raise ValueError(f"Unknown tag dimension: {dim_key!r}")
    val = s.query_one("tag_values", dimension_id=dim["dimension_id"], key=val_key)
    if not val or val.get("deprecated"):
        raise ValueError(f"Unknown or deprecated tag value: {value_key!r}")
    return val


def _active_assignments(tenant_id: str, object_type: str, object_id: str) -> list[dict]:
    return [a for a in s.query("tag_assignments", tenant_id=tenant_id,
                               object_type=object_type, object_id=object_id)
           if not a.get("removed_at")]


def get_tags(tenant_id: str, object_type: str, object_id: str) -> list[dict]:
    """Active tags on one object, joined with dimension/value labels for display."""
    out = []
    for a in _active_assignments(tenant_id, object_type, object_id):
        val = s.query_one("tag_values", value_id=a["value_id"])
        if not val:
            continue
        dim = s.query_one("tag_dimensions", dimension_id=val["dimension_id"])
        out.append({
            "assignment_id": a["assignment_id"], "dimension_key": (dim or {}).get("key"),
            "dimension_label": (dim or {}).get("label"), "value_key": val["key"],
            "value_label": val["label"], "origin": a["origin"], "actor": a.get("actor"),
            "ts": a.get("ts"),
        })
    return out


def assign_tags(tenant_id: str, object_type: str, object_id: str,
                value_keys: list[str], actor: str, origin: str = "user_added") -> list[dict]:
    """Assign one or more tags directly to an object (the root of a propagation
    chain — e.g. a dq_item). Skips values already actively assigned."""
    if origin not in ORIGIN_VALUES:
        raise ValueError(f"Invalid origin: {origin!r}")
    version = current_taxonomy_version(tenant_id)
    existing_value_ids = {a["value_id"] for a in _active_assignments(tenant_id, object_type, object_id)}
    created = []
    for key in value_keys:
        val = _resolve_value(tenant_id, key)
        if val["value_id"] in existing_value_ids:
            continue
        row = {
            "assignment_id": _id("tagasn"), "tenant_id": tenant_id,
            "object_type": object_type, "object_id": object_id,
            "value_id": val["value_id"], "taxonomy_version_id": version["version_id"],
            "origin": origin, "source_object_type": None, "source_object_id": None,
            "actor": actor, "ts": s.now_ist(),
            "removed_at": None, "removed_reason": None, "removed_by": None,
        }
        s.insert("tag_assignments", row)
        existing_value_ids.add(val["value_id"])
        created.append(row)
    return created


def remove_tag(tenant_id: str, assignment_id: str, reason: str, actor: str) -> dict:
    """Soft-delete: the row stays (audit trail), removed_at/reason/by are set.
    Removing an inherited tag requires a reason and an audit event
    (contracts.md §7) — enforced here for every origin, not just inherited."""
    if not (reason or "").strip():
        raise ValueError("A reason is required to remove a tag.")
    row = s.query_one("tag_assignments", assignment_id=assignment_id)
    if not row or row["tenant_id"] != tenant_id:
        raise KeyError("Unknown tag assignment")
    if row.get("removed_at"):
        raise ValueError("Tag assignment is already removed.")
    s.update("tag_assignments", {"assignment_id": assignment_id}, {
        "removed_at": s.now_ist(), "removed_reason": reason.strip(), "removed_by": actor,
    })
    s.insert("transaction_log", {
        "ts": s.now_ist(), "actor": actor, "event": "tag_remove",
        "payload": {"assignment_id": assignment_id, "object_type": row["object_type"],
                   "object_id": row["object_id"], "reason": reason.strip()},
    })
    return s.query_one("tag_assignments", assignment_id=assignment_id)


def inherit_tags(tenant_id: str, source_object_type: str, source_object_id: str,
                 target_object_type: str, target_object_id: str, actor: str) -> list[dict]:
    """Snapshot the source object's CURRENT active tags onto the target as new
    inherited assignments. Each new row keeps the SOURCE assignment's own
    taxonomy_version_id (the version live when that tag was applied), so later
    changes to the source's tags — or a taxonomy republish — never rewrite this
    snapshot (contracts.md §7: "later source-tag changes never rewrite
    history"). Idempotent per (target, source, value): re-inheriting the same
    source tag onto the same target is a no-op, so re-running a sync job
    doesn't grow the target's tag list unboundedly.
    """
    source_active = _active_assignments(tenant_id, source_object_type, source_object_id)
    if not source_active:
        return []
    already = {
        a["value_id"] for a in s.query("tag_assignments", tenant_id=tenant_id,
                                       object_type=target_object_type, object_id=target_object_id,
                                       origin="inherited", source_object_type=source_object_type,
                                       source_object_id=source_object_id)
    }
    created = []
    for src in source_active:
        if src["value_id"] in already:
            continue
        row = {
            "assignment_id": _id("tagasn"), "tenant_id": tenant_id,
            "object_type": target_object_type, "object_id": target_object_id,
            "value_id": src["value_id"], "taxonomy_version_id": src["taxonomy_version_id"],
            "origin": "inherited", "source_object_type": source_object_type,
            "source_object_id": source_object_id, "actor": actor, "ts": s.now_ist(),
            "removed_at": None, "removed_reason": None, "removed_by": None,
        }
        s.insert("tag_assignments", row)
        already.add(src["value_id"])
        created.append(row)
    return created
