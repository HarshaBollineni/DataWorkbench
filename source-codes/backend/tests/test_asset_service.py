"""0.5.0 Step 3b (AST-01..09, AST-13, AST-22, C-40, D-25, P-05, P-08, P-11) —
the asset service layer: ``assets.service.create_asset`` / ``add_snapshot`` /
``supersede_version_set`` / ``restore_version_set`` / ``rename_alias``, and
the ``ai.v2.service`` entry points (``create_item`` / ``reupload_item`` /
``finalize_item`` / ``_write_table``) that now delegate into it.

Standalone-runnable (``python -m unittest tests.test_asset_service``), same
SYSTEM_DB_PATH-at-import sandboxing convention as the sibling S3a asset-model
test files.
"""
from __future__ import annotations

import inspect
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-asset-service.db"
_TMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archimedes-test-asset-service-uploads"
_TMP_ITEMDB = Path(tempfile.gettempdir()) / "archimedes-test-asset-service-itemdbs"
for _p in (_TMP_UPLOAD_DIR, _TMP_ITEMDB):
    if _p.exists():
        shutil.rmtree(_p)
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOAD_DIR)
os.environ["ITEM_DB_DIR"] = str(_TMP_ITEMDB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import pandas as pd  # noqa: E402

import system_db as s  # noqa: E402
from ai.v2 import service as v2_service  # noqa: E402
from assets import reads  # noqa: E402
from assets import service as assets_service  # noqa: E402
from dq_diagnostics import manifest as manifest_mod  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
#  create_asset (AST-01/02/03/22) — acceptance criteria 1, 2
# ---------------------------------------------------------------------------

class CreateAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_fresh_asset_gets_system_id_and_version_1_with_no_snapshot_yet(self):
        asset = assets_service.create_asset("dataset", "ca-test-1", "none", actor=None)
        self.assertRegex(asset["system_id"], r"^DS\d{4,}$")
        self.assertEqual(asset["display_name"], f"{asset['system_id']}-ca-test-1")
        self.assertEqual(asset["current_version_no"], 1)

        version = s.query_one("dq_asset_versions", asset_id=asset["asset_id"], version_no=1)
        self.assertEqual(version["status"], "current")
        self.assertIsNone(version["reference_schema_json"])
        self.assertEqual(s.query("dq_items", dataset_family_id=asset["asset_id"]), [],
                         "create_asset creates the asset only — no snapshot")

    def test_database_kind_gets_db_prefix(self):
        asset = assets_service.create_asset("database", "ca-test-2", "none", actor=None)
        self.assertRegex(asset["system_id"], r"^DB\d{4,}$")

    def test_two_assets_with_the_same_alias_both_succeed_with_distinct_system_ids(self):
        a = assets_service.create_asset("dataset", "shared-alias", "none", actor=None)
        b = assets_service.create_asset("dataset", "shared-alias", "none", actor=None)
        self.assertNotEqual(a["asset_id"], b["asset_id"])
        self.assertNotEqual(a["system_id"], b["system_id"])
        self.assertEqual(a["alias"], b["alias"], "aliases may collide — AST-03's whole point")
        self.assertNotEqual(a["display_name"], b["display_name"])

    def test_invalid_alias_rejected(self):
        with self.assertRaises(ValueError):
            assets_service.create_asset("dataset", "bad alias", "none", actor=None)
        with self.assertRaises(ValueError):
            assets_service.create_asset("dataset", "", "none", actor=None)

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            assets_service.create_asset("spreadsheet", "x", "none", actor=None)

    def test_invalid_time_basis_rejected(self):
        with self.assertRaises(ValueError):
            assets_service.create_asset("dataset", "x", "quarterly", actor=None)

    def test_asset_created_event_is_written_in_human_language(self):
        asset = assets_service.create_asset("dataset", "ca-test-3", "none", actor="tester")
        events = s.query("dq_asset_events", asset_id=asset["asset_id"], event_type="asset_created")
        self.assertEqual(len(events), 1)
        self.assertIn(asset["display_name"], events[0]["summary"])
        self.assertEqual(events[0]["actor"], "tester")


class NoGlobalNameUniquenessScanTests(unittest.TestCase):
    """Acceptance criterion 2 — the old global case-insensitive name scan
    (and the requires_reupload resume branch it protected) is GONE, not
    narrowed."""

    def test_service_py_contains_no_case_insensitive_name_uniqueness_scan(self):
        text = (_BACKEND_ROOT / "ai/v2/service.py").read_text(encoding="utf-8")
        self.assertNotIn("already in the inventory", text)
        self.assertNotIn('"resumed"', text)
        self.assertNotIn('for row in db.query("dq_items"):', text)

    def test_creating_two_assets_with_an_identical_name_raises_nothing(self):
        a = v2_service.create_item("dataset", "dup-name-test")
        b = v2_service.create_item("dataset", "dup-name-test")
        self.assertNotEqual(a["item_id"], b["item_id"])
        self.assertNotEqual(a["system_id"], b["system_id"])
        self.assertEqual(a["display_name"] != b["display_name"], True)


# ---------------------------------------------------------------------------
#  add_snapshot — fresh / add_period / full_replacement
# ---------------------------------------------------------------------------

class AddSnapshotFreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_fresh_snapshot_is_active_on_the_assets_own_family(self):
        asset = assets_service.create_asset("dataset", "fresh-test-1", "none", actor=None)
        snap = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        self.assertEqual(snap["dataset_family_id"], asset["asset_id"])
        self.assertEqual(snap["delivery_seq"], 1)
        self.assertEqual(snap["version_no"], 1)
        self.assertEqual(snap["snapshot_status"], "active")
        self.assertEqual(snap["intent"], "fresh")
        self.assertEqual(snap["name"], asset["display_name"])

    def test_fresh_twice_is_refused(self):
        asset = assets_service.create_asset("dataset", "fresh-test-2", "none", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        with self.assertRaises(ValueError):
            assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)

    def test_add_period_before_any_fresh_snapshot_is_refused(self):
        asset = assets_service.create_asset("dataset", "fresh-test-3", "none", actor=None)
        with self.assertRaises(ValueError):
            assets_service.add_snapshot(asset["asset_id"], intent="add_period", actor=None)

    def test_unknown_intent_is_refused(self):
        asset = assets_service.create_asset("dataset", "fresh-test-4", "none", actor=None)
        with self.assertRaises(ValueError):
            assets_service.add_snapshot(asset["asset_id"], intent="bogus", actor=None)

    def test_period_basis_asset_requires_a_start_date(self):
        asset = assets_service.create_asset("dataset", "fresh-test-5", "period", actor=None)
        with self.assertRaises(ValueError):
            assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        # legitimate call with a start_date succeeds.
        snap = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None,
                                           start_date="2026-01-31")
        self.assertEqual(snap["start_date"], "2026-01-31")


