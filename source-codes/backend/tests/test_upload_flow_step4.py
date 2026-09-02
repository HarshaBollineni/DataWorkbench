"""UPL-14..29 and AST-17 behavioural acceptance evidence for Step 4."""
from __future__ import annotations

import io
import json
import os
import shutil
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent / ".step4-test-runtime"
if ROOT.exists():
    shutil.rmtree(ROOT)
ROOT.mkdir(parents=True)
os.environ["SYSTEM_DB_PATH"] = str(ROOT / "system.db")
os.environ["UPLOAD_DIR"] = str(ROOT / "uploads")
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from ai.v2 import service  # noqa: E402
from assets import schema_check  # noqa: E402
from assets import service as assets_service  # noqa: E402
from routers import v2  # noqa: E402


class Step4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        app = FastAPI()
        app.include_router(v2.router)
        cls.client = TestClient(app)

    def _profile(self, kind="dataset", basis="none", filename="source.csv", data=b"a,b\n1,2\n2,3\n"):
        item = service.create_item(kind, f"step4-{self._testMethodName[:24]}", basis)
        service.save_file(item["item_id"], "data", filename, data)
        service.profile_item(item["item_id"])
        return item

    def _stage(self, asset_id, label="staged", filename="new.csv", data=b"a,b\n1,2\n"):
        asset = s.query_one("dq_assets", asset_id=asset_id)
        fields = {"snapshot_label": label}
        if asset["time_basis"] == "period":
            fields.update(start_date="2026-03-01", end_date="2026-03-02")
        snap = assets_service.add_snapshot(asset_id, "add_period", **fields)
        service.save_file(snap["item_id"], "data", filename, data)
        service.profile_item(snap["item_id"])
        return snap

    def test_period_dates_and_none_label_are_server_validated_and_scoped(self):
        period = self._profile(basis="period")
        with self.assertRaisesRegex(ValueError, "together"):
            service.process_snapshot(period["item_id"], start_date="2026-01-01", snapshot_label="x")
        with self.assertRaisesRegex(ValueError, "on or before"):
            service.process_snapshot(period["item_id"], start_date="2026-02-02", end_date="2026-02-01", snapshot_label="x")
        service.process_snapshot(period["item_id"], start_date="2026-01-01", end_date="2026-01-01", snapshot_label="equal")
        none_a = self._profile()
        none_b = self._profile()
        with self.assertRaisesRegex(ValueError, "required"):
            service.process_snapshot(none_a["item_id"])
        service.process_snapshot(none_a["item_id"], snapshot_label="shared")
        service.process_snapshot(none_b["item_id"], snapshot_label="shared")
        self.assertNotEqual(none_a["asset_id"], none_b["asset_id"])
        with self.assertRaisesRegex(ValueError, "already used"):
            assets_service.add_snapshot(none_a["asset_id"], "add_period", snapshot_label="shared")

    def test_profile_stream_reports_real_stage_progress(self):
        item = service.create_item("dataset", f"progress-{self._testMethodName[:20]}", "none")
        service.save_file(item["item_id"], "data", "progress.csv", b"account,amount\nA1,10\nA2,20\n")
        response = self.client.get(f"/api/v2/items/{item['item_id']}/profile/stream")
        self.assertEqual(response.status_code, 200, response.text)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        progress = [event["progress"] for event in events if event.get("progress")]
        self.assertEqual(events[-1]["phase"], "done")
        self.assertEqual(progress[-1]["percent"], 100)
        self.assertEqual(progress[-1]["completed"], 9)
        self.assertTrue({"normalize_dictionary", "profile_data", "validate_schema", "finalize", "complete"}
                        <= {entry["stage"] for entry in progress})
        self.assertEqual([entry["percent"] for entry in progress],
                         sorted(entry["percent"] for entry in progress))

    def test_period_column_is_metadata_and_does_not_split_rows(self):
        item = self._profile(basis="period", data=b"period,value\n2026-01-01,1\n2026-01-02,2\n")
        before = s.query_one("dq_items", item_id=item["item_id"])["row_count"]
        service.process_snapshot(item["item_id"], start_date="2026-01-01", end_date="2026-01-02",
                                 snapshot_label="range", period_column="period")
        row = s.query_one("dq_items", item_id=item["item_id"])
        self.assertEqual(row["period_column"], "period")
        self.assertEqual(row["row_count"], before)

    def test_save_and_proceed_commits_a_snapshot_only_once(self):
        item = self._profile()
        service.process_snapshot(item["item_id"], snapshot_label="saved-once")

        with self.assertRaisesRegex(ValueError, "already been saved and processed"):
            service.process_snapshot(item["item_id"], snapshot_label="saved-twice")

        events = s.query("dq_asset_events", snapshot_id=item["item_id"],
                         event_type="snapshot_processed")
        self.assertEqual(len(events), 1)
        self.assertEqual(s.query_one("dq_items", item_id=item["item_id"])["snapshot_label"],
                         "saved-once")

    def test_failed_sourcing_cannot_be_promoted_to_ready(self):
        item = self._profile(data=b"id,id\n1,2\n")
        self.assertEqual(service.ingest_summary(item["item_id"])["status"], "failed")

        with self.assertRaisesRegex(ValueError, "failed sourcing run"):
            service.process_snapshot(item["item_id"], snapshot_label="must-not-save")

        self.assertEqual(service.ingest_summary(item["item_id"])["status"], "failed")

    def test_schema_compare_requires_similarity_and_type_evidence(self):
        result = schema_check.compare(
            {"tables": {"t": {"columns": ["amount"], "types": {"amount": "numeric"}}}},
            {"tables": {"t": {"columns": ["customer_name"], "types": {"customer_name": "text"}}}},
            "dataset",
        )
        diff = result["per_table"]["dataset"]
        self.assertEqual(diff["columns_missing"], ["amount"])
        self.assertEqual(diff["columns_extra"], ["customer_name"])
        self.assertEqual(diff["columns_renamed"], [])

    def test_database_reports_tables_and_still_compares_shared_columns(self):
        result = schema_check.compare(
            {"tables": {"loans": {"columns": ["id", "amount"], "types": {"id": "text", "amount": "numeric"}},
                        "old": {"columns": ["x"], "types": {"x": "text"}}}},
            {"tables": {"loans": {"columns": ["id", "amount"], "types": {"id": "text", "amount": "text"}},
                        "new": {"columns": ["x"], "types": {"x": "text"}}}},
            "database",
        )
        self.assertEqual(result["tables_removed"], ["old"])
        self.assertEqual(result["tables_added"], ["new"])
        self.assertEqual(result["per_table"]["loans"]["type_changes"], [{"column": "amount", "from": "numeric", "to": "text"}])
        self.assertTrue(any("amount: numeric → text" in message for message in schema_check.messages(result)))

    def test_process_endpoint_rechecks_override_and_full_replacement_confirmation(self):
        first = self._profile(data=b"a,b\n1,2\n")
        staged = self._stage(first["asset_id"], data=b"a,c\n1,2\n")
        s.insert("diag_runs", {
            "run_id": "run-step4-config", "item_id": first["item_id"], "diagnostic_id": 4,
            "manifest_json": {"diagnostic": {"name": "PSI comparison"},
                               "roles": {"affected": {"table": "dataset", "column": "b"}},
                               "scope": {"tables": []}},
            "status": "done", "engine_versions_json": {}, "created_at": s.now_ist(),
            "started_at": None, "finished_at": None,
        })
        response = self.client.post(f"/api/v2/items/{staged['item_id']}/process",
                                    json={"intent": "full_replacement", "snapshot_label": "replacement"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(s.query_one("dq_assets", asset_id=first["asset_id"])["current_version_no"], 1)
        self.assertEqual(s.query_one("dq_items", item_id=staged["item_id"])["schema_override_flag"], 0)
        response = self.client.post(f"/api/v2/items/{staged['item_id']}/process", json={
            "intent": "full_replacement", "snapshot_label": "replacement",
            "schema_override_confirmed": True, "full_replacement_confirmed": True,
            "uploaded_by": "tester"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(s.query_one("dq_assets", asset_id=first["asset_id"])["current_version_no"], 2)
        self.assertEqual(s.query_one("dq_items", item_id=staged["item_id"])["schema_override_flag"], 1)
        summary = service.ingest_summary(staged["item_id"])
        self.assertIn("PSI comparison", summary["schema_check"]["affected_test_configurations"])

    def test_schema_summary_explains_when_no_test_configuration_is_affected(self):
        first = self._profile(data=b"a,b\n1,2\n")
        staged = self._stage(first["asset_id"], data=b"a,c\n1,2\n")
        service.process_snapshot(staged["item_id"], intent="full_replacement",
                                 snapshot_label="replacement-no-config",
                                 schema_override_confirmed=True,
                                 full_replacement_confirmed=True)
        summary = service.ingest_summary(staged["item_id"])
        self.assertEqual(summary["schema_check"]["affected_test_configurations"], [])
        self.assertIn("no existing test configuration references the affected columns",
                      summary["schema_check"]["consequence"])

    def test_overlap_boundaries_and_none_basis(self):
        item = self._profile(basis="period")
        service.process_snapshot(item["item_id"], start_date="2026-01-01", end_date="2026-01-31", snapshot_label="jan")
        touching = self._stage(item["asset_id"], label="touching", data=b"a,b\n3,4\n")
        service.process_snapshot(touching["item_id"], start_date="2026-02-01", end_date="2026-02-28", snapshot_label="feb")
        self.assertEqual(service.ingest_summary(touching["item_id"])["overlap_warnings"], [])
        shared = self._stage(item["asset_id"], label="shared", data=b"a,b\n5,6\n")
        service.process_snapshot(shared["item_id"], start_date="2026-01-31", end_date="2026-02-05", snapshot_label="shared-range")
        self.assertTrue(service.ingest_summary(shared["item_id"])["overlap_warnings"])
        none = self._profile()
        staged = self._stage(none["asset_id"], label="none-two", data=b"a,b\n3,4\n4,5\n")
        service.process_snapshot(staged["item_id"], snapshot_label="none-two")
        self.assertEqual(service.ingest_summary(staged["item_id"])["overlap_warnings"], [])

    def test_fingerprint_is_one_row_per_column_and_summary_is_retrievable(self):
        item = self._profile(data=b"amount,label\n1,x\n2,y\n3,y\n")
        fingerprints = s.query("dq_snapshot_fingerprints", snapshot_id=item["item_id"])
        self.assertEqual(len(fingerprints), 2)
        amount = next(row for row in fingerprints if row["column_name"] == "amount")
        self.assertIsNotNone(amount["stddev_value"])
        self.assertTrue(amount["histogram_json"])
        self.assertTrue(amount["distinct_set_hash"])
        summary = service.ingest_summary(item["item_id"])
        self.assertEqual(summary["completion_summary"]["asset_name"], summary["asset_name"])
        self.assertEqual(self.client.get(f"/api/v2/snapshots/{item['item_id']}/summary").status_code, 200)


if __name__ == "__main__":
    unittest.main()
