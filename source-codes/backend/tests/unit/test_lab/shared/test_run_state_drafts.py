from datetime import datetime, timedelta

import pytest

from domains.test_lab.shared import run_state


def test_latest_draft_returns_newest_resumable_setup(monkeypatch):
    rows = [{
        "run_id": "drun_new",
        "item_id": "item_1",
        "diagnostic_id": 2,
        "status": "draft",
        "created_at": "2026-09-03T10:00:00",
        "manifest_json": {
            "updated_at": "2026-09-03T10:05:00",
            "scope": {"selected_features": ["balance", "occupancy"]},
        },
    }]
    monkeypatch.setattr(run_state.db, "query", lambda *args, **kwargs: rows)

    draft = run_state.latest_draft("item_1", 2)

    assert draft == {
        "run_id": "drun_new",
        "item_id": "item_1",
        "diagnostic_id": 2,
        "status": "draft",
        "created_at": "2026-09-03T10:00:00",
        "last_saved_at": "2026-09-03T10:05:00",
        "selected_feature_count": 2,
    }


def test_latest_draft_archives_older_duplicate_setups(monkeypatch):
    rows = [
        {"run_id": "drun_new", "item_id": "item_1", "diagnostic_id": 6,
         "status": "draft", "created_at": "2026-09-03T10:00:00", "manifest_json": {}},
        {"run_id": "drun_old", "item_id": "item_1", "diagnostic_id": 6,
         "status": "draft", "created_at": "2026-09-03T09:00:00", "manifest_json": {}},
    ]
    discarded = []
    monkeypatch.setattr(run_state.db, "query", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        run_state, "discard_draft",
        lambda run_id, actor, reason: discarded.append((run_id, actor, reason)),
    )

    selected = run_state.latest_draft("item_1", 6, actor="reviewer")

    assert selected["run_id"] == "drun_new"
    assert discarded == [("drun_old", "reviewer", "superseded_duplicate_draft")]


def test_discard_drafts_archives_every_open_setup_with_audit(monkeypatch):
    rows = [
        {"run_id": "drun_1", "manifest_json": {"status": "draft"}},
        {"run_id": "drun_2", "manifest_json": {"status": "draft"}},
    ]
    updates = []
    monkeypatch.setattr(run_state.db, "query", lambda *args, **kwargs: rows)
    monkeypatch.setattr(run_state.db, "now_ist", lambda: "2026-09-03T10:15:00")
    monkeypatch.setattr(
        run_state.db, "update",
        lambda table, where, changes: updates.append((table, where, changes)),
    )

    discarded = run_state.discard_drafts("item_1", 2, actor="reviewer")

    assert discarded == 2
    assert [changes["status"] for _, _, changes in updates] == [
        "discarded", "discarded",
    ]
    assert all(
        changes["manifest_json"]["discarded_by"] == "reviewer"
        for _, _, changes in updates
    )


def test_discard_draft_archives_only_the_selected_setup(monkeypatch):
    run = {
        "run_id": "drun_selected",
        "item_id": "item_1",
        "diagnostic_id": 2,
        "status": "draft",
        "manifest_json": {"status": "draft"},
    }
    updates = []
    monkeypatch.setattr(run_state, "get_run", lambda run_id: run)
    monkeypatch.setattr(run_state.db, "now_ist", lambda: "2026-09-03T10:20:00")
    monkeypatch.setattr(
        run_state.db, "update",
        lambda table, where, changes: updates.append((table, where, changes)),
    )

    result = run_state.discard_draft("drun_selected", actor="reviewer")

    assert result["run_id"] == "drun_selected"
    assert result["status"] == "discarded"
    assert updates[0][1] == {"run_id": "drun_selected"}
    assert updates[0][2]["manifest_json"]["discard_reason"] == "user_discarded"


def test_discard_draft_persists_against_the_governed_schema():
    run_state.db.init_schema()
    run_id = "drun_discard_schema_check"
    run_state.db.insert("diag_runs", {
        "run_id": run_id,
        "item_id": "item_schema_check",
        "diagnostic_id": 2,
        "manifest_json": {"run_id": run_id, "status": "draft"},
        "status": "draft",
        "engine_versions_json": {},
        "created_at": "2026-09-03T10:00:00",
        "started_at": None,
        "finished_at": None,
    })

    run_state.discard_draft(run_id, actor="reviewer")

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert stored["status"] == "discarded"
    assert stored["manifest_json"]["discarded_by"] == "reviewer"
    assert stored["manifest_json"]["discard_reason"] == "user_discarded"


