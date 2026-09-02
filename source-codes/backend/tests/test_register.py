"""Phase 3 — diagnostic register + framework taxonomy behavioral tests
(3-T1, 3-T2, 3-T10, FWK-07, FWK-18).

Standalone-runnable (``python -m unittest tests.test_register``); follows
the same SYSTEM_DB_PATH-before-first-import sandboxing convention as
test_admin_reset.py / test_taxonomy.py.
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-register.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from dq_diagnostics import register  # noqa: E402
from dq_diagnostics.result import DECISION_TYPES  # noqa: E402

EXPECTED_IDS = {2, 4, 6, 8, 11, 12, 14, 17, 20}
GAP_AREAS = {"L2-03", "L2-05", "L2-08", "L2-09", "L2-11"}


class RegisterSeedTests(unittest.TestCase):
    """3-T1 — the register seeds exactly the 9-row / 11-area / 6-theme /
    6-test-area shape docs/0.4.0/00-framework.md specifies."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_register_has_exactly_nine_rows_with_the_right_ids(self):
        rows = register.list_register()
        self.assertEqual(len(rows), 9)
        self.assertEqual({r["diagnostic_id"] for r in rows}, EXPECTED_IDS)

    def test_exactly_four_executable_rows(self):
        rows = register.list_register()
        executable = [r for r in rows if r["workflow_status"] == "executable"]
        self.assertEqual([r["diagnostic_id"] for r in executable], [2, 6, 11, 14])
        pending = [r for r in rows if r["workflow_status"] != "executable"]
        self.assertEqual(len(pending), 5)
        self.assertTrue(all(r["workflow_status"] == "workflow_pending" for r in pending))

    def test_taxonomy_counts_six_themes_eleven_areas_six_test_areas(self):
        areas = s.query("framework_taxonomy")
        self.assertEqual(len(areas), 11)
        self.assertEqual(len({a["l1_theme"] for a in areas}), 6)
        test_areas = s.query("framework_test_areas")
        self.assertEqual(len(test_areas), 6)
        self.assertEqual({a["area_id"] for a in test_areas}, {"T1", "T2", "T3", "T4", "T5", "T6"})

    def test_seed_register_is_idempotent(self):
        before_register = register.list_register()
        before_taxonomy = s.query("framework_taxonomy", order_by="l2_id")
        before_test_areas = s.query("framework_test_areas", order_by="area_id")
        register.seed_register()
        register.seed_register()
        after_register = register.list_register()
        after_taxonomy = s.query("framework_taxonomy", order_by="l2_id")
        after_test_areas = s.query("framework_test_areas", order_by="area_id")
        self.assertEqual(len(after_register), len(before_register))
        self.assertEqual(
            [{k: v for k, v in r.items() if k != "updated_at"} for r in before_register],
            [{k: v for k, v in r.items() if k != "updated_at"} for r in after_register],
        )
        self.assertEqual(before_taxonomy, after_taxonomy)
        self.assertEqual(before_test_areas, after_test_areas)
        # Re-seeding must never duplicate a default threshold row.
        defaults = s.query("threshold_settings", scope="default")
        self.assertEqual(len(defaults), len({(d["diagnostic_id"], d["key"]) for d in defaults}))

    def test_fwk18_enabled_by_only_set_for_executable_rows(self):
        rows = {r["diagnostic_id"]: r for r in register.list_register()}
        for diag_id in (2, 6, 11, 14):
            self.assertIsNotNone(rows[diag_id]["enabled_by"])
            self.assertNotEqual(rows[diag_id]["enabled_by"], "")
        for diag_id in EXPECTED_IDS - {2, 6, 11, 14}:
            self.assertIsNone(rows[diag_id]["enabled_by"], f"diagnostic {diag_id} must have enabled_by = null")

    def test_fwk07_every_register_row_has_a_valid_decision_type(self):
        for row in register.list_register():
            self.assertIn(row["decision_type"], DECISION_TYPES,
                          f"diagnostic {row['diagnostic_id']} has invalid decision_type {row['decision_type']!r}")


