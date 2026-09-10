"""AAR registration and persistence proofs for Dataset Structure Context."""
from __future__ import annotations

import json
import hashlib
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.dataset_structure_context import payload_hash, request_fingerprint, project_materialized_dataset_structure
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
        "intent": "fresh", "ingest_status": "ready", "sourcing_tenant_id": "tenant-a",
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
        "snapshot_profile", "table_profile", "table_inventory_profile", "schema_profile", "column_profile",
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
        "table.structure/row_grain", "table.temporal/temporal_binding",
        "table.temporal/observed_cadence",
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


def test_phase_c2_temporal_candidates_are_atomic_aggregate_only_and_dependency_local(aar, monkeypatch):
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    columns = ["reviewed_date", "reviewed_period", "unreviewed_date", "provisional_period", "missing_review_date"]
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": columns, "col_count": len(columns), "row_count": 10,
    })
    seed = {
        "reviewed_date": {"role": " Date ", "reviewed": 1, "provisional": 0,
                          "date_parse_failure_count": 2, "special": 2},
        "reviewed_period": {"role": "period", "reviewed": 1, "provisional": 0,
                            "period_bounds": {"start_date": "2024-01-01", "end_date": "2024-12-31",
                                              "format": "calendar_quarter"},
                            "period_format_evidence_available": True,
                            "period_format_checked_regular_count": 9,
                            "period_format_failure_count": 0, "special": 0},
        "unreviewed_date": {"role": "Date", "reviewed": 0, "provisional": 0, "special": 0},
        "provisional_period": {"role": "Period", "reviewed": 1, "provisional": 1, "special": 0},
        "missing_review_date": {"role": "Date", "reviewed": None, "provisional": 0, "special": 0},
    }
    for column, settings in seed.items():
        special = settings["special"]
        profile = {"total_count": 10, "non_null_count": 9, "null_count": 1,
                   "physical_null_count": 1, "regular_value_count": 9 - special,
                   "cardinality": 3, "calculation_method": "exact", "top_k": {"raw-private": 1},
                   **{key: value for key, value in settings.items()
                      if key not in {"role", "reviewed", "provisional", "special"}}}
        row = {"item_id": snapshot_id, "table_name": table, "column_name": column,
               "classification": "categorical", "data_type": "string", "description": "private detail",
               "discrepancies": [], "notes": "", "role": settings["role"],
               "role_reviewed": settings["reviewed"], "provisional": settings["provisional"],
               "missing_value_codes_json": [-999] if special else [],
               "missing_codes_confirmed": int(bool(special)), "profile_json": profile,
               "updated_at": db.now_ist()}
        if special:
            profile.update({"profile_basis": "confirmed_regular_values", "normalized_special_values": ["-999"],
                            "special_value_counts": {"-999": special}, "special_value_row_count": special})
        db.insert("variable_inventory", row)
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)

    optional = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.temporal/temporal_binding", ["proposed"], "optional"),
        clock=lambda: "2030-04-01T00:00:00+00:00")
    assert optional["selector_results"][0]["reason_codes"] == ["DSC_R_OPTIONAL_NOT_MATERIALIZED"]
    assert not repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="active")

    created = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                         predicates=["table.temporal/temporal_binding"])
    payloads = {repo.get(outcome.artifact.artifact_id)[1]["instance_key"]: repo.get(outcome.artifact.artifact_id)[1]
                for outcome in created}
    assert set(payloads) == {"column:reviewed_date", "column:reviewed_period", "column:provisional_period"}
    date, period, provisional = (payloads["column:reviewed_date"], payloads["column:reviewed_period"],
                                 payloads["column:provisional_period"])
    assert date["resolution"]["status"] == period["resolution"]["status"] == "proposed"
    assert date["claims"][0]["authority"] == "source_reviewed_metadata"
    assert date["claims"][0]["value"] == {
        "kind": "temporal_binding", "axis_id": "column:reviewed_date", "temporal_type": "date",
        "columns": [{"table": table, "column": "reviewed_date"}], "precision": "unknown", "calendar": "unknown",
    }
    assert period["claims"][0]["value"]["temporal_type"] == "period"
    assert period["claims"][0]["value"]["precision"] == "quarter"
    assert period["claims"][0]["value"]["calendar"] == "gregorian"
    assert "timezone" not in date["claims"][0]["value"] and "timezone" not in period["claims"][0]["value"]
    date_basis = date["evidence"][0]["basis"]
    assert date_basis == {"population": table, "total_count": 10,
                          "exclusions": {"physical_null": 1, "confirmed_special": 2, "parse_failure": 2},
                          "usable_count": 5, "computation": "exact"}
    assert {item["name"]: item["count"] for item in date["evidence"][0]["measurements"]} == {
        "regular_value_rows": 7, "temporal_parse_failure_available": 1, "temporal_parse_failure_rows": 2}
    assert {item["name"]: item["count"] for item in period["evidence"][0]["measurements"]} == {
        "regular_value_rows": 9, "temporal_parse_failure_available": 1, "temporal_parse_failure_rows": 0}
    rendered = json.dumps(payloads)
    assert all(secret not in rendered for secret in ("raw-private", "private detail", "top_k", "period_bounds"))

    def no_raw_scan(*_args, **_kwargs):
        raise AssertionError("temporal profile observation must not read raw rows")
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table", no_raw_scan)
    request = _phase_a_request(asset_id, snapshot_id, "table.temporal/temporal_binding", ["proposed"])
    reused = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-04-01T00:00:01+00:00")
    old_pins = {pin["assertion_id"]: pin for pin in reused["selector_results"][0]["pins"]}
    assert len(old_pins) == 3 and all(pin["reuse_disposition"] == "exact_reused" for pin in old_pins.values())

    date_profile = next(row for row in db.query("variable_inventory", item_id=snapshot_id)
                        if row["column_name"] == "reviewed_date")["profile_json"]
    date_profile["date_parse_failure_count"] = 1
    db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table, "column_name": "reviewed_date"},
              {"profile_json": date_profile, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    refreshed = resolve_dataset_structure_context(repo, request, clock=lambda: "2030-04-01T00:00:02+00:00")
    new_pins = {pin["assertion_id"]: pin for pin in refreshed["selector_results"][0]["pins"]}
    old_by_key = {payload["instance_key"]: metadata.artifact_id for metadata in
                  repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="superseded")
                  for _checked, payload in [repo.get(metadata.artifact_id)]}
    active_by_key = {repo.get(pin["artifact_id"])[1]["instance_key"]: pin["artifact_id"]
                     for pin in new_pins.values()}
    assert active_by_key["column:reviewed_date"] != old_by_key["column:reviewed_date"]
    assert active_by_key["column:reviewed_period"] == old_pins[period["assertion_id"]]["artifact_id"]
    assert active_by_key["column:provisional_period"] == old_pins[provisional["assertion_id"]]["artifact_id"]


