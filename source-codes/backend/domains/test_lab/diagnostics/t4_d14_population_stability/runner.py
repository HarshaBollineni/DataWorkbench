"""Execution and persistence adapter for Diagnostic #14."""
from __future__ import annotations

import json
import uuid
from typing import Any, Generator

import system_db as db
from domains.aar.repository import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader

from . import calculate_feature_psi, split_population, validate_bin_definition
from . import manifest as manifest_mod
from domains.test_lab.shared.run_state import DONE, DRAFT, RUNNING, get_run

DIAGNOSTIC_ID = 14
AGENT = "population_stability_index_engine"


def _id(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _frames(manifest: dict[str, Any]):
    loader = SnapshotLoader()
    baseline_table = manifest.get("baseline_table") or manifest["table"]
    current_table = manifest.get("current_table") or baseline_table
    baseline_id = manifest["baseline"]["snapshot"]["snapshot_id"]
    current_id = manifest["current"]["snapshot"]["snapshot_id"]
    columns = manifest["selected_features"]
    if baseline_id == current_id:
        predicate = manifest["baseline"].get("predicate") or {}
        split_feature = predicate.get("feature")
        spec = predicate.get("expression")
        if not split_feature or not isinstance(spec, dict):
            raise ValueError("one-snapshot PSI requires a baseline split feature and predicate")
        frame = loader.load_table(baseline_id, baseline_table, columns=list(dict.fromkeys([*columns, split_feature])))
        outcome = split_population(frame, split_feature, spec,
            null_policy=manifest["baseline"].get("null_policy", "baseline"),
            special_values=predicate.get("special_values"),
            special_policy=predicate.get("special_policy", "exclude"))
        return outcome["baseline"], outcome["current"]
    return (loader.load_table(baseline_id, baseline_table, columns=columns, allow_historical=True),
            loader.load_table(current_id, current_table, columns=columns, allow_historical=True))


def _bin(repo: AnalysisArtifactRepository, reference: dict[str, Any], feature: str):
    artifact_id = reference.get("artifact_id")
    if not artifact_id: raise ValueError(f"missing frozen-bin artifact for {feature}")
    metadata, payload = repo.get(artifact_id)
    if metadata.artifact_type not in {"psi_bins", "coarse_bins"} or metadata.feature != feature:
        raise ValueError(f"incompatible frozen-bin artifact for {feature}")
    if metadata.status != "active" and not reference.get("allow_historical"):
        raise ValueError(f"superseded frozen-bin artifact for {feature}")
    if reference.get("payload_hash") != metadata.payload_hash:
        raise ValueError(f"frozen-bin payload hash mismatch for {feature}")
    if metadata.artifact_type == "coarse_bins":
        reviewed = db.query_one("diag_binning_revisions", coarse_artifact_id=artifact_id)
        if not reviewed: raise ValueError(f"coarse-bin artifact for {feature} was not explicitly reviewed")
        definition = payload.get("definition") or {}
        if definition.get("feature") != feature or not definition.get("out_of_range_guard_bins"):
            raise ValueError(f"coarse-bin artifact for {feature} lacks compatible guard behavior")
        special_values = definition.get("special_values") or {}
        payload = {"payload_schema_version": 1, "governance_state": "frozen", "feature": feature,
            "physical_type": "reviewed_coarse_bins", "logical_type": definition.get("feature_type"),
            "kind": definition.get("feature_type"), "boundaries": definition.get("numeric_splits") or [],
            "groups": [{"label": f"group_{index + 1}", "values": values}
                       for index, values in enumerate(definition.get("categorical_groups") or [])],
            "boundary_semantics": "right_closed" if definition.get("feature_type") == "numeric" else "exact_match",
            "missing_bin": True, "special_value_bins": [{"label": label, "values": values}
                for label, values in sorted(special_values.items())], "unseen_category_policy": "unseen_bin",
            "underflow_guard": definition.get("feature_type") == "numeric",
            "overflow_guard": definition.get("feature_type") == "numeric",
            "requested_bin_count": len(payload.get("bins") or []), "actual_bin_count": len(payload.get("bins") or []),
            "creation_methodology": "reviewed_diagnostic_2_coarse_bins",
            "source_population_fingerprint": metadata.population_fingerprint,
            "source_artifact_references": [artifact_id], "creator": metadata.created_by,
            "reviewer": reviewed.get("actor"), "review_timestamp": reviewed.get("created_at"),
            "supersedes_artifact_id": None}
    validate_bin_definition(payload)
    return metadata, payload


def _baseline_profile(repo: AnalysisArtifactRepository, manifest: dict[str, Any], feature: str):
    """Resolve the immutable Data Sourcing profile frozen into this run's bindings."""
    for artifact_id in (manifest.get("bindings") or {}).get("column_profile_artifact_ids") or []:
        try:
            metadata, payload = repo.get(artifact_id)
        except (KeyError, OSError):
            continue
        if (metadata.artifact_type == "column_profile" and metadata.feature == feature
                and metadata.identity.get("table") == (manifest.get("baseline_table") or manifest["table"])):
            return metadata, payload
    return None


def _persist_feature(manifest: dict[str, Any], feature: str, result: dict[str, Any],
                     bin_meta: Any, actor: str) -> tuple[str, str]:
    repo = AnalysisArtifactRepository()
    profile = _baseline_profile(repo, manifest, feature)
    profile_meta, profile_payload = profile if profile else (None, None)
    payload = {**result, "feature": feature, "population_fingerprint": manifest["population_fingerprint"],
               "bin_artifact_id": bin_meta.artifact_id, "bin_payload_hash": bin_meta.payload_hash,
               "manifest_fingerprint": manifest["manifest_fingerprint"]}
    if profile_meta:
        payload.update({"profile_artifact_id": profile_meta.artifact_id,
                        "data_profile": profile_payload})
    source_artifacts = [{"artifact_id": bin_meta.artifact_id, "role": "frozen_bins"}]
    if profile_meta:
        source_artifacts.append({"artifact_id": profile_meta.artifact_id,
                                 "role": "baseline_feature_profile"})
    saved = repo.save(payload, artifact_type="psi", asset_id=manifest["baseline"]["snapshot"]["asset_id"],
        snapshot_id=manifest["baseline"]["snapshot"]["snapshot_id"],
        comparison_snapshot_id=manifest["current"]["snapshot"]["snapshot_id"],
        population_fingerprint=manifest["population_fingerprint"], target_fingerprint=None,
        feature=feature, methodology_fingerprint=manifest["methodology_fingerprint"],
        scope="diagnostic_local", owner_id="diagnostic:14", table=manifest["table"],
        source_artifacts=tuple(source_artifacts),
        identity_inputs={"manifest_fingerprint": manifest["manifest_fingerprint"],
                         "bin_payload_hash": bin_meta.payload_hash, "thresholds": manifest["thresholds"],
                         "epsilon": manifest["epsilon"], "engine_version": manifest["engine_version"],
                         "profile_payload_hash": profile_meta.payload_hash if profile_meta else None},
        run_id=manifest["run_id"], created_by=actor)
    result_id = _id("dres"); now = db.now_ist()
    db.insert("diag_results", {"result_id": result_id, "run_id": manifest["run_id"],
        "diagnostic_id": DIAGNOSTIC_ID, "entity_or_table": feature, "decision_type": "contextual",
        "verdict": None, "review_state": "open",
        "metrics_json": {"result_kind": "psi_feature", **payload,
                         "artifact_id": saved.artifact.artifact_id, "reuse_outcome": saved.outcome},
        "thresholds_used_json": {"values": manifest["thresholds"], "sources": manifest["threshold_sources"]},
        "scope_counts_json": {"baseline": result["baseline_count"], "current": result["current_count"]},
        "na_reason": None, "created_at": now})
    return result_id, saved.outcome


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    row = get_run(run_id)
    if row["status"] == DONE:
        yield {"phase": "start", "agent": AGENT, "run_id": run_id, "thought": "Replaying a completed run."}
        yield {"phase": "done", "agent": AGENT, "run_id": run_id, "thought": "Run already complete."}
        return
    manifest = manifest_mod.freeze(run_id, actor) if row["status"] == DRAFT else row["manifest_json"]
    if row["status"] not in {DRAFT, RUNNING}: raise RuntimeError(f"run {run_id} cannot execute")
    baseline, current = _frames(manifest); features = manifest["selected_features"]
    yield {"phase": "start", "agent": AGENT, "run_id": run_id, "total": len(features),
           "thought": f"Calculating frozen-bin PSI for {len(features)} feature(s)."}
    repo = AnalysisArtifactRepository(); complete, failed, findings, artifact_outcomes = [], [], [], []
    for index, feature in enumerate(features, 1):
        preview: dict[str, Any]
        try:
            bin_meta, bins = _bin(repo, manifest["frozen_bins"][feature], feature)
            result = calculate_feature_psi(baseline[feature], current[feature], bins,
                                           epsilon=manifest["epsilon"], thresholds=manifest["thresholds"])
            result_id, reuse = _persist_feature(manifest, feature, result, bin_meta, actor)
            complete.append(result_id); artifact_outcomes.append(reuse)
            preview = {"feature": feature, "status": "completed", "psi": result["psi"],
                       "classification": result["classification"],
                       "bin_count": len(result.get("bins") or []),
                       "baseline_count": result["baseline_count"],
                       "current_count": result["current_count"]}
            if result["classification"] != "stable":
                finding_id = _id("dfind"); findings.append(finding_id)
                db.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id, "run_id": run_id,
                    "rule_id": f"psi:{feature}", "kb_rule_id": None, "severity": "MATERIAL",
                    "outcome": "CONTEXTUAL", "violation_count": 0, "rate": result["psi"],
                    "tolerance": manifest["thresholds"]["watch"], "exceptions_json": {"count": 0},
                    "evidence_json": [{"feature": feature, "psi": result["psi"],
                                       "classification": result["classification"]}],
                    "pattern": "population_drift", "pattern_detail": result["classification"],
                    "regulatory_ref": None, "rule_text": "PSI threshold indicates contextual SME review.",
                    "rule_type": "statistical", "framework": None, "entity": feature,
                    "resolved_roles_json": {}, "tables_used": manifest["table"],
                    "scope_rows_evaluated": len(baseline) + len(current), "scope_rows_skipped": 0,
                    "na_reason": None, "review_state": "open", "seq": index - 1, "created_at": db.now_ist()})
        except Exception as exc:
            failed.append({"feature": feature, "error": str(exc)})
            preview = {"feature": feature, "status": "failed", "error": str(exc)}
        yield {"phase": "progress", "agent": AGENT, "run_id": run_id, "done": index,
               "total": len(features), "feature": feature, "preview": preview,
               "thought": f"[{index}/{len(features)}] {feature}"}
    status = "complete" if not failed else ("partial" if complete else "failed")
    summary_id = _id("dres"); rollup = {"features": len(features), "completed": len(complete),
        "failed": len(failed), "contextual_findings": len(findings), "artifact_outcomes": artifact_outcomes}
    db.insert("diag_results", {"result_id": summary_id, "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID,
        "entity_or_table": manifest["table"], "decision_type": "contextual", "verdict": None,
        "review_state": "open", "metrics_json": {"result_kind": "psi_run_summary", "status": status,
            "rollup": rollup, "feature_errors": failed, "methodology": manifest["methodology_version"]},
        "thresholds_used_json": {"values": manifest["thresholds"], "sources": manifest["threshold_sources"]},
        "scope_counts_json": {"features_selected": len(features), "features_completed": len(complete)},
        "na_reason": None, "created_at": db.now_ist()})
    db.update("diag_runs", {"run_id": run_id}, {"status": DONE if complete else "failed", "finished_at": db.now_ist()})
    yield {"phase": "done", "agent": AGENT, "run_id": run_id, "result_id": summary_id,
           "rollup": rollup, "findings": len(findings), "issues": {"created": []},
           "thought": f"PSI run {status}; contextual findings require human review."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last = {}
    for event in run(run_id, actor): last = event
    return last


def run_results(run_id: str) -> dict[str, Any]:
    from domains.test_lab.shared.results import run_results as shared
    response = shared(run_id)
    # Older persisted PSI results pre-date display labels. Hydrate them from
    # their immutable frozen-bin artifact so historical runs remain readable.
    from .engine import bin_display_labels
    repo = AnalysisArtifactRepository()
    manifest = get_run(run_id).get("manifest_json") or {}
    for row in response.get("results") or []:
        metrics = row.get("metrics_json") or {}
        if metrics.get("result_kind") != "psi_feature":
            continue
        if metrics.get("bin_artifact_id"):
            try:
                _, definition = _bin(repo, {"artifact_id": metrics["bin_artifact_id"],
                    "payload_hash": metrics.get("bin_payload_hash"), "allow_historical": True}, metrics["feature"])
                labels = bin_display_labels(definition)
                metrics["bins"] = [{**bin_row, "bin_label": bin_row.get("bin_label") or labels.get(bin_row.get("bin"), bin_row.get("bin"))}
                                   for bin_row in metrics.get("bins") or []]
            except Exception:
                # The persisted technical ID is still valid evidence if a legacy
                # external artifact is unavailable for display-only hydration.
                pass
        if not metrics.get("data_profile"):
            profile = _baseline_profile(repo, manifest, metrics.get("feature"))
            if profile:
                profile_meta, profile_payload = profile
                metrics["profile_artifact_id"] = profile_meta.artifact_id
                metrics["data_profile"] = profile_payload
    return response


def report_text(run_id: str) -> str:
    from .reporting import render_text, report_payload
    payload, _artifact, _reused = report_payload(run_id)
    return render_text(payload)


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    from .reporting import report_document as build_document
    return build_document(run_id)


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]


