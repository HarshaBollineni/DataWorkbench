from __future__ import annotations

import io
import uuid

import pandas as pd
import pytest

import system_db as db
from ai.v2 import service
from domains.aar.repository import AnalysisArtifactRepository
from domains.test_lab.diagnostics.t2_d08_value_semantics import adjudication, knowledge, manifest, runner
from domains.test_lab.diagnostics.t2_d08_value_semantics.adjudication import RoleAdjudicationCall
from domains.test_lab.diagnostics.t2_d08_value_semantics.adjudication_contract import RoleAdjudicationOutput
from dq_diagnostics.register import seed_register
from routers import analyses as analyses_router
from routers import diagnostics as diagnostics_router


@pytest.fixture()
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(service, "ITEM_DB_ROOT", tmp_path / "item-dbs")
    db.init_schema()
    seed_register()
    suffix = uuid.uuid4().hex[:8]
    asset_id, item_id = f"asset_{suffix}", f"item_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"VS{suffix[:4].upper()}",
        "alias": f"value-semantics-{suffix}", "display_name": f"Value Semantics {suffix}",
        "kind": "dataset", "time_basis": "period", "current_version_no": 1,
        "lifecycle_status": "active", "created_at": now, "updated_at": now,
    })
    db.insert("dq_items", {
        "item_id": item_id, "kind": "dataset", "name": f"VS-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": item_id, "intent": "fresh",
        "ingest_status": "ready", "target_variable": None,
    })
    frame = pd.DataFrame({
        "INCOME": [-999, 1250, 1300],
        "UNMAPPED_NOTE": ["a", "b", "c"],
        "CLTV": [45.0, 55.0, 65.0],
    })
    service._write_table(item_id, "portfolio", frame)
    for column in frame.columns:
        db.insert("variable_inventory", {
            "item_id": item_id, "table_name": "portfolio", "column_name": column,
            "classification": "numeric" if column == "INCOME" else "categorical",
            "data_type": str(frame[column].dtype),
            "description": ("Current Loan-to-Value (%), target 50-70"
                            if column == "CLTV" else column),
            "discrepancies": [], "notes": "", "role": "Feature", "dictionary_role": "feature",
            "profile_json": {"total_count": len(frame), "non_null_count": len(frame),
                             "null_count": 0, "cardinality": int(frame[column].nunique())},
            "provisional": 0, "updated_at": now,
        })
    return item_id


def test_cltv_ai_no_match_is_persisted_completed_review_is_not_repeated_and_failure_is_retryable(
        snapshot, monkeypatch):
    class NoMatchAdjudicator:
        model = "test-model"
        prompt_version = "value_semantics_role_adjudication_v0_2"

        def __init__(self, **_kwargs):
            pass

        def adjudicate(self, _input):
            return RoleAdjudicationCall(output=RoleAdjudicationOutput(
                decision="NO_CANDIDATE_MATCH",
                reason=("CLTV is a current loan-to-value measure and no supplied "
                        "Value Semantics role covers it."),
            ), response_id="response-cltv", response_model=self.model)

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", NoMatchAdjudicator)
    monkeypatch.setattr(adjudication, "create_openai_client", lambda: object())
    draft = manifest.build_manifest(snapshot, actor="analyst", enforce_register=False)
    updated = manifest.patch_manifest(draft["run_id"], {
        "kind": "request_ai_role_review", "feature": "CLTV",
    }, actor="analyst")
    cltv = next(row for row in updated["fields"] if row["column"] == "CLTV")
    assert cltv["adjudication"]["status"] == "proposal_ready"
    assert cltv["adjudication"]["output"]["decision"] == "NO_CANDIDATE_MATCH"
    assert cltv["binding_source"] == "ai_no_applicable_role"
    assert cltv["proposed_roles"] == []
    assert cltv["confirmed_roles"] == []
    assert not cltv.get("applicability")
    assert cltv["review_required"] is True

    class FailingAdjudicator(NoMatchAdjudicator):
        def adjudicate(self, _input):
            raise RuntimeError("provider details must remain sanitized")

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", FailingAdjudicator)
    unchanged = manifest.patch_manifest(draft["run_id"], {
        "kind": "request_ai_role_review", "feature": "CLTV",
    }, actor="analyst")
    cltv = next(row for row in unchanged["fields"] if row["column"] == "CLTV")
    assert cltv["adjudication"]["status"] == "proposal_ready"

    failed = manifest.patch_manifest(draft["run_id"], {
        "kind": "request_ai_role_review", "feature": "UNMAPPED_NOTE",
    }, actor="analyst")
    unmapped = next(row for row in failed["fields"] if row["column"] == "UNMAPPED_NOTE")
    assert unmapped["adjudication"]["status"] == "failed"
    assert unmapped["adjudication"]["error"] == "RuntimeError"
    assert "provider details" not in str(unmapped["adjudication"])
    assert unmapped["review_required"] is True

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", NoMatchAdjudicator)
    retried = manifest.patch_manifest(draft["run_id"], {
        "kind": "request_ai_role_review", "feature": "UNMAPPED_NOTE",
    }, actor="analyst")
    unmapped = next(row for row in retried["fields"] if row["column"] == "UNMAPPED_NOTE")
    assert unmapped["adjudication"]["status"] == "proposal_ready"


