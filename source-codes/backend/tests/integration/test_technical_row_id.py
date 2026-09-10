"""Focused contract checks for the pre-finalization technical row-ID transform."""
from __future__ import annotations

import uuid
import sqlite3

import pandas as pd
import pytest

import system_db as db
from ai.v2 import service


@pytest.fixture()
def staged_item(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(service, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(service, "ITEM_DB_ROOT", tmp_path / "cache")
    db.init_schema()
    item_id, now = f"item_{uuid.uuid4().hex[:8]}", db.now_ist()
    db.insert("dq_items", {"item_id": item_id, "kind": "dataset", "name": "staged", "status": "sourcing",
              "created_at": now, "updated_at": now, "dataset_family_id": "asset_test", "snapshot_status": "active",
              "ingest_status": "needs_review", "sourcing_draft_state": "active", "sourcing_owner": "user",
              "sourcing_tenant_id": "tenant-a", "source_parsing_options_json": {}})
    path = service._item_dir(item_id) / "source.csv"
    path.write_text("value\na\nb\na\n", encoding="utf-8")
    db.insert("dq_item_files", {"file_id": "file_source", "item_id": item_id, "role": "data", "filename": "source.csv", "path": str(path), "completed_at": now})
    service._write_table(item_id, "source", pd.DataFrame({"value": ["a", "b", "a"]}))
    return item_id


def _create(item_id: str, key: str = "key-1"):
    return service.create_technical_row_id(item_id, table="source",
        source_revision=service.technical_row_id_source_revision(item_id),
        acknowledge_non_business_identifier=True, acknowledge_snapshot_transformation=True,
        idempotency_key=key, tenant_id="tenant-a", actor="user")


def test_deterministic_derived_parquet_and_idempotent_replay(staged_item):
    result = _create(staged_item)
    assert result["status"] == "complete" and result["column"] == "technical_row_id"
    values = service.read_snapshot_table(staged_item, "source")["technical_row_id"].tolist()
    assert len(set(values)) == 3 and all(value.startswith("tri_") for value in values)
    assert _create(staged_item)["transform_id"] == result["transform_id"]
    transform = db.query_one("technical_row_id_transforms", snapshot_id=staged_item)
    assert transform["derived_path"].endswith(".parquet") and transform["source_revision"] == result["source_revision"]
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["source_data", "data"]
    # Cache rebuild uses the durable derived table while leaving source files untouched.
    service._item_db(staged_item).unlink()
    assert service.read_snapshot_table(staged_item, "source")["technical_row_id"].tolist() == values


def test_canonical_role_switch_rolls_back_when_registration_fails(staged_item):
    db.execute("CREATE TRIGGER fail_canonical_register BEFORE INSERT ON dq_item_files "
               "WHEN NEW.role='data' BEGIN SELECT RAISE(ABORT, 'register failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="register failed"):
        _create(staged_item)
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["data"]


def test_metadata_is_ignore_and_cannot_be_relabelled(staged_item):
    _create(staged_item)
    row = db.execute("SELECT * FROM variable_inventory WHERE item_id=? AND table_name=? AND column_name=?",
                     (staged_item, "source", "technical_row_id"))[0]
    assert (row["role"], row["role_reviewed"]) == ("Ignore", 1)
    with pytest.raises(ValueError, match="provenance cannot be changed"):
        service.put_inventory(staged_item, [{"table_name": "source", "column_name": "technical_row_id", "role": "Identifier"}])


def test_ledger_protection_rejects_notes_then_identifier_attack(staged_item):
    _create(staged_item)
    with pytest.raises(ValueError, match="provenance cannot be changed"):
        service.put_inventory(staged_item, [{"table_name": "source", "column_name": "technical_row_id",
                                             "notes": "user changed this"}])
    with pytest.raises(ValueError, match="provenance cannot be changed"):
        service.put_inventory(staged_item, [{"table_name": "source", "column_name": "technical_row_id",
                                             "role": "Identifier"}])
    row = db.query_one("variable_inventory", item_id=staged_item, table_name="source", column_name="technical_row_id")
    assert (row["role"], row["role_reviewed"], row["dictionary_role"], row["notes"]) == (
        "Ignore", 1, "ignore", "derived technical row identifier")


def test_published_transform_protects_inventory_before_activation(staged_item):
    result = _create(staged_item)
    db.update("technical_row_id_transforms", {"transform_id": result["transform_id"]}, {
        "status": "writing", "publication_state": "published",
    })
    with pytest.raises(ValueError, match="provenance cannot be changed"):
        service.put_inventory(staged_item, [{"table_name": "source", "column_name": "technical_row_id",
                                             "notes": "attempt during activation window"}])


def test_stale_revision_and_pending_transform_fail_closed(staged_item):
    with pytest.raises(ValueError, match="stale"):
        service.create_technical_row_id(staged_item, table="source", source_revision="old",
            acknowledge_non_business_identifier=True, acknowledge_snapshot_transformation=True,
            idempotency_key="old", tenant_id="tenant-a", actor="user")
    db.insert("technical_row_id_transforms", {"transform_id": "tri_pending", "tenant_id": "tenant-a", "snapshot_id": staged_item,
              "table_name": "pending", "idempotency_key": "pending", "request_digest": "x", "source_revision": "x",
              "parser_options_json": "{}", "source_files_json": "[]", "status": "writing", "derived_path": None,
              "derived_hash": None, "row_count": 0, "closed_error_code": None, "created_by": "user", "created_at": db.now_ist(), "updated_at": db.now_ist()})
    with pytest.raises(ValueError, match="pending"):
        service.process_snapshot(staged_item)


def test_multitable_source_is_ineligible_without_any_write(staged_item):
    service._write_table(staged_item, "other", pd.DataFrame({"value": [1, 2, 3]}))

    eligibility = service.technical_row_id_eligibility(staged_item)
    assert eligibility == {
        "eligible": False,
        "reason": "Technical row identifiers currently require a single-table staged source.",
    }
    with pytest.raises(ValueError, match="single-table"):
        _create(staged_item)

    assert db.query("technical_row_id_transforms", snapshot_id=staged_item) == []
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["data"]


@pytest.mark.parametrize("finalized", ["ready", "processed"])
def test_ready_or_processed_snapshot_is_immutable(staged_item, finalized):
    if finalized == "ready":
        db.update("dq_items", {"item_id": staged_item}, {"sourcing_draft_state": "ready"})
    else:
        db.insert("dq_asset_events", {
            "event_id": "evt_processed", "asset_id": "asset_test", "version_no": 1,
            "snapshot_id": staged_item, "event_type": "snapshot_processed", "actor": "user",
            "at": db.now_ist(), "summary": "processed", "detail_json": {},
        })

    with pytest.raises(ValueError, match="before snapshot finalization"):
        _create(staged_item)
    assert db.query("technical_row_id_transforms", snapshot_id=staged_item) == []
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["data"]


def test_normalized_reserved_column_name_is_never_overwritten(staged_item):
    service._write_table(staged_item, "source", pd.DataFrame({"technical row-id": [1, 2, 3]}))

    with pytest.raises(ValueError, match="already exists"):
        _create(staged_item)
    assert db.query("technical_row_id_transforms", snapshot_id=staged_item) == []
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["data"]


@pytest.mark.parametrize("seam", ["cache", "inventory", "ledger"])
def test_published_role_switch_recovers_every_activation_seam(staged_item, monkeypatch, seam):
    original_cache = service._write_table
    original_inventory = service._technical_row_id_inventory
    original_ledger = service._mark_technical_row_id_active

    if seam == "cache":
        monkeypatch.setattr(service, "_write_table", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache seam")))
    elif seam == "inventory":
        monkeypatch.setattr(service, "_technical_row_id_inventory", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("inventory seam")))
    else:
        monkeypatch.setattr(service, "_mark_technical_row_id_active", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger seam")))

    with pytest.raises(RuntimeError, match=f"{seam} seam"):
        _create(staged_item)
    transform = db.query_one("technical_row_id_transforms", snapshot_id=staged_item)
    assert (transform["status"], transform["publication_state"]) == ("writing", "published")
    assert [row["role"] for row in db.query("dq_item_files", item_id=staged_item)] == ["source_data", "data"]

    monkeypatch.setattr(service, "_write_table", original_cache)
    monkeypatch.setattr(service, "_technical_row_id_inventory", original_inventory)
    monkeypatch.setattr(service, "_mark_technical_row_id_active", original_ledger)
    cache_path = service._item_db(staged_item)
    if cache_path.exists():
        cache_path.unlink()
    assert service.recover_technical_row_id_transforms(staged_item) == 1
    recovered = db.query_one("technical_row_id_transforms", snapshot_id=staged_item)
    assert (recovered["status"], recovered["publication_state"]) == ("complete", "active")
    assert service.read_snapshot_table(staged_item, "source")["technical_row_id"].str.startswith("tri_").all()


def test_persisted_source_ordinals_survive_cache_reload_and_unordered_reads(staged_item):
    first, ordinals = service._read_table_with_technical_row_ordinals(staged_item, "source")
    assert first["value"].tolist() == ["a", "b", "a"] and ordinals == [0, 1, 2]
    expected = service._technical_row_id_values(
        staged_item, "source", service.technical_row_id_source_revision(staged_item), {}, ordinals,
    )

    # SQLite is free to retain insertion order even with the diagnostic pragma;
    # only the persisted ordinal is the transform's ordering contract.

    service._item_db(staged_item).unlink()
    rebuilt, rebuilt_ordinals = service._read_table_with_technical_row_ordinals(staged_item, "source")
    assert rebuilt["value"].tolist() == first["value"].tolist()
    assert rebuilt_ordinals == ordinals
    assert _create(staged_item)["status"] == "complete"
    assert service.read_snapshot_table(staged_item, "source")["technical_row_id"].tolist() == expected


@pytest.mark.parametrize("after_replacement", [False, True])
def test_prepublication_crash_retry_reuses_transform_and_converges(staged_item, monkeypatch, after_replacement):
    original = service.technical_row_id_source_revision
    calls = {"count": 0}

    def crash_after_derived(item_id):
        calls["count"] += 1
        if after_replacement and calls["count"] == 4:
            raise RuntimeError("crash after derived replacement")
        return original(item_id)

    if after_replacement:
        monkeypatch.setattr(service, "technical_row_id_source_revision", crash_after_derived)
    else:
        original_write = pd.DataFrame.to_parquet
        monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash before derived replacement")))
    with pytest.raises(RuntimeError, match="crash"):
        _create(staged_item, "crash-key")
    first = db.query_one("technical_row_id_transforms", snapshot_id=staged_item)
    assert (first["status"], first["publication_state"]) == ("failed", "preparing")
    # Migration-era unfinished entries have no authoritative ordinal contract
    # or durable target.  Retrying must repair both on the same transform ID.
    db.update("technical_row_id_transforms", {"transform_id": first["transform_id"]}, {
        "ordinal_provenance": "legacy-unpinned", "derived_path": None,
    })
    if after_replacement:
        monkeypatch.setattr(service, "technical_row_id_source_revision", original)
    else:
        monkeypatch.setattr(pd.DataFrame, "to_parquet", original_write)
    retried = _create(staged_item, "crash-key")
    assert retried["transform_id"] == first["transform_id"]
    final = db.query_one("technical_row_id_transforms", transform_id=first["transform_id"])
    assert (final["status"], final["publication_state"]) == ("complete", "active")
    assert final["ordinal_provenance"] == service.TECHNICAL_ROW_ID_ORDINAL_PROVENANCE
    assert final["derived_path"]


def test_legacy_transform_status_migration_never_promotes_writing_or_failed(staged_item):
    now = db.now_ist()
    for status in ("writing", "failed"):
        db.insert("technical_row_id_transforms", {"transform_id": f"tri_legacy_{status}", "tenant_id": "tenant-a",
                  "snapshot_id": f"legacy_{status}", "table_name": "source", "idempotency_key": f"legacy-{status}",
                  "request_digest": "x", "source_revision": "x", "parser_options_json": "{}", "source_files_json": "[]",
                  "status": status, "publication_state": "active", "ordinal_provenance": "legacy-unpinned",
                  "derived_path": None, "derived_hash": None, "row_count": 0, "closed_error_code": None,
                  "created_by": "legacy", "created_at": now, "updated_at": now})
    with db.get_conn() as conn:
        db._migrate_technical_row_id_publication_state(conn)
    assert {row["status"]: row["publication_state"] for row in db.query("technical_row_id_transforms")
            if row["snapshot_id"] in {"legacy_writing", "legacy_failed"}} == {
        "writing": "preparing", "failed": "preparing"}
