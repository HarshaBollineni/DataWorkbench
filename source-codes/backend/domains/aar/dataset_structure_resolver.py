"""Dataset Structure Context request resolution.

The resolver owns request negotiation, observation policy and context pinning;
the producer owns all profile interpretation and assertion construction.
"""
from __future__ import annotations

from typing import Any, Callable

import system_db as db
from analysis_runtime.dataset_structure_context import (
    DSCContractError, ERROR_SNAPSHOT_MISMATCH, PREDICATES, assemble_context,
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
                observed.setdefault((table, predicate), []).sort(key=lambda item: item[1]["instance_key"])
            except DatasetStructureObservationError as exc:
                errors[(table, predicate)] = exc.reason_code

    results: list[dict[str, Any]] = []; sensitivities: list[str] = []
    for selector in normalized["selectors"]:
        predicate, requirement, selector_id = selector["predicate"], selector["requirement"], selector["selector_id"]
        if predicate not in PREDICATES:
            results.append({"selector_id": selector_id, "result": "unsupported", "reason_codes": ["DSC_R_UNSUPPORTED_FACET"]}); continue
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
