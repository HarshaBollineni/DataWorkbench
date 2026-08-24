from __future__ import annotations

import math
import uuid

import pandas as pd
import pytest

from analysis_runtime.artifact_types import get_artifact_type, validate_payload
from analysis_runtime.artifacts import AnalysisArtifactRepository
from analysis_runtime.data_sourcing_artifacts import persist_snapshot_profile_artifacts
from analysis_runtime.population_guidance import exact_population_options, guidance_from_profile
from dq_diagnostics.dispatch import adapter
from dq_diagnostics.engines.population_stability.bins import create_draft_bins, validate_bin_definition
from dq_diagnostics.engines.population_stability.engine import calculate_feature_psi, classify
from dq_diagnostics.engines.population_stability.population import split_population
import system_db as db
from ai.v2 import service
from dq_diagnostics import manifest_population_stability, result_promotion, runner_feature_target, runner_population_stability
from dq_diagnostics.register import seed_register
from routers import v2


def frozen_numeric(**overrides):
    value = {"payload_schema_version": 1, "governance_state": "frozen", "feature": "score",
             "physical_type": "float64", "logical_type": "numeric", "kind": "numeric",
             "boundaries": [10.0, 20.0], "boundary_semantics": "right_closed", "missing_bin": True,
             "special_value_bins": [], "unseen_category_policy": "unseen_bin",
             "underflow_guard": True, "overflow_guard": True, "requested_bin_count": 3,
             "actual_bin_count": 3, "creation_methodology": "reviewed", "source_population_fingerprint": "p",
             "source_artifact_references": [], "creator": "a", "reviewer": "b",
             "review_timestamp": "2026-08-19", "supersedes_artifact_id": None}
    return {**value, **overrides}


def frozen_categorical(**overrides):
    value = frozen_numeric(kind="categorical", boundaries=None, boundary_semantics="exact_match",
        groups=[{"label": "A", "values": ["a"]}, {"label": "B", "values": ["b"]}],
        underflow_guard=False, overflow_guard=False)
    return {**value, **overrides}


def test_numeric_psi_reconciles_missing_underflow_and_overflow():
    result = calculate_feature_psi(pd.Series([-5, 5, 15, 25, None]),
                                   pd.Series([-100, 15, 30, 40, None]), frozen_numeric())
    assert math.isfinite(result["psi"])
    assert sum(row["baseline_count"] for row in result["bins"]) == 5
    assert sum(row["current_count"] for row in result["bins"]) == 5
    assert sum(row["contribution"] for row in result["bins"]) == pytest.approx(result["psi"])
    assert result["decision_type"] == "contextual"
    assert result["is_violation"] is False
    assert next(row for row in result["bins"] if row["bin"] == "bin_1")["bin_label"] == "(−∞, 10.0]"
    assert next(row for row in result["bins"] if row["bin"] == "missing")["bin_label"] == "Missing"


def test_numeric_psi_bins_are_ordered_by_interval_number_not_text():
    boundaries = [float(value) for value in range(1, 11)]
    values = pd.Series([0.5, *[value + 0.5 for value in range(1, 10)], 11.0, None])
    result = calculate_feature_psi(values, values, frozen_numeric(
        boundaries=boundaries, requested_bin_count=11, actual_bin_count=11,
    ))
    assert [row["bin"] for row in result["bins"]] == [
        "missing", *[f"bin_{value}" for value in range(1, 12)],
    ]


def test_unseen_category_is_visible_and_zero_proportions_are_smoothed():
    result = calculate_feature_psi(pd.Series(["a", "a", None]),
                                   pd.Series(["b", "new", None]), frozen_categorical())
    by_bin = {row["bin"]: row for row in result["bins"]}
    assert by_bin["unseen"]["current_count"] == 1
    assert by_bin["A"]["bin_label"] == "A: a"
    assert math.isfinite(result["psi"])


def test_declared_special_values_are_visible_and_reconcile():
    bins = frozen_numeric(special_value_bins=[{"label": "sentinel", "values": [-999]}])
    result = calculate_feature_psi(pd.Series([-999, 5]), pd.Series([5, -999]), bins)
    special = next(row for row in result["bins"] if row["bin"] == "special:sentinel")
    assert special["baseline_count"] == special["current_count"] == 1


def test_unseen_category_can_be_governed_as_error():
    with pytest.raises(ValueError, match="unseen"):
        calculate_feature_psi(pd.Series(["a"]), pd.Series(["new"]),
                              frozen_categorical(unseen_category_policy="error"))


def test_threshold_boundaries_are_inclusive():
    assert classify(0.099, {"watch": .10, "investigate": .25}) == "stable"
    assert classify(0.10, {"watch": .10, "investigate": .25}) == "watch"
    assert classify(0.25, {"watch": .10, "investigate": .25}) == "investigate"


def test_bins_must_be_reviewed_non_overlapping_and_guarded():
    with pytest.raises(ValueError, match="frozen"):
        validate_bin_definition(frozen_numeric(governance_state="draft"))
    with pytest.raises(ValueError, match="unique"):
        validate_bin_definition(frozen_numeric(boundaries=[10, 10]))
    with pytest.raises(ValueError, match="overlap"):
        validate_bin_definition(frozen_categorical(groups=[{"values": ["a"]}, {"values": ["a"]}]))
    with pytest.raises(ValueError, match="guards"):
        validate_bin_definition(frozen_numeric(overflow_guard=False))


def test_baseline_only_drafts_collapse_quantiles_and_use_top49_plus_other():
    draft = create_draft_bins(pd.Series([0] * 50 + [1] * 50 + list(range(2, 12))), "x")
    assert draft["governance_state"] == "draft"
    assert draft["boundaries"] == sorted(set(draft["boundaries"]))
    categorical = create_draft_bins(pd.Series([f"v{i}" for i in range(49)]), "x")
    assert len(categorical["groups"]) == 49
    assert all(len(group["values"]) == 1 for group in categorical["groups"])
    high = create_draft_bins(pd.Series([f"v{i}" for i in range(51)]), "x")
    assert len(high["groups"]) == 50
    assert high["groups"][-1]["label"] == "OTHER_BASELINE"
    assert len(high["groups"][-1]["values"]) == 2


def test_baseline_only_drafts_keep_confirmed_special_values_out_of_regular_bins():
    draft = create_draft_bins(pd.Series([-999, 1, 2, 3, None]), "x",
                              special_values={"source missing": [-999]})
    assert draft["special_value_bins"] == [{"label": "source missing", "values": [-999]}]
    assert all(boundary != -999 for boundary in draft["boundaries"])


def test_one_snapshot_predicate_is_disjoint_nonempty_and_fingerprinted():
    frame = pd.DataFrame({"period": [1, 1, 2, 2, None], "x": range(5)})
    result = split_population(frame, "period", {"operator": "<=", "value": 1}, null_policy="exclude")
    assert (result["baseline_count"], result["current_count"], result["excluded_count"]) == (2, 2, 1)
    assert len(result["population_fingerprint"]) == 64
    defaulted = split_population(frame, "period", {"operator": "<=", "value": 1})
    assert (defaulted["baseline_count"], defaulted["current_count"], defaulted["excluded_count"]) == (3, 2, 0)
    with pytest.raises(ValueError, match="non-empty"):
        split_population(frame, "period", {"operator": ">", "value": 99}, null_policy="exclude")


def test_temporal_population_cutoff_accepts_iso_strings():
    frame = pd.DataFrame({"as_of_date": pd.to_datetime([
        "2025-01-01", "2025-02-01", "2025-03-01",
    ])})
    result = split_population(frame, "as_of_date", {"operator": "<=", "value": "2025-02-01"})
    assert result["baseline_count"] == 2
    assert result["current_count"] == 1


