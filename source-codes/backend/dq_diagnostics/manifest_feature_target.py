"""Frozen scope contract for diagnostic #2, Single-feature target separation."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import system_db as db
from analysis_runtime.artifacts import AnalysisArtifactRepository

from .engines.feature_target_separation.information_value import BinningConstraints
from .engines.feature_target_separation.models import SeparationThresholds
from .engines.feature_target_separation.roc_gini import AnalysisConstraints
from .engines.feature_target_separation.roles import (
    EXCLUDED_ROLES,
    effective_role,
    is_eligible_feature,
    is_recommended_feature,
    recommendation_reason,
)
from .manifest import DRAFT, RUNNING, ManifestError, record_decision
from .readiness import readiness
from .register import require_executable
from .thresholds import effective_threshold

DIAGNOSTIC_ID = 2
MANIFEST_VERSION = 1
ENGINE_VERSION = "0.1.0"
FINDING_DEFAULTS = {
    "leakage_auc": 0.90,
    "leakage_iv": 0.50,
    "poor_auc": 0.60,
    "poor_iv": 0.05,
}
SEPARATION_KEYS = tuple(SeparationThresholds.model_fields)
THRESHOLD_KEYS = (*SEPARATION_KEYS, *FINDING_DEFAULTS)


def _id() -> str:
    return f"drun_{uuid.uuid4().hex[:12]}"


def _scope_rows(item_id: str) -> tuple[str, str, list[dict[str, Any]]]:
    item = db.query_one("dq_items", item_id=item_id)
    if item is None:
        raise KeyError(f"Unknown item: {item_id}")
    target = str(item.get("target_variable") or "").strip()
    if not target:
        raise ManifestError("target variable is not selected; confirm it in Data Sourcing first")
    inventory = db.query("variable_inventory", item_id=item_id)
    target_rows = [row for row in inventory if row.get("column_name") == target]
    tables = sorted({row.get("table_name") for row in target_rows if row.get("table_name")})
    if len(tables) != 1:
        raise ManifestError("the confirmed target must resolve to exactly one profiled table")
    table = tables[0]
    scope_rows = [
        row for row in inventory
        if row.get("table_name") == table and row.get("column_name") != target
    ]
    # The retained column-profile artifacts are the golden schema projection
    # for diagnostics. They are refreshed after Step 3 saves; prefer their
    # latest active role over any stale compatibility inventory value.
    try:
        from analysis_runtime.data_sourcing_artifacts import persist_snapshot_profile_artifacts
        persist_snapshot_profile_artifacts(item_id, actor="system")
    except Exception:
        pass
    repo = AnalysisArtifactRepository()
    artifact_roles: dict[str, str] = {}
    for artifact in repo.list(snapshot_id=item_id, artifact_type="column_profile", status="active"):
        if (artifact.identity.get("table") != table or not artifact.feature
                or artifact.feature in artifact_roles):
            continue
        _metadata, payload = repo.get(artifact.artifact_id)
        role = str((payload or {}).get("role") or "").strip()
        if role:
            artifact_roles[artifact.feature] = role
    scope_rows = [
        {**row, "role": artifact_roles.get(row["column_name"], row.get("role")),
         "_role_source": "analytics_artifact_repository" if row["column_name"] in artifact_roles else None}
        for row in scope_rows
    ]
    eligible = [row for row in scope_rows if is_eligible_feature(row)]
    if not eligible:
        raise ManifestError("no independent variables remain after excluding the confirmed target")
    return table, target, sorted(scope_rows, key=lambda row: row["column_name"])


def _scope_contract(table: str, scope_rows: list[dict[str, Any]],
                    selected: list[str] | None = None) -> dict[str, Any]:
    eligible = [row for row in scope_rows if is_eligible_feature(row)]
    recommended = [row for row in eligible if is_recommended_feature(row)] or eligible
    eligible_names = [row["column_name"] for row in eligible]
    recommended_names = [row["column_name"] for row in recommended]
    selected_names = recommended_names if selected is None else [
        name for name in selected if name in set(eligible_names)
    ]
    if not selected_names:
        selected_names = recommended_names
    return {
        "table": table,
        "schema_source": "data_sourcing.variable_inventory",
        "eligible_features": eligible_names,
        "recommended_features": recommended_names,
        "selected_features": selected_names,
        # Kept for older clients, but these roles are recommendation signals
        # only. They are not excluded from user selection.
        "excluded_roles": [],
        "non_recommended_roles": sorted(EXCLUDED_ROLES),
        "feature_metadata": [{
            "column": row["column_name"],
            "role": effective_role(row).title(),
            "role_source": (
                "analytics_artifact_repository" if row.get("_role_source") else
                "saved_schema" if row.get("role") else
                "dictionary" if row.get("dictionary_role") else
                "inferred_missing_role"
            ),
            "classification": row.get("classification"),
            "data_type": row.get("data_type"),
            "eligible": is_eligible_feature(row),
            "recommended": is_recommended_feature(row),
            "recommendation_reason": recommendation_reason(row),
        } for row in scope_rows],
    }


def analysis_fingerprint(manifest: dict[str, Any], feature: str) -> str:
    """Stable identity for one feature assessment on one immutable snapshot."""
    payload = {
        "item_id": manifest["item_id"],
        "diagnostic_id": DIAGNOSTIC_ID,
        "engine_version": manifest["engine_version"],
        "table": manifest["scope"]["table"],
        "feature": feature,
        "target": {key: manifest["target"].get(key) for key in (
            "column", "target_type", "positive_class", "missing_target_action",
        )},
        "thresholds": {key: spec["value"] for key, spec in manifest["thresholds"].items()},
        "parameters": manifest["parameters"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _completed_feature_results(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    wanted = {
        analysis_fingerprint(manifest, feature): feature
        for feature in manifest["scope"]["eligible_features"]
    }
    matches: dict[str, dict[str, str]] = {}
    runs = db.query("diag_runs", item_id=manifest["item_id"], diagnostic_id=DIAGNOSTIC_ID,
                    status="done", order_by="finished_at DESC")
    for run in runs:
        prior = run.get("manifest_json") or {}
        if not prior.get("scope"):
            continue
        for result in db.query("diag_results", run_id=run["run_id"]):
            metrics = result.get("metrics_json") or {}
            if metrics.get("result_kind") != "feature":
                continue
            feature = metrics.get("feature")
            fingerprint = metrics.get("analysis_fingerprint")
            if not fingerprint and feature:
                try:
                    fingerprint = analysis_fingerprint(prior, feature)
                except (KeyError, TypeError):
                    continue
            if fingerprint in wanted and wanted[fingerprint] not in matches:
                matches[wanted[fingerprint]] = {
                    "run_id": run["run_id"], "result_id": result["result_id"],
                    "analysis_fingerprint": fingerprint,
                }
    return matches


def refresh_completion_state(manifest: dict[str, Any]) -> dict[str, Any]:
    completed = _completed_feature_results(manifest)
    manifest["execution"] = {
        "completed_exact": completed,
        "runnable_features": [name for name in manifest["scope"]["selected_features"]
                              if name not in completed],
    }
    for row in manifest["scope"]["feature_metadata"]:
        row["completed_exact"] = completed.get(row["column"])
    return manifest


def build_manifest(item_id: str, actor: str = "system",
                   *, enforce_register: bool = True) -> dict[str, Any]:
    """Create the draft #2 manifest. Tests may build behind the pending gate."""
    item = db.query_one("dq_items", item_id=item_id)
    if item is not None and item.get("snapshot_status") == "superseded":
        raise ManifestError("a superseded snapshot cannot be used to build a diagnostic manifest")
    if enforce_register:
        register_row = require_executable(DIAGNOSTIC_ID)
        state = readiness(item_id, DIAGNOSTIC_ID)
        if state.status != "ready":
            raise ManifestError(f"{state.status}: {state.reason}")
    else:
        from .register import get_diagnostic
        register_row = get_diagnostic(DIAGNOSTIC_ID)

    table, target, scope_rows = _scope_rows(item_id)
    run_id = _id()
    now = db.now_ist()
    thresholds = {key: effective_threshold(DIAGNOSTIC_ID, key) for key in THRESHOLD_KEYS}
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_kind": "feature_target_separation",
        "run_id": run_id,
        "item_id": item_id,
        "item_name": item.get("name"),
        "diagnostic_id": DIAGNOSTIC_ID,
        "diagnostic": {key: register_row.get(key) for key in (
            "name", "area", "mode", "stage", "det_stat", "decision_type",
            "kb_dependency", "metric", "workflow_status",
        )},
        "engine_version": ENGINE_VERSION,
        "status": DRAFT,
        "created_at": now,
        "created_by": actor,
        "target": {
            "table": table,
            "column": target,
            "target_type": "auto",
            "positive_class": None,
            "missing_target_action": "drop",
            "source": "confirmed in Data Sourcing",
        },
        "scope": _scope_contract(table, scope_rows),
        "thresholds": thresholds,
        "parameters": {
            "percentile_bins": 10,
            "analysis_constraints": AnalysisConstraints().model_dump(mode="json"),
            "binning_constraints": BinningConstraints().model_dump(mode="json"),
        },
    }
    refresh_completion_state(manifest)
    db.insert("diag_runs", {
        "run_id": run_id, "item_id": item_id, "diagnostic_id": DIAGNOSTIC_ID,
        "manifest_json": manifest, "status": DRAFT,
        "engine_versions_json": {"feature_target_separation": ENGINE_VERSION},
        "created_at": now, "started_at": None, "finished_at": None,
    })
    record_decision(run_id, "default_applied", {
        "fields": {**{key: "default" for key in manifest["thresholds"]},
                   "target_type": "auto", "missing_target_action": "drop"},
    }, actor)
    return manifest


