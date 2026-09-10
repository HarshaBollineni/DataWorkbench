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



# These are the v1 compatibility defaults retained for existing callers.  New
# code must negotiate from ``SUPPORTED_CONTEXT_VERSIONS`` rather than treating
# them as the only supported contract.
SCHEMA_VERSION = 1
PROTOCOL_VERSION = "1"
CONTEXT_VERSION = "1"
SUPPORTED_CONTEXT_VERSIONS = ("2", "1")
SCHEMA_VERSION_BY_CONTEXT_VERSION = {"1": 1, "2": 2}
ASSERTION_ARTIFACT_TYPE = "dataset_structure_assertion"
CONTEXT_ARTIFACT_TYPE = "dataset_structure_context"

PREDICATES = frozenset({
    "table.physical/schema_column", "table.structure/dataset_form",
    "table.structure/entity_binding", "table.structure/row_grain",
    "table.temporal/temporal_binding", "table.temporal/observed_cadence",
    "relationship.declared/relationship",
})
V2_ONLY_PREDICATES = frozenset({
    "table.structure/default_entity_binding",
    "table.temporal/default_temporal_binding",
    "table.temporal/expected_cadence",
})
PREDICATES_BY_CONTEXT_VERSION = {
    "1": PREDICATES,
    # v2 contexts can request/pin validated v1 candidate and observation
    # assertions, as well as the v2 source-confirmed selection facets.
    "2": frozenset((*PREDICATES, *V2_ONLY_PREDICATES)),
}
MULTIPLICITY_BY_PREDICATE = {
    "table.physical/schema_column": "keyed_set",
    "table.structure/dataset_form": "single",
    "table.structure/entity_binding": "keyed_set",
    "table.structure/row_grain": "single",
    "table.temporal/temporal_binding": "keyed_set",
    "table.temporal/observed_cadence": "keyed_set",
    "relationship.declared/relationship": "keyed_set",
    "table.structure/default_entity_binding": "single",
    "table.temporal/default_temporal_binding": "single",
    "table.temporal/expected_cadence": "single",
}
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


def _context_version_for_request(request: Mapping[str, Any]) -> str:
    """Choose the highest mutually supported context contract."""
    offered = request.get("supported_context_versions")
    if not isinstance(offered, list):
        _fail("invalid supported_context_versions")
    return next((version for version in SUPPORTED_CONTEXT_VERSIONS if version in offered), "")


def _candidate_locator(value: Any, label: str, *, expected_predicate: str | None = None) -> dict[str, Any]:
    obj = _closed(value, {"predicate", "instance_key"}, {"predicate", "instance_key"}, label)
    if obj["predicate"] not in PREDICATES:
        _fail(f"{label}.predicate must identify a v1 candidate predicate")
    if expected_predicate is not None and obj["predicate"] != expected_predicate:
        _fail(f"{label}.predicate is incompatible")
    _string(obj["instance_key"], f"{label}.instance_key")
    return obj


def _candidate_pin(value: Any, label: str) -> dict[str, Any]:
    obj = _closed(value, {"artifact_id", "assertion_id", "payload_hash", "dependency_fingerprint"},
                  {"artifact_id", "assertion_id", "payload_hash", "dependency_fingerprint"}, label)
    _id(obj["artifact_id"], "artifact", f"{label}.artifact_id")
    _id(obj["assertion_id"], "assertion", f"{label}.assertion_id")
    _sha(obj["payload_hash"], f"{label}.payload_hash")
    _sha(obj["dependency_fingerprint"], f"{label}.dependency_fingerprint")
    return obj


def expected_cadence_instance_key(axis_id: Any, grouping_locator: Any) -> str:
    """Return the v2 registry key without putting grouping values in it.

    ``grouping_locator`` is either a closed candidate locator or ``None``.
    The digest deliberately uses canonical JSON and its full lowercase SHA-256
    spelling so it is unambiguous and independently reproducible.
    """
    axis = _string(axis_id, "axis_id")
    grouping = None if grouping_locator is None else _candidate_locator(grouping_locator, "grouping_locator",
                                                                         expected_predicate="table.structure/entity_binding")
    return f"{axis}:{payload_hash(grouping)}"


def _v2_default_binding_value(predicate: str, subject: dict[str, Any], value: Any) -> None:
    obj = _mapping(value, "claim.value")
    kind = "default_entity_binding" if predicate.endswith("default_entity_binding") else "default_temporal_binding"
    expected = ("table.structure/entity_binding" if kind == "default_entity_binding"
                else "table.temporal/temporal_binding")
    if obj.get("kind") != kind:
        _fail("predicate and claim value kind do not match")
    if obj.get("selection") == "none":
        _closed(obj, {"kind", "selection"}, {"kind", "selection"}, kind)
        return
    _closed(obj, {"kind", "candidate_locator", "candidate_pin"},
            {"kind", "candidate_locator", "candidate_pin"}, kind)
    _candidate_locator(obj["candidate_locator"], f"{kind}.candidate_locator", expected_predicate=expected)
    _candidate_pin(obj["candidate_pin"], f"{kind}.candidate_pin")


