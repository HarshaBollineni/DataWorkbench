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


def test_structural_precheck_explains_duplicate_identifier_and_tests_identifier_period_pair(staged_item):
    service._write_table(staged_item, "source", pd.DataFrame({
        "user_id": ["a", "b", "a"], "reporting_period": ["2025-01", "2025-01", "2025-02"],
    }))
    precheck = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "user_id", "role": "Identifier"},
        {"table_name": "source", "column_name": "reporting_period", "role": "Period"},
    ])
    assert precheck["outcome"] == "credible_candidate"
    candidates = precheck["tables"][0]["candidates"]
    single = next(row for row in candidates if row["columns"] == ["user_id"])
    pair = next(row for row in candidates if row["columns"] == ["user_id", "reporting_period"])
    assert single["columns"] == ["user_id"] and single["duplicate_excess_rows"] == 1 and not single["is_unique"]
    assert pair["columns"] == ["user_id", "reporting_period"] and pair["is_unique"]
    assert pair["recommended"] and pair["rank"] == 1
    assert precheck["candidate_limits"]["max_key_columns"] == 2
    assert precheck["authority"] == "none"
    assert not precheck["offer_technical_row_id"]


def test_structural_precheck_offers_technical_id_when_selected_identifier_has_duplicates(staged_item):
    precheck = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "value", "role": "Identifier"},
    ])
    assert precheck["outcome"] == "no_unique_candidate" and precheck["offer_technical_row_id"]
    candidate = precheck["tables"][0]["candidates"][0]
    assert candidate["distinct_key_count"] == 2 and candidate["duplicate_excess_rows"] == 1


def test_structural_precheck_ranks_two_identifiers_and_classifies_exact_duplicates(staged_item):
    rows = [{"account": f"A{index}", "sub_account": "main", "value": index}
            for index in range(19)]
    rows.extend([
        {"account": "A0", "sub_account": "main", "value": 0},
    ])
    service._write_table(staged_item, "source", pd.DataFrame(rows))
    precheck = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "account", "role": "Identifier"},
        {"table_name": "source", "column_name": "sub_account", "role": "Identifier"},
        {"table_name": "source", "column_name": "value", "role": "Feature"},
    ])
    pair = next(row for row in precheck["tables"][0]["candidates"]
                if row["columns"] == ["account", "sub_account"])
    assert pair["quality"] == "near_unique"
    assert pair["uniqueness_ratio"] == 0.95
    assert pair["exact_duplicate_groups"] == 1
    assert pair["conflicting_duplicate_groups"] == 0
    assert pair["deduplication_recommended"] is True


def test_structural_precheck_does_not_recommend_dedup_for_conflicting_rows(staged_item):
    rows = [{"account": f"A{index}", "period": "2025Q1", "value": index}
            for index in range(19)]
    rows.append({"account": "A0", "period": "2025Q1", "value": 999})
    service._write_table(staged_item, "source", pd.DataFrame(rows))
    precheck = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "account", "role": "Identifier"},
        {"table_name": "source", "column_name": "period", "role": "Period"},
        {"table_name": "source", "column_name": "value", "role": "Feature"},
    ])
    pair = next(row for row in precheck["tables"][0]["candidates"]
                if row["columns"] == ["account", "period"])
    assert pair["quality"] == "near_unique"
    assert pair["conflicting_duplicate_groups"] == 1
    assert pair["deduplication_recommended"] is False


