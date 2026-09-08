"""Dataset Structure Context (DSC) v1 portable runtime contracts.

This module deliberately owns validation and assembly only.  Persistence,
authorization, observation, and diagnostic consumers remain outside DSC v1.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


SCHEMA_VERSION = 1
PROTOCOL_VERSION = "1"
CONTEXT_VERSION = "1"
ASSERTION_ARTIFACT_TYPE = "dataset_structure_assertion"
CONTEXT_ARTIFACT_TYPE = "dataset_structure_context"

PREDICATES = frozenset({
    "table.physical/schema_column", "table.structure/dataset_form",
    "table.structure/entity_binding", "table.structure/row_grain",
    "table.temporal/temporal_binding", "table.temporal/observed_cadence",
    "relationship.declared/relationship",
})
RESOLUTION_STATES = frozenset({"observed", "proposed", "confirmed", "conflict", "unknown", "not_applicable"})
SENSITIVITIES = ("external_safe", "internal", "confidential")
REASON_CODES = frozenset({
    "DSC_R_NO_EVIDENCE", "DSC_R_AMBIGUOUS_CANDIDATES", "DSC_R_INSUFFICIENT_BASIS",
    "DSC_R_STATE_NOT_ACCEPTED", "DSC_R_NOT_APPLICABLE_CONFIRMED", "DSC_R_DECISION_CLEARED",
    "DSC_R_RELATIONSHIP_UNDECLARED", "DSC_R_CONFLICT_DECLARED_OBSERVED",
    "DSC_R_CONFLICT_COMPETING_CONFIRMATIONS", "DSC_R_CONFLICT_RELATIONSHIP_ENDPOINT",
    "DSC_R_CONFLICT_TEMPORAL_INTERPRETATION", "DSC_R_DEPENDENCY_CHANGED",
    "DSC_R_SOURCE_MISSING", "DSC_R_SOURCE_INTEGRITY_FAILED", "DSC_R_EVIDENCE_FORBIDDEN",
    "DSC_R_UNSUPPORTED_FACET", "DSC_R_OPTIONAL_NOT_MATERIALIZED",
})
ERROR_INVALID_REQUEST = "DSC_E_INVALID_REQUEST"
ERROR_PROTOCOL_VERSION_UNSUPPORTED = "DSC_E_PROTOCOL_VERSION_UNSUPPORTED"
ERROR_CONTEXT_VERSION_NO_MATCH = "DSC_E_CONTEXT_VERSION_NO_MATCH"
ERROR_SELECTOR_SCOPE = "DSC_E_SELECTOR_SCOPE"
ERROR_DUPLICATE_SELECTOR = "DSC_E_DUPLICATE_SELECTOR"
ERROR_SNAPSHOT_MISMATCH = "DSC_E_SNAPSHOT_MISMATCH"
ERROR_CROSS_SNAPSHOT_UNSUPPORTED = "DSC_E_CROSS_SNAPSHOT_UNSUPPORTED"
ERROR_AS_OF_UNSUPPORTED = "DSC_E_AS_OF_UNSUPPORTED"
ERROR_TENANT_SCOPE = "DSC_E_TENANT_SCOPE"
ERROR_CODES = frozenset({ERROR_INVALID_REQUEST, ERROR_PROTOCOL_VERSION_UNSUPPORTED,
    ERROR_CONTEXT_VERSION_NO_MATCH, ERROR_SELECTOR_SCOPE, ERROR_DUPLICATE_SELECTOR,
    ERROR_SNAPSHOT_MISMATCH, ERROR_CROSS_SNAPSHOT_UNSUPPORTED, ERROR_AS_OF_UNSUPPORTED,
    ERROR_TENANT_SCOPE})
# Protocol-name aliases make the stable contract codes straightforward to
# import without tying callers to the implementation-oriented ERROR_* names.
DSC_SCHEMA_VERSION = SCHEMA_VERSION
DSC_PROTOCOL_VERSION = PROTOCOL_VERSION
DSC_CONTEXT_VERSION = CONTEXT_VERSION
DSC_E_INVALID_REQUEST = ERROR_INVALID_REQUEST
DSC_E_PROTOCOL_VERSION_UNSUPPORTED = ERROR_PROTOCOL_VERSION_UNSUPPORTED
DSC_E_CONTEXT_VERSION_NO_MATCH = ERROR_CONTEXT_VERSION_NO_MATCH
DSC_E_SELECTOR_SCOPE = ERROR_SELECTOR_SCOPE
DSC_E_DUPLICATE_SELECTOR = ERROR_DUPLICATE_SELECTOR
DSC_E_SNAPSHOT_MISMATCH = ERROR_SNAPSHOT_MISMATCH
DSC_E_CROSS_SNAPSHOT_UNSUPPORTED = ERROR_CROSS_SNAPSHOT_UNSUPPORTED
DSC_E_AS_OF_UNSUPPORTED = ERROR_AS_OF_UNSUPPORTED
DSC_E_TENANT_SCOPE = ERROR_TENANT_SCOPE
for _reason_code in REASON_CODES:
    globals()[_reason_code] = _reason_code

_ID = {"artifact": r"art_[A-Za-z0-9_-]+", "assertion": r"dsca_[A-Za-z0-9_-]+",
       "claim": r"dscc_[A-Za-z0-9_-]+", "evidence": r"dsce_[A-Za-z0-9_-]+",
       "conflict": r"dscx_[A-Za-z0-9_-]+"}
_CONFLICT_REASON = {"declared_observed": "DSC_R_CONFLICT_DECLARED_OBSERVED",
    "competing_confirmations": "DSC_R_CONFLICT_COMPETING_CONFIRMATIONS",
    "relationship_endpoint": "DSC_R_CONFLICT_RELATIONSHIP_ENDPOINT",
    "temporal_interpretation": "DSC_R_CONFLICT_TEMPORAL_INTERPRETATION"}


class DSCContractError(ValueError):
    """A DSC validation error with a stable, machine-readable code."""
    def __init__(self, message: str, code: str = ERROR_INVALID_REQUEST):
        self.code = code
        super().__init__(message)


def _fail(message: str, code: str = ERROR_INVALID_REQUEST) -> None:
    raise DSCContractError(message, code)


def _json_native(value: Any) -> Any:
    """Copy JSON-native data while enforcing DSC canonical JSON constraints."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("DSC canonical JSON rejects non-finite numbers")
        return value
    if isinstance(value, list):
        return [_json_native(item) for item in value]
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("DSC canonical JSON object keys must be strings")
            key = unicodedata.normalize("NFC", key)
            if key in copied:
                raise TypeError("DSC canonical JSON rejects duplicate normalized keys")
            copied[key] = _json_native(item)
        return copied
    raise TypeError("DSC canonical JSON accepts only JSON-native values")


