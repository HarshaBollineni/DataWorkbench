"""RCA Stage 6 — auth/session hardening behavioral tests.

Standalone-runnable (``python -m unittest tests.test_auth_security``);
follows the same SYSTEM_DB_PATH-at-import-time sandboxing convention as the
other backend test files.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-auth-security.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

from fastapi import HTTPException  # noqa: E402

import system_db as s  # noqa: E402
from routers import auth  # noqa: E402


def _make_user(username: str) -> None:
    s.upsert("users", {
        "username": username, "password": "pw", "name": username, "email": f"{username}@example.com",
        "function": None, "role": None, "salutation": None, "call_name": None,
        "ai_personality": None, "theme": None, "authz_roles": [],
    })


class SessionExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_fresh_session_is_valid(self):
        _make_user("fresh_user")
        result = auth.login(auth.LoginRequest(username="fresh_user", password="pw"))
        user = auth.current_user(f"Bearer {result['token']}")
        self.assertEqual(user["username"], "fresh_user")

    def test_session_past_max_age_is_rejected(self):
        _make_user("stale_user")
        result = auth.login(auth.LoginRequest(username="stale_user", password="pw"))
        token = result["token"]
        # Backdate the session's created_at past SESSION_MAX_AGE_HOURS —
        # simulates real time passing without an actual sleep.
        sess = s.query_one("app_fsm", entity_type="session", entity_id=token)
        backdated = sess["context"].copy()
        old_ts = (auth.datetime.fromisoformat(backdated["created_at"])
                 - timedelta(hours=auth.SESSION_MAX_AGE_HOURS + 1)).isoformat()
        backdated["created_at"] = old_ts
        s.update("app_fsm", {"entity_type": "session", "entity_id": token}, {"context": backdated})

        with self.assertRaises(HTTPException) as ctx:
            auth.current_user(f"Bearer {token}")
        self.assertEqual(ctx.exception.status_code, 401)
        # The expired session must actually be ended, not just rejected once —
        # a second call must fail the same way, not resurrect it.
        with self.assertRaises(HTTPException):
            auth.current_user(f"Bearer {token}")
        expired_sess = s.query_one("app_fsm", entity_type="session", entity_id=token)
        self.assertEqual(expired_sess["state"], "ended")

    def test_session_created_before_the_expiry_check_existed_is_left_alone(self):
        """Backward-compat: a session with no created_at in its context
        (i.e. logged in before this hardening shipped) must not suddenly
        start failing auth — that would log everyone out on deploy."""
        _make_user("legacy_session_user")
        token = "legacy-token-no-created-at"
        s.insert("app_fsm", {
            "entity_type": "session", "entity_id": token, "state": "active",
            "context": {"username": "legacy_session_user"}, "updated_at": s.now_ist(),
        })
        user = auth.current_user(f"Bearer {token}")
        self.assertEqual(user["username"], "legacy_session_user")

    def test_logout_ends_the_session(self):
        _make_user("logout_user")
        result = auth.login(auth.LoginRequest(username="logout_user", password="pw"))
        token = result["token"]
        auth.logout(f"Bearer {token}")
        with self.assertRaises(HTTPException):
            auth.current_user(f"Bearer {token}")

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.current_user("Bearer not-a-real-token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_no_token_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.current_user(None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_bearer_token_whitespace_is_handled_consistently(self):
        _make_user("spaced_token_user")
        result = auth.login(auth.LoginRequest(username="spaced_token_user", password="pw"))
        authorization = f"Bearer   {result['token']}  "
        self.assertEqual(auth.current_user(authorization)["username"], "spaced_token_user")
        auth.logout(authorization)
        with self.assertRaises(HTTPException):
            auth.current_user(authorization)


if __name__ == "__main__":
    unittest.main()
