"""Diagnostic #4's run orchestration: freeze -> execute -> persist -> hand off.

This is the only module that turns a frozen manifest into rows. It:

* rebuilds the executable state from ``manifest_json`` ALONE (6-T12 — no
  path re-reads the KB or re-resolves roles at run time, so the run cannot
  drift from what the scope gate showed),
* streams ``start`` -> ``progress`` -> ``done`` events for the SSE endpoint
  (CFR-13), errors sanitized by the router (PLT-04),
* persists one ``diag_results`` row (decision-type-shaped, FWK-07) plus one
  ``diag_findings`` row per rule — PASS, VIOLATION *and* NOT-APPLICABLE, so
  the accounting is queryable rather than just the failures,
* auto-opens an issue for every VIOLATION into the existing Issue
  Management / RCA hand-off (CFR-14 / RCA-27).

Read-only against the dataset throughout (CFR-15).
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import system_db as s

from . import ENGINE_VERSION, iter_run, render_pdf, render_text
from . import manifest as manifest_mod
from .result import StructuredResult
from domains.test_lab.shared.results import run_results
from dq_diagnostics.engines.base import Readiness, register_engine
from dq_diagnostics.readiness import readiness as readiness_fn
from dq_diagnostics.result import DiagnosticResult

DIAGNOSTIC_ID = 4


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
#  Execution
# ---------------------------------------------------------------------------

def _preamble(manifest: dict[str, Any]) -> dict[str, Any]:
    docs = manifest["kb"]["documents"]
    label = ", ".join(f"{d.get('title')} (v{d.get('version_seq')})" for d in docs) or "published knowledge base"
    return {
        "kb_source": label,
        "kb_documents": docs,
        "use_case": manifest["use_case"]["value"],
        "dataset": manifest.get("item_name") or manifest["item_id"],
        "item_id": manifest["item_id"],
        "tables": manifest_mod._scoped_tables(manifest),
        "parameter_sources": manifest_mod.threshold_sources(manifest),
        # Verification is provenance only: its presence in report metadata
        # must not alter rule evaluation, maths, or the final verdict.
        "role_verification": manifest["role_verification"],
    }


def evaluate_manifest(manifest: dict[str, Any]) -> Generator[dict[str, Any], None, StructuredResult]:
    """Execute a manifest and yield progress. Pure of persistence — used
    both by :func:`run` and by the determinism/fidelity tests, which need to
    re-derive a result from ``manifest_json`` without touching the DB."""
    rules = manifest_mod.rules_from_manifest(manifest)
    resolved = manifest_mod.roles_from_manifest(manifest)
    vocab = manifest_mod.vocab_from_manifest(manifest)
    thresholds = manifest_mod.threshold_values(manifest)
    tables = manifest_mod.load_tables(manifest["item_id"], manifest_mod._scoped_tables(manifest))
    return (yield from iter_run(rules, resolved, vocab, tables, thresholds,
                                preamble=_preamble(manifest)))


def _dataset_verdict(structured: StructuredResult) -> tuple[str, str | None]:
    """CFR-04/CFR-08 at the dataset level: any VIOLATION -> violation; at
    least one rule actually evaluated and none violated -> pass; nothing
    evaluable -> NOT-APPLICABLE with the reason, never a silent pass."""
    rollup = structured.rollup
    if rollup.get("VIOLATION"):
        return "violation", None
    if rollup.get("PASS"):
        return "pass", None
    if rollup.get("NOT-APPLICABLE"):
        reasons = sorted({r.get("na_reason") for r in structured.by_outcome("NOT-APPLICABLE")
                          if r.get("na_reason")})
        head = reasons[0] if len(reasons) == 1 else f"{len(reasons)} distinct reasons; first: {reasons[0]}"
        return "not_applicable", f"no rule could be evaluated on this dataset — {head}"
    return "not_applicable", ("no knowledge base published for scope "
                              f"{structured.preamble.get('use_case')}")


def persist(manifest: dict[str, Any], structured: StructuredResult) -> dict[str, Any]:
    """Write the FWK-07 result + one finding per rule. Returns ids/counts."""
    run_id = manifest["run_id"]
    verdict, na_reason = _dataset_verdict(structured)
    evaluated = sum(int(r.get("scope_rows_evaluated") or 0) for r in structured.rules)
    skipped = sum(int(r.get("scope_rows_skipped") or 0) for r in structured.rules)
    excepted = sum(int(r.get("exceptions_applied") or 0) for r in structured.rules)

    # FWK-07 envelope: validated by DiagnosticResult.__post_init__ BEFORE
    # anything is written, so a structurally invalid result can never be
    # persisted (a candidate_flag payload could not take this path).
    envelope = DiagnosticResult(
        decision_type=manifest["diagnostic"]["decision_type"],
        diagnostic_id=manifest["diagnostic_id"],
        verdict=verdict,
        na_reason=na_reason,
        metric=_violated_rule_rate(structured),
        evidence={"rollup": structured.rollup,
                  "by_severity": structured.severity_counts(),
                  "rules_loaded": structured.preamble.get("rules_loaded", 0),
                  "unresolved_roles": structured.preamble.get("unresolved_roles", [])},
    )
    result_id = _id("dres")
    now = s.now_ist()
    s.insert("diag_results", {
        "result_id": result_id, "run_id": run_id,
        "diagnostic_id": manifest["diagnostic_id"],
        "entity_or_table": ",".join(sorted(manifest_mod._scoped_tables(manifest))),
        "decision_type": envelope.decision_type, "verdict": envelope.verdict,
        "review_state": envelope.review_state,
        "metrics_json": {"metric": envelope.metric, **envelope.evidence,
                         "structured_result": structured.to_dict()},
        "thresholds_used_json": {"values": manifest_mod.threshold_values(manifest),
                                 "sources": manifest_mod.threshold_sources(manifest)},
        "scope_counts_json": {
            "rules_total": structured.preamble.get("rules_loaded", 0),
            "rows_evaluated": evaluated,
            "rows_skipped_censored": skipped,
            "rows_excepted": excepted,
            "tables_excluded": manifest["scope"]["excluded_tables"],
        },
        "na_reason": envelope.na_reason, "created_at": now,
    })

    finding_ids = []
    for seq, entry in enumerate(structured.rules):
        finding_id = _id("dfind")
        finding_ids.append(finding_id)
        s.insert("diag_findings", {
            "finding_id": finding_id, "result_id": result_id, "run_id": run_id,
            "rule_id": entry["rule_id"], "kb_rule_id": entry.get("kb_rule_id"),
            "severity": entry.get("severity"), "outcome": entry["outcome"],
            "violation_count": entry.get("violation_count"),
            "rate": entry.get("rate"), "tolerance": entry.get("tolerance"),
            "exceptions_json": {"count": entry.get("exceptions_applied") or 0,
                                "notes": entry.get("exception_notes") or []},
            "evidence_json": entry.get("evidence") or [],
            "pattern": entry.get("pattern"), "pattern_detail": entry.get("pattern_detail"),
            "regulatory_ref": entry.get("regulatory_ref"),
            "rule_text": entry.get("rule_text"), "rule_type": entry.get("rule_type"),
            "framework": entry.get("framework"), "entity": entry.get("entity"),
            "resolved_roles_json": entry.get("resolved_roles") or {},
            "tables_used": entry.get("tables_used"),
            "scope_rows_evaluated": entry.get("scope_rows_evaluated"),
            "scope_rows_skipped": entry.get("scope_rows_skipped"),
            "na_reason": entry.get("na_reason"),
            "review_state": None, "seq": seq, "created_at": now,
        })
    return {"result_id": result_id, "verdict": verdict, "na_reason": na_reason,
            "findings": len(finding_ids), "rollup": structured.rollup}


def _violated_rule_rate(structured: StructuredResult) -> float | None:
    decided = structured.rollup.get("PASS", 0) + structured.rollup.get("VIOLATION", 0)
    if not decided:
        return None
    return structured.rollup.get("VIOLATION", 0) / decided


def run(run_id: str, actor: str = "system") -> Generator[dict[str, Any], None, None]:
    """Freeze (if still draft), execute, persist, hand off — as a stream of
    SSE-shaped events: ``start`` -> ``progress``* -> ``done``.

    Exceptions propagate to the router, which sanitizes them into an
    ``error`` frame (PLT-04) — this module never formats one itself, so a
    raw exception string cannot reach a client through here.
    """
    run_row = manifest_mod.get_run(run_id)
    if run_row["status"] != manifest_mod.DONE:
        from analytics.events import event_object_id, record_event  # noqa: PLC0415
        asset = s.query_one("dq_assets", asset_id=(s.query_one("dq_items", item_id=run_row["item_id"]) or {}).get("dataset_family_id")) or {}
        record_event(event_type="diagnostic_run_started", actor=actor or "system", at=s.now_ist(),
                     object_type="snapshot", object_id=event_object_id("snapshot", run_row["item_id"]),
                     workflow_context=asset.get("system_id"),
                     detail={"diagnostic_id": run_row.get("diagnostic_id"),
                             "test_id": run_row.get("diagnostic_id")})
    if run_row["status"] == manifest_mod.DONE:
        # A completed run is replayed, never re-executed: results are already
        # persisted and a second execution would duplicate them.
        results = s.query("diag_results", run_id=run_id)
        rollup = (results[0]["metrics_json"] or {}).get("rollup", {}) if results else {}
        yield {"phase": "start", "agent": "cross_field_engine", "run_id": run_id,
               "thought": "Replaying a completed run (results are already persisted)."}
        yield {"phase": "done", "agent": "cross_field_engine", "run_id": run_id,
               "result_id": results[0]["result_id"] if results else None,
               "verdict": results[0]["verdict"] if results else None,
               "rollup": rollup,
               "findings": len(s.query("diag_findings", run_id=run_id)),
               "issues": {"created": [], "updated": [], "skipped_locked": [],
                          "violations": rollup.get("VIOLATION", 0)},
               "thought": "Run already complete."}
        return
    if run_row["status"] == manifest_mod.DRAFT:
        manifest = manifest_mod.freeze(run_id, actor)
    else:
        manifest = run_row["manifest_json"]
    yield {"phase": "start", "agent": "cross_field_engine", "run_id": run_id,
           "total": len(manifest["rules"]),
           "thought": f"Evaluating {len(manifest['rules'])} bound rules "
                      f"({manifest['use_case']['value']}) against "
                      f"{len(manifest_mod._scoped_tables(manifest))} table(s)."}

    generator = evaluate_manifest(manifest)
    structured: StructuredResult | None = None
    while True:
        try:
            event = next(generator)
        except StopIteration as stop:
            structured = stop.value
            break
        yield {"phase": "progress", "agent": "cross_field_engine",
               "done": event["done"], "total": event["total"],
               "thought": f"[{event['done']}/{event['total']}] {event['rule_id']} "
                          f"[{event['severity']}] -> {event['outcome']}"}

    summary = persist(manifest, structured)
    from ai.v2 import issues as issues_svc
    issue_summary = issues_svc.sync_diagnostic_issues(manifest["item_id"], run_id)
    now = s.now_ist()
    s.update("diag_runs", {"run_id": run_id},
             {"status": manifest_mod.DONE, "finished_at": now})
    yield {"phase": "done", "agent": "cross_field_engine", "run_id": run_id,
           "result_id": summary["result_id"], "verdict": summary["verdict"],
           "rollup": summary["rollup"], "findings": summary["findings"],
           "issues": issue_summary,
           "thought": f"Run complete — {summary['rollup'].get('PASS', 0)} PASS · "
                      f"{summary['rollup'].get('VIOLATION', 0)} VIOLATION · "
                      f"{summary['rollup'].get('NOT-APPLICABLE', 0)} NOT-APPLICABLE."}


def execute_now(run_id: str, actor: str = "system") -> dict[str, Any]:
    """Synchronous convenience wrapper: drain :func:`run`, return the final
    ``done`` frame. Used by tests and by any caller that does not stream."""
    last: dict[str, Any] = {}
    for event in run(run_id, actor):
        last = event
    return last


# ---------------------------------------------------------------------------
#  Read-back
# ---------------------------------------------------------------------------

def latest_run(item_id: str, diagnostic_id: int = DIAGNOSTIC_ID,
               status: str | None = None) -> dict[str, Any] | None:
    filters: dict[str, Any] = {"item_id": item_id, "diagnostic_id": diagnostic_id}
    if status:
        filters["status"] = status
    rows = s.query("diag_runs", **filters, order_by="created_at DESC, run_id DESC")
    return rows[0] if rows else None


def structured_result(run_id: str) -> StructuredResult:
    """Rehydrate the CFR-10 structure a run persisted (report rendering)."""
    results = s.query("diag_results", run_id=run_id)
    if not results:
        raise KeyError(f"No results for run: {run_id}")
    payload = (results[0]["metrics_json"] or {}).get("structured_result") or {}
    return StructuredResult(preamble=payload.get("preamble", {}),
                            rollup=payload.get("rollup", {}),
                            rules=payload.get("rules", []))


def report_text(run_id: str) -> str:
    return render_text(structured_result(run_id))


def report_pdf(run_id: str) -> bytes:
    return render_pdf(structured_result(run_id))


# ---------------------------------------------------------------------------
#  The engine adapter (FWK-18 seam)
# ---------------------------------------------------------------------------

class CrossFieldEngine:
    """Diagnostic #4 as a :class:`~.engines.base.DiagnosticEngine`."""

    diagnostic_id = DIAGNOSTIC_ID
    engine_version = ENGINE_VERSION

    def readiness(self, item: dict[str, Any], ctx: dict[str, Any] | None = None) -> Readiness:
        ctx = ctx or {}
        return readiness_fn(item["item_id"], self.diagnostic_id,
                            ctx.get("tenant_id", "bootstrap"))

    def build_manifest(self, item: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = ctx or {}
        return manifest_mod.build_manifest(item["item_id"], self.diagnostic_id,
                                           ctx.get("actor", "system"),
                                           ctx.get("tenant_id", "bootstrap"),
                                           ctx.get("use_case"))

    def execute(self, manifest: dict[str, Any], ctx: dict[str, Any] | None = None,
                emit=None) -> dict[str, Any]:
        ctx = ctx or {}
        last: dict[str, Any] = {}
        for event in run(manifest["run_id"], ctx.get("actor", "system")):
            last = event
            if emit is not None:
                emit(event)
        return last


register_engine(CrossFieldEngine())


# ---------------------------------------------------------------------------
#  Coverage-honest roll-up (FWK-16)
# ---------------------------------------------------------------------------

def coverage_summary(item_id: str) -> dict[str, Any]:
    """testlab-redesign §3 step 5 — what ran, what is pending, what the
    numbers cover. There is deliberately NO weighted health-score field on
    this payload at all (not a zero, not a null): a one-diagnostic score
    presented as a health score is a lie of omission, and a field that does
    not exist cannot be rendered as one.
    """
    from dq_diagnostics.register import coverage_map, list_register, scoring_weights
    coverage = coverage_map()
    weights = scoring_weights()
    register = {r["diagnostic_id"]: r for r in list_register()}

    latest = latest_run(item_id, status=manifest_mod.DONE)
    verdicts: dict[str, int] = {}
    by_severity: dict[str, dict[str, int]] = {}
    ran: list[dict[str, Any]] = []
    if latest:
        results = s.query("diag_results", run_id=latest["run_id"])
        for res in results:
            metrics = res["metrics_json"] or {}
            verdicts = metrics.get("rollup", {})
            by_severity = metrics.get("by_severity", {})
        ran.append({
            "diagnostic_id": latest["diagnostic_id"],
            "name": (register.get(latest["diagnostic_id"]) or {}).get("name"),
            "run_id": latest["run_id"], "finished_at": latest["finished_at"],
            "decision_type": (register.get(latest["diagnostic_id"]) or {}).get("decision_type"),
        })
    return {
        "item_id": item_id,
        "executable": [{"diagnostic_id": d, "name": (register.get(d) or {}).get("name")}
                       for d in coverage["executable"]],
        "workflow_pending": [{"diagnostic_id": d, "name": (register.get(d) or {}).get("name")}
                             for d in coverage["workflow_pending"]],
        "registered_total": len(register),
        "executable_total": len(coverage["executable"]),
        "ran": ran,
        "not_run": [{"diagnostic_id": d, "name": (register.get(d) or {}).get("name")}
                    for d in coverage["executable"]
                    if d not in {r["diagnostic_id"] for r in ran}],
        "verdict_rollup": verdicts,
        "by_severity": by_severity,
        "gap_areas": [{"l2_id": a["l2_id"], "name": a["name"], "reason": a["reason"]}
                      for a in coverage["areas"] if a["status"] != "covered"],
        "coverage_statement": weights["coverage_statement"],
    }
