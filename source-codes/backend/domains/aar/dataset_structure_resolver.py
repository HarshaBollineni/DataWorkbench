"""Dataset Structure Context request resolution.

The resolver owns request negotiation, observation policy and context pinning;
the producer owns all profile interpretation and assertion construction.
"""
from __future__ import annotations

from typing import Any, Callable

import system_db as db
from analysis_runtime.dataset_structure_context import (
    DSCContractError, ERROR_SNAPSHOT_MISMATCH, PREDICATES_BY_CONTEXT_VERSION, V2_ONLY_PREDICATES, assemble_context,
    assemble_response, validate_resolution_request,
)
from analysis_runtime.snapshots import SnapshotLoader

from .dataset_structure_producer import (
    DatasetStructureObservationError, SUPPORTED_OBSERVER_PREDICATES,
    _matching_supported_assertions, observe_dataset_structure,
    save_dataset_structure_context,
)


def _pin(metadata: Any, payload: dict[str, Any], disposition: str) -> dict[str, str]:
    return {"artifact_id": metadata.artifact_id, "assertion_id": payload["assertion_id"],
            "payload_hash": metadata.payload_hash, "resolution": payload["resolution"]["status"],
            "reuse_disposition": disposition}


def _active_v2_authority(repo: Any, *, snapshot_id: str, asset_id: str, table: str, predicate: str) -> list[tuple[Any, dict[str, Any]]] | None:
    """Read only retained v2 authority whose pinned v1 inputs are still exact."""
    found = []
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type="dataset_structure_assertion", status="active", scope="universal"):
        try:
            checked, payload = repo.get(metadata.artifact_id)
        except Exception:
            continue
        if (checked.asset_id != asset_id or payload.get("context_version") != "2"
                or payload.get("subject", {}).get("table") != table or payload.get("predicate") != predicate):
            continue
        claim = (payload.get("claims") or [None])[0]
        value = claim.get("value") if isinstance(claim, dict) else None
        pins = []
        if isinstance(value, dict) and isinstance(value.get("candidate_pin"), dict): pins.append(value["candidate_pin"])
        if isinstance(value, dict) and isinstance(value.get("axis"), dict): pins.append(value["axis"].get("pin"))
        if isinstance(value, dict) and isinstance(value.get("grouping"), dict): pins.append(value["grouping"].get("pin"))
        exact = True
        for pin in pins:
            if not isinstance(pin, dict): exact = False; break
            try: source_meta, source_payload = repo.get(pin["artifact_id"])
            except Exception: exact = False; break
            if (source_meta.status != "active" or source_meta.asset_id != asset_id or source_meta.snapshot_id != snapshot_id
                    or source_meta.payload_hash != pin.get("payload_hash") or source_payload.get("assertion_id") != pin.get("assertion_id")
                    or source_payload.get("dependency_fingerprint") != pin.get("dependency_fingerprint")):
                exact = False; break
            # A matching v1 assertion hash alone is not enough: its governed
            # profile/inventory dependency chain may have since changed.  The
            # v1 reuse validator already reconstructs that exact chain, so
            # reuse it instead of maintaining a weaker parallel check here.
            source_subject = source_payload.get("subject")
            source_predicate = source_payload.get("predicate")
            if (not isinstance(source_subject, dict) or source_subject.get("kind") != "table"
                    or source_subject.get("table") != table
                    or source_predicate not in SUPPORTED_OBSERVER_PREDICATES):
                exact = False; break
            try:
                retained = _matching_supported_assertions(repo, snapshot_id=snapshot_id, asset_id=asset_id,
                                                           table=table, predicate=source_predicate)
            except DatasetStructureObservationError:
                exact = False; break
            if retained is None or not any(
                candidate_meta.artifact_id == source_meta.artifact_id
                and candidate_meta.payload_hash == source_meta.payload_hash
                and candidate_payload == source_payload
                for candidate_meta, candidate_payload in retained
            ):
                exact = False; break
        if exact: found.append((checked, payload))
    return sorted(found, key=lambda item: item[1].get("instance_key", "")) or None


