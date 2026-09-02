"""Backend integration proofs for Test 2, Diagnostic 6."""
from __future__ import annotations

import uuid

import pandas as pd
import pytest

import system_db as db
from ai.v2 import service
from domains.aar.repository import AnalysisArtifactRepository
from dq_diagnostics import manifest_row_completeness, runner_row_completeness
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
        "alias": f"row-{suffix}", "display_name": f"Row-{suffix}", "kind": "dataset",
        "time_basis": "period", "current_version_no": 1, "lifecycle_status": "active",
        "created_at": now, "updated_at": now})
    db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": f"Row-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": item_id, "intent": "fresh",
        "ingest_status": "ready", "period_column": "reporting_period"})
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "B", "B", "B", "B"],
        "reporting_period": ["2025-01", "2025-03", "2025-01", "2025-02", "2025-02", "2025-03"],
        "segment": ["Retail", "Retail", "SME", "SME", "SME", "SME"],
    })
    service._write_table(item_id, "portfolio", frame)
    roles = {"facility_id": "Identifier", "reporting_period": "Period", "segment": "Segment"}
    for column in frame.columns:
        db.insert("variable_inventory", {"item_id": item_id, "table_name": "portfolio",
            "column_name": column, "classification": "categorical", "data_type": str(frame[column].dtype),
            "description": column, "discrepancies": [], "notes": "", "role": roles[column],
            "dictionary_role": roles[column].lower(),
            "profile_json": {"total_count": len(frame), "non_null_count": len(frame),
                             "null_count": 0, "cardinality": int(frame[column].nunique()),
                             "calculation_method": "exact"},
            "provisional": 0, "updated_at": now})
    return item_id


def test_manifest_freeze_run_and_external_report(snapshot):
    assert require_executable(6)["diagnostic_id"] == 6
    manifest = manifest_row_completeness.build_manifest(snapshot)
    assert manifest["roles"]["facility_id"]["column"] == "facility_id"
    assert manifest["roles"]["period"]["column"] == "reporting_period"
    assert manifest["roles"]["segment"]["column"] == "segment"
    assert manifest["table_options"] == [{
        "table": "portfolio", "row_count": 6, "column_count": 3,
        "facility_candidate": True, "period_candidate": True, "segment_candidate": True,
    }]
    assert manifest["inference_disclosure"]["llm_call_count"] == 0
    assert manifest["kb"]["version_id"] == "kbver_t2d6_row_completeness_v1"
    assert len(manifest["kb"]["package_hash"]) == 64
    installed = db.query("kb_rules", version_id=manifest["kb"]["version_id"])
    assert len(installed) == 6
    assert {rule["lifecycle_state"] for rule in installed} == {"published"}
    assert {rule["binding_status"] for rule in installed} == {"bound"}

    done = runner_row_completeness.execute_now(manifest["run_id"])
    assert done["verdict"] == "violation"
    assert done["rollup"]["VIOLATION"] >= 2
    assert done["review_required"] == done["rollup"]["VIOLATION"]
    assert done["issues_created"] == 0
    findings = db.query("diag_findings", run_id=manifest["run_id"])
    assert len(findings) == 6
    assert all(finding["review_state"] == (
        "open" if finding["outcome"] == "VIOLATION" else "not_required"
    ) for finding in findings)
    assert db.query("issues_v2", run_id=manifest["run_id"], diagnostic_id=6) == []
    metrics = db.query("diag_results", run_id=manifest["run_id"])[0]["metrics_json"]
    assert metrics["reconciliation_artifact"]["reused"] is False
    assert metrics["knowledge_provenance"]["version_id"] == manifest["kb"]["version_id"]
    payload, report_artifact, reused = runner_row_completeness.report_payload(manifest["run_id"])
    assert reused is False
    assert report_artifact.artifact_type == "row_completeness_report"
    assert payload.safe_sharing.facility_identifiers_masked is True
    assert payload.finding_workflow_summary["awaiting_review"] == done["rollup"]["VIOLATION"]
    assert len(payload.finding_actions) == done["rollup"]["VIOLATION"]
    assert "No LLM calls" in payload.current_journey_inference_disclosure.statement
    assert payload.provenance["knowledge"]["package_hash"] == manifest["kb"]["package_hash"]
    report_text = runner_row_completeness.report_text(manifest["run_id"])
    assert "1. ANALYSIS OVERVIEW" in report_text
    assert "3. RULE RESULTS" in report_text
    assert "4. ROW-LEVEL FAILURE EVIDENCE" in report_text
    assert "Coverage is a principal continuity metric, not the sole rationale" in report_text
    assert "showing 1 of 1 retained evidence rows" in report_text
    assert "LLM influenced empirical verdict: No" in report_text
    assert f"Knowledge version: {manifest['kb']['version_id']}" in report_text
    pdf = runner_row_completeness.report_pdf(manifest["run_id"])
    assert pdf.startswith(b"%PDF")
    response = diagnostics_router.diagnostic_run_report(manifest["run_id"])
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].endswith(f'row-completeness-{manifest["run_id"]}.pdf"')
    assert response.headers["x-analysis-artifact-id"]


