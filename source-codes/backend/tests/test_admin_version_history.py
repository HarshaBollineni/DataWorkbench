"""0.5.0 Step 3c (ADM-06/07, R-08) — Admin's asset version-history endpoint:
``GET /api/admin/assets`` (the picker) and
``GET /api/admin/assets/{asset_id}/history`` (``backend/assets/history.py``,
mounted in ``routers/admin.py``).

Standalone-runnable (``python -m unittest tests.test_admin_version_history``),
same SYSTEM_DB_PATH-at-import sandboxing convention as the sibling S3
test files, and the same "call the route function directly" pattern
``test_admin_reset.py``/``test_asset_identity.py`` already established (no
TestClient anywhere in this suite; FastAPI's route decorators return the
undecorated callable, so ``admin.asset_version_history(...)`` is a plain
Python call that still exercises the real admin gate and the PLT-04
sanitizer).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-admin-version-history.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

from fastapi import HTTPException  # noqa: E402

import system_db as s  # noqa: E402
from assets import service as assets_service  # noqa: E402
from routers import admin, auth  # noqa: E402
from seeds.taxonomy_seed import seed_platform, seed_taxonomy  # noqa: E402


def _make_user(username: str, roles: list[str]) -> str:
    s.upsert("users", {
        "username": username, "password": "pw", "name": username,
        "email": f"{username}@example.com", "function": "", "role": "",
        "salutation": "", "call_name": username, "ai_personality": "professional",
        "theme": "minimalist", "authz_roles": roles,
    })
    return auth.login(auth.LoginRequest(username=username, password="pw"))["token"]


class TwoVersionsThreeSnapshotsTests(unittest.TestCase):
    """Acceptance criterion 1 / 3-A17 — for an asset with 2 versions x 3
    snapshots, the history view returns 2 version groups with EVERY
    snapshot (active and superseded), each carrying timestamp, actor,
    intent and a one-line change summary."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.admin_token = _make_user("history_admin_1", ["user", "admin"])

    def test_two_versions_three_snapshots_all_present_with_full_fields(self):
        # `resets` is asset-agnostic (ADM-07 — every factory_reset row, not
        # scoped to one asset), so when this file runs as part of the FULL
        # suite (all test modules share one process-wide `system_db` import
        # — see EpochBoundaryTests's own comment on this), a sibling test
        # FILE may already have performed a reset before this one runs. This
        # test performs no reset itself, so the only honest assertion is
        # "unchanged from whatever it was", never "zero".
        resets_before = len(s.query("transaction_log", event="factory_reset"))

        asset = assets_service.create_asset("dataset", "hist-test-1", "period", actor="alice")
        asset_id = asset["asset_id"]
        # Version 1: two snapshots (fresh + add_period).
        assets_service.add_snapshot(asset_id, intent="fresh", actor="alice",
                                    start_date="2026-01-31")
        assets_service.add_snapshot(asset_id, intent="add_period", actor="alice",
                                    start_date="2026-02-28")
        # Version 2: full replacement supersedes both of the above, adds
        # a third snapshot as the new version's own first snapshot.
        assets_service.add_snapshot(asset_id, intent="full_replacement", actor="bob",
                                    start_date="2026-03-31")

        payload = admin.asset_version_history(asset_id, f"Bearer {self.admin_token}")

        self.assertEqual(payload["asset"]["asset_id"], asset_id)
        self.assertEqual(payload["asset"]["system_id"], asset["system_id"])
        self.assertEqual(payload["asset"]["display_name"], asset["display_name"])

        versions = payload["versions"]
        self.assertEqual([v["version_no"] for v in versions], [1, 2])
        self.assertEqual(versions[0]["status"], "superseded")
        self.assertEqual(versions[1]["status"], "current")

        v1_snapshots = versions[0]["snapshots"]
        v2_snapshots = versions[1]["snapshots"]
        self.assertEqual(len(v1_snapshots), 2, "version 1 has both its original snapshots")
        self.assertEqual(len(v2_snapshots), 1, "version 2 has its own one snapshot")

        total_snapshots = len(v1_snapshots) + len(v2_snapshots)
        self.assertEqual(total_snapshots, 3, "every one of the 3 snapshots is present")

        # Superseded snapshots are NOT hidden — this view is explicitly the
        # one place they are meant to stay visible (AST-08).
        statuses = {row["snapshot_status"] for row in v1_snapshots}
        self.assertEqual(statuses, {"superseded"})
        self.assertEqual(v2_snapshots[0]["snapshot_status"], "active")

        # Every snapshot carries timestamp, actor, intent, and a one-line
        # human-language change summary (never blank, never a raw enum).
        for row in v1_snapshots + v2_snapshots:
            self.assertIsNotNone(row["uploaded_at"])
            self.assertIn(row["uploaded_by"], ("alice", "bob"))
            self.assertIn(row["intent"], ("fresh", "add_period", "full_replacement"))
            self.assertTrue(row["change_summary"].strip())
            self.assertNotEqual(row["change_summary"], row["intent"],
                               "change_summary must be a sentence, not the raw intent token")

        intents_v1 = {row["intent"] for row in v1_snapshots}
        self.assertEqual(intents_v1, {"fresh", "add_period"})
        self.assertEqual(v2_snapshots[0]["intent"], "full_replacement")

        # Version-level fields.
        for v in versions:
            self.assertIsNotNone(v["created_at"])
            self.assertTrue(v["reference_schema_summary"].strip())
            self.assertIsNotNone(v["change_summary"])

        self.assertEqual(len(payload["resets"]), resets_before,
                         "this test performs no reset, so the reset count is unchanged")

    def test_history_of_unknown_asset_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.asset_version_history("no-such-asset", f"Bearer {self.admin_token}")
        self.assertEqual(ctx.exception.status_code, 404)