def canonical_payload_bytes(value: Any) -> bytes:
    """Return compact, sorted, NFC-normalized UTF-8 bytes for JSON-native data."""
    return json.dumps(_json_native(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_persisted_canonical_payload(value: Any) -> None:
    """Require DSC payloads to hash exactly as the generic AAR serializes them."""
    try:
        _json_native(value)
        aar_bytes = json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DSCContractError(str(exc)) from exc
    if canonical_payload_bytes(value) != aar_bytes:
        _fail("DSC persisted payload must already be NFC-normalized")


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_payload_bytes(value)).hexdigest()


def request_fingerprint(request: Any) -> str:
    return payload_hash(request)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict): _fail(f"{label} must be an object")
    return value


def _closed(value: Any, required: set[str], allowed: set[str], label: str) -> dict[str, Any]:
    obj = _mapping(value, label)
    if set(obj) - allowed or required - set(obj): _fail(f"{label} has an invalid closed shape")
    return obj


def _string(value: Any, label: str, *, max_len: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len: _fail(f"{label} must be a non-empty string")
    return value


def _id(value: Any, family: str, label: str) -> str:
    value = _string(value, label, max_len=256)
    if not re.fullmatch(_ID[family], value): _fail(f"{label} has an invalid identifier")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", value): _fail(f"{label} must be SHA-256")
    return value


def _unique(items: list[Any], label: str) -> None:
    if len({payload_hash(x) for x in items}) != len(items): _fail(f"{label} must be unique")


def _snapshot(value: Any, label: str = "snapshot") -> dict[str, Any]:
    obj = _closed(value, {"asset_id", "snapshot_id"}, {"asset_id", "snapshot_id"}, label)
    _string(obj["asset_id"], f"{label}.asset_id", max_len=256); _string(obj["snapshot_id"], f"{label}.snapshot_id", max_len=256)
    return obj


def _subject(value: Any) -> dict[str, Any]:
    obj = _mapping(value, "subject")
    if obj.get("kind") == "table":
        _closed(obj, {"kind", "table"}, {"kind", "table"}, "subject"); _string(obj["table"], "subject.table")
    elif obj.get("kind") == "relationship":
        _closed(obj, {"kind", "from_table", "to_table"}, {"kind", "from_table", "to_table"}, "subject")
        _string(obj["from_table"], "subject.from_table"); _string(obj["to_table"], "subject.to_table")
    else: _fail("subject.kind is invalid")
    return obj


def _column(value: Any, label: str = "column") -> dict[str, Any]:
    obj = _closed(value, {"table", "column"}, {"table", "column"}, label)
    _string(obj["table"], f"{label}.table"); _string(obj["column"], f"{label}.column")
    return obj


def _columns(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) < minimum or (maximum is not None and len(value) > maximum): _fail(f"{label} has invalid cardinality")
    for col in value: _column(col, label)
    _unique(value, label)
    return value


def _value_for(predicate: str, subject: dict[str, Any], value: Any) -> None:
    obj = _mapping(value, "claim.value"); kind = obj.get("kind")
    expected = {"table.physical/schema_column": "schema_column", "table.structure/dataset_form": "dataset_form",
        "table.structure/entity_binding": "entity_binding", "table.structure/row_grain": "row_grain",
        "table.temporal/temporal_binding": "temporal_binding", "table.temporal/observed_cadence": "observed_cadence",
        "relationship.declared/relationship": "relationship"}[predicate]
    if kind != expected: _fail("predicate and claim value kind do not match")
    if predicate == "table.physical/schema_column":
        _closed(obj, {"kind", "column", "data_type", "nullable"}, {"kind", "column", "data_type", "nullable"}, "schema_column")
        col = _column(obj["column"]); _string(obj["data_type"], "data_type")
        if not isinstance(obj["nullable"], bool) or col["table"] != subject["table"]: _fail("schema column is invalid or out of subject scope")
    elif kind == "dataset_form":
        _closed(obj, {"kind", "form"}, {"kind", "form"}, "dataset_form")
        if obj["form"] not in {"cross_sectional", "panel", "event", "interval", "repeated_cross_section", "unclassified"}: _fail("invalid dataset form")
    elif kind in {"entity_binding", "row_grain"}:
        key = "columns" if kind == "entity_binding" else "key_columns"; _closed(obj, {"kind", key}, {"kind", key}, kind)
        cols = _columns(obj[key], key, minimum=1)
        if any(c["table"] != subject["table"] for c in cols): _fail("qualified column is out of subject scope")
    elif kind == "temporal_binding":
        allowed = {"kind", "axis_id", "temporal_type", "columns", "precision", "timezone", "calendar", "grouping"}
        _closed(obj, {"kind", "axis_id", "temporal_type", "columns", "precision", "calendar"}, allowed, kind)
        _string(obj["axis_id"], "axis_id"); typ = obj["temporal_type"]
        if typ not in {"date", "instant", "interval"} or obj["precision"] not in {"year", "quarter", "month", "day", "hour", "minute", "second", "millisecond", "microsecond", "unknown"} or obj["calendar"] not in {"gregorian", "other_declared", "unknown"}: _fail("invalid temporal binding")
        if typ == "date" and "timezone" in obj: _fail("date temporal binding cannot have timezone")
        if "timezone" in obj: _string(obj["timezone"], "timezone")
        cols = _columns(obj["columns"], "temporal columns", minimum=1, maximum=2)
        if len(cols) != (2 if typ == "interval" else 1): _fail("temporal type requires exact column count")
        if any(c["table"] != subject["table"] for c in cols): _fail("temporal column is out of subject scope")
        if "grouping" in obj and any(c["table"] != subject["table"] for c in _columns(obj["grouping"], "grouping")): _fail("grouping column is out of subject scope")
    elif kind == "observed_cadence":
        _closed(obj, {"kind", "axis_id", "grouping", "cadence"}, {"kind", "axis_id", "grouping", "cadence", "observed_interval_class"}, kind)
        _string(obj["axis_id"], "axis_id"); _columns(obj["grouping"], "grouping")
        if obj["cadence"] not in {"regular", "mixed", "irregular", "unknown"}: _fail("invalid cadence")
        if "observed_interval_class" in obj: _string(obj["observed_interval_class"], "observed_interval_class")
        if any(c["table"] != subject["table"] for c in obj["grouping"]): _fail("grouping column is out of subject scope")
    else:
        _closed(obj, {"kind", "key_pairs", "direction", "cardinality", "null_policy", "declaration_basis"}, {"kind", "key_pairs", "direction", "cardinality", "null_policy", "declaration_basis"}, kind)
        if not isinstance(obj["key_pairs"], list) or not obj["key_pairs"]: _fail("relationship requires key pairs")
        for pair in obj["key_pairs"]:
            pair = _closed(pair, {"from", "to"}, {"from", "to"}, "relationship key pair")
            if _column(pair["from"])["table"] != subject["from_table"] or _column(pair["to"])["table"] != subject["to_table"]: _fail("relationship endpoints do not match subject")
        if obj["direction"] not in {"from_references_to", "to_references_from", "undirected"} or obj["cardinality"] not in {"one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"} or obj["null_policy"] not in {"nulls_never_match", "nulls_equal"} or obj["declaration_basis"] not in {"source_constraint", "reviewed_declaration", "promoted_structural_decision"}: _fail("invalid relationship value")


def validate_assertion_payload(payload: Any) -> None:
    _validate_persisted_canonical_payload(payload)
    obj = _closed(payload, {"schema_version", "artifact_type", "assertion_id", "context_version", "snapshot", "subject", "predicate", "instance_key", "multiplicity", "claims", "evidence", "conflicts", "resolution", "dependency_fingerprint", "sensitivity"}, {"schema_version", "artifact_type", "assertion_id", "context_version", "snapshot", "subject", "predicate", "instance_key", "multiplicity", "claims", "evidence", "conflicts", "resolution", "dependency_fingerprint", "sensitivity"}, "assertion")
    if obj["schema_version"] != SCHEMA_VERSION or obj["artifact_type"] != ASSERTION_ARTIFACT_TYPE or obj["context_version"] != CONTEXT_VERSION: _fail("unsupported assertion version")
    _id(obj["assertion_id"], "assertion", "assertion_id"); _snapshot(obj["snapshot"]); subject = _subject(obj["subject"])
    if obj["predicate"] not in PREDICATES or obj["multiplicity"] not in {"single", "keyed_set"}: _fail("invalid predicate or multiplicity")
    expected_multiplicity = {
        "table.physical/schema_column": "keyed_set", "table.structure/dataset_form": "single",
        "table.structure/entity_binding": "keyed_set", "table.structure/row_grain": "single",
        "table.temporal/temporal_binding": "keyed_set", "table.temporal/observed_cadence": "keyed_set",
        "relationship.declared/relationship": "keyed_set",
    }[obj["predicate"]]
    if obj["multiplicity"] != expected_multiplicity: _fail("predicate has incorrect registry multiplicity")
    if (subject["kind"] == "relationship") != obj["predicate"].startswith("relationship."): _fail("predicate and subject kind do not match")
    _string(obj["instance_key"], "instance_key"); _sha(obj["dependency_fingerprint"], "dependency_fingerprint")
    if obj["sensitivity"] not in SENSITIVITIES: _fail("invalid sensitivity")
    if not isinstance(obj["evidence"], list) or not isinstance(obj["claims"], list) or not isinstance(obj["conflicts"], list): _fail("assertion collections must be arrays")
    evidence_ids: set[str] = set(); max_sensitivity = -1
    for evidence in obj["evidence"]:
        ev = _closed(evidence, {"evidence_id", "kind", "source_refs", "basis", "measurements", "sensitivity"}, {"evidence_id", "kind", "source_refs", "basis", "measurements", "sensitivity"}, "evidence")
        eid = _id(ev["evidence_id"], "evidence", "evidence_id")
        if eid in evidence_ids: _fail("duplicate evidence_id")
        evidence_ids.add(eid)
        if ev["kind"] not in {"physical_profile", "reviewed_metadata", "source_constraint", "structural_decision", "bounded_scan", "deterministic_inference"} or ev["sensitivity"] not in SENSITIVITIES: _fail("invalid evidence")
        max_sensitivity = max(max_sensitivity, SENSITIVITIES.index(ev["sensitivity"]))
        if not isinstance(ev["source_refs"], list) or not ev["source_refs"]: _fail("evidence source_refs required")
        _unique(ev["source_refs"], "source_refs")
        for ref in ev["source_refs"]:
            ref = _closed(ref, {"artifact_id", "role", "payload_hash"}, {"artifact_id", "role", "payload_hash"}, "source_ref")
            _id(ref["artifact_id"], "artifact", "source_ref.artifact_id"); _sha(ref["payload_hash"], "source_ref.payload_hash")
            if ref["role"] not in {"profile", "reviewed_metadata", "source_constraint", "decision", "scan", "dependency"}: _fail("invalid source role")
        basis = _closed(ev["basis"], {"population", "total_count", "exclusions", "usable_count", "computation"}, {"population", "total_count", "exclusions", "usable_count", "computation"}, "basis")
        _string(basis["population"], "basis.population", max_len=256)
        if not all(isinstance(basis[x], int) and not isinstance(basis[x], bool) and basis[x] >= 0 for x in ("total_count", "usable_count")): _fail("basis counts invalid")
        excl = _closed(basis["exclusions"], {"physical_null", "confirmed_special", "parse_failure"}, {"physical_null", "confirmed_special", "parse_failure"}, "exclusions")
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in excl.values()) or sum(excl.values()) + basis["usable_count"] != basis["total_count"]: _fail("evidence basis arithmetic invalid")
        if basis["computation"] not in {"exact", "bounded_scan", "sampled"} or not isinstance(ev["measurements"], list): _fail("invalid evidence measurements")
        _unique(ev["measurements"], "measurements")
        for measurement in ev["measurements"]:
            if not isinstance(measurement, dict) or set(measurement) not in ({"name", "count"}, {"name", "numerator", "denominator"}): _fail("invalid measurement shape")
            _string(measurement.get("name"), "measurement.name")
            keys = set(measurement)
            if keys == {"name", "count"} and (not isinstance(measurement["count"], int) or isinstance(measurement["count"], bool) or measurement["count"] < 0): _fail("invalid measurement count")
            if keys != {"name", "count"} and (not all(isinstance(measurement[x], int) and not isinstance(measurement[x], bool) for x in ("numerator", "denominator")) or measurement["numerator"] < 0 or measurement["denominator"] <= 0 or measurement["numerator"] > measurement["denominator"]): _fail("invalid ratio measurement")
    expected_sensitivity = SENSITIVITIES[max(max_sensitivity, 0)]
    if obj["sensitivity"] != expected_sensitivity:
        _fail("assertion sensitivity must equal maximum evidence sensitivity")
    claim_ids: set[str] = set(); claims: dict[str, dict[str, Any]] = {}
    decision_ids: set[str] = set()
    for claim in obj["claims"]:
        cl = _closed(claim, {"claim_id", "authority", "value", "evidence_ids"}, {"claim_id", "authority", "value", "evidence_ids", "decision", "algorithm", "confidence"}, "claim")
        cid = _id(cl["claim_id"], "claim", "claim_id")
        if cid in claim_ids: _fail("duplicate claim_id")
        claim_ids.add(cid); claims[cid] = cl
        if cl["authority"] not in {"verified_observation", "source_reviewed_metadata", "source_confirmed_structural", "consumer_local_decision", "deterministic_inference"}: _fail("invalid claim authority")
        if not isinstance(cl["evidence_ids"], list) or len(set(cl["evidence_ids"])) != len(cl["evidence_ids"]) or not set(cl["evidence_ids"]) <= evidence_ids: _fail("claim evidence references invalid")
        _value_for(obj["predicate"], subject, cl["value"])
        deterministic = cl["authority"] == "deterministic_inference"
        if deterministic != ("algorithm" in cl and "confidence" in cl): _fail("algorithm and confidence are reserved for deterministic inference")
        if deterministic:
            alg = _closed(cl["algorithm"], {"name", "version"}, {"name", "version"}, "algorithm"); _string(alg["name"], "algorithm.name"); _string(alg["version"], "algorithm.version")
            if not isinstance(cl["confidence"], (int, float)) or isinstance(cl["confidence"], bool) or not 0 <= cl["confidence"] <= 1 or not math.isfinite(cl["confidence"]): _fail("invalid inference confidence")
        decision = cl.get("decision")
        if cl["authority"] in {"consumer_local_decision", "source_confirmed_structural"} and decision is None:
            _fail("claim authority requires its matching decision")
        if decision is not None:
            dec = _closed(cl["decision"], {"decision_id", "action", "scope"}, {"decision_id", "action", "scope", "owner_id", "promotion_of"}, "decision")
            decision_id = _string(dec["decision_id"], "decision_id", max_len=256)
            if decision_id in decision_ids: _fail("duplicate decision_id")
            if dec["action"] not in {"confirm", "replace", "clear", "mark_not_applicable", "promote"} or dec["scope"] not in {"source_confirmed_structural", "consumer_local"}: _fail("invalid decision")
            if dec["scope"] == "consumer_local" and "owner_id" not in dec: _fail("consumer-local decision needs owner_id")
            if "owner_id" in dec: _string(dec["owner_id"], "owner_id", max_len=256)
            if cl["authority"] == "consumer_local_decision" and dec["scope"] != "consumer_local": _fail("consumer-local authority requires consumer-local decision")
            if cl["authority"] == "source_confirmed_structural" and dec["scope"] != "source_confirmed_structural": _fail("source-confirmed authority requires source-confirmed decision")
            if cl["authority"] not in {"consumer_local_decision", "source_confirmed_structural"} and dec["scope"] == "consumer_local": _fail("only consumer-local authority may carry a consumer-local decision")
            if dec["action"] == "promote":
                promotion_of = _string(dec.get("promotion_of"), "promotion_of", max_len=256)
                if (dec["scope"] != "source_confirmed_structural"
                        or cl["authority"] != "source_confirmed_structural"
                        or promotion_of == decision_id):
                    _fail("promotion must reference a non-self source-confirmed decision")
            elif "promotion_of" in dec:
                _fail("only promotion decisions may carry promotion_of")
            decision_ids.add(decision_id)
    conflict_ids: set[str] = set()
    for conflict in obj["conflicts"]:
        co = _closed(conflict, {"conflict_id", "kind", "reason_code", "claim_ids", "evidence_ids"}, {"conflict_id", "kind", "reason_code", "claim_ids", "evidence_ids"}, "conflict")
        xid = _id(co["conflict_id"], "conflict", "conflict_id")
        if xid in conflict_ids: _fail("duplicate conflict_id")
        conflict_ids.add(xid)
        if co["kind"] not in _CONFLICT_REASON or co["reason_code"] != _CONFLICT_REASON[co["kind"]] or not isinstance(co["claim_ids"], list) or not isinstance(co["evidence_ids"], list) or len(co["claim_ids"]) < 2 or len(co["evidence_ids"]) < 2 or len(set(co["claim_ids"])) != len(co["claim_ids"]) or len(set(co["evidence_ids"])) != len(co["evidence_ids"]) or not set(co["claim_ids"]) <= claim_ids or not set(co["evidence_ids"]) <= evidence_ids: _fail("invalid conflict references")
    res = _closed(obj["resolution"], {"status", "effective_claim_ids", "reason_codes", "conflict_ids"}, {"status", "effective_claim_ids", "reason_codes", "conflict_ids"}, "resolution")
    if res["status"] not in RESOLUTION_STATES or not isinstance(res["effective_claim_ids"], list) or not isinstance(res["reason_codes"], list) or not isinstance(res["conflict_ids"], list) or len(set(res["effective_claim_ids"])) != len(res["effective_claim_ids"]) or len(set(res["reason_codes"])) != len(res["reason_codes"]) or len(set(res["conflict_ids"])) != len(res["conflict_ids"]) or not set(res["effective_claim_ids"]) <= claim_ids or not set(res["conflict_ids"]) <= conflict_ids or not set(res["reason_codes"]) <= REASON_CODES: _fail("invalid resolution")
    if obj["multiplicity"] == "single" and len(res["effective_claim_ids"]) > 1: _fail("single assertion has too many effective claims")
    if res["status"] in {"observed", "proposed", "confirmed"} and len(res["effective_claim_ids"]) != 1: _fail("resolved assertion status requires exactly one effective claim")
    if res["status"] in {"unknown", "not_applicable", "conflict"} and res["effective_claim_ids"]: _fail("unresolved assertion status cannot have effective claims")
    if res["status"] == "conflict" and not res["conflict_ids"]: _fail("conflict status requires conflict IDs")
    if res["status"] != "conflict" and res["conflict_ids"]: _fail("non-conflict status cannot cite conflicts")
    if obj["predicate"] == "relationship.declared/relationship":
        for cid in res["effective_claim_ids"]:
            claim = claims[cid]
            permitted_authority = claim["authority"] in {"source_reviewed_metadata", "source_confirmed_structural"}
            promoted = (claim.get("decision") or {}).get("action") == "promote"
            if not (permitted_authority or promoted):
                _fail("effective relationship cannot be created by inference or observation")
            if claim["value"]["declaration_basis"] == "promoted_structural_decision" and not promoted:
                _fail("promoted relationship basis needs a promotion decision")
    if res["status"] == "not_applicable":
        reviewed_or_decided = (any(e["kind"] in {"reviewed_metadata", "structural_decision"} for e in obj["evidence"])
                               or any((c.get("decision") or {}).get("action") == "mark_not_applicable" for c in claims.values()))
        if "DSC_R_NOT_APPLICABLE_CONFIRMED" not in res["reason_codes"] or not reviewed_or_decided:
            _fail("not_applicable needs confirmation reason and affirmative reviewed evidence or decision")


