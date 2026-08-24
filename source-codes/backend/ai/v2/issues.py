"""Galileo v2 Issue Management — spec §9.

Turns failed Test Lab results into tracked issue rows (one per failed test per
table), serves the RCA Agent, and records Close / Raise decisions. Tracking
only: nothing here mutates data or re-runs tests. RCA output is regenerated
fresh on every open and never persisted. Additional analyses reuse the existing
snippet pipeline via plan_v2 rows under scope 'rca:{issue_row_id}' — those
scopes are excluded from scoring and from issue derivation by design.
"""
from __future__ import annotations

import json
from typing import Any

import system_db as db
from ai.v2 import service

OPEN_STATUSES = {"Open", "In RCA", "Escalated"}
_CRIT_ORDER = {"Critical": 0, "High": 1, "Medium": 2}


class ConflictError(Exception):
    """Raised when a resolution action conflicts with the row's status (→ 409)."""


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


# ── Derivation (the sync) ─────────────────────────────────────────────────────

def _failed_groups(item_id: str) -> list[dict]:
    """Group combined Test Lab failures by (table, test); union failing columns.

    One issue per failed test — but it carries the full detail for EVERY failing
    column (feedback 10-07 5.1): metric, threshold and violation count per
    column, never reduced to the worst one.
    """
    rows = db.execute(
        "SELECT r.*, p.columns_json AS plan_columns FROM results_v2 r "
        "LEFT JOIN plan_v2 p ON p.row_id = r.row_id "
        "WHERE r.item_id=? AND r.scope IN ('framework','incremental') "
        "AND r.status='fail' ORDER BY r.run_at", [item_id])
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["table_name"], r["test_name"])
        # The result's own columns are the exact failing variable(s); plan
        # columns are only a fallback for legacy rows (feedback 09-07 4.1).
        cols = _loads(r.get("columns_json"), None) or _loads(r.get("plan_columns"), [])
        g = groups.setdefault(key, {
            "table_name": r["table_name"], "test_name": r["test_name"],
            "area_id": None, "columns": [], "violation_count": 0,
            "metric": None, "threshold_json": None, "column_details": {},
        })
        g["area_id"] = g["area_id"] or r.get("area_id")
        for c in cols:
            if c not in g["columns"]:
                g["columns"].append(c)
        # Per-column detail: rows are ordered by run_at, so the latest run of
        # each column combination wins its slot.
        g["column_details"][tuple(cols)] = {
            "columns": list(cols),
            "metric": r.get("metric"),
            "threshold": _loads(r.get("threshold_json"), None),
            "violation_count": int(r.get("violation_count") or 0),
        }
        g["violation_count"] = max(g["violation_count"], int(r.get("violation_count") or 0))
        # rows are ordered by run_at: the latest run wins for metric/threshold
        g["metric"] = r.get("metric")
        g["threshold_json"] = _loads(r.get("threshold_json"), None)
    out = []
    for g in groups.values():
        g["column_details"] = list(g["column_details"].values())
        out.append(g)
    return out


def _sort_key(item: dict):
    def key(row: dict):
        if item["kind"] == "dataset":
            return (_CRIT_ORDER.get(row.get("criticality"), 3), -(row.get("violation_count") or 0))
        return (-(row.get("violation_count") or 0),)
    return key


def _reconcile_feature_target_duplicates(item_id: str | None = None) -> None:
    """Keep one visible governed issue per snapshot/feature; retain duplicates as audit rows."""
    filters = {"diagnostic_id": 2}
    if item_id:
        filters["item_id"] = item_id
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in db.query("issues_v2", **filters, order_by="created_at, issue_row_id"):
        if row.get("status") == "Superseded":
            continue
        for feature in row.get("columns_json") or []:
            groups.setdefault((row["item_id"], row.get("table_name") or "", feature), []).append(row)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        with_rca = [row for row in rows if db.query_one("rca_cases", issue_row_id=row["issue_row_id"])]
        canonical = (with_rca or rows)[0]
        for duplicate in rows:
            if duplicate["issue_row_id"] != canonical["issue_row_id"]:
                db.update("issues_v2", {"issue_row_id": duplicate["issue_row_id"]}, {
                    "status": "Superseded",
                    "superseded_by_issue_row_id": canonical["issue_row_id"],
                    "updated_at": db.now_ist(),
                })


