"""Governed intake, scope, role-binding, declaration, and freeze contract for T2-D08."""
from __future__ import annotations

import time
import uuid
from typing import Any

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from domains.test_lab.shared.run_state import (
    DRAFT, RUNNING, ManifestError, record_decision,
)
from dq_diagnostics.inference_audit import (
    inference_disclosure, record_llm_call, record_reused_llm_inference,
    record_zero_llm_usage,
)
from dq_diagnostics.readiness import readiness
from dq_diagnostics.register import get_diagnostic, require_executable

from . import knowledge
from .adjudication_contract import (
    RoleAdjudicationOutput, RoleDecision, build_adjudication_input, expand_adjudication_input,
    validate_role_adjudication,
)
from .resources import expand_implied_roles

DIAGNOSTIC_ID = 8
MANIFEST_KIND = "value_semantics"
MANIFEST_VERSION = 1
ENGINE_VERSION = "1.0.0"
AI_PURPOSE = "value_semantics_role_adjudication"
CONTEXTS = {"GENERAL", "PD", "LGD", "EAD"}


def _id() -> str:
    return f"vsrun_{uuid.uuid4().hex[:12]}"


def _run(run_id: str) -> dict[str, Any]:
    row = db.query_one("diag_runs", run_id=run_id)
    if row is None:
        raise KeyError(f"Unknown run: {run_id}")
    if row.get("diagnostic_id") != DIAGNOSTIC_ID:
        raise ManifestError("run does not belong to diagnostic #8")
    return row


def get_run(run_id: str) -> dict[str, Any]:
    return _run(run_id)


def _production_role(value: Any) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "primary_key": "identifier", "foreign_key": "identifier",
        "id": "identifier", "datetime": "date", "timestamp": "date",
        "categorical": "feature", "numeric": "feature", "segment": "group",
        "category": "group",
    }
    normalized = aliases.get(token, token)
    allowed = {"identifier", "period", "target", "feature", "score", "weight", "date", "ignore", "group"}
    return normalized if normalized in allowed else "ignore"


def _inventory(item_id: str, table: str) -> list[dict[str, Any]]:
    rows = db.query("variable_inventory", item_id=item_id)
    return [row for row in rows if row.get("table_name") == table and row.get("column_name")]


def _variable(row: dict[str, Any], contexts: list[str]) -> dict[str, Any]:
    profile = row.get("profile_json") or {}
    return {
        "name": row["column_name"],
        "description": row.get("description") or profile.get("description") or "",
        "business_name": row.get("business_name") or "",
        "allowed_values": row.get("allowed_values") or "",
        "data_type": row.get("data_type"),
        "role": _production_role(row.get("role")),
        "model_type": "" if contexts == ["GENERAL"] else ";".join(contexts),
        "use_cases": [],
        "product": "",
    }


def _field_card(row: dict[str, Any], contexts: list[str]) -> dict[str, Any]:
    kb, _terminology, matcher = knowledge.resources()
    from .matching import match_variable_to_roles
    result = match_variable_to_roles(_variable(row, contexts), matcher)
    exact = result.get("exact_match")
    exact_roles = expand_implied_roles([exact["role"]], kb) if exact else []
    candidates = [
        {key: candidate.get(key) for key in (
            "role", "definition", "representations", "matching_note",
            "combined_score", "tfidf_score", "best_matching_text", "rank",
        )}
        for candidate in result.get("detailed_candidates") or []
    ]
    return {
        "column": row["column_name"],
        "description": _variable(row, contexts)["description"],
        "data_type": row.get("data_type"),
        "profile_role": row.get("role"),
        "selected": bool(exact_roles),
        "confirmed_roles": exact_roles,
        "proposed_roles": exact_roles or ([candidates[0]["role"]] if candidates else []),
        "binding_source": "deterministic_exact" if exact_roles else "unconfirmed_candidate",
        "review_required": not bool(exact_roles),
        "match_status": result["match_status"],
        "match_method": result["match_method"],
        "candidates": candidates,
        "unresolved_abbreviations": result.get("unresolved_abbreviations") or [],
        "adjudication": {"status": "not_requested", "attempts": []},
    }


def _ai_reuse_identity(manifest: dict[str, Any], field: dict[str, Any]) -> str:
    """Stable identity for reusing advisory inference on one immutable field."""
    from .adjudication import CONTRACT_VERSION, PROMPT_VERSION

    value_kb, terminology, _matcher = knowledge.resources()
    return stable_fingerprint({
        "item_id": manifest["item_id"], "table": manifest["table"],
        "column": field["column"], "description": field.get("description") or "",
        "data_type": field.get("data_type"), "profile_role": field.get("profile_role"),
        "value_semantics_version": manifest["knowledge"]["value_semantics_version"],
        "terminology_version": manifest["knowledge"]["terminology_version"],
        "value_semantics_hash": stable_fingerprint(value_kb),
        "terminology_hash": stable_fingerprint(terminology),
        "prompt_version": PROMPT_VERSION, "contract_version": CONTRACT_VERSION,
        "prompt_hash": stable_fingerprint(knowledge.prompt()),
    })


