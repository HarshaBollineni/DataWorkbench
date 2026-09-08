"""Dataset Structure Context producer and persistence internals.

The application/workspace boundary that calls this module is trusted.  DSC
``owner_id`` values are provenance and reuse namespaces only; they are not an
authorization or tenant/RBAC mechanism.  The same validation is installed on
the DSC descriptors, so generic repository writes cannot bypass this seam.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable

import system_db as db
import pandas as pd
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader
from analysis_runtime.dataset_structure_context import (
    assertion_identity_key,
    validate_assertion_payload,
    validate_context_payload,
)

if TYPE_CHECKING:
    from .repository import ArtifactSaveOutcome


_ADAPTER_VERSION = "1"
_ASSERTION_TYPE = "dataset_structure_assertion"
_CONTEXT_TYPE = "dataset_structure_context"
_PROFILE_TYPES = {"snapshot_profile", "table_profile", "schema_profile", "column_profile"}
_SCHEMA_PREDICATE = "table.physical/schema_column"
_ENTITY_PREDICATE = "table.structure/entity_binding"
_GRAIN_PREDICATE = "table.structure/row_grain"
_OBSERVER_VERSION = "profile-structure-v2"
MAX_IDENTIFIER_CANDIDATES = 32
MAX_TEMPORAL_PARTNERS = 32
MAX_COMPOSITE_CANDIDATES = 256
MAX_KEY_COLUMNS = 2
SUPPORTED_OBSERVER_PREDICATES = frozenset({_SCHEMA_PREDICATE, _ENTITY_PREDICATE, _GRAIN_PREDICATE})
_EVIDENCE_PRIMARY_ROLE = {
    "physical_profile": "profile", "reviewed_metadata": "reviewed_metadata",
    "source_constraint": "source_constraint", "structural_decision": "decision",
    "bounded_scan": "scan", "deterministic_inference": "dependency",
}


def _methodology(kind: str) -> str:
    """A producer protocol fingerprint, deliberately not consumer methodology."""
    return stable_fingerprint({
        "producer": "dataset_structure_context_adapter",
        "adapter_version": _ADAPTER_VERSION,
        "kind": kind,
        "dsc_schema_version": 1,
        "context_version": "1",
        "protocol_version": "1",
    })


def _source_refs(payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return canonical lineage refs and payload-order refs (including hashes)."""
    ordered: list[dict[str, str]] = []
    seen: dict[str, tuple[str, str]] = {}
    for evidence in payload["evidence"]:
        for source in evidence["source_refs"]:
            ref = {"artifact_id": source["artifact_id"], "role": source["role"],
                   "payload_hash": source["payload_hash"].lower()}
            previous = seen.get(ref["artifact_id"])
            pair = (ref["role"], ref["payload_hash"])
            if previous is not None:
                if previous != pair:
                    raise ValueError("a DSC source artifact cannot have inconsistent role or payload hash")
                continue
            seen[ref["artifact_id"]] = pair
            ordered.append(ref)
    lineage = sorted(
        ({"artifact_id": ref["artifact_id"], "role": ref["role"]} for ref in ordered),
        key=lambda ref: (ref["role"], ref["artifact_id"]),
    )
    return lineage, ordered


def _lineage_matches(actual: tuple[dict[str, str], ...], expected: list[dict[str, str]]) -> None:
    if list(actual) != expected:
        raise ValueError("DSC source lineage must exactly equal the payload source union")


def _load_active_same_snapshot(load: Callable[[str], tuple[Any, Any]], ref: dict[str, str],
                               snapshot: dict[str, str]) -> tuple[Any, Any]:
    metadata, source_payload = load(ref["artifact_id"])
    if metadata.status != "active":
        raise ValueError("DSC source artifact must be active")
    if metadata.asset_id != snapshot["asset_id"] or metadata.snapshot_id != snapshot["snapshot_id"]:
        raise ValueError("DSC source artifact must belong to the assertion snapshot")
    if metadata.payload_hash.lower() != ref["payload_hash"]:
        raise ValueError("DSC source payload hash does not match source reference")
    return metadata, source_payload


def _require_current_snapshot(snapshot: dict[str, str], tables: set[str]) -> None:
    """Resolve the trusted snapshot inventory; no caller-supplied scope is enough."""
    reference = SnapshotLoader().reference(snapshot["snapshot_id"])
    if reference.asset_id != snapshot["asset_id"]:
        raise ValueError("DSC snapshot asset_id does not match the active snapshot")
    unknown = sorted(tables - set(reference.tables))
    if unknown:
        raise ValueError("DSC subject or selected table is absent from the active snapshot inventory")


def _assertion_scope(payload: dict[str, Any]) -> tuple[str, str | None]:
    owners = {
        claim["decision"]["owner_id"]
        for claim in payload["claims"]
        if claim.get("decision", {}).get("scope") == "consumer_local"
    }
    if len(owners) > 1:
        raise ValueError("consumer-local DSC decisions must share one owner_id")
    return ("diagnostic_local", next(iter(owners))) if owners else ("universal", None)


def _validate_evidence_source_roles(payload: dict[str, Any],
                                    loaded: dict[str, tuple[Any, Any]]) -> None:
    """Bind DSC evidence semantics to both declared role and artifact type."""
    for evidence in payload["evidence"]:
        primary_role = _EVIDENCE_PRIMARY_ROLE[evidence["kind"]]
        roles = {ref["role"] for ref in evidence["source_refs"]}
        if primary_role not in roles:
            raise ValueError("DSC evidence is missing its primary source role")
        for ref in evidence["source_refs"]:
            artifact_type = loaded[ref["artifact_id"]][0].artifact_type
            role = ref["role"]
            if role in {"profile", "scan"} and artifact_type not in _PROFILE_TYPES:
                raise ValueError("DSC profile or scan source role requires a profile artifact")
            if role in {"reviewed_metadata", "source_constraint"} and artifact_type not in (
                    _PROFILE_TYPES | {"governance_reference"}):
                raise ValueError("DSC metadata or constraint source role has an incompatible artifact type")
            if role == "decision" and artifact_type != _ASSERTION_TYPE:
                raise ValueError("DSC decision source role requires a DSC assertion")


