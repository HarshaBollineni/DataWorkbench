"""Read-time staleness for artefacts bound to asset snapshots.

There is deliberately no stored stale flag. Snapshot status is the source of
truth, so restoring a version makes its artefacts current again automatically.
"""
from __future__ import annotations

from typing import Any

import system_db as s


def _snapshot(snapshot_id: str | None) -> dict[str, Any] | None:
    return s.query_one("dq_items", item_id=snapshot_id) if snapshot_id else None


def bound_snapshot_ids(artefact: dict[str, Any]) -> list[str]:
    """Resolve the snapshot bindings used by the product's read models."""
    ids: list[str] = []

    def add(value: Any) -> None:
        if value and value not in ids:
            ids.append(value)

    def add_run(run_id: str | None) -> None:
        if run_id:
            run = s.query_one("diag_runs", run_id=run_id)
            add(run.get("item_id") if run else None)

    def add_result(result_id: str | None) -> None:
        if result_id:
            result = s.query_one("diag_results", result_id=result_id)
            add_run(result.get("run_id") if result else None)

    for key in ("item_id", "snapshot_id"):
        add(artefact.get(key))
    add_run(artefact.get("run_id"))
    add_result(artefact.get("result_id"))

    # Dispositions and taxonomy assignments point at work products rather than
    # carrying an item_id. Resolve their target through the same read-time
    # graph, without storing a duplicate binding on the artefact.
    target_id = artefact.get("target_id") or artefact.get("object_id")
    target_type = (artefact.get("target_type") or artefact.get("object_type") or "").lower()
    if target_id:
        if target_type in {"finding", "diag_finding", "findings"}:
            finding = s.query_one("diag_findings", finding_id=target_id)
            if finding:
                add_run(finding.get("run_id"))
                add_result(finding.get("result_id"))
        elif target_type in {"result", "diag_result", "results"}:
            add_result(target_id)
        elif target_type in {"run", "diag_run", "runs"}:
            add_run(target_id)
        elif target_type in {"issue", "issues_v2", "tracked_issue", "tracked_issues_v2"}:
            issue = s.query_one("issues_v2", issue_row_id=target_id)
            add(issue.get("item_id") if issue else None)
        elif target_type in {"case", "rca_case", "rca_cases"}:
            case = s.query_one("rca_cases", case_id=target_id)
            if case:
                issue = s.query_one("issues_v2", issue_row_id=case.get("issue_row_id"))
                add(issue.get("item_id") if issue else None)
        else:
            # Tag rows can use one of the concrete object types listed in
            # system_db.py; unknown object types remain unbound rather than
            # guessing from an ID that may belong to another table.
            if target_type in {"dq_item", "item"}:
                add(target_id)
            elif target_type in {"plan_v2", "results_v2", "scores_v2"}:
                key = "row_id" if target_type == "plan_v2" else (
                    "result_id" if target_type == "results_v2" else "item_id"
                )
                row = s.query_one(target_type, **{key: target_id})
                add(row.get("item_id") if row else None)
            elif target_type == "issues_v2":
                row = s.query_one(target_type, issue_row_id=target_id)
                add(row.get("item_id") if row else None)
    return ids


def stale_sources(artefact: dict[str, Any]) -> list[dict[str, Any]]:
    sources = []
    for snapshot_id in bound_snapshot_ids(artefact):
        snapshot = _snapshot(snapshot_id)
        if snapshot and snapshot.get("snapshot_status") == "superseded":
            sources.append({
                "snapshot_id": snapshot_id,
                "label": snapshot.get("snapshot_label") or snapshot_id,
                "superseded_at": snapshot.get("superseded_at"),
            })
    return sources


def is_stale(artefact: dict[str, Any]) -> bool:
    """An artefact spanning multiple snapshots is stale if ANY is superseded."""
    return bool(stale_sources(artefact))


def annotate_stale(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate read-model rows through this one derivation helper."""
    out = []
    for row in rows:
        sources = stale_sources(row)
        out.append({**row, "stale": bool(sources), "stale_sources": sources})
    return out


def annotate_payload(payload: Any) -> Any:
    """Apply the same helper to nested diagnostic read-model payloads."""
    if isinstance(payload, list):
        return [annotate_payload(item) for item in payload]
    if isinstance(payload, dict):
        row = {key: annotate_payload(value) for key, value in payload.items()}
        if any(key in row for key in ("item_id", "snapshot_id", "run_id", "result_id")):
            sources = stale_sources(row)
            row["stale"] = bool(sources)
            row["stale_sources"] = sources
        return row
    return payload
