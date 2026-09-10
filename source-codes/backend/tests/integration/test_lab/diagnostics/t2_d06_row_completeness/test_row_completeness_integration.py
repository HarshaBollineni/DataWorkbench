"""Backend integration proofs for Test 2, Diagnostic 6."""
from __future__ import annotations

import uuid
from contextlib import nullcontext

import pandas as pd
import pytest
from fastapi import HTTPException

import system_db as db
from ai.v2 import service
from domains.aar.repository import AnalysisArtifactRepository
from dq_diagnostics import manifest_row_completeness, runner_row_completeness
from dq_diagnostics.dispatch import adapter
from dq_diagnostics.register import require_executable, seed_register
from routers import diagnostics as diagnostics_router
from domains.test_lab.diagnostics.t2_d06_row_completeness import dsc_cadence_shadow


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
        "ingest_status": "ready", "period_column": "reporting_period", "sourcing_tenant_id": "tenant-d06"})
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


def _frozen_shadow_run(item_id):
    manifest = manifest_row_completeness.build_manifest(item_id)
    return manifest_row_completeness.freeze(manifest["run_id"], actor="shadow-test")


def _shadow_locator(frozen):
    scope = frozen["scope"]
    return {"asset_id": scope["asset_id"], "snapshot_id": scope["snapshot_id"], "table": scope["table"],
            "locator": {"predicate": "table.temporal/observed_cadence", "axis_id": f"column:{scope['period']['column']}",
            "axis_column": {"table": scope["table"], "column": scope["period"]["column"]},
            "grouping": [{"table": scope["table"], "column": scope["facility_id"]["column"]}]}}


def _shadow_response_base(frozen):
    scope = frozen["scope"]
    request = {"asset_id": scope["asset_id"], "snapshot_id": scope["snapshot_id"], "table": scope["table"],
        "predicate": "table.temporal/observed_cadence", "axis_id": f"column:{scope['period']['column']}",
        "axis_column": {"table": scope["table"], "column": scope["period"]["column"]},
        "grouping": [{"table": scope["table"], "column": scope["facility_id"]["column"]}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    return {"projection_version": 1, "request_identity": dsc_cadence_shadow.request_fingerprint(request),
            "resolved_as_of": {"timestamp": "2026-01-01T00:00:00+00:00", "read_boundary_fingerprint": "c" * 64}}


def test_d06_cadence_shadow_is_observational_for_frozen_d06_and_aar(snapshot, monkeypatch):
    frozen = _frozen_shadow_run(snapshot)
    before_manifest = db.query_one("diag_runs", run_id=frozen["run_id"])["manifest_json"]
    before_artifacts = db.query("analysis_artifacts", order_by="artifact_id")
    before_events = db.query("analysis_artifact_events", order_by="event_id")
    monkeypatch.setattr(dsc_cadence_shadow.tenancy, "is_flag_enabled", lambda *_args, **_kwargs: True)
    metrics, audits = [], []
    result = dsc_cadence_shadow.observe(frozen, tenant_id="tenant-shadow", projection=lambda *_args, **_kwargs: {
        **_shadow_response_base(frozen), "outcome": "selected_pair_absent", "reason": None},
        metric=lambda **item: metrics.append(item), audit=lambda *_args: audits.append(True))
    assert result == {"outcome": "selected_pair_absent", "reason": "selected_pair_absent"}
    assert db.query_one("diag_runs", run_id=frozen["run_id"])["manifest_json"] == before_manifest
    assert db.query("analysis_artifacts", order_by="artifact_id") == before_artifacts
    assert db.query("analysis_artifact_events", order_by="event_id") == before_events
    assert audits == []
    assert len(metrics) == 1
    assert {key: metrics[0][key] for key in ("outcome", "reason", "enabled")} == {
        "outcome": "selected_pair_absent", "reason": "selected_pair_absent", "enabled": True}


def test_d06_cross_tenant_manifest_request_is_unknown_item_and_never_assists(snapshot, monkeypatch):
    """D06 gets its tenant proof from item ownership, never an AAR payload."""
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "other", "tenant_id": "tenant-other", "authz_roles": []})
    with pytest.raises(HTTPException) as rejected:
        diagnostics_router.build_diagnostic_manifest(
            snapshot, diagnostics_router.ManifestIn(diagnostic_id=6), authorization="Bearer foreign")
    assert rejected.value.status_code == 404
    assert rejected.value.detail == f"Unknown item: {snapshot}"
    assert db.query("diag_runs", item_id=snapshot, diagnostic_id=6) == []