def sync_issues(item_id: str) -> None:
    """Materialise/refresh issue rows. Never overwrites status, rationale, or
    the tracked-issue link; Closed/Escalated rows are immutable to the sync."""
    item = service.require_item(item_id)
    family = item.get("use_case") or "IFRS9"
    for g in _failed_groups(item_id):
        crit = None
        if item["kind"] == "dataset":
            crit = service._criticality(g["area_id"], family)
        found = db.execute(
            "SELECT * FROM issues_v2 WHERE item_id=? AND table_name=? AND test_name=?",
            [item_id, g["table_name"], g["test_name"]])
        existing = found[0] if found else None
        fresh = {
            "area_id": g["area_id"], "criticality": crit,
            "columns_json": g["columns"], "violation_count": g["violation_count"],
            "threshold_json": g["threshold_json"], "metric": g["metric"],
            "column_details_json": g["column_details"],
            "updated_at": db.now_ist(),
        }
        if existing is None:
            issue_row_id = service._id("iss")
            import tenancy
            # RCA Stage 2 (contracts.md §2): new issues are labeled rca only
            # once the flag is on; existing/legacy issues (and any created
            # while the flag is off) keep the untouched legacy heuristic RCA
            # path at ai/v2/issues.py:rca() below.
            # Stage 7: once RCA_LEGACY_CREATION_RETIRED is flipped, new
            # legacy-v1 issues can never be created again — even if
            # RCA_ENABLED were later turned back off (e.g. an accidental
            # or emergency rollback toggle), retirement is a one-way,
            # independent floor beneath it, matching contracts.md §8's "once
            # true, blocks any new issue from taking the legacy heuristic
            # path even as a fallback."
            enabled = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_ENABLED")
            retired = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_LEGACY_CREATION_RETIRED")
            workflow_version = "rca" if (enabled or retired) else "legacy-v1"
            db.insert("issues_v2", {
                "issue_row_id": issue_row_id, "item_id": item_id,
                "table_name": g["table_name"], "test_name": g["test_name"],
                "status": "Open", "created_at": db.now_ist(),
                "workflow_version": workflow_version, **fresh,
            })
            # RCA Stage 1 — the issue snapshots the item's inherited tags
            # (contracts.md §7 propagation chain: ... -> Issue -> RCA Case).
            import taxonomy
            taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "dq_item", item_id,
                                  "issues_v2", issue_row_id, "system")
        elif existing["status"] in {"Open", "In RCA"}:
            db.update("issues_v2", {"issue_row_id": existing["issue_row_id"]}, fresh)


# ── Diagnostic-sourced issues (CFR-14 / RCA-27) ───────────────────────────────
# testlab-redesign-0.4.0.md §5.2: issues_v2 gains diagnostic_id / run_id /
# finding_id and is populated from verdicts instead of sync_issues over
# results_v2. The natural key becomes (item_id, diagnostic_id, rule_id) —
# which fixes §1.7's mutation bug: under the old (item, table, test) key a
# NEWLY failing column silently rewrote an existing issue's history. Here a
# different rule is a different issue, always.
#
# sync_issues() above is untouched: the retired wizard's results_v2 rows and
# this path never meet.

# CFR-08 severities -> issues_v2's existing criticality vocabulary
# (_CRIT_ORDER). Severity orders and labels; it never changed the verdict.
_SEVERITY_TO_CRITICALITY = {"CRITICAL": "Critical", "MATERIAL": "High", "MINOR": "Medium"}


def sync_diagnostic_issues(item_id: str, run_id: str) -> dict:
    """Auto-open an issue for every VIOLATION finding of one diagnostic run.

    A verdict VIOLATION opens its issue automatically (RCA-27); PASS and
    NOT-APPLICABLE never do. Closed/Escalated rows are immutable to this
    sync, exactly as they are to ``sync_issues``. Returns
    ``{"created": [...], "updated": [...], "skipped_locked": [...]}``.
    """
    service.require_item(item_id)
    import taxonomy
    import tenancy

    run = db.query_one("diag_runs", run_id=run_id)
    if not run:
        raise KeyError(f"Unknown run: {run_id}")
    diagnostic_id = run["diagnostic_id"]
    result_by_id = {r["result_id"]: r for r in db.query("diag_results", run_id=run_id)}
    findings = [f for f in db.query("diag_findings", run_id=run_id, order_by="seq")
                if f.get("outcome") == "VIOLATION"]

    created, updated, locked = [], [], []
    for finding in findings:
        parent = result_by_id.get(finding["result_id"]) or {}
        table_name = finding.get("tables_used") or parent.get("entity_or_table") or ""
        roles = finding.get("resolved_roles_json") or {}
        columns = sorted({str(v) for v in roles.values() if v})
        fresh = {
            "area_id": None, "criticality": _SEVERITY_TO_CRITICALITY.get(finding.get("severity"), "Medium"),
            "columns_json": columns, "violation_count": finding.get("violation_count") or 0,
            "threshold_json": {"tolerance": finding.get("tolerance"),
                               "rate": finding.get("rate"),
                               "pattern": finding.get("pattern")},
            "metric": finding.get("rate"),
            "column_details_json": [{
                "columns": columns, "metric": finding.get("rate"),
                "threshold": {"tolerance": finding.get("tolerance")},
                "violation_count": finding.get("violation_count") or 0,
                "rule_text": finding.get("rule_text"),
                "regulatory_ref": finding.get("regulatory_ref"),
                "pattern": finding.get("pattern"), "pattern_detail": finding.get("pattern_detail"),
                "evidence": finding.get("evidence_json") or [],
                "exceptions": finding.get("exceptions_json") or {},
            }],
            "table_name": table_name,
            "run_id": run_id, "finding_id": finding["finding_id"],
            "updated_at": db.now_ist(),
        }
        found = db.execute(
            "SELECT * FROM issues_v2 WHERE item_id=? AND diagnostic_id=? AND rule_id=?",
            [item_id, diagnostic_id, finding["rule_id"]])
        existing = found[0] if found else None
        if existing is None:
            issue_row_id = service._id("iss")
            enabled = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_ENABLED")
            retired = tenancy.is_flag_enabled(tenancy.DEFAULT_TENANT, "RCA_LEGACY_CREATION_RETIRED")
            db.insert("issues_v2", {
                "issue_row_id": issue_row_id, "item_id": item_id,
                # test_name carries the KB rule id so the existing Issue/RCA
                # surfaces (which key their display and triage off it) work
                # unchanged against a diagnostic-sourced issue.
                "test_name": finding["rule_id"],
                "diagnostic_id": diagnostic_id, "rule_id": finding["rule_id"],
                "status": "Open", "created_at": db.now_ist(),
                "workflow_version": "rca" if (enabled or retired) else "legacy-v1",
                **fresh,
            })
            taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "dq_item", item_id,
                                  "issues_v2", issue_row_id, "system")
            created.append(issue_row_id)
        elif existing["status"] in {"Open", "In RCA"}:
            db.update("issues_v2", {"issue_row_id": existing["issue_row_id"]}, fresh)
            updated.append(existing["issue_row_id"])
        else:
            locked.append(existing["issue_row_id"])
    return {"created": created, "updated": updated, "skipped_locked": locked,
            "violations": len(findings)}


