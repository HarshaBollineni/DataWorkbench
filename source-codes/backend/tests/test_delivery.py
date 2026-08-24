"""Phase 3 — PLT-02 delivery/family seam behavioral tests (3-T8).

An existing dq_items row (created before this migration ever ran) is
backfilled into a single-item family of its own; a later item joining an
existing family gets the next delivery_seq without disturbing any
existing member's row; the backfill/migration is idempotent.

Standalone-runnable (``python -m unittest tests.test_delivery``); same
SYSTEM_DB_PATH-before-first-import sandboxing convention as
test_admin_reset.py / test_taxonomy.py.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-delivery.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from dq_diagnostics import delivery  # noqa: E402


def _make_item(item_id: str) -> None:
    now = s.now_ist()
    s.insert("dq_items", {
        "item_id": item_id, "kind": "database", "name": item_id, "status": "finalized",
        "module_tag": "", "target_variable": "", "use_case": "",
        "created_at": now, "updated_at": now,
    })


class MigrationBackfillTests(unittest.TestCase):
    """A dq_items row created with no dataset_family_id yet (as every
    pre-Phase-3 row necessarily was) is backfilled to a single-item family
    of its own by system_db.init_schema() — behaviourally unchanged from
    before register_delivery() existed."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_existing_row_backfilled_to_family_of_one_at_seq_one(self):
        item_id = f"itm_backfill_{uuid.uuid4().hex[:8]}"
        _make_item(item_id)
        row_before = s.query_one("dq_items", item_id=item_id)
        self.assertIsNone(row_before["dataset_family_id"], "a freshly-inserted row starts unbackfilled")

        s.init_schema()  # runs _backfill_delivery_defaults

        row_after = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row_after["dataset_family_id"], item_id)
        self.assertEqual(row_after["delivery_seq"], 1)

    def test_migration_backfill_is_idempotent(self):
        item_id = f"itm_backfill2_{uuid.uuid4().hex[:8]}"
        _make_item(item_id)
        s.init_schema()
        first = s.query_one("dq_items", item_id=item_id)
        s.init_schema()
        s.init_schema()
        second = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(first["dataset_family_id"], second["dataset_family_id"])
        self.assertEqual(first["delivery_seq"], second["delivery_seq"])

    def test_backfill_never_touches_a_row_already_joined_to_a_real_family(self):
        """If register_delivery() already gave a row a family, re-running
        init_schema()'s backfill must not clobber it back to a
        single-item family of its own."""
        item_a = f"itm_real_a_{uuid.uuid4().hex[:8]}"
        item_b = f"itm_real_b_{uuid.uuid4().hex[:8]}"
        family = f"fam_real_{uuid.uuid4().hex[:8]}"
        _make_item(item_a)
        _make_item(item_b)
        delivery.register_delivery(item_a, family)
        delivery.register_delivery(item_b, family)

        s.init_schema()  # must be a no-op for already-backfilled rows

        row_a = s.query_one("dq_items", item_id=item_a)
        row_b = s.query_one("dq_items", item_id=item_b)
        self.assertEqual(row_a["dataset_family_id"], family)
        self.assertEqual(row_b["dataset_family_id"], family)
        self.assertEqual({row_a["delivery_seq"], row_b["delivery_seq"]}, {1, 2})

    def test_ensure_delivery_defaults_agrees_with_the_migration_path(self):
        item_id = f"itm_ensure_{uuid.uuid4().hex[:8]}"
        _make_item(item_id)
        updated = delivery.ensure_delivery_defaults()
        self.assertGreaterEqual(updated, 1)
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["dataset_family_id"], item_id)
        self.assertEqual(row["delivery_seq"], 1)
        # Idempotent: a second call updates nothing further for this row.
        s.query_one("dq_items", item_id=item_id)  # sanity re-read
        before_second = len(s.query("dq_items"))
        second_updated = delivery.ensure_delivery_defaults()
        self.assertEqual(second_updated, 0)
        self.assertEqual(len(s.query("dq_items")), before_second)


class RegisterDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_second_item_joins_family_at_seq_two_without_mutating_the_first(self):
        family = f"fam_{uuid.uuid4().hex[:8]}"
        item_a = f"itm_a_{uuid.uuid4().hex[:8]}"
        item_b = f"itm_b_{uuid.uuid4().hex[:8]}"
        _make_item(item_a)
        _make_item(item_b)

        result_a = delivery.register_delivery(item_a, family)
        self.assertEqual(result_a["delivery_seq"], 1)
        row_a_before = s.query_one("dq_items", item_id=item_a)

        result_b = delivery.register_delivery(item_b, family)
        self.assertEqual(result_b["delivery_seq"], 2)

        row_a_after = s.query_one("dq_items", item_id=item_a)
        self.assertEqual(row_a_before, row_a_after,
                        "a second item joining the family must not mutate the first item's row")

        members = delivery.family_deliveries(family)
        self.assertEqual([(m["item_id"], m["delivery_seq"]) for m in members],
                         [(item_a, 1), (item_b, 2)])

    def test_as_of_date_persists(self):
        family = f"fam_asof_{uuid.uuid4().hex[:8]}"
        item_id = f"itm_asof_{uuid.uuid4().hex[:8]}"
        _make_item(item_id)
        result = delivery.register_delivery(item_id, family, as_of_date="2026-07-30")
        self.assertEqual(result["as_of_date"], "2026-07-30")
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["as_of_date"], "2026-07-30")

    def test_a_third_item_gets_the_next_seq(self):
        family = f"fam_seq_{uuid.uuid4().hex[:8]}"
        items = [f"itm_seq{i}_{uuid.uuid4().hex[:6]}" for i in range(3)]
        for item_id in items:
            _make_item(item_id)
        delivery.register_delivery(items[0], family)
        delivery.register_delivery(items[1], family)
        result_3 = delivery.register_delivery(items[2], family)
        self.assertEqual(result_3["delivery_seq"], 3)


if __name__ == "__main__":
    unittest.main()