def test_phase_c2_temporal_parse_evidence_is_explicitly_unavailable_when_not_profiled(aar):
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": ["reviewed_period"], "col_count": 1, "row_count": 2,
    })
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": table, "column_name": "reviewed_period",
        "classification": "categorical", "data_type": "string", "description": "", "discrepancies": [],
        "notes": "", "role": "Period", "role_reviewed": 1, "provisional": 0,
        "missing_value_codes_json": [], "missing_codes_confirmed": 0,
        "profile_json": {"total_count": 2, "non_null_count": 2, "null_count": 0,
                         "physical_null_count": 0, "regular_value_count": 2, "cardinality": 2,
                         "calculation_method": "exact", "top_k": {"private-period": 1}}, "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    payload = repo.get(observe_dataset_structure(repo, snapshot_id, tables=[table],
                       predicates=["table.temporal/temporal_binding"])[0].artifact.artifact_id)[1]
    assert payload["claims"][0]["value"]["precision"] == payload["claims"][0]["value"]["calendar"] == "unknown"
    assert payload["evidence"][0]["basis"]["exclusions"]["parse_failure"] == 0
    assert {item["name"]: item["count"] for item in payload["evidence"][0]["measurements"]} == {
        "regular_value_rows": 2, "temporal_parse_failure_available": 0}


def test_phase_c2_period_precision_requires_full_zero_failure_format_evidence():
    column = SimpleNamespace(artifact_id="art_period_profile", payload_hash="a" * 64,
                             artifact_type="column_profile", feature="period")
    inventory = SimpleNamespace(artifact_id="art_period_inventory", payload_hash="b" * 64,
                                artifact_type="table_inventory_profile")
    profile = {"role": "Period", "metadata_reviewed": True, "total_count": 3,
               "non_null_count": 3, "physical_null_count": 0, "null_count": 0,
               "regular_value_count": 3, "distinct_count": 3,
               "period_bounds": {"start_date": "2024-01-01", "end_date": "2024-09-30",
                                 "format": "calendar_quarter"},
               "period_format_evidence_available": True,
               "period_format_checked_regular_count": 3, "period_format_failure_count": 1}
    payload = dsc_producer._temporal_payload("asset_x", "item_x", "orders", inventory, column, profile)
    assert payload["claims"][0]["value"]["precision"] == payload["claims"][0]["value"]["calendar"] == "unknown"
    assert payload["evidence"][0]["basis"]["exclusions"]["parse_failure"] == 1
    assert {item["name"]: item["count"] for item in payload["evidence"][0]["measurements"]} == {
        "regular_value_rows": 3, "temporal_parse_failure_available": 1, "temporal_parse_failure_rows": 1}


