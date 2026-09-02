"""Phase 1B Slice 2 missingness capability and review hand-off."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

_TMP_ROOT = Path(tempfile.gettempdir()) / "archimedes-test-missingness-capability"
if _TMP_ROOT.exists():
    shutil.rmtree(_TMP_ROOT)
_TMP_ROOT.mkdir(parents=True)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_ROOT / "state.db")
os.environ["ANALYSIS_ARTIFACT_DIR"] = str(_TMP_ROOT / "artifacts")
os.environ["ITEM_DB_DIR"] = str(_TMP_ROOT / "item-dbs")
os.environ["UPLOAD_DIR"] = str(_TMP_ROOT / "uploads")
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from ai.v2 import service  # noqa: E402
from analysis_runtime import runs  # noqa: E402
from domains.aar.repository import AnalysisArtifactRepository  # noqa: E402
from supporting_analyses import ensure_registered  # noqa: E402
from routers.analyses import (  # noqa: E402
    AnalysisManifestIn,
    analysis_catalog,
    create_analysis_manifest,
    run_analysis_manifest,
)


def _snapshot(frame: pd.DataFrame) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    asset_id = f"asset_{suffix}"
    snapshot_id = f"item_{suffix}"
    now = s.now_ist()
    s.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"missing-{suffix}", "display_name": f"DS-{suffix}",
        "kind": "dataset", "time_basis": "none", "current_version_no": 1,
        "lifecycle_status": "sourcing", "created_at": now, "updated_at": now,
    })
    s.insert("dq_items", {
        "item_id": snapshot_id, "kind": "dataset", "name": f"DS-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": snapshot_id,
        "intent": "fresh", "ingest_status": "ready",
    })
    service._write_table(snapshot_id, "portfolio", frame)
    classifications = {
        "segment": ("categorical", "Feature"),
        "gap_a": ("numerical", "Mandatory"),
        "gap_b": ("numerical", "Feature"),
        "zero_code": ("numerical", "Feature"),
    }
    for column in frame.columns:
        classification, role = classifications.get(column, ("numerical", "Feature"))
        s.upsert("variable_inventory", {
            "item_id": snapshot_id, "table_name": "portfolio", "column_name": column,
            "classification": classification, "data_type": str(frame[column].dtype),
            "description": f"Definition for {column}", "discrepancies": [],
            "notes": "", "role": role, "profile_json": {}, "provisional": 0,
            "updated_at": now,
        })
    return asset_id, snapshot_id


def _pattern_frame() -> pd.DataFrame:
    n = 240
    segment = np.array(["A"] * 120 + ["B"] * 120)
    gap = np.arange(n, dtype=float)
    gap[segment == "B"] = np.nan
    return pd.DataFrame({
        "segment": segment,
        "gap_a": gap,
        "gap_b": gap.copy(),
        "zero_code": np.where(np.arange(n) % 3 == 0, 0, np.arange(n) + 1),
    })


class MissingnessCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        ensure_registered()

    def _run(self, snapshot_id: str, **overrides):
        context = {"table": "portfolio", "columns": [], "population": {},
                   "parameters": {}, "period_column": None, "grain": None,
                   "analysis_context": None, "missing_value_codes": {}}
        context.update(overrides)
        manifest = runs.create_manifest(snapshot_id, "missingness_explanation", context)
        return runs.execute(manifest["run"]["run_id"])

    def test_report_preserves_block_exclusion_and_mcar_claim_boundary(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        outcome = self._run(snapshot_id)
        self.assertEqual(outcome["run"]["status"], "action_required")
        artifact_id = outcome["result"]["artifact"]["artifact_id"]
        _, report = AnalysisArtifactRepository().get(artifact_id)
        gap_a = next(row for row in report["columns"] if row["column"] == "gap_a")
        exclusions = gap_a["predictor_screening"]["excluded"]
        self.assertTrue(any(row["column"] == "gap_b" and
                            row["reason"] == "target co-missingness block mate"
                            for row in exclusions))
        self.assertIn("not proof of MCAR", report["preamble"]["mcar_caveat"])
        self.assertEqual(len(s.query("issues_v2", item_id=snapshot_id)), 0,
                         "supporting observations must never auto-open issues")

    def test_zero_is_valid_until_explicitly_confirmed_as_missing_code(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        ordinary = self._run(snapshot_id)
        ordinary_zero = next(row for row in ordinary["result"]["observations"]
                             if row["column"] == "zero_code")
        self.assertEqual(ordinary_zero["missing_count"], 0)

        confirmed = self._run(snapshot_id, missing_value_codes={"zero_code": [0]})
        confirmed_zero = next(row for row in confirmed["result"]["observations"]
                              if row["column"] == "zero_code")
        self.assertGreater(confirmed_zero["missing_count"], 0)

    def test_confirmed_data_sourcing_codes_are_frozen_automatically(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        s.update("variable_inventory", {
            "item_id": snapshot_id, "table_name": "portfolio", "column_name": "zero_code",
        }, {"missing_value_codes_json": [0], "missing_codes_confirmed": 1})
        outcome = self._run(snapshot_id)
        zero = next(row for row in outcome["result"]["observations"]
                    if row["column"] == "zero_code")
        self.assertGreater(zero["missing_count"], 0)
        self.assertEqual(outcome["manifest"]["methodology"]["missing_value_codes"],
                         {"zero_code": [0]})

    def test_changed_default_tolerance_applies_to_unmarked_feature_columns(self):
        frame = pd.DataFrame({
            "ordinary_feature": [np.nan] * 8 + list(range(192)),
            "independent_feature": ["A", "B"] * 100,
        })
        _, snapshot_id = _snapshot(frame)

        at_five = self._run(snapshot_id, parameters={"tolerance_default": 0.05})
        five_observation = next(row for row in at_five["result"]["observations"]
                                if row["column"] == "ordinary_feature")
        self.assertEqual(five_observation["tolerance"], 0.05)
        self.assertFalse(five_observation["exceeds_tolerance"])

        at_three = self._run(snapshot_id, parameters={"tolerance_default": 0.03})
        three_observation = next(row for row in at_three["result"]["observations"]
                                 if row["column"] == "ordinary_feature")
        self.assertEqual(three_observation["tolerance"], 0.03)
        self.assertTrue(three_observation["exceeds_tolerance"])
        self.assertEqual(three_observation["review_state"], "open")

        resolved_roles = at_three["manifest"]["methodology"]["parameters"]["role_tolerances"]
        self.assertEqual(resolved_roles["unmarked"], 0.03)

    def test_explicit_unmarked_tolerance_still_overrides_default(self):
        frame = pd.DataFrame({
            "ordinary_feature": [np.nan] * 8 + list(range(192)),
            "independent_feature": ["A", "B"] * 100,
        })
        _, snapshot_id = _snapshot(frame)
        outcome = self._run(snapshot_id, parameters={
            "tolerance_default": 0.03,
            "role_tolerances": {"unmarked": 0.05},
        })
        observation = next(row for row in outcome["result"]["observations"]
                           if row["column"] == "ordinary_feature")
        self.assertEqual(observation["tolerance"], 0.05)
        self.assertFalse(observation["exceeds_tolerance"])

    def test_tree_depth_is_frozen_and_used_by_the_engine(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        outcome = self._run(snapshot_id, parameters={"tree_depth": 5})
        self.assertEqual(outcome["manifest"]["methodology"]["parameters"]["tree_depth"], 5)
        self.assertEqual(outcome["manifest"]["methodology"]["resolved_parameters"]["tree_depth"], 5)
        artifact_id = outcome["result"]["artifact"]["artifact_id"]
        _, report = AnalysisArtifactRepository().get(artifact_id)
        self.assertEqual(report["preamble"]["tree_depth"], 5)

    def test_exact_manifest_reuses_artifact_and_parameter_change_does_not(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        first = self._run(snapshot_id)
        second = self._run(snapshot_id)
        changed = self._run(snapshot_id, parameters={"tree_depth": 2})
        first_id = first["result"]["artifact"]["artifact_id"]
        self.assertEqual(second["result"]["artifact"]["artifact_id"], first_id)
        self.assertNotEqual(changed["result"]["artifact"]["artifact_id"], first_id)

    def test_untestable_remains_distinct_from_no_pattern(self):
        frame = pd.DataFrame({"gap_a": [np.nan] * 60 + list(range(60)),
                              "gap_b": [np.nan] * 60 + list(range(60))})
        _, snapshot_id = _snapshot(frame)
        outcome = self._run(snapshot_id)
        subtypes = {row["column"]: row["subtype"] for row in outcome["result"]["observations"]}
        self.assertEqual(subtypes["gap_a"], "untestable")
        self.assertEqual(subtypes["gap_b"], "untestable")

    def test_explicit_confirmation_creates_one_issue_and_is_idempotent(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        outcome = self._run(snapshot_id)
        observation = next(row for row in outcome["result"]["observations"]
                           if row["column"] == "gap_a")
        first = runs.dispose_observation(observation["observation_id"], "confirm_issue")
        second = runs.dispose_observation(observation["observation_id"], "confirm_issue")
        self.assertEqual(first["issue_row_id"], second["issue_row_id"])
        self.assertEqual(len(s.query("issues_v2", item_id=snapshot_id)), 1)
        issue = s.query_one("issues_v2", issue_row_id=first["issue_row_id"])
        self.assertIsNone(issue["diagnostic_id"])
        self.assertEqual(issue["area_id"], "L2-05")
        self.assertEqual(issue["threshold_json"]["tree_depth"], 3)
        self.assertEqual(issue["column_details_json"][0]["analysis_run_id"],
                         outcome["run"]["run_id"])
        self.assertEqual(len(s.query("analysis_observation_dispositions",
                                     observation_id=observation["observation_id"])), 2)

        # A prior restart bug could delete the issue while leaving the
        # confirmed observation. Repair recreates the same externally-visible
        # issue id and preserves the frozen analysis context.
        s.delete("issues_v2", issue_row_id=first["issue_row_id"])
        repaired = runs.repair_confirmed_observation_issues()
        self.assertEqual(repaired["repaired"], 1)
        restored = s.query_one("issues_v2", issue_row_id=first["issue_row_id"])
        self.assertEqual(restored["threshold_json"]["tree_depth"], 3)

    def test_invalid_population_and_parameters_are_action_required(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        with self.assertRaisesRegex(ValueError, "Population filters"):
            self._run(snapshot_id, population={"country": "GB"})
        with self.assertRaisesRegex(ValueError, "tree_depth"):
            self._run(snapshot_id, parameters={"tree_depth": 9})

    def test_http_adapter_contract_drives_the_same_vertical_slice(self):
        _, snapshot_id = _snapshot(_pattern_frame())
        catalog = analysis_catalog(snapshot_id)
        capability = next(row for row in catalog["capabilities"]
                          if row["capability_id"] == "missingness_explanation")
        self.assertEqual(capability["decision_type"], "supporting")
        created = create_analysis_manifest(snapshot_id, AnalysisManifestIn(
            capability_id="missingness_explanation", table="portfolio",
        ))
        completed = run_analysis_manifest(created["run"]["run_id"])
        self.assertIn(completed["run"]["status"], {"complete", "action_required"})
        self.assertEqual(completed["result"]["capability_id"], "missingness_explanation")


if __name__ == "__main__":
    unittest.main()