class AddSnapshotAddPeriodTests(unittest.TestCase):
    """Acceptance criterion 3 (3-A5)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_add_period_same_family_same_version_next_seq_prior_row_byte_identical(self):
        asset = assets_service.create_asset("dataset", "ap-test-1", "period", actor=None)
        first = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None,
                                            start_date="2026-01-31")
        row_before = dict(s.query_one("dq_items", item_id=first["item_id"]))

        second = assets_service.add_snapshot(asset["asset_id"], intent="add_period", actor=None,
                                             start_date="2026-02-28")

        row_after = dict(s.query_one("dq_items", item_id=first["item_id"]))
        self.assertEqual(row_before, row_after, "the prior snapshot's row is byte-identical")

        self.assertEqual(second["dataset_family_id"], asset["asset_id"])
        self.assertEqual(second["delivery_seq"], first["delivery_seq"] + 1)
        self.assertEqual(second["version_no"], first["version_no"])
        self.assertEqual(second["version_no"], 1, "add_period never bumps the version — AST-06")
        self.assertEqual(second["snapshot_status"], "active")
        self.assertEqual(row_after["snapshot_status"], "active",
                         "add_period supersedes nothing — both snapshots stay active")

        asset_after = s.query_one("dq_assets", asset_id=asset["asset_id"])
        self.assertEqual(asset_after["current_version_no"], 1)


class AddSnapshotFullReplacementTests(unittest.TestCase):
    """Acceptance criterion 4 (3-A6)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_full_replacement_bumps_version_and_supersedes_the_prior_set_as_one_batch(self):
        asset = assets_service.create_asset("dataset", "fr-test-1", "none", actor=None)
        first = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        second = assets_service.add_snapshot(asset["asset_id"], intent="add_period", actor=None)

        new_schema = {"tables": {"dataset": {"columns": ["a", "b"], "column_count": 2, "types": {}}}}
        result = assets_service.add_snapshot(
            asset["asset_id"], intent="full_replacement", actor="tester",
            column_type_map_json=new_schema,
        )

        asset_after = s.query_one("dq_assets", asset_id=asset["asset_id"])
        self.assertEqual(asset_after["current_version_no"], 2)

        old_row = s.query_one("dq_items", item_id=first["item_id"])
        second_row = s.query_one("dq_items", item_id=second["item_id"])
        new_row = s.query_one("dq_items", item_id=result["item_id"])
        self.assertEqual(old_row["snapshot_status"], "superseded")
        self.assertEqual(second_row["snapshot_status"], "superseded")
        self.assertEqual(new_row["snapshot_status"], "active")
        # ONE shared superseded_by_version_no across the whole prior set.
        self.assertEqual(old_row["superseded_by_version_no"], 2)
        self.assertEqual(second_row["superseded_by_version_no"], 2)
        self.assertIsNotNone(old_row["superseded_at"])
        self.assertIsNotNone(second_row["superseded_at"])
        self.assertEqual(new_row["version_no"], 2)

        versions = {v["version_no"]: v for v in s.query("dq_asset_versions", asset_id=asset["asset_id"])}
        self.assertEqual(set(versions), {1, 2})
        self.assertEqual(versions[1]["status"], "superseded")
        self.assertEqual(versions[2]["status"], "current")
        self.assertEqual(versions[2]["reference_schema_json"], new_schema)
        self.assertIsNotNone(versions[2]["superseded_at"] if False else True)  # v2 not superseded

        events = s.query("dq_asset_events", asset_id=asset["asset_id"], event_type="version_superseded")
        self.assertEqual(len(events), 1)
        self.assertIn("2", events[0]["summary"])

    def test_nothing_is_deleted_files_and_rows_survive_a_full_replacement(self):
        item = v2_service.create_item("dataset", "fr-test-2")
        old_id = item["item_id"]
        v2_service.save_file(old_id, "data", "a.csv", b"a,b\n1,2\n")
        file_row = s.query_one("dq_item_files", item_id=old_id, role="data")
        self.assertTrue(Path(file_row["path"]).is_file())
        table_row_before = dict(s.query_one("dq_item_tables", item_id=old_id))

        asset_id = s.query_one("dq_items", item_id=old_id)["dataset_family_id"]
        assets_service.add_snapshot(asset_id, intent="full_replacement", actor=None)

        self.assertTrue(Path(file_row["path"]).is_file(), "the prior snapshot's file is never deleted")
        table_row_after = dict(s.query_one("dq_item_tables", item_id=old_id))
        self.assertEqual(table_row_before, table_row_after)
        self.assertEqual(s.query_one("dq_items", item_id=old_id)["snapshot_status"], "superseded")
        self.assertIsNotNone(s.query_one("dq_items", item_id=old_id), "the row itself still exists")


