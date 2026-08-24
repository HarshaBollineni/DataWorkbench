"""ANL-01..07 proofs: bounded IDs, append-only events, measures and catalogue."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DB = _ROOT / ".tmp-usage-analytics.db"
_UPLOADS = _ROOT / ".tmp-usage-analytics-uploads"
_ITEMDBS = _ROOT / ".tmp-usage-analytics-itemdbs"
if _DB.exists():
    _DB.unlink()
for _path in (_UPLOADS, _ITEMDBS):
    if _path.exists():
        shutil.rmtree(_path)
os.environ["SYSTEM_DB_PATH"] = str(_DB)
os.environ["UPLOAD_DIR"] = str(_UPLOADS)
os.environ["ITEM_DB_DIR"] = str(_ITEMDBS)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from analytics import catalogue, measures  # noqa: E402
from analytics.events import EVENT_TYPES, delete_for_factory_reset, record_event  # noqa: E402
from analytics.ids import FAMILIES, typed_id, variable_id  # noqa: E402
from assets import service as asset_service  # noqa: E402

s.SYS_DB_PATH = _DB


class UsageAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        cls.asset = asset_service.create_asset("dataset", "analytics-fixture", "none", actor="alice")
        cls.asset_b = asset_service.create_asset("dataset", "analytics-fixture-b", "none", actor="bob")

    def setUp(self):
        s.execute("DELETE FROM usage_events")

    def _event(self, kind: str, when: datetime, *, actor: str = "alice", detail=None,
               object_type: str = "asset", object_id: str | None = None, context: str | None = None):
        object_id = object_id or self.asset["system_id"]
        return record_event(event_type=kind, actor=actor, at=when.isoformat(),
                            object_type=object_type, object_id=object_id,
                            workflow_context=context or self.asset["system_id"], detail=detail)

    def test_required_fields_are_atomic_and_vocabulary_is_closed(self):
        before = len(s.query("usage_events"))
        with self.assertRaises(ValueError):
            self._event("asset_selected", datetime.now(timezone.utc), actor="")
        self.assertEqual(len(s.query("usage_events")), before)
        with self.assertRaises(ValueError):
            self._event("not_a_release_event", datetime.now(timezone.utc))
        self.assertEqual(set(EVENT_TYPES), {
            "asset_created", "asset_selected", "alias_renamed", "snapshot_added",
            "snapshot_ready", "upload_started", "upload_step_reached", "upload_abandoned",
            "type_override", "schema_warning_overridden", "version_created",
            "version_superseded", "version_restored", "dictionary_version_created",
            "dictionary_bound", "refresh_requested", "diagnostic_run_started",
            "finding_disposed", "use_case_set", "catalogue_viewed", "version_diff_viewed",
        })

    def test_every_vocabulary_member_has_a_real_production_call_site(self):
        sources = []
        for path in (_ROOT / "backend").rglob("*.py"):
            if ("tests" not in path.parts and
                    path.name not in {"events.py", "measures.py", "catalogue.py"}):
                sources.append(path.read_text(encoding="utf-8"))
        production = "\n".join(sources)
        missing = [kind for kind in EVENT_TYPES if not re.search(
            rf"(?:_usage_event|_usage|record_event)\([^\n]*[\"']{re.escape(kind)}[\"']",
            production)]
        self.assertEqual(missing, [], "a vocabulary entry without a call site would be dead analytics")

    def test_append_only_scanner_catches_a_temporary_update_and_allows_named_reset_exception(self):
        product_root = _ROOT / "backend"

        def violations():
            found = []
            for path in product_root.rglob("*.py"):
                if "tests" in path.parts or path.name == "events.py":
                    continue
                text = path.read_text(encoding="utf-8")
                if re.search(r"\b(?:UPDATE|DELETE\s+FROM)\s+usage_events\b", text, re.IGNORECASE):
                    found.append(path)
            return found

        self.assertEqual(violations(), [])
        probe = _ROOT / "backend" / "analytics" / "_append_only_probe.py"
        try:
            probe.write_text('SQL = "UPDATE usage_events SET actor = \'x\'"\n', encoding="utf-8")
            self.assertTrue(violations(), "the bites-check must fail if a normal writer adds UPDATE usage_events")
        finally:
            if probe.exists():
                probe.unlink()
        self.assertIn("delete_for_factory_reset", (_ROOT / "backend/system_db.py").read_text(encoding="utf-8"))
        self.assertIn("sole ANL-03 deletion exception", (_ROOT / "backend/analytics/events.py").read_text(encoding="utf-8"))

    def test_all_measures_return_known_answers_from_a_seeded_log(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._event("asset_created", base)
        self._event("asset_created", base + timedelta(days=1), actor="bob", object_id=self.asset_b["system_id"])
        self._event("asset_selected", base + timedelta(hours=1))
        self._event("asset_selected", base + timedelta(hours=2), actor="bob")
        self._event("snapshot_added", base + timedelta(hours=3))
        self._event("upload_started", base + timedelta(hours=4), object_type="snapshot", object_id="SN:s1")
        self._event("snapshot_ready", base + timedelta(hours=4, minutes=30), object_type="snapshot", object_id="SN:s1")
        self._event("upload_step_reached", base + timedelta(hours=5), detail={"step": 4})
        self._event("upload_abandoned", base + timedelta(hours=6), detail={"step": 4})
        self._event("type_override", base + timedelta(hours=7))
        self._event("schema_warning_overridden", base + timedelta(hours=8))
        self._event("diagnostic_run_started", base + timedelta(hours=9),
                    detail={"test_id": 4, "diagnostic_id": 4})
        self._event("diagnostic_run_started", base + timedelta(hours=10),
                    detail={"test_id": 5, "diagnostic_id": 4, "after_replacement": True})
        self._event("version_created", base + timedelta(hours=11), object_type="version", object_id="VR:0002")
        self._event("version_superseded", base + timedelta(hours=11, minutes=1), object_type="version", object_id="VR:0001")
        self._event("version_restored", base + timedelta(hours=12), object_type="version", object_id="VR:0001")
        self._event("dictionary_version_created", base + timedelta(hours=13), object_type="dictionary_version", object_id="DV:0001")
        self._event("dictionary_bound", base + timedelta(hours=14), object_type="dictionary_version", object_id="DV:0001")
        self._event("use_case_set", base + timedelta(hours=15), detail={"use_case": "IRB"})
        self._event("finding_disposed", base + timedelta(hours=16), detail={"disposition": "dismiss"})

        self.assertEqual(measures.test_preferences(), {"4": 1, "5": 1})
        self.assertEqual(measures.diagnostic_preferences(), {"4": 2})
        self.assertEqual(measures.use_case_frequency(), {"IRB": 1})
        self.assertEqual(measures.reuse_depth()["existing_selections"], 2)
        self.assertEqual(measures.time_to_ready()["count"], 1)
        self.assertEqual(measures.time_to_ready()["seconds"], [1800.0])
        self.assertEqual(measures.override_rate()["total_overrides"], 2)
        self.assertEqual(measures.abandonment_points(), {"4": 1})
        self.assertEqual(measures.snapshot_cadence(), {self.asset["system_id"]: 1})
        self.assertEqual(measures.finding_disposition_split(), {"dismiss": 1})
        self.assertEqual(measures.rerun_rate_after_replacement()["reruns_after_replacement"], 1)
        self.assertEqual(measures.restore_frequency(), {self.asset["system_id"]: 1})
        self.assertEqual(measures.dictionary_coverage_trend()[0]["created"], 1)
        self.assertEqual(measures.dictionary_coverage_trend()[0]["bound"], 1)
        self.assertEqual(measures.usage_over_time("day")[0]["count"], 19)

    def test_five_family_boundary_and_deterministic_variable_id(self):
        self.assertEqual(FAMILIES, ("asset", "version", "snapshot", "variable", "dictionary_version"))
        self.assertEqual(variable_id(self.asset["system_id"], "orders", "amount"),
                         f"VAR:{self.asset['system_id']}:orders:amount")
        with self.assertRaises(ValueError):
            typed_id("issue", "ISS:1")
        with self.assertRaises(ValueError):
            self._event("asset_selected", datetime.now(timezone.utc), object_type="issue", object_id="ISS:1")
        with self.assertRaises(ValueError):
            record_event(event_type="asset_selected", actor="alice", at=datetime.now(timezone.utc).isoformat(),
                         object_type="variable", object_id="VAR:not-an-asset:orders:amount")

    def test_factory_reset_is_the_only_explicit_event_deletion_path(self):
        self._event("asset_selected", datetime.now(timezone.utc))
        with s.get_conn() as conn:
            removed = delete_for_factory_reset(conn)
            conn.commit()
        self.assertEqual(removed, 1)
        self.assertEqual(s.query("usage_events"), [])

    def test_catalogue_exposes_active_and_superseded_snapshots_and_human_labels(self):
        asset = asset_service.create_asset("dataset", "catalogue-fixture", "none", actor="tester")
        first = asset_service.add_snapshot(asset["asset_id"], "fresh", actor="tester",
                                            snapshot_label="kept-old", row_count=2, column_count=1)
        second = asset_service.add_snapshot(asset["asset_id"], "full_replacement", actor="tester",
                                             snapshot_label="current-new", row_count=3, column_count=1,
                                             column_type_map_json={"tables": {"dataset": {"columns": ["a"], "types": {"a": "text"}}}})
        s.update("dq_items", {"item_id": second["item_id"]}, {"dictionary_state": "yes"})
        payload = catalogue.get_catalogue(asset["asset_id"])
        snapshots = [snap for version in payload["versions"] for snap in version["snapshots"]]
        labels = {snap["snapshot_label"]: snap["snapshot_status_label"] for snap in snapshots}
        self.assertEqual(labels["kept-old"], "Superseded (retained for audit)")
        self.assertEqual(labels["current-new"], "Active")
        self.assertEqual(payload["dictionary_state_label"], "Dictionary bound")
        self.assertEqual(payload["lifecycle_status_label"], "In progress")
        self.assertIn("completion_summary", snapshots[0])
        self.assertTrue(first["item_id"])


class StructuralSurfaceTests(unittest.TestCase):
    def test_catalogue_and_analytics_have_no_chart_export_or_dashboard_surface(self):
        paths = [_ROOT / "backend/analytics", _ROOT / "ui/src/pages/AssetCatalogue.jsx",
                 _ROOT / "ui/src/components/VersionDiff.jsx"]
        forbidden = re.compile(r"chart|canvas|svg-chart|export|dashboard", re.IGNORECASE)
        offenders = []
        for path in paths:
            candidates = path.rglob("*.py") if path.is_dir() else [path]
            if path.suffix == ".jsx":
                candidates = [path]
            for candidate in candidates:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8")
                    # JSX's required module declaration is not an export
                    # endpoint/component; scan the product surface for the
                    # prohibited feature vocabulary after removing it.
                    text = re.sub(r"^export default .*?$", "", text, flags=re.MULTILINE)
                    if forbidden.search(text):
                        offenders.append(str(candidate))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