def _assertion_identity(payload: dict[str, Any], scope: str, owner_id: str | None,
                        ordered_refs: list[dict[str, str]]) -> dict[str, Any]:
    subject = payload["subject"]
    return {
        "artifact_type": _ASSERTION_TYPE,
        "asset_id": payload["snapshot"]["asset_id"],
        "snapshot_id": payload["snapshot"]["snapshot_id"],
        "comparison_snapshot_id": None,
        "table": subject["table"] if subject["kind"] == "table" else None,
        "feature": None, "features": [],
        "population_fingerprint": payload["dependency_fingerprint"],
        "target_fingerprint": None,
        "methodology_fingerprint": _methodology("assertion"),
        "scope": scope, "owner_id": owner_id, "workflow_id": None,
        "identity_inputs": {
            "assertion_locator": assertion_identity_key(payload),
            "dependency_fingerprint": payload["dependency_fingerprint"],
            "versions": {"schema_version": 1, "context_version": "1", "adapter_version": _ADAPTER_VERSION},
            "ordered_source_refs": ordered_refs,
        },
    }


def _require_exact_identity(identity: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if identity.get(key) != value:
            raise ValueError(f"DSC identity field {key!r} must be adapter-derived")


def validate_assertion_write(payload: Any, identity: dict[str, Any],
                             refs: tuple[dict[str, str], ...],
                             load: Callable[[str], tuple[Any, Any]]) -> None:
    """Descriptor hook: validate trusted source evidence and all derived identity."""
    validate_assertion_payload(payload)
    subject = payload["subject"]
    _require_current_snapshot(
        payload["snapshot"],
        {subject["table"]} if subject["kind"] == "table"
        else {subject["from_table"], subject["to_table"]},
    )
    lineage, ordered = _source_refs(payload)
    _lineage_matches(refs, lineage)
    snapshot = payload["snapshot"]
    loaded: dict[str, tuple[Any, Any]] = {}
    for ref in ordered:
        loaded[ref["artifact_id"]] = _load_active_same_snapshot(load, ref, snapshot)
    _validate_evidence_source_roles(payload, loaded)
    scope, owner = _assertion_scope(payload)
    _require_exact_identity(identity, _assertion_identity(payload, scope, owner, ordered))

    # A promotion refers to an opaque decision ID held by an earlier DSC
    # assertion artifact, not a decision declared in this same payload.
    promotions = [claim["decision"]["promotion_of"] for claim in payload["claims"]
                  if claim.get("decision", {}).get("action") == "promote"]
    if promotions:
        decision_sources = [ref for ref in ordered if ref["role"] == "decision"]
        decisions: set[str] = set()
        for ref in decision_sources:
            metadata, source_payload = loaded[ref["artifact_id"]]
            if metadata.artifact_type != _ASSERTION_TYPE:
                raise ValueError("DSC promotion decision sources must be DSC assertions")
            validate_assertion_payload(source_payload)
            if source_payload["snapshot"] != snapshot:
                raise ValueError("DSC promotion decision source has a different snapshot")
            decisions.update(
                claim.get("decision", {}).get("decision_id")
                for claim in source_payload["claims"]
                if claim.get("decision", {}).get("decision_id")
            )
        if not all(value in decisions for value in promotions):
            raise ValueError("DSC promotion requires a decision-role source containing promotion_of")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("DSC resolved_as_of must be a valid timestamp") from exc


def _context_pins(payload: dict[str, Any], load: Callable[[str], tuple[Any, Any]]) -> tuple[list[dict[str, str]], list[dict[str, Any]], int]:
    snapshot = payload["snapshot"]
    resolved_as_of = _parse_time(payload["resolved_as_of"])
    ordered: list[dict[str, Any]] = []
    lineage_by_id: dict[str, dict[str, str]] = {}
    max_sensitivity = -1
    for result in payload["selector_results"]:
        if result["result"] != "fulfilled":
            continue
        selector = result["selector"]
        for pin in result["pins"]:
            if pin["reuse_disposition"] not in {"fresh", "exact_reused"}:
                raise ValueError("only fresh or exact_reused DSC pins may fulfill a v1 context")
            metadata, assertion = load(pin["artifact_id"])
            if metadata.status != "active" or metadata.artifact_type != _ASSERTION_TYPE:
                raise ValueError("DSC context pins must reference active DSC assertions")
            if metadata.scope == "universal":
                if metadata.owner_id is not None:
                    raise ValueError("universal DSC assertion pins must not have an owner_id")
            elif metadata.scope == "diagnostic_local":
                if metadata.owner_id != payload["consumer_id"]:
                    raise ValueError("consumer-local DSC assertion pin belongs to another consumer")
            else:
                raise ValueError("DSC context pin has an unsupported assertion reuse namespace")
            if (metadata.asset_id != snapshot["asset_id"] or metadata.snapshot_id != snapshot["snapshot_id"]
                    or metadata.payload_hash.lower() != pin["payload_hash"].lower()):
                raise ValueError("DSC context pin metadata does not match its snapshot or hash")
            validate_assertion_payload(assertion)
            if assertion["snapshot"] != snapshot:
                raise ValueError("DSC context pin assertion has a different snapshot")
            if assertion["assertion_id"] != pin["assertion_id"] or assertion["resolution"]["status"] != pin["resolution"]:
                raise ValueError("DSC context pin assertion ID or resolution does not match")
            if assertion["subject"] != selector["subject"] or assertion["predicate"] != selector["predicate"]:
                raise ValueError("DSC context pin does not satisfy its persisted selector")
            if metadata.created_at and _parse_time(metadata.created_at) > resolved_as_of:
                raise ValueError("DSC assertion was created after context resolved_as_of")
            max_sensitivity = max(max_sensitivity, ("external_safe", "internal", "confidential").index(assertion["sensitivity"]))
            item = {"selector_id": result["selector_id"], **pin}
            ordered.append(item)
            old = lineage_by_id.get(pin["artifact_id"])
            line = {"artifact_id": pin["artifact_id"], "role": "assertion_pin"}
            if old is not None and old != line:
                raise ValueError("DSC context pin lineage is inconsistent")
            lineage_by_id[pin["artifact_id"]] = line
    return sorted(lineage_by_id.values(), key=lambda ref: (ref["role"], ref["artifact_id"])), ordered, max_sensitivity


def _context_identity(payload: dict[str, Any], ordered_pins: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = payload["snapshot"]
    return {
        "artifact_type": _CONTEXT_TYPE, "asset_id": snapshot["asset_id"], "snapshot_id": snapshot["snapshot_id"],
        "comparison_snapshot_id": None, "table": None, "feature": None, "features": [],
        "population_fingerprint": stable_fingerprint({"snapshot": snapshot, "selected_tables": payload["selected_tables"]}),
        "target_fingerprint": None, "methodology_fingerprint": _methodology("context"),
        "scope": "diagnostic_local", "owner_id": payload["consumer_id"], "workflow_id": None,
        "identity_inputs": {
            "request_fingerprint": payload["request_fingerprint"], "resolved_as_of": payload["resolved_as_of"],
            "versions": {"schema_version": 1, "protocol_version": "1", "context_version": "1", "adapter_version": _ADAPTER_VERSION},
            "ordered_selectors": [result["selector"] for result in payload["selector_results"]],
            "ordered_pins": ordered_pins,
        },
    }


def validate_context_write(payload: Any, identity: dict[str, Any],
                           refs: tuple[dict[str, str], ...],
                           load: Callable[[str], tuple[Any, Any]]) -> None:
    validate_context_payload(payload)
    context_tables = set(payload["selected_tables"])
    for result in payload["selector_results"]:
        subject = result["selector"]["subject"]
        context_tables.update(
            {subject["table"]} if subject["kind"] == "table"
            else {subject["from_table"], subject["to_table"]}
        )
    _require_current_snapshot(payload["snapshot"], context_tables)
    lineage, ordered_pins, maximum = _context_pins(payload, load)
    _lineage_matches(refs, lineage)
    expected_sensitivity = ("external_safe", "internal", "confidential")[max(maximum, 0)]
    if payload["sensitivity"] != expected_sensitivity:
        raise ValueError("DSC context sensitivity must equal maximum pinned assertion sensitivity")
    _require_exact_identity(identity, _context_identity(payload, ordered_pins))


def save_dataset_structure_assertion(repo: Any, payload: Any, *, created_by: str | None = None,
                                     version: bool = False) -> "ArtifactSaveOutcome":
    """Persist a DSC assertion using only adapter-derived AAR metadata."""
    validate_assertion_payload(payload)
    lineage, ordered = _source_refs(payload)
    scope, owner = _assertion_scope(payload)
    expected = _assertion_identity(payload, scope, owner, ordered)
    return repo.save(payload, version=version, artifact_schema_version=1,
                     source_artifacts=tuple(lineage), created_by=created_by, **expected)


def save_dataset_structure_context(repo: Any, payload: Any, *, consumer_id: str,
                                   created_by: str | None = None, version: bool = False) -> "ArtifactSaveOutcome":
    """Persist a consumer-local context; consumer_id is a reuse namespace."""
    validate_context_payload(payload)
    if payload["consumer_id"] != consumer_id:
        raise ValueError("DSC context payload consumer_id must match the persistence consumer_id")
    lineage, ordered_pins, _maximum = _context_pins(payload, repo.get)
    expected = _context_identity(payload, ordered_pins)
    return repo.save(payload, version=version, artifact_schema_version=1,
                     source_artifacts=tuple(lineage), created_by=created_by, **expected)


class DatasetStructureObservationError(ValueError):
    """A deliberately non-diagnostic producer failure for resolver mapping."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _observation_failure(reason_code: str) -> None:
    raise DatasetStructureObservationError(reason_code)


def _sha_id(prefix: str, value: Any) -> str:
    """Stable protocol IDs, derived entirely from canonical JSON inputs."""
    return f"{prefix}_{stable_fingerprint(value)}"


def _source_sensitivity(artifact_type: str) -> str:
    # Delayed import avoids the type-registry/DSC module initialization cycle.
    from .types import get_artifact_type
    descriptor = get_artifact_type(artifact_type)
    return descriptor.sensitivity if descriptor is not None else "internal"


def _exact_count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    return value


def _active_single(repo: Any, *, artifact_type: str, snapshot_id: str,
                   table: str, column: str | None = None) -> tuple[Any, dict[str, Any]]:
    """Load one active, hash-verified profile and reject ambiguity explicitly."""
    candidates = [item for item in repo.list(snapshot_id=snapshot_id,
                                               artifact_type=artifact_type, status="active")
                  if item.identity.get("table") == table
                  and (column is None or item.feature == column)]
    if not candidates:
        _observation_failure("DSC_R_SOURCE_MISSING")
    if len(candidates) != 1:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    metadata = candidates[0]
    try:
        checked, payload = repo.get(metadata.artifact_id)
    except Exception as exc:
        # Repository integrity failures are intentionally reduced to a stable
        # protocol code: callers must not receive file or artifact details.
        if exc.__class__.__name__ == "ArtifactIntegrityError":
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    if checked.status != "active" or checked.integrity_status != "verified" or not isinstance(payload, dict):
        _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
    return checked, payload


def _profiles_for_table(repo: Any, snapshot_id: str, asset_id: str,
                        table: str) -> tuple[Any, dict[str, Any], list[tuple[Any, dict[str, Any]]]]:
    """Return one complete, exact, inventory-consistent retained profile set."""
    table_metadata, table_payload = _active_single(
        repo, artifact_type="table_profile", snapshot_id=snapshot_id, table=table)
    if (table_metadata.asset_id != asset_id or table_metadata.snapshot_id != snapshot_id
            or table_metadata.identity.get("table") != table
            or table_payload.get("table") != table):
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    columns = table_payload.get("columns")
    if (not isinstance(columns, list) or not columns or not all(isinstance(column, str) and column for column in columns)
            or len(set(columns)) != len(columns) or table_payload.get("column_count") != len(columns)):
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    table_rows = _exact_count(table_payload.get("row_count"))

    # ``system_db.query*`` names its first argument ``table_name``, so a data
    # column with that name cannot also be passed as a keyword filter.  Scope
    # by snapshot in SQL and apply the table filter to the bounded inventory
    # rows here.
    item_table = next((row for row in db.query("dq_item_tables", item_id=snapshot_id)
                       if row.get("table_name") == table), None)
    inventory = [row for row in db.query(
        "variable_inventory", item_id=snapshot_id, order_by="column_name")
        if row.get("table_name") == table]
    inventory_by_column = {row.get("column_name"): row for row in inventory}
    if (item_table is None or set(item_table.get("columns") or []) != set(columns)
            or set(inventory_by_column) != set(columns) or len(inventory_by_column) != len(inventory)):
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    if _exact_count(item_table.get("row_count")) != table_rows:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")

    profiles: list[tuple[Any, dict[str, Any]]] = []
    for column in sorted(columns):
        metadata, payload = _active_single(repo, artifact_type="column_profile",
                                           snapshot_id=snapshot_id, table=table, column=column)
        if (metadata.asset_id != asset_id or metadata.snapshot_id != snapshot_id
                or metadata.identity.get("table") != table or metadata.feature != column
                or not isinstance(payload.get("data_type"), str) or not payload["data_type"]
                or payload["data_type"] != inventory_by_column[column].get("data_type")
                or payload.get("calculation_method") != "exact"):
            _observation_failure("DSC_R_INSUFFICIENT_BASIS")
        total = _exact_count(payload.get("total_count"))
        non_null = _exact_count(payload.get("non_null_count"))
        physical_null = _exact_count(payload.get("physical_null_count", payload.get("null_count")))
        nulls = _exact_count(payload.get("null_count"))
        special = (_exact_count(payload.get("special_value_row_count", 0))
                   if payload.get("special_values_confirmed") else 0)
        regular = _exact_count(payload.get("regular_value_count", non_null - special))
        if (total != table_rows or nulls != physical_null or non_null != special + regular
                or total != physical_null + special + regular):
            _observation_failure("DSC_R_INSUFFICIENT_BASIS")
        profiles.append((metadata, payload))

    expected_sources = {metadata.artifact_id for metadata, _payload in profiles}
    if set(table_metadata.source_artifact_ids) != expected_sources:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    return table_metadata, table_payload, profiles


def _schema_assertion_payload(*, asset_id: str, snapshot_id: str, table: str,
                              table_metadata: Any,
                              profile_metadata: Any, profile: dict[str, Any]) -> dict[str, Any]:
    """Build the atomic schema assertion for one qualified table column."""
    column = profile_metadata.feature
    dependency_fingerprint = stable_fingerprint({
        "observer_version": _OBSERVER_VERSION,
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "table": table,
        "table_profile": {"artifact_id": table_metadata.artifact_id,
                          "payload_hash": table_metadata.payload_hash},
        "column_profile": {"column": column, "artifact_id": profile_metadata.artifact_id,
                           "payload_hash": profile_metadata.payload_hash},
    })
    total = int(profile["total_count"])
    physical_null = int(profile.get("physical_null_count", profile["null_count"]))
    confirmed_special = (int(profile.get("special_value_row_count", 0))
                         if profile.get("special_values_confirmed") else 0)
    regular = int(profile.get("regular_value_count", int(profile["non_null_count"]) - confirmed_special))
    source_sensitivity = max((_source_sensitivity(profile_metadata.artifact_type),
                              _source_sensitivity(table_metadata.artifact_type)),
                             key=lambda value: ("external_safe", "internal", "confidential").index(value))
    instance_key = f"column:{column}"
    assertion_id = _sha_id("dsca", {
        "schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _SCHEMA_PREDICATE,
        "instance_key": instance_key,
    })
    value = {"kind": "schema_column", "column": {"table": table, "column": column},
             "data_type": profile["data_type"], "nullable": physical_null > 0}
    source_refs = [
        {"artifact_id": profile_metadata.artifact_id, "role": "profile",
         "payload_hash": profile_metadata.payload_hash},
        {"artifact_id": table_metadata.artifact_id, "role": "dependency",
         "payload_hash": table_metadata.payload_hash},
    ]
    basis = {"population": table, "total_count": total,
             "exclusions": {"physical_null": physical_null, "confirmed_special": confirmed_special,
                            "parse_failure": 0}, "usable_count": regular, "computation": "exact"}
    measurements = [{"name": "physical_null_rows", "count": physical_null},
                    {"name": "confirmed_special_rows", "count": confirmed_special}]
    evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "physical_profile",
                                    "source_refs": source_refs, "basis": basis,
                                    "measurements": measurements})
    claim_id = _sha_id("dscc", {"assertion_id": assertion_id, "authority": "verified_observation",
                                 "value": value})
    return {
        "schema_version": 1, "artifact_type": _ASSERTION_TYPE, "assertion_id": assertion_id,
        "context_version": "1", "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _SCHEMA_PREDICATE,
        "instance_key": instance_key, "multiplicity": "keyed_set",
        "claims": [{
            "claim_id": claim_id, "authority": "verified_observation",
            "value": value,
            "evidence_ids": [evidence_id],
        }],
        "evidence": [{
            "evidence_id": evidence_id, "kind": "physical_profile",
            "source_refs": source_refs, "basis": basis, "measurements": measurements,
            "sensitivity": source_sensitivity,
        }], "conflicts": [],
        "resolution": {"status": "observed", "effective_claim_ids": [claim_id],
                       "reason_codes": [], "conflict_ids": []},
        "dependency_fingerprint": dependency_fingerprint, "sensitivity": source_sensitivity,
    }


def _profile_counts(profile: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Exact aggregate basis: total, physical null, confirmed special, usable, distinct."""
    total = _exact_count(profile.get("total_count"))
    physical = _exact_count(profile.get("physical_null_count", profile.get("null_count")))
    special = (_exact_count(profile.get("special_value_row_count", 0))
               if profile.get("special_values_confirmed") else 0)
    non_null = _exact_count(profile.get("non_null_count"))
    usable = _exact_count(profile.get("regular_value_count", non_null - special))
    distinct = _exact_count(profile.get("distinct_count"))
    if non_null != special + usable or total != physical + special + usable or distinct > usable:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    return total, physical, special, usable, distinct


def _structural_assertion(*, asset_id: str, snapshot_id: str, table: str, predicate: str,
                          instance_key: str, claims: list[dict[str, Any]], evidence: list[dict[str, Any]],
                          dependency: dict[str, Any], status: str, effective: list[str],
                          reasons: list[str], sensitivity: str) -> dict[str, Any]:
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": predicate,
        "instance_key": instance_key})
    # Callers use the locator when forming claim/evidence IDs; retain the
    # assertion builder as the single closed-shape owner.
    return {"schema_version": 1, "artifact_type": _ASSERTION_TYPE, "assertion_id": assertion_id,
        "context_version": "1", "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": predicate,
        "instance_key": instance_key,
        "multiplicity": "single" if predicate == _GRAIN_PREDICATE else "keyed_set",
        "claims": claims, "evidence": evidence, "conflicts": [],
        "resolution": {"status": status, "effective_claim_ids": effective,
                       "reason_codes": reasons, "conflict_ids": []},
        "dependency_fingerprint": stable_fingerprint(dependency), "sensitivity": sensitivity}


