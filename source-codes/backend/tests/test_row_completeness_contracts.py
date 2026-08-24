"""Phase-1 contracts and provenance for Test 2, Diagnostic 6."""
from __future__ import annotations

import json
import copy
import sqlite3
import uuid

import pytest
from pydantic import ValidationError

import system_db as db
from analysis_runtime.artifact_types import get_artifact_type, validate_payload
from analysis_runtime.artifacts import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint
from dq_diagnostics import inference_audit
from dq_diagnostics import row_completeness_knowledge
from dq_diagnostics.engines.row_completeness.models import (
    RULE_IDS,
    RULE_TITLES,
    InferenceDisclosure,
    ReconciliationPayload,
    build_external_report_payload,
    mask_facility_identifier,
)
from dq_diagnostics.engines.row_completeness.api import RowCompletenessRunRequest
from dq_diagnostics.row_completeness_knowledge import (
    EXPECTED_RULE_IDS,
    RowCompletenessKnowledgeError,
    _validate_specs,
    load_seed_package,
)


def test_governed_knowledge_package_matches_the_closed_engine_contract():
    package = load_seed_package()
    assert tuple(rule["rule_id"] for rule in package["rules"]) == EXPECTED_RULE_IDS
    assert [rule["rule_id"] for rule in package["rules"] if rule["uses_continuity_floor"]] == [
        "T2D6-03", "T2D6-05", "T2D6-06"
    ]
    assert package["rules"][-1]["optional"] is True


def test_governed_knowledge_rejects_unsupported_primitive_changes():
    rules = copy.deepcopy(load_seed_package()["rules"])
    rules[0]["primitive"] = "execute_arbitrary_expression"
    with pytest.raises(RowCompletenessKnowledgeError, match="supported primitive"):
        _validate_specs(rules)


