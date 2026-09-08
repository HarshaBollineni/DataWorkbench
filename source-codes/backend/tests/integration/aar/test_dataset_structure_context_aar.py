"""AAR registration and persistence proofs for Dataset Structure Context."""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.dataset_structure_context import request_fingerprint
from analysis_runtime.dataset_structure_context import DSCContractError, ERROR_SNAPSHOT_MISMATCH
import domains.aar.dataset_structure_producer as dsc_producer
from domains.aar.repository import AnalysisArtifactRepository
from domains.aar import save_dataset_structure_assertion, save_dataset_structure_context
from domains.aar import (
    SUPPORTED_OBSERVER_PREDICATES,
    observe_dataset_structure,
    resolve_dataset_structure_context,
)
from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
from domains.aar.types import get_artifact_type


_HASH = "a" * 64


@pytest.fixture()
def aar(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    db.init_schema()

    suffix = uuid.uuid4().hex[:8]
    asset_id, snapshot_id = f"asset_{suffix}", f"item_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"dsc-{suffix}", "display_name": f"DSC-{suffix}",
        "kind": "dataset", "time_basis": "none", "current_version_no": 1,
        "lifecycle_status": "active", "created_at": now, "updated_at": now,
    })
    db.insert("dq_items", {
        "item_id": snapshot_id, "kind": "dataset", "name": f"DSC-{suffix}",
        "status": "profiled", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": "active", "snapshot_label": snapshot_id,
        "intent": "fresh", "ingest_status": "ready",
    })
    db.insert("dq_item_tables", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "row_count": 10, "col_count": 1, "columns": ["private_application_id"],
    })
    return AnalysisArtifactRepository(tmp_path / "artifacts"), asset_id, snapshot_id


def _base_args(asset_id, snapshot_id, artifact_type, *, scope="universal", owner_id=None,
               source_artifacts=(), target_fingerprint=None, comparison_snapshot_id=None):
    return {
        "artifact_type": artifact_type,
        "asset_id": asset_id,
        "snapshot_id": snapshot_id,
        "population_fingerprint": stable_fingerprint({"dsc_snapshot": snapshot_id}),
        "methodology_fingerprint": stable_fingerprint({"dsc": "producer-v1"}),
        "scope": scope,
        "owner_id": owner_id,
        "target_fingerprint": target_fingerprint,
        "comparison_snapshot_id": comparison_snapshot_id,
        "table": "private_applications",
        "source_artifacts": source_artifacts,
        "identity_inputs": {"dsc": "v1"},
    }


def _profile(repo, asset_id, snapshot_id):
    return repo.save({"aggregate_only": True}, **_base_args(
        asset_id, snapshot_id, "snapshot_profile")).artifact


def _assertion_payload(asset_id, snapshot_id, profile):
    return {
        "schema_version": 1,
        "artifact_type": "dataset_structure_assertion",
        "assertion_id": "dsca_private_row_grain",
        "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": "private_applications"},
        "predicate": "table.structure/row_grain",
        "instance_key": "primary",
        "multiplicity": "single",
        "claims": [{
            "claim_id": "dscc_private_grain",
            "authority": "source_confirmed_structural",
            "value": {"kind": "row_grain", "key_columns": [{
                "table": "private_applications", "column": "private_application_id",
            }]},
            "evidence_ids": ["dsce_private_profile"],
            "decision": {"decision_id": "decision_private_grain", "action": "confirm",
                         "scope": "source_confirmed_structural"},
        }],
        "evidence": [{
            "evidence_id": "dsce_private_profile",
            "kind": "physical_profile",
            "source_refs": [{
                "artifact_id": profile.artifact_id,
                "payload_hash": profile.payload_hash,
                "role": "profile",
            }],
            "basis": {
                "population": "private_applications", "total_count": 10,
                "exclusions": {"physical_null": 0, "confirmed_special": 0,
                               "parse_failure": 0},
                "usable_count": 10, "computation": "exact",
            },
            "measurements": [{"name": "unique_key_rows", "count": 10}],
            "sensitivity": "internal",
        }],
        "conflicts": [],
        "resolution": {"status": "confirmed", "effective_claim_ids": [
            "dscc_private_grain"], "reason_codes": [], "conflict_ids": []},
        "dependency_fingerprint": _HASH,
        "sensitivity": "internal",
    }


def _context_payload(asset_id, snapshot_id, assertion):
    selector = {"selector_id": "private-row-grain",
                "subject": {"kind": "table", "table": "private_applications"},
                "predicate": "table.structure/row_grain", "requirement": "required",
                "accepted_resolution_states": ["confirmed"]}
    request = {"protocol_version": "1", "supported_context_versions": ["1"],
               "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
               "as_of": "latest", "tables": ["private_applications"],
               "selectors": [selector], "consumer_id": "consumer_dsc_test"}
    return {
        "schema_version": 1,
        "artifact_type": "dataset_structure_context",
        "protocol_version": "1",
        "context_version": "1",
        "request_fingerprint": request_fingerprint(request),
        "request_as_of": "latest",
        "supported_context_versions": ["1"],
        "consumer_id": "consumer_dsc_test",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "resolved_as_of": "2026-01-01T00:00:00+00:00",
        "sensitivity": "internal",
        "selected_tables": ["private_applications"],
        "overall_result": "fulfilled",
        "selector_results": [{
            "selector": selector,
            "selector_id": "private-row-grain", "result": "fulfilled",
            "pins": [{
                "artifact_id": assertion.artifact_id,
                "assertion_id": "dsca_private_row_grain",
                "payload_hash": assertion.payload_hash,
                "resolution": "confirmed", "reuse_disposition": "fresh",
            }],
        }],
    }


