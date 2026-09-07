"""Execution and persistence for T2-D11 directional consistency."""
from __future__ import annotations

import uuid
from typing import Any, Generator

import pandas as pd

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.snapshots import SnapshotLoader
from analysis_runtime.targets import resolve_target_route
from domains.aar.repository import AnalysisArtifactRepository
from dq_diagnostics.inference_audit import inference_disclosure
from domains.test_lab.shared.run_state import DONE, DRAFT, RUNNING

from . import manifest as manifest_mod
from .engine import DirectionalityThresholds, analyze_directionality, compare_expected_observed

DIAGNOSTIC_ID = 11
AGENT = "directional_monotonic_consistency_engine"
ARTIFACT_TYPE = "directionality_evidence"
REPORT_ARTIFACT_TYPE = "directionality_report"
REPORT_RENDERER_VERSION = "3"
METHODOLOGY = "broad_bin_shape_spearman_regression_consensus_v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _thresholds(manifest: dict[str, Any]) -> DirectionalityThresholds:
    values = {key: spec["value"] for key, spec in manifest["thresholds"].items()}
    return DirectionalityThresholds(
        min_sample=int(values["min_sample"]),
        min_binary_class=int(values["min_binary_class"]),
        significance_level=float(values["significance_level"]),
        corr_floor=float(values["corr_floor"]),
        regression_floor=float(values["regression_floor"]),
        bin_range_floor_sd=float(values["bin_range_floor_sd"]),
        requested_bins=int(manifest["parameters"]["requested_bins"]),
    )


def _feature_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["feature"]: row for row in manifest["features"]}


def _comparison(feature: dict[str, Any], manifest: dict[str, Any],
                evidence: dict[str, Any]) -> dict[str, Any]:
    conclusion = compare_expected_observed(
        feature["expected_direction"], manifest["reference"]["orientation"],
        evidence["observed_direction"])
    expected_reference = feature["expected_direction"]
    if manifest["reference"]["orientation"] == "HIGHER_IS_BETTER":
        expected_reference = {"INCREASING": "DECREASING",
                              "DECREASING": "INCREASING"}.get(expected_reference,
                                                                expected_reference)
    return {"kb_expected_risk_direction": feature["expected_direction"],
            "expected_reference_direction": expected_reference,
            "observed_direction": evidence["observed_direction"],
            "evidence_strength": evidence["evidence_strength"],
            "conclusion": conclusion,
            "review_recommended": conclusion in {
                "REVIEW_RECOMMENDED", "CONFLICTING_EVIDENCE", "WEAK_OR_NO_RELATIONSHIP"}}


def _segments(frame: pd.DataFrame, segment_definition: dict[str, Any] | None,
              min_sample: int) -> list[tuple[str, pd.DataFrame]]:
    del min_sample  # small selected samples are retained as insufficient-data evidence
    if not segment_definition:
        return []
    from domains.test_lab.diagnostics.t4_d14_population_stability.population import split_population
    feature = segment_definition["split_feature"]
    split = split_population(
        frame, feature, segment_definition["expression"],
        null_policy=segment_definition.get("null_policy") or "baseline",
        special_values=segment_definition.get("special_values") or [],
        special_policy=segment_definition.get("special_policy") or "exclude",
    )
    # Overall evidence is always calculated. Only the explicitly accepted side
    # receives a segment result; its complement is intentionally not analysed.
    return [(_segment_name(segment_definition), split["baseline"])]


def _segment_name(segment_definition: dict[str, Any]) -> str:
    """Return a stable short name; predicate details remain separate metadata."""
    feature = str(segment_definition.get("split_feature") or "segment")

    def compact(value: str) -> str:
        cleaned = "".join(character if character.isalnum() else "_" for character in value)
        return "_".join(part for part in cleaned.split("_") if part)[:40] or "selected"

    return f"seg_{compact(feature)}"


def _save_artifact(manifest: dict[str, Any], feature: dict[str, Any],
                   payload: dict[str, Any], actor: str) -> tuple[str, bool]:
    source_ids = tuple(value for value in (
        feature.get("profile_artifact_id"),
        next((row.get("profile_artifact_id") for row in manifest["features"]
              if row["feature"] == manifest["reference"]["column"]), None),
    ) if value)
    identity_inputs = {"manifest_fingerprint": manifest["manifest_fingerprint"],
                       "feature": feature["feature"], "payload_schema": 2}
    saved = AnalysisArtifactRepository().save(
        payload, artifact_type=ARTIFACT_TYPE,
        asset_id=SnapshotLoader().reference(manifest["item_id"]).asset_id,
        snapshot_id=manifest["item_id"], comparison_snapshot_id=None,
        population_fingerprint=stable_fingerprint({"snapshot": manifest["item_id"],
                                                   "table": manifest["table"]}),
        target_fingerprint=stable_fingerprint(manifest["reference"]),
        feature=feature["feature"],
        methodology_fingerprint=stable_fingerprint(identity_inputs),
        scope="diagnostic_local", workflow_id=None,
        owner_id="t2_d11_directional_monotonic_consistency",
        source_artifact_ids=source_ids, run_id=manifest["run_id"], created_by=actor)
    return saved.artifact.artifact_id, saved.outcome == "reused"