def test_d06_known_run_followups_require_snapshot_owner(snapshot, monkeypatch):
    """A foreign caller cannot use a guessed D06 run id at any follow-up seam."""
    manifest = manifest_row_completeness.build_manifest(snapshot)
    run_id = manifest["run_id"]
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "foreign", "tenant_id": "tenant-foreign", "authz_roles": []})

    foreign_calls = (
        lambda: diagnostics_router.resumable_diagnostic_draft(snapshot, 6, authorization="Bearer foreign"),
        lambda: diagnostics_router.diagnostic_run_history(snapshot, 6, authorization="Bearer foreign"),
        lambda: diagnostics_router.diagnostics_board_card(snapshot, 6, authorization="Bearer foreign"),
        lambda: diagnostics_router.get_diagnostic_manifest(run_id, authorization="Bearer foreign"),
        lambda: diagnostics_router.patch_diagnostic_manifest(
            run_id, diagnostics_router.ManifestPatch(kind="role_override", role="segment", column="segment"),
            authorization="Bearer foreign"),
        lambda: diagnostics_router.discard_diagnostic_draft(run_id, authorization="Bearer foreign"),
        lambda: diagnostics_router.run_diagnostic_manifest(
            run_id, diagnostics_router.RunIn(stream=True), authorization="Bearer foreign"),
        lambda: diagnostics_router.diagnostic_results(snapshot, run_id=run_id, authorization="Bearer foreign"),
        lambda: diagnostics_router.diagnostic_run_report(run_id, authorization="Bearer foreign"),
    )
    for request in foreign_calls:
        with pytest.raises(HTTPException) as rejected:
            request()
        assert rejected.value.status_code == 404

    # A valid owner can still discover the same draft and manifest by run id.
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "owner", "tenant_id": "tenant-d06", "authz_roles": []})
    assert diagnostics_router.resumable_diagnostic_draft(snapshot, 6, authorization="Bearer owner")["draft"]["run_id"] == run_id
    assert diagnostics_router.diagnostic_run_history(snapshot, 6, authorization="Bearer owner")["runs"][0]["run_id"] == run_id
    assert diagnostics_router.get_diagnostic_manifest(run_id, authorization="Bearer owner")["run"]["run_id"] == run_id

    # Headerless EventSource has no caller principal; without the authenticated
    # owner handoff it must fail before it can replay or execute the run.
    with pytest.raises(HTTPException) as stream_rejected:
        diagnostics_router.stream_diagnostic_run(run_id, authorization=None)
    assert stream_rejected.value.status_code == 409