def _assertion_source(profile):
    return ({"artifact_id": profile.artifact_id, "role": "profile"},)


def test_dsc_descriptor_contracts():
    assertion = get_artifact_type("dataset_structure_assertion")
    context = get_artifact_type("dataset_structure_context")

    assert assertion and context
    assert assertion.status == context.status == "active"
    assert assertion.owner == context.owner == "Dataset Structure Context"
    assert assertion.payload_schema_version == context.payload_schema_version == 1
    assert assertion.supported_scopes == ("universal", "diagnostic_local")
    assert context.supported_scopes == ("diagnostic_local",)
    assert assertion.granularity == "assertion"
    assert context.granularity == "snapshot_context"
    assert assertion.target_applicability == context.target_applicability == "not_applicable"
    assert assertion.comparison_snapshot_applicability == \
        context.comparison_snapshot_applicability == "not_applicable"
    assert assertion.sensitivity == context.sensitivity == "confidential"
    assert not assertion.allows_blob_payload and not context.allows_blob_payload
    assert assertion.allowed_source_types == (
        "snapshot_profile", "table_profile", "schema_profile", "column_profile",
        "governance_reference", "dataset_structure_assertion",
    )
    assert context.allowed_source_types == ("dataset_structure_assertion",)


def test_dsc_payloads_and_summaries_remain_discoverable(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    assertion_payload = _assertion_payload(asset_id, snapshot_id, profile)
    context_payload = _context_payload(asset_id, snapshot_id, profile)

    assertion_summary = get_artifact_type("dataset_structure_assertion").summary_adapter(
        assertion_payload)
    context_summary = get_artifact_type("dataset_structure_context").summary_adapter(
        context_payload)
    rendered = json.dumps({"assertion": assertion_summary, "context": context_summary})
    for secret in (
        "private_applications", "private_application_id", "dscc_private_grain",
        "dsce_private_profile", profile.artifact_id,
    ):
        assert secret not in rendered


@pytest.mark.parametrize("artifact_type", [
    "dataset_structure_assertion", "dataset_structure_context",
])
@pytest.mark.parametrize("identity_field, value", [
    ("target_fingerprint", "forbidden-target"),
    ("comparison_snapshot_id", "item_comparison"),
])
def test_dsc_rejects_target_and_comparison_identity(aar, artifact_type, identity_field, value):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    if artifact_type == "dataset_structure_assertion":
        payload, sources, scope, owner = (
            _assertion_payload(asset_id, snapshot_id, profile), _assertion_source(profile),
            "universal", None,
        )
    else:
        payload, sources, scope, owner = (
            _context_payload(asset_id, snapshot_id, profile), (),
            "diagnostic_local", "consumer_dsc_test",
        )
    with pytest.raises(ValueError, match="not applicable"):
        repo.save(payload, **_base_args(
            asset_id, snapshot_id, artifact_type, scope=scope, owner_id=owner,
            source_artifacts=sources, **{identity_field: value},
        ))


def test_dsc_adapter_persists_and_exactly_reuses_assertion(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    assertion_payload = _assertion_payload(asset_id, snapshot_id, profile)
    created = save_dataset_structure_assertion(repo, assertion_payload)
    reused = save_dataset_structure_assertion(repo, assertion_payload)
    assert created.outcome == "created" and reused.outcome == "reused"
    assert created.artifact.artifact_id == reused.artifact.artifact_id


def test_dsc_assertion_owner_source_and_promotion_contracts(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    local = _assertion_payload(asset_id, snapshot_id, profile)
    local["claims"][0]["authority"] = "consumer_local_decision"
    local["claims"][0]["decision"] = {"decision_id": "decision_local", "action": "confirm",
                                          "scope": "consumer_local", "owner_id": "consumer_a"}
    local_artifact = save_dataset_structure_assertion(repo, local).artifact
    assert local_artifact.scope == "diagnostic_local" and local_artifact.owner_id == "consumer_a"

    mixed = deepcopy(local)
    mixed["claims"].append({**mixed["claims"][0], "claim_id": "dscc_other", "decision": {
        "decision_id": "decision_other", "action": "replace", "scope": "consumer_local",
        "owner_id": "consumer_b"}})
    with pytest.raises(ValueError, match="share one owner"):
        save_dataset_structure_assertion(repo, mixed)

    wrong_hash = _assertion_payload(asset_id, snapshot_id, profile)
    wrong_hash["evidence"][0]["source_refs"][0]["payload_hash"] = "b" * 64
    with pytest.raises(ValueError, match="hash"):
        save_dataset_structure_assertion(repo, wrong_hash)

    inconsistent_role = _assertion_payload(asset_id, snapshot_id, profile)
    inconsistent_role["evidence"].append({**deepcopy(inconsistent_role["evidence"][0]),
        "evidence_id": "dsce_other", "source_refs": [{"artifact_id": profile.artifact_id,
        "payload_hash": profile.payload_hash, "role": "reviewed_metadata"}]})
    with pytest.raises(ValueError, match="inconsistent role"):
        save_dataset_structure_assertion(repo, inconsistent_role)

    mislabeled = _assertion_payload(asset_id, snapshot_id, profile)
    mislabeled["evidence"][0]["source_refs"][0]["role"] = "reviewed_metadata"
    with pytest.raises(ValueError, match="primary source role"):
        save_dataset_structure_assertion(repo, mislabeled)

    wrong_asset = _assertion_payload(asset_id, snapshot_id, profile)
    wrong_asset["snapshot"]["asset_id"] = "asset_wrong"
    with pytest.raises(ValueError, match="asset_id"):
        save_dataset_structure_assertion(repo, wrong_asset)

    unknown_table = _assertion_payload(asset_id, snapshot_id, profile)
    unknown_table["subject"]["table"] = "not_in_snapshot"
    unknown_table["claims"][0]["value"]["key_columns"][0]["table"] = "not_in_snapshot"
    with pytest.raises(ValueError, match="inventory"):
        save_dataset_structure_assertion(repo, unknown_table)

    promoted = _assertion_payload(asset_id, snapshot_id, profile)
    promoted["evidence"][0]["kind"] = "structural_decision"
    promoted["evidence"][0]["source_refs"] = [{"artifact_id": local_artifact.artifact_id,
        "payload_hash": local_artifact.payload_hash, "role": "decision"}]
    promoted["claims"][0]["decision"] = {"decision_id": "decision_promoted", "action": "promote",
        "scope": "source_confirmed_structural", "promotion_of": "decision_local"}
    promoted_artifact = save_dataset_structure_assertion(repo, promoted).artifact
    assert promoted_artifact.scope == "universal" and promoted_artifact.owner_id is None
    promoted["evidence"][0]["source_refs"][0] = {"artifact_id": profile.artifact_id,
        "payload_hash": profile.payload_hash, "role": "decision"}
    with pytest.raises(ValueError, match="decision source role"):
        save_dataset_structure_assertion(repo, promoted)

    local_context = _context_payload(asset_id, snapshot_id, local_artifact)
    local_context["resolved_as_of"] = "2030-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="another consumer"):
        save_dataset_structure_context(repo, local_context, consumer_id="consumer_dsc_test")


def test_dsc_context_adapter_binds_owner_lineage_and_reuses(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    assertion = save_dataset_structure_assertion(
        repo, _assertion_payload(asset_id, snapshot_id, profile)).artifact
    payload = _context_payload(asset_id, snapshot_id, assertion)
    payload["resolved_as_of"] = "2030-01-01T00:00:00+00:00"
    created = save_dataset_structure_context(repo, payload, consumer_id="consumer_dsc_test")
    reused = save_dataset_structure_context(repo, payload, consumer_id="consumer_dsc_test")
    assert created.outcome == "created" and reused.outcome == "reused"
    assert created.artifact.owner_id == "consumer_dsc_test"
    assert created.artifact.source_artifacts == ({"artifact_id": assertion.artifact_id,
                                                   "role": "assertion_pin"},)
    payload["selector_results"][0]["pins"][0]["reuse_disposition"] = "reuse_candidate"
    with pytest.raises(ValueError, match="only fresh"):
        save_dataset_structure_context(repo, payload, consumer_id="consumer_dsc_test")


def test_dsc_context_rejects_pinned_assertion_mismatches_and_sensitivity(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    assertion = save_dataset_structure_assertion(
        repo, _assertion_payload(asset_id, snapshot_id, profile)).artifact
    base = _context_payload(asset_id, snapshot_id, assertion)
    base["resolved_as_of"] = "2030-01-01T00:00:00+00:00"
    for field, value, message in (
        ("payload_hash", "b" * 64, "snapshot or hash"),
        ("assertion_id", "dsca_other", "assertion ID"),
        ("resolution", "observed", "resolution"),
    ):
        value_payload = deepcopy(base)
        value_payload["selector_results"][0]["pins"][0][field] = value
        with pytest.raises(ValueError, match=message):
            save_dataset_structure_context(repo, value_payload, consumer_id="consumer_dsc_test")
    selector_payload = deepcopy(base)
    selector_payload["selector_results"][0]["selector"]["predicate"] = "table.structure/dataset_form"
    selector_payload["request_fingerprint"] = request_fingerprint({
        "protocol_version": selector_payload["protocol_version"],
        "supported_context_versions": selector_payload["supported_context_versions"],
        "snapshot": selector_payload["snapshot"], "as_of": selector_payload["request_as_of"],
        "tables": selector_payload["selected_tables"],
        "selectors": [item["selector"] for item in selector_payload["selector_results"]],
        "consumer_id": selector_payload["consumer_id"],
    })
    with pytest.raises(ValueError, match="persisted selector"):
        save_dataset_structure_context(repo, selector_payload, consumer_id="consumer_dsc_test")
    downgraded = deepcopy(base)
    downgraded["sensitivity"] = "external_safe"
    with pytest.raises(ValueError, match="maximum pinned"):
        save_dataset_structure_context(repo, downgraded, consumer_id="consumer_dsc_test")


def test_dsc_context_without_fulfilled_pins_is_external_safe(aar):
    repo, asset_id, snapshot_id = aar
    profile = _profile(repo, asset_id, snapshot_id)
    payload = _context_payload(asset_id, snapshot_id, profile)
    payload["overall_result"] = "unfulfilled"
    payload["sensitivity"] = "external_safe"
    payload["selector_results"][0].pop("pins")
    payload["selector_results"][0].update(
        result="unavailable", reason_codes=["DSC_R_NO_EVIDENCE"])
    assert save_dataset_structure_context(repo, payload, consumer_id="consumer_dsc_test").outcome == "created"
    payload["sensitivity"] = "internal"
    with pytest.raises(ValueError, match="maximum pinned"):
        save_dataset_structure_context(repo, payload, consumer_id="consumer_dsc_test")


def test_profile_backed_schema_observer_and_resolver_are_atomic_and_reusable(aar):
    repo, asset_id, snapshot_id = aar
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": "private_applications"},
              {"columns": ["private_application_id", "amount"], "col_count": 2})
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "column_name": "private_application_id", "classification": "numerical",
        "data_type": "integer", "description": "must not leak", "discrepancies": [],
        "notes": "", "role": "Identifier", "role_reviewed": 1, "provisional": 0, "profile_json": {
            "total_count": 10, "non_null_count": 8, "null_count": 2,
            "cardinality": 8, "top_k": {"private-value": 8}, "calculation_method": "exact",
        }, "updated_at": db.now_ist(),
    })
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "column_name": "amount", "classification": "numerical", "data_type": "decimal",
        "description": "also must not leak", "discrepancies": [], "notes": "", "role": "Feature",
        "profile_json": {"total_count": 10, "non_null_count": 10, "null_count": 0,
                         "cardinality": 10, "calculation_method": "exact"}, "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    assert SUPPORTED_OBSERVER_PREDICATES == {
        "table.physical/schema_column", "table.structure/entity_binding",
        "table.structure/row_grain",
    }

    first = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"])
    second = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"])
    assert [item.outcome for item in first] == ["created", "created"]
    assert [item.outcome for item in second] == ["reused", "reused"]
    assertions = [repo.get(item.artifact.artifact_id)[1] for item in first]
    assert [item["instance_key"] for item in assertions] == ["column:amount", "column:private_application_id"]
    assertion = assertions[1]
    assert len(assertion["assertion_id"].split("_", 1)[1]) == 64
    assert assertion["claims"][0]["value"]["nullable"] is True
    assert assertion["evidence"][0]["basis"] == {
        "population": "private_applications", "total_count": 10,
        "exclusions": {"physical_null": 2, "confirmed_special": 0, "parse_failure": 0},
        "usable_count": 8, "computation": "exact",
    }
    rendered = json.dumps(assertion)
    assert "private-value" not in rendered and "must not leak" not in rendered

    selector = {"selector_id": "schema", "subject": {"kind": "table", "table": "private_applications"},
                "predicate": "table.physical/schema_column", "requirement": "required",
                "accepted_resolution_states": ["observed"]}
    request = {"protocol_version": "1", "supported_context_versions": ["1"],
               "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "as_of": "latest",
               "tables": ["private_applications"], "selectors": [selector], "consumer_id": "consumer_schema"}
    response = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-01-01T00:00:00+00:00")
    assert response["overall_result"] == "fulfilled"
    assert [pin["reuse_disposition"] for pin in response["selector_results"][0]["pins"]] == ["exact_reused", "exact_reused"]
    assert [repo.get(pin["artifact_id"])[1]["instance_key"]
            for pin in response["selector_results"][0]["pins"]] == ["column:amount", "column:private_application_id"]
    repeated = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-01-01T00:00:00+00:00")
    assert repeated["context_ref"] == response["context_ref"]

    optional = {**request, "selectors": [{**selector, "requirement": "optional",
                                             "accepted_resolution_states": ["confirmed"]}]}
    rejected = resolve_dataset_structure_context(repo, optional, clock=lambda: "2030-01-02T00:00:00+00:00")
    assert rejected["selector_results"][0]["reason_codes"] == ["DSC_R_STATE_NOT_ACCEPTED"]

    db.update("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "column_name": "private_application_id",
    }, {"profile_json": {"total_count": 10, "non_null_count": 10, "null_count": 0,
                            "cardinality": 10, "calculation_method": "exact"}, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    replacement = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"])
    assert replacement[0].outcome == "created"
    assert repo.get(replacement[0].artifact.artifact_id)[1]["assertion_id"] == assertions[0]["assertion_id"]
    assert repo.get_metadata(first[0].artifact.artifact_id).status == "superseded"


def test_schema_resolver_optional_and_unproduced_boundaries_fail_closed(aar, monkeypatch):
    repo, asset_id, snapshot_id = aar
    base = {"protocol_version": "1", "supported_context_versions": ["1"],
            "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "as_of": "latest",
            "tables": ["private_applications"], "consumer_id": "consumer_boundaries"}
    schema = {"selector_id": "schema", "subject": {"kind": "table", "table": "private_applications"},
              "predicate": "table.physical/schema_column", "requirement": "optional",
              "accepted_resolution_states": ["observed"]}
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("table read")))
    optional = resolve_dataset_structure_context(repo, {**base, "selectors": [schema]},
                                                 clock=lambda: "2030-01-03T00:00:00+00:00")
    assert optional["selector_results"][0]["reason_codes"] == ["DSC_R_OPTIONAL_NOT_MATERIALIZED"]
    assert not repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="active")

    known = {**schema, "selector_id": "form", "predicate": "table.structure/dataset_form", "requirement": "required",
             "accepted_resolution_states": ["observed"]}
    future = {**schema, "selector_id": "future", "predicate": "table.future/experimental_facet", "requirement": "required"}
    reasons = resolve_dataset_structure_context(repo, {**base, "selectors": [known, future]},
                                                clock=lambda: "2030-01-04T00:00:00+00:00")
    assert [row["reason_codes"] for row in reasons["selector_results"]] == [
        ["DSC_R_NO_EVIDENCE"], ["DSC_R_UNSUPPORTED_FACET"]]

    malformed = {**schema, "requirement": "required"}
    missing = resolve_dataset_structure_context(repo, {**base, "selectors": [malformed]},
                                               clock=lambda: "2030-01-05T00:00:00+00:00")
    assert missing["selector_results"][0]["reason_codes"] == ["DSC_R_SOURCE_MISSING"]

    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "column_name": "private_application_id", "classification": "numerical", "data_type": "integer",
        "description": "", "discrepancies": [], "notes": "", "role": "Identifier",
        "profile_json": {}, "updated_at": db.now_ist(),
    })
    malformed_column = repo.save({"data_type": "integer", "calculation_method": "exact",
                                  "total_count": 10, "non_null_count": 9, "null_count": 2},
                                artifact_type="column_profile", asset_id=asset_id, snapshot_id=snapshot_id,
                                population_fingerprint=stable_fingerprint({"table": "private_applications"}),
                                methodology_fingerprint=stable_fingerprint({"test": "malformed"}),
                                scope="universal", table="private_applications",
                                feature="private_application_id").artifact
    repo.save({"table": "private_applications", "columns": ["private_application_id"],
               "column_count": 1, "row_count": 10}, artifact_type="table_profile", asset_id=asset_id,
              snapshot_id=snapshot_id, population_fingerprint=stable_fingerprint({"table": "private_applications"}),
              methodology_fingerprint=stable_fingerprint({"test": "malformed-table"}), scope="universal",
              table="private_applications", source_artifact_ids=(malformed_column.artifact_id,))
    malformed_response = resolve_dataset_structure_context(repo, {**base, "selectors": [malformed]},
                                                           clock=lambda: "2030-01-06T00:00:00+00:00")
    assert malformed_response["selector_results"][0]["reason_codes"] == ["DSC_R_INSUFFICIENT_BASIS"]

    class BrokenLoader:
        def reference(self, _snapshot_id):
            raise KeyError("secret snapshot detail")
    with pytest.raises(DSCContractError) as exc:
        resolve_dataset_structure_context(repo, {**base, "selectors": [schema]},
                                          snapshot_loader=BrokenLoader())
    assert exc.value.code == ERROR_SNAPSHOT_MISMATCH
    assert "secret snapshot detail" not in str(exc.value)