def test_same_snapshot_rerun_reuses_prior_ai_and_only_new_field_invokes_provider(snapshot, monkeypatch):
    calls = []

    class CountingAdjudicator:
        model = "test-model"
        prompt_version = "value_semantics_role_adjudication_v0_2"

        def __init__(self, **_kwargs):
            pass

        def adjudicate(self, input_):
            calls.append(input_.input_variable.name)
            return RoleAdjudicationCall(output=RoleAdjudicationOutput(
                decision="NO_CANDIDATE_MATCH",
                reason=f"No governed role applies to {input_.input_variable.name}.",
            ), response_id=f"response-{len(calls)}", response_model=self.model)

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", CountingAdjudicator)
    monkeypatch.setattr(adjudication, "create_openai_client", lambda: object())
    first = manifest.build_manifest(snapshot, actor="analyst", tenant_id="tenant-a",
                                    enforce_register=False)
    first = manifest.patch_manifest(first["run_id"], {
        "kind": "request_ai_role_review", "feature": "CLTV",
    }, actor="analyst")
    source_event = next(row for row in db.query("diag_inference_events", run_id=first["run_id"])
                        if row["status"] == "succeeded")
    assert calls == ["CLTV"]
    manifest.discard_drafts(snapshot, actor="analyst", tenant_id="tenant-a")

    rerun = manifest.build_manifest(snapshot, actor="analyst", tenant_id="tenant-a",
                                    enforce_register=False)
    cltv = next(row for row in rerun["fields"] if row["column"] == "CLTV")
    assert cltv["adjudication"]["status"] == "proposal_ready"
    assert cltv["adjudication"]["reused_from"]["run_id"] == first["run_id"]
    assert cltv["adjudication"]["reused_from"]["event_id"] == source_event["event_id"]
    assert cltv["adjudication"]["output"] == next(
        row for row in first["fields"] if row["column"] == "CLTV")["adjudication"]["output"]
    assert calls == ["CLTV"]
    disclosure = rerun["inference_disclosure"]
    assert disclosure["llm_call_count"] == 0
    assert disclosure["reused_inference_count"] == 1
    assert "1 prior validated inference" in disclosure["statement"]
    reused_event = next(row for row in disclosure["events"] if row.get("reused_prior_inference"))
    assert reused_event["invoked"] is False
    assert reused_event["source_event_id"] == source_event["event_id"]

    # Even a direct duplicate request is idempotent and cannot spend another call.
    rerun = manifest.patch_manifest(rerun["run_id"], {
        "kind": "request_ai_role_review", "feature": "CLTV",
    }, actor="analyst")
    assert calls == ["CLTV"]
    assert rerun["inference_disclosure"]["reused_inference_count"] == 1

    # A field with no prior successful inference requires an explicit request.
    rerun = manifest.patch_manifest(rerun["run_id"], {
        "kind": "request_ai_role_review", "feature": "UNMAPPED_NOTE",
    }, actor="analyst")
    assert calls == ["CLTV", "UNMAPPED_NOTE"]
    assert rerun["inference_disclosure"]["llm_call_count"] == 1