def _entity_payload(asset_id: str, snapshot_id: str, table: str, table_metadata: Any,
                    metadata: Any, profile: dict[str, Any]) -> dict[str, Any]:
    column = metadata.feature
    instance_key = f"column:{column}"
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _ENTITY_PREDICATE,
        "instance_key": instance_key})
    total, physical, special, usable, distinct = _profile_counts(profile)
    value = {"kind": "entity_binding", "columns": [{"table": table, "column": column}]}
    refs = [{"artifact_id": metadata.artifact_id, "role": "reviewed_metadata", "payload_hash": metadata.payload_hash},
            {"artifact_id": table_metadata.artifact_id, "role": "dependency", "payload_hash": table_metadata.payload_hash}]
    basis = {"population": table, "total_count": total,
             "exclusions": {"physical_null": physical, "confirmed_special": special, "parse_failure": 0},
             "usable_count": usable, "computation": "exact"}
    measurements = [{"name": "distinct_key_count", "count": distinct},
                    {"name": "duplicate_excess_rows", "count": usable - distinct}]
    evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "reviewed_metadata",
                                    "source_refs": refs, "basis": basis, "measurements": measurements})
    claim_id = _sha_id("dscc", {"assertion_id": assertion_id, "authority": "source_reviewed_metadata", "value": value})
    sensitivity = max((_source_sensitivity(metadata.artifact_type), _source_sensitivity(table_metadata.artifact_type)),
                      key=lambda value: ("external_safe", "internal", "confidential").index(value))
    return {"schema_version": 1, "artifact_type": _ASSERTION_TYPE, "assertion_id": assertion_id,
        "context_version": "1", "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _ENTITY_PREDICATE,
        "instance_key": instance_key, "multiplicity": "keyed_set",
        "claims": [{"claim_id": claim_id, "authority": "source_reviewed_metadata", "value": value,
                    "evidence_ids": [evidence_id]}],
        "evidence": [{"evidence_id": evidence_id, "kind": "reviewed_metadata", "source_refs": refs,
                      "basis": basis, "measurements": measurements, "sensitivity": sensitivity}],
        "conflicts": [], "resolution": {"status": "proposed", "effective_claim_ids": [claim_id],
                                             "reason_codes": [], "conflict_ids": []},
        "dependency_fingerprint": stable_fingerprint({"observer_version": _OBSERVER_VERSION,
            "table_profile": [table_metadata.artifact_id, table_metadata.payload_hash],
            "column_profile": [metadata.artifact_id, metadata.payload_hash]}), "sensitivity": sensitivity}


