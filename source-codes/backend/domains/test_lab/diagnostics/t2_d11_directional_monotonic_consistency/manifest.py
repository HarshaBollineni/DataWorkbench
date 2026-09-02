"""Governed draft/freeze scope for T2-D11 directional consistency."""
from __future__ import annotations

import uuid
import time
from typing import Any

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader
from domains.test_lab.shared.run_state import DRAFT, RUNNING, ManifestError, list_decisions, record_decision
from dq_diagnostics.readiness import readiness
from dq_diagnostics.register import get_diagnostic, require_executable
from dq_diagnostics.thresholds import effective_threshold
from dq_diagnostics.inference_audit import (
    inference_disclosure, record_llm_call, record_zero_llm_usage,
)

from . import knowledge
from .adjudication import adjudicate, configuration_metadata
from .matching import match_feature_to_kb

DIAGNOSTIC_ID = 11
MANIFEST_KIND = "directional_monotonic_consistency"
MANIFEST_VERSION = 3
ENGINE_VERSION = "0.1.0"
THRESHOLD_KEYS = ("corr_floor", "regression_floor", "bin_range_floor_sd", "min_sample",
                  "min_binary_class", "significance_level")
EXPECTED_DIRECTIONS = {"INCREASING", "DECREASING", "NON_MONOTONIC",
                       "NO_CLEAR_DIRECTION", "NOT_APPLICABLE", "EXCLUDED"}
ORIENTATIONS = {"HIGHER_IS_WORSE", "HIGHER_IS_BETTER"}
AI_PURPOSE = "directionality_semantic_adjudication"


def _id() -> str:
    return f"drun_{uuid.uuid4().hex[:12]}"


def _run(run_id: str) -> dict[str, Any]:
    row = db.query_one("diag_runs", run_id=run_id)
    if row is None:
        raise KeyError(f"Unknown run: {run_id}")
    if row.get("diagnostic_id") != DIAGNOSTIC_ID:
        raise ManifestError("run does not belong to diagnostic #11")
    return row


def get_run(run_id: str) -> dict[str, Any]:
    return _run(run_id)


def _flip(direction: str) -> str:
    return {"INCREASING": "DECREASING", "DECREASING": "INCREASING"}.get(direction, direction)


def _cardinality(row: dict[str, Any]) -> int | None:
    try:
        value = (row.get("profile_json") or {}).get("cardinality")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _profile_artifacts(item_id: str, table: str) -> dict[str, dict[str, Any]]:
    try:
        from domains.aar.repository import AnalysisArtifactRepository
        repo = AnalysisArtifactRepository()
        result = {}
        for meta in repo.list(snapshot_id=item_id, artifact_type="column_profile", status="active"):
            if meta.identity.get("table") == table and meta.feature and meta.feature not in result:
                _metadata, payload = repo.get(meta.artifact_id)
                result[meta.feature] = {**(payload or {}), "artifact_id": meta.artifact_id}
        return result
    except Exception:
        return {}


def _direction_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature": row["feature"],
        "knowledge_base_concept": row.get("canonical_feature"),
        "representation_orientation": row.get("representation_orientation"),
        "expected_direction": row.get("expected_direction"),
        "classification_source": row.get("classification_source"),
    }


def _sanitized_ai_error(exc: Exception) -> str:
    parts = [type(exc).__name__]
    status_code = getattr(exc, "status_code", None)
    if status_code:
        parts.append(f"http_{status_code}")
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("code"):
        parts.append(str(body["code"]))
    return ":".join(parts)


def _scope(item_id: str) -> tuple[dict[str, Any], str, str, list[dict[str, Any]]]:
    item = db.query_one("dq_items", item_id=item_id)
    if item is None:
        raise KeyError(f"Unknown item: {item_id}")
    target = str(item.get("target_variable") or "").strip()
    if not target:
        raise ManifestError("target variable is not selected in Data Sourcing")
    inventory = db.query("variable_inventory", item_id=item_id)
    target_rows = [row for row in inventory if row.get("column_name") == target]
    tables = sorted({row.get("table_name") for row in target_rows if row.get("table_name")})
    if len(tables) != 1:
        raise ManifestError("confirmed target must resolve to exactly one profiled table")
    table = tables[0]
    rows = [row for row in inventory if row.get("table_name") == table]
    profiles = _profile_artifacts(item_id, table)
    enriched = []
    for row in rows:
        profile = profiles.get(row["column_name"], {})
        enriched.append({**row,
            "description": profile.get("description") or row.get("description") or "",
            "special_values": profile.get("special_values") or [],
            "special_values_confirmed": bool(profile.get("special_values_confirmed")),
            "profile_artifact_id": profile.get("artifact_id")})
    return item, table, target, enriched