def test_d06_shadow_enabled_disabled_pair_preserves_completed_d06_outputs_and_aar(snapshot, monkeypatch):
    """A test-only awaited shadow observes, but cannot mutate, a completed D06 run."""
    manifest = manifest_row_completeness.build_manifest(snapshot)
    # Disabled baseline: all governed D06 output exists before the optional
    # observer is ever allowed to start.
    completed = runner_row_completeness.execute_now(manifest["run_id"])
    report_payload, report_artifact, _ = runner_row_completeness.report_payload(manifest["run_id"])
    frozen = db.query_one("diag_runs", run_id=manifest["run_id"])["manifest_json"]
    repo = AnalysisArtifactRepository()
    baseline = {
        "manifest": db.query_one("diag_runs", run_id=manifest["run_id"])["manifest_json"],
        "result": runner_row_completeness.run_results(manifest["run_id"]),
        "findings": db.query("diag_findings", run_id=manifest["run_id"], order_by="finding_id"),
        "report_json": report_payload.model_dump(mode="json"),
        "report_hash": report_artifact.payload_hash,
        "completed": completed,
        "aar_rows": db.query("analysis_artifacts", order_by="artifact_id"),
        "aar_events": db.query("analysis_artifact_events", order_by="event_id"),
    }
    monkeypatch.setattr(dsc_cadence_shadow.tenancy, "is_flag_enabled", lambda *_args, **_kwargs: True)
    # Production schedules a daemon. The direct seam is intentionally awaited
    # only here, after completion, so the paired assertion is deterministic.
    result = dsc_cadence_shadow.observe(frozen, tenant_id="tenant-paired",
                                        metric=lambda **_item: None, audit=lambda *_args: None)
    assert result is not None
    assert db.query("d06_dsc_cadence_shadow_audit") == []
    assert db.query_one("diag_runs", run_id=manifest["run_id"])["manifest_json"] == baseline["manifest"]
    assert runner_row_completeness.run_results(manifest["run_id"]) == baseline["result"]
    assert db.query("diag_findings", run_id=manifest["run_id"], order_by="finding_id") == baseline["findings"]
    # The completed report's immutable payload/hash are represented by the
    # AAR rows below. Do not re-read it here: ordinary repository integrity
    # verification updates its audit timestamp and would obscure the test.
    assert report_payload.model_dump(mode="json") == baseline["report_json"]
    assert report_artifact.payload_hash == baseline["report_hash"]
    assert db.query("analysis_artifacts", order_by="artifact_id") == baseline["aar_rows"]
    assert db.query("analysis_artifact_events", order_by="event_id") == baseline["aar_events"]


def test_d06_cadence_shadow_audit_is_private_idempotent_and_fail_open(snapshot, monkeypatch):
    frozen = _frozen_shadow_run(snapshot)
    monkeypatch.setattr(dsc_cadence_shadow.tenancy, "is_flag_enabled", lambda *_args, **_kwargs: True)
    response = {**_shadow_response_base(frozen), "outcome": "fulfilled", "reason": None, "cadence": {
        "resolution_state": "observed", "value": {"state": "regular",
            "observed_interval_class": {"unit": "month", "step": 1}},
        "assertion_pin": {"artifact_id": "art_shadow", "payload_hash": "a" * 64,
                          "dependency_fingerprint": "b" * 64}}}
    for _ in range(2):
        assert dsc_cadence_shadow.observe(frozen, tenant_id="tenant-shadow", projection=lambda *_args, **_kwargs: response,
                                          metric=lambda **_item: None)["outcome"] == "supports"
    audit_rows = db.query("d06_dsc_cadence_shadow_audit")
    assert len(audit_rows) == 1
    audit = audit_rows[0]["payload_json"]
    assert audit["assertion_pin"]["artifact_id"] == "art_shadow"
    # Qualified columns/pins are allowed only here; cadence values and raw
    # entity data are not retained by the reproducibility transport.
    assert "observed_interval_class" not in str(audit) and "'A'" not in str(audit)
    # A telemetry transport fault remains advisory and never writes D06 state.
    before = db.query_one("diag_runs", run_id=frozen["run_id"])["manifest_json"]
    result = dsc_cadence_shadow.observe(frozen, tenant_id="tenant-shadow", projection=lambda *_args, **_kwargs: response,
        metric=lambda **_item: (_ for _ in ()).throw(RuntimeError("log down")),
        audit=lambda *_args: (_ for _ in ()).throw(RuntimeError("audit down")))
    assert result["outcome"] == "error"
    assert db.query_one("diag_runs", run_id=frozen["run_id"])["manifest_json"] == before


