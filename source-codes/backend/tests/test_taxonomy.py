"""RCA Stage 1 — taxonomy + tag propagation behavioral tests.

Runs against a throwaway SQLite file (never the developer's real
system_state.db), mirroring the pattern ci-local.ps1 uses for the backend
import-boot smoke. SYSTEM_DB_PATH must be set before `system_db` is first
imported anywhere in the process, since the module reads it at import time —
this file is written to be run standalone
(``python -m unittest tests.test_taxonomy``), which this repo's only
other test module (test_finalized_framework.py) never imports system_db, so
that ordering constraint holds for `python -m unittest discover` too.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-taxonomy.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
import taxonomy  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, TAXONOMY_DIMENSIONS, seed_platform, seed_taxonomy  # noqa: E402


class TaxonomySeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_seed_creates_all_four_dimensions(self):
        dims = {d["key"] for d in s.query("tag_dimensions", tenant_id=BOOTSTRAP_TENANT)}
        self.assertEqual(dims, {key for key, _label, _values in TAXONOMY_DIMENSIONS})

    def test_seed_creates_bootstrap_tenant_and_flags(self):
        self.assertIsNotNone(s.query_one("tenants", tenant_id=BOOTSTRAP_TENANT))
        flags = {f["key"]: f["enabled"] for f in s.query("feature_flags", tenant_id=BOOTSTRAP_TENANT)}
        self.assertEqual(flags.get("TAXONOMY_ENABLED"), 1)  # Stage 1 gate passed
        self.assertEqual(flags.get("KB_MODULE_ENABLED"), 1)  # Stage 2 gate passed
        self.assertEqual(flags.get("RCA_ENABLED"), 1)  # Stage 3 gate passed
        self.assertEqual(flags.get("RCA_LEGACY_CREATION_RETIRED"), 1)  # Stage 7 gate passed

    def test_migration_is_idempotent(self):
        before_dims = len(s.query("tag_dimensions"))
        before_values = len(s.query("tag_values"))
        s.init_schema()
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        self.assertEqual(len(s.query("tag_dimensions")), before_dims)
        self.assertEqual(len(s.query("tag_values")), before_values)
        # feature_flags must not be clobbered by a reseed after an operator
        # flip — flip to the OPPOSITE of the current value (not a hardcoded
        # 1/0) and restore the original afterwards, so this test doesn't go
        # stale again the next time a stage flips this flag's own default.
        flag_where = {"key": "RCA_ENABLED", "tenant_id": BOOTSTRAP_TENANT}
        original = s.query_one("feature_flags", **flag_where)["enabled"]
        flipped = 0 if original else 1
        s.update("feature_flags", flag_where, {"enabled": flipped})
        seed_platform()
        flag = s.query_one("feature_flags", **flag_where)
        self.assertEqual(flag["enabled"], flipped)
        s.update("feature_flags", flag_where, {"enabled": original})


class ProductDimensionCreTests(unittest.TestCase):
    """TAX-01 (0.5.0, A-Q05) — CRE (Commercial Real Estate) is added to the
    Product dimension only. The seeder upserts on natural-key ids, so this is
    purely additive: no migration, no taxonomy version bump, and it must
    survive a full wipe because wipe_all_items() re-runs
    seed_platform_and_taxonomy()."""

    @classmethod
    def setUpClass(cls):
        # This class calls the real wipe_all_items(), which also wipes
        # UPLOAD_DIR/KB_STORAGE_DIR on disk (system_db.py:1359-1360). Point
        # both at throwaway temp dirs BEFORE that call — this file's shared
        # sandboxing block above only sets SYSTEM_DB_PATH, so without this a
        # wipe here would delete the real backend/uploads and
        # backend/kb_storage content. Mirrors test_admin_reset.py's
        # convention. Restored in tearDownClass.
        cls._orig_upload_dir = os.environ.get("UPLOAD_DIR")
        cls._orig_kb_storage_dir = os.environ.get("KB_STORAGE_DIR")
        cls._tmp_upload_dir = Path(tempfile.gettempdir()) / "archimedes-test-taxonomy-uploads"
        cls._tmp_kb_storage_dir = Path(tempfile.gettempdir()) / "archimedes-test-taxonomy-kb-storage"
        os.environ["UPLOAD_DIR"] = str(cls._tmp_upload_dir)
        os.environ["KB_STORAGE_DIR"] = str(cls._tmp_kb_storage_dir)
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    @classmethod
    def tearDownClass(cls):
        import shutil
        if cls._orig_upload_dir is None:
            os.environ.pop("UPLOAD_DIR", None)
        else:
            os.environ["UPLOAD_DIR"] = cls._orig_upload_dir
        if cls._orig_kb_storage_dir is None:
            os.environ.pop("KB_STORAGE_DIR", None)
        else:
            os.environ["KB_STORAGE_DIR"] = cls._orig_kb_storage_dir
        shutil.rmtree(cls._tmp_upload_dir, ignore_errors=True)
        shutil.rmtree(cls._tmp_kb_storage_dir, ignore_errors=True)

    def _product_values(self):
        dim = s.query_one("tag_dimensions", tenant_id=BOOTSTRAP_TENANT, key="product")
        return s.query("tag_values", dimension_id=dim["dimension_id"])

    def _portfolio_values(self):
        dim = s.query_one("tag_dimensions", tenant_id=BOOTSTRAP_TENANT, key="portfolio")
        return s.query("tag_values", dimension_id=dim["dimension_id"])

    def test_product_dimension_has_nine_values_including_cre(self):
        values = self._product_values()
        self.assertEqual(len(values), 9)
        keys = {v["key"] for v in values}
        self.assertIn("cre", keys)
        label = next(v["label"] for v in values if v["key"] == "cre")
        self.assertEqual(label, "CRE")

    def test_portfolio_dimension_is_unchanged_at_four_values(self):
        values = self._portfolio_values()
        self.assertEqual(len(values), 4)
        self.assertNotIn("cre", {v["key"] for v in values})

    def test_seeding_three_times_still_yields_nine_product_values(self):
        seed_taxonomy()
        seed_taxonomy()
        seed_taxonomy()
        self.assertEqual(len(self._product_values()), 9)

    def test_cre_survives_a_full_wipe_and_reseed(self):
        # A full wipe clears tag_values wholesale, then immediately calls the
        # same seeders main.py's boot path runs (system_db.wipe_all_items()),
        # so the platform is restored — CRE included — without a restart.
        s.wipe_all_items()
        values = self._product_values()
        self.assertEqual(len(values), 9)
        self.assertIn("cre", {v["key"] for v in values})
        # Portfolio must come back at its original four values too — the
        # wipe's blanket tag_values clear is not scoped to Product alone.
        self.assertEqual(len(self._portfolio_values()), 4)

    def test_existing_tag_assignment_on_an_unrelated_value_still_resolves(self):
        """TAX-01 is purely additive: adding CRE must never renumber or
        re-key an existing value another object is already tagged with."""
        [created] = taxonomy.assign_tags(
            BOOTSTRAP_TENANT, "dq_item", "item_test_cre_check",
            ["product:mortgage"], "tester")
        seed_taxonomy()  # re-run the seeder, as TAX-01's addition does
        tags = taxonomy.get_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_cre_check")
        self.assertEqual({t["value_key"] for t in tags}, {"mortgage"})
        self.assertTrue(s.query_one("tag_assignments", assignment_id=created["assignment_id"]))


class TaxonomyPropagationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_assign_tags_and_get_tags(self):
        created = taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_a",
                                       ["risk_type:credit", "portfolio:retail"], "tester")
        self.assertEqual(len(created), 2)
        tags = taxonomy.get_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_a")
        self.assertEqual({t["value_key"] for t in tags}, {"credit", "retail"})
        self.assertTrue(all(t["origin"] == "user_added" for t in tags))

    def test_assign_tags_is_idempotent_no_duplicates(self):
        taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_b", ["risk_type:market"], "tester")
        taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_b", ["risk_type:market"], "tester")
        tags = taxonomy.get_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_b")
        self.assertEqual(len(tags), 1)

    def test_assign_unknown_value_rejected(self):
        with self.assertRaises(ValueError):
            taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_c", ["risk_type:not_a_real_value"], "tester")

    def test_remove_tag_requires_reason(self):
        [created] = taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_d", ["risk_type:liquidity"], "tester")
        with self.assertRaises(ValueError):
            taxonomy.remove_tag(BOOTSTRAP_TENANT, created["assignment_id"], "", "tester")
        taxonomy.remove_tag(BOOTSTRAP_TENANT, created["assignment_id"], "wrong classification", "tester")
        self.assertEqual(taxonomy.get_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_d"), [])

    def test_remove_tag_writes_audit_event(self):
        [created] = taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_e", ["risk_type:operational"], "tester")
        before = len(s.query("transaction_log", event="tag_remove"))
        taxonomy.remove_tag(BOOTSTRAP_TENANT, created["assignment_id"], "test removal", "tester")
        after = len(s.query("transaction_log", event="tag_remove"))
        self.assertEqual(after, before + 1)

    def test_inherit_tags_propagates_active_tags(self):
        taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_f",
                             ["risk_type:credit", "use_case:ifrs9"], "tester")
        inherited = taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_f",
                                          "plan_v2", "plan_row_f1", "system")
        self.assertEqual(len(inherited), 2)
        plan_tags = taxonomy.get_tags(BOOTSTRAP_TENANT, "plan_v2", "plan_row_f1")
        self.assertEqual({t["value_key"] for t in plan_tags}, {"credit", "ifrs9"})
        self.assertTrue(all(t["origin"] == "inherited" for t in plan_tags))

    def test_inherit_tags_is_idempotent(self):
        taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_g", ["risk_type:market"], "tester")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_g", "plan_v2", "plan_row_g1", "system")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_g", "plan_v2", "plan_row_g1", "system")
        self.assertEqual(len(taxonomy.get_tags(BOOTSTRAP_TENANT, "plan_v2", "plan_row_g1")), 1)

    def test_downstream_snapshot_survives_source_tag_removal(self):
        """The core immutability invariant (contracts.md §7): removing a tag
        from the source after it has propagated must NOT retroactively change
        an already-taken downstream snapshot."""
        [created] = taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_h", ["risk_type:credit"], "tester")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_h", "plan_v2", "plan_row_h1", "system")
        taxonomy.remove_tag(BOOTSTRAP_TENANT, created["assignment_id"], "reclassified", "tester")
        self.assertEqual(taxonomy.get_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_h"), [])
        downstream = taxonomy.get_tags(BOOTSTRAP_TENANT, "plan_v2", "plan_row_h1")
        self.assertEqual({t["value_key"] for t in downstream}, {"credit"})

    def test_full_propagation_chain_item_to_issue(self):
        """dq_item -> plan_v2 -> results_v2 -> issues_v2, exactly the chain
        wired into ai/v2/service.py and ai/v2/issues.py."""
        taxonomy.assign_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_i", ["portfolio:sovereign"], "tester")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_i", "plan_v2", "plan_row_i1", "system")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "plan_v2", "plan_row_i1", "results_v2", "result_i1", "system")
        taxonomy.inherit_tags(BOOTSTRAP_TENANT, "dq_item", "item_test_i", "issues_v2", "issue_i1", "system")
        for object_type, object_id in (("plan_v2", "plan_row_i1"), ("results_v2", "result_i1"), ("issues_v2", "issue_i1")):
            tags = taxonomy.get_tags(BOOTSTRAP_TENANT, object_type, object_id)
            self.assertEqual({t["value_key"] for t in tags}, {"sovereign"}, object_type)


if __name__ == "__main__":
    unittest.main()
