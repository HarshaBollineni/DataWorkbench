"""Slice-4 bounded, explicitly authorized DSC backfill acceptance tests."""
from __future__ import annotations

import uuid
import concurrent.futures
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import system_db as db
from domains.aar import dataset_structure_backfill as backfill
from domains.aar import materialization_jobs as jobs
from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
from routers import dataset_structure


@pytest.fixture()
def legacy_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()
    suffix, now = uuid.uuid4().hex[:8], db.now_ist()
    asset_id, snapshot_id = f"asset_{suffix}", f"item_{suffix}"
    db.insert("dq_assets", {"asset_id": asset_id, "system_id": f"DS{suffix}", "alias": "legacy",
              "display_name": "Legacy", "kind": "dataset", "time_basis": "none", "current_version_no": 1,
              "lifecycle_status": "active", "created_at": now, "updated_at": now})
    db.insert("dq_items", {"item_id": snapshot_id, "kind": "dataset", "name": "legacy", "status": "profiled",
              "created_at": now, "updated_at": now, "dataset_family_id": asset_id, "delivery_seq": 1,
              "version_no": 1, "snapshot_status": "active", "snapshot_label": "legacy", "intent": "fresh",
              "ingest_status": "ready", "sourcing_tenant_id": "tenant-a"})
    db.insert("dq_item_tables", {"item_id": snapshot_id, "table_name": "observations", "row_count": 3,
              "col_count": 2, "columns": ["facility_id", "period"]})
    profile = {"calculation_method": "exact", "profile_basis": "confirmed_regular_values", "total_count": 3,
               "non_null_count": 3, "null_count": 0, "cardinality": 3, "top_k": {"a": 1}}
    for column, role, data_type in (("facility_id", "Identifier", "string"), ("period", "Date", "date")):
        db.insert("variable_inventory", {"item_id": snapshot_id, "table_name": "observations", "column_name": column,
                  "classification": "categorical", "data_type": data_type, "description": "", "discrepancies": [],
                  "notes": "", "role": role, "role_reviewed": 1, "profile_json": profile, "updated_at": now})
    assert persist_snapshot_profile_artifacts(snapshot_id, actor="test")
    # Simulate a pre-Foundation snapshot: retained governed profiles but no proof/job.
    db.execute("DELETE FROM dataset_structure_materialization_jobs")
    db.execute("DELETE FROM dataset_structure_profile_publications")
    db.execute("DELETE FROM dataset_structure_profile_publication_completions")
    return SimpleNamespace(asset_id=asset_id, snapshot_id=snapshot_id)


def test_backfill_establishes_distinct_proof_schedules_once_and_never_decides(legacy_snapshot):
    first = backfill.run_backfill(tenant_id="tenant-a")
    assert first["counts"] == {"examined": 1, "scheduled": 1, "reused": 0, "incomplete": 0}
    completion = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=legacy_snapshot.snapshot_id)
    job = db.query_one("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)
    assert (completion["proof_source"], completion["proof_migration_version"], job["reason"]) == ("backfill", backfill.MIGRATION_VERSION, "backfill")
    assert db.query_one("dataset_structure_review_states", snapshot_id=legacy_snapshot.snapshot_id)["state"] == "materializing"
    assert not db.query("dataset_structure_review_decision_batches", snapshot_id=legacy_snapshot.snapshot_id)
    assert not db.query("dataset_structure_review_drafts", snapshot_id=legacy_snapshot.snapshot_id)
    second = backfill.run_backfill(tenant_id="tenant-a")
    assert second["counts"]["reused"] == 1
    assert len(db.query("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)) == 1


def test_incomplete_profiles_fail_closed_and_are_retryable(legacy_snapshot):
    db.execute("DELETE FROM analysis_artifacts")
    result = backfill.run_backfill(tenant_id="tenant-a")
    assert result["counts"] == {"examined": 1, "scheduled": 0, "reused": 0, "incomplete": 1}
    assert result["closed_error_code"] == "DSC_R_BACKFILL_PROFILE_INCOMPLETE"
    for table in ("dataset_structure_profile_publication_completions", "dataset_structure_profile_publications",
                  "dataset_structure_materialization_jobs", "dataset_structure_backfill_runs", "dataset_structure_review_states"):
        assert not db.query(table, snapshot_id=legacy_snapshot.snapshot_id)


def test_backfill_cursor_is_tenant_scoped_and_skips_inactive(legacy_snapshot):
    now = db.now_ist()
    for item_id, tenant, status in (("a_other", "tenant-b", "active"), ("b_inactive", "tenant-a", "inactive")):
        db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": item_id, "status": "profiled",
                  "created_at": now, "updated_at": now, "dataset_family_id": "asset_missing", "delivery_seq": 1,
                  "version_no": 1, "snapshot_status": status, "snapshot_label": item_id, "intent": "fresh",
                  "ingest_status": "ready", "sourcing_tenant_id": tenant})
    for index in range(51):
        item_id = f"page_{index:03d}"
        db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": item_id, "status": "profiled",
                  "created_at": now, "updated_at": now, "dataset_family_id": "asset_missing", "delivery_seq": 1,
                  "version_no": 1, "snapshot_status": "active", "snapshot_label": item_id, "intent": "fresh",
                  "ingest_status": "ready", "sourcing_tenant_id": "tenant-a"})
    first = backfill.run_backfill(tenant_id="tenant-a", limit=50)
    assert first["counts"]["examined"] == 50 and first["page"]["next_cursor"]
    second = backfill.run_backfill(tenant_id="tenant-a", cursor=first["page"]["next_cursor"], limit=50)
    assert second["counts"]["examined"] == 2  # resumes after the page boundary
    assert not db.query("dataset_structure_backfill_runs", snapshot_id="a_other")
    assert not db.query("dataset_structure_backfill_runs", snapshot_id="b_inactive")