def _exact_decision(match: dict[str, Any] | None, kb_version: str) -> dict[str, Any] | None:
    """Return the governed decision supplied by an exact KB representation match."""
    if not match:
        return None
    orientation = "INVERSE" if match.get("is_inverse_representation") else "SAME"
    direction = str(match["expected_direction"]).upper()
    if orientation == "INVERSE":
        direction = _flip(direction)
    return {
        "kb_version": str(kb_version),
        "canonical_feature": match["canonical_feature"],
        "representation_orientation": orientation,
        "expected_direction": direction,
    }


def _governed_exact_decision(row: dict[str, Any]) -> dict[str, Any] | None:
    """Recompute immutable exact-match evidence for a saved feature card."""
    kb, terminology, prepared = knowledge.resources()
    result = match_feature_to_kb(
        row["feature"], row.get("description") or "", kb, terminology,
        top_n=10, prepared_matcher=prepared,
    )
    return _exact_decision(
        result.get("deterministic_match"),
        (kb.get("metadata") or {}).get("version") or "0.3",
    )


def _backfill_governed_exact_decisions(manifest: dict[str, Any]) -> bool:
    """Upgrade pre-baseline drafts without changing a recorded baseline."""
    changed = False
    for row in manifest.get("features", []):
        if "governed_exact_decision" not in row:
            row["governed_exact_decision"] = _governed_exact_decision(row)
            changed = True
    return changed


def _matches_governed_exact_decision(
    row: dict[str, Any], *, canonical_feature: str | None,
    representation_orientation: str | None, expected_direction: str,
) -> bool:
    baseline = row.get("governed_exact_decision")
    if not isinstance(baseline, dict):
        return False
    return (
        canonical_feature == baseline.get("canonical_feature")
        and representation_orientation == baseline.get("representation_orientation")
        and expected_direction == baseline.get("expected_direction")
    )


def _feature_card(row: dict[str, Any]) -> dict[str, Any]:
    kb, terminology, prepared = knowledge.resources()
    name = row["column_name"]
    result = match_feature_to_kb(name, row.get("description") or "", kb, terminology,
                                 top_n=10, prepared_matcher=prepared)
    data_type = str(row.get("data_type") or "")
    numeric = any(token in data_type.lower() for token in (
        "int", "float", "double", "decimal", "numeric", "number"))
    base = {"feature": name, "description": row.get("description") or "",
            "data_type": data_type, "role": row.get("role"), "numeric": numeric,
            "special_values": row.get("special_values") if row.get("special_values_confirmed") else [],
            "profile_artifact_id": row.get("profile_artifact_id"),
            "match_status": result["match_status"], "match_method": result["match_method"],
            "candidates": result.get("top_candidates") or [], "candidate_display_limit": 3,
            "canonical_feature": None, "representation_orientation": None,
            "expected_direction": None, "knowledge_strength": None,
            "governed_exact_decision": None,
            "classification_source": None, "rationale": None,
            "review_required": True, "scope_selected": False, "selected": False,
            "adjudication": {"status": "not_requested", "attempts": []},
            "kb_proposal": None}
    exact = result.get("deterministic_match")
    if exact:
        governed_decision = _exact_decision(
            exact, (kb.get("metadata") or {}).get("version") or "0.3",
        )
        base.update({"canonical_feature": exact["canonical_feature"],
                     "representation_orientation": governed_decision["representation_orientation"],
                     "expected_direction": governed_decision["expected_direction"],
                     "governed_exact_decision": governed_decision,
                     "knowledge_strength": str(exact["knowledge_strength"]).upper(),
                     "classification_source": "KB_V0_3_EXACT",
                     "rationale": exact["rationale"],
                     "review_required": False})
    elif not numeric:
        base.update({"expected_direction": "NOT_APPLICABLE",
                     "classification_source": "SYSTEM_TYPE_GATE",
                     "rationale": "Ordered numeric directionality is not applicable.",
                     "selected": False})
    return base