def test_profile_guidance_uses_histogram_for_numeric_suggestions():
    guidance = guidance_from_profile(feature="score", feature_kind="numeric", distinct_count=100,
        profile={"distinct_count": 100, "histogram": [
            {"start": 0, "end": 25, "count": 25}, {"start": 25, "end": 50, "count": 25},
            {"start": 50, "end": 75, "count": 25}, {"start": 75, "end": 100, "count": 25},
        ]}, artifact_id="art_profile")
    assert guidance["strategy"] == "numeric_cutoff"
    assert guidance["suggestions"] == [25, 50, 75]
    assert guidance["source"]["artifact_id"] == "art_profile"

    categorical = guidance_from_profile(feature="state", feature_kind="categorical", distinct_count=3,
        profile={"distinct_count": 3, "top_k": {"CA": 2}}, artifact_id="art_profile")
    assert "current is the complement" in categorical["recommendation"].lower()
    assert "target" not in categorical["recommendation"].lower()


def test_low_cardinality_year_options_are_sorted_minimum_to_maximum():
    guidance = {"strategy": "category_groups", "distinct_count": 4}
    options = exact_population_options(
        pd.Series([2023, 2021, 2024, 2022, 2023, 2023]), guidance,
    )

    assert [row["value"] for row in options["exact_values"]] == ["2021", "2022", "2023", "2024"]
    assert options["exact_values"][2]["share"] == pytest.approx(0.5)


def test_artifact_types_are_registered_with_governed_semantics():
    assert get_artifact_type("psi").comparison_snapshot_applicability == "required"
    assert get_artifact_type("psi_bins").owner == "Population Stability Index"
    payload = {"feature": "x", "psi": .1, "classification": "watch", "bins": [], "epsilon": 1e-6,
               "thresholds": {"watch": .1, "investigate": .25}, "population_fingerprint": "p",
               "bin_artifact_id": "a", "bin_payload_hash": "h", "decision_type": "contextual",
               "is_violation": False}
    validate_payload("psi", payload, 1)
    with pytest.raises(ValueError, match="contextual"):
        validate_payload("psi", {**payload, "is_violation": True}, 1)


def test_dispatch_is_explicit_and_does_not_fall_through():
    assert adapter(2)["agent"] == "feature_target_separation_engine"
    assert adapter(4)["agent"] == "cross_field_engine"
    assert adapter(14)["agent"] == "population_stability_index_engine"
    with pytest.raises(ValueError, match="no diagnostic adapter"):
        adapter(99)


def _snapshot(frame, table="portfolio"):
    db.init_schema()
    suffix = uuid.uuid4().hex[:8]; asset_id = f"asset_psi_{suffix}"; item_id = f"item_psi_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {"asset_id": asset_id, "system_id": f"PSI{suffix[:4]}", "alias": asset_id,
        "display_name": "PSI fixture", "kind": "dataset", "time_basis": "period", "current_version_no": 1,
        "lifecycle_status": "active", "created_at": now, "updated_at": now, "target_variable": None,
        "use_case": "Monitoring", "product": "Loans"})
    db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": "PSI fixture", "status": "profiled",
        "created_at": now, "updated_at": now, "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": item_id, "intent": "fresh", "ingest_status": "ready",
        "target_variable": None, "use_case": "Monitoring", "period_column": "period"})
    service._write_table(item_id, table, frame)
    for column in frame:
        db.upsert("variable_inventory", {"item_id": item_id, "table_name": table, "column_name": column,
            "classification": "numerical", "data_type": str(frame[column].dtype), "description": column,
            "discrepancies": [], "notes": "", "role": "Period" if column == "period" else "Feature",
            "dictionary_role": "period" if column == "period" else "feature", "profile_json": {},
            "provisional": 0, "updated_at": now})
    return item_id


