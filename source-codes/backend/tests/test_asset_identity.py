"""0.5.0 Step 3a (AST-01/02/04, P-02/P-03/P-10) — the human-quotable ID
scheme: allocate(), validate_alias(), compose_display_name(), and the
factory-reset epoch boundary.

Standalone-runnable (``python -m unittest tests.test_asset_identity``),
mirroring test_admin_reset.py's/test_delivery.py's SYSTEM_DB_PATH-at-import
sandboxing convention.
"""
from __future__ import annotations

import concurrent.futures
import inspect
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-asset-identity.db"
_TMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archimedes-test-asset-identity-uploads"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_UPLOAD_DIR.exists():
    shutil.rmtree(_TMP_UPLOAD_DIR)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOAD_DIR)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

from fastapi import HTTPException  # noqa: E402

import system_db as s  # noqa: E402
from assets import identity  # noqa: E402
from routers import admin, auth  # noqa: E402
from seeds.taxonomy_seed import seed_platform, seed_taxonomy  # noqa: E402


def _make_admin(username: str) -> str:
    s.upsert("users", {
        "username": username, "password": "pw", "name": username,
        "email": f"{username}@example.com", "function": "", "role": "",
        "salutation": "", "call_name": username, "ai_personality": "professional",
        "theme": "minimalist", "authz_roles": ["user", "admin"],
    })
    return auth.login(auth.LoginRequest(username=username, password="pw"))["token"]


class AllocateFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_sequential_allocation_within_a_scope(self):
        scope = f"asset_dataset"
        # Use a fresh scope-independent probe: allocate three in a row and
        # confirm strictly increasing sequential numbering with no gaps.
        first = identity.allocate(scope)
        second = identity.allocate(scope)
        third = identity.allocate(scope)
        n1, n2, n3 = (int(v[2:]) for v in (first, second, third))
        self.assertEqual([n2, n3], [n1 + 1, n1 + 2])

    def test_all_five_scope_formats(self):
        self.assertEqual(set(identity.SCOPES),
                         {"asset_dataset", "asset_database", "snapshot", "version", "dictionary_version"})
        self.assertRegex(identity.allocate("asset_dataset"), r"^DS\d{4,}$")
        self.assertRegex(identity.allocate("asset_database"), r"^DB\d{4,}$")
        self.assertRegex(identity.allocate("snapshot"), r"^SN\d{6,}$")
        self.assertRegex(identity.allocate("version"), r"^VR\d{5,}$")
        self.assertRegex(identity.allocate("dictionary_version"), r"^DV\d{5,}$")

    def test_width_grows_past_the_pad_without_truncating(self):
        conn = s.get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO id_sequences (scope, next_value, epoch_started_at) "
                "VALUES ('asset_dataset', 10000, ?)", (s.now_ist(),)
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(identity.allocate("asset_dataset"), "DS10000")

    def test_unknown_scope_rejected(self):
        with self.assertRaises(ValueError):
            identity.allocate("not_a_real_scope")

    def test_scope_for_kind(self):
        self.assertEqual(identity.scope_for_kind("dataset"), "asset_dataset")
        self.assertEqual(identity.scope_for_kind("database"), "asset_database")
        with self.assertRaises(ValueError):
            identity.scope_for_kind("spreadsheet")


