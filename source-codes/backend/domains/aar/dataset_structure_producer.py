"""Dataset Structure Context producer and persistence internals.

The application/workspace boundary that calls this module is trusted.  DSC
``owner_id`` values are provenance and reuse namespaces only; they are not an
authorization or tenant/RBAC mechanism.  The same validation is installed on
the DSC descriptors, so generic repository writes cannot bypass this seam.
"""
from __future__ import annotations

from datetime import date, datetime
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import re
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
from domains.aar.cadence_provenance import validate_cadence_payload

if TYPE_CHECKING:
    from .repository import ArtifactSaveOutcome


_ADAPTER_VERSION = "1"
_ASSERTION_TYPE = "dataset_structure_assertion"
_VERIFIED_AUTHORITY_TOKEN: ContextVar[object | None] = ContextVar("dsc_verified_authority_token", default=None)
_AUTHORITY_CAPABILITY = object()


@contextmanager
def _verified_authority_write_scope():
    """Private capability used only by the Slice-3 decision UoW.

    Generic producers never enter this scope, so a direct repository save of a
    v2 authority assertion cannot bypass the decision service's fresh-pin
    verification and atomic workflow callback.
    """
    reset = _VERIFIED_AUTHORITY_TOKEN.set(_AUTHORITY_CAPABILITY)
    try:
        yield
    finally:
        _VERIFIED_AUTHORITY_TOKEN.reset(reset)
_CONTEXT_TYPE = "dataset_structure_context"
_PROFILE_TYPES = {"snapshot_profile", "table_profile", "table_inventory_profile", "schema_profile", "column_profile"}
_SCHEMA_PREDICATE = "table.physical/schema_column"
_ENTITY_PREDICATE = "table.structure/entity_binding"
_GRAIN_PREDICATE = "table.structure/row_grain"
_TEMPORAL_PREDICATE = "table.temporal/temporal_binding"
_CADENCE_PREDICATE = "table.temporal/observed_cadence"
_OBSERVER_VERSION = "profile-structure-v2"
_TEMPORAL_EVIDENCE_POLICY_VERSION = "1"
MAX_IDENTIFIER_CANDIDATES = 32
MAX_TEMPORAL_PARTNERS = 32
MAX_COMPOSITE_CANDIDATES = 256
MAX_KEY_COLUMNS = 2
MAX_CADENCE_ROWS = 1_000_000
SUPPORTED_OBSERVER_PREDICATES = frozenset({_SCHEMA_PREDICATE, _ENTITY_PREDICATE, _GRAIN_PREDICATE,
                                           _TEMPORAL_PREDICATE, _CADENCE_PREDICATE})
_EVIDENCE_PRIMARY_ROLE = {
    "physical_profile": "profile", "reviewed_metadata": "reviewed_metadata",
    "source_constraint": "source_constraint", "structural_decision": "decision",
    "bounded_scan": "scan", "deterministic_inference": "dependency",
}