def test_one_snapshot_manifest_bin_review_runner_artifact_and_contextual_handoff():
    item_id = _snapshot(pd.DataFrame({"period": [1] * 20 + [2] * 20,
        "score": list(range(20)) + list(range(20, 40))}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "generate_new"}}, actor="test")
    draft = manifest_population_stability.create_bin_draft(run_id, "score", actor="test")
    assert draft["payload"]["governance_state"] == "draft"
    persisted_draft = db.query_one("diag_runs", run_id=run_id)["manifest_json"]
    assert persisted_draft["bin_drafts"]["score"]["artifact_id"] == draft["artifact"]["artifact_id"]
    with pytest.raises(Exception, match="requires confirmation"):
        manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
            "value": {"mode": "repository"}}, actor="test")
    review = manifest_population_stability.bin_review(run_id, "score", draft["artifact"]["artifact_id"])
    assert review["population_match"] is True
    assert review["payload"]["source_population_fingerprint"] == persisted_draft["baseline"]["data_fingerprint"]
    frozen = manifest_population_stability.freeze_bin_draft(run_id, "score",
        draft["artifact"]["artifact_id"], actor="reviewer")
    assert frozen["artifact"]["status"] == "active"
    assert "score" not in frozen["manifest"]["bin_drafts"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    run_events = list(runner_population_stability.run(run_id, actor="test"))
    done = run_events[-1]
    assert done["phase"] == "done" and done["issues"] == {"created": []}
    live = next(event["preview"] for event in run_events if event["phase"] == "progress")
    assert live["feature"] == "score" and live["classification"] == "investigate"
    results = db.query("diag_results", run_id=run_id)
    feature = next(row for row in results if row["metrics_json"].get("result_kind") == "psi_feature")
    assert feature["decision_type"] == "contextual" and feature["verdict"] is None
    assert feature["metrics_json"]["classification"] == "investigate"
    finding = db.query_one("diag_findings", result_id=feature["result_id"])
    assert finding["outcome"] == "CONTEXTUAL" and finding["violation_count"] == 0
    assert db.query_one("issues_v2", finding_id=finding["finding_id"]) is None
    issue_id = runner_population_stability.ensure_contextual_issue(finding["finding_id"], actor="reviewer")
    assert db.query_one("issues_v2", issue_row_id=issue_id)["diagnostic_id"] == 14
    repo_artifact = db.query_one("analysis_artifacts", artifact_type="psi", run_id=run_id)
    assert repo_artifact and repo_artifact["comparison_snapshot_id"] == item_id
    artifact_count = len(db.query("analysis_artifacts", artifact_type="psi"))
    runner_population_stability.execute_now(run_id, actor="test")
    assert len(db.query("analysis_artifacts", artifact_type="psi")) == artifact_count


def test_batch_bin_approval_freezes_multiple_drafts_with_one_manifest_refresh(monkeypatch):
    item_id = _snapshot(pd.DataFrame({"period": [1] * 20 + [2] * 20,
        "score": list(range(40)), "balance": [value * 10 for value in range(40)]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score", "balance"]}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "generate_new"}}, actor="test")
    manifest_population_stability.create_bin_draft(run_id, "score", actor="test")
    manifest_population_stability.create_bin_draft(run_id, "balance", actor="test")

    refreshes = 0
    original_refresh = manifest_population_stability._refresh

    def counted_refresh(*args, **kwargs):
        nonlocal refreshes
        refreshes += 1
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(manifest_population_stability, "_refresh", counted_refresh)
    result = manifest_population_stability.approve_bin_batch(run_id, ["score", "balance"], actor="reviewer")

    assert result["approved_features"] == ["score", "balance"]
    assert set(result["manifest"]["frozen_bins"]) == {"score", "balance"}
    assert result["manifest"]["bin_drafts"] == {}
    assert len(result["artifacts"]) == 2
    assert refreshes == 1


def test_shared_result_promotion_requires_rationale_and_creates_auditable_issue():
    item_id = _snapshot(pd.DataFrame({"period": [1, 2], "score": [10, 10]}))
    run_id, result_id = f"drun_{uuid.uuid4().hex[:12]}", f"dres_{uuid.uuid4().hex[:12]}"
    now = db.now_ist()
    db.insert("diag_runs", {"run_id": run_id, "item_id": item_id, "diagnostic_id": 99,
        "manifest_json": {}, "status": "done", "created_at": now, "started_at": now, "finished_at": now})
    db.insert("diag_results", {"result_id": result_id, "run_id": run_id, "diagnostic_id": 99,
        "entity_or_table": "score", "decision_type": "classification_output", "verdict": None,
        "review_state": "open", "metrics_json": {"result_kind": "feature", "feature": "score"},
        "thresholds_used_json": {}, "scope_counts_json": {"rows_evaluated": 2},
        "na_reason": None, "created_at": now})
    with pytest.raises(ValueError, match="rationale"):
        result_promotion.ensure_override_finding(result_id, None)
    finding_id = result_promotion.ensure_override_finding(result_id, "Domain evidence warrants escalation")
    issue_id = result_promotion.ensure_generic_issue(finding_id)
    finding = db.query_one("diag_findings", finding_id=finding_id)
    issue = db.query_one("issues_v2", issue_row_id=issue_id)
    assert finding["pattern"] == "operator_override"
    assert finding["pattern_detail"] == "Domain evidence warrants escalation"
    assert issue["diagnostic_id"] == 99 and issue["finding_id"] == finding_id


def test_diagnostic_two_unflagged_feature_can_be_promoted_with_rationale():
    item_id = _snapshot(pd.DataFrame({"period": [1, 2], "score": [10, 10]}))
    run_id, result_id = f"drun_{uuid.uuid4().hex[:12]}", f"dres_{uuid.uuid4().hex[:12]}"
    now = db.now_ist()
    db.insert("diag_runs", {"run_id": run_id, "item_id": item_id, "diagnostic_id": 2,
        "manifest_json": {"scope": {"table": "portfolio"}}, "status": "done",
        "created_at": now, "started_at": now, "finished_at": now})
    db.insert("diag_results", {"result_id": result_id, "run_id": run_id, "diagnostic_id": 2,
        "entity_or_table": "score", "decision_type": "candidate_flag", "verdict": None,
        "review_state": "open", "metrics_json": {"result_kind": "feature", "feature": "score",
            "target": "target", "auc": 0.62, "gini": 0.24, "iv": 0.08,
            "category": "medium", "candidate": None, "rows_evaluated": 2, "artifact_ids": {}},
        "thresholds_used_json": {"values": {}}, "scope_counts_json": {"rows_evaluated": 2},
        "na_reason": None, "created_at": now})
    with pytest.raises(ValueError, match="rationale"):
        runner_feature_target.ensure_candidate_finding(result_id)
    finding_id = runner_feature_target.ensure_candidate_finding(result_id, "Known policy driver")
    issue_id = runner_feature_target.ensure_candidate_issue(finding_id)
    assert db.query_one("diag_findings", finding_id=finding_id)["pattern"] == "operator_override"
    assert db.query_one("issues_v2", issue_row_id=issue_id)["diagnostic_id"] == 2


def test_existing_v2_api_family_dispatches_psi_without_cross_field_fallthrough():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    seed_register(); db.update("diagnostic_register", {"diagnostic_id": 14},
                               {"workflow_status": "executable", "enabled_by": "test gate"})
    try:
        manifest = v2.build_diagnostic_manifest(item_id, v2.ManifestIn(diagnostic_id=14))
        assert manifest["manifest_kind"] == "population_stability_index"
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(kind="population_split",
            feature="period", value={"operator": "<=", "value": 1}))
        v2.patch_diagnostic_manifest(manifest["run_id"], v2.ManifestPatch(kind="target_choice",
            value={"mode": "target_free"}))
        fetched = v2.get_diagnostic_manifest(manifest["run_id"])
        assert fetched["manifest"]["population_preview"]["baseline_count"] == 2
        assert fetched["manifest"]["target_choice"]["does_not_modify_data_sourcing"] is True
        saved = manifest_population_stability.latest_draft(item_id)
        assert saved["run_id"] == manifest["run_id"]
        assert saved["completed_steps"] == 3
        assert saved["last_saved_at"] == fetched["manifest"]["updated_at"]

        with pytest.raises(Exception, match="explicitly resume it or start afresh"):
            v2.build_diagnostic_manifest(item_id, v2.ManifestIn(diagnostic_id=14))

        fresh = v2.build_diagnostic_manifest(item_id, v2.ManifestIn(
            diagnostic_id=14, start_afresh=True,
        ))
        assert fresh["run_id"] != manifest["run_id"]
        assert fresh["population_method"]["selected"] is None
        assert db.query_one("diag_runs", run_id=manifest["run_id"])["status"] == "discarded"
        assert manifest_population_stability.latest_draft(item_id)["run_id"] == fresh["run_id"]
    finally:
        seed_register()


def test_two_snapshot_mode_preserves_order_and_schema_compatibility():
    baseline_id = _snapshot(pd.DataFrame({"score": [1, 2, 3], "baseline_only": [1, 1, 1]}))
    current_id = _snapshot(pd.DataFrame({"score": [2, 3, 4], "current_only": [2, 2, 2]}))
    manifest = manifest_population_stability.build_manifest(baseline_id, current_snapshot_id=current_id,
                                                             enforce_register=False)
    assert manifest["mode"] == "two_snapshot"
    assert manifest["baseline"]["snapshot"]["snapshot_id"] == baseline_id
    assert manifest["current"]["snapshot"]["snapshot_id"] == current_id
    assert manifest["schema_comparison"]["compatible"] == ["score"]
    assert manifest["schema_comparison"]["baseline_only"] == ["baseline_only"]
    assert manifest["schema_comparison"]["current_only"] == ["current_only"]
    score = manifest["schema_comparison"]["compatible_details"][0]
    assert score["column"] == "score"
    assert score["baseline"]["analytical_type"] == score["current"]["analytical_type"] == "numeric"
    assert manifest["schema_comparison"]["compatibility_basis"]["schema_roles_used_for_type_compatibility"] is False
    assert manifest["schema_comparison"]["baseline_only_details"][0]["column"] == "baseline_only"
    assert manifest["schema_comparison"]["current_only_details"][0]["column"] == "current_only"


def test_schema_compatibility_uses_analytical_type_not_schema_role():
    baseline_id = _snapshot(pd.DataFrame({"score": [1, 2, 3]}))
    current_id = _snapshot(pd.DataFrame({"score": [2, 3, 4]}))
    db.update("variable_inventory", {"item_id": current_id, "table_name": "portfolio",
              "column_name": "score"}, {"role": "Identifier", "dictionary_role": "identifier",
              "classification": "numerical", "data_type": "float64"})

    role_difference = manifest_population_stability._comparison(
        baseline_id, current_id, "portfolio")
    assert role_difference["compatible"] == ["score"]
    assert role_difference["compatible_details"][0]["baseline"]["role"] == "feature"
    assert role_difference["compatible_details"][0]["current"]["role"] == "identifier"

    db.update("variable_inventory", {"item_id": current_id, "table_name": "portfolio",
              "column_name": "score"}, {"classification": "categorical", "data_type": "object"})
    type_difference = manifest_population_stability._comparison(
        baseline_id, current_id, "portfolio")
    assert type_difference["compatible"] == []
    assert type_difference["incompatible"] == ["score"]
    detail = type_difference["incompatible_details"][0]
    assert detail["baseline"]["analytical_type"] == "numeric"
    assert detail["current"]["analytical_type"] == "categorical"


def test_ready_dataset_with_different_table_name_is_discovered_by_schema_and_can_run():
    baseline_id = _snapshot(pd.DataFrame({"score": [1, 2, 3], "baseline_only": [1, 1, 1]}),
                            table="baseline_upload")
    draft = manifest_population_stability.build_manifest(baseline_id, actor="test", enforce_register=False)
    assert draft["population_method"]["selected"] is None
    assert all(row["compatibility_status"] == "pending_user_selection"
               for row in draft["population_method"]["options"]["compare_snapshots"]["snapshots"])

    current_id = _snapshot(pd.DataFrame({"score": [2, 3, 4], "current_only": [2, 2, 2]}),
                           table="current_upload")
    refreshed = manifest_population_stability.refresh_draft_scope(draft["run_id"])
    options = refreshed["population_method"]["options"]["compare_snapshots"]["snapshots"]
    candidate = next(row for row in options if row["snapshot"]["snapshot_id"] == current_id)
    assert candidate["available"] is True
    assert candidate["compatibility_status"] == "pending_user_selection"

    compared = manifest_population_stability.patch_manifest(draft["run_id"], {
        "kind": "population_method",
        "value": {"method": "compare_snapshots", "current_snapshot_id": current_id},
    }, actor="test")
    assert compared["baseline_table"] == "baseline_upload"
    assert compared["current_table"] == "current_upload"
    assert compared["schema_comparison"]["compatible"] == ["score"]

    cleared = manifest_population_stability.patch_manifest(draft["run_id"], {
        "kind": "population_method", "value": {"method": None},
    }, actor="test")
    assert cleared["population_method"]["selected"] is None
    assert cleared["population_ready"] is False

    comparison_pending = manifest_population_stability.patch_manifest(draft["run_id"], {
        "kind": "population_method", "value": {"method": "compare_snapshots"},
    }, actor="test")
    assert comparison_pending["population_method"]["selected"] == "compare_snapshots"
    assert comparison_pending["mode"] == "comparison_pending"
    assert comparison_pending["population_ready"] is False


def test_psi_roles_recommend_without_blocking_operator_selection():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    db.update("variable_inventory", {
        "item_id": item_id, "table_name": "portfolio", "column_name": "score",
    }, {"role": "Ignore", "dictionary_role": "ignore"})

    manifest = manifest_population_stability.build_manifest(
        item_id, actor="test", enforce_register=False)
    metadata = {row["column"]: row for row in manifest["feature_metadata"]}
    assert metadata["score"]["recommended"] is False
    assert "can still" in metadata["score"]["recommendation_reason"]

    updated = manifest_population_stability.patch_manifest(manifest["run_id"], {
        "kind": "scope_selection", "features": ["score"],
    }, actor="analyst")
    assert updated["selected_features"] == ["score"]


def test_constant_feature_can_be_selected_with_recorded_exact_value_guard_override():
    item_id = _snapshot(pd.DataFrame({
        "period": [1, 1, 2, 2], "accrual_indicator": [1, 1, 1, 1],
    }))
    db.update("variable_inventory", {
        "item_id": item_id, "table_name": "portfolio", "column_name": "accrual_indicator",
    }, {"role": "Ignore", "dictionary_role": "ignore",
        "profile_json": {"cardinality": 1, "unique_count": 1, "null_count": 0}})
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "population_method", "value": {"method": "split_snapshot"},
    }, actor="test")
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "baseline",
    }, actor="test")
    routed = manifest_population_stability.patch_manifest(run_id, {
        "kind": "target_choice", "value": {"mode": "target_free"},
    }, actor="test")
    before = next(row for row in routed["feature_metadata"] if row["column"] == "accrual_indicator")
    assert before["bin_route"]["workflow"] == "constant_feature_excluded"

    split_overridden = manifest_population_stability.patch_manifest(run_id, {
        "kind": "split_feature_override", "feature": "period", "enabled": True,
    }, actor="reviewer")
    period = next(row for row in split_overridden["feature_metadata"] if row["column"] == "period")
    assert split_overridden["selected_features"] == []
    assert period["bin_route"]["split_feature_override"] is True
    assert "structurally influenced" in period["bin_route"]["warning"]
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "split_feature_override", "feature": "period", "enabled": False,
    }, actor="reviewer")

    overridden = manifest_population_stability.patch_manifest(run_id, {
        "kind": "feature_eligibility_override", "feature": "accrual_indicator", "enabled": True,
    }, actor="reviewer")
    assert overridden["selected_features"] == []
    overridden = manifest_population_stability.patch_manifest(run_id, {
        "kind": "scope_selection", "features": ["accrual_indicator"],
    }, actor="reviewer")
    overridden = manifest_population_stability.patch_manifest(run_id, {
        "kind": "binning_source_choice", "value": {"mode": "generate_new"},
    }, actor="reviewer")
    after = next(row for row in overridden["feature_metadata"] if row["column"] == "accrual_indicator")
    assert overridden["selected_features"] == ["accrual_indicator"]
    assert after["bin_route"]["workflow"] == "generate_constant_guard"
    assert after["bin_route"]["eligibility_override"] is True
    assert "constant in Baseline" in after["bin_route"]["warning"]

    draft = manifest_population_stability.create_bin_draft(run_id, "accrual_indicator", actor="reviewer")
    assert draft["payload"]["kind"] == "categorical"
    assert draft["payload"]["groups"] == [{"label": "1", "values": ["1"]}]
    assert draft["payload"]["creation_methodology"] == "constant_baseline_exact_value_guard"


