from __future__ import annotations

from copy import deepcopy

import pytest

from analysis_runtime.dataset_structure_context import (
    DSCContractError,
    ERROR_AS_OF_UNSUPPORTED,
    ERROR_CONTEXT_VERSION_NO_MATCH,
    ERROR_CROSS_SNAPSHOT_UNSUPPORTED,
    ERROR_DUPLICATE_SELECTOR,
    ERROR_PROTOCOL_VERSION_UNSUPPORTED,
    ERROR_SELECTOR_SCOPE,
    ERROR_SNAPSHOT_MISMATCH,
    assemble_context,
    assemble_response,
    assertion_identity_key,
    canonical_payload_bytes,
    payload_hash,
    safe_assertion_summary,
    safe_context_summary,
    validate_assertion_payload,
    validate_context_payload,
    validate_resolution_request,
    validate_response_context,
)

HASH = "0" * 64


def assertion() -> dict:
    return {
        "schema_version": 1, "artifact_type": "dataset_structure_assertion",
        "assertion_id": "dsca_form", "context_version": "1",
        "snapshot": {"asset_id": "asset_one", "snapshot_id": "snapshot_one"},
        "subject": {"kind": "table", "table": "orders"},
        "predicate": "table.structure/dataset_form", "instance_key": "primary",
        "multiplicity": "single",
        "claims": [{"claim_id": "dscc_form", "authority": "source_confirmed_structural",
                    "value": {"kind": "dataset_form", "form": "event"},
                    "evidence_ids": ["dsce_meta"],
                    "decision": {"decision_id": "decision_form", "action": "confirm",
                                 "scope": "source_confirmed_structural"}}],
        "evidence": [{"evidence_id": "dsce_meta", "kind": "reviewed_metadata",
                      "source_refs": [{"artifact_id": "art_metadata", "role": "reviewed_metadata", "payload_hash": HASH}],
                      "basis": {"population": "orders", "total_count": 10,
                                "exclusions": {"physical_null": 1, "confirmed_special": 2, "parse_failure": 0},
                                "usable_count": 7, "computation": "exact"},
                      "measurements": [{"name": "rows", "count": 10}, {"name": "complete", "numerator": 7, "denominator": 10}],
                      "sensitivity": "internal"}],
        "conflicts": [],
        "resolution": {"status": "confirmed", "effective_claim_ids": ["dscc_form"], "reason_codes": [], "conflict_ids": []},
        "dependency_fingerprint": HASH, "sensitivity": "internal",
    }


def request() -> dict:
    return {"protocol_version": "1", "supported_context_versions": ["1"],
            "snapshot": {"asset_id": "asset_one", "snapshot_id": "snapshot_one"},
            "as_of": "latest", "tables": ["orders"], "consumer_id": "consumer_x",
            "selectors": [{"selector_id": "form", "subject": {"kind": "table", "table": "orders"},
                           "predicate": "table.structure/dataset_form", "requirement": "required",
                           "accepted_resolution_states": ["confirmed"]}]}


def results() -> list[dict]:
    return [{"selector_id": "form", "result": "fulfilled", "pins": [{
        "artifact_id": "art_assertion", "assertion_id": "dsca_form", "payload_hash": HASH,
        "resolution": "confirmed", "reuse_disposition": "fresh"}]}]


def test_valid_assertion_identity_arithmetic_and_safe_summary():
    value = assertion()
    validate_assertion_payload(value)
    assert assertion_identity_key(value) == assertion_identity_key(deepcopy(value))
    assert safe_assertion_summary(value) == {"predicate": "table.structure/dataset_form", "subject_kind": "table", "resolution_status": "confirmed", "claim_count": 1, "evidence_count": 1, "conflict_count": 0}


@pytest.mark.parametrize("mutate", [
    lambda p: p["evidence"][0]["basis"].update(usable_count=8),
    lambda p: p.update(multiplicity="keyed_set"),
    lambda p: p["claims"][0].update(algorithm={"name": "x", "version": "1"}, confidence=.5),
    lambda p: p.update(sensitivity="confidential"),
])
def test_assertion_rejects_semantic_boundaries(mutate):
    value = assertion(); mutate(value)
    with pytest.raises(DSCContractError): validate_assertion_payload(value)


