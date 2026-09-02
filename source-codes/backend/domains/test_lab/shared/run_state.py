"""Shared Test Lab run-state and append-only decision access."""
from __future__ import annotations

from typing import Any

import system_db as db

DRAFT, RUNNING, DONE, FAILED = "draft", "running", "done", "failed"


class ManifestError(RuntimeError):
    """A manifest operation that cannot proceed because its contract is invalid."""


def get_run(run_id: str) -> dict[str, Any]:
    row = db.query_one("diag_runs", run_id=run_id)
    if not row:
        raise KeyError(f"Unknown run: {run_id}")
    return row


def get_manifest(run_id: str) -> dict[str, Any]:
    return get_run(run_id)["manifest_json"]


def record_decision(run_id: str, kind: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Append one attributable decision to a diagnostic run."""
    timestamp = db.now_ist()
    row_id = db.insert("diag_run_decisions", {
        "run_id": run_id,
        "kind": kind,
        "payload_json": payload,
        "actor": actor,
        "ts": timestamp,
    })
    return {
        "id": row_id,
        "run_id": run_id,
        "kind": kind,
        "payload": payload,
        "actor": actor,
        "ts": timestamp,
    }


def list_decisions(run_id: str) -> list[dict[str, Any]]:
    return db.query("diag_run_decisions", run_id=run_id, order_by="id")
