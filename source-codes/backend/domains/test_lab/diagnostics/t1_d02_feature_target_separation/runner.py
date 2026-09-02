"""Execution, persistence, and reviewed issue hand-off for diagnostic #2."""
from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any, Generator

import system_db as db
from ai.v2 import service as item_service
from analysis_runtime.contracts import stable_fingerprint
from domains.aar.repository import AnalysisArtifactRepository

from . import manifest as manifest_mod
from . import assess_snapshot
from domains.test_lab.shared.run_state import DONE, FAILED, RUNNING

DIAGNOSTIC_ID = 2
AGENT = "feature_target_separation_engine"
REPORT_ARTIFACT_TYPE = "feature_target_separation_report"
REPORT_RENDERER_VERSION = "1"
REPORT_METHODOLOGY = "univariate_tree_auc_gini_supervised_iv_woe_v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _threshold_values(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: spec["value"] for key, spec in manifest["thresholds"].items()}


def _threshold_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {key: spec["source"] for key, spec in manifest["thresholds"].items()}


def _artifact_map(outcome: Any) -> dict[str, dict[str, str]]:
    repo = AnalysisArtifactRepository()
    mapped: dict[str, dict[str, str]] = {}
    for artifact_type, ids in outcome.artifact_ids.items():
        for artifact_id in ids:
            metadata = repo.get_metadata(artifact_id)
            if metadata.feature:
                mapped.setdefault(metadata.feature, {})[artifact_type] = artifact_id
    return mapped


def _candidate_text(candidate: dict[str, Any]) -> str:
    feature = candidate["feature"]
    if candidate["candidate_type"] == "target_leakage":
        return f"{feature} exceeds one or more target-leakage review thresholds."
    return f"{feature} falls below one or more discrimination review thresholds."


