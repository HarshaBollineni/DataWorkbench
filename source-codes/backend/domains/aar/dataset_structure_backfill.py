"""Explicit, tenant-scoped Slice-4 DSC migration scheduler.

This module intentionally has no startup hook.  An operator with the narrow
structure-admin scope invokes a bounded page; each eligible snapshot is
validated from retained governed artifacts before it can gain a migration
completion proof and a normal fenced materialization job.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

import system_db as db
from . import materialization_jobs

MIGRATION_VERSION = "dsc_slice4_v1"
MAX_PAGE_SIZE = 50
_CLOSED_FAILURE = "DSC_R_BACKFILL_PROFILE_INCOMPLETE"


class BackfillConfigurationError(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cursor_key() -> bytes:
    configured = os.environ.get("DSC_BACKFILL_CURSOR_SECRET")
    if configured:
        return configured.encode("utf-8")
    mode = os.environ.get("APP_ENV", "").strip().lower()
    if mode in {"development", "test"} or os.environ.get("PYTEST_CURRENT_TEST"):
        # Explicit development/test-only fallback; production must configure a
        # real secret so an operator cannot forge pagination state.
        return f"local:{db.SYS_DB_PATH}:dsc-slice4".encode("utf-8")
    raise BackfillConfigurationError("DSC backfill cursor secret is not configured")


def _cursor(value: str | None, *, tenant_id: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not value.startswith("dscbf1."):
        raise ValueError("cursor is invalid")
    try:
        encoded, signature = value[7:].split(".", 1)
        expected = hmac.new(_cursor_key(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc
    if not hmac.compare_digest(signature, expected) or set(payload) != {"v", "tenant", "after"}:
        raise ValueError("cursor is invalid")
    result = payload.get("after")
    if payload.get("v") != 1 or payload.get("tenant") != tenant_id or not isinstance(result, str) or not result or len(result) > 200:
        raise ValueError("cursor is invalid")
    return result


def _next_cursor(*, tenant_id: str, after: str | None) -> str | None:
    if not after:
        return None
    encoded = base64.urlsafe_b64encode(json.dumps({"v": 1, "tenant": tenant_id, "after": after}, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_cursor_key(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"dscbf1.{encoded}.{signature}"


def _schedule(item: dict[str, Any], *, tenant_id: str) -> tuple[str, dict[str, Any] | None]:
    """Validate and schedule one snapshot; no partial state exists on failure."""
    snapshot_id = item["item_id"]
    job, created = materialization_jobs.schedule_backfill(
        snapshot_id, tenant_id=tenant_id, migration_version=MIGRATION_VERSION,
    )
    if job is None:
        return "incomplete", None
    # A prior job may already have completed while this migration was waiting
    # to be explicitly authorized.  Initialize only from its projection.
    if job["status"] == "succeeded":
        from . import dataset_structure_review
        dataset_structure_review.complete_existing_backfill_projection(
            item, job_id=job["job_id"], generation=job["publication_generation"],
        )
    return "scheduled" if created else "reused", job


def run_backfill(*, tenant_id: str, cursor: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Schedule one bounded page for the authenticated operator's tenant."""
    if not tenant_id:
        raise ValueError("tenant is required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    _cursor_key()  # fail before any migration write when production is misconfigured
    after = _cursor(cursor, tenant_id=tenant_id)
    items = db.execute(
        "SELECT * FROM dq_items WHERE snapshot_status='active' AND ingest_status='ready' "
        "AND sourcing_tenant_id=? AND dataset_family_id IS NOT NULL AND item_id>? "
        "ORDER BY item_id LIMIT ?",
        (tenant_id, after, limit),
    )
    counts = {"examined": len(items), "scheduled": 0, "reused": 0, "incomplete": 0}
    for item in items:
        result, _job = _schedule(item, tenant_id=tenant_id)
        counts[result] += 1
    last = items[-1]["item_id"] if items else None
    return {"migration_version": MIGRATION_VERSION, "page": {"limit": limit,
            "next_cursor": _next_cursor(tenant_id=tenant_id, after=last) if last and len(items) == limit else None},
            "counts": counts, "closed_error_code": _CLOSED_FAILURE if counts["incomplete"] else None}