def test_phase_c2_temporal_withdrawal_is_owned_and_fails_closed(aar):
    repo, asset_id, snapshot_id = aar
    table, column = "private_applications", "reviewed_date"
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": [column], "col_count": 1, "row_count": 2,
    })
    db.insert("variable_inventory", {
        "item_id": snapshot_id, "table_name": table, "column_name": column,
        "classification": "categorical", "data_type": "string", "description": "", "discrepancies": [],
        "notes": "", "role": "Date", "role_reviewed": 1, "provisional": 0,
        "missing_value_codes_json": [], "missing_codes_confirmed": 0,
        "profile_json": {"total_count": 2, "non_null_count": 2, "null_count": 0,
                         "physical_null_count": 0, "regular_value_count": 2, "cardinality": 2,
                         "calculation_method": "exact"}, "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    proposal_outcome = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                                 predicates=["table.temporal/temporal_binding"])[0]
    proposal = repo.get(proposal_outcome.artifact.artifact_id)[1]
    unauthored_proposal = deepcopy(proposal)
    # Same temporal locator and proposed shape, but not the producer's exact
    # immutable provenance/dependency projection.
    unauthored_proposal["dependency_fingerprint"] = "f" * 64
    unauthored_artifact = save_dataset_structure_assertion(
        repo, unauthored_proposal, version=True).artifact
    confirmation = deepcopy(proposal)
    confirmation["assertion_id"] = "dsca_independent_temporal_confirmation"
    confirmation["claims"][0].update({
        "claim_id": "dscc_independent_temporal_confirmation",
        "authority": "source_confirmed_structural",
        "decision": {"decision_id": "decision_independent_temporal_confirmation", "action": "confirm",
                     "scope": "source_confirmed_structural"},
    })
    confirmation["resolution"] = {"status": "confirmed",
                                    "effective_claim_ids": ["dscc_independent_temporal_confirmation"],
                                    "reason_codes": [], "conflict_ids": []}
    # An external confirmation is an independently versioned assertion.  It
    # intentionally shares the candidate key but must not collide with (or be
    # mistaken for) the producer-owned proposal identity.
    confirmation_artifact = save_dataset_structure_assertion(repo, confirmation, version=True).artifact

    db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table, "column_name": column}, {
        "role_reviewed": 0, "updated_at": db.now_ist(),
    })
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    assert observe_dataset_structure(repo, snapshot_id, tables=[table],
                                     predicates=["table.temporal/temporal_binding"]) == ()
    assert repo.get_metadata(proposal_outcome.artifact.artifact_id).status == "superseded"
    assert repo.get_metadata(unauthored_artifact.artifact_id).status == "active"
    assert repo.get_metadata(confirmation_artifact.artifact_id).status == "active"
    response = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.temporal/temporal_binding", ["proposed"]),
        clock=lambda: "2030-04-02T00:00:00+00:00")
    assert response["selector_results"][0]["reason_codes"] == ["DSC_R_NO_EVIDENCE"]