def test_ai_reuse_is_tenant_and_field_metadata_bound(snapshot, monkeypatch):
    class NoMatchAdjudicator:
        model = "test-model"
        prompt_version = "value_semantics_role_adjudication_v0_2"

        def __init__(self, **_kwargs):
            pass

        def adjudicate(self, _input):
            return RoleAdjudicationCall(output=RoleAdjudicationOutput(
                decision="NO_CANDIDATE_MATCH", reason="No governed role applies.",
            ), response_model=self.model)

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", NoMatchAdjudicator)
    monkeypatch.setattr(adjudication, "create_openai_client", lambda: object())
    first = manifest.build_manifest(snapshot, tenant_id="tenant-a", enforce_register=False)
    manifest.patch_manifest(first["run_id"], {
        "kind": "request_ai_role_review", "feature": "CLTV",
    })
    manifest.discard_drafts(snapshot, tenant_id="tenant-a")
    other_tenant = manifest.build_manifest(snapshot, tenant_id="tenant-b", enforce_register=False)
    assert next(row for row in other_tenant["fields"] if row["column"] == "CLTV")["adjudication"]["status"] == "not_requested"
    manifest.discard_drafts(snapshot, tenant_id="tenant-b")
    db.update("variable_inventory", {"item_id": snapshot, "column_name": "CLTV"}, {
        "description": "Revised definition for a materially different CLTV field",
    })
    changed = manifest.build_manifest(snapshot, tenant_id="tenant-a", enforce_register=False)
    assert next(row for row in changed["fields"] if row["column"] == "CLTV")["adjudication"]["status"] == "not_requested"


def test_field_applicability_is_explicit_audited_and_reversible(snapshot):
    draft = manifest.build_manifest(snapshot, actor="analyst", enforce_register=False)
    run_id = draft["run_id"]
    draft = manifest.patch_manifest(run_id, {"kind": "field_scope", "feature": "CLTV", "enabled": False})
    decisions = {row["column"]: row for row in manifest.field_scope_decisions(draft)}
    assert decisions["CLTV"]["status"] == "excluded"
    for reason in (None, "  ", "x" * 2001):
        with pytest.raises(manifest.ManifestError, match="requires a reason"):
            manifest.patch_manifest(run_id, {
                "kind": "field_applicability", "feature": "CLTV", "value": "not_applicable", "reason": reason,
            })
    for invalid in ("guess", [], {}):
        with pytest.raises(manifest.ManifestError, match="applicability must"):
            manifest.patch_manifest(run_id, {"kind": "field_applicability", "feature": "CLTV", "value": invalid})

    reason = "No governed D08 role covers current loan-to-value."
    draft = manifest.patch_manifest(run_id, {
        "kind": "field_applicability", "feature": "CLTV", "value": "not_applicable", "reason": reason,
    }, actor="reviewer")
    cltv = next(row for row in draft["fields"] if row["column"] == "CLTV")
    assert cltv["selected"] is False
    assert cltv["confirmed_roles"] == []
    assert cltv["review_required"] is False
    assert cltv["applicability"]["confirmed_by"] == "reviewer"
    assert cltv["applicability"]["confirmed_at"]
    assert cltv["applicability"]["reason"] == reason
    for patch in (
        {"kind": "field_scope", "feature": "CLTV", "enabled": True},
        {"kind": "bulk_scope", "features": ["CLTV"]},
    ):
        with pytest.raises(manifest.ManifestError, match="Reopen"):
            manifest.patch_manifest(run_id, patch)

    reopened = manifest.patch_manifest(run_id, {
        "kind": "field_applicability", "feature": "CLTV", "value": "review",
    }, actor="reviewer")
    cltv = next(row for row in reopened["fields"] if row["column"] == "CLTV")
    assert cltv["applicability"] is None
    assert cltv["review_required"] is True
    assert cltv["selected"] is False
    audit = db.query("diag_run_decisions", run_id=run_id)
    assert any((row.get("payload_json") or {}).get("before", {}).get("reason") == reason
               for row in audit if isinstance((row.get("payload_json") or {}).get("before"), dict))
    manifest.patch_manifest(run_id, {
        "kind": "field_applicability", "feature": "CLTV", "value": "not_applicable", "reason": reason,
    })
    rebound = manifest.patch_manifest(run_id, {
        "kind": "role_binding", "feature": "CLTV", "value": ["static_attributes"],
    })
    field = next(row for row in rebound["fields"] if row["column"] == "CLTV")
    assert field["applicability"] is None
    assert field["selected"] is True
    assert field["confirmed_roles"] == ["static_attributes"]