def persist(manifest: dict[str, Any], outcome: Any) -> dict[str, Any]:
    run_id = manifest["run_id"]
    now = db.now_ist()
    artifacts = _artifact_map(outcome)
    candidates = {row["feature"]: row for row in outcome.candidate_findings}
    results_by_feature = {}
    category_counts = {"suspicious": 0, "strong": 0, "medium": 0, "weak": 0}
    candidate_counts = {"target_leakage": 0, "poor_discrimination": 0}

    for feature_result in outcome.response.results:
        feature = feature_result.feature
        category_counts[feature_result.category] += 1
        candidate = candidates.get(feature)
        if candidate:
            candidate_counts[candidate["candidate_type"]] += 1
        result_id = _id("dres")
        results_by_feature[feature] = result_id
        roc = feature_result.roc
        binning = feature_result.binning
        db.insert("diag_results", {
            "result_id": result_id, "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID,
            "entity_or_table": feature, "decision_type": "candidate_flag",
            "verdict": None, "review_state": "open",
            "metrics_json": {
                "result_kind": "feature",
                "analysis_fingerprint": manifest_mod.analysis_fingerprint(manifest, feature),
                "feature": feature,
                "target": outcome.response.target.target,
                "target_type": outcome.response.target.target_type,
                "auc": feature_result.auc,
                "gini": roc.primary_gini if roc else None,
                "iv": feature_result.iv,
                "category": feature_result.category,
                "leakage_status": feature_result.leakage_status,
                "candidate": candidate,
                "rows_evaluated": (roc.rows_evaluated if roc else
                                   binning.rows_evaluated if binning else None),
                "missing_rows": roc.generic_missing_rows if roc else None,
                "errors": list(feature_result.errors),
                "warnings": list(binning.warnings) if binning else [],
                "coarse_bins": ([row.model_dump(mode="json") for row in binning.coarse_bins]
                                if binning else []),
                "localized_candidates": [
                    row.model_dump(mode="json")
                    for row in (outcome.response.localized_leakage_scan.candidates
                                if outcome.response.localized_leakage_scan else [])
                    if row.feature == feature
                ],
                "artifact_ids": artifacts.get(feature, {}),
            },
            "thresholds_used_json": {
                "values": _threshold_values(manifest),
                "sources": _threshold_sources(manifest),
            },
            "scope_counts_json": {
                "rows_evaluated": (roc.rows_evaluated if roc else
                                   binning.rows_evaluated if binning else 0),
                "metrics_available": int(feature_result.auc is not None) + int(feature_result.iv is not None),
            },
            "na_reason": None, "created_at": now,
        })

    finding_ids = []
    for seq, candidate in enumerate(outcome.candidate_findings):
        finding_id = _id("dfind")
        finding_ids.append(finding_id)
        thresholds = candidate["thresholds"]
        db.insert("diag_findings", {
            "finding_id": finding_id,
            "result_id": results_by_feature[candidate["feature"]],
            "run_id": run_id,
            "rule_id": f"feature_target:{candidate['feature']}:{candidate['candidate_type']}",
            "kb_rule_id": None,
            "severity": "HIGH" if candidate["candidate_type"] == "target_leakage" else "MATERIAL",
            "outcome": "CANDIDATE",
            "violation_count": 0,
            "rate": candidate.get("auc"),
            "tolerance": (thresholds["leakage_auc"] if candidate["candidate_type"] == "target_leakage"
                          else thresholds["poor_auc"]),
            "exceptions_json": {"count": 0, "notes": []},
            "evidence_json": [{
                "feature": candidate["feature"], "auc": candidate.get("auc"),
                "gini": candidate.get("gini"), "iv": candidate.get("iv"),
                "category": candidate.get("category"),
                "candidate_type": candidate["candidate_type"],
                "reasons": ", ".join(candidate["candidate_reasons"]),
            }],
            "pattern": candidate["candidate_type"],
            "pattern_detail": ", ".join(candidate["candidate_reasons"]),
            "regulatory_ref": None,
            "rule_text": _candidate_text(candidate),
            "rule_type": "statistical",
            "framework": None,
            "entity": candidate["feature"],
            "resolved_roles_json": {
                "target": outcome.response.target.target,
                "feature": candidate["feature"],
            },
            "tables_used": manifest["scope"]["table"],
            "scope_rows_evaluated": outcome.response.target.evaluated_rows,
            "scope_rows_skipped": outcome.response.target.dropped_target_rows,
            "na_reason": None,
            "review_state": "open", "seq": seq, "created_at": now,
        })

    summary_id = _id("dres")
    rollup = {
        "features": len(outcome.response.results),
        "candidates": len(finding_ids),
        "target_leakage": candidate_counts["target_leakage"],
        "poor_discrimination": candidate_counts["poor_discrimination"],
        "partial_features": sum(1 for row in outcome.response.results if row.errors),
        "categories": category_counts,
    }
    db.insert("diag_results", {
        "result_id": summary_id, "run_id": run_id, "diagnostic_id": DIAGNOSTIC_ID,
        "entity_or_table": manifest["scope"]["table"],
        "decision_type": "candidate_flag", "verdict": None, "review_state": "open",
        "metrics_json": {
            "result_kind": "run_summary", "status": outcome.response.status,
            "target": outcome.response.target.model_dump(mode="json"),
            "rollup": rollup,
            "methodology": outcome.response.methodology,
            "artifact_ids": outcome.artifact_ids,
        },
        "thresholds_used_json": {
            "values": _threshold_values(manifest), "sources": _threshold_sources(manifest),
        },
        "scope_counts_json": {
            "features_selected": len(manifest["scope"]["selected_features"]),
            "features_completed": len(outcome.response.results),
        },
        "na_reason": None, "created_at": now,
    })
    return {"result_id": summary_id, "findings": len(finding_ids), "rollup": rollup,
            "status": outcome.response.status}


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    run_row = manifest_mod._run(run_id)
    if run_row["status"] == DONE:
        results = db.query("diag_results", run_id=run_id)
        summary = next((row for row in results
                        if (row.get("metrics_json") or {}).get("result_kind") == "run_summary"), None)
        rollup = (summary.get("metrics_json") or {}).get("rollup", {}) if summary else {}
        yield {"phase": "start", "agent": AGENT, "run_id": run_id,
               "thought": "Replaying a completed run."}
        yield {"phase": "done", "agent": AGENT, "run_id": run_id,
               "result_id": summary.get("result_id") if summary else None,
               "rollup": rollup, "thought": "Run already complete."}
        return
    if run_row["status"] == manifest_mod.DRAFT:
        # Re-read the persisted Step 3 inventory immediately before freezing
        # the workflow. The scope gate may have been opened before the final
        # schema-role save completed; saved roles are authoritative for the
        # executable feature set.
        manifest_mod.refresh_draft_scope(run_id, actor)
        manifest = manifest_mod.freeze(run_id, actor)
    elif run_row["status"] == RUNNING:
        manifest = run_row["manifest_json"]
    else:
        raise RuntimeError(f"run {run_id} cannot execute from status {run_row['status']}")

    selected = manifest["scope"].get("execution_features") or manifest["scope"]["selected_features"]
    total = len(selected) * 2
    yield {"phase": "start", "agent": AGENT, "run_id": run_id, "total": total,
           "thought": f"Assessing {len(selected)} feature(s) against confirmed target "
                      f"{manifest['target']['column']}."}

    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def progress(done: int, count: int, feature: str, stage: str) -> None:
        events.put(("progress", {"done": done, "total": count, "feature": feature, "stage": stage}))

    def feature_preview(preview: dict[str, Any]) -> None:
        events.put(("feature_result", preview))

    def worker() -> None:
        try:
            values = _threshold_values(manifest)
            separation_keys = set(manifest_mod.SeparationThresholds.model_fields)
            outcome = assess_snapshot(
                manifest["item_id"], table=manifest["scope"]["table"],
                feature_columns=selected,
                target_type=manifest["target"]["target_type"],
                positive_class=manifest["target"].get("positive_class"),
                missing_target_action=manifest["target"]["missing_target_action"],
                percentile_bins=manifest["parameters"]["percentile_bins"],
                analysis_constraints=manifest["parameters"]["analysis_constraints"],
                binning_constraints=manifest["parameters"]["binning_constraints"],
                separation_thresholds={key: values[key] for key in separation_keys},
                finding_thresholds={key: values[key] for key in manifest_mod.FINDING_DEFAULTS},
                actor=actor, run_id=run_id, progress_callback=progress,
                feature_preview_callback=feature_preview,
            )
            events.put(("outcome", outcome))
        except Exception as exc:  # carried internally; router sanitizes it
            events.put(("error", exc))

    thread = threading.Thread(target=worker, name=f"diagnostic-2-{run_id}", daemon=True)
    thread.start()
    outcome = None
    while outcome is None:
        kind, payload = events.get()
        if kind == "progress":
            stage_label = "ROC/AUC/Gini" if payload["stage"] == "roc" else "IV/WOE binning"
            yield {"phase": "progress", "agent": AGENT, **payload,
                   "thought": f"[{payload['done']}/{payload['total']}] {payload['feature']} - {stage_label}"}
        elif kind == "feature_result":
            stage = payload.get("stage")
            yield {"phase": "feature_result", "agent": AGENT, "run_id": run_id,
                   "preview": payload,
                   "thought": (f"{payload['feature']} ROC complete; IV pending."
                               if stage == "roc" else
                               f"{payload['feature']} completed; preliminary results are ready.")}
        elif kind == "error":
            raise payload
        else:
            outcome = payload
    thread.join()

    if outcome.response.status in {"invalid", "action_required"}:
        messages = "; ".join(message.message for message in outcome.response.target.messages)
        raise ValueError(messages or "target configuration requires review")
    summary = persist(manifest, outcome)
    db.update("diag_runs", {"run_id": run_id}, {"status": DONE, "finished_at": db.now_ist()})
    yield {"phase": "done", "agent": AGENT, "run_id": run_id,
           "result_id": summary["result_id"], "rollup": summary["rollup"],
           "findings": summary["findings"], "issues": {"created": []},
           "thought": f"Run complete - {summary['rollup']['features']} feature(s), "
                      f"{summary['findings']} candidate(s) for review."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    last = {}
    for event in run(run_id, actor):
        last = event
    return last


def run_results(run_id: str) -> dict[str, Any]:
    """Use the shared persisted-result projection, then router-level #2 hydration."""
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


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "not available"
    return f"{float(value):.{digits}f}"


def _threshold_source_label(thresholds: dict[str, Any], *keys: str) -> str:
    sources = [str((thresholds.get(key) or {}).get("source") or "not recorded")
               for key in keys]
    if len(set(sources)) == 1:
        return sources[0]
    return " / ".join(f"{key.split('_')[-1].upper()}: {source}"
                      for key, source in zip(keys, sources))


def _reason_text(reason: str, metrics: dict[str, Any], thresholds: dict[str, Any]) -> str:
    labels = {
        "auc_leakage_threshold": (
            f"AUC {_number(metrics.get('auc'))} met the leakage-review threshold "
            f"of {_number(thresholds.get('leakage_auc'))}"
        ),
        "iv_leakage_threshold": (
            f"IV {_number(metrics.get('iv'))} met the leakage-review threshold "
            f"of {_number(thresholds.get('leakage_iv'))}"
        ),
        "localized_leakage": (
            f"{len(metrics.get('localized_candidates') or [])} localized high-concentration "
            "pattern(s) met the governed scan criteria"
        ),
        "auc_poor_discrimination": (
            f"AUC {_number(metrics.get('auc'))} was below the poor-discrimination threshold "
            f"of {_number(thresholds.get('poor_auc'))}"
        ),
        "iv_poor_discrimination": (
            f"IV {_number(metrics.get('iv'))} was below the poor-discrimination threshold "
            f"of {_number(thresholds.get('poor_iv'))}"
        ),
    }
    return labels.get(reason, str(reason).replace("_", " "))


def _outcome_rationale(metrics: dict[str, Any], thresholds: dict[str, Any]) -> str:
    category = str(metrics.get("category") or "unavailable").title()
    auc, iv = metrics.get("auc"), metrics.get("iv")
    category_keys = {
        "suspicious": ("suspicious_auc", "suspicious_iv"),
        "strong": ("strong_auc", "strong_iv"),
        "medium": ("medium_auc", "medium_iv"),
    }
    evidence = []
    keys = category_keys.get(str(metrics.get("category")))
    if keys:
        if auc is not None and thresholds.get(keys[0]) is not None \
                and float(auc) >= float(thresholds[keys[0]]):
            evidence.append(f"AUC {_number(auc)} met the {keys[0].replace('_', ' ')} "
                            f"boundary of {_number(thresholds[keys[0]])}")
        if iv is not None and thresholds.get(keys[1]) is not None \
                and float(iv) >= float(thresholds[keys[1]]):
            evidence.append(f"IV {_number(iv)} met the {keys[1].replace('_', ' ')} "
                            f"boundary of {_number(thresholds[keys[1]])}")
    else:
        available = [f"AUC {_number(auc)}" if auc is not None else None,
                     f"IV {_number(iv)}" if iv is not None else None]
        evidence.append(", ".join(value for value in available if value)
                        + " remained below the medium-separation boundaries")
    basis = "; ".join(evidence) or (
        f"the available AUC ({_number(auc)}) and IV ({_number(iv)}) were evaluated "
        "under the governed precedence rules"
    )
    reasons = (metrics.get("candidate") or {}).get("candidate_reasons") or []
    review = (" Review rationale: " + "; ".join(
        _reason_text(reason, metrics, thresholds) for reason in reasons) + "."
        if reasons else
        " No issue-review threshold was triggered; promotion remains an explicit SME override."
    )
    return (f"{category} separation because {basis}. The displayed category uses the strongest "
            f"qualifying AUC or IV boundary, not an average of the two.{review}")


def report_payload(run_id: str) -> tuple[dict[str, Any], Any, bool]:
    """Build and retain the governed run-level T1-D02 report source."""
    projected = run_results(run_id)
    run_row = manifest_mod._run(run_id)
    manifest = run_row["manifest_json"]
    thresholds = _threshold_values(manifest)
    features, source_refs, finding_actions = [], {}, []
    workflow_counts = {"awaiting_review": 0, "promoted": 0, "closed": 0,
                       "dismissed": 0}
    summary_row = None
    for row in projected["results"]:
        metrics = row.get("metrics_json") or {}
        if metrics.get("result_kind") == "run_summary":
            summary_row = row
            continue
        if metrics.get("result_kind") != "feature":
            continue
        artifact_ids = metrics.get("artifact_ids") or {}
        for artifact_type, role in (("roc_feature", "roc_evidence"),
                                    ("iv", "iv_evidence")):
            artifact_id = artifact_ids.get(artifact_type)
            if artifact_id:
                source_refs[artifact_id] = role
        roc, binning = metrics.get("roc_detail") or {}, metrics.get("binning_detail") or {}
        findings = row.get("findings") or []
        finding = findings[0] if findings else None
        state = _workflow_state(finding) if finding else "no_review_required"
        for finding_row in findings:
            finding_state = _workflow_state(finding_row)
            workflow_counts[finding_state] += 1
            dispositions = finding_row.get("dispositions") or []
            finding_actions.append({
                "feature": metrics.get("feature"),
                "finding_id": finding_row["finding_id"], "state": finding_state,
                "candidate_type": (metrics.get("candidate") or {}).get("candidate_type"),
                "rationale": (dispositions[-1].get("reason") if dispositions else None),
                "action": (dispositions[-1].get("action") if dispositions else None),
                "issue_row_id": (finding_row.get("existing_issue") or {}).get("issue_row_id"),
                "issue_status": (finding_row.get("existing_issue") or {}).get("status"),
            })
        features.append({
            "feature": metrics.get("feature"),
            "feature_type": roc.get("feature_type") or binning.get("feature_type"),
            "rows_evaluated": metrics.get("rows_evaluated"),
            "missing_rows": metrics.get("missing_rows"),
            "auc": metrics.get("auc"), "gini": metrics.get("gini"),
            "iv": metrics.get("iv"), "category": metrics.get("category"),
            "robustness": roc.get("robustness"),
            "auc_range": [roc.get("auc_min"), roc.get("auc_max")],
            "coarse_bin_count": len(binning.get("coarse_bins") or metrics.get("coarse_bins") or []),
            "localized_candidate_count": len(metrics.get("localized_candidates") or []),
            "candidate_type": (metrics.get("candidate") or {}).get("candidate_type"),
            "candidate_reasons": (metrics.get("candidate") or {}).get("candidate_reasons") or [],
            "review_state": state,
            "outcome_rationale": _outcome_rationale(metrics, thresholds),
            "warnings": metrics.get("warnings") or [], "errors": metrics.get("errors") or [],
            "artifact_ids": artifact_ids,
        })
    summary_metrics = (summary_row or {}).get("metrics_json") or {}
    rollup = summary_metrics.get("rollup") or {}
    resolved_target = summary_metrics.get("target") or {}
    category_counts = rollup.get("categories") or {}
    report = {
        "artifact_kind": REPORT_ARTIFACT_TYPE, "schema_version": 1,
        "report_id": f"rpt_{run_id}", "run_id": run_id,
        "generated_at": run_row.get("finished_at") or db.now_ist(),
        "executor": manifest.get("frozen_by") or manifest.get("created_by") or "system",
        "introduction": (
            "This diagnostic evaluates how strongly each selected feature separates the governed "
            "target on its own. A primary univariate decision tree supplies AUC and Gini evidence; "
            "supervised coarse binning supplies IV and WOE evidence. The strongest qualifying AUC "
            "or IV boundary determines the displayed separation category, while separate review "
            "thresholds identify potential leakage or poor discrimination for SME judgment."
        ),
        "scope": {"item_id": run_row["item_id"], "table": manifest["scope"].get("table"),
                  "target": resolved_target.get("target") or manifest["target"].get("column"),
                  "target_type": resolved_target.get("target_type") or manifest["target"].get("target_type"),
                  "positive_class": manifest["target"].get("positive_class"),
                  "target_mapping": resolved_target.get("target_mapping") or {},
                  "missing_target_action": manifest["target"].get("missing_target_action")},
        "summary": {"features_selected": len(manifest["scope"].get("selected_features") or []),
                    "features_completed": rollup.get("features", len(features)),
                    "partial_features": rollup.get("partial_features", 0),
                    "review_findings": rollup.get("candidates", len(finding_actions)),
                    "target_leakage": rollup.get("target_leakage", 0),
                    "poor_discrimination": rollup.get("poor_discrimination", 0),
                    "awaiting_review": workflow_counts["awaiting_review"],
                    "issues_promoted": workflow_counts["promoted"],
                    "issues_closed": workflow_counts["closed"],
                    "findings_dismissed": workflow_counts["dismissed"],
                    "categories": category_counts},
        "features": features, "finding_actions": finding_actions,
        "decision_actions": projected.get("decisions") or [],
        "thresholds": {key: {"value": value,
                             "source": manifest["thresholds"][key].get("source")}
                       for key, value in thresholds.items()},
        "parameters": manifest.get("parameters") or {},
        "methodology": REPORT_METHODOLOGY,
        "limitations": [
            "This is in-sample, univariate association evidence and does not establish causality.",
            "A high AUC or IV is a review signal, not proof of target leakage; feature timing and business provenance require RCA review.",
            "Low univariate discrimination does not prove a feature is unusable in a multivariate model or business rule.",
            "Supervised bins depend on the governed target, constraints, and retained missing/special-value treatment.",
            "The primary decision tree is an explanatory diagnostic model, not a production model specification.",
        ],
        "recommended_actions": ([
            "Resolve every awaiting review finding by promotion or dismissal with a recorded rationale.",
            "Investigate promoted leakage candidates in RCA, including feature timing, provenance, and bin concentration.",
            "Perform bin editing or universal bin promotion only within the governed RCA workflow.",
        ] if finding_actions else [
            "Retain the governed evidence and repeat the diagnostic for the next relevant data delivery.",
        ]),
        "source_artifact_ids": list(source_refs),
    }
    repo = AnalysisArtifactRepository()
    metadata = [repo.get_metadata(value) for value in source_refs]
    if not metadata:
        raise RuntimeError("a feature-target separation report requires retained ROC or IV evidence")
    identity_inputs = {
        "run_id": run_id, "renderer_version": REPORT_RENDERER_VERSION,
        "source_hashes": [value.payload_hash for value in metadata],
        "decision_actions_hash": stable_fingerprint(report["decision_actions"]),
        "finding_actions_hash": stable_fingerprint(finding_actions),
    }
    saved = repo.save(
        report, artifact_type=REPORT_ARTIFACT_TYPE,
        asset_id=metadata[0].asset_id, snapshot_id=run_row["item_id"],
        comparison_snapshot_id=None,
        population_fingerprint=stable_fingerprint({"sources": identity_inputs["source_hashes"]}),
        target_fingerprint=(metadata[0].target_fingerprint
                            or stable_fingerprint(manifest.get("target") or {})),
        feature=None, methodology_fingerprint=stable_fingerprint(identity_inputs),
        scope="diagnostic_local", workflow_id=None,
        owner_id="t1_d02_feature_target_separation",
        table=manifest["scope"].get("table"),
        features=tuple(sorted(row["feature"] for row in features)),
        source_artifacts=tuple({"artifact_id": artifact_id, "role": role}
                               for artifact_id, role in source_refs.items()),
        identity_inputs=identity_inputs, run_id=run_id,
        created_by=manifest.get("frozen_by") or "system",
    )
    return report, saved.artifact, saved.outcome == "reused"


def _report_text(payload: dict[str, Any]) -> str:
    summary, scope = payload["summary"], payload["scope"]
    categories = summary.get("categories") or {}
    thresholds = payload["thresholds"]
    lines = ["SINGLE-FEATURE TARGET SEPARATION ANALYSIS REPORT", "",
             "1. ANALYSIS OVERVIEW", payload["introduction"], "", "Execution details",
             f"- Report timestamp: {payload['generated_at']}",
             f"- Executor: {payload['executor']}", f"- Run ID: {payload['run_id']}",
             f"- Data item / snapshot: {scope['item_id']}", f"- Table: {scope['table']}",
             "", "Analysis inputs", f"- Target: {scope['target']}",
             f"- Target type: {scope['target_type']}",
             f"- Positive class: {scope.get('positive_class') if scope.get('positive_class') is not None else 'Not applicable / automatically resolved'}",
             f"- Missing-target action: {scope['missing_target_action']}",
             f"- Selected variables: {summary['features_selected']}",
             "", "Separation category thresholds",
             "| Category | AUC | IV | Source |", "|---|---:|---:|---|"]
    for level in ("suspicious", "strong", "medium"):
        auc_key, iv_key = f"{level}_auc", f"{level}_iv"
        lines.append(f"| {level.title()} | {thresholds[auc_key]['value']} | "
                     f"{thresholds[iv_key]['value']} | "
                     f"{_threshold_source_label(thresholds, auc_key, iv_key)} |")
    lines.extend(["", "Issue-review thresholds",
                  "| Review signal | AUC | IV | Source |", "|---|---:|---:|---|",
                  f"| Potential leakage | {thresholds['leakage_auc']['value']} | "
                  f"{thresholds['leakage_iv']['value']} | "
                  f"{_threshold_source_label(thresholds, 'leakage_auc', 'leakage_iv')} |",
                  f"| Poor discrimination | {thresholds['poor_auc']['value']} | "
                  f"{thresholds['poor_iv']['value']} | "
                  f"{_threshold_source_label(thresholds, 'poor_auc', 'poor_iv')} |"])
    lines.extend(["", "2. OUTCOME SUMMARY",
                  "| Completed | Partial | Findings | Awaiting | Promoted | Closed |",
                  "|---:|---:|---:|---:|---:|---:|",
                  f"| {summary['features_completed']} | {summary['partial_features']} | "
                  f"{summary['review_findings']} | {summary['awaiting_review']} | "
                  f"{summary['issues_promoted']} | {summary['issues_closed']} |",
                  "", "Separation categories",
                  f"- Suspicious: {categories.get('suspicious', 0)}; Strong: {categories.get('strong', 0)}; "
                  f"Medium: {categories.get('medium', 0)}; Weak: {categories.get('weak', 0)}",
                  f"- Target-leakage candidates: {summary['target_leakage']}; "
                  f"poor-discrimination candidates: {summary['poor_discrimination']}",
                  "", "3. VARIABLE RESULTS",
                  "| Variable | Type | Population | AUC | Gini | IV | Category | Review state |",
                  "|---|---|---:|---:|---:|---:|---|---|"])
    for feature in payload["features"]:
        lines.append(f"| {feature['feature']} | {feature.get('feature_type') or '-'} | "
                     f"{feature.get('rows_evaluated') or 0} | {_number(feature.get('auc'))} | "
                     f"{_number(feature.get('gini'))} | {_number(feature.get('iv'))} | "
                     f"{feature.get('category')} | {feature.get('review_state')} |")
    lines.extend(["", "Outcome rationales and supporting evidence"])
    for feature in payload["features"]:
        lines.extend([f"- {feature['feature']}: {feature['outcome_rationale']}",
                      f"  Supporting evidence: robustness={feature.get('robustness') or 'not available'}; "
                      f"coarse bins={feature['coarse_bin_count']}; localized candidates="
                      f"{feature['localized_candidate_count']}; missing rows={feature.get('missing_rows') or 0}."])
        if feature["warnings"] or feature["errors"]:
            lines.append("  Availability notes: " + "; ".join(
                [*feature["warnings"], *feature["errors"]]))
    lines.extend(["", "4. FINDINGS AND ISSUES"])
    if payload["finding_actions"]:
        for action in payload["finding_actions"]:
            details = f"- {action['feature']}: {action['state']}"
            if action.get("issue_row_id"):
                details += f" ({action['issue_row_id']})"
            lines.append(details)
            if action.get("rationale"):
                lines.append(f"  Recorded rationale: {action['rationale']}")
    else:
        lines.append("- No finding required a user decision.")
    lines.extend(["", "5. RECOMMENDED ACTIONS"])
    lines.extend(f"- {value}" for value in payload["recommended_actions"])
    lines.extend(["", "6. INTERPRETATION LIMITS"])
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
        pdf.ln(2); pdf.set_fill_color(239, 246, 255); pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 7, safe(title), fill=True, new_x="LMARGIN", new_y="NEXT"); pdf.ln(1)

    def paragraph(value: Any, *, bold: bool = False) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", 8.4); pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 4.2, safe(value), new_x="LMARGIN", new_y="NEXT")

    def wraps(value: Any, width: float) -> list[str]:
        words = safe(value).split()
        if not words: return ["-"]
        output, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and pdf.get_string_width(candidate) > width - 3:
                output.append(current); current = word
            else: current = candidate
        output.append(current)
        return output

    def table(headers: list[str], rows: list[list[Any]], widths: list[float],
              row_fills: list[tuple[int, int, int] | None] | None = None) -> None:
        line_height = 4.1
        def draw(values: list[Any], *, header: bool = False,
                 fill: tuple[int, int, int] | None = None) -> None:
            pdf.set_font("Helvetica", "B" if header else "", 7.3)
            cells = [wraps(value, width) for value, width in zip(values, widths)]
            height = max(len(value) for value in cells) * line_height + 1.4
            if pdf.get_y() + height > pdf.h - 12: pdf.add_page()
            start_x, start_y = pdf.get_x(), pdf.get_y()
            for index, (cell, width) in enumerate(zip(cells, widths)):
                x = start_x + sum(widths[:index]); pdf.set_xy(x, start_y)
                pdf.set_fill_color(*((226, 232, 240) if header else fill or (255, 255, 255)))
                pdf.rect(x, start_y, width, height, style="DF"); pdf.set_xy(x + 1.2, start_y + .7)
                pdf.multi_cell(width - 2.4, line_height, "\n".join(cell))
            pdf.set_xy(start_x, start_y + height)
        draw(headers, header=True)
        for index, row in enumerate(rows):
            draw(row, fill=(row_fills[index] if row_fills and index < len(row_fills) else None))
        pdf.ln(1.5)

    summary, scope = payload["summary"], payload["scope"]
    categories = summary.get("categories") or {}
    pdf.set_text_color(15, 23, 42); pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Single-Feature Target Separation Analysis", new_x="LMARGIN", new_y="NEXT")
    paragraph("Governed diagnostic report", bold=True)
    section("1. Analysis overview"); paragraph(payload["introduction"])
    table(["Execution detail", "Value"], [
        ["Report timestamp", payload["generated_at"]], ["Executor", payload["executor"]],
        ["Run ID", payload["run_id"]], ["Data item / snapshot", scope["item_id"]],
        ["Table", scope["table"]],
    ], [48, 138])
    table(["Analysis input", "Selection"], [
        ["Target", scope["target"]], ["Target type", scope["target_type"]],
        ["Positive class", scope.get("positive_class") if scope.get("positive_class") is not None
         else "Not applicable / automatically resolved"],
        ["Missing-target action", scope["missing_target_action"]],
        ["Selected variables", summary["features_selected"]],
    ], [48, 138])
    thresholds = payload["thresholds"]
    paragraph("Separation category thresholds", bold=True)
    table(["Category", "AUC", "IV", "Source"], [[
        level.title(), thresholds[f"{level}_auc"]["value"],
        thresholds[f"{level}_iv"]["value"],
        _threshold_source_label(thresholds, f"{level}_auc", f"{level}_iv"),
    ] for level in ("suspicious", "strong", "medium")], [42, 26, 26, 92])
    paragraph("Issue-review thresholds", bold=True)
    table(["Review signal", "AUC", "IV", "Source"], [
        ["Potential leakage", thresholds["leakage_auc"]["value"],
         thresholds["leakage_iv"]["value"],
         _threshold_source_label(thresholds, "leakage_auc", "leakage_iv")],
        ["Poor discrimination", thresholds["poor_auc"]["value"],
         thresholds["poor_iv"]["value"],
         _threshold_source_label(thresholds, "poor_auc", "poor_iv")],
    ], [50, 26, 26, 84], row_fills=[(254, 226, 226), (254, 243, 199)])
    section("2. Outcome summary")
    table(["Completed", "Partial", "Findings", "Awaiting", "Promoted", "Closed"], [[
        summary["features_completed"], summary["partial_features"], summary["review_findings"],
        summary["awaiting_review"], summary["issues_promoted"], summary["issues_closed"],
    ]], [31, 31, 31, 31, 31, 31])
    table(["Suspicious", "Strong", "Medium", "Weak", "Leakage", "Poor discrimination"], [[
        categories.get("suspicious", 0), categories.get("strong", 0),
        categories.get("medium", 0), categories.get("weak", 0),
        summary["target_leakage"], summary["poor_discrimination"],
    ]], [31, 31, 31, 31, 31, 31])
    section("3. Variable results")
    table(["Variable", "Type", "Population", "AUC", "Gini", "IV", "Category", "Review"], [[
        row["feature"], row.get("feature_type") or "-", row.get("rows_evaluated") or 0,
        _number(row.get("auc")), _number(row.get("gini")), _number(row.get("iv")),
        row.get("category"), row.get("review_state"),
    ] for row in payload["features"]], [31, 20, 25, 19, 19, 19, 25, 28])
    paragraph("Outcome rationales and supporting evidence", bold=True)
    for row in payload["features"]:
        detail_rows = [
            ["Outcome rationale", row["outcome_rationale"]],
            ["Supporting evidence", f"Robustness: {row.get('robustness') or 'not available'}; "
             f"coarse bins: {row['coarse_bin_count']}; localized candidates: "
             f"{row['localized_candidate_count']}; missing rows: {row.get('missing_rows') or 0}"],
        ]
        if row["warnings"] or row["errors"]:
            detail_rows.append(["Availability notes", "; ".join([*row["warnings"], *row["errors"]])])
        table([row["feature"], "Governed result"], detail_rows, [42, 144])
    section("4. Findings and issues")
    if payload["finding_actions"]:
        table(["Variable", "State", "Issue reference", "Recorded rationale"], [[
            row["feature"], row["state"], row.get("issue_row_id") or "-",
            row.get("rationale") or "Awaiting a recorded disposition",
        ] for row in payload["finding_actions"]], [38, 31, 45, 72])
    else: paragraph("No finding required a user decision.")
    section("5. Recommended actions")
    for value in payload["recommended_actions"]: paragraph(f"- {value}")
    section("6. Interpretation limits")
    for value in payload["limitations"]: paragraph(f"- {value}")
    return bytes(pdf.output())