class ConcurrentAllocationTests(unittest.TestCase):
    """Acceptance criterion 5 — allocate() never collides even called
    concurrently. Proven with real threads hitting the same scope, plus the
    UNIQUE index as the documented last line of defence (adversarial case 9)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_concurrent_calls_never_produce_a_duplicate_id(self):
        scope = "asset_database"
        n_calls = 60
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: identity.allocate(scope), range(n_calls)))
        self.assertEqual(len(results), n_calls)
        self.assertEqual(len(set(results)), n_calls, f"duplicate IDs allocated: {results}")
        numbers = sorted(int(v[2:]) for v in results)
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + n_calls)),
                         "allocation must be gap-free and collision-free under concurrency")

    def test_unique_index_on_system_id_exists_as_the_backstop(self):
        """AST-01: even if two callers somehow raced past allocate()'s own
        transaction (a different SQLite build, a network filesystem edge
        case), a duplicate system_id must fail LOUDLY at the database level,
        never silently succeed twice."""
        conn = s.get_conn()
        try:
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
            self.assertIn("ux_dq_assets_system_id", indexes)

            now = s.now_ist()
            conn.execute(
                "INSERT INTO dq_assets (asset_id, system_id, alias, display_name, kind, "
                "time_basis, current_version_no, created_at, updated_at) "
                "VALUES ('probe-a','DSPROBE','x','DSPROBE-x','dataset','none',1,?,?)",
                (now, now),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO dq_assets (asset_id, system_id, alias, display_name, kind, "
                    "time_basis, current_version_no, created_at, updated_at) "
                    "VALUES ('probe-b','DSPROBE','y','DSPROBE-y','dataset','none',1,?,?)",
                    (now, now),
                )
        finally:
            conn.rollback()
            conn.close()


class ValidateAliasTests(unittest.TestCase):
    """Acceptance criterion 6 and adversarial case 2 of the S3a task brief."""

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("")

    def test_rejects_spaces(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("retail pd")

    def test_rejects_forward_slash(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("retail/pd")

    def test_rejects_dot(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("retail.pd")

    def test_rejects_path_traversal_like_string(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("../etc")

    def test_rejects_percent_encoded_space(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("retail%20pd")

    def test_rejects_a_500_character_string(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("a" * 500)

    def test_rejects_emoji(self):
        with self.assertRaises(ValueError):
            identity.validate_alias("retail\U0001F600pd")

    def test_accepts_well_formed_aliases(self):
        for alias in ("a", "retail-pd", "retail_pd_2", "RETAIL-PD"):
            self.assertEqual(identity.validate_alias(alias), alias)

    def test_error_message_is_showable(self):
        try:
            identity.validate_alias("bad alias!")
        except ValueError as exc:
            self.assertTrue(str(exc).strip())
            self.assertNotIn("Traceback", str(exc))
        else:
            self.fail("expected ValueError")


class SanitizeAliasForMigrationTests(unittest.TestCase):
    def test_spaces_become_dashes(self):
        self.assertEqual(identity.sanitize_alias_for_migration("Retail PD Data"), "Retail-PD-Data")

    def test_symbols_are_dropped(self):
        self.assertEqual(identity.sanitize_alias_for_migration("Retail/PD.Data%20!"), "RetailPDData20")

    def test_whitespace_only_falls_back_to_asset(self):
        self.assertEqual(identity.sanitize_alias_for_migration("   "), "asset")

    def test_none_falls_back_to_asset(self):
        self.assertEqual(identity.sanitize_alias_for_migration(None), "asset")

    def test_empty_string_falls_back_to_asset(self):
        self.assertEqual(identity.sanitize_alias_for_migration(""), "asset")

    def test_never_raises(self):
        for bad in (None, "", "   ", "!!!!", "\U0001F600" * 20, "a" * 5000):
            identity.sanitize_alias_for_migration(bad)  # must not raise


class ComposeDisplayNameTests(unittest.TestCase):
    def test_composes_system_id_dash_alias(self):
        self.assertEqual(identity.compose_display_name("DS0007", "retail-pd"), "DS0007-retail-pd")

    def test_no_parse_display_name_function_exists_anywhere(self):
        """P-03 — 'if you find yourself writing one, you have a design
        error.' Structural (N) assertion: no callable in the assets package
        is named anything that looks like a display-name parser."""
        import assets.identity as identity_mod
        import assets.reads as reads_mod
        for mod in (identity_mod, reads_mod):
            for name, _ in inspect.getmembers(mod, inspect.isfunction):
                self.assertNotIn("parse_display_name", name.lower())
                self.assertFalse(
                    re.search(r"parse.*display.*name|split.*display.*name", name, re.IGNORECASE),
                    f"{mod.__name__}.{name} looks like a display-name parser (P-03 forbids one)",
                )


class EpochResetTests(unittest.TestCase):
    """Acceptance criterion 7 — both reset grades clear id_sequences; the
    next allocate() afterwards restarts at 1; the transaction_log
    factory_reset row's payload carries the pre-reset counters as id_epoch."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_surgical_reset_clears_counters_and_reissues_ds0001(self):
        token = _make_admin("epoch_surgical_admin")
        identity.allocate("asset_dataset")
        identity.allocate("asset_dataset")
        before = identity.epoch_snapshot()
        self.assertGreaterEqual(before.get("asset_dataset", 0), 3)

        result = admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {token}")
        self.assertTrue(result["ok"])
        self.assertIn("id_sequences", result["deleted"])
        self.assertGreaterEqual(result["deleted"]["id_sequences"], 1)

        self.assertEqual(s.query("id_sequences"), [])
        self.assertEqual(identity.allocate("asset_dataset"), "DS0001")

        audit_rows = [r for r in s.query("transaction_log", order_by="id")
                     if r["event"] == "factory_reset" and r["payload"].get("grade") == "surgical"]
        self.assertTrue(audit_rows)
        last = audit_rows[-1]
        self.assertIn("id_epoch", last["payload"])
        self.assertEqual(last["payload"]["id_epoch"].get("asset_dataset"), before["asset_dataset"])

    def test_wipe_clears_counters_and_reissues_db0001(self):
        token = _make_admin("epoch_wipe_admin")
        identity.allocate("asset_database")
        before = identity.epoch_snapshot()

        result = admin.factory_reset(
            admin.FactoryResetRequest(grade="wipe", confirm="WIPE EVERYTHING"),
            f"Bearer {token}")
        self.assertTrue(result["ok"])
        self.assertEqual(s.query("id_sequences"), [])
        self.assertEqual(identity.allocate("asset_database"), "DB0001")

        audit_rows = [r for r in s.query("transaction_log", order_by="id")
                     if r["event"] == "factory_reset" and r["payload"].get("grade") == "wipe"]
        self.assertTrue(audit_rows)
        self.assertEqual(audit_rows[-1]["payload"]["id_epoch"].get("asset_database"),
                        before["asset_database"])

    def test_id_reuse_within_an_epoch_never_recycles_a_freed_number(self):
        """Adversarial case 8 — create 3, supersede/delete nothing (no such
        surface exists yet — S3b), create a 4th within the SAME epoch: it
        must be DS0004, never a recycled DS0002. (Reuse ONLY happens across
        a factory-reset epoch boundary, proven above.)"""
        token = _make_admin("epoch_no_reuse_admin")
        admin.factory_reset(admin.FactoryResetRequest(grade="wipe", confirm="WIPE EVERYTHING"),
                            f"Bearer {token}")
        ids = [identity.allocate("asset_dataset") for _ in range(4)]
        self.assertEqual(ids, ["DS0001", "DS0002", "DS0003", "DS0004"])

    def test_no_auth_reset_leaves_counters_untouched(self):
        identity.allocate("dictionary_version")
        before = identity.epoch_snapshot()
        with self.assertRaises(HTTPException):
            admin.factory_reset(
                admin.FactoryResetRequest(grade="surgical", confirm="RESET"), None)
        self.assertEqual(identity.epoch_snapshot().get("dictionary_version"),
                        before.get("dictionary_version"))


if __name__ == "__main__":
    unittest.main()
