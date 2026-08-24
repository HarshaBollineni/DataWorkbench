"""Governed draft/freeze manifest for Test 2, Diagnostic 6: Row Completeness."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

import system_db as db
from ai import control_plane, llm
from analysis_runtime.artifacts import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.data_sourcing_artifacts import persist_snapshot_profile_artifacts
from analysis_runtime.snapshots import SnapshotLoader

from .engines.row_completeness.engine import ENGINE_VERSION, METHODOLOGY_VERSION
from .engines.row_completeness.models import RULE_IDS, RoleBinding, RunScope
from .inference_audit import (
    inference_disclosure,
    record_deterministic_inference,
    record_llm_call,
    record_zero_llm_usage,
)
from .manifest import DRAFT, RUNNING, ManifestError, list_decisions, record_decision
from .readiness import readiness
from .register import get_diagnostic, require_executable
from .thresholds import effective_threshold
from .row_completeness_knowledge import resolve_package


DIAGNOSTIC_ID = 6
MANIFEST_VERSION = 1
DEFAULT_REPORTING_GRAIN = "monthly"
OWNER_ID = "diagnostic:6"
ROLE_VERIFIER_KEY = "role_mapping_verifier"
ROLE_INFERENCE_VERSION = "row-completeness-role-binding-v1"
VERIFICATION_POLICY = "advisory_only_user_must_apply_role_override"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _run(run_id: str) -> dict[str, Any]:
    row = db.query_one("diag_runs", run_id=run_id)
    if row is None:
        raise KeyError(f"Unknown run: {run_id}")
    if row.get("diagnostic_id") != DIAGNOSTIC_ID:
        raise ManifestError(f"run {run_id} does not belong to diagnostic #6")
    return row


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _role_score(row: dict[str, Any], role: str, *, configured_period: str | None) -> tuple[float, str]:
    column = str(row.get("column_name") or "")
    words = _tokens(column)
    saved = str(row.get("role") or "").strip().lower()
    dictionary = str(row.get("dictionary_role") or "").strip().lower()
    classification = str(row.get("classification") or "").strip().lower()
    if role == "period":
        if configured_period and column == configured_period:
            return 1.0, "Selected as the reporting-period column in Data Sourcing."
        if saved in {"period", "date"} or dictionary in {"period", "date", "reporting_period"}:
            return 0.95, "Saved schema metadata identifies this as a period/date column."
        if words & {"period", "month", "quarter", "year", "date"}:
            return 0.78, "The column name indicates reporting time."
        if classification in {"date", "datetime", "timestamp"}:
            return 0.70, "The profiled data type is date-like."
    elif role == "facility_id":
        if saved in {"identifier", "primary_key", "business_key"} or dictionary in {
            "identifier", "primary_key", "business_key", "facility_id"
        }:
            return 0.95, "Saved schema metadata identifies this as an identifier."
        if (words & {"facility", "account", "loan", "contract", "customer", "obligor"}
                and words & {"id", "key", "number", "no"}):
            return 0.84, "The column name indicates a facility-level identifier."
        if column.lower() in {"facility", "facility_id", "account_id", "loan_id", "contract_id"}:
            return 0.82, "The column name is a common facility identifier."
    elif role == "segment":
        if saved == "segment" or dictionary in {"segment", "portfolio_segment"}:
            return 0.95, "Saved schema metadata identifies this as a segment."
        if words & {"segment"}:
            return 0.80, "The column name explicitly identifies a segment."
    return 0.0, "No strong deterministic signal for this role."


def _candidate_rows(item_id: str) -> list[dict[str, Any]]:
    return db.query("variable_inventory", item_id=item_id, order_by="table_name, column_name")


def _rank(rows: list[dict[str, Any]], role: str, configured_period: str | None) -> list[dict[str, Any]]:
    values = []
    for row in rows:
        score, reason = _role_score(row, role, configured_period=configured_period)
        if score:
            values.append({"table": row["table_name"], "column": row["column_name"],
                           "score": score, "reason": reason,
                           "role": row.get("role"), "dictionary_role": row.get("dictionary_role"),
                           "classification": row.get("classification"), "data_type": row.get("data_type")})
    return sorted(values, key=lambda value: (-value["score"], value["table"], value["column"]))


def _select_table(rows: list[dict[str, Any]], period: list[dict[str, Any]],
                  facility: list[dict[str, Any]]) -> str:
    tables = sorted({row["table_name"] for row in rows})
    if not tables:
        raise ManifestError("dataset has no profiled columns")
    scores = []
    for table in tables:
        period_score = max((row["score"] for row in period if row["table"] == table), default=0.0)
        facility_score = max((row["score"] for row in facility if row["table"] == table), default=0.0)
        scores.append((period_score + facility_score, min(period_score, facility_score), table))
    return max(scores)[2]


def _binding(candidates: list[dict[str, Any]], table: str, role: str,
             used: set[str]) -> dict[str, Any] | None:
    selected = next((row for row in candidates
                     if row["table"] == table and row["column"] not in used), None)
    if selected is None:
        return None
    used.add(selected["column"])
    governed = selected["score"] >= 0.95
    return {"table": table, "column": selected["column"],
            "source": "governed_metadata" if governed else "deterministic",
            "score": selected["score"], "reason": selected["reason"]}


def _source_artifacts(item_id: str, table: str, columns: list[str], actor: str) -> list[dict[str, str]]:
    try:
        persist_snapshot_profile_artifacts(item_id, actor=actor)
    except (ValueError, KeyError):
        # A retained profile can be temporarily ineligible for publication;
        # the manifest still uses the normalized inventory and records only
        # artifacts that already exist and are active.
        pass
    repo = AnalysisArtifactRepository()
    refs: list[dict[str, str]] = []
    table_profile = next((artifact for artifact in repo.list(
        snapshot_id=item_id, artifact_type="table_profile", status="active"
    ) if artifact.identity.get("table") == table), None)
    if table_profile:
        refs.append({"artifact_id": table_profile.artifact_id, "role": "selected_table_profile",
                     "payload_hash": table_profile.payload_hash})
    wanted = set(columns)
    for artifact in repo.list(snapshot_id=item_id, artifact_type="column_profile", status="active"):
        if artifact.identity.get("table") == table and artifact.feature in wanted:
            refs.append({"artifact_id": artifact.artifact_id,
                         "role": f"{artifact.feature}_column_profile",
                         "payload_hash": artifact.payload_hash})
    return sorted(refs, key=lambda row: (row["role"], row["artifact_id"]))


def _table_options(item_id: str, rows: list[dict[str, Any]],
                   candidates: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    profiles = {row["table_name"]: row for row in db.query(
        "dq_item_tables", item_id=item_id, order_by="table_name")}
    names = sorted({row["table_name"] for row in rows})
    return [{"table": table,
             "row_count": (profiles.get(table) or {}).get("row_count"),
             "column_count": len([row for row in rows if row["table_name"] == table]),
             "facility_candidate": any(row["table"] == table for row in candidates["facility_id"]),
             "period_candidate": any(row["table"] == table for row in candidates["period"]),
             "segment_candidate": any(row["table"] == table for row in candidates["segment"])}
            for table in names]


def _bounded_candidates(values: list[dict[str, Any]], limit_per_table: int = 8) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for value in values:
        table = value["table"]
        if counts.get(table, 0) >= limit_per_table:
            continue
        retained.append(value)
        counts[table] = counts.get(table, 0) + 1
    return retained


def build_manifest(item_id: str, actor: str = "system", *, enforce_register: bool = True) -> dict[str, Any]:
    item = db.query_one("dq_items", item_id=item_id)
    if item is None:
        raise KeyError(f"Unknown item: {item_id}")
    if enforce_register:
        register_row = require_executable(DIAGNOSTIC_ID)
        state = readiness(item_id, DIAGNOSTIC_ID)
        if state.status != "ready":
            raise ManifestError(f"{state.status}: {state.reason}")
    else:
        register_row = get_diagnostic(DIAGNOSTIC_ID)
    snapshot = SnapshotLoader().reference(item_id)
    rows = _candidate_rows(item_id)
    period_candidates = _rank(rows, "period", item.get("period_column"))
    facility_candidates = _rank(rows, "facility_id", item.get("period_column"))
    segment_candidates = _rank(rows, "segment", item.get("period_column"))
    table = _select_table(rows, period_candidates, facility_candidates)
    used: set[str] = set()
    roles = {
        "facility_id": _binding(facility_candidates, table, "facility_id", used),
        "period": _binding(period_candidates, table, "period", used),
        "segment": _binding(segment_candidates, table, "segment", used),
    }
    table_columns = [row["column_name"] for row in rows if row["table_name"] == table]
    source_refs = _source_artifacts(item_id, table,
                                    [binding["column"] for binding in roles.values() if binding], actor)
    run_id, now = _id("drun"), db.now_ist()
    continuity_floor = effective_threshold(DIAGNOSTIC_ID, "continuity_floor")
    knowledge = resolve_package(case_id=item_id)
    if knowledge["methodology"] != METHODOLOGY_VERSION:
        raise ManifestError("published T2D6 knowledge targets an unsupported methodology version")
    candidates = {"facility_id": facility_candidates, "period": period_candidates,
                  "segment": segment_candidates}
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_kind": "row_completeness",
        "run_id": run_id,
        "item_id": item_id,
        "item_name": item.get("name"),
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic": knowledge["diagnostic"],
        "snapshot": snapshot.to_dict(),
        "status": DRAFT,
        "created_at": now,
        "created_by": actor,
        "table": table,
        "table_options": _table_options(item_id, rows, candidates),
        "available_columns": table_columns,
        "roles": roles,
        "role_candidates": {"facility_id": _bounded_candidates(facility_candidates),
                            "period": _bounded_candidates(period_candidates),
                            "segment": _bounded_candidates(segment_candidates)},
        "configuration": {
            "reporting_grain": {"value": DEFAULT_REPORTING_GRAIN, "source": "default"},
            "continuity_floor": continuity_floor,
            "segment_label_policy": {"value": "mask", "source": "default"},
        },
        "rule_ids": list(RULE_IDS),
        "kb": knowledge,
        "source_artifact_references": source_refs,
        "role_verification": {"enabled": False, "status": "not_requested",
                              "policy": VERIFICATION_POLICY},
        "engine_version": ENGINE_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
    }
    db.insert("diag_runs", {"run_id": run_id, "item_id": item_id,
        "diagnostic_id": DIAGNOSTIC_ID, "manifest_json": manifest, "status": DRAFT,
        "engine_versions_json": {"row_completeness": ENGINE_VERSION},
        "created_at": now, "started_at": None, "finished_at": None})
    record_zero_llm_usage(run_id, actor=actor)
    for role, binding in roles.items():
        record_deterministic_inference(run_id, inference_type=f"{role}_role_binding",
            methodology_version=ROLE_INFERENCE_VERSION,
            input_references=source_refs,
            evidence_summary={"candidate_count": len(manifest["role_candidates"][role]),
                              "selected_table": table},
            proposed_value=binding, final_value=binding,
            disposition="proposed_for_scope_review" if binding else "unresolved", actor=actor)
    manifest["inference_disclosure"] = inference_disclosure(run_id)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    record_decision(run_id, "default_applied", {
        "fields": {"reporting_grain": DEFAULT_REPORTING_GRAIN,
                   "continuity_floor": continuity_floor["value"],
                   "segment_label_policy": "mask"},
        "deterministic_roles": roles,
    }, actor)
    return manifest


def _validate_role_override(manifest: dict[str, Any], role: str, column: str | None) -> None:
    if role not in {"facility_id", "period", "segment"}:
        raise ManifestError(f"unknown semantic role: {role!r}")
    if role != "segment" and not column:
        raise ManifestError(f"{role} is required and cannot be unbound")
    if column and column not in manifest["available_columns"]:
        raise ManifestError(f"column {column!r} is not in selected table {manifest['table']!r}")
    if column:
        clashes = [name for name, binding in manifest["roles"].items()
                   if name != role and binding and binding.get("column") == column]
        if clashes:
            raise ManifestError(f"column {column!r} is already bound to {clashes[0]}")


def _llm_verify(manifest: dict[str, Any], actor: str) -> dict[str, Any]:
    current = {role: (binding or {}).get("column") for role, binding in manifest["roles"].items()}
    candidates = {role: [{"column": row["column"], "score": row["score"]}
                         for row in values if row["table"] == manifest["table"]]
                  for role, values in manifest["role_candidates"].items()}
    redacted = {"schema_version": 1, "table_alias": "selected_table",
                "columns": manifest["available_columns"][:100], "current_mapping": current,
                "bounded_candidates": candidates, "policy": VERIFICATION_POLICY}
    messages = [
        {"role": "system", "content": "Review semantic column roles only. Return JSON with a suggestions object whose keys are facility_id, period and segment; each value is a candidate column or null. Do not assess data quality or a verdict."},
        {"role": "user", "content": json.dumps(redacted, sort_keys=True, separators=(",", ":"))},
    ]
    cfg = control_plane.resolve(ROLE_VERIFIER_KEY)
    request_model = llm.get_model()
    provider = "azure_openai" if os.getenv("AZURE_OPENAI_ENDPOINT") else cfg["house"]
    provider_api_version = os.getenv("AZURE_OPENAI_API_VERSION") or "configured"
    started = time.monotonic()
    try:
        response = llm.get_client(cfg["house"]).chat.completions.create(
            model=request_model, messages=messages, temperature=cfg["temperature"],
            max_completion_tokens=500, response_format={"type": "json_object"})
        parsed = json.loads(response.choices[0].message.content or "")
        suggestions = parsed.get("suggestions") if isinstance(parsed, dict) else None
        if not isinstance(suggestions, dict) or set(suggestions) != {"facility_id", "period", "segment"}:
            raise ValueError("response did not contain the required bounded suggestions")
        allowed = {role: {row["column"] for row in values if row["table"] == manifest["table"]}
                   for role, values in manifest["role_candidates"].items()}
        for role, column in suggestions.items():
            if column is not None and column not in allowed[role]:
                raise ValueError(f"response proposed an out-of-scope {role} column")
        usage = getattr(response, "usage", None)
        record_llm_call(run_id=manifest["run_id"], status="succeeded", provider=provider,
            model=request_model, provider_api_version=provider_api_version, prompt_template_id="t2d6_role_review",
            prompt_template_version="1", prompt_hash=stable_fingerprint(messages),
            redacted_input_manifest=redacted, validated_response=parsed,
            source_artifact_references=manifest["source_artifact_references"],
            proposed_mapping=suggestions, deterministic_mapping=current,
            user_disposition="pending_manual_review", final_applied_mapping=current, actor=actor,
            provider_request_id=getattr(response, "id", None),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=round((time.monotonic() - started) * 1000))
        return {"enabled": True, "status": "completed", "policy": VERIFICATION_POLICY,
                "model": request_model, "suggestions": suggestions, "mapping_applied": False}
    except Exception as exc:
        error_parts = [type(exc).__name__]
        if getattr(exc, "status_code", None):
            error_parts.append(f"http_{exc.status_code}")
        body = getattr(exc, "body", None)
        if isinstance(body, dict) and body.get("code"):
            error_parts.append(str(body["code"]))
        record_llm_call(run_id=manifest["run_id"], status="failed", provider=provider,
            model=request_model, provider_api_version=provider_api_version,
            prompt_template_id="t2d6_role_review", prompt_template_version="1",
            prompt_hash=stable_fingerprint(messages), redacted_input_manifest=redacted,
            validated_response=None, source_artifact_references=manifest["source_artifact_references"],
            proposed_mapping=None, deterministic_mapping=current, user_disposition="not_available",
            final_applied_mapping=current, actor=actor,
            latency_ms=round((time.monotonic() - started) * 1000),
            sanitized_error=":".join(error_parts))
        return {"enabled": True, "status": "failed", "policy": VERIFICATION_POLICY,
                "model": request_model, "suggestions": {},
                "mapping_applied": False,
                "error": "Advisory role review was unavailable; deterministic mappings are unchanged."}


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"manifest {run_id} is frozen (status {run['status']}) and cannot be edited")
    manifest, kind = run["manifest_json"], patch.get("kind")
    if kind == "table_selection":
        table = str(patch.get("table") or "").strip()
        rows = _candidate_rows(manifest["item_id"])
        available_tables = {row["table_name"] for row in rows}
        if table not in available_tables:
            raise ManifestError(f"unknown profiled table: {table!r}")
        before = {"table": manifest["table"], "roles": manifest["roles"]}
        used: set[str] = set()
        roles = {role: _binding(manifest["role_candidates"][role], table, role, used)
                 for role in ("facility_id", "period", "segment")}
        manifest["table"] = table
        manifest["available_columns"] = [row["column_name"] for row in rows if row["table_name"] == table]
        manifest["roles"] = roles
        manifest["source_artifact_references"] = _source_artifacts(
            manifest["item_id"], table, [value["column"] for value in roles.values() if value], actor)
        record_decision(run_id, "scope_exclusion", {"event": "table_selection",
                        "before": before, "after": {"table": table, "roles": roles}}, actor)
        for role, binding in roles.items():
            record_deterministic_inference(run_id, inference_type=f"{role}_role_binding",
                methodology_version=ROLE_INFERENCE_VERSION,
                input_references=manifest["source_artifact_references"],
                evidence_summary={"selected_table": table,
                                  "candidate_count": len(manifest["role_candidates"][role])},
                proposed_value=binding, final_value=binding,
                disposition="recomputed_after_table_selection" if binding else "unresolved", actor=actor)
    elif kind == "role_override":
        role = str(patch.get("role") or "")
        column_value = patch.get("column")
        column = str(column_value).strip() if column_value is not None else None
        column = column or None
        _validate_role_override(manifest, role, column)
        before = manifest["roles"].get(role)
        manifest["roles"][role] = (None if column is None else {
            "table": manifest["table"], "column": column, "source": "manual", "score": None,
            "reason": f"Explicitly selected by {actor} in the scope gate."})
        record_decision(run_id, "role_override", {"role": role, "column": column,
                        "before": before, "after": manifest["roles"][role]}, actor)
    elif kind == "threshold_tune":
        key = patch.get("key")
        if key != "continuity_floor":
            raise ManifestError("the only row-completeness threshold is continuity_floor")
        try:
            value = float(patch.get("value"))
        except (TypeError, ValueError) as exc:
            raise ManifestError("continuity_floor must be a number between 0 and 1") from exc
        if not 0 <= value <= 1:
            raise ManifestError("continuity_floor must be a number between 0 and 1")
        before = manifest["configuration"][key]
        manifest["configuration"][key] = {"value": value, "source": f"user-set ({actor}, {db.now_ist()})"}
        record_decision(run_id, "threshold_tune", {"key": key, "before": before,
                        "after": manifest["configuration"][key]}, actor)
    elif kind == "parameter_tune":
        key, value = patch.get("key"), patch.get("value")
        if key == "reporting_grain" and value not in {"monthly", "quarterly", "semiannual", "annual"}:
            raise ManifestError("reporting_grain must be monthly, quarterly, semiannual, or annual")
        if key == "segment_label_policy" and value not in {"retain", "mask", "omit"}:
            raise ManifestError("segment_label_policy must be retain, mask, or omit")
        if key not in {"reporting_grain", "segment_label_policy"}:
            raise ManifestError("unknown row-completeness parameter")
        before = manifest["configuration"][key]
        manifest["configuration"][key] = {"value": value, "source": f"user-set ({actor}, {db.now_ist()})"}
        record_decision(run_id, "threshold_tune", {"event": "parameter_tune", "key": key,
                        "before": before, "after": manifest["configuration"][key]}, actor)
    elif kind == "role_verification_change":
        enabled = bool(patch.get("enabled"))
        if enabled:
            manifest["role_verification"] = _llm_verify(manifest, actor)
        else:
            manifest["role_verification"] = {"enabled": False, "status": "disabled_by_user",
                                              "policy": VERIFICATION_POLICY}
        record_decision(run_id, "role_verification_change", {
            "requested": enabled, "mapping_applied": False,
            "status": manifest["role_verification"]["status"]}, actor)
    else:
        raise ManifestError("kind must be table_selection, role_override, threshold_tune, parameter_tune, or role_verification_change")
    manifest["inference_disclosure"] = inference_disclosure(run_id)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def _frozen_scope(manifest: dict[str, Any]) -> RunScope:
    if not manifest["roles"].get("facility_id") or not manifest["roles"].get("period"):
        raise ManifestError("facility identifier and reporting period must both be resolved before running")
    return RunScope(asset_id=manifest["snapshot"]["asset_id"], snapshot_id=manifest["item_id"],
        table=manifest["table"], facility_id=RoleBinding.model_validate(manifest["roles"]["facility_id"]),
        period=RoleBinding.model_validate(manifest["roles"]["period"]),
        segment=(RoleBinding.model_validate(manifest["roles"]["segment"])
                 if manifest["roles"].get("segment") else None),
        reporting_grain=manifest["configuration"]["reporting_grain"]["value"],
        continuity_floor=manifest["configuration"]["continuity_floor"]["value"])


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"run {run_id} is already {run['status']}")
    manifest = run["manifest_json"]
    scope = _frozen_scope(manifest)
    manifest["scope"] = scope.model_dump(mode="json")
    manifest["inference_disclosure"] = inference_disclosure(run_id)
    manifest["actor_decisions"] = list_decisions(run_id)
    fingerprint_inputs = {"schema_version": MANIFEST_VERSION, "diagnostic_id": DIAGNOSTIC_ID,
        "scope": manifest["scope"], "rule_ids": manifest["rule_ids"], "kb": manifest["kb"],
        "source_artifact_references": manifest["source_artifact_references"],
        "engine_version": ENGINE_VERSION, "methodology_version": METHODOLOGY_VERSION}
    manifest["manifest_fingerprint"] = stable_fingerprint(fingerprint_inputs)
    manifest["status"] = "frozen"
    manifest["frozen_at"], manifest["frozen_by"] = db.now_ist(), actor
    # Validate the stable execution projection independently of draft-only UI metadata.
    from .engines.row_completeness.api import FrozenRowCompletenessManifest
    FrozenRowCompletenessManifest.model_validate({key: manifest[key] for key in (
        "diagnostic_id", "run_id", "status", "scope", "rule_ids", "kb",
        "source_artifact_references", "inference_disclosure", "engine_version",
        "methodology_version", "actor_decisions", "manifest_fingerprint")})
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest,
        "status": RUNNING, "started_at": manifest["frozen_at"]})
    return manifest


def get_run(run_id: str) -> dict[str, Any]:
    return _run(run_id)