def _present(row: dict, item: dict | None = None) -> dict:
    out = dict(row)
    out["columns"] = _loads(out.pop("columns_json", None), [])
    out["thresholds"] = _loads(out.pop("threshold_json", None), None)
    out["column_details"] = _loads(out.pop("column_details_json", None), [])
    if item:
        out["item_name"] = item.get("name")
        out["item_kind"] = item.get("kind")
        out["use_case"] = item.get("use_case")
    if row.get("tracked_issue_id"):
        tracked = db.query_one("tracked_issues_v2", issue_id=row["tracked_issue_id"])
        if tracked:
            out["tracked"] = {k: tracked.get(k) for k in
                              ("issue_id", "title", "owner", "priority", "target_date", "status")}
    case = db.query_one("rca_cases", issue_row_id=row["issue_row_id"])
    if case:
        state = case.get("state")
        if state in {"created", "triage", "intake"}:
            stage = "Intake"
        elif state == "opening_looks":
            stage = "Initial checks"
        elif state in {"awaiting_fix_approval", "all_hypotheses_rejected", "closed", "unresolved"}:
            stage = "Conclusion approval"
        else:
            stage = "Investigate"
        out["rca_case_id"] = case["case_id"]
        out["rca_stage"] = stage
    else:
        out["rca_case_id"] = None
        out["rca_stage"] = None
    out["violation_applicability"] = "aggregate_metric" if row.get("diagnostic_id") in {2, 14} else "rows"
    return out


def list_issues(item_id: str) -> dict:
    """Issue screen payload for one item: sorted failures + could-not-assess."""
    item = service.require_item(item_id)
    sync_issues(item_id)
    _reconcile_feature_target_duplicates(item_id)
    rows = [_present(r, item) for r in db.query("issues_v2", item_id=item_id)
            if r.get("status") != "Superseded"]
    rows.sort(key=_sort_key(item))

    # Review decisions belong to diagnostic findings, while Open/In RCA/Closed
    # belong to issue rows. Keep both concepts in one selected-item rollup
    # without treating an unreviewed finding as an already-created issue.
    latest_completed: dict[int, dict] = {}
    for run in db.query("diag_runs", item_id=item_id,
                        order_by="created_at DESC, run_id DESC"):
        diagnostic_id = run.get("diagnostic_id")
        if run.get("status") == "done" and diagnostic_id not in latest_completed:
            latest_completed[diagnostic_id] = run
    review_needed = sum(
        1
        for run in latest_completed.values()
        for finding in db.query("diag_findings", run_id=run["run_id"])
        if finding.get("review_state") == "open"
    )
    issue_status = {
        "open": sum(row.get("status") == "Open" for row in rows),
        "in_review": sum(row.get("status") in {"In RCA", "Escalated"} for row in rows),
        "closed": sum(row.get("status") == "Closed" for row in rows),
    }
    nr = db.execute(
        "SELECT table_name, test_name, evidence_json FROM results_v2 "
        "WHERE item_id=? AND scope IN ('framework','incremental') AND status='not_runnable' "
        "ORDER BY table_name, test_name", [item_id])
    could_not_assess = [{
        "table_name": r["table_name"], "test_name": r["test_name"],
        "reason": (_loads(r.get("evidence_json"), {}) or {}).get("reason") or "Not runnable on this data.",
    } for r in nr]
    ran = db.execute(
        "SELECT COUNT(*) AS n FROM results_v2 WHERE item_id=? AND scope IN ('framework','incremental') "
        "AND status IN ('pass','fail')", [item_id])[0]["n"]
    return {"issues": rows, "could_not_assess": could_not_assess,
            "all_passed": bool(ran) and not rows,
            "summary": {"review_needed": review_needed, **issue_status}}