def _v2_expected_cadence_value(subject: dict[str, Any], value: Any, instance_key: str) -> None:
    obj = _mapping(value, "claim.value")
    if obj.get("kind") != "expected_cadence":
        _fail("predicate and claim value kind do not match")
    if obj.get("selection") == "none":
        _closed(obj, {"kind", "selection"}, {"kind", "selection"}, "expected_cadence")
        return
    _closed(obj, {"kind", "unit", "step", "axis", "grouping"},
            {"kind", "unit", "step", "axis", "grouping"}, "expected_cadence")
    if obj["unit"] not in {"day", "week", "month", "quarter", "year"}:
        _fail("invalid expected cadence unit")
    if not isinstance(obj["step"], int) or isinstance(obj["step"], bool) or obj["step"] <= 0:
        _fail("invalid expected cadence step")
    axis = _closed(obj["axis"], {"axis_id", "locator", "pin"}, {"axis_id", "locator", "pin"}, "expected_cadence.axis")
    axis_id = _string(axis["axis_id"], "expected_cadence.axis.axis_id")
    _candidate_locator(axis["locator"], "expected_cadence.axis.locator", expected_predicate="table.temporal/temporal_binding")
    _candidate_pin(axis["pin"], "expected_cadence.axis.pin")
    grouping = obj["grouping"]
    if grouping is not None:
        grouping = _closed(grouping, {"locator", "pin"}, {"locator", "pin"}, "expected_cadence.grouping")
        _candidate_locator(grouping["locator"], "expected_cadence.grouping.locator",
                           expected_predicate="table.structure/entity_binding")
        _candidate_pin(grouping["pin"], "expected_cadence.grouping.pin")
        grouping_locator = grouping["locator"]
    else:
        grouping_locator = None
    if instance_key != expected_cadence_instance_key(axis_id, grouping_locator):
        _fail("expected cadence instance_key does not match axis/grouping locator")