def _persist_feature(manifest: dict[str, Any], feature: dict[str, Any],
                     evidence: dict[str, Any], segments: list[dict[str, Any]],
                     artifact_id: str, reused: bool, actor: str) -> tuple[str, int]:
    result_id, now = _id("dres"), db.now_ist()
    comparison = _comparison(feature, manifest, evidence)
    # ``diag_results`` is the queryable run index. The immutable AAR payload is
    # the governed source for evidence and metrics and is hydrated on read.
    metrics = {"result_kind": "directionality_feature", "feature": feature["feature"],
               "artifact_type": ARTIFACT_TYPE, "artifact_id": artifact_id,
               "artifact_reused": reused, "methodology": METHODOLOGY}
    db.insert("diag_results", {"result_id": result_id, "run_id": manifest["run_id"],
        "diagnostic_id": DIAGNOSTIC_ID, "entity_or_table": feature["feature"],
        "decision_type": "contextual", "verdict": None, "review_state": "open",
        "metrics_json": metrics,
        "thresholds_used_json": {"values": {key: value["value"] for key, value in manifest["thresholds"].items()},
                                 "sources": {key: value["source"] for key, value in manifest["thresholds"].items()}},
        "scope_counts_json": {"rows_evaluated": evidence["n_paired"],
                              "rows_skipped": evidence["n_dropped"],
                              "segments": len(segments)},
        "na_reason": evidence["status_reason"] if evidence["observed_direction"] in {
            "INSUFFICIENT_DATA", "NOT_APPLICABLE"} else None, "created_at": now})
    finding_count = 0
    if comparison["review_recommended"]:
        finding_count = 1
        db.insert("diag_findings", {"finding_id": _id("dfind"), "result_id": result_id,
            "run_id": manifest["run_id"],
            "rule_id": f"directionality:{feature['feature']}", "kb_rule_id": None,
            "severity": "MATERIAL", "outcome": "CONTEXTUAL", "violation_count": 0,
            "rate": evidence["spearman"].get("value"),
            "tolerance": manifest["thresholds"]["corr_floor"]["value"],
            "exceptions_json": {"count": 0, "notes": []},
            "evidence_json": [{"feature": feature["feature"], **comparison}],
            "pattern": comparison["conclusion"],
            "pattern_detail": evidence["status_reason"], "regulatory_ref": None,
            "rule_text": "KB expected direction requires contextual review against empirical evidence.",
            "rule_type": "statistical", "framework": None, "entity": feature["feature"],
            "resolved_roles_json": {"feature": feature["feature"],
                                    "target": manifest["reference"]["column"]},
            "tables_used": manifest["table"], "scope_rows_evaluated": evidence["n_paired"],
            "scope_rows_skipped": evidence["n_dropped"], "na_reason": None,
            "review_state": "open", "seq": 0, "created_at": now})
    return result_id, finding_count


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    row = manifest_mod.get_run(run_id)
    if row["status"] == DONE:
        yield {"phase": "start", "agent": AGENT, "run_id": run_id,
               "thought": "Replaying a completed directionality run."}
        yield {"phase": "done", "agent": AGENT, "run_id": run_id,
               "thought": "Run already complete."}
        return
    manifest = manifest_mod.freeze(run_id, actor) if row["status"] == DRAFT else row["manifest_json"]
    if row["status"] not in {DRAFT, RUNNING}:
        raise RuntimeError(f"run cannot execute from status {row['status']}")
    features = _feature_map(manifest)
    selected = manifest["selected_features"]
    columns = list(dict.fromkeys([manifest["reference"]["column"], *selected,
                                  *([manifest["segment_column"]] if manifest.get("segment_column") else [])]))
    frame = SnapshotLoader().load_table(manifest["item_id"], manifest["table"], columns=columns)
    target = frame[manifest["reference"]["column"]]
    target_type, positive_class = resolve_target_route(
        target, requested_type=manifest["reference"]["type"],
        positive_class=manifest["reference"].get("positive_class"))
    if target_type not in {"binary", "continuous"}:
        raise ValueError(f"T2-D11 does not support target type {target_type!r}")
    config = _thresholds(manifest)
    yield {"phase": "start", "agent": AGENT, "run_id": run_id, "total": len(selected),
           "thought": f"Assessing {len(selected)} feature(s) against {manifest['reference']['column']}."}
    complete, failed, findings = 0, [], 0
    for index, name in enumerate(selected, start=1):
        feature = features[name]
        try:
            evidence = analyze_directionality(
                frame[name], target, target_type=target_type, positive_class=positive_class,
                thresholds=config, feature_special_values=feature.get("special_values") or [],
                reference_special_values=manifest["reference"].get("special_values") or [])
            segment_evidence = []
            for segment, subset in _segments(
                frame, manifest.get("segment_definition"), config.min_sample,
            ):
                item = analyze_directionality(
                    subset[name], subset[manifest["reference"]["column"]],
                    target_type=target_type, positive_class=positive_class,
                    thresholds=config, feature_special_values=feature.get("special_values") or [],
                    reference_special_values=manifest["reference"].get("special_values") or [])
                segment_evidence.append({"segment": segment, "comparison": _comparison(feature, manifest, item),
                                         "evidence": item})
            segmented = bool(manifest.get("segment_definition"))
            segment_label = (_segment_name(manifest["segment_definition"])
                             if segmented else None)
            analysis_view = {
                "mode": "segmented_rerun" if segmented else "overall_only",
                "overall_label": "Entire sample",
                "overall_always_calculated": True,
                "segmentation": ({
                    "analysed_label": segment_label,
                    "not_analysed_label": "Not analysed",
                    "choice": manifest.get("segment_definition"),
                    "population_counts": manifest.get("segment_preview"),
                    "complement_tested": False,
                } if segmented else None),
            }
            payload = {"artifact_kind": ARTIFACT_TYPE, "schema_version": 2,
                       "run_id": run_id, "feature": name,
                       "reference": manifest["reference"],
                       "analysis_view": analysis_view,
                       "segment_column": manifest.get("segment_column"),
                       "segment_definition": manifest.get("segment_definition"),
                       "segment_preview": manifest.get("segment_preview"),
                       "expected": {key: feature.get(key) for key in (
                           "canonical_feature", "expected_direction", "representation_orientation",
                           "knowledge_strength", "classification_source", "rationale")},
                       "overall": evidence, "segments": segment_evidence,
                       "comparison": _comparison(feature, manifest, evidence),
                       "methodology": METHODOLOGY,
                       "manifest_fingerprint": manifest["manifest_fingerprint"]}
            artifact_id, reused = _save_artifact(manifest, feature, payload, actor)
            _result_id, found = _persist_feature(manifest, feature, evidence, segment_evidence,
                                                 artifact_id, reused, actor)
            findings += found
            complete += 1
            yield {"phase": "progress", "agent": AGENT, "run_id": run_id,
                   "done": index, "total": len(selected), "feature": name,
                   "thought": f"[{index}/{len(selected)}] {name}: {evidence['observed_direction']}"}
        except Exception as exc:  # partial outcomes are preserved
            failed.append({"feature": name, "error": f"{type(exc).__name__}: {exc}"})
            yield {"phase": "progress", "agent": AGENT, "run_id": run_id,
                   "done": index, "total": len(selected), "feature": name,
                   "thought": f"[{index}/{len(selected)}] {name}: evidence unavailable"}
    summary_id = _id("dres")
    rollup = {"features_selected": len(selected), "features_completed": complete,
              "features_failed": len(failed), "review_findings": findings}
    db.insert("diag_results", {"result_id": summary_id, "run_id": run_id,
        "diagnostic_id": DIAGNOSTIC_ID, "entity_or_table": manifest["table"],
        "decision_type": "contextual", "verdict": None, "review_state": "open",
        "metrics_json": {"result_kind": "directionality_run_summary", "rollup": rollup,
                         "feature_errors": failed,
                         "methodology": METHODOLOGY},
        "thresholds_used_json": {"values": {key: value["value"] for key, value in manifest["thresholds"].items()},
                                 "sources": {key: value["source"] for key, value in manifest["thresholds"].items()}},
        "scope_counts_json": rollup, "na_reason": None, "created_at": db.now_ist()})
    db.update("diag_runs", {"run_id": run_id}, {"status": DONE, "finished_at": db.now_ist()})
    yield {"phase": "done", "agent": AGENT, "run_id": run_id,
           "result_id": summary_id, "rollup": rollup, "findings": findings,
           "issues": {"created": []},
           "thought": f"Directionality run complete: {complete} feature(s), {findings} review finding(s)."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last = {}
    for event in run(run_id, actor):
        last = event
    return last


def hydrate_result(result: dict[str, Any]) -> dict[str, Any]:
    """Hydrate one D11 result index from its immutable governed artifact."""
    metrics = result.get("metrics_json") or {}
    if metrics.get("result_kind") != "directionality_feature":
        return result
    artifact_id = metrics.get("artifact_id")
    if not artifact_id:
        # Legacy pre-AAR rows may contain their evidence inline.
        return result
    try:
        metadata, payload = AnalysisArtifactRepository().get(artifact_id)
    except (KeyError, FileNotFoundError):
        if metrics.get("evidence") is not None:
            return result
        raise RuntimeError(f"governed directionality artifact is unavailable: {artifact_id}")
    if metadata.artifact_type != ARTIFACT_TYPE:
        raise RuntimeError(f"unexpected artifact type for D11 result: {metadata.artifact_type}")
    if payload.get("feature") != metrics.get("feature"):
        raise RuntimeError("directionality artifact feature does not match its result index")
    expected = payload.get("expected") or {}
    hydrated = {
        **metrics,
        "canonical_feature": expected.get("canonical_feature"),
        "classification_source": expected.get("classification_source"),
        "representation_orientation": expected.get("representation_orientation"),
        "knowledge_strength": expected.get("knowledge_strength"),
        "expected_rationale": expected.get("rationale"),
        "reference": payload["reference"],
        "analysis_view": payload.get("analysis_view") or {
            "mode": "segmented_rerun" if payload.get("segment_definition") else "overall_only",
            "overall_label": "Entire sample", "overall_always_calculated": True,
        },
        "segment_definition": payload.get("segment_definition"),
        "segment_preview": payload.get("segment_preview"),
        "comparison": payload["comparison"],
        "evidence": payload["overall"],
        "segments": payload.get("segments") or [],
        "methodology": payload.get("methodology") or metrics.get("methodology"),
    }
    return {**result, "metrics_json": hydrated}


def run_results(run_id: str) -> dict[str, Any]:
    from domains.test_lab.shared.results import run_results as shared
    return shared(run_id)


def _workflow_state(finding: dict[str, Any]) -> str:
    issue = finding.get("existing_issue") or {}
    if issue.get("status") == "Closed":
        return "closed"
    if issue or finding.get("review_state") == "confirmed":
        return "promoted"
    if finding.get("review_state") == "dismissed":
        return "dismissed"
    return "awaiting_review"


def report_payload(run_id: str) -> tuple[dict[str, Any], Any, bool]:
    """Build and retain the governed run-level D11 report source."""
    projected = run_results(run_id)
    run_row = manifest_mod.get_run(run_id)
    manifest = run_row["manifest_json"]
    manifest_features = _feature_map(manifest)
    features, source_ids, finding_actions = [], [], []
    workflow_counts = {"awaiting_review": 0, "promoted": 0, "closed": 0,
                       "dismissed": 0}
    for row in projected["results"]:
        metrics = row.get("metrics_json") or {}
        if metrics.get("result_kind") != "directionality_feature":
            continue
        evidence, comparison = metrics["evidence"], metrics["comparison"]
        source_ids.append(metrics["artifact_id"])
        configured = manifest_features.get(metrics["feature"]) or {}
        features.append({
            "feature": metrics["feature"],
            "canonical_feature": metrics.get("canonical_feature"),
            "classification_source": metrics.get("classification_source"),
            "expected_rationale": (metrics.get("expected_rationale")
                                   or configured.get("rationale")),
            "confirmed_by": configured.get("confirmed_by"),
            "human_review_confirmed": bool(configured.get("confirmed_by")),
            "expected_reference_direction": comparison["expected_reference_direction"],
            "observed_direction": comparison["observed_direction"],
            "evidence_strength": evidence["evidence_strength"],
            "conclusion": comparison["conclusion"],
            "spearman": (evidence.get("spearman") or {}).get("value"),
            "regression_coefficient": (evidence.get("regression") or {}).get("value"),
            "pearson_display_only": (evidence.get("pearson") or {}).get("value"),
            "paired_observations": evidence.get("n_paired"),
            "bin_count": len((evidence.get("binned") or {}).get("bins") or []),
            "segment_count": len(metrics.get("segments") or []),
            "analysis_view": metrics.get("analysis_view") or {},
            "segment_results": [{
                "label": item.get("segment"),
                "expected_reference_direction": (item.get("comparison") or {}).get(
                    "expected_reference_direction"),
                "observed_direction": (item.get("comparison") or {}).get("observed_direction"),
                "evidence_strength": (item.get("evidence") or {}).get("evidence_strength"),
                "conclusion": (item.get("comparison") or {}).get("conclusion"),
                "paired_observations": (item.get("evidence") or {}).get("n_paired"),
            } for item in metrics.get("segments") or []],
            "artifact_id": metrics["artifact_id"],
        })
        for finding in row.get("findings") or []:
            state = _workflow_state(finding)
            workflow_counts[state] += 1
            finding_actions.append({
                "feature": metrics["feature"], "finding_id": finding["finding_id"],
                "state": state,
                "issue_row_id": (finding.get("existing_issue") or {}).get("issue_row_id"),
                "issue_status": (finding.get("existing_issue") or {}).get("status"),
                "dispositions": finding.get("dispositions") or [],
            })
    disclosure = inference_disclosure(run_id)
    ai_reviews = []
    for event in disclosure.get("events") or []:
        if not event.get("invoked"):
            continue
        searched = ((event.get("redacted_input_manifest") or {}).get("feature") or {})
        response = event.get("validated_response") or {}
        feature_name = searched.get("name")
        configured = manifest_features.get(feature_name) or {}
        ai_reviews.append({
            "variable_searched": feature_name,
            "status": event.get("status"),
            "why_required": (
                "The variable did not have a sufficiently reliable automatic Knowledge Base "
                "match, so AI was requested to assess the most relevant candidate concepts."
            ),
            "ai_outcome": response.get("decision") or "NO_RESULT",
            "ai_selected_concept": response.get("selected_candidate"),
            "ai_rationale": response.get("reason") or event.get("sanitized_error"),
            "final_concept": configured.get("canonical_feature"),
            "final_expected_direction": configured.get("expected_direction"),
            "final_rationale": configured.get("rationale"),
            "human_review_confirmed": bool(configured.get("confirmed_by")),
            "confirmed_by": configured.get("confirmed_by"),
            "confirmation_statement": (
                f"Confirmed by {configured['confirmed_by']} before analysis execution."
                if configured.get("confirmed_by") else
                "No AI proposal was applied without an explicit user confirmation."
            ),
        })
    summary_row = next((row for row in projected["results"]
                        if (row.get("metrics_json") or {}).get("result_kind")
                        == "directionality_run_summary"), None)
    rollup = ((summary_row or {}).get("metrics_json") or {}).get("rollup") or {}
    report = {
        "artifact_kind": REPORT_ARTIFACT_TYPE, "schema_version": 2,
        "report_id": f"rpt_{run_id}", "run_id": run_id,
        "generated_at": run_row.get("finished_at") or db.now_ist(),
        "executor": manifest.get("frozen_by") or manifest.get("created_by") or "system",
        "introduction": (
            "This diagnostic compares the expected economic risk direction for each selected "
            "numeric variable with its observed relationship to the chosen target or substitute. "
            "Broad bin shape determines the empirical pattern; Spearman correlation and a "
            "univariate regression provide directional confirmation."
        ),
        "scope": {"item_id": run_row["item_id"], "table": manifest.get("table"),
                  "reference_column": (manifest.get("reference") or {}).get("column"),
                  "reference_orientation": (manifest.get("reference") or {}).get("orientation"),
                  "segment_column": manifest.get("segment_column"),
                  "segment_definition": manifest.get("segment_definition"),
                  "segment_preview": manifest.get("segment_preview")},
        "summary": {"features_selected": rollup.get("features_selected", len(features)),
                    "features_completed": rollup.get("features_completed", len(features)),
                    "features_failed": rollup.get("features_failed", 0),
                    "review_findings": rollup.get("review_findings", len(finding_actions)),
                    "awaiting_review": workflow_counts["awaiting_review"],
                    "issues_promoted": workflow_counts["promoted"],
                    "issues_closed": workflow_counts["closed"],
                    "findings_dismissed": workflow_counts["dismissed"]},
        "features": features, "finding_actions": finding_actions,
        "decision_actions": projected.get("decisions") or [],
        "inference_disclosure": disclosure,
        "ai_reviews": ai_reviews,
        "methodology": METHODOLOGY,
        "requested_bins": (manifest.get("parameters") or {}).get("requested_bins"),
        "knowledge": manifest.get("knowledge") or {},
        "limitations": [
            "Directionality is univariate evidence and does not establish causality.",
            "The regression fit is directional evidence, not a final model specification.",
            "Expected direction is frozen before empirical comparison and is not rewritten by results.",
            "Pearson correlation is displayed for context and is not used in classification.",
        ],
        "recommended_actions": ([
            "Review every awaiting contextual finding and either promote it to an issue or dismiss it with rationale.",
            "Track promoted issues through RCA and closure in Issue Management.",
        ] if finding_actions else [
            "Retain the governed evidence and repeat the diagnostic for the next relevant data delivery.",
        ]),
        "source_artifact_ids": source_ids,
    }
    metadata = [AnalysisArtifactRepository().get_metadata(value) for value in source_ids]
    if not metadata:
        raise RuntimeError("a directionality report requires at least one evidence artifact")
    identity_inputs = {
        "run_id": run_id, "renderer_version": REPORT_RENDERER_VERSION,
        "source_hashes": [value.payload_hash for value in metadata],
        "decision_actions_hash": stable_fingerprint(report["decision_actions"]),
        "finding_actions_hash": stable_fingerprint(finding_actions),
        "inference_event_set_hash": disclosure["event_set_hash"],
    }
    saved = AnalysisArtifactRepository().save(
        report, artifact_type=REPORT_ARTIFACT_TYPE,
        asset_id=metadata[0].asset_id, snapshot_id=run_row["item_id"],
        comparison_snapshot_id=None,
        population_fingerprint=stable_fingerprint({"sources": identity_inputs["source_hashes"]}),
        target_fingerprint=stable_fingerprint(manifest.get("reference") or {}),
        feature=None, methodology_fingerprint=stable_fingerprint(identity_inputs),
        scope="diagnostic_local", workflow_id=None,
        owner_id="t2_d11_directional_monotonic_consistency",
        table=manifest.get("table"), features=tuple(sorted(row["feature"] for row in features)),
        source_artifacts=tuple({"artifact_id": value, "role": "feature_evidence"}
                               for value in source_ids),
        identity_inputs=identity_inputs, run_id=run_id,
        created_by=manifest.get("frozen_by") or "system",
    )
    return report, saved.artifact, saved.outcome == "reused"


def _report_text(payload: dict[str, Any]) -> str:
    summary, disclosure = payload["summary"], payload["inference_disclosure"]
    scope = payload["scope"]
    orientation = {"HIGHER_IS_WORSE": "Higher target value = higher risk",
                   "HIGHER_IS_BETTER": "Higher target value = lower risk"}.get(
                       scope.get("reference_orientation"), scope.get("reference_orientation"))
    lines = ["DIRECTIONAL CONSISTENCY ANALYSIS REPORT", "", "1. ANALYSIS OVERVIEW",
             payload["introduction"], "", "Execution details",
             f"- Report timestamp: {payload['generated_at']}",
             f"- Executor: {payload['executor']}", f"- Run ID: {payload['run_id']}",
             f"- Data item / snapshot: {scope['item_id']}", f"- Table: {scope['table']}",
             "", "Analysis inputs",
             f"- Target or substitute: {scope['reference_column']}",
             f"- Target risk direction: {orientation}",
             f"- Segment analysis: {scope.get('segment_column') or 'Overall portfolio only'}",
             *( [f"- Analysed segment rows: {scope['segment_preview']['baseline_count']}",
                  f"- Complement not analysed: {scope['segment_preview']['current_count']} rows"]
                if scope.get("segment_preview") else [] ),
             f"- Selected variables: {summary['features_selected']}",
             f"- Broad bins requested: {payload.get('requested_bins')}",
             f"- Knowledge Base version: {payload['knowledge'].get('version')}",
             "", "2. OUTCOME SUMMARY",
             "| Completed | Failed | Contextual findings | Awaiting review | Promoted | Closed |",
             "|---:|---:|---:|---:|---:|---:|",
             f"| {summary['features_completed']} | {summary['features_failed']} | "
             f"{summary['review_findings']} | {summary['awaiting_review']} | "
             f"{summary['issues_promoted']} | {summary['issues_closed']} |",
             "", "3. VARIABLE RESULTS",
             "| Variable | KB concept / basis | Expected | Observed | Strength | Outcome |",
             "|---|---|---|---|---|---|"]
    for feature in payload["features"]:
        basis = feature.get("canonical_feature") or feature.get("classification_source") or "User decision"
        lines.append(f"| {feature['feature']} | {basis} | "
                     f"{feature['expected_reference_direction']} | {feature['observed_direction']} | "
                     f"{feature['evidence_strength']} | {feature['conclusion']} |")
    lines.extend(["", "Direction rationales and supporting metrics"])
    for feature in payload["features"]:
        lines.extend([f"- {feature['feature']}",
                      f"  Rationale: {feature.get('expected_rationale') or 'Not recorded'}",
                      f"  Evidence: Spearman={feature['spearman']}; "
                      f"regression coefficient={feature['regression_coefficient']}; "
                      f"Pearson (display only)={feature['pearson_display_only']}; "
                      f"paired observations={feature['paired_observations']}; bins={feature['bin_count']}"])
        for segment_result in feature.get("segment_results") or []:
            lines.append(
                f"  Segmented result ({segment_result['label']}): expected="
                f"{segment_result['expected_reference_direction']}; observed="
                f"{segment_result['observed_direction']}; strength="
                f"{segment_result['evidence_strength']}; outcome="
                f"{segment_result['conclusion']}; paired observations="
                f"{segment_result['paired_observations']}"
            )
    lines.extend(["", "4. AI ASSISTANCE AND HUMAN REVIEW",
                  "AI is optional and is used only when a variable needs semantic assistance to "
                  "identify a suitable Knowledge Base concept. It does not calculate metrics or "
                  "determine the empirical result.", disclosure["statement"],
                  f"LLM calls made: {disclosure['llm_call_count']}",
                  "LLM influenced empirical verdict: No"])
    if payload["ai_reviews"]:
        for review in payload["ai_reviews"]:
            lines.extend([f"- Variable searched: {review['variable_searched']}",
                          f"  Why AI was requested: {review['why_required']}",
                          f"  AI outcome: {review['ai_outcome']}",
                          f"  AI-selected concept: {review.get('ai_selected_concept') or 'None'}",
                          f"  AI rationale / failure reason: {review.get('ai_rationale') or 'Not available'}",
                          f"  Final choice: {review.get('final_concept') or 'No KB concept'}; "
                          f"direction={review.get('final_expected_direction') or 'Not selected'}",
                          f"  Final rationale: {review.get('final_rationale') or 'Not recorded'}",
                          f"  Human review: {review['confirmation_statement']}"])
    else:
        lines.append("- No variable was submitted to AI. Expected directions came from the "
                     "governed Knowledge Base or direct user classification.")
    lines.extend(["", "5. FINDINGS AND ISSUES"])
    if payload["finding_actions"]:
        for action in payload["finding_actions"]:
            lines.append(f"- {action['feature']}: {action['state']}"
                         + (f" ({action['issue_row_id']})" if action.get("issue_row_id") else ""))
    else:
        lines.append("- No contextual findings required a user decision.")
    lines.extend(["", "6. RECOMMENDED ACTIONS"])
    lines.extend(f"- {value}" for value in payload["recommended_actions"])
    lines.extend(["", "7. INTERPRETATION LIMITS"])
    lines.extend(f"- {value}" for value in payload["limitations"])
    return "\n".join(lines)


def report_text(run_id: str) -> str:
    payload, _artifact, _reused = report_payload(run_id)
    return _report_text(payload)


def _render_pdf(payload: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError:  # pragma: no cover
        return _report_text(payload).encode("utf-8")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    def safe(value: Any) -> str:
        if value is None:
            return "-"
        return str(value).encode("latin-1", "replace").decode("latin-1")

    def section(title: str) -> None:
        pdf.ln(2)
        pdf.set_fill_color(239, 246, 255)
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 7, safe(title), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def paragraph(value: Any, *, bold: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 8.4)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 4.2, safe(value), new_x="LMARGIN", new_y="NEXT")

    def wraps(value: Any, width: float) -> list[str]:
        words = safe(value).split()
        if not words:
            return ["-"]
        output, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and pdf.get_string_width(candidate) > width - 3:
                output.append(current)
                current = word
            else:
                current = candidate
        output.append(current)
        return output

    def table(headers: list[str], rows: list[list[Any]], widths: list[float]) -> None:
        line_height = 4.1

        def draw(values: list[Any], *, header: bool = False) -> None:
            pdf.set_font("Helvetica", "B" if header else "", 7.3)
            cells = [wraps(value, width) for value, width in zip(values, widths)]
            height = max(len(value) for value in cells) * line_height + 1.4
            if pdf.get_y() + height > pdf.h - 12:
                pdf.add_page()
            start_x, start_y = pdf.get_x(), pdf.get_y()
            for index, (cell, width) in enumerate(zip(cells, widths)):
                x = start_x + sum(widths[:index])
                pdf.set_xy(x, start_y)
                pdf.set_fill_color(*( (226, 232, 240) if header else (255, 255, 255) ))
                pdf.rect(x, start_y, width, height, style="DF")
                # Lines are measured above. Draw them explicitly so FPDF cannot
                # re-wrap the final line and move only that fragment to a new page.
                for line_index, line in enumerate(cell):
                    baseline = start_y + .7 + (line_index + .8) * line_height
                    pdf.text(x + 1.2, baseline, line)
            pdf.set_xy(start_x, start_y + height)

        draw(headers, header=True)
        for row in rows:
            draw(row)
        pdf.ln(1.5)

    summary, scope = payload["summary"], payload["scope"]
    orientation = {"HIGHER_IS_WORSE": "Higher target value = higher risk",
                   "HIGHER_IS_BETTER": "Higher target value = lower risk"}.get(
                       scope.get("reference_orientation"), scope.get("reference_orientation"))
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Directional Consistency Analysis", new_x="LMARGIN", new_y="NEXT")
    paragraph("Governed diagnostic report", bold=True)
    section("1. Analysis overview")
    paragraph(payload["introduction"])
    table(["Execution detail", "Value"], [
        ["Report timestamp", payload["generated_at"]], ["Executor", payload["executor"]],
        ["Run ID", payload["run_id"]], ["Data item / snapshot", scope["item_id"]],
        ["Table", scope["table"]],
    ], [48, 138])
    table(["Analysis input", "Selection"], [
        ["Target or substitute", scope["reference_column"]],
        ["Target risk direction", orientation],
        ["Segment analysis", scope.get("segment_column") or "Overall portfolio only"],
        *([["Analysed segment", f"{scope['segment_preview']['baseline_count']} rows"],
           ["Complement", f"{scope['segment_preview']['current_count']} rows - not analysed"]]
          if scope.get("segment_preview") else []),
        ["Selected variables", summary["features_selected"]],
        ["Broad bins requested", payload.get("requested_bins")],
        ["Knowledge Base version", payload["knowledge"].get("version")],
    ], [48, 138])
    section("2. Outcome summary")
    table(["Completed", "Failed", "Findings", "Awaiting", "Promoted", "Closed"], [[
        summary["features_completed"], summary["features_failed"], summary["review_findings"],
        summary["awaiting_review"], summary["issues_promoted"], summary["issues_closed"],
    ]], [31, 31, 31, 31, 31, 31])
    section("3. Variable results")
    table(["Variable", "KB concept / basis", "Expected", "Observed", "Strength", "Outcome"], [[
        row["feature"], row.get("canonical_feature") or row.get("classification_source"),
        row["expected_reference_direction"], row["observed_direction"],
        row["evidence_strength"], row["conclusion"],
    ] for row in payload["features"]], [30, 40, 27, 27, 25, 37])
    paragraph("Direction rationale and supporting evidence", bold=True)
    for row in payload["features"]:
        detail_rows = [
            ["Direction rationale", row.get("expected_rationale") or "Not recorded"],
            ["Metrics", f"Spearman {row['spearman']}; regression {row['regression_coefficient']}; "
             f"Pearson (display only) {row['pearson_display_only']}"],
            ["Population", f"{row['paired_observations']} paired observations; "
             f"{row['bin_count']} bins; {row['segment_count']} segment results"],
        ]
        detail_rows.extend(["Segmented result", (
            f"{segment['label']}: expected {segment['expected_reference_direction']}; "
            f"observed {segment['observed_direction']}; {segment['evidence_strength']}; "
            f"{segment['conclusion']}; {segment['paired_observations']} paired observations"
        )] for segment in row.get("segment_results") or [])
        table([row["feature"], "Governed result"], detail_rows, [42, 144])
    section("4. AI assistance and human review")
    paragraph("AI is optional and is used only when a variable needs semantic assistance to "
              "identify a suitable Knowledge Base concept. It does not calculate metrics or "
              "determine the empirical result.")
    table(["Disclosure", "Recorded outcome"], [
        ["Usage", payload["inference_disclosure"]["statement"]],
        ["Empirical verdict influenced by AI", "No"],
    ], [55, 131])
    if payload["ai_reviews"]:
        for review in payload["ai_reviews"]:
            table(["AI review", review.get("variable_searched") or "Unknown variable"], [
                ["Why requested", review["why_required"]],
                ["AI outcome", f"{review['ai_outcome']}; concept: "
                 f"{review.get('ai_selected_concept') or 'none'}"],
                ["AI rationale / failure", review.get("ai_rationale") or "Not available"],
                ["Final user choice", f"{review.get('final_concept') or 'No KB concept'}; "
                 f"direction: {review.get('final_expected_direction') or 'not selected'}"],
                ["Final rationale", review.get("final_rationale") or "Not recorded"],
                ["Human confirmation", review["confirmation_statement"]],
            ], [44, 142])
    else:
        paragraph("No variable was submitted to AI. Expected directions came from the governed "
                  "Knowledge Base or direct user classification.")
    section("5. Findings and issues")
    if payload["finding_actions"]:
        table(["Variable", "State", "Issue reference"], [[
            row["feature"], row["state"], row.get("issue_row_id") or "-",
        ] for row in payload["finding_actions"]], [65, 55, 66])
    else:
        paragraph("No contextual findings required a user decision.")
    section("6. Recommended actions")
    for value in payload["recommended_actions"]:
        paragraph(f"- {value}")
    section("7. Interpretation limits")
    for value in payload["limitations"]:
        paragraph(f"- {value}")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    payload, artifact, reused = report_payload(run_id)
    return _render_pdf(payload), {
        "run_id": run_id,
        "report_artifact": {"artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type,
                            "payload_hash": artifact.payload_hash, "reused": reused},
        "filename": f"directionality-{run_id}.pdf",
    }


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]


__all__ = ["execute_now", "hydrate_result", "report_document", "report_payload",
           "report_pdf", "report_text", "run", "run_results"]