@pytest.mark.parametrize("artifact_type, scope, owner_id", [
    ("dataset_structure_assertion", "universal", None),
    ("dataset_structure_context", "diagnostic_local", "consumer_dsc_test"),
])
def test_dsc_direct_blob_save_is_rejected(aar, artifact_type, scope, owner_id):
    repo, asset_id, snapshot_id = aar
    with pytest.raises(ValueError, match="does not allow blob"):
        repo.save_blob(b"not-a-dsc-payload", {},
                       payload_media_type="application/vnd.apache.parquet",
                       payload_extension="parquet",
                       **_base_args(asset_id, snapshot_id, artifact_type,
                                    scope=scope, owner_id=owner_id))


def _phase_a_identifier(repo, asset_id, snapshot_id, *, distinct, nulls=0, special=0,
                        reviewed=True, role="Identifier"):
    """Seed one exact aggregate-only identifier profile for Phase-A assertions."""
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": "private_applications"}, {
        "columns": ["private_application_id"], "col_count": 1, "row_count": 10})
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications", "column_name": "private_application_id",
        "classification": "numerical", "data_type": "integer", "description": "private identifier text",
        "discrepancies": [], "notes": "", "role": role,
        "role_reviewed": int(reviewed), "provisional": 0 if reviewed else 1,
        "missing_value_codes_json": [-999] if special else [], "missing_codes_confirmed": special > 0,
        "profile_json": {"total_count": 10, "non_null_count": 10 - nulls,
            "null_count": nulls, "physical_null_count": nulls, "cardinality": distinct,
            "calculation_method": "exact", "regular_value_count": 10 - nulls - special,
            "special_values_confirmed": special > 0, "special_value_row_count": special,
            "profile_basis": "confirmed_regular_values" if special else None,
            "normalized_special_values": ["-999"] if special else [],
            "special_value_counts": {"-999": special} if special else {},
            "top_k": {"private-raw-value": 1}}, "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)