def ensure_contextual_issue(finding_id: str, actor: str = "system") -> str:
    """Promote PSI evidence only after the explicit human confirmation route."""
    rows = db.execute("SELECT f.*, r.metrics_json, dr.item_id FROM diag_findings f "
        "JOIN diag_results r ON r.result_id=f.result_id JOIN diag_runs dr ON dr.run_id=f.run_id "
        "WHERE f.finding_id=? AND dr.diagnostic_id=?", [finding_id, DIAGNOSTIC_ID])
    if not rows: raise KeyError(f"Unknown PSI finding: {finding_id}")
    finding = rows[0]; metrics = finding.get("metrics_json") or {}
    if isinstance(metrics, str): metrics = json.loads(metrics)
    feature = metrics.get("feature") or finding["entity"]
    existing = db.query_one("issues_v2", finding_id=finding_id)
    if existing: return existing["issue_row_id"]
    from ai.v2 import service as item_service
    issue_id = item_service._id("iss"); now = db.now_ist()
    db.insert("issues_v2", {"issue_row_id": issue_id, "item_id": finding["item_id"],
        "table_name": finding.get("tables_used") or "", "test_name": "Population Stability Index review",
        "area_id": "T4", "criticality": "Medium", "columns_json": [feature], "violation_count": 0,
        "threshold_json": metrics.get("thresholds") or {}, "column_details_json": [{"columns": [feature],
            "metric": metrics.get("psi"), "classification": metrics.get("classification"),
            "artifact_id": metrics.get("artifact_id")}], "metric": metrics.get("psi"), "status": "Open",
        "workflow_version": "rca", "run_id": finding["run_id"], "finding_id": finding_id,
        "diagnostic_id": DIAGNOSTIC_ID, "rule_id": finding["rule_id"], "created_at": now, "updated_at": now})
    return issue_id