def _v2_value_pins(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the immutable candidate pins a selected v2 value must evidence."""
    if value.get("selection") == "none":
        return []
    if value["kind"] in {"default_entity_binding", "default_temporal_binding"}:
        return [value["candidate_pin"]]
    pins = [value["axis"]["pin"]]
    if value["grouping"] is not None:
        pins.append(value["grouping"]["pin"])
    return pins


def _value_for(predicate: str, subject: dict[str, Any], value: Any, *, context_version: str = "1",
               instance_key: str | None = None) -> None:
    if context_version == "2" and predicate in V2_ONLY_PREDICATES:
        if predicate == "table.temporal/expected_cadence":
            _v2_expected_cadence_value(subject, value, instance_key or "")
        else:
            _v2_default_binding_value(predicate, subject, value)
        return
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
        if typ not in {"date", "period", "instant", "interval"} or obj["precision"] not in {"year", "quarter", "month", "day", "hour", "minute", "second", "millisecond", "microsecond", "unknown"} or obj["calendar"] not in {"gregorian", "other_declared", "unknown"}: _fail("invalid temporal binding")
        if typ in {"date", "period"} and "timezone" in obj: _fail("date or period temporal binding cannot have timezone")
        if "timezone" in obj: _string(obj["timezone"], "timezone")
        cols = _columns(obj["columns"], "temporal columns", minimum=1, maximum=2)
        if len(cols) != (2 if typ == "interval" else 1): _fail("temporal type requires exact column count")
        if any(c["table"] != subject["table"] for c in cols): _fail("temporal column is out of subject scope")
        if "grouping" in obj and any(c["table"] != subject["table"] for c in _columns(obj["grouping"], "grouping")): _fail("grouping column is out of subject scope")
    elif kind == "observed_cadence":
        _closed(obj, {"kind", "axis_id", "grouping", "cadence"}, {"kind", "axis_id", "grouping", "cadence", "observed_interval_class"}, kind)
        _string(obj["axis_id"], "axis_id"); _columns(obj["grouping"], "grouping", minimum=1, maximum=1)
        if obj["cadence"] not in {"regular", "mixed", "irregular", "unknown"}: _fail("invalid cadence")
        interval = obj.get("observed_interval_class")
        if (obj["cadence"] == "regular") != (interval is not None):
            _fail("regular cadence requires exactly one interval class")
        if interval is not None:
            interval = _closed(interval, {"unit", "step"}, {"unit", "step"}, "observed_interval_class")
            if interval["unit"] not in {"day", "week", "month", "quarter", "year"}:
                _fail("invalid observed interval unit")
            if not isinstance(interval["step"], int) or isinstance(interval["step"], bool) or interval["step"] <= 0:
                _fail("invalid observed interval step")
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
    context_version = obj["context_version"]
    if (obj["artifact_type"] != ASSERTION_ARTIFACT_TYPE
            or context_version not in SCHEMA_VERSION_BY_CONTEXT_VERSION
            or obj["schema_version"] != SCHEMA_VERSION_BY_CONTEXT_VERSION[context_version]):
        _fail("unsupported assertion version")
    _id(obj["assertion_id"], "assertion", "assertion_id"); _snapshot(obj["snapshot"]); subject = _subject(obj["subject"])
    if obj["predicate"] not in PREDICATES_BY_CONTEXT_VERSION[context_version] or obj["multiplicity"] not in {"single", "keyed_set"}: _fail("invalid predicate or multiplicity")
    # v2 assertion artifacts are deliberately authority artifacts only.  v1
    # candidate/observed assertions remain immutable inputs pinned by v2.
    if context_version == "2" and obj["predicate"] not in V2_ONLY_PREDICATES:
        _fail("v2 assertion payloads are reserved for v2 authority predicates")
    expected_multiplicity = MULTIPLICITY_BY_PREDICATE[obj["predicate"]]
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
        _value_for(obj["predicate"], subject, cl["value"], context_version=context_version,
                   instance_key=obj["instance_key"])
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
            allowed_actions = ({"confirm", "clear", "mark_not_applicable"}
                               if context_version == "2" else {"confirm", "replace", "clear", "mark_not_applicable", "promote"})
            if dec["action"] not in allowed_actions or dec["scope"] not in {"source_confirmed_structural", "consumer_local"}: _fail("invalid decision")
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
    if context_version == "2":
        # All v2 authority artifacts are source-confirmed, single-action
        # decisions.  ``replace`` belongs exclusively to mutable draft edits;
        # a later confirm supersedes a prior active value at persistence time.
        if len(claims) != 1:
            _fail("v2 authority assertion requires exactly one claim")
        claim = next(iter(claims.values()))
        decision = claim.get("decision")
        if (claim.get("authority") != "source_confirmed_structural"
                or not isinstance(decision, dict)
                or decision.get("scope") != "source_confirmed_structural"):
            _fail("v2 authority assertion must be source-confirmed")
        action = decision["action"]
        none_value = claim["value"].get("selection") == "none"
        if action == "confirm":
            if none_value or res["status"] != "confirmed" or res["effective_claim_ids"] != [claim["claim_id"]]:
                _fail("v2 confirm requires a selected confirmed value")
        elif action == "clear":
            if not none_value or res["status"] != "unknown" or res["effective_claim_ids"] or set(res["reason_codes"]) != {"DSC_R_DECISION_CLEARED"}:
                _fail("v2 clear requires an empty unknown resolution")
        else:  # mark_not_applicable
            if not none_value or res["status"] != "not_applicable" or res["effective_claim_ids"]:
                _fail("v2 mark_not_applicable requires an empty not-applicable resolution")
        # A v2 authority is never a free-standing declaration.  It carries one
        # structural-decision witness and the claim is linked to that witness
        # exactly.  The descriptor/UoW additionally dereferences its pins in
        # the same write transaction.
        if len(obj["evidence"]) != 1 or obj["evidence"][0]["kind"] != "structural_decision":
            _fail("v2 authority assertion requires exactly one structural decision evidence")
        evidence = obj["evidence"][0]
        if claim["evidence_ids"] != [evidence["evidence_id"]]:
            _fail("v2 authority claim must link exactly its structural decision evidence")
        evidence_refs = {(ref["artifact_id"], ref["payload_hash"], ref["role"])
                         for ref in evidence["source_refs"]}
        pins = _v2_value_pins(claim["value"])
        if pins and not {(pin["artifact_id"], pin["payload_hash"], "decision") for pin in pins} <= evidence_refs:
            _fail("v2 authority evidence must link every selected candidate pin")


def assertion_identity_key(payload: Any) -> str:
    validate_assertion_payload(payload)
    obj = _json_native(payload)
    return canonical_payload_bytes({"snapshot": obj["snapshot"], "subject": obj["subject"], "predicate": obj["predicate"], "instance_key": obj["instance_key"]}).decode("utf-8")


def _validate_cadence_unknown_projection(payload: dict[str, Any], dependencies: list[tuple[dict[str, Any], dict[str, Any]]],
                                          asset_id: str, snapshot_id: str, table: str, axis_id: str,
                                          axis: dict[str, Any], grouping: list[dict[str, Any]]) -> bool:
    """Pure, read-side reconstruction of the cadence producer's unknown state.

    The projector deliberately receives only rows/payloads already read in its
    transaction.  It therefore cannot accidentally call a repository getter
    (which would update integrity status) while proving producer provenance.
    """
    try:
        evidence = payload.get("evidence")
        resolution = payload.get("resolution")
        if (not isinstance(evidence, list) or len(evidence) != 1 or evidence[0].get("kind") != "bounded_scan"
                or payload.get("claims") != [] or not isinstance(resolution, dict)
                or resolution.get("status") != "unknown" or resolution.get("effective_claim_ids") != []
                or resolution.get("reason_codes") != ["DSC_R_INSUFFICIENT_BASIS"]):
            return False
        expected_assertion_id = "dsca_" + payload_hash({"schema_version": 1, "context_version": "1",
            "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
            "subject": {"kind": "table", "table": table}, "predicate": "table.temporal/observed_cadence",
            "instance_key": payload.get("instance_key")})
        if payload.get("assertion_id") != expected_assertion_id:
            return False
        refs = evidence[0].get("source_refs")
        if not isinstance(refs, list) or len(refs) != 6 or len({ref.get("artifact_id") for ref in refs if isinstance(ref, dict)}) != 6:
            return False
        sources = {item[0]["artifact_id"]: item for item in dependencies}
        if set(sources) != {ref.get("artifact_id") for ref in refs if isinstance(ref, dict)}:
            return False
        ref_by_id = {ref["artifact_id"]: ref for ref in refs if isinstance(ref, dict)}
        typed = {"scan": [], "profile": [], "inventory": [], "temporal": [], "entity": []}
        for artifact_id, (meta, source_payload) in sources.items():
            ref = ref_by_id[artifact_id]
            if ref.get("payload_hash") != meta.get("payload_hash"):
                return False
            if ref.get("role") == "scan" and meta.get("artifact_type") == "table_profile": typed["scan"].append((meta, source_payload))
            elif ref.get("role") == "profile" and meta.get("artifact_type") == "column_profile": typed["profile"].append((meta, source_payload))
            elif ref.get("role") == "dependency" and meta.get("artifact_type") == "table_inventory_profile": typed["inventory"].append((meta, source_payload))
            elif ref.get("role") == "dependency" and meta.get("artifact_type") == "dataset_structure_assertion":
                if source_payload.get("predicate") == "table.temporal/temporal_binding": typed["temporal"].append((meta, source_payload))
                elif source_payload.get("predicate") == "table.structure/entity_binding": typed["entity"].append((meta, source_payload))
                else: return False
            else: return False
        if any(len(typed[name]) != count for name, count in {"scan": 1, "profile": 2, "inventory": 1, "temporal": 1, "entity": 1}.items()):
            return False
        profile_by_feature = {meta.get("feature"): meta for meta, _source in typed["profile"]}
        if set(profile_by_feature) != {axis["column"], grouping[0]["column"]}:
            return False
        temporal_meta, temporal_payload = typed["temporal"][0]
        entity_meta, entity_payload = typed["entity"][0]
        # These prerequisite facts must be the producer-owned proposed facts,
        # not a syntactically valid hand-authored assertion.
        def effective(source: dict[str, Any]) -> dict[str, Any] | None:
            ids = source.get("resolution", {}).get("effective_claim_ids")
            claims = source.get("claims")
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(claims, list): return None
            return next((claim for claim in claims if claim.get("claim_id") == ids[0]), None)
        temporal_claim, entity_claim = effective(temporal_payload), effective(entity_payload)
        temporal_value = temporal_claim.get("value") if isinstance(temporal_claim, dict) else None
        entity_value = entity_claim.get("value") if isinstance(entity_claim, dict) else None
        if (temporal_payload.get("resolution", {}).get("status") != "proposed"
                or temporal_claim is None or temporal_claim.get("authority") != "source_reviewed_metadata"
                or not isinstance(temporal_value, dict) or temporal_value.get("kind") != "temporal_binding"
                or temporal_value.get("axis_id") != axis_id or temporal_value.get("columns") != [axis]
                or temporal_value.get("temporal_type") not in {"date", "period"}
                or entity_payload.get("resolution", {}).get("status") != "proposed"
                or entity_claim is None or entity_claim.get("authority") != "source_reviewed_metadata"
                or entity_value != {"kind": "entity_binding", "columns": [grouping[0]]}):
            return False
        table_meta, _table_payload = typed["scan"][0]
        inventory_meta, _inventory_payload = typed["inventory"][0]
        temporal_profile_meta = profile_by_feature[axis["column"]]
        entity_profile_meta = profile_by_feature[grouping[0]["column"]]
        temporal_profile = next(source for meta, source in typed["profile"] if meta["artifact_id"] == temporal_profile_meta["artifact_id"])
        entity_profile = next(source for meta, source in typed["profile"] if meta["artifact_id"] == entity_profile_meta["artifact_id"])
        def exact_reviewed_source(source: dict[str, Any], *, predicate: str, column: str,
                                  profile_meta: dict[str, Any], supporting_meta: dict[str, Any],
                                  supporting_role: str, dependency: dict[str, Any]) -> bool:
            try:
                assertion_id = "dsca_" + payload_hash({"schema_version": 1, "context_version": "1",
                    "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
                    "subject": {"kind": "table", "table": table}, "predicate": predicate,
                    "instance_key": f"column:{column}"})
                evidence = source.get("evidence")
                claims = source.get("claims")
                if (source.get("assertion_id") != assertion_id or not isinstance(evidence, list) or len(evidence) != 1
                        or evidence[0].get("kind") != "reviewed_metadata" or not isinstance(claims, list) or len(claims) != 1): return False
                expected_refs = [{"artifact_id": profile_meta["artifact_id"], "role": "reviewed_metadata", "payload_hash": profile_meta["payload_hash"]},
                                 {"artifact_id": supporting_meta["artifact_id"], "role": supporting_role, "payload_hash": supporting_meta["payload_hash"]}]
                claim = claims[0]
                if (evidence[0].get("source_refs") != expected_refs or claim.get("evidence_ids") != [evidence[0].get("evidence_id")]
                        or source.get("dependency_fingerprint") != payload_hash(dependency)): return False
                return (claim.get("claim_id") == "dscc_" + payload_hash({"assertion_id": assertion_id,
                        "authority": "source_reviewed_metadata", "value": claim.get("value")})
                        and evidence[0].get("evidence_id") == "dsce_" + payload_hash({"assertion_id": assertion_id,
                        "kind": "reviewed_metadata", "source_refs": expected_refs, "basis": evidence[0].get("basis"),
                        "measurements": evidence[0].get("measurements")}))
            except Exception: return False
        temporal_role = str(temporal_profile.get("role") or "").strip()
        if temporal_role.lower() not in {"date", "period"} or temporal_profile.get("metadata_reviewed") is not True:
            return False
        if not exact_reviewed_source(temporal_payload, predicate="table.temporal/temporal_binding", column=axis["column"],
                profile_meta=temporal_profile_meta, supporting_meta=inventory_meta, supporting_role="dependency",
                dependency={"observer_version": "profile-structure-v2", "temporal_evidence_policy_version": "1",
                    "temporal_producer_version": "1", "schema_version": 1, "context_version": "1",
                    "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "qualified_column": axis,
                    "table_inventory_profile": [inventory_meta["artifact_id"], inventory_meta["payload_hash"]],
                    "column_profile": [temporal_profile_meta["artifact_id"], temporal_profile_meta["payload_hash"]],
                    "metadata_reviewed": True, "reviewed_role": temporal_role,
                    "temporal_type": temporal_role.lower()}): return False
        if not exact_reviewed_source(entity_payload, predicate="table.structure/entity_binding", column=grouping[0]["column"],
                profile_meta=entity_profile_meta, supporting_meta=table_meta, supporting_role="dependency",
                dependency={"observer_version": "profile-structure-v2", "table_profile": [table_meta["artifact_id"], table_meta["payload_hash"]],
                    "column_profile": [entity_profile_meta["artifact_id"], entity_profile_meta["payload_hash"]]}): return False
        dependencies_value = {
            "cadence_observer_version": "1", "normalizer_version": "strict-date-quarter-v1",
            "classifier_version": "gregorian-exact-v1", "evidence_policy_version": "1",
            "schema_version": 1, "context_version": "1", "registry_version": "1",
            "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "table": table,
            "axis": [temporal_meta["artifact_id"], temporal_meta["payload_hash"]],
            "grouping": [entity_meta["artifact_id"], entity_meta["payload_hash"]],
            "axis_profile": [profile_by_feature[axis["column"]]["artifact_id"], profile_by_feature[axis["column"]]["payload_hash"]],
            "grouping_profile": [profile_by_feature[grouping[0]["column"]]["artifact_id"], profile_by_feature[grouping[0]["column"]]["payload_hash"]],
            "table_profile": [table_meta["artifact_id"], table_meta["payload_hash"]],
            "inventory_profile": [inventory_meta["artifact_id"], inventory_meta["payload_hash"]],
            "policy": {"max_groupings": 32, "max_axes": 32, "max_pairs": 256, "max_rows": 1_000_000},
}
        # Deferred import avoids a package-import cycle while keeping the
        # actual provenance arithmetic in the shared pure module.
        from domains.aar.cadence_provenance import validate_cadence_payload
        return validate_cadence_payload(payload, snapshot={"asset_id": asset_id, "snapshot_id": snapshot_id},
                                        table=table, instance_key=payload.get("instance_key", ""),
                                        dependency_fingerprint=payload_hash(dependencies_value),
                                        expected_refs={(ref["artifact_id"], ref["role"], ref["payload_hash"]) for ref in refs},
                                        axis_id=axis_id, grouping=grouping, unknown_only=True)
    except Exception:
        return False


def project_materialized_dataset_structure(request: Mapping[str, Any], *, repository: Any | None = None,
                                           deadline: float | None = None) -> dict[str, Any]:
    """Project one already-materialised cadence locator without changing AAR.

    This deliberately is not the DSC resolver.  It has a deliberately narrow
    selector so consumers cannot turn it into candidate discovery.  All SQL and
    payload reads occur while one SQLite read transaction is open; unlike
    ``AnalysisArtifactRepository.get`` this function never updates integrity
    audit fields.
    """
    from pathlib import Path
    import time
    import system_db as db

    dependency_cap, payload_cap, evidence_cap = 16, 262_144, 8
    closed_reasons = {None, "dependency_changed", "dependency_limit_exceeded", "source_missing",
                      "source_integrity_failed", "payload_limit_exceeded", "evidence_limit_exceeded",
                      "invalid_response", "deadline_exceeded", "db_busy", "projection_error"}
    try:
        identity = request_fingerprint(dict(request))
    except Exception:
        identity = payload_hash({"projection_version": 1, "invalid_request": True})

    def expired() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def response(outcome: str, reason: str | None = None, *, timestamp: str | None = None,
                 boundary: str | None = None, cadence: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one closed v1 response builder, including malformed inputs."""
        # This is the final guard on every return path, including successful
        # projection branches reached immediately after a slow SQLite call.
        if expired() and reason != "deadline_exceeded":
            outcome, reason, cadence = "error", "deadline_exceeded", None
        if reason not in closed_reasons:
            reason = "projection_error"
        result: dict[str, Any] = {
            "projection_version": 1, "request_identity": identity, "outcome": outcome,
            "resolved_as_of": {"timestamp": timestamp or db.now_ist(),
                                "read_boundary_fingerprint": boundary or ("0" * 64)},
            "reason": reason,
        }
        if cadence is not None:
            result["cadence"] = cadence
        return result

    try:
        value = dict(request)
        required = {"asset_id", "snapshot_id", "table", "predicate", "axis_id", "axis_column", "grouping", "consumer_id"}
        if set(value) != required or value["consumer_id"] != "diagnostic:6:dsc-cadence-shadow-v1":
            raise ValueError
        asset_id, snapshot_id, table = (value[key] for key in ("asset_id", "snapshot_id", "table"))
        axis, grouping = value["axis_column"], value["grouping"]
        if (not all(isinstance(item, str) and item for item in (asset_id, snapshot_id, table, value["axis_id"]))
                or value["predicate"] != "table.temporal/observed_cadence" or not isinstance(axis, dict)
                or set(axis) != {"table", "column"} or not isinstance(grouping, list) or len(grouping) != 1
                or not isinstance(grouping[0], dict) or set(grouping[0]) != {"table", "column"}
                or axis.get("table") != table or grouping[0].get("table") != table
                or not isinstance(axis.get("column"), str) or not axis["column"]
                or not isinstance(grouping[0].get("column"), str) or not grouping[0]["column"]
                or value["axis_id"] != f"column:{axis['column']}"):
            raise ValueError
        instance_key = "axis_grouping:" + hashlib.sha256(canonical_payload_bytes(
            {"axis_id": value["axis_id"], "grouping": grouping})).hexdigest()
        locator = canonical_payload_bytes({"snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
            "subject": {"kind": "table", "table": table}, "predicate": value["predicate"], "instance_key": instance_key}).decode("utf-8")
    except Exception:
        return response("error", "invalid_response")

    root = Path(getattr(repository, "root", None) or __import__("domains.aar.repository", fromlist=["artifact_storage_root"]).artifact_storage_root()).resolve()
    timestamp, boundary = db.now_ist(), payload_hash({"assertions_and_dependencies": [], "dependency_edges": [],
                                                       "events": [], "catalogue_marker": None})
    try:
        if expired():
            return response("error", "deadline_exceeded", timestamp=timestamp, boundary=boundary)
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return response("error", "deadline_exceeded", timestamp=timestamp, boundary=boundary)
        connection_timeout = min(0.1, remaining) if remaining is not None else 0.1
        with db.get_conn(timeout=connection_timeout, configure_journal=False) as conn:
            busy_ms = max(1, round(connection_timeout * 1000))
            conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
            conn.set_progress_handler(lambda: 1 if expired() else 0, 100)
            conn.execute("BEGIN")
            timestamp = db.now_ist()
            if expired():
                return response("error", "deadline_exceeded", timestamp=timestamp, boundary=boundary)
            query = """SELECT * FROM analysis_artifacts WHERE artifact_type=? AND asset_id=? AND snapshot_id=?
                AND json_extract(identity_json, '$.table')=?
                AND json_extract(identity_json, '$.identity_inputs.assertion_locator')=?"""
            all_locator_rows = conn.execute(query + " ORDER BY artifact_id LIMIT 5",
                                            ("dataset_structure_assertion", asset_id, snapshot_id, table, locator)).fetchall()
            if expired(): raise RuntimeError("deadline_exceeded")
            rows = conn.execute(query + " AND status='active' ORDER BY artifact_id LIMIT 5",
                                ("dataset_structure_assertion", asset_id, snapshot_id, table, locator)).fetchall()
            if expired(): raise RuntimeError("deadline_exceeded")
            loaded: list[tuple[dict[str, Any], dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]] = []
            boundary_rows: dict[str, dict[str, Any]] = {row["artifact_id"]: db._decode("analysis_artifacts", row)
                                                         for row in all_locator_rows}

            def build_boundary() -> str:
                """Fingerprint every record observed so far in this one read."""
                boundary_ids = sorted(boundary_rows)
                events, edges = [], []
                if boundary_ids:
                    marks = ",".join("?" for _ in boundary_ids)
                    events = [dict(row) for row in conn.execute(
                        f"SELECT artifact_id,event_id,event_type,created_at,detail_json FROM analysis_artifact_events WHERE artifact_id IN ({marks}) ORDER BY artifact_id,event_id",
                        boundary_ids).fetchall()]
                    if expired(): raise RuntimeError("deadline_exceeded")
                    edges = [dict(row) for row in conn.execute(
                        f"SELECT artifact_id,source_artifact_id,role FROM analysis_artifact_sources WHERE artifact_id IN ({marks}) OR source_artifact_id IN ({marks}) ORDER BY artifact_id,source_artifact_id,role",
                        [*boundary_ids, *boundary_ids]).fetchall()]
                    if expired(): raise RuntimeError("deadline_exceeded")
                catalogue = conn.execute("SELECT item_id,table_name,row_count,col_count,columns FROM dq_item_tables WHERE item_id=? AND table_name=?",
                                         (snapshot_id, table)).fetchone()
                if expired(): raise RuntimeError("deadline_exceeded")
                records = [{"artifact_id": item["artifact_id"], "payload_hash": item["payload_hash"], "status": item["status"],
                            "lifecycle": {"created_at": item.get("created_at"), "superseded_at": item.get("superseded_at"),
                                          "superseded_by_artifact_id": item.get("superseded_by_artifact_id"),
                                          "integrity_status": item.get("integrity_status")}}
                           for _aid, item in sorted(boundary_rows.items())]
                return payload_hash({"assertions_and_dependencies": records, "dependency_edges": edges, "events": events,
                                     "catalogue_marker": dict(catalogue) if catalogue else None})

            boundary = build_boundary()
            # LIMIT 5 is an ambiguity sentinel, never an invitation to read a
            # corrupt fifth payload. Candidate integrity is irrelevant once a
            # fifth active row proves no unique projection exists.
            if len(rows) > 4:
                return response("ambiguous", timestamp=timestamp, boundary=boundary)

            def read_payload(item: dict[str, Any], *, limit: int = payload_cap) -> dict[str, Any]:
                if expired(): raise RuntimeError("deadline_exceeded")
                path = (root / item["payload_path"]).resolve()
                if path.parent != root or path.suffix != ".json": raise RuntimeError("source_integrity_failed")
                try:
                    if path.stat().st_size > limit: raise RuntimeError("payload_limit_exceeded")
                    if expired(): raise RuntimeError("deadline_exceeded")
                    with path.open("rb") as stream: raw = stream.read(limit + 1)
                except FileNotFoundError as exc: raise RuntimeError("source_missing") from exc
                if expired(): raise RuntimeError("deadline_exceeded")
                if len(raw) > limit: raise RuntimeError("payload_limit_exceeded")
                if hashlib.sha256(raw).hexdigest() != item["payload_hash"]: raise RuntimeError("source_integrity_failed")
                try: payload = json.loads(raw)
                except Exception as exc: raise RuntimeError("source_integrity_failed") from exc
                if expired(): raise RuntimeError("deadline_exceeded")
                if payload_hash(payload) != item["payload_hash"]: raise RuntimeError("source_integrity_failed")
                return payload

            for row in rows:
                item = db._decode("analysis_artifacts", row)
                payload = read_payload(item)
                try: validate_assertion_payload(payload)
                except Exception as exc: raise RuntimeError("source_integrity_failed") from exc
                if len(payload.get("evidence", [])) > evidence_cap: raise RuntimeError("evidence_limit_exceeded")
                refs = {(ref.get("artifact_id"), ref.get("role"), ref.get("payload_hash"))
                        for evidence in payload.get("evidence", []) for ref in evidence.get("source_refs", [])
                        if isinstance(ref, dict)}
                if len(refs) > dependency_cap: raise RuntimeError("dependency_limit_exceeded")
                dependencies: list[tuple[dict[str, Any], dict[str, Any]]] = []
                for artifact_id, role, digest in sorted(refs):
                    if not isinstance(artifact_id, str) or not isinstance(role, str) or not isinstance(digest, str):
                        raise RuntimeError("source_integrity_failed")
                    source_row = conn.execute("SELECT * FROM analysis_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                    if expired(): raise RuntimeError("deadline_exceeded")
                    if source_row is None: raise RuntimeError("dependency_changed")
                    source = db._decode("analysis_artifacts", source_row)
                    if (source["status"] != "active" or source["integrity_status"] not in {"verified", "unknown"}
                            or source["asset_id"] != asset_id or source["snapshot_id"] != snapshot_id
                            or source["payload_hash"] != digest): raise RuntimeError("dependency_changed")
                    boundary_rows[source["artifact_id"]] = source
                    boundary = build_boundary()
                    source_payload = read_payload(source)
                    dependencies.append((source, source_payload))
                    boundary = build_boundary()
                loaded.append((item, payload, dependencies))
            if not rows:
                return response("selected_pair_absent", timestamp=timestamp, boundary=boundary)

            states: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, list[tuple[dict[str, Any], dict[str, Any]]]]] = []
            for item, payload, dependencies in loaded:
                if (payload.get("snapshot") != {"asset_id": asset_id, "snapshot_id": snapshot_id}
                        or payload.get("subject") != {"kind": "table", "table": table}
                        or payload.get("predicate") != value["predicate"] or payload.get("instance_key") != instance_key):
                    raise RuntimeError("source_integrity_failed")
                effective = payload.get("resolution", {}).get("effective_claim_ids", [])
                claim = next((entry for entry in payload.get("claims", []) if entry.get("claim_id") in effective), None)
                states.append((item, payload, claim, dependencies))
            observed = [entry for entry in states if entry[1]["resolution"].get("status") == "observed"]
            confirmed = [entry for entry in states if entry[1]["resolution"].get("status") == "confirmed"]
            unknown = [entry for entry in states if entry[1]["resolution"].get("status") == "unknown"]
            if len(observed) > 1 or len(confirmed) > 1 or len(unknown) > 1 or len(states) != len(observed) + len(confirmed) + len(unknown):
                return response("ambiguous", timestamp=timestamp, boundary=boundary)
            if unknown:
                item, payload, claim, dependencies = unknown[0]
                if observed or confirmed or claim is not None or not _validate_cadence_unknown_projection(
                        payload, dependencies, asset_id, snapshot_id, table, value["axis_id"], axis, grouping):
                    raise RuntimeError("source_integrity_failed")
                return response("fulfilled", timestamp=timestamp, boundary=boundary,
                    cadence={"resolution_state": "unknown", "value": {"state": "unknown"},
                             "assertion_pin": {"artifact_id": item["artifact_id"], "payload_hash": item["payload_hash"],
                                               "dependency_fingerprint": payload["dependency_fingerprint"]}})
            if not observed:
                return response("ambiguous", timestamp=timestamp, boundary=boundary)
            observed_meta, observed_payload, observed_claim, _deps = observed[0]
            if not isinstance(observed_claim, dict) or observed_claim.get("authority") != "verified_observation":
                raise RuntimeError("source_integrity_failed")
            selected_meta, selected_payload, selected_claim, state = observed_meta, observed_payload, observed_claim, "observed"
            if confirmed:
                conf_meta, conf_payload, conf_claim, _deps = confirmed[0]
                refs = {(ref.get("artifact_id"), ref.get("role"), ref.get("payload_hash"))
                        for evidence in conf_payload.get("evidence", []) for ref in evidence.get("source_refs", []) if isinstance(ref, dict)}
                if (not isinstance(conf_claim, dict) or conf_claim.get("authority") != "source_confirmed_structural"
                        or conf_claim.get("value") != observed_claim.get("value")
                        or conf_payload.get("dependency_fingerprint") != observed_payload.get("dependency_fingerprint")
                        or (observed_meta["artifact_id"], "dependency", observed_meta["payload_hash"]) not in refs):
                    return response("ambiguous", timestamp=timestamp, boundary=boundary)
                selected_meta, selected_payload, selected_claim, state = conf_meta, conf_payload, conf_claim, "confirmed"
            observed_value = selected_claim.get("value", {})
            if (observed_value.get("kind") != "observed_cadence" or observed_value.get("axis_id") != value["axis_id"]
                    or observed_value.get("grouping") != grouping or observed_value.get("cadence") not in {"regular", "mixed", "irregular"}):
                raise RuntimeError("source_integrity_failed")
            cadence_value = {"state": observed_value["cadence"]}
            if observed_value["cadence"] == "regular":
                interval = observed_value.get("observed_interval_class")
                if not isinstance(interval, dict) or set(interval) != {"unit", "step"}: raise RuntimeError("source_integrity_failed")
                cadence_value["observed_interval_class"] = interval
            elif "observed_interval_class" in observed_value:
                raise RuntimeError("source_integrity_failed")
            return response("fulfilled", timestamp=timestamp, boundary=boundary,
                cadence={"resolution_state": state, "value": cadence_value,
                         "assertion_pin": {"artifact_id": selected_meta["artifact_id"], "payload_hash": selected_meta["payload_hash"],
                                           "dependency_fingerprint": selected_payload["dependency_fingerprint"]}})
    except RuntimeError as exc:
        reason = str(exc)
        if reason == "deadline_exceeded": return response("error", reason, timestamp=timestamp, boundary=boundary)
        return response("unavailable", reason if reason in closed_reasons else "source_integrity_failed", timestamp=timestamp, boundary=boundary)
    except Exception as exc:
        if expired():
            return response("error", "deadline_exceeded", timestamp=timestamp, boundary=boundary)
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return response("error", "db_busy", timestamp=timestamp, boundary=boundary)
        return response("error", "projection_error", timestamp=timestamp, boundary=boundary)


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


