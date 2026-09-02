"""End-to-end backend proof for T2-D11."""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kb
import system_db as db
from ai.v2 import service
from domains.aar.repository import AnalysisArtifactRepository
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import knowledge, manifest, runner
from dq_diagnostics.dispatch import adapter
from dq_diagnostics.register import require_executable, seed_register
from routers import diagnostics as diagnostics_router


@pytest.fixture()
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(service, "ITEM_DB_ROOT", tmp_path / "item-dbs")
    db.init_schema(); seed_register()
    suffix = uuid.uuid4().hex[:8]
    asset_id, item_id = f"asset_{suffix}", f"item_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {"asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"dir-{suffix}", "display_name": f"Directionality-{suffix}", "kind": "dataset",
        "time_basis": "period", "current_version_no": 1, "lifecycle_status": "active",
        "created_at": now, "updated_at": now})
    db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": f"Dir-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": item_id, "intent": "fresh",
        "ingest_status": "ready", "target_variable": "default_flag"})
    rng = np.random.default_rng(12)
    current_ltv = np.linspace(20, 120, 240)
    probability = 1 / (1 + np.exp(-(current_ltv - 70) / 14))
    frame = pd.DataFrame({"CURRENT_LTV": current_ltv,
                          "DSCR": np.linspace(3.0, 0.5, 240),
                          "mystery_metric": rng.normal(size=240),
                          "segment": ["Retail"] * 120 + ["SME"] * 120,
                          "default_flag": rng.binomial(1, probability)})
    service._write_table(item_id, "portfolio", frame)
    roles = {"CURRENT_LTV": "Feature", "DSCR": "Feature", "mystery_metric": "Feature",
             "segment": "Segment", "default_flag": "Target"}
    for column in frame.columns:
        db.insert("variable_inventory", {"item_id": item_id, "table_name": "portfolio",
            "column_name": column,
            "classification": "numeric" if pd.api.types.is_numeric_dtype(frame[column]) else "categorical",
            "data_type": str(frame[column].dtype), "description": column,
            "discrepancies": [], "notes": "", "role": roles[column],
            "dictionary_role": roles[column].lower(),
            "profile_json": {"total_count": len(frame), "non_null_count": len(frame),
                             "null_count": 0, "cardinality": int(frame[column].nunique())},
            "provisional": 0, "updated_at": now})
    return item_id


