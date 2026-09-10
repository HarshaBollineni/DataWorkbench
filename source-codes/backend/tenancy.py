"""RCA Stage 0 — Principal resolution (docs/rca/00-contracts.md §1).

Every RCA/v3 service call takes an explicit Principal argument, resolved once
here from the Bearer session, and never re-derives tenant scope from a mutable
global. No endpoint accepts a caller-supplied tenant_id — it always comes from
this resolution.
"""
from __future__ import annotations

from routers.auth import current_user

DEFAULT_TENANT = "bootstrap"


def resolve_principal(authorization: str | None) -> dict:
    """Bearer token -> {username, tenant_id, authz_roles}. Raises the same
    401 as current_user() if the session is invalid or expired."""
    user = current_user(authorization)
    return {
        "username": user["username"],
        "tenant_id": user.get("tenant_id") or DEFAULT_TENANT,
        "tenant_resolved": bool(user.get("tenant_id")),
        "authz_roles": user.get("authz_roles") or [],
    }


def is_flag_enabled(tenant_id: str, key: str, *, timeout: float | None = None) -> bool:
    """docs/rca/00-contracts.md §8 — feature_flags is a config table (not
    an env var) so rollout is controllable per-flag without a redeploy."""
    import system_db as s
    if timeout is not None:
        # Advisory paths must not consume an execution worker's normal
        # connection budget or reconfigure SQLite's process-wide journal.
        remaining = min(0.1, float(timeout))
        if remaining <= 0:
            return False
        with s.get_conn(timeout=remaining, configure_journal=False) as conn:
            conn.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
            row = conn.execute(
                "SELECT enabled FROM feature_flags WHERE key=? AND tenant_id=?",
                (key, tenant_id),
            ).fetchone()
            return bool(row and row["enabled"])
    row = s.query_one("feature_flags", key=key, tenant_id=tenant_id)
    return bool(row and row.get("enabled"))