def _validate_selector(selector: Any, selected_tables: list[str] | None = None, *, context_version: str = "1") -> dict[str, Any]:
    se = _closed(selector, {"selector_id", "subject", "predicate", "requirement", "accepted_resolution_states"}, {"selector_id", "subject", "predicate", "requirement", "accepted_resolution_states"}, "selector")
    _string(se["selector_id"], "selector_id", max_len=256)
    sub = _subject(se["subject"])
    predicate = se["predicate"]
    if predicate in PREDICATES_BY_CONTEXT_VERSION[context_version]:
        if (sub["kind"] == "relationship") != predicate.startswith("relationship."):
            _fail("selector predicate and subject mismatch")
    elif predicate in V2_ONLY_PREDICATES:
        # A v1 request is allowed to express a known newer facet so the
        # resolver can return the closed unsupported-facet result, not a
        # protocol error or a silently fulfilled future selector.
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
    # Protocol v1 remains the compatibility floor.  A v2-capable caller offers
    # ["2", "1"] and negotiates v2; a legacy request that only offers a future
    # version keeps its historical no-match failure.
    if "1" not in obj["supported_context_versions"]:
        _fail("no mutual context version", ERROR_CONTEXT_VERSION_NO_MATCH)
    context_version = _context_version_for_request(obj)
    req_snap = _snapshot(obj["snapshot"])
    if obj["as_of"] != "latest": _fail("only latest as_of is supported", ERROR_AS_OF_UNSUPPORTED)
    if not isinstance(obj["tables"], list) or not obj["tables"] or not all(isinstance(t, str) and t for t in obj["tables"]) or len(set(obj["tables"])) != len(obj["tables"]): _fail("invalid selected tables")
    _string(obj["consumer_id"], "consumer_id", max_len=256)
    if not isinstance(obj["selectors"], list) or not obj["selectors"]: _fail("selectors required")
    selector_ids: set[str] = set()
    for selector in obj["selectors"]:
        se = _validate_selector(selector, obj["tables"], context_version=context_version)
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