def test_manifest_scope_segment_run_artifacts_and_results(snapshot):
    assert require_executable(11)["diagnostic_id"] == 11
    assert adapter(11)["agent"] == "directional_monotonic_consistency_engine"
    draft = manifest.build_manifest(snapshot)
    by_feature = {row["feature"]: row for row in draft["features"]}
    assert by_feature["CURRENT_LTV"]["canonical_feature"] == "loan_to_value"
    assert by_feature["CURRENT_LTV"]["selected"] is False
    assert by_feature["mystery_metric"]["canonical_feature"] is None
    assert next(row for row in draft["reference_candidates"]
                if row["column"] == "default_flag")["is_saved_target"] is True
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_selection", "column": "DSCR"})
    assert draft["reference"]["source"] == "user-selected target substitute"
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_selection", "column": "default_flag"})
    draft = diagnostics_router.patch_diagnostic_manifest(
        draft["run_id"], diagnostics_router.ManifestPatch(
            kind="reference_orientation", orientation="HIGHER_IS_WORSE"))
    assert draft["reference"]["orientation"] == "HIGHER_IS_WORSE"
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "segment_selection", "segment_column": "segment"})
    assert draft["segment_preview"]["distinct_values"] == 2
    assert {row["value"] for row in draft["segment_preview"]["groups"]} == {"Retail", "SME"}
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection", "features": ["CURRENT_LTV", "DSCR"]})
    assert draft["ready_to_run"] is True
    done = runner.execute_now(draft["run_id"])
    assert done["rollup"]["features_completed"] >= 2
    stored_results = db.query("diag_results", run_id=draft["run_id"])
    stored_feature = next(
        row for row in stored_results
        if row["metrics_json"].get("result_kind") == "directionality_feature"
    )
    assert "evidence" not in stored_feature["metrics_json"]
    assert stored_feature["metrics_json"]["artifact_type"] == "directionality_evidence"
    results = runner.run_results(draft["run_id"])["results"]
    feature_results = [row for row in results
                       if row["metrics_json"].get("result_kind") == "directionality_feature"]
    assert {row["metrics_json"]["feature"] for row in feature_results} >= {"CURRENT_LTV", "DSCR"}
    ltv_result = next(row for row in feature_results
                      if row["metrics_json"]["feature"] == "CURRENT_LTV")
    ltv = ltv_result["metrics_json"]
    assert ltv["evidence"]["observed_direction"] == "INCREASING"
    assert len(ltv["segments"]) == 2
    artifact = db.query_one("analysis_artifacts", artifact_id=ltv["artifact_id"])
    assert artifact["artifact_type"] == "directionality_evidence"
    metadata, artifact_payload = AnalysisArtifactRepository().get(ltv["artifact_id"])
    assert metadata.run_id == draft["run_id"]
    assert artifact_payload["overall"] == ltv["evidence"]
    assert artifact_payload["comparison"] == ltv["comparison"]
    assert artifact_payload["methodology"] == ltv["methodology"]
    report, report_artifact, report_reused = runner.report_payload(draft["run_id"])
    assert report_reused is False
    assert report_artifact.artifact_type == "directionality_report"
    assert report["summary"]["features_completed"] >= 2
    assert report["inference_disclosure"]["llm_used"] is False
    assert report["inference_disclosure"]["llm_call_count"] == 0
    assert report["introduction"].startswith("This diagnostic compares")
    assert report["executor"]
    assert report["requested_bins"] == 5
    assert {row["feature"] for row in report["features"]} >= {"CURRENT_LTV", "DSCR"}
    assert all(row["expected_rationale"] for row in report["features"])
    report_text = runner.report_text(draft["run_id"])
    assert "1. ANALYSIS OVERVIEW" in report_text
    assert "Direction rationales and supporting metrics" in report_text
    assert "RECORDED USER / SYSTEM DECISIONS" not in report_text
    pdf, report_metadata = runner.report_document(draft["run_id"])
    assert pdf
    assert report_metadata["report_artifact"]["artifact_type"] == "directionality_report"
    assert report_metadata["report_artifact"]["reused"] is True
    assert not ltv_result["findings"]
    override = diagnostics_router.promote_diagnostic_result(
        ltv_result["result_id"], diagnostics_router.PsiResultPromotionIn(
            reason="SME override: investigate this aligned result through RCA."))
    assert override["issue_row_id"]


def test_directionality_kb_is_visible_as_one_approved_system_document(snapshot):
    first = knowledge.seed_document()
    second = knowledge.seed_document()

    assert first["inserted"] is True
    assert second == {**first, "inserted": False}
    document = db.query_one(
        "kb_documents", document_id=knowledge.SYSTEM_DOCUMENT_ID,
    )
    version = db.query_one(
        "kb_document_versions", version_id=knowledge.SYSTEM_VERSION_ID,
    )
    assert document["title"] == (
        "Test 2, Diagnostic 11 — Expected Risk Direction Knowledge Base"
    )
    assert version["review_state"] == "approved"
    assert version["converter_name"] == "system-reference-document"
    assert version["conversion_report_json"]["kb_version"] == "0.3"
    assert "## Credit quality score (`credit_quality_score`)" in version["converted_markdown"]
    assert "Higher feature value → Lower risk" in version["converted_markdown"]
    assert "credit_risk_abbreviations" not in version["converted_markdown"]
    assert not db.query("kb_rules", document_id=knowledge.SYSTEM_DOCUMENT_ID)
    listed = kb.list_documents("bootstrap")
    assert [row["document_id"] for row in listed] == [knowledge.SYSTEM_DOCUMENT_ID]