def test_phase_cadence_states_parser_privacy_and_exact_reuse(aar, monkeypatch):
    """Cadence remains an aggregate-only, one-read observation over C1/C2 facts."""
    repo, asset_id, snapshot_id = aar
    table = "private_applications"

    def materialize(frame):
        rows = len(frame.index)
        db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
            "columns": ["entity", "observed_on"], "col_count": 2, "row_count": rows})
        for column, role, cardinality in (("entity", "Identifier", frame["entity"].nunique()),
                                          ("observed_on", "Date", frame["observed_on"].nunique())):
            db.insert("variable_inventory", {
                "item_id": snapshot_id, "table_name": table, "column_name": column,
                "classification": "categorical", "data_type": "string", "description": "private raw description",
                "discrepancies": [], "notes": "", "role": role, "role_reviewed": 1, "provisional": 0,
                "missing_value_codes_json": [], "missing_codes_confirmed": 0,
                "profile_json": {"total_count": rows, "non_null_count": rows, "null_count": 0,
                                 "physical_null_count": 0, "regular_value_count": rows,
                                 "cardinality": int(cardinality), "calculation_method": "exact",
                                 "top_k": {"private-entity-value": 1}}, "updated_at": db.now_ist()})
        persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
        observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=[
            "table.structure/entity_binding", "table.temporal/temporal_binding"])

    regular = pd.DataFrame({"entity": ["private-a"] * 3 + ["private-b"] * 3,
                            "observed_on": ["2024-01-01", "2024-02-01", "2024-03-01"] * 2})
    materialize(regular)
    calls = []
    def load_table(_self, received_snapshot, received_table, columns=None, **_kwargs):
        calls.append((received_snapshot, received_table, list(columns or [])))
        return regular.loc[:, columns].copy()
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table", load_table)
    created = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                         predicates=["table.temporal/observed_cadence"])
    assert len(created) == 1 and calls == [(snapshot_id, table, ["entity", "observed_on"])]
    payload = repo.get(created[0].artifact.artifact_id)[1]
    assert payload["resolution"]["status"] == "observed"
    assert payload["claims"][0]["value"]["cadence"] == "regular"
    assert payload["claims"][0]["value"]["observed_interval_class"] == {"unit": "month", "step": 1}
    counts = {item["name"]: item.get("count") for item in payload["evidence"][0]["measurements"]}
    assert counts["distinct_entity_axis_observations"] == 6
    assert counts["duplicate_entity_axis_excess_rows"] == 0
    assert counts["usable_delta_count"] == 4
    assert "private-a" not in json.dumps(payload) and "private-entity-value" not in json.dumps(payload)
    shadow_request = {"asset_id": asset_id, "snapshot_id": snapshot_id, "table": table,
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:observed_on",
        "axis_column": {"table": table, "column": "observed_on"},
        "grouping": [{"table": table, "column": "entity"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    before_rows = db.query("analysis_artifacts", order_by="artifact_id")
    before_events = db.query("analysis_artifact_events", order_by="event_id")
    projected = project_materialized_dataset_structure(shadow_request, repository=repo)
    assert projected["outcome"] == "fulfilled"
    assert projected["projection_version"] == 1
    assert projected["request_identity"] == request_fingerprint(shadow_request)
    assert projected["cadence"]["resolution_state"] == "observed"
    assert db.query("analysis_artifacts", order_by="artifact_id") == before_rows
    assert db.query("analysis_artifact_events", order_by="event_id") == before_events

    def no_scan(*_args, **_kwargs):
        raise AssertionError("exact cadence reuse must not read source rows")
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table", no_scan)
    reused = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                        predicates=["table.temporal/observed_cadence"])
    assert len(reused) == 1 and reused[0].outcome != "created"

    confirmed = deepcopy(payload)
    confirmed["assertion_id"] = "dsca_confirmed_cadence"
    confirmed["claims"][0].update({
        "claim_id": "dscc_confirmed_cadence", "authority": "source_confirmed_structural",
        "decision": {"decision_id": "decision_confirmed_cadence", "action": "confirm",
                     "scope": "source_confirmed_structural"}})
    confirmed["resolution"] = {"status": "confirmed", "effective_claim_ids": ["dscc_confirmed_cadence"],
                                 "reason_codes": [], "conflict_ids": []}
    confirmed["evidence"][0]["source_refs"].append({
        "artifact_id": created[0].artifact.artifact_id, "role": "dependency",
        "payload_hash": created[0].artifact.payload_hash})
    confirmation = save_dataset_structure_assertion(repo, confirmed, version=True).artifact
    response = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.temporal/observed_cadence", ["confirmed"]),
        clock=lambda: "2030-05-01T00:00:00+00:00")
    assert response["selector_results"][0]["pins"][0]["artifact_id"] == confirmation.artifact_id
    assert project_materialized_dataset_structure(shadow_request, repository=repo)["cadence"]["resolution_state"] == "confirmed"

    forged = deepcopy(payload)
    forged["assertion_id"] = "dsca_forged_cadence"
    forged["dependency_fingerprint"] = "f" * 64
    forged_artifact = save_dataset_structure_assertion(repo, forged, version=True).artifact
    # A malformed active same-locator row fails closed; it cannot be silently
    # ignored in favour of the otherwise valid confirmed cadence.
    forged_projection = project_materialized_dataset_structure(shadow_request, repository=repo)
    assert forged_projection["outcome"] == "ambiguous"
    assert repo.get_metadata(forged_artifact.artifact_id).status == "active"

    duplicate = deepcopy(confirmed)
    duplicate["assertion_id"] = "dsca_duplicate_cadence_confirmation"
    duplicate["claims"][0]["claim_id"] = "dscc_duplicate_cadence_confirmation"
    duplicate["claims"][0]["decision"]["decision_id"] = "decision_duplicate_cadence_confirmation"
    duplicate["resolution"]["effective_claim_ids"] = ["dscc_duplicate_cadence_confirmation"]
    save_dataset_structure_assertion(repo, duplicate, version=True)
    ambiguous = resolve_dataset_structure_context(
        repo, _phase_a_request(asset_id, snapshot_id, "table.temporal/observed_cadence", ["confirmed"]),
        clock=lambda: "2030-05-01T00:00:01+00:00")
    assert ambiguous["selector_results"][0]["reason_codes"] == ["DSC_R_AMBIGUOUS_CANDIDATES"]
    assert project_materialized_dataset_structure(shadow_request, repository=repo)["outcome"] == "ambiguous"

    # Removing the reviewed grouping withdraws only the mechanically proven
    # producer fact.  An independent bounded scan at the former locator is
    # not producer-owned and remains readable.
    db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table, "column_name": "entity"},
              {"role_reviewed": 0, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    assert observe_dataset_structure(repo, snapshot_id, tables=[table],
                                     predicates=["table.temporal/observed_cadence"]) == ()
    assert repo.get_metadata(forged_artifact.artifact_id).status == "active"


@pytest.mark.parametrize(("frame", "expected"), [
    (pd.DataFrame({"entity": ["a"] * 4 + ["b"] * 4,
                  "observed_on": ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01",
                                  "2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"]}), "mixed"),
    (pd.DataFrame({"entity": ["a", "a", "b", "b"],
                  "observed_on": ["2024-01-01", "not-a-date", "2024-01-01", "2024-02-01"]}), "unknown"),
])
def test_phase_cadence_mixed_and_unknown_have_closed_evidence(frame, expected):
    axis = {"column": "observed_on", "temporal_type": "date"}
    profile = {"special_values": []}
    candidate = {"axis": {**axis, "profile": (None, profile)}, "group": {"column": "entity", "profile": (None, profile)},
                 "table": (None, {"table": "orders"})}
    result = dsc_producer._cadence_scan_pair(frame, candidate)
    assert result["cadence"] == expected
    assert result["basis"]["total_count"] == sum(result["basis"]["exclusions"].values()) + result["basis"]["usable_count"]
    assert "not-a-date" not in json.dumps(result)