def _phase_a_request(asset_id, snapshot_id, predicate, states, requirement="required"):
    return {"protocol_version": "1", "supported_context_versions": ["1"],
            "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "as_of": "latest",
            "tables": ["private_applications"], "consumer_id": "phase_a_consumer", "selectors": [{
                "selector_id": predicate.rsplit("/", 1)[-1],
                "subject": {"kind": "table", "table": "private_applications"},
                "predicate": predicate, "requirement": requirement, "accepted_resolution_states": states,
            }]}


def test_phase_a_reviewed_identifier_entity_and_grain_matrix(aar):
    repo, asset_id, snapshot_id = aar
    _phase_a_identifier(repo, asset_id, snapshot_id, distinct=6)
    observed = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"], predicates={
        "table.structure/entity_binding", "table.structure/row_grain"})
    entity, grain = [repo.get(item.artifact.artifact_id)[1] for item in observed]
    assert entity["resolution"]["status"] == "proposed"
    assert entity["claims"][0]["authority"] == "source_reviewed_metadata"
    assert grain["resolution"] == {"status": "unknown", "effective_claim_ids": [],
                                    "reason_codes": ["DSC_R_INSUFFICIENT_BASIS"], "conflict_ids": []}
    assert grain["claims"] == []
    assert grain["evidence"][0]["measurements"] == [
        {"name": "distinct_key_count", "count": 6}, {"name": "duplicate_excess_rows", "count": 4}]
    assert "private-raw-value" not in json.dumps({"entity": entity, "grain": grain})
    entity_response = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.structure/entity_binding", ["proposed"]),
        clock=lambda: "2030-02-01T00:00:00+00:00")
    assert entity_response["selector_results"][0]["result"] == "fulfilled"
    grain_response = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.structure/row_grain", ["observed"]),
        clock=lambda: "2030-02-01T00:00:01+00:00")
    assert grain_response["selector_results"][0]["reason_codes"] == ["DSC_R_STATE_NOT_ACCEPTED"]


