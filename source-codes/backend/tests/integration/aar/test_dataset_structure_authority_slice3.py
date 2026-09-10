"""Focused Slice-3 authority persistence contracts (no broad regression run)."""
from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from fastapi import HTTPException

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from domains.aar import save_dataset_structure_assertion
from domains.aar.dataset_structure_review import DecisionInputError, IdempotencyReuse, review, save_decisions
from domains.aar import dataset_structure_review as review_service
from domains.aar.repository import AnalysisArtifactRepository
from domains.aar.dataset_structure_resolver import resolve_dataset_structure_context


def _base(asset: str, snapshot: str, artifact_type: str):
    return {"artifact_type": artifact_type, "asset_id": asset, "snapshot_id": snapshot,
            "population_fingerprint": stable_fingerprint({"snapshot": snapshot}),
            "methodology_fingerprint": stable_fingerprint({"test": "authority"}), "scope": "universal",
            "table": "orders", "identity_inputs": {"test": "authority"}}


@pytest.fixture()
def authority_item(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()
    suffix, now = uuid.uuid4().hex[:8], db.now_ist()
    item = {"item_id": f"item_{suffix}", "dataset_family_id": f"asset_{suffix}", "sourcing_tenant_id": "tenant-a"}
    db.insert("dq_assets", {"asset_id": item["dataset_family_id"], "system_id": f"DS{suffix[:4]}", "alias": "authority",
                              "display_name": "Authority", "kind": "dataset", "time_basis": "none", "current_version_no": 1,
                              "lifecycle_status": "active", "created_at": now, "updated_at": now})
    db.insert("dq_items", {"item_id": item["item_id"], "kind": "dataset", "name": "authority", "status": "profiled",
              "created_at": now, "updated_at": now, "dataset_family_id": item["dataset_family_id"], "delivery_seq": 1,
              "version_no": 1, "snapshot_status": "active", "snapshot_label": "authority", "intent": "fresh",
              "ingest_status": "ready", "sourcing_tenant_id": "tenant-a"})
    db.insert("dq_item_tables", {"item_id": item["item_id"], "table_name": "orders", "row_count": 10, "col_count": 2, "columns": ["customer_id", "period"]})
    db.insert("dataset_structure_materialization_jobs", {"job_id": "dscj_test", "snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"], "tenant_id": "tenant-a", "reason": "post_ready", "dsc_context_version": "1", "publication_generation": 1, "publication_fingerprint": "private", "fencing_token": 0, "status": "succeeded", "attempt_count": 1, "max_attempts": 3, "available_at": now, "lease_owner": None, "lease_expires_at": None, "heartbeat_at": None, "started_at": now, "finished_at": now, "closed_error_code": None, "progress_json": "{}", "created_at": now, "updated_at": now})
    db.insert("dataset_structure_profile_publications", {"snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"], "tenant_id": "tenant-a", "generation": 1, "fingerprint": "private", "published_at": now})
    db.insert("dataset_structure_profile_publication_completions", {"snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"], "tenant_id": "tenant-a", "generation": 1, "completion_token": "dscpc_test", "fingerprint": "private", "proof_source": "publication", "proof_migration_version": None, "state": "completed", "completed_at": now})
    repo = AnalysisArtifactRepository(tmp_path / "artifacts")
    profile = repo.save({"aggregate_only": True}, **_base(item["dataset_family_id"], item["item_id"], "snapshot_profile")).artifact
    def assertion(predicate, instance, kind, value, resolution="proposed"):
        aid = f"dsca_{kind}_{instance.replace(':', '_')}"
        cid, eid = f"dscc_{kind}_{instance.replace(':', '_')}", f"dsce_{kind}_{instance.replace(':', '_')}"
        payload = {"schema_version": 1, "artifact_type": "dataset_structure_assertion", "assertion_id": aid, "context_version": "1",
          "snapshot": {"asset_id": item["dataset_family_id"], "snapshot_id": item["item_id"]}, "subject": {"kind": "table", "table": "orders"},
          "predicate": predicate, "instance_key": instance, "multiplicity": "keyed_set", "claims": [{"claim_id": cid, "authority": "source_reviewed_metadata", "value": value, "evidence_ids": [eid]}],
          "evidence": [{"evidence_id": eid, "kind": "physical_profile", "source_refs": [{"artifact_id": profile.artifact_id, "role": "profile", "payload_hash": profile.payload_hash}], "basis": {"population": "orders", "total_count": 10, "exclusions": {"physical_null": 0, "confirmed_special": 0, "parse_failure": 0}, "usable_count": 10, "computation": "exact"}, "measurements": [{"name": "basis", "count": 10}], "sensitivity": "internal"}],
          "conflicts": [], "resolution": {"status": resolution, "effective_claim_ids": [cid], "reason_codes": [], "conflict_ids": []}, "dependency_fingerprint": stable_fingerprint({"candidate": aid}), "sensitivity": "internal"}
        return save_dataset_structure_assertion(repo, payload).artifact
    assertion("table.structure/entity_binding", "column:customer_id", "entity", {"kind": "entity_binding", "columns": [{"table": "orders", "column": "customer_id"}]})
    assertion("table.temporal/temporal_binding", "column:period", "temporal", {"kind": "temporal_binding", "axis_id": "column:period", "temporal_type": "period", "columns": [{"table": "orders", "column": "period"}], "precision": "month", "calendar": "gregorian"})
    # The cadence assertion is a real retained input; it proves the proposal
    # is tied to a regular observation rather than inferred at confirmation.
    assertion("table.temporal/observed_cadence", "column:period:customer_id", "cadence", {"kind": "observed_cadence", "axis_id": "column:period", "grouping": [{"table": "orders", "column": "customer_id"}], "cadence": "regular", "observed_interval_class": {"unit": "month", "step": 1}})
    return item