@pytest.mark.parametrize(("value", "accepted"), [
    ("2024Q1", True), ("2024-Q1", True), ("2024q1", True),
    (" 2024 Q1 ", True), ("2024Q0", False), ("2024-Q5", False), ("Q1-2024", False),
])
def test_phase_cadence_quarter_parser_matches_c2_profile_grammar(value, accepted):
    assert (dsc_producer._strict_quarter_key(value) is not None) is accepted


def test_d06_shadow_projector_rejects_deadline_and_broad_selector_without_writes(aar):
    repo, asset_id, snapshot_id = aar
    request = {"asset_id": asset_id, "snapshot_id": snapshot_id, "table": "private_applications",
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:period",
        "axis_column": {"table": "private_applications", "column": "period"},
        "grouping": [{"table": "private_applications", "column": "facility"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    before = (db.query("analysis_artifacts", order_by="artifact_id"),
              db.query("analysis_artifact_events", order_by="event_id"))
    import time
    timed = project_materialized_dataset_structure(request, repository=repo, deadline=time.monotonic() - 1)
    assert timed["outcome"] == "error" and timed["reason"] == "deadline_exceeded"
    assert set(timed) == {"projection_version", "request_identity", "outcome", "resolved_as_of", "reason"}
    assert set(timed["resolved_as_of"]) == {"timestamp", "read_boundary_fingerprint"}
    broad = dict(request, grouping=[])
    assert project_materialized_dataset_structure(broad, repository=repo)["outcome"] == "error"
    assert (db.query("analysis_artifacts", order_by="artifact_id"),
            db.query("analysis_artifact_events", order_by="event_id")) == before


def test_d06_shadow_projector_projects_real_producer_unknown_and_fails_closed(aar, monkeypatch):
    """Exercise the narrow seam against a producer artifact, not an envelope stub."""
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    frame = pd.DataFrame({"entity": ["a", "a", "b", "b"],
                          "observed_on": ["2024-01-01", "bad-date", "2024-01-01", "2024-02-01"]})
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": list(frame.columns), "col_count": 2, "row_count": len(frame)})
    for column, role in (("entity", "Identifier"), ("observed_on", "Date")):
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": table, "column_name": column,
            "classification": "categorical", "data_type": "string", "description": "",
            "discrepancies": [], "notes": "", "role": role, "role_reviewed": 1,
            "provisional": 0, "missing_value_codes_json": [], "missing_codes_confirmed": 0,
            "profile_json": {"total_count": 4, "non_null_count": 4, "null_count": 0,
                             "physical_null_count": 0, "regular_value_count": 4,
                             "cardinality": 2, "calculation_method": "exact", "top_k": {}},
            "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=[
        "table.structure/entity_binding", "table.temporal/temporal_binding"])
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table",
                        lambda _self, _snapshot, _table, columns=None, **_kwargs: frame.loc[:, columns].copy())
    created = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                        predicates=["table.temporal/observed_cadence"])
    assert len(created) == 1
    assertion = created[0].artifact
    request = {"asset_id": asset_id, "snapshot_id": snapshot_id, "table": table,
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:observed_on",
        "axis_column": {"table": table, "column": "observed_on"},
        "grouping": [{"table": table, "column": "entity"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    baseline = (db.query("analysis_artifacts", order_by="artifact_id"),
                db.query("analysis_artifact_events", order_by="event_id"))
    monkeypatch.setattr(dsc_producer, "_temporal_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("projection called producer")))
    monkeypatch.setattr(dsc_producer, "_entity_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("projection called producer")))
    projected = project_materialized_dataset_structure(request, repository=repo)
    assert projected["outcome"] == "fulfilled"
    assert projected["cadence"]["resolution_state"] == "unknown"
    assert projected["cadence"]["value"] == {"state": "unknown"}
    repeat = project_materialized_dataset_structure(request, repository=repo)
    assert repeat["resolved_as_of"]["read_boundary_fingerprint"] == projected["resolved_as_of"]["read_boundary_fingerprint"]
    assert (db.query("analysis_artifacts", order_by="artifact_id"),
            db.query("analysis_artifact_events", order_by="event_id")) == baseline

    # The boundary includes the selected snapshot/table catalogue marker even
    # though the cadence payload itself has not changed.
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {"row_count": 5})
    catalogue_changed = project_materialized_dataset_structure(request, repository=repo)
    assert catalogue_changed["outcome"] == "fulfilled"
    assert catalogue_changed["resolved_as_of"]["read_boundary_fingerprint"] != projected["resolved_as_of"]["read_boundary_fingerprint"]
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {"row_count": 4})

    path = repo.root / db.query_one("analysis_artifacts", artifact_id=assertion.artifact_id)["payload_path"]
    original = path.read_bytes()
    path.unlink()
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "source_missing"
    path.write_bytes(original + b" ")
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "source_integrity_failed"
    path.write_bytes(original)
    original_payload = json.loads(original)
    def replace_payload(value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.write_bytes(raw)
        db.update("analysis_artifacts", {"artifact_id": assertion.artifact_id},
                  {"payload_hash": payload_hash(value)})

    forged_unknown = deepcopy(original_payload)
    forged_unknown["dependency_fingerprint"] = "f" * 64
    replace_payload(forged_unknown)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "source_integrity_failed"
    replace_payload(original_payload)
    forged_sufficient = deepcopy(original_payload)
    measurements = {entry["name"]: entry for entry in forged_sufficient["evidence"][0]["measurements"]}
    measurements["usable_delta_count"]["count"] = 1
    measurements["entities_with_usable_observation"]["count"] = 2
    measurements["entities_with_three_or_more_observations"]["count"] = 2
    replace_payload(forged_sufficient)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "source_integrity_failed"
    replace_payload(original_payload)
    # A syntactically valid forged prerequisite assertion cannot become a
    # producer-owned unknown. Update its reference hash too, so this proves
    # the producer reconstruction rather than merely a stale pin.
    temporal_ref = next(ref for ref in original_payload["evidence"][0]["source_refs"]
                        if db.query_one("analysis_artifacts", artifact_id=ref["artifact_id"])["artifact_type"] == "dataset_structure_assertion"
                        and repo.get(ref["artifact_id"])[1]["predicate"] == "table.temporal/temporal_binding")
    temporal_row = db.query_one("analysis_artifacts", artifact_id=temporal_ref["artifact_id"])
    temporal_path = repo.root / temporal_row["payload_path"]
    temporal_original = temporal_path.read_bytes()
    temporal_forged = json.loads(temporal_original)
    temporal_forged["dependency_fingerprint"] = "e" * 64
    temporal_raw = json.dumps(temporal_forged, sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporal_path.write_bytes(temporal_raw)
    temporal_hash = hashlib.sha256(temporal_raw).hexdigest()
    db.update("analysis_artifacts", {"artifact_id": temporal_ref["artifact_id"]}, {"payload_hash": temporal_hash})
    forged_source = deepcopy(original_payload)
    next(ref for ref in forged_source["evidence"][0]["source_refs"] if ref["artifact_id"] == temporal_ref["artifact_id"])["payload_hash"] = temporal_hash
    replace_payload(forged_source)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "source_integrity_failed"
    temporal_path.write_bytes(temporal_original)
    db.update("analysis_artifacts", {"artifact_id": temporal_ref["artifact_id"]}, {"payload_hash": temporal_row["payload_hash"]})
    replace_payload(original_payload)
    too_many_refs = deepcopy(original_payload)
    too_many_refs["evidence"][0]["source_refs"] = [{
        "artifact_id": f"art_dependency_{index}", "role": "dependency", "payload_hash": "a" * 64,
    } for index in range(17)]
    replace_payload(too_many_refs)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "dependency_limit_exceeded"
    too_many_evidence = deepcopy(original_payload)
    too_many_evidence["evidence"] = [dict(original_payload["evidence"][0], evidence_id=f"dsce_bound_{index}")
                                      for index in range(9)]
    replace_payload(too_many_evidence)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "evidence_limit_exceeded"
    oversized = deepcopy(original_payload)
    oversized["evidence"][0]["source_refs"] = [{
        "artifact_id": "art_" + "x" * (256 * 1024), "role": "dependency", "payload_hash": "a" * 64,
    }]
    replace_payload(oversized)
    oversized_path = path
    original_open = Path.open
    def reject_oversized_open(candidate_path, *args, **kwargs):
        if candidate_path == oversized_path:
            raise AssertionError("oversized payload must be rejected from stat before open")
        return original_open(candidate_path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", reject_oversized_open)
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "payload_limit_exceeded"
    monkeypatch.setattr(Path, "open", original_open)
    replace_payload(original_payload)
    dependency_id = repo.get(assertion.artifact_id)[1]["evidence"][0]["source_refs"][0]["artifact_id"]
    db.update("analysis_artifacts", {"artifact_id": dependency_id}, {"status": "superseded"})
    assert project_materialized_dataset_structure(request, repository=repo)["reason"] == "dependency_changed"
    db.update("analysis_artifacts", {"artifact_id": assertion.artifact_id}, {"status": "superseded"})
    changed = project_materialized_dataset_structure(request, repository=repo)
    assert changed["outcome"] == "selected_pair_absent"
    assert changed["resolved_as_of"]["read_boundary_fingerprint"] != projected["resolved_as_of"]["read_boundary_fingerprint"]


def test_d06_shadow_projector_enforces_candidate_and_payload_bounds(aar, monkeypatch):
    """The fifth candidate and any oversized payload fail closed without truncation."""
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    frame = pd.DataFrame({"entity": ["a", "a", "b", "b"],
                          "observed_on": ["2024-01-01", "bad-date", "2024-01-01", "2024-02-01"]})
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": list(frame.columns), "col_count": 2, "row_count": len(frame)})
    for column, role in (("entity", "Identifier"), ("observed_on", "Date")):
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": table, "column_name": column,
            "classification": "categorical", "data_type": "string", "description": "",
            "discrepancies": [], "notes": "", "role": role, "role_reviewed": 1,
            "provisional": 0, "missing_value_codes_json": [], "missing_codes_confirmed": 0,
            "profile_json": {"total_count": 4, "non_null_count": 4, "null_count": 0,
                             "physical_null_count": 0, "regular_value_count": 4,
                             "cardinality": 2, "calculation_method": "exact", "top_k": {}},
            "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=[
        "table.structure/entity_binding", "table.temporal/temporal_binding"])
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table",
                        lambda _self, _snapshot, _table, columns=None, **_kwargs: frame.loc[:, columns].copy())
    created = observe_dataset_structure(repo, snapshot_id, tables=[table],
                                        predicates=["table.temporal/observed_cadence"])
    payload = repo.get(created[0].artifact.artifact_id)[1]
    request = {"asset_id": asset_id, "snapshot_id": snapshot_id, "table": table,
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:observed_on",
        "axis_column": {"table": table, "column": "observed_on"},
        "grouping": [{"table": table, "column": "entity"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    copies = [save_dataset_structure_assertion(repo, payload, version=True).artifact for _ in range(4)]
    # Candidate LIMIT 5 is checked from metadata before any candidate payload
    # is touched; a corrupt fifth row cannot replace ambiguity with integrity.
    fifth_path = repo.root / db.query_one("analysis_artifacts", artifact_id=copies[-1].artifact_id)["payload_path"]
    fifth_path.write_bytes(b"not-json")
    capped = project_materialized_dataset_structure(request, repository=repo)
    assert (capped["outcome"], capped["reason"]) == ("ambiguous", None)


def test_phase_cadence_batch_guard_leaves_two_candidates_unpublished(aar, monkeypatch):
    """A mutation after the one read but before batch commit has no partial output."""
    repo, asset_id, snapshot_id = aar
    table = "private_applications"
    frame = pd.DataFrame({"entity_a": ["a"] * 3 + ["b"] * 3,
                          "entity_b": ["x", "x", "x", "y", "y", "y"],
                          "observed_on": ["2024-01-01", "2024-02-01", "2024-03-01"] * 2})
    db.update("dq_item_tables", {"item_id": snapshot_id, "table_name": table}, {
        "columns": list(frame.columns), "col_count": 3, "row_count": len(frame)})
    for column, role in (("entity_a", "Identifier"), ("entity_b", "Identifier"), ("observed_on", "Date")):
        db.insert("variable_inventory", {
            "item_id": snapshot_id, "table_name": table, "column_name": column,
            "classification": "categorical", "data_type": "string", "description": "", "discrepancies": [],
            "notes": "", "role": role, "role_reviewed": 1, "provisional": 0,
            "missing_value_codes_json": [], "missing_codes_confirmed": 0,
            "profile_json": {"total_count": 6, "non_null_count": 6, "null_count": 0,
                             "physical_null_count": 0, "regular_value_count": 6, "cardinality": 2,
                             "calculation_method": "exact", "top_k": {}}, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=[
        "table.structure/entity_binding", "table.temporal/temporal_binding"])
    monkeypatch.setattr(dsc_producer.SnapshotLoader, "load_table",
                        lambda _self, _snapshot, _table, columns=None, **_kwargs: frame.loc[:, columns].copy())
    original = repo.save_batch
    def mutate_before_guard(entries, **kwargs):
        db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table, "column_name": "entity_a"},
                  {"role_reviewed": 0, "updated_at": db.now_ist()})
        return original(entries, **kwargs)
    monkeypatch.setattr(repo, "save_batch", mutate_before_guard)
    before = list(repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion"))
    before_files = {path.name for path in repo.root.glob("*.json")}
    with pytest.raises(dsc_producer.DatasetStructureObservationError) as exc:
        observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=["table.temporal/observed_cadence"])
    assert exc.value.reason_code == "DSC_R_SOURCE_INTEGRITY_FAILED"
    after = list(repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion"))
    assert [(item.artifact_id, item.status) for item in after] == [(item.artifact_id, item.status) for item in before]
    assert {path.name for path in repo.root.glob("*.json")} == before_files

    # Retry from a refreshed prerequisite projection produces both pairs; an
    # unchanged retry is local exact reuse and needs no new materialization.
    monkeypatch.setattr(repo, "save_batch", original)
    db.update("variable_inventory", {"item_id": snapshot_id, "table_name": table, "column_name": "entity_a"},
              {"role_reviewed": 1, "updated_at": db.now_ist()})
    persist_snapshot_profile_artifacts(snapshot_id, artifact_repository=repo)
    observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=[
        "table.structure/entity_binding", "table.temporal/temporal_binding"])
    retry = observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=["table.temporal/observed_cadence"])
    assert len(retry) == 2 and all(item.outcome == "created" for item in retry)
    exact = observe_dataset_structure(repo, snapshot_id, tables=[table], predicates=["table.temporal/observed_cadence"])
    assert len(exact) == 2 and all(item.outcome == "reused" for item in exact)
