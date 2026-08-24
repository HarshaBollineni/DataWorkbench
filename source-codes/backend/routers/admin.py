"""Plan 4 / F14-F16 — admin: session-gated (authz role) user CRUD + scoped reset.

The admin gate is now the logged-in session + an `admin` authz role (F14), not a
standalone password. Admins manage users (F15), trigger the surgical
demo-reset (F16, implemented in ``system_db.reset_demo``), and — WSP-08 / D-19,
plan Phase 2.9 — the full factory-reset pair (surgical / wipe) below.
"""
from __future__ import annotations

import functools
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import system_db as s
from routers.auth import current_user, is_admin

router = APIRouter(prefix="/api/admin")
logger = logging.getLogger(__name__)


def _now() -> str:
    return s.now_ist()


def _require_admin(authorization: str | None) -> dict:
    """Resolve the session and enforce the `admin` authz role (F14)."""
    user = current_user(authorization)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def _plt04_sanitize(fn):
    """PLT-04 error sanitizer for new admin routes.

    Wraps a route handler so any exception that is NOT a deliberately raised
    ``HTTPException`` (i.e. every genuinely unexpected failure — a bug, a
    disk error, an unhandled edge case) surfaces to the client as a generic
    HTTP 500 ``{"detail": "internal error"}`` — never the raw exception text,
    which can leak internals (stack traces, file paths, SQL, secrets). The
    real exception is still logged server-side via ``logger.exception``.
    ``HTTPException`` instances (401/403/400/404/...) are deliberate,
    already-sanitized responses and pass through unchanged.

    This is the PLT-04 sanitizer idiom for new routes — Phases 4-6 should
    decorate their new handlers with this same helper rather than
    re-implementing sanitization per endpoint.
    """
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled error in admin route %s", fn.__name__)
            raise HTTPException(status_code=500, detail="internal error") from None
    return _wrapped


def _public(u: dict) -> dict:
    pub = {k: v for k, v in u.items() if k != "password"}
    pub["authz_roles"] = u.get("authz_roles") or []
    return pub


# --- F15: user management ----------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str
    name: str = ""
    email: str = ""
    function: str = ""
    role: str = ""
    authz_roles: list[str] = ["user"]