def _validate_selector_outcome(result: Any, selector: dict[str, Any], *, context_version: str) -> None:
    obj = _mapping(result, "selector result")
    if obj.get("result") == "fulfilled":
        _closed(obj, {"selector_id", "result", "pins"}, {"selector_id", "result", "pins"}, "fulfilled selector result")
        if selector["predicate"] not in PREDICATES_BY_CONTEXT_VERSION[context_version]: _fail("unknown selector predicate must be unsupported")
        if not isinstance(obj["pins"], list) or not obj["pins"]: _fail("fulfilled selector needs pins")
        _unique(obj["pins"], "pins")
        for pin in obj["pins"]:
            _validate_pin(pin)
            if pin["resolution"] not in selector["accepted_resolution_states"]: _fail("pin resolution not accepted")
    elif obj.get("result") in {"unavailable", "unsupported"}:
        _closed(obj, {"selector_id", "result", "reason_codes"}, {"selector_id", "result", "reason_codes"}, "failed selector result")
        if not isinstance(obj["reason_codes"], list) or not obj["reason_codes"] or len(set(obj["reason_codes"])) != len(obj["reason_codes"]) or not set(obj["reason_codes"]) <= REASON_CODES: _fail("invalid selector reason codes")
        if selector["predicate"] not in PREDICATES_BY_CONTEXT_VERSION[context_version] and (obj["result"] != "unsupported" or set(obj["reason_codes"]) != {"DSC_R_UNSUPPORTED_FACET"}):
            _fail("unknown selector predicate must report unsupported facet")
    else: _fail("invalid selector result")