def _body(current):
    row = current["tables"][0]
    entity, temporal = row["candidates"]["entities"][0]["candidate_id"], row["candidates"]["temporals"][0]["candidate_id"]
    return {"confirm": True, "draft_revision": current["draft"]["revision"], "decision_basis": {"evidence_fingerprint": current["draft"]["evidence_fingerprint"]}, "selections": {"tables": [{"table": "orders", "default_entity_candidate_id": entity, "default_temporal_candidate_id": temporal, "expected_cadence": {"action": "confirm", "axis_candidate_id": temporal, "grouping_candidate_id": entity, "value": {"unit": "month", "step": 1}}}]}}


def test_authority_batch_is_atomic_replayable_and_supersedes_prior_value(authority_item):
    current, body = review(authority_item), None
    body = _body(current)
    first = save_decisions(authority_item, body, idempotency_key="authority-1", actor="reviewer")
    assert first["status"] == "confirmed" and first["decision_count"] == 3
    assert db.query_one("dataset_structure_review_states", snapshot_id=authority_item["item_id"])["state"] == "confirmed"
    assert save_decisions(authority_item, body, idempotency_key="authority-1", actor="reviewer") == first
    with pytest.raises(IdempotencyReuse):
        save_decisions(authority_item, {**body, "confirm": False}, idempotency_key="authority-1", actor="reviewer")
    # Confirm a new temporal default: only its prior authority is superseded;
    # original v1 candidate alternatives remain active.
    again = review(authority_item)
    next_body = _body(again); next_body["draft_revision"] = again["draft"]["revision"]
    save_decisions(authority_item, next_body, idempotency_key="authority-2", actor="reviewer")
    repo = AnalysisArtifactRepository()
    active = repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal")
    assert any(repo.get(meta.artifact_id)[1].get("context_version") == "1" for meta in active)
    assert db.query("dataset_structure_review_decision_batches", snapshot_id=authority_item["item_id"])