def test_temporal_and_relationship_boundaries():
    value = assertion(); value.update(predicate="table.temporal/temporal_binding", multiplicity="keyed_set")
    value["claims"][0]["value"] = {"kind": "temporal_binding", "axis_id": "day", "temporal_type": "date", "columns": [{"table": "orders", "column": "ordered_on"}], "precision": "day", "calendar": "gregorian", "timezone": "UTC"}
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    value["claims"][0]["value"].pop("timezone")
    validate_assertion_payload(value)
    value["claims"][0]["value"]["temporal_type"] = "interval"
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    relationship = assertion(); relationship.update(
        subject={"kind": "relationship", "from_table": "orders", "to_table": "customers"},
        predicate="relationship.declared/relationship", multiplicity="keyed_set")
    relationship["claims"][0]["value"] = {"kind": "relationship", "key_pairs": [{"from": {"table": "orders", "column": "customer_id"}, "to": {"table": "customers", "column": "id"}}], "direction": "from_references_to", "cardinality": "many_to_one", "null_policy": "nulls_never_match", "declaration_basis": "source_constraint"}
    validate_assertion_payload(relationship)
    relationship["claims"][0]["authority"] = "deterministic_inference"
    relationship["claims"][0]["algorithm"] = {"name": "x", "version": "1"}
    relationship["claims"][0]["confidence"] = 1.0
    with pytest.raises(DSCContractError): validate_assertion_payload(relationship)


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda r: r.update(protocol_version="2"), ERROR_PROTOCOL_VERSION_UNSUPPORTED),
    (lambda r: r.update(supported_context_versions=["2"]), ERROR_CONTEXT_VERSION_NO_MATCH),
    (lambda r: r.update(as_of="2020-01-01"), ERROR_AS_OF_UNSUPPORTED),
    (lambda r: r.update(comparison_snapshot_id="snapshot_old"), ERROR_CROSS_SNAPSHOT_UNSUPPORTED),
    (lambda r: r["selectors"].append(deepcopy(r["selectors"][0])), ERROR_DUPLICATE_SELECTOR),
])
def test_request_error_matrix(mutate, code):
    value = request(); mutate(value)
    with pytest.raises(DSCContractError) as exc: validate_resolution_request(value)
    assert exc.value.code == code


def test_request_portability_scope_and_defensive_copy():
    value = request(); normalized = validate_resolution_request(value, {"asset_id": "asset_one", "snapshot_id": "snapshot_one", "tables": ["orders"]})
    value["tables"][0] = "changed"
    assert normalized["tables"] == ["orders"]
    with pytest.raises(DSCContractError) as exc:
        validate_resolution_request(request(), {"asset_id": "asset_one", "snapshot_id": "other"})
    assert exc.value.code == ERROR_SNAPSHOT_MISMATCH
    scoped = request(); scoped["selectors"][0]["subject"]["table"] = "payments"
    with pytest.raises(DSCContractError) as exc: validate_resolution_request(scoped)
    assert exc.value.code == ERROR_SELECTOR_SCOPE


def test_context_fulfillment_response_and_privacy():
    context = assemble_context(request(), results(), resolved_as_of="2026-01-02T03:04:05Z", sensitivity="internal")
    validate_context_payload(context)
    assert context["overall_result"] == "fulfilled"
    assert context["selector_results"][0]["selector"] == request()["selectors"][0]
    assert safe_context_summary(context) == {"overall_result": "fulfilled", "selector_count": 1, "fulfilled_selector_count": 1}
    response = assemble_response({"artifact_id": "art_context", "payload_hash": payload_hash(context)}, context)
    validate_response_context(response, context)
    response["selector_results"][0]["pins"][0]["reuse_disposition"] = "exact_reused"
    with pytest.raises(DSCContractError): validate_response_context(response, context)
    fingerprint_mismatch = deepcopy(context)
    fingerprint_mismatch["supported_context_versions"] = ["1", "2"]
    with pytest.raises(DSCContractError, match="request_fingerprint"):
        validate_context_payload(fingerprint_mismatch)


