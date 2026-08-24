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
        "authz_roles": user.get("authz_roles") or [],
    }


def is_flag_enabled(tenant_id: str, key: str) -> bool:
    """docs/rca/00-contracts.md §8 — feature_flags is a config table (not
    an env var) so rollout is controllable per-flag without a redeploy."""
    import system_db as s
    row = s.query_one("feature_flags", key=key, tenant_id=tenant_id)
    return bool(row and row.get("enabled"))