@router.get("/users")
def list_users(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return [_public(u) for u in s.query("users", order_by="username")]


@router.post("/users")
def create_user(body: UserCreate, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if s.query_one("users", username=username):
        raise HTTPException(status_code=409, detail=f"User '{username}' already exists")
    # Only the recognised authz roles are persisted. kb_editor/kb_reviewer
    # (RCA Stage 2 — docs/rca/00-contracts.md §9.2) are separate from
    # admin: admin does not inherit knowledge upload/publish rights.
    roles = [r for r in body.authz_roles if r in {"user", "admin", "kb_editor", "kb_reviewer"}] or ["user"]
    s.upsert("users", {
        "username": username, "password": body.password, "name": body.name,
        "email": body.email, "function": body.function, "role": body.role,
        "salutation": "", "call_name": body.name.split(" ")[0] if body.name else username,
        "ai_personality": "professional", "theme": "minimalist", "authz_roles": roles,
    })
    s.insert("transaction_log", {"ts": _now(), "actor": "admin",
                                 "event": "create_user", "payload": {"username": username}})
    return {"ok": True, "user": _public(s.query_one("users", username=username))}


@router.delete("/users/{username}")
def delete_user(username: str, authorization: str | None = Header(default=None)):
    actor = _require_admin(authorization)
    target = s.query_one("users", username=username)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if username == actor["username"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    # Block removing the last admin (no admin lockout).
    if is_admin(target):
        remaining_admins = [u for u in s.query("users")
                            if is_admin(u) and u["username"] != username]
        if not remaining_admins:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    s.delete("users", username=username)
    s.insert("transaction_log", {"ts": _now(), "actor": actor["username"],
                                 "event": "delete_user", "payload": {"username": username}})
    return {"ok": True, "deleted": username}


# --- WSP-08 / D-19 (plan Phase 2.9): admin factory reset ---------------------
# Server-side type-to-confirm — the exact phrase per grade, checked here (never
# trust a client-side disabled button alone).
_CONFIRM_PHRASES = {
    "development": "WIPE DEVELOPMENT",
    "diagnostics": "WIPE DIAGNOSTICS",
    "surgical": "RESET",
    "wipe": "WIPE EVERYTHING",
}


class FactoryResetRequest(BaseModel):
    grade: str
    confirm: str
    legacy_asset_ids: list[str] = Field(default_factory=list)
    legacy_run_ids: list[str] = Field(default_factory=list)
    diagnostic_run_ids: list[str] = Field(default_factory=list)
    wipe_all_diagnostics: bool = False


@router.get("/development-artifacts")
@_plt04_sanitize
def development_artifact_candidates(
    authorization: str | None = Header(default=None),
):
    """Return cleanup-eligible roots and legacy roots needing human review."""
    _require_admin(authorization)
    s.init_schema()
    assets = s.execute(
        "SELECT asset_id, system_id, display_name, artifact_origin "
        "FROM dq_assets WHERE artifact_origin IN ('development', 'unclassified') "
        "ORDER BY system_id, display_name"
    )
    runs = s.execute(
        "SELECT dr.run_id, dr.diagnostic_id, dr.status, dr.created_at, "
        "dr.artifact_origin, i.name AS asset_name, a.system_id "
        "FROM diag_runs dr LEFT JOIN dq_items i ON i.item_id=dr.item_id "
        "LEFT JOIN dq_assets a ON a.asset_id=i.dataset_family_id "
        "WHERE dr.artifact_origin IN ('development', 'unclassified') "
        "ORDER BY dr.created_at DESC, dr.run_id DESC"
    )
    return {"assets": assets, "runs": runs}


@router.get("/diagnostic-runs")
@_plt04_sanitize
def diagnostic_run_candidates(
    authorization: str | None = Header(default=None),
):
    """List every diagnostic run for the scoped diagnostics reset UI."""
    _require_admin(authorization)
    s.init_schema()
    return {"runs": s.execute(
        "SELECT dr.run_id, dr.diagnostic_id, dr.status, dr.created_at, "
        "dr.finished_at, dr.artifact_origin, i.name AS asset_name, a.system_id "
        "FROM diag_runs dr LEFT JOIN dq_items i ON i.item_id=dr.item_id "
        "LEFT JOIN dq_assets a ON a.asset_id=i.dataset_family_id "
        "ORDER BY dr.created_at DESC, dr.run_id DESC"
    )}


@router.post("/factory-reset")
@_plt04_sanitize
def factory_reset(body: FactoryResetRequest, authorization: str | None = Header(default=None)):
    """Admin-only factory reset, two grades:

    - ``surgical`` (system_db.reset_demo): items + every derived artefact
      (plans/results/scores/issues/RCA cases/item-keyed tags/uploads) are
      removed; users, taxonomy, and the knowledge base survive.
    - ``wipe`` (system_db.wipe_all_items): blank slate — everything surgical
      clears PLUS the knowledge base, all ingested inventory, and every
      platform seed table (immediately re-seeded so the app stays usable).
      Only users/sessions, transaction_log, feature_flags, and the schema
      itself survive a wipe.

    Requires typing the exact confirm phrase for the requested grade
    (``RESET`` / ``WIPE EVERYTHING``) — a wrong or missing phrase is a 400
    and deletes nothing. On success, an append-only audit row is written to
    transaction_log (actor + grade + per-table counts removed, PLT-05) AFTER
    the delete completes, so it survives even a full wipe.

    0.5.0 AST-01/D-29/P-10: both reset grades also restart the human-quotable
    asset-ID counters (``id_sequences``) from a clean baseline. The counters'
    pre-delete values are captured BEFORE the reset call (there is nothing
    left to read afterwards — the reset itself deletes them) and carried
    into the same post-delete audit row as ``id_epoch``, so that row is the
    legible boundary between one ID generation and the next (R-08).
    """
    actor = _require_admin(authorization)
    grade = body.grade
    if grade not in _CONFIRM_PHRASES:
        raise HTTPException(
            status_code=400,
            detail="grade must be 'development', 'diagnostics', 'surgical', or 'wipe'",
        )
    expected = _CONFIRM_PHRASES[grade]
    if body.confirm != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Type '{expected}' exactly to confirm the {grade} reset. Nothing was deleted.",
        )
    if (body.legacy_asset_ids or body.legacy_run_ids) and grade != "development":
        raise HTTPException(
            status_code=400,
            detail="Legacy asset/run selections are accepted only for development cleanup",
        )
    if (body.diagnostic_run_ids or body.wipe_all_diagnostics) and grade != "diagnostics":
        raise HTTPException(
            status_code=400,
            detail="Diagnostic run scope is accepted only for diagnostics cleanup",
        )
    if grade == "diagnostics":
        if bool(body.diagnostic_run_ids) == bool(body.wipe_all_diagnostics):
            raise HTTPException(
                status_code=400,
                detail="Choose either one or more diagnostic runs, or all diagnostics",
            )
        if body.diagnostic_run_ids:
            requested_runs = set(body.diagnostic_run_ids)
            placeholders = ",".join("?" for _ in requested_runs)
            found_runs = {
                str(row["run_id"]) for row in s.execute(
                    f"SELECT run_id FROM diag_runs WHERE run_id IN ({placeholders})",
                    sorted(requested_runs),
                )
            }
            if found_runs != requested_runs:
                raise HTTPException(
                    status_code=400,
                    detail="One or more selected diagnostic runs are missing",
                )
    if grade == "development" and body.legacy_asset_ids:
        requested = set(body.legacy_asset_ids)
        with s.get_conn() as conn:
            rows = conn.execute(
                f"SELECT asset_id, artifact_origin FROM dq_assets WHERE asset_id IN "
                f"({','.join('?' for _ in requested)})",
                sorted(requested),
            ).fetchall()
            found = {str(row["asset_id"]): row["artifact_origin"] for row in rows}
            invalid = sorted(asset_id for asset_id in requested
                             if found.get(asset_id) != "unclassified")
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail="One or more selected legacy assets are missing or no longer unclassified.",
                )
            conn.execute(
                f"UPDATE dq_assets SET artifact_origin='development' WHERE asset_id IN "
                f"({','.join('?' for _ in requested)})",
                sorted(requested),
            )
            conn.execute(
                f"UPDATE dq_items SET artifact_origin='development' WHERE dataset_family_id IN "
                f"({','.join('?' for _ in requested)})",
                sorted(requested),
            )
            conn.commit()
    if grade == "development" and body.legacy_run_ids:
        requested = set(body.legacy_run_ids)
        with s.get_conn() as conn:
            rows = conn.execute(
                f"SELECT run_id, artifact_origin FROM diag_runs WHERE run_id IN "
                f"({','.join('?' for _ in requested)})",
                sorted(requested),
            ).fetchall()
            found = {str(row["run_id"]): row["artifact_origin"] for row in rows}
            invalid = sorted(run_id for run_id in requested
                             if found.get(run_id) != "unclassified")
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail="One or more selected legacy runs are missing or no longer unclassified.",
                )
            conn.execute(
                f"UPDATE diag_runs SET artifact_origin='development' WHERE run_id IN "
                f"({','.join('?' for _ in requested)})",
                sorted(requested),
            )
            conn.commit()
    from assets import identity  # noqa: PLC0415
    id_epoch = identity.epoch_snapshot()
    if grade == "development":
        result = s.wipe_development_artifacts()
    elif grade == "diagnostics":
        result = s.wipe_diagnostics(
            None if body.wipe_all_diagnostics else set(body.diagnostic_run_ids)
        )
    elif grade == "surgical":
        result = s.reset_demo()
    else:
        result = s.wipe_all_items()
    counts = result["deleted"]
    # Audit AFTER the delete (not before) so the wipe grade's own audit row —
    # written into a table the wipe deliberately preserves — survives it.
    s.insert("transaction_log", {
        "ts": _now(), "actor": actor["username"], "event": "factory_reset",
        "payload": {"grade": grade, "deleted": counts, "id_epoch": id_epoch,
                    "protected": result.get("protected")},
    })
    return {"ok": True, "grade": grade, "deleted": counts,
            "protected": result.get("protected")}