def _refresh(manifest: dict[str, Any]) -> dict[str, Any]:
    reference = manifest["reference"]["column"]
    segment = manifest.get("segment_column")
    scoped = [row["feature"] for row in manifest["features"]
              if row.get("scope_selected") and row.get("numeric")
              and row["feature"] not in {reference, segment}]
    for row in manifest["features"]:
        row["selected"] = bool(
            row["feature"] in scoped
            and row.get("expected_direction") not in {None, "NOT_APPLICABLE", "EXCLUDED"}
        )
    selected = [row["feature"] for row in manifest["features"] if row.get("selected")]
    unresolved = [row["feature"] for row in manifest["features"]
                  if row["feature"] in scoped and (
                      row.get("expected_direction") is None or row.get("review_required"))]
    blockers = []
    if manifest["reference"].get("orientation") not in ORIENTATIONS:
        blockers.append({"code": "reference_orientation_required",
                         "message": "Confirm whether a higher target value indicates higher or lower risk."})
    if not scoped:
        blockers.append({"code": "feature_classification_required",
                         "message": "Select at least one numeric column for analysis."})
    elif unresolved:
        blockers.append({"code": "feature_classification_required",
                         "message": f"Confirm the expected risk direction for {len(unresolved)} selected column(s) before running."})
    elif not selected:
        blockers.append({"code": "feature_classification_required",
                         "message": "At least one selected column must remain applicable for analysis."})
    manifest["scope_features"] = scoped
    manifest["selected_features"] = selected
    manifest["blockers"] = blockers
    manifest["ready_to_run"] = not blockers
    return manifest


def build_manifest(item_id: str, actor: str = "system", *,
                   tenant_id: str = "bootstrap",
                   enforce_register: bool = True) -> dict[str, Any]:
    if latest_draft(item_id, tenant_id=tenant_id, actor=actor):
        raise ManifestError(
            "An open directionality draft already exists; resume it or start afresh."
        )
    register = require_executable(DIAGNOSTIC_ID) if enforce_register else get_diagnostic(DIAGNOSTIC_ID)
    if enforce_register:
        state = readiness(item_id, DIAGNOSTIC_ID, tenant_id)
        if state.status != "ready":
            raise ManifestError(f"{state.status}: {state.reason}")
    item, table, target, rows = _scope(item_id)
    target_row = next(row for row in rows if row["column_name"] == target)
    features = [_feature_card(row) for row in rows if row["column_name"] != target]
    if not features:
        raise ManifestError("no independent variables are available")
    source_artifact_references = [
        {"artifact_id": row["profile_artifact_id"], "artifact_type": "column_profile"}
        for row in rows if row.get("profile_artifact_id")
    ]
    thresholds = {key: effective_threshold(DIAGNOSTIC_ID, key) for key in THRESHOLD_KEYS}
    run_id, now = _id(), db.now_ist()
    manifest = {"manifest_version": MANIFEST_VERSION, "manifest_kind": MANIFEST_KIND,
        "run_id": run_id, "item_id": item_id, "item_name": item.get("name"),
        "diagnostic_id": DIAGNOSTIC_ID, "tenant_id": tenant_id,
        "diagnostic": {key: register.get(key) for key in (
            "name", "area", "mode", "stage", "decision_type", "metric", "kb_dependency")},
        "engine_version": ENGINE_VERSION, "status": DRAFT, "created_at": now,
        "created_by": actor, "table": table,
        "source_artifact_references": source_artifact_references,
        "reference": {"column": target, "type": "auto", "positive_class": None,
                      "orientation": None, "source": "confirmed Data Sourcing target",
                      "special_values": target_row.get("special_values") if target_row.get("special_values_confirmed") else []},
        "reference_candidates": [{"column": row["column_name"],
            "description": row.get("description") or "", "data_type": row.get("data_type"),
            "role": row.get("role"), "is_saved_target": row["column_name"] == target,
            "cardinality": _cardinality(row),
            "special_values": row.get("special_values") if row.get("special_values_confirmed") else []}
            for row in rows if row["column_name"] == target or (
                str(row.get("role") or "").lower() not in {"identifier", "period", "segment", "category", "group"}
                and (any(token in str(row.get("data_type") or "").lower() for token in (
                    "int", "float", "double", "decimal", "numeric", "number"))
                    or (_cardinality(row) is not None and _cardinality(row) <= 20)))],
        "segment_column": None, "segment_preview": None,
        "segment_candidates": [{"column": row["column_name"],
            "description": row.get("description") or "", "data_type": row.get("data_type"),
            "cardinality": _cardinality(row)}
            for row in rows if row["column_name"] != target and str(row.get("role") or "").lower() in {
                "segment", "category", "group"}],
        "features": features, "thresholds": thresholds,
        "parameters": {"requested_bins": 5, "chart_sample_limit": 400,
                       "bin_method": "equal_frequency_broad_shape"},
        "knowledge": {"version": "0.3", "terminology_version": "0.2",
                      "prompt_version": "v0_2"}}
    _refresh(manifest)
    db.insert("diag_runs", {"run_id": run_id, "item_id": item_id,
        "diagnostic_id": DIAGNOSTIC_ID, "manifest_json": manifest, "status": DRAFT,
        "engine_versions_json": {"directionality": ENGINE_VERSION},
        "created_at": now, "started_at": None, "finished_at": None})
    record_zero_llm_usage(
        run_id, actor=actor, purpose=AI_PURPOSE,
        reason="AI matching is optional and has not been requested",
    )
    manifest["inference_disclosure"] = inference_disclosure(run_id)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    record_decision(run_id, "default_applied", {
        "kb_version": "0.3", "requested_bins": 5,
        "exact_matches_available": sum(row["classification_source"] == "KB_V0_3_EXACT" for row in features)}, actor)
    return manifest