def _special_key(value: Any, *, numeric: bool = False) -> str:
    """Match confirmed specials with ingestion's string/numeric equivalence."""
    if numeric and isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    try:
        if not numeric:
            return text
        number = float(text)
        if number == number and number not in (float("inf"), float("-inf")):
            return str(int(number)) if number.is_integer() else str(number)
    except (TypeError, ValueError):
        pass
    return text


def _grain_plan(table_metadata: Any, candidates: list[tuple[Any, dict[str, Any]]],
                temporal: list[tuple[Any, dict[str, Any]]]) -> tuple[bool, bool, list[dict[str, str]], dict[str, Any]]:
    """The complete pre-scan identity plan shared by observe and reuse."""
    capped = (len(candidates) > MAX_IDENTIFIER_CANDIDATES or len(temporal) > MAX_TEMPORAL_PARTNERS
              or len(candidates) * len(temporal) > MAX_COMPOSITE_CANDIDATES)
    pair_scan = bool(candidates and temporal and not capped)
    profiles = sorted({meta.feature: meta for meta, _ in candidates + temporal}.values(), key=lambda meta: meta.feature)
    refs = [{"artifact_id": table_metadata.artifact_id, "role": "scan" if pair_scan else "dependency",
             "payload_hash": table_metadata.payload_hash}]
    refs += [{"artifact_id": meta.artifact_id, "role": "dependency", "payload_hash": meta.payload_hash}
             for meta in profiles]
    dependency = {"observer_version": _OBSERVER_VERSION,
        "table_profile": [table_metadata.artifact_id, table_metadata.payload_hash],
        "candidates": [[meta.artifact_id, meta.payload_hash] for meta, _ in candidates],
        "temporal_partners": [[meta.artifact_id, meta.payload_hash] for meta, _ in temporal],
        "policy": {"max_identifier_candidates": MAX_IDENTIFIER_CANDIDATES,
                    "max_temporal_partners": MAX_TEMPORAL_PARTNERS,
                    "max_composite_candidates": MAX_COMPOSITE_CANDIDATES,
                    "max_key_columns": MAX_KEY_COLUMNS},
        "phase": "reviewed_identifier_temporal_pairs"}
    return capped, pair_scan, refs, dependency


