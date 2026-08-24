"""Pure, capture-only measures over ``usage_events``.

No function in this module reads a live asset, snapshot, diagnostic or user
table.  A connection may be supplied by tests; otherwise one read-only query
connection is opened through system_db.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import system_db as s


def _rows(conn=None) -> list[dict[str, Any]]:
    if conn is not None:
        return [dict(row) for row in conn.execute("SELECT * FROM usage_events ORDER BY at,event_id").fetchall()]
    return s.query("usage_events", order_by="at, event_id")


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("detail_json") or {}
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def overall_usage(conn=None) -> dict[str, int]:
    return dict(Counter(row["event_type"] for row in _rows(conn)))


def usage_per_user(conn=None) -> dict[str, int]:
    return dict(Counter(row["actor"] for row in _rows(conn)))


def usage_over_time(bucket: str = "day", conn=None) -> list[dict[str, Any]]:
    if bucket not in {"day", "week", "month"}:
        raise ValueError("bucket must be day, week or month")
    counts = Counter()
    for row in _rows(conn):
        dt = datetime.fromisoformat(row["at"])
        if bucket == "day": key = dt.date().isoformat()
        elif bucket == "month": key = dt.strftime("%Y-%m")
        else: key = (dt.date().fromordinal(dt.date().toordinal() - dt.weekday())).isoformat()
        counts[key] += 1
    return [{"bucket": key, "count": counts[key]} for key in sorted(counts)]


def test_preferences(conn=None) -> dict[str, int]:
    return dict(Counter(str(_detail(r).get("test_id")) for r in _rows(conn)
                        if r["event_type"] == "diagnostic_run_started" and _detail(r).get("test_id") is not None))


def diagnostic_preferences(conn=None) -> dict[str, int]:
    return dict(Counter(str(_detail(r).get("diagnostic_id")) for r in _rows(conn)
                        if r["event_type"] == "diagnostic_run_started" and _detail(r).get("diagnostic_id") is not None))


def use_case_frequency(conn=None) -> dict[str, int]:
    return dict(Counter(str(_detail(r).get("use_case")) for r in _rows(conn)
                        if r["event_type"] == "use_case_set" and _detail(r).get("use_case") is not None))


def reuse_depth(conn=None) -> dict[str, Any]:
    rows = _rows(conn)
    created = sum(r["event_type"] == "asset_created" for r in rows)
    selected = sum(r["event_type"] == "asset_selected" for r in rows)
    return {"new_assets": created, "existing_selections": selected,
            "selection_to_creation": selected / created if created else 0.0}


def time_to_ready(conn=None) -> dict[str, Any]:
    starts = {r["object_id"]: r for r in _rows(conn) if r["event_type"] == "upload_started"}
    durations = []
    for row in _rows(conn):
        if row["event_type"] != "snapshot_ready" or row["object_id"] not in starts:
            continue
        durations.append((datetime.fromisoformat(row["at"]) - datetime.fromisoformat(starts[row["object_id"]]["at"])).total_seconds())
    return {"count": len(durations), "seconds": durations,
            "mean_seconds": sum(durations) / len(durations) if durations else None}


def override_rate(conn=None) -> dict[str, Any]:
    rows = _rows(conn)
    type_overrides = sum(r["event_type"] == "type_override" for r in rows)
    schema_overrides = sum(r["event_type"] == "schema_warning_overridden" for r in rows)
    denominator = sum(r["event_type"] in {"type_override", "schema_warning_overridden", "snapshot_ready"} for r in rows)
    return {"type_overrides": type_overrides, "schema_warning_overrides": schema_overrides,
            "total_overrides": type_overrides + schema_overrides,
            "rate": (type_overrides + schema_overrides) / denominator if denominator else 0.0}


def abandonment_points(conn=None) -> dict[str, int]:
    return dict(Counter(str(_detail(r).get("step", "unknown")) for r in _rows(conn)
                        if r["event_type"] == "upload_abandoned"))


def snapshot_cadence(conn=None) -> dict[str, int]:
    return dict(Counter(r.get("workflow_context") for r in _rows(conn)
                        if r["event_type"] == "snapshot_added" and r.get("workflow_context")))


def dictionary_coverage_trend(conn=None) -> list[dict[str, Any]]:
    buckets = defaultdict(lambda: {"bound": 0, "created": 0})
    for row in _rows(conn):
        if row["event_type"] not in {"dictionary_bound", "dictionary_version_created"}:
            continue
        bucket = row["at"][:10]
        buckets[bucket]["bound" if row["event_type"] == "dictionary_bound" else "created"] += 1
    return [{"bucket": key, **buckets[key]} for key in sorted(buckets)]


def finding_disposition_split(conn=None) -> dict[str, int]:
    return dict(Counter(str(_detail(r).get("disposition") or _detail(r).get("action"))
                        for r in _rows(conn) if r["event_type"] == "finding_disposed"))


def rerun_rate_after_replacement(conn=None) -> dict[str, Any]:
    rows = _rows(conn)
    # The ingest call site marks a run explicitly when it is a replacement
    # rerun.  Inferring this from an asset-wide context would count every
    # historical run after the first replacement, which is not the measure.
    reruns = sum(r["event_type"] == "diagnostic_run_started" and
                 bool(_detail(r).get("after_replacement")) for r in rows)
    runs = sum(r["event_type"] == "diagnostic_run_started" for r in rows)
    return {"runs": runs, "reruns_after_replacement": reruns,
            "rate": reruns / runs if runs else 0.0}


def restore_frequency(conn=None) -> dict[str, int]:
    return dict(Counter(r.get("workflow_context") for r in _rows(conn)
                        if r["event_type"] == "version_restored" and r.get("workflow_context")))