def test_llm_failure_preserves_candidates_and_manual_fallback(snapshot, monkeypatch):
    draft = manifest.build_manifest(snapshot)
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_orientation", "orientation": "HIGHER_IS_WORSE"})
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection", "features": ["mystery_metric"]})
    monkeypatch.setattr(manifest, "adjudicate", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        TimeoutError("model unavailable")))
    updated = manifest.patch_manifest(draft["run_id"], {
        "kind": "semantic_adjudication", "feature": "mystery_metric"})
    row = next(item for item in updated["features"] if item["feature"] == "mystery_metric")
    assert row["adjudication"]["status"] == "unavailable"
    assert row["canonical_feature"] is None
    assert row["expected_direction"] is None
    assert len(row["candidates"]) == 10
    updated = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_classification", "feature": "mystery_metric",
        "expected_direction": "NO_CLEAR_DIRECTION",
        "rationale": "Reviewed directly; no supplied KB concept is economically appropriate.",
        "include_in_kb": False})
    row = next(item for item in updated["features"] if item["feature"] == "mystery_metric")
    assert row["classification_source"] == "USER_CONFIRMED"
    assert row["selected"] is True
    runner.execute_now(draft["run_id"])
    report, _artifact, _reused = runner.report_payload(draft["run_id"])
    assert report["inference_disclosure"]["llm_used"] is True
    assert report["inference_disclosure"]["llm_call_count"] == 1
    assert any(event["status"] == "failed"
               for event in report["inference_disclosure"]["events"])
    assert report["ai_reviews"][0]["variable_searched"] == "mystery_metric"
    assert report["ai_reviews"][0]["ai_outcome"] == "NO_RESULT"
    assert report["ai_reviews"][0]["human_review_confirmed"] is True
    assert report["ai_reviews"][0]["confirmed_by"] == "system"
    assert "Reviewed directly" in report["ai_reviews"][0]["final_rationale"]
    assert report["features"][0]["classification_source"] == "USER_CONFIRMED"
    assert any(row["kind"] == "role_verification_change"
               for row in report["decision_actions"])


def test_contextual_finding_can_be_promoted_and_tracked_as_an_issue(snapshot):
    draft = manifest.build_manifest(snapshot)
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_orientation", "orientation": "HIGHER_IS_WORSE"})
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection", "features": ["mystery_metric"]})
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_classification", "feature": "mystery_metric",
        "expected_direction": "INCREASING",
        "rationale": "SME expects higher values to indicate higher risk.",
        "include_in_kb": False})
    assert draft["ready_to_run"] is True
    runner.execute_now(draft["run_id"])

    result = next(row for row in runner.run_results(draft["run_id"])["results"]
                  if (row.get("metrics_json") or {}).get("result_kind")
                  == "directionality_feature")
    finding = result["findings"][0]
    assert finding["review_state"] == "open"
    promoted = diagnostics_router.disposition_finding(
        finding["finding_id"], diagnostics_router.DispositionIn(
            action="confirm_issue", reason="Escalate the unexpected empirical relationship."))
    assert promoted["issue_row_id"]

    refreshed = next(row for row in runner.run_results(draft["run_id"])["results"]
                     if (row.get("metrics_json") or {}).get("result_kind")
                     == "directionality_feature")
    tracked = refreshed["findings"][0]
    assert tracked["review_state"] == "confirmed"
    assert tracked["existing_issue"]["issue_row_id"] == promoted["issue_row_id"]


def _queue_ltv_knowledge_proposal(snapshot: str, direction: str, rationale: str) -> dict:
    draft = manifest.build_manifest(snapshot)
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_orientation", "orientation": "HIGHER_IS_WORSE"})
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection", "features": ["CURRENT_LTV"]})
    return manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_classification", "feature": "CURRENT_LTV",
        "expected_direction": direction, "canonical_feature": "loan_to_cost",
        "representation_orientation": "SAME", "rationale": rationale,
        "include_in_kb": True,
    })


