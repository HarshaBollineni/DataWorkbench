"""CTX-01/03/08 and R-09 refresh contract tests."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

_root = Path(tempfile.gettempdir()) / "archimedes-test-refresh"
if _root.exists():
    shutil.rmtree(_root)

import system_db as s  # noqa: E402
from ai.v2 import service as v2  # noqa: E402
from assets.refresh import refresh_derived  # noqa: E402


class RefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_db_path = s.SYS_DB_PATH
        cls._old_backup_path = s.SYS_DB_BACKUP_PATH
        cls._old_upload_root = v2.UPLOAD_ROOT
        cls._old_item_db_root = v2.ITEM_DB_ROOT
        _root.mkdir()
        s.SYS_DB_PATH = _root / "system.db"
        s.SYS_DB_BACKUP_PATH = None
        v2.UPLOAD_ROOT = _root / "uploads"
        v2.ITEM_DB_ROOT = _root / "item-dbs"
        s.init_schema()

    @classmethod
    def tearDownClass(cls):
        s.SYS_DB_PATH = cls._old_db_path
        s.SYS_DB_BACKUP_PATH = cls._old_backup_path
        v2.UPLOAD_ROOT = cls._old_upload_root
        v2.ITEM_DB_ROOT = cls._old_item_db_root

    def _profiled(self, alias="refresh-fixture"):
        item = v2.create_item("dataset", alias)
        path = _root / f"{alias}.csv"
        path.write_text("amount,flag\n10,0\n20,1\n", encoding="utf-8")
        v2.save_file(item["item_id"], "data", path.name, path.read_bytes())
        v2.profile_item(item["item_id"])
        return item, path

    @staticmethod
    def _derived(item_id):
        return {
            table: s.query("variable_inventory", item_id=item_id)
            for table in ("variable_inventory", "dq_item_mappings", "dq_item_warnings", "dq_snapshot_fingerprints")
        }

    def test_refresh_is_idempotent_and_does_not_touch_upload_or_cache(self):
        item, source = self._profiled()
        cache = v2._item_db(item["item_id"])
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        cache_hash = hashlib.sha256(cache.read_bytes()).hexdigest()
        refresh_derived(item["asset_id"], "tester", "test one")
        first = self._derived(item["item_id"])
        refresh_derived(item["asset_id"], "tester", "test two")
        second = self._derived(item["item_id"])
        self.assertEqual(first, second)
        self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(cache_hash, hashlib.sha256(cache.read_bytes()).hexdigest())

    def test_refresh_never_writes_mark_stale_rows(self):
        item, _ = self._profiled("refresh-stale")
        s.insert("diag_runs", {"run_id": "run_refresh", "item_id": item["item_id"], "diagnostic_id": 4,
                                "manifest_json": {}, "status": "done", "created_at": "2026-08-01"})
        s.insert("diag_results", {"result_id": "result_refresh", "run_id": "run_refresh", "diagnostic_id": 4,
                                   "decision_type": "verdict", "verdict": "violation", "created_at": "2026-08-01"})
        before = s.query("diag_results")
        refresh_derived(item["asset_id"], reason="negative property")
        self.assertEqual(before, s.query("diag_results"))

    def test_refresh_preserves_user_confirmed_schema_metadata(self):
        item, _ = self._profiled("refresh-reviewed-schema")
        amount = next(row for row in v2.get_inventory(item["item_id"])
                      if row["column_name"] == "amount")
        amount.update({
            "role": "Ignore", "classification": "text",
            "description": "User-confirmed definition",
            "missing_value_codes_json": ["-999"],
            "missing_codes_confirmed": True,
        })
        v2.put_inventory(item["item_id"], [amount], table=amount["table_name"])

        refresh_derived(item["asset_id"], "tester", "preserve reviewed schema")

        saved = next(row for row in v2.get_inventory(item["item_id"])
                     if row["column_name"] == "amount")
        self.assertEqual(saved["role"], "Ignore")
        self.assertEqual(saved["classification"], "text")
        self.assertEqual(saved["description"], "User-confirmed definition")
        self.assertEqual(saved["missing_value_codes_json"], ["-999"])
        self.assertTrue(saved["missing_codes_confirmed"])


if __name__ == "__main__":
    unittest.main()