def report_document(run_id: str) -> tuple[bytes, dict[str, Any]]:
    payload, artifact, reused = report_payload(run_id)
    return _render_pdf(payload), {
        "run_id": run_id,
        "report_artifact": {"artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type,
                            "payload_hash": artifact.payload_hash, "reused": reused},
        "filename": f"feature-target-separation-{run_id}.pdf",
    }


def report_pdf(run_id: str) -> bytes:
    return report_document(run_id)[0]


class CandidateIssueConflict(RuntimeError):
    def __init__(self, issue_row_id: str):
        self.issue_row_id = issue_row_id
        super().__init__(f"Feature already has promoted issue {issue_row_id}; explicit overwrite is required.")


def _feature_issue(item_id: str, table_name: str, feature: str) -> dict[str, Any] | None:
    rows = db.execute(
        "SELECT * FROM issues_v2 WHERE item_id=? AND diagnostic_id=? AND table_name=? "
        "AND COALESCE(status, '') <> 'Superseded' ORDER BY created_at, issue_row_id",
        [item_id, DIAGNOSTIC_ID, table_name],
    )
    matches = [row for row in rows if feature in (row.get("columns_json") or [])]
    if not matches:
        return None
    with_rca = [row for row in matches if db.query_one("rca_cases", issue_row_id=row["issue_row_id"])]
    canonical = (with_rca or matches)[0]
    for duplicate in matches:
        if duplicate["issue_row_id"] != canonical["issue_row_id"]:
            db.update("issues_v2", {"issue_row_id": duplicate["issue_row_id"]}, {
                "status": "Superseded", "superseded_by_issue_row_id": canonical["issue_row_id"],
                "updated_at": db.now_ist(),
            })
    return canonical


