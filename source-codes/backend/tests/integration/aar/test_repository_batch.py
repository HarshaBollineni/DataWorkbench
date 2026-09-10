"""Focused contract coverage for all-or-nothing AAR JSON batch persistence."""
from __future__ import annotations

import sqlite3
import threading
import uuid

import pytest

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from domains.aar.repository import AnalysisArtifactRepository, ArtifactConflictError
from domains.aar.types import ArtifactTypeDescriptor, register_artifact_type


def _args(artifact_type: str, *, table: str) -> dict[str, object]:
    token = uuid.uuid4().hex
    return {
        "artifact_type": artifact_type,
        "asset_id": f"asset_batch_{token}",
        "snapshot_id": f"snapshot_batch_{token}",
        "population_fingerprint": stable_fingerprint({"population": token}),
        "methodology_fingerprint": stable_fingerprint({"method": token}),
        "scope": "universal",
        "table": table,
    }


@pytest.fixture
def batch_repo(tmp_path):
    db.init_schema()
    artifact_type = f"batch_test_{uuid.uuid4().hex[:10]}"
    register_artifact_type(ArtifactTypeDescriptor(
        artifact_type=artifact_type, display_name="Batch test", description="test-only", owner="tests",
    ))
    return AnalysisArtifactRepository(tmp_path / "artifacts"), artifact_type


def _entry(payload, args):
    return {"payload": payload, **args}


def test_save_batch_persists_two_rows_and_lineage_edges(batch_repo):
    repo, artifact_type = batch_repo
    source = repo.save({"source": True}, **_args(artifact_type, table="source")).artifact
    first = _entry({"one": 1}, _args(artifact_type, table="one"))
    second_args = _args(artifact_type, table="two")
    second_args["source_artifact_ids"] = (source.artifact_id,)

    outcomes = repo.save_batch([first, _entry({"two": 2}, second_args)])

    assert [outcome.outcome for outcome in outcomes] == ["created", "created"]
    assert all((repo.root / outcome.artifact.payload_path).is_file() for outcome in outcomes)
    edges = db.query("analysis_artifact_sources", artifact_id=outcomes[1].artifact.artifact_id)
    assert [(edge["source_artifact_id"], edge["role"]) for edge in edges] == [
        (source.artifact_id, "source")
    ]