def register(status: str | None = None, item_id: str | None = None,
             criticality: str | None = None) -> list[dict]:
    """Global register across all items (the Issue screen doubles as this)."""
    items = {i["item_id"]: i for i in db.query("dq_items")}
    for iid in items:
        try:
            sync_issues(iid)
        except Exception:  # noqa: BLE001 — an item without results has nothing to sync
            continue
    _reconcile_feature_target_duplicates(item_id)
    out = []
    for row in db.query("issues_v2"):
        if row.get("status") == "Superseded":
            continue
        if status and row.get("status") != status:
            continue
        if item_id and row.get("item_id") != item_id:
            continue
        if criticality and row.get("criticality") != criticality:
            continue
        item = items.get(row["item_id"])
        if item:
            out.append(_present(row, item))
    out.sort(key=lambda r: (_CRIT_ORDER.get(r.get("criticality"), 3),
                            -(r.get("violation_count") or 0)))
    return out


def require_issue(issue_row_id: str) -> dict:
    row = db.query_one("issues_v2", issue_row_id=issue_row_id)
    if not row:
        raise KeyError("Unknown issue row")
    return row


def get_issue(issue_row_id: str) -> dict:
    row = require_issue(issue_row_id)
    item = service.require_item(row["item_id"])
    out = _present(row, item)
    failing = db.execute(
        "SELECT * FROM results_v2 WHERE item_id=? AND table_name=? AND test_name=? "
        "AND scope IN ('framework','incremental') AND status='fail' "
        "ORDER BY run_at",
        [row["item_id"], row["table_name"], row["test_name"]])
    if failing:
        latest = failing[-1]
        out["evidence"] = _loads(latest.get("evidence_json"), {})
    # Per-column evidence (feedback 10-07 5.1): each failing column's own
    # example rows and supporting evidence — the latest run per column wins.
    by_cols: dict[tuple, dict] = {}
    for r in failing:
        cols = tuple(_loads(r.get("columns_json"), None) or [])
        by_cols[cols] = r
    details = out.get("column_details") or []
    for d in details:
        r = by_cols.get(tuple(d.get("columns") or []))
        if r:
            d["evidence"] = _loads(r.get("evidence_json"), {})
    out["column_details"] = details
    # Diagnostic-sourced issues retain a reference to the immutable result
    # rather than copying its evidence into the RCA record. The detail API
    # hydrates a bounded, read-only view so the workspace can choose the most
    # useful evidence treatment for PSI, feature-target separation, or any
    # other registered diagnostic.
    if row.get("finding_id"):
        diagnostic_finding = db.query_one("diag_findings", finding_id=row["finding_id"])
        diagnostic_result = db.query_one(
            "diag_results", result_id=diagnostic_finding.get("result_id")) if diagnostic_finding else None
        if diagnostic_result:
            # Diagnostic 2 persists a compact result row and keeps the ROC
            # tree plus fine/coarse-bin evidence in governed AAR artifacts.
            # Test Lab hydrates those references on read; RCA must use the
            # same projection or it incorrectly reports that retained detail
            # is unavailable even though the artifacts are active.
            if diagnostic_result.get("diagnostic_id") == 2:
                from dq_diagnostics.binning_reviews import hydrate_result

                diagnostic_result = hydrate_result(diagnostic_result)
            diagnostic_metrics = _loads(diagnostic_result.get("metrics_json"), {})
            out["source_evidence"] = {
                "result_id": diagnostic_result["result_id"],
                "run_id": diagnostic_result.get("run_id"),
                "diagnostic_id": diagnostic_result.get("diagnostic_id"),
                "decision_type": diagnostic_result.get("decision_type"),
                "entity_or_table": diagnostic_result.get("entity_or_table"),
                "metrics": diagnostic_metrics,
                "finding": _present_finding_evidence(diagnostic_finding),
            }
            # Intake needs the retained shape of the affected feature as well
            # as the diagnostic verdict. Resolve the immutable column-profile
            # artifact by snapshot/table/feature and expose its bounded payload
            # instead of asking the UI to reconstruct profile statistics.
            feature = (diagnostic_metrics.get("feature") or diagnostic_metrics.get("column")
                       or next(iter(out.get("columns") or []), None))
            if feature:
                try:
                    from analysis_runtime.artifacts import AnalysisArtifactRepository

                    repository = AnalysisArtifactRepository()
                    profiles = repository.list(
                        snapshot_id=row["item_id"], artifact_type="column_profile",
                        status="active", feature=feature,
                    )
                    profile_metadata = next(
                        (profile for profile in profiles
                         if profile.identity.get("table") == row.get("table_name")),
                        profiles[0] if profiles else None,
                    )
                    if profile_metadata:
                        _, profile_payload = repository.get(profile_metadata.artifact_id)
                        out["source_evidence"]["data_profile"] = {
                            "artifact_id": profile_metadata.artifact_id,
                            **profile_payload,
                        }
                except Exception:  # noqa: BLE001 - missing profile must not block issue intake
                    pass
    return out