def assertion_identity_key(payload: Any) -> str:
    validate_assertion_payload(payload)
    obj = _json_native(payload)
    return canonical_payload_bytes({"snapshot": obj["snapshot"], "subject": obj["subject"], "predicate": obj["predicate"], "instance_key": obj["instance_key"]}).decode("utf-8")


def _snapshot_parts(snapshot: Any) -> tuple[str, str, set[str] | None]:
    if is_dataclass(snapshot): snapshot = asdict(snapshot)
    if not isinstance(snapshot, Mapping): _fail("snapshot must be SnapshotRef or mapping", ERROR_SNAPSHOT_MISMATCH)
    try: asset, sid = snapshot["asset_id"], snapshot["snapshot_id"]
    except KeyError: _fail("snapshot identity missing", ERROR_SNAPSHOT_MISMATCH)
    tables = snapshot.get("tables")
    return asset, sid, set(tables) if tables is not None else None


def _is_future_predicate(predicate: Any) -> bool:
    return (isinstance(predicate, str) and bool(re.fullmatch(
        r"[a-z][a-z0-9_]*(?:[./][a-z][a-z0-9_]*)+", predicate)))


def _validate_selector(selector: Any, selected_tables: list[str] | None = None) -> dict[str, Any]:
    se = _closed(selector, {"selector_id", "subject", "predicate", "requirement", "accepted_resolution_states"}, {"selector_id", "subject", "predicate", "requirement", "accepted_resolution_states"}, "selector")
    _string(se["selector_id"], "selector_id", max_len=256)
    sub = _subject(se["subject"])
    predicate = se["predicate"]
    if predicate in PREDICATES:
        if (sub["kind"] == "relationship") != predicate.startswith("relationship."):
            _fail("selector predicate and subject mismatch")
    elif not _is_future_predicate(predicate):
        _fail("selector predicate is invalid")
    if se["requirement"] not in {"required", "advisory", "optional"} or not isinstance(se["accepted_resolution_states"], list) or not se["accepted_resolution_states"] or len(set(se["accepted_resolution_states"])) != len(se["accepted_resolution_states"]) or not set(se["accepted_resolution_states"]) <= RESOLUTION_STATES:
        _fail("invalid selector requirements")
    if selected_tables is not None:
        endpoint_tables = [sub["table"]] if sub["kind"] == "table" else [sub["from_table"], sub["to_table"]]
        if not set(endpoint_tables) <= set(selected_tables): _fail("selector subject is outside selected tables", ERROR_SELECTOR_SCOPE)
    return se