# --- 0.5.0 Step 3c (ADM-06/07): asset version history -----------------------
@router.get("/assets")
@_plt04_sanitize
def list_assets_for_history(authorization: str | None = Header(default=None)):
    """The version-history screen's asset picker. Deliberately unfiltered —
    ADM-06 says "for any asset", and this is an audit surface, not the
    workflow-selection picker SRC-04 governs (which excludes `complete` /
    `archived` / `requires_reupload`): an admin must be able to look up a
    completed or requires-reupload asset's history just as easily as a live
    one. Reuses `assets.reads.list_assets` (S3a) rather than a second query.
    """
    _require_admin(authorization)
    from assets import reads  # noqa: PLC0415
    return reads.list_assets()


@router.get("/assets/{asset_id}/history")
@_plt04_sanitize
def asset_version_history(asset_id: str, authorization: str | None = Header(default=None)):
    """ADM-06/07 — an asset's full version/snapshot history (both currently
    active and retained-for-audit-and-rollback snapshots — this is
    explicitly the one place retained snapshots stay visible, never hidden)
    plus every factory-reset action, in one payload the frontend renders as
    a single chronological list (R-08's epoch boundary).
    """
    _require_admin(authorization)
    from assets import history  # noqa: PLC0415
    try:
        return history.asset_history(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


# --- F16: surgical demo reset (no password; admin session is the gate) --------
@router.get("/context-memory")
def context_memory(object_key: str | None = None,
                   authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    rows = s.query("object_contexts")
    if object_key:
        rows = [r for r in rows if r.get("object_key") == object_key]
    return rows