def test_not_applicable_is_retained_in_results_aar_and_downloads_not_cell_tags(snapshot):
    draft = manifest.build_manifest(snapshot, actor="analyst", enforce_register=False)
    run_id = draft["run_id"]
    reason = "No D08 role applies to current loan-to-value; not a static attribute."
    for patch in (
        {"kind": "introduction_acknowledgement", "enabled": True},
        {"kind": "bulk_scope", "features": ["INCOME"]},
        {"kind": "role_binding", "feature": "INCOME", "value": ["income_measure"]},
        {"kind": "declaration_update", "key": "field_specific_sentinel_definitions", "value": {"INCOME": -999}},
        {"kind": "field_applicability", "feature": "CLTV", "value": "not_applicable", "reason": reason},
    ):
        draft = manifest.patch_manifest(run_id, patch, actor="reviewer")
    assert draft["ready_to_run"]
    runner.execute_now(run_id, actor="reviewer")
    projected = runner.run_results(run_id)
    metrics = next(row["metrics_json"] for row in projected["results"]
                   if row["metrics_json"]["result_kind"] == "value_semantics_run_summary")
    decisions = {row["column"]: row for row in metrics["field_scope_decisions"]}
    assert {column: row["status"] for column, row in decisions.items()} == {
        "INCOME": "included", "CLTV": "not_applicable", "UNMAPPED_NOTE": "excluded",
    }
    assert decisions["CLTV"]["reason"] == reason
    assert decisions["CLTV"]["confirmed_by"] == "reviewer"
    assert metrics["rollup"]["not_applicable_cells"] == 0
    assert all(row["input_variable"] != "CLTV" for row in metrics["field_summary"])
    repo = AnalysisArtifactRepository()
    for key in ("bindings", "report"):
        metadata, payload = repo.get(metrics["artifacts"][key]["artifact_id"])
        assert "CLTV" in metadata.identity["features"]
        assert payload["field_scope_decisions"] == metrics["field_scope_decisions"]
    text_report = runner.report_text(run_id)
    assert "CLTV: Not Applicable" in text_report
    assert "UNMAPPED_NOTE: Excluded" in text_report
    assert reason in text_report
    assert "Confirmed by: reviewer" in text_report
    assert runner.report_pdf(run_id).startswith(b"%PDF")
    first, artifact, reused = runner.report_payload(run_id)
    assert reused
    assert runner.report_payload(run_id)[1].artifact_id == artifact.artifact_id
    assert first["field_scope_decisions"] == metrics["field_scope_decisions"]
    with pytest.raises(manifest.ManifestError, match="immutable"):
        manifest.patch_manifest(run_id, {"kind": "field_applicability", "feature": "CLTV", "value": "review"})