def validate_resolution_request(request: Any, snapshot: Any = None) -> dict[str, Any]:
    try: obj = _json_native(request)
    except TypeError as exc: raise DSCContractError(str(exc), ERROR_INVALID_REQUEST) from exc
    if isinstance(obj, dict) and "comparison_snapshot_id" in obj:
        _fail("cross-snapshot requests are unsupported", ERROR_CROSS_SNAPSHOT_UNSUPPORTED)
    if isinstance(obj, dict) and any(key in obj for key in ("tenant", "tenant_id")):
        _fail("tenant fields are unsupported and reserved in DSC v1", ERROR_TENANT_SCOPE)
    obj = _closed(obj, {"protocol_version", "supported_context_versions", "snapshot", "as_of", "tables", "selectors", "consumer_id"}, {"protocol_version", "supported_context_versions", "snapshot", "as_of", "tables", "selectors", "consumer_id"}, "request")
    if obj["protocol_version"] != PROTOCOL_VERSION: _fail("protocol version unsupported", ERROR_PROTOCOL_VERSION_UNSUPPORTED)
    if not isinstance(obj["supported_context_versions"], list) or not obj["supported_context_versions"] or len(set(obj["supported_context_versions"])) != len(obj["supported_context_versions"]) or not all(isinstance(v, str) and v for v in obj["supported_context_versions"]): _fail("invalid supported_context_versions")
    if CONTEXT_VERSION not in obj["supported_context_versions"]: _fail("no mutual context version", ERROR_CONTEXT_VERSION_NO_MATCH)
    req_snap = _snapshot(obj["snapshot"])
    if obj["as_of"] != "latest": _fail("only latest as_of is supported", ERROR_AS_OF_UNSUPPORTED)
    if not isinstance(obj["tables"], list) or not obj["tables"] or not all(isinstance(t, str) and t for t in obj["tables"]) or len(set(obj["tables"])) != len(obj["tables"]): _fail("invalid selected tables")
    _string(obj["consumer_id"], "consumer_id", max_len=256)
    if not isinstance(obj["selectors"], list) or not obj["selectors"]: _fail("selectors required")
    selector_ids: set[str] = set()
    for selector in obj["selectors"]:
        se = _validate_selector(selector, obj["tables"])
        sid = se["selector_id"]
        if sid in selector_ids: _fail("duplicate selector_id", ERROR_DUPLICATE_SELECTOR)
        selector_ids.add(sid)
    if snapshot is not None:
        asset, sid, available = _snapshot_parts(snapshot)
        if req_snap["asset_id"] != asset or req_snap["snapshot_id"] != sid: _fail("request snapshot does not match resolved snapshot", ERROR_SNAPSHOT_MISMATCH)
        if available is not None and not set(obj["tables"]) <= available: _fail("selected table does not belong to snapshot", ERROR_SELECTOR_SCOPE)
    return deepcopy(obj)