def _methodology(kind: str, *, schema_version: int = 1, context_version: str = "1") -> str:
    """A producer protocol fingerprint, deliberately not consumer methodology."""
    return stable_fingerprint({
        "producer": "dataset_structure_context_adapter",
        "adapter_version": _ADAPTER_VERSION,
        "kind": kind,
        "dsc_schema_version": schema_version,
        "context_version": context_version,
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
        # A v2 clear/N/A has no candidate pin by design.  Its affirmative
        # structural decision is anchored to the exact governed profile
        # publication rather than inventing an unrelated candidate assertion.
        # All selected v2 bindings still require their ``decision`` pin below.
        if (payload.get("context_version") == "2" and evidence["kind"] == "structural_decision"
                and len(payload.get("claims") or []) == 1
                and isinstance(payload["claims"][0].get("value"), dict)
                and payload["claims"][0]["value"].get("selection") == "none"):
            primary_role = "profile"
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
        "methodology_fingerprint": _methodology("assertion", schema_version=payload["schema_version"],
                                                context_version=payload["context_version"]),
        "scope": scope, "owner_id": owner_id, "workflow_id": None,
        "identity_inputs": {
            "assertion_locator": assertion_identity_key(payload),
            "dependency_fingerprint": payload["dependency_fingerprint"],
            "versions": {"schema_version": payload["schema_version"],
                         "context_version": payload["context_version"], "adapter_version": _ADAPTER_VERSION},
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
    if payload.get("context_version") == "2" and _VERIFIED_AUTHORITY_TOKEN.get() is not _AUTHORITY_CAPABILITY:
        raise ValueError("DSC v2 authority assertions require the verified decision transaction")
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
        "target_fingerprint": None, "methodology_fingerprint": _methodology(
            "context", schema_version=payload["schema_version"], context_version=payload["context_version"]),
        "scope": "diagnostic_local", "owner_id": payload["consumer_id"], "workflow_id": None,
        "identity_inputs": {
            "request_fingerprint": payload["request_fingerprint"], "resolved_as_of": payload["resolved_as_of"],
            "versions": {"schema_version": payload["schema_version"], "protocol_version": payload["protocol_version"],
                         "context_version": payload["context_version"], "adapter_version": _ADAPTER_VERSION},
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
                                     version: bool = False,
                                     precommit_guard: Callable[[], None] | None = None) -> "ArtifactSaveOutcome":
    """Persist a DSC assertion using only adapter-derived AAR metadata."""
    validate_assertion_payload(payload)
    lineage, ordered = _source_refs(payload)
    scope, owner = _assertion_scope(payload)
    expected = _assertion_identity(payload, scope, owner, ordered)
    return repo.save(payload, version=version, artifact_schema_version=payload["schema_version"],
                     source_artifacts=tuple(lineage), created_by=created_by,
                     precommit_guard=precommit_guard, **expected)


def save_dataset_structure_context(repo: Any, payload: Any, *, consumer_id: str,
                                   created_by: str | None = None, version: bool = False) -> "ArtifactSaveOutcome":
    """Persist a consumer-local context; consumer_id is a reuse namespace."""
    validate_context_payload(payload)
    if payload["consumer_id"] != consumer_id:
        raise ValueError("DSC context payload consumer_id must match the persistence consumer_id")
    lineage, ordered_pins, _maximum = _context_pins(payload, repo.get)
    expected = _context_identity(payload, ordered_pins)
    return repo.save(payload, version=version, artifact_schema_version=payload["schema_version"],
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


def _repository_get(repo: Any, artifact_id: str, *, conn: Any = None) -> tuple[Any, Any]:
    """Use the batch connection only when a transactional guard supplies one."""
    return repo.get(artifact_id) if conn is None else repo.get(artifact_id, conn=conn)


def _repository_get_metadata(repo: Any, artifact_id: str, *, conn: Any = None) -> Any:
    return repo.get_metadata(artifact_id) if conn is None else repo.get_metadata(artifact_id, conn=conn)


def _active_single(repo: Any, *, artifact_type: str, snapshot_id: str,
                   table: str, column: str | None = None, conn: Any = None) -> tuple[Any, dict[str, Any]]:
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
        checked, payload = _repository_get(repo, metadata.artifact_id, conn=conn)
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
                        table: str, *, conn: Any = None) -> tuple[Any, dict[str, Any], list[tuple[Any, dict[str, Any]]]]:
    """Return one complete, exact, inventory-consistent retained profile set."""
    table_metadata, table_payload = _active_single(
        repo, artifact_type="table_profile", snapshot_id=snapshot_id, table=table, conn=conn)
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
                                           snapshot_id=snapshot_id, table=table, column=column, conn=conn)
        if (metadata.asset_id != asset_id or metadata.snapshot_id != snapshot_id
                or metadata.identity.get("table") != table or metadata.feature != column
                or not isinstance(payload.get("data_type"), str) or not payload["data_type"]
                or payload["data_type"] != inventory_by_column[column].get("data_type")
                or payload.get("role") != inventory_by_column[column].get("role")
                or payload.get("metadata_reviewed") is not (inventory_by_column[column].get("role_reviewed") == 1)
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


def _temporal_inventory_profile(repo: Any, snapshot_id: str, asset_id: str, table: str,
                                table_payload: dict[str, Any], *, conn: Any = None) -> tuple[Any, dict[str, Any]]:
    """Load the stable table-membership witness used only by C2 temporal DSC."""
    metadata, payload = _active_single(repo, artifact_type="table_inventory_profile",
                                       snapshot_id=snapshot_id, table=table, conn=conn)
    if (metadata.asset_id != asset_id or metadata.snapshot_id != snapshot_id
            or metadata.identity.get("table") != table or payload.get("table") != table
            or payload.get("source") != "retained_inventory_membership"
            or payload.get("columns") != table_payload.get("columns")
            or payload.get("column_count") != table_payload.get("column_count")
            or payload.get("row_count") != table_payload.get("row_count")):
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    inventory = [row for row in db.query("variable_inventory", item_id=snapshot_id, order_by="column_name")
                 if row.get("table_name") == table]
    membership = [{"column": row.get("column_name"), "data_type": row.get("data_type")}
                  for row in inventory]
    if metadata.identity.get("identity_inputs", {}).get("inventory_membership_fingerprint") != stable_fingerprint(membership):
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    return metadata, payload


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


def _temporal_payload(asset_id: str, snapshot_id: str, table: str, table_inventory_metadata: Any,
                      metadata: Any, profile: dict[str, Any]) -> dict[str, Any]:
    """Propose one reviewed Date/Period binding from retained aggregates only."""
    column = metadata.feature
    role = str(profile.get("role") or "").strip()
    temporal_type = {"date": "date", "period": "period"}.get(role.lower())
    if temporal_type is None or profile.get("metadata_reviewed") is not True:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    total, physical, special, regular, _distinct = _profile_counts(profile)
    quarter_profile = profile.get("period_bounds")
    recognised_calendar_quarter = (temporal_type == "period" and isinstance(quarter_profile, dict)
                                   and set(quarter_profile) == {"start_date", "end_date", "format"}
                                   and quarter_profile.get("format") == "calendar_quarter"
                                   and all(isinstance(quarter_profile.get(key), str) and quarter_profile[key]
                                           for key in ("start_date", "end_date"))
                                   and profile.get("period_format_evidence_available") is True
                                   and _exact_count(profile.get("period_format_checked_regular_count")) == regular
                                   and _exact_count(profile.get("period_format_failure_count")) == 0)
    date_parse_available = "date_parse_failure_count" in profile
    period_parse_available = (profile.get("period_format_evidence_available") is True
                              and "period_format_failure_count" in profile)
    parse_available = date_parse_available or period_parse_available
    parse_failure = (_exact_count(profile["date_parse_failure_count"]) if date_parse_available
                     else _exact_count(profile["period_format_failure_count"]) if period_parse_available else 0)
    if parse_failure > regular:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    usable = regular - parse_failure
    instance_key = f"column:{column}"
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _TEMPORAL_PREDICATE,
        "instance_key": instance_key})
    value = {"kind": "temporal_binding", "axis_id": instance_key, "temporal_type": temporal_type,
             "columns": [{"table": table, "column": column}],
             "precision": "quarter" if recognised_calendar_quarter else "unknown",
             "calendar": "gregorian" if recognised_calendar_quarter else "unknown"}
    refs = [
        {"artifact_id": metadata.artifact_id, "role": "reviewed_metadata", "payload_hash": metadata.payload_hash},
        {"artifact_id": table_inventory_metadata.artifact_id, "role": "dependency",
         "payload_hash": table_inventory_metadata.payload_hash},
    ]
    basis = {"population": table, "total_count": total,
             "exclusions": {"physical_null": physical, "confirmed_special": special,
                            "parse_failure": parse_failure},
             "usable_count": usable, "computation": "exact"}
    measurements = [
        {"name": "regular_value_rows", "count": regular},
        {"name": "temporal_parse_failure_available", "count": int(parse_available)},
    ]
    if parse_available:
        measurements.append({"name": "temporal_parse_failure_rows", "count": parse_failure})
    evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "reviewed_metadata",
                                    "source_refs": refs, "basis": basis,
                                    "measurements": measurements})
    claim_id = _sha_id("dscc", {"assertion_id": assertion_id,
                                 "authority": "source_reviewed_metadata", "value": value})
    sensitivity = max((_source_sensitivity(metadata.artifact_type),
                       _source_sensitivity(table_inventory_metadata.artifact_type)),
                      key=lambda item: ("external_safe", "internal", "confidential").index(item))
    return _structural_assertion(
        asset_id=asset_id, snapshot_id=snapshot_id, table=table, predicate=_TEMPORAL_PREDICATE,
        instance_key=instance_key,
        claims=[{"claim_id": claim_id, "authority": "source_reviewed_metadata", "value": value,
                 "evidence_ids": [evidence_id]}],
        evidence=[{"evidence_id": evidence_id, "kind": "reviewed_metadata", "source_refs": refs,
                   "basis": basis, "measurements": measurements, "sensitivity": sensitivity}],
        dependency={"observer_version": _OBSERVER_VERSION,
                    "temporal_evidence_policy_version": _TEMPORAL_EVIDENCE_POLICY_VERSION,
                    "temporal_producer_version": "1",
                    "schema_version": 1, "context_version": "1",
                    "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
                    "qualified_column": {"table": table, "column": column},
                    "table_inventory_profile": [table_inventory_metadata.artifact_id,
                                                table_inventory_metadata.payload_hash],
                    "column_profile": [metadata.artifact_id, metadata.payload_hash],
                    "metadata_reviewed": True,
                    "reviewed_role": role, "temporal_type": temporal_type},
        status="proposed", effective=[claim_id], reasons=[], sensitivity=sensitivity)


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


def _cadence_key(axis_id: str, grouping: list[dict[str, str]]) -> str:
    encoded = json.dumps({"axis_id": axis_id, "grouping": grouping}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "axis_grouping:" + hashlib.sha256(encoded).hexdigest()


def _cadence_period_profile(profile: dict[str, Any]) -> bool:
    bounds = profile.get("period_bounds")
    try:
        _total, _physical, _special, regular, _distinct = _profile_counts(profile)
        return (isinstance(bounds, dict) and set(bounds) == {"start_date", "end_date", "format"}
                and bounds.get("format") == "calendar_quarter"
                and all(isinstance(bounds.get(key), str) and bounds[key] for key in ("start_date", "end_date"))
                and profile.get("period_format_evidence_available") is True
                and _exact_count(profile.get("period_format_checked_regular_count")) == regular
                and _exact_count(profile.get("period_format_failure_count")) == 0)
    except DatasetStructureObservationError:
        return False


def _active_locator_assertions(repo: Any, *, snapshot_id: str, asset_id: str, table: str,
                               predicate: str, instance_key: str, conn: Any = None) -> list[tuple[Any, dict[str, Any]]]:
    """Return only hash-verified, syntactically valid same-snapshot locator facts."""
    found: list[tuple[Any, dict[str, Any]]] = []
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type=_ASSERTION_TYPE, status="active"):
        if (metadata.asset_id != asset_id or metadata.snapshot_id != snapshot_id
                or metadata.scope != "universal" or metadata.identity.get("table") != table):
            continue
        try:
            checked, payload = _repository_get(repo, metadata.artifact_id, conn=conn)
            validate_assertion_payload(payload)
        except Exception as exc:
            if exc.__class__.__name__ == "ArtifactIntegrityError":
                _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
            continue
        if (checked.status == "active" and checked.integrity_status == "verified"
                and checked.payload_hash == metadata.payload_hash
                and payload.get("snapshot") == {"asset_id": asset_id, "snapshot_id": snapshot_id}
                and payload.get("subject") == {"kind": "table", "table": table}
                and payload.get("predicate") == predicate and payload.get("instance_key") == instance_key):
            found.append((checked, payload))
    return found