@pytest.mark.parametrize("decision", ["exact", "role", "not_applicable"])
def test_ai_requires_explicit_reopen_of_confirmed_decisions(snapshot, monkeypatch, decision):
    class NoMatchAdjudicator:
        model = "test-model"
        prompt_version = "value_semantics_role_adjudication_v0_2"

        def __init__(self, **_kwargs):
            pass

        def adjudicate(self, input_):
            assert input_.detailed_candidates
            return RoleAdjudicationCall(output=RoleAdjudicationOutput(
                decision="NO_CANDIDATE_MATCH", reason="No current governed role fits this business meaning.",
            ), response_model=self.model)

    monkeypatch.setattr(adjudication, "OpenAIRoleAdjudicator", NoMatchAdjudicator)
    monkeypatch.setattr(adjudication, "create_openai_client", lambda: object())
    if decision == "exact":
        db.update("variable_inventory", {"item_id": snapshot, "column_name": "INCOME"}, {
            "column_name": "NET_OPERATING_INCOME", "description": "Net operating income",
        })
        service._write_table(snapshot, "portfolio", pd.DataFrame({
            "NET_OPERATING_INCOME": [-999, 1250, 1300],
            "UNMAPPED_NOTE": ["a", "b", "c"], "CLTV": [45.0, 55.0, 65.0],
        }))
    draft = manifest.build_manifest(snapshot, enforce_register=False)
    run_id = draft["run_id"]
    column = "NET_OPERATING_INCOME" if decision == "exact" else "CLTV"
    if decision == "role":
        draft = manifest.patch_manifest(run_id, {"kind": "role_binding", "feature": column, "value": ["static_attributes"]})
    elif decision == "not_applicable":
        draft = manifest.patch_manifest(run_id, {
            "kind": "field_applicability", "feature": column, "value": decision, "reason": "SME checked the catalog.",
        })
    before = next(row for row in draft["fields"] if row["column"] == column)
    if decision == "exact":
        assert before["binding_source"] == "deterministic_exact"
    with pytest.raises(manifest.ManifestError, match="only for fields requiring confirmation"):
        manifest.patch_manifest(run_id, {"kind": "request_ai_role_review", "feature": column})
    unchanged = next(row for row in manifest.get_run(run_id)["manifest_json"]["fields"]
                     if row["column"] == column)
    for key in ("selected", "confirmed_roles", "binding_source", "review_required", "applicability"):
        assert unchanged.get(key) == before.get(key)

    reopened = manifest.patch_manifest(run_id, {
        "kind": "field_applicability", "feature": column, "value": "review",
    }, actor="reviewer")
    field = next(row for row in reopened["fields"] if row["column"] == column)
    assert field["review_required"] is True
    assert field["confirmed_roles"] == []
    assert field["selected"] is False
    updated = manifest.patch_manifest(run_id, {
        "kind": "request_ai_role_review", "feature": column,
    }, actor="reviewer")
    field = next(row for row in updated["fields"] if row["column"] == column)
    assert field["adjudication"]["status"] == "proposal_ready"
    assert field["adjudication"]["output"]["decision"] == "NO_CANDIDATE_MATCH"
    assert field["review_required"] is True
    assert field["confirmed_roles"] == []