def _validate_pin(pin: Any) -> None:
    pin = _closed(pin, {"artifact_id", "assertion_id", "payload_hash", "resolution", "reuse_disposition"}, {"artifact_id", "assertion_id", "payload_hash", "resolution", "reuse_disposition"}, "pin")
    _id(pin["artifact_id"], "artifact", "pin.artifact_id"); _id(pin["assertion_id"], "assertion", "pin.assertion_id"); _sha(pin["payload_hash"], "pin.payload_hash")
    if pin["resolution"] not in RESOLUTION_STATES or pin["reuse_disposition"] not in {"fresh", "exact_reused", "reuse_candidate", "not_reusable"}: _fail("invalid pin")


def _validate_selector_outcome(result: Any, selector: dict[str, Any]) -> None:
    obj = _mapping(result, "selector result")
    if obj.get("result") == "fulfilled":
        _closed(obj, {"selector_id", "result", "pins"}, {"selector_id", "result", "pins"}, "fulfilled selector result")
        if selector["predicate"] not in PREDICATES: _fail("unknown selector predicate must be unsupported")
        if not isinstance(obj["pins"], list) or not obj["pins"]: _fail("fulfilled selector needs pins")
        _unique(obj["pins"], "pins")
        for pin in obj["pins"]:
            _validate_pin(pin)
            if pin["resolution"] not in selector["accepted_resolution_states"]: _fail("pin resolution not accepted")
    elif obj.get("result") in {"unavailable", "unsupported"}:
        _closed(obj, {"selector_id", "result", "reason_codes"}, {"selector_id", "result", "reason_codes"}, "failed selector result")
        if not isinstance(obj["reason_codes"], list) or not obj["reason_codes"] or len(set(obj["reason_codes"])) != len(obj["reason_codes"]) or not set(obj["reason_codes"]) <= REASON_CODES: _fail("invalid selector reason codes")
        if selector["predicate"] not in PREDICATES and (obj["result"] != "unsupported" or set(obj["reason_codes"]) != {"DSC_R_UNSUPPORTED_FACET"}):
            _fail("unknown selector predicate must report unsupported facet")
    else: _fail("invalid selector result")