def _effective_locator_claim(payload: dict[str, Any]) -> dict[str, Any] | None:
    resolution = payload.get("resolution", {})
    if resolution.get("status") not in {"proposed", "confirmed"}:
        return None
    effective = resolution.get("effective_claim_ids")
    if not isinstance(effective, list) or len(effective) != 1:
        return None
    return next((claim for claim in payload.get("claims", [])
                 if claim.get("claim_id") == effective[0]), None)


def _is_entity_producer_payload(repo: Any, payload: dict[str, Any], *, conn: Any = None) -> bool:
    """Recognise the immutable reviewed-Identifier producer fact, not lookalikes."""
    if (payload.get("predicate") != _ENTITY_PREDICATE or payload.get("resolution", {}).get("status") != "proposed"
            or len(payload.get("claims", [])) != 1 or payload["claims"][0].get("authority") != "source_reviewed_metadata"
            or len(payload.get("evidence", [])) != 1 or payload["evidence"][0].get("kind") != "reviewed_metadata"):
        return False
    snapshot, subject, key = payload.get("snapshot"), payload.get("subject"), payload.get("instance_key")
    if not isinstance(snapshot, dict) or not isinstance(subject, dict) or not isinstance(key, str):
        return False
    refs = {ref.get("role"): ref for ref in payload["evidence"][0].get("source_refs", []) if isinstance(ref, dict)}
    if set(refs) != {"reviewed_metadata", "dependency"}:
        return False
    try:
        column_meta, column_profile = _repository_get(repo, refs["reviewed_metadata"]["artifact_id"], conn=conn)
        table_meta, _table_profile = _repository_get(repo, refs["dependency"]["artifact_id"], conn=conn)
        expected = _entity_payload(snapshot["asset_id"], snapshot["snapshot_id"], subject["table"],
                                   table_meta, column_meta, column_profile)
    except Exception:
        return False
    return payload == expected


def _project_cadence_prerequisite(repo: Any, *, asset_id: str, snapshot_id: str, table: str,
                                  predicate: str, instance_key: str, conn: Any = None) -> tuple[Any, dict[str, Any], dict[str, Any]] | None:
    """Apply the confirmation-over-one-agreeing-producer rule at one locator."""
    confirmations: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    proposals: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for metadata, payload in _active_locator_assertions(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                                         table=table, predicate=predicate, instance_key=instance_key,
                                                         conn=conn):
        claim = _effective_locator_claim(payload)
        if claim is None:
            continue
        if payload["resolution"]["status"] == "confirmed":
            if claim.get("authority") == "source_confirmed_structural":
                confirmations.append((metadata, payload, claim))
        elif ((predicate == _TEMPORAL_PREDICATE and _is_temporal_producer_payload(repo, payload, conn=conn))
              or (predicate == _ENTITY_PREDICATE and _is_entity_producer_payload(repo, payload, conn=conn))):
            proposals.append((metadata, payload, claim))
    if len(confirmations) > 1 or len(proposals) > 1:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    if confirmations and proposals and confirmations[0][2].get("value") != proposals[0][2].get("value"):
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    return confirmations[0] if confirmations else proposals[0] if proposals else None


def _cadence_plan(repo: Any, *, asset_id: str, snapshot_id: str, table: str,
                  table_metadata: Any, table_payload: dict[str, Any],
                  profiles: list[tuple[Any, dict[str, Any]]], conn: Any = None) -> list[dict[str, Any]]:
    """Build the complete candidate-local plan before any source-table read."""
    try:
        inventory_metadata, _inventory_payload = _temporal_inventory_profile(
            repo, snapshot_id, asset_id, table, table_payload, conn=conn)
    except DatasetStructureObservationError as exc:
        if exc.reason_code == "DSC_R_INSUFFICIENT_BASIS":
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
        raise
    profile_by_column = {metadata.feature: (metadata, profile) for metadata, profile in profiles}
    axes: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    # Only mechanical profile locators are considered.  Assertions cannot
    # create a column absent from the retained inventory.
    for column, (profile_meta, profile) in sorted(profile_by_column.items()):
        key = f"column:{column}"
        temporal = _project_cadence_prerequisite(repo, asset_id=asset_id, snapshot_id=snapshot_id,
                                                  table=table, predicate=_TEMPORAL_PREDICATE, instance_key=key,
                                                  conn=conn)
        if temporal is not None:
            meta, payload, claim = temporal; value = claim.get("value", {})
            cols = value.get("columns")
            valid_date = (value.get("kind") == "temporal_binding" and value.get("axis_id") == key
                          and value.get("temporal_type") == "date" and cols == [{"table": table, "column": column}]
                          and "timezone" not in value)
            valid_period = (value.get("kind") == "temporal_binding" and value.get("axis_id") == key
                            and value.get("temporal_type") == "period" and cols == [{"table": table, "column": column}]
                            and value.get("precision") == "quarter" and value.get("calendar") == "gregorian"
                            and "timezone" not in value and _cadence_period_profile(profile))
            if valid_date or valid_period:
                axes.append({"axis_id": key, "column": column, "temporal_type": value["temporal_type"],
                             "assertion": (meta, payload), "profile": (profile_meta, profile)})
        entity = _project_cadence_prerequisite(repo, asset_id=asset_id, snapshot_id=snapshot_id,
                                                table=table, predicate=_ENTITY_PREDICATE, instance_key=key,
                                                conn=conn)
        if entity is not None:
            meta, payload, claim = entity; value = claim.get("value", {})
            if (value == {"kind": "entity_binding", "columns": [{"table": table, "column": column}]}
                    and profile.get("metadata_reviewed") is True
                    and str(profile.get("role") or "").strip().lower() == "identifier"):
                groups.append({"column": column, "assertion": (meta, payload), "profile": (profile_meta, profile)})
    axes.sort(key=lambda item: (item["axis_id"], item["column"]))
    groups.sort(key=lambda item: item["column"])
    if len(axes) > MAX_TEMPORAL_PARTNERS or len(groups) > MAX_IDENTIFIER_CANDIDATES or len(axes) * len(groups) > MAX_COMPOSITE_CANDIDATES:
        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
    plan: list[dict[str, Any]] = []
    for axis in axes:
        for group in groups:
            grouping = [{"table": table, "column": group["column"]}]
            key = _cadence_key(axis["axis_id"], grouping)
            dependencies = {
                "cadence_observer_version": "1", "normalizer_version": "strict-date-quarter-v1",
                "classifier_version": "gregorian-exact-v1", "evidence_policy_version": "1",
                "schema_version": 1, "context_version": "1", "registry_version": "1",
                "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id}, "table": table,
                "axis": [axis["assertion"][0].artifact_id, axis["assertion"][0].payload_hash],
                "grouping": [group["assertion"][0].artifact_id, group["assertion"][0].payload_hash],
                "axis_profile": [axis["profile"][0].artifact_id, axis["profile"][0].payload_hash],
                "grouping_profile": [group["profile"][0].artifact_id, group["profile"][0].payload_hash],
                "table_profile": [table_metadata.artifact_id, table_metadata.payload_hash],
                "inventory_profile": [inventory_metadata.artifact_id, inventory_metadata.payload_hash],
                "policy": {"max_groupings": MAX_IDENTIFIER_CANDIDATES, "max_axes": MAX_TEMPORAL_PARTNERS,
                           "max_pairs": MAX_COMPOSITE_CANDIDATES, "max_rows": MAX_CADENCE_ROWS},
            }
            plan.append({"axis": axis, "group": group, "grouping": grouping, "instance_key": key,
                         "dependency_fingerprint": stable_fingerprint(dependencies), "dependencies": dependencies,
                         "table": (table_metadata, table_payload), "inventory": inventory_metadata})
    return plan