def test_required_failure_and_canonicalization_rejection():
    unavailable = [{"selector_id": "form", "result": "unavailable", "reason_codes": ["DSC_R_NO_EVIDENCE"]}]
    context = assemble_context(request(), unavailable, resolved_as_of="2026-01-02T03:04:05+00:00", sensitivity="external_safe")
    assert context["overall_result"] == "unfulfilled"
    assert canonical_payload_bytes({"b": "e\u0301", "a": 1}) == canonical_payload_bytes({"a": 1, "b": "é"})
    with pytest.raises(TypeError): canonical_payload_bytes({1: "not-a-string-key"})
    with pytest.raises(TypeError): canonical_payload_bytes(float("nan"))


def test_persisted_payloads_reject_nfd_strings_and_keys():
    value = assertion(); value["subject"]["table"] = "cafe\u0301"
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    value = assertion()
    value["evidence"][0]["basis"] = {
        "population": "orders", "total_count": 10,
        "exclusions": {"physical_null": 1, "confirmed_special": 2, "parse_failure": 0},
        "usable_count": 7, "computation": "exact", "note\u0301": "forbidden",
    }
    with pytest.raises(DSCContractError): validate_assertion_payload(value)


def test_empty_assertion_evidence_is_external_safe_only():
    value = assertion()
    value["evidence"] = []
    value["claims"][0]["evidence_ids"] = []
    value["sensitivity"] = "external_safe"
    validate_assertion_payload(value)
    value["sensitivity"] = "internal"
    with pytest.raises(DSCContractError, match="maximum evidence"):
        validate_assertion_payload(value)


@pytest.mark.parametrize(("status", "effective", "conflicts"), [
    ("observed", [], []), ("proposed", [], []), ("confirmed", [], []),
    ("unknown", ["dscc_form"], []), ("not_applicable", ["dscc_form"], []),
    ("conflict", [], []),
])
def test_resolution_status_matrix_rejects_invalid_effective_claims(status, effective, conflicts):
    value = assertion()
    value["resolution"].update(status=status, effective_claim_ids=effective, conflict_ids=conflicts)
    with pytest.raises(DSCContractError): validate_assertion_payload(value)


def test_not_applicable_and_decision_portability_contracts():
    value = assertion()
    value["resolution"].update(status="not_applicable", effective_claim_ids=[],
                                reason_codes=["DSC_R_NOT_APPLICABLE_CONFIRMED"])
    validate_assertion_payload(value)
    value["resolution"]["reason_codes"] = []
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    value = assertion()
    value["claims"][0]["authority"] = "consumer_local_decision"
    value["claims"][0]["decision"] = {"decision_id": "decision_local", "action": "confirm",
                                        "scope": "consumer_local", "owner_id": "consumer_x"}
    validate_assertion_payload(value)
    value["claims"][0]["decision"].pop("owner_id")
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    value = assertion()
    value["claims"].append({"claim_id": "dscc_promoted", "authority": "source_confirmed_structural",
        "value": {"kind": "dataset_form", "form": "event"}, "evidence_ids": ["dsce_meta"],
        "decision": {"decision_id": "decision_promoted", "action": "promote",
                     "scope": "source_confirmed_structural", "promotion_of": "decision_form"}})
    validate_assertion_payload(value)
    value["claims"].reverse()
    validate_assertion_payload(value)
    value["claims"].reverse()
    value["claims"][0]["decision"]["promotion_of"] = "decision_promoted"
    with pytest.raises(DSCContractError): validate_assertion_payload(value)


def test_entity_binding_requires_columns_and_unknown_facets_are_unsupported():
    value = assertion(); value.update(predicate="table.structure/entity_binding", multiplicity="keyed_set")
    value["claims"][0]["value"] = {"kind": "entity_binding", "columns": []}
    with pytest.raises(DSCContractError): validate_assertion_payload(value)
    unknown_request = request()
    unknown_request["selectors"][0]["predicate"] = "table.future/experimental_facet"
    unsupported = [{"selector_id": "form", "result": "unsupported",
                    "reason_codes": ["DSC_R_UNSUPPORTED_FACET"]}]
    context = assemble_context(unknown_request, unsupported,
                               resolved_as_of="2026-01-02T03:04:05Z", sensitivity="internal")
    assert context["overall_result"] == "unfulfilled"
    with pytest.raises(DSCContractError):
        assemble_context(unknown_request, results(), resolved_as_of="2026-01-02T03:04:05Z", sensitivity="internal")