class RequireExecutableTests(unittest.TestCase):
    """3-T2 — require_executable()/get_diagnostic() refuse honestly and
    identically for pending vs unknown ids."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_require_executable_on_pending_diagnostic_raises_workflow_pending(self):
        for diagnostic_id in (4, 8):
            with self.subTest(diagnostic_id=diagnostic_id):
                with self.assertRaises(register.WorkflowPendingError) as ctx:
                    register.require_executable(diagnostic_id)
                self.assertEqual(str(ctx.exception), "workflow not yet defined")
                self.assertEqual(str(ctx.exception), register.REFUSAL_WORKFLOW_PENDING)

    def test_require_executable_on_the_executable_diagnostic_succeeds(self):
        self.assertEqual(register.require_executable(2)["diagnostic_id"], 2)
        self.assertEqual(register.require_executable(6)["diagnostic_id"], 6)
        self.assertEqual(register.require_executable(14)["diagnostic_id"], 14)
        self.assertEqual(register.require_executable(11)["diagnostic_id"], 11)

    def test_unknown_defer_row_id_and_pure_nonsense_id_are_indistinguishable(self):
        """get_diagnostic(3) — a real S8 defer-row id, never registered —
        and get_diagnostic(999) — pure nonsense — must raise the SAME
        exception type with the SAME message shape (FWK-05/D-17)."""
        with self.assertRaises(KeyError) as ctx_defer:
            register.get_diagnostic(3)
        with self.assertRaises(KeyError) as ctx_nonsense:
            register.get_diagnostic(999)
        self.assertIs(type(ctx_defer.exception), type(ctx_nonsense.exception))
        msg_defer = re.sub(r"\d+", "<id>", str(ctx_defer.exception))
        msg_nonsense = re.sub(r"\d+", "<id>", str(ctx_nonsense.exception))
        self.assertEqual(msg_defer, msg_nonsense)
        self.assertIn("unknown diagnostic", msg_defer)

    def test_require_executable_on_unknown_id_raises_keyerror_not_workflow_pending(self):
        with self.assertRaises(KeyError):
            register.require_executable(999)


class CoverageMapTests(unittest.TestCase):
    """3-T10 — coverage semantics never round up: exactly five gap areas
    (each with a non-empty reason), six covered_thin, nothing 'covered'."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_coverage_map_has_eleven_areas(self):
        cov = register.coverage_map()
        self.assertEqual(len(cov["areas"]), 11)
        self.assertEqual({a["l2_id"] for a in cov["areas"]},
                         {f"L2-{i:02d}" for i in range(1, 12)})

    def test_five_gap_areas_with_nonempty_reasons(self):
        cov = register.coverage_map()
        gap = {a["l2_id"]: a for a in cov["areas"] if a["status"] == "gap"}
        self.assertEqual(set(gap), GAP_AREAS)
        for l2_id, area in gap.items():
            self.assertTrue(area["reason"], f"{l2_id} gap area must have a non-empty reason")
            self.assertEqual(area["diagnostics"], [])

    def test_six_covered_thin_areas(self):
        cov = register.coverage_map()
        covered_thin = [a for a in cov["areas"] if a["status"] == "covered_thin"]
        self.assertEqual(len(covered_thin), 6)
        self.assertEqual({a["l2_id"] for a in covered_thin},
                         {"L2-01", "L2-02", "L2-04", "L2-06", "L2-07", "L2-10"})
        for area in covered_thin:
            self.assertTrue(area["diagnostics"], f"{area['l2_id']} covered_thin area must list its diagnostics")

    def test_nothing_rounds_up_to_covered_or_partial(self):
        cov = register.coverage_map()
        statuses = {a["status"] for a in cov["areas"]}
        self.assertEqual(statuses, {"gap", "covered_thin"})

    def test_coverage_map_splits_register_executable_vs_pending(self):
        cov = register.coverage_map()
        self.assertEqual(cov["executable"], [2, 6, 11, 14])
        self.assertEqual(set(cov["workflow_pending"]), EXPECTED_IDS - {2, 6, 11, 14})

    def test_fwk16_scoring_weights_re_derived_with_coverage_honesty(self):
        """3-T10 / FWK-16: weights are re-derived for the 9-row register
        (never the retired 14-test WEIGHTS/fw_family_weights scheme), the
        coverage-honesty statement is encoded (a score must say what it
        covers), and the delta against the old scheme is explained — a
        fixture score movement is a reviewed decision, never a surprise.
        """
        sw = register.scoring_weights()
        self.assertIn("coverage_statement", sw)
        self.assertIn("workflow-pending", sw["coverage_statement"])
        self.assertIn("health score", sw["coverage_statement"])
        self.assertIn("superseded_scheme_note", sw)
        self.assertIn("fw_family_weights", sw["superseded_scheme_note"])

        # Fixture before/after (the "record a fixture score" instruction):
        # the retired roll-up (scoring/categories.roll_up_results) scored a
        # 3-result fixture as two averaged units at passed_fraction 0.5/1.0
        # with no coverage statement at all — presentable as ~75% "health"
        # while actually covering 0 of the new register's diagnostics.
        from scoring.categories import roll_up_results
        before_units = roll_up_results([
            {"row_id": "r1", "table_name": "t", "scope": "framework",
             "test_name": "Completeness", "status": "pass"},
            {"row_id": "r1", "table_name": "t", "scope": "framework",
             "test_name": "Completeness", "status": "fail"},
            {"row_id": "r2", "table_name": "t", "scope": "framework",
             "test_name": "MCAR", "status": "pass"},
        ])
        before_fraction = sum(u["passed_fraction"] for u in before_units) / len(before_units)
        # The new scheme covering the same slice-1 reality: 1 of 9 register
        # rows executable after D-22, none yet run — the honest statement is "no score
        # displayed", not a number derived from an incomplete run.
        cov = register.coverage_map()
        after_covered_count = len(cov["executable"])
        after_total = len(cov["executable"]) + len(cov["workflow_pending"])
        self.assertAlmostEqual(before_fraction, 0.75, places=2)
        self.assertEqual((after_covered_count, after_total), (4, 9))
        self.assertNotEqual(
            before_fraction, after_covered_count / after_total,
            "the delta must be explained, not coincidentally identical",
        )


if __name__ == "__main__":
    unittest.main()