def test_binning_route_is_automatic_and_user_source_patches_are_overridden():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    repository = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "repository"}}, actor="test")
    generated = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "generate_new"}}, actor="test")
    assert repository["selected_features"] == generated["selected_features"] == ["score"]
    assert repository["binning_source_choice"]["mode"] == "psi_contract_generate"
    assert generated["binning_source_choice"]["mode"] == "psi_contract_generate"
    assert generated["binning_source_choice"]["automatic"] is True


def test_refresh_repairs_orphaned_binning_state_and_progress_is_sequential():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest.update({
        "selected_features": [],
        "binning_source_choice": {"status": "confirmed", "mode": "repository"},
        "frozen_bins": {"score": {"artifact_id": "orphan"}},
        "bin_drafts": {"score": {"artifact_id": "orphan-draft"}},
    })
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})

    summary = manifest_population_stability.latest_draft(item_id)
    assert summary["completed_steps"] == 0
    assert summary["selected_feature_count"] == 0
    assert summary["frozen_bin_count"] == 0

    repaired = manifest_population_stability.refresh_draft_scope(run_id)
    assert repaired["binning_source_choice"]["status"] == "pending"
    assert repaired["frozen_bins"] == {} and repaired["bin_drafts"] == {}


def test_target_free_route_always_creates_a_new_baseline_draft():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    repository = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "repository"}}, actor="test")
    score = next(row for row in repository["feature_metadata"] if row["column"] == "score")
    score["bin_route"].update({"workflow": "confirm_frozen_reuse", "readiness": "confirmation_required",
                               "artifact": {"artifact_id": "matched-library"}})
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": repository})

    draft = manifest_population_stability.create_bin_draft(
        run_id, "score", actor="reviewer", ignore_repository_match=True)
    saved = db.query_one("diag_runs", run_id=run_id)["manifest_json"]
    assert draft["payload"]["source_artifact_references"] == []
    assert saved["bin_drafts"]["score"]["resolution"] == "automatic_bins_generated"