@pytest.mark.parametrize("nulls,special", [(1, 0), (0, 1)])
def test_phase_a_null_or_confirmed_special_never_confirms_grain(aar, nulls, special):
    repo, asset_id, snapshot_id = aar
    _phase_a_identifier(repo, asset_id, snapshot_id, distinct=10 - nulls - special, nulls=nulls, special=special)
    payload = repo.get(observe_dataset_structure(repo, snapshot_id, tables=["private_applications"],
                       predicates=["table.structure/row_grain"])[0].artifact.artifact_id)[1]
    assert payload["resolution"]["status"] == "unknown" and not payload["claims"]
    assert payload["evidence"][0]["basis"]["exclusions"]["physical_null"] == nulls
    assert payload["evidence"][0]["basis"]["exclusions"]["confirmed_special"] == special


@pytest.mark.parametrize("reviewed,role", [(False, "Identifier"), (True, "Feature")])
def test_phase_a_unreviewed_or_non_identifier_has_no_entity_candidate(aar, reviewed, role):
    repo, asset_id, snapshot_id = aar
    _phase_a_identifier(repo, asset_id, snapshot_id, distinct=10, reviewed=reviewed, role=role)
    outcomes = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"], predicates={
        "table.structure/entity_binding", "table.structure/row_grain"})
    assert len(outcomes) == 1
    grain = repo.get(outcomes[0].artifact.artifact_id)[1]
    assert grain["predicate"] == "table.structure/row_grain"
    assert grain["resolution"]["status"] == "unknown" and not grain["claims"]
    assert grain["evidence"][0]["measurements"] == [
        {"name": "reviewed_identifier_candidate_count", "count": 0},
        {"name": "reviewed_temporal_partner_count", "count": 0},
        {"name": "candidate_cap_exceeded", "count": 0},
    ]