def test_staged_structure_review_starts_unselected_and_resumes_explicit_choice(staged_item):
    service._write_table(staged_item, "source", pd.DataFrame({
        "account": ["A1", "A1", "A2"], "period": ["2025Q1", "2025Q2", "2025Q1"],
    }))
    inventory = [
        {"table_name": "source", "column_name": "account", "role": "Identifier", "role_reviewed": True},
        {"table_name": "source", "column_name": "period", "role": "Period", "role_reviewed": True},
    ]
    precheck = service.staged_structural_precheck(staged_item, inventory)
    recommended = next(candidate for candidate in precheck["tables"][0]["candidates"]
                       if candidate["recommended"])
    assert precheck["draft"] == {
        "revision": 0, "state": "review_required", "stale": False,
        "reviewed_roles": [], "selections": {"tables": []},
    }

    saved = service.save_staged_structure_review(
        staged_item, inventory,
        evidence_fingerprint=precheck["evidence_fingerprint"],
        selections={"tables": [{
            "table": "source", "row_grain_candidate_id": recommended["candidate_id"],
        }]},
        expected_revision=0, tenant_id="tenant-a", actor="user",
    )
    assert saved["draft"]["revision"] == 1
    assert saved["draft"]["stale"] is False
    assert saved["draft"]["selections"]["tables"] == [{
        "table": "source", "default_entity_candidate_id": None,
        "default_temporal_candidate_id": None,
        "row_grain_candidate_id": recommended["candidate_id"],
        "entity_acknowledged": False, "temporal_acknowledged": False,
        "row_grain_acknowledged": False, "expected_cadence": None,
    }]
    assert db.query("dataset_structure_review_states", snapshot_id=staged_item) == []


def test_structural_precheck_exposes_holistic_ranked_structure_evidence(staged_item):
    service._write_table(staged_item, "source", pd.DataFrame({
        "account": ["A1", "A1", "A1", "A1", "A2", "A2", "A2", "A2"],
        "period": ["2025Q1", "2025Q2", "2025Q3", "2025Q4"] * 2,
        "value": list(range(8)),
    }))
    precheck = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "account", "role": "Identifier", "role_reviewed": True},
        {"table_name": "source", "column_name": "period", "role": "Period", "role_reviewed": True},
        {"table_name": "source", "column_name": "value", "role": "Feature", "role_reviewed": True},
    ])
    table = precheck["tables"][0]
    assert table["entities"][0] == {
        "candidate_id": table["entities"][0]["candidate_id"], "column": "account", "role": "Identifier",
        "total_rows": 8, "usable_rows": 8, "key_coverage": 1.0,
        "distinct_key_count": 2, "duplicate_excess_rows": 6,
        "uniqueness_ratio": 0.25, "rank": 1, "recommended": True,
    }
    assert table["temporals"][0]["column"] == "period"
    assert table["temporals"][0]["role"] == "Period"
    assert table["temporals"][0]["recommended"] is True
    grain = next(row for row in table["candidates"] if row["columns"] == ["account", "period"])
    assert grain["quality"] == "exact" and grain["recommended"] is True
    cadence = table["cadences"][0]
    assert cadence["entity_candidate_id"] == table["entities"][0]["candidate_id"]
    assert cadence["temporal_candidate_id"] == table["temporals"][0]["candidate_id"]
    assert cadence["cadence"] == "regular"
    assert cadence["interval"] == {"unit": "quarter", "step": 1}
    assert cadence["recommended"] is True
    assert precheck["draft"]["selections"] == {"tables": []}
    saved = service.save_staged_structure_review(
        staged_item,
        [
            {"table_name": "source", "column_name": "account", "role": "Identifier", "role_reviewed": True},
            {"table_name": "source", "column_name": "period", "role": "Period", "role_reviewed": True},
            {"table_name": "source", "column_name": "value", "role": "Feature", "role_reviewed": True},
        ],
        evidence_fingerprint=precheck["evidence_fingerprint"],
        selections={"tables": [{
            "table": "source",
            "default_entity_candidate_id": table["entities"][0]["candidate_id"],
            "default_temporal_candidate_id": table["temporals"][0]["candidate_id"],
            "row_grain_candidate_id": grain["candidate_id"],
            "expected_cadence": {"action": "confirm", "value": {"unit": "quarter", "step": 1}},
        }]},
        expected_revision=0, tenant_id="tenant-a", actor="user",
    )
    selection = saved["draft"]["selections"]["tables"][0]
    assert selection["default_entity_candidate_id"] == table["entities"][0]["candidate_id"]
    assert selection["default_temporal_candidate_id"] == table["temporals"][0]["candidate_id"]
    assert selection["row_grain_candidate_id"] == grain["candidate_id"]
    assert selection["expected_cadence"] == {
        "action": "confirm", "value": {"unit": "quarter", "step": 1},
    }