def _validate_selector_results(results: Any, request: dict[str, Any]) -> None:
    if not isinstance(results, list) or len(results) != len(request["selectors"]): _fail("selector results must match request")
    by_id = {s["selector_id"]: s for s in request["selectors"]}
    seen: set[str] = set()
    context_version = _context_version_for_request(request)
    for result in results:
        obj = _mapping(result, "selector result"); sid = obj.get("selector_id")
        if sid not in by_id or sid in seen: _fail("selector results do not match request")
        seen.add(sid)
        _validate_selector_outcome(obj, by_id[sid], context_version=context_version)


def validate_context_payload(payload: Any) -> None:
    _validate_persisted_canonical_payload(payload)
    obj = _closed(payload, {"schema_version", "artifact_type", "protocol_version", "context_version", "request_fingerprint", "request_as_of", "supported_context_versions", "consumer_id", "snapshot", "resolved_as_of", "sensitivity", "selected_tables", "overall_result", "selector_results"}, {"schema_version", "artifact_type", "protocol_version", "context_version", "request_fingerprint", "request_as_of", "supported_context_versions", "consumer_id", "snapshot", "resolved_as_of", "sensitivity", "selected_tables", "overall_result", "selector_results"}, "context")
    context_version = obj["context_version"]
    if (obj["artifact_type"] != CONTEXT_ARTIFACT_TYPE or obj["protocol_version"] != PROTOCOL_VERSION
            or context_version not in SCHEMA_VERSION_BY_CONTEXT_VERSION
            or obj["schema_version"] != SCHEMA_VERSION_BY_CONTEXT_VERSION[context_version]):
        _fail("unsupported context version")
    _sha(obj["request_fingerprint"], "request_fingerprint"); _string(obj["consumer_id"], "consumer_id", max_len=256); _snapshot(obj["snapshot"])
    if not isinstance(obj["resolved_as_of"], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", obj["resolved_as_of"]): _fail("resolved_as_of must be RFC3339 date-time")
    if obj["sensitivity"] not in SENSITIVITIES or not isinstance(obj["selected_tables"], list) or not obj["selected_tables"] or len(set(obj["selected_tables"])) != len(obj["selected_tables"]) or not all(isinstance(t, str) and t for t in obj["selected_tables"]) or obj["overall_result"] not in {"fulfilled", "unfulfilled"}: _fail("invalid context")
    if not isinstance(obj["selector_results"], list) or not obj["selector_results"]: _fail("selector results required")
    ids: set[str] = set(); required_failed = False
    for result in obj["selector_results"]:
        ro = _mapping(result, "selector result")
        if not isinstance(ro.get("selector"), dict): _fail("context selector result requires selector")
        selector = _validate_selector(ro["selector"], obj["selected_tables"], context_version=context_version)
        sid = selector["selector_id"]
        if sid in ids: _fail("duplicate context selector result")
        ids.add(sid)
        outcome = {key: value for key, value in ro.items() if key != "selector"}
        _validate_selector_outcome(outcome, selector, context_version=context_version)
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
    if _context_version_for_request(normalized_request) != context_version:
        _fail("context version does not match request negotiation")


def assemble_context(request: Any, selector_results: Any, *, resolved_as_of: str, sensitivity: str) -> dict[str, Any]:
    request = validate_resolution_request(request)
    context_version = _context_version_for_request(request)
    if sensitivity not in SENSITIVITIES: _fail("invalid sensitivity")
    if not isinstance(resolved_as_of, str): _fail("resolved_as_of must be a string")
    _validate_selector_results(selector_results, request)
    by_id = {r["selector_id"]: r for r in selector_results}
    overall = "unfulfilled" if any(s["requirement"] == "required" and by_id[s["selector_id"]]["result"] != "fulfilled" for s in request["selectors"]) else "fulfilled"
    context = {"schema_version": SCHEMA_VERSION_BY_CONTEXT_VERSION[context_version], "artifact_type": CONTEXT_ARTIFACT_TYPE,
        "protocol_version": PROTOCOL_VERSION, "context_version": context_version,
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
    return {"negotiated": {"protocol_version": PROTOCOL_VERSION, "context_version": context["context_version"]},
        "overall_result": context["overall_result"], "context_ref": deepcopy(ref),
        "selector_results": deepcopy(context["selector_results"])}


def validate_response_context(response: Any, context: Any) -> None:
    validate_context_payload(context)
    obj = _closed(response, {"negotiated", "overall_result", "context_ref", "selector_results"}, {"negotiated", "overall_result", "context_ref", "selector_results"}, "response")
    neg = _closed(obj["negotiated"], {"protocol_version", "context_version"}, {"protocol_version", "context_version"}, "negotiated")
    if neg["protocol_version"] != PROTOCOL_VERSION or neg["context_version"] != context["context_version"] or obj["overall_result"] != context["overall_result"]: _fail("response negotiation or overall result mismatch")
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