def test_phase_a_unique_identifier_observed_reuses_and_optional_never_observes(aar):
    repo, asset_id, snapshot_id = aar
    _phase_a_identifier(repo, asset_id, snapshot_id, distinct=10)
    optional = _phase_a_request(asset_id, snapshot_id, "table.structure/row_grain", ["observed"], "optional")
    first = resolve_dataset_structure_context(repo, optional, clock=lambda: "2030-02-02T00:00:00+00:00")
    assert first["selector_results"][0]["reason_codes"] == ["DSC_R_OPTIONAL_NOT_MATERIALIZED"]
    assert not repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="active")
    created = observe_dataset_structure(repo, snapshot_id, tables=["private_applications"],
                                         predicates=["table.structure/row_grain"])
    payload = repo.get(created[0].artifact.artifact_id)[1]
    assert payload["resolution"]["status"] == "observed" and len(payload["claims"]) == 1
    second = resolve_dataset_structure_context(repo, optional, clock=lambda: "2030-02-02T00:00:01+00:00")
    assert second["selector_results"][0]["pins"][0]["reuse_disposition"] == "exact_reused"
    assert payload["assertion_id"].startswith("dsca_") and payload["claims"][0]["claim_id"].startswith("dscc_")


def test_phase_a_multiple_unique_identifiers_remain_ambiguous(aar):
    repo, asset_id, snapshot_id = aar
    _phase_a_identifier(repo, asset_id, snapshot_id, distinct=10)
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": "private_applications"}, {
        "columns": ["private_application_id", "application_key"], "col_count": 2})
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": "private_applications",
        "column_name": "application_key", "classification": "categorical",
        "data_type": "string", "description": "second private identifier",
        "discrepancies": [], "notes": "", "role": "Identifier", "role_reviewed": 1, "provisional": 0,
        "missing_value_codes_json": [], "missing_codes_confirmed": 0,
        "profile_json": {"total_count": 10, "non_null_count": 10, "null_count": 0,
                         "physical_null_count": 0, "cardinality": 10,
                         "regular_value_count": 10, "calculation_method": "exact",
                         "top_k": {"secret-key": 1}},
        "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)

    outcome = observe_dataset_structure(
        repo, snapshot_id, tables=["private_applications"],
        predicates=["table.structure/row_grain"],
    )[0]
    grain = repo.get(outcome.artifact.artifact_id)[1]
    assert grain["resolution"] == {
        "status": "unknown", "effective_claim_ids": [],
        "reason_codes": ["DSC_R_AMBIGUOUS_CANDIDATES"], "conflict_ids": [],
    }
    assert len(grain["claims"]) == 2 and len(grain["evidence"]) == 2
    assert len({evidence["evidence_id"] for evidence in grain["evidence"]}) == 2
    assert "secret-key" not in json.dumps(grain)

    response = resolve_dataset_structure_context(
        repo,
        _phase_a_request(asset_id, snapshot_id, "table.structure/row_grain", ["unknown"]),
        clock=lambda: "2030-02-03T00:00:00+00:00",
    )
    assert response["overall_result"] == "fulfilled"
    assert response["selector_results"][0]["pins"][0]["resolution"] == "unknown"


