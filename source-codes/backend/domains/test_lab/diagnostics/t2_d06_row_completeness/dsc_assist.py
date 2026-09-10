"""Fail-open, draft-only DSC assist for D06 scope review.

This is not the cadence shadow adapter.  It reads only active, confirmed v2
authority assertions for one tenant-owned immutable snapshot and returns
editable role suggestions.  It never changes D06 execution semantics.
"""
from __future__ import annotations

import os
from typing import Any

from domains.aar.repository import AnalysisArtifactRepository
import tenancy

_ASSERTION = "dataset_structure_assertion"
_FLAG = "D06_DSC_ASSIST_ENABLED"


def _enabled(tenant_id: str) -> bool:
    """Development is default-on; deployments can explicitly control rollout."""
    environment = os.getenv("DATAWORKBENCH_ENV", "development").strip().lower()
    if environment in {"development", "dev", "test"}:
        return True
    try:
        return tenancy.is_flag_enabled(tenant_id, _FLAG)
    except Exception:
        return False


def _effective(payload: dict[str, Any]) -> dict[str, Any] | None:
    ids = payload.get("resolution", {}).get("effective_claim_ids")
    claims = payload.get("claims")
    if payload.get("resolution", {}).get("status") != "confirmed" or not isinstance(ids, list) or len(ids) != 1 or not isinstance(claims, list):
        return None
    return next((claim for claim in claims if claim.get("claim_id") == ids[0]), None)


def _candidate(repo: AnalysisArtifactRepository, value: dict[str, Any], *, snapshot: dict[str, str],
               predicate: str, table: str) -> dict[str, Any] | None:
    locator, pin = value.get("candidate_locator"), value.get("candidate_pin")
    if not isinstance(locator, dict) or not isinstance(pin, dict) or locator.get("predicate") != predicate:
        return None
    try:
        metadata, payload = repo.get(pin["artifact_id"])
    except Exception:
        return None
    if (metadata.status != "active" or metadata.artifact_type != _ASSERTION
            or metadata.asset_id != snapshot["asset_id"] or metadata.snapshot_id != snapshot["snapshot_id"]
            or metadata.payload_hash != pin.get("payload_hash") or payload.get("assertion_id") != pin.get("assertion_id")
            or payload.get("dependency_fingerprint") != pin.get("dependency_fingerprint")
            or payload.get("snapshot") != snapshot or payload.get("subject", {}).get("table") != table
            or payload.get("predicate") != predicate or payload.get("instance_key") != locator.get("instance_key")):
        return None
    ids, claims = payload.get("resolution", {}).get("effective_claim_ids"), payload.get("claims")
    if (payload.get("resolution", {}).get("status") not in {"observed", "proposed", "confirmed"}
            or not isinstance(ids, list) or len(ids) != 1 or not isinstance(claims, list)):
        return None
    return next((claim for claim in claims if claim.get("claim_id") == ids[0]), None)


def _pinned_candidate(repo: AnalysisArtifactRepository, locator: Any, pin: Any, *,
                      snapshot: dict[str, str], predicate: str, table: str) -> dict[str, Any] | None:
    """Revalidate the embedded axis/group candidate pin before using cadence."""
    return _candidate(repo, {"candidate_locator": locator, "candidate_pin": pin},
                      snapshot=snapshot, predicate=predicate, table=table)


def resolved_d06_assist(*, item: dict[str, Any], table: str) -> dict[str, Any]:
    """Return exact-table confirmed suggestions, or a closed fail-open state."""
    if not item.get("sourcing_tenant_id") or not item.get("item_id") or not item.get("dataset_family_id"):
        return {"state": "unavailable"}
    if not _enabled(str(item["sourcing_tenant_id"])):
        return {"state": "disabled"}
    snapshot = {"asset_id": item["dataset_family_id"], "snapshot_id": item["item_id"]}
    repo = AnalysisArtifactRepository()
    authorities: dict[str, dict[str, Any]] = {}
    try:
        rows = repo.list(snapshot_id=item["item_id"], artifact_type=_ASSERTION, status="active")
        for metadata in rows:
            # The server has already bound this item to the requesting tenant.
            # AAR is tenant-neutral, so both the authority and every pin must
            # nevertheless be physically exact to that owned snapshot.
            if metadata.asset_id != snapshot["asset_id"] or metadata.snapshot_id != snapshot["snapshot_id"]:
                continue
            payload = repo.get(metadata.artifact_id)[1]
            if (payload.get("context_version") != "2" or payload.get("snapshot") != snapshot
                    or payload.get("subject", {}).get("table") != table):
                continue
            predicate = payload.get("predicate")
            if predicate not in {"table.structure/default_entity_binding", "table.temporal/default_temporal_binding", "table.temporal/expected_cadence"}:
                continue
            if predicate in authorities or _effective(payload) is None:
                # Multiple active authorities are ambiguous; do not select one.
                if predicate in authorities:
                    return {"state": "ambiguous"}
                continue
            authorities[predicate] = payload
    except Exception:
        return {"state": "unavailable"}
    entity = authorities.get("table.structure/default_entity_binding")
    temporal = authorities.get("table.temporal/default_temporal_binding")
    if entity is None or temporal is None:
        return {"state": "unavailable"}
    entity_claim, temporal_claim = _effective(entity), _effective(temporal)
    entity_source = _candidate(repo, entity_claim["value"], snapshot=snapshot,
                               predicate="table.structure/entity_binding", table=table)
    temporal_source = _candidate(repo, temporal_claim["value"], snapshot=snapshot,
                                 predicate="table.temporal/temporal_binding", table=table)
    if entity_source is None or temporal_source is None:
        return {"state": "unavailable"}
    entity_columns = entity_source.get("value", {}).get("columns")
    temporal_columns = temporal_source.get("value", {}).get("columns")
    if not (isinstance(entity_columns, list) and len(entity_columns) == 1 and isinstance(temporal_columns, list) and len(temporal_columns) == 1):
        return {"state": "unavailable"}
    result: dict[str, Any] = {"state": "available", "entity_column": entity_columns[0].get("column"),
                              "period_column": temporal_columns[0].get("column"), "expected_cadence": None,
                              "provenance": "confirmed_dataset_structure"}
    cadence = authorities.get("table.temporal/expected_cadence")
    cadence_claim = _effective(cadence) if cadence else None
    cadence_value = cadence_claim.get("value") if cadence_claim else None
    if isinstance(cadence_value, dict) and cadence_value.get("kind") == "expected_cadence" and cadence_value.get("selection") != "none":
        axis = cadence_value.get("axis")
        grouping = cadence_value.get("grouping")
        axis_valid = isinstance(axis, dict) and _pinned_candidate(
            repo, axis.get("locator"), axis.get("pin"), snapshot=snapshot,
            predicate="table.temporal/temporal_binding", table=table,
        ) is not None
        grouping_valid = grouping is None or (
            isinstance(grouping, dict) and _pinned_candidate(
                repo, grouping.get("locator"), grouping.get("pin"), snapshot=snapshot,
                predicate="table.structure/entity_binding", table=table,
            ) is not None
        )
        if axis_valid and grouping_valid:
            result["expected_cadence"] = {"unit": cadence_value.get("unit"), "step": cadence_value.get("step")}
    if not isinstance(result["entity_column"], str) or not isinstance(result["period_column"], str):
        return {"state": "unavailable"}
    return result
