"""Focused Slice-2 review draft/privacy contracts."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
from fastapi import HTTPException

import system_db as db
from domains.aar import dataset_structure_review as review


@pytest.fixture()
def item(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()
    suffix, now = uuid.uuid4().hex[:10], db.now_ist()
    row = {"item_id": f"item_{suffix}", "kind": "dataset", "name": "safe", "status": "profiled",
           "created_at": now, "updated_at": now, "dataset_family_id": f"asset_{suffix}",
           "delivery_seq": 1, "version_no": 1, "snapshot_status": "active", "snapshot_label": "safe",
           "intent": "fresh", "ingest_status": "ready", "sourcing_tenant_id": "tenant-a"}
    db.insert("dq_items", row)
    db.insert("dq_item_tables", {"item_id": row["item_id"], "table_name": "observations",
                                   "row_count": 3, "col_count": 2, "columns": ["entity", "period"]})
    db.insert("dataset_structure_materialization_jobs", {
        "job_id": "dscj_safe", "snapshot_id": row["item_id"], "asset_id": row["dataset_family_id"],
        "tenant_id": "tenant-a", "reason": "post_ready", "dsc_context_version": "1",
        "publication_generation": 1, "publication_fingerprint": "private-publication-hash",
        "fencing_token": 0, "status": "succeeded", "attempt_count": 1, "max_attempts": 3,
        "available_at": now, "lease_owner": None, "lease_expires_at": None, "heartbeat_at": None,
        "started_at": now, "finished_at": now, "closed_error_code": None, "progress_json": "{}",
        "created_at": now, "updated_at": now,
    })
    db.insert("dataset_structure_profile_publications", {
        "snapshot_id": row["item_id"], "asset_id": row["dataset_family_id"], "tenant_id": "tenant-a",
        "generation": 1, "fingerprint": "private-publication-hash", "published_at": now,
    })
    return row


def _candidates(pin: str = "private-payload-hash"):
    def candidate(kind, label, usable):
        predicate = {"entity": "table.structure/entity_binding", "temporal": "table.temporal/temporal_binding",
                     "grain": "table.structure/row_grain"}[kind]
        summary = {"evidence_count": 1, "aggregate_basis": "materialized",
                   "usable_observations": usable, "total_observations": 3}
        return {"candidate_id": "cand_" + kind, "display_label": label, "safe_label": label,
                "predicate": predicate, "instance_key": "safe-instance", "resolution_status": "proposed",
                "evidence_summary": summary, "evidence": summary,
                "warnings": [], "_pin": {"artifact": "art_private", "payload": pin, "dependency": "private-dependency", "kind": kind}}
    return {"observations": {
        "entities": [candidate("entity", "Entity identifier: entity", 3)],
        "temporals": [candidate("temporal", "Date field: period", 3)],
        "row_grains": [candidate("grain", "Row grain: entity + period", 3)],
        "observed_cadences": [],
    }}


def test_slice2_projection_is_private_idempotent_and_preserves_stale_draft(item, monkeypatch):
    current = {"value": _candidates()}
    monkeypatch.setattr(review, "_artifacts", lambda _item: current["value"])
    first = review.review(item)
    encoded = str(first)
    assert "art_private" not in encoded and "private-payload-hash" not in encoded and "private-dependency" not in encoded
    assert first["materialization"] == {"job_id": "dscj_safe", "status": "succeeded", "generation": 1,
                                        "freshness": "current", "retry_after_ms": None}
    assert first["diagnostic_assistance"][0]["state"] == "not_adopted"

    body = {"draft_revision": 0, "evidence_fingerprint": first["draft"]["evidence_fingerprint"],
            "selections": {"tables": [{"table": "observations", "default_entity_candidate_id": "cand_entity"}]}}
    saved = review.save_draft(item, body, idempotency_key="save-1", actor="reviewer")
    assert saved["status"] == "review_required" and saved["draft_revision"] == 1
    assert review.save_draft(item, body, idempotency_key="save-1", actor="reviewer") == saved
    assert len(db.query("dataset_structure_review_drafts", snapshot_id=item["item_id"])) == 2

    revision_race = review.save_draft(item, {**body, "draft_revision": 0}, idempotency_key="save-2", actor="reviewer")
    assert revision_race["status"] == "needs_reconfirmation" and revision_race["draft_revision"] == 2

    # The candidate evidence changes after the user began editing.  The old
    # candidate input remains a draft and gets a workflow-continuation 200.
    current["value"] = _candidates("new-private-payload-hash")
    stale = review.save_draft(item, body, idempotency_key="save-3", actor="reviewer")
    assert stale["status"] == "needs_reconfirmation"
    assert stale["preserved_selections"] == body["selections"]
    assert db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])["state"] == "needs_reconfirmation"

    latest = review.review(item)
    null_body = {"draft_revision": latest["draft"]["revision"],
                 "evidence_fingerprint": latest["draft"]["evidence_fingerprint"],
                 "selections": {"tables": [{"table": "observations", "default_entity_candidate_id": None}]}}
    explicit_null = review.save_draft(item, null_body, idempotency_key="save-null", actor="reviewer")
    assert explicit_null["preserved_selections"]["tables"][0]["default_entity_candidate_id"] is None


def test_slice2_router_reuses_tenant_404_parity(item, monkeypatch):
    from routers import dataset_structure
    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal",
                        lambda _auth: {"tenant_resolved": True, "tenant_id": "tenant-b", "username": "other"})
    with pytest.raises(HTTPException) as error:
        dataset_structure.get_review(item["item_id"], "Bearer tenant-b")
    assert error.value.status_code == 404
    assert error.value.detail == "Unknown dataset structure materialization."


@pytest.mark.parametrize("principal,item_tenant", [
    ({"tenant_resolved": True, "tenant_id": "tenant-b", "username": "other"}, "tenant-a"),
    ({"tenant_resolved": False, "tenant_id": "", "username": "unknown"}, "tenant-a"),
    ({"tenant_resolved": True, "tenant_id": "tenant-a", "username": "owner"}, None),
])
def test_inventory_router_keeps_cross_tenant_and_tenantless_access_indistinguishable(item, monkeypatch, principal, item_tenant):
    from routers import sourcing
    db.update("dq_items", {"item_id": item["item_id"]}, {"sourcing_tenant_id": item_tenant})
    monkeypatch.setattr(sourcing.tenancy, "resolve_principal", lambda _auth: principal)
    called = {"put": False}
    monkeypatch.setattr(sourcing.service, "put_inventory", lambda *_args, **_kwargs: called.__setitem__("put", True))
    for inaccessible_id in (item["item_id"], "missing-inventory-item"):
        for action in (lambda value=inaccessible_id: sourcing.get_inventory(value, authorization="Bearer wrong"),
                       lambda value=inaccessible_id: sourcing.put_inventory(value, [], authorization="Bearer wrong")):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 404
            assert error.value.detail == "Unknown column definitions."
    assert called["put"] is False


def test_inventory_batch_validates_every_row_before_any_authoritative_write(item, monkeypatch):
    from ai.v2 import service as sourcing_service
    import domains.aar.data_sourcing as data_sourcing
    now = db.now_ist()
    for column, classification in (("entity", "text"), ("amount", "numerical")):
        db.insert("variable_inventory", {"item_id": item["item_id"], "table_name": "observations",
                                           "column_name": column, "classification": classification,
                                           "data_type": "object" if column == "entity" else "float64",
                                           "description": "", "discrepancies": [], "notes": "", "role": "Feature",
                                           "role_reviewed": 0, "profile_json": {}, "updated_at": now,
                                           "missing_value_codes_json": [], "missing_codes_confirmed": 0, "provisional": 0})
    monkeypatch.setattr(sourcing_service, "_read_table", lambda *_args: pd.DataFrame({"entity": ["a", "b"], "amount": [1.0, 2.0]}))
    monkeypatch.setattr(data_sourcing, "persist_snapshot_profile_artifacts", lambda *_args, **_kwargs: [])
    with pytest.raises(ValueError, match="Numeric column"):
        sourcing_service.put_inventory(item["item_id"], [
            {"table_name": "observations", "column_name": "entity", "role": "Identifier"},
            {"table_name": "observations", "column_name": "amount", "classification": "numerical",
             "missing_value_codes_json": ["UNKNOWN"], "missing_codes_confirmed": True},
        ])
    retained = db.query_one("variable_inventory", item_id=item["item_id"], table_name="observations", column_name="entity")
    assert retained["role"] == "Feature" and retained["role_reviewed"] == 0


def test_confirmed_inventory_metadata_save_fences_dsc_before_committing_the_new_role(item, monkeypatch):
    from ai.v2 import service as sourcing_service
    import domains.aar.data_sourcing as data_sourcing
    now = db.now_ist()
    db.insert("variable_inventory", {"item_id": item["item_id"], "table_name": "observations",
                                       "column_name": "entity", "classification": "text", "data_type": "object",
                                       "description": "", "discrepancies": [], "notes": "", "role": "Feature",
                                       "role_reviewed": 0, "profile_json": {}, "updated_at": now,
                                       "missing_value_codes_json": [], "missing_codes_confirmed": 0, "provisional": 0})
    monkeypatch.setattr(sourcing_service, "_read_table", lambda *_args: pd.DataFrame({"entity": ["a", "b"]}))
    monkeypatch.setattr(data_sourcing, "persist_snapshot_profile_artifacts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(review, "_artifacts", lambda _item: _candidates())
    review.review(item)
    sourcing_service.put_inventory(item["item_id"], [{"table_name": "observations", "column_name": "entity", "role": "Identifier"}])
    state = db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])
    saved = db.query_one("variable_inventory", item_id=item["item_id"], table_name="observations", column_name="entity")
    assert state["state"] == "metadata_review"
    assert db.query_one("dq_items", item_id=item["item_id"])["profile_publication_attempt_token"]
    assert saved["role"] == "Identifier" and saved["role_reviewed"] == 1


def test_slice2_endpoint_shape_limits_and_candidate_free_tables_are_bounded(item, monkeypatch):
    many = _candidates()
    template = many["observations"]["entities"][0]
    many["observations"]["entities"] = [
        {**template, "candidate_id": f"cand_entity_{n:02d}", "_pin": {**template["_pin"], "artifact": f"art_{n:02d}"}}
        for n in range(25)
    ]
    monkeypatch.setattr(review, "_artifacts", lambda _item: many)
    db.insert("dq_item_tables", {"item_id": item["item_id"], "table_name": "candidate_free",
                                   "row_count": 1, "col_count": 1, "columns": ["value"]})
    from routers import dataset_structure
    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal",
                        lambda _auth: {"tenant_resolved": True, "tenant_id": "tenant-a", "username": "reader"})
    response = dataset_structure.get_review(item["item_id"], "Bearer tenant-a")
    assert response["structure_review_state"] == "limited"
    assert set(response["materialization"]) == {"job_id", "status", "generation", "freshness", "retry_after_ms"}
    observations = next(row for row in response["tables"] if row["table"] == "observations")
    entity = observations["candidates"]["entities"][0]
    assert {"candidate_id", "display_label", "evidence_summary", "predicate", "instance_key"} <= set(entity)
    assert len(observations["candidates"]["entities"]) == 20
    assert observations["candidate_limits"]["entities"] == {"returned": 20, "truncated": True, "limit": 20}
    assert next(row for row in response["tables"] if row["table"] == "candidate_free")["state"] == "limited"


def test_materializing_state_does_not_create_a_stale_revision_zero_draft(item, monkeypatch):
    monkeypatch.setattr(review, "_artifacts", lambda _item: {})
    db.update("dataset_structure_materialization_jobs", {"job_id": "dscj_safe"}, {"status": "running", "finished_at": None})
    before = review.review(item)
    assert before["draft"]["draft_id"] is None and before["draft"]["editable"] is False
    assert not db.query("dataset_structure_review_drafts", snapshot_id=item["item_id"])

    db.update("dataset_structure_materialization_jobs", {"job_id": "dscj_safe"}, {"status": "succeeded", "finished_at": db.now_ist()})
    monkeypatch.setattr(review, "_artifacts", lambda _item: _candidates())
    after = review.review(item)
    assert after["draft"]["revision"] == 0 and after["draft"]["editable"] is True
    assert after["structure_review_state"] != "needs_reconfirmation"


def test_metadata_correction_fences_review_preserves_full_draft_and_reconfirms_after_republication(item, monkeypatch):
    current = {"value": _candidates("before-metadata-change")}
    monkeypatch.setattr(review, "_artifacts", lambda _item: current["value"])
    first = review.review(item)
    selections = {"tables": [{"table": "observations", "default_entity_candidate_id": "cand_entity",
                                "default_temporal_candidate_id": "cand_temporal",
                                "expected_cadence": {"action": "replace", "axis_candidate_id": "cand_temporal",
                                                     "grouping_candidate_id": "cand_entity",
                                                     "value": {"unit": "month", "step": 1}}}]}
    saved = review.save_draft(item, {"draft_revision": first["draft"]["revision"],
                                     "evidence_fingerprint": first["draft"]["evidence_fingerprint"],
                                     "selections": selections}, idempotency_key="metadata-before", actor="reviewer")
    assert saved["preserved_selections"] == selections

    assert review.begin_metadata_correction(item["item_id"], tenant_id="tenant-a") is True
    retained = db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])
    assert retained["state"] == "metadata_review"
    assert db.query_one("dataset_structure_review_drafts", draft_id=retained["current_draft_id"])["selections_json"] == selections
    paused = review.review(item)
    assert paused["structure_review_state"] == "metadata_review"
    assert paused["draft"]["selections"] == selections and paused["draft"]["editable"] is False

    # A replacement publication fences the old completed generation. While its
    # materialization is queued, the draft remains intact and editable later,
    # without incorrectly flagging old evidence as the new evidence.
    db.update("dataset_structure_materialization_jobs", {"job_id": "dscj_safe"}, {"status": "revoked"})
    replacement_attempt = db.query_one("dq_items", item_id=item["item_id"])["profile_publication_attempt_token"]
    db.insert("dataset_structure_materialization_jobs", {
        "job_id": "dscj_republished", "snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"],
        "tenant_id": "tenant-a", "reason": "post_ready", "dsc_context_version": "1",
        "publication_generation": 2, "publication_fingerprint": "replacement-publication", "publication_attempt_token": replacement_attempt, "fencing_token": 0,
        "status": "queued", "attempt_count": 0, "max_attempts": 3, "available_at": db.now_ist(),
        "lease_owner": None, "lease_expires_at": None, "heartbeat_at": None, "started_at": None,
        "finished_at": None, "closed_error_code": None, "progress_json": "{}", "created_at": db.now_ist(),
        "updated_at": db.now_ist(),
    })
    db.update("dataset_structure_profile_publications", {"snapshot_id": item["item_id"]},
              {"generation": 2, "fingerprint": "replacement-publication"})
    queued = review.review(item)
    assert queued["structure_review_state"] == "materializing"
    assert queued["draft"]["selections"] == selections

    db.update("dataset_structure_materialization_jobs", {"job_id": "dscj_republished"},
              {"status": "succeeded", "finished_at": db.now_ist()})
    current["value"] = _candidates("after-metadata-change")
    refreshed = review.review(item)
    assert refreshed["structure_review_state"] == "needs_reconfirmation"
    assert refreshed["draft"]["selections"] == selections


def test_metadata_correction_fences_a_concurrently_running_old_worker(item, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from domains.aar import materialization_jobs
    monkeypatch.setattr(review, "_artifacts", lambda _item: _candidates())
    review.review(item)
    db.insert("dataset_structure_profile_publication_completions", {
        "snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"], "tenant_id": "tenant-a",
        "generation": 1, "completion_token": "", "fingerprint": "private-publication-hash",
        "proof_source": "publication", "proof_migration_version": None, "state": "completed", "completed_at": db.now_ist(),
    })
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    db.update("dataset_structure_materialization_jobs", {"job_id": "dscj_safe"}, {
        "status": "running", "lease_owner": "old-worker", "lease_expires_at": expiry,
        "fencing_token": 1, "publication_completion_token": "", "publication_attempt_token": "",
    })
    db.update("dq_items", {"item_id": item["item_id"]}, {"profile_publication_attempt_token": ""})
    old_job = db.query_one("dataset_structure_materialization_jobs", job_id="dscj_safe")
    materialization_jobs._lease_guard(old_job, "old-worker")
    assert review.begin_metadata_correction(item["item_id"], tenant_id="tenant-a") is True
    with pytest.raises(materialization_jobs.DatasetStructureObservationError, match="DSC_R_WORKER_LEASE_LOST"):
        materialization_jobs._lease_guard(old_job, "old-worker")


def test_slice2_concurrent_first_review_creates_one_revision_zero_draft(item, monkeypatch):
    monkeypatch.setattr(review, "_artifacts", lambda _item: _candidates())
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _n: review.review(item), range(2)))
    assert {reply["draft"]["revision"] for reply in replies} == {0}
    drafts = db.query("dataset_structure_review_drafts", snapshot_id=item["item_id"])
    assert len(drafts) == 1 and drafts[0]["revision"] == 0


def test_slice2_facet_page_exposes_all_safe_candidates_beyond_summary_cap(item, monkeypatch):
    many = _candidates()
    template = many["observations"]["entities"][0]
    many["observations"]["entities"] = [
        {**template, "candidate_id": f"cand_entity_{n:02d}", "safe_label": f"Entity identifier: id_{n:02d}",
         "display_label": f"Entity identifier: id_{n:02d}", "_pin": {**template["_pin"], "artifact": f"art_{n:02d}"}}
        for n in range(27)
    ]
    monkeypatch.setattr(review, "_artifacts", lambda _item: many)
    first = review.review_facet(item, table_id="observations", facet="entities", limit=25)
    second = review.review_facet(item, table_id="observations", facet="entities", cursor=first["page"]["next_cursor"], limit=25)
    assert first["page"] == {"offset": 0, "limit": 25, "returned": 25, "total": 27,
                             "next_offset": 25, "next_cursor": "offset:25"}
    assert [row["candidate_id"] for row in first["candidates"] + second["candidates"]] == [f"cand_entity_{n:02d}" for n in range(27)]
    assert "art_00" not in str(first)


def test_slice3_expected_cadence_draft_save_and_reopen(item, monkeypatch):
    monkeypatch.setattr(review, "_artifacts", lambda _item: _candidates())
    current = review.review(item)
    body = {"draft_revision": current["draft"]["revision"], "evidence_fingerprint": current["draft"]["evidence_fingerprint"],
            "selections": {"tables": [{"table": "observations", "expected_cadence": {"action": "replace", "axis_candidate_id": "cand_temporal", "grouping_candidate_id": "cand_entity", "value": {"unit": "quarter", "step": 1}}}]}}
    saved = review.save_draft(item, body, idempotency_key="cadence-resume", actor="reviewer")
    reopened = review.review(item)
    assert saved["status"] == "review_required"
    assert reopened["draft"]["selections"]["tables"][0]["expected_cadence"] == body["selections"]["tables"][0]["expected_cadence"]