def test_exact_kb_decision_is_persisted_and_an_unchanged_proposal_is_blocked(snapshot):
    draft = manifest.build_manifest(snapshot)
    row = next(item for item in draft["features"] if item["feature"] == "CURRENT_LTV")
    assert row["governed_exact_decision"] == {
        "kb_version": "0.3",
        "canonical_feature": "loan_to_value",
        "representation_orientation": "SAME",
        "expected_direction": "INCREASING",
    }

    with pytest.raises(
        manifest.ManifestError,
        match=r"already covered by KB v0\.3; no Knowledge Base proposal is needed",
    ):
        manifest.patch_manifest(draft["run_id"], {
            "kind": "feature_classification", "feature": "CURRENT_LTV",
            "expected_direction": "INCREASING", "canonical_feature": "loan_to_value",
            "representation_orientation": "SAME",
            "rationale": "The exact governed direction remains appropriate.",
            "include_in_kb": True,
        })

    stored = db.query_one("diag_runs", run_id=draft["run_id"])["manifest_json"]
    stored_row = next(item for item in stored["features"]
                      if item["feature"] == "CURRENT_LTV")
    assert stored_row["classification_source"] == "KB_V0_3_EXACT"
    assert stored_row["kb_proposal"] is None
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")


def test_exact_kb_match_can_queue_a_genuine_decision_challenge(snapshot):
    queued = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "This source variable represents loan-to-cost instead."
    )
    row = next(item for item in queued["features"] if item["feature"] == "CURRENT_LTV")
    assert row["canonical_feature"] == "loan_to_cost"
    assert row["kb_proposal"]["lifecycle_state"] == "queued_for_run"
    assert row["governed_exact_decision"]["canonical_feature"] == "loan_to_value"


def test_refresh_backfills_exact_decision_evidence_for_an_older_draft(snapshot):
    draft = manifest.build_manifest(snapshot)
    stored = db.query_one("diag_runs", run_id=draft["run_id"])["manifest_json"]
    row = next(item for item in stored["features"] if item["feature"] == "CURRENT_LTV")
    row.pop("governed_exact_decision")
    db.update("diag_runs", {"run_id": draft["run_id"]}, {"manifest_json": stored})

    refreshed = manifest.refresh_draft_scope(draft["run_id"])
    row = next(item for item in refreshed["features"] if item["feature"] == "CURRENT_LTV")
    assert row["governed_exact_decision"] == {
        "kb_version": "0.3",
        "canonical_feature": "loan_to_value",
        "representation_orientation": "SAME",
        "expected_direction": "INCREASING",
    }


def test_freeze_suppresses_a_preexisting_unchanged_exact_kb_intent(snapshot):
    draft = manifest.build_manifest(snapshot)
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_orientation", "orientation": "HIGHER_IS_WORSE"})
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection", "features": ["CURRENT_LTV"]})
    stored = db.query_one("diag_runs", run_id=draft["run_id"])["manifest_json"]
    row = next(item for item in stored["features"] if item["feature"] == "CURRENT_LTV")
    row["kb_proposal"] = {
        "requested": True, "materialized": False,
        "lifecycle_state": "queued_for_run", "requested_by": "legacy-actor",
    }
    db.update("diag_runs", {"run_id": draft["run_id"]}, {"manifest_json": stored})

    frozen = manifest.freeze(draft["run_id"], actor="runner")
    row = next(item for item in frozen["features"] if item["feature"] == "CURRENT_LTV")
    assert row["kb_proposal"] == {
        "requested": False,
        "materialized": False,
        "suppressed": True,
        "lifecycle_state": "not_required",
        "proposal_action": "already_covered_by_governed_kb",
        "requested_by": "legacy-actor",
        "reason": "The unchanged decision is already covered by KB v0.3; no proposal was created.",
    }
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")
    assert not db.query("kb_rule_proposal_evidence")


def test_kb_proposal_intent_is_discardable_until_the_run_freezes(snapshot):
    queued = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "Higher leverage reduces collateral protection.")
    feature = next(row for row in queued["features"] if row["feature"] == "CURRENT_LTV")
    assert feature["kb_proposal"] == {
        "requested": True, "materialized": False,
        "lifecycle_state": "queued_for_run", "requested_by": "system",
    }
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")
    assert manifest.discard_drafts(snapshot, actor="tester") == 1
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")


