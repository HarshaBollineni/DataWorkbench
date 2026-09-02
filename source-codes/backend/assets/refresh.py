"""Deterministic recomputation of active-snapshot derived records."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import system_db as s
from ingest import dictionary_state, mapping, warnings


def _same_without(row: dict[str, Any], candidate: dict[str, Any], ignored: set[str]) -> bool:
    def stable(value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)
    return all(stable(row.get(key)) == stable(value)
               for key, value in candidate.items() if key not in ignored)


def _warning_id(item_id: str, table: str, warning: dict[str, Any]) -> str:
    raw = f"{item_id}|{table}|{warning.get('column')}|{warning.get('code')}|{warning.get('message')}"
    return f"warn_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _dictionary_rows(item_id: str, table: str) -> list[tuple[str, dict[str, str]]]:
    """Use the persisted mapping declarations; never reopen a source file."""
    rows = s.execute("SELECT * FROM dq_item_mappings WHERE item_id=? AND table_name=?", (item_id, table))
    return [(row.get("canonical_field"), {
        "definition": row.get("definition") or "",
        "declared_type": row.get("declared_type") or "",
    }) for row in rows if row.get("canonical_field")]


def _replace_mapping(item_id: str, table: str, mapped: list[dict[str, Any]], dtype: dict[str, str]) -> None:
    old = {(r.get("canonical_field"), r.get("source_column")): r for r in s.execute(
        "SELECT * FROM dq_item_mappings WHERE item_id=? AND table_name=?", (item_id, table))}
    s.execute("DELETE FROM dq_item_mappings WHERE item_id=? AND table_name=?", (item_id, table))
    now = s.now_ist()
    for rec in mapped:
        key = (rec.get("canonical_field"), rec.get("source_column"))
        previous = old.get(key)
        s.insert("dq_item_mappings", {
            "item_id": item_id, "table_name": table,
            "canonical_field": rec["canonical_field"], "source_column": rec.get("source_column"),
            "tier": rec["tier"], "score": rec.get("score"), "status": rec.get("status"),
            "confirmed_by": rec.get("confirmed_by"),
            "dtype": dtype.get(rec.get("source_column")),
            "definition": rec.get("definition", ""), "declared_type": rec.get("declared_type", ""),
            "updated_at": previous.get("updated_at") if previous and _same_without(previous, {
                "canonical_field": rec["canonical_field"], "source_column": rec.get("source_column"),
                "tier": rec["tier"], "score": rec.get("score"), "status": rec.get("status"),
                "confirmed_by": rec.get("confirmed_by"), "dtype": dtype.get(rec.get("source_column")),
                "definition": rec.get("definition", ""), "declared_type": rec.get("declared_type", ""),
            }, {"updated_at", "item_id", "table_name"}) else now,
        })


def _replace_warnings(item_id: str, table: str, values: list[dict[str, Any]]) -> None:
    old = {(r.get("column_name"), r.get("code"), r.get("message")): r for r in s.execute(
        "SELECT * FROM dq_item_warnings WHERE item_id=? AND table_name=?", (item_id, table))}
    s.execute("DELETE FROM dq_item_warnings WHERE item_id=? AND table_name=?", (item_id, table))
    now = s.now_ist()
    for warning in values:
        key = (warning.get("column"), warning.get("code"), warning.get("message"))
        previous = old.get(key)
        s.insert("dq_item_warnings", {
            "warning_id": previous.get("warning_id") if previous else _warning_id(item_id, table, warning),
            "item_id": item_id, "table_name": table, "column_name": warning.get("column", ""),
            "code": warning["code"], "message": warning["message"],
            "created_at": previous.get("created_at") if previous else now,
        })


def _upsert_inventory(item: dict[str, Any], table: str, column: str, frame_column, mapped: dict[str, Any] | None) -> dict[str, Any]:
    from ai.v2 import service as v2

    old_rows = s.execute("SELECT * FROM variable_inventory WHERE item_id=? AND table_name=? AND column_name=?",
                         (item["item_id"], table, column))
    old = old_rows[0] if old_rows else {}
    declared = v2._classification_from_declared((mapped or {}).get("declared_type"))
    special_values = v2._special_value_list(old.get("missing_value_codes_json"))
    specials_confirmed = bool(old.get("missing_codes_confirmed"))
    regular = v2._regular_values(frame_column, special_values, specials_confirmed)
    observed = v2._classify(column, regular, item.get("target_variable"))
    classification = declared or observed
    profile = {**v2._column_profile(frame_column, special_values, specials_confirmed, classification),
               "inferred_type": observed,
               "sample_values": [v2._jsonable(value) for value in regular.head(5).tolist()]}
    candidate = {
        # A refresh may recompute observed evidence, but the existing row is
        # the canonical Step 3 schema decision. Start with every stored field
        # so INSERT OR REPLACE cannot erase newer metadata columns.
        **old,
        "item_id": item["item_id"], "table_name": table, "column_name": column,
        "classification": old.get("classification") or classification,
        "data_type": str(frame_column.dtype),
        "description": old.get("description") if old else (mapped or {}).get("definition", ""),
        "discrepancies": old.get("discrepancies") or [], "notes": old.get("notes", ""),
        "role": old.get("role") or v2._role_for(column, classification, item.get("target_variable")),
        "profile_json": profile,
        "provisional": (old.get("provisional") if old.get("provisional") is not None
                        else 0 if mapped and mapped.get("tier") == "high" and declared else 1),
    }
    # The cache is immutable, so a repeated refresh has no new source event.
    # Preserve the existing row timestamp; content changes still replace the
    # deterministic fields, while identical runs remain byte-identical.
    candidate["updated_at"] = old.get("updated_at") or s.now_ist()
    s.upsert("variable_inventory", candidate)
    fingerprint = {
        "snapshot_id": item["item_id"], "table_name": table, "column_name": column,
        "dtype": profile.get("dtype"), "confirmed_type": classification,
        "null_rate": profile.get("null_share"), "distinct_count": profile.get("cardinality"),
        "min_value": None if profile.get("min") is None else str(profile.get("min")),
        "max_value": None if profile.get("max") is None else str(profile.get("max")),
        "mean_value": profile.get("mean"), "stddev_value": profile.get("stddev"),
        "histogram_json": json.dumps(profile.get("histogram") or [], sort_keys=True),
        "distinct_set_hash": profile.get("distinct_set_hash"),
        "top_k_json": json.dumps(profile.get("top_k") or {}, sort_keys=True),
    }
    previous_rows = s.execute(
        "SELECT * FROM dq_snapshot_fingerprints WHERE snapshot_id=? AND table_name=? AND column_name=?",
        (item["item_id"], table, column))
    previous = previous_rows[0] if previous_rows else None
    fingerprint["computed_at"] = previous.get("computed_at") if previous and _same_without(previous, fingerprint, {"computed_at"}) else s.now_ist()
    s.upsert("dq_snapshot_fingerprints", fingerprint)
    return candidate


def refresh_derived(asset_id: str, actor: str | None = None, reason: str = "active snapshot set changed") -> dict[str, Any]:
    """Recompute only deterministic records for the asset's active snapshots.

    The cache existence check is intentional: `_read_table` can rebuild a
    missing cache from an upload file, which this path must never do.
    """
    from ai.v2 import service as v2

    asset = s.query_one("dq_assets", asset_id=asset_id)
    if asset is None:
        raise ValueError(f"No such asset: {asset_id!r}")
    snapshots = [row for row in s.query("dq_items", dataset_family_id=asset_id)
                 if row.get("snapshot_status") == "active"]
    recomputed: list[str] = []
    skipped: list[str] = []
    for item in snapshots:
        cache = v2._item_db(item["item_id"])
        if not cache.exists():
            skipped.append(item["item_id"])
            continue
        inventory_count = 0
        all_mappings: list[dict[str, Any]] = []
        all_warnings: list[dict[str, Any]] = []
        for table_row in s.query("dq_item_tables", item_id=item["item_id"], order_by="table_name"):
            table = table_row["table_name"]
            frame = v2._read_table(item["item_id"], table)
            columns = [str(column) for column in frame.columns]
            declared = _dictionary_rows(item["item_id"], table)
            mapped = mapping.compute_mapping(declared, columns) if declared else []
            by_source = {r.get("source_column"): r for r in mapped if r.get("source_column")}
            dtype = {column: str(frame[column].dtype) for column in columns}
            for column in columns:
                _upsert_inventory(item, table, column, frame[column], by_source.get(column))
                inventory_count += 1
                observed = v2._classify(column, frame[column], item.get("target_variable"))
                entry = by_source.get(column)
                declared_type = v2._classification_from_declared((entry or {}).get("declared_type"))
                if declared_type and declared_type != observed:
                    warning = warnings.type_conflict_warning(column, declared_type, observed)
                    if warning: all_warnings.append(warning)
                all_warnings.extend(filter(None, (
                    warnings.column_all_null_warning(column, frame[column]),
                    warnings.column_mixed_type_warning(column, frame[column]),
                    warnings.column_parse_failure_warning(column, frame[column], observed),
                )))
            all_mappings.extend(mapped)
            all_warnings.extend(warnings.mapping_warnings(mapped, columns))
            _replace_mapping(item["item_id"], table, mapped, dtype)
            _replace_warnings(item["item_id"], table, all_warnings)
            all_warnings = []
        state = dictionary_state.compute(
            has_dictionary=bool(all_mappings), total_columns=inventory_count,
            declared_covered=sum(1 for row in all_mappings
                                 if row.get("tier") == "high" and row.get("declared_type")))
        s.update("dq_items", {"item_id": item["item_id"]}, {"dictionary_state": state,
                 "ingest_status": "ready" if inventory_count else item.get("ingest_status"),
                 "updated_at": item.get("updated_at")})
        if inventory_count:
            # Artefact creation is a deterministic projection of retained
            # profile evidence, after the snapshot has reached its committed
            # ready state. It never rewrites variable_inventory.
            from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
            persist_snapshot_profile_artifacts(item["item_id"], actor=actor)
        recomputed.append(item["item_id"])
    return {"asset_id": asset_id, "reason": reason, "actor": actor,
            "active_snapshots": len(snapshots), "recomputed": recomputed,
            "skipped": skipped, "recomputed_classes": [
                "variable_inventory", "dq_item_mappings", "dq_item_warnings",
                "dq_snapshot_fingerprints", "dictionary_state", "ingest_status",
                "read-model summaries"]}