def _draft_progress(manifest: dict[str, Any]) -> dict[str, Any]:
    scoped = manifest.get("scope_features") or [row["feature"] for row in manifest.get("features", [])
        if row.get("scope_selected")]
    classified = [row["feature"] for row in manifest.get("features", [])
                  if row["feature"] in scoped and row.get("expected_direction") is not None]
    completed = 1 if manifest.get("reference", {}).get("orientation") in ORIENTATIONS else 0
    if completed == 1 and scoped:
        completed = 2
    if manifest.get("ready_to_run"):
        completed = 3
    return {"completed_steps": completed, "total_steps": 3,
            "selected_feature_count": len(scoped),
            "classified_feature_count": len(classified), "frozen_bin_count": 0,
            "workflow_kind": "directionality"}


def _discard_rows(rows: list[dict[str, Any]], actor: str, reason: str) -> int:
    now = db.now_ist()
    for run in rows:
        payload = dict(run.get("manifest_json") or {})
        payload.update({"status": "discarded", "discarded_at": now,
                        "discarded_by": actor, "discard_reason": reason,
                        "updated_at": now})
        db.update("diag_runs", {"run_id": run["run_id"]}, {
            "manifest_json": payload, "status": "discarded", "finished_at": now,
        })
    return len(rows)


def _tenant_drafts(item_id: str, tenant_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "diag_runs", item_id=item_id, diagnostic_id=DIAGNOSTIC_ID,
        status=DRAFT, order_by="created_at DESC, run_id DESC",
    )
    return [row for row in rows if str(
        (row.get("manifest_json") or {}).get("tenant_id") or "bootstrap"
    ) == tenant_id]


def latest_draft(item_id: str, tenant_id: str = "bootstrap",
                 actor: str = "system") -> dict[str, Any] | None:
    """Return one resumable draft and archive older duplicate active drafts."""
    rows = _tenant_drafts(item_id, tenant_id)
    if not rows:
        return None
    if len(rows) > 1:
        _discard_rows(rows[1:], actor, "superseded_duplicate_draft")
    run = rows[0]
    payload = refresh_draft_scope(
        run["run_id"], actor=actor, tenant_id=tenant_id,
    )
    return {"run_id": run["run_id"], "item_id": run["item_id"],
            "diagnostic_id": DIAGNOSTIC_ID, "status": DRAFT,
            "created_at": run.get("created_at"),
            "last_saved_at": payload.get("updated_at") or run.get("created_at"),
            **_draft_progress(payload)}


def discard_drafts(item_id: str, actor: str = "system",
                   tenant_id: str = "bootstrap") -> int:
    rows = _tenant_drafts(item_id, tenant_id)
    return _discard_rows(rows, actor, "start_afresh")


def refresh_draft_scope(run_id: str, actor: str = "system", *,
                        tenant_id: str | None = None) -> dict[str, Any]:
    """Upgrade older open directionality drafts without changing frozen runs."""
    run = _run(run_id)
    payload = dict(run.get("manifest_json") or {})
    stored_tenant = str(payload.get("tenant_id") or "bootstrap")
    if tenant_id is not None and stored_tenant != tenant_id:
        raise KeyError("Unknown directionality run")
    if run["status"] != DRAFT:
        return payload
    changed = False
    if "tenant_id" not in payload:
        payload["tenant_id"] = stored_tenant
        changed = True
    if _backfill_governed_exact_decisions(payload):
        changed = True
    rules = knowledge.rule_index()
    for row in payload.get("features", []):
        if "scope_selected" not in row:
            row["scope_selected"] = bool(row.get("selected"))
            changed = True
        concept = row.get("canonical_feature")
        if concept in rules and row.get("rationale") == "Deterministic exact KB representation match.":
            row["rationale"] = rules[concept]["rationale"]
            changed = True
        for candidate in row.get("candidates") or []:
            candidate_rule = rules.get(candidate.get("canonical_feature"))
            if candidate_rule and not candidate.get("rationale"):
                candidate["rationale"] = candidate_rule["rationale"]
                changed = True
    if "reference_candidates" not in payload:
        reference = payload.get("reference") or {}
        payload["reference_candidates"] = [{"column": reference.get("column"),
            "is_saved_target": True, "special_values": reference.get("special_values") or []},
            *[{"column": row["feature"], "description": row.get("description") or "",
               "data_type": row.get("data_type"), "role": row.get("role"),
               "is_saved_target": False, "special_values": row.get("special_values") or []}
              for row in payload.get("features", []) if row.get("numeric")]]
        changed = True
    if "segment_preview" not in payload:
        payload["segment_preview"] = None
        changed = True
    if "source_artifact_references" not in payload:
        payload["source_artifact_references"] = [
            {"artifact_id": row["profile_artifact_id"], "artifact_type": "column_profile"}
            for row in payload.get("features", []) if row.get("profile_artifact_id")
        ]
        changed = True
    if not db.query("diag_inference_events", run_id=run_id):
        record_zero_llm_usage(
            run_id, actor=actor, purpose=AI_PURPOSE,
            reason="AI matching is optional and has not been requested",
        )
    disclosure = inference_disclosure(run_id)
    if payload.get("inference_disclosure") != disclosure:
        payload["inference_disclosure"] = disclosure
        changed = True
    if int(payload.get("manifest_version") or 1) < MANIFEST_VERSION:
        payload["manifest_version"] = MANIFEST_VERSION
        changed = True
    _refresh(payload)
    if changed:
        payload["updated_at"] = db.now_ist()
        db.update("diag_runs", {"run_id": run_id}, {"manifest_json": payload})
    return payload


