"""D06's private authenticated-launch to headerless-SSE handoff."""
from __future__ import annotations

from datetime import datetime, timedelta
import time

import pytest

import system_db as db
from domains.test_lab.shared import run_state


@pytest.fixture
def isolated_system_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    db.init_schema()
    return db


def test_d06_context_persists_across_independent_connections_and_rebinds_safely(isolated_system_db):
    run_id = "drun_d06_context_handoff"
    run_state.bind_trusted_execution_tenant(run_id, "tenant-a")
    # The lookup opens another connection: this models a headerless SSE request
    # arriving on a different worker after the authenticated launch returned.
    assert run_state.trusted_execution_tenant(run_id) == "tenant-a"


def test_d06_context_same_tenant_rebind_refreshes_expiry(isolated_system_db):
    run_id = "drun_d06_context_refresh"
    initial = (datetime.fromisoformat(db.now_ist()) + timedelta(seconds=1)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO diagnostic_execution_context (run_id,tenant_id,bound_at,expires_at) "
            "VALUES (?,?,?,?)", (run_id, "tenant-a", db.now_ist(), initial),
        )
    assert run_state.bind_trusted_execution_tenant(run_id, "tenant-a") is True
    refreshed = db.query_one("diagnostic_execution_context", run_id=run_id)
    assert refreshed["tenant_id"] == "tenant-a"
    assert refreshed["expires_at"] > initial
    run_state.bind_trusted_execution_tenant(run_id, "tenant-a")
    with pytest.raises(run_state.ManifestError, match="another tenant"):
        run_state.bind_trusted_execution_tenant(run_id, "tenant-b")
    assert run_state.trusted_execution_tenant(run_id) == "tenant-a"


def test_d06_context_consumption_and_expiry_cleanup(isolated_system_db):
    run_id = "drun_d06_context_cleanup"
    run_state.bind_trusted_execution_tenant(run_id, "tenant-a")
    run_state.clear_trusted_execution_tenant(run_id, "tenant-a")
    assert run_state.trusted_execution_tenant(run_id) is None

    expired = (datetime.fromisoformat(db.now_ist()) - timedelta(seconds=1)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO diagnostic_execution_context (run_id,tenant_id,bound_at,expires_at) "
            "VALUES (?,?,?,?)", (run_id, "tenant-a", expired, expired),
        )
    assert run_state.trusted_execution_tenant(run_id) is None
    assert db.query("diagnostic_execution_context", run_id=run_id) == []


def test_d06_context_storage_lock_is_bounded_and_fail_open(isolated_system_db):
    lock = db.get_conn()
    lock.execute("BEGIN EXCLUSIVE")
    try:
        started = time.monotonic()
        assert run_state.bind_trusted_execution_tenant("drun_context_locked", "tenant-a") is False
        assert time.monotonic() - started < 0.35

        started = time.monotonic()
        assert run_state.trusted_execution_tenant("drun_context_locked") is None
        assert time.monotonic() - started < 0.35

        started = time.monotonic()
        assert run_state.clear_trusted_execution_tenant("drun_context_locked", "tenant-a") is False
        assert time.monotonic() - started < 0.35
    finally:
        lock.rollback()
        lock.close()
