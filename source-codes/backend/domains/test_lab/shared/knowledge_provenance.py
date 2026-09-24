"""Shared DSC and Knowledge Base provenance for diagnostic manifests."""
from __future__ import annotations

from typing import Any

import system_db as db
from analysis_runtime.dataset_structure_context import DSCContractError
from domains.aar.dataset_structure_resolver import resolve_dataset_structure_context
from domains.aar.repository import AnalysisArtifactRepository


def knowledge_reference(*, knowledge_base_id: str, version_id: str,
                        consumer_id: str, version_label: str | None = None,
                        content_hash: str | None = None) -> dict[str, Any]:
    """Return the immutable document-version pin used by a diagnostic."""
    version = db.query_one("kb_document_versions", version_id=version_id)
    if version and version.get("document_id") != knowledge_base_id:
        raise RuntimeError(f"knowledge version {version_id} belongs to another knowledge base")
    if not version and (not version_label or not content_hash):
        raise RuntimeError(f"knowledge version {version_id} is not installed")
    report = (version or {}).get("conversion_report_json") or {}
    return {
        "knowledge_base_id": knowledge_base_id,
        "version_id": version_id,
        "version_label": str(
            report.get("kb_version") or report.get("terminology_version")
            or (version or {}).get("version_seq") or version_label
        ),
        "content_hash": (version or {}).get("original_sha256") or content_hash,
        "consumer_id": consumer_id,
    }


def selector(selector_id: str, table: str, predicate: str, *,
             requirement: str = "advisory",
             accepted_states: tuple[str, ...] = ("confirmed",)) -> dict[str, Any]:
    return {
        "selector_id": selector_id,
        "subject": {"kind": "table", "table": table},
        "predicate": predicate,
        "requirement": requirement,
        "accepted_resolution_states": list(accepted_states),
    }


def dsc_request_from_execution_context(
        execution_context: dict[str, Any], table: str) -> tuple[str, list[dict[str, Any]]]:
    """Project a validated KB DSC declaration into a table-bound request."""
    contract = execution_context.get("dataset_structure_context")
    if not isinstance(contract, dict):
        raise ValueError("execution_context.dataset_structure_context is required")
    consumer_id = str(contract.get("consumer_id") or "").strip()
    declarations = contract.get("selectors")
    if not consumer_id or not isinstance(declarations, list) or not declarations:
        raise ValueError("the DSC execution contract requires a consumer and selectors")
    selectors = []
    for declaration in declarations:
        selectors.append(selector(
            str(declaration["selector_id"]), table, str(declaration["predicate"]),
            requirement=str(declaration.get("requirement") or "advisory"),
            accepted_states=tuple(declaration.get("accepted_resolution_states") or ["confirmed"]),
        ))
    return consumer_id, selectors


def validate_dsc_execution_context(
        execution_context: Any, *, consumer_id: str,
        expected_selectors: dict[str, tuple[str, str, tuple[str, ...], str]]) -> None:
    """Fail closed when a KB attempts to change the supported DSC boundary."""
    if not isinstance(execution_context, dict):
        raise ValueError("execution_context must be a mapping")
    contract = execution_context.get("dataset_structure_context")
    if not isinstance(contract, dict) or str(contract.get("contract_version")) != "2":
        raise ValueError("execution_context requires Dataset Structure Context contract version 2")
    if contract.get("consumer_id") != consumer_id:
        raise ValueError("execution_context contains an unsupported DSC consumer_id")
    if contract.get("unavailable_behavior") != "require_visible_local_configuration":
        raise ValueError("execution_context contains an unsupported DSC unavailable behavior")
    if contract.get("pin_resolved_context_in_run_manifest") is not True:
        raise ValueError("execution_context must pin resolved DSC in the run manifest")
    local = execution_context.get("local_changes")
    if not isinstance(local, dict) or local.get("scope") != "run_only" \
            or local.get("changes_knowledge_base") is not False:
        raise ValueError("execution_context local changes must remain run-only")
    declarations = contract.get("selectors")
    if not isinstance(declarations, list):
        raise ValueError("execution_context DSC selectors must be a list")
    actual: dict[str, tuple[str, str, tuple[str, ...], str]] = {}
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise ValueError("each execution_context DSC selector must be a mapping")
        selector_id = str(declaration.get("selector_id") or "")
        if selector_id in actual:
            raise ValueError(f"duplicate execution_context selector: {selector_id}")
        actual[selector_id] = (
            str(declaration.get("predicate") or ""),
            str(declaration.get("requirement") or "advisory"),
            tuple(declaration.get("accepted_resolution_states") or ["confirmed"]),
            str(declaration.get("maps_to") or ""),
        )
    if actual != expected_selectors:
        raise ValueError("execution_context DSC selectors do not match the supported engine contract")