def existing_candidate_issue(finding: dict[str, Any]) -> dict[str, Any] | None:
    feature = finding.get("entity")
    if not feature:
        return None
    return _feature_issue(finding["item_id"], finding.get("tables_used") or "", feature)


def ensure_candidate_issue(finding_id: str, actor: str = "system",
                           *, overwrite: bool = False) -> str:
    """Create exactly one Issue Management row after explicit confirmation."""
    rows = db.execute(
        "SELECT f.*, r.metrics_json, dr.item_id, dr.diagnostic_id "
        "FROM diag_findings f JOIN diag_results r ON r.result_id=f.result_id "
        "JOIN diag_runs dr ON dr.run_id=f.run_id WHERE f.finding_id=?",
        [finding_id],
    )
    if not rows:
        raise KeyError(f"Unknown finding: {finding_id}")
    finding = rows[0]
    if finding["diagnostic_id"] != DIAGNOSTIC_ID:
        raise ValueError("finding does not belong to diagnostic #2")
    metrics = finding.get("metrics_json") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    candidate = metrics.get("candidate") or {
        "candidate_type": "operator_override", "candidate_reasons": [finding.get("pattern_detail")],
        "auc": metrics.get("auc"), "gini": metrics.get("gini"), "iv": metrics.get("iv"),
        "category": metrics.get("category"),
        "thresholds": (finding.get("thresholds_used_json") or {}).get("values") or {},
    }
    issue_row_id = item_service._id("iss")
    now = db.now_ist()
    try:
        import tenancy
        enabled = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_ENABLED")
        retired = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_LEGACY_CREATION_RETIRED")
        workflow_version = "rca" if (enabled or retired) else "legacy-v1"
    except (KeyError, ValueError):
        workflow_version = "rca"
    feature = metrics.get("feature") or finding.get("entity")
    thresholds = candidate.get("thresholds") or {}
    issue_values = {
        "issue_row_id": issue_row_id, "item_id": finding["item_id"],
        "table_name": finding.get("tables_used") or "",
        "test_name": "Single-feature target separation review",
        "area_id": "T1",
        "criticality": "High" if candidate.get("candidate_type") == "target_leakage" else "Medium",
        "columns_json": [feature], "violation_count": 0,
        "threshold_json": thresholds,
        "column_details_json": [{
            "columns": [feature], "metric": candidate.get("auc"),
            "auc": candidate.get("auc"), "gini": candidate.get("gini"),
            "iv": candidate.get("iv"), "category": candidate.get("category"),
            "candidate_type": candidate.get("candidate_type"),
            "candidate_reasons": candidate.get("candidate_reasons") or [],
            "artifact_ids": metrics.get("artifact_ids") or {},
            "target": metrics.get("target"),
        }],
        "metric": candidate.get("auc"), "status": "Open",
        "workflow_version": workflow_version, "run_id": finding["run_id"],
        "finding_id": finding_id, "diagnostic_id": DIAGNOSTIC_ID,
        "rule_id": finding["rule_id"], "created_at": now, "updated_at": now,
    }
    existing = _feature_issue(finding["item_id"], issue_values["table_name"], feature)
    if existing:
        if existing.get("finding_id") == finding_id:
            return existing["issue_row_id"]
        if not overwrite:
            raise CandidateIssueConflict(existing["issue_row_id"])
        issue_row_id = existing["issue_row_id"]
        issue_values.pop("issue_row_id")
        issue_values.pop("created_at")
        issue_values.pop("status")
        db.update("issues_v2", {"issue_row_id": issue_row_id}, issue_values)
        return issue_row_id
    db.insert("issues_v2", issue_values)
    try:
        import taxonomy
        import tenancy
        taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "dq_item", finding["item_id"],
                              "issues_v2", issue_row_id, actor)
    except (KeyError, ValueError):
        pass
    return issue_row_id


