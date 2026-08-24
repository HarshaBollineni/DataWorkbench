"""0.5.0 Step 3a (AST-10/PLT-06) — the asset-model migration/backfill gate.

Mirrors test_migration_idempotency.py's/test_delivery.py's standalone-
runnable, SYSTEM_DB_PATH-at-import sandboxing convention. Proves (plan
operating rule 3, acceptance criteria 2/3/4/9/10 of the S3a task brief):

  - init_schema() on a FRESH EMPTY DB succeeds with zero assets, no error.
  - init_schema() run TWICE on a POPULATED pre-0.5.0-shaped DB produces an
    IDENTICAL canonical schema hash and an IDENTICAL hash over every
    surviving row, on the second run — true idempotence, not "it didn't
    crash" (plan operating rule 5).
  - every pre-existing family becomes EXACTLY one dq_assets row, version_no=1,
    every dq_items row of it snapshot_status='active'.
  - the four PLT-02 columns are never dropped/renamed (rule 4).
  - the hostile-DB edge cases named in the S3a task brief: a whitespace-only
    origin name, a tied delivery_seq, a family joined via register_delivery
    (dataset_family_id backfilled by _backfill_delivery_defaults BEFORE the
    asset backfill sees it).
  - the R-04 drift repair is itself idempotent.

Each test method gets its OWN brand-new, never-before-touched SQLite file
(via ``_FreshDbTestCase`` below, monkeypatching ``system_db.SYS_DB_PATH``)
rather than deleting a shared file between tests. This codebase's sqlite3
connections are never explicitly ``.close()``d (see
``system_db.insert``/``update``/``query``'s ``with get_conn() as conn``
convention — that context manager only manages the transaction, not the
file handle), so on Windows, unlinking a shared DB file mid-suite fails with
``WinError 32`` while an earlier test's handle is still open. A fresh path
per test sidesteps that entirely and gives genuine isolation besides.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-asset-migration.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from dq_diagnostics import delivery  # noqa: E402


def _fresh_db_path() -> Path:
    return Path(tempfile.gettempdir()) / f"archimedes-test-asset-migration-{uuid.uuid4().hex[:10]}.db"


class _FreshDbTestCase(unittest.TestCase):
    """Point system_db at a brand-new SQLite file for the duration of each
    test method (see module docstring for why this beats deleting a shared
    file between tests on Windows)."""

    def setUp(self):
        self._prior_path = s.SYS_DB_PATH
        s.SYS_DB_PATH = _fresh_db_path()

    def tearDown(self):
        s.SYS_DB_PATH = self._prior_path


def _schema_hash() -> str:
    """A hash over the canonical schema — every table/index's CREATE SQL,
    ordered deterministically. Identical on two runs iff the schema itself
    (not merely the row counts) is unchanged."""
    conn = s.get_conn()
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table','index') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()
    blob = "\n".join(f"{r[0]}|{r[1]}|{r[2]}" for r in rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _rows_hash(tables: list[str]) -> str:
    """A hash over every surviving row of ``tables``, in a stable column and
    row order, so re-running the migration is provably a true no-op — not
    merely "the same COUNT of rows", which would miss a value silently
    getting rewritten."""
    conn = s.get_conn()
    try:
        parts = []
        for t in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
            if not cols:
                parts.append(f"{t}|MISSING")
                continue
            col_list = ", ".join(f'"{c}"' for c in cols)
            pk = cols[0]
            rows = conn.execute(
                f'SELECT {col_list} FROM "{t}" ORDER BY "{pk}"'
            ).fetchall()
            parts.append(f"{t}|" + repr([tuple(r) for r in rows]))
    finally:
        conn.close()
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


_SURVIVING_TABLES = [
    "dq_items", "dq_item_files", "dq_item_tables", "variable_inventory",
    "dq_assets", "dq_asset_versions", "dq_asset_dictionaries", "dq_asset_events",
    "id_sequences",
]


class FreshEmptyDbTests(_FreshDbTestCase):
    """Acceptance criterion 2: a fresh empty DB migrates cleanly to zero
    assets, no error, on the first call AND on a repeat call."""

    def test_fresh_db_has_zero_assets_no_error(self):
        s.init_schema()  # must not raise
        self.assertEqual(s.query("dq_assets"), [])
        self.assertEqual(s.query("dq_asset_versions"), [])
        self.assertEqual(s.query("dq_asset_events"), [])

    def test_fresh_db_second_run_still_zero_assets(self):
        s.init_schema()
        s.init_schema()
        self.assertEqual(s.query("dq_assets"), [])


class PopulatedDbIdempotenceTests(_FreshDbTestCase):
    """Acceptance criterion 3/10: run init_schema() TWICE on a populated
    pre-0.5.0-shaped DB (today's dq_items rows with families, no dq_assets
    yet) — identical canonical schema hash AND identical surviving-row hash
    on the second run."""

    def _seed_pre_050_shape(self) -> None:
        """Build a DB that looks like today's product: dq_items rows joined
        into PLT-02 families via register_delivery (exactly how
        reupload_item does it today), with NO dq_assets rows at all —
        i.e. the exact shape the S3a migration must act on."""
        s.init_schema()  # establish the 0.5.0 columns as NULL, no assets yet
        now = s.now_ist()
        s.insert("dq_items", {
            "item_id": "itmA1", "kind": "dataset", "name": "Retail PD Data",
            "status": "complete", "module_tag": "", "target_variable": "default_flag",
            "use_case": "IFRS9", "created_at": now, "updated_at": now,
        })
        s.insert("dq_items", {
            "item_id": "itmA2", "kind": "dataset", "name": "Retail PD Data",
            "status": "complete", "module_tag": "", "target_variable": "default_flag",
            "use_case": "IFRS9", "created_at": now, "updated_at": now,
        })
        s.insert("dq_item_tables", {
            "item_id": "itmA1", "table_name": "t1", "row_count": 100, "col_count": 3,
            "columns": ["a", "b", "c"],
        })
        s.insert("variable_inventory", {
            "item_id": "itmA1", "table_name": "t1", "column_name": "a",
            "classification": "numeric", "data_type": "int", "description": "",
            "discrepancies": [], "notes": "", "role": "feature", "profile_json": {},
            "updated_at": now,
        })
        s.insert("dq_item_files", {
            "file_id": "fA1", "item_id": "itmA1", "role": "data",
            "filename": "jan.csv", "path": "x", "completed_at": now,
        })
        delivery.register_delivery("itmA1", "famA")
        delivery.register_delivery("itmA2", "famA", as_of_date="2026-06-30")
        # A second, single-item family with no as_of_date at all (none basis).
        s.insert("dq_items", {
            "item_id": "itmB1", "kind": "database", "name": "Collateral DB",
            "status": "sourcing", "module_tag": "", "target_variable": None,
            "use_case": None, "created_at": now, "updated_at": now,
        })

    def test_migration_run_twice_is_byte_identical(self):
        self._seed_pre_050_shape()
        s.init_schema()  # PASS 1 — this is the migration under test

        schema_hash_1 = _schema_hash()
        rows_hash_1 = _rows_hash(_SURVIVING_TABLES)
        assets_after_1 = {a["asset_id"]: a for a in s.query("dq_assets")}
        self.assertEqual(len(assets_after_1), 2, "exactly one asset per pre-existing family")

        s.init_schema()  # PASS 2 — must be a true no-op

        schema_hash_2 = _schema_hash()
        rows_hash_2 = _rows_hash(_SURVIVING_TABLES)

        self.assertEqual(schema_hash_1, schema_hash_2, "canonical schema hash changed on rerun")
        self.assertEqual(rows_hash_1, rows_hash_2, "surviving-row hash changed on rerun")

        s.init_schema()  # PASS 3 — a true fixed point, not "stable after one extra run"
        self.assertEqual(_schema_hash(), schema_hash_2)
        self.assertEqual(_rows_hash(_SURVIVING_TABLES), rows_hash_2)

    def test_every_family_becomes_exactly_one_asset_v1_all_rows_active(self):
        self._seed_pre_050_shape()
        s.init_schema()

        fam_a = s.query_one("dq_assets", asset_id="famA")
        self.assertIsNotNone(fam_a)
        self.assertEqual(fam_a["current_version_no"], 1)
        self.assertEqual(fam_a["kind"], "dataset")
        self.assertEqual(fam_a["time_basis"], "period", "famA has a non-null as_of_date member")
        self.assertRegex(fam_a["system_id"], r"^DS\d{4,}$")
        self.assertEqual(fam_a["display_name"], f"{fam_a['system_id']}-{fam_a['alias']}")

        fam_b = s.query_one("dq_assets", asset_id="itmB1")
        self.assertIsNotNone(fam_b)
        self.assertEqual(fam_b["time_basis"], "none", "itmB1's family has no as_of_date at all")
        self.assertRegex(fam_b["system_id"], r"^DB\d{4,}$")

        for item_id in ("itmA1", "itmA2", "itmB1"):
            row = s.query_one("dq_items", item_id=item_id)
            self.assertEqual(row["version_no"], 1)
            self.assertEqual(row["snapshot_status"], "active")

        self.assertEqual(s.query_one("dq_items", item_id="itmA1")["intent"], "fresh")
        self.assertEqual(s.query_one("dq_items", item_id="itmA2")["intent"], "add_period")

        versions = s.query("dq_asset_versions", asset_id="famA")
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version_no"], 1)
        self.assertEqual(versions[0]["status"], "current")
        self.assertIsNotNone(versions[0]["reference_schema_json"], "origin was profiled — a schema is expected")
        self.assertIn("t1", versions[0]["reference_schema_json"]["tables"])

        # itmB1 was never profiled (no dq_item_tables / variable_inventory rows) —
        # its reference schema must be left NULL, never invented.
        versions_b = s.query("dq_asset_versions", asset_id="itmB1")
        self.assertEqual(len(versions_b), 1)
        self.assertIsNone(versions_b[0]["reference_schema_json"])

        events_a = s.query("dq_asset_events", asset_id="famA")
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0]["event_type"], "asset_created")
        self.assertEqual(events_a[0]["summary"], "Migrated from the 0.4.0 item model.")

    def test_drift_repair_is_idempotent(self):
        self._seed_pre_050_shape()
        s.init_schema()
        display_name = s.query_one("dq_assets", asset_id="famA")["display_name"]

        s.update("dq_items", {"item_id": "itmA2"}, {"name": "some stale drifted name"})
        self.assertEqual(s.query_one("dq_items", item_id="itmA2")["name"], "some stale drifted name")

        s.init_schema()
        self.assertEqual(s.query_one("dq_items", item_id="itmA2")["name"], display_name)
        # No duplicate asset was created by the drift-repair pass.
        self.assertEqual(len(s.query("dq_assets")), 2)

        rows_hash_1 = _rows_hash(_SURVIVING_TABLES)
        s.init_schema()  # drift repair itself must be a no-op on an already-correct row
        self.assertEqual(_rows_hash(_SURVIVING_TABLES), rows_hash_1)

    def test_plt02_columns_survive_untouched(self):
        """rule 4 / verify_plan8.py:136 — the migration adds columns, never
        drops or renames dataset_family_id/delivery_seq/as_of_date/
        baseline_delivery_id."""
        self._seed_pre_050_shape()
        s.init_schema()
        conn = s.get_conn()
        try:
            cols = {r[1] for r in conn.execute('PRAGMA table_info("dq_items")')}
        finally:
            conn.close()
        needed = {"dataset_family_id", "delivery_seq", "as_of_date", "baseline_delivery_id"}
        self.assertTrue(needed <= cols, f"missing PLT-02 columns: {needed - cols}")


class HostileMigrationInputTests(_FreshDbTestCase):
    """Adversarial case 10 of the S3a task brief."""

    def test_whitespace_only_name_sanitises_to_asset_never_empty(self):
        s.init_schema()
        now = s.now_ist()
        s.insert("dq_items", {
            "item_id": "itmWS", "kind": "database", "name": "   ",
            "status": "sourcing", "module_tag": "", "target_variable": None,
            "use_case": None, "created_at": now, "updated_at": now,
        })
        s.init_schema()
        asset = s.query_one("dq_assets", asset_id="itmWS")
        self.assertEqual(asset["alias"], "asset")
        self.assertTrue(asset["display_name"].endswith("-asset"))
        self.assertNotIn(" ", asset["display_name"])

    def test_tied_delivery_seq_picks_a_deterministic_origin(self):
        s.init_schema()
        now = s.now_ist()
        for item_id in ("itmTieB", "itmTieA"):
            s.insert("dq_items", {
                "item_id": item_id, "kind": "dataset", "name": "Tied Family",
                "status": "complete", "module_tag": "", "target_variable": None,
                "use_case": None, "created_at": now, "updated_at": now,
            })
        # Force both rows to share delivery_seq=1 under the same family —
        # a shape register_delivery() itself would never produce, but the
        # backfill must still resolve to exactly one deterministic origin
        # rather than raising or picking randomly.
        s.update("dq_items", {"item_id": "itmTieA"}, {"dataset_family_id": "famTie", "delivery_seq": 1})
        s.update("dq_items", {"item_id": "itmTieB"}, {"dataset_family_id": "famTie", "delivery_seq": 1})

        s.init_schema()
        s.init_schema()  # run twice — the deterministic pick must also be stable across reruns

        assets = s.query("dq_assets", asset_id="famTie")
        self.assertEqual(len(assets), 1, "a tied delivery_seq must still yield exactly one asset")
        fresh_rows = [r for r in s.query("dq_items")
                     if r.get("dataset_family_id") == "famTie" and r.get("intent") == "fresh"]
        self.assertEqual(len(fresh_rows), 1, "exactly one origin ('fresh') must be picked, deterministically")
        self.assertEqual(fresh_rows[0]["item_id"], "itmTieA", "item_id is the documented tiebreaker")

    def test_null_family_row_is_backfilled_by_delivery_defaults_before_asset_backfill_runs(self):
        """A dq_items row with dataset_family_id IS NULL (as every row is
        before _backfill_delivery_defaults runs) must still end up with
        exactly one dq_assets row — proving the call order inside
        init_schema() (delivery defaults, THEN asset backfill) is correct."""
        s.init_schema()
        now = s.now_ist()
        s.insert("dq_items", {
            "item_id": "itmNull", "kind": "dataset", "name": "Orphan Family",
            "status": "sourcing", "module_tag": "", "target_variable": None,
            "use_case": None, "created_at": now, "updated_at": now,
        })
        self.assertIsNone(s.query_one("dq_items", item_id="itmNull")["dataset_family_id"])

        s.init_schema()

        row = s.query_one("dq_items", item_id="itmNull")
        self.assertIsNotNone(row["dataset_family_id"], "delivery backfill must run before asset backfill")
        asset = s.query_one("dq_assets", asset_id=row["dataset_family_id"])
        self.assertIsNotNone(asset, "the asset backfill must see the family the delivery backfill just gave it")


if __name__ == "__main__":
    unittest.main()