def test_latest_contract_valid_source_controlled_package_is_selected(tmp_path, monkeypatch):
    first = copy.deepcopy(load_seed_package())
    second = copy.deepcopy(first)
    second.update({"version_id": "kbver_t2d6_row_completeness_v2", "version_seq": 2})
    second["rules"][0]["title"] = "Facility and period key assignability"
    (tmp_path / "row_completeness_v1.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "row_completeness_v2.json").write_text(json.dumps(second), encoding="utf-8")
    monkeypatch.setattr(row_completeness_knowledge, "PACKAGE_DIR", tmp_path)
    assert row_completeness_knowledge.load_seed_package()["version_seq"] == 2


def test_duplicate_package_versions_fail_closed(tmp_path, monkeypatch):
    first = copy.deepcopy(load_seed_package())
    duplicate = copy.deepcopy(first)
    duplicate["version_id"] = "kbver_t2d6_duplicate"
    (tmp_path / "row_completeness_v1.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "row_completeness_v1_duplicate.json").write_text(json.dumps(duplicate), encoding="utf-8")
    monkeypatch.setattr(row_completeness_knowledge, "PACKAGE_DIR", tmp_path)
    with pytest.raises(RowCompletenessKnowledgeError, match="versions must be unique"):
        row_completeness_knowledge.load_seed_packages()


@pytest.fixture()
def isolated_run(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()
    run_id = f"run_t2d6_{uuid.uuid4().hex[:8]}"
    db.insert("diag_runs", {
        "run_id": run_id,
        "item_id": "snapshot_1",
        "diagnostic_id": 6,
        "manifest_json": {},
        "status": "draft",
        "engine_versions_json": {},
        "created_at": db.now_ist(),
        "started_at": None,
        "finished_at": None,
    })
    return run_id, tmp_path


def _rule(rule_id: str, **overrides):
    value = {
        "rule_id": rule_id,
        "title": RULE_TITLES[rule_id],
        "outcome": "PASS",
        "issue_count": 0,
        "measure": None,
        "continuity_floor": None,
        "affected_population": {},
        "explanation": "No issue was found.",
        "total_findings": 0,
        "evidence": [],
        "evidence_truncated": False,
        "na_reason": None,
    }
    value.update(overrides)
    return value


def _reconciliation(disclosure: dict, *, run_id: str):
    rules = [_rule(rule_id) for rule_id in RULE_IDS]
    rules[0] = _rule("T2D6-01", outcome="VIOLATION", issue_count=1,
        explanation="One row has an invalid key.", total_findings=1,
        evidence=[{"finding_type": "invalid_key", "row_reference": "ROW-7",
                   "reason": "Period is blank."}])
    rules[2] = _rule("T2D6-03", outcome="VIOLATION", issue_count=1,
        measure=0.9, continuity_floor=0.95,
        explanation="One period was below the minimum.", total_findings=1,
        evidence=[{"finding_type": "missing_pair", "facility_id": "RAW-FACILITY-123",
                   "period": "2025-03", "reason": "Required pair was not received."}])
    rules[3] = _rule("T2D6-04", outcome="VIOLATION", issue_count=1,
        explanation="One repeated pair was found.", total_findings=1,
        evidence=[{"finding_type": "duplicate_pair", "facility_id": "RAW-FACILITY-123",
                   "period": "2025-02", "row_reference": "ROW-4",
                   "reason": "One surplus row was received."}])
    rules[4] = _rule("T2D6-05", outcome="VIOLATION", issue_count=1,
        measure=0.9, continuity_floor=0.95,
        explanation="Portfolio coverage was below the minimum.", total_findings=1,
        evidence=[{"finding_type": "missing_pair", "facility_id": "RAW-FACILITY-123",
                   "period": "2025-03", "reason": "Interior period was absent."}])
    rules[5] = _rule("T2D6-06", outcome="NOT-APPLICABLE",
        explanation="No segment role was bound.", na_reason="No segment role was bound.")
    return {
        "schema_version": 1,
        "diagnostic_id": 6,
        "origin_run_id": run_id,
        "scope": {
            "asset_id": "asset_1", "snapshot_id": "snapshot_1", "table": "portfolio",
            "facility_id": {"table": "portfolio", "column": "facility_id",
                            "source": "manual", "score": None, "reason": "Confirmed by user."},
            "period": {"table": "portfolio", "column": "period",
                       "source": "governed_metadata", "score": 1.0,
                       "reason": "Confirmed period metadata."},
            "segment": None, "reporting_grain": "monthly", "continuity_floor": 0.95,
        },
        "overall_verdict": "violation",
        "facilities_assessed": 3,
        "periods_assessed": 4,
        "required_facility_period_pairs": 10,
        "received_required_pairs": 9,
        "continuity_coverage": 0.9,
        "issue_summary": {
            "invalid_key_rows": 1, "missing_facility_period_pairs": 1,
            "duplicate_pairs": 1, "surplus_duplicate_rows": 1,
            "affected_facilities": 1, "affected_periods": 2,
            "affected_segments": 0, "primary_issue_instances": 3,
        },
        "rules": rules,
        "calculation_inference_disclosure": disclosure,
        "manifest_fingerprint": stable_fingerprint({"run": run_id, "version": 1}),
        "methodology_version": "row-completeness-observed-span-v1",
        "engine_version": "1.0.0",
        "source_artifact_references": [{"artifact_id": "profile_1", "role": "table_profile"}],
    }


def test_zero_llm_usage_is_explicit_and_validated(isolated_run):
    run_id, _ = isolated_run
    inference_audit.record_zero_llm_usage(run_id, actor="tester")
    disclosure = inference_audit.inference_disclosure(run_id)

    validated = InferenceDisclosure.model_validate(disclosure)

    assert validated.llm_call_count == 0
    assert validated.llm_used is False
    assert "No LLM calls" in validated.statement
    assert validated.events[0]["status"] == "skipped"
    assert validated.verdict_influenced_by_llm is False


def test_inference_events_follow_diagnostic_run_reset_lifecycle(isolated_run):
    run_id, _ = isolated_run
    inference_audit.record_zero_llm_usage(run_id, actor="tester")

    result = db.wipe_diagnostics({run_id})

    assert result["deleted"]["diag_inference_events"] == 1
    assert db.query_one("diag_inference_events", run_id=run_id) is None
    assert db.query_one("diag_runs", run_id=run_id) is None


def test_llm_call_provenance_is_bounded_and_cannot_claim_verdict_influence(isolated_run):
    run_id, _ = isolated_run
    event = inference_audit.record_llm_call(
        run_id,
        status="succeeded",
        provider="azure_openai",
        model="role-review-deployment",
        provider_api_version="2025-01-01-preview",
        prompt_template_id="t2d6-role-review",
        prompt_template_version="1",
        prompt_hash="a" * 64,
        redacted_input_manifest={"roles": ["facility_id", "period"],
                                 "columns": ["account_ref", "reporting_month"]},
        validated_response={"decisions": [{"role": "period", "decision": "agree"}]},
        source_artifact_references=[{"artifact_id": "profile_1", "role": "table_profile"}],
        proposed_mapping=None,
        deterministic_mapping={"facility_id": "account_ref", "period": "reporting_month"},
        user_disposition="keep",
        final_applied_mapping={"facility_id": "account_ref", "period": "reporting_month"},
        actor="tester",
        prompt_tokens=120,
        completion_tokens=18,
        latency_ms=42,
    )
    disclosure = InferenceDisclosure.model_validate(inference_audit.inference_disclosure(run_id))

    assert event["row_level_data_included"] == 0
    assert disclosure.llm_call_count == 1 and disclosure.llm_used
    assert disclosure.events[0]["metrics_produced"] is False
    assert disclosure.events[0]["verdict_changed"] is False
    assert "raw prompt" not in json.dumps(disclosure.model_dump()).lower()

    with pytest.raises(ValueError, match="row-level"):
        inference_audit.record_llm_call(
            run_id, status="succeeded", provider="p", model="m", provider_api_version="v",
            prompt_template_id="p", prompt_template_version="1", prompt_hash="b" * 64,
            redacted_input_manifest={}, validated_response={}, source_artifact_references=[],
            proposed_mapping=None, deterministic_mapping={}, user_disposition="keep",
            final_applied_mapping={}, actor="tester", row_level_data_included=True,
        )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO diag_inference_events "
            "(event_id,run_id,event_kind,stage,purpose,status,invoked,row_level_data_included,"
            "verdict_influenced,payload_json,actor,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("bad", run_id, "llm", "execution", "verdict", "succeeded", 1, 0, 1,
             "{}", "tester", db.now_ist()),
        )


def test_reconciliation_contract_enforces_rule_order_counts_and_single_floor(isolated_run):
    run_id, _ = isolated_run
    inference_audit.record_zero_llm_usage(run_id, actor="tester")
    payload = _reconciliation(inference_audit.inference_disclosure(run_id), run_id=run_id)

    result = ReconciliationPayload.model_validate(payload)

    assert [rule.rule_id for rule in result.rules] == list(RULE_IDS)
    assert result.issue_summary.primary_issue_instances == 3
    assert result.scope.continuity_floor == 0.95
    assert not hasattr(result.scope, "peer_tolerance")

    invalid = {**payload, "rules": list(reversed(payload["rules"]))}
    with pytest.raises(ValidationError, match="display order"):
        ReconciliationPayload.model_validate(invalid)
    invalid_count = {**payload, "issue_summary": {
        **payload["issue_summary"], "primary_issue_instances": 4,
    }}
    with pytest.raises(ValidationError, match="must equal"):
        ReconciliationPayload.model_validate(invalid_count)


def test_minimum_api_input_contract_keeps_segment_and_llm_optional():
    request = RowCompletenessRunRequest(
        item_id="snapshot_1", table="portfolio", facility_id_column="facility_id",
        period_column="period", reporting_grain="monthly",
    )
    assert request.continuity_floor == 0.95
    assert request.segment_column is None
    assert request.llm_role_verification is False

    with pytest.raises(ValidationError, match="distinct columns"):
        RowCompletenessRunRequest(
            item_id="snapshot_1", table="portfolio", facility_id_column="period",
            period_column="period", reporting_grain="monthly",
        )
    with pytest.raises(ValidationError, match="extra"):
        RowCompletenessRunRequest(
            item_id="snapshot_1", table="portfolio", facility_id_column="facility_id",
            period_column="period", reporting_grain="monthly", peer_tolerance=0.02,
        )


def test_external_report_masks_facilities_and_preserves_explainable_counts(isolated_run):
    run_id, _ = isolated_run
    inference_audit.record_zero_llm_usage(run_id, actor="tester")
    reconciliation = ReconciliationPayload.model_validate(
        _reconciliation(inference_audit.inference_disclosure(run_id), run_id=run_id)
    )

    report = build_external_report_payload(
        reconciliation,
        report_id="report_1",
        source_artifact_id="artifact_1",
        source_payload_hash="c" * 64,
        renderer_version="1.0.0",
        report_salt="report_1",
        recommended_next_steps=[{"rule_id": "T2D6-03", "action": "Restore missing rows and rerun."}],
        limitations=["Facilities absent for the full snapshot cannot be detected."],
        current_run_id=run_id,
        segment_label_policy="retain",
    )
    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)

    assert "RAW-FACILITY-123" not in serialized
    assert "ROW-7" not in serialized
    assert "FAC-" in serialized
    assert report.safe_sharing.raw_rows_included is False
    assert report.issue_summary.primary_issue_instances == 3
    assert mask_facility_identifier("RAW-FACILITY-123", report_salt="report_1") == \
        mask_facility_identifier("RAW-FACILITY-123", report_salt="report_1")