def _feature(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    row = next((row for row in manifest["features"] if row["feature"] == name), None)
    if row is None:
        raise ManifestError(f"unknown feature: {name!r}")
    return row


def _segment_preview(manifest: dict[str, Any], column: str) -> dict[str, Any]:
    frame = SnapshotLoader().load_table(
        manifest["item_id"], manifest["table"], columns=[column],
    )
    series = frame[column]
    counts = series.dropna().value_counts(dropna=True)
    if len(counts) > 50:
        raise ManifestError("segment column has more than 50 observed non-null values")
    non_null = int(series.notna().sum())
    return {
        "column": column, "total_rows": int(len(series)), "non_null_rows": non_null,
        "null_rows": int(series.isna().sum()), "distinct_values": int(len(counts)),
        "groups": [{"value": str(value), "rows": int(count),
                    "share": float(count / non_null) if non_null else 0.0}
                   for value, count in counts.items()],
    }


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError("frozen directionality manifests are immutable")
    manifest, kind = dict(run["manifest_json"]), patch.get("kind")
    _backfill_governed_exact_decisions(manifest)
    if kind == "reference_selection":
        value = str(patch.get("column") or "").strip()
        candidate = next((row for row in manifest.get("reference_candidates", [])
                          if row["column"] == value), None)
        if candidate is None:
            raise ManifestError("reference column is not an available target or substitute")
        before = manifest["reference"].get("column")
        manifest["reference"].update({
            "column": value, "type": "auto", "positive_class": None,
            "source": ("confirmed Data Sourcing target" if candidate.get("is_saved_target")
                       else "user-selected target substitute"),
            "special_values": candidate.get("special_values") or [],
        })
        for row in manifest["features"]:
            if row["feature"] == value:
                row["scope_selected"] = False
                row["selected"] = False
        if manifest.get("segment_column") == value:
            manifest["segment_column"] = None
        record_decision(run_id, "scope_exclusion", {"event": "reference_selection",
                        "before": before, "after": value,
                        "source": manifest["reference"]["source"]}, actor)
    elif kind == "reference_orientation":
        value = patch.get("orientation")
        if value not in ORIENTATIONS:
            raise ManifestError("orientation must be HIGHER_IS_WORSE or HIGHER_IS_BETTER")
        before = manifest["reference"].get("orientation")
        manifest["reference"]["orientation"] = value
        record_decision(run_id, "scope_exclusion", {"event": "reference_orientation",
                        "before": before, "after": value}, actor)
    elif kind == "target_config":
        target_type = patch.get("target_type")
        if target_type not in {"auto", "binary", "continuous"}:
            raise ManifestError("target_type must be auto, binary, or continuous")
        manifest["reference"].update({"type": target_type,
                                      "positive_class": patch.get("positive_class")})
        record_decision(run_id, "scope_exclusion", {"event": "target_config",
                        "target_type": target_type,
                        "positive_class": patch.get("positive_class")}, actor)
    elif kind == "segment_selection":
        value = patch.get("segment_column") or None
        available = {row["column"] if isinstance(row, dict) else row
                     for row in manifest.get("segment_candidates", [])}
        if value is not None and value not in available:
            raise ManifestError("segment column is not an available segmentation candidate")
        manifest["segment_column"] = value
        manifest["segment_preview"] = _segment_preview(manifest, value) if value else None
        for row in manifest["features"]:
            if row["feature"] == value:
                row["scope_selected"] = False
                row["selected"] = False
        record_decision(run_id, "scope_exclusion", {"event": "segment_column", "value": value}, actor)
    elif kind == "candidate_display":
        row = _feature(manifest, patch.get("feature"))
        row["candidate_display_limit"] = min(len(row["candidates"]),
                                               max(3, int(patch.get("limit") or 3)))
    elif kind == "semantic_adjudication":
        row = _feature(manifest, patch.get("feature"))
        attempt = {"at": db.now_ist(), "actor": actor, "status": "failed"}
        deterministic_mapping = _direction_mapping(row)
        metadata = {"model": "unavailable", "provider": "configured_ai",
                    "provider_api_version": "configured",
                    "configuration_source": "unavailable"}
        redacted_input = {
            "feature": {"name": row["feature"],
                        "description": row.get("description") or None},
            "candidate_concepts": [
                {"name": item["canonical_feature"]}
                for item in (row.get("candidates") or [])[:3]
            ],
        }
        started = time.monotonic()
        try:
            metadata = configuration_metadata()
            result = adjudicate(row["feature"], row.get("description") or "", row["candidates"])
            output = result["output"]
            attempt.update({"status": "succeeded", "response_id": result.get("response_id"),
                            "model": result.get("model"), "decision": output["decision"]})
            row["adjudication"]["status"] = "succeeded"
            row["adjudication"]["result"] = result
            if output["decision"] == "MATCH":
                candidate = next(item for item in row["candidates"]
                                 if item["canonical_feature"] == output["selected_candidate"])
                direction = (None if output["representation_orientation"] == "UNDETERMINED"
                             else str(candidate["expected_direction"]).upper())
                if output["representation_orientation"] == "INVERSE":
                    direction = _flip(direction)
                row.update({"canonical_feature": output["selected_candidate"],
                            "representation_orientation": output["representation_orientation"],
                            "expected_direction": direction,
                            "knowledge_strength": str(candidate["knowledge_strength"]).upper(),
                            "classification_source": "LLM_ADJUDICATED_KB_V0_3",
                            "rationale": (f"{candidate.get('rationale') or knowledge.rule_index()[candidate['canonical_feature']]['rationale']} "
                                          f"AI match rationale: {output['reason']}"),
                            "review_required": True,
                            "selected": False})
            elif output["decision"] == "NOT_DIRECTIONAL":
                row.update({"canonical_feature": None, "representation_orientation": None,
                            "expected_direction": "NOT_APPLICABLE", "selected": False,
                            "knowledge_strength": None,
                            "classification_source": "LLM_ADJUDICATED_NOT_DIRECTIONAL",
                            "rationale": f"AI rationale: {output['reason']}",
                            "review_required": True})
            elif output["decision"] == "NO_CANDIDATE_MATCH":
                row.update({"canonical_feature": None, "representation_orientation": None,
                            "expected_direction": None, "knowledge_strength": None,
                            "classification_source": "LLM_NO_KB_MATCH",
                            "rationale": f"AI rationale: {output['reason']}",
                            "review_required": True, "selected": False})
            elif output["decision"] == "INSUFFICIENT_CONTEXT":
                row.update({"canonical_feature": None, "representation_orientation": None,
                            "expected_direction": None, "knowledge_strength": None,
                            "classification_source": "LLM_INSUFFICIENT_CONTEXT",
                            "rationale": f"AI rationale: {output['reason']}",
                            "review_required": True, "selected": False})
            usage = result.get("usage") or {}
            record_llm_call(
                run_id=run_id, status="succeeded",
                provider=result.get("provider") or metadata["provider"],
                model=result.get("model") or metadata["model"],
                provider_api_version=(result.get("provider_api_version")
                                      or metadata["provider_api_version"]),
                prompt_template_id="t2d11_semantic_feature_adjudication",
                prompt_template_version=result.get("prompt_version") or "v0_2",
                prompt_hash=stable_fingerprint({"prompt": knowledge.prompt()}),
                redacted_input_manifest=redacted_input,
                validated_response=output,
                source_artifact_references=manifest.get("source_artifact_references") or [],
                proposed_mapping=_direction_mapping(row),
                deterministic_mapping=deterministic_mapping,
                user_disposition="pending_manual_review",
                final_applied_mapping=deterministic_mapping,
                actor=actor, purpose=AI_PURPOSE,
                provider_request_id=result.get("response_id"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                latency_ms=round((time.monotonic() - started) * 1000),
                retry_count=len(row["adjudication"].get("attempts") or []),
            )
        except Exception as exc:  # retained for manual fallback; never fails the manifest
            sanitized_error = _sanitized_ai_error(exc)
            attempt["error"] = sanitized_error
            row["adjudication"]["status"] = "unavailable"
            record_llm_call(
                run_id=run_id, status="failed", provider=metadata["provider"],
                model=metadata["model"],
                provider_api_version=metadata["provider_api_version"],
                prompt_template_id="t2d11_semantic_feature_adjudication",
                prompt_template_version="semantic_feature_adjudication_v0_2",
                prompt_hash=stable_fingerprint({"prompt": knowledge.prompt()}),
                redacted_input_manifest=redacted_input, validated_response=None,
                source_artifact_references=manifest.get("source_artifact_references") or [],
                proposed_mapping=None, deterministic_mapping=deterministic_mapping,
                user_disposition="not_available",
                final_applied_mapping=deterministic_mapping,
                actor=actor, purpose=AI_PURPOSE,
                latency_ms=round((time.monotonic() - started) * 1000),
                retry_count=len(row["adjudication"].get("attempts") or []),
                sanitized_error=sanitized_error,
            )
        row["adjudication"].setdefault("attempts", []).append(attempt)
        manifest["inference_disclosure"] = inference_disclosure(run_id)
        record_decision(run_id, "role_verification_change", {"event": "semantic_adjudication",
                        "feature": row["feature"], **attempt}, actor)
    elif kind == "feature_classification":
        row = _feature(manifest, patch.get("feature"))
        direction = patch.get("expected_direction")
        rationale = str(patch.get("rationale") or "").strip()
        if direction not in EXPECTED_DIRECTIONS:
            raise ManifestError("unsupported expected direction")
        if not rationale:
            raise ManifestError("a rationale is required for manual classification")
        canonical = str(patch.get("canonical_feature") or "").strip() or None
        orientation = patch.get("representation_orientation")
        if orientation not in {None, "SAME", "INVERSE", "UNDETERMINED"}:
            raise ManifestError("unsupported representation orientation")
        proposal_requested = bool(patch.get("include_in_kb"))
        if proposal_requested and direction in knowledge.INELIGIBLE_PROPOSAL_DIRECTIONS:
            raise ManifestError(
                f"{direction} is a run-scope decision and cannot become reusable knowledge"
            )
        if proposal_requested and _matches_governed_exact_decision(
            row, canonical_feature=canonical,
            representation_orientation=orientation,
            expected_direction=direction,
        ):
            kb_version = row["governed_exact_decision"]["kb_version"]
            raise ManifestError(
                f"This decision is already covered by KB v{kb_version}; "
                "no Knowledge Base proposal is needed."
            )
        row.update({"expected_direction": direction, "canonical_feature": canonical,
                    "representation_orientation": orientation,
                    "classification_source": "USER_CONFIRMED",
                    "rationale": rationale, "review_required": False,
                    "confirmed_by": actor,
                    "selected": bool(row.get("scope_selected") and row["numeric"]
                                     and direction not in {"NOT_APPLICABLE", "EXCLUDED"})})
        row["kb_proposal"] = ({
            "requested": True, "materialized": False,
            "lifecycle_state": "queued_for_run", "requested_by": actor,
        } if proposal_requested else None)
        record_decision(run_id, "scope_exclusion", {"event": "feature_classification",
            "feature": row["feature"],
            "expected_direction": direction, "canonical_feature": canonical,
            "include_in_kb": proposal_requested, "kb_rule_id": None,
            "rationale": rationale}, actor)
    elif kind == "feature_selection":
        selected = set(patch.get("features") or [])
        known = {row["feature"] for row in manifest["features"]}
        unknown = selected - known
        if unknown:
            raise ManifestError(f"unknown feature selection: {sorted(unknown)}")
        for row in manifest["features"]:
            in_scope = bool(row["numeric"]
                            and row["feature"] not in {manifest["reference"]["column"],
                                                       manifest.get("segment_column")}
                            and row["feature"] in selected)
            row["scope_selected"] = in_scope
            row["selected"] = bool(in_scope and row.get("expected_direction")
                                   not in {None, "NOT_APPLICABLE", "EXCLUDED"})
        accepted = sorted(row["feature"] for row in manifest["features"] if row["scope_selected"])
        record_decision(run_id, "scope_exclusion", {"selected": accepted}, actor)
    elif kind == "threshold_tune":
        key = patch.get("key")
        if key not in manifest["thresholds"]:
            raise ManifestError("unknown directionality threshold")
        value = patch.get("value")
        manifest["thresholds"][key] = {"value": value,
                                       "source": f"user-set ({actor}, {db.now_ist()})"}
        record_decision(run_id, "threshold_tune", {"key": key, "value": value}, actor)
    else:
        raise ManifestError("unsupported directionality manifest decision")
    _refresh(manifest)
    manifest["updated_at"] = db.now_ist()
    changed = db.update(
        "diag_runs", {"run_id": run_id, "status": DRAFT},
        {"manifest_json": manifest},
    )
    if changed != 1:
        raise ManifestError("the directionality draft changed while this decision was saved")
    return manifest


def _materialize_knowledge_proposals(manifest: dict[str, Any], actor: str, *,
                                     conn=None) -> None:
    for row in manifest["features"]:
        proposal_intent = row.get("kb_proposal") or {}
        if not proposal_intent.get("requested") or proposal_intent.get("materialized"):
            continue
        if (not row.get("scope_selected")
                or row["feature"] in {manifest["reference"]["column"],
                                      manifest.get("segment_column")}):
            row["kb_proposal"] = None
            continue
        proposal_actor = proposal_intent.get("requested_by") or row.get("confirmed_by") or actor
        if _matches_governed_exact_decision(
            row, canonical_feature=row.get("canonical_feature"),
            representation_orientation=row.get("representation_orientation"),
            expected_direction=row.get("expected_direction"),
        ):
            kb_version = row["governed_exact_decision"]["kb_version"]
            row["kb_proposal"] = {
                "requested": False, "materialized": False, "suppressed": True,
                "lifecycle_state": "not_required",
                "proposal_action": "already_covered_by_governed_kb",
                "requested_by": proposal_actor,
                "reason": (
                    f"The unchanged decision is already covered by KB v{kb_version}; "
                    "no proposal was created."
                ),
            }
            continue
        proposal = knowledge.create_draft_proposal(
            run_id=manifest["run_id"], item_id=manifest["item_id"],
            table=manifest["table"], feature=row["feature"],
            canonical_feature=row.get("canonical_feature"),
            expected_direction=row["expected_direction"],
            representation_orientation=row.get("representation_orientation"),
            rationale=row["rationale"], actor=proposal_actor,
            tenant_id=manifest.get("tenant_id") or "bootstrap", conn=conn,
        )
        row["kb_proposal"] = {
            "requested": True, "materialized": True,
            "rule_id": proposal["rule_id"],
            "lifecycle_state": proposal["lifecycle_state"],
            "proposal_action": proposal.get("proposal_action") or "created",
            "requested_by": proposal_actor,
        }


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        return run["manifest_json"]
    with db.get_conn() as conn:
        # Serialize finalization before reading proposal state. Proposal
        # materialization and the DRAFT -> RUNNING transition then commit or
        # roll back together, so a failed freeze cannot leave a KB orphan.
        conn.execute("BEGIN IMMEDIATE")
        current = db.query_one("diag_runs", conn=conn, run_id=run_id)
        if not current:
            raise KeyError("Unknown directionality run")
        if current["status"] != DRAFT:
            return current["manifest_json"]
        manifest = dict(current["manifest_json"])
        _backfill_governed_exact_decisions(manifest)
        _refresh(manifest)
        if manifest["blockers"]:
            raise ManifestError(
                "scope is not ready: "
                + "; ".join(row["message"] for row in manifest["blockers"])
            )
        _materialize_knowledge_proposals(manifest, actor, conn=conn)
        inputs = {
            "diagnostic_id": DIAGNOSTIC_ID,
            "tenant_id": manifest.get("tenant_id") or "bootstrap",
            "item_id": manifest["item_id"], "table": manifest["table"],
            "reference": manifest["reference"],
            "segment_column": manifest["segment_column"],
            "features": [{key: row.get(key) for key in (
                "feature", "selected", "canonical_feature", "expected_direction",
                "representation_orientation", "classification_source", "rationale")}
                | {"kb_proposal_requested": bool(
                    (row.get("kb_proposal") or {}).get("requested"))}
                for row in manifest["features"]],
            "thresholds": manifest["thresholds"],
            "parameters": manifest["parameters"], "knowledge": manifest["knowledge"],
        }
        manifest["inference_disclosure"] = inference_disclosure(run_id)
        manifest.update({
            "manifest_fingerprint": stable_fingerprint(inputs), "status": "frozen",
            "frozen_at": db.now_ist(), "frozen_by": actor,
            "actor_decisions": list_decisions(run_id),
        })
        changed = db.update(
            "diag_runs", {"run_id": run_id, "status": DRAFT},
            {"manifest_json": manifest, "status": RUNNING,
             "started_at": manifest["frozen_at"]}, conn=conn,
        )
        if changed != 1:
            raise ManifestError("the directionality run changed while it was being frozen")
        conn.commit()
        return manifest


__all__ = ["DIAGNOSTIC_ID", "DRAFT", "ManifestError", "build_manifest",
           "discard_drafts", "freeze", "get_run", "latest_draft",
           "patch_manifest", "refresh_draft_scope"]