class ListAssetsPickerTests(unittest.TestCase):
    """The GET /api/admin/assets picker — unfiltered (ADM-06 "for any
    asset"), unlike the SRC-04 landing-page dropdown."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.admin_token = _make_user("history_admin_2", ["user", "admin"])

    def test_lists_an_asset_regardless_of_lifecycle_status(self):
        asset = assets_service.create_asset("dataset", "picker-test-1", "none", actor=None)
        s.update("dq_assets", {"asset_id": asset["asset_id"]}, {"lifecycle_status": "complete"})

        rows = admin.list_assets_for_history(f"Bearer {self.admin_token}")
        ids = {r["asset_id"] for r in rows}
        self.assertIn(asset["asset_id"], ids, "a 'complete' asset must still be pickable in Admin")

    def test_requires_admin(self):
        plain_token = _make_user("picker_plain_user", ["user"])
        with self.assertRaises(HTTPException) as ctx:
            admin.list_assets_for_history(f"Bearer {plain_token}")
        self.assertEqual(ctx.exception.status_code, 403)


class EpochBoundaryTests(unittest.TestCase):
    """Acceptance criterion 2 / 3-A18 / R-08 — both reset actions appear in
    the same chronological list with timestamp and ADM-02-grouped-ready
    counts (the raw `deleted` dict the frontend feeds through
    ui/src/lib/resetLabels.js's summarizeReset — reused, not rebuilt), and
    creating an asset, resetting, then creating another asset with the SAME
    reissued system_id shows exactly one boundary row (the one reset) in
    the post-reset asset's history."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.admin_token = _make_user("epoch_history_admin", ["user", "admin"])

    def test_exactly_one_boundary_row_between_two_same_id_assets_from_different_epochs(self):
        # A clean epoch boundary first, so this test's own asset_1 is the
        # first thing allocated in it — independent of whatever a sibling
        # TestCase in this shared sandboxed DB allocated earlier (unittest/
        # pytest give no ordering guarantee across classes).
        admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {self.admin_token}")

        asset_1 = assets_service.create_asset("dataset", "epoch-test", "none", actor="alice")
        # Confirm asset_1's OWN history, pre-reset, carries no reset rows
        # from AFTER its own creation yet (resets from EARLIER epochs may
        # already be in transaction_log — that table is never cleared).
        pre_reset_payload = admin.asset_version_history(asset_1["asset_id"], f"Bearer {self.admin_token}")
        resets_before = len(pre_reset_payload["resets"])

        result = admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {self.admin_token}")
        self.assertTrue(result["ok"])

        asset_2 = assets_service.create_asset("dataset", "epoch-test", "none", actor="alice")
        self.assertEqual(asset_2["system_id"], asset_1["system_id"],
                         "the human-quotable ID reissues from a clean baseline after a reset (D-29)"
                         " — asset_1 was the FIRST asset allocated after this test's own clean reset,"
                         " so asset_2 (also first-after-a-reset) must reissue the identical system_id")
        self.assertNotEqual(asset_1["asset_id"], asset_2["asset_id"],
                            "two DISTINCT assets, same reissued system_id, different epochs")

        post_reset_payload = admin.asset_version_history(asset_2["asset_id"], f"Bearer {self.admin_token}")
        resets = post_reset_payload["resets"]
        self.assertEqual(len(resets), resets_before + 1,
                         "exactly ONE new boundary row — the one reset that just happened —"
                         " added since asset_1's own pre-reset view")

        boundary = resets[-1]
        self.assertEqual(boundary["grade"], "surgical")
        self.assertIsNotNone(boundary["at"])
        self.assertIn("id_epoch", boundary)
        self.assertGreaterEqual(boundary["id_epoch"].get("asset_dataset", 0), 2,
                                "id_epoch names the counter value BEFORE the reset — asset_1 had"
                                " already consumed DS0001, so the pre-reset counter was >= 2")

        # The boundary sits chronologically BEFORE asset_2's own asset_created
        # event/version — asset_2 was created strictly after the reset ran.
        asset_2_created_at = post_reset_payload["versions"][0]["created_at"]
        self.assertLessEqual(boundary["at"], asset_2_created_at)

        # asset_1's own record is gone (the reset deletes dq_assets/
        # dq_asset_versions/dq_asset_events for the epoch it closes) — the
        # single surviving trace of its existence is the reset's own
        # transaction_log row, which is exactly what makes it "the boundary".
        with self.assertRaises(HTTPException) as ctx:
            admin.asset_version_history(asset_1["asset_id"], f"Bearer {self.admin_token}")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_deleted_counts_are_raw_and_feed_the_one_shared_label_map(self):
        """The backend must NOT re-label the reset counts (that would be a
        second label map) — it returns the raw table-name-keyed dict
        unchanged, exactly as `transaction_log.payload['deleted']` stored
        it, for the frontend's existing `summarizeReset` to render."""
        asset = assets_service.create_asset("dataset", "raw-counts-test", "none", actor=None)
        admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {self.admin_token}")
        asset_2 = assets_service.create_asset("dataset", "raw-counts-test-2", "none", actor=None)
        payload = admin.asset_version_history(asset_2["asset_id"], f"Bearer {self.admin_token}")
        deleted = payload["resets"][-1]["deleted"]
        self.assertIsInstance(deleted, dict)
        self.assertIn("dq_assets", deleted, "raw table-name keys, not pre-labelled")


class AdminAuthRequiredTests(unittest.TestCase):
    """Acceptance criterion 3 / 3-A(admin authorization) — a non-admin
    session gets 401/403, never the history payload."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.admin_token = _make_user("auth_history_admin", ["user", "admin"])
        cls.plain_token = _make_user("auth_history_plain", ["user"])

    def test_non_admin_session_is_403(self):
        asset = assets_service.create_asset("dataset", "auth-test-1", "none", actor=None)
        with self.assertRaises(HTTPException) as ctx:
            admin.asset_version_history(asset["asset_id"], f"Bearer {self.plain_token}")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_no_auth_is_401(self):
        asset = assets_service.create_asset("dataset", "auth-test-2", "none", actor=None)
        with self.assertRaises(HTTPException) as ctx:
            admin.asset_version_history(asset["asset_id"], None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_picker_non_admin_session_is_403(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.list_assets_for_history(f"Bearer {self.plain_token}")
        self.assertEqual(ctx.exception.status_code, 403)


class SanitizerTests(unittest.TestCase):
    """PLT-04 — an unexpected exception from the history aggregation must
    never leak its text to the client; the response is a generic 500."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.admin_token = _make_user("sanitizer_history_admin", ["user", "admin"])

    def test_unexpected_exception_returns_generic_500(self):
        asset = assets_service.create_asset("dataset", "sanitizer-test-1", "none", actor=None)
        from assets import history as history_mod
        original = history_mod.asset_history

        def _boom(_asset_id):
            raise RuntimeError("sqlite3.OperationalError: disk I/O error at C:/secret/path.db")

        history_mod.asset_history = _boom
        try:
            with self.assertRaises(HTTPException) as ctx:
                admin.asset_version_history(asset["asset_id"], f"Bearer {self.admin_token}")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertEqual(ctx.exception.detail, "internal error")
        finally:
            history_mod.asset_history = original


if __name__ == "__main__":
    unittest.main()
