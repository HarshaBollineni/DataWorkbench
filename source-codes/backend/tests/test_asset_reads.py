"""0.5.0 Step 3a (task 3.6) — the read-only asset query helpers:
assets.reads.list_assets / ordered_snapshots / snapshot_read_model.

Standalone-runnable (``python -m unittest tests.test_asset_reads``), same
SYSTEM_DB_PATH-at-import sandboxing convention as the sibling asset-model
test files.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-asset-reads.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from assets import identity, reads  # noqa: E402


def _make_asset(kind: str, time_basis: str, lifecycle_status: str = "sourcing") -> str:
    asset_id = f"asset_{uuid.uuid4().hex[:8]}"
    system_id = identity.allocate(identity.scope_for_kind(kind))
    alias = "probe"
    display_name = identity.compose_display_name(system_id, alias)
    now = s.now_ist()
    s.insert("dq_assets", {
        "asset_id": asset_id, "system_id": system_id, "alias": alias,
        "display_name": display_name, "kind": kind, "time_basis": time_basis,
        "current_version_no": 1, "lifecycle_status": lifecycle_status,
        "target_variable": None, "use_case": None,
        "current_dictionary_version_id": None,
        "created_by": None, "created_at": now, "updated_at": now,
    })
    return asset_id


def _make_snapshot(asset_id: str, *, snapshot_status: str = "active",
                   start_date: str | None = None, created_at: str | None = None,
                   name: str | None = None) -> str:
    item_id = f"itm_{uuid.uuid4().hex[:8]}"
    now = created_at or s.now_ist()
    # AST-22/UPL-14: a 'none'-basis snapshot's label must be unique WITHIN
    # the asset (ux_dq_items_label_in_family) — use the item_id itself as
    # the fallback label so repeated calls for the same asset never collide.
    s.insert("dq_items", {
        "item_id": item_id, "kind": "dataset", "name": name or asset_id,
        "status": "sourcing", "module_tag": "", "target_variable": None, "use_case": None,
        "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1,
        "version_no": 1, "snapshot_status": snapshot_status, "start_date": start_date,
        "snapshot_label": start_date or item_id, "intent": "fresh",
        "row_count": 10, "column_count": 2, "file_name": "f.csv",
        "has_time_period": 1 if start_date else 0,
    })
    return item_id


class ListAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_one_row_per_asset_with_active_snapshot_count(self):
        asset_id = _make_asset("dataset", "none")
        _make_snapshot(asset_id, snapshot_status="active")
        _make_snapshot(asset_id, snapshot_status="active")
        _make_snapshot(asset_id, snapshot_status="superseded")

        rows = reads.list_assets()
        row = next(r for r in rows if r["asset_id"] == asset_id)
        self.assertEqual(row["snapshot_count"], 2, "only active snapshots are counted")
        self.assertIsNotNone(row["last_upload_at"])

    def test_kind_filter(self):
        ds_id = _make_asset("dataset", "none")
        db_id = _make_asset("database", "none")
        dataset_rows = {r["asset_id"] for r in reads.list_assets(kind="dataset")}
        database_rows = {r["asset_id"] for r in reads.list_assets(kind="database")}
        self.assertIn(ds_id, dataset_rows)
        self.assertNotIn(db_id, dataset_rows)
        self.assertIn(db_id, database_rows)
        self.assertNotIn(ds_id, database_rows)

    def test_exclude_lifecycle_filters_out_matching_assets(self):
        keep_id = _make_asset("dataset", "none", lifecycle_status="sourcing")
        drop_id = _make_asset("dataset", "none", lifecycle_status="complete")
        rows = reads.list_assets(exclude_lifecycle=["complete", "archived", "requires_reupload"])
        ids = {r["asset_id"] for r in rows}
        self.assertIn(keep_id, ids)
        self.assertNotIn(drop_id, ids)

    def test_no_exclusion_means_everything_listed(self):
        drop_id = _make_asset("dataset", "none", lifecycle_status="complete")
        ids = {r["asset_id"] for r in reads.list_assets()}
        self.assertIn(drop_id, ids)

    def test_profiled_staged_snapshot_is_exposed_for_sourcing_continuation(self):
        asset_id = _make_asset("dataset", "none")
        item_id = _make_snapshot(asset_id, snapshot_status="active")
        now = s.now_ist()
        conn = s.get_conn()
        try:
            conn.execute(
                "UPDATE dq_items SET ingest_status=?, snapshot_label=?, intent=? WHERE item_id=?",
                ("needs_review", f"__staged_{item_id}", "fresh", item_id),
            )
            conn.commit()
        finally:
            conn.close()
        s.insert("dq_item_files", {
            "file_id": f"file_{item_id}", "item_id": item_id, "role": "data",
            "filename": "retained.csv", "path": "retained.csv", "completed_at": now,
        })

        row = next(r for r in reads.list_assets() if r["asset_id"] == asset_id)

        self.assertFalse(row["selectable"], "unfinished snapshots must remain outside Test Lab")
        self.assertTrue(row["resumable"])
        self.assertEqual(row["resume_snapshot_id"], item_id)
        self.assertEqual(row["resume_ingest_status"], "needs_review")
        self.assertEqual(row["resume_intent"], "fresh")
        self.assertEqual(row["resume_file_name"], "retained.csv")
        self.assertTrue(row["resume_has_data"])

    def test_ready_snapshot_is_selectable_and_not_marked_resumable(self):
        asset_id = _make_asset("dataset", "none")
        item_id = _make_snapshot(asset_id, snapshot_status="active")
        conn = s.get_conn()
        try:
            conn.execute(
                "UPDATE dq_items SET ingest_status=?, snapshot_label=? WHERE item_id=?",
                ("ready", "Approved snapshot", item_id),
            )
            conn.commit()
        finally:
            conn.close()

        row = next(r for r in reads.list_assets() if r["asset_id"] == asset_id)

        self.assertTrue(row["selectable"])
        self.assertFalse(row["resumable"])
        self.assertIsNone(row["resume_snapshot_id"])

    def test_does_not_replace_list_items(self):
        """rule 1/7 — list_items keeps working; this is a NEW, coexisting
        read model, not a swap-in replacement."""
        from ai.v2 import service
        self.assertTrue(callable(service.list_items))


class OrderedSnapshotsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_period_basis_sorts_by_start_date(self):
        asset_id = _make_asset("dataset", "period")
        item_mar = _make_snapshot(asset_id, start_date="2026-03-31")
        item_jan = _make_snapshot(asset_id, start_date="2026-01-31")
        item_feb = _make_snapshot(asset_id, start_date="2026-02-28")

        ordered = reads.ordered_snapshots(asset_id)
        self.assertEqual([r["item_id"] for r in ordered], [item_jan, item_feb, item_mar])

    def test_none_basis_sorts_by_created_at(self):
        asset_id = _make_asset("dataset", "none")
        item_late = _make_snapshot(asset_id, created_at="2026-03-01T00:00:00+05:30")
        item_early = _make_snapshot(asset_id, created_at="2026-01-01T00:00:00+05:30")
        item_mid = _make_snapshot(asset_id, created_at="2026-02-01T00:00:00+05:30")

        ordered = reads.ordered_snapshots(asset_id)
        self.assertEqual([r["item_id"] for r in ordered], [item_early, item_mid, item_late])

    def test_only_active_excludes_superseded_by_default(self):
        asset_id = _make_asset("dataset", "none")
        active_item = _make_snapshot(asset_id, snapshot_status="active")
        _make_snapshot(asset_id, snapshot_status="superseded")

        ordered = reads.ordered_snapshots(asset_id)
        self.assertEqual([r["item_id"] for r in ordered], [active_item])

        ordered_all = reads.ordered_snapshots(asset_id, only_active=False)
        self.assertEqual(len(ordered_all), 2)

    def test_unknown_asset_raises(self):
        with self.assertRaises(ValueError):
            reads.ordered_snapshots("no-such-asset")

    def test_well_formed_period_set_does_not_raise(self):
        """The 'guard NOT firing on a well-formed set' half of the
        bites-check (acceptance criterion 8) — a normal, correctly-built
        period-basis asset must sort without incident."""
        asset_id = _make_asset("dataset", "period")
        _make_snapshot(asset_id, start_date="2026-01-31")
        _make_snapshot(asset_id, start_date="2026-02-28")
        try:
            reads.ordered_snapshots(asset_id)
        except ValueError:
            self.fail("ordered_snapshots raised on a well-formed, non-mixed period-basis set")

    def test_well_formed_none_set_does_not_raise(self):
        asset_id = _make_asset("dataset", "none")
        _make_snapshot(asset_id)
        _make_snapshot(asset_id)
        try:
            reads.ordered_snapshots(asset_id)
        except ValueError:
            self.fail("ordered_snapshots raised on a well-formed, non-mixed none-basis set")

    def test_forced_mixed_basis_set_raises_not_silently_interleaves(self):
        """AST-11/AST-22 bites-check (acceptance criterion 8, adversarial
        case 4): force, via raw SQL, a dated snapshot into a
        time_basis='none' asset that already has undated snapshots. This
        state is supposed to be impossible by construction; prove the read
        model refuses to order it rather than silently interleaving."""
        asset_id = _make_asset("dataset", "none")
        _make_snapshot(asset_id)  # undated, as every 'none'-basis snapshot should be
        forced_item = _make_snapshot(asset_id)
        conn = s.get_conn()
        try:
            conn.execute(
                "UPDATE dq_items SET start_date=? WHERE item_id=?",
                ("2026-05-31", forced_item),
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(ValueError):
            reads.ordered_snapshots(asset_id)

    def test_forced_mixed_basis_set_under_period_basis_also_raises(self):
        """The symmetric case: a period-basis asset with one snapshot
        missing its start_date must also raise, not silently sort the
        undated row to one end."""
        asset_id = _make_asset("dataset", "period")
        _make_snapshot(asset_id, start_date="2026-01-31")
        undated_item = _make_snapshot(asset_id, start_date="2026-02-28")
        conn = s.get_conn()
        try:
            conn.execute(
                "UPDATE dq_items SET start_date=NULL WHERE item_id=?", (undated_item,)
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaises(ValueError):
            reads.ordered_snapshots(asset_id)


class SnapshotReadModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_exposes_every_ast13_field_one_to_one(self):
        asset_id = _make_asset("dataset", "period")
        item_id = _make_snapshot(asset_id, start_date="2026-01-31")
        asset = s.query_one("dq_assets", asset_id=asset_id)

        model = reads.snapshot_read_model(item_id)

        self.assertEqual(set(model.keys()), set(reads.AST_13_FIELDS))
        self.assertEqual(model["snapshot_id"], item_id, "snapshot_id <- item_id (P-06)")
        self.assertEqual(model["asset_id"], asset_id)
        self.assertEqual(model["asset_name"], asset["display_name"])
        self.assertEqual(model["status"], "active")
        self.assertIsNotNone(model["uploaded_at"], "uploaded_at <- created_at (P-06)")
        self.assertEqual(model["start_date"], "2026-01-31")
        self.assertEqual(model["row_count"], 10)
        self.assertEqual(model["column_count"], 2)
        self.assertEqual(model["intent"], "fresh")
        self.assertIsInstance(model["has_time_period"], bool)
        self.assertIsInstance(model["schema_override_flag"], bool)

    def test_unknown_snapshot_raises(self):
        with self.assertRaises(ValueError):
            reads.snapshot_read_model("no-such-item")


if __name__ == "__main__":
    unittest.main()