def ensure_candidate_finding(result_id: str, reason: str | None = None,
                             actor: str = "system") -> str:
    """Create candidate evidence for an explicit operator override on any assessed feature."""
    rows = db.execute("SELECT r.*, dr.item_id, dr.manifest_json FROM diag_results r "
                      "JOIN diag_runs dr ON dr.run_id=r.run_id WHERE r.result_id=? AND dr.diagnostic_id=?",
                      [result_id, DIAGNOSTIC_ID])
    if not rows: raise KeyError(f"Unknown single-feature result: {result_id}")
    result = rows[0]; metrics = result.get("metrics_json") or {}
    if isinstance(metrics, str): metrics = json.loads(metrics)
    if metrics.get("result_kind") != "feature": raise ValueError("only assessed feature results can be promoted")
    existing = db.query_one("diag_findings", result_id=result_id)
    if existing: return existing["finding_id"]
    rationale = (reason or "").strip()
    if not rationale: raise ValueError("a rationale is required to override the product recommendation")
    manifest = result.get("manifest_json") or {}
    if isinstance(manifest, str): manifest = json.loads(manifest)
    threshold_payload = result.get("thresholds_used_json") or {}
    if isinstance(threshold_payload, str): threshold_payload = json.loads(threshold_payload)
    thresholds = threshold_payload.get("values") or {}
    finding_id = _id("dfind")
    db.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id,
        "run_id": result["run_id"], "rule_id": f"feature_target:{metrics.get('feature')}:operator_override",
        "kb_rule_id": None, "severity": "MATERIAL", "outcome": "CANDIDATE", "violation_count": 0,
        "rate": metrics.get("auc"), "tolerance": thresholds.get("leakage_auc"),
        "exceptions_json": {"count": 0, "notes": []}, "evidence_json": [{
            "feature": metrics.get("feature"), "auc": metrics.get("auc"), "gini": metrics.get("gini"),
            "iv": metrics.get("iv"), "category": metrics.get("category"),
            "candidate_type": "operator_override", "operator_override": True,
            "override_rationale": rationale}], "pattern": "operator_override",
        "pattern_detail": rationale, "regulatory_ref": None,
        "rule_text": "Operator promoted feature evidence despite no automatic candidate recommendation.",
        "rule_type": "statistical", "framework": None, "entity": metrics.get("feature"),
        "resolved_roles_json": {"target": metrics.get("target"), "feature": metrics.get("feature")},
        "tables_used": (manifest.get("scope") or {}).get("table") or result.get("entity_or_table"),
        "scope_rows_evaluated": metrics.get("rows_evaluated") or 0, "scope_rows_skipped": 0,
        "na_reason": None, "review_state": "open", "seq": 0, "created_at": db.now_ist()})
    return finding_id