def _present_finding_evidence(finding: dict) -> dict:
    """Bound the finding payload used by the RCA source-evidence view."""
    return {
        "finding_id": finding.get("finding_id"), "rule_id": finding.get("rule_id"),
        "rule_text": finding.get("rule_text"), "outcome": finding.get("outcome"),
        "classification": finding.get("classification"), "rate": finding.get("rate"),
        "tolerance": finding.get("tolerance"), "violation_count": finding.get("violation_count"),
        "evidence": _loads(finding.get("evidence_json"), []),
    }


# ── Resolution (tracking only — never touches results or plans) ───────────────

def close_issue(issue_row_id: str, rationale: str) -> dict:
    row = require_issue(issue_row_id)
    if row["status"] == "Closed":
        raise ConflictError("Issue row is already Closed.")
    if not (rationale or "").strip():
        raise ValueError("A resolution rationale is required to close an issue.")
    db.update("issues_v2", {"issue_row_id": issue_row_id}, {
        "status": "Closed", "resolution_rationale": rationale.strip(),
        "updated_at": db.now_ist(),
    })
    return _present(require_issue(issue_row_id))


def _next_issue_id() -> str:
    rows = db.execute("SELECT issue_id FROM tracked_issues_v2 ORDER BY issue_id DESC LIMIT 1")
    if not rows:
        return "ISS-0001"
    return f"ISS-{int(rows[0]['issue_id'].split('-')[1]) + 1:04d}"


def raise_issue(issue_row_id: str, *, title: str, description: str = "",
                owner: str = "", priority: str = "Medium",
                target_date: str = "") -> dict:
    row = require_issue(issue_row_id)
    if row.get("tracked_issue_id"):
        raise ConflictError("A tracked remediation item already exists for this issue.")
    if not (title or "").strip():
        raise ValueError("A title is required to raise a tracked issue.")
    issue_id = _next_issue_id()
    db.insert("tracked_issues_v2", {
        "issue_id": issue_id, "issue_row_id": issue_row_id,
        "title": title.strip(), "description": description or "",
        "owner": owner or "", "priority": priority or "Medium",
        "target_date": target_date or "", "status": "Open",
        "created_at": db.now_ist(),
    })
    db.update("issues_v2", {"issue_row_id": issue_row_id}, {
        # A remediation handoff is independent of both managed-issue status
        # and RCA outcome; creating it must not reopen or rename either one.
        "tracked_issue_id": issue_id, "updated_at": db.now_ist(),
    })
    return _present(require_issue(issue_row_id))


# ── RCA Agent (6th agent) ─────────────────────────────────────────────────────
# Test-level analysis across all failing columns together. Framework remediation
# approaches (fw_areas.remediation) are the primary solution source; autonomous
# fallbacks are explicitly tagged ai_generated. Regenerated fresh on every open.

_CAUSE_HEURISTICS = [
    (("missing", "completeness", "mnar"),
     "upstream capture gaps or conditionally collected fields — the missingness pattern "
     "suggests the source system only records {cols} in certain workflows"),
    (("plausibility", "range", "domain", "outlier"),
     "unit or code-mapping errors in the source feed for {cols} (e.g. wrong currency unit, "
     "stale reference codes, or manual-entry outliers)"),
    (("referential", "orphan", "key", "relationship"),
     "orphaned or late-arriving keys — load-order problems or missing parent records "
     "for {cols}"),
    (("drift", "psi", "stability", "distribution"),
     "a population or pipeline change between snapshots shifting the distribution of {cols} "
     "(new origination mix, changed defaults, or an ETL recalibration)"),
    (("monotonic",),
     "a ranking inversion in {cols} — score calibration drift or a segment-mix effect "
     "that breaks the expected risk ordering"),
    (("vintage", "maturity", "seasoning"),
     "immature cohorts inside the modelling window — recent vintages of {cols} have not "
     "had time to season, so their observed outcome rates are understated"),
    (("label", "target", "consistency"),
     "label-definition inconsistency around {cols} — the outcome flag and its driver fields "
     "were derived under different rules or timing conventions"),
    (("duplicate", "unique", "identifier"),
     "duplicate or reused keys in {cols} — typically merge fan-out or repeated loads "
     "of the same delivery"),
    (("type", "schema", "format"),
     "a schema or type change upstream — {cols} arrive in a different type/format than "
     "the contract declares"),
]

