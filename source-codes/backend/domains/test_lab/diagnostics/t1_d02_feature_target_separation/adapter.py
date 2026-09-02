"""DataWorkbench adapter for diagnostic #2's feature-target separation engine.

It binds the ported deterministic engines to DataWorkbench snapshots,
inventory roles, and immutable analysis artifacts. The registered workflow
wraps this adapter with a frozen manifest, persisted candidate findings, and
an explicit Issue Management hand-off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import uuid

import system_db as db
from domains.aar.repository import AnalysisArtifactRepository
from analysis_runtime.contracts import AnalysisScope, FrozenAnalysisManifest
from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
from analysis_runtime.snapshots import SnapshotLoader
from analysis_runtime.targets import resolve_target_route

from domains.test_lab.shared.binning import BinningConstraints, BinningFeatureSpec
from .orchestration import assess_feature_target_separation
from .roc_gini import AnalysisConstraints, FeatureSpec, TargetSpec
from .models import FeatureTargetSeparationResponse, SeparationThresholds
from .roles import EXCLUDED_ROLES, is_eligible_feature, is_recommended_feature

DIAGNOSTIC_ID = 2
CAPABILITY_ID = "diagnostic_2_feature_target_separation"
CAPABILITY_VERSION = "0.1.0"

FINDING_THRESHOLD_DEFAULTS = {
    "leakage_auc": 0.90,
    "leakage_iv": 0.50,
    "poor_auc": 0.60,
    "poor_iv": 0.05,
}

@dataclass(frozen=True)
class FeatureTargetSeparationOutcome:
    response: FeatureTargetSeparationResponse
    artifact_ids: dict[str, list[str]]
    candidate_findings: tuple[dict[str, Any], ...]
    manifest: FrozenAnalysisManifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response.model_dump(mode="json"),
            "artifact_ids": self.artifact_ids,
            "candidate_findings": list(self.candidate_findings),
            "manifest": self.manifest.to_dict(),
        }


def assess_snapshot(
    snapshot_id: str,
    *,
    table: str | None = None,
    feature_columns: list[str] | None = None,
    target_type: str = "auto",
    positive_class: str | int | float | bool | None = None,
    missing_target_action: str = "prompt",
    percentile_bins: int = 10,
    analysis_constraints: dict[str, Any] | None = None,
    binning_constraints: dict[str, Any] | None = None,
    separation_thresholds: dict[str, Any] | None = None,
    finding_thresholds: dict[str, float] | None = None,
    actor: str = "system",
    run_id: str | None = None,
    artifact_repository: AnalysisArtifactRepository | None = None,
    progress_callback: Callable[[int, int, str, str], None] | None = None,
    feature_preview_callback: Callable[[dict[str, Any]], None] | None = None,
) -> FeatureTargetSeparationOutcome:
    """Run #2's domain engine against one ready immutable snapshot.

    Data Sourcing roles provide diagnostic-local defaults but do not prevent an
    explicit feature override. The function returns candidate findings but does
    not create issues itself; the diagnostic runner persists review candidates
    and only an explicit disposition can create an Issue Management record.
    """
    item = db.query_one("dq_items", item_id=snapshot_id)
    if item is None:
        raise KeyError(f"Unknown snapshot: {snapshot_id}")
    target = (item.get("target_variable") or "").strip()
    if not target:
        raise ValueError("target variable is not selected; confirm it in Data Sourcing first")

    rows = db.query("variable_inventory", item_id=snapshot_id)
    if not rows:
        raise ValueError("variable inventory is empty; complete Data Sourcing first")

    target_rows = [row for row in rows if row.get("column_name") == target]
    if table:
        target_rows = [row for row in target_rows if row.get("table_name") == table]
    if not target_rows:
        raise ValueError(f"target variable {target!r} is not present in the selected table inventory")
    if len({row.get("table_name") for row in target_rows}) > 1:
        raise ValueError("target variable appears in multiple tables; select a table explicitly")
    table_name = table or target_rows[0]["table_name"]

    eligible = _eligible_inventory(rows, table_name, target, feature_columns)
    if not eligible:
        raise ValueError("Select at least one eligible independent variable.")
    features = [row["column_name"] for row in eligible]
    selected_columns = [target, *features]

    loader = SnapshotLoader()
    snapshot_ref = loader.reference(snapshot_id)
    frame = loader.load_table(snapshot_id, table_name, selected_columns)
    effective_target_type, effective_positive_class = resolve_target_route(
        frame[target], target_type, positive_class,
    )
    methodology = {
        "parameters": {
            "target_type": target_type,
            "positive_class": positive_class,
            "effective_target_type": effective_target_type,
            "effective_positive_class": effective_positive_class,
            "missing_target_action": missing_target_action,
            "percentile_bins": percentile_bins,
            "analysis_constraints": analysis_constraints or {},
            "binning_constraints": binning_constraints or {},
            "separation_thresholds": separation_thresholds or {},
            "finding_thresholds": finding_thresholds or FINDING_THRESHOLD_DEFAULTS,
        },
        "eligible_feature_policy": {
            "recommended_roles": ["Feature", "Score"],
            "non_recommended_roles": sorted(EXCLUDED_ROLES),
            "role_override_allowed": True,
        },
    }
    repo = artifact_repository or AnalysisArtifactRepository()
    execution_identity = run_id or f"adhoc_{uuid.uuid4().hex[:12]}"
    # Materialize Data Sourcing evidence before resolving diagnostic inputs so
    # downstream evidence starts from the governed repository when available.
    persist_snapshot_profile_artifacts(
        snapshot_id, actor=actor, artifact_repository=repo,
    )
    profile_artifacts = {
        (row.identity.get("table"), row.feature): row.artifact_id
        for row in repo.list(
            snapshot_id=snapshot_id, artifact_type="column_profile",
            status="active",
        )
    }
    manifest_source_ids = tuple(sorted(filter(None, (
        profile_artifacts.get((table_name, column))
        for column in selected_columns
    ))))
    manifest = FrozenAnalysisManifest(
        capability_id=CAPABILITY_ID,
        capability_version=CAPABILITY_VERSION,
        snapshot=snapshot_ref,
        scope=AnalysisScope(
            table_name, tuple(features), {}, target,
            effective_target_type, effective_positive_class,
        ),
        methodology=methodology,
        source_artifact_ids=manifest_source_ids,
    )
    artifact_ids: dict[str, list[str]] = {
        "roc_feature": [],
        "fine_bins": [],
        "coarse_bins": [],
        "iv": [],
    }
    feature_profiles = {_row_key(row): _feature_profile(row) for row in eligible}

    def artifact_base(feature: str) -> dict[str, Any]:
        return {
            "asset_id": snapshot_ref.asset_id,
            "snapshot_id": snapshot_ref.snapshot_id,
            "population_fingerprint": manifest.scope.population_fingerprint,
            "target_fingerprint": manifest.scope.target_fingerprint,
            "feature": feature,
            "methodology_fingerprint": manifest.methodology_fingerprint,
            "scope": "diagnostic_local",
            "owner_id": "diagnostic:2",
            "workflow_id": None,
            "run_id": run_id,
            "identity_inputs": {"diagnostic_execution": execution_identity},
            "created_by": actor,
        }

    def profile_source_ids(feature: str) -> tuple[str, ...]:
        return tuple(sorted(filter(None, (
            profile_artifacts.get((table_name, target)),
            profile_artifacts.get((table_name, feature)),
        ))))

    cached_roc = _cached_roc(
        repo, manifest, snapshot_ref.asset_id, snapshot_ref.snapshot_id,
        features, profile_source_ids,
    )
    cached_binning = _cached_binning(repo, manifest, snapshot_ref.asset_id,
                                     snapshot_ref.snapshot_id, features,
                                     profile_source_ids,
                                     allow_promoted=(BinningConstraints(**(binning_constraints or {})).model_dump(mode="json")
                                                     == BinningConstraints().model_dump(mode="json")))

    def publish_roc_preview(feature: str, payload: dict[str, Any]) -> None:
        if not feature_preview_callback:
            return
        result = payload.get("result") or {}
        feature_preview_callback({
            "feature": feature,
            "stage": "roc",
            "status": "roc_complete",
            "feature_type": result.get("feature_type"),
            "auc": result.get("primary_auc"),
            "gini": result.get("primary_gini"),
            "iv": None,
            "category": None,
            "fine_bin_count": None,
            "coarse_bin_count": None,
            "warnings": [],
        })

    for cached_feature, cached_payload in cached_roc.items():
        publish_roc_preview(cached_feature, cached_payload)

    def save_roc(_target: TargetSpec, feature: FeatureSpec, payload: dict[str, Any]) -> None:
        metadata, _reused = repo.save_or_reuse(
            payload, artifact_type="roc_feature", **artifact_base(feature.column),
            source_artifact_ids=profile_source_ids(feature.column),
        )
        artifact_ids["roc_feature"].append(metadata.artifact_id)
        publish_roc_preview(feature.column, payload)

    def save_binning(feature: BinningFeatureSpec, result: Any) -> None:
        result_payload = result.model_dump(mode="json")
        target_route = {
            "column": target,
            "target_type": effective_target_type,
            "positive_class": effective_positive_class,
            "fingerprint": manifest.scope.target_fingerprint,
        }
        result_payload["target_route"] = target_route
        fine, _ = repo.save_or_reuse(
            {
                "definition": result.fine_definition.model_dump(mode="json"),
                "bins": [row.model_dump(mode="json") for row in result.fine_bins],
                "metrics": [metric.model_dump(mode="json") for metric in result.fine_metrics],
                "target_route": target_route,
            },
            artifact_type="fine_bins",
            **artifact_base(feature.column),
            source_artifact_ids=profile_source_ids(feature.column),
        )
        coarse, _ = repo.save_or_reuse(
            {
                "definition": result.coarse_definition.model_dump(mode="json"),
                "bins": [row.model_dump(mode="json") for row in result.coarse_bins],
                "metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                "warnings": list(result.warnings),
                "target_route": target_route,
            },
            artifact_type="coarse_bins",
            **artifact_base(feature.column),
            source_artifact_ids=(fine.artifact_id,),
        )
        iv, _ = repo.save_or_reuse(
            result_payload,
            artifact_type="iv",
            **artifact_base(feature.column),
            source_artifact_ids=(fine.artifact_id, coarse.artifact_id),
        )
        artifact_ids["fine_bins"].append(fine.artifact_id)
        artifact_ids["coarse_bins"].append(coarse.artifact_id)
        artifact_ids["iv"].append(iv.artifact_id)

    response = assess_feature_target_separation(
        frame,
        TargetSpec(column=target, target_type=effective_target_type,
                   positive_class=effective_positive_class),
        [_roc_spec(row) for row in eligible],
        [_binning_spec(row) for row in eligible],
        missing_target_action=missing_target_action,
        percentile_bins=percentile_bins,
        analysis_constraints=AnalysisConstraints(**(analysis_constraints or {})),
        binning_constraints=BinningConstraints(**(binning_constraints or {})),
        separation_thresholds=(None if separation_thresholds is None
                               else SeparationThresholds(**separation_thresholds)),
        feature_profiles=feature_profiles,
        cached_roc_results={target: cached_roc},
        roc_result_callback=save_roc,
        cached_binning_results=cached_binning,
        binning_result_callback=save_binning,
        progress_callback=progress_callback,
        feature_preview_callback=feature_preview_callback,
    )
    _include_reused_artifacts(
        repo, manifest, snapshot_ref.asset_id, snapshot_ref.snapshot_id,
        features, artifact_ids, profile_source_ids,
        allow_promoted=(BinningConstraints(**(binning_constraints or {})).model_dump(mode="json")
                        == BinningConstraints().model_dump(mode="json")),
    )
    return FeatureTargetSeparationOutcome(
        response=response,
        artifact_ids=_dedupe_artifact_ids(artifact_ids),
        candidate_findings=tuple(_candidate_findings(
            response, snapshot_id, table_name, run_id,
            {**FINDING_THRESHOLD_DEFAULTS, **(finding_thresholds or {})},
        )),
        manifest=manifest,
    )


def _eligible_inventory(rows: list[dict[str, Any]], table: str, target: str,
                        selected: list[str] | None) -> list[dict[str, Any]]:
    requested = set(selected or [])
    out = []
    for row in rows:
        column = row.get("column_name")
        if row.get("table_name") != table or column == target:
            continue
        if not is_eligible_feature(row):
            continue
        if selected is None and not is_recommended_feature(row):
            continue
        if selected is not None and column not in requested:
            continue
        out.append(row)
    if selected is None and not out:
        out = [row for row in rows
               if row.get("table_name") == table
               and row.get("column_name") != target
               and is_eligible_feature(row)]
    absent = sorted(requested - {row["column_name"] for row in out})
    if absent:
        raise ValueError(f"Selected columns are not available independent variables: {absent}")
    return sorted(out, key=lambda row: row["column_name"])


def _row_key(row: dict[str, Any]) -> str:
    return str(row["column_name"])


def _feature_profile(row: dict[str, Any]) -> dict[str, Any]:
    profile = dict(row.get("profile_json") or {})
    distinct = row.get("distinct_count")
    if distinct is not None:
        profile["n_levels"] = int(distinct)
    return profile


def _feature_type(row: dict[str, Any]) -> str:
    classification = str(row.get("classification") or row.get("logical_type") or "").lower()
    dtype = str(row.get("data_type") or "").lower()
    if any(token in classification for token in ("numeric", "number", "integer", "decimal")):
        return "numeric"
    if any(token in dtype for token in ("int", "float", "decimal", "number")):
        return "numeric"
    return "categorical"


def _confirmed_special_values(row: dict[str, Any]) -> list[Any]:
    if not row.get("missing_codes_confirmed"):
        return []
    return list(row.get("missing_value_codes_json") or [])


def _roc_spec(row: dict[str, Any]) -> FeatureSpec:
    return FeatureSpec(
        column=row["column_name"],
        feature_type=_feature_type(row),
        special_values=_confirmed_special_values(row),
    )


def _binning_spec(row: dict[str, Any]) -> BinningFeatureSpec:
    specials = {
        f"Special value {value}": [value]
        for value in _confirmed_special_values(row)
    }
    return BinningFeatureSpec(
        column=row["column_name"],
        feature_type=_feature_type(row),
        special_values=specials,
    )


def _cached_roc(repo: AnalysisArtifactRepository, manifest: FrozenAnalysisManifest,
                asset_id: str, snapshot_id: str, features: list[str],
                profile_source_ids: Callable[[str], tuple[str, ...]]) -> dict[str, dict[str, Any]]:
    out = {}
    for feature in features:
        metadata = repo.find_exact(
            artifact_type="roc_feature", asset_id=asset_id, snapshot_id=snapshot_id,
            comparison_snapshot_id=None,
            population_fingerprint=manifest.scope.population_fingerprint,
            target_fingerprint=manifest.scope.target_fingerprint,
            feature=feature, methodology_fingerprint=manifest.methodology_fingerprint,
            scope="universal", workflow_id=None,
            source_artifact_ids=profile_source_ids(feature),
        )
        if metadata is not None:
            _meta, payload = repo.get(metadata.artifact_id)
            out[feature] = payload
    return out


def _cached_binning(repo: AnalysisArtifactRepository, manifest: FrozenAnalysisManifest,
                    asset_id: str, snapshot_id: str, features: list[str],
                    profile_source_ids: Callable[[str], tuple[str, ...]],
                    allow_promoted: bool = True) -> dict[str, Any]:
    out = {}
    for feature in features:
        promoted = _promoted_universal_chain(repo, snapshot_id, manifest.scope.table,
                                             feature, manifest.scope.target_fingerprint) \
            if allow_promoted else None
        if promoted:
            out[feature] = promoted["iv_payload"]
            continue
        fine = _find_artifact(
            repo, manifest, asset_id, snapshot_id, feature, "fine_bins",
            profile_source_ids(feature),
        )
        coarse = None
        iv = None
        if fine is not None:
            coarse = _find_artifact(repo, manifest, asset_id, snapshot_id, feature,
                                    "coarse_bins", (fine.artifact_id,))
        if fine is not None and coarse is not None:
            iv = _find_artifact(repo, manifest, asset_id, snapshot_id, feature,
                                "iv", (fine.artifact_id, coarse.artifact_id))
        if iv is not None:
            _meta, payload = repo.get(iv.artifact_id)
            out[feature] = payload
    return out


def _find_artifact(repo: AnalysisArtifactRepository, manifest: FrozenAnalysisManifest,
                   asset_id: str, snapshot_id: str, feature: str, artifact_type: str,
                   source_artifact_ids: tuple[str, ...] = ()):
    return repo.find_exact(
        artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
        comparison_snapshot_id=None,
        population_fingerprint=manifest.scope.population_fingerprint,
        target_fingerprint=manifest.scope.target_fingerprint,
        feature=feature, methodology_fingerprint=manifest.methodology_fingerprint,
        scope="universal", workflow_id=None,
        source_artifact_ids=source_artifact_ids,
    )


def _include_reused_artifacts(repo: AnalysisArtifactRepository, manifest: FrozenAnalysisManifest,
                              asset_id: str, snapshot_id: str, features: list[str],
                              artifact_ids: dict[str, list[str]],
                              profile_source_ids: Callable[[str], tuple[str, ...]],
                              allow_promoted: bool = True) -> None:
    for feature in features:
        promoted = _promoted_universal_chain(repo, snapshot_id, manifest.scope.table,
                                             feature, manifest.scope.target_fingerprint) \
            if allow_promoted else None
        if promoted:
            artifact_ids["fine_bins"].append(promoted["fine"].artifact_id)
            artifact_ids["coarse_bins"].append(promoted["coarse"].artifact_id)
            artifact_ids["iv"].append(promoted["iv"].artifact_id)
            continue
        fine = _find_artifact(
            repo, manifest, asset_id, snapshot_id, feature, "fine_bins",
            profile_source_ids(feature),
        )
        if fine is not None:
            artifact_ids["fine_bins"].append(fine.artifact_id)
        coarse = None
        if fine is not None:
            coarse = _find_artifact(repo, manifest, asset_id, snapshot_id, feature,
                                    "coarse_bins", (fine.artifact_id,))
        if coarse is not None:
            artifact_ids["coarse_bins"].append(coarse.artifact_id)
        iv = None
        if fine is not None and coarse is not None:
            iv = _find_artifact(repo, manifest, asset_id, snapshot_id, feature,
                                "iv", (fine.artifact_id, coarse.artifact_id))
        if iv is not None:
            artifact_ids["iv"].append(iv.artifact_id)
        roc = _find_artifact(
            repo, manifest, asset_id, snapshot_id, feature, "roc_feature",
            profile_source_ids(feature),
        )
        if roc is not None:
            artifact_ids["roc_feature"].append(roc.artifact_id)


def _dedupe_artifact_ids(value: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(dict.fromkeys(ids)) for key, ids in value.items()}


def _promoted_universal_chain(repo: AnalysisArtifactRepository, snapshot_id: str,
                              table: str, feature: str,
                              target_fingerprint: str | None) -> dict[str, Any] | None:
    """Resolve the active explicit universal promotion independent of its creation config."""
    for revision in db.query("diag_binning_revisions", scope="universal", order_by="created_at DESC"):
        if revision.get("item_id") != snapshot_id or revision.get("feature") != feature:
            continue
        try:
            fine = repo.get_metadata(revision["fine_artifact_id"])
            coarse = repo.get_metadata(revision["coarse_artifact_id"])
            iv = repo.get_metadata(revision["iv_artifact_id"])
            _meta, iv_payload = repo.get(iv.artifact_id)
        except Exception:
            continue
        if any(meta.status != "active" for meta in (fine, coarse, iv)):
            continue
        if (fine.snapshot_id != snapshot_id or fine.identity.get("table") != table
                or fine.target_fingerprint != target_fingerprint):
            continue
        return {"fine": fine, "coarse": coarse, "iv": iv, "iv_payload": iv_payload}
    return None


def _candidate_findings(response: FeatureTargetSeparationResponse, snapshot_id: str,
                        table: str, run_id: str | None,
                        thresholds: dict[str, float]) -> list[dict[str, Any]]:
    findings = []
    for result in response.results:
        reasons = []
        if result.auc is not None and result.auc >= thresholds["leakage_auc"]:
            reasons.append("auc_leakage_threshold")
        if result.iv is not None and result.iv >= thresholds["leakage_iv"]:
            reasons.append("iv_leakage_threshold")
        if result.leakage_status == "candidate":
            reasons.append("localized_leakage")
        if result.auc is not None and result.auc < thresholds["poor_auc"]:
            reasons.append("auc_poor_discrimination")
        if result.iv is not None and result.iv < thresholds["poor_iv"]:
            reasons.append("iv_poor_discrimination")
        if not reasons:
            continue
        candidate_type = "target_leakage" if any(
            reason in {"auc_leakage_threshold", "iv_leakage_threshold", "localized_leakage"}
            for reason in reasons
        ) else "poor_discrimination"
        findings.append({
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "diagnostic_id": DIAGNOSTIC_ID,
            "table_name": table,
            "feature": result.feature,
            "decision_type": "candidate_flag",
            "review_state": "open",
            "category": result.category,
            "leakage_status": result.leakage_status,
            "candidate_type": candidate_type,
            "candidate_reasons": reasons,
            "auc": result.auc,
            "iv": result.iv,
            "gini": result.roc.primary_gini if result.roc else None,
            "thresholds": dict(thresholds),
            "issue_row_id": None,
        })
    return findings
