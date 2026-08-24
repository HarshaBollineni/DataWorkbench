"""Progressive governed scope contract for Diagnostic #14 (PSI)."""
from __future__ import annotations

import math
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any

import pandas as pd

import system_db as db
from analysis_runtime.artifacts import AnalysisArtifactRepository
from analysis_runtime.contracts import SnapshotRef, stable_fingerprint, target_fingerprint
from analysis_runtime.snapshots import SnapshotLoader
from analysis_runtime.targets import resolve_target_route
from .manifest import DRAFT, RUNNING, ManifestError, record_decision
from .register import require_executable

DIAGNOSTIC_ID = 14
MANIFEST_KIND = "population_stability_index"
MANIFEST_VERSION = 3
ENGINE_VERSION = "1.1.0"
FULL_POPULATION = stable_fingerprint({})
RECOMMENDED_ROLES = {"feature", "score"}
EXCLUDED_ROLES = {"target", "identifier", "key"}


def _target_fingerprint(manifest: dict[str, Any]) -> str | None:
    choice = manifest.get("target_choice") or {}
    if choice.get("mode") != "saved_target":
        return None
    stored = choice.get("target_fingerprint")
    if stored:
        return stored
    # Historical open drafts are upgraded when the target choice is confirmed.
    # Column-only legacy identities are intentionally not treated as exact binary
    # matches because their event class cannot be proven.
    return target_fingerprint(
        choice.get("saved_target"), target_type=choice.get("target_type"),
        positive_class=choice.get("positive_class"),
    )


@dataclass(frozen=True)
class PopulationRef:
    role: str
    snapshot: SnapshotRef
    predicate: dict[str, Any]
    null_policy: str = "not_applicable"
    data_fingerprint: str = FULL_POPULATION

    def to_dict(self) -> dict[str, Any]:
        value = {"role": self.role, "snapshot": self.snapshot.to_dict(), "predicate": self.predicate,
                 "null_policy": self.null_policy, "data_fingerprint": self.data_fingerprint}
        return {**value, "fingerprint": stable_fingerprint(value)}


def _id() -> str:
    return f"drun_{uuid.uuid4().hex[:12]}"


def _snapshot(value: dict[str, Any]) -> SnapshotRef:
    return SnapshotRef(**{**value, "tables": tuple(value.get("tables") or ())})


def _inventory(snapshot_id: str, table: str) -> dict[str, dict[str, Any]]:
    return {row["column_name"]: row for row in db.query("variable_inventory", item_id=snapshot_id)
            if row.get("table_name") == table}


def _profile(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("profile_json") or {})


def _distinct(row: dict[str, Any]) -> int:
    value = _profile(row)
    return int(value.get("distinct_count", value.get("cardinality", value.get("unique_count", 0))) or 0)


def _role(row: dict[str, Any]) -> str:
    return str(row.get("role") or row.get("dictionary_role") or "feature").strip().lower()


def _logical(row: dict[str, Any]) -> str:
    return str(row.get("classification") or "other").strip().lower()


def _kind(row: dict[str, Any]) -> str:
    text = f"{_logical(row)} {row.get('data_type') or ''}".lower()
    if "date" in text or "time" in text: return "date"
    if any(token in text for token in ("int", "float", "numeric", "number", "decimal")): return "numeric"
    if "bool" in text or _distinct(row) == 2: return "boolean"
    return "categorical"


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = _kind(left), _kind(right)
    return a == b or {a, b} <= {"numeric", "boolean"}


def _schema_column(row: dict[str, Any]) -> dict[str, Any]:
    """Return the governed fields that explain PSI schema compatibility."""
    return {"analytical_type": _kind(row), "physical_type": row.get("data_type"),
            "classification": _logical(row), "role": _role(row)}