_AI_SOLUTIONS = [
    (("missing", "completeness", "mnar"),
     ["Trace {first} back to the source system and confirm whether the field is optional "
      "in the originating workflow; make capture mandatory or document the segment gap.",
      "Add a completeness SLA on {first} at ingestion so the gap is caught before modelling."]),
    (("plausibility", "range", "domain", "outlier"),
     ["Reconcile a sample of extreme {first} values against source documents to separate "
      "genuine tails from unit errors before applying caps."]),
    (("referential", "orphan", "key", "relationship"),
     ["Re-sequence the load so parent tables commit before child tables, and quarantine "
      "orphaned {first} rows into an exception table instead of dropping them."]),
    (("drift", "psi", "stability", "distribution"),
     ["Compare the {first} distribution by origination period to locate exactly when the "
      "shift started, then decide between recalibration and segment exclusion."]),
    (("monotonic",),
     ["Recompute the {first} ordering within homogeneous segments to test whether the "
      "inversion is a mix effect rather than a calibration problem."]),
    (("vintage", "maturity", "seasoning"),
     ["Exclude or down-weight unseasoned vintages of {first} and re-fit; keep them in a "
      "monitoring-only holdout until they mature."]),
    (("label", "target", "consistency"),
     ["Rebuild the outcome flag from raw events with one documented rule set and diff it "
      "against the delivered {first} to quantify the disagreement."]),
    (("duplicate", "unique", "identifier"),
     ["De-duplicate on the business key behind {first} keeping the latest snapshot, and add "
      "a uniqueness constraint at ingestion."]),
]


def _match(keywords_map, test_name: str):
    name = test_name.lower()
    for keys, value in keywords_map:
        if any(k in name for k in keys):
            return value
    return None


def _profile_facts(item_id: str, table: str, columns: list[str]) -> list[str]:
    # Every failing column gets its profile fact — the RCA covers them all
    # (feedback 10-07 5.1), not a truncated subset.
    facts = []
    for col in columns:
        found = db.execute(
            "SELECT * FROM variable_inventory WHERE item_id=? AND table_name=? AND column_name=?",
            [item_id, table, col])
        inv = found[0] if found else None
        if not inv:
            continue
        prof = _loads(inv.get("profile_json"), {}) or {}
        bits = []
        if prof.get("null_share") is not None:
            bits.append(f"{round(float(prof['null_share']) * 100, 1)}% missing")
        if prof.get("distinct") is not None:
            bits.append(f"{prof['distinct']} distinct values")
        desc = (inv.get("description") or "").strip()
        if desc:
            bits.append(f'defined as "{desc[:80]}"')
        if bits:
            facts.append(f"{col}: " + ", ".join(bits))
    return facts


def _suggest_analyses(item_id: str, table: str, columns: list[str],
                      target: str | None) -> list[dict]:
    """Suggested analyses cover EVERY failing column (feedback 10-07 5.1) —
    each column gets its own segment breakdown, deep profile and cross-check,
    never just the first one."""
    inv = db.execute(
        "SELECT * FROM variable_inventory WHERE item_id=? AND table_name=? ORDER BY column_name",
        [item_id, table])
    failing = set(columns)
    segments = [r["column_name"] for r in inv
                if r.get("classification") in {"categorical", "ordinal", "binary"}
                and r["column_name"] not in failing]
    datetimes = [r["column_name"] for r in inv
                 if r.get("classification") == "datetime" and r["column_name"] not in failing]
    cols = columns or ([inv[0]["column_name"]] if inv else [])
    out = []
    for col in cols:
        if segments:
            out.append({"kind": "segment_breakdown", "columns": [col, segments[0]],
                        "title": f"Break down the {col} failure by {segments[0]}",
                        "rationale": "If violations concentrate in one segment, the cause is a "
                                     "workflow or source difference, not a systemic defect."})
        out.append({"kind": "profile_column", "columns": [col],
                    "title": f"Deep profile of {col}",
                    "rationale": "Distribution, missingness and top values confirm whether the "
                                 "failing metric is driven by a data shape change."})
        partner = None
        if target and target not in failing:
            partner = target
        elif datetimes:
            partner = datetimes[0]
        else:
            partner = next((c for c in cols if c != col), None)
        if partner:
            out.append({"kind": "cross_check", "columns": [col, partner],
                        "title": f"Cross-check {col} against {partner}",
                        "rationale": "Joint completeness and association reveal whether the two "
                                     "fields disagree systematically (timing or definition gap)."})
    return out