def test_unapproved_psi_draft_is_resumable_only_by_its_originating_run():
    item_id = _snapshot(pd.DataFrame({"score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    manifest["target_choice"] = {"status": "confirmed", "mode": "target_free"}
    snapshot = manifest["baseline"]["snapshot"]
    payload = frozen_numeric(
        governance_state="draft", feature="score",
        source_population_fingerprint=manifest["baseline"]["data_fingerprint"],
        reviewer=None, review_timestamp=None,
    )
    draft = AnalysisArtifactRepository().save(
        payload, artifact_type="psi_bins", asset_id=snapshot["asset_id"],
        snapshot_id=snapshot["snapshot_id"],
        population_fingerprint=manifest["baseline"]["data_fingerprint"],
        target_fingerprint=None, methodology_fingerprint="psi-draft-test",
        scope="diagnostic_local", owner_id="diagnostic:14", feature="score",
        table="portfolio", run_id="originating-run", created_by="test",
    ).artifact

    other_run = {**manifest, "run_id": "different-run"}
    other_evidence = manifest_population_stability._evidence_catalog(other_run, {"score"})["score"]
    assert other_evidence["exact_draft"] == []
    assert {row["artifact_id"]: row["reason"] for row in other_evidence["rejected"]}[draft.artifact_id] \
        == "unapproved_psi_draft_from_another_run"

    originating_run = {**manifest, "run_id": "originating-run"}
    own_evidence = manifest_population_stability._evidence_catalog(originating_run, {"score"})["score"]
    assert [row["artifact_id"] for row in own_evidence["exact_draft"]] == [draft.artifact_id]


def test_workflow_local_binning_foundation_does_not_cross_run_boundaries():
    from analysis_runtime.contracts import stable_fingerprint

    item_id = _snapshot(pd.DataFrame({"score": [1, 2, 3, 4], "outcome": [0, 1, 0, 1]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    manifest["target_choice"] = {"status": "confirmed", "mode": "saved_target",
                                 "saved_target": "outcome"}
    snapshot = manifest["baseline"]["snapshot"]
    definition = {"feature": "score", "feature_type": "numeric", "target": "outcome",
                  "target_type": "binary", "numeric_splits": [2.0],
                  "out_of_range_guard_bins": True}
    common = dict(
        artifact_type="fine_bins", asset_id=snapshot["asset_id"],
        snapshot_id=snapshot["snapshot_id"],
        population_fingerprint=manifest["baseline"]["data_fingerprint"],
        target_fingerprint=stable_fingerprint({"target": "outcome"}),
        methodology_fingerprint="fine-foundation-test",
        feature="score", table="portfolio", created_by="test",
    )
    repo = AnalysisArtifactRepository()
    private = repo.save({"definition": definition, "bins": []}, scope="workflow_local",
                        owner_id="diagnostic:2", run_id="diagnostic-2-private-run", **common).artifact
    shared = repo.save({"definition": definition, "bins": []}, scope="universal",
                       owner_id=None, run_id="diagnostic-2-shared-run", **common).artifact

    evidence = manifest_population_stability._evidence_catalog(
        {**manifest, "run_id": "new-psi-run"}, {"score"})["score"]
    assert [row["artifact_id"] for row in evidence["exact_fine"]] == [shared.artifact_id]
    assert {row["artifact_id"]: row["reason"] for row in evidence["rejected"]}[private.artifact_id] \
        == "workflow_local_to_another_run"


def test_psi_without_saved_target_automatically_uses_target_free_route(monkeypatch):
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    manifest = manifest_population_stability.patch_manifest(manifest["run_id"], {
        "kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude",
    }, actor="test")
    assert manifest["target_choice"]["status"] == "confirmed"
    assert manifest["target_choice"]["mode"] == "target_free"
    assert manifest["target_choice"]["automatic"] is True
    assert not any(row["code"] == "target_choice_required" for row in manifest["blockers"])
    with pytest.raises(Exception, match="select the feature"):
        manifest_population_stability.create_bin_draft(manifest["run_id"], "score", actor="test")
    list_calls = []
    original_list = AnalysisArtifactRepository.list
    def tracked_list(repository, *args, **kwargs):
        list_calls.append(kwargs.get("artifact_type"))
        return original_list(repository, *args, **kwargs)
    monkeypatch.setattr(AnalysisArtifactRepository, "list", tracked_list)
    manifest_population_stability.patch_manifest(manifest["run_id"], {
        "kind": "scope_selection", "features": ["score"],
    }, actor="test")
    manifest_population_stability.patch_manifest(manifest["run_id"], {
        "kind": "binning_source_choice", "value": {"mode": "repository"},
    }, actor="test")
    assert list_calls.count("psi_bins") == 0
    assert list_calls.count("coarse_bins") == 0
    assert list_calls.count("fine_bins") == 0


def test_saved_target_choice_generates_target_aware_guarded_draft_from_baseline_only():
    item_id = _snapshot(pd.DataFrame({
        "period": [1] * 40 + [2] * 40,
        "score": list(range(40)) + list(range(40, 80)),
        "outcome": [0, 1] * 40,
    }))
    item = db.query_one("dq_items", item_id=item_id)
    db.update("dq_items", {"item_id": item_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {"item_id": item_id, "table_name": "portfolio", "column_name": "outcome"},
              {"role": "Target", "dictionary_role": "target"})
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "generate_new"}}, actor="test")
    score = next(row for row in routed["feature_metadata"] if row["column"] == "score")
    assert score["bin_route"]["workflow"] == "generate_target_aware"
    draft = manifest_population_stability.create_bin_draft(run_id, "score", actor="test")
    assert draft["payload"]["creation_methodology"] == "diagnostic_2_target_aware_iv"
    assert draft["payload"]["target"] == "outcome"
    assert draft["payload"]["underflow_guard"] is True
    assert draft["payload"]["source_population_fingerprint"] == routed["baseline"]["data_fingerprint"]
    applicability = draft["payload"]["baseline_applicability"]
    assert applicability["fine_definition"]["feature"] == "score"
    assert sum(row["rows"] for row in applicability["fine_bins"]) == 40
    preview = manifest_population_stability.preview_fine_revision(
        run_id, "score", draft["artifact"]["artifact_id"], draft["payload"]["definition"])
    assert sum(row["rows"] for row in preview["bins"]) == 40


def test_psi_target_choice_governs_positive_class_in_aar_identity():
    item_id = _snapshot(pd.DataFrame({
        "period": [1] * 20 + [2] * 20,
        "score": list(range(40)),
        "outcome": [0, 1] * 20,
    }))
    item = db.query_one("dq_items", item_id=item_id)
    db.update("dq_items", {"item_id": item_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {
        "item_id": item_id, "table_name": "portfolio", "column_name": "outcome",
    }, {"role": "Target", "dictionary_role": "target"})

    fingerprints = []
    for positive_class in (1, 0):
        manifest = manifest_population_stability.build_manifest(
            item_id, actor="test", enforce_register=False,
        )
        run_id = manifest["run_id"]
        manifest_population_stability.patch_manifest(run_id, {
            "kind": "population_split", "feature": "period",
            "value": {"operator": "<=", "value": 1}, "null_policy": "exclude",
        }, actor="test")
        selected = manifest_population_stability.patch_manifest(run_id, {
            "kind": "target_choice",
            "value": {"mode": "saved_target", "positive_class": positive_class},
        }, actor="test")
        assert selected["target_choice"]["target_type"] == "binary"
        assert selected["target_choice"]["positive_class"] == str(positive_class)
        fingerprints.append(selected["target_choice"]["target_fingerprint"])

    assert fingerprints[0] != fingerprints[1]


def test_two_snapshot_target_aware_promotes_and_reuses_exact_universal_iv_chain():
    frame = pd.DataFrame({"score": list(range(80)), "outcome": [0, 1] * 40})
    baseline_id = _snapshot(frame)
    current_id = _snapshot(frame.assign(score=frame["score"] + 1))
    baseline_item = db.query_one("dq_items", item_id=baseline_id)
    db.update("dq_items", {"item_id": baseline_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": baseline_item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {"item_id": baseline_id, "table_name": "portfolio", "column_name": "outcome"},
              {"role": "Target", "dictionary_role": "target"})

    manifest = manifest_population_stability.build_manifest(
        baseline_id, actor="test", current_snapshot_id=current_id, enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    assert next(row for row in routed["feature_metadata"] if row["column"] == "score")["bin_route"]["workflow"] == "generate_target_aware"
    draft = manifest_population_stability.create_bin_draft(run_id, "score", actor="test")
    promoted = manifest_population_stability.promote_psi_iv_bins(
        run_id, "score", draft["artifact"]["artifact_id"], actor="reviewer")
    assert promoted["status"] == "promoted"

    reversed_run = manifest_population_stability.build_manifest(
        baseline_id, actor="test", current_snapshot_id=current_id, enforce_register=False)
    manifest_population_stability.patch_manifest(reversed_run["run_id"], {"kind": "target_choice",
        "value": {"mode": "saved_target", "positive_class": 0}}, actor="test")
    reversed_routed = manifest_population_stability.patch_manifest(reversed_run["run_id"], {
        "kind": "scope_selection", "features": ["score"]}, actor="test")
    reversed_route = next(row for row in reversed_routed["feature_metadata"]
                          if row["column"] == "score")["bin_route"]
    assert reversed_route["workflow"] == "generate_target_aware"
    reversed_draft = manifest_population_stability.create_bin_draft(
        reversed_run["run_id"], "score", actor="test")
    reversed_promoted = manifest_population_stability.promote_psi_iv_bins(
        reversed_run["run_id"], "score", reversed_draft["artifact"]["artifact_id"],
        actor="reviewer")
    assert reversed_promoted["revision_id"] != promoted["revision_id"]

    rerun = manifest_population_stability.build_manifest(
        baseline_id, actor="test", current_snapshot_id=current_id, enforce_register=False)
    manifest_population_stability.patch_manifest(rerun["run_id"], {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    rerouted = manifest_population_stability.patch_manifest(rerun["run_id"], {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    route = next(row for row in rerouted["feature_metadata"] if row["column"] == "score")["bin_route"]
    assert route["workflow"] == "use_universal_iv"
    assert route["artifact"]["promotion_revision_id"] == promoted["revision_id"]

    reversed_rerun = manifest_population_stability.build_manifest(
        baseline_id, actor="test", current_snapshot_id=current_id, enforce_register=False)
    manifest_population_stability.patch_manifest(reversed_rerun["run_id"], {
        "kind": "target_choice", "value": {"mode": "saved_target", "positive_class": 0},
    }, actor="test")
    reversed_rerouted = manifest_population_stability.patch_manifest(
        reversed_rerun["run_id"], {"kind": "scope_selection", "features": ["score"]},
        actor="test")
    reversed_exact = next(row for row in reversed_rerouted["feature_metadata"]
                          if row["column"] == "score")["bin_route"]
    assert reversed_exact["workflow"] == "use_universal_iv"
    assert reversed_exact["artifact"]["promotion_revision_id"] == reversed_promoted["revision_id"]
    original_baseline_frame = manifest_population_stability._baseline_frame
    manifest_population_stability._baseline_frame = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("exact universal aggregates should not rescan Baseline"))
    try:
        events = list(manifest_population_stability.generate_bin_draft_events(rerun["run_id"], actor="test"))
    finally:
        manifest_population_stability._baseline_frame = original_baseline_frame
    assert events[-1]["phase"] == "done"
    assert not events[-1]["failed"]
    from dq_diagnostics.engines.feature_target_separation.adapter import assess_snapshot
    diagnostic_2 = assess_snapshot(baseline_id, table="portfolio", feature_columns=["score"],
                                   missing_target_action="drop", actor="test")
    assert diagnostic_2.artifact_ids["fine_bins"] == [promoted["artifact_ids"]["fine_bins"]]
    assert diagnostic_2.artifact_ids["coarse_bins"] == [promoted["artifact_ids"]["coarse_bins"]]
    assert diagnostic_2.artifact_ids["iv"] == [promoted["artifact_ids"]["iv"]]


def test_numeric_psi_override_validates_csv_and_records_rescan_policy():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1.0, 2.0, 3.0, 4.0]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    with pytest.raises(Exception, match="value 2"):
        manifest_population_stability.create_numeric_bin_override(run_id, "score", "1,NA,3")
    override = manifest_population_stability.create_numeric_bin_override(
        run_id, "score", "1.5,2.5,3.5", "-999,-888", "business cut points", actor="reviewer")
    assert override["payload"]["baseline_rescan_required"] is True
    assert override["payload"]["rerun_policy"].startswith("ignore_override")
    assert override["payload"]["boundaries"] == [1.5, 2.5, 3.5]


def test_open_v1_draft_without_target_upgrades_to_automatic_target_free_route():
    item_id = _snapshot(pd.DataFrame({"period": [1, 1, 2, 2], "score": [1, 2, 3, 4]}))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    legacy = {key: value for key, value in manifest.items() if key not in {
        "governed_context", "population_method", "population_ready", "population_definition",
        "target_choice", "readiness_summary", "blockers", "ready_to_run",
    }}
    legacy["manifest_schema_version"] = 1
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": legacy})
    upgraded = manifest_population_stability.refresh_draft_scope(run_id)
    assert upgraded["manifest_schema_version"] == 3
    assert upgraded["binning_source_choice"]["status"] == "pending"
    assert upgraded["target_choice"]["status"] == "confirmed"
    assert upgraded["target_choice"]["mode"] == "target_free"
    assert upgraded["target_choice"]["automatic"] is True
    assert upgraded["governed_context"]["snapshot"]["snapshot_id"] == item_id


def test_manual_categorical_groups_use_complete_baseline_values_and_separate_specials():
    item_id = _snapshot(pd.DataFrame({
        "period": [1, 1, 1, 2, 2, 2],
        "segment": ["A", "B", "MISSING", "A", "C", "MISSING"],
    }))
    db.update("variable_inventory", {
        "item_id": item_id, "table_name": "portfolio", "column_name": "segment",
    }, {
        "classification": "categorical", "data_type": "object",
        "missing_codes_confirmed": 1, "missing_value_codes_json": ["MISSING"],
    })
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude",
    }, actor="test")
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "target_choice", "value": {"mode": "target_free"},
    }, actor="test")
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "scope_selection", "features": ["segment"],
    }, actor="test")
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "binning_source_choice", "value": {"mode": "generate_new"},
    }, actor="test")

    candidates = manifest_population_stability.candidate_values(run_id, "segment")
    assert candidates["values"] == ["A", "B"]
    with pytest.raises(Exception, match="exact baseline values"):
        manifest_population_stability.create_manual_bin_draft(run_id, "segment", [
            {"label": "Known", "values": ["A"]},
        ], actor="test")

    draft = manifest_population_stability.create_manual_bin_draft(run_id, "segment", [
        {"label": "Known", "values": ["A", "B"]},
    ], actor="test")
    assert draft["payload"]["groups"] == [{"label": "Known", "values": ["A", "B"]}]
    assert draft["payload"]["special_value_bins"] == [
        {"label": "Special value MISSING", "values": ["MISSING"]},
    ]


def test_artifact_guided_categorical_split_returns_exact_values_and_audited_preview():
    item_id = _snapshot(pd.DataFrame({
        "state": ["CA", "CA", "TX", "NY", "MISSING", None],
        "score": [1, 2, 3, 4, 5, 6],
    }))
    db.update("variable_inventory", {
        "item_id": item_id, "table_name": "portfolio", "column_name": "state",
    }, {"classification": "categorical", "data_type": "object",
        "profile_json": service._column_profile(
            pd.Series(["CA", "CA", "TX", "NY", "MISSING", None]),
            ["MISSING"], True, "categorical"),
        "missing_codes_confirmed": 1, "missing_value_codes_json": ["MISSING"]})
    artifact_ids = persist_snapshot_profile_artifacts(item_id, actor="test")
    assert artifact_ids

    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {
        "kind": "population_method", "value": {"method": "split_snapshot"},
    }, actor="test")
    options = manifest_population_stability.split_feature_options(run_id, "state")
    assert options["strategy"] == "category_groups"
    assert options["source"]["kind"] == "column_profile_artifact"
    assert {row["value"] for row in options["exact_values"]} == {"CA", "TX", "NY"}
    assert options["special_values"] == ["MISSING"]

    previewed = manifest_population_stability.patch_manifest(run_id, {
        "kind": "population_split", "feature": "state",
        "value": {"operator": "in", "values": ["CA"]}, "null_policy": "exclude",
    }, actor="test")
    # Null retention is governed: even a stale client request to exclude them
    # keeps them in the baseline review sample.
    assert previewed["population_preview"]["baseline_count"] == 3
    assert previewed["population_preview"]["current_count"] == 2
    assert previewed["population_preview"]["special_count"] == 1
    assert previewed["population_definition"]["null_policy"] == "baseline"
    assert previewed["population_definition"]["strategy"] == "category_groups"
    assert previewed["population_definition"]["profile_artifact_id"]


