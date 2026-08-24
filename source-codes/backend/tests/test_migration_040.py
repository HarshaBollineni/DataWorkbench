"""3-T4 — the Phase-3 framework-retirement drop: run twice, survivors intact.

Follows the suite's SYSTEM_DB_PATH-at-import-time sandboxing convention
(test_admin_reset.py is the model). Seeds a DB shaped like a 0.3.0 install
(old framework content + work-product keyed to retired tests + the record
classes that must survive), runs ``retire_old_framework()`` twice, and
asserts: dropped classes gone with counts; surviving classes bit-identical;
second run is a no-op that writes no second audit row (idempotent — M layer).
"""
from __future__ import annotations

import os
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-migration-040.db"
_TMP_SNAPSHOT = Path(tempfile.gettempdir()) / "archimedes-test-migration-040.pre-retirement-040.db"
_TMP_RESTORED = Path(tempfile.gettempdir()) / "archimedes-test-migration-040-restored.db"
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PRIVATE_SYSTEM_DB = "_test_migration_040_system_db"
_PRIVATE_MIGRATION = "_test_migration_040_impl"

# Assigned by setUpClass after loading private module copies.  Do not import
# production system_db here: pytest collection may already have cached it with
# another test's SYSTEM_DB_PATH.
s = None
retire_old_framework = None

SURVIVORS = ("dq_items", "dq_item_files", "dq_item_tables",
             "variable_inventory", "users", "kb_documents")
DROPPED = ("test_library", "fw_areas", "fw_tests", "fw_family_weights",
           "plan_v2", "results_v2", "scores_v2", "issues_v2",
           "tracked_issues_v2", "rca_cases")


def _count(table: str) -> int:
    return len(s.query(table))


