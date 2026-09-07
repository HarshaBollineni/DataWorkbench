"""Append-only provenance for deterministic and LLM inference in diagnostics.

The ledger records advisory model usage without placing raw prompts, credentials,
or row-level data in the system database. Diagnostic engines never consume these
events as calculation inputs; a user-approved manifest is the only execution
boundary.
"""
from __future__ import annotations

import uuid
from typing import Any

import system_db as db
from analysis_runtime.contracts import stable_fingerprint


_LLM_STATUSES = {"skipped", "succeeded", "failed"}


def _require_run(run_id: str) -> None:
    if not db.query_one("diag_runs", run_id=run_id):
        raise KeyError(f"Unknown diagnostic run: {run_id}")


def _non_negative(name: str, value: int | None) -> int | None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _insert(
    *,
    run_id: str,
    event_kind: str,
    stage: str,
    purpose: str,
    status: str,
    invoked: bool,
    payload: dict[str, Any],
    actor: str,
    provider: str | None = None,
    model: str | None = None,
    provider_api_version: str | None = None,
    prompt_template_id: str | None = None,
    prompt_template_version: str | None = None,
    prompt_hash: str | None = None,
    input_hash: str | None = None,
    response_hash: str | None = None,
    provider_request_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    row_level_data_included: bool = False,
) -> dict[str, Any]:
    _require_run(run_id)
    if not stage.strip() or not purpose.strip() or not actor.strip():
        raise ValueError("stage, purpose and actor are required")
    event_id = f"inf_{uuid.uuid4().hex[:16]}"
    row = {
        "event_id": event_id,
        "run_id": run_id,
        "event_kind": event_kind,
        "stage": stage.strip(),
        "purpose": purpose.strip(),
        "status": status,
        "invoked": int(invoked),
        "provider": provider,
        "model": model,
        "provider_api_version": provider_api_version,
        "prompt_template_id": prompt_template_id,
        "prompt_template_version": prompt_template_version,
        "prompt_hash": prompt_hash,
        "input_hash": input_hash,
        "response_hash": response_hash,
        "provider_request_id": provider_request_id,
        "prompt_tokens": _non_negative("prompt_tokens", prompt_tokens),
        "completion_tokens": _non_negative("completion_tokens", completion_tokens),
        "latency_ms": _non_negative("latency_ms", latency_ms),
        "row_level_data_included": int(row_level_data_included),
        "verdict_influenced": 0,
        "payload_json": payload,
        "actor": actor.strip(),
        "ts": db.now_ist(),
    }
    db.insert("diag_inference_events", row)
    return db.query_one("diag_inference_events", event_id=event_id) or row


def record_zero_llm_usage(
    run_id: str,
    *,
    actor: str,
    reason: str = "disabled by default",
    purpose: str = "semantic_role_verification",
) -> dict[str, Any]:
    """Record an explicit zero-call decision instead of relying on missing rows."""
    return _insert(
        run_id=run_id,
        event_kind="llm",
        stage="pre_manifest_freeze",
        purpose=purpose,
        status="skipped",
        invoked=False,
        payload={"reason": reason, "metrics_produced": False, "verdict_changed": False},
        actor=actor,
    )


def record_deterministic_inference(
    run_id: str,
    *,
    inference_type: str,
    methodology_version: str,
    input_references: list[dict[str, Any]],
    evidence_summary: dict[str, Any],
    proposed_value: Any,
    final_value: Any,
    disposition: str,
    actor: str,
    validation_status: str = "valid",
) -> dict[str, Any]:
    payload = {
        "inference_type": inference_type,
        "methodology_version": methodology_version,
        "input_references": input_references,
        "input_hash": stable_fingerprint(input_references),
        "evidence_summary": evidence_summary,
        "proposed_value": proposed_value,
        "final_value": final_value,
        "disposition": disposition,
        "validation_status": validation_status,
    }
    return _insert(
        run_id=run_id,
        event_kind="deterministic",
        stage="scope_configuration",
        purpose=inference_type,
        status="recorded",
        invoked=False,
        payload=payload,
        actor=actor,
        input_hash=payload["input_hash"],
    )