def rca(issue_row_id: str) -> dict:
    """Build the RCA record for one issue row. Fresh every call; not persisted."""
    row = require_issue(issue_row_id)
    if row["status"] == "Open":
        db.update("issues_v2", {"issue_row_id": issue_row_id},
                  {"status": "In RCA", "updated_at": db.now_ist()})
        row = require_issue(issue_row_id)
    item = service.require_item(row["item_id"])
    columns = _loads(row.get("columns_json"), [])
    threshold = _loads(row.get("threshold_json"), None)
    test = db.query_one("fw_tests", test_name=row["test_name"]) or {}
    area = db.query_one("fw_areas", area_id=row.get("area_id")) or {}

    def _fmt(value) -> str:
        return "n/a" if value is None else f"{float(value):.4f}".rstrip("0").rstrip(".")

    cols_txt = ", ".join(columns) or "the selected columns"
    details = _loads(row.get("column_details_json"), [])
    if details:
        # Per-column causes (feedback 10-07 5.1): every failing column is
        # reported with its own metric, threshold and violation count.
        per_col = "; ".join(
            f"{', '.join(d.get('columns') or []) or 'combined'} — metric {_fmt(d.get('metric'))} "
            f"vs threshold {json.dumps(d.get('threshold'))} ({int(d.get('violation_count') or 0)} violating rows)"
            for d in details)
        parts = [
            f"'{row['test_name']}' failed in {row['table_name']} on "
            f"{len(details)} independent execution(s): {per_col}.",
        ]
    else:
        parts = [
            f"'{row['test_name']}' failed on {cols_txt} in {row['table_name']}: "
            f"metric {_fmt(row.get('metric'))} breached threshold {json.dumps(threshold)} "
            f"with {row.get('violation_count') or 0} violating rows.",
        ]
    what = (test.get("what_it_tests") or "").strip()
    if what:
        parts.append(f"The test checks {what[0].lower() + what[1:]}.")
    cause = _match(_CAUSE_HEURISTICS, row["test_name"])
    if cause:
        parts.append("The most likely cause is " + cause.format(cols=cols_txt) + ".")
    facts = _profile_facts(row["item_id"], row["table_name"], columns)
    if facts:
        parts.append("Profile context: " + "; ".join(facts) + ".")
    caveat = (test.get("caveats") or "").strip()
    if caveat:
        parts.append(f"Caveat from the framework: {caveat}.")
    likely_cause = " ".join(parts)

    solutions = [{"text": s.strip(), "source": "framework"}
                 for s in (area.get("remediation") or "").replace(";", ",").split(",")
                 if s.strip()]
    ai_templates = _match(_AI_SOLUTIONS, row["test_name"]) or [
        "Walk the lineage of {first} from source extract to the modelling table and diff "
        "row counts and types at each hop to locate where the defect enters."]
    # Solutions name every failing column, not just the first (feedback 10-07 5.1).
    first = ", ".join(columns) if columns else cols_txt
    solutions += [{"text": t.format(first=first), "source": "ai_generated"}
                  for t in ai_templates]

    analyses = _suggest_analyses(row["item_id"], row["table_name"], columns,
                                 item.get("target_variable"))
    return {
        "issue_row_id": issue_row_id,
        "likely_cause": likely_cause,
        "solutions": solutions,
        "suggested_analyses": analyses,
    }


# ── Additional analyses — reuse the snippet pipeline via plan_v2 ──────────────

def _analysis_code(kind: str, columns: list[str]) -> str:
    # Snippets run in the analytics sandbox (pandas/numpy only — no json import),
    # so numpy scalars are converted with .item() before entering the result dict.
    first = columns[0] if columns else ""
    second = columns[1] if len(columns) > 1 else ""
    if kind == "segment_breakdown":
        return f'''import pandas as pd

col, seg = {first!r}, {second!r}
grp = df.groupby(seg, dropna=False)[col]
summary = pd.DataFrame({{
    "rows": grp.size(),
    "null_share": grp.apply(lambda s: round(float(s.isna().mean()), 4)),
}})
if pd.api.types.is_numeric_dtype(df[col]):
    summary["mean"] = grp.mean().round(4)
def plain(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return str(v)

records = [{{str(k): plain(v) for k, v in rec.items()}}
           for rec in summary.reset_index().head(12).to_dict("records")]
result = {{
    "status": "info",
    "metric": float(df[col].isna().mean()),
    "threshold": None,
    "violation_count": int(df[col].isna().sum()),
    "evidence": {{"analysis": "segment_breakdown", "column": col, "segment": seg, "sample": records}},
}}
'''
    if kind == "cross_check":
        return f'''import pandas as pd

a, b = {first!r}, {second!r}
both = int((df[a].notna() & df[b].notna()).sum())
a_only = int((df[a].notna() & df[b].isna()).sum())
b_only = int((df[a].isna() & df[b].notna()).sum())
corr = None
if pd.api.types.is_numeric_dtype(df[a]) and pd.api.types.is_numeric_dtype(df[b]):
    corr = round(float(df[a].corr(df[b])), 4)
result = {{
    "status": "info",
    "metric": corr,
    "threshold": None,
    "violation_count": a_only + b_only,
    "evidence": {{"analysis": "cross_check", "columns": [a, b], "summary": {{"populated_both": both, "only_" + a: a_only, "only_" + b: b_only, "correlation": corr}}}},
}}
'''
    # default: profile_column
    return f'''import pandas as pd

col = {first!r}
s = df[col]
stats = [{{"stat": str(k), "value": str(v)}}
         for k, v in s.describe(include="all").to_dict().items()]
top = [{{"value": str(k), "count": int(v)}}
       for k, v in s.value_counts(dropna=False).head(6).to_dict().items()]
result = {{
    "status": "info",
    "metric": round(float(s.isna().mean()), 4),
    "threshold": None,
    "violation_count": int(s.isna().sum()),
    "evidence": {{"analysis": "profile_column", "column": col,
                  "distinct": int(s.nunique(dropna=True)), "sample": (stats + top)[:16]}},
}}
'''