def _grain_payload(asset_id: str, snapshot_id: str, table: str, table_metadata: Any, table_payload: dict[str, Any],
                   candidates: list[tuple[Any, dict[str, Any]]], temporal: list[tuple[Any, dict[str, Any]]],
                   snapshot_loader: SnapshotLoader | None = None) -> dict[str, Any]:
    instance_key = "primary"
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _GRAIN_PREDICATE,
        "instance_key": instance_key})
    claims, evidence, sensitivities, unique_ids = [], [], [_source_sensitivity(table_metadata.artifact_type)], []
    capped, pair_scan, stable_refs, dependency = _grain_plan(table_metadata, candidates, temporal)
    # All grain evidence uses one stable role map, even when a column is in
    # more than one candidate.  This is required by the source-ref validator.
    profiles = {meta.feature: (meta, profile) for meta, profile in candidates + temporal}
    for metadata, profile in ([] if capped else candidates):
        total, physical, special, usable, distinct = _profile_counts(profile)
        value = {"kind": "row_grain", "key_columns": [{"table": table, "column": metadata.feature}]}
        refs = stable_refs
        basis = {"population": table, "total_count": total,
                 "exclusions": {"physical_null": physical, "confirmed_special": special, "parse_failure": 0},
                 "usable_count": usable, "computation": "exact"}
        measurements = [{"name": "distinct_key_count", "count": distinct},
                        {"name": "duplicate_excess_rows", "count": usable - distinct}]
        evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "deterministic_inference",
                                        "value": value, "source_refs": refs, "basis": basis,
                                        "measurements": measurements})
        claim_id = _sha_id("dscc", {"assertion_id": assertion_id, "authority": "verified_observation", "value": value})
        sensitivity = max((_source_sensitivity(metadata.artifact_type), _source_sensitivity(table_metadata.artifact_type)),
                          key=lambda value: ("external_safe", "internal", "confidential").index(value))
        evidence.append({"evidence_id": evidence_id, "kind": "deterministic_inference", "source_refs": refs,
                         "basis": basis, "measurements": measurements, "sensitivity": sensitivity})
        sensitivities.append(sensitivity)
        if total > 0 and physical == special == 0 and distinct == total:
            claims.append({"claim_id": claim_id, "authority": "verified_observation", "value": value,
                           "evidence_ids": [evidence_id]})
            unique_ids.append(claim_id)
    # Composite candidates are deliberately closed: no arbitrary pair search,
    # no temporal parsing and exactly one all-row read when pairs exist.
    pair_ids: list[str] = []
    if pair_scan:
        pair_columns = sorted({meta.feature for meta, _ in candidates + temporal})
        try:
            frame = (snapshot_loader or SnapshotLoader()).load_table(snapshot_id, table, columns=pair_columns)
        except Exception:
            _observation_failure("DSC_R_INSUFFICIENT_BASIS")
        if list(frame.columns) != pair_columns or len(frame.index) != _exact_count(table_payload.get("row_count")):
            _observation_failure("DSC_R_INSUFFICIENT_BASIS")
        for identifier, id_profile in candidates:
            for time_meta, time_profile in temporal:
                columns = [identifier.feature, time_meta.feature]
                total = len(frame.index); physical = special = 0; tuples = []
                specials = {column: {_special_key(value, numeric=(pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]))) for value in (profile.get("special_values") or [])}
                            for column, profile in ((identifier.feature, id_profile), (time_meta.feature, time_profile))
                            if profile.get("special_values_confirmed")}
                for values in frame[columns].itertuples(index=False, name=None):
                    if any(value is None or bool(pd.isna(value)) for value in values):
                        physical += 1; continue
                    if any(_special_key(value, numeric=(pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]))) in specials.get(column, set()) for column, value in zip(columns, values)):
                        special += 1; continue
                    tuples.append(tuple(values))
                usable, distinct = len(tuples), len(set(tuples))
                value = {"kind": "row_grain", "key_columns": [{"table": table, "column": col} for col in columns]}
                basis = {"population": table, "total_count": total, "exclusions": {"physical_null": physical, "confirmed_special": special, "parse_failure": 0}, "usable_count": usable, "computation": "bounded_scan"}
                measurements = [{"name": "distinct_key_count", "count": distinct}, {"name": "duplicate_excess_rows", "count": usable - distinct}]
                evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "bounded_scan",
                                                "value": value, "source_refs": stable_refs,
                                                "basis": basis, "measurements": measurements})
                claim_id = _sha_id("dscc", {"assertion_id": assertion_id, "authority": "verified_observation", "value": value})
                sensitivity = max((_source_sensitivity(meta.artifact_type) for meta, _ in profiles.values()), key=lambda value: ("external_safe", "internal", "confidential").index(value)); sensitivities.append(sensitivity)
                evidence.append({"evidence_id": evidence_id, "kind": "bounded_scan", "source_refs": stable_refs, "basis": basis, "measurements": measurements, "sensitivity": sensitivity})
                if total > 0 and physical == special == 0 and distinct == total:
                    claims.append({"claim_id": claim_id, "authority": "verified_observation", "value": value, "evidence_ids": [evidence_id]}); pair_ids.append(claim_id)
    unique_ids.extend(pair_ids)
    if len(unique_ids) == 1:
        status, effective, reasons = "observed", unique_ids, []
    elif len(unique_ids) > 1:
        status, effective, reasons = "unknown", [], ["DSC_R_AMBIGUOUS_CANDIDATES"]
    else:
        status, effective, reasons = "unknown", [], ["DSC_R_INSUFFICIENT_BASIS"]
    if not evidence:
        total = _exact_count(table_payload.get("row_count"))
        basis = {"population": table, "total_count": total,
                 "exclusions": {"physical_null": 0, "confirmed_special": 0, "parse_failure": 0},
                 "usable_count": total, "computation": "exact"}
        measurements = [
            {"name": "reviewed_identifier_candidate_count", "count": len(candidates)},
            {"name": "reviewed_temporal_partner_count", "count": len(temporal)},
            {"name": "candidate_cap_exceeded", "count": int(capped)},
        ]
        refs = stable_refs
        evidence.append({"evidence_id": _sha_id("dsce", {"assertion_id": assertion_id,
                         "kind": "deterministic_inference", "source_refs": refs, "basis": basis,
                         "measurements": measurements}), "kind": "deterministic_inference",
                         "source_refs": refs, "basis": basis, "measurements": measurements,
                         "sensitivity": _source_sensitivity(table_metadata.artifact_type)})
    return {"schema_version": 1, "artifact_type": _ASSERTION_TYPE, "assertion_id": assertion_id,
        "context_version": "1", "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _GRAIN_PREDICATE,
        "instance_key": instance_key, "multiplicity": "single", "claims": claims, "evidence": evidence,
        "conflicts": [], "resolution": {"status": status, "effective_claim_ids": effective,
                                             "reason_codes": reasons, "conflict_ids": []},
        "dependency_fingerprint": stable_fingerprint(dependency),
        "sensitivity": (max(sensitivities, key=lambda value: ("external_safe", "internal", "confidential").index(value))
                        if evidence else "external_safe")}


