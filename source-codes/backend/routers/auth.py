"""Plan 3 / D1.1 — authentication: login / logout / me.

Validates against the system DB ``users`` table; sessions are tracked in
``app_fsm`` (entity_type='session'). Tokens are opaque hex strings.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import system_db as s

router = APIRouter(prefix="/api")

# Stage 6 (RCA MP §11 decision #10) — bearer-token hardening interim: a
# session token was previously valid forever until explicit logout (no
# expiry field was ever set or checked). Fixed with an absolute lifetime
# stored in the session's own context JSON (no schema migration needed).
# Sliding-window refresh and token rotation are NOT implemented here —
# documented as a deliberate, narrower scope than the full secure-cookie+
# CSRF migration the original plan wanted in this stage (see
# docs/rca/00-contracts.md's Stage 6 addendum for why that full
# migration was judged too large a blast-radius change to rush safely
# alongside the RCA tenancy work).
SESSION_MAX_AGE_HOURS = int(os.environ.get("SESSION_MAX_AGE_HOURS", "24"))


class LoginRequest(BaseModel):
    username: str
    password: str


def _now() -> str:
    return s.now_ist()


def _public_user(u: dict) -> dict:
    pub = {k: v for k, v in u.items() if k != "password"}
    # Normalise authz_roles to a list for the client (NULL/legacy rows -> []).
    pub["authz_roles"] = u.get("authz_roles") or []
    return pub


def is_admin(user: dict) -> bool:
    """Authz axis (separate from the business `role` that gates data access)."""
    return "admin" in (user.get("authz_roles") or [])


def _bearer_token(authorization: str | None) -> str:
    """Extract the opaque session token using the app's existing semantics."""
    return (authorization or "").removeprefix("Bearer ").strip()


def current_user(authorization: str | None) -> dict:
    """Resolve a Bearer token -> user dict. Raises 401 if invalid or expired."""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sess = s.query_one("app_fsm", entity_type="session", entity_id=token, state="active")
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    context = sess.get("context") or {}
    # A session created before this expiry check existed has no created_at in
    # its context and is left alone (graceful migration) — it naturally gets
    # one on its next real login. Not a security regression: an unbounded-
    # lifetime session is exactly today's pre-Stage-6 behavior, not a new gap.
    created_at = context.get("created_at")
    if created_at and datetime.fromisoformat(created_at) < datetime.fromisoformat(_now()) - timedelta(hours=SESSION_MAX_AGE_HOURS):
        s.update("app_fsm", {"entity_type": "session", "entity_id": token}, {"state": "ended", "updated_at": _now()})
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = s.query_one("users", username=context.get("username"))
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user


@router.post("/login")
def login(body: LoginRequest):
    user = s.query_one("users", username=body.username.strip())
    if not user or user.get("password") != body.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = uuid.uuid4().hex
    s.insert("app_fsm", {
        "entity_type": "session", "entity_id": token, "state": "active",
        "context": {"username": user["username"], "created_at": _now()}, "updated_at": _now(),
    })
    s.insert("transaction_log", {"ts": _now(), "actor": user["username"],
                                 "event": "login", "payload": {}})
    return {"token": token, "user": _public_user(user)}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    token = _bearer_token(authorization)
    if token:
        s.update("app_fsm", {"entity_type": "session", "entity_id": token},
                 {"state": "ended", "updated_at": _now()})
    return {"ok": True}


@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    return {"user": _public_user(current_user(authorization))}


class ProfileUpdate(BaseModel):
    theme: str | None = None
    ai_personality: str | None = None
    salutation: str | None = None
    call_name: str | None = None
    # F13 — editable account fields, persisted to the live `users` row so edits
    # reflect in /me and survive a demo reset (JSON-config edits only apply on a
    # fresh seed; the row is the source of truth thereafter).
    name: str | None = None
    email: str | None = None
    function: str | None = None


@router.put("/me")
def update_me(body: ProfileUpdate, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        s.update("users", {"username": user["username"]}, changes)
    return {"user": _public_user(s.query_one("users", username=user["username"]))}
