"""The framework count gate — rebuilt against the 0.4.0 register (rule 7 / C-19).

Until Phase 3 this file asserted the retired 14-test Galileo framework
(``len(TEST_NAMES) == 14`` plus per-test reference values for MCAR / leakage
AUC / MNAR / global rules). Those tests retired with their framework
(FWK-13, docs/0.4.0/01-disposition.md); the reference-value pins went with
the implementations they pinned. This rewrite asserts the replacement at the
same gate strength: the seeded register data itself (source of truth:
``knowledge_base/dq_framework_data.json``, per docs/0.4.0/00-framework.md)
and the retirement of the old registry. DB-level behaviour is covered by
``tests/test_register.py``; the standalone gate is ``verify_plan8.py``.
"""
import json
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
FRAMEWORK_JSON = BACKEND / "knowledge_base" / "dq_framework_data.json"

CORE_IDS = [2, 4, 6, 8, 11, 12, 14, 17, 20]
DEFER_IDS = [1, 3, 5, 7, 9, 10, 13, 15, 16, 18, 19]


class FinalizedFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FRAMEWORK_JSON.read_text(encoding="utf-8"))

    def test_finalized_framework_has_exactly_nine_diagnostics(self):
        register = self.data["register"]
        self.assertEqual(len(register), 9)
        self.assertEqual(sorted(r["diagnostic_id"] for r in register), CORE_IDS)

    def test_accepted_backends_are_the_only_executable_diagnostics(self):
        executable = [r for r in self.data["register"]
                      if r["workflow_status"] == "executable"]
        self.assertEqual([r["diagnostic_id"] for r in executable], [2, 6, 8, 11, 14])
        self.assertTrue(all(row.get("enabled_by") for row in executable),
                        "FWK-18: every executable flip must carry its decision")
        pending = [r for r in self.data["register"]
                   if r["workflow_status"] == "workflow_pending"]
        self.assertEqual(len(pending), 4)

    def test_no_defer_row_is_registered(self):
        ids = {r["diagnostic_id"] for r in self.data["register"]}
        self.assertFalse(ids & set(DEFER_IDS),
                         "D-17: S8 defer rows are documentation, not product scope")

    def test_taxonomy_is_six_themes_eleven_areas_six_test_areas(self):
        self.assertEqual(len(self.data["l1_themes"]), 6)
        self.assertEqual(len(self.data["l2_areas"]), 11)
        self.assertEqual(len(self.data["test_areas"]), 6)
        self.assertEqual({t["l1_theme"] for t in self.data["l2_areas"]},
                         set(self.data["l1_themes"]))

    def test_coverage_never_rounds_up(self):
        cov = {c["l2_id"]: c for c in self.data["coverage"]}
        self.assertEqual(len(cov), 11)
        gaps = {k for k, c in cov.items() if c["status"] == "gap"}
        self.assertEqual(gaps, {"L2-03", "L2-05", "L2-08", "L2-09", "L2-11"})
        self.assertFalse([c for c in cov.values() if c["status"] == "covered"],
                         "FWK-15: nothing qualifies as fully covered in 0.4.0")
        for c in cov.values():
            if c["status"] == "gap":
                self.assertTrue(c.get("reason"))

    def test_old_registry_is_retired_and_refuses_every_name(self):
        from dq_tests import param_specs, registry
        self.assertEqual(registry.TEST_NAMES, [])
        self.assertEqual(registry.TEST_REGISTRY, {})
        self.assertEqual(param_specs.PARAM_SPECS, {})
        with self.assertRaises(KeyError):
            registry.run_registered("PSI (Population Stability Index)", None)
        with self.assertRaises(KeyError):
            registry.run_registered("never-existed", None)


if __name__ == "__main__":
    unittest.main()
