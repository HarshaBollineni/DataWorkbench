"""AST-16..19 executable proofs for the metadata-only version diff."""
from __future__ import annotations

import contextlib
import gc
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
_DB = _ROOT / ".tmp-version-diff.db"
_UPLOADS = _ROOT / ".tmp-version-diff-uploads"
_ITEMDBS = _ROOT / ".tmp-version-diff-itemdbs"


def _cleanup_temp_paths() -> None:
    # sqlite3 connection objects opened by helpers can become collectible only
    # after the final test frame is released. Force that collection and allow
    # Windows a brief handle-release window before removing the module fixture.
    gc.collect()
    for path in (_DB,):
        if path.exists():
            for attempt in range(5):
                try:
                    path.unlink()
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    gc.collect()
                    time.sleep(0.05)
    for path in (_UPLOADS, _ITEMDBS):
        if path.exists():
            shutil.rmtree(path)


_cleanup_temp_paths()
os.environ["SYSTEM_DB_PATH"] = str(_DB)
os.environ["UPLOAD_DIR"] = str(_UPLOADS)
os.environ["ITEM_DB_DIR"] = str(_ITEMDBS)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import pandas as pd  # noqa: E402

import system_db as s  # noqa: E402
from ai.v2 import service as ingest_service  # noqa: E402
from assets import schema_check  # noqa: E402
from assets import service as asset_service  # noqa: E402
from assets.diff import diff_versions  # noqa: E402

s.SYS_DB_PATH = _DB


def _schema(columns: list[str], types: dict[str, str] | None = None) -> dict:
    return {"tables": {"dataset": {"columns": columns, "types": types or {c: "text" for c in columns}}}}


def _file(item_id: str, name: str, size: int) -> Path:
    path = _UPLOADS / "fixtures" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    s.insert("dq_item_files", {"file_id": f"file_{item_id}", "item_id": item_id,
                                "role": "data", "filename": name, "path": str(path),
                                "completed_at": s.now_ist()})
    return path


def _fingerprint(item_id: str, column: str = "amount", null_rate: float = 0.1) -> None:
    s.insert("dq_snapshot_fingerprints", {
        "snapshot_id": item_id, "table_name": "dataset", "column_name": column,
        "dtype": "float64", "confirmed_type": "numeric", "null_rate": null_rate,
        "distinct_count": 4, "min_value": "1", "max_value": "9", "mean_value": 5.0,
        "stddev_value": 2.0,
        "histogram_json": json.dumps([{"count": 2}, {"count": 2}]),
        "distinct_set_hash": "hash", "top_k_json": json.dumps([{"value": "x", "count": 2}]),
        "computed_at": s.now_ist(),
    })


class VersionDiffFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        cls.asset = asset_service.create_asset("dataset", "diff-fixture", "none", actor="tester")
        cls.v1_schema = _schema(["amount", "name"], {"amount": "numeric", "name": "text"})
        s.update("dq_asset_versions", {"asset_id": cls.asset["asset_id"], "version_no": 1},
                 {"reference_schema_json": cls.v1_schema})
        cls.snap1 = asset_service.add_snapshot(
            cls.asset["asset_id"], "fresh", actor="tester", snapshot_label="first",
            row_count=10, column_count=2)
        _file(cls.snap1["item_id"], "first.csv", 10)
        _fingerprint(cls.snap1["item_id"], null_rate=0.1)
        cls.v2_schema = _schema(["name", "total"], {"name": "text", "total": "numeric"})
        cls.snap2 = asset_service.add_snapshot(
            cls.asset["asset_id"], "full_replacement", actor="tester", snapshot_label="second",
            row_count=25, column_count=2, column_type_map_json=cls.v2_schema)
        _file(cls.snap2["item_id"], "second.csv", 30)
        _fingerprint(cls.snap2["item_id"], column="total", null_rate=0.2)

    def test_full_diff_has_three_tiers_and_uses_latest_snapshot_basis(self):
        result = diff_versions(self.asset["asset_id"], 1, 2)
        self.assertIn("tier1_schema", result)
        self.assertIn("tier2_shape", result)
        self.assertIn("tier3_distribution", result)
        self.assertEqual(result["tier2_shape"]["row_count"]["delta"], 15)
        self.assertTrue(result["tier3_distribution"]["available"])
        self.assertIn("v1's latest snapshot", result["tier3_distribution"]["comparison_basis"])
        self.assertFalse(result["row_level"]["available"])

    def test_no_data_readers_or_item_database_are_needed(self):
        import ai.v2.service as service_module

        original_connect = sqlite3.connect

        def fail_item_db_connect(database, *args, **kwargs):
            if str(_ITEMDBS) in str(database):
                raise AssertionError("version diff opened an item database")
            return original_connect(database, *args, **kwargs)

        with mock.patch.object(service_module, "_read_table", side_effect=AssertionError("read table")), \
             mock.patch.object(service_module, "_read_tabular", side_effect=AssertionError("read tabular")), \
             mock.patch.object(pd, "read_csv", side_effect=AssertionError("read csv")), \
             mock.patch.object(pd, "read_excel", side_effect=AssertionError("read excel")), \
             mock.patch.object(sqlite3, "connect", side_effect=fail_item_db_connect):
            result = diff_versions(self.asset["asset_id"], 1, 2)
        self.assertTrue(result["tier3_distribution"]["available"])

    def test_query_count_is_identical_for_small_and_large_row_counts(self):
        original_get_conn = s.get_conn

        def counted_diff():
            count = {"n": 0}

            @contextlib.contextmanager
            def counted_conn():
                with original_get_conn() as conn:
                    class Proxy:
                        def execute(self, *args, **kwargs):
                            count["n"] += 1
                            return conn.execute(*args, **kwargs)

                    yield Proxy()

            with mock.patch.object(s, "get_conn", counted_conn):
                diff_versions(self.asset["asset_id"], 1, 2)
            return count["n"]

        s.update("dq_items", {"item_id": self.snap1["item_id"]}, {"row_count": 10})
        small = counted_diff()
        s.update("dq_items", {"item_id": self.snap1["item_id"]}, {"row_count": 1_000_000})
        large = counted_diff()
        self.assertEqual(small, large)
        self.assertEqual(small, 5)

    def test_pre_fingerprint_snapshot_keeps_tiers_one_and_two_and_reports_unavailable_tier_three(self):
        asset = asset_service.create_asset("dataset", "old-fingerprint", "none", actor="tester")
        first = _schema(["a"], {"a": "text"})
        s.update("dq_asset_versions", {"asset_id": asset["asset_id"], "version_no": 1},
                 {"reference_schema_json": first})
        snap_a = asset_service.add_snapshot(asset["asset_id"], "fresh", actor="tester",
                                            snapshot_label="old-a", row_count=2, column_count=1)
        second = _schema(["b"], {"b": "text"})
        snap_b = asset_service.add_snapshot(asset["asset_id"], "full_replacement", actor="tester",
                                             snapshot_label="old-b", row_count=3, column_count=1,
                                             column_type_map_json=second)
        result = diff_versions(asset["asset_id"], 1, 2)
        self.assertEqual(result["tier2_shape"]["row_count"]["delta"], 1)
        self.assertIn("tier1_schema", result)
        self.assertFalse(result["tier3_distribution"]["available"])
        self.assertIn("before fingerprinting", result["tier3_distribution"]["unavailable_reason"])
        self.assertTrue(snap_a["item_id"] and snap_b["item_id"])


class DiffEvidenceTests(unittest.TestCase):
    def test_rename_requires_shared_evidence_and_drop_add_is_not_a_rename(self):
        renamed = schema_check.compare(
            _schema(["amount"], {"amount": "numeric"}),
            _schema(["am0unt"], {"am0unt": "numeric"}), "dataset")
        self.assertEqual(renamed["per_table"]["dataset"]["columns_renamed"][0]["from"], "amount")
        negative = schema_check.compare(
            _schema(["amount"], {"amount": "numeric"}),
            _schema(["customer_identifier"], {"customer_identifier": "text"}), "dataset")
        evidence = negative["per_table"]["dataset"]
        self.assertEqual(evidence["columns_renamed"], [])
        self.assertEqual(evidence["columns_missing"], ["amount"])
        self.assertEqual(evidence["columns_extra"], ["customer_identifier"])


class MountPointTests(unittest.TestCase):
    def test_all_three_mount_points_import_the_single_version_diff_module(self):
        files = [
            _ROOT / "ui/src/components/AssetPicker.jsx",
            _ROOT / "ui/src/pages/DataSourcing.jsx",
            _ROOT / "ui/src/pages/AssetCatalogue.jsx",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r'import VersionDiff from ["\']@/components/VersionDiff["\'];', path.name)
        component = (_ROOT / "ui/src/components/VersionDiff.jsx").read_text(encoding="utf-8")
        self.assertEqual(sum("function VersionDiff" in p.read_text(encoding="utf-8") for p in files), 0)
        self.assertIn("version-diff-tier-1", component)
        self.assertIn("version-diff-tier-2", component)
        self.assertIn("version-diff-tier-3", component)


def tearDownModule() -> None:
    """Remove module-level fixtures under pytest as well as unittest CLI runs."""
    _cleanup_temp_paths()


if __name__ == "__main__":
    unittest.main()
