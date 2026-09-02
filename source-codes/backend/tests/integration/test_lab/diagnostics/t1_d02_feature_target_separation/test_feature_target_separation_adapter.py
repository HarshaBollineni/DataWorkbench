"""Phase 1B Slice 4 foundation for diagnostic #2.

These tests prove the DataWorkbench adapter and enabled diagnostic workflow
bind target/feature metadata, preserve partial outcomes, persist reusable
artifacts, and require explicit review before issue creation.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import pandas as pd

_TMP_ROOT = Path(tempfile.gettempdir()) / "archimedes-test-feature-target-separation"
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
from domains.aar.repository import AnalysisArtifactRepository  # noqa: E402
from dq_diagnostics.engines.feature_target_separation.adapter import assess_snapshot  # noqa: E402
from dq_diagnostics import manifest_feature_target  # noqa: E402
from dq_diagnostics import runner_feature_target  # noqa: E402
from dq_diagnostics.register import require_executable, seed_register  # noqa: E402
from routers import v2  # noqa: E402


def _snapshot(frame: pd.DataFrame, *, target: str = "target") -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    asset_id = f"asset_{suffix}"
    snapshot_id = f"item_{suffix}"
    now = s.now_ist()
    s.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"fts-{suffix}", "display_name": f"FTS-{suffix}",
        "kind": "dataset", "time_basis": "period", "current_version_no": 1,
        "lifecycle_status": "active", "created_at": now, "updated_at": now,
        "target_variable": target, "use_case": "Model development", "product": "Loans",
    })
    s.insert("dq_items", {
        "item_id": snapshot_id, "kind": "dataset", "name": f"FTS-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": snapshot_id,
        "intent": "fresh", "ingest_status": "ready", "target_variable": target,
        "use_case": "Model development", "period_column": "period",
    })
    service._write_table(snapshot_id, "portfolio", frame)
    roles = {
        "target": "Target",
        "period": "Period",
        "customer_id": "Identifier",
        "constant": "Feature",
        "perfect": "Feature",
        "noisy": "Feature",
        "ignored": "Ignore",
    }
    for column in frame.columns:
        distinct = int(frame[column].nunique(dropna=True))
        s.upsert("variable_inventory", {
            "item_id": snapshot_id, "table_name": "portfolio", "column_name": column,
            "classification": "numerical", "data_type": str(frame[column].dtype),
            "description": f"Definition for {column}", "discrepancies": [],
            "notes": "", "role": roles.get(column, "Feature"),
            "dictionary_role": roles.get(column, "").lower(),
            "profile_json": {
                "n_levels": distinct,
                "summary": {"count": int(frame[column].notna().sum()),
                            "missing": int(frame[column].isna().sum())},
            },
            "provisional": 0, "updated_at": now,
        })
    return asset_id, snapshot_id


def _frame(rows: int = 120) -> pd.DataFrame:
    half = rows // 2
    return pd.DataFrame({
        "target": [0] * half + [1] * half,
        "perfect": [0] * half + [1] * half,
        "noisy": [index % 2 for index in range(rows)],
        "constant": [1] * rows,
        "period": ["2026Q1"] * rows,
        "customer_id": [f"C{index:04d}" for index in range(rows)],
        "ignored": list(range(rows)),
    })


class FeatureTargetSeparationAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_register()

    def _run(self, snapshot_id: str, **overrides):
        defaults = {
            "table": "portfolio",
            "target_type": "binary",
            "positive_class": 1,
            "missing_target_action": "drop",
            "binning_constraints": {"execution_mode": "quick", "max_workers": 1},
        }
        defaults.update(overrides)
        return assess_snapshot(snapshot_id, **defaults)

    def test_sourcing_role_inference_matches_the_reference_selection_policy(self):
        self.assertEqual(service.INVENTORY_ROLE_BY_DICTIONARY, {
            "feature": "Feature", "score": "Feature", "target": "Target",
            "identifier": "Identifier", "period": "Period", "date": "Date",
            "weight": "Weight", "group": "Group", "ignore": "Ignore",
        })
        self.assertEqual(service._role_for("facility_id", "text", None), "Identifier")
        self.assertEqual(service._role_for("reporting_quarter", "text", None), "Period")
        self.assertEqual(service._role_for("origination_date", "datetime", None), "Date")
        self.assertEqual(service._role_for("rating", "numerical", None), "Score")
        unique = pd.Series([f"value-{index}" for index in range(20)])
        self.assertEqual(service._role_for("reference", "text", None, unique), "Identifier")

    def test_register_row_2_is_executable_after_slice_acceptance(self):
        self.assertEqual(require_executable(2)["diagnostic_id"], 2)

    def test_uses_confirmed_target_and_only_eligible_feature_roles(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        outcome = self._run(snapshot_id)

        requested = outcome.manifest.scope.columns
        self.assertEqual(requested, ("noisy", "perfect"))
        self.assertEqual(outcome.manifest.scope.target, "target")
        self.assertEqual(outcome.response.results[0].feature, "perfect")
        self.assertTrue(outcome.candidate_findings)
        self.assertTrue(all(row["decision_type"] == "candidate_flag"
                            and row["review_state"] == "open"
                            for row in outcome.candidate_findings))
        self.assertEqual(s.query("issues_v2", item_id=snapshot_id), [])

    def test_opposite_binary_positive_classes_have_distinct_aar_routes(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        event_one = self._run(snapshot_id, feature_columns=["noisy"], positive_class=1)
        event_zero = self._run(snapshot_id, feature_columns=["noisy"], positive_class=0)

        self.assertEqual(event_one.manifest.scope.positive_class, "1")
        self.assertEqual(event_zero.manifest.scope.positive_class, "0")
        self.assertNotEqual(
            event_one.manifest.scope.target_fingerprint,
            event_zero.manifest.scope.target_fingerprint,
        )
        repo = AnalysisArtifactRepository()
        one_meta, one_payload = repo.get(event_one.artifact_ids["iv"][0])
        zero_meta, zero_payload = repo.get(event_zero.artifact_ids["iv"][0])
        self.assertNotEqual(one_meta.target_fingerprint, zero_meta.target_fingerprint)
        self.assertEqual(one_payload["target_route"]["positive_class"], "1")
        self.assertEqual(zero_payload["target_route"]["positive_class"], "0")

    def test_partial_feature_outcomes_survive_binning_failure(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        outcome = self._run(snapshot_id, feature_columns=["constant", "perfect"])
        by_feature = {row.feature: row for row in outcome.response.results}

        self.assertEqual(outcome.response.status, "partial")
        self.assertIn("perfect", by_feature)
        self.assertEqual(by_feature["perfect"].auc, 1.0)
        self.assertEqual(by_feature["constant"].errors,
                         ["Feature has fewer than two distinct regular values after exclusions."])

    def test_feature_level_automatic_artifacts_are_diagnostic_specific_versions(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        first = self._run(snapshot_id)
        second = self._run(snapshot_id)

        self.assertNotEqual(first.artifact_ids, second.artifact_ids)
        for artifact_type in ("roc_feature", "fine_bins", "coarse_bins", "iv"):
            self.assertTrue(first.artifact_ids[artifact_type], artifact_type)

        repo = AnalysisArtifactRepository()
        metadata, payload = repo.get(first.artifact_ids["roc_feature"][0])
        self.assertEqual(metadata.artifact_type, "roc_feature")
        self.assertEqual(metadata.scope, "diagnostic_local")
        self.assertIn("result", payload)
        self.assertEqual(
            {repo.get_metadata(value).artifact_type for value in metadata.source_artifact_ids},
            {"column_profile"},
        )
        self.assertEqual(len(metadata.source_artifact_ids), 2)
        self.assertTrue(outcome_source_ids := first.manifest.source_artifact_ids)
        self.assertTrue(set(metadata.source_artifact_ids).issubset(outcome_source_ids))

        iv_metadata = repo.get_metadata(first.artifact_ids["iv"][0])
        self.assertEqual(len(iv_metadata.source_artifact_ids), 2)

    def test_schema_role_does_not_block_explicit_feature_override(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        outcome = self._run(snapshot_id, feature_columns=["customer_id"])
        self.assertEqual(outcome.manifest.scope.columns, ("customer_id",))

    def test_dictionary_role_fills_an_unset_inventory_role(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        s.update("variable_inventory", {
            "item_id": snapshot_id,
            "table_name": "portfolio",
            "column_name": "customer_id",
        }, {"role": None, "dictionary_role": "identifier"})

        outcome = self._run(snapshot_id, feature_columns=["customer_id"])
        self.assertEqual(outcome.manifest.scope.columns, ("customer_id",))

    def test_unset_legacy_roles_are_inferred_as_advisory_recommendations(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        for column in ("customer_id", "period"):
            s.update("variable_inventory", {
                "item_id": snapshot_id,
                "table_name": "portfolio",
                "column_name": column,
            }, {"role": None, "dictionary_role": None})

        manifest = manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        metadata = {row["column"]: row for row in manifest["scope"]["feature_metadata"]}
        self.assertEqual(metadata["customer_id"]["role"], "Identifier")
        self.assertEqual(metadata["period"]["role"], "Period")
        self.assertNotIn("customer_id", manifest["scope"]["selected_features"])
        self.assertNotIn("period", manifest["scope"]["selected_features"])

        stale = manifest["scope"]
        stale["eligible_features"].append("customer_id")
        stale["selected_features"].append("customer_id")
        metadata["customer_id"].update({"role": "Feature", "eligible": True})
        s.update("diag_runs", {"run_id": manifest["run_id"]}, {"manifest_json": manifest})

        refreshed = manifest_feature_target.refresh_draft_scope(manifest["run_id"])
        self.assertIn("customer_id", refreshed["scope"]["eligible_features"])
        self.assertIn("customer_id", refreshed["scope"]["selected_features"])
        self.assertEqual(refreshed["scope"]["feature_metadata"][1]["role"], "Identifier")

    def test_diagnostic_manifest_freezes_confirmed_target_and_feature_scope(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        s.update("variable_inventory", {
            "item_id": snapshot_id,
            "table_name": "portfolio",
            "column_name": "noisy",
        }, {"role": "Feature", "dictionary_role": "score"})
        manifest = manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        self.assertEqual(manifest["target"]["column"], "target")
        self.assertEqual(manifest["scope"]["selected_features"],
                         ["noisy", "perfect"])
        metadata = {row["column"]: row for row in manifest["scope"]["feature_metadata"]}
        self.assertEqual(set(metadata), {
            "constant", "customer_id", "ignored", "noisy", "perfect", "period",
        })
        self.assertEqual(metadata["customer_id"]["role"], "Identifier")
        self.assertTrue(metadata["customer_id"]["eligible"])
        self.assertFalse(metadata["customer_id"]["recommended"])
        self.assertIn("can still select", metadata["customer_id"]["recommendation_reason"])
        self.assertEqual(metadata["period"]["role"], "Period")
        self.assertTrue(metadata["period"]["eligible"])
        self.assertFalse(metadata["period"]["recommended"])
        self.assertFalse(metadata["constant"]["recommended"])
        self.assertIn("1 observed level", metadata["constant"]["recommendation_reason"])
        self.assertEqual(metadata["noisy"]["role"], "Feature")
        self.assertEqual(metadata["noisy"]["role_source"],
                         "analytics_artifact_repository")
        self.assertTrue(metadata["noisy"]["recommended"])

        manifest_feature_target.patch_manifest(manifest["run_id"], {
            "kind": "feature_selection", "features": ["perfect", "constant"],
        }, actor="analyst")
        frozen = manifest_feature_target.freeze(manifest["run_id"], actor="analyst")
        self.assertEqual(frozen["scope"]["selected_features"], ["perfect", "constant"])
        with self.assertRaisesRegex(Exception, "frozen"):
            manifest_feature_target.patch_manifest(manifest["run_id"], {
                "kind": "feature_selection", "features": ["perfect"],
            })

    def test_runner_persists_partial_feature_results_and_review_candidates(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        manifest = manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        # A non-recommended constant remains a valid operator override and is
        # retained as a feature-level partial outcome rather than UI-gated.
        manifest_feature_target.patch_manifest(manifest["run_id"], {
            "kind": "feature_selection", "features": ["constant", "noisy", "perfect"],
        })
        manifest_feature_target.patch_manifest(manifest["run_id"], {
            "kind": "parameter_tune", "key": "target_type", "value": "binary",
        })
        manifest_feature_target.patch_manifest(manifest["run_id"], {
            "kind": "parameter_tune", "key": "positive_class", "value": 1,
        })
        manifest_feature_target.patch_manifest(manifest["run_id"], {
            "kind": "parameter_tune", "key": "binning_constraints",
            "value": {"execution_mode": "quick", "max_workers": 1},
        })

        frames = list(runner_feature_target.run(manifest["run_id"], actor="test"))
        self.assertEqual(frames[0]["phase"], "start")
        self.assertEqual(frames[-1]["phase"], "done")
        self.assertTrue(any(frame["phase"] == "progress" for frame in frames))
        previews = [frame["preview"] for frame in frames if frame["phase"] == "feature_result"]
        self.assertEqual({row["feature"] for row in previews}, {"constant", "noisy", "perfect"})
        roc_previews = [row for row in previews if row["stage"] == "roc"]
        iv_previews = [row for row in previews if row["stage"] == "iv"]
        self.assertEqual(len(roc_previews), 3)
        self.assertEqual(len(iv_previews), 3)
        self.assertTrue(all(row["status"] == "roc_complete" for row in roc_previews))
        self.assertTrue(all("category" in row for row in iv_previews))
        results = s.query("diag_results", run_id=manifest["run_id"])
        feature_results = [row for row in results
                           if (row.get("metrics_json") or {}).get("result_kind") == "feature"]
        self.assertEqual(len(feature_results), 3)
        self.assertTrue(any((row["metrics_json"] or {}).get("errors") for row in feature_results))
        findings = s.query("diag_findings", run_id=manifest["run_id"])
        self.assertTrue(findings)
        self.assertTrue(all(row["review_state"] == "open" for row in findings))
        self.assertEqual(s.query("issues_v2", item_id=snapshot_id), [])

        leakage = next(row for row in findings if row["pattern"] == "target_leakage")
        first_issue = runner_feature_target.ensure_candidate_issue(leakage["finding_id"], "test")
        second_issue = runner_feature_target.ensure_candidate_issue(leakage["finding_id"], "test")
        self.assertEqual(first_issue, second_issue)
        issue = s.query_one("issues_v2", issue_row_id=first_issue)
        self.assertEqual(issue["diagnostic_id"], 2)
        self.assertEqual(issue["finding_id"], leakage["finding_id"])

        manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        board = v2.diagnostics_board(snapshot_id)
        card = next(row for row in board["cards"] if row["diagnostic_id"] == 2)
        self.assertEqual(card["last_run"]["run_id"], manifest["run_id"])
        read_back = v2.diagnostic_results(snapshot_id, diagnostic_id=2)
        self.assertEqual(read_back["run"]["run_id"], manifest["run_id"])
        self.assertTrue(any(
            (row.get("metrics_json") or {}).get("auc") is not None
            for row in read_back["results"]
        ))

    def test_v2_api_dispatches_scope_run_results_and_issue_promotion(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        manifest = v2.build_diagnostic_manifest(
            snapshot_id, v2.ManifestIn(diagnostic_id=2))
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(
            kind="feature_selection", features=["perfect"]))
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(
            kind="parameter_tune", key="target_type", value="binary"))
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(
            kind="parameter_tune", key="positive_class", value=1))
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(
            kind="parameter_tune", key="binning_constraints",
            value={"execution_mode": "quick", "max_workers": 1}))

        completed = v2.run_diagnostic_manifest(
            manifest["run_id"], v2.RunIn(stream=False))
        self.assertEqual(completed["phase"], "done")
        payload = v2.diagnostic_results(snapshot_id, manifest["run_id"])
        feature = next(row for row in payload["results"]
                       if (row.get("metrics_json") or {}).get("result_kind") == "feature")
        self.assertEqual(feature["metrics_json"]["feature"], "perfect")
        finding = feature["findings"][0]
        disposition = v2.disposition_finding(
            finding["finding_id"], v2.DispositionIn(action="confirm_issue", reason="Confirmed after evidence review"))
        self.assertEqual(disposition["review_state"], "confirmed")
        self.assertTrue(disposition["issue_row_id"])
        issue = s.query_one("issues_v2", issue_row_id=disposition["issue_row_id"])
        self.assertEqual(issue["diagnostic_id"], 2)

    def test_exact_feature_settings_run_once_and_changed_analysis_overwrites_same_issue(self):
        _asset_id, snapshot_id = _snapshot(_frame())

        def configured_manifest(*, leakage_auc: float = 0.90):
            manifest = manifest_feature_target.build_manifest(
                snapshot_id, actor="test", enforce_register=False)
            run_id = manifest["run_id"]
            manifest_feature_target.patch_manifest(run_id, {
                "kind": "feature_selection", "features": ["perfect"],
            })
            manifest_feature_target.patch_manifest(run_id, {
                "kind": "parameter_tune", "key": "target_type", "value": "binary",
            })
            manifest_feature_target.patch_manifest(run_id, {
                "kind": "parameter_tune", "key": "positive_class", "value": 1,
            })
            manifest_feature_target.patch_manifest(run_id, {
                "kind": "parameter_tune", "key": "binning_constraints",
                "value": {"execution_mode": "quick", "max_workers": 1},
            })
            if leakage_auc != 0.90:
                manifest_feature_target.patch_manifest(run_id, {
                    "kind": "threshold_tune", "key": "leakage_auc", "value": leakage_auc,
                })
            return run_id

        first_run = configured_manifest()
        runner_feature_target.execute_now(first_run, actor="test")
        first_finding = s.query("diag_findings", run_id=first_run)[0]
        issue_id = runner_feature_target.ensure_candidate_issue(first_finding["finding_id"], "test")

        exact_run = configured_manifest()
        exact_manifest = s.query_one("diag_runs", run_id=exact_run)["manifest_json"]
        self.assertEqual(exact_manifest["execution"]["runnable_features"], [])
        self.assertEqual(
            exact_manifest["execution"]["completed_exact"]["perfect"]["run_id"],
            first_run,
        )
        with self.assertRaisesRegex(Exception, "already has a completed result"):
            runner_feature_target.execute_now(exact_run, actor="test")

        changed_run = configured_manifest(leakage_auc=0.95)
        runner_feature_target.execute_now(changed_run, actor="test")
        changed_finding = s.query("diag_findings", run_id=changed_run)[0]
        with self.assertRaises(Exception) as conflict:
            v2.disposition_finding(
                changed_finding["finding_id"], v2.DispositionIn(action="confirm_issue", reason="Confirmed after evidence review"))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertTrue(conflict.exception.detail["overwrite_required"])
        self.assertEqual(
            s.query("diag_dispositions", target_id=changed_finding["finding_id"]), [])

        disposition = v2.disposition_finding(
            changed_finding["finding_id"],
            v2.DispositionIn(action="confirm_issue", reason="Replace with newer governed evidence", overwrite_existing=True),
        )
        overwritten_id = disposition["issue_row_id"]
        self.assertEqual(overwritten_id, issue_id)
        active = [row for row in s.query("issues_v2", item_id=snapshot_id)
                  if row.get("status") != "Superseded"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["finding_id"], changed_finding["finding_id"])

    def test_results_hydrate_tree_and_fine_bins_and_persist_governed_revisions(self):
        _asset_id, snapshot_id = _snapshot(_frame())
        manifest = manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        run_id = manifest["run_id"]
        for patch in (
            {"kind": "feature_selection", "features": ["perfect"]},
            {"kind": "parameter_tune", "key": "target_type", "value": "binary"},
            {"kind": "parameter_tune", "key": "positive_class", "value": 1},
            {"kind": "parameter_tune", "key": "binning_constraints",
             "value": {"execution_mode": "quick", "max_workers": 1}},
        ):
            manifest_feature_target.patch_manifest(run_id, patch)
        runner_feature_target.execute_now(run_id, actor="test")

        payload = v2.diagnostic_results(snapshot_id, run_id)
        feature = next(row for row in payload["results"]
                       if (row.get("metrics_json") or {}).get("result_kind") == "feature")
        metrics = feature["metrics_json"]
        self.assertTrue(metrics["roc_detail"]["primary_tree"])
        self.assertTrue(metrics["binning_detail"]["fine_bins"])
        self.assertEqual(metrics["data_profile"]["classification"], "numerical")
        profile_meta = AnalysisArtifactRepository().get_metadata(
            metrics["profile_artifact_id"])
        self.assertEqual(profile_meta.artifact_type, "column_profile")
        self.assertEqual(profile_meta.feature, "perfect")

        report, report_artifact, report_reused = runner_feature_target.report_payload(run_id)
        self.assertFalse(report_reused)
        self.assertEqual(report_artifact.artifact_type,
                         "feature_target_separation_report")
        self.assertEqual(report["scope"]["target"], "target")
        self.assertEqual(report["summary"]["features_completed"], 1)
        outcome_rationale = report["features"][0]["outcome_rationale"]
        self.assertIn("Suspicious separation", outcome_rationale)
        self.assertIn("Review rationale", outcome_rationale)
        self.assertIn("AUC", outcome_rationale)
        self.assertNotIn("ai_reviews", report)
        self.assertTrue(report["source_artifact_ids"])
        report_text = runner_feature_target.report_text(run_id)
        self.assertIn("1. ANALYSIS OVERVIEW", report_text)
        self.assertIn("Separation category thresholds", report_text)
        self.assertIn("| Potential leakage |", report_text)
        self.assertIn("| Poor discrimination |", report_text)
        self.assertIn("Outcome rationales and supporting evidence", report_text)
        self.assertNotIn("AI ASSISTANCE", report_text)
        pdf, report_metadata = runner_feature_target.report_document(run_id)
        self.assertTrue(pdf)
        self.assertEqual(report_metadata["report_artifact"]["artifact_type"],
                         "feature_target_separation_report")
        self.assertTrue(report_metadata["report_artifact"]["reused"])
        response = v2.diagnostic_run_report(run_id)
        self.assertIn(f"feature-target-separation-{run_id}.pdf",
                      response.headers["content-disposition"])
        self.assertEqual(response.headers["x-analysis-artifact-id"],
                         report_metadata["report_artifact"]["artifact_id"])

        definition = metrics["binning_detail"]["coarse_definition"]
        revisions_before_preview = len(s.query(
            "diag_binning_revisions", result_id=feature["result_id"]))
        preview = v2.preview_diagnostic_binning(
            feature["result_id"],
            v2.BinningReviewIn(definition=definition),
        )
        self.assertTrue(preview["bins"])
        self.assertEqual(
            len(s.query("diag_binning_revisions", result_id=feature["result_id"])),
            revisions_before_preview,
        )
        local = v2.review_diagnostic_binning(
            feature["result_id"],
            v2.BinningReviewIn(definition=definition, scope="local"),
        )
        self.assertEqual(local["scope"], "local")
        self.assertEqual(local["revision_no"], 1)
        self.assertIsNotNone(local["governance"]["local"]["iv"])

        with self.assertRaises(Exception) as confirmation:
            v2.review_diagnostic_binning(
                feature["result_id"],
                v2.BinningReviewIn(definition=definition, scope="universal"),
            )
        self.assertEqual(confirmation.exception.status_code, 409)
        universal = v2.review_diagnostic_binning(
            feature["result_id"],
            v2.BinningReviewIn(
                definition=definition, scope="universal", confirm_universal=True),
        )
        self.assertEqual(universal["scope"], "universal")
        self.assertIsNotNone(universal["governance"]["universal"]["iv"])
        self.assertEqual(len(s.query("diag_binning_revisions", result_id=feature["result_id"])), 2)

    def test_high_cardinality_categorical_fine_bins_use_top_49_and_other(self):
        from dq_diagnostics.engines.feature_target_separation.information_value.binning import (
            fit_feature_binning,
        )

        values = [f"value-{index:02d}" for index in range(60) for _ in range(index + 1)]
        target = [index % 2 for index in range(len(values))]
        fitted = fit_feature_binning(
            pd.Series(values, name="segment"), pd.Series(target, name="target"),
            feature_name="segment", target_name="target", target_type="binary",
            feature_type="categorical",
        )

        self.assertEqual(len(fitted.fine_definition.categorical_groups), 50)
        self.assertEqual(fitted.fine_definition.categorical_group_labels[-1], "Other")
        self.assertEqual(fitted.fine_bins[-1].label, "Other")
        top_values = {group[0] for group in fitted.fine_definition.categorical_groups[:49]}
        self.assertIn("value-59", top_values)
        self.assertNotIn("value-00", top_values)
        other = set(fitted.fine_definition.categorical_groups[-1])
        self.assertIn("value-00", other)
        self.assertEqual(sum(row.rows for row in fitted.fine_bins), len(values))
        self.assertTrue(all(
            len({next(index for index, group in enumerate(fitted.coarse_definition.categorical_groups)
                      if value in group) for value in fine_group}) == 1
            for fine_group in fitted.fine_definition.categorical_groups
        ))

    def test_numeric_csv_override_creates_new_fine_foundation_then_optimizes_coarse(self):
        frame = _frame().assign(override_score=list(range(120)))
        _asset_id, snapshot_id = _snapshot(frame)
        manifest = manifest_feature_target.build_manifest(
            snapshot_id, actor="test", enforce_register=False)
        run_id = manifest["run_id"]
        for patch in (
            {"kind": "feature_selection", "features": ["override_score"]},
            {"kind": "parameter_tune", "key": "target_type", "value": "binary"},
            {"kind": "parameter_tune", "key": "positive_class", "value": 1},
            {"kind": "parameter_tune", "key": "binning_constraints",
             "value": {"execution_mode": "quick", "max_workers": 1}},
        ):
            manifest_feature_target.patch_manifest(run_id, patch)
        runner_feature_target.execute_now(run_id, actor="test")
        payload = v2.diagnostic_results(snapshot_id, run_id)
        feature = next(row for row in payload["results"]
                       if (row.get("metrics_json") or {}).get("result_kind") == "feature")

        created = v2.create_numeric_diagnostic_binning_override(
            feature["result_id"],
            v2.NumericIvOverrideIn(
                cuts="20,50,80", special_values="-999",
                rationale="approved business fine foundation",
            ),
        )
        self.assertEqual(created["fine_definition"]["method"],
                         "operator_numeric_cut_prebinning")
        self.assertEqual(created["fine_definition"]["numeric_splits"], [20.0, 50.0, 80.0])
        self.assertLessEqual(
            set(created["coarse_definition"]["numeric_splits"]),
            set(created["fine_definition"]["numeric_splits"]),
        )
        self.assertEqual(created["numeric_override"]["added_special_values"], [-999.0])
        self.assertEqual(created["scope"], "diagnostic_specific")
        self.assertIsNotNone(created["governance"]["diagnostic_specific"]["iv"])
        hydrated = v2.diagnostic_results(snapshot_id, run_id)
        hydrated_feature = next(row for row in hydrated["results"]
                                if row["result_id"] == feature["result_id"])
        detail = hydrated_feature["metrics_json"]["binning_detail"]
        self.assertEqual(detail["fine_definition"]["numeric_splits"], [20.0, 50.0, 80.0])
        self.assertEqual(detail["numeric_override"]["rationale"],
                         "approved business fine foundation")

    def test_numeric_override_csv_reports_the_invalid_token_position(self):
        from dq_diagnostics.binning_reviews import _numeric_csv

        with self.assertRaisesRegex(
            ValueError, r"Numeric cuts: value 2 .* is not numeric"
        ):
            _numeric_csv("10,NA,30", "Numeric cuts")


if __name__ == "__main__":
    unittest.main()