def test_aar_registration_validation_lineage_and_exact_reuse(isolated_run):
    run_id, tmp_path = isolated_run
    inference_audit.record_zero_llm_usage(run_id, actor="tester")
    reconciliation = ReconciliationPayload.model_validate(
        _reconciliation(inference_audit.inference_disclosure(run_id), run_id=run_id)
    )
    repo = AnalysisArtifactRepository(tmp_path / "artifacts")
    common = {
        "asset_id": "asset_1", "snapshot_id": "snapshot_1",
        "population_fingerprint": stable_fingerprint({"table": "portfolio"}),
        "methodology_fingerprint": stable_fingerprint({"method": "profile-v1"}),
    }
    profile = repo.save({"table": "portfolio", "row_count": 11, "column_count": 2,
                         "source": "test"}, artifact_type="table_profile",
                        scope="universal", table="portfolio", **common).artifact
    reconciliation_payload = reconciliation.model_dump(mode="json")
    reconciliation_args = {
        **common,
        "artifact_type": "row_completeness_reconciliation",
        "methodology_fingerprint": stable_fingerprint({"method": "observed-span-v1"}),
        "scope": "diagnostic_local", "owner_id": "diagnostic:6", "table": "portfolio",
        "source_artifacts": ({"artifact_id": profile.artifact_id, "role": "table_profile"},),
        "identity_inputs": {"manifest_fingerprint": reconciliation.manifest_fingerprint},
        "run_id": run_id,
    }
    first = repo.save(reconciliation_payload, **reconciliation_args)
    second = repo.save(reconciliation_payload, **reconciliation_args)
    assert first.outcome == "created" and second.outcome == "reused"

    report = build_external_report_payload(
        reconciliation, report_id="report_1", source_artifact_id=first.artifact.artifact_id,
        source_payload_hash=first.artifact.payload_hash, renderer_version="1.0.0",
        report_salt="report_1", recommended_next_steps=[], limitations=["Observed-span method."],
        current_run_id=run_id, segment_label_policy="retain",
    )
    report_artifact = repo.save(
        report.model_dump(mode="json"), artifact_type="row_completeness_report",
        asset_id="asset_1", snapshot_id="snapshot_1",
        population_fingerprint=common["population_fingerprint"],
        methodology_fingerprint=stable_fingerprint({"renderer": "1.0.0"}),
        scope="diagnostic_local", owner_id="diagnostic:6", table="portfolio",
        source_artifacts=({"artifact_id": first.artifact.artifact_id,
                           "role": "reconciliation"},),
        identity_inputs={"audience": "external", "safe_sharing_profile": "v1"},
        run_id=run_id,
    ).artifact

    assert get_artifact_type("row_completeness_reconciliation").granularity == "table"
    assert get_artifact_type("row_completeness_report").sensitivity == "external_safe"
    assert repo.ancestors(report_artifact.artifact_id)[0].artifact_id == first.artifact.artifact_id
    _, persisted = repo.get(report_artifact.artifact_id)
    assert "RAW-FACILITY-123" not in json.dumps(persisted)

    invalid = {**reconciliation_payload, "diagnostic_id": 4}
    with pytest.raises(ValueError):
        validate_payload("row_completeness_reconciliation", invalid, 1)
