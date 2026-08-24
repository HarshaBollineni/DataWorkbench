"""CTX-02 and R-09/M-11: stale state follows snapshot status at read time."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

_root = Path(tempfile.gettempdir()) / "archimedes-test-staleness"
if _root.exists():
    shutil.rmtree(_root)

import system_db as s  # noqa: E402
from ai.v2 import service as v2  # noqa: E402
from assets import staleness  # noqa: E402


class StalenessTests(unittest.TestCase):
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

    def test_full_replacement_preserves_finding_disposition_and_issue_intact_and_stale(self):
        item = v2.create_item("dataset", "negative-property")
        old_id = item["item_id"]
        s.update("dq_items", {"item_id": old_id}, {"snapshot_label": "old snapshot"})
        s.insert("diag_runs", {"run_id": "stale_run", "item_id": old_id, "diagnostic_id": 4,
                                "manifest_json": {}, "status": "done", "created_at": "2026-08-01"})
        s.insert("diag_results", {"result_id": "stale_result", "run_id": "stale_run", "diagnostic_id": 4,
                                   "decision_type": "verdict", "verdict": "violation", "created_at": "2026-08-01"})
        s.insert("diag_findings", {"finding_id": "stale_finding", "result_id": "stale_result", "run_id": "stale_run",
                                    "rule_id": "CF-01", "outcome": "VIOLATION", "rule_text": "preserve me",
                                    "created_at": "2026-08-01"})
        s.insert("diag_dispositions", {"target_type": "finding", "target_id": "stale_finding",
                                        "action": "confirm_issue", "reason": "human decision", "ts": "2026-08-01"})
        s.insert("issues_v2", {"issue_row_id": "stale_issue", "item_id": old_id, "table_name": "dataset",
                                "test_name": "CF-01", "status": "In RCA", "created_at": "2026-08-01"})
        old_finding = s.query_one("diag_findings", finding_id="stale_finding")
        old_issue = s.query_one("issues_v2", issue_row_id="stale_issue")

        v2.reupload_item(old_id, intent="full_replacement", snapshot_label="new snapshot")

        self.assertEqual(old_finding, s.query_one("diag_findings", finding_id="stale_finding"))
        self.assertEqual(old_issue, s.query_one("issues_v2", issue_row_id="stale_issue"))
        finding_view = staleness.annotate_stale([{**old_finding, "run_id": "stale_run"}])[0]
        issue_view = staleness.annotate_stale([old_issue])[0]
        disposition_view = staleness.annotate_stale([{
            "target_type": "finding", "target_id": "stale_finding",
        }])[0]
        self.assertTrue(finding_view["stale"])
        self.assertTrue(issue_view["stale"])
        self.assertTrue(disposition_view["stale"])
        self.assertEqual(finding_view["stale_sources"][0]["label"], "old snapshot")
        self.assertEqual(s.query("diag_dispositions", target_id="stale_finding")[0]["action"], "confirm_issue")

    def test_restore_makes_the_restored_snapshot_current_again(self):
        item = v2.create_item("dataset", "restore-staleness")
        old_id = item["item_id"]
        replacement = v2.reupload_item(old_id, intent="full_replacement", snapshot_label="replacement")
        self.assertTrue(staleness.is_stale({"item_id": old_id}))
        from assets.service import restore_version_set
        restore_version_set(item["asset_id"], 1)
        self.assertFalse(staleness.is_stale({"item_id": old_id}))
        self.assertTrue(staleness.is_stale({"item_id": replacement["item_id"]}))


if __name__ == "__main__":
    unittest.main()