def test_batch_draft_generation_reads_baseline_once_and_resumes(monkeypatch):
    item_id = _snapshot(pd.DataFrame({
        "period": [1] * 20 + [2] * 20,
        "score_a": list(range(40)),
        "score_b": list(range(100, 140)),
    }))
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "target_free"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score_a", "score_b"]}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "generate_new"}}, actor="test")

    original = manifest_population_stability._baseline_frame
    calls = []

    def counted_baseline(current_manifest, columns):
        calls.append(tuple(columns))
        return original(current_manifest, columns)

    monkeypatch.setattr(manifest_population_stability, "_baseline_frame", counted_baseline)
    events = list(manifest_population_stability.generate_bin_draft_events(run_id, actor="test", max_workers=2))
    assert len(calls) == 1
    assert events[0]["phase"] == "start"
    assert events[0]["max_workers"] == 2
    assert events[0]["optimization_profile"] == {
        "mode": "deterministic", "solver_time_limit_seconds": None, "max_workers": 2,
    }
    assert len([event for event in events if event["phase"] == "progress"]) == 2
    previews = [event["preview"] for event in events if event["phase"] == "progress"]
    assert {row["feature"] for row in previews} == {"score_a", "score_b"}
    assert all(row["status"] == "completed" for row in previews)
    assert events[-1]["phase"] == "done"
    assert events[-1]["failed"] == 0
    saved = db.query_one("diag_runs", run_id=run_id)["manifest_json"]
    assert set(saved["bin_drafts"]) == {"score_a", "score_b"}

    resumed = list(manifest_population_stability.generate_bin_draft_events(run_id, actor="test", max_workers=2))
    assert len(calls) == 1
    assert [event["phase"] for event in resumed] == ["start", "done"]
    assert resumed[-1]["done"] == 2


