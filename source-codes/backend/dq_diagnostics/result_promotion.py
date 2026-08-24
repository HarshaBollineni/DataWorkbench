"""Shared, auditable operator-override promotion for diagnostic results."""
from __future__ import annotations

import json
import uuid
from typing import Any

import system_db as db


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ensure_override_finding(result_id: str, reason: str | None, actor: str = "system") -> str:
    rationale = (reason or "").strip()
    rows = db.execute("SELECT r.*, dr.item_id, dr.diagnostic_id FROM diag_results r "
                      "JOIN diag_runs dr ON dr.run_id=r.run_id WHERE r.result_id=?", [result_id])
    if not rows: raise KeyError(f"Unknown diagnostic result: {result_id}")
    result = rows[0]
    metrics = result.get("metrics_json") or {}
    if isinstance(metrics, str): metrics = json.loads(metrics)
    if metrics.get("result_kind") in {"run_summary", "psi_run_summary"}:
        raise ValueError("run summaries cannot be promoted; select an assessed result")
    findings = db.query("diag_findings", result_id=result_id, order_by="seq")
    recommended = next((finding for finding in findings
                        if finding.get("outcome") in {"VIOLATION", "CANDIDATE", "CONTEXTUAL"}
                        and finding.get("review_state") != "dismissed"), None)
    if recommended: return recommended["finding_id"]
    existing = next((finding for finding in findings if finding.get("pattern") == "operator_override"), None)
    if existing: return existing["finding_id"]
    if not rationale:
        raise ValueError("a rationale is required to override the product recommendation")
    finding_id = _id("dfind")
    scope = result.get("scope_counts_json") or {}
    if isinstance(scope, str): scope = json.loads(scope)
    evaluated = next((int(value) for key, value in scope.items()
                      if "evaluated" in key and isinstance(value, (int, float))), 0)
    entity = metrics.get("feature") or result.get("entity_or_table")
    db.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id,
        "run_id": result["run_id"], "rule_id": f"operator_override:{result['diagnostic_id']}:{entity}",
        "kb_rule_id": None, "severity": "MATERIAL", "outcome": "CONTEXTUAL",
        "violation_count": 0, "rate": None, "tolerance": None,
        "exceptions_json": {"count": 0}, "evidence_json": [{"entity": entity,
            "operator_override": True, "override_rationale": rationale}],
        "pattern": "operator_override", "pattern_detail": rationale, "regulatory_ref": None,
        "rule_text": "Operator promoted this diagnostic result contrary to the product recommendation.",
        "rule_type": "operator_decision", "framework": None, "entity": entity,
        "resolved_roles_json": {}, "tables_used": result.get("entity_or_table"),
        "scope_rows_evaluated": evaluated, "scope_rows_skipped": 0, "na_reason": None,
        "review_state": "open", "seq": 0, "created_at": db.now_ist()})
    return finding_id


def ensure_generic_issue(finding_id: str, actor: str = "system") -> str:
    rows = db.execute("SELECT f.*, r.metrics_json, dr.item_id, dr.diagnostic_id FROM diag_findings f "
                      "JOIN diag_results r ON r.result_id=f.result_id JOIN diag_runs dr ON dr.run_id=f.run_id "
                      "WHERE f.finding_id=?", [finding_id])
    if not rows: raise KeyError(f"Unknown finding: {finding_id}")
    finding = rows[0]
    existing = db.query_one("issues_v2", finding_id=finding_id)
    if existing: return existing["issue_row_id"]
    register = db.query_one("diagnostic_register", diagnostic_id=finding["diagnostic_id"]) or {}
    issue_id = _id("iss"); now = db.now_ist()
    db.insert("issues_v2", {"issue_row_id": issue_id, "item_id": finding["item_id"],
        "table_name": finding.get("tables_used") or "", "test_name": register.get("name") or "Diagnostic review",
        "area_id": register.get("area") or "Test Lab", "criticality": "Medium",
        "columns_json": [finding.get("entity")] if finding.get("entity") else [],
        "violation_count": int(finding.get("violation_count") or 0),
        "threshold_json": {}, "column_details_json": [{"columns": [finding.get("entity")],
            "operator_override": finding.get("pattern") == "operator_override",
            "rationale": finding.get("pattern_detail")}], "metric": finding.get("rate"),
        "status": "Open", "workflow_version": "rca", "run_id": finding["run_id"],
        "finding_id": finding_id, "diagnostic_id": finding["diagnostic_id"],
        "rule_id": finding.get("rule_id"), "created_at": now, "updated_at": now})
    return issue_id


