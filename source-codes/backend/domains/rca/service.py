"""RCA Stage 3 — vertical slice service.

One complete threshold-failure path: failed test -> case+intake -> opening
look -> one Planner/Runner/Reader cycle -> suspect update -> hypothesis ->
rejection-capable confirmation check -> human-approved simulated fix ->
original-test rerun -> closure. An optional, explicit post-closure action can
propose reusable knowledge for independent KB review. See
docs/rca/00-contracts.md §3-§4 for the frozen record model and agent I/O
contract this implements.

Deliberately consolidated into one module rather than the many small files
sketched illustratively in the Stage 0 contract (`rca/planner.py` etc.)
— those were directional target paths, and one well-organized ~700-line
module is a better fit for this stage's actual size than a package of
one-function files. Function names below are grouped and commented to match
each agent's role from the contract.

Deterministic throughout, by design: MP §14 Stage 3 says "do not require
generated helper code for the first slice" and "add deterministic fake
agents for automated tests" — this module IS that deterministic
implementation, not a stand-in for a live-model one. No Azure OpenAI call is
required anywhere in this file or its tests (contracts §0/testing posture:
"do not make billable live model calls without explicit authorization").

Note: the legacy `ai/rca_helpers.py` module is NOT reused here — it imports
`from database import TABLE_KEYS`, and `database.py` was retired from this
product generation, so that module is currently unimportable
(`ModuleNotFoundError: No module named 'database'`, confirmed directly). The
deterministic "look" functions below are written fresh against the current
v2 data model (`ai/v2/service._read_table` etc.) instead of resurrecting
dead code tied to the retired warehouse.
"""
from __future__ import annotations

import uuid

import kb
import system_db as s
import taxonomy
import tenancy
from ai.v2 import service as v2_service

DEFAULT_TENANT = tenancy.DEFAULT_TENANT

# Cause-family inference + fix routing (Fix advisor, Agent 9). Previously
# imported from ai/rca_cases.py — that module (and its sibling ai/rca_agent.py)
# was dead code with no reachable caller anywhere in the app (confirmed by
# grep: nothing outside those two files imported from them), so both were
# deleted rather than kept around just to avoid moving these two small
# pieces. Their legacy rca_cases/rca_hypotheses/etc. tables are gone too —
# this module's own tables no longer need any version suffix to avoid
# colliding with them.
CAUSE_FAMILIES = {
    "freshness": ("freshness", "date", "gap", "batch", "stale"),
    "population_mix": ("psi", "ks", "drift", "distribution", "mix"),
    "schema_type": ("schema", "type", "parse", "dtype"),
    "duplicate_join": ("duplicate", "join", "reference", "integrity", "key"),
    "leakage": ("leakage", "target", "post-outcome"),
    "outlier_sentinel": ("outlier", "sentinel", "9999", "-1", "spike"),
    "mapping_logic": ("mapping", "rule", "logic", "transform"),
    "source_feed": ("source", "vendor", "feed", "upstream"),
    "policy_change": ("policy", "cutoff", "threshold"),
    "model_target": ("target", "vintage", "cohort"),
}

FIX_BY_CAUSE = {
    "freshness": "backfill",
    "population_mix": "threshold_rebaseline",
    "schema_type": "schema_contract_change",
    "duplicate_join": "deduplication",
    "leakage": "mapping_change",
    "outlier_sentinel": "mapping_change",
    "mapping_logic": "mapping_change",
    "source_feed": "source_feed_fix",
    "policy_change": "threshold_rebaseline",
    "model_target": "model_recalibration",
    "reference_data": "reference_data_refresh",
    "unknown": "source_feed_fix",
}


def infer_cause_family(*parts) -> str:
    text = " ".join(str(p or "") for p in parts).lower()
    for family, tokens in CAUSE_FAMILIES.items():
        if any(token in text for token in tokens):
            return family
    return "unknown"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RcaError(Exception):
    pass


class TransitionError(RcaError):
    pass


# --- State machine (contracts.md §3, verbatim) --------------------------------

ALLOWED_NEXT: dict[str, set[str]] = {
    "created": {"triage"},
    "triage": {"intake"},
    "intake": {"opening_looks"},
    "opening_looks": {"investigation_loop"},
    "investigation_loop": {"awaiting_human_answer", "coverage_challenge_blind", "hypothesis_composition"},
    "awaiting_human_answer": {"investigation_loop"},
    "coverage_challenge_blind": {"coverage_challenge_history"},
    "coverage_challenge_history": {"reopened_kill_attempt", "hypothesis_composition"},
    "reopened_kill_attempt": {"hypothesis_composition"},
    "hypothesis_composition": {"ready_for_verification"},
    "ready_for_verification": {"verification_planning"},
    "verification_planning": {"confirmation_checks"},
    "confirmation_checks": {"judging"},
    # judging -> confirmation_checks covers two Stage 5 loop-backs: an
    # inconclusive verdict awaiting its one refinement, and a rejected/
    # unverified verdict advancing to the next tier-ordered queued check.
    "judging": {"symptom_accounting", "all_hypotheses_rejected", "confirmation_checks"},
    "symptom_accounting": {"awaiting_fix_approval", "confirmation_checks"},
    "awaiting_fix_approval": {"awaiting_fix_application"},
    "awaiting_fix_application": {"closure_rerun"},
    "closure_rerun": {"reconciliation"},
    "reconciliation": {"closed"},
    "all_hypotheses_rejected": {"second_chance_part_a", "escalated"},
    # second_chance_part_a re-enters the SAME investigation_loop/coverage-
    # challenge/composition/verification path used the first time (WF §6:
    # "the case returns once, with the disproof added, and ~5 fresh looks")
    # — no separate mechanism is needed, only a reduced bonus budget
    # (see _second_chance_budget_bonus) and exclusion of already-explored
    # columns (already implicit: planner_propose_look never revisits a used
    # segment_column regardless of the suspect's eventual verdict).
    "second_chance_part_a": {"investigation_loop", "second_chance_failed"},
    "second_chance_failed": {"escalated"},
    "escalated": {"unresolved"},
}

# The product-facing MVP stops when an evidence-backed conclusion is approved.
# Internal analytical states remain available to the deterministic engine, but
# they may all hand off to an approved conclusion without pretending that a fix
# was applied or requiring a diagnostic rerun.
_CONCLUSION_READY_STATES = {
    "opening_looks", "investigation_loop", "awaiting_human_answer",
    "coverage_challenge_blind", "coverage_challenge_history",
    "reopened_kill_attempt", "hypothesis_composition", "ready_for_verification",
    "verification_planning", "confirmation_checks", "judging",
    "symptom_accounting", "awaiting_fix_approval", "all_hypotheses_rejected",
}


def _product_transition(case: dict, new_state: str, actor: str, reason: str,
                        evidence_ids: list[str] | None = None) -> None:
    """Record an MVP product-stage handoff without widening legacy internals.

    ``transition`` retains its frozen granular state contract for old callers;
    only the conclusion services use this deliberately narrow bridge.
    """
    now = s.now_ist()
    s.insert("rca_state_transitions", {
        "id": _id("trs"), "case_id": case["case_id"], "prev_state": case["state"],
        "new_state": new_state, "actor": actor, "reason": reason,
        "evidence_ids_json": evidence_ids or [], "ts": now,
        "workflow_version": "rca-mvp", "contract_version": "2",
    })
    s.update("rca_cases", {"case_id": case["case_id"]}, {
        "state": new_state, "updated_at": now,
        "closed_at": now if new_state == "closed" else case.get("closed_at"),
    })
    _audit(case["tenant_id"], actor, "state_transition", "rca_case", case["case_id"],
           before={"state": case["state"]}, after={"state": new_state}, reason=reason)


def _audit(tenant_id: str, actor: str, event_type: str, object_type: str, object_id: str,
          before: dict | None = None, after: dict | None = None, reason: str | None = None) -> None:
    s.insert("rca_audit_events", {
        "event_id": _id("aud"), "tenant_id": tenant_id, "actor": actor, "event_type": event_type,
        "object_type": object_type, "object_id": object_id,
        "before_json": before or {}, "after_json": after or {}, "reason": reason,
        "ts": s.now_ist(),
    })