def _successful_ai_event(run_id: str, column: str,
                         output: dict[str, Any]) -> dict[str, Any] | None:
    for event in reversed(db.query("diag_inference_events", run_id=run_id,
                                   order_by="ts, event_id")):
        payload = event.get("payload_json") or {}
        proposed = payload.get("proposed_mapping") or {}
        if (event.get("event_kind") == "llm" and event.get("status") == "succeeded"
                and proposed.get("column") == column
                and payload.get("validated_response") == output):
            return event
    return None


def _reusable_ai_inference(manifest: dict[str, Any], field: dict[str, Any]) -> dict[str, Any] | None:
    if not field.get("review_required"):
        return None
    identity = _ai_reuse_identity(manifest, field)
    active_roles = set(knowledge.role_index())
    prior_runs = db.query("diag_runs", item_id=manifest["item_id"],
                          diagnostic_id=DIAGNOSTIC_ID,
                          order_by="created_at DESC, run_id DESC")
    for prior in prior_runs:
        prior_manifest = prior.get("manifest_json") or {}
        if (prior["run_id"] == manifest["run_id"]
                or str(prior_manifest.get("tenant_id") or "bootstrap") != manifest["tenant_id"]
                or prior_manifest.get("table") != manifest["table"]):
            continue
        prior_field = next((row for row in prior_manifest.get("fields") or []
                            if row.get("column") == field["column"]), None)
        prior_adjudication = (prior_field or {}).get("adjudication") or {}
        output = prior_adjudication.get("output")
        if (not prior_field or prior_adjudication.get("status") != "proposal_ready"
                or _ai_reuse_identity(prior_manifest, prior_field) != identity):
            continue
        try:
            validated = RoleAdjudicationOutput.model_validate(output).model_dump(mode="json")
        except Exception:  # stale or pre-contract response; never reuse it
            continue
        proposed_roles = [value for value in [validated.get("primary_role"),
                          *(validated.get("secondary_roles") or [])] if value]
        if not set(proposed_roles) <= active_roles:
            continue
        source_event = _successful_ai_event(prior["run_id"], field["column"], validated)
        if source_event:
            return {"identity": identity, "output": validated,
                    "source_run_id": prior["run_id"], "source_event": source_event}
    return None


def _hydrate_reusable_ai(manifest: dict[str, Any], actor: str) -> int:
    reused = 0
    existing = {
        ((event.get("payload_json") or {}).get("field"),
         (event.get("payload_json") or {}).get("reuse_identity"))
        for event in db.query("diag_inference_events", run_id=manifest["run_id"])
        if (event.get("payload_json") or {}).get("reused_prior_inference")
    }
    for field in manifest.get("fields") or []:
        if (field.get("adjudication") or {}).get("status") == "proposal_ready":
            continue
        reusable = _reusable_ai_inference(manifest, field)
        if not reusable:
            continue
        output = reusable["output"]
        proposed = [value for value in [output.get("primary_role"),
                    *(output.get("secondary_roles") or [])] if value]
        field["proposed_roles"] = expand_implied_roles(proposed, knowledge.resources()[0]) if proposed else []
        field["binding_source"] = {
            "MATCH": "reused_ai_proposal_unconfirmed",
            "MULTI_ROLE_MATCH": "reused_ai_proposal_unconfirmed",
            "NO_CANDIDATE_MATCH": "reused_ai_no_applicable_role",
            "INSUFFICIENT_CONTEXT": "reused_ai_insufficient_context",
            "AMBIGUOUS_ROLE": "reused_ai_ambiguous_role",
        }.get(output["decision"], "reused_ai_review_unresolved")
        field["adjudication"] = {
            "status": "proposal_ready", "output": output, "attempts": [],
            "reused_from": {
                "run_id": reusable["source_run_id"],
                "event_id": reusable["source_event"]["event_id"],
                "reuse_identity": reusable["identity"],
                "reused_at": db.now_ist(),
            },
        }
        key = (field["column"], reusable["identity"])
        if key not in existing:
            record_reused_llm_inference(
                manifest["run_id"], source_event=reusable["source_event"],
                source_run_id=reusable["source_run_id"], field=field["column"],
                reuse_identity=reusable["identity"], actor=actor, purpose=AI_PURPOSE,
            )
            existing.add(key)
        reused += 1
    return reused


