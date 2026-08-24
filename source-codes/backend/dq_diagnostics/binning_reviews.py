"""Governed coarse-bin revisions for diagnostic #2 cached fine-bin evidence."""
from __future__ import annotations

import math
import uuid
from typing import Any

import system_db as db
from analysis_runtime.artifacts import AnalysisArtifactRepository

from .engines.feature_target_separation.information_value.binning import review_cached_fine_binning
from .engines.feature_target_separation.information_value.models import BinDefinition, BinRow


def _id() -> str:
    return f"brev_{uuid.uuid4().hex[:12]}"


def _result(result_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = db.query_one("diag_results", result_id=result_id)
    if not result or result.get("diagnostic_id") != 2:
        raise KeyError(f"Unknown diagnostic #2 feature result: {result_id}")
    metrics = result.get("metrics_json") or {}
    if metrics.get("result_kind") != "feature":
        raise ValueError("binning revisions require a feature result")
    run = db.query_one("diag_runs", run_id=result["run_id"])
    if not run:
        raise KeyError(f"Unknown run: {result['run_id']}")
    return result, metrics, run


def _latest(result_id: str, scope: str) -> dict[str, Any] | None:
    rows = db.query("diag_binning_revisions", result_id=result_id, scope=scope,
                    order_by="revision_no DESC")
    return rows[0] if rows else None


def _active_revision(result_id: str) -> dict[str, Any] | None:
    return (_latest(result_id, "diagnostic_local") or _latest(result_id, "local")
            or _latest(result_id, "universal"))


def _foundation_ids(result_id: str, metrics: dict[str, Any]) -> tuple[str | None, str | None]:
    revision = _active_revision(result_id)
    if revision:
        return revision.get("fine_artifact_id"), revision.get("iv_artifact_id")
    ids = metrics.get("artifact_ids") or {}
    return ids.get("fine_bins"), ids.get("iv")


def _iv_value(metrics: list[dict[str, Any]]) -> float | None:
    names = {"information_value", "maximum_one_vs_rest_iv"}
    return next((row.get("value") for row in metrics if row.get("name") in names), None)


def governance(result_id: str, original_iv: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "diagnostic_specific": {"iv": original_iv, "revision_no": 0, "source": "automatic"},
        "local": None,  # legacy API alias
        "universal": None,
    }
    for public_scope, stored_scopes in (("diagnostic_specific", ("diagnostic_local", "local")),
                                        ("universal", ("universal",))):
        row = next((_latest(result_id, scope) for scope in stored_scopes
                    if _latest(result_id, scope)), None)
        if row:
            out[public_scope] = {
                "revision_id": row["revision_id"], "revision_no": row["revision_no"],
                "iv": _iv_value(row.get("metrics_json") or []),
                "coarse_artifact_id": row["coarse_artifact_id"],
                "iv_artifact_id": row["iv_artifact_id"],
                "created_at": row["created_at"], "source": "reviewed",
            }
    out["local"] = out["diagnostic_specific"]  # legacy response alias
    return out


def hydrate_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics_json") or {}
    artifact_ids = metrics.get("artifact_ids") or {}
    repo = AnalysisArtifactRepository()
    enriched = dict(metrics)
    try:
        if artifact_ids.get("roc_feature"):
            _meta, roc_payload = repo.get(artifact_ids["roc_feature"])
            enriched["roc_detail"] = roc_payload.get("result", roc_payload)
        if artifact_ids.get("iv"):
            _meta, enriched["binning_detail"] = repo.get(artifact_ids["iv"])
    except (KeyError, OSError):
        pass
    applied = _active_revision(result["result_id"])
    if enriched.get("binning_detail"):
        enriched["binning_detail"]["automatic_coarse_definition"] = enriched["binning_detail"].get(
            "coarse_definition")
    if applied and enriched.get("binning_detail"):
        automatic = enriched["binning_detail"].get("automatic_coarse_definition")
        try:
            _applied_meta, applied_payload = repo.get(applied["iv_artifact_id"])
            enriched["binning_detail"] = {
                **applied_payload, "automatic_coarse_definition": automatic,
            }
        except (KeyError, OSError):
            enriched["binning_detail"] = {
                **enriched["binning_detail"],
                "coarse_definition": applied["definition_json"],
                "coarse_bins": applied["bins_json"],
                "coarse_metrics": applied["metrics_json"],
                "warnings": applied.get("warnings_json") or [],
            }
    enriched["binning_governance"] = governance(result["result_id"], metrics.get("iv"))
    return {**result, "metrics_json": enriched}


def preview(result_id: str, definition: dict[str, Any]) -> dict[str, Any]:
    """Recalculate a draft from immutable fine bins without writing any revision."""
    _result_row, metrics, _run = _result(result_id)
    fine_id, iv_id = _foundation_ids(result_id, metrics)
    if not fine_id or not iv_id:
        raise ValueError("cached fine-bin and IV artifacts are required")
    repo = AnalysisArtifactRepository()
    _fine_meta, fine_payload = repo.get(fine_id)
    _iv_meta, iv_payload = repo.get(iv_id)
    reviewed = review_cached_fine_binning(
        BinDefinition(**fine_payload["definition"]),
        [BinRow(**row) for row in fine_payload["bins"]],
        BinDefinition(**definition),
        iv_payload["target_type"],
    )
    return reviewed.model_dump(mode="json")


def impact(result_id: str) -> dict[str, Any]:
    _result(result_id)
    repo = AnalysisArtifactRepository()
    latest = _latest(result_id, "universal")
    if latest:
        artifact_id = latest["coarse_artifact_id"]
    else:
        artifact_id = (_result(result_id)[1].get("artifact_ids") or {}).get("coarse_bins")
    dependencies = repo.impact(artifact_id) if artifact_id else []
    return {
        "artifact_id": artifact_id,
        "dependent_count": len(dependencies),
        "affected_workflow_ids": sorted({row.workflow_id for row in dependencies if row.workflow_id}),
    }


def review(result_id: str, definition: dict[str, Any], scope: str,
           *, confirm_universal: bool = False, actor: str = "system") -> dict[str, Any]:
    requested_scope = scope
    if scope == "diagnostic_specific":
        scope = "diagnostic_local"
    if scope not in {"diagnostic_local", "local", "universal"}:
        raise ValueError("scope must be diagnostic_specific or universal")
    result, metrics, run = _result(result_id)
    if scope == "universal" and not confirm_universal:
        error = RuntimeError("Universal coarse-bin replacement requires explicit impact confirmation.")
        error.impact = impact(result_id)  # type: ignore[attr-defined]
        raise error

    repo = AnalysisArtifactRepository()
    confirmed_impact = impact(result_id) if scope == "universal" else None
    fine_id, iv_id = _foundation_ids(result_id, metrics)
    if not fine_id or not iv_id:
        raise ValueError("cached fine-bin and IV artifacts are required")
    fine_meta, fine_payload = repo.get(fine_id)
    _iv_meta, iv_payload = repo.get(iv_id)
    reviewed = review_cached_fine_binning(
        BinDefinition(**fine_payload["definition"]),
        [BinRow(**row) for row in fine_payload["bins"]],
        BinDefinition(**definition),
        iv_payload["target_type"],
    )
    reviewed_payload = reviewed.model_dump(mode="json")
    workflow_id = None
    previous = _latest(result_id, scope)
    revision_no = (previous["revision_no"] + 1) if previous else 1
    base = {
        "asset_id": fine_meta.asset_id, "snapshot_id": fine_meta.snapshot_id,
        "population_fingerprint": fine_meta.population_fingerprint,
        "target_fingerprint": fine_meta.target_fingerprint, "feature": fine_meta.feature,
        "methodology_fingerprint": fine_meta.methodology_fingerprint,
        "table": fine_meta.identity.get("table"),
        "scope": scope, "workflow_id": workflow_id,
        "owner_id": "diagnostic:2" if scope != "universal" else None,
        "run_id": run["run_id"],
        "created_by": actor,
    }
    governed_fine_id = fine_id
    if scope == "universal":
        governed_fine = repo.save_version({
            "definition": fine_payload["definition"], "bins": fine_payload["bins"],
            "metrics": fine_payload.get("metrics") or [],
        }, artifact_type="fine_bins", source_artifact_ids=fine_meta.source_artifact_ids,
           **base)
        governed_fine_id = governed_fine.artifact_id
    coarse = repo.save_version({
        "definition": reviewed_payload["definition"], "bins": reviewed_payload["bins"],
        "metrics": reviewed_payload["metrics"], "warnings": reviewed_payload["warnings"],
    }, artifact_type="coarse_bins", source_artifact_ids=(governed_fine_id,), **base)
    revised_iv = {
        **iv_payload, "coarse_definition": reviewed_payload["definition"],
        "coarse_bins": reviewed_payload["bins"], "coarse_metrics": reviewed_payload["metrics"],
        "warnings": reviewed_payload["warnings"],
        "coarse_groups": reviewed_payload["coarse_groups"],
    }
    iv = repo.save_version(revised_iv, artifact_type="iv",
                           source_artifact_ids=(governed_fine_id, coarse.artifact_id), **base)
    if previous:
        repo.supersede(previous["coarse_artifact_id"], by_artifact_id=coarse.artifact_id)
        repo.supersede(previous["iv_artifact_id"], by_artifact_id=iv.artifact_id)
    elif scope == "universal":
        # The automatic Diagnostic 2 result remains an immutable
        # diagnostic-specific version.  Promotion creates a separate universal
        # fine/coarse/IV chain instead of relabelling or superseding it.
        pass
    revision_id = _id()
    db.insert("diag_binning_revisions", {
        "revision_id": revision_id, "result_id": result_id, "run_id": run["run_id"],
        "item_id": run["item_id"], "feature": metrics["feature"], "scope": scope,
        "revision_no": revision_no, "fine_artifact_id": governed_fine_id,
        "coarse_artifact_id": coarse.artifact_id, "iv_artifact_id": iv.artifact_id,
        "supersedes_revision_id": previous["revision_id"] if previous else None,
        "definition_json": reviewed_payload["definition"], "bins_json": reviewed_payload["bins"],
        "metrics_json": reviewed_payload["metrics"], "warnings_json": reviewed_payload["warnings"],
        "governance_json": {"confirmed_universal": confirm_universal,
                            "impact": confirmed_impact},
        "actor": actor, "created_at": db.now_ist(),
    })
    public_scope = "local" if requested_scope == "local" else (
        "diagnostic_specific" if scope == "diagnostic_local" else scope)
    return {**reviewed_payload, "revision_id": revision_id, "scope": public_scope,
            "revision_no": revision_no,
            "artifact_ids": {"fine_bins": governed_fine_id, "coarse_bins": coarse.artifact_id,
                             "iv": iv.artifact_id},
            "governance": governance(result_id, metrics.get("iv"))}


def _numeric_csv(value: str, field: str, *, allow_empty: bool = False) -> list[float]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return []
    if not text:
        raise ValueError(f"{field} are required")
    values: list[float] = []
    for position, token in enumerate(text.split(","), start=1):
        candidate = token.strip()
        if not candidate:
            raise ValueError(
                f"{field}: value {position} is blank; enter comma-separated numeric values only"
            )
        try:
            number = float(candidate)
        except ValueError as exc:
            raise ValueError(
                f"{field}: value {position} ({candidate!r}) is not numeric; "
                "NA, Null and Infinity are not allowed"
            ) from exc
        if not math.isfinite(number):
            raise ValueError(
                f"{field}: value {position} ({candidate!r}) must be a finite numeric value"
            )
        values.append(number)
    return values


def create_numeric_override(result_id: str, cuts: str, special_values: str = "",
                            rationale: str = "", scope: str = "diagnostic_specific",
                            *, confirm_universal: bool = False,
                            actor: str = "system") -> dict[str, Any]:
    """Create a new numeric fine foundation and optimize coarse bins over it."""
    requested_scope = scope
    if scope == "diagnostic_specific":
        scope = "diagnostic_local"
    if scope not in {"diagnostic_local", "local", "universal"}:
        raise ValueError("scope must be diagnostic_specific or universal")
    if scope == "universal" and not confirm_universal:
        error = RuntimeError("Universal numeric-bin replacement requires explicit impact confirmation.")
        error.impact = impact(result_id)  # type: ignore[attr-defined]
        raise error

    result_row, metrics, run = _result(result_id)
    repo = AnalysisArtifactRepository()
    fine_id, iv_id = _foundation_ids(result_id, metrics)
    if not fine_id or not iv_id:
        raise ValueError("fine-bin and IV artifacts are required")
    fine_meta, _fine_payload = repo.get(fine_id)
    _iv_meta, iv_payload = repo.get(iv_id)
    if iv_payload.get("feature_type") != "numeric":
        raise ValueError("numeric overrides are available only for numeric features")

    boundaries = _numeric_csv(cuts, "Numeric cuts")
    if boundaries != sorted(set(boundaries)):
        raise ValueError("Numeric cuts must be unique and strictly increasing")
    additions = _numeric_csv(special_values, "Special values", allow_empty=True)

    manifest = run.get("manifest_json") or {}
    feature = metrics.get("feature") or fine_meta.feature
    target = (manifest.get("target") or {}).get("column") or iv_payload.get("target")
    table = (manifest.get("scope") or {}).get("table") or fine_meta.identity.get("table")
    if not feature or not target or not table:
        raise ValueError("the governed table, feature and target are required")

    inventory = next((row for row in db.query(
        "variable_inventory", item_id=run["item_id"],
    ) if row.get("table_name") == table and row.get("column_name") == feature), {})
    sourced = (list(inventory.get("missing_value_codes_json") or [])
               if inventory.get("missing_codes_confirmed") else [])
    combined: list[Any] = []
    for value in [*sourced, *additions]:
        if str(value) not in {str(existing) for existing in combined}:
            combined.append(value)
    specials = {f"Special value {value}": [value] for value in combined}

    from analysis_runtime.snapshots import SnapshotLoader
    from .engines.feature_target_separation.information_value import BinningConstraints
    from .engines.feature_target_separation.information_value.binning import fit_feature_binning

    frame = SnapshotLoader().load_table(run["item_id"], table, [feature, target])
    frame = frame.dropna(subset=[target]).reset_index(drop=True)
    if frame.empty:
        raise ValueError("no rows remain after excluding missing target values")
    fitted = fit_feature_binning(
        frame[feature], frame[target], feature_name=feature, target_name=target,
        target_type=iv_payload["target_type"], feature_type="numeric",
        positive_class=(manifest.get("target") or {}).get("positive_class"),
        special_values=specials,
        constraints=BinningConstraints(**((manifest.get("parameters") or {}).get(
            "binning_constraints") or {})),
        numeric_fine_splits=boundaries,
    )
    payload = fitted.model_dump(mode="json")
    payload["target_route"] = iv_payload.get("target_route")
    payload["numeric_override"] = {
        "cuts": boundaries, "sourced_special_values": sourced,
        "added_special_values": additions, "rationale": rationale.strip(),
        "evaluated_population_read": "feature_and_governed_target",
        "fine_foundation": "operator_numeric_cuts",
        "coarse_method": "diagnostic_2_optimizer_over_override_fine_foundation",
    }

    confirmed_impact = impact(result_id) if scope == "universal" else None
    base = {
        "asset_id": fine_meta.asset_id, "snapshot_id": fine_meta.snapshot_id,
        "population_fingerprint": fine_meta.population_fingerprint,
        "target_fingerprint": fine_meta.target_fingerprint, "feature": feature,
        "methodology_fingerprint": fine_meta.methodology_fingerprint,
        "table": table, "scope": scope, "workflow_id": None,
        "owner_id": "diagnostic:2" if scope != "universal" else None,
        "run_id": run["run_id"], "created_by": actor,
    }
    fine = repo.save_version({
        "definition": payload["fine_definition"], "bins": payload["fine_bins"],
        "metrics": payload.get("fine_metrics") or [],
        "target_route": payload.get("target_route"),
        "numeric_override": payload["numeric_override"],
    }, artifact_type="fine_bins", source_artifact_ids=fine_meta.source_artifact_ids, **base)
    coarse = repo.save_version({
        "definition": payload["coarse_definition"], "bins": payload["coarse_bins"],
        "metrics": payload.get("coarse_metrics") or [], "warnings": payload.get("warnings") or [],
        "target_route": payload.get("target_route"),
        "numeric_override": payload["numeric_override"],
    }, artifact_type="coarse_bins", source_artifact_ids=(fine.artifact_id,), **base)
    iv = repo.save_version(payload, artifact_type="iv",
                           source_artifact_ids=(fine.artifact_id, coarse.artifact_id), **base)

    previous = _latest(result_id, scope)
    if previous:
        for key in ("fine_artifact_id", "coarse_artifact_id", "iv_artifact_id"):
            prior_id = previous.get(key)
            replacement = {"fine_artifact_id": fine.artifact_id,
                           "coarse_artifact_id": coarse.artifact_id,
                           "iv_artifact_id": iv.artifact_id}[key]
            if prior_id and prior_id != replacement:
                repo.supersede(prior_id, by_artifact_id=replacement)
    revision_no = (previous["revision_no"] + 1) if previous else 1
    revision_id = _id()
    db.insert("diag_binning_revisions", {
        "revision_id": revision_id, "result_id": result_id, "run_id": run["run_id"],
        "item_id": run["item_id"], "feature": feature, "scope": scope,
        "revision_no": revision_no, "fine_artifact_id": fine.artifact_id,
        "coarse_artifact_id": coarse.artifact_id, "iv_artifact_id": iv.artifact_id,
        "supersedes_revision_id": previous["revision_id"] if previous else None,
        "definition_json": payload["coarse_definition"], "bins_json": payload["coarse_bins"],
        "metrics_json": payload.get("coarse_metrics") or [],
        "warnings_json": payload.get("warnings") or [],
        "governance_json": {"confirmed_universal": confirm_universal,
                            "impact": confirmed_impact,
                            "numeric_override": payload["numeric_override"]},
        "actor": actor, "created_at": db.now_ist(),
    })
    public_scope = "local" if requested_scope == "local" else (
        "diagnostic_specific" if scope == "diagnostic_local" else scope)
    return {
        **payload, "revision_id": revision_id, "revision_no": revision_no,
        "scope": public_scope,
        "artifact_ids": {"fine_bins": fine.artifact_id,
                         "coarse_bins": coarse.artifact_id, "iv": iv.artifact_id},
        "governance": governance(result_id, metrics.get("iv")),
    }