def test_findings_require_explicit_idempotent_review_before_issue_creation(snapshot):
    manifest = manifest_row_completeness.build_manifest(snapshot)
    runner_row_completeness.execute_now(manifest["run_id"])
    violations = db.query("diag_findings", run_id=manifest["run_id"],
                          outcome="VIOLATION", order_by="seq")
    assert len(violations) >= 2

    promoted = diagnostics_router.disposition_finding(
        violations[0]["finding_id"], diagnostics_router.DispositionIn(
            action="confirm_issue", reason="Reviewed evidence; remediation ownership is required."))
    assert promoted["review_state"] == "confirmed"
    assert promoted["issue_row_id"]
    issue = db.query_one("issues_v2", finding_id=violations[0]["finding_id"])
    assert issue["issue_row_id"] == promoted["issue_row_id"]
    assert issue["column_details_json"][0]["promotion_rationale"] == \
        "Reviewed evidence; remediation ownership is required."
    assert len(db.query("diag_dispositions", target_type="finding",
                        target_id=violations[0]["finding_id"])) == 1

    replayed = diagnostics_router.disposition_finding(
        violations[0]["finding_id"], diagnostics_router.DispositionIn(
            action="confirm_issue", reason="Repeated request after a client retry."))
    assert replayed["idempotent"] is True
    assert replayed["issue_row_id"] == promoted["issue_row_id"]
    assert len(db.query("issues_v2", finding_id=violations[0]["finding_id"])) == 1
    assert len(db.query("diag_dispositions", target_type="finding",
                        target_id=violations[0]["finding_id"])) == 1

    dismissed = diagnostics_router.disposition_finding(
        violations[1]["finding_id"], diagnostics_router.DispositionIn(
            action="dismiss", reason="Reviewed and accepted as an explained source exception."))
    assert dismissed["review_state"] == "dismissed"
    assert dismissed["issue_row_id"] is None
    assert db.query_one("issues_v2", finding_id=violations[1]["finding_id"]) is None
    assert db.query_one("diag_findings", finding_id=violations[1]["finding_id"])[
        "review_state"] == "dismissed"

    replay = runner_row_completeness.execute_now(manifest["run_id"])
    assert replay["review_required"] == len(violations) - 2
    assert replay["issues_created"] == 1


def test_exact_reconciliation_reuse_preserves_source_and_current_provenance(snapshot):
    first = manifest_row_completeness.build_manifest(snapshot)
    runner_row_completeness.execute_now(first["run_id"])
    first_metrics = db.query("diag_results", run_id=first["run_id"])[0]["metrics_json"]
    first_artifact = first_metrics["reconciliation_artifact"]

    second = manifest_row_completeness.build_manifest(snapshot)
    runner_row_completeness.execute_now(second["run_id"])
    second_metrics = db.query("diag_results", run_id=second["run_id"])[0]["metrics_json"]
    second_artifact = second_metrics["reconciliation_artifact"]
    assert second_artifact["artifact_id"] == first_artifact["artifact_id"]
    assert second_artifact["reused"] is True
    assert second_metrics["structured_result"]["origin_run_id"] == first["run_id"]
    assert second_metrics["current_journey_inference_disclosure"]["event_set_hash"] != \
           second_metrics["source_calculation_inference_disclosure"]["event_set_hash"]
    response = runner_row_completeness.run_results(second["run_id"])["row_completeness_result"]
    assert response["reconciliation_artifact"]["reused"] is True
    assert response["result"]["origin_run_id"] == first["run_id"]
    assert len(AnalysisArtifactRepository().list(
        snapshot_id=snapshot, artifact_type="row_completeness_reconciliation", status="active")) == 1