def test_exact_grain_skips_duplicate_group_iteration(monkeypatch):
    frame = pd.DataFrame({"account": [f"A{index}" for index in range(100)]})
    original_groupby = pd.DataFrame.groupby
    monkeypatch.setattr(pd.DataFrame, "groupby", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("exact candidates must not iterate singleton groups")))
    try:
        candidate = service._staged_grain_candidate(frame, ({
            "table_name": "source", "column_name": "account", "role": "Identifier",
        },))
    finally:
        monkeypatch.setattr(pd.DataFrame, "groupby", original_groupby)
    assert candidate["quality"] == "exact"
    assert candidate["duplicate_groups"] == 0


def test_saving_staged_structure_review_scans_each_table_once(staged_item, monkeypatch):
    inventory = [{"table_name": "source", "column_name": "value", "role": "Identifier"}]
    precheck = service.staged_structural_precheck(staged_item, inventory)
    original_read = service.read_snapshot_table
    calls = []

    def counted_read(*args, **kwargs):
        calls.append(args[:2])
        return original_read(*args, **kwargs)

    monkeypatch.setattr(service, "read_snapshot_table", counted_read)
    saved = service.save_staged_structure_review(
        staged_item, inventory, evidence_fingerprint=precheck["evidence_fingerprint"],
        selections={"tables": []}, expected_revision=0,
        tenant_id="tenant-a", actor="user",
    )
    assert len(calls) == 1
    assert saved["draft"]["revision"] == 1


def test_staged_structure_review_rejects_stale_evidence_and_revision(staged_item):
    inventory = [{"table_name": "source", "column_name": "value", "role": "Identifier"}]
    precheck = service.staged_structural_precheck(staged_item, inventory)
    candidate_id = precheck["tables"][0]["candidates"][0]["candidate_id"]

    with pytest.raises(ValueError, match="evidence changed"):
        service.save_staged_structure_review(
            staged_item, inventory, evidence_fingerprint="staged-evidence-old",
            selections={"tables": []}, expected_revision=0,
            tenant_id="tenant-a", actor="user",
        )
    assert db.query_one("dataset_structure_staged_reviews", snapshot_id=staged_item) is None

    service.save_staged_structure_review(
        staged_item, inventory, evidence_fingerprint=precheck["evidence_fingerprint"],
        selections={"tables": [{"table": "source", "row_grain_candidate_id": candidate_id}]},
        expected_revision=0, tenant_id="tenant-a", actor="user",
    )
    with pytest.raises(ValueError, match="revision changed"):
        service.save_staged_structure_review(
            staged_item, inventory, evidence_fingerprint=precheck["evidence_fingerprint"],
            selections={"tables": []}, expected_revision=0,
            tenant_id="tenant-a", actor="user",
        )


def test_role_change_marks_saved_staged_structure_review_stale(staged_item):
    inventory = [{"table_name": "source", "column_name": "value", "role": "Identifier"}]
    precheck = service.staged_structural_precheck(staged_item, inventory)
    service.save_staged_structure_review(
        staged_item, inventory, evidence_fingerprint=precheck["evidence_fingerprint"],
        selections={"tables": []}, expected_revision=0,
        tenant_id="tenant-a", actor="user",
    )

    changed = service.staged_structural_precheck(staged_item, [
        {"table_name": "source", "column_name": "value", "role": "Feature"},
    ])
    assert changed["evidence_fingerprint"] != precheck["evidence_fingerprint"]
    assert changed["draft"]["revision"] == 1
    assert changed["draft"]["state"] == "stale" and changed["draft"]["stale"] is True


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


def test_legacy_staged_cache_without_ordinal_rebuilds_from_durable_source(staged_item):
    with sqlite3.connect(service._item_db(staged_item)) as conn:
        pd.DataFrame({"value": ["a", "b", "a"]}).to_sql("source", conn, if_exists="replace", index=False)
    result = _create(staged_item)
    assert result["status"] == "complete"
    frame, ordinals = service._read_table_with_technical_row_ordinals(staged_item, "source")
    assert frame["technical_row_id"].str.startswith("tri_").all()
    assert ordinals == [0, 1, 2]


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
