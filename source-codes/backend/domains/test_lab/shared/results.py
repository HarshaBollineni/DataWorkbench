"""Shared persisted-result projection for Test Lab diagnostics."""
from __future__ import annotations

from typing import Any

import system_db as db

from .run_state import get_run, list_decisions


def run_results(run_id: str) -> dict[str, Any]:
    """Return one run with decision history, hydrated results, findings, and issue state."""
    run_row = get_run(run_id)
    projected = []
    for result in db.query("diag_results", run_id=run_id):
        if run_row["diagnostic_id"] == 2 and (result.get("metrics_json") or {}).get("result_kind") == "feature":
            from domains.test_lab.diagnostics.t1_d02_feature_target_separation.binning_reviews import hydrate_result
            result = hydrate_result(result)
        if (run_row["diagnostic_id"] == 11
                and (result.get("metrics_json") or {}).get("result_kind")
                == "directionality_feature"):
            from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.runner import hydrate_result
            result = hydrate_result(result)
        findings = db.query("diag_findings", result_id=result["result_id"], order_by="seq")
        for finding in findings:
            finding["dispositions"] = db.query(
                "diag_dispositions", target_type="finding",
                target_id=finding["finding_id"], order_by="id",
            )
            issue_rows = db.execute(
                "SELECT * FROM issues_v2 WHERE finding_id=? "
                "AND COALESCE(status, '') <> 'Superseded' "
                "ORDER BY created_at, issue_row_id LIMIT 1",
                [finding["finding_id"]],
            )
            issue = issue_rows[0] if issue_rows else None
            if not issue and run_row["diagnostic_id"] == 2:
                from domains.test_lab.diagnostics.t1_d02_feature_target_separation.runner import existing_candidate_issue
                issue = existing_candidate_issue({**finding, "item_id": run_row["item_id"]})
            if issue:
                finding["existing_issue"] = {
                    "issue_row_id": issue["issue_row_id"],
                    "finding_id": issue.get("finding_id"),
                    "status": issue.get("status"),
                    "same_finding": issue.get("finding_id") == finding["finding_id"],
                }
        projected.append({**result, "findings": findings})
    return {
        "run": {key: run_row[key] for key in (
            "run_id", "item_id", "diagnostic_id", "status",
            "created_at", "started_at", "finished_at",
        )},
        "manifest": run_row["manifest_json"],
        "decisions": list_decisions(run_id),
        "results": projected,
    }