def _run(run_id: str) -> dict[str, Any]:
    run = db.query_one("diag_runs", run_id=run_id)
    if run is None:
        raise KeyError(f"Unknown run: {run_id}")
    if run.get("diagnostic_id") != DIAGNOSTIC_ID:
        raise ManifestError(f"run {run_id} does not belong to diagnostic #2")
    return run


def refresh_draft_scope(run_id: str, actor: str = "system") -> dict[str, Any]:
    """Synchronize an open draft with the latest normalized sourcing roles."""
    run = _run(run_id)
    manifest = run["manifest_json"]
    if run["status"] != DRAFT:
        return manifest
    table, _target, scope_rows = _scope_rows(run["item_id"])
    before = manifest["scope"]
    refreshed = _scope_contract(table, scope_rows, before.get("selected_features") or [])
    if refreshed == before:
        return refresh_completion_state(manifest)
    manifest["scope"] = refreshed
    record_decision(run_id, "scope_exclusion", {
        "event": "role_recommendation_refresh",
        "before_selected": before.get("selected_features") or [],
        "after_selected": refreshed["selected_features"],
        "removed_unavailable": sorted(
            set(before.get("selected_features") or []) - set(refreshed["selected_features"])
        ),
    }, actor)
    refresh_completion_state(manifest)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"manifest {run_id} is frozen (status {run['status']}) and cannot be edited")
    manifest = run["manifest_json"]
    kind = patch.get("kind")
    now = db.now_ist()

    if kind == "feature_selection":
        selected = list(dict.fromkeys(patch.get("features") or []))
        eligible = set(manifest["scope"]["eligible_features"])
        unknown = sorted(set(selected) - eligible)
        if unknown:
            raise ManifestError(f"selected columns are not available independent variables: {unknown}")
        if not selected:
            raise ManifestError("select at least one independent variable")
        before = list(manifest["scope"]["selected_features"])
        manifest["scope"]["selected_features"] = selected
        record_decision(run_id, "scope_exclusion", {
            "event": "feature_selection", "before": before, "after": selected,
            "excluded": sorted(eligible - set(selected)),
        }, actor)
    elif kind == "threshold_tune":
        key = patch.get("key")
        if key not in manifest["thresholds"]:
            raise ManifestError(f"unknown threshold key: {key!r}")
        before = dict(manifest["thresholds"][key])
        manifest["thresholds"][key] = {
            "value": patch.get("value"), "source": f"user-set ({actor}, {now})",
        }
        _validate(manifest)
        record_decision(run_id, "threshold_tune", {
            "key": key, "before": before, "after": manifest["thresholds"][key],
        }, actor)
    elif kind == "parameter_tune":
        key = patch.get("key")
        value = patch.get("value")
        before = None
        if key in {"target_type", "positive_class", "missing_target_action"}:
            before = manifest["target"].get(key)
            manifest["target"][key] = value
        elif key == "percentile_bins":
            before = manifest["parameters"][key]
            manifest["parameters"][key] = value
        elif key in {"analysis_constraints", "binning_constraints"}:
            if not isinstance(value, dict):
                raise ManifestError(f"{key} must be an object")
            before = dict(manifest["parameters"][key])
            manifest["parameters"][key] = {**before, **value}
        else:
            raise ManifestError(f"unknown parameter key: {key!r}")
        _validate(manifest)
        record_decision(run_id, "threshold_tune", {
            "event": "parameter_tune", "key": key, "before": before, "after": value,
        }, actor)
    else:
        raise ManifestError("kind must be feature_selection, threshold_tune, or parameter_tune")
    refresh_completion_state(manifest)
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def _validate(manifest: dict[str, Any]) -> None:
    values = {key: spec["value"] for key, spec in manifest["thresholds"].items()}
    SeparationThresholds(**{key: values[key] for key in SeparationThresholds.model_fields})
    for key in FINDING_DEFAULTS:
        if not isinstance(values[key], (int, float)):
            raise ManifestError(f"{key} must be numeric")
    if not 0.5 <= float(values["poor_auc"]) <= float(values["leakage_auc"]) <= 1.0:
        raise ManifestError("AUC finding thresholds must satisfy 0.5 <= poor <= leakage <= 1.0")
    if not 0 <= float(values["poor_iv"]) <= float(values["leakage_iv"]):
        raise ManifestError("IV finding thresholds must satisfy 0 <= poor <= leakage")
    target = manifest["target"]
    if target["target_type"] not in {"auto", "binary", "continuous", "multinomial"}:
        raise ManifestError("unsupported target_type")
    if target["missing_target_action"] not in {"prompt", "drop"}:
        raise ManifestError("missing_target_action must be prompt or drop")
    AnalysisConstraints(**manifest["parameters"]["analysis_constraints"])
    BinningConstraints(**manifest["parameters"]["binning_constraints"])
    bins = manifest["parameters"]["percentile_bins"]
    if not isinstance(bins, int) or not 2 <= bins <= 100:
        raise ManifestError("percentile_bins must be an integer between 2 and 100")


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    run = _run(run_id)
    if run["status"] != DRAFT:
        raise ManifestError(f"run {run_id} is already {run['status']}")
    manifest = run["manifest_json"]
    _validate(manifest)
    if not manifest["scope"]["selected_features"]:
        raise ManifestError("select at least one independent variable")
    refresh_completion_state(manifest)
    runnable = manifest["execution"]["runnable_features"]
    if not runnable:
        raise ManifestError(
            "Every selected feature already has a completed result for this snapshot and these analysis settings."
        )
    manifest["scope"]["execution_features"] = runnable
    now = db.now_ist()
    manifest.update({"status": RUNNING, "frozen_at": now, "frozen_by": actor})
    db.update("diag_runs", {"run_id": run_id}, {
        "manifest_json": manifest, "status": RUNNING, "started_at": now,
    })
    return manifest