def test_general_context_manifest_run_reports_artifacts_kb_and_findings(snapshot):
    draft = manifest.build_manifest(snapshot, actor="analyst", enforce_register=False)
    assert draft["context"]["selected"] == ["GENERAL"]
    assert draft["context"]["execution_effect"] == "none"
    assert any(blocker["code"] == "introduction_required" for blocker in draft["blockers"])

    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "introduction_acknowledgement", "enabled": True,
    }, actor="analyst")
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "bulk_scope", "features": ["INCOME"],
    }, actor="analyst")
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "role_binding", "column": "INCOME", "value": ["income_measure"],
    }, actor="analyst")
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "declaration_update", "key": "field_specific_sentinel_definitions",
        "value": {"INCOME": -999},
    }, actor="analyst")
    assert draft["ready_to_run"] is True
    assert draft["coverage"]["context_is_non_enforcing"] is True
    assert any(route.get("evaluation_mode") == "sentinel_only"
               for route in draft["coverage"]["ready_routes"])

    done = runner.execute_now(draft["run_id"], actor="analyst")
    assert done["phase"] == "done"
    assert done["rollup"]["stale_frozen_cells"] == 1
    assert done["issues"] == {"created": []}

    projected = runner.run_results(draft["run_id"])
    result = next(row for row in projected["results"]
                  if row["metrics_json"]["result_kind"] == "value_semantics_run_summary")
    metrics = result["metrics_json"]
    assert metrics["field_summary"][0]["input_variable"] == "INCOME"
    assert {value["artifact_type"] for value in metrics["artifacts"].values()} == {
        "value_semantics_bindings", "value_semantics_tags",
        "value_semantics_assessment_ledger", "value_semantics_report",
    }
    assert result["findings"]
    assert all(finding.get("existing_issue") is None for finding in result["findings"])

    artifacts = db.query("analysis_artifacts", run_id=draft["run_id"])
    assert {row["artifact_type"] for row in artifacts} == {
        "value_semantics_bindings", "value_semantics_tags",
        "value_semantics_assessment_ledger", "value_semantics_report",
    }
    repo = AnalysisArtifactRepository()
    tags_row = next(row for row in artifacts if row["artifact_type"] == "value_semantics_tags")
    metadata, payload = repo.get(tags_row["artifact_id"])
    assert metadata.payload_media_type == "application/vnd.apache.parquet"
    assert isinstance(payload, bytes)
    _metadata, tag_bytes = repo.read_bytes(tags_row["artifact_id"])
    stored_tags = pd.read_parquet(io.BytesIO(tag_bytes))
    assert "input_value" not in stored_tags.columns
    assert stored_tags["tag"].tolist() == ["STALE_FROZEN"]
    download = analyses_router.download_analysis_artifact(tags_row["artifact_id"])
    assert download.body == tag_bytes
    assert download.headers["content-disposition"].endswith('.parquet"')
    with pytest.raises(Exception) as binary_payload:
        analyses_router.get_analysis_artifact_payload(tags_row["artifact_id"])
    assert getattr(binary_payload.value, "status_code", None) == 415

    report_text = runner.report_text(draft["run_id"])
    assert "VALUE SEMANTICS ANALYSIS" in report_text
    assert "GENERAL" in report_text
    assert runner.report_pdf(draft["run_id"])

    seeded = knowledge.seed_documents()
    assert seeded["documents"]
    versions = db.query("kb_document_versions")
    assert {row["version_id"] for row in versions} >= {
        knowledge.VALUE_VERSION_V01_ID, knowledge.VALUE_VERSION_ID,
        knowledge.TERMINOLOGY_VERSION_V02_ID, knowledge.TERMINOLOGY_VERSION_ID,
    }
    terminology_versions = sorted(
        (row for row in versions if row["document_id"] == knowledge.TERMINOLOGY_DOCUMENT_ID),
        key=lambda row: row["version_seq"], reverse=True,
    )
    assert [row["version_id"] for row in terminology_versions] == [
        knowledge.TERMINOLOGY_VERSION_ID, knowledge.TERMINOLOGY_VERSION_V02_ID,
    ]
    assert terminology_versions[0]["conversion_report_json"]["lifecycle"] == "active"
    assert terminology_versions[1]["conversion_report_json"]["lifecycle"] == "historical"