def test_successful_backfill_initializes_review_only_after_materialization(legacy_snapshot, monkeypatch):
    backfill.run_backfill(tenant_id="tenant-a")
    monkeypatch.setattr(jobs, "observe_dataset_structure", lambda *_args, **_kwargs: None)
    outcome = jobs.process_one(owner="backfill-test")
    assert outcome and outcome["status"] == "succeeded"
    state = db.query_one("dataset_structure_review_states", snapshot_id=legacy_snapshot.snapshot_id)
    run = db.query_one("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)
    assert state["state"] in {"review_required", "limited"}
    assert run["status"] == "materialized"
    assert not db.query("dataset_structure_review_decision_batches", snapshot_id=legacy_snapshot.snapshot_id)


def test_backfill_endpoint_fails_closed_without_exact_scope(legacy_snapshot, monkeypatch):
    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal", lambda _auth: {
        "tenant_resolved": True, "tenant_id": "tenant-a", "authz_roles": ["admin"]})
    with pytest.raises(HTTPException) as denied:
        dataset_structure.run_structure_backfill(dataset_structure.BackfillIn(), "Bearer x")
    assert denied.value.status_code == 403
    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal", lambda _auth: {
        "tenant_resolved": True, "tenant_id": "tenant-a", "authz_roles": ["data_sourcing.structure.admin"]})
    accepted = dataset_structure.run_structure_backfill(dataset_structure.BackfillIn(limit=1), "Bearer x")
    assert accepted.status_code == 202


def test_schedule_failure_rolls_back_proof_marker_job_state_and_run(legacy_snapshot):
    db.execute("CREATE TRIGGER fail_backfill_job BEFORE INSERT ON dataset_structure_materialization_jobs "
               "BEGIN SELECT RAISE(ABORT, 'injected schedule failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected schedule failure"):
        backfill.run_backfill(tenant_id="tenant-a")
    for table in ("dataset_structure_profile_publication_completions", "dataset_structure_profile_publications",
                  "dataset_structure_materialization_jobs", "dataset_structure_backfill_runs", "dataset_structure_review_states"):
        assert not db.query(table, snapshot_id=legacy_snapshot.snapshot_id)