def _cadence_is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not isinstance(result, (list, tuple)) else False
    except (TypeError, ValueError):
        return False


def _strict_date_key(value: Any) -> tuple[int, date] | None:
    """Closed Date parser: no inference, trimming, offsets, or non-midnight values."""
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None or value.hour or value.minute or value.second or value.microsecond or value.nanosecond:
            return None
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.hour or value.minute or value.second or value.microsecond:
            return None
        value = value.date()
    if isinstance(value, date):
        return value.toordinal(), value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            parsed = date.fromisoformat(value)
            return parsed.toordinal(), parsed
        except ValueError:
            return None
    return None


def _strict_quarter_key(value: Any) -> int | None:
    # This intentionally mirrors the full-column C2 profiler recognizer.
    # Cadence cannot reject a lexical form that qualified the exact
    # calendar-quarter profile which authorizes this axis.
    match = re.fullmatch(r"\s*(\d{4})\D*[Qq]([1-4])\s*", value) if isinstance(value, str) else None
    if match is None:
        return None
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _cadence_group_key(value: Any) -> tuple[str, str]:
    # This is strictly transient.  The type tag prevents accidental string/numeric
    # conflation while safely handling otherwise unhashable scalar-like values.
    return (type(value).__module__ + "." + type(value).__qualname__, repr(value))


def _interval_class(left: Any, right: Any, temporal_type: str) -> tuple[str, int] | None:
    if temporal_type == "period":
        step = int(right) - int(left)
        return ("quarter", step) if step > 0 else None
    left_date, right_date = left, right
    days = right_date.toordinal() - left_date.toordinal()
    if days <= 0:
        return None
    if left_date.month == right_date.month and left_date.day == right_date.day:
        years = right_date.year - left_date.year
        if years > 0:
            return "year", years
    if left_date.day == right_date.day:
        months = (right_date.year - left_date.year) * 12 + right_date.month - left_date.month
        if months > 0 and months % 3 == 0:
            return "quarter", months // 3
        if months > 0:
            return "month", months
    if days % 7 == 0:
        return "week", days // 7
    return "day", days


def _cadence_scan_pair(frame: pd.DataFrame, candidate: dict[str, Any]) -> dict[str, Any]:
    axis_col, group_col = candidate["axis"]["column"], candidate["group"]["column"]
    axis_profile, group_profile = candidate["axis"]["profile"][1], candidate["group"]["profile"][1]
    numeric = {column: (pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]))
               for column in (axis_col, group_col)}
    special_columns = {axis_col: ({_special_key(item, numeric=numeric[axis_col]) for item in axis_profile.get("special_values", [])}
                                  if axis_profile.get("special_values_confirmed") else set()),
                       group_col: ({_special_key(item, numeric=numeric[group_col]) for item in group_profile.get("special_values", [])}
                                   if group_profile.get("special_values_confirmed") else set())}
    total = len(frame.index); physical = special = parse_failure = 0
    entity_values: dict[tuple[str, str], dict[int, Any]] = {}
    for axis_value, group_value in frame[[axis_col, group_col]].itertuples(index=False, name=None):
        if _cadence_is_null(axis_value) or _cadence_is_null(group_value):
            physical += 1; continue
        if (_special_key(axis_value, numeric=numeric[axis_col]) in special_columns[axis_col]
                or _special_key(group_value, numeric=numeric[group_col]) in special_columns[group_col]):
            special += 1; continue
        parsed = (_strict_date_key(axis_value) if candidate["axis"]["temporal_type"] == "date"
                  else _strict_quarter_key(axis_value))
        if parsed is None:
            parse_failure += 1; continue
        temporal_key, temporal_value = parsed if candidate["axis"]["temporal_type"] == "date" else (parsed, parsed)
        entity_values.setdefault(_cadence_group_key(group_value), {})[temporal_key] = temporal_value
    usable = total - physical - special - parse_failure
    distinct = sum(len(values) for values in entity_values.values())
    duplicates = usable - distinct
    entities = len(entity_values); adequate = sum(len(values) >= 3 for values in entity_values.values())
    deltas: list[tuple[str, int] | None] = []
    for values in entity_values.values():
        ordered = [values[key] for key in sorted(values)]
        deltas.extend(_interval_class(a, b, candidate["axis"]["temporal_type"]) for a, b in zip(ordered, ordered[1:]))
    recognised: dict[tuple[str, int], int] = {}
    for interval in deltas:
        if interval is not None:
            recognised[interval] = recognised.get(interval, 0) + 1
    ordered_classes = sorted(recognised.items(), key=lambda item: (-item[1], json.dumps({"unit": item[0][0], "step": item[0][1]}, sort_keys=True, separators=(",", ":"))))
    dominant = ordered_classes[0][1] if ordered_classes else 0
    second = ordered_classes[1][1] if len(ordered_classes) > 1 else 0
    d = len(deltas); sufficient = d > 0 and 10 * adequate >= 9 * entities
    if not sufficient:
        cadence, interval = "unknown", None
    elif dominant * 20 >= 19 * d:
        cadence, interval = "regular", ordered_classes[0][0]
    elif (len(ordered_classes) >= 2 and 20 * ordered_classes[0][1] >= d and 20 * ordered_classes[1][1] >= d
          and 20 * sum(recognised.values()) >= 19 * d):
        cadence, interval = "mixed", None
    else:
        cadence, interval = "irregular", None
    return {"basis": {"population": candidate["table"][1]["table"], "total_count": total,
                      "exclusions": {"physical_null": physical, "confirmed_special": special,
                                     "parse_failure": parse_failure}, "usable_count": usable,
                      "computation": "bounded_scan"},
            "measurements": [
                {"name": "distinct_entity_axis_observations", "count": distinct},
                {"name": "duplicate_entity_axis_excess_rows", "count": duplicates},
                {"name": "entities_with_usable_observation", "count": entities},
                {"name": "entities_with_three_or_more_observations", "count": adequate},
                {"name": "usable_delta_count", "count": d},
                {"name": "classified_delta_count", "count": sum(recognised.values())},
                {"name": "recognised_interval_class_count", "count": len(recognised)},
                {"name": "dominant_interval_delta_count", "count": dominant},
                {"name": "second_interval_delta_count", "count": second},
            ] + ([{"name": "adequately_observed_entity_ratio", "numerator": adequate, "denominator": entities}] if entities else [])
              + ([{"name": "dominant_interval_ratio", "numerator": dominant, "denominator": d}] if d else []),
            "cadence": cadence, "interval": interval}


