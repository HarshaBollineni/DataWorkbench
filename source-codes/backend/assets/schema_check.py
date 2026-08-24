"""UPL-20/21/29 schema comparison for staged snapshot uploads.

The comparison is deliberately metadata-only.  It consumes the reference
schema stored on the current asset version and the confirmed map produced by
the ingestion profiler; it never opens an uploaded file.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

# UPL-21: a rename is only evidence-backed when both signals clear this floor.
RENAME_SIMILARITY_THRESHOLD = 0.80


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _tables(schema: Any, kind: str) -> dict[str, dict]:
    raw = _as_dict(schema).get("tables") or {}
    if kind == "dataset" and not raw:
        # A few older callers use a flat {columns, types} schema for datasets.
        flat = _as_dict(schema)
        if "columns" in flat or "types" in flat:
            raw = {"dataset": flat}
    out = {}
    for table, spec in raw.items():
        spec = spec if isinstance(spec, dict) else {}
        columns = [str(c) for c in (spec.get("columns") or list((spec.get("types") or {}).keys()))]
        types = {str(k): str(v) for k, v in (spec.get("types") or {}).items()}
        out[str(table)] = {"columns": columns, "types": types}
    if kind == "dataset" and out:
        # A Dataset is one logical table; filenames are not schema identity.
        only = next(iter(out.values()))
        return {"dataset": only}
    return out


def _type_agrees(left: str | None, right: str | None) -> bool:
    aliases = {"numeric": "numerical", "number": "numerical", "integer": "numerical",
               "float": "numerical", "date": "datetime", "boolean": "binary"}
    return aliases.get(str(left).lower(), str(left).lower()) == aliases.get(str(right).lower(), str(right).lower())


def _table_diff(reference: dict, incoming: dict) -> dict:
    ref_cols = list(reference.get("columns") or [])
    in_cols = list(incoming.get("columns") or [])
    missing = [c for c in ref_cols if c not in in_cols]
    extra = [c for c in in_cols if c not in ref_cols]
    renamed = []
    for old in list(missing):
        candidates = []
        for new in extra:
            similarity = difflib.SequenceMatcher(None, old.casefold(), new.casefold()).ratio()
            if similarity >= RENAME_SIMILARITY_THRESHOLD and _type_agrees(
                    (reference.get("types") or {}).get(old), (incoming.get("types") or {}).get(new)):
                candidates.append((similarity, new))
        if candidates:
            similarity, new = max(candidates)
            missing.remove(old)
            extra.remove(new)
            renamed.append({"from": old, "to": new, "confidence": round(similarity, 3)})
    type_changes = []
    for col in ref_cols:
        if col in in_cols and not _type_agrees((reference.get("types") or {}).get(col),
                                               (incoming.get("types") or {}).get(col)):
            type_changes.append({"column": col, "from": (reference.get("types") or {}).get(col),
                                 "to": (incoming.get("types") or {}).get(col)})
    # Reordering is deliberately reported as a separate, non-conflicting
    # fact.  Upload-time validation does not need to warn about it, but the
    # version-diff consumer reuses this comparator and adds the index detail.
    reordered = [
        {"column": column, "from_index": ref_cols.index(column),
         "to_index": in_cols.index(column)}
        for column in ref_cols
        if column in in_cols and ref_cols.index(column) != in_cols.index(column)
    ]
    return {"columns_missing": missing, "columns_extra": extra, "columns_renamed": renamed,
            "type_changes": type_changes, "reordered": reordered}


def compare(reference_schema: Any, incoming_schema: Any, kind: str) -> dict:
    """Return the complete, warn-only schema conflict contract."""
    reference = _tables(reference_schema, kind)
    incoming = _tables(incoming_schema, kind)
    ref_names = set(reference)
    in_names = set(incoming)
    tables_removed = sorted(ref_names - in_names) if kind == "database" else []
    tables_added = sorted(in_names - ref_names) if kind == "database" else []
    table_names = sorted(ref_names | in_names)
    per_table = {}
    for table in table_names:
        diff = (_table_diff(reference[table], incoming[table])
                if table in ref_names and table in in_names
                else {"columns_missing": [], "columns_extra": [],
                      "columns_renamed": [], "type_changes": [], "reordered": []})
        per_table[table] = diff
    ref_count = sum(len(spec.get("columns") or []) for spec in reference.values())
    in_count = sum(len(spec.get("columns") or []) for spec in incoming.values())
    changed = bool(tables_added or tables_removed or ref_count != in_count or any(
        d["columns_missing"] or d["columns_extra"] or d["columns_renamed"] or d["type_changes"]
        for d in per_table.values()))
    return {
        "tables_added": tables_added,
        "tables_removed": tables_removed,
        "per_table": per_table,
        "column_count_change": {"from": ref_count, "to": in_count},
        "is_match": not changed,
    }


def affected_columns(result: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for table, diff in (result.get("per_table") or {}).items():
        out.update((table, c) for c in diff.get("columns_missing") or [])
        out.update((table, c) for c in diff.get("columns_extra") or [])
        out.update((table, pair.get("from")) for pair in diff.get("columns_renamed") or [])
        out.update((table, c.get("column")) for c in diff.get("type_changes") or [])
    return {(t, c) for t, c in out if c}


def messages(result: dict) -> list[str]:
    """Human-readable differences, including the exact required type syntax."""
    out = []
    out.extend(f"table removed: {table}" for table in result.get("tables_removed") or [])
    out.extend(f"table added: {table}" for table in result.get("tables_added") or [])
    for table, diff in (result.get("per_table") or {}).items():
        out.extend(f"{table}: column missing: {col}" for col in diff.get("columns_missing") or [])
        out.extend(f"{table}: column added: {col}" for col in diff.get("columns_extra") or [])
        out.extend(f"{table}: column likely renamed: {p['from']} → {p['to']} (confidence {p['confidence']:.3f})"
                   for p in diff.get("columns_renamed") or [])
        out.extend(f"{table}: {p['column']}: {p.get('from')} → {p.get('to')}"
                   for p in diff.get("type_changes") or [])
    return out