def test_d06_shadow_router_launches_propagate_authenticated_tenant_not_manifest(snapshot, monkeypatch):
    """Both launch routes hand D06 only the authenticated tenant context."""
    frozen = _frozen_shadow_run(snapshot)
    # The frozen manifest is intentionally tenant-free: D06's identity and
    # persisted manifest must not absorb this feature's enablement scope.
    assert "tenant_id" not in frozen
    captured = []

    class Runner:
        @staticmethod
        def execute_now(run_id, actor="system", **kwargs):
            captured.append(("sync", run_id, actor, kwargs))
            return {"run_id": run_id}

        @staticmethod
        def run(run_id, actor="system", **kwargs):
            captured.append(("stream", run_id, actor, kwargs))
            yield {"phase": "done", "run_id": run_id}

    selected = {"runner": Runner, "agent": "row_completeness_engine",
                "manifest": type("Manifest", (), {"freeze": staticmethod(lambda *_a, **_k: frozen)})(),
                "work_count": lambda _manifest: 6}
    monkeypatch.setattr("dq_diagnostics.dispatch.adapter", lambda diagnostic_id: selected)
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "trusted-user", "tenant_id": "tenant-d06", "authz_roles": []})

    db.update("diag_runs", {"run_id": frozen["run_id"]}, {"status": "done"})
    result = diagnostics_router.run_diagnostic_manifest(
        frozen["run_id"], diagnostics_router.RunIn(stream=False), authorization="Bearer ignored")
    assert result == {"run_id": frozen["run_id"]}
    assert captured == [("sync", frozen["run_id"], "system", {"tenant_id": "tenant-d06"})]

    captured.clear()
    db.update("diag_runs", {"run_id": frozen["run_id"]}, {"status": "running"})
    from domains.test_lab.shared import run_state
    monkeypatch.setattr(run_state, "managed_run_execution",
                        lambda *_args, **_kwargs: nullcontext(type("Lease", (), {
                            "owner": "route-test", "heartbeat": staticmethod(lambda: None)})()))
    monkeypatch.setattr(diagnostics_router, "observe_background_events", lambda _key, factory: factory())
    monkeypatch.setattr(diagnostics_router, "_sse", lambda events: list(events))
    # Native EventSource supplies no authorization header. Its trusted tenant
    # is the server-side handoff written by the authenticated launch above.
    streamed = diagnostics_router.stream_diagnostic_run(frozen["run_id"], authorization=None)
    assert streamed == [{"phase": "done", "run_id": frozen["run_id"]}]
    assert captured == [("stream", frozen["run_id"], frozen["frozen_by"],
                         {"tenant_id": "tenant-d06"})]

    # A bounded context-store outage must not suppress D06 itself.  With no
    # handoff, both launch paths run normally but skip the advisory shadow.
    run_state.clear_trusted_execution_tenant(frozen["run_id"])
    monkeypatch.setattr(run_state, "bind_trusted_execution_tenant", lambda *_args: False)
    captured.clear()
    db.update("diag_runs", {"run_id": frozen["run_id"]}, {"status": "done"})
    assert diagnostics_router.run_diagnostic_manifest(
        frozen["run_id"], diagnostics_router.RunIn(stream=False), authorization="Bearer ignored",
    ) == {"run_id": frozen["run_id"]}
    assert captured == [("sync", frozen["run_id"], "system", {"tenant_id": None})]

    captured.clear()
    db.update("diag_runs", {"run_id": frozen["run_id"]}, {"status": "running"})
    with pytest.raises(HTTPException) as rejected:
        diagnostics_router.stream_diagnostic_run(frozen["run_id"], authorization=None)
    assert rejected.value.status_code == 409
    assert captured == []


def test_d06_shadow_audit_is_tenant_pinned_idempotent_and_cleared_by_run_and_factory_cleanup(snapshot):
    """The only permitted shadow write is private, tenant-bearing, and disposable."""
    frozen = _frozen_shadow_run(snapshot)
    payload = {"run_id": frozen["run_id"], "manifest_fingerprint": frozen["manifest_fingerprint"],
               "snapshot": {"asset_id": frozen["scope"]["asset_id"], "snapshot_id": snapshot},
               "table": frozen["scope"]["table"],
               "axis_column": {"table": frozen["scope"]["table"], "column": "reporting_period"},
               "grouping": [{"table": frozen["scope"]["table"], "column": "facility_id"}],
               "resolved_as_of": "read-boundary", "outcome": "supports",
               "reason": "equivalent_observed_interval",
               "assertion_pin": {"artifact_id": "art_private", "payload_hash": "a" * 64,
                                 "dependency_fingerprint": "b" * 64}}
    dsc_cadence_shadow.append_audit_event("tenant-a", payload)
    # The unique run/version key is intentionally insert-ignore. A later
    # caller cannot replace the tenant of the original audit record.
    dsc_cadence_shadow.append_audit_event("tenant-b", payload)
    rows = db.query("d06_dsc_cadence_shadow_audit", run_id=frozen["run_id"])
    assert len(rows) == 1 and rows[0]["tenant_id"] == "tenant-a"
    assert rows[0]["payload_json"]["assertion_pin"]["artifact_id"] == "art_private"

    deleted = db.wipe_diagnostics({frozen["run_id"]})
    assert deleted["deleted"]["d06_dsc_cadence_shadow_audit"] == 1
    assert db.query("d06_dsc_cadence_shadow_audit", run_id=frozen["run_id"]) == []

    # Factory cleanup uses the shared item-work-product list; it must not
    # retain an audit for a different completed D06 run.
    second = _frozen_shadow_run(snapshot)
    factory_payload = dict(payload, run_id=second["run_id"])
    dsc_cadence_shadow.append_audit_event("tenant-a", factory_payload)
    assert db.query("d06_dsc_cadence_shadow_audit", run_id=second["run_id"])
    factory = db.reset_demo()
    assert factory["deleted"]["d06_dsc_cadence_shadow_audit"] >= 1
    assert db.query("d06_dsc_cadence_shadow_audit") == []