def _cadence_payload(asset_id: str, snapshot_id: str, table: str, candidate: dict[str, Any],
                     result: dict[str, Any]) -> dict[str, Any]:
    key = candidate["instance_key"]
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1",
        "snapshot": {"asset_id": asset_id, "snapshot_id": snapshot_id},
        "subject": {"kind": "table", "table": table}, "predicate": _CADENCE_PREDICATE, "instance_key": key})
    refs = [
        {"artifact_id": candidate["table"][0].artifact_id, "role": "scan", "payload_hash": candidate["table"][0].payload_hash},
        {"artifact_id": candidate["inventory"].artifact_id, "role": "dependency", "payload_hash": candidate["inventory"].payload_hash},
        {"artifact_id": candidate["axis"]["assertion"][0].artifact_id, "role": "dependency", "payload_hash": candidate["axis"]["assertion"][0].payload_hash},
        {"artifact_id": candidate["group"]["assertion"][0].artifact_id, "role": "dependency", "payload_hash": candidate["group"]["assertion"][0].payload_hash},
        {"artifact_id": candidate["axis"]["profile"][0].artifact_id, "role": "profile", "payload_hash": candidate["axis"]["profile"][0].payload_hash},
        {"artifact_id": candidate["group"]["profile"][0].artifact_id, "role": "profile", "payload_hash": candidate["group"]["profile"][0].payload_hash},
    ]
    # A column can serve both roles; evidence source union requires one stable role.
    dedup: dict[str, dict[str, str]] = {}
    for ref in refs:
        dedup.setdefault(ref["artifact_id"], ref)
    refs = list(dedup.values())
    sensitivity = max((_source_sensitivity(ref_meta.artifact_type) for ref_meta in
                       (candidate["table"][0], candidate["inventory"], candidate["axis"]["assertion"][0],
                        candidate["group"]["assertion"][0], candidate["axis"]["profile"][0], candidate["group"]["profile"][0])),
                      key=lambda value: ("external_safe", "internal", "confidential").index(value))
    evidence_id = _sha_id("dsce", {"assertion_id": assertion_id, "kind": "bounded_scan", "source_refs": refs,
                                    "basis": result["basis"], "measurements": result["measurements"]})
    claims: list[dict[str, Any]] = []; effective: list[str] = []
    if result["cadence"] != "unknown":
        value: dict[str, Any] = {"kind": "observed_cadence", "axis_id": candidate["axis"]["axis_id"],
                                 "grouping": candidate["grouping"], "cadence": result["cadence"]}
        if result["interval"] is not None:
            value["observed_interval_class"] = {"unit": result["interval"][0], "step": result["interval"][1]}
        claim_id = _sha_id("dscc", {"assertion_id": assertion_id, "authority": "verified_observation", "value": value})
        claims = [{"claim_id": claim_id, "authority": "verified_observation", "value": value, "evidence_ids": [evidence_id]}]
        effective = [claim_id]
    return _structural_assertion(asset_id=asset_id, snapshot_id=snapshot_id, table=table,
        predicate=_CADENCE_PREDICATE, instance_key=key, claims=claims,
        evidence=[{"evidence_id": evidence_id, "kind": "bounded_scan", "source_refs": refs,
                   "basis": result["basis"], "measurements": result["measurements"], "sensitivity": sensitivity}],
        dependency=candidate["dependencies"], status="observed" if claims else "unknown", effective=effective,
        reasons=[] if claims else ["DSC_R_INSUFFICIENT_BASIS"], sensitivity=sensitivity)


def _cadence_measurements(payload: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    evidence = payload.get("evidence", [])
    if len(evidence) != 1 or evidence[0].get("kind") != "bounded_scan":
        return None
    entries = evidence[0].get("measurements")
    if not isinstance(entries, list):
        return None
    by_name = {item.get("name"): item for item in entries if isinstance(item, dict)}
    required = {"distinct_entity_axis_observations", "duplicate_entity_axis_excess_rows",
                "entities_with_usable_observation", "entities_with_three_or_more_observations",
                "usable_delta_count", "classified_delta_count", "recognised_interval_class_count",
                "dominant_interval_delta_count", "second_interval_delta_count"}
    return by_name if set(by_name) == required | ({"adequately_observed_entity_ratio"} if by_name.get("adequately_observed_entity_ratio") else set()) | ({"dominant_interval_ratio"} if by_name.get("dominant_interval_ratio") else set()) else None


def _is_cadence_producer_payload(repo: Any, payload: dict[str, Any], candidate: dict[str, Any], *,
                                 historical: bool = False, conn: Any = None) -> bool:
    """Recognise only an exact C2-generated assertion, never a lookalike."""
    key, table = candidate["instance_key"], candidate["table"][1]["table"]
    snapshot = {"asset_id": candidate["dependencies"]["snapshot"]["asset_id"],
                "snapshot_id": candidate["dependencies"]["snapshot"]["snapshot_id"]}
    assertion_id = _sha_id("dsca", {"schema_version": 1, "context_version": "1", "snapshot": snapshot,
        "subject": {"kind": "table", "table": table}, "predicate": _CADENCE_PREDICATE, "instance_key": key})
    if (payload.get("assertion_id") != assertion_id or payload.get("predicate") != _CADENCE_PREDICATE
            or payload.get("instance_key") != key or payload.get("dependency_fingerprint") != candidate["dependency_fingerprint"]
            or payload.get("snapshot") != snapshot or payload.get("subject") != {"kind": "table", "table": table}):
        return False
    # Source identities, roles, hashes and artifact types must exactly match
    # this candidate-local plan.  This is what prevents a hand-authored scan
    # assertion from entering the producer namespace.
    expected_refs = {
        (candidate["table"][0].artifact_id, "scan", candidate["table"][0].payload_hash),
        (candidate["inventory"].artifact_id, "dependency", candidate["inventory"].payload_hash),
        (candidate["axis"]["assertion"][0].artifact_id, "dependency", candidate["axis"]["assertion"][0].payload_hash),
        (candidate["group"]["assertion"][0].artifact_id, "dependency", candidate["group"]["assertion"][0].payload_hash),
        (candidate["axis"]["profile"][0].artifact_id, "profile", candidate["axis"]["profile"][0].payload_hash),
        (candidate["group"]["profile"][0].artifact_id, "profile", candidate["group"]["profile"][0].payload_hash),
    }
    expected_refs = {(aid, role, digest) for aid, role, digest in expected_refs}
    evidence = payload.get("evidence", [])
    if len(evidence) != 1:
        return False
    actual_refs = {(ref.get("artifact_id"), ref.get("role"), ref.get("payload_hash"))
                   for ref in evidence[0].get("source_refs", []) if isinstance(ref, dict)}
    if actual_refs != expected_refs:
        return False
    try:
        for artifact_id, _role, digest in actual_refs:
            meta, _value = _repository_get(repo, artifact_id, conn=conn)
            if (not historical and (meta.status != "active" or meta.integrity_status != "verified")
                    or meta.payload_hash != digest):
                return False
    except Exception:
        return False
    return validate_cadence_payload(payload, snapshot=snapshot, table=table, instance_key=key,
                                    dependency_fingerprint=candidate["dependency_fingerprint"],
                                    expected_refs=expected_refs, axis_id=candidate["axis"]["axis_id"],
                                    grouping=candidate["grouping"])


def _cadence_existing(repo: Any, *, asset_id: str, snapshot_id: str, table: str,
                      candidate: dict[str, Any], conn: Any = None) -> tuple[Any, dict[str, Any]] | None:
    prior = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=asset_id, table=table,
                                         predicate=_CADENCE_PREDICATE, instance_key=candidate["instance_key"],
                                         temporal_producer_only=False, conn=conn)
    ours = [(meta, payload) for meta, payload in prior
            if _is_cadence_producer_payload(repo, payload, candidate, conn=conn)]
    if len(ours) > 1:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    confirmations: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for metadata, payload in _active_locator_assertions(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                                         table=table, predicate=_CADENCE_PREDICATE,
                                                         instance_key=candidate["instance_key"], conn=conn):
        claim = _effective_locator_claim(payload)
        if payload.get("resolution", {}).get("status") == "confirmed":
            decision = (claim or {}).get("decision", {})
            if (claim is None or claim.get("authority") != "source_confirmed_structural"
                    or decision.get("scope") != "source_confirmed_structural"
                    or decision.get("action") != "confirm"):
                _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
            confirmations.append((metadata, payload, claim))
    if len(confirmations) > 1:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    if not confirmations:
        return ours[0] if ours else None
    # A confirmation is a separate source artifact.  It can project only over
    # one exact immutable observed assertion, never create cadence by itself.
    if len(ours) != 1:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    observed_meta, observed = ours[0]
    observed_claim = observed["claims"][0] if observed.get("claims") else None
    confirmation_meta, confirmation, confirmation_claim = confirmations[0]
    refs = {(ref.get("artifact_id"), ref.get("role"), ref.get("payload_hash"))
            for evidence in confirmation.get("evidence", []) for ref in evidence.get("source_refs", [])
            if isinstance(ref, dict)}
    tied = (observed_claim is not None
            and confirmation.get("dependency_fingerprint") == candidate["dependency_fingerprint"]
            and confirmation_claim.get("value") == observed_claim.get("value")
            and (observed_meta.artifact_id, "dependency", observed_meta.payload_hash) in refs)
    if not tied:
        _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
    return confirmation_meta, confirmation