def _target(snapshot_id: str, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = db.query_one("dq_items", item_id=snapshot_id) or {}
    saved = str(item.get("target_variable") or "").strip()
    candidates = sorted(name for name, row in rows.items() if _role(row) == "target")
    if not saved:
        return {"state": "absent", "column": None, "role_candidates": candidates,
                "source": "data_sourcing.target_variable",
                "message": "Data Sourcing saved this snapshot without a target."}
    row = rows.get(saved)
    if not row or _role(row) != "target":
        return {"state": "unresolved", "column": saved, "role_candidates": candidates,
                "source": "data_sourcing.target_variable",
                "message": "The saved target conflicts with the confirmed schema role."}
    return {"state": "present", "column": saved, "role_candidates": candidates,
            "logical_type": _logical(row), "physical_type": row.get("data_type"),
            "source": "data_sourcing.target_variable",
            "message": f"Data Sourcing identified {saved} as the saved target."}


def _provenance(snapshot_id: str, table: str) -> dict[str, Any]:
    item = db.query_one("dq_items", item_id=snapshot_id) or {}
    version = db.query_one("dq_asset_versions", asset_id=item.get("dataset_family_id"),
                           version_no=item.get("version_no")) or {}
    repo = AnalysisArtifactRepository()
    columns = [row.artifact_id for row in repo.list(snapshot_id=snapshot_id, artifact_type="column_profile", status="active")
               if row.identity.get("table") == table]
    tables = [row.artifact_id for row in repo.list(snapshot_id=snapshot_id, artifact_type="table_profile", status="active")
              if row.identity.get("table") == table]
    return {"schema_version_id": version.get("version_id"), "dictionary_version_id": item.get("dictionary_version_id"),
            "table_profile_artifact_id": tables[0] if tables else None,
            "column_profile_artifact_ids": columns,
            "schema_source": "Analytics Artifact Repository"}


def _context(ref: SnapshotRef, table: str, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matches = db.execute("SELECT * FROM dq_item_tables WHERE item_id=? AND table_name=?", [ref.snapshot_id, table])
    table_row = matches[0] if matches else {}
    target = _target(ref.snapshot_id, rows)
    return {"snapshot": ref.to_dict(), "table": table, "row_count": int(table_row.get("row_count") or 0),
            "column_count": int(table_row.get("col_count") or len(rows)),
            "schema_status": "action_required" if target["state"] == "unresolved" else "confirmed",
            "target": target, "provenance": _provenance(ref.snapshot_id, table),
            "message": "PSI uses the schema and feature roles confirmed during Data Sourcing."}


def _profile_evidence_catalog(snapshot_id: str, table: str,
                              rows: dict[str, dict[str, Any]]) -> dict[str, tuple[str | None, dict[str, Any]]]:
    """Resolve all profile evidence with one repository listing per refresh."""
    repo = AnalysisArtifactRepository()
    result: dict[str, tuple[str | None, dict[str, Any]]] = {}
    for metadata in repo.list(snapshot_id=snapshot_id, artifact_type="column_profile", status="active"):
        feature = metadata.feature
        if feature not in rows or feature in result or metadata.identity.get("table") != table:
            continue
        try:
            _stored, payload = repo.get(metadata.artifact_id)
            result[feature] = (metadata.artifact_id, dict(payload))
        except Exception:
            continue
    for feature, inventory_row in rows.items():
        if feature in result: continue
        profile = _profile(inventory_row)
        result[feature] = (None, {**profile, "data_type": inventory_row.get("data_type"),
            "classification": inventory_row.get("classification"), "role": inventory_row.get("role"),
            "distinct_count": _distinct(inventory_row),
            "special_values": inventory_row.get("missing_value_codes_json") or []})
    return result


def _comparison(left_id: str, right_id: str, left_table: str,
                right_table: str | None = None) -> dict[str, Any]:
    right_table = right_table or left_table
    left, right = _inventory(left_id, left_table), _inventory(right_id, right_table)
    overlap = sorted(set(left) & set(right)); compatible = [x for x in overlap if _compatible(left[x], right[x])]
    lt, rt = _target(left_id, left), _target(right_id, right)
    difference = None if (lt["state"], lt.get("column")) == (rt["state"], rt.get("column")) else {"baseline": lt, "current": rt}
    incompatible = [x for x in overlap if x not in compatible]
    baseline_only, current_only = sorted(set(left) - set(right)), sorted(set(right) - set(left))
    paired = lambda name: {"column": name, "baseline": _schema_column(left[name]),
                           "current": _schema_column(right[name])}
    return {"status": "complete", "baseline_table": left_table, "current_table": right_table,
            "compatible": compatible, "incompatible": incompatible,
            "baseline_only": baseline_only, "current_only": current_only,
            "compatible_details": [paired(name) for name in compatible],
            "incompatible_details": [paired(name) for name in incompatible],
            "baseline_only_details": [{"column": name, "baseline": _schema_column(left[name])}
                                      for name in baseline_only],
            "current_only_details": [{"column": name, "current": _schema_column(right[name])}
                                     for name in current_only],
            "compatibility_basis": {
                "matching_key": "exact_column_name",
                "type_rule": "analytical_kind",
                "analytical_kinds": ["numeric", "categorical", "boolean", "date"],
                "numeric_boolean_compatible": True,
                "schema_roles_used_for_type_compatibility": False,
            },
            "target_difference": difference}


def _best_table_match(baseline: SnapshotRef, baseline_table: str,
                      candidate: SnapshotRef) -> dict[str, Any]:
    comparisons = [_comparison(baseline.snapshot_id, candidate.snapshot_id, baseline_table, table)
                   for table in candidate.tables]
    if not comparisons:
        return {"available": False, "current_table": None, "comparison": None,
                "reason": "No governed table is available"}
    best = max(comparisons, key=lambda row: (
        len(row["compatible"]),
        row["current_table"] == baseline_table,
        -len(row["incompatible"]),
    ))
    available = bool(best["compatible"])
    return {"available": available, "current_table": best["current_table"], "comparison": best,
            "reason": None if available else "No schema-compatible columns"}


def _options(baseline: SnapshotRef, baseline_table: str) -> list[dict[str, Any]]:
    result = []
    for item in db.query("dq_items", ingest_status="ready", snapshot_status="active"):
        if item["item_id"] == baseline.snapshot_id: continue
        try: ref = SnapshotLoader().reference(item["item_id"])
        except Exception: continue
        result.append({"snapshot": ref.to_dict(), "available": bool(ref.tables),
                       "compatibility_status": "pending_user_selection",
                       "reason": None if ref.tables else "No governed table is available"})
    return result


def _reviewed(artifact_id: str) -> bool:
    return db.query_one("diag_binning_revisions", coarse_artifact_id=artifact_id) is not None


def _automatic_binning_source(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the governed route; PSI no longer asks users to choose a source."""
    target_aware = (manifest.get("target_choice") or {}).get("mode") == "saved_target"
    if manifest.get("mode") == "two_snapshot" and target_aware:
        mode = "diagnostic_2_universal_or_generate"
        explanation = "Reuse an exact promoted Diagnostic 2 definition, or create a promotion candidate."
    elif target_aware:
        mode = "diagnostic_2_generate"
        explanation = "Generate Diagnostic 2 IV fine and optimized coarse bins from the split Baseline."
    else:
        mode = "psi_contract_generate"
        explanation = "Generate deterministic PSI-contract bins from the Baseline."
    return {"status": "confirmed", "mode": mode, "automatic": True,
            "scope": "psi_run", "explanation": explanation}


def _exact_promoted_iv(manifest: dict[str, Any], feature: str) -> dict[str, Any] | None:
    """Find the newest explicitly promoted IV coarse definition for this full Baseline.

    A revision row is the promotion receipt.  This intentionally excludes old
    automatic artifacts that happened to be written with universal scope.
    """
    if manifest.get("mode") != "two_snapshot":
        return None
    snapshot = manifest["baseline"]["snapshot"]
    target_fp = _target_fingerprint(manifest)
    repo = AnalysisArtifactRepository()
    rows = db.query("diag_binning_revisions", scope="universal", order_by="created_at DESC")
    for revision in rows:
        if revision.get("item_id") != snapshot["snapshot_id"] or revision.get("feature") != feature:
            continue
        try:
            coarse_meta, coarse_payload = repo.get(revision["coarse_artifact_id"])
        except Exception:
            continue
        if (coarse_meta.status != "active" or coarse_meta.snapshot_id != snapshot["snapshot_id"]
                or coarse_meta.identity.get("table") != manifest.get("baseline_table", manifest["table"])
                or coarse_meta.target_fingerprint != target_fp):
            continue
        return {"artifact_id": coarse_meta.artifact_id, "artifact_type": "coarse_bins",
                "payload_hash": coarse_meta.payload_hash, "scope": "universal",
                "fine_artifact_id": revision.get("fine_artifact_id"),
                "coarse_bin_count": len(coarse_payload.get("bins") or []),
                "promotion_revision_id": revision.get("revision_id")}
    return None


def _safe_coarse(payload: dict[str, Any], feature: str) -> bool:
    definition = payload.get("definition") or {}
    return definition.get("feature") == feature and bool(definition.get("out_of_range_guard_bins"))


def _target_compatible(manifest: dict[str, Any], meta: Any, payload: dict[str, Any]) -> bool:
    """Require the reusable library to follow this run's confirmed target route."""
    choice = manifest.get("target_choice") or {}
    definition = payload.get("definition") or {}
    artifact_target = payload.get("target") or definition.get("target")
    if choice.get("mode") == "saved_target":
        return artifact_target == choice.get("saved_target")
    return artifact_target in {None, ""} and meta.target_fingerprint in {None, ""}


def _empty_evidence() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in ("exact_frozen", "exact_draft", "exact_automatic", "exact_fine", "safe_mismatch", "rejected")}


def _has_repository_matches(manifest: dict[str, Any]) -> bool:
    """Return whether the active PSI setup contains applicable matched evidence."""
    applicable = {"exact_frozen", "exact_draft", "exact_automatic", "exact_fine", "safe_mismatch"}
    selected = set(manifest.get("selected_features") or [])
    for row in manifest.get("feature_metadata") or []:
        if row.get("column") not in selected:
            continue
        evidence = (row.get("bin_route") or {}).get("evidence") or {}
        if any(evidence.get(key) for key in applicable):
            return True
    return False


def _evidence_catalog(manifest: dict[str, Any], features: set[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Classify bin evidence for all features with two repository listings, not two per feature."""
    repo, baseline = AnalysisArtifactRepository(), manifest["baseline"]
    current_run_id = manifest.get("run_id")
    catalog = {feature: _empty_evidence() for feature in features}
    reviewed = {row.get("coarse_artifact_id") for row in db.query("diag_binning_revisions")}
    for artifact_type in ("psi_bins", "coarse_bins", "fine_bins"):
        for meta in repo.list(asset_id=baseline["snapshot"]["asset_id"], artifact_type=artifact_type):
            feature = meta.feature
            if feature not in catalog: continue
            groups = catalog[feature]
            try: _stored, payload = repo.get(meta.artifact_id)
            except Exception:
                groups["rejected"].append({"artifact_id": meta.artifact_id, "reason": "corrupt_or_missing"}); continue
            ref = {"artifact_id": meta.artifact_id, "artifact_type": artifact_type, "payload_hash": meta.payload_hash,
                   "scope": meta.scope, "population_fingerprint": meta.population_fingerprint,
                   "target_fingerprint": meta.target_fingerprint, "created_at": meta.created_at,
                   "fine_bin_count": len(payload.get("bins") or []) if artifact_type == "fine_bins" else None,
                   "coarse_bin_count": (len(payload.get("bins") or []) or payload.get("actual_bin_count"))
                       if artifact_type != "fine_bins" else None}
            active = meta.status == "active" and meta.integrity_status in {"verified", "unknown"}
            exact = meta.population_fingerprint == baseline["data_fingerprint"]
            target_compatible = _target_compatible(manifest, meta, payload)
            definition = payload.get("definition") or {}
            target_aware_numeric = (manifest.get("target_choice", {}).get("mode") == "saved_target"
                                    and (definition.get("feature_type") == "numeric"
                                         or payload.get("kind") in {"numeric", "date"}))
            if artifact_type != "fine_bins" and target_aware_numeric:
                fine_source = _fine_source(repo, meta)
                if not fine_source or not _target_compatible(manifest, fine_source[0], fine_source[1]):
                    groups["rejected"].append({"artifact_id": meta.artifact_id,
                        "reason": "target_aware_numeric_requires_iv_fine_foundation"})
                    continue
            # Workflow-local evidence is private to its originating run. PSI
            # drafts are also unapproved decisions and must never propagate to
            # another run merely because their Baseline fingerprint matches.
            if meta.scope == "workflow_local" and meta.run_id != current_run_id:
                groups["rejected"].append({"artifact_id": meta.artifact_id,
                    "reason": "workflow_local_to_another_run"})
                continue
            if (artifact_type == "psi_bins" and payload.get("governance_state") != "frozen"
                    and meta.run_id != current_run_id):
                groups["rejected"].append({"artifact_id": meta.artifact_id,
                    "reason": "unapproved_psi_draft_from_another_run"})
                continue
            if artifact_type == "psi_bins":
                frozen = payload.get("governance_state") == "frozen"
                key = "exact_frozen" if active and exact and target_compatible and frozen else "exact_draft" if active and exact and target_compatible else "safe_mismatch" if active and target_compatible and frozen else "rejected"
            elif artifact_type == "coarse_bins":
                was_reviewed, safe = meta.artifact_id in reviewed, _safe_coarse(payload, feature)
                key = "exact_frozen" if active and exact and target_compatible and was_reviewed and safe else "exact_automatic" if active and exact and target_compatible and not was_reviewed and safe else "safe_mismatch" if active and target_compatible and was_reviewed and safe else "rejected"
            else:
                definition = payload.get("definition") or {}
                key = "exact_fine" if active and exact and target_compatible and definition.get("feature") == feature else "rejected"
            groups[key].append(ref if key != "rejected" else {"artifact_id": meta.artifact_id, "reason": "unreviewed_or_unsafe"})
    return catalog


def _route(manifest: dict[str, Any], feature: str, row: dict[str, Any],
           evidence: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    role, distinct = _role(row), _distinct(row)
    split = (manifest.get("population_definition") or {}).get("split_feature")
    if role in EXCLUDED_ROLES:
        return {"workflow": "ineligible", "readiness": "excluded", "reason": "Excluded saved schema role."}
    if distinct == 1 and feature not in set(manifest.get("feature_eligibility_overrides") or []):
        return {"workflow": "constant_feature_excluded", "readiness": "excluded",
                "reason": "Constant in Baseline; operator review is required before inclusion.",
                "override_available": True}
    if feature == split and feature not in set(manifest.get("split_feature_overrides") or []):
        return {"workflow": "split_feature_excluded", "readiness": "excluded", "reason": "This feature defines the populations."}
    if not manifest.get("population_ready"):
        return {"workflow": "awaiting_population", "readiness": "blocked", "reason": "Define valid populations first."}
    if manifest["governed_context"]["target"]["state"] == "unresolved":
        return {"workflow": "schema_action_required", "readiness": "blocked", "reason": "Review the saved schema."}
    if manifest.get("target_choice", {}).get("status") != "confirmed":
        return {"workflow": "awaiting_target_confirmation", "readiness": "blocked", "reason": "Confirm target usage for this run."}
    if feature not in set(manifest.get("selected_features") or []):
        return {"workflow": "eligible_for_selection", "readiness": "eligible",
                "reason": "Eligible for PSI selection; binning is resolved after selection."}
    source_choice = manifest.get("binning_source_choice") or {}
    if source_choice.get("status") != "confirmed":
        return {"workflow": "awaiting_scope_selection", "readiness": "source_required",
                "reason": "Save the PSI variable selection to determine binning automatically."}
    evidence = evidence or _empty_evidence(); assigned = manifest.get("frozen_bins", {}).get(feature)
    if assigned: return {"workflow": "reuse_frozen", "readiness": "ready", "reason": "Confirmed reviewed bins will be reused.", "artifact": assigned, "evidence": evidence}
    draft = manifest.get("bin_drafts", {}).get(feature)
    if draft:
        resolution = draft.get("resolution")
        reasons = {
            "no_applicable_repository_match": "No applicable repository bins were found; a new Baseline draft was generated.",
            "repository_match_prepared_for_review": "Repository bin evidence was found and copied into a workflow-local review draft.",
            "repository_match_rejected_new": "A repository candidate was not selected for reuse; a new Baseline draft was generated.",
            "repository_match_revised": "Matched fine-bin evidence was revised into a new workflow-local coarse-bin draft.",
        }
        return {"workflow": "review_draft", "readiness": "review_required",
            "reason": reasons.get(resolution, "A new Baseline bin draft was generated for this PSI setup."),
            "artifact": draft, "evidence": evidence, "resolution": resolution}
    if manifest.get("mode") == "two_snapshot" and manifest["target_choice"]["mode"] == "saved_target":
        promoted = _exact_promoted_iv(manifest, feature)
        if promoted:
            return {"workflow": "use_universal_iv", "readiness": "review_required",
                    "reason": "Using the active promoted Diagnostic 2 definition for this exact Baseline snapshot, table, feature and target.",
                    "artifact": promoted, "evidence": evidence}
    if distinct == 1 and feature in set(manifest.get("feature_eligibility_overrides") or []):
        return {"workflow": "generate_constant_guard", "readiness": "generation_available",
                "reason": "Generate an exact Baseline-value bin with an explicit unseen-value guard.",
                "evidence": evidence}
    if manifest["target_choice"]["mode"] == "saved_target":
        return {"workflow": "generate_target_aware", "readiness": "generation_available", "reason": "Generate IV bins from the baseline and saved target.", "evidence": evidence}
    return {"workflow": "generate_target_free", "readiness": "generation_available", "reason": "Generate deterministic baseline-only bins.", "evidence": evidence}


def _refresh(manifest: dict[str, Any], *, reuse_feature_evidence: bool = False) -> dict[str, Any]:
    manifest.setdefault("feature_eligibility_overrides", [])
    target_context = (manifest.get("governed_context") or {}).get("target") or {}
    if target_context.get("state") == "absent":
        manifest["target_choice"] = {
            "status": "confirmed", "mode": "target_free", "saved_target": None,
            "scope": "psi_run_only", "automatic": True,
            "does_not_modify_data_sourcing": True,
        }
    left = manifest["baseline"]["snapshot"]["snapshot_id"]
    right = manifest["current"]["snapshot"]["snapshot_id"]
    baseline_table = manifest.get("baseline_table") or manifest["table"]
    current_table = manifest.get("current_table") or baseline_table
    manifest["baseline_table"], manifest["current_table"] = baseline_table, current_table
    if not reuse_feature_evidence:
        baseline_ref = _snapshot(manifest["baseline"]["snapshot"])
        options = _options(baseline_ref, baseline_table)
        manifest["population_method"]["options"]["compare_snapshots"] = {
            "available": any(row["available"] for row in options), "snapshots": options}
        manifest["schema_comparison"] = _comparison(left, right, baseline_table, current_table)
        if manifest.get("mode") == "two_snapshot":
            manifest["population_ready"] = bool(manifest["schema_comparison"]["compatible"])
    rows = _inventory(left, baseline_table)
    compatible = set(manifest["schema_comparison"]["compatible"])
    previous = {row["column"]: row for row in manifest.get("feature_metadata", [])}
    retained_guidance = {name: previous[name]["split_guidance"] for name in compatible
                         if reuse_feature_evidence and name in previous and previous[name].get("split_guidance")}
    missing_profiles = {name: rows[name] for name in compatible - set(retained_guidance)}
    profile_catalog = _profile_evidence_catalog(left, baseline_table, missing_profiles) if missing_profiles else {}
    selected = set(manifest.get("selected_features") or [])
    if selected and manifest.get("target_choice", {}).get("status") == "confirmed":
        manifest["binning_source_choice"] = _automatic_binning_source(manifest)
    repository_selected: set[str] = set()
    retained_evidence = {name: previous[name]["bin_route"].get("evidence") or _empty_evidence()
                         for name in repository_selected if reuse_feature_evidence and name in previous
                         and previous[name].get("bin_route", {}).get("evidence")}
    missing_evidence = repository_selected - set(retained_evidence)
    evidence_catalog = dict(retained_evidence)
    if missing_evidence:
        evidence_catalog.update(_evidence_catalog(manifest, missing_evidence))
    metadata, recommended = [], []
    for name in manifest["schema_comparison"]["compatible"]:
        row = rows[name]
        route = _route(manifest, name, row, evidence_catalog.get(name))
        if _distinct(row) == 1 and name in set(manifest.get("feature_eligibility_overrides") or []):
            route = {**route, "eligibility_override": True,
                     "warning": "Operator included a feature that is constant in Baseline. Current may still contain a different or unseen value."}
        if name == (manifest.get("population_definition") or {}).get("split_feature") and name in set(manifest.get("split_feature_overrides") or []):
            route = {**route, "split_feature_override": True,
                     "warning": "Operator included the variable used to define the populations; interpret its PSI as structurally influenced by the split."}
        from analysis_runtime.population_guidance import guidance_from_profile
        if name in retained_guidance:
            split_guidance = retained_guidance[name]
        else:
            artifact_id, profile = profile_catalog[name]
            split_guidance = guidance_from_profile(feature=name, feature_kind=_kind(row),
                distinct_count=_distinct(row), profile=profile, artifact_id=artifact_id)
        recommend = _role(row) in RECOMMENDED_ROLES and route["readiness"] != "excluded"
        if recommend: recommended.append(name)
        metadata.append({"column": name, "role": _role(row).title(), "logical_type": _logical(row),
                         "physical_type": row.get("data_type"), "feature_kind": _kind(row),
                         "distinct_count": _distinct(row), "null_count": int(_profile(row).get("null_count") or 0),
                         "special_values": list(row.get("missing_value_codes_json") or []), "recommended": recommend,
                         "recommendation_reason": None if recommend else
                            f"Not recommended: the saved schema role is {_role(row).title()}. You can still select it when eligible.",
                         "split_guidance": split_guidance, "bin_route": route})
    manifest["feature_metadata"], manifest["recommended_features"] = metadata, recommended
    allowed = {x["column"] for x in metadata if x["bin_route"]["readiness"] != "excluded"}
    manifest["selected_features"] = [x for x in manifest.get("selected_features", []) if x in allowed]
    selected = set(manifest["selected_features"])
    if selected and manifest.get("target_choice", {}).get("status") == "confirmed":
        manifest["binning_source_choice"] = _automatic_binning_source(manifest)
    manifest["frozen_bins"] = {name: value for name, value in (manifest.get("frozen_bins") or {}).items()
                               if name in selected}
    manifest["bin_drafts"] = {name: value for name, value in (manifest.get("bin_drafts") or {}).items()
                              if name in selected}
    if not selected:
        manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
    summary: dict[str, int] = {}
    for row in metadata: summary[row["bin_route"]["readiness"]] = summary.get(row["bin_route"]["readiness"], 0) + 1
    manifest["readiness_summary"] = summary; blockers = []
    if manifest["governed_context"]["target"]["state"] == "unresolved": blockers.append({"code": "target_unresolved", "message": "Review saved target metadata."})
    if not manifest.get("population_ready"): blockers.append({"code": "population_required", "message": "Define valid populations."})
    if manifest.get("target_choice", {}).get("status") != "confirmed": blockers.append({"code": "target_choice_required", "message": "Confirm target usage."})
    if (manifest["schema_comparison"].get("target_difference")
            and manifest.get("target_choice", {}).get("mode") == "saved_target"
            and not manifest.get("schema_difference_confirmed")):
        blockers.append({"code": "target_schema_difference", "message": "Confirm that the baseline saved target governs this comparison."})
    if not manifest["selected_features"]: blockers.append({"code": "features_required", "message": "Select at least one variable."})
    if manifest.get("binning_source_choice", {}).get("status") != "confirmed":
        blockers.append({"code": "binning_route_required", "message": "Save the PSI variable selection."})
    route_by_name = {x["column"]: x["bin_route"] for x in metadata}
    for name in manifest["selected_features"]:
        if route_by_name[name]["readiness"] != "ready": blockers.append({"code": "frozen_bins_required", "feature": name, "message": f"{name} requires reviewed bins."})
    manifest["blockers"], manifest["ready_to_run"] = blockers, not blockers
    return manifest


def build_manifest(item_id: str, actor: str = "system", *, current_snapshot_id: str | None = None,
                   enforce_register: bool = True) -> dict[str, Any]:
    if enforce_register: require_executable(DIAGNOSTIC_ID)
    loader = SnapshotLoader(); baseline = loader.reference(item_id, allow_historical=True)
    current = loader.reference(current_snapshot_id or item_id)
    if not baseline.tables: raise ManifestError("baseline snapshot has no governed table")
    table, run_id, now = baseline.tables[0], _id(), db.now_ist(); rows = _inventory(item_id, table)
    context, options = _context(baseline, table, rows), _options(baseline, table)
    compare = item_id != current.snapshot_id
    current_table = table
    if compare:
        match = _best_table_match(baseline, table, current)
        if match["current_table"] is None: raise ManifestError("current snapshot has no governed table")
        current_table = match["current_table"]
    manifest = {"manifest_kind": MANIFEST_KIND, "manifest_schema_version": MANIFEST_VERSION,
        "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID, "mode": "two_snapshot" if compare else "one_snapshot",
        "population_method": {"selected": "compare_snapshots" if compare else None,
            "options": {"split_snapshot": {"available": True},
                        "compare_snapshots": {"available": bool([x for x in options if x["available"]]), "snapshots": options}}},
        "baseline": PopulationRef("baseline", baseline, {}).to_dict(),
        "current": PopulationRef("current", current, {}).to_dict(),
        "population_ready": bool(compare and match["available"]) if compare else False,
        "population_definition": {"method": "compare_snapshots"} if compare else None,
        "table": table, "baseline_table": table, "current_table": current_table,
        "governed_context": context,
        "target_choice": {"status": "pending", "mode": None, "saved_target": context["target"].get("column")},
        "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"},
        "selected_features": [], "recommended_features": [], "feature_metadata": [], "excluded_features": [],
        "split_feature_overrides": [], "feature_eligibility_overrides": [],
        "schema_difference_confirmed": False, "frozen_bins": {}, "bin_drafts": {}, "thresholds": {"watch": .10, "investigate": .25},
        "threshold_sources": {"watch": "canonical", "investigate": "canonical"}, "epsilon": 1e-6,
        "epsilon_source": "canonical", "engine_version": ENGINE_VERSION, "methodology_version": "frozen_bins_psi_v3",
        "bindings": context["provenance"], "operator_decisions": [], "status": DRAFT,
        "created_by": actor, "created_at": now, "updated_at": now}
    _refresh(manifest)
    db.insert("diag_runs", {"run_id": run_id, "item_id": item_id, "diagnostic_id": DIAGNOSTIC_ID,
        "manifest_json": manifest, "status": DRAFT, "created_at": now, "started_at": None, "finished_at": None})
    return manifest


def _draft_progress(manifest: dict[str, Any]) -> dict[str, Any]:
    """Small, stable summary for the launch-time resume decision."""
    completed = 0
    if manifest.get("population_method", {}).get("selected"):
        completed = 1
    if completed == 1 and manifest.get("population_ready"):
        completed = 2
    if completed == 2 and manifest.get("target_choice", {}).get("status") == "confirmed":
        completed = 3
    selected = manifest.get("selected_features") or []
    if completed == 3 and selected:
        completed = 4
    if completed == 4 and manifest.get("binning_source_choice", {}).get("status") == "confirmed":
        completed = 5
    frozen = manifest.get("frozen_bins") or {}
    if completed == 5 and all(name in frozen for name in selected):
        completed = 6
    if manifest.get("ready_to_run"):
        completed = 6
    return {
        "completed_steps": completed,
        "total_steps": 6,
        "population_method": manifest.get("population_method", {}).get("selected"),
        "target_choice": manifest.get("target_choice", {}).get("mode"),
        "selected_feature_count": len(selected),
        "frozen_bin_count": sum(name in frozen for name in selected),
    }


def latest_draft(item_id: str) -> dict[str, Any] | None:
    """Return the one PSI draft offered for explicit resume, newest first."""
    rows = db.query("diag_runs", item_id=item_id, diagnostic_id=DIAGNOSTIC_ID,
                    status=DRAFT, order_by="created_at DESC, run_id DESC")
    if not rows:
        return None
    run = rows[0]
    manifest = run.get("manifest_json") or {}
    return {
        "run_id": run["run_id"],
        "item_id": run["item_id"],
        "diagnostic_id": run["diagnostic_id"],
        "status": run["status"],
        "created_at": run.get("created_at"),
        "last_saved_at": manifest.get("updated_at") or run.get("created_at"),
        **_draft_progress(manifest),
    }


def discard_drafts(item_id: str, actor: str = "system") -> int:
    """Remove PSI drafts from the active workspace without erasing their audit trail."""
    rows = db.query("diag_runs", item_id=item_id, diagnostic_id=DIAGNOSTIC_ID, status=DRAFT)
    now = db.now_ist()
    for run in rows:
        manifest = dict(run.get("manifest_json") or {})
        manifest.update({"status": "discarded", "discarded_at": now,
                         "discarded_by": actor, "updated_at": now})
        db.update("diag_runs", {"run_id": run["run_id"]}, {
            "manifest_json": manifest, "status": "discarded", "finished_at": now,
        })
    return len(rows)


def refresh_draft_scope(run_id: str) -> dict[str, Any]:
    """Upgrade an open v1 PSI draft without rewriting frozen historical manifests."""
    from .manifest import get_run
    run = get_run(run_id); manifest = dict(run["manifest_json"])
    if run["status"] != DRAFT:
        return manifest
    version = int(manifest.get("manifest_schema_version") or 1)
    if version >= 2:
        manifest["manifest_schema_version"] = MANIFEST_VERSION
        manifest.setdefault("binning_source_choice", {
            "status": "confirmed" if manifest.get("frozen_bins") else "pending",
            "mode": "repository" if manifest.get("frozen_bins") else None,
            "scope": "psi_run_only",
            "migration": "v2_existing_assignments" if manifest.get("frozen_bins") else None,
        })
        manifest.setdefault("bin_drafts", {})
        manifest["methodology_version"] = "frozen_bins_psi_v3"
        decisions = manifest.get("operator_decisions") or []
        explicitly_chosen = any(row.get("kind") == "population_method" for row in decisions)
        if (manifest.get("mode") == "one_snapshot" and not manifest.get("population_ready")
                and not manifest.get("population_definition") and not explicitly_chosen):
            manifest["population_method"]["selected"] = None
        _refresh(manifest); db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
        return manifest
    baseline, current, table = manifest["baseline"], manifest["current"], manifest["table"]
    base_ref = _snapshot(baseline["snapshot"]); rows = _inventory(base_ref.snapshot_id, table)
    context, options = _context(base_ref, table, rows), _options(base_ref, table)
    one_snapshot = manifest.get("mode") == "one_snapshot"
    preview = manifest.get("population_preview") or {}
    data_fp = preview.get("population_fingerprint") or FULL_POPULATION
    baseline = {**baseline, "data_fingerprint": data_fp}
    current = {**current, "data_fingerprint": data_fp if one_snapshot else FULL_POPULATION}
    population_definition = None
    if one_snapshot and (baseline.get("predicate") or {}).get("feature"):
        population_definition = {"method": "split_snapshot",
            "split_feature": baseline["predicate"]["feature"],
            "expression": baseline["predicate"].get("expression") or {},
            "null_policy": baseline.get("null_policy") or "baseline"}
    elif not one_snapshot:
        population_definition = {"method": "compare_snapshots"}
    manifest.update({"manifest_schema_version": MANIFEST_VERSION,
        "population_method": {"selected": "split_snapshot" if one_snapshot else "compare_snapshots",
            "options": {"split_snapshot": {"available": True},
                "compare_snapshots": {"available": bool([x for x in options if x["available"]]), "snapshots": options}}},
        "baseline": baseline, "current": current, "baseline_table": table,
        "current_table": table if one_snapshot else manifest.get("current_table") or table,
        "population_ready": bool(population_definition), "population_definition": population_definition,
        "governed_context": context,
        "target_choice": {"status": "pending", "mode": None, "saved_target": context["target"].get("column")},
        "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"},
        "selected_features": [], "recommended_features": [], "feature_metadata": [],
        "split_feature_overrides": [], "feature_eligibility_overrides": [],
        "schema_difference_confirmed": False,
        "frozen_bins": {}, "bin_drafts": {}, "bindings": context["provenance"], "engine_version": ENGINE_VERSION,
        "methodology_version": "frozen_bins_psi_v3"})
    _refresh(manifest); db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return manifest


def _persist(run_id: str, manifest: dict[str, Any], kind: str, patch: dict[str, Any], actor: str,
             *, reuse_feature_evidence: bool = False) -> dict[str, Any]:
    persisted = kind if kind == "threshold_tune" else ("scope_exclusion" if kind in {
        "scope_selection", "population_split", "population_method", "split_feature_override",
        "feature_eligibility_override", "binning_source_choice", "binning_source_reset"} else "default_applied")
    decision = record_decision(run_id, persisted, {"psi_kind": kind, **patch}, actor)
    manifest["operator_decisions"] = [*manifest.get("operator_decisions", []), {**decision, "kind": kind, "payload": patch}]
    _refresh(manifest, reuse_feature_evidence=reuse_feature_evidence)
    manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest}); return manifest


def patch_manifest(run_id: str, patch: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    from .manifest import get_run
    run = get_run(run_id)
    if run["diagnostic_id"] != DIAGNOSTIC_ID: raise ManifestError("not a PSI manifest")
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    manifest, kind = dict(run["manifest_json"]), patch.get("kind")
    if kind == "target_choice":
        value = patch.get("value") or {}; mode = value.get("mode") if isinstance(value, dict) else value
        if mode not in {"saved_target", "target_free"}: raise ManifestError("choose saved_target or target_free")
        if mode == "saved_target" and manifest["governed_context"]["target"]["state"] != "present":
            raise ManifestError("no resolved saved target is available")
        saved_target = manifest["governed_context"]["target"].get("column")
        target_route: dict[str, Any] = {}
        if mode == "saved_target":
            try:
                target_frame = _baseline_frame(manifest, [saved_target])
                effective_type, effective_positive = resolve_target_route(
                    target_frame[saved_target], "auto", value.get("positive_class"),
                )
            except ValueError as exc:
                raise ManifestError(str(exc)) from exc
            target_route = {
                "target_type": effective_type,
                "positive_class": effective_positive,
                "target_fingerprint": target_fingerprint(
                    saved_target, target_type=effective_type,
                    positive_class=effective_positive,
                ),
            }
        manifest["target_choice"] = {"status": "confirmed", "mode": mode,
            "saved_target": saved_target, **target_route,
            "scope": "psi_run_only", "does_not_modify_data_sourcing": True}
        manifest["frozen_bins"], manifest["bin_drafts"], manifest["selected_features"] = {}, {}, []
        manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
    elif kind == "population_method":
        value = patch.get("value") or {}; method = value.get("method")
        if method is None:
            base = _snapshot(manifest["baseline"]["snapshot"])
            manifest.update({"mode": "one_snapshot", "current": PopulationRef("current", base, {}).to_dict(),
                "current_table": manifest.get("baseline_table") or manifest["table"],
                "population_ready": False, "population_definition": None, "population_preview": None,
                "frozen_bins": {}, "bin_drafts": {}, "selected_features": [],
                "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"}})
        elif method == "split_snapshot":
            base = _snapshot(manifest["baseline"]["snapshot"])
            manifest.update({"mode": "one_snapshot", "current": PopulationRef("current", base, {}).to_dict(),
                "current_table": manifest.get("baseline_table") or manifest["table"],
                "population_ready": False, "population_definition": None, "population_preview": None,
                "frozen_bins": {}, "bin_drafts": {}, "selected_features": [],
                "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"}})
        elif method == "compare_snapshots":
            current_id = value.get("current_snapshot_id")
            base = manifest["baseline"]["snapshot"]
            if not current_id:
                base_ref = _snapshot(base)
                manifest.update({"mode": "comparison_pending",
                    "current": PopulationRef("current", base_ref, {}).to_dict(),
                    "current_table": manifest.get("baseline_table") or manifest["table"],
                    "population_ready": False, "population_definition": None, "population_preview": None,
                    "frozen_bins": {}, "bin_drafts": {}, "selected_features": [],
                    "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"}})
                manifest["population_method"]["selected"] = method
                return _persist(run_id, manifest, kind, patch, actor)
            current = SnapshotLoader().reference(current_id)
            baseline_ref = _snapshot(base); baseline_table = manifest.get("baseline_table") or manifest["table"]
            match = _best_table_match(baseline_ref, baseline_table, current)
            requested_table = value.get("current_table")
            if requested_table:
                comparison = _comparison(baseline_ref.snapshot_id, current_id, baseline_table, requested_table)
                match = {"available": bool(comparison["compatible"]), "current_table": requested_table}
            if match.get("current_table") is None: raise ManifestError("current snapshot has no governed table")
            manifest.update({"mode": "two_snapshot", "current": PopulationRef("current", current, {}).to_dict(),
                "current_table": match["current_table"],
                "population_ready": bool(match["available"]),
                "population_definition": {"method": "compare_snapshots"},
                "frozen_bins": {}, "bin_drafts": {}, "selected_features": [],
                "binning_source_choice": {"status": "pending", "mode": None, "scope": "psi_run_only"}})
        else: raise ManifestError("unsupported population method")
        manifest["population_method"]["selected"] = method
    elif kind == "population_split":
        if manifest["mode"] != "one_snapshot": raise ManifestError("population split applies only to one-snapshot mode")
        feature, expression = patch.get("feature"), patch.get("value")
        rows = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])
        if feature not in rows or not isinstance(expression, dict): raise ManifestError("select a governed split feature and predicate")
        from .engines.population_stability.population import split_population
        frame = SnapshotLoader().load_table(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"], columns=[feature])
        raw = expression.get("value")
        if raw is not None and expression.get("operator") != "in":
            try: expression = {**expression, "value": int(raw) if frame[feature].dtype.kind in "iu" else float(raw) if frame[feature].dtype.kind == "f" else str(raw)}
            except Exception: pass
        # Null rows are review evidence and remain in the baseline population.
        # Downstream PSI bin definitions already require a separate missing bin.
        null_policy = "baseline"
        special_policy = patch.get("special_policy") or "exclude"
        row = rows[feature]
        special_values = list(row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed") else []
        preview = split_population(frame, feature, expression, null_policy=null_policy,
                                   special_values=special_values, special_policy=special_policy)
        total = max(int(len(frame)), 1)
        baseline_share = preview["baseline_count"] / total
        current_share = preview["current_count"] / total
        warnings = []
        if min(preview["baseline_count"], preview["current_count"]) < 30:
            warnings.append("One population has fewer than 30 rows; PSI may be unstable.")
        if min(baseline_share, current_share) < 0.10:
            warnings.append("The split is highly imbalanced; one population contains less than 10% of rows.")
        predicate = {"feature": feature, "expression": expression, "special_values": special_values,
                     "special_policy": special_policy}; base = _snapshot(manifest["baseline"]["snapshot"])
        manifest["baseline"] = PopulationRef("baseline", base, predicate, null_policy, preview["population_fingerprint"]).to_dict()
        manifest["current"] = PopulationRef("current", base, {"complement_of": predicate}, null_policy, preview["population_fingerprint"]).to_dict()
        manifest["population_method"]["selected"] = "split_snapshot"
        manifest["population_ready"] = True
        metadata = next((item for item in manifest.get("feature_metadata", []) if item["column"] == feature), {})
        guidance = metadata.get("split_guidance") or {}
        manifest["population_definition"] = {"method": "split_snapshot", "split_feature": feature,
                                               "expression": expression, "null_policy": null_policy,
                                               "special_values": special_values, "special_policy": special_policy,
                                               "strategy": guidance.get("strategy"),
                                               "profile_artifact_id": (guidance.get("source") or {}).get("artifact_id")}
        manifest["population_preview"] = {key: preview[key] for key in (
            "baseline_count", "current_count", "excluded_count", "null_count", "special_count", "population_fingerprint")}
        manifest["population_preview"].update({"total_count": int(len(frame)),
            "baseline_share": round(baseline_share, 6), "current_share": round(current_share, 6),
            "warnings": warnings})
        manifest["frozen_bins"], manifest["bin_drafts"], manifest["selected_features"] = {}, {}, []
        manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
    elif kind == "scope_selection":
        allowed = {row["column"] for row in manifest["feature_metadata"] if row["bin_route"]["readiness"] != "excluded"}
        selected = list(dict.fromkeys(patch.get("features") or []))
        if not selected or not set(selected) <= allowed: raise ManifestError("select eligible PSI variables")
        if selected != manifest.get("selected_features", []):
            manifest["frozen_bins"] = {}
            manifest["bin_drafts"] = {}
            manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
        manifest["selected_features"] = selected
    elif kind == "binning_source_choice":
        value = patch.get("value") or {}; mode = value.get("mode") if isinstance(value, dict) else value
        if mode not in {"repository", "generate_new"}:
            raise ManifestError("choose repository or generate_new")
        if not manifest.get("selected_features"):
            raise ManifestError("select PSI variables before choosing a binning source")
        previous_mode = manifest.get("binning_source_choice", {}).get("mode")
        changing_source = bool(previous_mode and previous_mode != mode)
        has_work = bool(manifest.get("frozen_bins") or manifest.get("bin_drafts") or _has_repository_matches(manifest))
        confirm_clear = bool(value.get("confirm_clear")) if isinstance(value, dict) else False
        if changing_source and has_work and not confirm_clear:
            raise ManifestError("changing the binning source requires confirmation that this PSI setup's binning work will be cleared")
        if changing_source and has_work:
            manifest["frozen_bins"] = {}
            manifest["bin_drafts"] = {}
        manifest["binning_source_choice"] = {"status": "confirmed", "mode": mode,
            "scope": "psi_run_only", "repository_assessed": mode == "repository",
            "operator_decision": "assess_existing_assets" if mode == "repository" else "new_bins_requested",
            "prior_work_cleared": bool(changing_source and has_work)}
    elif kind == "binning_source_reset":
        if not patch.get("enabled"):
            raise ManifestError("binning reset requires explicit confirmation")
        manifest["frozen_bins"] = {}
        manifest["bin_drafts"] = {}
        manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
    elif kind == "split_feature_override":
        feature = (manifest.get("population_definition") or {}).get("split_feature")
        if patch.get("feature") != feature: raise ManifestError("override must reference the active split feature")
        values = set(manifest.get("split_feature_overrides") or [])
        if patch.get("enabled"):
            values.add(feature)
        else:
            values.discard(feature)
            if feature in set(manifest.get("selected_features") or []):
                manifest["selected_features"] = [name for name in manifest.get("selected_features", []) if name != feature]
                manifest["frozen_bins"], manifest["bin_drafts"] = {}, {}
                manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
        manifest["split_feature_overrides"] = sorted(values)
    elif kind == "feature_eligibility_override":
        feature = patch.get("feature")
        row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"],
                         manifest.get("baseline_table") or manifest["table"]).get(feature)
        if not row or _distinct(row) != 1 or _role(row) in EXCLUDED_ROLES:
            raise ManifestError("eligibility override is available only for a constant non-protected feature")
        values = set(manifest.get("feature_eligibility_overrides") or [])
        if patch.get("enabled"):
            values.add(feature)
        else:
            values.discard(feature)
            if feature in set(manifest.get("selected_features") or []):
                manifest["selected_features"] = [name for name in manifest.get("selected_features", []) if name != feature]
                manifest["frozen_bins"], manifest["bin_drafts"] = {}, {}
                manifest["binning_source_choice"] = {"status": "pending", "mode": None, "scope": "psi_run_only"}
        manifest["feature_eligibility_overrides"] = sorted(values)
    elif kind == "schema_difference_confirmation":
        if not manifest["schema_comparison"].get("target_difference"):
            raise ManifestError("there is no target schema difference to confirm")
        manifest["schema_difference_confirmed"] = bool(patch.get("enabled"))
    elif kind == "bin_assignment":
        feature, value = patch.get("feature"), patch.get("value") or {}
        reference = _validated_reference(manifest, feature, value.get("artifact_id"), bool(value.get("confirm_mismatch")))
        manifest["frozen_bins"] = {**manifest["frozen_bins"], feature: reference}
    elif kind == "threshold_tune":
        key, value = patch.get("key"), float(patch.get("value"))
        if key not in {"watch", "investigate"}: raise ManifestError("unknown PSI threshold")
        manifest["thresholds"] = {**manifest["thresholds"], key: value}
        manifest["threshold_sources"] = {**manifest["threshold_sources"], key: "operator_override"}
    else: raise ManifestError("unsupported PSI manifest decision")
    return _persist(run_id, manifest, kind, patch, actor,
                    reuse_feature_evidence=kind == "target_choice")


def _validated_reference(manifest: dict[str, Any], feature: str, artifact_id: str | None,
                         confirm_mismatch: bool = False) -> dict[str, Any]:
    if not feature or not artifact_id: raise ManifestError("bin assignment requires a feature and artifact")
    repo = AnalysisArtifactRepository()
    try: meta, payload = repo.get(artifact_id)
    except Exception as exc: raise ManifestError("bin artifact is missing or corrupt") from exc
    if meta.feature != feature or meta.status != "active": raise ManifestError("bin artifact is inactive or belongs to another feature")
    if not _target_compatible(manifest, meta, payload):
        raise ManifestError("bin artifact target lineage does not match this PSI run")
    if meta.artifact_type == "psi_bins":
        from .engines.population_stability.bins import validate_bin_definition
        validate_bin_definition(payload)
    elif meta.artifact_type == "coarse_bins":
        if not _reviewed(artifact_id) or not _safe_coarse(payload, feature):
            raise ManifestError("Diagnostic #2 bins must be reviewed and include out-of-range guards")
    else: raise ManifestError("unsupported bin artifact type")
    exact = meta.population_fingerprint == manifest["baseline"]["data_fingerprint"]
    if not exact and not confirm_mismatch: raise ManifestError("bin lineage differs from the baseline; explicit confirmation is required")
    return {"artifact_id": artifact_id, "artifact_type": meta.artifact_type, "payload_hash": meta.payload_hash,
            "governance_state": "frozen", "population_match": exact,
            "coarse_bin_count": len(payload.get("bins") or []) or payload.get("actual_bin_count"),
            "reuse_reason": "exact baseline match" if exact else "operator-confirmed safe lineage mismatch",
            "override_confirmed": not exact}


def _baseline_frame(manifest: dict[str, Any], columns: list[str]) -> pd.DataFrame:
    loader = SnapshotLoader(); snapshot_id, table = manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"]
    if manifest["mode"] == "two_snapshot": return loader.load_table(snapshot_id, table, columns=columns, allow_historical=True)
    definition = manifest.get("population_definition") or {}; split = definition.get("split_feature")
    frame = loader.load_table(snapshot_id, table, columns=list(dict.fromkeys([*columns, split])))
    from .engines.population_stability.population import split_population
    return split_population(frame, split, definition["expression"], null_policy=definition["null_policy"],
        special_values=definition.get("special_values"), special_policy=definition.get("special_policy", "exclude"))["baseline"]


def _portable(definition: Any, feature: str, count: int, method: str,
              population: str, actor: str) -> dict[str, Any]:
    value = definition.model_dump(mode="json") if hasattr(definition, "model_dump") else dict(definition)
    kind = "numeric" if value.get("feature_type") == "numeric" else "categorical"
    return {"payload_schema_version": 1, "governance_state": "draft", "feature": feature,
        "physical_type": "generated", "logical_type": value.get("feature_type"), "kind": kind,
        "boundaries": value.get("numeric_splits") or [],
        "groups": [{"label": f"group_{i + 1}", "values": group} for i, group in enumerate(value.get("categorical_groups") or [])],
        "boundary_semantics": "right_closed" if kind == "numeric" else "exact_match", "missing_bin": True,
        "special_value_bins": [{"label": label, "values": values} for label, values in sorted((value.get("special_values") or {}).items())],
        "unseen_category_policy": "unseen_bin", "underflow_guard": kind == "numeric", "overflow_guard": kind == "numeric",
        "requested_bin_count": count, "actual_bin_count": count, "creation_methodology": method,
        "source_population_fingerprint": population, "source_artifact_references": [], "creator": actor,
        "reviewer": None, "review_timestamp": None, "supersedes_artifact_id": None}


_AUTOMATIC_BIN_WORKFLOWS = {
    "generate_target_aware", "generate_target_free", "generate_constant_guard",
}
_MATCH_PREPARATION_WORKFLOWS = {"use_universal_iv"}
_BATCH_LOCKS: dict[str, Lock] = {}
_BATCH_LOCKS_GUARD = Lock()


def _batch_lock(run_id: str) -> Lock:
    with _BATCH_LOCKS_GUARD:
        return _BATCH_LOCKS.setdefault(run_id, Lock())


def _special_values(row: dict[str, Any]) -> dict[str, list[Any]]:
    if not row.get("missing_codes_confirmed"):
        return {}
    return {f"Special value {value}": [value]
            for value in (row.get("missing_value_codes_json") or [])}


def _generated_payload(manifest: dict[str, Any], metadata: dict[str, Any],
                       row: dict[str, Any], frame: pd.DataFrame, actor: str,
                       target_type: str | None = None) -> tuple[dict[str, Any], str | None]:
    """Build one draft from an already-loaded Baseline frame (no writes)."""
    from .engines.population_stability.bins import create_draft_bins

    feature = metadata["column"]
    route = metadata["bin_route"]
    specials = _special_values(row)
    target_fp = None
    if route["workflow"] == "generate_constant_guard":
        payload = create_draft_bins(frame[feature].astype("string"), feature, creator=actor,
            source_population_fingerprint=manifest["baseline"]["data_fingerprint"], special_values=specials)
        payload["physical_type"] = row.get("data_type")
        payload["logical_type"] = _logical(row)
        payload["creation_methodology"] = "constant_baseline_exact_value_guard"
    elif manifest["target_choice"]["mode"] == "saved_target":
        from .engines.feature_target_separation.information_value import BinningConstraints, fit_feature_binning
        target = manifest["target_choice"]["saved_target"]
        result = fit_feature_binning(frame[feature], frame[target], feature_name=feature, target_name=target,
            target_type=target_type, feature_type="numeric" if metadata["feature_kind"] in {"numeric", "date"} else "categorical",
            positive_class=manifest["target_choice"].get("positive_class"),
            special_values=specials, constraints=BinningConstraints(create_out_of_range_guard_bins=True))
        payload = _portable(result.coarse_definition, feature, len(result.coarse_bins), "diagnostic_2_target_aware_iv",
                            manifest["baseline"]["data_fingerprint"], actor)
        fine_rows = [row.model_dump(mode="json") for row in result.fine_bins]
        coarse_rows = [row.model_dump(mode="json") for row in result.coarse_bins]
        payload.update({"target": target, "target_type": target_type,
                        "positive_class": manifest["target_choice"].get("positive_class"),
                        "definition": result.coarse_definition.model_dump(mode="json"), "bins": coarse_rows,
                        "iv_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                        "baseline_applicability": {
                            "population_fingerprint": manifest["baseline"]["data_fingerprint"],
                            "fine_definition": result.fine_definition.model_dump(mode="json"),
                            "coarse_definition": result.coarse_definition.model_dump(mode="json"),
                            "fine_bins": fine_rows, "coarse_bins": coarse_rows,
                            "fine_metrics": [metric.model_dump(mode="json") for metric in result.fine_metrics],
                            "coarse_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                            "coarse_groups": result.coarse_groups, "evaluated_rows": len(frame),
                            "fine_count": len(fine_rows), "coarse_count": len(coarse_rows),
                            "reconciled": True, "warnings": list(result.warnings),
                            "evaluation_method": "generated_baseline_fine_aggregation_v1"}})
        target_fp = _target_fingerprint(manifest)
    else:
        payload = create_draft_bins(frame[feature], feature, creator=actor,
                                    source_population_fingerprint=manifest["baseline"]["data_fingerprint"],
                                    special_values=specials)
    return payload, target_fp


def _prepared_match_payload(manifest: dict[str, Any], metadata: dict[str, Any],
                            frame: pd.DataFrame, actor: str,
                            target_type: str | None = None
                            ) -> tuple[dict[str, Any], str | None, tuple[str, ...]]:
    """Prepare a workflow-local coarse draft from matched repository evidence."""
    from .engines.feature_target_separation.information_value.models import BinDefinition

    feature = metadata["column"]
    route = metadata["bin_route"]
    source = route["artifact"]["artifact_id"]
    meta, matched = AnalysisArtifactRepository().get(source)
    target = manifest["target_choice"].get("saved_target") \
        if manifest["target_choice"].get("mode") == "saved_target" else None

    if route["workflow"] == "build_coarse_from_fine":
        if not target or not target_type:
            raise ManifestError("Diagnostic 2 coarse optimization requires a confirmed saved target")
        from .engines.feature_target_separation.information_value import (
            fit_coarse_from_fine_binning,
        )
        fine_definition = BinDefinition.model_validate(matched["definition"])
        constraints = fine_definition.constraints.model_copy(update={
            "min_bins": min(fine_definition.constraints.min_bins, 8),
            "max_bins": min(fine_definition.constraints.max_bins, 8),
            "create_out_of_range_guard_bins": fine_definition.feature_type == "numeric",
        })
        result = fit_coarse_from_fine_binning(
            frame[feature], frame[target], fine_definition,
            target_name=target, target_type=target_type,
            positive_class=manifest["target_choice"].get("positive_class"),
            constraints=constraints,
        )
        fine_rows = [row.model_dump(mode="json") for row in result.fine_bins]
        coarse_rows = [row.model_dump(mode="json") for row in result.coarse_bins]
        payload = _portable(
            result.coarse_definition, feature, len(result.coarse_bins),
            "diagnostic_2_coarse_from_matched_fine",
            manifest["baseline"]["data_fingerprint"], actor,
        )
        payload.update({
            "target": target,
            "target_type": target_type,
            "positive_class": manifest["target_choice"].get("positive_class"),
            "definition": result.coarse_definition.model_dump(mode="json"),
            "bins": coarse_rows,
            "iv_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
            "baseline_applicability": {
                "population_fingerprint": manifest["baseline"]["data_fingerprint"],
                "source_artifact_id": source,
                "source_payload_hash": meta.payload_hash,
                "fine_artifact_id": source,
                "fine_payload_hash": meta.payload_hash,
                "fine_definition": result.fine_definition.model_dump(mode="json"),
                "coarse_definition": result.coarse_definition.model_dump(mode="json"),
                "fine_bins": fine_rows,
                "coarse_bins": coarse_rows,
                "fine_metrics": [metric.model_dump(mode="json") for metric in result.fine_metrics],
                "coarse_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                "coarse_groups": result.coarse_groups,
                "evaluated_rows": len(frame),
                "fine_count": len(fine_rows),
                "coarse_count": len(coarse_rows),
                "reconciled": (sum(row["rows"] for row in fine_rows)
                               == sum(row["rows"] for row in coarse_rows) == len(frame)),
                "warnings": list(result.warnings),
                "evaluation_method": "diagnostic_2_coarse_optimizer_from_cached_fine_v1",
            },
        })
    elif route["workflow"] == "use_universal_iv":
        fine_source = _fine_source(AnalysisArtifactRepository(), meta)
        if not fine_source:
            raise ManifestError("the promoted universal coarse definition has no readable fine foundation")
        fine_meta, fine_payload = fine_source
        fine_rows = fine_payload.get("bins") or []
        coarse_rows = matched.get("bins") or []
        fine_total = sum(int(row.get("rows") or 0) for row in fine_rows)
        coarse_total = sum(int(row.get("rows") or 0) for row in coarse_rows)
        applicability = {
            "population_fingerprint": manifest["baseline"]["data_fingerprint"],
            "source_artifact_id": source, "source_payload_hash": meta.payload_hash,
            "fine_artifact_id": fine_meta.artifact_id, "fine_payload_hash": fine_meta.payload_hash,
            "fine_definition": fine_payload.get("definition"), "fine_bins": fine_rows,
            "fine_metrics": fine_payload.get("metrics") or [],
            "coarse_definition": matched.get("definition"), "coarse_bins": coarse_rows,
            "coarse_metrics": matched.get("metrics") or [],
            "coarse_groups": matched.get("coarse_groups") or [],
            "fine_count": len(fine_rows), "coarse_count": len(coarse_rows),
            "evaluated_rows": coarse_total, "reconciled": fine_total == coarse_total and coarse_total > 0,
            "warnings": matched.get("warnings") or [],
            "evaluation_method": "exact_universal_cached_aggregates_v1",
        }
        if not applicability["reconciled"]:
            raise ManifestError("promoted universal fine/coarse counts do not reconcile")
        payload = _portable(matched["definition"], feature, len(coarse_rows),
                            "exact_universal_diagnostic_2", manifest["baseline"]["data_fingerprint"], actor)
        payload.update({"target": target, "target_type": matched["definition"].get("target_type"),
                        "definition": matched["definition"], "bins": coarse_rows,
                        "iv_metrics": matched.get("metrics") or [],
                        "baseline_applicability": applicability})
    else:
        applicability = _matched_bin_applicability(
            manifest, feature, meta, matched, baseline_frame=frame,
        )
        definition = applicability.get("coarse_definition") or matched.get("definition") or {}
        payload = _portable(
            definition, feature,
            len(applicability.get("coarse_bins") or matched.get("bins") or []),
            "existing_diagnostic_2_candidate",
            manifest["baseline"]["data_fingerprint"], actor,
        )
        payload.update({"definition": definition,
                        "bins": applicability.get("coarse_bins") or [],
                        "baseline_applicability": applicability})

    payload["source_artifact_references"] = [source]
    return payload, meta.target_fingerprint, (source,)


def _save_draft(run_id: str, manifest: dict[str, Any], feature: str, payload: dict[str, Any],
                actor: str, *, target_fp: str | None = None, source_ids: tuple[str, ...] = (),
                resolution: str = "no_applicable_repository_match",
                source_workflow: str | None = None) -> dict[str, Any]:
    """Persist one draft without performing the expensive full manifest refresh."""
    payload["methodology_fingerprint"] = stable_fingerprint({"method": payload["creation_methodology"], "target_choice": manifest["target_choice"]})
    snapshot = manifest["baseline"]["snapshot"]
    repo = AnalysisArtifactRepository()
    psi_source_ids = source_ids
    applicability = payload.get("baseline_applicability") or {}
    if not source_ids and target_fp and applicability.get("fine_definition"):
        # A target-aware PSI run is a first-class Diagnostic 2 producer.  Save
        # its immutable fine/coarse/IV evidence as diagnostic-specific.  An
        # explicit promotion action can later make a full-Baseline definition
        # the active universal definition.
        d2_base = {
            "asset_id": snapshot["asset_id"], "snapshot_id": snapshot["snapshot_id"],
            "population_fingerprint": manifest["baseline"]["data_fingerprint"],
            "target_fingerprint": target_fp, "methodology_fingerprint": payload["methodology_fingerprint"],
            "scope": "diagnostic_local", "owner_id": "diagnostic:2", "feature": feature,
            "table": manifest.get("baseline_table") or manifest["table"], "run_id": run_id,
            "created_by": actor,
        }
        fine = repo.save({
            "definition": applicability["fine_definition"],
            "bins": applicability.get("fine_bins") or [],
            "metrics": applicability.get("fine_metrics") or [],
        }, artifact_type="fine_bins", **d2_base).artifact
        coarse = repo.save({
            "definition": applicability["coarse_definition"],
            "bins": applicability.get("coarse_bins") or [],
            "metrics": applicability.get("coarse_metrics") or [],
            "warnings": applicability.get("warnings") or [],
            "coarse_groups": applicability.get("coarse_groups") or [],
        }, artifact_type="coarse_bins", source_artifact_ids=(fine.artifact_id,), **d2_base).artifact
        iv = repo.save({
            "feature": feature, "target": payload.get("target"),
            "feature_type": applicability["fine_definition"].get("feature_type"),
            "target_type": payload.get("target_type"),
            "positive_class": payload.get("positive_class"),
            "rows_evaluated": applicability.get("evaluated_rows") or sum(
                int(row.get("rows") or 0) for row in applicability.get("fine_bins") or []),
            "fine_definition": applicability["fine_definition"],
            "fine_bins": applicability.get("fine_bins") or [],
            "fine_metrics": applicability.get("fine_metrics") or [],
            "coarse_definition": applicability["coarse_definition"],
            "coarse_bins": applicability.get("coarse_bins") or [],
            "coarse_metrics": applicability.get("coarse_metrics") or [],
            "coarse_groups": applicability.get("coarse_groups") or [],
            "warnings": applicability.get("warnings") or [],
            "binning_profile": {},
        }, artifact_type="iv", source_artifact_ids=(fine.artifact_id, coarse.artifact_id), **d2_base).artifact
        payload["diagnostic_2_artifact_ids"] = {
            "fine_bins": fine.artifact_id, "coarse_bins": coarse.artifact_id, "iv": iv.artifact_id,
        }
        payload["universal_eligibility"] = (
            "candidate" if manifest.get("mode") == "two_snapshot" else "ineligible_split_population"
        )
        psi_source_ids = (coarse.artifact_id,)
        payload["source_artifact_references"] = [coarse.artifact_id]
    if source_ids:
        source_types = {repo.get(source_id)[0].artifact_type for source_id in source_ids}
        if "fine_bins" in source_types:
            if not target_fp:
                raise ManifestError("a target fingerprint is required for a coarse artifact")
            applicability = payload.get("baseline_applicability") or {}
            coarse = repo.save({
                "definition": payload.get("definition"),
                "bins": payload.get("bins") or [],
                "metrics": payload.get("iv_metrics") or applicability.get("coarse_metrics") or [],
                "warnings": applicability.get("warnings") or [],
                "coarse_groups": applicability.get("coarse_groups") or [],
            }, artifact_type="coarse_bins", asset_id=snapshot["asset_id"],
                snapshot_id=snapshot["snapshot_id"],
                population_fingerprint=manifest["baseline"]["data_fingerprint"],
                target_fingerprint=target_fp,
                methodology_fingerprint=payload["methodology_fingerprint"],
                scope="workflow_local", owner_id="diagnostic:2", feature=feature,
                table=manifest["table"], source_artifact_ids=source_ids,
                identity_inputs={"governance_state": "draft", "manifest_run_id": run_id,
                                 "purpose": "psi_coarse_review_proposal"},
                run_id=run_id, created_by=actor).artifact
            psi_source_ids = (coarse.artifact_id,)
            payload["source_artifact_references"] = list(dict.fromkeys([
                *(payload.get("source_artifact_references") or []), coarse.artifact_id,
            ]))
    payload["payload_fingerprint"] = stable_fingerprint({
        key: value for key, value in payload.items() if key != "payload_fingerprint"
    })
    outcome = repo.save(payload, artifact_type="psi_bins", asset_id=snapshot["asset_id"],
        snapshot_id=snapshot["snapshot_id"], population_fingerprint=manifest["baseline"]["data_fingerprint"],
        target_fingerprint=target_fp, methodology_fingerprint=payload["methodology_fingerprint"], scope="diagnostic_local",
        owner_id="diagnostic:14", feature=feature, table=manifest["table"], source_artifact_ids=psi_source_ids,
        identity_inputs={"governance_state": "draft", "manifest_run_id": run_id}, run_id=run_id, created_by=actor)
    manifest["bin_drafts"] = {**manifest.get("bin_drafts", {}), feature: {
        "artifact_id": outcome.artifact.artifact_id, "artifact_type": "psi_bins",
        "payload_hash": outcome.artifact.payload_hash, "governance_state": "draft",
        "coarse_bin_count": len(payload.get("bins") or []) or payload.get("actual_bin_count"),
        "resolution": resolution, "source_workflow": source_workflow}}
    manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return {"artifact": outcome.artifact.to_dict(), "payload": payload, "outcome": outcome.outcome}


def create_bin_draft(run_id: str, feature: str, actor: str = "system", *,
                     ignore_repository_match: bool = False) -> dict[str, Any]:
    from .manifest import get_run
    from .engines.population_stability.bins import create_draft_bins
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    metadata = next((row for row in manifest["feature_metadata"] if row["column"] == feature), None)
    if feature not in set(manifest.get("selected_features") or []):
        raise ManifestError("select the feature before generating bins")
    if manifest.get("binning_source_choice", {}).get("status") != "confirmed":
        raise ManifestError("choose a binning source before generating bins")
    if not metadata or metadata["bin_route"]["readiness"] not in {
            "generation_available", "review_required", "decision_required", "confirmation_required"}:
        raise ManifestError("feature is not eligible for bin generation or review")
    source_ids: tuple[str, ...] = (); target_fp = None; route = metadata["bin_route"]
    if not ignore_repository_match and route["workflow"] in _MATCH_PREPARATION_WORKFLOWS:
        target = manifest["target_choice"].get("saved_target") \
            if manifest["target_choice"].get("mode") == "saved_target" else None
        frame = _baseline_frame(manifest, [feature, *([target] if target else [])])
        target_type = None
        if target:
            frame = frame.dropna(subset=[target])
            unique = int(frame[target].nunique())
            target_type = "binary" if unique == 2 else "continuous" if pd.api.types.is_numeric_dtype(frame[target]) else "multinomial"
        payload, target_fp, source_ids = _prepared_match_payload(
            manifest, metadata, frame, actor, target_type,
        )
    elif route["workflow"] == "generate_constant_guard":
        frame = _baseline_frame(manifest, [feature])
        row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
        specials = {f"Special value {value}": [value] for value in (row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed")}
        payload = create_draft_bins(frame[feature].astype("string"), feature, creator=actor,
            source_population_fingerprint=manifest["baseline"]["data_fingerprint"], special_values=specials)
        payload["physical_type"] = row.get("data_type")
        payload["logical_type"] = _logical(row)
        payload["creation_methodology"] = "constant_baseline_exact_value_guard"
    elif manifest["target_choice"]["mode"] == "saved_target":
        from .engines.feature_target_separation.information_value import BinningConstraints, fit_feature_binning
        target = manifest["target_choice"]["saved_target"]
        frame = _baseline_frame(manifest, [feature, target]).dropna(subset=[target]); unique = int(frame[target].nunique())
        target_type = "binary" if unique == 2 else "continuous" if pd.api.types.is_numeric_dtype(frame[target]) else "multinomial"
        row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
        specials = {f"Special value {value}": [value] for value in (row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed")}
        result = fit_feature_binning(frame[feature], frame[target], feature_name=feature, target_name=target,
            target_type=target_type, feature_type="numeric" if metadata["feature_kind"] in {"numeric", "date"} else "categorical",
            positive_class=manifest["target_choice"].get("positive_class"),
            special_values=specials, constraints=BinningConstraints(create_out_of_range_guard_bins=True))
        payload = _portable(result.coarse_definition, feature, len(result.coarse_bins), "diagnostic_2_target_aware_iv",
                            manifest["baseline"]["data_fingerprint"], actor)
        fine_rows = [row.model_dump(mode="json") for row in result.fine_bins]
        coarse_rows = [row.model_dump(mode="json") for row in result.coarse_bins]
        payload.update({"target": target, "target_type": target_type,
                        "positive_class": manifest["target_choice"].get("positive_class"),
                        "definition": result.coarse_definition.model_dump(mode="json"), "bins": coarse_rows,
                        "iv_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                        "baseline_applicability": {
                            "population_fingerprint": manifest["baseline"]["data_fingerprint"],
                            "fine_definition": result.fine_definition.model_dump(mode="json"),
                            "coarse_definition": result.coarse_definition.model_dump(mode="json"),
                            "fine_bins": fine_rows, "coarse_bins": coarse_rows,
                            "fine_metrics": [metric.model_dump(mode="json") for metric in result.fine_metrics],
                            "coarse_metrics": [metric.model_dump(mode="json") for metric in result.coarse_metrics],
                            "coarse_groups": result.coarse_groups, "evaluated_rows": len(frame),
                            "fine_count": len(fine_rows), "coarse_count": len(coarse_rows),
                            "reconciled": True, "warnings": list(result.warnings),
                            "evaluation_method": "generated_baseline_fine_aggregation_v1"}})
        target_fp = _target_fingerprint(manifest)
    else:
        frame = _baseline_frame(manifest, [feature])
        row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
        specials = {f"Special value {value}": [value] for value in (row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed")}
        payload = create_draft_bins(frame[feature], feature, creator=actor,
                                    source_population_fingerprint=manifest["baseline"]["data_fingerprint"],
                                    special_values=specials)
    resolution = "universal_iv_prepared_for_psi" if route.get("workflow") == "use_universal_iv" else "automatic_bins_generated"
    result = _save_draft(run_id, manifest, feature, payload, actor, target_fp=target_fp,
                         source_ids=source_ids, resolution=resolution,
                         source_workflow=route.get("workflow"))
    _refresh(manifest); manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return result


def generate_bin_draft_events(run_id: str, actor: str = "system", *, max_workers: int = 3):
    """Generate outstanding automatic drafts with one shared Baseline read."""
    from .manifest import get_run

    started = monotonic()
    lock = _batch_lock(run_id)
    if not lock.acquire(blocking=False):
        yield {"phase": "error", "message": "PSI draft generation is already running for this workflow."}
        return
    try:
        run = get_run(run_id)
        manifest = run["manifest_json"]
        if run["status"] != DRAFT:
            raise ManifestError("frozen manifests are immutable")
        if manifest.get("binning_source_choice", {}).get("status") != "confirmed":
            raise ManifestError("choose a binning source before generating bins")
        selected = set(manifest.get("selected_features") or [])
        metadata_by_name = {row["column"]: row for row in manifest.get("feature_metadata", [])}
        batch_workflows = _AUTOMATIC_BIN_WORKFLOWS | _MATCH_PREPARATION_WORKFLOWS
        pending = [name for name in manifest.get("selected_features", [])
                   if name not in manifest.get("bin_drafts", {})
                   and name not in manifest.get("frozen_bins", {})
                   and metadata_by_name.get(name, {}).get("bin_route", {}).get("workflow") in batch_workflows]
        completed = sum(1 for name, draft in (manifest.get("bin_drafts") or {}).items()
                        if name in selected and draft.get("source_workflow") in batch_workflows)
        total = completed + len(pending)
        worker_count = min(max(1, max_workers), max(1, len(pending)))
        target_aware = any(
            metadata_by_name[name]["bin_route"]["workflow"] == "generate_target_aware"
            for name in pending
        )
        optimization_profile: dict[str, Any] = {
            "mode": "deterministic", "solver_time_limit_seconds": None,
            "max_workers": worker_count,
        }
        if target_aware:
            from .engines.feature_target_separation.information_value import BinningConstraints
            governed_constraints = BinningConstraints()
            optimization_profile = {
                "mode": governed_constraints.execution_mode,
                "solver_time_limit_seconds": governed_constraints.solver_time_limit_seconds,
                "max_workers": worker_count,
            }
        yield {"phase": "start", "done": completed, "total": total, "pending": len(pending),
               "max_workers": worker_count,
               "optimization_profile": optimization_profile,
               "thought": "Loading the Baseline once for all remaining variables."}
        if not pending:
            yield {"phase": "done", "done": completed, "total": total, "failed": 0,
                   "elapsed_seconds": round(monotonic() - started, 1)}
            return

        target = (manifest.get("target_choice") or {}).get("saved_target") \
            if manifest.get("target_choice", {}).get("mode") == "saved_target" else None
        generation_pending = [name for name in pending
                              if metadata_by_name[name]["bin_route"]["workflow"] != "use_universal_iv"]
        columns = list(dict.fromkeys([*generation_pending, *([target] if target and generation_pending else [])]))
        frame = _baseline_frame(manifest, columns) if columns else pd.DataFrame()
        target_type = None
        if target and generation_pending:
            frame = frame.dropna(subset=[target])
            unique = int(frame[target].nunique())
            target_type = "binary" if unique == 2 else "continuous" if pd.api.types.is_numeric_dtype(frame[target]) else "multinomial"
        inventory = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])
        failures: list[dict[str, str]] = []
        newly_done = 0
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="psi-bin") as pool:
            futures = {}
            for name in pending:
                route = metadata_by_name[name]["bin_route"]
                if route["workflow"] in _MATCH_PREPARATION_WORKFLOWS:
                    future = pool.submit(_prepared_match_payload, manifest, metadata_by_name[name],
                                         frame, actor, target_type)
                else:
                    future = pool.submit(_generated_payload, manifest, metadata_by_name[name],
                                         inventory[name], frame, actor, target_type)
                futures[future] = name
            for future in as_completed(futures):
                feature = futures[future]
                status = "completed"
                preview: dict[str, Any]
                try:
                    route = metadata_by_name[feature]["bin_route"]
                    prepared = route.get("workflow") in _MATCH_PREPARATION_WORKFLOWS
                    if prepared:
                        payload, target_fp, source_ids = future.result()
                    else:
                        payload, target_fp = future.result()
                        source_ids = ()
                    _save_draft(run_id, manifest, feature, payload, actor, target_fp=target_fp,
                                source_ids=source_ids,
                                resolution="repository_match_prepared_for_review" if prepared
                                           else "no_applicable_repository_match",
                                source_workflow=route.get("workflow"))
                    applicability = payload.get("baseline_applicability") or {}
                    metrics = payload.get("iv_metrics") or applicability.get("coarse_metrics") or []
                    iv_metric = next((row for row in metrics if row.get("name") in {
                        "information_value", "maximum_one_vs_rest_iv",
                        "between_bin_variance_ratio",
                    }), None)
                    preview = {
                        "feature": feature,
                        "status": status,
                        "kind": payload.get("kind") or payload.get("logical_type"),
                        "fine_bin_count": applicability.get("fine_count"),
                        "coarse_bin_count": applicability.get("coarse_count")
                            or len(payload.get("bins") or []),
                        "metric_name": iv_metric.get("name") if iv_metric else None,
                        "metric_value": iv_metric.get("value") if iv_metric else None,
                        "methodology": payload.get("creation_methodology"),
                        "warnings": applicability.get("warnings") or [],
                    }
                except Exception as exc:
                    status = "failed"
                    failures.append({"feature": feature, "message": str(exc)})
                    preview = {"feature": feature, "status": status, "error": str(exc)}
                newly_done += 1
                done = completed + newly_done
                elapsed = monotonic() - started
                eta = (elapsed / newly_done) * (len(pending) - newly_done)
                yield {"phase": "progress", "done": done, "total": total, "feature": feature,
                       "status": status, "failed": len(failures), "elapsed_seconds": round(elapsed, 1),
                       "eta_seconds": round(eta, 1),
                       "preview": preview,
                       "optimization_profile": optimization_profile}
        _refresh(manifest, reuse_feature_evidence=True)
        manifest["updated_at"] = db.now_ist()
        db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
        yield {"phase": "done", "done": completed + newly_done, "total": total,
               "failed": len(failures), "failures": failures,
               "optimization_profile": optimization_profile,
               "elapsed_seconds": round(monotonic() - started, 1)}
    except Exception as exc:
        yield {"phase": "error", "message": str(exc),
               "elapsed_seconds": round(monotonic() - started, 1)}
    finally:
        lock.release()


def candidate_values(run_id: str, feature: str, limit: int = 100) -> dict[str, Any]:
    """Return bounded exact baseline values for operator grouping; never read current data."""
    from .manifest import get_run
    manifest = get_run(run_id)["manifest_json"]
    metadata = next((row for row in manifest.get("feature_metadata", []) if row["column"] == feature), None)
    if not metadata or metadata["feature_kind"] not in {"categorical", "boolean"}:
        raise ManifestError("candidate values are available only for categorical or boolean features")
    row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
    special_values = ({str(value) for value in (row.get("missing_value_codes_json") or [])}
                      if row.get("missing_codes_confirmed") else set())
    values = sorted({str(value) for value in _baseline_frame(manifest, [feature])[feature].dropna().tolist()}
                    - special_values)
    if len(values) > limit:
        raise ManifestError(f"feature has {len(values)} values; bounded inspection is limited to {limit}")
    return {"feature": feature, "distinct_count": len(values), "values": values,
            "complete": True, "source": "bounded_read_only_baseline_scan", "current_population_read": False}


def _fine_source(repo: AnalysisArtifactRepository, meta: Any) -> tuple[Any, dict[str, Any]] | None:
    candidates = [meta]
    for ref in meta.source_artifacts:
        try:
            candidates.append(repo.get(ref["artifact_id"])[0])
        except Exception:
            continue
    for candidate in candidates:
        candidate_meta, candidate_payload = repo.get(candidate.artifact_id)
        if candidate_meta.artifact_type == "fine_bins":
            return candidate_meta, candidate_payload
        applicability = candidate_payload.get("baseline_applicability") or {}
        if applicability.get("fine_definition"):
            return candidate_meta, {
                "definition": applicability["fine_definition"],
                "bins": applicability.get("fine_bins") or [],
                "metrics": applicability.get("fine_metrics") or [],
                "target": candidate_payload.get("target") or applicability["fine_definition"].get("target"),
                "target_type": candidate_payload.get("target_type") or applicability["fine_definition"].get("target_type"),
            }
        for ref in candidate.source_artifacts:
            try:
                linked_meta, linked_payload = repo.get(ref["artifact_id"])
            except Exception:
                continue
            if linked_meta.artifact_type == "fine_bins":
                return linked_meta, linked_payload
    return None


def _governed_fine_source(repo: AnalysisArtifactRepository, manifest: dict[str, Any],
                          feature: str, meta: Any) -> tuple[Any, dict[str, Any]] | None:
    """Prefer the target-compatible Diagnostic 2 IV fine foundation for target-aware PSI."""
    linked = _fine_source(repo, meta)
    if manifest.get("target_choice", {}).get("mode") != "saved_target":
        return linked

    def compatible(value: tuple[Any, dict[str, Any]] | None) -> bool:
        if not value:
            return False
        candidate_meta, payload = value
        definition = payload.get("definition") or {}
        return (definition.get("feature_type") == "numeric"
                and _target_compatible(manifest, candidate_meta, payload))

    if compatible(linked):
        return linked
    baseline = manifest["baseline"]
    candidates = repo.list(asset_id=baseline["snapshot"]["asset_id"], artifact_type="fine_bins",
                           status="active", feature=feature)
    candidates.sort(key=lambda candidate: (
        candidate.snapshot_id == baseline["snapshot"]["snapshot_id"],
        candidate.population_fingerprint == baseline["data_fingerprint"],
        candidate.created_at,
    ), reverse=True)
    for candidate in candidates:
        try:
            value = repo.get(candidate.artifact_id)
        except Exception:
            continue
        if compatible(value):
            return value
    return linked


def _ordered_review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep regular IV bins in definition order rather than first-observed row order."""
    def key(row: dict[str, Any]) -> tuple[int, int | str]:
        bin_id = str(row.get("bin_id") or "")
        if bin_id.startswith("R") and bin_id[1:].isdigit():
            return 1, int(bin_id[1:])
        order = {"GUARD_LOW": 0, "GUARD_HIGH": 2, "MISSING": 3, "UNSEEN": 4}
        return order.get(bin_id, 5), bin_id
    return sorted(rows, key=key)


def _definition_review_rows(frame: pd.DataFrame, feature: str, definition_value: dict[str, Any],
                            target: str | None) -> dict[str, Any]:
    """Apply an IV definition to the PSI Baseline and return fresh aggregate rows."""
    from .engines.feature_target_separation.information_value import (
        BinningTargetSpec, apply_bin_definition, review_bin_definition,
    )
    from .engines.feature_target_separation.information_value.models import BinDefinition

    definition = BinDefinition.model_validate(definition_value)
    if target:
        reviewed = review_bin_definition(frame[feature], frame[target],
            BinningTargetSpec(column=target, target_type=definition.target_type), definition)
        return {"definition": reviewed.definition.model_dump(mode="json"),
                "bins": _ordered_review_rows([row.model_dump(mode="json") for row in reviewed.bins]),
                "metrics": [metric.model_dump(mode="json") for metric in reviewed.metrics],
                "warnings": list(reviewed.warnings)}

    assigned = apply_bin_definition(frame[feature], definition).reset_index(drop=True)
    raw = frame[feature].reset_index(drop=True)
    grouped = assigned.assign(_raw=raw).groupby(["bin_id", "label", "kind"], sort=False, observed=True)
    bins = []
    for (bin_id, label, kind), group in grouped:
        values = pd.to_numeric(group["_raw"], errors="coerce").dropna()
        bins.append({"bin_id": str(bin_id), "label": str(label), "kind": str(kind),
                     "rows": len(group), "population_share": len(group) / len(frame),
                     "feature_min": float(values.min()) if len(values) else None,
                     "feature_max": float(values.max()) if len(values) else None,
                     "feature_mean": float(values.mean()) if len(values) else None,
                     "class_counts": {}, "class_rates": {}, "class_iv": {}})
    return {"definition": definition.model_dump(mode="json"), "bins": _ordered_review_rows(bins),
            "metrics": [], "warnings": []}


def _coverage_evidence(series: pd.Series, definition: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize machine-verified value coverage without exposing a manual comparison task."""
    observed = {str(value) for value in series.dropna().astype("string").unique().tolist()}
    exception_rows = sum(int(row.get("rows") or 0) for row in rows
                         if row.get("kind") in {"unseen", "guard"})
    evidence: dict[str, Any] = {
        "observed_distinct_count": len(observed),
        "assigned_rows": len(series) - exception_rows,
        "exception_rows": exception_rows,
    }
    if definition.get("feature_type") == "categorical":
        declared = {str(value) for group in definition.get("categorical_groups") or [] for value in group}
        declared.update(str(value) for values in (definition.get("special_values") or {}).values() for value in values)
        unmatched, unused = observed - declared, declared - observed
        evidence.update({
            "covered_distinct_count": len(observed - unmatched),
            "exact_value_set": not unmatched and not unused,
            "unmatched_value_count": len(unmatched),
            "unused_library_value_count": len(unused),
        })
    else:
        evidence.update({"covered_distinct_count": len(observed) if not exception_rows else None,
                         "exact_value_set": None})
    return evidence


def _baseline_exact_categorical_foundation(frame: pd.DataFrame, feature: str,
                                           fine_value: dict[str, Any],
                                           coarse_value: dict[str, Any],
                                           target: str | None) -> dict[str, Any] | None:
    """Use one Baseline-observed value per fine bin and retain applicable coarse grouping."""
    from .engines.feature_target_separation.information_value.binning import _coarse_groups
    from .engines.feature_target_separation.information_value.models import BinDefinition

    if (fine_value.get("feature_type") != "categorical"
            and coarse_value.get("feature_type") != "categorical"):
        return None
    base = fine_value or coarse_value
    special = {str(value) for values in (base.get("special_values") or {}).values() for value in values}
    observed = sorted({str(value) for value in frame[feature].dropna().astype("string").tolist()} - special)
    if not observed or len(observed) >= 50:
        return None

    fine = {**base, "categorical_groups": [[value] for value in observed],
            "numeric_splits": [], "method": "psi_baseline_exact_categories", "solver_status": "GENERATED"}
    remaining = set(observed)
    projected_groups = []
    for group in coarse_value.get("categorical_groups") or []:
        projected = [str(value) for value in group if str(value) in remaining]
        if projected:
            projected_groups.append(projected)
            remaining.difference_update(projected)
    projected_groups.extend([[value] for value in observed if value in remaining])
    coarse = {**coarse_value, "categorical_groups": projected_groups,
              "numeric_splits": [], "method": "psi_baseline_projected_coarse",
              "solver_status": coarse_value.get("solver_status") or "REVIEWED"}
    fine_review = _definition_review_rows(frame, feature, fine, target)
    coarse_review = _definition_review_rows(frame, feature, coarse, target)
    groups = _coarse_groups(BinDefinition.model_validate(fine),
                            BinDefinition.model_validate(coarse), require_partition=True)
    return {"fine_definition": fine_review["definition"], "fine_bins": fine_review["bins"],
            "fine_metrics": fine_review["metrics"], "coarse_definition": coarse_review["definition"],
            "coarse_bins": coarse_review["bins"], "coarse_metrics": coarse_review["metrics"],
            "coarse_groups": groups, "fine_foundation_policy": "baseline_exact_values_under_50",
            "warnings": [*fine_review["warnings"], *coarse_review["warnings"]]}


def _portable_review_rows(frame: pd.DataFrame, feature: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply a portable PSI definition to Baseline for reviewed PSI-bin reuse."""
    from .engines.population_stability.engine import _assign
    assigned = _assign(frame[feature], payload)
    counts = assigned.value_counts(dropna=False)
    total = len(frame)
    return [{"bin_id": str(label), "label": str(label),
             "kind": "missing" if label == "missing" else "unseen" if label == "unseen"
                     else "guard" if str(label).startswith("guard") else "special" if str(label).startswith("special:") else "regular",
             "rows": int(count), "population_share": int(count) / total}
            for label, count in counts.items()]


def _portable_iv_definition(manifest: dict[str, Any], feature: str,
                            payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt a legacy PSI definition into the editable IV definition contract."""
    from .engines.feature_target_separation.information_value import BinningConstraints
    from .engines.feature_target_separation.information_value.models import BinDefinition
    numeric = payload.get("kind") in {"numeric", "date"}
    target = manifest["target_choice"].get("saved_target") \
        if manifest["target_choice"].get("mode") == "saved_target" else None
    target_type = payload.get("target_type") if target else None
    return BinDefinition(feature=feature, feature_type="numeric" if numeric else "categorical",
        target=target, target_type=target_type,
        numeric_splits=list(payload.get("boundaries") or []),
        numeric_transform="datetime_days" if payload.get("kind") == "date" else "identity",
        out_of_range_guard_bins=bool(payload.get("underflow_guard") or payload.get("overflow_guard")),
        categorical_groups=[list(group.get("values") or []) for group in payload.get("groups") or []],
        special_values={str(row["label"]): list(row.get("values") or [])
                        for row in payload.get("special_value_bins") or []},
        method="legacy_psi_coarse_foundation", solver_status="REVIEWED",
        constraints=BinningConstraints(create_out_of_range_guard_bins=numeric)).model_dump(mode="json")


def _matched_bin_applicability(manifest: dict[str, Any], feature: str, meta: Any,
                               payload: dict[str, Any], *,
                               baseline_frame: pd.DataFrame | None = None) -> dict[str, Any]:
    """Reapply matched definitions once and cache PSI-Baseline applicability evidence."""
    cached = payload.get("baseline_applicability") or {}
    cache_matches_payload = (cached.get("source_payload_hash") == meta.payload_hash
                             or (payload.get("definition") and cached.get("coarse_definition") == payload.get("definition")))
    trusted_cache_methods = {"vectorized_baseline_reapplication_v2",
                             "generated_baseline_fine_aggregation_v1",
                             "cached_baseline_fine_aggregation_v1",
                             "diagnostic_2_coarse_optimizer_from_cached_fine_v1"}
    if (cached.get("population_fingerprint") == manifest["baseline"]["data_fingerprint"]
            and cache_matches_payload and cached.get("reconciled")
            and cached.get("evaluation_method") in trusted_cache_methods):
        return cached

    repo = AnalysisArtifactRepository()
    fine = _governed_fine_source(repo, manifest, feature, meta)
    definitions = [("coarse", meta, payload)]
    if meta.artifact_type == "fine_bins":
        definitions = [("fine", meta, payload), ("coarse", meta, payload)]
    elif fine:
        definitions.insert(0, ("fine", fine[0], fine[1]))
    target = manifest["target_choice"].get("saved_target") \
        if manifest["target_choice"].get("mode") == "saved_target" else None
    frame = baseline_frame if baseline_frame is not None else _baseline_frame(
        manifest, [feature, *([target] if target else [])]
    )
    if target and baseline_frame is None:
        frame = frame.dropna(subset=[target])
    outcome: dict[str, Any] = {}
    for role, definition_meta, definition_payload in definitions:
        if definition_payload.get("definition"):
            reviewed = _definition_review_rows(frame, feature, definition_payload["definition"], target)
            outcome[f"{role}_definition"] = reviewed["definition"]
            outcome[f"{role}_bins"] = reviewed["bins"]
            outcome[f"{role}_metrics"] = reviewed["metrics"]
            outcome.setdefault("warnings", []).extend(reviewed["warnings"])
            outcome[f"{role}_artifact_id"] = definition_meta.artifact_id
            outcome[f"{role}_payload_hash"] = definition_meta.payload_hash
        elif role == "coarse" and definition_meta.artifact_type == "psi_bins":
            definition = _portable_iv_definition(manifest, feature, definition_payload)
            if target and not definition.get("target_type"):
                unique = int(frame[target].nunique())
                definition["target_type"] = "binary" if unique == 2 else "continuous" if pd.api.types.is_numeric_dtype(frame[target]) else "multinomial"
            reviewed = _definition_review_rows(frame, feature, definition, target)
            outcome.update({"fine_definition": reviewed["definition"], "coarse_definition": reviewed["definition"],
                            "fine_bins": reviewed["bins"], "coarse_bins": reviewed["bins"],
                            "fine_metrics": reviewed["metrics"], "coarse_metrics": reviewed["metrics"],
                            "coarse_groups": [[row["bin_id"]] for row in reviewed["bins"] if row["kind"] == "regular"]})
            outcome["coarse_artifact_id"] = definition_meta.artifact_id
            outcome["coarse_payload_hash"] = definition_meta.payload_hash
    if (target and fine and outcome.get("fine_definition", {}).get("feature_type") == "numeric"
            and outcome.get("coarse_definition")):
        fine_splits = outcome["fine_definition"].get("numeric_splits") or []
        coarse_splits = outcome["coarse_definition"].get("numeric_splits") or []
        retained = []
        for split in coarse_splits:
            match = next((candidate for candidate in fine_splits if math.isclose(
                float(split), float(candidate), rel_tol=1e-9, abs_tol=1e-12)), None)
            if match is not None:
                retained.append(match)
        projected = {**outcome["coarse_definition"], "numeric_splits": sorted(set(retained)),
                     "method": "psi_coarse_projected_to_iv_fine"}
        reviewed = _definition_review_rows(frame, feature, projected, target)
        outcome.update({"coarse_definition": reviewed["definition"], "coarse_bins": reviewed["bins"],
                        "coarse_metrics": reviewed["metrics"],
                        "fine_foundation_policy": "diagnostic_2_iv_target_aware"})
        if len(retained) != len(coarse_splits):
            outcome.setdefault("warnings", []).append(
                "Coarse boundaries not present in the Diagnostic 2 IV fine foundation were removed.")
    exact_categorical = _baseline_exact_categorical_foundation(
        frame, feature, outcome.get("fine_definition") or outcome.get("coarse_definition") or {},
        outcome.get("coarse_definition") or outcome.get("fine_definition") or {}, target,
    )
    if exact_categorical:
        source_fields = {key: value for key, value in outcome.items()
                         if key.endswith("_artifact_id") or key.endswith("_payload_hash")}
        outcome.update(exact_categorical)
        outcome.update(source_fields)
    if outcome.get("fine_definition") and outcome.get("coarse_definition") and not outcome.get("coarse_groups"):
        from .engines.feature_target_separation.information_value.binning import review_cached_fine_binning
        from .engines.feature_target_separation.information_value.models import BinDefinition, BinRow
        fine_definition = BinDefinition.model_validate(outcome["fine_definition"])
        coarse_definition = BinDefinition.model_validate(outcome["coarse_definition"])
        target_type = coarse_definition.target_type or fine_definition.target_type
        if target_type:
            grouped = review_cached_fine_binning(fine_definition,
                [BinRow.model_validate(row) for row in outcome.get("fine_bins") or []],
                coarse_definition, target_type)
            outcome["coarse_groups"] = grouped.coarse_groups
    fine_total = sum(row["rows"] for row in outcome.get("fine_bins") or [])
    coarse_total = sum(row["rows"] for row in outcome.get("coarse_bins") or [])
    warnings = list(dict.fromkeys(outcome.get("warnings") or []))
    for role in ("fine", "coarse"):
        rows = outcome.get(f"{role}_bins") or []
        unseen = sum(row["rows"] for row in rows if row["kind"] == "unseen")
        guards = sum(row["rows"] for row in rows if row["kind"] == "guard")
        if unseen: warnings.append(f"{role.title()} definition assigned {unseen} Baseline rows to the unseen bin.")
        if guards: warnings.append(f"{role.title()} definition assigned {guards} Baseline rows to guard bins.")
    reconciled = coarse_total == len(frame) and (not outcome.get("fine_bins") or fine_total == coarse_total)
    if not reconciled:
        warnings.append("Fine/coarse Baseline counts do not reconcile to the evaluated population.")
    coverage_definition = outcome.get("fine_definition") or outcome.get("coarse_definition") or {}
    coverage_rows = outcome.get("fine_bins") or outcome.get("coarse_bins") or []
    coverage = _coverage_evidence(frame[feature], coverage_definition, coverage_rows)
    coverage["complete"] = reconciled and coverage["exception_rows"] == 0
    outcome.update({"population_fingerprint": manifest["baseline"]["data_fingerprint"],
                    "source_artifact_id": meta.artifact_id, "source_payload_hash": meta.payload_hash,
                    "evaluated_rows": len(frame), "fine_count": len(outcome.get("fine_bins") or []),
                    "coarse_count": len(outcome.get("coarse_bins") or []),
                    "reconciled": reconciled, "coverage": coverage,
                    "warnings": list(dict.fromkeys(warnings)),
                    "evaluation_method": "vectorized_baseline_reapplication_v2"})
    return outcome


def bin_review(run_id: str, feature: str, artifact_id: str | None = None) -> dict[str, Any]:
    """Return one selected feature's governed coarse definition and fine lineage."""
    from .manifest import get_run
    manifest = get_run(run_id)["manifest_json"]
    if feature not in set(manifest.get("selected_features") or []):
        raise ManifestError("review is available only for selected PSI variables")
    route = next((row["bin_route"] for row in manifest.get("feature_metadata", [])
                  if row["column"] == feature), {})
    reference = artifact_id or (route.get("artifact") or {}).get("artifact_id")
    if not reference:
        raise ManifestError("generate or match a bin artifact before review")
    repo = AnalysisArtifactRepository(); meta, payload = repo.get(reference)
    if meta.feature != feature or meta.asset_id != manifest["baseline"]["snapshot"]["asset_id"]:
        raise ManifestError("bin review artifact does not belong to this feature and asset")
    fine = _governed_fine_source(repo, manifest, feature, meta)
    applicability = _matched_bin_applicability(manifest, feature, meta, payload)
    review_payload = {**payload}
    if applicability.get("coarse_bins"):
        review_payload["bins"] = applicability["coarse_bins"]
    review_fine_payload = ({**fine[1],
                            "definition": applicability.get("fine_definition") or fine[1].get("definition"),
                            "bins": applicability.get("fine_bins") or fine[1].get("bins") or []}
                           if fine else {"definition": applicability.get("fine_definition"),
                                         "bins": applicability.get("fine_bins") or [],
                                         "metrics": applicability.get("fine_metrics") or []}
                           if applicability.get("fine_definition") else None)
    return {"feature": feature, "artifact": meta.to_dict(), "payload": review_payload,
            "fine_artifact": fine[0].to_dict() if fine else None,
            "fine_payload": review_fine_payload, "applicability": applicability,
            "population_match": meta.population_fingerprint == manifest["baseline"]["data_fingerprint"],
            "target_compatible": _target_compatible(manifest, meta, payload)}


def _preview_fine_revision(manifest: dict[str, Any], feature: str, source_meta: Any,
                           source_payload: dict[str, Any], definition: dict[str, Any]):
    from .engines.feature_target_separation.information_value.binning import _coarse_groups, review_cached_fine_binning
    from .engines.feature_target_separation.information_value.models import (
        BinDefinition, BinningReviewResult, BinRow,
    )
    applicability = _matched_bin_applicability(manifest, feature, source_meta, source_payload)
    fine_value = applicability.get("fine_definition")
    if not fine_value:
        raise ManifestError("the selected artifact has no compatible fine-bin foundation")
    fine_definition = BinDefinition.model_validate(fine_value)
    edited = BinDefinition.model_validate(definition)
    target_type = edited.target_type or fine_definition.target_type
    if not target_type:
        groups = _coarse_groups(fine_definition, edited, require_partition=True)
        frame = _baseline_frame(manifest, [feature])
        applied = _definition_review_rows(frame, feature, edited.model_dump(mode="json"), None)
        reviewed = BinningReviewResult(
            definition=edited.model_copy(update={"method": "baseline_applied_user_revision",
                                                  "solver_status": "USER_EDITED"}),
            bins=[BinRow.model_validate(row) for row in applied["bins"]],
            metrics=[], warnings=applied["warnings"], coarse_groups=groups,
        )
        return reviewed, applicability
    fine_rows = [BinRow.model_validate(row) for row in applicability.get("fine_bins") or []]
    if not fine_rows:
        raise ManifestError("the selected artifact has no Baseline-applied fine-bin statistics")
    reviewed = review_cached_fine_binning(fine_definition, fine_rows, edited, target_type)
    return reviewed, applicability


def preview_fine_revision(run_id: str, feature: str, artifact_id: str,
                          definition: dict[str, Any]) -> dict[str, Any]:
    """Preview a PSI coarse edit from cached Baseline fine aggregates without saving."""
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    if feature not in set(manifest.get("selected_features") or []):
        raise ManifestError("select the feature before revising bins")
    source_meta, source_payload = AnalysisArtifactRepository().get(artifact_id)
    if source_meta.feature != feature or not _target_compatible(manifest, source_meta, source_payload):
        raise ManifestError("fine-bin target lineage does not match this PSI run")
    reviewed, _applicability = _preview_fine_revision(
        manifest, feature, source_meta, source_payload, definition)
    return reviewed.model_dump(mode="json")


def revise_from_fine(run_id: str, feature: str, artifact_id: str,
                     definition: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    """Create a workflow-local PSI draft from a user-edited cached fine-bin partition."""
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    if feature not in set(manifest.get("selected_features") or []):
        raise ManifestError("select the feature before revising bins")
    repo = AnalysisArtifactRepository(); source_meta, _source_payload = repo.get(artifact_id)
    if not _target_compatible(manifest, source_meta, _source_payload):
        raise ManifestError("fine-bin target lineage does not match this PSI run")
    fine = _governed_fine_source(repo, manifest, feature, source_meta)
    fine_meta = fine[0] if fine else None
    reviewed, applicability = _preview_fine_revision(
        manifest, feature, source_meta, _source_payload, definition)
    payload = _portable(reviewed.definition, feature, len(reviewed.bins),
        "cached_fine_bin_user_revision", manifest["baseline"]["data_fingerprint"], actor)
    coarse_rows = [row.model_dump(mode="json") for row in reviewed.bins]
    revised_applicability = {**applicability, "coarse_definition": reviewed.definition.model_dump(mode="json"),
        "coarse_bins": coarse_rows, "coarse_metrics": [row.model_dump(mode="json") for row in reviewed.metrics],
        "coarse_count": len(coarse_rows), "warnings": list(reviewed.warnings), "reconciled": True,
        "evaluation_method": "cached_baseline_fine_aggregation_v1"}
    payload.update({"definition": reviewed.definition.model_dump(mode="json"), "bins": coarse_rows,
                    "review_metrics": [row.model_dump(mode="json") for row in reviewed.metrics],
                    "review_warnings": list(reviewed.warnings),
                    "source_artifact_references": [fine_meta.artifact_id if fine_meta else source_meta.artifact_id],
                    "baseline_applicability": revised_applicability})
    payload["methodology_fingerprint"] = stable_fingerprint({"method": payload["creation_methodology"], "definition": definition})
    payload["payload_fingerprint"] = stable_fingerprint({key: value for key, value in payload.items() if key != "payload_fingerprint"})
    snapshot = manifest["baseline"]["snapshot"]
    repository_source_id = applicability.get("coarse_artifact_id")
    repository_source_ids: tuple[str, ...] = ()
    if repository_source_id:
        candidate_meta, _candidate_payload = repo.get(repository_source_id)
        if candidate_meta.artifact_type == "coarse_bins":
            repository_source_ids = (repository_source_id,)
    outcome = repo.save(payload, artifact_type="psi_bins", asset_id=snapshot["asset_id"],
        snapshot_id=snapshot["snapshot_id"], population_fingerprint=manifest["baseline"]["data_fingerprint"],
        target_fingerprint=source_meta.target_fingerprint, methodology_fingerprint=payload["methodology_fingerprint"],
        scope="diagnostic_local", owner_id="diagnostic:14", feature=feature, table=manifest["table"],
        source_artifact_ids=repository_source_ids, identity_inputs={"governance_state": "draft", "manifest_run_id": run_id},
        run_id=run_id, created_by=actor)
    manifest["bin_drafts"] = {**manifest.get("bin_drafts", {}), feature: {
        "artifact_id": outcome.artifact.artifact_id, "artifact_type": "psi_bins",
        "payload_hash": outcome.artifact.payload_hash, "governance_state": "draft",
        "coarse_bin_count": len(reviewed.bins),
        "resolution": "repository_match_revised", "source_workflow": "cached_fine_revision"}}
    _refresh(manifest); manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return {"artifact": outcome.artifact.to_dict(), "payload": payload,
            "review": reviewed.model_dump(mode="json"), "outcome": outcome.outcome}


def split_feature_options(run_id: str, feature: str, limit: int = 200) -> dict[str, Any]:
    """Reusable type-aware controls backed by profile lineage and exact snapshot values."""
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    metadata = next((row for row in manifest.get("feature_metadata", []) if row["column"] == feature), None)
    if not metadata or metadata.get("role") in {"Identifier", "Target"}:
        raise ManifestError("select an eligible governed split feature")
    if limit < 1 or limit > 500:
        raise ManifestError("split option limit must be between 1 and 500")
    frame = SnapshotLoader().load_table(manifest["baseline"]["snapshot"]["snapshot_id"],
        manifest.get("baseline_table") or manifest["table"], columns=[feature], allow_historical=True)
    row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"],
                     manifest.get("baseline_table") or manifest["table"])[feature]
    specials = list(row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed") else []
    from analysis_runtime.population_guidance import exact_population_options
    return exact_population_options(frame[feature], metadata["split_guidance"],
                                    special_values=specials, limit=limit)


def create_manual_bin_draft(run_id: str, feature: str, groups: list[dict[str, Any]],
                            actor: str = "system") -> dict[str, Any]:
    """Persist an operator-defined categorical PSI draft after exact baseline validation."""
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    if feature not in set(manifest.get("selected_features") or []):
        raise ManifestError("select the feature before defining manual bins")
    if manifest.get("binning_source_choice", {}).get("status") != "confirmed":
        raise ManifestError("choose a binning source before defining manual bins")
    candidates = candidate_values(run_id, feature); observed = set(candidates["values"])
    normalized, seen = [], set()
    for index, group in enumerate(groups or []):
        label = str(group.get("label") or f"Group {index + 1}").strip()
        values = [str(value) for value in (group.get("values") or [])]
        if not label or not values: raise ManifestError("each manual group requires a label and values")
        overlap = seen & set(values)
        if overlap: raise ManifestError(f"manual groups overlap: {sorted(overlap)}")
        seen.update(values); normalized.append({"label": label, "values": values})
    if seen != observed:
        missing, unknown = sorted(observed - seen), sorted(seen - observed)
        raise ManifestError(f"manual groups must cover the exact baseline values; missing={missing[:10]}, unknown={unknown[:10]}")
    row = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
    specials = [{"label": f"Special value {value}", "values": [str(value)]} for value in
                (row.get("missing_value_codes_json") or []) if row.get("missing_codes_confirmed")]
    payload = {"payload_schema_version": 1, "governance_state": "draft", "feature": feature,
        "physical_type": row.get("data_type"), "logical_type": _logical(row), "kind": "categorical",
        "boundaries": [], "groups": normalized, "boundary_semantics": "exact_match", "missing_bin": True,
        "special_value_bins": specials, "unseen_category_policy": "unseen_bin", "underflow_guard": False,
        "overflow_guard": False, "requested_bin_count": len(normalized), "actual_bin_count": len(normalized),
        "creation_methodology": "operator_defined_baseline_groups", "source_population_fingerprint": manifest["baseline"]["data_fingerprint"],
        "source_artifact_references": [], "candidate_value_source": candidates["source"], "creator": actor,
        "reviewer": None, "review_timestamp": None, "supersedes_artifact_id": None}
    target_fp = None
    if manifest.get("target_choice", {}).get("mode") == "saved_target":
        payload["target"] = manifest["target_choice"].get("saved_target")
        payload["target_type"] = manifest["target_choice"].get("target_type")
        payload["positive_class"] = manifest["target_choice"].get("positive_class")
        target_fp = _target_fingerprint(manifest)
    payload["methodology_fingerprint"] = stable_fingerprint({"method": payload["creation_methodology"],
        "groups": normalized, "target_choice": manifest.get("target_choice")})
    payload["payload_fingerprint"] = stable_fingerprint({key: value for key, value in payload.items() if key != "payload_fingerprint"})
    snapshot = manifest["baseline"]["snapshot"]
    outcome = AnalysisArtifactRepository().save(payload, artifact_type="psi_bins", asset_id=snapshot["asset_id"],
        snapshot_id=snapshot["snapshot_id"], population_fingerprint=manifest["baseline"]["data_fingerprint"],
        target_fingerprint=target_fp, methodology_fingerprint=payload["methodology_fingerprint"], scope="diagnostic_local", owner_id="diagnostic:14",
        feature=feature, table=manifest["table"], identity_inputs={"governance_state": "draft", "manifest_run_id": run_id},
        run_id=run_id, created_by=actor)
    manifest["bin_drafts"] = {**manifest.get("bin_drafts", {}), feature: {
        "artifact_id": outcome.artifact.artifact_id, "artifact_type": "psi_bins",
        "payload_hash": outcome.artifact.payload_hash, "governance_state": "draft",
        "coarse_bin_count": payload.get("actual_bin_count"),
        "resolution": "no_applicable_repository_match" if manifest.get("binning_source_choice", {}).get("mode") == "repository" else "new_bins_requested",
        "source_workflow": "manual_grouping"}}
    _refresh(manifest); manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return {"artifact": outcome.artifact.to_dict(), "payload": payload, "outcome": outcome.outcome}


def _numeric_csv(value: str, field: str, *, allow_empty: bool = False) -> list[float]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return []
    tokens = text.split(",")
    values: list[float] = []
    for position, token in enumerate(tokens, start=1):
        candidate = token.strip()
        if not candidate:
            raise ManifestError(f"{field}: value {position} is blank; enter comma-separated numeric values only")
        try:
            number = float(candidate)
        except ValueError as exc:
            raise ManifestError(f"{field}: value {position} ({candidate!r}) is not numeric; NA, Null and Infinity are not allowed") from exc
        if not math.isfinite(number):
            raise ManifestError(f"{field}: value {position} ({candidate!r}) must be a finite numeric value")
        values.append(number)
    return values


def create_numeric_bin_override(run_id: str, feature: str, cuts: str,
                                special_values: str = "", rationale: str = "",
                                actor: str = "system") -> dict[str, Any]:
    """Create the exceptional current-PSI-only numeric coarse definition.

    Arbitrary cuts cannot be derived from cached decile/fine aggregates, so
    review deliberately performs one feature-only Baseline rescan.
    """
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT:
        raise ManifestError("frozen manifests are immutable")
    metadata = next((row for row in manifest.get("feature_metadata") or []
                     if row["column"] == feature), None)
    if not metadata or metadata.get("feature_kind") not in {"numeric", "date"}:
        raise ManifestError("numeric cut overrides are available only for numeric features")
    boundaries = _numeric_csv(cuts, "Numeric cuts")
    if boundaries != sorted(set(boundaries)):
        raise ManifestError("Numeric cuts must be unique and strictly increasing")
    additions = _numeric_csv(special_values, "Special values", allow_empty=True)
    inventory = _inventory(manifest["baseline"]["snapshot"]["snapshot_id"], manifest["table"])[feature]
    sourced = list(inventory.get("missing_value_codes_json") or []) if inventory.get("missing_codes_confirmed") else []
    specials = []
    for value in [*sourced, *additions]:
        if str(value) not in {str(existing) for existing in specials}:
            specials.append(value)
    payload = {
        "payload_schema_version": 1, "governance_state": "draft", "feature": feature,
        "physical_type": inventory.get("data_type"), "logical_type": _logical(inventory), "kind": "numeric",
        "boundaries": boundaries, "groups": [], "boundary_semantics": "right_closed",
        "missing_bin": True,
        "special_value_bins": [{"label": f"Special value {value}", "values": [value]} for value in specials],
        "unseen_category_policy": "unseen_bin", "underflow_guard": True, "overflow_guard": True,
        "requested_bin_count": len(boundaries) + 1, "actual_bin_count": len(boundaries) + 1,
        "creation_methodology": "psi_numeric_operator_override", "creator": actor,
        "reviewer": None, "review_timestamp": None, "supersedes_artifact_id": None,
        "source_population_fingerprint": manifest["baseline"]["data_fingerprint"],
        "source_artifact_references": [], "override_rationale": rationale.strip(),
        "baseline_rescan_required": True, "universal_eligibility": "psi_specific_only",
        "rerun_policy": "ignore_override_and_resume_standard_iv_or_psi_contract_route",
    }
    target_fp = None
    if manifest.get("target_choice", {}).get("mode") == "saved_target":
        payload["target"] = manifest["target_choice"].get("saved_target")
        payload["target_type"] = manifest["target_choice"].get("target_type")
        payload["positive_class"] = manifest["target_choice"].get("positive_class")
        target_fp = _target_fingerprint(manifest)
    saved = _save_draft(run_id, manifest, feature, payload, actor, target_fp=target_fp,
                         resolution="psi_numeric_override", source_workflow="psi_numeric_override")
    _refresh(manifest); manifest["updated_at"] = db.now_ist()
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest})
    return {**saved, "notice": "The Baseline feature was rescanned to validate arbitrary cuts. This PSI-only override will not be reused on reruns."}


def promote_psi_iv_bins(run_id: str, feature: str, draft_artifact_id: str,
                        actor: str = "system") -> dict[str, Any]:
    """Explicitly promote a PSI-created full-Baseline Diagnostic 2 chain."""
    from .manifest import get_run
    run = get_run(run_id); manifest = run["manifest_json"]
    if run["status"] != DRAFT or manifest.get("mode") != "two_snapshot":
        raise ManifestError("only a draft two-snapshot PSI run can promote universal bins")
    if manifest.get("target_choice", {}).get("mode") != "saved_target":
        raise ManifestError("universal Diagnostic 2 promotion requires the governed target route")
    repo = AnalysisArtifactRepository(); draft_meta, draft = repo.get(draft_artifact_id)
    if draft_meta.feature != feature or draft.get("universal_eligibility") != "candidate":
        raise ManifestError("this definition is not a full-Baseline universal candidate")
    ids = draft.get("diagnostic_2_artifact_ids") or {}
    if not all(ids.get(key) for key in ("fine_bins", "coarse_bins", "iv")):
        raise ManifestError("the Diagnostic 2 fine/coarse/IV artifact chain is incomplete")
    fine_meta, fine_payload = repo.get(ids["fine_bins"])
    _coarse_meta, coarse_payload = repo.get(ids["coarse_bins"])
    _iv_meta, iv_payload = repo.get(ids["iv"])
    fine_total = sum(int(row.get("rows") or 0) for row in fine_payload.get("bins") or [])
    coarse_total = sum(int(row.get("rows") or 0) for row in coarse_payload.get("bins") or [])
    if fine_total <= 0 or fine_total != coarse_total:
        raise ManifestError("fine/coarse counts do not reconcile; promotion is blocked")
    definition = coarse_payload.get("definition") or {}
    if not definition.get("solver_status"):
        raise ManifestError("the optimizer status or deterministic fallback is not documented")
    base = {
        "asset_id": fine_meta.asset_id, "snapshot_id": fine_meta.snapshot_id,
        "population_fingerprint": fine_meta.population_fingerprint,
        "target_fingerprint": fine_meta.target_fingerprint, "feature": feature,
        "methodology_fingerprint": fine_meta.methodology_fingerprint,
        "scope": "universal", "table": manifest.get("baseline_table") or manifest["table"],
        "run_id": run_id, "created_by": actor,
    }
    universal_fine = repo.save_version(fine_payload, artifact_type="fine_bins", **base)
    universal_coarse = repo.save_version(coarse_payload, artifact_type="coarse_bins",
                                         source_artifact_ids=(universal_fine.artifact_id,), **base)
    universal_iv = repo.save_version(iv_payload, artifact_type="iv",
        source_artifact_ids=(universal_fine.artifact_id, universal_coarse.artifact_id), **base)
    previous = _exact_promoted_iv(manifest, feature)
    if previous:
        prior = db.query_one("diag_binning_revisions", revision_id=previous.get("promotion_revision_id")) or {}
        for old, new in ((prior.get("fine_artifact_id"), universal_fine.artifact_id),
                         (prior.get("coarse_artifact_id"), universal_coarse.artifact_id),
                         (prior.get("iv_artifact_id"), universal_iv.artifact_id)):
            if old:
                repo.supersede(old, by_artifact_id=new)
    revision_id = f"brev_{uuid.uuid4().hex[:12]}"
    db.insert("diag_binning_revisions", {
        "revision_id": revision_id, "result_id": f"psi:{run_id}:{feature}", "run_id": run_id,
        "item_id": manifest["baseline"]["snapshot"]["snapshot_id"], "feature": feature,
        "scope": "universal", "revision_no": 1,
        "fine_artifact_id": universal_fine.artifact_id,
        "coarse_artifact_id": universal_coarse.artifact_id, "iv_artifact_id": universal_iv.artifact_id,
        "supersedes_revision_id": previous.get("promotion_revision_id") if previous else None,
        "definition_json": coarse_payload.get("definition") or {}, "bins_json": coarse_payload.get("bins") or [],
        "metrics_json": coarse_payload.get("metrics") or [], "warnings_json": coarse_payload.get("warnings") or [],
        "governance_json": {"confirmed_universal": True, "source": "psi_full_baseline",
                            "validation": {"counts_reconcile": True, "artifacts_readable": True,
                                           "optimizer_status": definition.get("solver_status")}},
        "actor": actor, "created_at": db.now_ist(),
    })
    draft["universal_eligibility"] = "promoted"
    draft["universal_revision_id"] = revision_id
    repo.save_version(draft, artifact_type="psi_bins", asset_id=draft_meta.asset_id,
        snapshot_id=draft_meta.snapshot_id, population_fingerprint=draft_meta.population_fingerprint,
        target_fingerprint=draft_meta.target_fingerprint,
        methodology_fingerprint=draft_meta.methodology_fingerprint,
        scope="diagnostic_local", owner_id="diagnostic:14", feature=feature,
        table=manifest.get("baseline_table") or manifest["table"],
        source_artifact_ids=(universal_coarse.artifact_id,), run_id=run_id, created_by=actor,
        identity_inputs={"governance_state": "draft", "manifest_run_id": run_id,
                         "universal_revision_id": revision_id})
    return {"status": "promoted", "revision_id": revision_id,
            "artifact_ids": {"fine_bins": universal_fine.artifact_id,
                             "coarse_bins": universal_coarse.artifact_id, "iv": universal_iv.artifact_id}}


def _checked_bin_draft(repo: AnalysisArtifactRepository, feature: str, draft_artifact_id: str):
    draft_meta, draft = repo.get(draft_artifact_id)
    if (draft_meta.artifact_type != "psi_bins" or draft_meta.feature != feature
            or draft_meta.status != "active" or draft.get("governance_state") != "draft"):
        raise ManifestError("review requires the matching active PSI bin draft")
    return draft_meta, draft


def _freeze_checked_bin_draft(run_id: str, manifest: dict[str, Any], feature: str, draft_artifact_id: str,
                              draft_meta, draft: dict[str, Any], actor: str,
                              repo: AnalysisArtifactRepository):
    frozen = {**draft, "governance_state": "frozen", "reviewer": actor,
              "review_timestamp": db.now_ist(), "supersedes_artifact_id": draft_artifact_id}
    frozen["payload_fingerprint"] = stable_fingerprint({key: value for key, value in frozen.items() if key != "payload_fingerprint"})
    snapshot = manifest["baseline"]["snapshot"]
    saved = repo.save_version(frozen, artifact_type="psi_bins", asset_id=snapshot["asset_id"], snapshot_id=snapshot["snapshot_id"],
        population_fingerprint=draft_meta.population_fingerprint, target_fingerprint=draft_meta.target_fingerprint,
        methodology_fingerprint=draft_meta.methodology_fingerprint, scope="diagnostic_local", owner_id="diagnostic:14",
        feature=feature, table=manifest["table"], source_artifacts=tuple(draft_meta.source_artifacts),
        identity_inputs={"governance_state": "frozen", "draft_artifact_id": draft_artifact_id}, run_id=run_id, created_by=actor)
    repo.supersede(draft_artifact_id, by_artifact_id=saved.artifact_id, actor=actor)
    manifest["frozen_bins"] = {**manifest["frozen_bins"], feature: {"artifact_id": saved.artifact_id,
        "artifact_type": "psi_bins", "payload_hash": saved.payload_hash, "governance_state": "frozen",
        "coarse_bin_count": len(frozen.get("bins") or []) or frozen.get("actual_bin_count"),
        "population_match": True, "reuse_reason": "reviewed PSI draft", "override_confirmed": False}}
    manifest["bin_drafts"] = {name: value for name, value in manifest.get("bin_drafts", {}).items()
                              if name != feature}
    return saved


def freeze_bin_draft(run_id: str, feature: str, draft_artifact_id: str, actor: str = "system") -> dict[str, Any]:
    from .manifest import get_run
    run = get_run(run_id)
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    manifest = run["manifest_json"]
    repo = AnalysisArtifactRepository()
    draft_meta, draft = _checked_bin_draft(repo, feature, draft_artifact_id)
    saved = _freeze_checked_bin_draft(run_id, manifest, feature, draft_artifact_id, draft_meta, draft, actor, repo)
    persisted = _persist(run_id, manifest, "bin_assignment", {"feature": feature, "artifact_id": saved.artifact_id}, actor)
    return {"artifact": saved.to_dict(), "manifest": persisted}


def approve_bin_batch(run_id: str, features: list[str], actor: str = "system") -> dict[str, Any]:
    """Approve reviewed PSI bin choices with one manifest refresh and persistence write."""
    from .manifest import get_run
    run = get_run(run_id)
    if run["status"] != DRAFT: raise ManifestError("frozen manifests are immutable")
    manifest = run["manifest_json"]
    requested = list(dict.fromkeys(features or []))
    selected = set(manifest.get("selected_features") or [])
    if not requested or not set(requested) <= selected:
        raise ManifestError("select one or more eligible PSI variables")

    metadata = {row["column"]: row for row in manifest.get("feature_metadata") or []}
    repo = AnalysisArtifactRepository()
    prepared: list[tuple[str, str, Any]] = []
    # Validate the entire request before creating or superseding any artifacts.
    for feature in requested:
        route = (metadata.get(feature) or {}).get("bin_route") or {}
        artifact = route.get("artifact") or {}
        if route.get("workflow") == "confirm_frozen_reuse":
            reference = _validated_reference(manifest, feature, artifact.get("artifact_id"))
            prepared.append((feature, "reuse", reference))
        elif route.get("workflow") == "review_draft":
            artifact_id = artifact.get("artifact_id")
            meta, payload = _checked_bin_draft(repo, feature, artifact_id)
            prepared.append((feature, "draft", (artifact_id, meta, payload)))
        else:
            raise ManifestError(f"{feature} is not ready for batch approval")

    artifacts = []
    for feature, action, value in prepared:
        if action == "reuse":
            manifest["frozen_bins"] = {**manifest.get("frozen_bins", {}), feature: value}
        else:
            artifact_id, meta, payload = value
            saved = _freeze_checked_bin_draft(run_id, manifest, feature, artifact_id, meta, payload, actor, repo)
            artifacts.append(saved.to_dict())
    persisted = _persist(run_id, manifest, "bin_assignment_batch", {"features": requested}, actor)
    return {"approved_features": requested, "artifacts": artifacts, "manifest": persisted}


def freeze(run_id: str, actor: str = "system") -> dict[str, Any]:
    from .manifest import get_run
    run = get_run(run_id); manifest = dict(run["manifest_json"])
    if run["status"] != DRAFT: return manifest
    _refresh(manifest)
    if manifest["blockers"]: raise ManifestError("PSI scope is not ready: " + "; ".join(row["message"] for row in manifest["blockers"]))
    identity = {key: manifest[key] for key in ("manifest_schema_version", "mode", "target_choice", "binning_source_choice", "baseline", "current",
        "table", "baseline_table", "current_table", "selected_features", "split_feature_overrides",
        "feature_eligibility_overrides", "frozen_bins", "thresholds", "epsilon",
        "engine_version", "methodology_version", "bindings")}
    manifest.update({"population_fingerprint": stable_fingerprint([manifest["baseline"], manifest["current"]]),
        "methodology_fingerprint": stable_fingerprint({"target_choice": manifest["target_choice"], "thresholds": manifest["thresholds"],
            "epsilon": manifest["epsilon"], "engine": manifest["engine_version"], "methodology": manifest["methodology_version"]}),
        "manifest_fingerprint": stable_fingerprint(identity), "status": RUNNING, "frozen_at": db.now_ist(), "frozen_by": actor})
    db.update("diag_runs", {"run_id": run_id}, {"manifest_json": manifest, "status": RUNNING, "started_at": manifest["frozen_at"]})
    return manifest