def test_manifest_freeze_run_and_external_report(snapshot, monkeypatch):
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
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "owner", "tenant_id": "tenant-d06", "authz_roles": []})
    response = diagnostics_router.diagnostic_run_report(manifest["run_id"], authorization="Bearer owner")
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].endswith(f'row-completeness-{manifest["run_id"]}.pdf"')
    assert response.headers["x-analysis-artifact-id"]


def test_findings_require_explicit_idempotent_review_before_issue_creation(snapshot, monkeypatch):
    manifest = manifest_row_completeness.build_manifest(snapshot)
    runner_row_completeness.execute_now(manifest["run_id"])
    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "owner", "tenant_id": "tenant-d06", "authz_roles": []})
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


def test_d06_finding_disposition_and_result_promotion_require_owner(snapshot, monkeypatch):
    """Known D06 finding/result ids cannot mutate a foreign tenant's snapshot."""
    manifest = manifest_row_completeness.build_manifest(snapshot)
    runner_row_completeness.execute_now(manifest["run_id"])
    result = db.query("diag_results", run_id=manifest["run_id"])[0]
    violations = db.query("diag_findings", run_id=manifest["run_id"], outcome="VIOLATION", order_by="seq")
    assert len(violations) >= 2
    body = diagnostics_router.DispositionIn(
        action="confirm_issue", reason="Reviewed evidence requires remediation ownership.")

    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "foreign", "tenant_id": "tenant-foreign", "authz_roles": []})
    with pytest.raises(HTTPException) as finding_rejected:
        diagnostics_router.disposition_finding(violations[0]["finding_id"], body, authorization="Bearer foreign")
    assert finding_rejected.value.status_code == 404
    assert db.query_one("diag_findings", finding_id=violations[0]["finding_id"])["review_state"] == "open"

    with pytest.raises(HTTPException) as result_rejected:
        diagnostics_router.promote_diagnostic_result(
            result["result_id"], diagnostics_router.PsiResultPromotionIn(reason=body.reason),
            authorization="Bearer foreign")
    assert result_rejected.value.status_code == 404
    assert db.query_one("diag_findings", finding_id=violations[1]["finding_id"])["review_state"] == "open"

    monkeypatch.setattr(diagnostics_router, "_diagnostic_principal",
                        lambda _authorization: {"username": "owner", "tenant_id": "tenant-d06", "authz_roles": []})
    disposition = diagnostics_router.disposition_finding(
        violations[0]["finding_id"], body, authorization="Bearer owner")
    assert disposition["review_state"] == "confirmed"

    # D06 currently persists a summary result; isolate the generic promotion
    # adapter so this proves the router owner gate and authenticated handoff.
    monkeypatch.setattr("dq_diagnostics.result_promotion.ensure_override_finding",
                        lambda *_args, **_kwargs: violations[1]["finding_id"])
    promotion = diagnostics_router.promote_diagnostic_result(
        result["result_id"], diagnostics_router.PsiResultPromotionIn(reason=body.reason),
        authorization="Bearer owner")
    assert promotion["review_state"] == "confirmed"


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