def create_analyses(issue_row_id: str, analyses: list[dict]) -> list[dict]:
    """Turn selected suggested analyses into plan rows under scope 'rca:{id}'.
    HITL is identical to Test Lab: the snippet is editable and nothing runs
    until approved (the edited code is the source of truth)."""
    row = require_issue(issue_row_id)
    scope = f"rca:{issue_row_id}"
    out = []
    for a in analyses:
        title = (a.get("title") or a.get("kind") or "Additional analysis").strip()
        columns = [c for c in (a.get("columns") or []) if c]
        plan_row = {
            "row_id": service._id("plan"), "item_id": row["item_id"],
            "table_name": row["table_name"], "scope": scope,
            # status "pending" (not "proposed") so finalize_plan can pick the row
            # up — the HITL gate is the Approve button, exactly like Test Lab.
            # ("proposed" rows are excluded from finalize by design for the
            # incremental scope; that exclusion silently blocked RCA analyses.)
            "test_name": title, "area_id": row.get("area_id"),
            "origin": "rca", "status": "pending",
            "columns_json": columns, "params_json": {"kind": a.get("kind", "profile_column")},
            "reason": a.get("rationale") or "RCA additional analysis",
            "snippet_code": _analysis_code(a.get("kind", "profile_column"), columns),
            "approved": 0, "trigger": issue_row_id,
            "created_at": db.now_ist(), "updated_at": db.now_ist(),
        }
        db.insert("plan_v2", plan_row)
        out.append(plan_row)
    return out


# ── Interpretation of executed analyses (feedback 5.1) ───────────────────────
# Deterministic per-analysis summaries grounded in the stored result.

def _summarise_analysis(res: dict) -> str:
    evidence = res.get("evidence") or {}
    kind = evidence.get("analysis") or "analysis"
    rows = evidence.get("sample") or []
    metric = res.get("metric")
    violations = int(res.get("violation_count") or 0)
    if kind == "segment_breakdown":
        col, seg = evidence.get("column"), evidence.get("segment")
        worst = max((r for r in rows if isinstance(r, dict) and r.get("null_share") is not None),
                    key=lambda r: r["null_share"], default=None)
        parts = [f"Breakdown of {col} by {seg} across {len(rows)} segment(s)."]
        if worst is not None:
            seg_value = worst.get(seg) if seg in worst else next(iter(worst.values()), "?")
            parts.append(f"The worst segment is '{seg_value}' with {round(float(worst['null_share']) * 100, 1)}% missing —"
                         " concentration in one segment points to a workflow or source difference rather than a systemic defect.")
        if metric is not None:
            parts.append(f"Overall missing share of {col}: {round(float(metric) * 100, 1)}%.")
        return " ".join(parts)
    if kind == "cross_check":
        cols = evidence.get("columns") or []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        corr = first.get("correlation")
        parts = [f"Cross-check of {' vs '.join(map(str, cols))}: {violations} row(s) populated on one side only."]
        if corr is not None:
            strength = "strong" if abs(float(corr)) >= 0.7 else "moderate" if abs(float(corr)) >= 0.3 else "weak"
            parts.append(f"Correlation {corr} ({strength}).")
        parts.append("A systematic one-sided gap indicates a timing or definition mismatch between the two fields."
                     if violations else "The two fields populate together consistently.")
        return " ".join(parts)
    # profile_column (default)
    col = evidence.get("column")
    distinct = evidence.get("distinct")
    parts = [f"Deep profile of {col}:"]
    if metric is not None:
        parts.append(f"{round(float(metric) * 100, 1)}% missing ({violations} rows),")
    if distinct is not None:
        parts.append(f"{distinct} distinct values.")
    parts.append("Compare these stats against the dictionary definition to confirm whether the failing metric reflects a data-shape change.")
    return " ".join(parts)


def interpret_analyses(issue_row_id: str) -> dict:
    """Deterministic summary and interpretation of executed analyses."""
    row = require_issue(issue_row_id)
    scope = f"rca:{issue_row_id}"
    executed = service.results(row["item_id"], scope)
    summaries = []
    for res in executed:
        summaries.append({"row_id": res["row_id"], "test_name": res["test_name"],
                          "text": _summarise_analysis(res)})
    if not summaries:
        return {"summaries": [], "overall": ""}
    fallback = " ".join(s["text"] for s in summaries)
    return {"summaries": summaries, "overall": fallback}


def update_tracked(issue_id: str, changes: dict) -> dict:
    """Update details of a raised tracked issue (feedback 5.3)."""
    tracked = db.query_one("tracked_issues_v2", issue_id=issue_id)
    if not tracked:
        raise KeyError(f"Unknown tracked issue: {issue_id}")
    allowed = {k: v for k, v in changes.items()
               if k in {"title", "description", "owner", "priority", "target_date", "status"} and v is not None}
    if allowed:
        db.update("tracked_issues_v2", {"issue_id": issue_id}, allowed)
    tracked = db.query_one("tracked_issues_v2", issue_id=issue_id)
    return {k: tracked.get(k) for k in
            ("issue_id", "title", "description", "owner", "priority", "target_date", "status")}