def test_failed_freeze_rolls_back_the_entire_kb_proposal_handoff(snapshot, monkeypatch):
    queued = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "Higher leverage reduces collateral protection."
    )
    original_update = db.update

    def fail_final_run_transition(table, where, changes, **kwargs):
        if table == "diag_runs" and changes.get("status") == "running":
            raise RuntimeError("simulated final run transition failure")
        return original_update(table, where, changes, **kwargs)

    monkeypatch.setattr(db, "update", fail_final_run_transition)
    with pytest.raises(RuntimeError, match="simulated final run transition failure"):
        manifest.freeze(queued["run_id"], actor="analyst")

    assert db.query_one("diag_runs", run_id=queued["run_id"])["status"] == "draft"
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")
    assert not db.query("kb_rule_proposal_evidence")


def test_direct_draft_stream_cannot_freeze_or_materialize_kb_proposals(snapshot):
    queued = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "Higher leverage reduces collateral protection."
    )

    app = FastAPI()
    app.include_router(diagnostics_router.router, prefix="/api/v2")
    response = TestClient(app).get(
        f"/api/v2/diagnostics/runs/{queued['run_id']}/stream"
    )

    assert response.status_code == 409
    assert "authenticated run action" in response.json()["detail"]
    assert db.query_one("diag_runs", run_id=queued["run_id"])["status"] == "draft"
    assert not db.query("kb_rules", proposal_kind="t2_d11_expected_direction")
    assert not db.query("kb_rule_proposal_evidence")


def test_repeated_kb_proposals_reuse_one_open_reviewer_candidate(snapshot):
    first = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "Higher leverage reduces collateral protection.")
    frozen_first = manifest.freeze(first["run_id"], actor="first-reviewer")
    first_feature = next(row for row in frozen_first["features"]
                         if row["feature"] == "CURRENT_LTV")
    rule_id = first_feature["kb_proposal"]["rule_id"]
    assert first_feature["kb_proposal"]["proposal_action"] == "created"

    second = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "A second run confirms the same economic direction.")
    frozen_second = manifest.freeze(second["run_id"], actor="second-reviewer")
    second_feature = next(row for row in frozen_second["features"]
                          if row["feature"] == "CURRENT_LTV")
    assert second_feature["kb_proposal"]["rule_id"] == rule_id
    assert second_feature["kb_proposal"]["proposal_action"] == "reused"

    changed = _queue_ltv_knowledge_proposal(
        snapshot, "DECREASING", "The governed source definition supports the opposite direction.")
    frozen_changed = manifest.freeze(changed["run_id"], actor="third-reviewer")
    changed_feature = next(row for row in frozen_changed["features"]
                           if row["feature"] == "CURRENT_LTV")
    assert changed_feature["kb_proposal"]["rule_id"] == rule_id
    assert changed_feature["kb_proposal"]["proposal_action"] == "updated"

    open_rules = db.query(
        "kb_rules", proposal_kind="t2_d11_expected_direction", lifecycle_state="draft")
    assert [rule["rule_id"] for rule in open_rules] == [rule_id]
    assert "Expected risk direction: DECREASING" in open_rules[0]["rule_text"]
    evidence = db.query("kb_rule_proposal_evidence", rule_id=rule_id)
    assert len(evidence) == 3
    assert {row["source_run_id"] for row in evidence} == {
        first["run_id"], second["run_id"], changed["run_id"],
    }
    assert next(row for row in evidence if row["source_run_id"] == changed["run_id"])[
        "evidence_state"
    ] == "supporting"
    assert all(
        row["evidence_state"] == "conflicting"
        for row in evidence if row["source_run_id"] != changed["run_id"]
    )
    reviewer_candidate = next(
        row for row in kb.list_learning_candidates("bootstrap")
        if row["candidate_id"] == rule_id
    )
    assert len(reviewer_candidate["proposal_evidence"]) == 3


