"""AST-16..19: metadata-only three-tier version comparison.

This module intentionally has no pandas/file-reader import.  It reads the
stored schema, snapshot metadata and ingest-time fingerprints; file size is
the only filesystem operation and is a ``stat()``, never a file read.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import system_db as s

from . import schema_check
from . import reads


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except (TypeError, ValueError):
            return {}
    return value or {}


def _delta(a: Any, b: Any) -> dict[str, Any]:
    if a is None or b is None:
        return {"a": a, "b": b, "delta": None}
    return {"a": a, "b": b, "delta": b - a}


def _schema_tier(reference_a: Any, reference_b: Any, kind: str) -> dict[str, Any]:
    # AST-16/7-A9: this is the Step-4 comparator, not a second implementation.
    compared = schema_check.compare(reference_a, reference_b, kind)
    per_table: dict[str, Any] = {}
    for table, result in (compared.get("per_table") or {}).items():
        per_table[table] = {
            "added": result.get("columns_extra") or [],
            "removed": result.get("columns_missing") or [],
            "likely_renamed": result.get("columns_renamed") or [],
            "reordered": result.get("reordered") or [],
            "type_changes": result.get("type_changes") or [],
        }
    return {
        "tables_added": compared.get("tables_added") or [],
        "tables_removed": compared.get("tables_removed") or [],
        "per_table": per_table,
        "is_match": compared.get("is_match", False),
    }


def _ordered(rows: list[dict[str, Any]], basis: str) -> list[dict[str, Any]]:
    if basis == "period":
        return sorted(rows, key=lambda row: (row.get("start_date") or "", row.get("created_at") or ""))
    return sorted(rows, key=lambda row: row.get("created_at") or "")


def _fingerprint_tier(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]],
                      latest_a: dict[str, Any] | None, latest_b: dict[str, Any] | None,
                      fingerprints: dict[str, list[dict[str, Any]]], basis: str) -> dict[str, Any]:
    if latest_a is None or latest_b is None:
        return {"available": False,
                "unavailable_reason": "A version has no snapshot to fingerprint.",
                "per_column": {}, "comparison_basis": None}
    missing = []
    for snap in (latest_a, latest_b):
        if not fingerprints.get(snap["item_id"]):
            missing.append(snap["item_id"])
    statement = (f"comparing v{latest_a.get('version_no')}'s latest snapshot "
                 f"{latest_a.get('snapshot_label') or latest_a['item_id']} with v{latest_b.get('version_no')}'s latest snapshot "
                 f"{latest_b.get('snapshot_label') or latest_b['item_id']}")
    if missing:
        return {
            "available": False,
            "unavailable_reason": (
                "Distribution comparison unavailable for snapshot(s) "
                f"{', '.join(missing)} (ingested before fingerprinting)."),
            "comparison_basis": statement, "per_column": {},
        }
    by_a = {(r["table_name"], r["column_name"]): r for r in fingerprints[latest_a["item_id"]]}
    by_b = {(r["table_name"], r["column_name"]): r for r in fingerprints[latest_b["item_id"]]}
    per_column: dict[str, Any] = {}
    for key in sorted(set(by_a) | set(by_b)):
        left, right = by_a.get(key), by_b.get(key)
        label = f"{key[0]}.{key[1]}"
        if not left or not right:
            per_column[label] = {"available": False, "reason": "column exists in only one latest snapshot"}
            continue
        comparable = str(left.get("confirmed_type")) == str(right.get("confirmed_type"))
        entry = {
            "null_rate": _delta(left.get("null_rate"), right.get("null_rate")),
            "distinct_count": _delta(left.get("distinct_count"), right.get("distinct_count")),
            "min": {"a": left.get("min_value"), "b": right.get("min_value")},
            "max": {"a": left.get("max_value"), "b": right.get("max_value")},
            "mean": _delta(left.get("mean_value"), right.get("mean_value")) if comparable else None,
            "stddev": _delta(left.get("stddev_value"), right.get("stddev_value")) if comparable else None,
            "top_k": {"a": _json(left.get("top_k_json")), "b": _json(right.get("top_k_json"))},
            "histogram_shift": _histogram_shift(left.get("histogram_json"), right.get("histogram_json")),
        }
        if not comparable:
            entry["distribution_note"] = "Distribution is not comparable because the confirmed types changed."
            entry["mean"] = None
            entry["stddev"] = None
        per_column[label] = entry
    return {"available": True, "unavailable_reason": None,
            "comparison_basis": statement, "per_column": per_column}


def _histogram_shift(left: Any, right: Any) -> float | None:
    a, b = _json(left), _json(right)
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b) or not a:
        return None
    total_a = sum(float(bin_.get("count", 0)) for bin_ in a) or 1.0
    total_b = sum(float(bin_.get("count", 0)) for bin_ in b) or 1.0
    return round(sum(abs(float(x.get("count", 0)) / total_a - float(y.get("count", 0)) / total_b)
                     for x, y in zip(a, b)), 6)


def _metadata(asset_id: str, version_a: int, version_b: int) -> tuple[dict, dict, list, dict, dict]:
    """Load all diff metadata with one connection and a fixed query shape."""
    with s.get_conn() as conn:
        asset_row = conn.execute("SELECT * FROM dq_assets WHERE asset_id=?", (asset_id,)).fetchone()
        versions = conn.execute(
            "SELECT * FROM dq_asset_versions WHERE asset_id=? AND version_no IN (?,?)",
            (asset_id, version_a, version_b)).fetchall()
        snapshots = conn.execute(
            "SELECT * FROM dq_items WHERE dataset_family_id=? AND version_no IN (?,?)",
            (asset_id, version_a, version_b)).fetchall()
        ids = [row["item_id"] for row in snapshots]
        files = []
        fingerprints = []
        if ids:
            marks = ",".join("?" for _ in ids)
            files = conn.execute(f"SELECT item_id,path FROM dq_item_files WHERE item_id IN ({marks}) AND role='data'", ids).fetchall()
            fingerprints = conn.execute(
                f"SELECT * FROM dq_snapshot_fingerprints WHERE snapshot_id IN ({marks})", ids).fetchall()
    asset = dict(asset_row) if asset_row else {}
    version_rows = {row["version_no"]: dict(row) for row in versions}
    snapshot_rows = [dict(row) for row in snapshots]
    file_rows = [dict(row) for row in files]
    fp_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    for row in fingerprints:
        fp_by_snapshot.setdefault(row["snapshot_id"], []).append(dict(row))
    return asset, version_rows, snapshot_rows, {row["item_id"]: row for row in file_rows}, fp_by_snapshot


def diff_versions(asset_id: str, version_a: int, version_b: int) -> dict[str, Any]:
    """Compare two schema generations without opening an uploaded data file."""
    asset, versions, snapshots, files, fingerprints = _metadata(asset_id, int(version_a), int(version_b))
    if not asset:
        raise ValueError(f"No such asset: {asset_id!r}")
    if int(version_a) not in versions or int(version_b) not in versions:
        raise ValueError("Both requested versions must exist for this asset.")
    basis = asset.get("time_basis") or "none"
    a_rows = _ordered([r for r in snapshots if r.get("version_no") == int(version_a)], basis)
    b_rows = _ordered([r for r in snapshots if r.get("version_no") == int(version_b)], basis)
    latest_a = a_rows[-1] if a_rows else None
    latest_b = b_rows[-1] if b_rows else None
    if not latest_a or not latest_b:
        raise ValueError("Both versions must have at least one snapshot to compare.")
    def coverage(rows):
        if basis == "period":
            return {"start": min(r.get("start_date") for r in rows if r.get("start_date")),
                    "end": max(r.get("end_date") or r.get("start_date") for r in rows if r.get("start_date"))}
        return {"labels": [r.get("snapshot_label") for r in rows]}
    def size(rows):
        total = 0
        for row in rows:
            file_row = files.get(row["item_id"])
            if file_row:
                try:
                    total += Path(file_row["path"]).stat().st_size
                except (OSError, TypeError):
                    pass
        return total
    ref_a = versions[int(version_a)].get("reference_schema_json")
    ref_b = versions[int(version_b)].get("reference_schema_json")
    return {
        "asset_id": asset_id, "version_a": int(version_a), "version_b": int(version_b),
        "tier1_schema": _schema_tier(ref_a, ref_b, asset.get("kind", "dataset")),
        "tier2_shape": {
            "row_count": _delta(sum(int(r.get("row_count") or 0) for r in a_rows), sum(int(r.get("row_count") or 0) for r in b_rows)),
            "column_count": _delta(sum(int(r.get("column_count") or 0) for r in a_rows), sum(int(r.get("column_count") or 0) for r in b_rows)),
            "period_coverage": {"a": coverage(a_rows), "b": coverage(b_rows)},
            "file_size": _delta(size(a_rows), size(b_rows)),
            "snapshot_count": _delta(len(a_rows), len(b_rows)),
        },
        "tier3_distribution": _fingerprint_tier(a_rows, b_rows, latest_a, latest_b, fingerprints, basis),
        "row_level": {"available": False,
                      "reason": "Row-level diff is deferred in 0.5.0: no row-digest substrate was captured and it is not a default view (AST-18/OOS-13)."},
    }