def _stale_cadence_assertions(repo: Any, *, asset_id: str, snapshot_id: str, table: str,
                              plan: list[dict[str, Any]], conn: Any = None) -> list[Any]:
    """Locate only our scan assertions whose prerequisite projection vanished.

    The readable axis/grouping claim is deliberately absent for an ``unknown``
    result, so source assertion IDs—not source values—are the durable local
    withdrawal witness.
    """
    current_assertions = {
        item["axis"]["assertion"][0].artifact_id for item in plan
    } | {item["group"]["assertion"][0].artifact_id for item in plan}
    current_keys = {item["instance_key"] for item in plan}
    stale: list[Any] = []
    for metadata, payload in _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                                          table=table, predicate=_CADENCE_PREDICATE, conn=conn):
        if not _historical_cadence_owner(repo, payload, conn=conn):
            continue
        assertion_refs: set[str] = set()
        for ref in payload["evidence"][0].get("source_refs", []):
            if ref.get("role") != "dependency":
                continue
            try:
                source_metadata, _source_payload = _repository_get(repo, ref.get("artifact_id"), conn=conn)
            except Exception:
                assertion_refs.add("<unavailable>"); continue
            if source_metadata.artifact_type == _ASSERTION_TYPE:
                assertion_refs.add(source_metadata.artifact_id)
        if payload.get("instance_key") not in current_keys and (not assertion_refs or not assertion_refs <= current_assertions):
            stale.append(metadata)
    return stale


def _historical_cadence_owner(repo: Any, payload: dict[str, Any], *, conn: Any = None) -> bool:
    """Conservative lineage verifier for a no-longer-plannable producer fact."""
    snapshot, subject, key = payload.get("snapshot"), payload.get("subject"), payload.get("instance_key")
    if not (isinstance(snapshot, dict) and isinstance(subject, dict) and isinstance(key, str)
            and payload.get("assertion_id") == _sha_id("dsca", {"schema_version": 1, "context_version": "1",
                "snapshot": snapshot, "subject": subject, "predicate": _CADENCE_PREDICATE, "instance_key": key})):
        return False
    measures = _cadence_measurements(payload)
    evidence = payload.get("evidence", [])
    if measures is None or len(evidence) != 1:
        return False
    refs = evidence[0].get("source_refs", [])
    try:
        loaded = {ref["artifact_id"]: _repository_get(repo, ref["artifact_id"], conn=conn) for ref in refs}
        types = {artifact_id: item[0].artifact_type for artifact_id, item in loaded.items()}
    except Exception:
        return False
    roles = [(ref.get("role"), types.get(ref.get("artifact_id"))) for ref in refs]
    if roles.count(("scan", "table_profile")) != 1 or roles.count(("dependency", "table_inventory_profile")) != 1:
        return False
    if sum(role == "dependency" and typ == _ASSERTION_TYPE for role, typ in roles) != 2:
        return False
    if not all(role == "profile" and typ == "column_profile" for role, typ in roles if role == "profile"):
        return False
    scan = next(ref["artifact_id"] for ref in refs if (ref.get("role"), types[ref["artifact_id"]]) == ("scan", "table_profile"))
    inventory = next(ref["artifact_id"] for ref in refs if (ref.get("role"), types[ref["artifact_id"]]) == ("dependency", "table_inventory_profile"))
    assertions = [ref["artifact_id"] for ref in refs if (ref.get("role"), types[ref["artifact_id"]]) == ("dependency", _ASSERTION_TYPE)]
    axis_id = next((artifact_id for artifact_id in assertions
                    if loaded[artifact_id][1].get("predicate") == _TEMPORAL_PREDICATE), None)
    group_id = next((artifact_id for artifact_id in assertions
                     if loaded[artifact_id][1].get("predicate") == _ENTITY_PREDICATE), None)
    if axis_id is None or group_id is None:
        return False
    axis_claim, group_claim = _effective_locator_claim(loaded[axis_id][1]), _effective_locator_claim(loaded[group_id][1])
    if axis_claim is None or group_claim is None:
        return False
    axis_value, group_value = axis_claim.get("value", {}), group_claim.get("value", {})
    cols, groups = axis_value.get("columns"), group_value.get("columns")
    if not (isinstance(cols, list) and len(cols) == 1 and isinstance(groups, list) and len(groups) == 1):
        return False
    axis_column, group_column = cols[0].get("column"), groups[0].get("column")
    profiles = [ref["artifact_id"] for ref in refs if (ref.get("role"), types[ref["artifact_id"]]) == ("profile", "column_profile")]
    profile_by_feature = {loaded[item][0].feature: item for item in profiles}
    if axis_column not in profile_by_feature or group_column not in profile_by_feature:
        return False
    table_meta, table_payload = loaded[scan]
    axis_meta, axis_payload = loaded[axis_id]; group_meta, group_payload = loaded[group_id]
    axis_profile_meta, axis_profile = loaded[profile_by_feature[axis_column]]
    group_profile_meta, group_profile = loaded[profile_by_feature[group_column]]
    grouping = [{"table": subject.get("table"), "column": group_column}]
    dependencies = {"cadence_observer_version": "1", "normalizer_version": "strict-date-quarter-v1",
        "classifier_version": "gregorian-exact-v1", "evidence_policy_version": "1", "schema_version": 1,
        "context_version": "1", "registry_version": "1", "snapshot": snapshot, "table": subject.get("table"),
        "axis": [axis_meta.artifact_id, axis_meta.payload_hash], "grouping": [group_meta.artifact_id, group_meta.payload_hash],
        "axis_profile": [axis_profile_meta.artifact_id, axis_profile_meta.payload_hash],
        "grouping_profile": [group_profile_meta.artifact_id, group_profile_meta.payload_hash],
        "table_profile": [table_meta.artifact_id, table_meta.payload_hash],
        "inventory_profile": [loaded[inventory][0].artifact_id, loaded[inventory][0].payload_hash],
        "policy": {"max_groupings": MAX_IDENTIFIER_CANDIDATES, "max_axes": MAX_TEMPORAL_PARTNERS,
                   "max_pairs": MAX_COMPOSITE_CANDIDATES, "max_rows": MAX_CADENCE_ROWS}}
    candidate = {"axis": {"axis_id": axis_value.get("axis_id"), "column": axis_column,
                             "temporal_type": axis_value.get("temporal_type"), "assertion": (axis_meta, axis_payload),
                             "profile": (axis_profile_meta, axis_profile)},
                 "group": {"column": group_column, "assertion": (group_meta, group_payload),
                           "profile": (group_profile_meta, group_profile)}, "grouping": grouping,
                 "instance_key": _cadence_key(axis_value.get("axis_id"), grouping),
                 "dependency_fingerprint": stable_fingerprint(dependencies), "dependencies": dependencies,
                 "table": (table_meta, table_payload), "inventory": loaded[inventory][0]}
    return _is_cadence_producer_payload(repo, payload, candidate, historical=True)


def _assertion_batch_entry(payload: dict[str, Any], *, created_by: str | None) -> dict[str, Any]:
    """The save_batch spelling of the normal descriptor-validated assertion save."""
    lineage, ordered = _source_refs(payload)
    scope, owner = _assertion_scope(payload)
    return {"payload": payload, "version": False, "artifact_schema_version": payload["schema_version"],
            "source_artifacts": tuple(lineage), "created_by": created_by,
            **_assertion_identity(payload, scope, owner, ordered)}