def test_backfill_schedule_is_atomic_and_idempotent_under_race(legacy_snapshot):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _i: jobs.schedule_backfill(legacy_snapshot.snapshot_id,
                             tenant_id="tenant-a", migration_version=backfill.MIGRATION_VERSION), range(4)))
    assert sum(created for _job, created in outcomes) == 1
    assert len(db.query("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)) == 1
    assert len(db.query("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)) == 1


def test_normal_publication_elevates_same_fingerprint_backfill_proof(legacy_snapshot):
    backfill.run_backfill(tenant_id="tenant-a")
    before = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=legacy_snapshot.snapshot_id)
    assert jobs.record_profile_completion(legacy_snapshot.snapshot_id)
    after = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=legacy_snapshot.snapshot_id)
    assert after["generation"] == before["generation"]
    assert (after["proof_source"], after["proof_migration_version"]) == ("publication", None)


def test_projection_failure_is_retryable_and_never_marks_materialized(legacy_snapshot, monkeypatch):
    backfill.run_backfill(tenant_id="tenant-a")
    monkeypatch.setattr(jobs, "observe_dataset_structure", lambda *_args, **_kwargs: None)
    from domains.aar import dataset_structure_review as review
    monkeypatch.setattr(review, "commit_backfill_projection", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection down")))
    first = jobs.process_one(owner="projection-failure")
    assert first and first["error_code"] == "DSC_R_REVIEW_PROJECTION_FAILED"
    job = db.query_one("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)
    run = db.query_one("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)
    assert job["status"] == "retry_wait" and run["status"] == "scheduled"
    assert not db.query("dataset_structure_review_drafts", snapshot_id=legacy_snapshot.snapshot_id)


def test_cursor_is_signed_and_bound_to_tenant(legacy_snapshot):
    page = backfill.run_backfill(tenant_id="tenant-a", limit=1)
    cursor = page["page"]["next_cursor"]
    assert cursor and "item_" not in cursor
    with pytest.raises(ValueError, match="cursor is invalid"):
        backfill.run_backfill(tenant_id="tenant-b", cursor=cursor)
    with pytest.raises(ValueError, match="cursor is invalid"):
        backfill.run_backfill(tenant_id="tenant-a", cursor=cursor[:-1] + ("0" if cursor[-1] != "0" else "1"))


def test_existing_success_projection_rechecks_current_marker_and_proof(legacy_snapshot):
    backfill.run_backfill(tenant_id="tenant-a")
    job = db.query_one("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)
    db.update("dataset_structure_materialization_jobs", {"job_id": job["job_id"]}, {"status": "succeeded"})
    db.update("dataset_structure_profile_publications", {"snapshot_id": legacy_snapshot.snapshot_id}, {"fingerprint": "stale"})
    item = db.query_one("dq_items", item_id=legacy_snapshot.snapshot_id)
    from domains.aar import dataset_structure_review as review
    with pytest.raises(review.DraftInputError, match="not ready"):
        review.complete_existing_backfill_projection(item, job_id=job["job_id"], generation=job["publication_generation"])
    assert db.query_one("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)["status"] == "scheduled"
    assert not db.query("dataset_structure_review_drafts", snapshot_id=legacy_snapshot.snapshot_id)


def test_normal_provenance_elevation_refuses_mutated_governed_profiles(legacy_snapshot):
    backfill.run_backfill(tenant_id="tenant-a")
    artifact = db.query_one("analysis_artifacts", snapshot_id=legacy_snapshot.snapshot_id, artifact_type="column_profile")
    db.execute("DELETE FROM analysis_artifacts WHERE artifact_id=?", (artifact["artifact_id"],))
    assert jobs.record_profile_completion(legacy_snapshot.snapshot_id) is None
    proof = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=legacy_snapshot.snapshot_id)
    assert proof["proof_source"] == "backfill"


def test_cursor_secret_is_required_outside_explicit_development(legacy_snapshot, monkeypatch):
    monkeypatch.delenv("DSC_BACKFILL_CURSOR_SECRET", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(backfill.BackfillConfigurationError, match="not configured"):
        backfill.run_backfill(tenant_id="tenant-a")
    assert not db.query("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)


def test_worker_finish_retries_when_projection_binding_set_changes(legacy_snapshot, monkeypatch):
    backfill.run_backfill(tenant_id="tenant-a")
    monkeypatch.setattr(jobs, "observe_dataset_structure", lambda *_args, **_kwargs: None)
    from domains.aar import dataset_structure_review as review
    original = review._projection_bindings
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        candidates, fingerprint = original(*args, **kwargs)
        calls += 1
        return candidates, fingerprint if calls == 1 else "mutated-active-assertion-set"
    monkeypatch.setattr(review, "_projection_bindings", changed)
    outcome = jobs.process_one(owner="projection-binding-race")
    assert outcome and outcome["error_code"] == "DSC_R_REVIEW_PROJECTION_FAILED"
    job = db.query_one("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)
    run = db.query_one("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)
    assert job["status"] == "retry_wait" and run["status"] == "scheduled"
    assert not db.query("dataset_structure_review_drafts", snapshot_id=legacy_snapshot.snapshot_id)


def test_existing_success_projection_rejects_changed_assertion_binding_set(legacy_snapshot, monkeypatch):
    backfill.run_backfill(tenant_id="tenant-a")
    job = db.query_one("dataset_structure_materialization_jobs", snapshot_id=legacy_snapshot.snapshot_id)
    db.update("dataset_structure_materialization_jobs", {"job_id": job["job_id"]}, {"status": "succeeded"})
    from domains.aar import dataset_structure_review as review
    original = review._projection_bindings
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        candidates, fingerprint = original(*args, **kwargs)
        calls += 1
        return candidates, fingerprint if calls == 1 else "mutated-active-assertion-set"
    monkeypatch.setattr(review, "_projection_bindings", changed)
    item = db.query_one("dq_items", item_id=legacy_snapshot.snapshot_id)
    with pytest.raises(review.DraftInputError, match="evidence changed"):
        review.complete_existing_backfill_projection(item, job_id=job["job_id"], generation=job["publication_generation"])
    assert db.query_one("dataset_structure_backfill_runs", snapshot_id=legacy_snapshot.snapshot_id)["status"] == "scheduled"
    assert not db.query("dataset_structure_review_drafts", snapshot_id=legacy_snapshot.snapshot_id)