def observe_dataset_structure(repo: Any, snapshot_id: str, *, tables: Iterable[str],
                              predicates: Iterable[str] = (_SCHEMA_PREDICATE,),
                              snapshot_loader: SnapshotLoader | None = None,
                              created_by: str | None = None) -> tuple["ArtifactSaveOutcome", ...]:
    """Materialise only universal observed physical-schema assertions from profiles.

    Schema/entity and singleton grain evidence uses retained profiles only.
    A closed reviewed Identifier x Date/Period candidate set may make one
    all-row bounded ``load_table`` call for composite tuple uniqueness; it
    emits aggregates only and never copies source values or distributions.
    """
    requested_predicates = set(predicates)
    if not requested_predicates <= SUPPORTED_OBSERVER_PREDICATES:
        raise ValueError("this DSC observer supports only registered profile-backed predicates")
    if not requested_predicates:
        return ()
    selected_tables = sorted(set(tables))
    if not selected_tables or not all(isinstance(table, str) and table for table in selected_tables):
        raise ValueError("tables must be a non-empty collection of table names")
    try:
        reference = (snapshot_loader or SnapshotLoader()).reference(snapshot_id)
    except Exception:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    if not set(selected_tables) <= set(reference.tables):
        _observation_failure("DSC_R_SOURCE_MISSING")
    outcomes = []
    for table in selected_tables:
        table_metadata, table_payload, profiles = _profiles_for_table(
            repo, snapshot_id, reference.asset_id, table)
        payloads: list[dict[str, Any]] = []
        # A current grain assertion is immutable evidence for this immutable
        # snapshot.  Verify its source projection before any bounded scan.
        reuse_grain = None
        if _GRAIN_PREDICATE in requested_predicates:
            reuse_grain = _matching_supported_assertions(
                repo, snapshot_id=snapshot_id, asset_id=reference.asset_id, table=table,
                predicate=_GRAIN_PREDICATE)
            if reuse_grain is not None:
                for _metadata, retained_payload in reuse_grain:
                    outcomes.append(save_dataset_structure_assertion(
                        repo, retained_payload, created_by=created_by))
        if _SCHEMA_PREDICATE in requested_predicates:
            payloads.extend(_schema_assertion_payload(asset_id=reference.asset_id, snapshot_id=snapshot_id,
                table=table, table_metadata=table_metadata, profile_metadata=metadata, profile=profile)
                for metadata, profile in profiles)
        reviewed_ids = [(metadata, profile) for metadata, profile in profiles
                        if profile.get("metadata_reviewed") is True
                        and str(profile.get("role") or "").strip().lower() == "identifier"]
        reviewed_temporal = [(metadata, profile) for metadata, profile in profiles
                             if profile.get("metadata_reviewed") is True
                             and str(profile.get("role") or "").strip().lower() in {"date", "period"}]
        if _ENTITY_PREDICATE in requested_predicates:
            payloads.extend(_entity_payload(reference.asset_id, snapshot_id, table, table_metadata, metadata, profile)
                            for metadata, profile in reviewed_ids)
        if _GRAIN_PREDICATE in requested_predicates and reuse_grain is None:
            payloads.append(_grain_payload(reference.asset_id, snapshot_id, table, table_metadata, table_payload,
                                            reviewed_ids, reviewed_temporal, snapshot_loader))
        for payload in payloads:
            prior = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=reference.asset_id,
                                                 table=table, predicate=payload["predicate"],
                                                 instance_key=payload["instance_key"])
            if len(prior) > 1: _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
            outcome = save_dataset_structure_assertion(repo, payload, created_by=created_by)
            if prior and prior[0][1] != payload and prior[0][0].artifact_id != outcome.artifact.artifact_id:
                repo.supersede(prior[0][0].artifact_id, by_artifact_id=outcome.artifact.artifact_id, actor=created_by)
            outcomes.append(outcome)
    return tuple(outcomes)