def _effective_value(repo: AnalysisArtifactRepository,
                     pins: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(pins) != 1:
        return None
    metadata, payload = repo.get(pins[0]["artifact_id"])
    pin = pins[0]
    if (metadata.payload_hash != pin.get("payload_hash")
            or payload.get("assertion_id") != pin.get("assertion_id")):
        return None
    effective = payload.get("resolution", {}).get("effective_claim_ids") or []
    if len(effective) != 1:
        return None
    claim = next(
        (row for row in payload.get("claims") or [] if row.get("claim_id") == effective[0]),
        None,
    )
    return claim.get("value") if claim else None


def resolve_dsc(*, item: dict[str, Any], table: str, consumer_id: str,
                selectors: list[dict[str, Any]], actor: str,
                observe_missing: bool = True) -> dict[str, Any]:
    """Resolve and pin a bounded DSC context without exposing raw data."""
    snapshot = {
        "asset_id": item.get("dataset_family_id"),
        "snapshot_id": item.get("item_id"),
    }
    if not all(snapshot.values()):
        return {
            "state": "unavailable", "consumer_id": consumer_id,
            "reason_code": "snapshot_identity_unavailable", "selector_results": [],
        }
    repo = AnalysisArtifactRepository()
    request = {
        "protocol_version": "1", "supported_context_versions": ["2", "1"],
        "snapshot": snapshot, "as_of": "latest", "tables": [table],
        "selectors": selectors, "consumer_id": consumer_id,
    }
    try:
        response = resolve_dataset_structure_context(
            repo, request, created_by=actor, observe_missing=observe_missing,
        )
    except DSCContractError as exc:
        return {
            "state": "unavailable", "consumer_id": consumer_id,
            "reason_code": exc.code, "selector_results": [],
        }
    projected = []
    for result in response["selector_results"]:
        projected.append({
            **result,
            "value": _effective_value(repo, result.get("pins") or [])
            if result.get("result") == "fulfilled" else None,
        })
    return {
        "state": response["overall_result"],
        "consumer_id": consumer_id,
        "protocol_version": response["negotiated"]["protocol_version"],
        "context_version": response["negotiated"]["context_version"],
        "context_ref": response["context_ref"],
        "snapshot": snapshot,
        "selected_tables": [table],
        "selector_results": projected,
    }


def selector_value(context: dict[str, Any], selector_id: str) -> dict[str, Any] | None:
    row = next(
        (item for item in context.get("selector_results") or []
         if item.get("selector_id") == selector_id and item.get("result") == "fulfilled"),
        None,
    )
    return row.get("value") if row else None


def default_binding_column(context: dict[str, Any], selector_id: str) -> str | None:
    """Follow a confirmed default authority to its pinned candidate column."""
    value = selector_value(context, selector_id)
    pin = value.get("candidate_pin") if isinstance(value, dict) else None
    if not isinstance(pin, dict):
        return None
    repo = AnalysisArtifactRepository()
    try:
        metadata, payload = repo.get(pin["artifact_id"])
    except (KeyError, OSError, ValueError):
        return None
    if (metadata.payload_hash != pin.get("payload_hash")
            or payload.get("assertion_id") != pin.get("assertion_id")):
        return None
    effective = payload.get("resolution", {}).get("effective_claim_ids") or []
    claim = next(
        (row for row in payload.get("claims") or [] if row.get("claim_id") in effective),
        None,
    )
    columns = ((claim or {}).get("value") or {}).get("columns") or []
    return columns[0].get("column") if len(columns) == 1 else None