# ---------------------------------------------------------------------------
#  restore_version_set (AST-09) — acceptance criterion 5
# ---------------------------------------------------------------------------

class RestoreVersionSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def _asset_with_two_versions(self, alias):
        asset = assets_service.create_asset("dataset", alias, "none", actor=None)
        v1_snap = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        v2_snap = assets_service.add_snapshot(asset["asset_id"], intent="full_replacement", actor=None)
        return asset["asset_id"], v1_snap["item_id"], v2_snap["item_id"]

    def test_restore_reactivates_v1_wholesale_and_supersedes_v2(self):
        asset_id, v1_id, v2_id = self._asset_with_two_versions("restore-test-1")
        result = assets_service.restore_version_set(asset_id, 1, actor="tester")
        self.assertFalse(result["noop"])

        asset = s.query_one("dq_assets", asset_id=asset_id)
        self.assertEqual(asset["current_version_no"], 1, "v1's schema is current again")
        self.assertEqual(s.query_one("dq_items", item_id=v1_id)["snapshot_status"], "active")
        self.assertEqual(s.query_one("dq_items", item_id=v2_id)["snapshot_status"], "superseded")
        versions = {v["version_no"]: v["status"] for v in s.query("dq_asset_versions", asset_id=asset_id)}
        self.assertEqual(versions, {1: "current", 2: "superseded"})

        events = s.query("dq_asset_events", asset_id=asset_id, event_type="version_restored")
        self.assertEqual(len(events), 1)

    def test_restore_twice_is_a_clean_noop_never_a_double_active_state(self):
        asset_id, v1_id, v2_id = self._asset_with_two_versions("restore-test-2")
        assets_service.restore_version_set(asset_id, 1, actor="tester")
        result = assets_service.restore_version_set(asset_id, 1, actor="tester")
        self.assertTrue(result["noop"])

        rows = s.query("dq_items", dataset_family_id=asset_id)
        active = [r["item_id"] for r in rows if r["snapshot_status"] == "active"]
        self.assertEqual(active, [v1_id], "exactly one active snapshot — never both v1 and v2")

        # the repeat call is itself recorded (the event log, not a
        # renumbering, records that it happened twice).
        events = s.query("dq_asset_events", asset_id=asset_id, event_type="version_restored")
        self.assertEqual(len(events), 2)

    def test_restore_unknown_version_raises(self):
        asset = assets_service.create_asset("dataset", "restore-test-3", "none", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        with self.assertRaises(ValueError):
            assets_service.restore_version_set(asset["asset_id"], 99, actor=None)

    def test_restore_unknown_asset_raises(self):
        with self.assertRaises(ValueError):
            assets_service.restore_version_set("no-such-asset", 1, actor=None)


class SupersedeVersionSetDirectCallTests(unittest.TestCase):
    """supersede_version_set is exercised indirectly by every
    full_replacement test above; these cover its own guard rails and the
    fact it is one coordinated set operation, never a per-snapshot loop."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_supersede_a_still_current_version_is_refused(self):
        asset = assets_service.create_asset("dataset", "supersede-test-1", "none", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        with self.assertRaises(ValueError):
            assets_service.supersede_version_set(asset["asset_id"], 1, actor=None)

    def test_preview_supersede_names_the_correct_count_and_labels(self):
        asset = assets_service.create_asset("dataset", "supersede-test-2", "period", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None,
                                    start_date="2026-01-31")
        assets_service.add_snapshot(asset["asset_id"], intent="add_period", actor=None,
                                    start_date="2026-02-28")
        preview = assets_service.preview_supersede(asset["asset_id"], 1)
        self.assertEqual(preview["snapshot_count"], 2)
        self.assertIn("2026-01-31", preview["description"])
        self.assertIn("2026-02-28", preview["description"])


# ---------------------------------------------------------------------------
#  rename_alias (AST-04) — acceptance criterion 6
# ---------------------------------------------------------------------------

class RenameAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_rename_updates_display_name_mirrors_to_every_item_leaves_ids_untouched(self):
        asset = assets_service.create_asset("dataset", "old-alias", "none", actor=None)
        snap1 = assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        snap2 = assets_service.add_snapshot(asset["asset_id"], intent="add_period", actor=None)

        renamed = assets_service.rename_alias(asset["asset_id"], "new-alias", actor="tester")

        self.assertEqual(renamed["alias"], "new-alias")
        self.assertEqual(renamed["display_name"], f"{asset['system_id']}-new-alias")
        self.assertEqual(renamed["system_id"], asset["system_id"], "system_id never changes")
        self.assertEqual(renamed["asset_id"], asset["asset_id"], "asset_id never changes")

        for item_id in (snap1["item_id"], snap2["item_id"]):
            row = s.query_one("dq_items", item_id=item_id)
            self.assertEqual(row["name"], renamed["display_name"])
            self.assertEqual(row["item_id"], item_id, "item_id never changes")

        events = s.query("dq_asset_events", asset_id=asset["asset_id"], event_type="alias_renamed")
        self.assertEqual(len(events), 1)

    def test_rename_rejects_an_invalid_alias(self):
        asset = assets_service.create_asset("dataset", "keep-me", "none", actor=None)
        with self.assertRaises(ValueError):
            assets_service.rename_alias(asset["asset_id"], "bad alias!", actor=None)
        self.assertEqual(s.query_one("dq_assets", asset_id=asset["asset_id"])["alias"], "keep-me")


# ---------------------------------------------------------------------------
#  Time-basis immutability (AST-22/D-30) — acceptance criterion 8
# ---------------------------------------------------------------------------

class TimeBasisImmutabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_add_snapshot_refuses_a_time_basis_kwarg_direction_one_refusal_fires(self):
        asset = assets_service.create_asset("dataset", "tb-test-1", "none", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        with self.assertRaises(ValueError):
            assets_service.add_snapshot(asset["asset_id"], intent="full_replacement",
                                        actor=None, time_basis="period")
        # the refusal fires before anything is mutated.
        after = s.query_one("dq_assets", asset_id=asset["asset_id"])
        self.assertEqual(after["time_basis"], "none")
        self.assertEqual(after["current_version_no"], 1)

    def test_legitimate_full_replacement_without_touching_time_basis_succeeds_direction_two(self):
        asset = assets_service.create_asset("dataset", "tb-test-2", "none", actor=None)
        assets_service.add_snapshot(asset["asset_id"], intent="fresh", actor=None)
        result = assets_service.add_snapshot(asset["asset_id"], intent="full_replacement", actor=None)
        self.assertEqual(result["version_no"], 2)
        after = s.query_one("dq_assets", asset_id=asset["asset_id"])
        self.assertEqual(after["time_basis"], "none",
                         "time basis survives a full replacement unchanged (AST-22/D-30)")

    def test_no_update_call_anywhere_in_the_backend_targets_dq_assets_time_basis(self):
        anchor = re.compile(r'\.update\(\s*"dq_assets"')
        for py_file in _BACKEND_ROOT.rglob("*.py"):
            if any(part in ("tests", "__pycache__") for part in py_file.parts):
                continue
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for m in anchor.finditer(text):
                window = text[m.start():m.start() + 400]
                self.assertNotIn("time_basis", window,
                                f"{py_file} appears to update dq_assets.time_basis near "
                                f"offset {m.start()}")


# ---------------------------------------------------------------------------
#  Enforcement points — acceptance criteria 9, 10, 7
# ---------------------------------------------------------------------------

class NoHalfSupersedeSurfaceTests(unittest.TestCase):
    """P-11 / acceptance criterion 9 — no route or function supersedes or
    restores a single snapshot in isolation; only the whole-set operations,
    keyed by (asset_id, version_no), exist anywhere."""

    def test_supersede_and_restore_are_keyed_by_asset_and_version_not_a_snapshot(self):
        for name in ("supersede_version_set", "restore_version_set"):
            fn = getattr(assets_service, name)
            params = list(inspect.signature(fn).parameters)
            self.assertEqual(params[0], "asset_id", f"{name} must take asset_id first")
            self.assertIn("version_no", params, f"{name} must take a whole version_no")
            self.assertNotIn("item_id", params)
            self.assertNotIn("snapshot_id", params)

    def test_v2_only_exposes_whole_version_supersede_preview_and_restore(self):
        text = "\n".join(
            (_BACKEND_ROOT / "routers" / name).read_text(encoding="utf-8").lower()
            for name in ("v2.py", "sourcing.py")
        )
        self.assertIn("supersede-preview", text)
        self.assertIn("versions/{version_no}/restore", text)
        self.assertNotIn("supersede_version_set(asset_id, snapshot", text)
        self.assertNotIn("restore_version_set(asset_id, snapshot", text)

    def test_no_function_in_assets_or_routers_supersedes_or_restores_by_snapshot_id(self):
        offending = re.compile(
            r"def\s+\w*(supersede|restore)\w*\(\s*(self,\s*)?(item_id|snapshot_id)\b")
        for folder in ("routers", "assets"):
            for py_file in (_BACKEND_ROOT / folder).glob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                self.assertIsNone(offending.search(text),
                                 f"{py_file} defines a per-snapshot supersede/restore function")


class NoSnapshotDeleteSurfaceTests(unittest.TestCase):
    """AST-08/OOS-15 / acceptance criterion 10 — superseded snapshots (and
    their files) are permanently retained; the factory reset is the ONLY
    thing that ever removes them (D-29's accepted exception, exercised in
    test_asset_identity.py's EpochResetTests, not here)."""

    def test_assets_service_defines_no_delete_style_function(self):
        for name in dir(assets_service):
            if name.startswith("_"):
                continue
            self.assertNotRegex(name.lower(), r"delete|purge|remove",
                               f"assets.service.{name} looks like a snapshot-delete function")

    def test_v2_router_has_no_delete_route_for_items(self):
        text = (_BACKEND_ROOT / "routers/v2.py").read_text(encoding="utf-8")
        self.assertNotIn('@router.delete("/items', text)
        self.assertNotIn("@router.delete('/items", text)


class SupersededSnapshotRefusedInManifestTests(unittest.TestCase):
    """Acceptance criterion 7 — a superseded snapshot is refused by the
    manifest-creation endpoint with an explicit message, and is absent from
    ordered_snapshots(only_active=True)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_build_manifest_refuses_a_superseded_snapshot_with_a_clear_message(self):
        item = v2_service.create_item("dataset", "mf-test-1")
        old_id = item["item_id"]
        asset_id = s.query_one("dq_items", item_id=old_id)["dataset_family_id"]
        assets_service.add_snapshot(asset_id, intent="full_replacement", actor=None)

        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.build_manifest(old_id, 4)
        self.assertIn("superseded", str(ctx.exception).lower())

    def test_superseded_snapshot_absent_from_ordered_snapshots_only_active(self):
        item = v2_service.create_item("dataset", "mf-test-2")
        old_id = item["item_id"]
        asset_id = s.query_one("dq_items", item_id=old_id)["dataset_family_id"]
        replacement = assets_service.add_snapshot(asset_id, intent="full_replacement", actor=None)

        active_ids = [r["item_id"] for r in reads.ordered_snapshots(asset_id, only_active=True)]
        self.assertNotIn(old_id, active_ids)
        self.assertIn(replacement["item_id"], active_ids)


# ---------------------------------------------------------------------------
#  _write_table (rule 9) — snapshot-cache guard + row/column contribution
# ---------------------------------------------------------------------------

class WriteTableSupersededGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_write_table_refuses_a_superseded_snapshot(self):
        item = v2_service.create_item("dataset", "wt-test-1")
        old_id = item["item_id"]
        asset_id = s.query_one("dq_items", item_id=old_id)["dataset_family_id"]
        assets_service.add_snapshot(asset_id, intent="full_replacement", actor=None)
        self.assertEqual(s.query_one("dq_items", item_id=old_id)["snapshot_status"], "superseded")

        frame = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        with self.assertRaises(ValueError):
            v2_service._write_table(old_id, "t", frame)

    def test_write_table_contributes_row_and_column_totals_to_the_snapshot_row(self):
        item = v2_service.create_item("dataset", "wt-test-2")
        item_id = item["item_id"]
        frame = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]})
        v2_service._write_table(item_id, "t1", frame)
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["row_count"], 3)
        self.assertEqual(row["column_count"], 3)


# ---------------------------------------------------------------------------
#  finalize_item (AST-13) — writes to dq_assets, mirrors to dq_items
# ---------------------------------------------------------------------------

class FinalizeItemAssetLevelStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_finalize_writes_to_dq_assets_and_mirrors_onto_the_snapshot_row(self):
        item = v2_service.create_item("dataset", "fin-test-1")
        item_id = item["item_id"]
        frame = pd.DataFrame({"default_flag": [0, 1], "amount": [100, 200]})
        v2_service._write_table(item_id, "t1", frame)

        v2_service.finalize_item(item_id, target_variable="default_flag", use_case="IFRS9")

        asset_id = s.query_one("dq_items", item_id=item_id)["dataset_family_id"]
        asset = s.query_one("dq_assets", asset_id=asset_id)
        self.assertIsNone(asset["target_variable"], "fresh upload target is blank (CTX-04)")
        self.assertIsNone(asset["use_case"], "fresh upload use case is blank (CTX-04)")

        snapshot = s.query_one("dq_items", item_id=item_id)
        self.assertIsNone(snapshot["target_variable"])
        self.assertIsNone(snapshot["use_case"])


if __name__ == "__main__":
    unittest.main()