def _producer_locator_candidates(repo: Any, *, snapshot_id: str, asset_id: str,
                                 table: str, predicate: str | None = None,
                                 instance_key: str | None = None) -> list[tuple[Any, dict[str, Any]]]:
    """Return atomic producer candidates without treating arbitrary claims as ours."""
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type=_ASSERTION_TYPE, status="active"):
        if metadata.asset_id != asset_id or metadata.scope != "universal":
            continue
        try:
            _checked, payload = repo.get(metadata.artifact_id)
        except Exception:
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
        if (isinstance(payload, dict) and (predicate is None or payload.get("predicate") == predicate)
                and payload.get("subject") == {"kind": "table", "table": table}
                and (instance_key is None or payload.get("instance_key") == instance_key)):
            candidates.append((metadata, payload))
    return candidates


def _matching_supported_assertions(repo: Any, *, snapshot_id: str, asset_id: str,
                                  table: str, predicate: str) -> list[tuple[Any, dict[str, Any]]] | None:
    """Reuse only the complete current projection; optional never observes."""
    # An optional selector with no materialized assertion must not require
    # profiles (or turn their absence into a source error).  Only validate and
    # recompute dependencies after there is something eligible to reuse.
    actual = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                          table=table, predicate=predicate)
    if not actual:
        return None
    table_metadata, table_payload, profiles = _profiles_for_table(repo, snapshot_id, asset_id, table)
    if predicate == _SCHEMA_PREDICATE:
        expected = [_schema_assertion_payload(asset_id=asset_id, snapshot_id=snapshot_id, table=table,
            table_metadata=table_metadata, profile_metadata=metadata, profile=profile) for metadata, profile in profiles]
    else:
        reviewed = [(metadata, profile) for metadata, profile in profiles if profile.get("metadata_reviewed") is True
                    and str(profile.get("role") or "").strip().lower() == "identifier"]
        temporal = [(metadata, profile) for metadata, profile in profiles if profile.get("metadata_reviewed") is True
                    and str(profile.get("role") or "").strip().lower() in {"date", "period"}]
        if predicate == _ENTITY_PREDICATE:
            expected = [_entity_payload(asset_id, snapshot_id, table, table_metadata, metadata, profile)
                        for metadata, profile in reviewed]
        elif predicate == _GRAIN_PREDICATE:
            _capped, _pair_scan, plan_refs, dependency = _grain_plan(table_metadata, reviewed, temporal)
            expected_refs = {(ref["artifact_id"], ref["role"], ref["payload_hash"]) for ref in plan_refs}
            if len(actual) != 1:
                return None
            payload = actual[0][1]
            actual_refs = {(ref["artifact_id"], ref["role"], ref["payload_hash"])
                           for evidence in payload.get("evidence", []) for ref in evidence.get("source_refs", [])}
            # The fingerprint contains the observer/policy/candidate ordering;
            # source refs prove that the retained immutable profile projection
            # is still current without repeating a bounded scan.
            return actual if (actual_refs == expected_refs
                              and payload.get("dependency_fingerprint") == stable_fingerprint(dependency)) else None
        else:
            return None
    if not expected:
        return None
    by_key: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
    for item in actual: by_key.setdefault(item[1].get("instance_key", ""), []).append(item)
    expected_by_key = {payload["instance_key"]: payload for payload in expected}
    if any(len(items) > 1 for items in by_key.values()): _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    if set(by_key) != set(expected_by_key): return None
    ordered = [by_key[key][0] for key in sorted(expected_by_key)]
    return ordered if all(payload == expected_by_key[payload["instance_key"]] for _metadata, payload in ordered) else None