def test_scope_edits_are_validated_and_dispatch_is_registered(snapshot):
    manifest = manifest_row_completeness.build_manifest(snapshot)
    run_id = manifest["run_id"]
    updated = manifest_row_completeness.patch_manifest(run_id, {
        "kind": "role_override", "role": "segment", "column": ""})
    assert updated["roles"]["segment"] is None
    updated = manifest_row_completeness.patch_manifest(run_id, {
        "kind": "threshold_tune", "key": "continuity_floor", "value": 0.9})
    assert updated["configuration"]["continuity_floor"]["value"] == 0.9
    updated = manifest_row_completeness.patch_manifest(run_id, {
        "kind": "parameter_tune", "key": "reporting_grain", "value": "monthly"})
    assert updated["configuration"]["reporting_grain"]["value"] == "monthly"
    assert adapter(6)["agent"] == "row_completeness_engine"


def test_opt_in_llm_role_review_is_audited_and_never_applies_mapping(snapshot, monkeypatch):
    manifest = manifest_row_completeness.build_manifest(snapshot)
    calls = {}

    class Response:
        id = "provider-request-1"
        usage = type("Usage", (), {"prompt_tokens": 20, "completion_tokens": 8})()
        choices = [type("Choice", (), {"message": type("Message", (), {"content": (
            '{"suggestions":{"facility_id":"facility_id",'
            '"period":"reporting_period","segment":"segment"}}')})()})()]

    class Completions:
        @staticmethod
        def create(**kwargs):
            calls.update(kwargs)
            return Response()

    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    monkeypatch.setattr(manifest_row_completeness.control_plane, "resolve", lambda _key: {
        "house": "test-provider", "model": "test-model", "temperature": 0})
    monkeypatch.setattr(manifest_row_completeness.llm, "get_model", lambda: "test-deployment")
    monkeypatch.setattr(manifest_row_completeness.llm, "get_client", lambda _house: client)

    updated = manifest_row_completeness.patch_manifest(manifest["run_id"], {
        "kind": "role_verification_change", "enabled": True})
    disclosure = updated["inference_disclosure"]
    assert disclosure["llm_call_count"] == 1
    assert disclosure["llm_used"] is True
    assert disclosure["verdict_influenced_by_llm"] is False
    assert disclosure["events"][-1]["row_level_data_included"] is False
    assert updated["role_verification"]["mapping_applied"] is False
    assert updated["role_verification"]["model"] == "test-deployment"
    assert calls["model"] == "test-deployment"
    assert calls["max_completion_tokens"] == 500
    assert "max_tokens" not in calls
    assert updated["roles"] == manifest["roles"]

    runner_row_completeness.execute_now(manifest["run_id"])
    report_text = runner_row_completeness.report_text(manifest["run_id"])
    assert "AI suggestion: facility_id=facility_id; period=reporting_period; segment=segment" in report_text
    assert "Final applied mapping: facility_id=facility_id; period=reporting_period; segment=segment" in report_text
    assert "Row-level data included: No" in report_text


def test_failed_advisory_review_is_reported_without_blocking_deterministic_run(snapshot, monkeypatch):
    manifest = manifest_row_completeness.build_manifest(snapshot)
    monkeypatch.setattr(manifest_row_completeness.control_plane, "resolve", lambda _key: {
        "house": "test-provider", "model": "test-model", "temperature": 0})
    monkeypatch.setattr(manifest_row_completeness.llm, "get_client",
                        lambda _house: (_ for _ in ()).throw(RuntimeError("provider unavailable")))

    updated = manifest_row_completeness.patch_manifest(manifest["run_id"], {
        "kind": "role_verification_change", "enabled": True})

    assert updated["role_verification"]["status"] == "failed"
    assert updated["role_verification"]["mapping_applied"] is False
    assert updated["roles"] == manifest["roles"]
    assert updated["inference_disclosure"]["llm_call_count"] == 1
    assert updated["inference_disclosure"]["events"][-1]["status"] == "failed"
    assert runner_row_completeness.execute_now(manifest["run_id"])["verdict"] == "violation"