def require_case(case_id: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    row = s.query_one("rca_cases", case_id=case_id)
    if not row or row["tenant_id"] != tenant_id:
        raise KeyError("Unknown RCA case")
    return row


def transition(case_id: str, new_state: str, actor: str, reason: str | None = None,
               evidence_ids: list[str] | None = None, tenant_id: str = DEFAULT_TENANT) -> dict:
    """The only function that ever writes rca_cases.state — validates
    against ALLOWED_NEXT and records the move, per contracts.md §3's
    'deterministic workflow services own every transition' rule. tenant_id
    defaults to the bootstrap tenant for source compatibility with existing
    single-argument-style call sites, but every real caller in this module
    passes its own already-validated tenant_id explicitly (Stage 6 hardening
    — this function used to be tenant-blind, silently checking every case
    against the bootstrap tenant no matter which tenant was really calling)."""
    case = require_case(case_id, tenant_id)
    prev = case["state"]
    if new_state not in ALLOWED_NEXT.get(prev, set()):
        raise TransitionError(f"Illegal transition {prev!r} -> {new_state!r}")
    now = s.now_ist()
    s.insert("rca_state_transitions", {
        "id": _id("trs"), "case_id": case_id, "prev_state": prev, "new_state": new_state,
        "actor": actor, "reason": reason, "evidence_ids_json": evidence_ids or [], "ts": now,
        "workflow_version": "rca", "contract_version": "1",
    })
    closed_at = now if new_state == "closed" else case.get("closed_at")
    s.update("rca_cases", {"case_id": case_id}, {"state": new_state, "updated_at": now, "closed_at": closed_at})
    _audit(case["tenant_id"], actor, "state_transition", "rca_case", case_id,
          before={"state": prev}, after={"state": new_state}, reason=reason)
    return require_case(case_id, case["tenant_id"])


# --- Case creation: case + intake ---------------------------------------------

TEST_FAMILIES = {
    "drift": ("psi", "ks", "drift", "distribution", "stability"),
    "missing_data": ("completeness", "missing", "mcar", "mnar"),
    "schema": ("schema", "type", "format"),
    "ordering": ("ordering", "sequence", "chronology"),
    "series_break": ("trend", "vintage", "maturity", "regime", "seasoning"),
}


def _infer_test_family(test_name: str) -> str:
    low = (test_name or "").lower()
    for family, tokens in TEST_FAMILIES.items():
        if any(t in low for t in tokens):
            return family
    return "missing_data"  # the only family Stage 4's opening looks implement content for


# --- Triage (Agent 0) ----------------------------------------------------------
# contracts.md §0 rules: two-signal grouping (same lineage/time window AND
# similar symptom shape — one signal alone keeps failures separate).
# "Lineage/time window" proxy: same item_id + table_name + an open group
# created within TRIAGE_TIME_WINDOW_MINUTES. "Symptom shape" proxy: same
# inferred test family. Both must match to group.

TRIAGE_TIME_WINDOW_MINUTES = 30


def _minutes_since(iso_ts: str) -> float:
    from datetime import datetime
    return (datetime.fromisoformat(s.now_ist()) - datetime.fromisoformat(iso_ts)).total_seconds() / 60.0


def find_matching_open_group(tenant_id: str, item_id: str, table_name: str, test_name: str) -> dict | None:
    family = _infer_test_family(test_name)
    for group in s.query("rca_failure_groups", tenant_id=tenant_id):
        case = s.query_one("rca_cases", case_id=group["case_id"])
        if not case or case["state"] == "closed" or case["item_id"] != item_id or case["table_name"] != table_name:
            continue
        same_time_window = _minutes_since(case["created_at"]) <= TRIAGE_TIME_WINDOW_MINUTES
        same_symptom_shape = group.get("two_signal_evidence_json", {}).get("family") == family
        if same_time_window and same_symptom_shape:
            return group
    return None


def create_case_from_issue(issue_row_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """New cases never start with pre-seeded causes (WF §1.1) — no
    hypothesis/suspect row is created here, only the case shell + intake
    file. One case per issue row (idempotent: returns the existing case if
    called again). Triage (Agent 0): a two-signal match against an existing
    open case's failure group attaches this issue's failure instead of
    starting a new investigation — damage from a wrong grouping is capped by
    design (attachments are never investigated themselves; MP/WF Triage
    rules), so this stays a coarse, defensible heuristic rather than a full
    lineage graph the current data model doesn't have."""
    existing = s.query_one("rca_cases", issue_row_id=issue_row_id, tenant_id=tenant_id)
    if existing:
        # Idempotent-return race guard: each insert/transition below commits
        # independently (system_db has no shared-transaction wrapper across
        # a whole service call), so a case is briefly visible mid-bootstrap
        # (state=created/triage/intake) to any concurrent caller — a real
        # scenario, not just theoretical: React StrictMode double-invokes
        # useEffect in dev, and two browser tabs/a network retry can do the
        # same in production. Poll briefly for the case to clear these
        # transient states rather than handing back a half-built snapshot;
        # bounded so a genuinely stuck case (a bug elsewhere) still returns
        # promptly instead of hanging.
        if existing["state"] in {"created", "triage", "intake"}:
            import time
            for _ in range(20):
                time.sleep(0.05)
                existing = s.query_one("rca_cases", case_id=existing["case_id"], tenant_id=tenant_id)
                if existing["state"] not in {"created", "triage", "intake"}:
                    break
        return existing
    issue = s.query_one("issues_v2", issue_row_id=issue_row_id)
    if not issue:
        raise KeyError(f"Unknown issue row: {issue_row_id}")
    item = v2_service.require_item(issue["item_id"])
    family = _infer_test_family(issue["test_name"])

    matching_group = find_matching_open_group(tenant_id, issue["item_id"], issue["table_name"], issue["test_name"])
    if matching_group:
        s.insert("rca_attached_failures", {
            "id": _id("attf"), "group_id": matching_group["group_id"], "run_id": None,
            "reconciliation_status": "pending", "reconciled_at": None,
        })
        _audit(tenant_id, actor, "agent_invocation", "rca_failure_groups", matching_group["group_id"],
              after={"attached_issue_row_id": issue_row_id},
              reason="two-signal Triage match: same table + time window + symptom shape")
        return require_case(matching_group["case_id"], tenant_id)

    case_id = _id("rca")
    now = s.now_ist()
    tag_snapshot = taxonomy.get_tags(tenant_id, "issues_v2", issue_row_id)
    s.insert("rca_cases", {
        "case_id": case_id, "tenant_id": tenant_id, "issue_row_id": issue_row_id,
        "item_id": issue["item_id"], "table_name": issue["table_name"],
        "state": "created", "part": "A", "tag_snapshot_json": tag_snapshot,
        "complaint_text": None, "created_by": actor, "created_at": now, "updated_at": now,
        "closed_at": None, "contract_version": "1",
    })
    _audit(tenant_id, actor, "agent_invocation", "rca_case", case_id, after={"issue_row_id": issue_row_id})

    transition(case_id, "triage", actor, reason=f"no matching open group (family={family}) — new representative case", tenant_id=tenant_id)
    group_id = _id("rgrp")
    s.insert("rca_failure_groups", {
        "group_id": group_id, "tenant_id": tenant_id, "case_id": case_id,
        "representative_run_id": None, "two_signal_evidence_json": {"family": family}, "created_at": s.now_ist(),
    })
    transition(case_id, "intake", actor, tenant_id=tenant_id)

    # Fixed intake checklist (WF Agent 1), by test family.
    checklist = {
        "test_name": issue["test_name"], "test_family": family, "table_name": issue["table_name"],
        "columns": issue.get("columns_json") or [], "metric": issue.get("metric"),
        "threshold": issue.get("threshold_json"), "violation_count": issue.get("violation_count"),
        "target_variable": item.get("target_variable"), "use_case": item.get("use_case"),
    }
    s.insert("rca_case_files", {
        "case_id": case_id, "checklist_json": checklist,
        "schema_snapshot_json": v2_service._inventory_map(issue["item_id"], issue["table_name"]),
        "tags_snapshot_json": tag_snapshot, "complaint_json": None, "created_at": now,
    })
    transition(case_id, "opening_looks", actor, tenant_id=tenant_id)
    return require_case(case_id, tenant_id)


def get_case(case_id: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    case_file = s.query_one("rca_case_files", case_id=case_id)
    looks = s.query("rca_looks", order_by="seq", case_id=case_id)
    executions = {e["look_id"]: e for e in s.query("rca_look_executions")
                 if e["look_id"] in {lk["look_id"] for lk in looks}}
    suspects = s.query("rca_suspects", order_by="created_at", case_id=case_id)
    hypotheses = s.query("rca_hypotheses", order_by="created_at", case_id=case_id)
    checks = []
    for h in hypotheses:
        checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
    checks.sort(key=lambda c: c["order_rank"])
    # Judge verdicts per check, and the case's confirmed hypothesis (if any)
    # — the UI needs both to route the fix-approval flow to the RIGHT
    # hypothesis now that Stage 4/5 can produce more than one.
    judge_decisions = {c["check_id"]: s.query("rca_judge_decisions", order_by="ts", check_id=c["check_id"])
                       for c in checks}
    accounting = s.query("rca_symptom_accounting", order_by="computed_at", case_id=case_id)
    closure = s.query_one("rca_closures", case_id=case_id)
    transitions = s.query("rca_state_transitions", order_by="ts", case_id=case_id)
    audit_events = [event for event in s.query("rca_audit_events", order_by="ts", tenant_id=tenant_id)
                    if event.get("object_type") == "rca_case" and event.get("object_id") == case_id]
    conclusion_events = [event for event in audit_events
                         if event.get("event_type") == "conclusion_approved"]
    return {**case, "case_file": case_file, "looks": looks, "executions": executions,
           "suspects": suspects, "hypotheses": hypotheses, "confirmation_checks": checks,
           "judge_decisions": judge_decisions, "confirmed_hypothesis": _confirmed_hypothesis(case_id),
           "symptom_accounting": accounting[-1] if accounting else None,
           "closure": closure, "transitions": transitions,
           "conclusion": conclusion_events[-1].get("after_json") if conclusion_events else None,
           "audit_events": audit_events}


def approve_conclusion(case_id: str, actor: str, conclusion: dict,
                       tenant_id: str = DEFAULT_TENANT) -> dict:
    """Complete RCA by approving its conclusion, never by asserting a fix.

    The rich conclusion remains in the immutable audit record while the compact
    closure table continues to provide the existing outcome/index contract.
    """
    case = require_case(case_id, tenant_id)
    if case["state"] == "closed":
        raise TransitionError("This RCA conclusion has already been approved")
    if case["state"] not in _CONCLUSION_READY_STATES:
        raise TransitionError(f"A conclusion cannot be approved from state {case['state']!r}")

    conclusion_type = (conclusion.get("conclusion_type") or "").strip()
    if conclusion_type not in {"root_cause_identified", "unresolved"}:
        raise ValueError("Conclusion type must be root_cause_identified or unresolved")
    rationale = (conclusion.get("approval_rationale") or "").strip()
    if not rationale:
        raise ValueError("An approval rationale is required")
    evidence_ids = [str(value) for value in (conclusion.get("supporting_evidence_ids") or []) if value]
    if not evidence_ids:
        raise ValueError("At least one supporting evidence reference is required")
    root_cause = (conclusion.get("root_cause") or "").strip()
    if conclusion_type == "root_cause_identified" and not root_cause:
        raise ValueError("A root cause is required for an identified conclusion")
    alternatives = (conclusion.get("alternatives_considered") or "").strip()
    if not alternatives:
        raise ValueError("Reasonable alternatives considered must be recorded")
    limiting_evidence = (conclusion.get("limiting_evidence") or "").strip()
    if not limiting_evidence:
        raise ValueError("Contradicting or limiting evidence must be disclosed")
    related_failures = (conclusion.get("related_failures") or "").strip()
    if not related_failures:
        raise ValueError("Related failures must be reconciled against the conclusion")

    outcome = "root_cause_identified" if conclusion_type == "root_cause_identified" else "unresolved"
    now = s.now_ist()
    _product_transition(case, "closed", actor, rationale, evidence_ids)
    s.insert("rca_closures", {
        "case_id": case_id, "outcome": outcome, "rerun_run_id": None,
        "frozen_snapshot": 1,
        "fresh_snapshot_warning": "RCA approval does not confirm remediation.",
        "knowledge_draft_id": None,
        "closed_at": now,
    })
    approved = {
        **conclusion, "conclusion_type": conclusion_type, "root_cause": root_cause,
        "approval_rationale": rationale, "supporting_evidence_ids": evidence_ids,
        "approved_by": actor, "approved_at": now,
        "outcome_label": "Root cause identified" if outcome == "root_cause_identified" else "Unresolved",
        "knowledge_draft_id": None,
    }
    _audit(tenant_id, actor, "conclusion_approved", "rca_case", case_id,
           after=approved, reason=rationale)
    # Managed issue closure means the investigation record is complete. It is
    # deliberately independent of any optional tracked remediation item.
    s.update("issues_v2", {"issue_row_id": case["issue_row_id"]}, {
        "status": "Closed", "resolution_rationale": rationale, "updated_at": now,
    })
    return get_case(case_id, tenant_id)


def propose_reusable_knowledge(case_id: str, actor: str, proposal: dict,
                               tenant_id: str = DEFAULT_TENANT) -> dict:
    """Explicitly hand a closed, evidenced RCA lesson to KB review.

    RCA closure itself never writes knowledge. This separate human action
    creates one reviewable case-history candidate and cannot publish it.
    """
    case = require_case(case_id, tenant_id)
    closure = s.query_one("rca_closures", case_id=case_id)
    if case["state"] != "closed" or not closure:
        raise TransitionError("Reusable knowledge can be proposed only after RCA closure")
    if closure.get("outcome") == "unresolved":
        raise ValueError("An unresolved RCA case cannot be proposed as reusable knowledge")
    if closure.get("knowledge_draft_id"):
        raise TransitionError("This RCA case already has a reusable-knowledge proposal")

    lesson = (proposal.get("reusable_lesson") or "").strip()
    applicability = (proposal.get("applicability_scope") or "").strip()
    generalization = (proposal.get("generalization_reason") or "").strip()
    if not lesson or not applicability or not generalization:
        raise ValueError(
            "reusable_lesson, applicability_scope, and generalization_reason are required")
    evidence_ids = [str(value).strip() for value in
                    (proposal.get("supporting_evidence_ids") or []) if str(value).strip()]
    if not evidence_ids:
        raise ValueError("At least one supporting evidence reference is required")
    case_bundle = get_case(case_id, tenant_id)
    approved_evidence = set((case_bundle.get("conclusion") or {}).get(
        "supporting_evidence_ids") or [])
    approved_evidence.update(entry.get("execution_id")
                             for entry in (case_bundle.get("executions") or {}).values())
    approved_evidence.discard(None)
    unknown = sorted(set(evidence_ids) - approved_evidence)
    if unknown:
        raise ValueError(f"Evidence is not part of the closed RCA record: {', '.join(unknown)}")

    issue = s.query_one("issues_v2", issue_row_id=case["issue_row_id"]) or {}
    related_diagnostic_id = proposal.get("related_diagnostic_id")
    if related_diagnostic_id is not None and related_diagnostic_id != "":
        try:
            related_diagnostic_id = int(related_diagnostic_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("related_diagnostic_id must be an integer") from exc
        if issue.get("diagnostic_id") is None or related_diagnostic_id != issue.get("diagnostic_id"):
            raise ValueError("related_diagnostic_id must match the diagnostic on the closed issue")
    else:
        related_diagnostic_id = None
    related_tables = [str(value).strip() for value in
                      (proposal.get("related_tables") or []) if str(value).strip()]
    if not related_tables:
        related_tables = [case["table_name"]]

    metadata = {
        "reusable_lesson": lesson, "applicability_scope": applicability,
        "generalization_reason": generalization,
        "supporting_evidence_ids": evidence_ids, "related_tables": related_tables,
        "related_diagnostic_id": related_diagnostic_id,
        "source_issue_row_id": case["issue_row_id"],
    }
    draft_rule = kb.draft_rule_from_case_closure(
        tenant_id, category="case_history",
        rule_text=f"{lesson}\n\nApplicability: {applicability}",
        related_tables=related_tables, source_case_id=case_id, actor=actor,
        proposal_metadata=metadata,
    )
    s.update("rca_closures", {"case_id": case_id}, {
        "knowledge_draft_id": draft_rule["rule_id"]})
    _audit(tenant_id, actor, "reusable_knowledge_proposed", "rca_case", case_id,
           after={**metadata, "knowledge_draft_id": draft_rule["rule_id"]},
           reason=generalization)
    return {"case": get_case(case_id, tenant_id), "candidate": draft_rule}


def return_to_investigation(case_id: str, actor: str, reason: str,
                            tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValueError("A reason is required to return to Investigate")
    if case["state"] in {"opening_looks", "investigation_loop"}:
        _audit(tenant_id, actor, "conclusion_returned", "rca_case", case_id,
               after={"stage": "investigate"}, reason=clean_reason)
        return get_case(case_id, tenant_id)
    _product_transition(case, "investigation_loop", actor, clean_reason)
    _audit(tenant_id, actor, "conclusion_returned", "rca_case", case_id,
           after={"stage": "investigate"}, reason=clean_reason)
    return get_case(case_id, tenant_id)


# --- Deterministic look primitive ---------------------------------------------
# Fixed, deterministic computation — no model-generated code, matching the
# opening-look/look contract ("fixed deterministic calculations", WF §4).

def _profile_column(df, column: str) -> dict:
    if column not in df.columns:
        return {"column": column, "found": False}
    series = df[column]
    out = {"column": column, "found": True, "null_share": round(float(series.isna().mean()), 4),
          "distinct": int(series.nunique(dropna=True))}
    if len(series.dropna()):
        try:
            out["mean"] = round(float(series.dropna().astype(float).mean()), 4)
        except (TypeError, ValueError):
            pass
    return out


def _segment_breakdown(df, column: str, segment_column: str) -> dict:
    if column not in df.columns or segment_column not in df.columns:
        return {"column": column, "segment_column": segment_column, "found": False}
    grp = df.groupby(segment_column, dropna=False)[column]
    null_share_by_segment = {str(k): round(float(v), 4) for k, v in grp.apply(lambda x: x.isna().mean()).items()}
    null_count_by_segment = {str(k): int(v) for k, v in grp.apply(lambda x: x.isna().sum()).items()}
    worst_segment, worst_rate = max(null_share_by_segment.items(), key=lambda kv: kv[1], default=(None, 0.0))
    _, best_rate = min(null_share_by_segment.items(), key=lambda kv: kv[1], default=(None, 0.0))
    worst_count = null_count_by_segment.get(worst_segment, 0)
    return {"column": column, "segment_column": segment_column, "found": True,
           "null_share_by_segment": null_share_by_segment,
           "worst_segment": worst_segment, "worst_rate": worst_rate, "best_rate": best_rate,
           "worst_count": worst_count}


# --- Opening looks (Agent 2) ---------------------------------------------------

def run_opening_look(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    case_file = s.query_one("rca_case_files", case_id=case_id)
    checklist = case_file["checklist_json"]
    columns = checklist.get("columns") or []
    column = columns[0] if columns else None
    frame = v2_service._read_table(case["item_id"], case["table_name"])
    summary = _profile_column(frame, column) if column else {"found": False}

    look_id = _id("look")
    s.insert("rca_looks", {
        "look_id": look_id, "case_id": case_id, "seq": 1, "kind": "opening",
        "proposed_by": "opening_looks", "fork_json": None, "sql_or_helper_ref": "profile_column",
        "budget_counted": 0, "created_at": s.now_ist(),
    })
    execution_id = _id("exec")
    s.insert("rca_look_executions", {
        "execution_id": execution_id, "look_id": look_id, "status": "done",
        "summary_json": summary, "crashed": 0, "retried": 0, "executed_at": s.now_ist(),
    })
    transition(case_id, "investigation_loop", actor, evidence_ids=[look_id], tenant_id=tenant_id)
    return {"look_id": look_id, "execution_id": execution_id, "summary": summary}


# --- Planner / Runner / Reader — budgeted loop (Agents 3/4/5) ------------------
# WF §5: budget ~10 looks; stops on converged / battle-tested / budget-spent /
# dead-end; board cap 8 active suspects (9th needs a named kill target).

LOOK_BUDGET = 10
BOARD_CAP = 8
SECOND_CHANCE_BONUS_LOOKS = 5


def _case_planned_looks(case_id: str) -> list[dict]:
    return s.query("rca_looks", order_by="seq", case_id=case_id, kind="planned")


def _budget_spent(case_id: str) -> int:
    return sum(lk.get("budget_counted") or 0 for lk in _case_planned_looks(case_id))


def _second_chance_used(case_id: str) -> bool:
    return any(t["new_state"] == "second_chance_part_a"
              for t in s.query("rca_state_transitions", case_id=case_id))


def _effective_look_budget(case_id: str) -> int:
    """WF §6: the second-chance return gets ~5 fresh looks on top of the
    original budget."""
    return LOOK_BUDGET + (SECOND_CHANCE_BONUS_LOOKS if _second_chance_used(case_id) else 0)


def _active_suspects(case_id: str) -> list[dict]:
    return [x for x in s.query("rca_suspects", case_id=case_id) if x["status"] == "active"]


def _suspect_origin_look(suspect_id: str) -> dict | None:
    hist = s.query("rca_suspect_history", suspect_id=suspect_id, order_by="ts")
    if not hist:
        return None
    return s.query_one("rca_looks", look_id=hist[0]["look_id"])


def planner_propose_look(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT,
                         kill_target_suspect_id: str | None = None) -> dict:
    """Proposes exactly one look + a precommitted fork (WF Agent 3). Reads
    only the case file and quarantines history — this function never queries
    rca_hypotheses/closures from OTHER cases, only this case's own
    checklist, satisfying the Planner history-quarantine invariant.

    Explores one new segment column per call; once columns run out, attempts
    a kill-attempt look against an active suspect that hasn't been attacked
    yet (this is how 'battle-tested' becomes reachable). Returns
    {"dead_end": True} instead of creating a look when neither is available —
    the caller must not run/interpret anything in that case."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "investigation_loop":
        raise TransitionError(f"Planner cannot propose a look from state {case['state']!r}")
    if _budget_spent(case_id) >= _effective_look_budget(case_id):
        return {"dead_end": True, "reason": "budget already spent"}

    case_file = s.query_one("rca_case_files", case_id=case_id)
    checklist = case_file["checklist_json"]
    column = (checklist.get("columns") or [None])[0]
    schema = case_file["schema_snapshot_json"] or {}
    planned = _case_planned_looks(case_id)
    used_segments = {lk["fork_json"].get("segment_column") for lk in planned if lk.get("fork_json")}
    active = _active_suspects(case_id)

    if kill_target_suspect_id:
        target = next((sp for sp in active if sp["suspect_id"] == kill_target_suspect_id), None)
        if not target:
            raise RcaError(f"{kill_target_suspect_id!r} is not an active suspect on this case.")
        origin = _suspect_origin_look(kill_target_suspect_id)
        segment_column = (origin or {}).get("fork_json", {}).get("segment_column")
        kind = "kill_attempt"
    elif len(active) >= BOARD_CAP:
        raise RcaError(f"Board cap reached ({BOARD_CAP} active) — name a kill_target_suspect_id for the next look.")
    else:
        candidates = [c for c, cls in schema.items()
                     if cls in {"categorical", "ordinal", "binary"} and c != column and c not in used_segments]
        if candidates:
            segment_column, kind, target = candidates[0], "explore_column", None
        else:
            already_attacked = set()
            for lk in planned:
                fk = lk.get("fork_json") or {}
                if fk.get("kind") == "kill_attempt" and fk.get("target_suspect_id"):
                    already_attacked.add(fk["target_suspect_id"])
            target = next((sp for sp in active if sp["suspect_id"] not in already_attacked), None)
            if not target:
                return {"dead_end": True, "reason": "no unexplored columns and no un-attacked active suspects"}
            origin = _suspect_origin_look(target["suspect_id"])
            segment_column = (origin or {}).get("fork_json", {}).get("segment_column")
            kind = "kill_attempt"

    fork = {
        "check": "segment_breakdown", "kind": kind, "column": column, "segment_column": segment_column,
        "target_suspect_id": (target or {}).get("suspect_id"),
        "if_concentrated_in_one_segment": {"status": "active"},
        "if_uniform_across_segments": {"status": "ruled_out"},
        "concentration_threshold": 2.0,
    }
    look_id = _id("look")
    s.insert("rca_looks", {
        "look_id": look_id, "case_id": case_id, "seq": len(planned) + 2, "kind": "planned",
        "proposed_by": "planner", "fork_json": fork, "sql_or_helper_ref": "segment_breakdown",
        "budget_counted": 1, "created_at": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "look_execution", "rca_look", look_id, after={"kind": kind, "fork": fork})
    return {"look_id": look_id, "fork": fork}


def handle_dead_end(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """WF §5 dead-end stop: 'the Planner says no useful check exists.' Must
    be called by the caller whenever planner_propose_look returns
    {"dead_end": True} — that call creates no look, so nothing will ever
    invoke reader_interpret (the usual place a stop condition is evaluated)
    to move the case out of investigation_loop on its own."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "investigation_loop":
        raise TransitionError(f"Cannot declare dead-end from state {case['state']!r}")
    transition(case_id, "coverage_challenge_blind", actor, reason="dead_end", tenant_id=tenant_id)
    return require_case(case_id, tenant_id)


def runner_execute(look_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Executes the look read-only. Never interprets fork_json — the fork
    passes through untouched, per WF's 'code never interprets meaning'
    rule (only the "what to check" fields are read here; the outcome
    branches if_concentrated_in_one_segment/if_uniform_across_segments are
    read only by reader_interpret)."""
    look = s.query_one("rca_looks", look_id=look_id)
    if not look:
        raise KeyError("Unknown look")
    case = require_case(look["case_id"], tenant_id)
    frame = v2_service._read_table(case["item_id"], case["table_name"])
    fork = look["fork_json"] or {}
    if look["sql_or_helper_ref"] == "segment_breakdown" and fork.get("segment_column"):
        summary = _segment_breakdown(frame, fork["column"], fork["segment_column"])
    else:
        summary = {"found": False, "reason": "no eligible segment column for this table"}
    execution_id = _id("exec")
    s.insert("rca_look_executions", {
        "execution_id": execution_id, "look_id": look_id, "status": "done",
        "summary_json": summary, "crashed": 0, "retried": 0, "executed_at": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "look_execution", "rca_look_execution", execution_id,
          after={"look_id": look_id, "found": summary.get("found")})
    return {"execution_id": execution_id, "summary": summary}


def revive_suspect(suspect_id: str, reason: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Reader-controlled revival (WF §1.3): ruled-out is not forever. Only
    callable directly by the Reader role/flow, never automatically by the
    Planner (WF §4 Agent 3: 'cannot re-open a ruled-out suspect on its own
    — it must ask the Reader, with a reason')."""
    if not (reason or "").strip():
        raise ValueError("A reason is required to revive a ruled-out suspect.")
    suspect = s.query_one("rca_suspects", suspect_id=suspect_id)
    if not suspect:
        raise KeyError("Unknown suspect")
    case = require_case(suspect["case_id"], tenant_id)  # tenant gate — a suspect has no tenant_id of its own
    if suspect["status"] != "ruled_out":
        raise RcaError(f"Only a ruled_out suspect can be revived (current status: {suspect['status']!r}).")
    s.update("rca_suspects", {"suspect_id": suspect_id}, {"status": "revived", "updated_at": s.now_ist()})
    s.insert("rca_suspect_history", {
        "id": _id("sh"), "suspect_id": suspect_id, "prev_status": "ruled_out", "new_status": "revived",
        "reason": reason.strip(), "look_id": None, "ts": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "suspect_status_change", "rca_suspect", suspect_id,
          before={"status": "ruled_out"}, after={"status": "revived"}, reason=reason.strip())
    return s.query_one("rca_suspects", suspect_id=suspect_id)


def _create_pairwise_combination(case_id: str, member_suspect_ids: list[str], actor: str,
                                 tenant_id: str = DEFAULT_TENANT) -> dict:
    """Combinations are legal suspects (WF §5): pairs only, created only on
    their trigger (converged but failure size unexplained) — never
    speculatively mid-loop. Ruling out a member never rules out the
    combination (kills do not spread to combinations) — enforced simply by
    the combination being its own independent rca_suspects row."""
    if len(member_suspect_ids) != 2:
        raise ValueError("Combinations are pairs-only in this version.")
    suspect_id = _id("susp")
    s.insert("rca_suspects", {
        "suspect_id": suspect_id, "case_id": case_id, "kind": "pair",
        "member_cause_ids_json": member_suspect_ids, "status": "active",
        "origin": "combination_trigger", "created_at": s.now_ist(), "updated_at": s.now_ist(),
    })
    s.insert("rca_suspect_history", {
        "id": _id("sh"), "suspect_id": suspect_id, "prev_status": None, "new_status": "active",
        "reason": "symptom-size-unexplained trigger: two active suspects together may explain the full size",
        "look_id": None, "ts": s.now_ist(),
    })
    _audit(tenant_id, actor, "suspect_status_change", "rca_suspect", suspect_id,
          after={"status": "active", "members": member_suspect_ids}, reason="pairwise combination trigger")
    return s.query_one("rca_suspects", suspect_id=suspect_id)


def evaluate_stop_condition(case_id: str) -> dict:
    """WF §5: the loop stops when any ONE of converged / battle-tested /
    budget-spent / dead-end is true. Returns {"stop": bool, "reason": str|None}.
    Called after every reader_interpret (and independently inspectable)."""
    active = _active_suspects(case_id)
    planned = _case_planned_looks(case_id)

    if _budget_spent(case_id) >= _effective_look_budget(case_id):
        return {"stop": True, "reason": "budget_spent"}

    if active:
        battle_tested = all(
            any(h["suspect_id"] == sp["suspect_id"] and "survived kill-attempt" in (h.get("reason") or "")
               for h in s.query("rca_suspect_history", suspect_id=sp["suspect_id"]))
            for sp in active)
        if battle_tested:
            return {"stop": True, "reason": "battle_tested"}

    if len(active) <= 3 and len(planned) >= 2:
        last_two = planned[-2:]
        ruled_out_recently = any(
            h["new_status"] == "ruled_out"
            for lk in last_two
            for h in s.query("rca_suspect_history", look_id=lk["look_id"]))
        if not ruled_out_recently:
            return {"stop": True, "reason": "converged"}

    return {"stop": False, "reason": None}


def reader_interpret(execution_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """The sole writer of suspect status (WF Agent 5). Compares the result
    against the look's precommitted fork; for a kill_attempt look, survival
    (still concentrated) keeps the suspect active and records the
    kill-attempt-survived evidence battle_tested checks for; a miss rules it
    out. After interpreting, evaluates the stop condition and either loops
    back (stays in investigation_loop) or moves on to composition —
    triggering a pairwise combination first if convergence fires with
    exactly two independent active suspects (WF §5 symptom-size trigger)."""
    execution = s.query_one("rca_look_executions", execution_id=execution_id)
    if not execution:
        raise KeyError("Unknown look execution")
    look = s.query_one("rca_looks", look_id=execution["look_id"])
    case = require_case(look["case_id"], tenant_id)
    fork = look["fork_json"] or {}
    summary = execution["summary_json"] or {}
    kind = fork.get("kind", "explore_column")

    if not summary.get("found"):
        # "This result doesn't answer the question — that look costs no
        # budget and goes back for re-planning" (WF Agent 5). The look was
        # provisionally counted when the Planner proposed it; zero it out
        # now that the Reader has ruled it non-answering, and write no
        # suspect/status-change.
        s.update("rca_looks", {"look_id": look["look_id"]}, {"budget_counted": 0})
        return {"suspect": None, "stop": evaluate_stop_condition(case["case_id"])}

    worst_rate = summary.get("worst_rate", 0.0)
    best_rate = summary.get("best_rate", 0.0)
    concentrated = worst_rate >= max(best_rate, 0.01) * fork.get("concentration_threshold", 2.0)

    if kind == "kill_attempt":
        target_id = fork["target_suspect_id"]
        if concentrated:
            s.insert("rca_suspect_history", {
                "id": _id("sh"), "suspect_id": target_id, "prev_status": "active", "new_status": "active",
                "reason": f"survived kill-attempt look {look['look_id']}", "look_id": look["look_id"], "ts": s.now_ist(),
            })
        else:
            s.update("rca_suspects", {"suspect_id": target_id}, {"status": "ruled_out", "updated_at": s.now_ist()})
            s.insert("rca_suspect_history", {
                "id": _id("sh"), "suspect_id": target_id, "prev_status": "active", "new_status": "ruled_out",
                "reason": f"failed kill-attempt look {look['look_id']}", "look_id": look["look_id"], "ts": s.now_ist(),
            })
        suspect = s.query_one("rca_suspects", suspect_id=target_id)
        _audit(case["tenant_id"], actor, "suspect_status_change", "rca_suspect", target_id,
              after={"status": suspect["status"]}, reason=f"kill-attempt look {look['look_id']}")
    else:
        status = "active" if concentrated else "ruled_out"
        suspect_id = _id("susp")
        s.insert("rca_suspects", {
            "suspect_id": suspect_id, "case_id": case["case_id"], "kind": "single",
            "member_cause_ids_json": [f"segment_issue_in_{fork['segment_column']}"], "status": status,
            "origin": "look", "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
        s.insert("rca_suspect_history", {
            "id": _id("sh"), "suspect_id": suspect_id, "prev_status": None, "new_status": status,
            "reason": "reader interpretation of segment_breakdown vs precommitted fork",
            "look_id": look["look_id"], "ts": s.now_ist(),
        })
        suspect = s.query_one("rca_suspects", suspect_id=suspect_id)
        _audit(case["tenant_id"], actor, "suspect_status_change", "rca_suspect", suspect_id,
              after={"status": status}, reason=f"look {look['look_id']}")

    stop = evaluate_stop_condition(case["case_id"])
    if stop["stop"] and stop["reason"] == "converged":
        active = _active_suspects(case["case_id"])
        existing_combos = [sp for sp in s.query("rca_suspects", case_id=case["case_id"]) if sp["kind"] == "pair"]
        if len(active) == 2 and not existing_combos:
            _create_pairwise_combination(case["case_id"], [sp["suspect_id"] for sp in active], actor, case["tenant_id"])

    if stop["stop"]:
        transition(case["case_id"], "coverage_challenge_blind", actor, reason=stop["reason"],
                  evidence_ids=[look["look_id"]], tenant_id=case["tenant_id"])
    return {"suspect": suspect, "stop": stop}


# --- Coverage challenge (safety check before Composer) -------------------------
# WF §4: pass 1 is fully blind (raw evidence only, no board/history); pass 2
# may consult permitted KB case_history but can only nominate — a suspect
# enters the board only if this case's own raw evidence supports it too.

def coverage_challenge_pass1(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Blind pass: sees only raw look-execution evidence, never the suspect
    board or case history. Deterministic heuristic: any explored segment
    column whose worst-segment null rate is notable (>=0.15) but that never
    became an active suspect (e.g. it lost to a later kill-attempt, or the
    board cap blocked it) is flagged as a possibly-overlooked cause."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "coverage_challenge_blind":
        raise TransitionError(f"Coverage pass 1 cannot run from state {case['state']!r}")
    raw_evidence = []
    for lk in s.query("rca_looks", case_id=case_id):
        ex = s.query_one("rca_look_executions", look_id=lk["look_id"])
        if ex:
            raw_evidence.append({"look_id": lk["look_id"], "summary": ex["summary_json"]})
    look_ids_with_a_suspect = {
        h["look_id"] for sp in s.query("rca_suspects", case_id=case_id)
        for h in s.query("rca_suspect_history", suspect_id=sp["suspect_id"]) if h["look_id"]
    }
    overlooked = [e for e in raw_evidence
                 if (e["summary"] or {}).get("worst_rate", 0) >= 0.15
                 and e["look_id"] not in look_ids_with_a_suspect]
    s.insert("rca_coverage_passes", {
        "id": _id("cov"), "case_id": case_id, "pass": 1,
        "raw_evidence_ref_json": [e["look_id"] for e in raw_evidence],
        "history_nominations_json": [], "added_suspect_id": None, "ts": s.now_ist(),
    })
    transition(case_id, "coverage_challenge_history", actor, tenant_id=tenant_id)
    return {"raw_evidence_count": len(raw_evidence), "possibly_overlooked": [e["look_id"] for e in overlooked]}


def coverage_challenge_pass2(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """History-aware pass: may consult the KB's case_history category
    (through the same tenant/category-restricted retrieval as any other
    caller — WF's quarantine rule applies equally here) but can only
    nominate; a suspect is admitted only if it also survives a fresh
    kill-attempt look in the reopened loop, tagged origin='challenge_history'
    with no confidence credit for the tag itself (WF §4)."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "coverage_challenge_history":
        raise TransitionError(f"Coverage pass 2 cannot run from state {case['state']!r}")
    eligible = kb.list_eligible_rules(tenant_id, "coverage_challenge_pass2", case_id=case_id)
    nominations = [r["rule_id"] for r in eligible["rules"]]
    added_suspect_id = None
    if nominations and len(_active_suspects(case_id)) < BOARD_CAP:
        checklist = s.query_one("rca_case_files", case_id=case_id)["checklist_json"]
        suspect_id = _id("susp")
        s.insert("rca_suspects", {
            "suspect_id": suspect_id, "case_id": case_id, "kind": "single",
            "member_cause_ids_json": [f"history_nominated_{checklist.get('test_family', 'unknown')}"],
            "status": "active", "origin": "challenge_history",
            "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
        s.insert("rca_suspect_history", {
            "id": _id("sh"), "suspect_id": suspect_id, "prev_status": None, "new_status": "active",
            "reason": f"history-nominated by {len(nominations)} eligible case_history rule(s); no confidence credit for the nomination itself",
            "look_id": None, "ts": s.now_ist(),
        })
        added_suspect_id = suspect_id
        _audit(tenant_id, actor, "suspect_status_change", "rca_suspect", suspect_id,
              after={"status": "active", "origin": "challenge_history"}, reason="coverage pass 2 nomination")
    s.insert("rca_coverage_passes", {
        "id": _id("cov"), "case_id": case_id, "pass": 2,
        "raw_evidence_ref_json": [], "history_nominations_json": nominations,
        "added_suspect_id": added_suspect_id, "ts": s.now_ist(),
    })
    if added_suspect_id:
        transition(case_id, "reopened_kill_attempt", actor,
                  reason="history-nominated suspect must survive a kill-attempt before reaching the Composer",
                  tenant_id=tenant_id)
    else:
        transition(case_id, "hypothesis_composition", actor, tenant_id=tenant_id)
    return {"nominations": nominations, "added_suspect_id": added_suspect_id}


def run_reopened_kill_attempt(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """A history-nominated suspect (WF §4) must survive one kill-attempt
    look before it can reach the Composer — reuses the same
    Planner/Runner/Reader kill-attempt machinery, targeted at the nominated
    suspect specifically."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "reopened_kill_attempt":
        raise TransitionError(f"Reopened kill-attempt cannot run from state {case['state']!r}")
    nominated = next((sp for sp in s.query("rca_suspects", case_id=case_id, origin="challenge_history")
                      if sp["status"] == "active"), None)
    if not nominated:
        transition(case_id, "hypothesis_composition", actor, reason="no surviving history-nominated suspect", tenant_id=tenant_id)
        return {"survived": None}
    # Re-enter the loop briefly (contracts.md §3 allows investigation_loop <->
    # this state's neighbors conceptually; here we run the check inline using
    # the same primitives without a full state round-trip). These two direct
    # writes are a deliberate, narrow exception to "transition() is the only
    # writer of state" — they're an internal round-trip within one function
    # call, not a caller-visible transition, so they're not validated against
    # ALLOWED_NEXT or routed through transition(), but they are still audited
    # here so the state-change trail is complete either way.
    s.update("rca_cases", {"case_id": case_id}, {"state": "investigation_loop", "updated_at": s.now_ist()})
    _audit(case["tenant_id"], actor, "state_transition", "rca_case", case_id,
          before={"state": "reopened_kill_attempt"}, after={"state": "investigation_loop"},
          reason="internal round-trip to reuse Planner/Runner machinery for the reopened kill-attempt")
    proposed = planner_propose_look(case_id, actor, tenant_id, kill_target_suspect_id=nominated["suspect_id"])
    if proposed.get("dead_end"):
        s.update("rca_cases", {"case_id": case_id}, {"state": "reopened_kill_attempt", "updated_at": s.now_ist()})
        _audit(case["tenant_id"], actor, "state_transition", "rca_case", case_id,
              before={"state": "investigation_loop"}, after={"state": "reopened_kill_attempt"},
              reason="internal round-trip complete: no viable kill-attempt look available")
        transition(case_id, "hypothesis_composition", actor, reason="no viable kill-attempt look available", tenant_id=tenant_id)
        return {"survived": None}
    executed = runner_execute(proposed["look_id"], actor, tenant_id)
    look = s.query_one("rca_looks", look_id=proposed["look_id"])
    fork = look["fork_json"]
    worst_rate = executed["summary"].get("worst_rate", 0.0)
    best_rate = executed["summary"].get("best_rate", 0.0)
    survived = worst_rate >= max(best_rate, 0.01) * fork.get("concentration_threshold", 2.0)
    if survived:
        s.insert("rca_suspect_history", {
            "id": _id("sh"), "suspect_id": nominated["suspect_id"], "prev_status": "active", "new_status": "active",
            "reason": f"survived kill-attempt look {look['look_id']}", "look_id": look["look_id"], "ts": s.now_ist(),
        })
    else:
        s.update("rca_suspects", {"suspect_id": nominated["suspect_id"]}, {"status": "ruled_out", "updated_at": s.now_ist()})
        s.insert("rca_suspect_history", {
            "id": _id("sh"), "suspect_id": nominated["suspect_id"], "prev_status": "active", "new_status": "ruled_out",
            "reason": f"failed kill-attempt look {look['look_id']}", "look_id": look["look_id"], "ts": s.now_ist(),
        })
    _audit(case["tenant_id"], actor, "suspect_status_change", "rca_suspect", nominated["suspect_id"],
          after={"status": "active" if survived else "ruled_out"}, reason="reopened kill-attempt")
    s.update("rca_cases", {"case_id": case_id}, {"state": "reopened_kill_attempt", "updated_at": s.now_ist()})
    _audit(case["tenant_id"], actor, "state_transition", "rca_case", case_id,
          before={"state": "investigation_loop"}, after={"state": "reopened_kill_attempt"},
          reason="internal round-trip complete: reopened kill-attempt look executed")
    transition(case_id, "hypothesis_composition", actor, tenant_id=tenant_id)
    return {"survived": survived}


# --- Composer (Agent 6) --------------------------------------------------------

MAX_HYPOTHESES = 6


def _suspect_tier(suspect_id: str) -> str:
    """WF §4 confidence tiers, earned from the evidence trail only:
    Strong = survived >=1 kill-attempt AND has >=2 independent supporting
    looks; Moderate = survived a kill-attempt; Weak = cited evidence only."""
    history = s.query("rca_suspect_history", suspect_id=suspect_id)
    survived_kill = any("survived kill-attempt" in (h.get("reason") or "") for h in history)
    supporting_looks = {h["look_id"] for h in history if h.get("look_id") and h["new_status"] == "active"}
    if survived_kill and len(supporting_looks) >= 2:
        return "strong"
    if survived_kill:
        return "moderate"
    return "weak"


def compose_hypothesis(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """3-6 hypotheses (WF §4 Agent 6), one per active suspect (singles and
    pairwise combinations alike), each citing its own evidence with an
    earned tier and a rejection-capable confirm check. Reads only the final
    suspect board and evidence trail."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "hypothesis_composition":
        raise TransitionError(f"Composer cannot run from state {case['state']!r}")
    case_file = s.query_one("rca_case_files", case_id=case_id)
    checklist = case_file["checklist_json"]
    family = infer_cause_family(checklist.get("test_name"))
    # WF §7: four closing states, not three — "genuine_change" (the data
    # legitimately changed; not a defect to fix, not a bad test) needs its
    # own reachable path, not just confirmed/test_design_flaw. Stage 7
    # acceptance-gate finding: infer_cause_family's own "policy_change"
    # family (tokens: policy/cutoff/threshold) is the one family whose
    # plain-English meaning IS "the world changed", so it maps to
    # genuine_change rather than defect.
    if family == "policy_change":
        label = "genuine_change"
    elif family != "unknown":
        label = "defect"
    else:
        label = "test_design_flaw"
    active_suspects = _active_suspects(case_id)[:MAX_HYPOTHESES]

    created = []
    for suspect in active_suspects:
        evidence_look_ids = [h["look_id"] for h in s.query("rca_suspect_history", suspect_id=suspect["suspect_id"]) if h.get("look_id")]
        cause = ", ".join(suspect["member_cause_ids_json"] or [])
        statement = f"'{checklist.get('test_name')}' on {checklist.get('table_name')} is most consistent with {cause}"
        reject_condition = {"rule": "rerun the same check; REJECT if the concentration signal no longer exceeds the threshold"}
        confirm_check = {"check": "segment_breakdown", "suspect_id": suspect["suspect_id"], "reject_condition": reject_condition["rule"]}
        hypothesis_id = _id("hyp")
        s.insert("rca_hypotheses", {
            "hypothesis_id": hypothesis_id, "case_id": case_id, "suspect_id": suspect["suspect_id"],
            "statement": statement, "label": label, "tier": _suspect_tier(suspect["suspect_id"]),
            "evidence_look_ids_json": evidence_look_ids or [suspect["suspect_id"]],
            "confirm_check_json": confirm_check, "reject_condition_json": reject_condition,
            "owner": None, "created_at": s.now_ist(),
        })
        created.append(s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id))

    if not active_suspects:
        # A dead-end/budget-spent stop with nothing surviving still needs a
        # hypothesis of last resort (Weak, cited to the full evidence trail)
        # so the case has something to verify rather than stalling forever.
        looks = s.query("rca_looks", case_id=case_id)
        hypothesis_id = _id("hyp")
        s.insert("rca_hypotheses", {
            "hypothesis_id": hypothesis_id, "case_id": case_id, "suspect_id": None,
            "statement": f"'{checklist.get('test_name')}' on {checklist.get('table_name')} — no surviving suspect; weakly attributed to {family.replace('_', ' ')}",
            "label": label, "tier": "weak", "evidence_look_ids_json": [lk["look_id"] for lk in looks],
            "confirm_check_json": {"check": "segment_breakdown", "reject_condition": "rerun; REJECT if no signal reappears"},
            "reject_condition_json": {"rule": "rerun; REJECT if no signal reappears"},
            "owner": None, "created_at": s.now_ist(),
        })
        created.append(s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id))

    # Sort tier order strong -> moderate -> weak for the confirmation queue
    # (verification-ordering rule, completed in Stage 5; this establishes
    # the order_rank Stage 5's scheduler will read).
    tier_rank = {"strong": 0, "moderate": 1, "weak": 2}
    created.sort(key=lambda h: tier_rank.get(h["tier"], 3))

    _audit(tenant_id, actor, "agent_invocation", "rca_case", case_id,
          after={"hypotheses_created": [h["hypothesis_id"] for h in created]}, reason="Composer")
    transition(case_id, "ready_for_verification", actor, tenant_id=tenant_id)
    transition(case_id, "verification_planning", actor, tenant_id=tenant_id)
    transition(case_id, "confirmation_checks", actor, tenant_id=tenant_id)

    check_ids = []
    for i, hyp in enumerate(created):
        check_id = _id("chk")
        s.insert("rca_confirmation_checks", {
            "check_id": check_id, "hypothesis_id": hyp["hypothesis_id"], "order_rank": i + 1,
            "cost_hint": 1.0, "status": "pending", "executed_at": None,
        })
        check_ids.append(check_id)
    return {"hypotheses": created, "check_ids": check_ids,
           "hypothesis": created[0], "check_id": check_ids[0]}


# --- Check runner (Agent 7) + Judge (Agent 8) ----------------------------------

def _suspect_check_result(case: dict, suspect_id: str | None) -> dict:
    """Re-runs the concentration check for the specific suspect a hypothesis
    is about — a single suspect re-checks its own origin look's column; a
    pairwise combination requires BOTH members to still show the signal
    (kills don't spread to combinations, but a real re-confirm needs both
    legs to still hold)."""
    if not suspect_id:
        return {"found": False}
    suspect = s.query_one("rca_suspects", suspect_id=suspect_id)
    frame = v2_service._read_table(case["item_id"], case["table_name"])
    member_ids = suspect["member_cause_ids_json"] if suspect["kind"] == "pair" else [suspect_id]
    results = []
    for member_id in member_ids:
        origin = _suspect_origin_look(member_id) if suspect["kind"] == "pair" else _suspect_origin_look(suspect_id)
        fork = (origin or {}).get("fork_json") or {}
        if not fork.get("segment_column"):
            results.append({"found": False})
            continue
        results.append(_segment_breakdown(frame, fork["column"], fork["segment_column"]))
    if not results:
        return {"found": False}
    if suspect["kind"] == "pair":
        found_all = all(r.get("found") for r in results)
        worst_rate = min((r.get("worst_rate", 0.0) for r in results), default=0.0) if found_all else 0.0
        best_rate = max((r.get("best_rate", 0.0) for r in results), default=0.0) if found_all else 0.0
        # Conservative joint contribution: the smaller of the two members'
        # counts, since a jointly-explained row must show up in both legs.
        worst_count = min((r.get("worst_count", 0) for r in results), default=0) if found_all else 0
        return {"found": found_all, "worst_rate": worst_rate, "best_rate": best_rate,
               "worst_count": worst_count, "members": results}
    return results[0]


def _next_pending_check(case_id: str) -> dict | None:
    checks = []
    for h in s.query("rca_hypotheses", case_id=case_id):
        checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
    pending = [c for c in checks if c["status"] == "pending"]
    pending.sort(key=lambda c: c["order_rank"])
    return pending[0] if pending else None


def _judge_verdict(result: dict, refine: bool = False) -> str:
    """WF §11 Agent 8: confirmed / rejected / inconclusive, with one
    refinement allowed. The refined pass uses a single decisive cutoff
    instead of leaving a middle "inconclusive" band, so a refinement always
    resolves one way or the other (never inconclusive twice by construction —
    the caller still treats a still-ambiguous refined result as
    'unverified', never silently confirmed, matching WF's rule)."""
    if not result.get("found"):
        return "rejected"
    worst_rate = result.get("worst_rate", 0.0)
    best_rate = result.get("best_rate", 0.0)
    baseline = max(best_rate, 0.01)
    ratio = worst_rate / baseline
    if refine:
        return "confirmed" if ratio >= 1.5 else "rejected"
    if ratio >= 2.0:
        return "confirmed"
    if ratio < 1.2:
        return "rejected"
    return "inconclusive"


def record_symptom_accounting(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """WF §11 Agent 8: confirmed causes must together explain the observed
    size of the failure. total_symptom_size comes from the original issue's
    violation_count; explained_size sums the worst-segment row count of
    every CONFIRMED hypothesis's suspect (a real, if approximate, measure —
    not an invented score)."""
    case = require_case(case_id, tenant_id)
    case_file = s.query_one("rca_case_files", case_id=case_id)
    total = float((case_file["checklist_json"] or {}).get("violation_count") or 0)
    explained = 0.0
    for h in s.query("rca_hypotheses", case_id=case_id):
        checks = s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"])
        confirmed = any(jd["verdict"] == "confirmed"
                        for chk in checks
                        for jd in s.query("rca_judge_decisions", check_id=chk["check_id"]))
        if confirmed:
            result = _suspect_check_result(case, h.get("suspect_id"))
            explained += float(result.get("worst_count") or 0)
    if total:
        explained = min(explained, total)
    remaining = max(0.0, total - explained)
    s.insert("rca_symptom_accounting", {
        "id": _id("sym"), "case_id": case_id, "total_symptom_size": total,
        "explained_size": explained, "remaining_size": remaining, "computed_at": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "agent_invocation", "rca_case", case_id,
          after={"total_symptom_size": total, "explained_size": explained, "remaining_size": remaining},
          reason="symptom accounting")
    return {"total_symptom_size": total, "explained_size": explained, "remaining_size": remaining}


def _blame_back_history_nominations(case_id: str, tenant_id: str, reason: str) -> None:
    """WF §3a blame-back: a case_history KB rule that nominated a suspect
    (coverage_challenge_pass2) gets flagged under_suspicion when that
    nomination turns out wrong — a rejected Strong hypothesis or an
    Unresolved closure. Only fires for cases coverage pass 2 actually
    nominated something for; most cases never call this at all."""
    passes = [p for p in s.query("rca_coverage_passes", case_id=case_id) if p.get("pass") == 2]
    if not passes:
        return
    for rule_id in (passes[0].get("history_nominations_json") or []):
        try:
            kb.mark_under_suspicion(tenant_id, rule_id, reason)
        except KeyError:
            continue  # rule no longer exists / wrong tenant — nothing to blame


def run_confirmation_check(check_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Check runner + Judge (WF §11 Agents 7/8), completed for Part B:
    processes the tier-ordered queue Composer built (order_rank), handles
    inconclusive verdicts with exactly one refinement, performs symptom
    accounting on every confirmation, and continues verifying the remaining
    queue when the confirmed cause(s) only partly explain the failure size —
    only stopping at 'all queued checks exhausted, nothing confirmed' or
    'symptom size fully (or acceptably) explained'."""
    check = s.query_one("rca_confirmation_checks", check_id=check_id)
    if not check:
        raise KeyError("Unknown confirmation check")
    hypothesis = s.query_one("rca_hypotheses", hypothesis_id=check["hypothesis_id"])
    case = require_case(hypothesis["case_id"], tenant_id)
    if not hypothesis.get("reject_condition_json"):
        # Judge gatekeeper duty (WF §11 Agent 8): a check that cannot reject
        # must be sent back for redesign, never executed.
        raise RcaError("Confirmation check has no reject_condition — cannot run (gatekeeper duty).")

    prior = s.query("rca_judge_decisions", check_id=check_id, order_by="ts")
    is_refinement = bool(prior) and prior[-1]["verdict"] == "inconclusive" and not prior[-1]["refined"]

    result = _suspect_check_result(case, hypothesis.get("suspect_id"))
    s.update("rca_confirmation_checks", {"check_id": check_id}, {"status": "run", "executed_at": s.now_ist()})

    verdict = _judge_verdict(result, refine=is_refinement)
    if verdict == "inconclusive" and is_refinement:
        # Still ambiguous after the one allowed refinement: WF says this
        # counts as unverified and is flagged for a human, never silently
        # confirmed.
        verdict = "unverified"
    judge_decision_id = _id("jd")
    s.insert("rca_judge_decisions", {
        "id": judge_decision_id, "check_id": check_id, "verdict": verdict, "refined": 1 if is_refinement else 0,
        "reasoning": f"worst_rate={result.get('worst_rate')} best_rate={result.get('best_rate')} refine={is_refinement}",
        "ts": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "agent_invocation", "rca_judge_decision", judge_decision_id,
          after={"check_id": check_id, "verdict": verdict, "refined": is_refinement})
    transition(case["case_id"], "judging", actor, tenant_id=case["tenant_id"])

    if verdict == "rejected" and hypothesis.get("tier") == "strong":
        # WF §3a blame-back: a rejected Strong hypothesis is a real signal
        # that whatever case_history KB rule nominated this line of
        # investigation (if any) was wrong, not just an unlucky check.
        _blame_back_history_nominations(
            case["case_id"], case["tenant_id"],
            reason=f"Strong hypothesis {hypothesis['hypothesis_id']} rejected by confirmation check {check_id}")

    if verdict == "inconclusive":
        # Reset to 'pending' so the same check_id is picked up again by
        # _next_pending_check / the UI's pending-check picker for its one
        # allowed refinement run — otherwise it is stuck at status='run'
        # (set above) and becomes unreachable forever.
        s.update("rca_confirmation_checks", {"check_id": check_id}, {"status": "pending"})
        transition(case["case_id"], "confirmation_checks", actor,
                  reason="inconclusive verdict — one refinement allowed", tenant_id=case["tenant_id"])
        return {"verdict": verdict, "result": result, "needs_refinement": True}

    if verdict == "confirmed":
        accounting = record_symptom_accounting(case["case_id"], actor, case["tenant_id"])
        transition(case["case_id"], "symptom_accounting", actor, tenant_id=case["tenant_id"])
        next_check = _next_pending_check(case["case_id"])
        if accounting["remaining_size"] <= 0 or not next_check:
            transition(case["case_id"], "awaiting_fix_approval", actor,
                      reason="symptom size fully explained" if accounting["remaining_size"] <= 0
                      else "tier-ordered queue exhausted; proceeding on the evidence confirmed so far",
                      tenant_id=case["tenant_id"])
            return {"verdict": verdict, "result": result, "accounting": accounting}
        transition(case["case_id"], "confirmation_checks", actor,
                  reason="symptom size only partly explained — continuing verification of the remaining queue",
                  tenant_id=case["tenant_id"])
        return {"verdict": verdict, "result": result, "accounting": accounting, "next_check_id": next_check["check_id"]}

    # rejected or unverified — try the next queued hypothesis, tier order first.
    next_check = _next_pending_check(case["case_id"])
    if next_check:
        transition(case["case_id"], "confirmation_checks", actor,
                  reason=f"{verdict} — trying the next tier-ordered queued hypothesis", tenant_id=case["tenant_id"])
        return {"verdict": verdict, "result": result, "next_check_id": next_check["check_id"]}
    transition(case["case_id"], "all_hypotheses_rejected", actor,
              reason="every queued confirmation check is exhausted with no confirmation", tenant_id=case["tenant_id"])
    return {"verdict": verdict, "result": result}


# --- Second chance / escalation (WF §6, §12) -----------------------------------

def start_second_chance(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """WF §6: 'the downstream investigation disproves ALL hypotheses. Case
    returns once, with the disproof added, and ~5 fresh looks.' A second
    exhaustion (this already having been used once) escalates straight to
    Unresolved instead — 'second chance occurs no more than once.'"""
    case = require_case(case_id, tenant_id)
    if case["state"] != "all_hypotheses_rejected":
        raise TransitionError(f"Cannot start a second chance from state {case['state']!r}")
    if _second_chance_used(case_id):
        transition(case_id, "escalated", actor, reason="second chance already used once", tenant_id=tenant_id)
        transition(case_id, "unresolved", actor, tenant_id=tenant_id)
        # WF §3a blame-back: an Unresolved outcome after both investigation
        # rounds is a real signal against any case_history KB rule that
        # nominated a suspect here.
        _blame_back_history_nominations(case_id, case["tenant_id"], reason=f"case {case_id} closed unresolved")
        s.insert("rca_closures", {
            "case_id": case_id, "outcome": "unresolved", "rerun_run_id": None,
            "frozen_snapshot": 1, "fresh_snapshot_warning": None,
            "knowledge_draft_id": None, "closed_at": s.now_ist(),
        })
        _audit(case["tenant_id"], actor, "closure", "rca_case", case_id, after={"outcome": "unresolved"})
        return {"case": require_case(case_id, tenant_id), "second_chance_granted": False, "outcome": "unresolved"}
    transition(case_id, "second_chance_part_a", actor,
              reason="all hypotheses rejected — one-time return to Part A with the disproof evidence",
              tenant_id=tenant_id)
    transition(case_id, "investigation_loop", actor,
              reason=f"~{SECOND_CHANCE_BONUS_LOOKS} fresh looks with the second-chance budget bonus",
              tenant_id=tenant_id)
    return {"case": require_case(case_id, tenant_id), "second_chance_granted": True}


# --- Fix advisor (Agent 9) + human approval ------------------------------------

def propose_fix(hypothesis_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    hypothesis = s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id)
    if not hypothesis:
        raise KeyError("Unknown hypothesis")
    case = require_case(hypothesis["case_id"], tenant_id)  # tenant gate — a hypothesis has no tenant_id of its own
    family = infer_cause_family(hypothesis["statement"])
    fix_type = FIX_BY_CAUSE.get(family, "source_feed_fix")
    proposal_id = _id("fix")
    s.insert("rca_fix_proposals", {
        "id": proposal_id, "hypothesis_id": hypothesis_id,
        "options_json": [{"fix_type": fix_type, "text": f"Apply a {fix_type.replace('_', ' ')}."}],
        "routed_to": hypothesis["label"], "label": hypothesis["label"], "created_at": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "fix_proposal", "rca_fix_proposal", proposal_id,
          after={"hypothesis_id": hypothesis_id, "fix_type": fix_type})
    return s.query_one("rca_fix_proposals", id=proposal_id)


TIME_BOX_DAYS = 5  # matches the legacy rca_remediation_plans.sla default ("5 business days")


def approve_fix(fix_proposal_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Human approval rule (WF §11 Agent 9): fixes are never applied
    automatically. A human approves; the system only tracks status. Sets a
    time-box deadline (WF §11 Agent 10: 'a case waiting on an owner's fix
    beyond its time-box is escalated, not left open forever')."""
    proposal = s.query_one("rca_fix_proposals", id=fix_proposal_id)
    if not proposal:
        raise KeyError("Unknown fix proposal")
    hypothesis = s.query_one("rca_hypotheses", hypothesis_id=proposal["hypothesis_id"])
    case = require_case(hypothesis["case_id"], tenant_id)
    from datetime import datetime, timedelta
    deadline = (datetime.fromisoformat(s.now_ist()) + timedelta(days=TIME_BOX_DAYS)).isoformat()
    approval_id = _id("appr")
    s.insert("rca_fix_approvals", {
        "id": approval_id, "fix_proposal_id": fix_proposal_id,
        "approved_by": actor, "approved_at": s.now_ist(),
        "applied_confirmed_by": None, "applied_confirmed_at": None,
        "time_box_deadline": deadline, "escalated_at": None,
    })
    transition(case["case_id"], "awaiting_fix_application", actor, tenant_id=case["tenant_id"])
    _audit(case["tenant_id"], actor, "fix_approval", "rca_fix_proposals", fix_proposal_id)
    return s.query_one("rca_fix_approvals", id=approval_id)


def check_time_box_escalations(tenant_id: str = DEFAULT_TENANT, actor: str = "system") -> list[dict]:
    """Sweep for approved-but-not-yet-applied fixes past their time-box
    deadline; marks escalated_at and writes an audit event. Never applies
    the fix itself or moves the case out of awaiting_fix_application — the
    owner must still act (or a human re-routes ownership); this only makes
    the overdue state visible."""
    now = s.now_ist()
    escalated = []
    for approval in s.query("rca_fix_approvals"):
        if approval.get("applied_confirmed_at") or approval.get("escalated_at"):
            continue
        deadline = approval.get("time_box_deadline")
        if not deadline or deadline > now:
            continue
        proposal = s.query_one("rca_fix_proposals", id=approval["fix_proposal_id"])
        hypothesis = s.query_one("rca_hypotheses", hypothesis_id=proposal["hypothesis_id"]) if proposal else None
        case = s.query_one("rca_cases", case_id=hypothesis["case_id"]) if hypothesis else None
        if case and case["tenant_id"] != tenant_id:
            # A real case exists but belongs to a DIFFERENT tenant — leave
            # escalated_at untouched so a later sweep for the OWNING tenant
            # still catches it. Marking it here would silently swallow the
            # escalation with no audit event, since the guard above skips
            # anything already marked escalated.
            continue
        # No case at all (orphaned/malformed approval) still gets marked —
        # the deadline itself is real and shouldn't be silently un-escalated
        # forever just because enrichment failed; there's just no case to
        # attribute an audit event to.
        s.update("rca_fix_approvals", {"id": approval["id"]}, {"escalated_at": now})
        if case:
            _audit(tenant_id, actor, "fix_time_box_escalation", "rca_fix_approvals", approval["id"],
                  reason=f"no applied-fix confirmation by {deadline}")
        escalated.append(s.query_one("rca_fix_approvals", id=approval["id"]))
    return escalated


def sweep_all_tenants_time_box_escalations(actor: str = "system") -> list[dict]:
    """Runs check_time_box_escalations() once per known tenant — the sole
    entry point main.py's periodic background thread calls. A case waiting
    on an owner's fix beyond its time-box must actually get escalated in a
    running deployment, not just be escalatable in a test (WF §11 Agent 10)."""
    escalated = []
    for tenant in s.query("tenants"):
        escalated.extend(check_time_box_escalations(tenant["tenant_id"], actor))
    return escalated


def confirm_fix_applied(fix_approval_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Human confirms an owner applied the fix (production fixes are
    simulated in this product — never applied automatically)."""
    approval = s.query_one("rca_fix_approvals", id=fix_approval_id)
    if not approval:
        raise KeyError("Unknown fix approval")
    proposal = s.query_one("rca_fix_proposals", id=approval["fix_proposal_id"])
    hypothesis = s.query_one("rca_hypotheses", hypothesis_id=proposal["hypothesis_id"])
    case = require_case(hypothesis["case_id"], tenant_id)
    s.update("rca_fix_approvals", {"id": fix_approval_id},
            {"applied_confirmed_by": actor, "applied_confirmed_at": s.now_ist()})
    transition(case["case_id"], "closure_rerun", actor, tenant_id=case["tenant_id"])
    _audit(case["tenant_id"], actor, "fix_applied", "rca_fix_approvals", fix_approval_id)
    return s.query_one("rca_fix_approvals", id=fix_approval_id)


# --- Closure (Agent 10) --------------------------------------------------------

def _rerun_original_test(case: dict) -> dict:
    """Real closure rerun: re-executes the original plan_v2 row's snippet
    read-only against the currently stored data (frozen_snapshot=1 — this
    slice never re-uploads/re-profiles data mid-case, so the snapshot is
    implicitly frozen). No production fix is ever applied automatically —
    'fix applied' is a human-confirmed status only (contracts.md §0
    decisions), so this rerun reproduces the ORIGINAL result unless the
    caller substitutes a fake rerun_fn (contracts.md §0 decision: 'human-
    approved fixes are simulated in automated tests')."""
    from ai import code_sandbox
    issue = s.query_one("issues_v2", issue_row_id=case["issue_row_id"])
    # Raw SQL, not s.query(): plan_v2's own "table_name" column collides with
    # query()'s first positional parameter of the same name (the existing
    # codebase's ai/v2/service.py:get_plan hits the same footgun and uses
    # raw SQL for exactly this reason).
    plan_rows = s.execute(
        "SELECT * FROM plan_v2 WHERE item_id=? AND table_name=? AND test_name=? AND scope=?",
        [case["item_id"], case["table_name"], issue["test_name"], "framework"])
    if not plan_rows:
        return {"status": "not_runnable", "reason": "original plan row not found"}
    row = plan_rows[0]
    frame = v2_service._read_table(case["item_id"], case["table_name"])
    inv = v2_service._inventory_map(case["item_id"], case["table_name"])
    item = v2_service.require_item(case["item_id"])
    support = v2_service._supporting(frame, item, case["table_name"])
    code = row.get("snippet_code")
    if not code:
        return {"status": "not_runnable", "reason": "no snippet code on the original plan row"}
    run = code_sandbox.run(code, frame, {"supporting": support, "classification": inv})
    result = run.get("result_obj") if run.get("ok") else None
    if isinstance(result, list):
        result = result[0] if result else None
    return result or {"status": "not_runnable", "reason": run.get("error") or "no result"}


def _confirmed_hypothesis(case_id: str) -> dict | None:
    """With multiple hypotheses (Stage 4+), plain s.query_one() would grab an
    arbitrary one — closure must use the one an earlier run_confirmation_check
    call actually confirmed, preferring the highest tier if more than one was
    (Strong verified before Moderate before Weak, per WF's ordering rule)."""
    tier_rank = {"strong": 0, "moderate": 1, "weak": 2}
    confirmed = []
    for h in s.query("rca_hypotheses", case_id=case_id):
        checks = s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"])
        if any(jd["verdict"] == "confirmed" for chk in checks
              for jd in s.query("rca_judge_decisions", check_id=chk["check_id"])):
            confirmed.append(h)
    confirmed.sort(key=lambda h: tier_rank.get(h["tier"], 3))
    return confirmed[0] if confirmed else None


def reconcile_attached_failures(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> list[dict]:
    """WF §11 Agent 10: every attached 'probably same cause' failure
    (Triage's two-signal matches, contracts.md §0) is checked against the
    confirmed cause at closure; mismatches open their own case. Reconciled
    here at closure time as a batch — Triage's own damage cap already
    limits the blast radius of a wrong grouping (attachments are never
    investigated themselves before this point)."""
    case = require_case(case_id, tenant_id)
    group = s.query_one("rca_failure_groups", case_id=case_id)
    if not group:
        return []
    hypothesis = _confirmed_hypothesis(case_id)
    results = []
    for attached in s.query("rca_attached_failures", group_id=group["group_id"]):
        if attached.get("reconciliation_status") != "pending":
            continue
        # Simplification: Triage already required the same inferred test
        # family to attach; without finer-grained per-failure re-diagnosis,
        # "matches" is the honest default whenever a real cause was
        # confirmed. This is a coarser check than a full per-attachment
        # re-investigation would give — documented in the traceability
        # matrix rather than silently assumed complete.
        status = "matches" if hypothesis else "mismatch_needs_own_case"
        s.update("rca_attached_failures", {"id": attached["id"]},
                {"reconciliation_status": status, "reconciled_at": s.now_ist()})
        results.append(s.query_one("rca_attached_failures", id=attached["id"]))
        _audit(case["tenant_id"], actor, "agent_invocation", "rca_attached_failure", attached["id"],
              after={"reconciliation_status": status})
    return results


def close_case(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT, rerun_fn=None) -> dict:
    case = require_case(case_id, tenant_id)
    rerun_fn = rerun_fn or _rerun_original_test
    result = rerun_fn(case)
    passed = result.get("status") == "pass"

    hypothesis = _confirmed_hypothesis(case_id)
    if passed:
        outcome = {"defect": "confirmed", "test_design_flaw": "test_design_flaw",
                  "genuine_change": "genuine_change"}.get((hypothesis or {}).get("label"), "confirmed")
    else:
        # Honest scope limit: a simulated fix never actually mutates data, so
        # a rerun without a real caller-supplied rerun_fn will reproduce the
        # original failure. Stage 3 records this truthfully rather than
        # fabricating a Confirmed outcome — it does not auto-close as
        # Unresolved either (that requires the exhausted second-chance loop,
        # which is Stage 5's job). The case is left at closure_rerun with the
        # honest result on record for a human to see.
        s.insert("rca_audit_events", {
            "event_id": _id("aud"), "tenant_id": tenant_id, "actor": actor, "event_type": "closure",
            "object_type": "rca_case", "object_id": case_id,
            "before_json": {}, "after_json": {"rerun_result": result},
            "reason": "closure rerun still fails — no real fix was applied (simulated-fix scope limit)",
            "ts": s.now_ist(),
        })
        return {"case": case, "closed": False, "rerun_result": result}

    transition(case_id, "reconciliation", actor, evidence_ids=[], tenant_id=tenant_id)
    reconcile_attached_failures(case_id, actor, tenant_id)
    transition(case_id, "closed", actor, tenant_id=tenant_id)

    closure_id = case_id
    s.insert("rca_closures", {
        "case_id": closure_id, "outcome": outcome, "rerun_run_id": None,
        "frozen_snapshot": 1, "fresh_snapshot_warning": None,
        "knowledge_draft_id": None, "closed_at": s.now_ist(),
    })
    _audit(tenant_id, actor, "closure", "rca_case", case_id, after={"outcome": outcome})
    return {"case": require_case(case_id, tenant_id), "closed": True, "outcome": outcome,
            "draft_rule": None}