def record_llm_call(
    run_id: str,
    *,
    status: str,
    provider: str,
    model: str,
    provider_api_version: str,
    prompt_template_id: str,
    prompt_template_version: str,
    prompt_hash: str,
    redacted_input_manifest: dict[str, Any],
    validated_response: dict[str, Any] | None,
    source_artifact_references: list[dict[str, str]],
    proposed_mapping: dict[str, Any] | None,
    deterministic_mapping: dict[str, Any],
    user_disposition: str,
    final_applied_mapping: dict[str, Any],
    actor: str,
    purpose: str = "semantic_role_verification",
    provider_request_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    retry_count: int = 0,
    sanitized_error: str | None = None,
    row_level_data_included: bool = False,
) -> dict[str, Any]:
    """Persist one bounded role-verification call without raw prompt content."""
    if status not in _LLM_STATUSES - {"skipped"}:
        raise ValueError("an invoked LLM call status must be 'succeeded' or 'failed'")
    if row_level_data_included:
        raise ValueError("semantic-role verification must not include row-level data")
    _non_negative("retry_count", retry_count)
    input_hash = stable_fingerprint(redacted_input_manifest)
    response_hash = stable_fingerprint(validated_response) if validated_response is not None else None
    payload = {
        "redacted_input_manifest": redacted_input_manifest,
        "source_artifact_references": source_artifact_references,
        "validated_response": validated_response,
        "proposed_mapping": proposed_mapping,
        "deterministic_mapping": deterministic_mapping,
        "user_disposition": user_disposition,
        "final_applied_mapping": final_applied_mapping,
        "retry_count": retry_count,
        "sanitized_error": sanitized_error,
        "metrics_produced": False,
        "verdict_changed": False,
    }
    return _insert(
        run_id=run_id,
        event_kind="llm",
        stage="pre_manifest_freeze",
        purpose=purpose,
        status=status,
        invoked=True,
        payload=payload,
        actor=actor,
        provider=provider,
        model=model,
        provider_api_version=provider_api_version,
        prompt_template_id=prompt_template_id,
        prompt_template_version=prompt_template_version,
        prompt_hash=prompt_hash,
        input_hash=input_hash,
        response_hash=response_hash,
        provider_request_id=provider_request_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        row_level_data_included=False,
    )


def record_reused_llm_inference(
    run_id: str,
    *,
    source_event: dict[str, Any],
    source_run_id: str,
    field: str,
    reuse_identity: str,
    actor: str,
    purpose: str = "semantic_role_verification",
) -> dict[str, Any]:
    """Reference a prior validated response without recording a new provider call."""
    if source_event.get("event_kind") != "llm" or source_event.get("status") != "succeeded":
        raise ValueError("only a successful LLM inference can be reused")
    payload = {
        "reason": "reused prior validated inference for the same immutable input identity",
        "reused_prior_inference": True,
        "source_run_id": source_run_id,
        "source_event_id": source_event["event_id"],
        "field": field,
        "reuse_identity": reuse_identity,
        "source_input_hash": source_event.get("input_hash"),
        "source_response_hash": source_event.get("response_hash"),
        "metrics_produced": False,
        "verdict_changed": False,
    }
    return _insert(
        run_id=run_id, event_kind="llm", stage="pre_manifest_freeze",
        purpose=purpose, status="skipped", invoked=False, payload=payload,
        actor=actor, provider=source_event.get("provider"), model=source_event.get("model"),
        provider_api_version=source_event.get("provider_api_version"),
        prompt_template_id=source_event.get("prompt_template_id"),
        prompt_template_version=source_event.get("prompt_template_version"),
        prompt_hash=source_event.get("prompt_hash"), input_hash=source_event.get("input_hash"),
        response_hash=source_event.get("response_hash"), row_level_data_included=False,
    )


def inference_disclosure(run_id: str) -> dict[str, Any]:
    """Return the bounded projection stored in diagnostic and report artifacts."""
    _require_run(run_id)
    rows = db.query("diag_inference_events", run_id=run_id, order_by="ts, event_id")
    llm_rows = [row for row in rows if row["event_kind"] == "llm"]
    deterministic = [
        {
            "event_id": row["event_id"],
            "stage": row["stage"],
            "purpose": row["purpose"],
            "status": row["status"],
            "actor": row["actor"],
            "timestamp": row["ts"],
            **row["payload_json"],
        }
        for row in rows if row["event_kind"] == "deterministic"
    ]
    llm_events = [
        {
            "call_id": row["event_id"],
            "stage": row["stage"],
            "purpose": row["purpose"],
            "status": row["status"],
            "invoked": bool(row["invoked"]),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "provider_api_version": row.get("provider_api_version"),
            "prompt_template_id": row.get("prompt_template_id"),
            "prompt_template_version": row.get("prompt_template_version"),
            "prompt_hash": row.get("prompt_hash"),
            "input_hash": row.get("input_hash"),
            "response_hash": row.get("response_hash"),
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "latency_ms": row.get("latency_ms"),
            "provider_request_id": row.get("provider_request_id"),
            "row_level_data_included": bool(row["row_level_data_included"]),
            "verdict_influenced": False,
            "actor": row["actor"],
            "timestamp": row["ts"],
            **row["payload_json"],
        }
        for row in llm_rows
    ]
    call_count = sum(bool(row["invoked"]) for row in llm_rows)
    reused_count = sum(bool((row.get("payload_json") or {}).get("reused_prior_inference"))
                       for row in llm_rows)
    canonical_events = [{key: row[key] for key in row if key != "ts"} for row in rows]
    return {
        "llm_call_count": call_count,
        "llm_used": call_count > 0,
        "verdict_influenced_by_llm": False,
        "reused_inference_count": reused_count,
        "statement": (("No LLM calls were made during this diagnostic journey; "
                       f"{reused_count} prior validated inference(s) were reused")
                      if call_count == 0 and reused_count else
                      "No LLM calls were made during this diagnostic journey"
                      if call_count == 0 else f"{call_count} advisory LLM call(s) were made before manifest freeze"),
        "events": llm_events,
        "deterministic_inferences": deterministic,
        "event_set_hash": stable_fingerprint(canonical_events),
    }