def test_pending_review_conflicts_stay_single_and_publication_governs_revisions(snapshot):
    first = _queue_ltv_knowledge_proposal(
        snapshot, "INCREASING", "Higher leverage reduces collateral protection.")
    frozen_first = manifest.freeze(first["run_id"], actor="analyst")
    first_feature = next(row for row in frozen_first["features"]
                         if row["feature"] == "CURRENT_LTV")
    rule_id = first_feature["kb_proposal"]["rule_id"]
    db.update("kb_rules", {"rule_id": rule_id}, {"lifecycle_state": "pending_review"})

    conflict = _queue_ltv_knowledge_proposal(
        snapshot, "DECREASING", "A later run proposes a conflicting economic direction.")
    frozen_conflict = manifest.freeze(conflict["run_id"], actor="challenger")
    conflict_feature = next(row for row in frozen_conflict["features"]
                            if row["feature"] == "CURRENT_LTV")
    assert conflict_feature["kb_proposal"]["rule_id"] == rule_id
    assert conflict_feature["kb_proposal"]["proposal_action"] == "conflict_retained"
    assert len(db.query("kb_rules", proposal_kind="t2_d11_expected_direction")) == 1
    conflict_evidence = db.query_one(
        "kb_rule_proposal_evidence", rule_id=rule_id, source_run_id=conflict["run_id"])
    assert conflict_evidence["evidence_state"] == "conflicting"

    kb.publish_rule(
        "bootstrap", rule_id, "kb-reviewer", ["kb_reviewer"], "domain_fact",
        related_tables=["portfolio"], related_columns=["CURRENT_LTV"],
    )
    revision = _queue_ltv_knowledge_proposal(
        snapshot, "DECREASING", "Reviewer consideration is requested as a governed revision.")
    frozen_revision = manifest.freeze(revision["run_id"], actor="analyst")
    revision_feature = next(row for row in frozen_revision["features"]
                            if row["feature"] == "CURRENT_LTV")
    revision_id = revision_feature["kb_proposal"]["rule_id"]
    assert revision_id != rule_id
    assert db.query_one("kb_rules", rule_id=revision_id)["based_on_rule_id"] == rule_id
    assert db.query_one("kb_rules", rule_id=rule_id)["lifecycle_state"] == "published"

    kb.publish_rule(
        "bootstrap", revision_id, "kb-reviewer", ["kb_reviewer"], "domain_fact",
        related_tables=["portfolio"], related_columns=["CURRENT_LTV"],
    )
    predecessor = db.query_one("kb_rules", rule_id=rule_id)
    assert predecessor["lifecycle_state"] == "superseded"
    assert predecessor["superseded_by_rule_id"] == revision_id
    assert db.query_one("kb_rules", rule_id=revision_id)["lifecycle_state"] == "published"


def test_launch_discovers_one_saved_draft_and_start_afresh_discards_it(snapshot):
    draft = diagnostics_router.build_diagnostic_manifest(
        snapshot, diagnostics_router.ManifestIn(diagnostic_id=11))
    updated = diagnostics_router.patch_diagnostic_manifest(
        draft["run_id"], diagnostics_router.ManifestPatch(
            kind="reference_orientation", orientation="HIGHER_IS_WORSE"))
    updated = diagnostics_router.patch_diagnostic_manifest(
        draft["run_id"], diagnostics_router.ManifestPatch(
            kind="feature_selection", features=["CURRENT_LTV", "DSCR"]))
    saved = diagnostics_router.resumable_diagnostic_draft(snapshot, 11)["draft"]
    assert saved["run_id"] == draft["run_id"]
    assert saved["completed_steps"] == 3
    assert saved["selected_feature_count"] == 2
    assert updated["reference"]["orientation"] == "HIGHER_IS_WORSE"
    relaunched = diagnostics_router.build_diagnostic_manifest(
        snapshot, diagnostics_router.ManifestIn(diagnostic_id=11))
    assert relaunched["run_id"] == draft["run_id"]
    fresh = diagnostics_router.build_diagnostic_manifest(
        snapshot, diagnostics_router.ManifestIn(diagnostic_id=11, start_afresh=True))
    assert fresh["run_id"] != draft["run_id"]
    assert db.query_one("diag_runs", run_id=draft["run_id"])["status"] == "discarded"
    active = db.query("diag_runs", item_id=snapshot, diagnostic_id=11, status="draft")
    assert [row["run_id"] for row in active] == [fresh["run_id"]]
    duplicate_id = "drun_000000000000"
    duplicate_manifest = {**fresh, "run_id": duplicate_id}
    db.insert("diag_runs", {"run_id": duplicate_id, "item_id": snapshot,
        "diagnostic_id": 11, "manifest_json": duplicate_manifest, "status": "draft",
        "engine_versions_json": {"directionality": "0.1.0"},
        "created_at": "2000-01-01T00:00:00", "started_at": None, "finished_at": None})
    assert manifest.latest_draft(snapshot)["run_id"] == fresh["run_id"]
    assert db.query_one("diag_runs", run_id=duplicate_id)["status"] == "discarded"
    assert len(db.query("diag_runs", item_id=snapshot, diagnostic_id=11, status="draft")) == 1