def _reused_assertion_outcome(metadata: Any) -> "ArtifactSaveOutcome":
    from .repository import ArtifactSaveOutcome
    return ArtifactSaveOutcome(metadata, "reused")


def _revalidate_cadence_batch(repo: Any, *, asset_id: str, snapshot_id: str, table: str,
                              expected_plan: list[dict[str, Any]], stale: list[Any],
                              expected_locators: dict[str, tuple[str, str, str] | None],
                              conn: Any = None) -> None:
    """Commit-boundary check for every candidate and withdrawal target."""
    try:
        table_meta, table_payload, profiles = _profiles_for_table(
            repo, snapshot_id, asset_id, table, conn=conn)
        fresh = _cadence_plan(repo, asset_id=asset_id, snapshot_id=snapshot_id, table=table,
                              table_metadata=table_meta, table_payload=table_payload, profiles=profiles,
                              conn=conn)
    except DatasetStructureObservationError as exc:
        if exc.reason_code == "DSC_R_INSUFFICIENT_BASIS":
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
        raise
    if ([item["instance_key"] for item in fresh] != [item["instance_key"] for item in expected_plan]
            or [item["dependency_fingerprint"] for item in fresh]
            != [item["dependency_fingerprint"] for item in expected_plan]):
        _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
    for candidate in fresh:
        retained = _cadence_existing(repo, asset_id=asset_id, snapshot_id=snapshot_id,
                                     table=table, candidate=candidate, conn=conn)
        actual = (None if retained is None else (retained[0].artifact_id, retained[0].payload_hash,
                                                  retained[0].status))
        if actual != expected_locators.get(candidate["instance_key"]):
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
    for metadata in stale:
        if _repository_get_metadata(repo, metadata.artifact_id, conn=conn).status != "active":
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")


def observe_dataset_structure(repo: Any, snapshot_id: str, *, tables: Iterable[str],
                              predicates: Iterable[str] = (_SCHEMA_PREDICATE,),
                              snapshot_loader: SnapshotLoader | None = None,
                              created_by: str | None = None,
                              precommit_guard: Callable[[], None] | None = None) -> tuple["ArtifactSaveOutcome", ...]:
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
        try:
            table_metadata, table_payload, profiles = _profiles_for_table(
                repo, snapshot_id, reference.asset_id, table)
        except DatasetStructureObservationError as exc:
            # Cadence has no profile-derived ``unknown`` state: an inconsistent
            # retained source is integrity failure, while a completed scan with
            # too little longitudinal evidence is handled below as ``unknown``.
            if _CADENCE_PREDICATE in requested_predicates and exc.reason_code == "DSC_R_INSUFFICIENT_BASIS":
                _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
            raise
        if _CADENCE_PREDICATE in requested_predicates:
            cadence_plan = _cadence_plan(repo, asset_id=reference.asset_id, snapshot_id=snapshot_id,
                                         table=table, table_metadata=table_metadata, table_payload=table_payload,
                                         profiles=profiles)
            stale_cadence = _stale_cadence_assertions(repo, asset_id=reference.asset_id, snapshot_id=snapshot_id,
                                                      table=table, plan=cadence_plan)
            # There is intentionally no affirmative global/not-applicable cadence.
            # A table without one qualified pair simply contributes no assertion.
            if cadence_plan:
                if _exact_count(table_payload.get("row_count")) > MAX_CADENCE_ROWS:
                    _observation_failure("DSC_R_INSUFFICIENT_BASIS")
                existing = {candidate["instance_key"]: _cadence_existing(
                    repo, asset_id=reference.asset_id, snapshot_id=snapshot_id, table=table, candidate=candidate)
                            for candidate in cadence_plan}
                expected_locators = {
                    key: (None if item is None else (item[0].artifact_id, item[0].payload_hash, item[0].status))
                    for key, item in existing.items()}
                missing = [candidate for candidate in cadence_plan if existing[candidate["instance_key"]] is None]
                retained = [existing[candidate["instance_key"]] for candidate in cadence_plan
                            if existing[candidate["instance_key"]] is not None]
                if missing:
                    columns = sorted({candidate["axis"]["column"] for candidate in missing}
                                     | {candidate["group"]["column"] for candidate in missing})
                    try:
                        frame = (snapshot_loader or SnapshotLoader()).load_table(snapshot_id, table, columns=columns)
                    except Exception:
                        _observation_failure("DSC_R_INSUFFICIENT_BASIS")
                    if list(frame.columns) != columns or len(frame.index) != _exact_count(table_payload.get("row_count")):
                        _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
                    scanned = {candidate["instance_key"]: _cadence_scan_pair(frame, candidate) for candidate in missing}
                    payloads = [_cadence_payload(reference.asset_id, snapshot_id, table, candidate,
                                                 scanned[candidate["instance_key"]]) for candidate in missing]
                    supersessions: list[dict[str, Any]] = [
                        {"artifact_id": metadata.artifact_id, "actor": created_by} for metadata in stale_cadence]
                    for index, candidate in enumerate(missing):
                        old = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=reference.asset_id,
                                                           table=table, predicate=_CADENCE_PREDICATE,
                                                           instance_key=candidate["instance_key"])
                        owned = [(meta, prior) for meta, prior in old
                                 if _is_cadence_producer_payload(repo, prior, candidate)]
                        if len(owned) > 1:
                            _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
                        if owned:
                            supersessions.append({"artifact_id": owned[0][0].artifact_id,
                                                   "by_write_index": index, "actor": created_by})
                    batch = repo.save_batch(
                        [_assertion_batch_entry(payload, created_by=created_by) for payload in payloads],
                        supersessions=supersessions,
                        precommit_guard=lambda conn=None: (precommit_guard() if precommit_guard else None, _revalidate_cadence_batch(
                            repo, asset_id=reference.asset_id, snapshot_id=snapshot_id, table=table,
                            expected_plan=cadence_plan, stale=stale_cadence,
                            expected_locators=expected_locators, conn=conn)))
                    outcomes.extend(_reused_assertion_outcome(metadata) for metadata, _payload in retained)
                    outcomes.extend(batch)
                else:
                    guard = lambda conn=None: (precommit_guard() if precommit_guard else None, _revalidate_cadence_batch(
                        repo, asset_id=reference.asset_id, snapshot_id=snapshot_id, table=table,
                        expected_plan=cadence_plan, stale=stale_cadence,
                        expected_locators=expected_locators, conn=conn))
                    repo.save_batch([], supersessions=[{"artifact_id": meta.artifact_id, "actor": created_by}
                                                      for meta in stale_cadence], precommit_guard=guard)
                    outcomes.extend(_reused_assertion_outcome(metadata) for metadata, _payload in retained)
            elif stale_cadence:
                # No successor payload is needed, but withdrawal remains one
                # serialized all-or-nothing lifecycle batch.
                repo.save_batch([], supersessions=[{"artifact_id": meta.artifact_id, "actor": created_by}
                                                   for meta in stale_cadence],
                                precommit_guard=lambda conn=None: (precommit_guard() if precommit_guard else None, _revalidate_cadence_batch(
                                repo, asset_id=reference.asset_id, snapshot_id=snapshot_id, table=table,
                                    expected_plan=cadence_plan, stale=stale_cadence, expected_locators={}, conn=conn)))
        payloads: list[dict[str, Any]] = []
        lifecycle_supersessions: list[dict[str, Any]] = []
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
                        repo, retained_payload, created_by=created_by,
                        precommit_guard=precommit_guard))
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
        if _TEMPORAL_PREDICATE in requested_predicates:
            temporal_inventory_metadata, _temporal_inventory_payload = _temporal_inventory_profile(
                repo, snapshot_id, reference.asset_id, table, table_payload)
            payloads.extend(_temporal_payload(reference.asset_id, snapshot_id, table, temporal_inventory_metadata, metadata, profile)
                            for metadata, profile in profiles
                            if profile.get("metadata_reviewed") is True
                            and str(profile.get("role") or "").strip().lower() in {"date", "period"})
            # Withdrawal is candidate-local.  Do not let a stale producer
            # proposal remain active after its reviewed temporal eligibility
            # disappears, and do not touch independently authored assertions.
            temporal_keys = {payload["instance_key"] for payload in payloads
                             if payload["predicate"] == _TEMPORAL_PREDICATE}
            for prior_metadata, prior_payload in _producer_locator_candidates(
                    repo, snapshot_id=snapshot_id, asset_id=reference.asset_id, table=table,
                    predicate=_TEMPORAL_PREDICATE, temporal_producer_only=True):
                if prior_payload["instance_key"] not in temporal_keys:
                    lifecycle_supersessions.append({"artifact_id": prior_metadata.artifact_id,
                                                     "actor": created_by})
        if _GRAIN_PREDICATE in requested_predicates and reuse_grain is None:
            payloads.append(_grain_payload(reference.asset_id, snapshot_id, table, table_metadata, table_payload,
                                            reviewed_ids, reviewed_temporal, snapshot_loader))
        for write_index, payload in enumerate(payloads):
            prior = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=reference.asset_id,
                                                 table=table, predicate=payload["predicate"],
                                                 instance_key=payload["instance_key"],
                                                 temporal_producer_only=(payload["predicate"] == _TEMPORAL_PREDICATE))
            if len(prior) > 1: _observation_failure("DSC_R_AMBIGUOUS_CANDIDATES")
            if prior and prior[0][1] != payload:
                lifecycle_supersessions.append({"artifact_id": prior[0][0].artifact_id,
                                                 "by_write_index": write_index, "actor": created_by})
        if payloads or lifecycle_supersessions:
            outcomes.extend(repo.save_batch(
                [_assertion_batch_entry(payload, created_by=created_by) for payload in payloads],
                supersessions=lifecycle_supersessions,
                precommit_guard=precommit_guard,
            ))
    return tuple(outcomes)