def test_report_persistence_failure_cannot_leave_a_successful_or_running_run(
        snapshot, monkeypatch):
    draft = manifest.build_manifest(snapshot, actor="analyst", enforce_register=False)
    for patch in (
        {"kind": "introduction_acknowledgement", "enabled": True},
        {"kind": "bulk_scope", "features": ["INCOME"]},
        {"kind": "role_binding", "column": "INCOME", "value": ["income_measure"]},
        {"kind": "declaration_update", "key": "field_specific_sentinel_definitions",
         "value": {"INCOME": -999}},
    ):
        manifest.patch_manifest(draft["run_id"], patch, actor="analyst")

    def fail_report(*_args, **_kwargs):
        raise RuntimeError("private storage failure detail")

    monkeypatch.setattr(runner, "_save_report_artifact", fail_report)
    with pytest.raises(Exception) as failure:
        diagnostics_router.run_diagnostic_manifest(
            draft["run_id"], diagnostics_router.RunIn(stream=False),
        )

    stored = db.query_one("diag_runs", run_id=draft["run_id"])
    assert getattr(failure.value, "status_code", None) == 500
    assert getattr(failure.value, "detail", None) == "internal error"
    assert stored["status"] == "failed"
    assert stored["finished_at"]
    assert stored["status_detail_json"]["code"] == "execution_failed"
    assert "private storage failure detail" not in str(stored["status_detail_json"])
    assert not db.query("analysis_artifacts", run_id=draft["run_id"],
                        artifact_type="value_semantics_report")


def test_shared_api_launch_and_report_are_tenant_bound(snapshot, monkeypatch):
    principals = {
        "Bearer tenant-a": {"username": "analyst-a", "tenant_id": "tenant-a", "authz_roles": []},
        "Bearer tenant-b": {"username": "analyst-b", "tenant_id": "tenant-b", "authz_roles": []},
    }
    monkeypatch.setattr(
        diagnostics_router.tenancy, "resolve_principal",
        lambda authorization: principals[authorization],
    )
    authorization = "Bearer tenant-a"
    draft = diagnostics_router.build_diagnostic_manifest(
        snapshot, diagnostics_router.ManifestIn(diagnostic_id=8), authorization,
    )
    assert draft["tenant_id"] == "tenant-a"
    assert draft["introduction"]["title"] == "Understand value semantics before you run"
    for decision in (
        diagnostics_router.ManifestPatch(kind="introduction_acknowledgement", enabled=True),
        diagnostics_router.ManifestPatch(kind="bulk_scope", features=["INCOME"]),
        diagnostics_router.ManifestPatch(kind="role_binding", column="INCOME", value=["income_measure"]),
        diagnostics_router.ManifestPatch(kind="field_applicability", feature="CLTV", value="not_applicable",
                                         reason="No governed D08 role covers this loan-to-value field."),
        diagnostics_router.ManifestPatch(
            kind="declaration_update", key="field_specific_sentinel_definitions",
            value={"INCOME": -999},
        ),
    ):
        draft = diagnostics_router.patch_diagnostic_manifest(
            draft["run_id"], decision, authorization,
        )
    started = diagnostics_router.run_diagnostic_manifest(
        draft["run_id"], diagnostics_router.RunIn(stream=False), authorization,
    )
    assert started["phase"] == "done"
    text_response = diagnostics_router.diagnostic_run_report(
        draft["run_id"], "text", authorization,
    )
    assert b"VALUE SEMANTICS ANALYSIS" in text_response.body
    assert b"CLTV: Not Applicable" in text_response.body
    assert b"Confirmed by: analyst-a" in text_response.body
    pdf_response = diagnostics_router.diagnostic_run_report(
        draft["run_id"], "pdf", authorization,
    )
    assert pdf_response.headers["content-disposition"].endswith('.pdf"')
    assert pdf_response.headers["x-analysis-artifact-id"]

    with pytest.raises(Exception) as denied:
        diagnostics_router.get_diagnostic_manifest(draft["run_id"], "Bearer tenant-b")
    assert getattr(denied.value, "status_code", None) == 404
    with pytest.raises(Exception) as denied_report:
        diagnostics_router.diagnostic_run_report(
            draft["run_id"], "text", "Bearer tenant-b",
        )
    assert getattr(denied_report.value, "status_code", None) == 404
