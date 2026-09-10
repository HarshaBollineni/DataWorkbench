"""Slice-1 DSC Foundation acceptance tests.

These exercise the durable Data-Sourcing hand-off rather than the DSC v1
producer semantics (which are covered by ``test_dataset_structure_context``).
They intentionally use a real SQLite catalogue and AAR store.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

import system_db as db
from domains.aar import (materialization_jobs as jobs, observe_dataset_structure,
                         resolve_dataset_structure_context)
from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
from domains.aar.dataset_structure_producer import DatasetStructureObservationError
from domains.aar.repository import AnalysisArtifactRepository


@pytest.fixture()
def source_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()
    suffix = uuid.uuid4().hex[:10]
    asset_id, snapshot_id = f"asset_{suffix}", f"item_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"foundation-{suffix}", "display_name": f"DSC {suffix}",
        "kind": "dataset", "time_basis": "none", "current_version_no": 1,
        "lifecycle_status": "active", "created_at": now, "updated_at": now,
    })
    db.insert("dq_items", {
        "item_id": snapshot_id, "kind": "dataset", "name": snapshot_id,
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": snapshot_id,
        "intent": "fresh", "ingest_status": "ready", "sourcing_tenant_id": "tenant-a",
    })
    db.insert("dq_item_tables", {
        "item_id": snapshot_id, "table_name": "observations", "row_count": 3,
        "col_count": 2, "columns": ["facility_id", "period"],
    })
    exact = {
        "calculation_method": "exact", "profile_basis": "confirmed_regular_values",
        "total_count": 3, "non_null_count": 3, "null_count": 0, "cardinality": 3,
        "top_k": {"a": 1},
    }
    for column, role, data_type in (("facility_id", "Identifier", "string"),
                                    ("period", "Date", "date")):
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": "observations", "column_name": column,
            "classification": "categorical", "data_type": data_type, "description": "",
            "discrepancies": [], "notes": "", "role": role, "role_reviewed": 1,
            "profile_json": exact, "updated_at": now,
        })
    return SimpleNamespace(asset_id=asset_id, snapshot_id=snapshot_id)


def _active_artifacts(snapshot_id: str, kind: str) -> list:
    return AnalysisArtifactRepository().list(snapshot_id=snapshot_id, artifact_type=kind, status="active")


def _profile_save_args(source_snapshot):
    return {
        "artifact_type": "snapshot_profile", "asset_id": source_snapshot.asset_id,
        "snapshot_id": source_snapshot.snapshot_id, "population_fingerprint": "p" * 64,
        "methodology_fingerprint": "m" * 64, "scope": "universal", "table": "observations",
    }


def _materialize_schema_and_context(source_snapshot) -> None:
    repo = AnalysisArtifactRepository()
    observe_dataset_structure(
        repo, source_snapshot.snapshot_id, tables=["observations"],
        predicates=["table.physical/schema_column"], created_by="foundation-test",
    )
    resolve_dataset_structure_context(repo, {
        "protocol_version": "1", "supported_context_versions": ["1"],
        "snapshot": {"asset_id": source_snapshot.asset_id,
                     "snapshot_id": source_snapshot.snapshot_id},
        "as_of": "latest", "tables": ["observations"],
        "consumer_id": "foundation_acceptance",
        "selectors": [{
            "selector_id": "schema", "subject": {"kind": "table", "table": "observations"},
            "predicate": "table.physical/schema_column", "requirement": "required",
            "accepted_resolution_states": ["observed"],
        }],
    }, created_by="foundation-test")


def test_complete_profile_publication_enqueues_once_and_exact_generation_reuses(source_snapshot):
    ids = persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    assert ids
    marker = db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id)
    assert marker and marker["generation"] == 1
    rows = db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)
    assert len(rows) == 1 and rows[0]["status"] == "queued"

    same, created = jobs.enqueue(source_snapshot.snapshot_id)
    assert not created and same["job_id"] == rows[0]["job_id"]
    assert len(db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)) == 1


def test_enqueue_failure_does_not_change_ready_ingest_or_profile_publication(source_snapshot, monkeypatch):
    original_enqueue = jobs.enqueue
    def broken_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue temporarily unavailable")

    monkeypatch.setattr(jobs, "enqueue", broken_enqueue)
    assert persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    item = db.query_one("dq_items", item_id=source_snapshot.snapshot_id)
    assert item["ingest_status"] == "ready"
    assert db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id)
    assert _active_artifacts(source_snapshot.snapshot_id, "column_profile")
    assert not db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)

    # Do not call ``monkeypatch.undo`` here: it also restores this test's
    # database/artifact-root isolation patches.
    monkeypatch.setattr(jobs, "enqueue", original_enqueue)
    repaired = jobs.reconcile(source_snapshot.snapshot_id)
    assert repaired == {"recovered": 0, "queued": 1}


def test_partial_profile_publication_never_creates_marker_or_job(source_snapshot, monkeypatch):
    repo = AnalysisArtifactRepository()
    original_save = repo.save
    calls = 0

    def crash_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("simulated crash")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(repo, "save", crash_after_first)
    with pytest.raises(RuntimeError, match="simulated crash"):
        persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, artifact_repository=repo)
    assert db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id) is None
    assert not db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)


def test_reconciliation_repairs_missing_marker_only_after_complete_profiles(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    db.execute("DELETE FROM dataset_structure_materialization_jobs")
    db.execute("DELETE FROM dataset_structure_profile_publications")
    result = jobs.reconcile(source_snapshot.snapshot_id)
    assert result == {"recovered": 0, "queued": 1}
    assert db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id)


def test_refreshed_profile_generation_revokes_old_live_job_and_queues_new(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    first = db.query_one("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)
    db.update("variable_inventory", {"item_id": source_snapshot.snapshot_id,
                                      "table_name": "observations", "column_name": "facility_id"},
              {"description": "review refresh", "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    marker = db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id)
    rows = db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id,
                    order_by="created_at")
    assert marker["generation"] == 2
    assert len(rows) == 2
    assert rows[0]["job_id"] == first["job_id"] and rows[0]["status"] == "revoked"
    assert rows[1]["publication_generation"] == 2 and rows[1]["status"] == "queued"


def test_exact_profile_reuse_preserves_snapshot_row_and_publication_attempt(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    with db.get_conn() as conn:
        before = tuple(conn.execute(
            "SELECT * FROM dq_items WHERE item_id=?", (source_snapshot.snapshot_id,)
        ).fetchone())

    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")

    with db.get_conn() as conn:
        after = tuple(conn.execute(
            "SELECT * FROM dq_items WHERE item_id=?", (source_snapshot.snapshot_id,)
        ).fetchone())
    assert after == before


def test_profile_replacement_fences_before_the_first_profile_write(source_snapshot, monkeypatch):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    before = db.query_one("dq_items", item_id=source_snapshot.snapshot_id)["profile_publication_attempt_token"]
    db.update("variable_inventory", {"item_id": source_snapshot.snapshot_id,
                                      "table_name": "observations", "column_name": "facility_id"},
              {"description": "review refresh", "updated_at": db.now_ist()})
    repo = AnalysisArtifactRepository()
    original_save, checked = repo.save, []

    def assert_fenced(*args, **kwargs):
        if kwargs.get("artifact_type") in {"column_profile", "table_profile", "table_inventory_profile"}:
            token = db.query_one("dq_items", item_id=source_snapshot.snapshot_id)["profile_publication_attempt_token"]
            assert token and token != before
            checked.append(kwargs["artifact_type"])
        return original_save(*args, **kwargs)

    monkeypatch.setattr(repo, "save", assert_fenced)
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test", artifact_repository=repo)
    assert checked


def test_concurrent_enqueue_has_one_live_winner(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    # Make retry creation exercise the unique live-work constraint.
    db.execute("DELETE FROM dataset_structure_materialization_jobs")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _unused: jobs.enqueue(source_snapshot.snapshot_id), range(16)))
    assert sum(created for _job, created in values) == 1
    rows = db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)
    assert len(rows) == 1 and rows[0]["status"] == "queued"


def test_claim_is_fenced_and_lost_fence_blocks_finish(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    claimed = jobs._claim_one("worker-a")
    assert claimed and claimed["fencing_token"] == 1
    assert jobs._claim_one("worker-b") is None
    db.execute("UPDATE dataset_structure_materialization_jobs SET lease_owner='worker-b',fencing_token=2 "
               "WHERE job_id=?", (claimed["job_id"],))
    with pytest.raises(Exception, match="DSC_R_WORKER_LEASE_LOST"):
        jobs._lease_guard(claimed, "worker-a")
    assert not jobs._finish(claimed, "worker-a", None, {"tables_total": 1})
    assert db.query_one("dataset_structure_materialization_jobs", job_id=claimed["job_id"])["status"] == "running"


def test_expired_claim_retries_then_terminal_and_public_status_is_aggregate_only(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    row = jobs._claim_one("worker-a")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute("UPDATE dataset_structure_materialization_jobs SET lease_expires_at=? WHERE job_id=?",
               (expired, row["job_id"]))
    assert jobs.reconcile(source_snapshot.snapshot_id, ensure_job=False)["recovered"] == 1
    recovered = db.query_one("dataset_structure_materialization_jobs", job_id=row["job_id"])
    assert recovered["status"] == "retry_wait" and recovered["closed_error_code"] == "DSC_R_WORKER_LEASE_EXPIRED"
    public = jobs.public_status(jobs._decode(recovered))
    assert set(public) == {"job_id", "status", "attempt_count", "max_attempts", "progress", "retry_after_ms", "error_code"}
    assert "publication_fingerprint" not in public and "tenant_id" not in public


def test_retry_attempts_become_terminal_and_deadline_is_a_closed_error(source_snapshot, monkeypatch):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")

    def fail_observation(*_args, **_kwargs):
        raise DatasetStructureObservationError("DSC_R_MATERIALIZATION_FAILED")

    monkeypatch.setattr(jobs, "observe_dataset_structure", fail_observation)
    for attempt in range(3):
        outcome = jobs.process_one(owner="retry-worker")
        assert outcome and outcome["error_code"] == "DSC_R_MATERIALIZATION_FAILED"
        row = db.query_one("dataset_structure_materialization_jobs", job_id=outcome["job_id"])
        assert row["attempt_count"] == attempt + 1
        if attempt < 2:
            assert row["status"] == "retry_wait"
            db.execute("UPDATE dataset_structure_materialization_jobs SET available_at=? WHERE job_id=?",
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), outcome["job_id"]))
        else:
            assert row["status"] == "failed"

    # A deadline is a closed retry error and cannot be turned into a success.
    db.execute("DELETE FROM dataset_structure_materialization_jobs")
    jobs.enqueue(source_snapshot.snapshot_id)
    monkeypatch.setattr(jobs, "JOB_DEADLINE_SECONDS", -1)
    deadline = jobs.process_one(owner="deadline-worker")
    assert deadline and deadline["error_code"] == "DSC_R_JOB_DEADLINE_EXCEEDED"


def test_lost_guard_blocks_save_reuse_batch_and_supersession(source_snapshot):
    repo = AnalysisArtifactRepository()
    old = repo.save({"aggregate_only": True}, **_profile_save_args(source_snapshot))

    def lost():
        raise DatasetStructureObservationError("DSC_R_WORKER_LEASE_LOST")

    with pytest.raises(DatasetStructureObservationError):
        repo.save({"aggregate_only": True}, precommit_guard=lost, **_profile_save_args(source_snapshot))
    with pytest.raises(DatasetStructureObservationError):
        repo.save_batch([{"payload": {"aggregate_only": False}, "version": True,
                         **_profile_save_args(source_snapshot)}],
                        precommit_guard=lost)
    with pytest.raises(DatasetStructureObservationError):
        repo.supersede(old.artifact.artifact_id, precommit_guard=lost)
    assert repo.get_metadata(old.artifact.artifact_id).status == "active"


def test_revocation_is_fenced_and_has_a_non_disclosing_public_projection(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    claimed = jobs._claim_one("shutdown-worker")
    assert claimed
    assert jobs.revoke_owned("shutdown-worker") == 1
    with pytest.raises(DatasetStructureObservationError, match="DSC_R_WORKER_LEASE_LOST"):
        jobs._lease_guard(claimed, "shutdown-worker")
    internal = jobs._decode(db.query_one("dataset_structure_materialization_jobs", job_id=claimed["job_id"]))
    assert internal["status"] == "revoked"
    assert jobs.public_status(internal)["status"] == "failed"
    assert jobs.public_status(internal)["error_code"] == "DSC_R_PUBLICATION_REPLACED"


def test_router_hides_tenantless_cross_tenant_and_unknown_items(source_snapshot, monkeypatch):
    from fastapi import HTTPException
    from routers import dataset_structure

    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal",
                        lambda _auth: {"tenant_resolved": True, "tenant_id": "tenant-a"})
    assert dataset_structure._item_or_404(source_snapshot.snapshot_id, "Bearer ok")[1]["item_id"] == source_snapshot.snapshot_id
    cases = [
        ("missing", {"tenant_resolved": True, "tenant_id": "tenant-a"}),
        (source_snapshot.snapshot_id, {"tenant_resolved": False, "tenant_id": "tenant-a"}),
        (source_snapshot.snapshot_id, {"tenant_resolved": True, "tenant_id": "tenant-b"}),
    ]
    for item_id, principal in cases:
        monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal", lambda _auth, p=principal: p)
        with pytest.raises(HTTPException) as error:
            dataset_structure._item_or_404(item_id, "Bearer test")
        assert error.value.status_code == 404
        assert error.value.detail == "Unknown dataset structure materialization."


def test_diagnostic_wipe_preserves_source_owned_dsc_state(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    _materialize_schema_and_context(source_snapshot)
    # The reset itself is the acceptance boundary: no diagnostic run is needed
    # for an all-diagnostics wipe to prove source-owned records are untouched.
    before = {
        "marker": db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id),
        "jobs": db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id),
        "profiles": _active_artifacts(source_snapshot.snapshot_id, "column_profile"),
        "assertions": _active_artifacts(source_snapshot.snapshot_id, "dataset_structure_assertion"),
        "contexts": _active_artifacts(source_snapshot.snapshot_id, "dataset_structure_context"),
    }
    db.wipe_diagnostics()
    assert db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id) == before["marker"]
    assert db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id) == before["jobs"]
    assert _active_artifacts(source_snapshot.snapshot_id, "column_profile") == before["profiles"]
    assert _active_artifacts(source_snapshot.snapshot_id, "dataset_structure_assertion") == before["assertions"]
    assert _active_artifacts(source_snapshot.snapshot_id, "dataset_structure_context") == before["contexts"]


def test_full_source_cleanup_removes_all_dsc_state(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    _materialize_schema_and_context(source_snapshot)
    db.wipe_all_items()
    assert db.query_one("dq_items", item_id=source_snapshot.snapshot_id) is None
    assert db.query_one("dataset_structure_profile_publications", snapshot_id=source_snapshot.snapshot_id) is None
    assert not db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)
    for kind in ("column_profile", "dataset_structure_assertion", "dataset_structure_context"):
        assert not _active_artifacts(source_snapshot.snapshot_id, kind)


def test_failed_refresh_over_existing_profiles_never_synthesizes_completion_or_job(source_snapshot, monkeypatch):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    before = db.query_one("dataset_structure_profile_publication_completions",
                          snapshot_id=source_snapshot.snapshot_id)
    db.execute("DELETE FROM dataset_structure_materialization_jobs")
    db.update("variable_inventory", {"item_id": source_snapshot.snapshot_id,
                                      "table_name": "observations", "column_name": "facility_id"},
              {"description": "changed but publication fails", "updated_at": db.now_ist()})
    repo = AnalysisArtifactRepository()
    monkeypatch.setattr(repo, "save", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("refresh failed")))
    with pytest.raises(RuntimeError, match="refresh failed"):
        persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, artifact_repository=repo)
    assert db.query_one("dataset_structure_profile_publication_completions",
                        snapshot_id=source_snapshot.snapshot_id) == before
    jobs.reconcile(source_snapshot.snapshot_id)
    assert db.query_one("dataset_structure_profile_publications",
                        snapshot_id=source_snapshot.snapshot_id)["generation"] == before["generation"]
    assert not db.query("dataset_structure_materialization_jobs",
                        snapshot_id=source_snapshot.snapshot_id)


def test_replaced_marker_and_completion_fence_stale_worker_everywhere(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    stale = jobs._claim_one("stale-worker")
    db.update("variable_inventory", {"item_id": source_snapshot.snapshot_id,
                                      "table_name": "observations", "column_name": "facility_id"},
              {"description": "new publication", "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    with pytest.raises(DatasetStructureObservationError, match="DSC_R_WORKER_LEASE_LOST"):
        jobs._lease_guard(stale, "stale-worker")
    assert not jobs._heartbeat(stale, "stale-worker")
    assert not jobs._finish(stale, "stale-worker", None, {"tables_total": 1})


def test_item_reconciliation_expires_only_its_own_snapshot(source_snapshot):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    first = jobs._claim_one("worker-a")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute("UPDATE dataset_structure_materialization_jobs SET lease_expires_at=? WHERE job_id=?",
               (expired, first["job_id"]))
    clone = dict(db.query_one("dataset_structure_materialization_jobs", job_id=first["job_id"]))
    clone["job_id"] = f"dscj_other_{uuid.uuid4().hex[:8]}"; clone["snapshot_id"] = "unrelated-snapshot"
    clone["lease_expires_at"] = expired; clone["lease_owner"] = "worker-b"; clone["status"] = "running"
    db.insert("dataset_structure_materialization_jobs", clone)
    assert jobs.reconcile(source_snapshot.snapshot_id, ensure_job=False)["recovered"] == 1
    assert db.query_one("dataset_structure_materialization_jobs", job_id=first["job_id"])["status"] == "retry_wait"
    assert db.query_one("dataset_structure_materialization_jobs", job_id=clone["job_id"])["status"] == "running"


def test_partial_profile_replacement_immediately_fences_old_worker(source_snapshot, monkeypatch):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    old = jobs._claim_one("old-worker")
    db.update("variable_inventory", {"item_id": source_snapshot.snapshot_id,
                                      "table_name": "observations", "column_name": "facility_id"},
              {"description": "refresh begins", "updated_at": db.now_ist()})
    repo = AnalysisArtifactRepository(); original = repo.save; calls = 0
    def replace_once_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("partial refresh")
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, "save", replace_once_then_fail)
    with pytest.raises(RuntimeError, match="partial refresh"):
        persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, artifact_repository=repo)
    with pytest.raises(DatasetStructureObservationError, match="DSC_R_WORKER_LEASE_LOST"):
        jobs._lease_guard(old, "old-worker")
    assert not jobs._finish(old, "old-worker", None, {"tables_total": 1})


def test_completion_proof_failure_is_not_a_successful_profile_publication(source_snapshot, monkeypatch):
    monkeypatch.setattr(jobs, "record_profile_completion", lambda _snapshot: None)
    with pytest.raises(RuntimeError, match="DSC_R_PROFILE_PUBLICATION_PROOF_FAILED"):
        persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    assert db.query_one("dataset_structure_profile_publication_completions",
                        snapshot_id=source_snapshot.snapshot_id) is None
    assert db.query_one("dataset_structure_profile_publications",
                        snapshot_id=source_snapshot.snapshot_id) is None
    assert not db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)


def test_tenantless_legacy_snapshot_persists_profiles_without_dsc_handoff(source_snapshot):
    db.update("dq_items", {"item_id": source_snapshot.snapshot_id}, {"sourcing_tenant_id": None})

    assert persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")

    assert _active_artifacts(source_snapshot.snapshot_id, "column_profile")
    assert db.query_one("dataset_structure_profile_publication_completions",
                        snapshot_id=source_snapshot.snapshot_id) is None
    assert db.query_one("dataset_structure_profile_publications",
                        snapshot_id=source_snapshot.snapshot_id) is None
    assert not db.query("dataset_structure_materialization_jobs", snapshot_id=source_snapshot.snapshot_id)
    assert db.query_one("dq_items", item_id=source_snapshot.snapshot_id)[
        "profile_publication_attempt_token"
    ] is None


@pytest.mark.parametrize("column,value", [("sourcing_tenant_id", "tenant-b"),
                                             ("dataset_family_id", "asset-rebound")])
def test_guard_rejects_tenant_or_asset_rebinding(source_snapshot, column, value):
    persist_snapshot_profile_artifacts(source_snapshot.snapshot_id, actor="test")
    claimed = jobs._claim_one("guard-worker")
    db.update("dq_items", {"item_id": source_snapshot.snapshot_id}, {column: value})
    with pytest.raises(DatasetStructureObservationError, match="DSC_R_WORKER_LEASE_LOST"):
        jobs._lease_guard(claimed, "guard-worker")


def test_global_reconciliation_keyset_cursor_progresses_beyond_one_batch(source_snapshot):
    db.execute("DELETE FROM dataset_structure_materialization_reconcile_cursor")
    now = db.now_ist()
    for index in range(55):
        db.insert("dq_items", {"item_id": f"page_{index:03d}", "kind": "dataset",
                  "name": "page", "status": "profiled", "created_at": now, "updated_at": now,
                  "snapshot_status": "active", "ingest_status": "ready", "sourcing_tenant_id": "tenant-a"})
    jobs.reconcile(None, ensure_job=False)
    first = db.query_one("dataset_structure_materialization_reconcile_cursor", cursor_name="global")
    jobs.reconcile(None, ensure_job=False)
    second = db.query_one("dataset_structure_materialization_reconcile_cursor", cursor_name="global")
    assert first["last_snapshot_id"] != second["last_snapshot_id"]
    assert second["last_snapshot_id"] >= "page_054"