def resolve_dataset_structure_context(repo: Any, request: Any, *, snapshot_loader: SnapshotLoader | None = None,
                                      clock: Callable[[], str] = db.now_ist,
                                      created_by: str | None = None) -> dict[str, Any]:
    """Resolve and persist one consistently pinned DSC v1 context."""
    loader = snapshot_loader or SnapshotLoader()
    try:
        preliminary = validate_resolution_request(request)
    except DSCContractError:
        raise
    try:
        reference = loader.reference(preliminary["snapshot"]["snapshot_id"])
    except Exception as exc:
        raise DSCContractError("snapshot cannot be resolved", ERROR_SNAPSHOT_MISMATCH) from exc
    normalized = validate_resolution_request(preliminary, reference)

    materialized: dict[str, set[str]] = {}
    for selector in normalized["selectors"]:
        if selector["predicate"] in SUPPORTED_OBSERVER_PREDICATES and selector["requirement"] in {"required", "advisory"}:
            materialized.setdefault(selector["subject"]["table"], set()).add(selector["predicate"])
    observed: dict[tuple[str, str], list[tuple[Any, dict[str, Any], str]]] = {}
    errors: dict[tuple[str, str], str] = {}
    for table, predicates in sorted(materialized.items()):
        for predicate in sorted(predicates):
            try:
                produced = observe_dataset_structure(repo, reference.snapshot_id, tables=[table],
                                                     predicates=(predicate,), snapshot_loader=loader,
                                                     created_by=created_by)
                for outcome in produced:
                    metadata, payload = repo.get(outcome.artifact.artifact_id)
                    observed.setdefault((table, payload["predicate"]), []).append(
                        (metadata, payload, "fresh" if outcome.outcome == "created" else "exact_reused"))
                if (table, predicate) in observed:
                    observed[(table, predicate)].sort(key=lambda item: item[1]["instance_key"])
            except DatasetStructureObservationError as exc:
                errors[(table, predicate)] = exc.reason_code

    context_version = "2" if "2" in normalized["supported_context_versions"] else "1"
    results: list[dict[str, Any]] = []; sensitivities: list[str] = []
    for selector in normalized["selectors"]:
        predicate, requirement, selector_id = selector["predicate"], selector["requirement"], selector["selector_id"]
        if predicate not in PREDICATES_BY_CONTEXT_VERSION[context_version]:
            results.append({"selector_id": selector_id, "result": "unsupported", "reason_codes": ["DSC_R_UNSUPPORTED_FACET"]}); continue
        if predicate in V2_ONLY_PREDICATES:
            if context_version != "2":
                results.append({"selector_id": selector_id, "result": "unsupported", "reason_codes": ["DSC_R_UNSUPPORTED_FACET"]}); continue
            items = _active_v2_authority(repo, snapshot_id=reference.snapshot_id, asset_id=reference.asset_id,
                                         table=selector["subject"]["table"], predicate=predicate)
            if not items or any(payload["resolution"]["status"] not in selector["accepted_resolution_states"] for _, payload in items):
                results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": ["DSC_R_NO_EVIDENCE"]}); continue
            results.append({"selector_id": selector_id, "result": "fulfilled", "pins": [_pin(metadata, payload, "exact_reused") for metadata, payload in items]})
            sensitivities.extend(payload["sensitivity"] for _, payload in items)
            continue
        if predicate not in SUPPORTED_OBSERVER_PREDICATES:
            reason = "DSC_R_RELATIONSHIP_UNDECLARED" if predicate == "relationship.declared/relationship" else "DSC_R_NO_EVIDENCE"
            results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": [reason]}); continue
        table = selector["subject"]["table"]
        items = observed.get((table, predicate))
        if items is None and (table, predicate) in errors:
            results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": [errors[(table, predicate)]]}); continue
        if items is None:
            try:
                existing = _matching_supported_assertions(repo, snapshot_id=reference.snapshot_id,
                                                          asset_id=reference.asset_id, table=table, predicate=predicate)
            except DatasetStructureObservationError as exc:
                # Optional cadence is strictly non-materializing.  A partial,
                # stale, or no-longer-verifiable retained set is not allowed to
                # disclose prerequisite integrity detail through an optional
                # selector; it is simply not materialized.
                if requirement == "optional" and predicate == "table.temporal/observed_cadence":
                    results.append({"selector_id": selector_id, "result": "unavailable",
                                    "reason_codes": ["DSC_R_OPTIONAL_NOT_MATERIALIZED"]})
                    continue
                results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": [exc.reason_code]}); continue
            if existing is None:
                reason = "DSC_R_OPTIONAL_NOT_MATERIALIZED" if requirement == "optional" else "DSC_R_NO_EVIDENCE"
                results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": [reason]}); continue
            items = [(metadata, payload, "exact_reused") for metadata, payload in existing]
        if not items or any(payload["resolution"]["status"] not in selector["accepted_resolution_states"] for _, payload, _ in items):
            results.append({"selector_id": selector_id, "result": "unavailable", "reason_codes": ["DSC_R_STATE_NOT_ACCEPTED"]}); continue
        results.append({"selector_id": selector_id, "result": "fulfilled",
                        "pins": [_pin(metadata, payload, disposition) for metadata, payload, disposition in items]})
        sensitivities.extend(payload["sensitivity"] for _, payload, _ in items)
    sensitivity = max(sensitivities or ["external_safe"], key=("external_safe", "internal", "confidential").index)
    context = assemble_context(normalized, results, resolved_as_of=clock(), sensitivity=sensitivity)
    saved = save_dataset_structure_context(repo, context, consumer_id=normalized["consumer_id"], created_by=created_by)
    return assemble_response({"artifact_id": saved.artifact.artifact_id, "payload_hash": saved.artifact.payload_hash}, context)


__all__ = ["resolve_dataset_structure_context"]
