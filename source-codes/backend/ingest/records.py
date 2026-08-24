"""Persisted ingestion records (ING-08) — the downstream substrate.

Everything ``ai/v2/service.py`` computes during ingestion (mapping,
dictionary state, warnings, status) is written here, and read back in
EXACTLY the shape Phase 6's Test Lab manifest / CFR-05 role resolution
consume (docs/0.4.0/06-ingestion-contract.md §6):

    confirmed mapping | per column: {source_column, canonical_field, tier,
                                      score, confirmed_by, dtype}
    warnings          | list of {column, code, message}
    dictionary state  | 'yes'/'thin'/'absent' (+ per-column ``provisional``
                         on ``variable_inventory``, read via
                         ``ai/v2/service.py::get_inventory``)

Field names in ``get_mapping``/``get_warnings`` below are the contract, not
an implementation detail — Phase 6 reads these directly.
"""
from __future__ import annotations

import system_db as db


def replace_mapping(item_id: str, table_name: str, records: list[dict],
                     dtype_by_column: dict[str, str]) -> None:
    """Overwrite the persisted mapping for one table with a freshly computed
    set (re-profiling — e.g. a replaced dictionary — always recomputes from
    scratch rather than layering)."""
    db.execute("DELETE FROM dq_item_mappings WHERE item_id=? AND table_name=?", [item_id, table_name])
    now = db.now_ist()
    for rec in records:
        source_column = rec.get("source_column")
        db.insert("dq_item_mappings", {
            "item_id": item_id, "table_name": table_name,
            "canonical_field": rec["canonical_field"],
            "source_column": source_column,
            "tier": rec["tier"], "score": rec.get("score"),
            "status": rec.get("status"), "confirmed_by": rec.get("confirmed_by"),
            "dtype": dtype_by_column.get(source_column) if source_column else None,
            "definition": rec.get("definition", ""), "declared_type": rec.get("declared_type", ""),
            "role": rec.get("role", ""),
            "missing_value_codes_json": rec.get("missing_value_codes", []),
            "business_context": rec.get("business_context", ""),
            "updated_at": now,
        })


def get_mapping(item_id: str, table_name: str | None = None) -> list[dict]:
    """Confirmed-mapping records, exactly per contract §6:
    ``{source_column, canonical_field, tier, score, confirmed_by, dtype}``
    (plus ``table_name``/``status``/``definition``/``declared_type`` as
    additional, additive context)."""
    rows = db.query("dq_item_mappings", item_id=item_id,
                    **({"table_name": table_name} if table_name else {}))
    return [{
        "source_column": r.get("source_column"), "canonical_field": r.get("canonical_field"),
        "tier": r.get("tier"), "score": r.get("score"), "confirmed_by": r.get("confirmed_by"),
        "dtype": r.get("dtype"), "table_name": r.get("table_name"), "status": r.get("status"),
        "definition": r.get("definition", ""), "declared_type": r.get("declared_type", ""),
        "role": r.get("role", ""),
        "missing_value_codes": r.get("missing_value_codes_json") or [],
        "business_context": r.get("business_context", ""),
    } for r in rows]


def confirm_mapping(item_id: str, table_name: str, source_column: str, accept: bool) -> bool:
    """A human resolves a ``confirm_suggestion`` (fuzzy-tier) mapping via the
    Review screen — surfaced through the existing ``PUT inventory``
    mechanism (ING-06 names no other decision surface), not a bespoke
    endpoint. ``accept=True`` applies it (``confirmed_by='user'``,
    ``status='applied'``); ``accept=False`` dismisses it
    (``status='dismissed'``) without deleting the preserved definition.
    Returns True if a matching pending suggestion was found and updated."""
    rows = db.execute(
        "SELECT * FROM dq_item_mappings WHERE item_id=? AND table_name=? AND source_column=?",
        [item_id, table_name, source_column],
    )
    pending = next((r for r in rows if r.get("tier") == "fuzzy" and r.get("status") == "confirm_suggestion"), None)
    if not pending:
        return False
    db.execute(
        "UPDATE dq_item_mappings SET status=?, confirmed_by=?, updated_at=? "
        "WHERE item_id=? AND table_name=? AND canonical_field=?",
        ["applied" if accept else "dismissed", "user" if accept else None, db.now_ist(),
         item_id, table_name, pending["canonical_field"]],
    )
    return True


def replace_warnings(item_id: str, table_name: str, warnings: list[dict]) -> None:
    db.execute("DELETE FROM dq_item_warnings WHERE item_id=? AND table_name=?", [item_id, table_name])
    now = db.now_ist()
    for w in warnings:
        db.insert("dq_item_warnings", {
            "warning_id": _wid(),
            "item_id": item_id, "table_name": table_name,
            "column_name": w.get("column", ""), "code": w["code"], "message": w["message"],
            "created_at": now,
        })


def _wid() -> str:
    import uuid
    return f"warn_{uuid.uuid4().hex[:12]}"


def get_warnings(item_id: str, table_name: str | None = None) -> list[dict]:
    """Warning records, exactly per contract §6: ``{column, code, message}``
    (plus ``table_name`` as additional, additive context)."""
    rows = db.query("dq_item_warnings", item_id=item_id,
                    **({"table_name": table_name} if table_name else {}))
    return [{"column": r.get("column_name", ""), "code": r.get("code"),
            "message": r.get("message"), "table_name": r.get("table_name")} for r in rows]


def set_status(item_id: str, ingest_status: str, fail_reason: str | None = None) -> None:
    db.update("dq_items", {"item_id": item_id}, {
        "ingest_status": ingest_status, "ingest_fail_reason": fail_reason,
        "updated_at": db.now_ist(),
    })


def set_dictionary_state(item_id: str, state: str) -> None:
    db.update("dq_items", {"item_id": item_id}, {"dictionary_state": state, "updated_at": db.now_ist()})


def get_ingest_summary(item_id: str) -> dict:
    item = db.query_one("dq_items", item_id=item_id) or {}
    return {
        "status": item.get("ingest_status"),
        "fail_reason": item.get("ingest_fail_reason"),
        "dictionary_state": item.get("dictionary_state"),
    }