def test_phase_b_composite_grain_is_bounded_aggregate_only_and_reusable():
    """The producer reads once for pairs and never places values in DSC."""
    def profile(role, *, special=False, special_values=None):
        return {"metadata_reviewed": True, "role": role, "total_count": 4,
                "non_null_count": 4, "null_count": 0, "regular_value_count": 4,
                "distinct_count": 2, "special_values_confirmed": special,
                "special_values": (special_values if special_values is not None else [-999]) if special else [],
                "special_value_row_count": 0}
    identifier = SimpleNamespace(artifact_id="art_identifier", payload_hash="a" * 64,
                                 artifact_type="column_profile", feature="identifier")
    period = SimpleNamespace(artifact_id="art_period", payload_hash="b" * 64,
                             artifact_type="column_profile", feature="period")
    table = SimpleNamespace(artifact_id="art_table", payload_hash="c" * 64,
                            artifact_type="table_profile")
    class Loader:
        def __init__(self, frame): self.frame, self.calls = frame, []
        def load_table(self, snapshot, name, columns):
            self.calls.append((snapshot, name, columns)); return self.frame.loc[:, columns]
    loader = Loader(pd.DataFrame({"identifier": ["raw-a", "raw-a", "raw-b", "raw-b"],
                                  "period": ["2024Q1", "2024Q2", "2024Q1", "2024Q2"]}))
    payload = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
        {"row_count": 4}, [(identifier, profile("Identifier"))], [(period, profile("Period"))], loader)
    assert loader.calls == [("item_x", "private_applications", ["identifier", "period"])]
    assert payload["resolution"]["status"] == "observed"
    assert payload["claims"][0]["value"]["key_columns"] == [
        {"table": "private_applications", "column": "identifier"},
        {"table": "private_applications", "column": "period"}]
    assert payload["evidence"][-1]["kind"] == "bounded_scan"
    assert "raw-a" not in json.dumps(payload)

    duplicate = Loader(pd.DataFrame({"identifier": ["x"] * 4, "period": ["p"] * 4}))
    duplicated = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
        {"row_count": 4}, [(identifier, profile("Identifier"))], [(period, profile("Period"))], duplicate)
    assert duplicated["resolution"]["reason_codes"] == ["DSC_R_INSUFFICIENT_BASIS"]

    for frame, special in ((pd.DataFrame({"identifier": [None, "a", "b", "c"], "period": [1, 2, 3, 4]}), False),
                           (pd.DataFrame({"identifier": [-999, "a", "b", "c"], "period": [1, 2, 3, 4]}), True)):
        excluded = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
            {"row_count": 4}, [(identifier, profile("Identifier", special=special))],
            [(period, profile("Period"))], Loader(frame))
        assert excluded["resolution"]["status"] == "unknown" and not excluded["claims"]

    bool_excluded = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
        {"row_count": 4}, [(identifier, profile("Identifier", special=True, special_values=[1]))],
        [(period, profile("Period"))], Loader(pd.DataFrame({"identifier": [True, False, False, False],
                                                                "period": [1, 2, 3, 4]})))
    assert bool_excluded["evidence"][-1]["basis"]["exclusions"]["confirmed_special"] == 1

    two_periods = SimpleNamespace(artifact_id="art_period_2", payload_hash="d" * 64,
                                  artifact_type="column_profile", feature="period_2")
    ambiguous = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
        {"row_count": 4}, [(identifier, profile("Identifier"))],
        [(period, profile("Period")), (two_periods, profile("Date"))],
        Loader(pd.DataFrame({"identifier": ["a", "a", "b", "b"], "period": [1, 2, 1, 2], "period_2": [1, 2, 1, 2]})))
    assert ambiguous["resolution"]["reason_codes"] == ["DSC_R_AMBIGUOUS_CANDIDATES"]
    assert len({evidence["evidence_id"] for evidence in ambiguous["evidence"]}) == len(ambiguous["evidence"])

    capped_loader = Loader(pd.DataFrame({"identifier": [], "period": []}))
    capped = dsc_producer._grain_payload("asset_x", "item_x", "private_applications", table,
        {"row_count": 4}, [(identifier, profile("Identifier"))] * 33, [(period, profile("Period"))], capped_loader)
    assert capped["resolution"]["reason_codes"] == ["DSC_R_INSUFFICIENT_BASIS"] and not capped_loader.calls