def _producer_locator_candidates(repo: Any, *, snapshot_id: str, asset_id: str,
                                 table: str, predicate: str | None = None,
                                 instance_key: str | None = None,
                                 temporal_producer_only: bool = False,
                                 conn: Any = None) -> list[tuple[Any, dict[str, Any]]]:
    """Return atomic producer candidates without treating arbitrary claims as ours."""
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type=_ASSERTION_TYPE, status="active"):
        if metadata.asset_id != asset_id or metadata.scope != "universal":
            continue
        try:
            _checked, payload = _repository_get(repo, metadata.artifact_id, conn=conn)
        except Exception:
            _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
        if (isinstance(payload, dict) and (predicate is None or payload.get("predicate") == predicate)
                and payload.get("subject") == {"kind": "table", "table": table}
                and (instance_key is None or payload.get("instance_key") == instance_key)
                and (not temporal_producer_only or _is_temporal_producer_payload(repo, payload, conn=conn))):
            candidates.append((metadata, payload))
    return candidates


def _is_temporal_producer_payload(repo: Any, payload: dict[str, Any], *, conn: Any = None) -> bool:
    """Recognise only a complete, immutable C2 profile-provenance payload."""
    claims = payload.get("claims")
    evidence = payload.get("evidence")
    snapshot, subject, instance_key = payload.get("snapshot"), payload.get("subject"), payload.get("instance_key")
    expected_id = (_sha_id("dsca", {"schema_version": 1, "context_version": "1",
                    "snapshot": snapshot, "subject": subject, "predicate": _TEMPORAL_PREDICATE,
                    "instance_key": instance_key})
                   if isinstance(snapshot, dict) and isinstance(subject, dict) and isinstance(instance_key, str) else None)
    if not (payload.get("predicate") == _TEMPORAL_PREDICATE
            and payload.get("assertion_id") == expected_id
            and payload.get("resolution", {}).get("status") == "proposed"
            and isinstance(claims, list) and len(claims) == 1
            and claims[0].get("authority") == "source_reviewed_metadata"
            and isinstance(evidence, list) and len(evidence) == 1
            and evidence[0].get("kind") == "reviewed_metadata"
            and isinstance(snapshot, dict) and isinstance(subject, dict)
            and isinstance(instance_key, str)):
        return False
    refs = evidence[0].get("source_refs")
    if not isinstance(refs, list) or len(refs) != 2:
        return False
    by_role = {ref.get("role"): ref for ref in refs if isinstance(ref, dict)}
    if set(by_role) != {"reviewed_metadata", "dependency"}:
        return False
    try:
        column_metadata, column_profile = _repository_get(repo, by_role["reviewed_metadata"].get("artifact_id"), conn=conn)
        inventory_metadata, inventory_payload = _repository_get(repo, by_role["dependency"].get("artifact_id"), conn=conn)
    except Exception:
        _observation_failure("DSC_R_SOURCE_INTEGRITY_FAILED")
    if (column_metadata.artifact_type != "column_profile"
            or inventory_metadata.artifact_type != "table_inventory_profile"
            or column_metadata.payload_hash != by_role["reviewed_metadata"].get("payload_hash")
            or inventory_metadata.payload_hash != by_role["dependency"].get("payload_hash")
            or column_metadata.asset_id != snapshot.get("asset_id")
            or inventory_metadata.asset_id != snapshot.get("asset_id")
            or column_metadata.snapshot_id != snapshot.get("snapshot_id")
            or inventory_metadata.snapshot_id != snapshot.get("snapshot_id")
            or column_metadata.identity.get("table") != subject.get("table")
            or inventory_metadata.identity.get("table") != subject.get("table")
            or column_metadata.feature != instance_key.removeprefix("column:")
            or not isinstance(column_profile, dict) or not isinstance(inventory_payload, dict)):
        return False
    try:
        expected = _temporal_payload(snapshot["asset_id"], snapshot["snapshot_id"], subject["table"],
                                     inventory_metadata, column_metadata, column_profile)
    except DatasetStructureObservationError:
        return False
    # Exact reconstruction validates source refs/roles/hashes, the stable
    # inventory witness, every policy/version dependency input, and its hash.
    return payload == expected


def _matching_supported_assertions(repo: Any, *, snapshot_id: str, asset_id: str,
                                  table: str, predicate: str) -> list[tuple[Any, dict[str, Any]]] | None:
    """Reuse only the complete current projection; optional never observes."""
    # An optional selector with no materialized assertion must not require
    # profiles (or turn their absence into a source error).  Only validate and
    # recompute dependencies after there is something eligible to reuse.
    actual = _producer_locator_candidates(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                          table=table, predicate=predicate,
                                          temporal_producer_only=(predicate == _TEMPORAL_PREDICATE))
    if not actual:
        return None
    table_metadata, table_payload, profiles = _profiles_for_table(repo, snapshot_id, asset_id, table)
    if predicate == _CADENCE_PREDICATE:
        plan = _cadence_plan(repo, asset_id=asset_id, snapshot_id=snapshot_id, table=table,
                             table_metadata=table_metadata, table_payload=table_payload, profiles=profiles)
        if not plan:
            return None
        retained = [_cadence_existing(repo, asset_id=asset_id, snapshot_id=snapshot_id, table=table,
                                      candidate=candidate) for candidate in plan]
        return ([item for item in retained if item is not None]
                if all(item is not None for item in retained) else None)
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
        elif predicate == _TEMPORAL_PREDICATE:
            temporal_inventory_metadata, _temporal_inventory_payload = _temporal_inventory_profile(
                repo, snapshot_id, asset_id, table, table_payload)
            expected = [_temporal_payload(asset_id, snapshot_id, table, temporal_inventory_metadata, metadata, profile)
                        for metadata, profile in profiles
                        if profile.get("metadata_reviewed") is True
                        and str(profile.get("role") or "").strip().lower() in {"date", "period"}]
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