def test_save_batch_second_insert_failure_rolls_back_rows_edges_and_files(batch_repo, monkeypatch):
    repo, artifact_type = batch_repo
    source = repo.save({"source": True}, **_args(artifact_type, table="source")).artifact
    first = _entry({"one": 1}, _args(artifact_type, table="one"))
    second_args = _args(artifact_type, table="two")
    second_args["source_artifact_ids"] = (source.artifact_id,)
    original, calls = db.insert_analysis_artifact, 0

    def fail_second(row, refs, *, conn=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.IntegrityError("injected second insert failure")
        return original(row, refs, conn=conn)

    monkeypatch.setattr(db, "insert_analysis_artifact", fail_second)
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_batch([first, _entry({"two": 2}, second_args)])

    assert len(repo.list(artifact_type=artifact_type, status="active")) == 1
    assert db.query("analysis_artifact_sources", source_artifact_id=source.artifact_id) == []
    assert db.query_one("analysis_artifacts", artifact_id=source.artifact_id)["status"] == "active"
    assert list(repo.root.glob("*.json")) == [repo.root / source.payload_path]


def test_save_batch_supersession_failure_rolls_back_new_artifact_and_lifecycle(batch_repo):
    repo, artifact_type = batch_repo
    prior = repo.save({"prior": True}, **_args(artifact_type, table="prior")).artifact
    with pytest.raises(KeyError):
        repo.save_batch(
            [_entry({"replacement": True}, _args(artifact_type, table="replacement"))],
            supersessions=[{"artifact_id": prior.artifact_id, "by_artifact_id": "missing"}],
        )

    assert repo.get_metadata(prior.artifact_id).status == "active"
    assert db.query("analysis_artifact_events", artifact_id=prior.artifact_id) == []
    assert len(repo.list(artifact_type=artifact_type)) == 1
    assert list(repo.root.glob("*.json")) == [repo.root / prior.payload_path]


def test_save_batch_guard_failure_has_zero_mutation(batch_repo):
    repo, artifact_type = batch_repo
    with pytest.raises(RuntimeError, match="changed"):
        repo.save_batch(
            [_entry({"one": 1}, _args(artifact_type, table="one"))],
            precommit_guard=lambda: (_ for _ in ()).throw(RuntimeError("changed")),
        )

    assert repo.list(artifact_type=artifact_type) == []
    assert list(repo.root.glob("*.json")) == []


def test_save_batch_guard_verifies_source_with_its_transaction_connection(batch_repo, monkeypatch):
    repo, artifact_type = batch_repo
    source = repo.save({"source": True}, **_args(artifact_type, table="source")).artifact
    integrity_connections = []
    original_mark_integrity = repo._mark_integrity

    def capture_integrity(artifact_id, status, *, conn=None):
        integrity_connections.append(conn)
        return original_mark_integrity(artifact_id, status, conn=conn)

    monkeypatch.setattr(repo, "_mark_integrity", capture_integrity)

    def guard(conn=None):
        if conn is not None:
            metadata, payload = repo.get(source.artifact_id, conn=conn)
            assert metadata.integrity_status == "verified"
            assert payload == {"source": True}

    repo.save_batch([_entry({"one": 1}, _args(artifact_type, table="one"))],
                    precommit_guard=guard)

    assert integrity_connections and all(conn is not None for conn in integrity_connections)


def test_save_batch_retry_reuses_exact_entries(batch_repo):
    repo, artifact_type = batch_repo
    entry = _entry({"one": 1}, _args(artifact_type, table="one"))
    first = repo.save_batch([entry])[0]
    retry = repo.save_batch([entry])[0]

    assert (first.outcome, retry.outcome) == ("created", "reused")
    assert first.artifact.artifact_id == retry.artifact.artifact_id
    assert list(repo.root.glob("*.json")) == [repo.root / first.artifact.payload_path]


def test_save_batch_identity_conflict_leaves_whole_batch_unwritten(batch_repo):
    repo, artifact_type = batch_repo
    args = _args(artifact_type, table="same")
    with pytest.raises(ArtifactConflictError):
        repo.save_batch([_entry({"value": 1}, args), _entry({"value": 2}, args)])

    assert repo.list(artifact_type=artifact_type) == []
    assert list(repo.root.glob("*.json")) == []


def test_public_supersede_rolls_back_status_when_lifecycle_event_fails(batch_repo, monkeypatch):
    repo, artifact_type = batch_repo
    prior = repo.save({"prior": True}, **_args(artifact_type, table="prior")).artifact
    original = db.insert

    def fail_event(table_name, row, *, conn=None):
        if table_name == "analysis_artifact_events":
            raise sqlite3.IntegrityError("injected lifecycle failure")
        return original(table_name, row, conn=conn)

    monkeypatch.setattr(db, "insert", fail_event)
    with pytest.raises(sqlite3.IntegrityError):
        repo.supersede(prior.artifact_id)

    assert repo.get_metadata(prior.artifact_id).status == "active"
    assert db.query("analysis_artifact_events", artifact_id=prior.artifact_id) == []


def test_public_supersede_is_serialized(batch_repo, monkeypatch):
    repo, artifact_type = batch_repo
    first = repo.save({"one": True}, **_args(artifact_type, table="one")).artifact
    second = repo.save({"two": True}, **_args(artifact_type, table="two")).artifact
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    original = db.insert

    def pause_first(table_name, row, *, conn=None):
        if table_name == "analysis_artifact_events" and row["artifact_id"] == first.artifact_id:
            entered.set(); assert release.wait(5)
        return original(table_name, row, conn=conn)

    monkeypatch.setattr(db, "insert", pause_first)
    one = threading.Thread(target=lambda: repo.supersede(first.artifact_id))
    two = threading.Thread(target=lambda: (second_started.set(), repo.supersede(second.artifact_id)))
    one.start(); assert entered.wait(5); two.start()
    # The second caller can start, but cannot enter its lifecycle insertion
    # while the public writer slot is held by the first transaction.
    assert second_started.wait(1)
    assert db.query("analysis_artifact_events", artifact_id=second.artifact_id) == []
    release.set(); one.join(5); two.join(5)
    assert repo.get_metadata(first.artifact_id).status == repo.get_metadata(second.artifact_id).status == "superseded"