def test_phase_b_resolver_isolates_failed_grain_observation_from_schema(aar):
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": ["private_application_id", "reviewed_period"], "col_count": 2, "row_count": 4,
    })
    for column, role, data_type in (
        ("private_application_id", "Identifier", "integer"),
        ("reviewed_period", "Period", "string"),
    ):
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": table, "column_name": column,
            "classification": "categorical", "data_type": data_type, "description": "",
            "discrepancies": [], "notes": "", "role": role, "role_reviewed": 1, "provisional": 0,
            "profile_json": {"total_count": 4, "non_null_count": 4, "null_count": 0,
                             "physical_null_count": 0, "regular_value_count": 4,
                             "cardinality": 2, "calculation_method": "exact", "top_k": {}},
            "updated_at": db.now_ist(),
        })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)

    reference = dsc_producer.SnapshotLoader().reference(snapshot_id)
    calls = []
    class FailingScanLoader:
        def reference(self, received_snapshot):
            calls.append(("reference", received_snapshot))
            return reference

        def load_table(self, received_snapshot, received_table, columns=None, **_kwargs):
            calls.append(("load_table", received_snapshot, received_table, list(columns or [])))
            raise OSError("bounded scan unavailable")

    schema = {"selector_id": "schema", "subject": {"kind": "table", "table": table},
              "predicate": "table.physical/schema_column", "requirement": "required",
              "accepted_resolution_states": ["observed"]}
    grain = {"selector_id": "grain", "subject": {"kind": "table", "table": table},
             "predicate": "table.structure/row_grain", "requirement": "required",
             "accepted_resolution_states": ["observed"]}
    request = {"protocol_version": "1", "supported_context_versions": ["1"],
               "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "as_of": "latest",
               "tables": [table], "selectors": [schema, grain], "consumer_id": "phase_b_consumer"}
    response = resolve_dataset_structure_context(
        repo, request, snapshot_loader=FailingScanLoader(),
        clock=lambda: "2030-03-01T00:00:00+00:00")

    assert response["selector_results"][0]["result"] == "fulfilled"
    assert [pin["reuse_disposition"] for pin in response["selector_results"][0]["pins"]] == ["fresh", "fresh"]
    assert response["selector_results"][1]["result"] == "unavailable"
    assert response["selector_results"][1]["reason_codes"] == ["DSC_R_INSUFFICIENT_BASIS"]
    assert [call[0] for call in calls] == ["reference", "reference", "reference", "load_table"]


def test_phase_b_composite_grain_resolver_scans_once_reuses_and_refreshes(aar, monkeypatch):
    """A retained composite scan is optional, exact-reusable, and dependency-bound."""
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": ["private_application_id", "reviewed_period"], "col_count": 2, "row_count": 4,
    })
    profiles = {
        "private_application_id": {"role": "Identifier", "data_type": "integer", "cardinality": 2,
                                   "top_k": {"raw-identifier-a": 2}, "distinct_set_hash": "aggregate-id-v1"},
        "reviewed_period": {"role": "Period", "data_type": "string", "cardinality": 2,
                              "top_k": {"raw-period-a": 2}, "distinct_set_hash": "aggregate-period-v1"},
    }
    for column, profile in profiles.items():
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": table, "column_name": column,
            "classification": "categorical", "data_type": profile["data_type"], "description": "",
            "discrepancies": [], "notes": "", "role": profile["role"], "role_reviewed": 1, "provisional": 0,
            "profile_json": {"total_count": 4, "non_null_count": 4, "null_count": 0,
                             "physical_null_count": 0, "regular_value_count": 4,
                             "calculation_method": "exact", **{key: value for key, value in profile.items()
                                                                    if key not in {"role", "data_type"}}},
            "updated_at": db.now_ist(),
        })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)

    source = pd.DataFrame({"private_application_id": ["raw-identifier-a", "raw-identifier-a",
                                                        "raw-identifier-b", "raw-identifier-b"],
                           "reviewed_period": ["raw-period-a", "raw-period-b",
                                               "raw-period-a", "raw-period-b"]})
    calls = []
    def load_table(_self, received_snapshot, received_table, columns=None, **_kwargs):
        calls.append((received_snapshot, received_table, list(columns or [])))
        return source.loc[:, columns].copy()
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table", load_table)

    optional = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.structure/row_grain", ["observed"], "optional"),
        clock=lambda: "2030-03-01T00:00:00+00:00")
    assert optional["selector_results"][0]["reason_codes"] == ["DSC_R_OPTIONAL_NOT_MATERIALIZED"]
    assert calls == []

    request = _phase_a_request(asset_id, snapshot_id, "table.structure/row_grain", ["observed"])
    first = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-03-01T00:00:01+00:00")
    assert calls == [(snapshot_id, table, ["private_application_id", "reviewed_period"])]
    first_pin = first["selector_results"][0]["pins"][0]
    first_grain = repo.get(first_pin["artifact_id"])[1]
    assert first_pin["reuse_disposition"] == "fresh"
    assert first_grain["resolution"]["status"] == "observed"
    assert first_grain["claims"][0]["value"]["key_columns"] == [
        {"table": table, "column": "private_application_id"},
        {"table": table, "column": "reviewed_period"},
    ]
    rendered_evidence = json.dumps(first_grain["evidence"])
    assert "raw-identifier-a" not in rendered_evidence and "raw-period-a" not in rendered_evidence

    reused = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-03-01T00:00:01+00:00")
    reused_pin = reused["selector_results"][0]["pins"][0]
    assert len(calls) == 1
    assert (reused_pin["artifact_id"], reused_pin["payload_hash"]) == (
        first_pin["artifact_id"], first_pin["payload_hash"])
    assert reused_pin["reuse_disposition"] == "exact_reused"

    updated_period = {"total_count": 4, "non_null_count": 4, "null_count": 0,
                      "physical_null_count": 0, "regular_value_count": 4, "cardinality": 2,
                      "calculation_method": "exact", "top_k": {"raw-period-a": 2},
                      "distinct_set_hash": "aggregate-period-v2"}
    db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table,
                                      "column_name": "reviewed_period"},
              {"profile_json": updated_period, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    replacement = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-03-01T00:00:02+00:00")
    replacement_pin = replacement["selector_results"][0]["pins"][0]
    assert len(calls) == 2 and replacement_pin["reuse_disposition"] == "fresh"
    assert replacement_pin["artifact_id"] != first_pin["artifact_id"]
    assert repo.get_metadata(first_pin["artifact_id"]).status == "superseded"