def test_authenticated_principal_owns_d11_proposal_and_cross_tenant_writes_are_hidden(
    snapshot, monkeypatch,
):
    principals = {
        "Bearer tenant-a": {
            "username": "analyst-a", "tenant_id": "tenant-a", "authz_roles": [],
        },
        "Bearer tenant-b": {
            "username": "analyst-b", "tenant_id": "tenant-b", "authz_roles": [],
        },
    }
    monkeypatch.setattr(
        diagnostics_router.tenancy, "resolve_principal",
        lambda authorization: principals[authorization],
    )
    authorization = "Bearer tenant-a"
    draft = diagnostics_router.build_diagnostic_manifest(
        snapshot, diagnostics_router.ManifestIn(diagnostic_id=11), authorization,
    )
    assert draft["tenant_id"] == "tenant-a"
    assert draft["created_by"] == "analyst-a"
    for patch in (
        diagnostics_router.ManifestPatch(
            kind="reference_orientation", orientation="HIGHER_IS_WORSE",
        ),
        diagnostics_router.ManifestPatch(
            kind="feature_selection", features=["CURRENT_LTV"],
        ),
        diagnostics_router.ManifestPatch(
            kind="feature_classification", feature="CURRENT_LTV",
            expected_direction="INCREASING", canonical_feature="loan_to_cost",
            representation_orientation="SAME",
            rationale="Higher leverage reduces collateral protection.",
            include_in_kb=True,
        ),
    ):
        draft = diagnostics_router.patch_diagnostic_manifest(
            draft["run_id"], patch, authorization,
        )
    queued = next(row for row in draft["features"] if row["feature"] == "CURRENT_LTV")
    assert queued["kb_proposal"]["requested_by"] == "analyst-a"

    started = diagnostics_router.run_diagnostic_manifest(
        draft["run_id"], diagnostics_router.RunIn(stream=True), authorization,
    )
    assert started["status"] == "running"
    frozen = db.query_one("diag_runs", run_id=draft["run_id"])["manifest_json"]
    assert frozen["frozen_by"] == "analyst-a"
    proposal = next(
        row for row in db.query("kb_rules", proposal_kind="t2_d11_expected_direction")
        if row["tenant_id"] == "tenant-a"
    )
    evidence = db.query_one("kb_rule_proposal_evidence", rule_id=proposal["rule_id"])
    assert evidence["actor"] == "analyst-a"
    assert not db.query(
        "kb_rules", tenant_id="bootstrap", proposal_kind="t2_d11_expected_direction",
    )

    with pytest.raises(Exception) as denied:
        diagnostics_router.patch_diagnostic_manifest(
            draft["run_id"], diagnostics_router.ManifestPatch(
                kind="threshold_tune", key="corr_floor", value=0.25,
            ), "Bearer tenant-b",
        )
    assert getattr(denied.value, "status_code", None) == 404