def test_stale_authority_preserves_draft_without_authority_write(authority_item):
    current, body = review(authority_item), None
    body = _body(current); body["decision_basis"]["evidence_fingerprint"] = "evidence_stale"
    before = len(AnalysisArtifactRepository().list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal"))
    result = save_decisions(authority_item, body, idempotency_key="stale-1", actor="reviewer")
    after = len(AnalysisArtifactRepository().list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal"))
    assert result["status"] == "needs_reconfirmation" and after == before
    assert db.query_one("dataset_structure_review_states", snapshot_id=authority_item["item_id"])["state"] == "needs_reconfirmation"


def test_authority_callback_failure_rolls_back_aars_and_workflow(authority_item, monkeypatch):
    current, body = review(authority_item), None
    body = _body(current)
    original = AnalysisArtifactRepository.save_batch
    def failing_callback(self, entries, **kwargs):
        def fail(_conn, _metadata):
            raise RuntimeError("injected workflow failure")
        kwargs["database_callback"] = fail
        return original(self, entries, **kwargs)
    monkeypatch.setattr(AnalysisArtifactRepository, "save_batch", failing_callback)
    with pytest.raises(RuntimeError, match="injected workflow failure"):
        save_decisions(authority_item, body, idempotency_key="rollback-1", actor="reviewer")
    repo = AnalysisArtifactRepository()
    assert not [meta for meta in repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal")
                if repo.get(meta.artifact_id)[1].get("context_version") == "2"]
    assert not db.query("dataset_structure_review_decision_batches", snapshot_id=authority_item["item_id"])


def test_expected_cadence_difference_is_retained_with_bounded_warning(authority_item):
    current, body = review(authority_item), None
    body = _body(current)
    body["selections"]["tables"][0]["expected_cadence"]["value"] = {"unit": "week", "step": 1}
    result = save_decisions(authority_item, body, idempotency_key="different-cadence", actor="reviewer")
    saved = result["preserved_selections"]["tables"][0]
    assert "expected_cadence_differs_from_observation" in saved["warnings"]


def test_irregular_observation_has_no_expected_cadence_proposal(authority_item, monkeypatch):
    raw = deepcopy(review_service._artifacts(authority_item))
    observed = raw["orders"]["observed_cadences"][0]
    observed["_value"] = {**observed["_value"], "cadence": "irregular"}
    observed.pop("observed_interval", None)
    monkeypatch.setattr(review_service, "_artifacts", lambda _item: raw)
    response = review_service.review(authority_item)
    assert response["tables"][0]["recommendations"]["expected_cadence"] is None


def test_decision_post_has_cross_tenant_tenantless_and_unknown_404_parity(authority_item, monkeypatch):
    from routers import dataset_structure
    monkeypatch.setattr(dataset_structure.tenancy, "resolve_principal", lambda _auth: {"tenant_resolved": True, "tenant_id": "tenant-b", "username": "other"})
    with pytest.raises(HTTPException) as cross:
        dataset_structure.post_review_decisions(authority_item["item_id"], {}, "key-cross", "Bearer tenant-b")
    db.update("dq_items", {"item_id": authority_item["item_id"]}, {"sourcing_tenant_id": None})
    with pytest.raises(HTTPException) as tenantless:
        dataset_structure.post_review_decisions(authority_item["item_id"], {}, "key-none", "Bearer tenant-b")
    with pytest.raises(HTTPException) as unknown:
        dataset_structure.post_review_decisions("item_unknown", {}, "key-unknown", "Bearer tenant-b")
    assert (cross.value.status_code, tenantless.value.status_code, unknown.value.status_code) == (404, 404, 404)
    assert (cross.value.detail, tenantless.value.detail, unknown.value.detail) == ("Unknown dataset structure materialization.",) * 3


def test_precommit_revision_race_returns_reconfirmation_without_v2_write(authority_item, monkeypatch):
    current, body = review(authority_item), None
    body = _body(current)
    original = AnalysisArtifactRepository.save_batch
    def race(self, entries, **kwargs):
        db.update("dataset_structure_review_drafts", {"draft_id": db.query_one("dataset_structure_review_states", snapshot_id=authority_item["item_id"])["current_draft_id"]}, {"revision": 99})
        return original(self, entries, **kwargs)
    monkeypatch.setattr(AnalysisArtifactRepository, "save_batch", race)
    result = save_decisions(authority_item, body, idempotency_key="race-guard", actor="reviewer")
    assert result["status"] == "needs_reconfirmation"
    repo = AnalysisArtifactRepository()
    assert not [meta for meta in repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal") if repo.get(meta.artifact_id)[1].get("context_version") == "2"]


def test_precommit_publication_marker_race_returns_reconfirmation_without_v2_write(authority_item, monkeypatch):
    current, body = review(authority_item), None
    body = _body(current)
    original = AnalysisArtifactRepository.save_batch
    def race(self, entries, **kwargs):
        db.update("dataset_structure_profile_publications", {"snapshot_id": authority_item["item_id"]}, {"fingerprint": "replacement"})
        return original(self, entries, **kwargs)
    monkeypatch.setattr(AnalysisArtifactRepository, "save_batch", race)
    result = save_decisions(authority_item, body, idempotency_key="marker-race", actor="reviewer")
    assert result["status"] == "needs_reconfirmation"
    repo = AnalysisArtifactRepository()
    assert not [meta for meta in repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal") if repo.get(meta.artifact_id)[1].get("context_version") == "2"]


def test_closed_no_selection_requires_acknowledgement(authority_item):
    current, body = review(authority_item), None
    body = _body(current)
    row = body["selections"]["tables"][0]
    row["default_entity_candidate_id"] = None
    row["default_entity_candidate_id_action"] = "clear"
    with pytest.raises(DecisionInputError):
        save_decisions(authority_item, body, idempotency_key="clear-without-ack", actor="reviewer")
    row["default_entity_candidate_id_acknowledged"] = True
    result = save_decisions(authority_item, body, idempotency_key="clear-with-ack", actor="reviewer")
    assert result["status"] == "confirmed"


def test_empty_facets_clear_with_governed_profile_witness_not_candidate(authority_item, monkeypatch):
    raw = deepcopy(review_service._artifacts(authority_item))
    raw["orders"]["entities"], raw["orders"]["temporals"] = [], []
    monkeypatch.setattr(review_service, "_artifacts", lambda _item: raw)
    current = review(authority_item)
    body = {"confirm": True, "draft_revision": current["draft"]["revision"],
            "decision_basis": {"evidence_fingerprint": current["draft"]["evidence_fingerprint"]},
            "selections": {"tables": [{"table": "orders", "default_entity_candidate_id": None,
                                           "default_entity_candidate_id_action": "clear",
                                           "default_entity_candidate_id_acknowledged": True,
                                           "default_temporal_candidate_id": None,
                                           "default_temporal_candidate_id_action": "mark_not_applicable",
                                           "default_temporal_candidate_id_acknowledged": True}]}}
    result = save_decisions(authority_item, body, idempotency_key="empty-facets", actor="reviewer")
    assert result["status"] == "confirmed" and result["decision_count"] == 2
    repo = AnalysisArtifactRepository()
    created = [repo.get(meta.artifact_id)[1] for meta in repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal") if repo.get(meta.artifact_id)[1].get("context_version") == "2"]
    assert all(payload["claims"][0]["value"].get("selection") == "none" for payload in created)
    assert all(all(ref["role"] == "profile" for ref in payload["evidence"][0]["source_refs"]) for payload in created)


def test_row_grain_acknowledgement_is_durable_without_v2_assertion(authority_item):
    current = review(authority_item)
    # The fixture has no row-grain candidate, which models an explicit user
    # acknowledgement of an unavailable facet without inventing v2 authority.
    body = {"confirm": True, "draft_revision": current["draft"]["revision"],
            "decision_basis": {"evidence_fingerprint": current["draft"]["evidence_fingerprint"]},
            "selections": {"tables": [{"table": "orders", "row_grain_candidate_id": None,
                                           "row_grain_candidate_id_action": "mark_not_applicable",
                                           "row_grain_candidate_id_acknowledged": True}]}}
    result = save_decisions(authority_item, body, idempotency_key="row-grain-ack", actor="reviewer")
    assert result["status"] == "confirmed" and result["decision_count"] == 0
    stored = result["preserved_selections"]["tables"][0]
    assert stored["row_grain_candidate_id_action"] == "mark_not_applicable"
    assert stored["row_grain_candidate_id_acknowledged"] is True
    batch = db.query("dataset_structure_review_decision_batches", snapshot_id=authority_item["item_id"])[-1]
    assert batch["decision_assertion_refs_json"] == []


@pytest.mark.parametrize("bad", [
    {"tables": [{"table": "orders", "default_entity_candidate_id": "cand_x", "extra": "x"}]},
    {"tables": [{"table": "orders", "default_entity_candidate_id": "cand_" + "x" * 101}]},
    {"tables": [{"table": "orders", "expected_cadence": {"action": "confirm", "axis_candidate_id": "cand_axis", "value": {"unit": "month", "step": 1, "nested": []}}}]},
])
def test_stale_continuation_rejects_unknown_or_oversized_shape(authority_item, bad):
    current = review(authority_item)
    body = {"confirm": True, "draft_revision": current["draft"]["revision"],
            "decision_basis": {"evidence_fingerprint": "stale"}, "selections": bad}
    with pytest.raises(DecisionInputError):
        save_decisions(authority_item, body, idempotency_key="stale-shape-" + uuid.uuid4().hex, actor="reviewer")


def _retained_v1(repo, *, snapshot_id, asset_id, table, predicate):
    values = []
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="active", scope="universal"):
        checked, payload = repo.get(metadata.artifact_id)
        if (checked.asset_id == asset_id and payload.get("context_version") == "1"
                and payload.get("subject", {}).get("table") == table and payload.get("predicate") == predicate):
            values.append((checked, payload))
    return values or None


def test_persisted_v2_authority_resolves_and_stale_pin_fails_closed(authority_item, monkeypatch):
    current, body = review(authority_item), None
    body = _body(current)
    save_decisions(authority_item, body, idempotency_key="resolver-v2", actor="reviewer")
    request = {"protocol_version": "1", "supported_context_versions": ["2", "1"],
               "snapshot": {"asset_id": authority_item["dataset_family_id"], "snapshot_id": authority_item["item_id"]},
               "as_of": "latest", "tables": ["orders"], "consumer_id": "resolver-test",
               "selectors": [{"selector_id": "entity-default", "subject": {"kind": "table", "table": "orders"},
                              "predicate": "table.structure/default_entity_binding", "requirement": "required", "accepted_resolution_states": ["confirmed"]}]}
    # The authority resolver delegates full profile-chain verification to the
    # v1 reuse validator. This compact fixture supplies its known-good v1
    # retained set through that boundary.
    monkeypatch.setattr("domains.aar.dataset_structure_resolver._matching_supported_assertions", _retained_v1)
    response = resolve_dataset_structure_context(AnalysisArtifactRepository(), request)
    assert response["selector_results"][0]["result"] == "fulfilled"
    repo = AnalysisArtifactRepository()
    authority = next(meta for meta in repo.list(snapshot_id=authority_item["item_id"], artifact_type="dataset_structure_assertion", status="active", scope="universal") if repo.get(meta.artifact_id)[1].get("predicate") == "table.structure/default_entity_binding")
    value = repo.get(authority.artifact_id)[1]["claims"][0]["value"]
    db.update("analysis_artifacts", {"artifact_id": value["candidate_pin"]["artifact_id"]}, {"status": "superseded"})
    stale = resolve_dataset_structure_context(repo, request)
    assert stale["selector_results"][0]["result"] == "unavailable"


def test_v2_resolver_fails_closed_when_v1_dependency_chain_is_no_longer_reusable(authority_item, monkeypatch):
    current, body = review(authority_item), _body(review(authority_item))
    save_decisions(authority_item, body, idempotency_key="resolver-chain", actor="reviewer")
    request = {"protocol_version": "1", "supported_context_versions": ["2", "1"],
               "snapshot": {"asset_id": authority_item["dataset_family_id"], "snapshot_id": authority_item["item_id"]},
               "as_of": "latest", "tables": ["orders"], "consumer_id": "resolver-chain-test",
               "selectors": [{"selector_id": "entity-default", "subject": {"kind": "table", "table": "orders"},
                              "predicate": "table.structure/default_entity_binding", "requirement": "required",
                              "accepted_resolution_states": ["confirmed"]}]}
    reusable = {"value": True}
    def matcher(*args, **kwargs):
        return _retained_v1(*args, **kwargs) if reusable["value"] else None
    monkeypatch.setattr("domains.aar.dataset_structure_resolver._matching_supported_assertions", matcher)
    repo = AnalysisArtifactRepository()
    assert resolve_dataset_structure_context(repo, request)["selector_results"][0]["result"] == "fulfilled"
    reusable["value"] = False  # models an unchanged pin whose profile chain was replaced
    assert resolve_dataset_structure_context(repo, request)["selector_results"][0]["result"] == "unavailable"
