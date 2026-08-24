"""CTX-04/CTX-06 intent-conditional target and use-case tests."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

_root = Path(tempfile.gettempdir()) / "archimedes-test-ctx-defaults"
if _root.exists(): shutil.rmtree(_root)

import system_db as s  # noqa: E402
from ai.v2 import service as v2  # noqa: E402
from assets import service as assets_service  # noqa: E402


class IntentDefaultsTests(unittest.TestCase):
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

    def test_fresh_clears_values_full_replacement_clears_and_add_period_carries(self):
        fresh = v2.create_item("dataset", "fresh-blank")
        aid = fresh["asset_id"]
        v2._write_table(fresh["item_id"], "data", pd.DataFrame({"target": [1]}))
        s.update("dq_assets", {"asset_id": aid}, {"target_variable": "target", "use_case": "IRB"})
        v2.finalize_item(fresh["item_id"], "target", "IRB")
        self.assertIsNone(s.query_one("dq_assets", asset_id=aid)["target_variable"])
        self.assertIsNone(s.query_one("dq_assets", asset_id=aid)["use_case"])

        carried = v2.create_item("dataset", "carry-forward")
        caid = carried["asset_id"]
        s.update("dq_assets", {"asset_id": caid}, {"target_variable": "outcome", "use_case": "IFRS9"})
        added = assets_service.add_snapshot(caid, "add_period", snapshot_label="period-2")
        self.assertEqual(added["target_variable"], "outcome")
        self.assertEqual(added["use_case"], "IFRS9")
        replacement = assets_service.add_snapshot(caid, "full_replacement", snapshot_label="replacement")
        self.assertIsNone(s.query_one("dq_assets", asset_id=caid)["target_variable"])
        self.assertIsNone(s.query_one("dq_assets", asset_id=caid)["use_case"])
        self.assertIsNone(replacement["target_variable"])
        self.assertIsNone(replacement["use_case"])

    def test_finalize_staged_snapshot_prefers_supplied_value_on_fresh(self):
        """Data_Sourcing_11: a target/use-case/product decision made ON THIS
        commit must survive it — CTX-04's blank-on-fresh rule only applies
        when the caller supplies nothing, so a STALE prior decision is never
        silently carried into a new reference schema."""
        supplied = v2.create_item("dataset", "supplied-on-fresh")
        v2._write_table(supplied["item_id"], "data", pd.DataFrame({"revenue": [1, 2]}))
        committed = assets_service.finalize_staged_snapshot(
            supplied["asset_id"], supplied["item_id"], "fresh", actor=None,
            snapshot_label="supplied-snap",
            target_variable="revenue", use_case="IRB", product="Mortgage",
        )
        self.assertEqual(committed["target_variable"], "revenue")
        self.assertEqual(committed["use_case"], "IRB")
        self.assertEqual(committed["product"], "Mortgage")
        asset_row = s.query_one("dq_assets", asset_id=supplied["asset_id"])
        self.assertEqual(asset_row["target_variable"], "revenue")
        self.assertEqual(asset_row["use_case"], "IRB")
        self.assertEqual(asset_row["product"], "Mortgage")

        unsupplied = v2.create_item("dataset", "unsupplied-on-fresh")
        v2._write_table(unsupplied["item_id"], "data", pd.DataFrame({"revenue": [1, 2]}))
        s.update("dq_assets", {"asset_id": unsupplied["asset_id"]},
                 {"target_variable": "stale", "use_case": "stale-use-case", "product": "stale-product"})
        committed_blank = assets_service.finalize_staged_snapshot(
            unsupplied["asset_id"], unsupplied["item_id"], "fresh", actor=None,
            snapshot_label="unsupplied-snap",
        )
        self.assertIsNone(committed_blank["target_variable"])
        self.assertIsNone(committed_blank["use_case"])
        self.assertIsNone(committed_blank["product"])


if __name__ == "__main__": unittest.main()