def _validate_selector_results(results: Any, request: dict[str, Any]) -> None:
    if not isinstance(results, list) or len(results) != len(request["selectors"]): _fail("selector results must match request")
    by_id = {s["selector_id"]: s for s in request["selectors"]}
    seen: set[str] = set()
    for result in results:
        obj = _mapping(result, "selector result"); sid = obj.get("selector_id")
        if sid not in by_id or sid in seen: _fail("selector results do not match request")
        seen.add(sid)
        _validate_selector_outcome(obj, by_id[sid])


def validate_context_payload(payload: Any) -> None:
    _validate_persisted_canonical_payload(payload)
    obj = _closed(payload, {"schema_version", "artifact_type", "protocol_version", "context_version", "request_fingerprint", "request_as_of", "supported_context_versions", "consumer_id", "snapshot", "resolved_as_of", "sensitivity", "selected_tables", "overall_result", "selector_results"}, {"schema_version", "artifact_type", "protocol_version", "context_version", "request_fingerprint", "request_as_of", "supported_context_versions", "consumer_id", "snapshot", "resolved_as_of", "sensitivity", "selected_tables", "overall_result", "selector_results"}, "context")
    if obj["schema_version"] != SCHEMA_VERSION or obj["artifact_type"] != CONTEXT_ARTIFACT_TYPE or obj["protocol_version"] != PROTOCOL_VERSION or obj["context_version"] != CONTEXT_VERSION: _fail("unsupported context version")
    _sha(obj["request_fingerprint"], "request_fingerprint"); _string(obj["consumer_id"], "consumer_id", max_len=256); _snapshot(obj["snapshot"])
    if not isinstance(obj["resolved_as_of"], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", obj["resolved_as_of"]): _fail("resolved_as_of must be RFC3339 date-time")
    if obj["sensitivity"] not in SENSITIVITIES or not isinstance(obj["selected_tables"], list) or not obj["selected_tables"] or len(set(obj["selected_tables"])) != len(obj["selected_tables"]) or not all(isinstance(t, str) and t for t in obj["selected_tables"]) or obj["overall_result"] not in {"fulfilled", "unfulfilled"}: _fail("invalid context")
    if not isinstance(obj["selector_results"], list) or not obj["selector_results"]: _fail("selector results required")
    ids: set[str] = set(); required_failed = False
    for result in obj["selector_results"]:
        ro = _mapping(result, "selector result")
        if not isinstance(ro.get("selector"), dict): _fail("context selector result requires selector")
        selector = _validate_selector(ro["selector"], obj["selected_tables"])
        sid = selector["selector_id"]
        if sid in ids: _fail("duplicate context selector result")
        ids.add(sid)
        outcome = {key: value for key, value in ro.items() if key != "selector"}
        _validate_selector_outcome(outcome, selector)
        if selector["requirement"] == "required" and ro.get("result") != "fulfilled": required_failed = True
    if obj["overall_result"] != ("unfulfilled" if required_failed else "fulfilled"):
        _fail("context overall result does not match persisted selector requirements")
    # Persist enough request material to independently recompute the opaque
    # request fingerprint, without storing an unbounded request envelope.
    reconstructed_request = {
        "protocol_version": obj["protocol_version"],
        "supported_context_versions": obj["supported_context_versions"],
        "snapshot": obj["snapshot"], "as_of": obj["request_as_of"],
        "tables": obj["selected_tables"],
        "selectors": [result["selector"] for result in obj["selector_results"]],
        "consumer_id": obj["consumer_id"],
    }
    normalized_request = validate_resolution_request(reconstructed_request)
    if obj["request_fingerprint"].lower() != request_fingerprint(normalized_request):
        _fail("context request_fingerprint does not match persisted request material")


def assemble_context(request: Any, selector_results: Any, *, resolved_as_of: str, sensitivity: str) -> dict[str, Any]:
    request = validate_resolution_request(request)
    if sensitivity not in SENSITIVITIES: _fail("invalid sensitivity")
    if not isinstance(resolved_as_of, str): _fail("resolved_as_of must be a string")
    _validate_selector_results(selector_results, request)
    by_id = {r["selector_id"]: r for r in selector_results}
    overall = "unfulfilled" if any(s["requirement"] == "required" and by_id[s["selector_id"]]["result"] != "fulfilled" for s in request["selectors"]) else "fulfilled"
    context = {"schema_version": SCHEMA_VERSION, "artifact_type": CONTEXT_ARTIFACT_TYPE,
        "protocol_version": PROTOCOL_VERSION, "context_version": CONTEXT_VERSION,
        "request_fingerprint": request_fingerprint(request), "request_as_of": request["as_of"],
        "supported_context_versions": deepcopy(request["supported_context_versions"]),
        "consumer_id": request["consumer_id"], "snapshot": deepcopy(request["snapshot"]),
        "resolved_as_of": resolved_as_of, "sensitivity": sensitivity,
        "selected_tables": deepcopy(request["tables"]), "overall_result": overall,
        "selector_results": [{"selector": deepcopy(selector),
                              **deepcopy(by_id[selector["selector_id"]])}
                             for selector in request["selectors"]]}
    validate_context_payload(context)
    return context


def assemble_response(context_ref: Any, context: Any) -> dict[str, Any]:
    validate_context_payload(context)
    ref = _closed(context_ref, {"artifact_id", "payload_hash"}, {"artifact_id", "payload_hash"}, "context_ref")
    _id(ref["artifact_id"], "artifact", "context_ref.artifact_id"); _sha(ref["payload_hash"], "context_ref.payload_hash")
    return {"negotiated": {"protocol_version": PROTOCOL_VERSION, "context_version": CONTEXT_VERSION},
        "overall_result": context["overall_result"], "context_ref": deepcopy(ref),
        "selector_results": deepcopy(context["selector_results"])}


def validate_response_context(response: Any, context: Any) -> None:
    validate_context_payload(context)
    obj = _closed(response, {"negotiated", "overall_result", "context_ref", "selector_results"}, {"negotiated", "overall_result", "context_ref", "selector_results"}, "response")
    neg = _closed(obj["negotiated"], {"protocol_version", "context_version"}, {"protocol_version", "context_version"}, "negotiated")
    if neg["protocol_version"] != PROTOCOL_VERSION or neg["context_version"] != CONTEXT_VERSION or obj["overall_result"] != context["overall_result"]: _fail("response negotiation or overall result mismatch")
    ref = _closed(obj["context_ref"], {"artifact_id", "payload_hash"}, {"artifact_id", "payload_hash"}, "context_ref")
    _id(ref["artifact_id"], "artifact", "context_ref.artifact_id"); _sha(ref["payload_hash"], "context_ref.payload_hash")
    if ref["payload_hash"].lower() != payload_hash(context): _fail("context_ref payload hash does not match context")
    if obj["selector_results"] != context["selector_results"]: _fail("response selector results must equal context")


def safe_assertion_summary(payload: Any) -> dict[str, Any]:
    validate_assertion_payload(payload)
    return {"predicate": payload["predicate"], "subject_kind": payload["subject"]["kind"],
            "resolution_status": payload["resolution"]["status"], "claim_count": len(payload["claims"]),
            "evidence_count": len(payload["evidence"]), "conflict_count": len(payload["conflicts"])}


def safe_context_summary(payload: Any) -> dict[str, Any]:
    validate_context_payload(payload)
    return {"overall_result": payload["overall_result"], "selector_count": len(payload["selector_results"]),
            "fulfilled_selector_count": sum(r["result"] == "fulfilled" for r in payload["selector_results"])}
