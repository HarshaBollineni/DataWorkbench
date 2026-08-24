"""CTX-05: target-dependent readiness is NOT APPLICABLE, not a failure."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

_root = Path(tempfile.gettempdir()) / "archimedes-test-scope-target"
if _root.exists(): shutil.rmtree(_root)

import system_db as s  # noqa: E402
from dq_diagnostics.readiness import readiness  # noqa: E402
from dq_diagnostics.engines.base import Readiness  # noqa: E402


class ScopeGateTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_db_path = s.SYS_DB_PATH
        cls._old_backup_path = s.SYS_DB_BACKUP_PATH
        _root.mkdir()
        s.SYS_DB_PATH = _root / "system.db"
        s.SYS_DB_BACKUP_PATH = None
        s.init_schema()
        s.insert("diagnostic_register", {"diagnostic_id": 2, "area": "A", "mode": "statistical",
                                          "name": "Target diagnostic", "workflow_status": "executable"})

    @classmethod
    def tearDownClass(cls):
        s.SYS_DB_PATH = cls._old_db_path
        s.SYS_DB_BACKUP_PATH = cls._old_backup_path

    def test_missing_target_is_not_applicable_and_names_field_and_location(self):
        s.insert("dq_items", {"item_id": "scope-item", "kind": "dataset", "ingest_status": "ready",
                               "target_variable": None, "use_case": None})
        s.insert("dq_item_tables", {"item_id": "scope-item", "table_name": "data", "columns": ["x"], "col_count": 1})
        result = readiness("scope-item", 2)
        self.assertEqual(result.status, "not_applicable")
        self.assertIn("target variable", result.reason)
        self.assertIn("Data Sourcing", result.reason)
        self.assertNotIn("failure", result.reason.lower())


if __name__ == "__main__": unittest.main()