def _insert_lifecycle_run(run_id: str, *, lease_expires_at: str | None = None,
                          execution_owner: str | None = None) -> None:
    now = run_state.db.now_ist()
    run_state.db.insert("diag_runs", {
        "run_id": run_id, "item_id": f"item_{run_id}", "diagnostic_id": 8,
        "manifest_json": {"run_id": run_id, "status": "running"},
        "status": "running", "engine_versions_json": {}, "created_at": now,
        "started_at": now, "finished_at": None, "heartbeat_at": now,
        "lease_expires_at": lease_expires_at, "execution_owner": execution_owner,
        "status_detail_json": None,
    })


def test_expired_worker_lease_is_recovered_as_a_safe_failure():
    run_state.db.init_schema()
    now = datetime.fromisoformat(run_state.db.now_ist())
    run_id = "drun_expired_lease_schema_check"
    _insert_lifecycle_run(
        run_id,
        lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
        execution_owner="dead-worker",
    )

    outcome = run_state.recover_expired_runs(run_ids={run_id})

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert outcome["run_ids"] == [run_id]
    assert stored["status"] == "failed"
    assert stored["finished_at"]
    assert stored["execution_owner"] is None
    assert stored["status_detail_json"]["code"] == "worker_lease_expired"
    assert "worker stopped responding" in stored["status_detail_json"]["message"]


def test_active_worker_lease_is_not_recovered():
    run_state.db.init_schema()
    now = datetime.fromisoformat(run_state.db.now_ist())
    run_id = "drun_active_lease_schema_check"
    _insert_lifecycle_run(
        run_id,
        lease_expires_at=(now + timedelta(minutes=5)).isoformat(),
        execution_owner="live-worker",
    )

    outcome = run_state.recover_expired_runs(run_ids={run_id})

    assert outcome["recovered"] == 0
    assert run_state.db.query_one("diag_runs", run_id=run_id)["status"] == "running"
    run_state.db.update("diag_runs", {"run_id": run_id}, {
        "status": "done", "finished_at": run_state.db.now_ist(),
        "lease_expires_at": None, "execution_owner": None,
    })


def test_live_worker_lease_refuses_a_duplicate_executor():
    run_state.db.init_schema()
    run_id = "drun_duplicate_executor_schema_check"
    _insert_lifecycle_run(run_id)

    assert run_state.claim_run_lease(run_id, "worker-one") is True
    with pytest.raises(run_state.RunAlreadyExecuting, match="already executing"):
        run_state.claim_run_lease(run_id, "worker-two")

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert stored["status"] == "running"
    assert stored["execution_owner"] == "worker-one"
    run_state.db.update("diag_runs", {"run_id": run_id}, {
        "status": "done", "finished_at": run_state.db.now_ist(),
        "lease_expires_at": None, "execution_owner": None,
    })


def test_managed_execution_sanitizes_failure_and_releases_lease():
    run_state.db.init_schema()
    run_id = "drun_managed_failure_schema_check"
    _insert_lifecycle_run(run_id)

    with pytest.raises(RuntimeError, match="secret provider detail"):
        with run_state.managed_run_execution(
            run_id, channel="test", actor="reviewer",
        ) as lease:
            assert lease.claimed is True
            raise RuntimeError("secret provider detail")

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert stored["status"] == "failed"
    assert stored["status_detail_json"]["code"] == "execution_failed"
    assert "secret provider detail" not in str(stored["status_detail_json"])
    assert stored["execution_owner"] is None


def test_managed_execution_clears_lease_detail_after_success():
    run_state.db.init_schema()
    run_id = "drun_managed_success_schema_check"
    _insert_lifecycle_run(run_id)

    with run_state.managed_run_execution(
        run_id, channel="test", actor="reviewer",
    ):
        run_state.db.update("diag_runs", {"run_id": run_id}, {
            "status": "done", "finished_at": run_state.db.now_ist(),
        })

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert stored["status"] == "done"
    assert stored["status_detail_json"] is None
    assert stored["execution_owner"] is None


def test_managed_execution_finalizes_a_pre_freeze_stream_failure():
    run_state.db.init_schema()
    run_id = "drun_managed_draft_failure_schema_check"
    now = run_state.db.now_ist()
    run_state.db.insert("diag_runs", {
        "run_id": run_id, "item_id": f"item_{run_id}", "diagnostic_id": 4,
        "manifest_json": {"run_id": run_id, "status": "draft"},
        "status": "draft", "engine_versions_json": {}, "created_at": now,
        "started_at": None, "finished_at": None,
    })

    with run_state.managed_run_execution(
        run_id, channel="legacy-stream", actor="reviewer",
    ):
        pass

    stored = run_state.db.query_one("diag_runs", run_id=run_id)
    assert stored["status"] == "failed"
    assert stored["status_detail_json"]["code"] == "execution_stopped_before_completion"