def _schema_hash(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        schema = conn.execute("""SELECT type, name, tbl_name, sql
                              FROM sqlite_master
                              WHERE name NOT LIKE 'sqlite_%'
                              ORDER BY type, name""").fetchall()
    return _hash(schema)


def _table_rows_hash(path: Path, table: str) -> str:
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        quoted = ", ".join(f'"{column}"' for column in columns)
        rows = conn.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid').fetchall()
    return _hash(rows)


def _all_table_hashes(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute("""SELECT name FROM sqlite_master
                                                   WHERE type = 'table'
                                                     AND name NOT LIKE 'sqlite_%'
                                                   ORDER BY name""")]
    return {table: _table_rows_hash(path, table) for table in tables}


def _hash(value) -> str:
    canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=list)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_private_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load private fixture module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Migration040Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global s, retire_old_framework
        for path in (_TMP_DB, _TMP_SNAPSHOT, _TMP_RESTORED):
            path.unlink(missing_ok=True)

        # SYSTEM_DB_PATH is import-time configuration.  Load a private state
        # store under this temporary environment, then inject it while loading
        # a private migration.  The canonical module entry is restored before
        # other collected tests can observe the fixture-specific database.
        with patch.dict(os.environ, {
            "SYSTEM_DB_PATH": str(_TMP_DB),
        }, clear=False):
            os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)
            private_db = _load_private_module(_PRIVATE_SYSTEM_DB,
                                              _BACKEND_ROOT / "system_db.py")
            canonical_db = sys.modules.get("system_db")
            sys.modules["system_db"] = private_db
            try:
                private_migration = _load_private_module(
                    _PRIVATE_MIGRATION, _BACKEND_ROOT / "dq_diagnostics" / "migrate_040.py")
            finally:
                if canonical_db is None:
                    sys.modules.pop("system_db", None)
                else:
                    sys.modules["system_db"] = canonical_db

        s = private_db
        private_migration.retirement_snapshot_path = lambda: _TMP_SNAPSHOT
        retire_old_framework = private_migration.retire_old_framework
        s.init_schema()
        now = s.now_ist()
        # --- survivors -----------------------------------------------------
        s.upsert("users", {"username": "mig-user", "password": "pw",
                           "name": "M", "authz_roles": ["user"]})
        s.insert("dq_items", {"item_id": "item_mig", "kind": "dataset",
                              "name": "mig item", "status": "profiled",
                              "created_at": now, "updated_at": now})
        s.insert("dq_item_files", {"file_id": "file_mig", "item_id": "item_mig",
                                   "role": "data", "filename": "d.csv",
                                   "path": "x", "completed_at": now})
        s.insert("dq_item_tables", {"item_id": "item_mig", "table_name": "t",
                                    "columns": ["a"], "row_count": 1})
        s.insert("variable_inventory", {"item_id": "item_mig", "table_name": "t",
                                        "column_name": "a", "classification": "numerical",
                                        "updated_at": now})
        s.insert("kb_documents", {"tenant_id": "bootstrap", "title": "doc",
                                  "created_at": now})
        # --- old-framework content + keyed work-product ---------------------
        s.insert("test_library", {"test_id": "C1", "name": "old test"})
        s.insert("fw_areas", {"area_id": "A1", "l2_area": "Old area", "stage": "both"})
        s.insert("fw_tests", {"test_name": "PSI (Population Stability Index)", "stage": "stage2"})
        s.insert("fw_family_weights", {"area_id": "A1", "family": "IRB / Basel",
                                       "criticality": "High"})
        s.insert("plan_v2", {"row_id": "r1", "item_id": "item_mig", "table_name": "t",
                             "test_name": "PSI (Population Stability Index)",
                             "scope": "framework", "status": "finalized"})
        s.insert("results_v2", {"result_id": "res1", "row_id": "r1", "item_id": "item_mig",
                                "table_name": "t",
                                "test_name": "PSI (Population Stability Index)",
                                "scope": "framework", "status": "fail"})
        s.insert("scores_v2", {"item_id": "item_mig", "scope": "all", "final": 42.0})
        s.insert("issues_v2", {"issue_row_id": "iss1", "item_id": "item_mig",
                               "table_name": "t",
                               "test_name": "PSI (Population Stability Index)",
                               "status": "Open"})
        s.insert("tracked_issues_v2", {"issue_id": "trk1", "issue_row_id": "iss1",
                                       "title": "old issue", "status": "Open",
                                       "created_at": now})
        s.insert("rca_cases", {"case_id": "case1", "tenant_id": "bootstrap",
                               "issue_row_id": "iss1", "state": "intake",
                               "created_at": now})

    @classmethod
    def tearDownClass(cls):
        global s, retire_old_framework
        s = None
        retire_old_framework = None
        sys.modules.pop(_PRIVATE_MIGRATION, None)
        sys.modules.pop(_PRIVATE_SYSTEM_DB, None)

    def test_a_snapshot_failure_aborts_before_delete_or_audit(self):
        """The destructive migration must fail closed if no recovery point exists."""
        before_rows = {table: _count(table) for table in DROPPED}
        before_audits = [row for row in s.query("transaction_log")
                         if row.get("event") == "framework_retirement_drop"]

        with patch.object(s, "backup_to_path", side_effect=OSError("snapshot target unavailable")):
            with self.assertRaisesRegex(OSError, "snapshot target unavailable"):
                retire_old_framework()

        self.assertEqual({table: _count(table) for table in DROPPED}, before_rows,
                         "a failed snapshot must issue no DELETE")
        after_audits = [row for row in s.query("transaction_log")
                        if row.get("event") == "framework_retirement_drop"]
        self.assertEqual(after_audits, before_audits,
                         "a failed snapshot must write no retirement audit")

    def test_b_snapshot_cannot_replace_live_database(self):
        before_schema = _schema_hash(_TMP_DB)
        before_rows = _all_table_hashes(_TMP_DB)

        with self.assertRaisesRegex(ValueError, "must not be the live system database"):
            s.backup_to_path(s.SYS_DB_PATH)

        self.assertEqual(_schema_hash(_TMP_DB), before_schema)
        self.assertEqual(_all_table_hashes(_TMP_DB), before_rows)

    def test_drop_runs_twice_and_survivors_are_intact(self):
        before = {t: _count(t) for t in SURVIVORS}
        self.assertTrue(all(before.values()), f"fixture incomplete: {before}")
        before_schema = _schema_hash(_TMP_DB)
        before_all_rows = _all_table_hashes(_TMP_DB)
        before_survivors = {table: _table_rows_hash(_TMP_DB, table) for table in SURVIVORS}

        first = retire_old_framework()
        self.assertTrue(_TMP_SNAPSHOT.is_file(), "legacy data requires a recovery snapshot")
        self.assertGreater(_TMP_SNAPSHOT.stat().st_size, 0)

        # A real SQLite restore (not a file comparison) must reproduce the
        # exact pre-drop schema and records, including the retired classes.
        with sqlite3.connect(_TMP_SNAPSHOT) as source, sqlite3.connect(_TMP_RESTORED) as restored:
            source.backup(restored)
        self.assertEqual(_schema_hash(_TMP_RESTORED), before_schema)
        self.assertEqual(_all_table_hashes(_TMP_RESTORED), before_all_rows)

        for table in DROPPED:
            self.assertEqual(_count(table), 0, table)
            self.assertGreaterEqual(first.get(table, 0), 1,
                                    f"{table} should report a dropped row")
        after_first = {t: _count(t) for t in SURVIVORS}
        self.assertEqual(before, after_first, "a surviving class changed")
        after_first_hashes = {table: _table_rows_hash(_TMP_DB, table) for table in SURVIVORS}
        self.assertEqual(before_survivors, after_first_hashes,
                         "surviving row content or IDs changed")
        self.assertEqual(before_schema, _schema_hash(_TMP_DB),
                         "retirement must not change the schema")

        audits = [r for r in s.query("transaction_log")
                  if r.get("event") == "framework_retirement_drop"]
        self.assertEqual(len(audits), 1)
        self.assertIn("dropped", audits[0].get("payload") or {})

        # Current-framework work created after retirement must survive every
        # later boot. This is the regression that the old count-based
        # "idempotence" missed.
        s.insert("issues_v2", {"issue_row_id": "iss_current", "item_id": "item_mig",
                               "table_name": "t", "test_name": "Missingness Mechanism review",
                               "status": "Open", "rule_id": "supporting:missingness:a"})
        second = retire_old_framework()
        self.assertTrue(all(v == 0 for v in second.values()), str(second))
        self.assertIsNotNone(s.query_one("issues_v2", issue_row_id="iss_current"))
        after_second = {t: _count(t) for t in SURVIVORS}
        self.assertEqual(before, after_second)
        after_second_hashes = {table: _table_rows_hash(_TMP_DB, table) for table in SURVIVORS}
        self.assertEqual(before_survivors, after_second_hashes)
        audits2 = [r for r in s.query("transaction_log")
                   if r.get("event") == "framework_retirement_drop"]
        self.assertEqual(len(audits2), 1, "idempotent rerun must not re-audit")


if __name__ == "__main__":
    unittest.main()