def test_matched_iv_bins_are_reapplied_once_then_refined_from_cached_baseline_aggregates(monkeypatch):
    from analysis_runtime.contracts import stable_fingerprint
    from dq_diagnostics.engines.feature_target_separation.information_value.models import (
        BinDefinition, BinningConstraints,
    )

    item_id = _snapshot(pd.DataFrame({
        "period": [1] * 40 + [2] * 40,
        "score": list(range(80)),
        "outcome": [0, 1] * 40,
    }))
    item = db.query_one("dq_items", item_id=item_id)
    db.update("dq_items", {"item_id": item_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {"item_id": item_id, "table_name": "portfolio", "column_name": "outcome"},
              {"role": "Target", "dictionary_role": "target"})
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    selected = manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")

    constraints = BinningConstraints(create_out_of_range_guard_bins=True)
    common = {"feature": "score", "feature_type": "numeric", "target": "outcome", "target_type": "binary",
              "observed_min": 0.0, "observed_max": 39.0, "out_of_range_guard_bins": True,
              "method": "test_cached_iv", "solver_status": "OPTIMAL", "constraints": constraints}
    fine_definition = BinDefinition(**common, numeric_splits=[10.0, 20.0, 30.0])
    coarse_definition = BinDefinition(**common, numeric_splits=[20.0])
    repository = AnalysisArtifactRepository()
    snapshot = selected["baseline"]["snapshot"]
    target_fp = stable_fingerprint({"target": "outcome"})
    save_args = {"asset_id": snapshot["asset_id"], "snapshot_id": snapshot["snapshot_id"],
                 "population_fingerprint": selected["baseline"]["data_fingerprint"],
                 "target_fingerprint": target_fp, "methodology_fingerprint": "test-method",
                 "scope": "universal", "owner_id": None, "feature": "score",
                 "table": "portfolio", "run_id": "iv-source", "created_by": "test"}
    fine = repository.save({"definition": fine_definition.model_dump(mode="json"), "bins": [], "metrics": []},
                           artifact_type="fine_bins", **save_args).artifact
    coarse = repository.save({"definition": coarse_definition.model_dump(mode="json"), "bins": [], "metrics": []},
                             artifact_type="coarse_bins", source_artifact_ids=(fine.artifact_id,), **save_args).artifact
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "repository"}}, actor="test")
    score = next(row for row in routed["feature_metadata"] if row["column"] == "score")
    assert score["bin_route"]["workflow"] == "generate_target_aware"

    draft = manifest_population_stability.create_bin_draft(run_id, "score", actor="test")
    applicability = draft["payload"]["baseline_applicability"]
    assert applicability["reconciled"] is True
    assert applicability["fine_definition"]["method"] == "population_balanced_prebinning"
    assert set(applicability["coarse_definition"]["numeric_splits"]) <= set(
        applicability["fine_definition"]["numeric_splits"])
    assert applicability["evaluated_rows"] == 40
    assert sum(row["rows"] for row in applicability["fine_bins"]) == 40
    assert sum(row["rows"] for row in applicability["coarse_bins"]) == 40

    monkeypatch.setattr(manifest_population_stability, "_baseline_frame",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Baseline was rescanned")))
    generated_edges = applicability["fine_definition"]["numeric_splits"]
    edited = coarse_definition.model_copy(update={"numeric_splits": generated_edges[:2]})
    preview = manifest_population_stability.preview_fine_revision(
        run_id, "score", draft["artifact"]["artifact_id"], edited.model_dump(mode="json"))
    assert sum(row["rows"] for row in preview["bins"]) == 40
    before_save = db.query_one("diag_runs", run_id=run_id)["manifest_json"]["bin_drafts"]["score"]["artifact_id"]
    assert before_save == draft["artifact"]["artifact_id"]
    revised = manifest_population_stability.revise_from_fine(
        run_id, "score", draft["artifact"]["artifact_id"], edited.model_dump(mode="json"), actor="reviewer")
    assert revised["payload"]["baseline_applicability"]["evaluation_method"] == "cached_baseline_fine_aggregation_v1"
    assert sum(row["rows"] for row in revised["payload"]["bins"]) == 40


def test_baseline_review_orders_numeric_bins_and_verifies_exact_categorical_values():
    from dq_diagnostics.engines.feature_target_separation.information_value.models import (
        BinDefinition, BinningConstraints,
    )

    numeric = BinDefinition(feature="score", feature_type="numeric", numeric_splits=[20.0, 40.0],
                            method="test", solver_status="REVIEWED", constraints=BinningConstraints())
    frame = pd.DataFrame({"score": [50.0, 10.0, 30.0]})
    reviewed = manifest_population_stability._definition_review_rows(
        frame, "score", numeric.model_dump(mode="json"), None)
    assert [row["bin_id"] for row in reviewed["bins"]] == ["R0", "R1", "R2"]

    categorical = BinDefinition(feature="state", feature_type="categorical",
                                categorical_groups=[["CA"], ["NY"], ["TX"]],
                                method="test", solver_status="REVIEWED", constraints=BinningConstraints())
    categorical_frame = pd.DataFrame({"state": ["TX", "CA", "NY", "TX"]})
    exact = manifest_population_stability._baseline_exact_categorical_foundation(
        categorical_frame, "state", categorical.model_dump(mode="json"),
        categorical.model_dump(mode="json"), None)
    coverage = manifest_population_stability._coverage_evidence(
        categorical_frame["state"], exact["fine_definition"], exact["fine_bins"])
    assert exact["fine_foundation_policy"] == "baseline_exact_values_under_50"
    assert [group for group in exact["fine_definition"]["categorical_groups"]] == [["CA"], ["NY"], ["TX"]]
    assert coverage["exact_value_set"] is True


def test_exact_fine_match_is_auto_prepared_by_diagnostic_2_coarse_optimizer():
    from analysis_runtime.contracts import stable_fingerprint
    from dq_diagnostics.engines.feature_target_separation.information_value.models import (
        BinDefinition, BinningConstraints,
    )

    baseline_rows = 120
    item_id = _snapshot(pd.DataFrame({
        "period": [1] * baseline_rows + [2] * baseline_rows,
        "score": list(range(baseline_rows)) * 2,
        "outcome": ([0] * 30 + [1] * 30 + [0] * 30 + [1] * 30) * 2,
    }))
    item = db.query_one("dq_items", item_id=item_id)
    db.update("dq_items", {"item_id": item_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {"item_id": item_id, "table_name": "portfolio", "column_name": "outcome"},
              {"role": "Target", "dictionary_role": "target"})
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    selected = manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")

    constraints = BinningConstraints(min_bins=2, max_bins=4, monotonic_trend="none",
                                     create_out_of_range_guard_bins=True)
    fine_definition = BinDefinition(
        feature="score", feature_type="numeric", target="outcome", target_type="binary",
        observed_min=0.0, observed_max=119.0, out_of_range_guard_bins=True,
        method="population_balanced_prebinning", solver_status="GENERATED",
        constraints=constraints, numeric_splits=[float(value) for value in range(10, 120, 10)],
    )
    snapshot = selected["baseline"]["snapshot"]
    fine = AnalysisArtifactRepository().save(
        {"definition": fine_definition.model_dump(mode="json"), "bins": [], "metrics": []},
        artifact_type="fine_bins", asset_id=snapshot["asset_id"], snapshot_id=snapshot["snapshot_id"],
        population_fingerprint=selected["baseline"]["data_fingerprint"],
        target_fingerprint=stable_fingerprint({"target": "outcome"}),
        methodology_fingerprint="diagnostic-2-test", scope="universal",
        owner_id=None, feature="score", table="portfolio", run_id="iv-source",
        created_by="test",
    ).artifact
    routed = manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "repository"}}, actor="test")
    score = next(row for row in routed["feature_metadata"] if row["column"] == "score")
    assert score["bin_route"]["workflow"] == "generate_target_aware"

    events = list(manifest_population_stability.generate_bin_draft_events(run_id, actor="test", max_workers=2))
    assert events[0]["optimization_profile"] == {
        "mode": "full", "solver_time_limit_seconds": 30, "max_workers": 1,
    }
    assert events[-1]["phase"] == "done"
    assert events[-1].get("failures") == [], events[-1].get("failures")
    saved = db.query_one("diag_runs", run_id=run_id)["manifest_json"]
    draft_id = saved["bin_drafts"]["score"]["artifact_id"]
    draft_meta, payload = AnalysisArtifactRepository().get(draft_id)
    applicability = payload["baseline_applicability"]
    regular_fine = [row for row in applicability["fine_bins"] if row["kind"] == "regular"]
    regular_coarse = [row for row in applicability["coarse_bins"] if row["kind"] == "regular"]
    assert payload["creation_methodology"] == "diagnostic_2_target_aware_iv"
    assert fine.artifact_id not in payload["source_artifact_references"]
    assert AnalysisArtifactRepository().get(draft_meta.source_artifacts[0]["artifact_id"])[0].artifact_type == "coarse_bins"
    assert 2 <= len(regular_fine) <= 50
    assert 2 <= len(regular_coarse) <= 4
    assert len(regular_coarse) < len(regular_fine)
    assert sum(row["rows"] for row in regular_fine) == baseline_rows
    assert sum(row["rows"] for row in regular_coarse) == baseline_rows
    assert applicability["evaluation_method"] == "generated_baseline_fine_aggregation_v1"