def _context_suggestions(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = knowledge.role_index()
    counts = {name: 0 for name in ("PD", "LGD", "EAD")}
    evidence: dict[str, list[str]] = {name: [] for name in counts}
    for field in fields:
        candidate_roles = field.get("confirmed_roles") or field.get("proposed_roles") or []
        for role_name in candidate_roles:
            for context in roles.get(role_name, {}).get("model_types") or []:
                label = str(context).upper()
                if label in counts:
                    counts[label] += 1
                    evidence[label].append(field["column"])
    return [
        {"context": name, "evidence_count": count,
         "example_fields": sorted(set(evidence[name]))[:5],
         "explanation": "Suggested from field-to-role evidence; it does not activate or suppress rules."}
        for name, count in counts.items() if count
    ]


def _known_declarations() -> set[str]:
    kb = knowledge.resources()[0]
    declaration_contract = kb.get("required_runtime_declarations") or {}
    if isinstance(declaration_contract, dict):
        declared = {name for values in declaration_contract.values() for name in (values or [])}
    else:
        declared = set(declaration_contract)
    for rule in kb["rules"]:
        for entry in rule["entries"]:
            declared.update(entry.get("required_declarations") or [])
            declared.update(entry.get("indicator_declarations") or [])
    return declared


def _validate_declarations(values: dict[str, Any]) -> None:
    """Reject unsafe declaration shapes before a manifest can be frozen."""
    unknown = set(values) - _known_declarations()
    if unknown:
        raise ManifestError(f"unknown runtime declarations: {sorted(unknown)}")
    for key in ("variance_window", "minimum_row_count"):
        if key in values and (isinstance(values[key], bool)
                              or not isinstance(values[key], (int, float))
                              or int(values[key]) != values[key] or int(values[key]) < 1):
            raise ManifestError(f"{key} must be a positive integer")
    if "not_applicable_share_threshold" in values:
        threshold = values["not_applicable_share_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) \
                or not 0 <= float(threshold) <= 1:
            raise ManifestError("not_applicable_share_threshold must be between 0 and 1")
    mappings = {
        "field_specific_sentinel_definitions", "forward_horizon_by_label",
        "return_to_performing_domain", "non_arrears_trigger_domain",
        "ead_measurement_point_by_target", "default_event_domain",
        "commitment_applicability_by_facility_type",
    }
    for key in mappings & set(values):
        if not isinstance(values[key], dict):
            raise ManifestError(f"{key} must be a JSON object")
    for key in {"finished_outcome_states", "outcome_state_domain", "declared_exit_events"} & set(values):
        if not isinstance(values[key], list):
            raise ManifestError(f"{key} must be a JSON array")
    if "lower_frequency_fields_and_review_cycles" in values and not isinstance(
        values["lower_frequency_fields_and_review_cycles"], (dict, str, int, float)
    ):
        raise ManifestError("lower_frequency_fields_and_review_cycles must be an object or frequency value")
    if "behavioural_balance_update_frequency" in values and not isinstance(
        values["behavioural_balance_update_frequency"], (str, int, float)
    ):
        raise ManifestError("behavioural_balance_update_frequency must be a frequency value")


def _bindings(manifest: dict[str, Any]) -> dict[str, list[str]]:
    return {
        row["column"]: list(row.get("confirmed_roles") or [])
        for row in manifest.get("fields") or []
        if row.get("selected") and row.get("confirmed_roles")
    }


def _coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    kb = knowledge.resources()[0]
    bindings = _bindings(manifest)
    role_columns: dict[str, list[str]] = {}
    for column, roles in bindings.items():
        for role in roles:
            role_columns.setdefault(role, []).append(column)
    declarations = manifest.get("runtime_declarations") or {}
    ready, unscoped, unrouted = [], [], []
    for rule in kb["rules"]:
        for entry in rule["entries"]:
            targets = role_columns.get(entry["target_role"], [])
            if not targets:
                unrouted.append({"rule": rule["rule"], "entry": entry["entry"],
                                 "target_role": entry["target_role"]})
                continue
            for target in targets:
                missing_roles = [
                    role for role in entry.get("indicator_roles") or []
                    if len(role_columns.get(role, [])) != 1
                ]
                variance_requirements = (
                    ((rule.get("evaluation_limbs") or {}).get("variance") or {})
                    .get("required_declarations") or []
                )
                required = [*variance_requirements,
                            *(entry.get("required_declarations") or []),
                            *(entry.get("indicator_declarations") or [])]
                missing_declarations = [key for key in required if key not in declarations]
                if entry["entry"].endswith("pd_construction_constant"):
                    scope = declarations.get("panel_scope_exclusions")
                    if not isinstance(scope, dict) or not isinstance(scope.get("row_predicate"), dict):
                        missing_declarations = sorted(set([
                            *missing_declarations, "panel_scope_exclusions.row_predicate",
                        ]))
                item = {"rule": rule["rule"], "tag": rule["tag_assigned"],
                        "entry": entry["entry"], "target_role": entry["target_role"],
                        "target_column": target, "missing_roles": missing_roles,
                        "missing_declarations": missing_declarations}
                (ready if not missing_roles and not missing_declarations else unscoped).append(item)
                sentinels = declarations.get("field_specific_sentinel_definitions") or {}
                if (rule["tag_assigned"] == "STALE_FROZEN" and target in sentinels
                        and (missing_roles or missing_declarations)):
                    ready.append({**item, "evaluation_mode": "sentinel_only",
                                  "missing_roles": [], "missing_declarations": []})
    return {
        "kb_entries": sum(len(rule["entries"]) for rule in kb["rules"]),
        "routed_entries": len(ready) + len(unscoped),
        "ready_routes": ready,
        "unscoped_routes": unscoped,
        "unrouted_entries": unrouted,
        "selected_fields": len(bindings),
        "coverage_status": "complete" if ready and not unscoped else "partial",
        "context_is_non_enforcing": True,
    }


def _refresh(manifest: dict[str, Any]) -> dict[str, Any]:
    # Upgrade drafts only; completed manifests and their artifact identities stay immutable.
    manifest["field_decisions_version"] = 1
    selected = [row for row in manifest.get("fields") or [] if row.get("selected")]
    unresolved = [row["column"] for row in selected
                  if row.get("review_required") or not row.get("confirmed_roles")]
    target_roles = {entry["target_role"] for rule in knowledge.resources()[0]["rules"]
                    for entry in rule["entries"]}
    target_fields = [row["column"] for row in selected
                     if set(row.get("confirmed_roles") or []) & target_roles]
    blockers = []
    if not manifest.get("introduction_acknowledged"):
        blockers.append({"code": "introduction_required",
                         "message": "Review the intended-use information before configuring the diagnostic."})
    if not manifest.get("table"):
        blockers.append({"code": "table_required", "message": "Select one profiled table."})
    if not selected:
        blockers.append({"code": "field_scope_required", "message": "Select at least one field."})
    if unresolved:
        blockers.append({"code": "binding_confirmation_required",
                         "message": f"Confirm semantic roles for {len(unresolved)} selected field(s).",
                         "fields": unresolved})
    if selected and not target_fields:
        blockers.append({"code": "target_role_required",
                         "message": "Select at least one field whose confirmed role is classified by a KB rule."})
    manifest["selected_fields"] = [row["column"] for row in selected]
    manifest["confirmed_role_bindings"] = _bindings(manifest)
    manifest["coverage"] = _coverage(manifest)
    manifest["blockers"] = blockers
    manifest["ready_to_run"] = not blockers
    manifest["inference_disclosure"] = inference_disclosure(manifest["run_id"])
    return manifest


def field_scope_decisions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Field-level coverage decisions, not the engine's cell-level NOT_APPLICABLE tag."""
    decisions = []
    for field in manifest.get("fields") or []:
        applicability = field.get("applicability") or {}
        not_applicable = applicability.get("status") == "not_applicable"
        decisions.append({
            "column": field["column"],
            "status": "not_applicable" if not_applicable else (
                "included" if field.get("selected") else "excluded"),
            "reason": applicability.get("reason") if not_applicable else None,
            "confirmed_by": applicability.get("confirmed_by") if not_applicable else field.get("confirmed_by"),
            "confirmed_at": applicability.get("confirmed_at") if not_applicable else field.get("confirmed_at"),
            "confirmed_roles": field.get("confirmed_roles") or [],
            "binding_source": field.get("binding_source"),
            "review_required": bool(field.get("review_required")),
            "applicability": applicability or None,
            "ai_review": field.get("adjudication") or {},
        })
    return decisions


def build_manifest(item_id: str, actor: str = "system", *, tenant_id: str = "bootstrap",
                   enforce_register: bool = True) -> dict[str, Any]:
    register = require_executable(DIAGNOSTIC_ID) if enforce_register else get_diagnostic(DIAGNOSTIC_ID)
    if enforce_register:
        state = readiness(item_id, DIAGNOSTIC_ID, tenant_id)
        if state.status != "ready":
            raise ManifestError(f"{state.status}: {state.reason}")
    item = db.query_one("dq_items", item_id=item_id)
    if item is None:
        raise KeyError(f"Unknown item: {item_id}")
    tables = [row["table_name"] for row in db.query("dq_item_tables", item_id=item_id,
                                                     order_by="table_name")]
    if not tables:
        raise ManifestError("dataset has no profiled tables")
    table = tables[0]
    contexts = ["GENERAL"]
    inventory_rows = _inventory(item_id, table)
    fields = [_field_card(row, contexts) for row in inventory_rows]
    if not fields:
        raise ManifestError("selected table has no profiled fields")
    now, run_id = db.now_ist(), _id()
    safe_defaults: dict[str, Any] = {
        "variance_window": 3,
        "minimum_row_count": 10,
        "not_applicable_share_threshold": 0.8,
        "field_specific_sentinel_definitions": {},
    }
    if item.get("as_of_date"):
        safe_defaults["as_of_date"] = item["as_of_date"]
    manifest = {
        "manifest_version": MANIFEST_VERSION, "manifest_kind": MANIFEST_KIND,
        "run_id": run_id, "item_id": item_id, "item_name": item.get("name"),
        "diagnostic_id": DIAGNOSTIC_ID, "tenant_id": tenant_id,
        "diagnostic": {key: register.get(key) for key in (
            "name", "area", "mode", "stage", "decision_type", "metric", "kb_dependency")},
        "engine_version": ENGINE_VERSION, "status": DRAFT, "created_at": now,
        "created_by": actor, "introduction_acknowledged": False,
        "introduction": {
            "title": "Understand value semantics before you run",
            "purpose": "Classify cells that are censored, stale/frozen, or not applicable so downstream analysis can treat them correctly.",
            "tags": [
                {"tag": "CENSORED", "meaning": "The value is not yet observable inside the declared outcome or forward window."},
                {"tag": "STALE_FROZEN", "meaning": "A sentinel is present or a value fails to vary at its declared monitoring grain."},
                {"tag": "NOT_APPLICABLE", "meaning": "The concept does not apply to the row under confirmed business conditions."},
            ],
            "boundaries": [
                "No tag means no KB condition fired; it is not a universal validity certificate.",
                "UNSCOPED and UNCLASSIFIED describe coverage or evidence gaps; they are not cell tags.",
                "PD, LGD, and EAD are optional context labels and never activate rules by themselves.",
                "AI suggestions use column metadata only and require human confirmation before execution.",
                "Tags are treatment evidence. Only reviewed anomaly groups should be promoted to Issues and RCA.",
            ],
        },
        "tables": tables, "table": table, "fields": fields,
        "role_catalog": [
            {key: role.get(key) for key in ("role", "definition", "role_kind", "model_types")}
            for role in knowledge.resources()[0]["semantic_roles"]
        ],
        "context": {"selected": contexts, "confirmation_required": False,
                    "execution_effect": "none",
                    "suggestions": _context_suggestions(fields)},
        "runtime_declarations": safe_defaults,
        "known_declarations": sorted(_known_declarations()),
        "knowledge": {
            "value_semantics_version": str(knowledge.resources()[0]["metadata"]["version"]),
            "terminology_version": str(knowledge.resources()[1]["metadata"]["version"]),
            "value_semantics_document_id": knowledge.VALUE_DOCUMENT_ID,
            "terminology_document_id": knowledge.TERMINOLOGY_DOCUMENT_ID,
        },
        "row_reference": {"column": "__d08_row_reference__", "generated": True,
                          "raw_values_retained": False},
        "source_artifact_references": [
            {"artifact_id": row["profile_artifact_id"], "artifact_type": "column_profile"}
            for row in inventory_rows if row.get("profile_artifact_id")
        ],
    }
    db.insert("diag_runs", {
        "run_id": run_id, "item_id": item_id, "diagnostic_id": DIAGNOSTIC_ID,
        "status": DRAFT, "manifest_json": manifest,
        "engine_versions_json": {"value_semantics": ENGINE_VERSION},
        "artifact_origin": item.get("artifact_origin") or "unclassified",
        "created_at": now, "started_at": None, "finished_at": None,
    })
    record_zero_llm_usage(run_id, actor=actor, purpose=AI_PURPOSE,
                          reason="AI role adjudication is optional and has not been requested")
    reused_ai = _hydrate_reusable_ai(manifest, actor)
    _refresh(manifest)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    record_decision(run_id, "default_applied", {
        "context": ["GENERAL"], "context_is_non_enforcing": True,
        "safe_runtime_defaults": safe_defaults,
        "exact_bindings": sum(not row["review_required"] for row in fields),
        "reused_ai_inferences": reused_ai,
    }, actor)
    return manifest


def latest_draft(item_id: str, tenant_id: str = "bootstrap",
                 actor: str = "system") -> dict[str, Any] | None:
    rows = [row for row in db.query(
        "diag_runs", item_id=item_id, diagnostic_id=DIAGNOSTIC_ID,
        status=DRAFT, order_by="created_at DESC, run_id DESC",
    ) if str((row.get("manifest_json") or {}).get("tenant_id") or "bootstrap") == tenant_id]
    if not rows:
        return None
    if len(rows) > 1:
        for duplicate in rows[1:]:
            _discard(duplicate, actor, "superseded_duplicate_draft")
    run = rows[0]
    manifest = refresh_draft_scope(run["run_id"], tenant_id=tenant_id)
    completed = int(manifest.get("introduction_acknowledged", False))
    completed += int(bool(manifest.get("selected_fields")))
    completed += int(not any(b["code"] == "binding_confirmation_required" for b in manifest["blockers"]))
    completed += int(manifest.get("ready_to_run", False))
    return {"run_id": run["run_id"], "item_id": item_id, "diagnostic_id": DIAGNOSTIC_ID,
            "status": DRAFT, "created_at": run.get("created_at"),
            "last_saved_at": manifest.get("updated_at") or run.get("created_at"),
            "completed_steps": completed, "total_steps": 4,
            "selected_feature_count": len(manifest.get("selected_fields") or []),
            "workflow_kind": "value_semantics"}


def _discard(run: dict[str, Any], actor: str, reason: str) -> None:
    now = db.now_ist()
    manifest = dict(run.get("manifest_json") or {})
    manifest.update({"status": "discarded", "discarded_at": now,
                     "discarded_by": actor, "discard_reason": reason, "updated_at": now})
    db.update("diag_runs", {"run_id": run["run_id"]}, {
        "status": "discarded", "manifest_json": manifest, "finished_at": now,
    })


def discard_drafts(item_id: str, actor: str = "system",
                   tenant_id: str = "bootstrap") -> int:
    rows = [row for row in db.query("diag_runs", item_id=item_id,
                                     diagnostic_id=DIAGNOSTIC_ID, status=DRAFT)
            if str((row.get("manifest_json") or {}).get("tenant_id") or "bootstrap") == tenant_id]
    for row in rows:
        _discard(row, actor, "start_afresh")
    return len(rows)


def refresh_draft_scope(run_id: str, actor: str = "system", *,
                        tenant_id: str | None = None) -> dict[str, Any]:
    run = _run(run_id)
    manifest = dict(run.get("manifest_json") or {})
    if tenant_id is not None and str(manifest.get("tenant_id") or "bootstrap") != tenant_id:
        raise KeyError("Unknown value-semantics run")
    if run["status"] != DRAFT:
        return manifest
    _hydrate_reusable_ai(manifest, actor)
    _refresh(manifest)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def _field(manifest: dict[str, Any], column: str) -> dict[str, Any]:
    row = next((item for item in manifest["fields"] if item["column"] == column), None)
    if row is None:
        raise ManifestError(f"unknown field: {column!r}")
    return row


def _adjudicate_with_expansion(adjudicator: Any, input_: Any, kb: dict[str, Any]):
    """Validate provider output and expand bounded role details when requested."""
    attempts: list[dict[str, Any]] = []
    calls = []
    current_input = input_
    expansion_passes = 0
    while True:
        call = adjudicator.adjudicate(current_input)
        output = validate_role_adjudication(current_input, call.output)
        attempts.append(output.model_dump(mode="json"))
        calls.append(call)
        if output.decision is not RoleDecision.CANDIDATE_SET_INCOMPLETE:
            return call, output, current_input, attempts, calls
        expansion_passes += 1
        if expansion_passes > 3:
            raise RuntimeError("Role adjudication exceeded three candidate-expansion passes")
        current_input = expand_adjudication_input(
            current_input, output.requested_expansion_roles, kb,
        )


def _request_ai_review(run_id: str, manifest: dict[str, Any], column: str,
                       actor: str) -> None:
    from .adjudication import OpenAIRoleAdjudicator, configured_model, create_openai_client

    field = _field(manifest, column)
    if not field.get("review_required"):
        raise ManifestError(
            "AI role review is available only for fields requiring confirmation; "
            "reopen the confirmed decision first"
        )
    if (field.get("adjudication") or {}).get("status") == "proposal_ready":
        return
    _hydrate_reusable_ai(manifest, actor)
    if (field.get("adjudication") or {}).get("status") == "proposal_ready":
        return
    inventory = next(row for row in _inventory(manifest["item_id"], manifest["table"])
                     if row["column_name"] == column)
    from .matching import match_variable_to_roles
    kb, _terminology, prepared = knowledge.resources()
    match = match_variable_to_roles(_variable(inventory, manifest["context"]["selected"]), prepared)
    if match.get("exact_match"):
        # A formerly exact binding can reach this branch only after the user
        # explicitly reopens it. Supply its governed definition to the bounded review.
        exact_role = match["exact_match"]["role"]
        match["detailed_candidates"] = [knowledge.role_index()[exact_role]]
    input_ = build_adjudication_input(match, kb)
    prompt_text = knowledge.prompt()
    started = time.monotonic()
    try:
        adjudicator = OpenAIRoleAdjudicator(
            client=create_openai_client(), prompt=prompt_text,
            model=configured_model(),
        )
        call, validated, input_, attempts, calls = _adjudicate_with_expansion(
            adjudicator, input_, kb,
        )
        output = validated.model_dump(mode="json")
        proposed = [value for value in [output.get("primary_role"),
                    *(output.get("secondary_roles") or [])] if value]
        field["proposed_roles"] = expand_implied_roles(proposed, kb) if proposed else []
        proposal_source = {
            "MATCH": "ai_proposal_unconfirmed",
            "MULTI_ROLE_MATCH": "ai_proposal_unconfirmed",
            "NO_CANDIDATE_MATCH": "ai_no_applicable_role",
            "INSUFFICIENT_CONTEXT": "ai_insufficient_context",
            "AMBIGUOUS_ROLE": "ai_ambiguous_role",
        }.get(output["decision"], "ai_review_unresolved")
        field["binding_source"] = proposal_source
        field["review_required"] = True
        field["adjudication"] = {"status": "proposal_ready", "output": output,
                                 "expansion_passes": len(attempts) - 1,
                                 "attempts": [*(field.get("adjudication") or {}).get("attempts", []),
                                              *attempts]}
        record_llm_call(
            run_id, status="succeeded", provider="openai_responses",
            model=call.response_model or adjudicator.model,
            provider_api_version="responses_v1",
            prompt_template_id="value_semantics_role_adjudication",
            prompt_template_version=adjudicator.prompt_version,
            prompt_hash=stable_fingerprint(prompt_text),
            redacted_input_manifest=input_.model_dump(mode="json"),
            validated_response=output,
            source_artifact_references=manifest.get("source_artifact_references") or [],
            proposed_mapping={"column": column, "roles": field["proposed_roles"]},
            deterministic_mapping={"column": column, "roles": field.get("confirmed_roles") or []},
            user_disposition="pending_human_confirmation", final_applied_mapping={},
            actor=actor, purpose=AI_PURPOSE, provider_request_id=call.response_id,
            prompt_tokens=sum(value.input_tokens or 0 for value in calls) or None,
            completion_tokens=sum(value.output_tokens or 0 for value in calls) or None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        field["adjudication"] = {"status": "failed", "error": type(exc).__name__,
                                 "attempts": (field.get("adjudication") or {}).get("attempts", [])}
        field["binding_source"] = "ai_review_failed"
        field["review_required"] = True
        record_llm_call(
            run_id, status="failed", provider="openai_responses", model=configured_model(),
            provider_api_version="responses_v1",
            prompt_template_id="value_semantics_role_adjudication",
            prompt_template_version="value_semantics_role_adjudication_v0_2",
            prompt_hash=stable_fingerprint(prompt_text),
            redacted_input_manifest=input_.model_dump(mode="json"), validated_response=None,
            source_artifact_references=manifest.get("source_artifact_references") or [],
            proposed_mapping=None,
            deterministic_mapping={"column": column, "roles": field.get("confirmed_roles") or []},
            user_disposition="manual_review_required", final_applied_mapping={}, actor=actor,
            purpose=AI_PURPOSE, latency_ms=int((time.monotonic() - started) * 1000),
            sanitized_error=type(exc).__name__,
        )


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError("frozen value-semantics manifests are immutable")
    manifest, kind = dict(run["manifest_json"]), str(patch.get("kind") or "")
    before: Any = None
    if kind == "introduction_acknowledgement":
        before = manifest.get("introduction_acknowledged")
        manifest["introduction_acknowledged"] = bool(patch.get("enabled"))
    elif kind == "context_selection":
        raw = patch.get("value") or ["GENERAL"]
        values = [str(value).upper() for value in (raw if isinstance(raw, list) else [raw])]
        if not values or not set(values) <= CONTEXTS:
            raise ManifestError("context must contain General, PD, LGD, and/or EAD")
        before = manifest["context"]["selected"]
        manifest["context"]["selected"] = list(dict.fromkeys(values))
    elif kind == "table_selection":
        table = str(patch.get("table") or "")
        if table not in manifest["tables"]:
            raise ManifestError("select a profiled table")
        before = manifest["table"]
        manifest["table"] = table
        contexts = manifest["context"]["selected"]
        inventory_rows = _inventory(manifest["item_id"], table)
        manifest["fields"] = [_field_card(row, contexts) for row in inventory_rows]
        manifest["source_artifact_references"] = [
            {"artifact_id": row["profile_artifact_id"], "artifact_type": "column_profile"}
            for row in inventory_rows if row.get("profile_artifact_id")
        ]
        _hydrate_reusable_ai(manifest, actor)
    elif kind == "field_scope":
        field = _field(manifest, str(patch.get("feature") or patch.get("column") or ""))
        if patch.get("enabled") and (field.get("applicability") or {}).get("status") == "not_applicable":
            raise ManifestError("Reopen the not-applicable decision or confirm a role before including this field")
        before = field.get("selected")
        field["selected"] = bool(patch.get("enabled"))
    elif kind == "bulk_scope":
        values = set(patch.get("features") or [])
        unknown = values - {row["column"] for row in manifest["fields"]}
        if unknown:
            raise ManifestError(f"unknown fields: {sorted(unknown)}")
        if any(field["column"] in values and (field.get("applicability") or {}).get("status") == "not_applicable"
               for field in manifest["fields"]):
            raise ManifestError("Reopen not-applicable decisions or confirm roles before including these fields")
        before = manifest.get("selected_fields") or []
        for field in manifest["fields"]:
            field["selected"] = field["column"] in values
    elif kind == "field_applicability":
        field = _field(manifest, str(patch.get("feature") or patch.get("column") or ""))
        status = patch.get("value")
        if not isinstance(status, str) or status not in {"not_applicable", "review"}:
            raise ManifestError("applicability must be not_applicable or review")
        reason = str(patch.get("reason") or "").strip()
        if status == "not_applicable" and not 1 <= len(reason) <= 2000:
            raise ManifestError("A not-applicable decision requires a reason of 1 to 2000 characters")
        before = next(row for row in field_scope_decisions(manifest) if row["column"] == field["column"])
        field.update({"selected": False, "confirmed_roles": [], "confirmed_by": None,
                      "confirmed_at": None, "review_required": status == "review",
                      "binding_source": "human_not_applicable" if status == "not_applicable" else "human_reopened",
                      "applicability": {
                          "status": "not_applicable", "reason": reason,
                          "confirmed_by": actor, "confirmed_at": db.now_ist(),
                          "source": "human_confirmation",
                          "ai_evidence": (field.get("adjudication") or {}).get("output"),
                      } if status == "not_applicable" else None})
    elif kind == "role_binding":
        field = _field(manifest, str(patch.get("feature") or patch.get("column") or ""))
        raw = patch.get("value") if patch.get("value") is not None else patch.get("role")
        roles = [str(value) for value in (raw if isinstance(raw, list) else [raw]) if value]
        valid = set(knowledge.role_index())
        if not roles or not set(roles) <= valid:
            raise ManifestError("select one or more roles from the active Value Semantics KB")
        before = next(row for row in field_scope_decisions(manifest) if row["column"] == field["column"])
        field.update({"confirmed_roles": expand_implied_roles(roles, knowledge.resources()[0]),
                      "proposed_roles": roles, "binding_source": "human_confirmed",
                      "review_required": False, "selected": True, "confirmed_by": actor,
                      "confirmed_at": db.now_ist(), "applicability": None})
    elif kind == "declaration_update":
        key = str(patch.get("key") or "")
        if key not in _known_declarations():
            raise ManifestError("unknown runtime declaration")
        before = (manifest.get("runtime_declarations") or {}).get(key)
        value = patch.get("value")
        if value is None or value == "":
            manifest["runtime_declarations"].pop(key, None)
        else:
            _validate_declarations({key: value})
            manifest["runtime_declarations"][key] = value
    elif kind == "declarations_replace":
        value = patch.get("value")
        if not isinstance(value, dict):
            raise ManifestError("runtime declarations must be a JSON object")
        _validate_declarations(value)
        before = manifest.get("runtime_declarations") or {}
        manifest["runtime_declarations"] = value
    elif kind == "request_ai_role_review":
        column = str(patch.get("feature") or patch.get("column") or "")
        _request_ai_review(run_id, manifest, column, actor)
        before = "not_requested"
    else:
        raise ManifestError(f"unsupported value-semantics manifest decision: {kind!r}")
    manifest["context"]["suggestions"] = _context_suggestions(manifest["fields"])
    manifest["updated_at"] = db.now_ist()
    _refresh(manifest)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    audit_kind = (
        "role_override" if kind == "role_binding" else
        "role_verification_change" if kind == "request_ai_role_review" else
        "threshold_tune" if kind in {"declaration_update", "declarations_replace"} else
        "scope_exclusion"
    )
    record_decision(run_id, audit_kind, {
        "event": kind, "before": before, "patch": patch,
        "ready_to_run": manifest["ready_to_run"],
    }, actor)
    return manifest


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        if run["status"] == RUNNING:
            return run["manifest_json"]
        raise ManifestError(f"run cannot freeze from status {run['status']}")
    manifest = dict(run["manifest_json"])
    _refresh(manifest)
    if manifest["blockers"]:
        raise ManifestError("; ".join(item["message"] for item in manifest["blockers"]))
    now = db.now_ist()
    fingerprint_input = {key: value for key, value in manifest.items()
                         if key not in {"run_id", "status", "created_at", "created_by",
                                       "updated_at", "inference_disclosure", "blockers",
                                       "ready_to_run"}}
    manifest.update({"status": RUNNING, "frozen_at": now, "frozen_by": actor,
                     "manifest_fingerprint": stable_fingerprint(fingerprint_input),
                     "inference_disclosure": inference_disclosure(run_id)})
    db.update("diag_runs", {"run_id": run_id}, {
        "status": RUNNING, "started_at": now, "manifest_json": manifest,
    })
    record_decision(run_id, "scope_exclusion", {
        "event": "manifest_freeze", "manifest_fingerprint": manifest["manifest_fingerprint"],
        "selected_fields": manifest["selected_fields"],
        "ready_routes": len(manifest["coverage"]["ready_routes"]),
        "unscoped_routes": len(manifest["coverage"]["unscoped_routes"]),
    }, actor)
    return manifest


__all__ = [
    "DIAGNOSTIC_ID", "MANIFEST_KIND", "build_manifest", "discard_drafts",
    "freeze", "get_run", "latest_draft", "patch_manifest", "refresh_draft_scope",
]