def dispose_row_completeness_finding(finding_id: str, action: str, reason: str,
                                     actor: str = "system") -> dict[str, Any]:
    """Atomically record the T2D6 review decision and optional issue.

    A failed diagnostic produces only an open finding. This is the sole T2D6
    finding-to-issue transition; issue, disposition, and finding state commit
    together so an interrupted request cannot leave an unreviewed orphan issue.
    """
    rationale = (reason or "").strip()
    if action not in {"confirm_issue", "dismiss"}:
        raise ValueError("action must be confirm_issue or dismiss")
    if not rationale:
        raise ValueError("a rationale is required for a diagnostic disposition")

    def insert_row(conn, table: str, row: dict[str, Any]) -> None:
        data = db._encode(table, db._with_root_provenance(table, row))  # noqa: SLF001
        columns = ", ".join(f'"{column}"' for column in data)
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                     list(data.values()))

    with db.get_conn() as conn:
        # Acquire the writer lock before the read so concurrent confirmations
        # cannot both observe "no issue" and create duplicate records.
        conn.execute("BEGIN IMMEDIATE")
        finding_row = conn.execute(
            "SELECT f.*, dr.item_id, dr.diagnostic_id FROM diag_findings f "
            "JOIN diag_runs dr ON dr.run_id=f.run_id WHERE f.finding_id=?",
            (finding_id,),
        ).fetchone()
        if not finding_row:
            raise KeyError(f"Unknown finding: {finding_id}")
        finding = db._decode("diag_findings", finding_row)  # noqa: SLF001
        if finding["diagnostic_id"] != 6:
            raise ValueError("the row-completeness disposition service accepts only T2D6 findings")
        if finding.get("outcome") != "VIOLATION":
            raise ValueError("only a T2D6 violation finding requires disposition")

        existing_issue_row = conn.execute(
            "SELECT * FROM issues_v2 WHERE finding_id=?", (finding_id,)
        ).fetchone()
        existing_issue = dict(existing_issue_row) if existing_issue_row else None
        target_state = "confirmed" if action == "confirm_issue" else "dismissed"
        if finding.get("review_state") == target_state:
            return {"finding_id": finding_id, "action": action,
                    "review_state": target_state,
                    "issue_row_id": (existing_issue or {}).get("issue_row_id"),
                    "reason": rationale, "idempotent": True}
        if finding.get("review_state") not in {None, "open"}:
            raise ValueError(
                f"finding is already {finding.get('review_state')}; a separate reopen workflow is required"
            )
        if action == "dismiss" and existing_issue:
            raise ValueError("a finding with an existing issue cannot be dismissed")

        issue_id = (existing_issue or {}).get("issue_row_id")
        now = db.now_ist()
        if action == "confirm_issue" and not issue_id:
            register_row = conn.execute(
                "SELECT * FROM diagnostic_register WHERE diagnostic_id=6"
            ).fetchone()
            register = dict(register_row) if register_row else {}
            issue_id = _id("iss")
            roles = finding.get("resolved_roles_json") or {}
            if isinstance(roles, str):
                roles = json.loads(roles)
            columns = sorted({str(value) for value in roles.values() if value})
            insert_row(conn, "issues_v2", {
                "issue_row_id": issue_id, "item_id": finding["item_id"],
                "table_name": finding.get("tables_used") or "",
                "test_name": finding.get("rule_id") or register.get("name") or "Row Completeness",
                "area_id": register.get("area") or "T2", "criticality": (
                    "Critical" if finding.get("severity") == "CRITICAL" else "High"
                ),
                "columns_json": columns,
                "violation_count": int(finding.get("violation_count") or 0),
                "threshold_json": {"tolerance": finding.get("tolerance"),
                                   "rate": finding.get("rate"),
                                   "pattern": finding.get("pattern")},
                "column_details_json": [{
                    "columns": columns, "metric": finding.get("rate"),
                    "threshold": {"tolerance": finding.get("tolerance")},
                    "violation_count": int(finding.get("violation_count") or 0),
                    "rule_text": finding.get("rule_text"),
                    "pattern": finding.get("pattern"),
                    "pattern_detail": finding.get("pattern_detail"),
                    "evidence": finding.get("evidence_json") or [],
                    "promotion_rationale": rationale,
                }],
                "metric": finding.get("rate"), "status": "Open",
                "workflow_version": "rca", "run_id": finding["run_id"],
                "finding_id": finding_id, "diagnostic_id": 6,
                "rule_id": finding.get("rule_id"), "created_at": now, "updated_at": now,
            })
        insert_row(conn, "diag_dispositions", {
            "target_type": "finding", "target_id": finding_id, "action": action,
            "reason": rationale, "actor": actor, "ts": now,
        })
        conn.execute("UPDATE diag_findings SET review_state=? WHERE finding_id=?",
                     (target_state, finding_id))
        conn.commit()
    return {"finding_id": finding_id, "action": action, "review_state": target_state,
            "issue_row_id": issue_id, "reason": rationale, "ts": now, "idempotent": False}