def test_legacy_reviewed_psi_bins_get_a_safe_coarse_only_editor_foundation():
    from analysis_runtime.contracts import stable_fingerprint

    item_id = _snapshot(pd.DataFrame({
        "period": [1] * 20 + [2] * 20,
        "score": list(range(40)),
        "outcome": [0, 1] * 20,
    }))
    item = db.query_one("dq_items", item_id=item_id)
    db.update("dq_items", {"item_id": item_id}, {"target_variable": "outcome"})
    db.update("dq_assets", {"asset_id": item["dataset_family_id"]}, {"target_variable": "outcome"})
    db.update("variable_inventory", {"item_id": item_id, "table_name": "portfolio", "column_name": "outcome"},
              {"role": "Target", "dictionary_role": "target"})
    manifest = manifest_population_stability.build_manifest(item_id, actor="test", enforce_register=False)
    run_id = manifest["run_id"]
    manifest_population_stability.patch_manifest(run_id, {"kind": "population_split", "feature": "period",
        "value": {"operator": "<=", "value": 1}, "null_policy": "exclude"}, actor="test")
    selected = manifest_population_stability.patch_manifest(run_id, {"kind": "target_choice",
        "value": {"mode": "saved_target"}}, actor="test")
    manifest_population_stability.patch_manifest(run_id, {"kind": "scope_selection",
        "features": ["score"]}, actor="test")
    snapshot = selected["baseline"]["snapshot"]
    legacy = frozen_numeric(target="outcome", target_type="binary",
                            source_population_fingerprint=selected["baseline"]["data_fingerprint"])
    saved = AnalysisArtifactRepository().save(legacy, artifact_type="psi_bins",
        asset_id=snapshot["asset_id"], snapshot_id=snapshot["snapshot_id"],
        population_fingerprint=selected["baseline"]["data_fingerprint"],
        target_fingerprint=stable_fingerprint({"target": "outcome"}), methodology_fingerprint="legacy",
        scope="diagnostic_local", owner_id="diagnostic:14", feature="score", table="portfolio",
        run_id="legacy-psi", created_by="test").artifact
    manifest_population_stability.patch_manifest(run_id, {"kind": "binning_source_choice",
        "value": {"mode": "repository"}}, actor="test")

    review = manifest_population_stability.bin_review(run_id, "score", saved.artifact_id)
    applicability = review["applicability"]
    assert applicability["reconciled"] is True
    assert applicability["fine_count"] == applicability["coarse_count"]
    assert applicability["coarse_groups"] == [[row["bin_id"]] for row in applicability["fine_bins"] if row["kind"] == "regular"]
    edited = {**applicability["coarse_definition"], "numeric_splits": [20.0]}
    preview = manifest_population_stability.preview_fine_revision(
        run_id, "score", saved.artifact_id, edited)
    assert sum(row["rows"] for row in preview["bins"]) == 20