def ensure_contextual_finding(result_id: str, reason: str | None = None,
                              actor: str = "system") -> str:
    """Create auditable PSI review evidence when an operator overrides a recommendation."""
    rows = db.execute("SELECT r.*, dr.item_id FROM diag_results r JOIN diag_runs dr ON dr.run_id=r.run_id "
                      "WHERE r.result_id=? AND dr.diagnostic_id=?", [result_id, DIAGNOSTIC_ID])
    if not rows: raise KeyError(f"Unknown PSI result: {result_id}")
    result = rows[0]; metrics = result.get("metrics_json") or {}
    if isinstance(metrics, str): metrics = json.loads(metrics)
    if metrics.get("result_kind") != "psi_feature": raise ValueError("only PSI feature results can be promoted")
    existing = db.query_one("diag_findings", result_id=result_id)
    if existing: return existing["finding_id"]
    classification = metrics.get("classification")
    rationale = (reason or "").strip()
    if classification == "stable" and not rationale:
        raise ValueError("a rationale is required to override the stable PSI recommendation")
    finding_id = _id("dfind")
    db.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id,
        "run_id": result["run_id"], "rule_id": f"psi:{metrics.get('feature')}", "kb_rule_id": None,
        "severity": "MINOR" if classification == "stable" else "MATERIAL", "outcome": "CONTEXTUAL",
        "violation_count": 0, "rate": metrics.get("psi"),
        "tolerance": (metrics.get("thresholds") or {}).get("watch"), "exceptions_json": {"count": 0},
        "evidence_json": [{"feature": metrics.get("feature"), "psi": metrics.get("psi"),
            "classification": classification, "operator_override": classification == "stable",
            "override_rationale": rationale or None}], "pattern": "population_drift",
        "pattern_detail": classification, "regulatory_ref": None,
        "rule_text": "Operator promoted PSI evidence after reviewing the product recommendation."
            if classification == "stable" else "PSI threshold indicates contextual SME review.",
        "rule_type": "statistical", "framework": None, "entity": metrics.get("feature"),
        "resolved_roles_json": {}, "tables_used": result.get("entity_or_table"),
        "scope_rows_evaluated": int(metrics.get("baseline_count") or 0) + int(metrics.get("current_count") or 0),
        "scope_rows_skipped": 0, "na_reason": None, "review_state": "open", "seq": 0,
        "created_at": db.now_ist()})
    return finding_id
