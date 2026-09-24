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

The workflow retains deterministic state transitions and analysis execution.
Initial Review now adds a governed live-model interpretation after the static
opening evidence; automated tests disable or fake that boundary so validation
never creates billable calls. Later investigation stages remain deterministic
until their agent-driven slices are migrated deliberately.

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
from ai.v2 import issues as issues_service
from ai import sandbox_capabilities
from ai.v2 import service as v2_service
from domains.aar.repository import AnalysisArtifactRepository
from domains.rca import data_chat, feature_states, investigation_agent, investigation_runtime
from domains.rca import evidence as rca_evidence
from domains.rca import initial_review_evidence
from domains.rca import progress


@progress.phase("Loading dataset")
def _read_analysis_table(*args, **kwargs):
    return v2_service._read_table(*args, **kwargs)

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


def _diagnostic_analysis_table(issue: dict) -> str:
    """Resolve the dataset table behind a diagnostic feature-level issue."""
    run_id = issue.get("run_id")
    if not run_id and issue.get("finding_id"):
        finding = s.query_one("diag_findings", finding_id=issue["finding_id"])
        run_id = (finding or {}).get("run_id")
    if run_id:
        run = s.query_one("diag_runs", run_id=run_id)
        manifest = (run or {}).get("manifest_json") or {}
        if manifest.get("table"):
            return str(manifest["table"])
    return str(issue.get("table_name") or "")


def _case_analysis_table(case: dict) -> str:
    issue = s.query_one("issues_v2", issue_row_id=case["issue_row_id"]) or {}
    return _diagnostic_analysis_table(issue) or str(case.get("table_name") or "")


def _feature_state_snapshot(item_id: str, table: str) -> dict:
    return feature_states.snapshot_from_inventory(v2_service.get_inventory(item_id, table))


INITIAL_REVIEW_HYPOTHESIS_ORIGIN = "llm_initial_review"
DRIVER_SEARCH_HYPOTHESIS_ORIGIN = "driver_search"
COMPOSER_HYPOTHESIS_ORIGIN = "composer"
ALTERNATIVE_HYPOTHESIS_ORIGIN = "alternative_explanation"


def _active_investigation_hypothesis(case_id: str) -> dict | None:
    for origin in (ALTERNATIVE_HYPOTHESIS_ORIGIN, DRIVER_SEARCH_HYPOTHESIS_ORIGIN,
                   INITIAL_REVIEW_HYPOTHESIS_ORIGIN):
        selected = next((row for row in _case_hypotheses(case_id, origin=origin)
                         if row.get("lifecycle_status") == "selected"), None)
        if selected:
            return selected
    return None


def _case_hypotheses(case_id: str, *, origin: str = COMPOSER_HYPOTHESIS_ORIGIN,
                     conn=None) -> list[dict]:
    return s.query(
        "rca_hypotheses", conn=conn, order_by="created_at",
        case_id=case_id, origin=origin,
    )


class RcaError(Exception):
    pass


class RcaAgentUnavailable(RcaError):
    pass


class TransitionError(RcaError):
    pass


# --- State machine (contracts.md §3, verbatim) --------------------------------

ALLOWED_NEXT: dict[str, set[str]] = {
    "created": {"triage"},
    "triage": {"intake"},
    "intake": {"opening_looks"},
    "opening_looks": {"initial_review_complete"},
    "initial_review_complete": {"investigation_loop"},
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
    "intake", "opening_looks", "initial_review_complete", "investigation_loop", "awaiting_human_answer",
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


def _ensure_case_context(case: dict) -> str:
    """Publish (or reuse) the immutable Intake evidence for this generation."""
    from ai.v2 import issues as issues_service

    case_file = s.query_one("rca_case_files", case_id=case["case_id"])
    if not case_file:
        raise RcaError("RCA case intake is not complete")
    issue = issues_service.get_issue(case["issue_row_id"])
    item = v2_service.require_item(case["item_id"])
    return rca_evidence.ensure_case_context(case, case_file, issue, item)


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
        if existing["state"] in {"created", "triage"}:
            import time
            for _ in range(20):
                time.sleep(0.05)
                existing = s.query_one("rca_cases", case_id=existing["case_id"], tenant_id=tenant_id)
                if existing["state"] not in {"created", "triage"}:
                    break
        if existing["state"] not in {"created", "triage"} and s.query_one(
            "rca_case_files", case_id=existing["case_id"]
        ):
            _ensure_case_context(existing)
        return existing
    issue = s.query_one("issues_v2", issue_row_id=issue_row_id)
    if not issue:
        raise KeyError(f"Unknown issue row: {issue_row_id}")
    item = v2_service.require_item(issue["item_id"])
    family = _infer_test_family(issue["test_name"])
    analysis_table = _diagnostic_analysis_table(issue)

    matching_group = find_matching_open_group(
        tenant_id, issue["item_id"], issue["table_name"], issue["test_name"]
    )
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
        # Diagnostic findings may use table_name for their governed entity
        # (for PSI, the feature). Keep that identity for issue grouping; row-
        # level execution resolves the physical table from the run manifest.
        "item_id": issue["item_id"], "table_name": issue["table_name"],
        "state": "created", "part": "A", "tag_snapshot_json": tag_snapshot,
        "complaint_text": None, "created_by": actor, "created_at": now, "updated_at": now,
        "closed_at": None, "contract_version": "1", "workflow_generation": 1,
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
        "test_name": issue["test_name"], "test_family": family, "table_name": analysis_table,
        "columns": issue.get("columns_json") or [], "metric": issue.get("metric"),
        "threshold": issue.get("threshold_json"), "violation_count": issue.get("violation_count"),
        "target_variable": item.get("target_variable"), "use_case": item.get("use_case"),
        "feature_state_snapshot": _feature_state_snapshot(issue["item_id"], analysis_table),
    }
    s.insert("rca_case_files", {
        "case_id": case_id, "checklist_json": checklist,
        "schema_snapshot_json": v2_service._inventory_map(issue["item_id"], analysis_table),
        "tags_snapshot_json": tag_snapshot, "complaint_json": None, "created_at": now,
    })
    case = require_case(case_id, tenant_id)
    _ensure_case_context(case)
    return case


def start_afresh(case_id: str, actor: str, confirmed: bool,
                  tenant_id: str = DEFAULT_TENANT) -> dict:
    """Discard derived RCA work and return the existing case to blank Intake.

    The source issue, immutable intake file, failure-group membership, and case
    identity remain. A monotonically increasing generation fences off late
    results from work started before the reset. The only retained history of
    the discarded work is the required reset marker itself.
    """
    if not confirmed:
        raise RcaError("Starting afresh requires explicit confirmation.")
    case = require_case(case_id, tenant_id)
    if case["state"] == "closed" or s.query_one("rca_closures", case_id=case_id):
        raise RcaError("A closed RCA cannot be reset. Reopen it before starting afresh.")

    now = s.now_ist()
    previous_generation = int(case.get("workflow_generation") or 1)
    case_file = s.query_one("rca_case_files", case_id=case_id) or {}
    refreshed_checklist = dict(case_file.get("checklist_json") or {})
    analysis_table = _case_analysis_table(case)
    refreshed_checklist["feature_state_snapshot"] = _feature_state_snapshot(
        case["item_id"], analysis_table
    )
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        looks = s.query("rca_looks", conn=conn, case_id=case_id)
        look_ids = [row["look_id"] for row in looks]
        executions = [dict(row) for row in conn.execute(
            "SELECT execution.* FROM rca_look_executions execution "
            "JOIN rca_looks look ON look.look_id = execution.look_id WHERE look.case_id = ?",
            (case_id,),
        ).fetchall()]
        suspects = s.query("rca_suspects", conn=conn, case_id=case_id)
        suspect_ids = [row["suspect_id"] for row in suspects]
        hypotheses = s.query("rca_hypotheses", conn=conn, case_id=case_id)
        hypothesis_ids = [row["hypothesis_id"] for row in hypotheses]
        checks = [dict(row) for row in conn.execute(
            "SELECT check_row.* FROM rca_confirmation_checks check_row "
            "JOIN rca_hypotheses hypothesis ON hypothesis.hypothesis_id = check_row.hypothesis_id "
            "WHERE hypothesis.case_id = ?", (case_id,),
        ).fetchall()]
        check_ids = [row["check_id"] for row in checks]
        judges = [dict(row) for row in conn.execute(
            "SELECT decision.* FROM rca_judge_decisions decision "
            "JOIN rca_confirmation_checks check_row ON check_row.check_id = decision.check_id "
            "JOIN rca_hypotheses hypothesis ON hypothesis.hypothesis_id = check_row.hypothesis_id "
            "WHERE hypothesis.case_id = ?", (case_id,),
        ).fetchall()]
        proposals = [dict(row) for row in conn.execute(
            "SELECT proposal.* FROM rca_fix_proposals proposal "
            "JOIN rca_hypotheses hypothesis ON hypothesis.hypothesis_id = proposal.hypothesis_id "
            "WHERE hypothesis.case_id = ?", (case_id,),
        ).fetchall()]
        proposal_ids = [row["id"] for row in proposals]
        approvals = [dict(row) for row in conn.execute(
            "SELECT approval.* FROM rca_fix_approvals approval "
            "JOIN rca_fix_proposals proposal ON proposal.id = approval.fix_proposal_id "
            "JOIN rca_hypotheses hypothesis ON hypothesis.hypothesis_id = proposal.hypothesis_id "
            "WHERE hypothesis.case_id = ?", (case_id,),
        ).fetchall()]
        questions = s.query("rca_human_questions", conn=conn, case_id=case_id)
        coverage = s.query("rca_coverage_passes", conn=conn, case_id=case_id)
        accounting = s.query("rca_symptom_accounting", conn=conn, case_id=case_id)

        def delete_ids(table: str, column: str, values: list[str]) -> int:
            if not values:
                return 0
            placeholders = ",".join("?" for _ in values)
            return conn.execute(
                f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})', values
            ).rowcount

        discarded = {
            "fix_approvals": delete_ids("rca_fix_approvals", "id", [row["id"] for row in approvals]),
            "fix_proposals": delete_ids("rca_fix_proposals", "id", proposal_ids),
            "judge_decisions": delete_ids("rca_judge_decisions", "id", [row["id"] for row in judges]),
            "confirmation_checks": delete_ids("rca_confirmation_checks", "check_id", check_ids),
            "hypotheses": delete_ids("rca_hypotheses", "hypothesis_id", hypothesis_ids),
            "suspect_history": delete_ids("rca_suspect_history", "suspect_id", suspect_ids),
            "suspects": delete_ids("rca_suspects", "suspect_id", suspect_ids),
            "look_executions": delete_ids("rca_look_executions", "execution_id", [row["execution_id"] for row in executions]),
            "looks": delete_ids("rca_looks", "look_id", look_ids),
            "human_questions": delete_ids("rca_human_questions", "id", [row["id"] for row in questions]),
            "coverage_passes": delete_ids("rca_coverage_passes", "id", [row["id"] for row in coverage]),
            "symptom_accounting": delete_ids("rca_symptom_accounting", "id", [row["id"] for row in accounting]),
        }

        related_object_ids = {
            case_id, *look_ids, *[row["execution_id"] for row in executions], *suspect_ids,
            *hypothesis_ids, *check_ids, *[row["id"] for row in judges], *proposal_ids,
            *[row["id"] for row in approvals],
        }
        delete_ids("rca_audit_events", "object_id", list(related_object_ids))
        conn.execute('DELETE FROM rca_state_transitions WHERE case_id = ?', (case_id,))
        conn.execute('DELETE FROM rca_closures WHERE case_id = ?', (case_id,))
        s.update("rca_case_files", {"case_id": case_id}, {
            "complaint_json": None, "checklist_json": refreshed_checklist,
            "schema_snapshot_json": v2_service._inventory_map(case["item_id"], analysis_table),
        }, conn=conn)
        s.update("rca_cases", {"case_id": case_id}, {
            "state": "intake", "part": "A", "complaint_text": None,
            "updated_at": now, "closed_at": None,
            "workflow_generation": previous_generation + 1,
        }, conn=conn)
        s.insert("rca_state_transitions", {
            "id": _id("trs"), "case_id": case_id, "prev_state": case["state"],
            "new_state": "intake", "actor": actor, "reason": "start afresh",
            "evidence_ids_json": [], "ts": now,
            "workflow_version": "rca", "contract_version": "1",
        }, conn=conn)
        s.insert("rca_audit_events", {
            "event_id": _id("aud"), "tenant_id": tenant_id, "actor": actor,
            "event_type": "rca_reset", "object_type": "rca_case", "object_id": case_id,
            "before_json": {"state": case["state"], "workflow_generation": previous_generation},
            "after_json": {"state": "intake", "workflow_generation": previous_generation + 1,
                           "discarded": discarded},
            "reason": "User confirmed permanent loss of current RCA details.", "ts": now,
        }, conn=conn)
        conn.commit()
    # The user chose destructive restart: prior RCA-owned AAR payloads are
    # removed, then the retained source issue is pinned into a fresh immutable
    # generation with one reset decision marker. Diagnostic source artifacts
    # are lineage inputs and are never deleted here.
    rca_evidence.discard_case_evidence(case_id)
    refreshed = require_case(case_id, tenant_id)
    context_id = _ensure_case_context(refreshed)
    rca_evidence.record_event(
        refreshed, evidence_kind="workflow_reset", stage="system", status="completed",
        actor=actor, source_artifact_ids=(context_id,), details={
            "previous_state": case["state"],
            "previous_workflow_generation": previous_generation,
            "workflow_generation": previous_generation + 1,
            "discarded": discarded,
            "decision": "User confirmed permanent loss of current RCA details.",
        },
    )
    return get_case(case_id, tenant_id)


def get_case(case_id: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    case_file = s.query_one("rca_case_files", case_id=case_id)
    looks = s.query("rca_looks", order_by="seq", case_id=case_id)
    look_ids = [look["look_id"] for look in looks]
    look_id_set = set(look_ids)
    execution_rows = [row for row in s.query("rca_look_executions")
                      if row["look_id"] in look_id_set]
    executions = {row["look_id"]: row for row in execution_rows}
    suspects = s.query("rca_suspects", order_by="created_at", case_id=case_id)
    investigation_context = s.query(
        "rca_human_questions", order_by="created_at", case_id=case_id
    )
    hypothesis_candidates = _case_hypotheses(
        case_id, origin=INITIAL_REVIEW_HYPOTHESIS_ORIGIN
    )
    focused_hypothesis_candidates = _case_hypotheses(
        case_id, origin=DRIVER_SEARCH_HYPOTHESIS_ORIGIN
    )
    hypotheses = _case_hypotheses(case_id)
    hypothesis_ids = [hypothesis["hypothesis_id"] for hypothesis in hypotheses]
    hypothesis_id_set = set(hypothesis_ids)
    checks = [row for row in s.query("rca_confirmation_checks")
              if row["hypothesis_id"] in hypothesis_id_set]
    checks.sort(key=lambda c: c["order_rank"])
    # Judge verdicts per check, and the case's confirmed hypothesis (if any)
    # — the UI needs both to route the fix-approval flow to the RIGHT
    # hypothesis now that Stage 4/5 can produce more than one.
    check_ids = [check["check_id"] for check in checks]
    check_id_set = set(check_ids)
    decision_rows = [row for row in s.query("rca_judge_decisions", order_by="ts")
                     if row["check_id"] in check_id_set]
    judge_decisions = {check_id: [] for check_id in check_ids}
    for decision in decision_rows:
        judge_decisions[decision["check_id"]].append(decision)
    accounting = s.query("rca_symptom_accounting", order_by="computed_at", case_id=case_id)
    closure = s.query_one("rca_closures", case_id=case_id)
    transitions = s.query("rca_state_transitions", order_by="ts", case_id=case_id)
    audit_events = s.query(
        "rca_audit_events", order_by="ts", tenant_id=tenant_id,
        object_type="rca_case", object_id=case_id,
    )
    conclusion_events = [event for event in audit_events
                         if event.get("event_type") == "conclusion_approved"]
    aar_evidence = rca_evidence.list_case_evidence(
        case_id, int(case.get("workflow_generation") or 1)
    )
    selected_hypothesis = next(
        (row for row in hypothesis_candidates if row.get("lifecycle_status") == "selected"),
        None,
    )
    selected_focused_hypothesis = next(
        (row for row in focused_hypothesis_candidates
         if row.get("lifecycle_status") == "selected"), None,
    )
    confirmed_hypothesis = _confirmed_hypothesis(case_id)
    conclusion_draft = _compose_conclusion_draft(
        case=case, case_file=case_file, hypotheses=hypotheses,
        hypothesis_candidates=hypothesis_candidates,
        selected_hypothesis=_active_investigation_hypothesis(case_id),
        confirmed_hypothesis=confirmed_hypothesis,
        aar_evidence=aar_evidence, executions=executions,
    )
    data_chat_state = _data_chat_state(case, looks, executions, aar_evidence)
    from domains.rca.presentation import present_execution
    for execution in executions.values():
        execution["presentation"] = present_execution(execution)
    hypothesis_catalog = [
        {"hypothesis_id": row["hypothesis_id"], "number": index + 1,
         "statement": row.get("statement"), "lifecycle_status": row.get("lifecycle_status")}
        for index, row in enumerate(s.query(
            "rca_hypotheses", case_id=case_id, order_by="created_at, rowid"
        ))
    ]
    return {**case, "hypothesis_catalog": hypothesis_catalog,
           "active_investigation_hypothesis": _active_investigation_hypothesis(case_id),
           "case_file": case_file, "looks": looks, "executions": executions,
           "suspects": suspects, "hypotheses": hypotheses,
           "hypothesis_candidates": hypothesis_candidates,
           "selected_initial_hypothesis": selected_hypothesis,
           "focused_hypothesis_candidates": focused_hypothesis_candidates,
           "selected_focused_hypothesis": selected_focused_hypothesis,
           "investigation_context": investigation_context,
           "confirmation_checks": checks,
           "judge_decisions": judge_decisions, "confirmed_hypothesis": confirmed_hypothesis,
           "conclusion_draft": conclusion_draft,
           "symptom_accounting": accounting[-1] if accounting else None,
           "closure": closure, "transitions": transitions,
           "conclusion": conclusion_events[-1].get("after_json") if conclusion_events else None,
           "audit_events": audit_events, "aar_evidence": aar_evidence,
           "data_chat": data_chat_state,
           "investigation_limit": _investigation_limit(looks, executions)}


def _successful_agent_investigations(looks: list[dict],
                                     executions: dict[str, dict]) -> list[dict]:
    completed = []
    by_id = {look["look_id"]: look for look in looks}
    for look in looks:
        fork = look.get("fork_json") or {}
        parent_id = fork.get("combined_parent_look_id")
        if parent_id and ((by_id.get(parent_id) or {}).get("fork_json") or {}).get("combined_run_state") != "completed":
            continue
        execution = executions.get(look["look_id"]) or {}
        summary = execution.get("summary_json") or {}
        runtime = summary.get("runtime") or {}
        if (look.get("kind") == "planned"
                and fork.get("kind") == "agent_hypothesis_test"
                and fork.get("agent_runtime")
                and execution.get("status") in {"completed", "done"}
                and runtime.get("ok") is True):
            completed.append({
                "look_id": look["look_id"],
                "execution_id": execution.get("execution_id"),
                "completed_at": execution.get("executed_at"),
            })
    return completed


def _data_chat_state(case: dict, looks: list[dict], executions: dict[str, dict],
                     aar_evidence: list[dict]) -> dict:
    successful = _successful_agent_investigations(looks, executions)
    turns: dict[str, dict] = {}
    order: list[str] = []
    for event in aar_evidence:
        if not str(event.get("evidence_kind") or "").startswith("data_chat_"):
            continue
        details = event.get("details") or {}
        turn_id = details.get("turn_id")
        if not turn_id:
            continue
        if turn_id not in turns:
            turns[turn_id] = {"turn_id": turn_id, "events": []}
            order.append(turn_id)
        turn = turns[turn_id]
        turn["events"].append({
            "artifact_id": event.get("artifact_id"),
            "kind": event.get("evidence_kind"), "status": event.get("status"),
            "recorded_at": event.get("recorded_at"),
        })
        kind = event.get("evidence_kind")
        if kind == "data_chat_user_message":
            turn.update({
                "question": details.get("question"),
                "asked_at": event.get("recorded_at"),
                "user_evidence_artifact_id": event.get("artifact_id"),
            })
        elif kind == "data_chat_plan":
            turn["plan"] = details.get("plan")
            turn["model"] = details.get("model")
        elif kind == "data_chat_library_search":
            turn["library_search"] = details.get("library_search")
        elif kind == "data_chat_code_generation":
            turn["generated_code"] = details.get("generated_code")
        elif kind in {"data_chat_analysis_execution", "data_chat_sandbox_execution"}:
            turn["execution"] = {
                "status": event.get("status"),
                "result": details.get("result"),
                "error": details.get("error"),
                "download_artifact_id": details.get("download_artifact_id"),
            }
        elif kind == "data_chat_assistant_message":
            turn.update({
                "answer": details.get("answer"),
                "evidence_references": details.get("evidence_references") or [],
                "limitations": details.get("limitations") or [],
                "answered_at": event.get("recorded_at"),
                "assistant_evidence_artifact_id": event.get("artifact_id"),
                "status": event.get("status"),
            })
    return {
        "unlocked": len(successful) >= 2,
        "successful_investigation_count": len(successful),
        "required_successful_investigations": 2,
        "successful_investigations": successful,
        "turns": [turns[turn_id] for turn_id in order],
        "workflow_generation": int(case.get("workflow_generation") or 1),
    }


def _chat_supplied_evidence(case: dict, looks: list[dict], executions: dict[str, dict],
                            aar_evidence: list[dict]) -> tuple[dict, tuple[str, ...]]:
    # Operational checkpoints must not displace analytical evidence in bounded prompts.
    aar_evidence = [event for event in aar_evidence if event.get("evidence_kind") != "operation_progress"]
    refs = tuple(dict.fromkeys(
        event["artifact_id"] for event in aar_evidence
        if event.get("artifact_id") and event.get("status") in {
            "recorded", "completed", "accepted",
        }
    ))[-24:]
    completed = []
    for look in looks:
        execution = executions.get(look["look_id"]) or {}
        summary = execution.get("summary_json") or {}
        if not summary:
            continue
        result = summary.get("result") or {}
        completed.append({
            "look_id": look["look_id"],
            "question": ((look.get("fork_json") or {}).get("plan") or {}).get("question"),
            "status": execution.get("status"),
            "summary": result.get("summary") or summary.get("summary"),
            "metrics": result.get("metrics") or {},
            "evidence_rows": list(result.get("evidence_rows") or [])[:12],
        })
    relevant_events = []
    for event in aar_evidence[-30:]:
        if event.get("evidence_kind") not in {
            "case_context_created", "static_initial_review", "llm_initial_review",
            "agent_interpretation", "human_context", "human_decision",
        }:
            continue
        relevant_events.append({
            "artifact_id": event.get("artifact_id"),
            "kind": event.get("evidence_kind"), "status": event.get("status"),
            "summary": event.get("summary"), "details": event.get("details"),
        })
    hypotheses = [
        {key: row.get(key) for key in (
            "hypothesis_id", "statement", "evidence_basis", "proposed_test",
            "lifecycle_status", "origin",
        )}
        for row in s.query("rca_hypotheses", order_by="created_at", case_id=case["case_id"])
    ]
    return ({
        "case": {key: case.get(key) for key in (
            "case_id", "item_id", "table_name", "test_name", "test_family",
            "metric", "threshold", "state", "workflow_generation",
        )},
        "hypotheses": hypotheses,
        "completed_investigations": completed[-8:],
        "retained_events": relevant_events,
    }, refs)


@progress.action("Data chat")
def ask_data_chat(case_id: str, question: str, actor: str,
                  tenant_id: str = DEFAULT_TENANT) -> dict:
    """Answer one scoped RCA question with retained evidence or one calculation."""
    value = str(question or "").strip()
    if not value:
        raise ValueError("A data-chat question is required")
    if len(value) > 2000:
        raise ValueError("A data-chat question must not exceed 2000 characters")
    case = require_case(case_id, tenant_id)
    case_file = s.query_one("rca_case_files", case_id=case_id) or {}
    looks = s.query("rca_looks", order_by="seq", case_id=case_id)
    look_ids = {look["look_id"] for look in looks}
    executions = {
        row["look_id"]: row for row in s.query("rca_look_executions")
        if row["look_id"] in look_ids
    }
    aar_evidence = rca_evidence.list_case_evidence(
        case_id, int(case.get("workflow_generation") or 1)
    )
    successful = _successful_agent_investigations(looks, executions)
    if len(successful) < 2:
        raise TransitionError(
            "Ask about this data unlocks after two successful hypothesis-test runs"
        )
    supplied, evidence_refs = _chat_supplied_evidence(
        case, looks, executions, aar_evidence
    )
    turn_id = _id("chat")
    user_artifact_id = rca_evidence.record_event(
        case, evidence_kind="data_chat_user_message", stage="investigate",
        status="recorded", actor=actor, source_artifact_ids=evidence_refs,
        details={
            "turn_id": turn_id, "question": value,
            "supplied_evidence": supplied,
            "supplied_evidence_refs": list(evidence_refs),
        },
    )
    checklist = case_file.get("checklist_json") or {}
    analysis_table = _case_analysis_table(case)
    schema = case_file.get("schema_snapshot_json") or {}
    catalog = investigation_runtime.helper_catalog(
        case.get("test_family") or checklist.get("test_family"),
        analysis_table, list(schema),
    )
    planner_payload = {
        "question": value, "active_scope": supplied["case"],
        "available_schema": schema, "retained_evidence": supplied,
        "helper_catalog": catalog,
        "prior_chat": _data_chat_state(case, looks, executions, aar_evidence)["turns"][-6:],
        "guardrails": {"one_analysis_maximum": True, "read_only": True,
                       "library_first": True, "no_autonomous_followup": True},
    }
    try:
        planned = data_chat.plan(planner_payload)
        plan = planned["output"]
    except Exception as exc:
        failed_plan_id = rca_evidence.record_event(
            case, evidence_kind="data_chat_plan", stage="investigate", status="failed",
            actor=actor, source_artifact_ids=(user_artifact_id,),
            details={"turn_id": turn_id, "error_type": type(exc).__name__,
                     "error": str(exc), "model": None,
                     "attempts": getattr(exc, "attempts", [])},
        )
        rca_evidence.record_event(
            case, evidence_kind="data_chat_assistant_message", stage="investigate",
            status="failed", actor=actor, source_artifact_ids=(failed_plan_id,),
            details={
                "turn_id": turn_id,
                "answer": "The question could not be planned, so no response was accepted.",
                "evidence_references": [], "limitations": [str(exc)],
                "error_type": type(exc).__name__,
            },
        )
        raise RcaAgentUnavailable(
            "The data-chat agent could not plan this answer; the question and failure were retained"
        ) from exc
    plan_artifact_id = rca_evidence.record_event(
        case, evidence_kind="data_chat_plan", stage="investigate", status="completed",
        actor=actor, source_artifact_ids=(user_artifact_id,),
        details={
            "turn_id": turn_id, "plan": plan,
            "model": planned["selected_model"], "attempts": planned["attempts"],
            "prompt_version": planned["prompt_version"],
        },
    )
    if plan["scope_decision"] == "out_of_scope":
        library = {"decision": "not_searched_out_of_scope", "selected_helper_id": None}
        library_id = rca_evidence.record_event(
            case, evidence_kind="data_chat_library_search", stage="investigate",
            status="completed", actor=actor, source_artifact_ids=(plan_artifact_id,),
            details={"turn_id": turn_id, "library_search": library},
        )
        rca_evidence.record_event(
            case, evidence_kind="data_chat_assistant_message", stage="investigate",
            status="rejected", actor=actor, source_artifact_ids=(library_id,),
            details={
                "turn_id": turn_id,
                "answer": "I can only answer questions about this active RCA and its retained dataset evidence.",
                "evidence_references": [], "limitations": [plan["scope_reason"]],
                "model": planned["selected_model"],
            },
        )
        return get_case(case_id, tenant_id)

    analysis_result = None
    analysis_artifact_id = None
    library = {"decision": "not_required_retained_evidence", "selected_helper_id": None}
    if plan["response_mode"] == "analysis":
        issue = issues_service.get_issue(case["issue_row_id"])
        source_evidence = issue.get("source_evidence") or {}
        population_context = source_evidence.get("population_context") or {}
        diagnostic = (_opening_summary(case_id).get("diagnostic") or {})
        params = {key: item for key, item in (plan.get("helper_params") or {}).items()
                  if item not in (None, [], "")}
        search = investigation_runtime.search_helpers(
            plan, catalog, has_retained_psi=bool(diagnostic.get("bins")),
            available_columns=set(schema),
            has_population_context=bool(
                (population_context.get("definition") or {}).get("method") == "split_snapshot"
            ),
        )
        library = search
    library_id = rca_evidence.record_event(
        case, evidence_kind="data_chat_library_search", stage="investigate",
        status="completed", actor=actor, source_artifact_ids=(plan_artifact_id,),
        details={"turn_id": turn_id, "library_search": library},
    )

    if plan["response_mode"] == "analysis":
        frame = _read_analysis_table(case["item_id"], analysis_table)
        feature_snapshot = checklist.get("feature_state_snapshot") or {}
        data_profile = source_evidence.get("data_profile") or {}
        declared = ((data_profile.get("declared_special_values")
                     or data_profile.get("special_values") or [])
                    if data_profile.get("special_values_confirmed") else [])
        helper_id = library.get("selected_helper_id")
        execution_kind = "data_chat_analysis_execution"
        execution_source_ids = [library_id]
        execution_failure_recorded = False
        execution_metadata = sandbox_capabilities.execution_metadata()
        try:
            if helper_id:
                analysis_result = investigation_runtime.run_helper(
                    helper_id, frame, params, diagnostic, population_context,
                    declared, {}, feature_snapshot,
                )
            else:
                try:
                    generated = investigation_agent.generate_code({
                        "plan": plan, "question": value,
                        "available_schema": schema,
                        "retained_aggregate_evidence": supplied,
                        "diagnostic_context": {
                            "population_context": population_context,
                            "feature_state_snapshot": feature_snapshot,
                        },
                        "investigation_history": supplied["completed_investigations"],
                    })
                except Exception as exc:
                    failed_code_id = rca_evidence.record_event(
                        case, evidence_kind="data_chat_code_generation",
                        stage="investigate", status="failed", actor=actor,
                        source_artifact_ids=(library_id,),
                        details={
                            "turn_id": turn_id, "error_type": type(exc).__name__,
                            "error": str(exc), "model": None,
                            "attempts": getattr(exc, "attempts", []),
                        },
                    )
                    execution_source_ids.append(failed_code_id)
                    execution_failure_recorded = True
                    raise RcaAgentUnavailable(
                        "The data-chat agent could not generate the bounded analysis; "
                        "the question, plan, and failure were retained"
                    ) from exc
                code = generated["output"]["python_code"]
                code_id = rca_evidence.record_event(
                    case, evidence_kind="data_chat_code_generation", stage="investigate",
                    status="completed", actor=actor, source_artifact_ids=(library_id,),
                    details={
                        "turn_id": turn_id, "generated_code": generated["output"],
                        "sandbox_contract": sandbox_capabilities.public_contract(),
                        "model": generated["selected_model"],
                        "attempts": generated["attempts"],
                        "prompt_version": generated["prompt_version"],
                    },
                )
                execution_kind = "data_chat_sandbox_execution"
                execution_source_ids.append(code_id)
                runtime = investigation_runtime.run_generated_code(
                    code, frame, {
                        "analysis_params": params, "retained_evidence": supplied,
                        "diagnostic_context": {"population_context": population_context},
                    }, feature_state_snapshot=feature_snapshot,
                )
                if not runtime.get("ok"):
                    rca_evidence.record_event(
                        case, evidence_kind=execution_kind, stage="investigate",
                        status=runtime.get("status") or "failed", actor=actor,
                        source_artifact_ids=tuple(execution_source_ids),
                        details={"turn_id": turn_id, "error": runtime.get("error")
                                 or runtime.get("errors"), **execution_metadata},
                    )
                    execution_failure_recorded = True
                    raise RcaError(runtime.get("error") or "The chat analysis did not complete")
                analysis_result = runtime["result"]
                full_result = runtime.get("full_result")
                if isinstance(full_result, dict):
                    output_id = rca_evidence.record_event(
                        case, evidence_kind="data_chat_sandbox_output", stage="investigate",
                        status="completed", actor=actor,
                        source_artifact_ids=tuple(execution_source_ids),
                        details={"turn_id": turn_id, "result": full_result},
                    )
                    analysis_result["download_artifact_id"] = output_id
                    analysis_result["download_filename"] = f"rca-{case_id}-{turn_id}-chat-output.txt"
                    execution_source_ids.append(output_id)
            analysis_artifact_id = rca_evidence.record_event(
                case, evidence_kind=execution_kind, stage="investigate", status="completed",
                actor=actor, source_artifact_ids=tuple(execution_source_ids),
                details={
                    "turn_id": turn_id, "helper_id": helper_id,
                    **execution_metadata,
                    "result": analysis_result,
                    "download_artifact_id": (analysis_result or {}).get("download_artifact_id"),
                },
            )
        except Exception as exc:
            if not execution_failure_recorded:
                rca_evidence.record_event(
                    case, evidence_kind=execution_kind, stage="investigate", status="failed",
                    actor=actor, source_artifact_ids=tuple(execution_source_ids),
                    details={"turn_id": turn_id, "error_type": type(exc).__name__,
                             "error": str(exc), **execution_metadata},
                )
            rca_evidence.record_event(
                case, evidence_kind="data_chat_assistant_message", stage="investigate",
                status="failed", actor=actor,
                source_artifact_ids=tuple(execution_source_ids),
                details={
                    "turn_id": turn_id,
                    "answer": "The bounded analysis did not complete, so no analytical answer was accepted.",
                    "evidence_references": [], "limitations": [str(exc)],
                },
            )
            raise

    answer_refs = list(evidence_refs)
    if analysis_artifact_id:
        answer_refs.append(analysis_artifact_id)
    try:
        answered = data_chat.answer({
            "question": value, "plan": plan, "retained_evidence": supplied,
            "analysis_result": analysis_result,
            "supplied_evidence_refs": answer_refs,
        })
    except Exception as exc:
        rca_evidence.record_event(
            case, evidence_kind="data_chat_assistant_message", stage="investigate",
            status="failed", actor=actor,
            source_artifact_ids=tuple(dict.fromkeys(
                (plan_artifact_id, library_id,
                 *([analysis_artifact_id] if analysis_artifact_id else []))
            )),
            details={
                "turn_id": turn_id,
                "answer": "The answer could not be completed, so no response was accepted.",
                "evidence_references": [], "limitations": [str(exc)],
                "error_type": type(exc).__name__,
                "attempts": getattr(exc, "attempts", []),
            },
        )
        raise RcaAgentUnavailable(
            "The data-chat agent could not complete the answer; the question, method, and failure were retained"
        ) from exc
    output = answered["output"]
    valid_refs = set(answer_refs)
    cited = [value for value in output.get("evidence_references") or []
             if value in valid_refs]
    assistant_id = rca_evidence.record_event(
        case, evidence_kind="data_chat_assistant_message", stage="investigate",
        status="completed", actor=actor,
        source_artifact_ids=tuple(dict.fromkeys((plan_artifact_id, library_id, *cited,
                                                 *([analysis_artifact_id]
                                                   if analysis_artifact_id else [])))),
        details={
            "turn_id": turn_id, "answer": output["answer"],
            "evidence_references": cited,
            "limitations": output.get("limitations") or [],
            "model": answered["selected_model"], "attempts": answered["attempts"],
            "prompt_version": answered["prompt_version"],
        },
    )
    _audit(
        tenant_id, actor, "data_chat_answered", "rca_case", case_id,
        after={"turn_id": turn_id, "response_mode": plan["response_mode"],
               "assistant_artifact_id": assistant_id},
    )
    return get_case(case_id, tenant_id)


def _compose_conclusion_draft(*, case: dict, case_file: dict,
                              hypotheses: list[dict], hypothesis_candidates: list[dict],
                              selected_hypothesis: dict | None,
                              confirmed_hypothesis: dict | None,
                              aar_evidence: list[dict],
                              executions: dict[str, dict]) -> dict:
    """Build a reviewable draft from governed RCA evidence without another model call."""
    proposed = confirmed_hypothesis or selected_hypothesis or (hypotheses[0] if hypotheses else None)
    hypothesis_id = (proposed or {}).get("hypothesis_id")
    readings = []
    for event in aar_evidence:
        details = event.get("details") or {}
        reading = details.get("interpretation") or {}
        if (event.get("evidence_kind") == "agent_interpretation"
                and event.get("status") == "completed"
                and (not hypothesis_id or details.get("hypothesis_id") == hypothesis_id)):
            readings.append({**reading, "look_id": details.get("look_id")})
    reading = readings[-1] if readings else {}
    assessment = reading.get("assessment")
    evidence_look_ids = list((proposed or {}).get("evidence_look_ids_json") or [])
    look_id = reading.get("look_id") or next(
        (value for value in reversed(evidence_look_ids) if value in executions), None
    )
    execution = executions.get(look_id) or {}
    execution_summary = execution.get("summary_json") or {}
    result = execution_summary.get("result") or {}
    result_summary = (result.get("summary") or "").strip()
    if not result_summary and execution_summary.get("found"):
        observed = [
            f"{key.replace('_', ' ')}={value}"
            for key, value in execution_summary.items()
            if key not in {"found", "result", "runtime"}
            and isinstance(value, (str, int, float, bool))
        ][:6]
        if observed:
            result_summary = "Governed analysis recorded " + ", ".join(observed) + "."
    statement = ((proposed or {}).get("statement") or "").strip()

    root_parts = []
    if statement:
        root_parts.append(statement)
    if result_summary:
        root_parts.append(f"Observed result: {result_summary}")
    if reading.get("rationale"):
        root_parts.append(f"RCA interpretation: {reading['rationale'].strip()}")

    rationale_parts = []
    if result_summary:
        rationale_parts.append(result_summary)
    evidence_points = [str(value).strip() for value in reading.get("evidence_points") or []
                       if str(value).strip()]
    if evidence_points:
        rationale_parts.append("Supporting observations:\n- " + "\n- ".join(evidence_points[:8]))
    if assessment:
        rationale_parts.append(f"The governed reader assessed the selected hypothesis as {assessment}.")
    elif confirmed_hypothesis and statement:
        rationale_parts.append(
            f"The governed confirmation workflow verified the proposed conclusion: {statement}"
        )
    elif statement:
        rationale_parts.append(f"Review the retained evidence for the proposed conclusion: {statement}")

    alternatives = []
    for row in [*hypothesis_candidates, *hypotheses]:
        if row.get("hypothesis_id") == hypothesis_id or not row.get("statement"):
            continue
        status = row.get("lifecycle_status") or row.get("tier") or "considered"
        text = f"{row['statement']} ({str(status).replace('_', ' ')})"
        if text not in alternatives:
            alternatives.append(text)

    next_question = (reading.get("next_question") or "").strip()
    if next_question:
        limiting_evidence = f"The retained evidence does not yet resolve: {next_question}"
    elif assessment == "supported" or confirmed_hypothesis:
        limiting_evidence = (
            "No contradictory evidence was identified within the governed analysis scope. "
            "The result establishes the observed data pattern; it does not by itself prove "
            "the upstream process mechanism or confirm that remediation has occurred."
        )
    else:
        limiting_evidence = (
            "The retained evidence does not yet support a conclusive root cause. "
            "Review the investigation record and document any additional scope limitation."
        )

    tier = str((proposed or {}).get("tier") or "").lower()
    confidence = "High" if tier == "strong" else "Low" if tier == "weak" else "Moderate"
    checklist = (case_file or {}).get("checklist_json") or {}
    table_name = checklist.get("table_name") or case.get("table_name") or "Table"
    columns = list(checklist.get("columns") or [])
    return {
        "conclusion_type": (
            "root_cause_identified"
            if confirmed_hypothesis or assessment == "supported" else "unresolved"
        ),
        "root_cause": "\n\n".join(root_parts),
        "confidence": confidence,
        "limiting_evidence": limiting_evidence,
        "alternatives_considered": (
            "Alternative explanations reviewed:\n- " + "\n- ".join(alternatives[:6])
            if alternatives else
            "No additional evidence-backed alternative was identified in the available scope."
        ),
        "affected_scope": f"{table_name}{' · ' + ', '.join(columns) if columns else ''}",
        "related_failures": (
            f"Reviewed against the {checklist.get('test_name') or 'diagnostic failure'} "
            "and the evidence retained in this RCA case."
        ),
        "owner": (proposed or {}).get("owner") or "",
        "approval_rationale": "\n\n".join(rationale_parts),
        "basis": {"hypothesis_id": hypothesis_id, "look_id": look_id,
                  "assessment": assessment},
    }


@progress.action("Approving conclusion")
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
    closed_case = require_case(case_id, tenant_id)
    aar_sources = tuple(value for value in evidence_ids
                        if s.query_one("analysis_artifacts", artifact_id=value))
    rca_evidence.record_event(
        closed_case, evidence_kind="human_decision", stage="closure", status="accepted",
        actor=actor, source_artifact_ids=aar_sources,
        details={"decision": "approve_conclusion", "conclusion": approved},
    )
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
    rca_evidence.record_event(
        require_case(case_id, tenant_id), evidence_kind="human_decision", stage="closure",
        status="accepted", actor=actor,
        source_artifact_ids=tuple(value for value in evidence_ids
                                  if s.query_one("analysis_artifacts", artifact_id=value)),
        details={"decision": "propose_reusable_knowledge",
                 "knowledge_draft_id": draft_rule["rule_id"], **metadata},
    )
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
        rca_evidence.record_event(
            case, evidence_kind="human_decision", stage="closure", status="rejected",
            actor=actor, details={"decision": "return_to_investigation", "reason": clean_reason},
        )
        return get_case(case_id, tenant_id)
    _product_transition(case, "investigation_loop", actor, clean_reason)
    _audit(tenant_id, actor, "conclusion_returned", "rca_case", case_id,
           after={"stage": "investigate"}, reason=clean_reason)
    rca_evidence.record_event(
        require_case(case_id, tenant_id), evidence_kind="human_decision", stage="closure",
        status="rejected", actor=actor,
        details={"decision": "return_to_investigation", "reason": clean_reason},
    )
    return get_case(case_id, tenant_id)


# --- Deterministic look primitive ---------------------------------------------
# Fixed, deterministic computation — no model-generated code, matching the
# opening-look/look contract ("fixed deterministic calculations", WF §4).

def _profile_column(df, column: str, feature_state_snapshot: dict | None = None) -> dict:
    if column not in df.columns:
        return {"column": column, "found": False}
    series = df[column]
    governance = feature_states.column_governance(feature_state_snapshot, column)
    states = feature_states.classify(series, governance)
    regular = series.loc[states["regular"]]
    effective_missing = ~states["regular"]
    out = {"column": column, "found": True,
           "null_share": round(float(effective_missing.mean()), 4),
           "physical_null_share": round(float(states["physical_missing"].mean()), 4),
           "distinct": int(regular.nunique(dropna=True)),
           "feature_state_reconciliation": feature_states.reconciliation(
               df, feature_state_snapshot, [column]
           )}
    if len(regular.dropna()):
        try:
            out["mean"] = round(float(regular.dropna().astype(float).mean()), 4)
        except (TypeError, ValueError):
            pass
    return out


def _segment_breakdown(df, column: str, segment_column: str,
                       feature_state_snapshot: dict | None = None) -> dict:
    if column not in df.columns or segment_column not in df.columns:
        return {"column": column, "segment_column": segment_column, "found": False}
    target_states = feature_states.classify(
        df[column], feature_states.column_governance(feature_state_snapshot, column)
    )
    segment_states = feature_states.classify(
        df[segment_column], feature_states.column_governance(
            feature_state_snapshot, segment_column
        )
    )
    segment_labels = df[segment_column].astype("string")
    segment_labels.loc[segment_states["physical_missing"]] = f"{segment_column} physical missing"
    for raw, mask in segment_states["specials"]:
        segment_labels.loc[mask] = f"{segment_column} special: {raw}"
    effective_missing = ~target_states["regular"]
    grouped = segment_labels.rename("segment").to_frame().assign(
        effective_missing=effective_missing,
        physical_missing=target_states["physical_missing"],
    ).groupby("segment", dropna=False)
    null_share_by_segment = {
        str(k): round(float(v), 4)
        for k, v in grouped["effective_missing"].mean().items()
    }
    null_count_by_segment = {
        str(k): int(v) for k, v in grouped["effective_missing"].sum().items()
    }
    physical_null_count_by_segment = {
        str(k): int(v) for k, v in grouped["physical_missing"].sum().items()
    }
    worst_segment, worst_rate = max(null_share_by_segment.items(), key=lambda kv: kv[1], default=(None, 0.0))
    _, best_rate = min(null_share_by_segment.items(), key=lambda kv: kv[1], default=(None, 0.0))
    worst_count = null_count_by_segment.get(worst_segment, 0)
    return {"column": column, "segment_column": segment_column, "found": True,
           "null_share_by_segment": null_share_by_segment,
           "physical_null_count_by_segment": physical_null_count_by_segment,
           "worst_segment": worst_segment, "worst_rate": worst_rate, "best_rate": best_rate,
           "worst_count": worst_count,
           "feature_state_reconciliation": feature_states.reconciliation(
               df, feature_state_snapshot, [column, segment_column]
           )}


# --- Opening looks (Agent 2) ---------------------------------------------------

def _persist_initial_review_candidates(case: dict, output: dict,
                                       source_evidence_id: str) -> list[dict]:
    """Persist structured LLM proposals without promoting them to conclusions."""
    candidates = output.get("candidate_hypotheses") or []
    created = []
    for rank, candidate in enumerate(candidates, start=1):
        hypothesis_id = _id("hyp")
        s.insert("rca_hypotheses", {
            "hypothesis_id": hypothesis_id,
            "case_id": case["case_id"],
            "suspect_id": None,
            "statement": candidate["statement"],
            "label": "candidate",
            "tier": "unassessed",
            "evidence_look_ids_json": [],
            "confirm_check_json": {"proposed_test": candidate["testable_next_step"]},
            "reject_condition_json": None,
            "owner": None,
            "created_at": s.now_ist(),
            "origin": INITIAL_REVIEW_HYPOTHESIS_ORIGIN,
            "lifecycle_status": "candidate",
            "evidence_basis": candidate["evidence_basis"],
            "proposed_test": candidate["testable_next_step"],
            "source_evidence_id": source_evidence_id,
            "candidate_rank": rank,
            "selected_by": None,
            "selected_at": None,
        })
        created.append(s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id))
    return created


def _persist_driver_hypothesis_candidate(case: dict, look_id: str, reading: dict,
                                         source_evidence_id: str) -> dict:
    existing = next((row for row in _case_hypotheses(
        case["case_id"], origin=DRIVER_SEARCH_HYPOTHESIS_ORIGIN
    ) if look_id in (row.get("evidence_look_ids_json") or [])), None)
    if existing:
        return existing
    hypothesis_id = _id("hyp")
    s.insert("rca_hypotheses", {
        "hypothesis_id": hypothesis_id, "case_id": case["case_id"],
        "suspect_id": None, "statement": reading["focused_hypothesis"],
        "label": "driver-focused candidate", "tier": "unassessed",
        "evidence_look_ids_json": [look_id],
        "confirm_check_json": {"proposed_test": reading["proposed_test"]},
        "reject_condition_json": None, "owner": None, "created_at": s.now_ist(),
        "origin": DRIVER_SEARCH_HYPOTHESIS_ORIGIN, "lifecycle_status": "candidate",
        "evidence_basis": reading["evidence_basis"],
        "proposed_test": reading["proposed_test"],
        "source_evidence_id": source_evidence_id, "candidate_rank": 1,
        "selected_by": None, "selected_at": None,
    })
    # A new agent-proposed candidate must receive an explicit human review;
    # an earlier selected focused hypothesis must not suppress the new choice in the UI.
    with s.get_conn() as conn:
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='not_selected', "
            "selected_by=NULL, selected_at=NULL WHERE case_id=? AND origin=? "
            "AND lifecycle_status='selected'",
            (case["case_id"], DRIVER_SEARCH_HYPOTHESIS_ORIGIN),
        )
        conn.commit()
    return s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id)


def _run_llm_initial_review(case: dict, actor: str, checklist: dict, summary: dict,
                            source_artifact_ids: tuple[str, ...]) -> dict | None:
    """Run the configured LLM review without making static evidence depend on it."""
    from domains.rca import initial_review

    if not initial_review.enabled():
        return None
    try:
        policy = initial_review.public_policy()
    except Exception:  # configuration details are captured by the failed event below
        policy = None
    rca_evidence.record_event(
        case, evidence_kind="llm_initial_review", stage="initial_review", status="started",
        actor=actor, source_artifact_ids=source_artifact_ids, details={
            "workload": initial_review.WORKLOAD,
            "prompt_version": initial_review.PROMPT_VERSION,
            "contract_version": initial_review.CONTRACT_VERSION,
            "model_policy": policy,
            "evidence_bundle_fingerprint": summary.get("evidence_bundle_fingerprint"),
            "evidence_source_artifact_ids": list(source_artifact_ids),
        },
    )
    try:
        result = initial_review.review({
            "case_id": case["case_id"],
            "test_name": checklist.get("test_name"),
            "test_family": checklist.get("test_family"),
            "table_name": checklist.get("table_name"),
            "columns": checklist.get("columns") or [],
            "metric": checklist.get("metric"),
            "threshold": checklist.get("threshold"),
            "violation_count": checklist.get("violation_count"),
            "user_context": [row["answer"] for row in s.query(
                "rca_human_questions", order_by="created_at", case_id=case["case_id"]
            ) if row.get("answer")],
        }, summary)
    except Exception as exc:  # noqa: BLE001 - failure is retained and manual flow remains usable
        rca_evidence.record_event(
            case, evidence_kind="llm_initial_review", stage="initial_review", status="failed",
            actor=actor, source_artifact_ids=source_artifact_ids, details={
                "workload": initial_review.WORKLOAD,
                "error_type": type(exc).__name__,
                "attempts": getattr(exc, "attempts", []),
                "manual_continuation_available": True,
                "evidence_bundle_fingerprint": summary.get("evidence_bundle_fingerprint"),
                "evidence_source_artifact_ids": list(source_artifact_ids),
            },
        )
        return {"status": "failed", "error_type": type(exc).__name__}
    evidence_id = rca_evidence.record_event(
        case, evidence_kind="llm_initial_review", stage="initial_review", status="completed",
        actor=actor, source_artifact_ids=source_artifact_ids, details={
            **result,
            "evidence_bundle_fingerprint": summary.get("evidence_bundle_fingerprint"),
            "evidence_source_artifact_ids": list(source_artifact_ids),
        },
    )
    candidates = _persist_initial_review_candidates(
        case, result.get("output") or {}, evidence_id
    )
    return {
        "status": "completed", "aar_evidence_id": evidence_id,
        "candidate_hypothesis_ids": [row["hypothesis_id"] for row in candidates],
        **result,
    }

@progress.action("Initial review")
def run_opening_look(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    context_id = _ensure_case_context(case)
    if case["state"] == "intake":
        case = transition(case_id, "opening_looks", actor, reason="initial review started",
                          tenant_id=tenant_id)
    if case["state"] != "opening_looks":
        raise TransitionError(f"Initial review cannot run from state {case['state']!r}")
    rca_evidence.record_event(
        case, evidence_kind="static_initial_review", stage="initial_review", status="started",
        actor=actor, source_artifact_ids=(context_id,),
        details={"analysis": "diagnostic_aware_opening_evidence", "deterministic": True},
    )
    try:
        case_file = s.query_one("rca_case_files", case_id=case_id)
        checklist = case_file["checklist_json"]
        columns = checklist.get("columns") or []
        column = columns[0] if columns else None
        repository = AnalysisArtifactRepository()
        context_metadata, case_context = repository.get(context_id)

        source_evidence = (case_context.get("issue") or {}).get("source_evidence") or {}
        source_metrics = source_evidence.get("metrics")
        source_metrics = source_metrics if isinstance(source_metrics, dict) else {}
        has_governed_opening_evidence = bool(
            source_evidence.get("data_profile")
            or source_metrics.get("data_profile")
            or source_metrics.get("artifact_id")
        )
        if column and not has_governed_opening_evidence:
            frame = _read_analysis_table(case["item_id"], case["table_name"])
            raw_summary = _profile_column(
                frame, column, checklist.get("feature_state_snapshot") or {}
            )
        else:
            raw_summary = {"column": column, "found": bool(column)}

        def load_artifact(artifact_id: str) -> dict | None:
            try:
                _, payload = repository.get(artifact_id)
            except KeyError:
                return None
            return payload if isinstance(payload, dict) else None

        source_artifact_ids = tuple(dict.fromkeys(
            (context_id, *context_metadata.source_artifact_ids)
        ))
        summary = initial_review_evidence.build_opening_evidence(
            case_context, raw_summary,
            source_artifact_ids=source_artifact_ids,
            artifact_loader=load_artifact,
        )
    except Exception as exc:
        rca_evidence.record_event(
            case, evidence_kind="static_initial_review", stage="initial_review", status="failed",
            actor=actor, source_artifact_ids=(context_id,),
            details={"analysis": "diagnostic_aware_opening_evidence",
                     "error_type": type(exc).__name__,
                     "error": str(exc)},
        )
        raise

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
    transition(case_id, "initial_review_complete", actor, evidence_ids=[look_id], tenant_id=tenant_id)
    completed_case = require_case(case_id, tenant_id)
    evidence_id = rca_evidence.record_event(
        completed_case, evidence_kind="static_initial_review", stage="initial_review",
        status="completed", actor=actor, source_artifact_ids=source_artifact_ids,
        details={"look_id": look_id, "execution_id": execution_id,
                 "analysis": "diagnostic_aware_opening_evidence", "deterministic": True,
                 "evidence_bundle_fingerprint": summary["evidence_bundle_fingerprint"],
                 "evidence_source_artifact_ids": list(source_artifact_ids),
                 "result": summary},
    )
    llm_review = _run_llm_initial_review(
        completed_case, actor, checklist, summary, source_artifact_ids
    )
    return {"look_id": look_id, "execution_id": execution_id, "summary": summary,
            "aar_evidence_id": evidence_id, "llm_review": llm_review}


def continue_from_initial_review(case_id: str, actor: str,
                                 tenant_id: str = DEFAULT_TENANT) -> dict:
    case = require_case(case_id, tenant_id)
    if case["state"] != "initial_review_complete":
        raise TransitionError(f"Investigation cannot start from state {case['state']!r}")
    candidates = _case_hypotheses(
        case_id, origin=INITIAL_REVIEW_HYPOTHESIS_ORIGIN
    )
    selected = next(
        (row for row in candidates if row.get("lifecycle_status") == "selected"), None
    )
    if candidates and selected is None:
        raise TransitionError(
            "Select one candidate hypothesis before starting the investigation"
        )
    transition(case_id, "investigation_loop", actor, reason="initial review accepted",
               tenant_id=tenant_id)
    continued = require_case(case_id, tenant_id)
    rca_evidence.record_event(
        continued, evidence_kind="human_decision", stage="initial_review", status="accepted",
        actor=actor,
        source_artifact_ids=((selected.get("source_evidence_id"),)
                             if selected and selected.get("source_evidence_id") else ()),
        details={"decision": "continue_to_investigation",
                 "selected_hypothesis_id": (selected or {}).get("hypothesis_id"),
                 "selected_hypothesis": (selected or {}).get("statement"),
                 "from_state": "initial_review_complete",
                 "to_state": "investigation_loop"},
    )
    return get_case(case_id, tenant_id)


def select_initial_review_hypothesis(case_id: str, hypothesis_id: str, actor: str,
                                     tenant_id: str = DEFAULT_TENANT) -> dict:
    """Select exactly one persisted Initial Review proposal for investigation."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "initial_review_complete":
        raise TransitionError(
            f"An Initial Review hypothesis cannot be selected from state {case['state']!r}"
        )
    candidates = _case_hypotheses(
        case_id, origin=INITIAL_REVIEW_HYPOTHESIS_ORIGIN
    )
    target = next(
        (row for row in candidates if row["hypothesis_id"] == hypothesis_id), None
    )
    if target is None:
        raise RcaError("The selected hypothesis is not an Initial Review candidate for this case")
    if target.get("lifecycle_status") == "selected":
        return get_case(case_id, tenant_id)
    previous = next(
        (row for row in candidates if row.get("lifecycle_status") == "selected"), None
    )
    now = s.now_ist()
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='not_selected', "
            "selected_by=NULL, selected_at=NULL "
            "WHERE case_id=? AND origin=? AND lifecycle_status='selected'",
            (case_id, INITIAL_REVIEW_HYPOTHESIS_ORIGIN),
        )
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='selected', selected_by=?, "
            "selected_at=? WHERE hypothesis_id=? AND case_id=? AND origin=?",
            (actor, now, hypothesis_id, case_id, INITIAL_REVIEW_HYPOTHESIS_ORIGIN),
        )
        conn.commit()
    selected = s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id)
    _audit(
        tenant_id, actor, "hypothesis_selected", "rca_case", case_id,
        before={"hypothesis_id": (previous or {}).get("hypothesis_id")},
        after={"hypothesis_id": hypothesis_id, "statement": selected["statement"]},
        reason="Selected Initial Review hypothesis for investigation",
    )
    source_id = selected.get("source_evidence_id")
    rca_evidence.record_event(
        case, evidence_kind="human_decision", stage="initial_review", status="accepted",
        actor=actor, source_artifact_ids=((source_id,) if source_id else ()),
        details={
            "decision": "select_hypothesis",
            "hypothesis_id": hypothesis_id,
            "statement": selected["statement"],
            "evidence_basis": selected.get("evidence_basis"),
            "proposed_test": selected.get("proposed_test"),
            "replaced_hypothesis_id": (previous or {}).get("hypothesis_id"),
            "selected_at": now,
        },
    )
    return get_case(case_id, tenant_id)


def select_focused_hypothesis(case_id: str, hypothesis_id: str, actor: str,
                              tenant_id: str = DEFAULT_TENANT) -> dict:
    """Human-select one driver-informed candidate for the normal RCA loop."""
    case = require_case(case_id, tenant_id)
    if case["state"] != "investigation_loop":
        raise TransitionError(
            f"A focused hypothesis cannot be selected from state {case['state']!r}"
        )
    candidates = _case_hypotheses(case_id, origin=DRIVER_SEARCH_HYPOTHESIS_ORIGIN)
    target = next((row for row in candidates if row["hypothesis_id"] == hypothesis_id), None)
    if target is None:
        raise RcaError("The selected hypothesis is not a driver-search candidate for this case")
    if target.get("lifecycle_status") == "selected":
        return get_case(case_id, tenant_id)
    now = s.now_ist()
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='not_selected', "
            "selected_by=NULL, selected_at=NULL WHERE case_id=? AND origin=? "
            "AND lifecycle_status='selected'",
            (case_id, DRIVER_SEARCH_HYPOTHESIS_ORIGIN),
        )
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='selected', selected_by=?, "
            "selected_at=? WHERE hypothesis_id=? AND case_id=? AND origin=?",
            (actor, now, hypothesis_id, case_id, DRIVER_SEARCH_HYPOTHESIS_ORIGIN),
        )
        conn.commit()
    rca_evidence.record_event(
        case, evidence_kind="human_decision", stage="investigate", status="accepted",
        actor=actor,
        source_artifact_ids=((target.get("source_evidence_id"),)
                             if target.get("source_evidence_id") else ()),
        details={"decision": "select_driver_focused_hypothesis",
                 "hypothesis_id": hypothesis_id, "statement": target["statement"],
                 "evidence_basis": target.get("evidence_basis"),
                 "proposed_test": target.get("proposed_test"), "selected_at": now},
    )
    _audit(tenant_id, actor, "focused_hypothesis_selected", "rca_case", case_id,
           after={"hypothesis_id": hypothesis_id, "statement": target["statement"]})
    return get_case(case_id, tenant_id)


def review_hypothesis(case_id: str, hypothesis_id: str, actor: str, review: dict,
                      tenant_id: str = DEFAULT_TENANT) -> dict:
    """Retain a human review, optionally create a revised candidate, and select it."""
    case = require_case(case_id, tenant_id)
    target = s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id, case_id=case_id)
    if target is None or target.get("origin") not in {
        INITIAL_REVIEW_HYPOTHESIS_ORIGIN, DRIVER_SEARCH_HYPOTHESIS_ORIGIN,
    }:
        raise RcaError("The hypothesis is not a reviewable candidate for this case")
    origin = target["origin"]
    expected_state = (
        "initial_review_complete"
        if origin == INITIAL_REVIEW_HYPOTHESIS_ORIGIN else "investigation_loop"
    )
    if case["state"] != expected_state:
        raise TransitionError(
            f"This hypothesis cannot be reviewed from state {case['state']!r}"
        )

    def reviewed_value(name: str, limit: int) -> str:
        value = str(review.get(name) or target.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name.replace('_', ' ').title()} is required")
        if len(value) > limit:
            raise ValueError(
                f"{name.replace('_', ' ').title()} must not exceed {limit} characters"
            )
        return value

    statement = reviewed_value("statement", 4000)
    evidence_basis = reviewed_value("evidence_basis", 8000)
    proposed_test = reviewed_value("proposed_test", 8000)
    comment = str(review.get("comment") or "").strip()
    if len(comment) > 4000:
        raise ValueError("Hypothesis context must not exceed 4000 characters")
    revised = any((
        statement != str(target.get("statement") or "").strip(),
        evidence_basis != str(target.get("evidence_basis") or "").strip(),
        proposed_test != str(target.get("proposed_test") or "").strip(),
    ))
    now = s.now_ist()
    selected_id = hypothesis_id
    if revised:
        selected_id = _id("hyp")
        ranks = [int(row.get("candidate_rank") or 0)
                 for row in _case_hypotheses(case_id, origin=origin)]
        s.insert("rca_hypotheses", {
            "hypothesis_id": selected_id, "case_id": case_id,
            "suspect_id": target.get("suspect_id"), "statement": statement,
            "label": "human-refined candidate", "tier": target.get("tier") or "unassessed",
            "evidence_look_ids_json": target.get("evidence_look_ids_json") or [],
            "confirm_check_json": {"proposed_test": proposed_test},
            "reject_condition_json": target.get("reject_condition_json"),
            "owner": target.get("owner"), "created_at": now, "origin": origin,
            "lifecycle_status": "candidate", "evidence_basis": evidence_basis,
            "proposed_test": proposed_test,
            "source_evidence_id": target.get("source_evidence_id"),
            "candidate_rank": max(ranks, default=0) + 1,
            "selected_by": None, "selected_at": None,
        })

    context_id = None
    context_evidence_id = None
    stage = "initial_review" if origin == INITIAL_REVIEW_HYPOTHESIS_ORIGIN else "investigate"
    if comment:
        context_id = _id("hctx")
        s.insert("rca_human_questions", {
            "id": context_id, "case_id": case_id,
            "question": f"Context for hypothesis {hypothesis_id}",
            "asked_by_look_id": None, "answer": comment, "answered_by": actor,
            "answered_at": now, "knowledge_rule_id_out": None, "created_at": now,
        })
        context_evidence_id = rca_evidence.record_event(
            case, evidence_kind="human_context", stage=stage, status="recorded",
            actor=actor,
            source_artifact_ids=((target.get("source_evidence_id"),)
                                 if target.get("source_evidence_id") else ()),
            details={"context_id": context_id, "comment": comment,
                     "hypothesis_id": hypothesis_id,
                     "reviewed_hypothesis_id": selected_id},
        )

    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='not_selected', "
            "selected_by=NULL, selected_at=NULL WHERE case_id=? AND origin=? "
            "AND lifecycle_status='selected'",
            (case_id, origin),
        )
        conn.execute(
            "UPDATE rca_hypotheses SET lifecycle_status='selected', selected_by=?, "
            "selected_at=? WHERE hypothesis_id=? AND case_id=? AND origin=?",
            (actor, now, selected_id, case_id, origin),
        )
        conn.commit()
    source_ids = tuple(value for value in (
        target.get("source_evidence_id"), context_evidence_id
    ) if value)
    rca_evidence.record_event(
        case, evidence_kind="human_decision", stage=stage, status="accepted",
        actor=actor, source_artifact_ids=source_ids,
        details={
            "decision": "review_and_select_hypothesis",
            "original_hypothesis_id": hypothesis_id,
            "selected_hypothesis_id": selected_id,
            "revision_created": revised,
            "original": {
                "statement": target.get("statement"),
                "evidence_basis": target.get("evidence_basis"),
                "proposed_test": target.get("proposed_test"),
            },
            "reviewed": {"statement": statement, "evidence_basis": evidence_basis,
                         "proposed_test": proposed_test},
            "context_id": context_id, "selected_at": now,
        },
    )
    _audit(
        tenant_id, actor, "hypothesis_reviewed", "rca_case", case_id,
        before={"hypothesis_id": hypothesis_id, "statement": target.get("statement")},
        after={"hypothesis_id": selected_id, "statement": statement,
               "revision_created": revised, "context_id": context_id},
        reason="Reviewed and selected hypothesis for governed investigation",
    )
    return get_case(case_id, tenant_id)


def add_investigation_context(case_id: str, comment: str, actor: str,
                              tenant_id: str = DEFAULT_TENANT,
                              hypothesis_id: str | None = None) -> dict:
    """Retain human context and cancel an unexecuted plan so it can be replanned."""
    case = require_case(case_id, tenant_id)
    intake = case["state"] == "intake"
    if case["state"] not in {"intake", "investigation_loop"}:
        raise TransitionError(
            f"Investigation context cannot be added from state {case['state']!r}"
        )
    value = str(comment or "").strip()
    if not value:
        raise ValueError("Investigation context is required")
    if len(value) > 4000:
        raise ValueError("Investigation context must not exceed 4000 characters")
    if hypothesis_id:
        if intake:
            raise TransitionError("Intake context belongs to the case, not a hypothesis")
        target = s.query_one("rca_hypotheses", case_id=case_id, hypothesis_id=hypothesis_id)
        if not target:
            raise KeyError("Unknown hypothesis for this RCA")
        active = _active_investigation_hypothesis(case_id)
        if not active or active["hypothesis_id"] != hypothesis_id:
            raise TransitionError("Context can only be added to the active hypothesis")
    planned_looks = _case_planned_looks(case_id)
    if any((look.get("fork_json") or {}).get("combined_run_state") == "running"
           for look in planned_looks):
        raise TransitionError("Wait for the current hypothesis run before adding context")
    now = s.now_ist()
    context_id = _id("hctx")
    s.insert("rca_human_questions", {
        "id": context_id, "case_id": case_id,
        "question": (f"Context for hypothesis {hypothesis_id}" if hypothesis_id
                     else "User-provided investigation context"), "asked_by_look_id": None,
        "answer": value, "answered_by": actor, "answered_at": now,
        "knowledge_rule_id_out": None, "created_at": now,
    })
    context_evidence_id = rca_evidence.record_event(
        case, evidence_kind="human_context", stage="intake" if intake else "investigate", status="recorded",
        actor=actor, details={"context_id": context_id, "comment": value,
                             "hypothesis_id": hypothesis_id},
    )
    executions = {row["look_id"] for row in s.query("rca_look_executions")}
    pending = next((look for look in reversed(planned_looks)
                    if look["look_id"] not in executions
                    and (not hypothesis_id or (look.get("fork_json") or {}).get("hypothesis_id") == hypothesis_id)), None)
    if pending:
        execution_id = _id("exec")
        s.insert("rca_look_executions", {
            "execution_id": execution_id, "look_id": pending["look_id"],
            "status": "cancelled",
            "summary_json": {"found": False, "cancelled": True,
                             "reason": "Superseded by new user investigation context.",
                             "context_evidence_id": context_evidence_id},
            "crashed": 0, "retried": 0, "executed_at": now,
        })
        s.update("rca_looks", {"look_id": pending["look_id"]}, {"budget_counted": 0})
        rca_evidence.record_event(
            case, evidence_kind="investigation_plan_cancelled", stage="investigate",
            status="cancelled", actor=actor,
            source_artifact_ids=(context_evidence_id,),
            details={"look_id": pending["look_id"], "execution_id": execution_id,
                     "reason": "New user context requires replanning."},
        )
    _audit(tenant_id, actor, "investigation_context_added", "rca_case", case_id,
           after={"context_id": context_id, "comment": value, "hypothesis_id": hypothesis_id})
    return get_case(case_id, tenant_id)


# --- Planner / Runner / Reader — budgeted loop (Agents 3/4/5) ------------------
# WF §5: budget ~10 looks; stops on converged / battle-tested / budget-spent /
# dead-end; board cap 8 active suspects (9th needs a named kill target).

LOOK_BUDGET = 10
HYPOTHESIS_RUN_LIMIT = 2
BOARD_CAP = 8
SECOND_CHANCE_BONUS_LOOKS = 5


def _investigation_limit(looks: list[dict], executions: dict[str, dict]) -> dict:
    """Count started root runs, never proposals, opening evidence or confirmation children."""
    used = 0
    for look in looks:
        fork = look.get("fork_json") or {}
        execution = executions.get(look["look_id"]) or {}
        if (look.get("kind") != "planned" or not fork.get("agent_runtime")
                or fork.get("combined_parent_look_id") or execution.get("status") == "cancelled"):
            continue
        if fork.get("execution_started") or fork.get("combined_run_state") or execution:
            used += 1
    return {"limit": HYPOTHESIS_RUN_LIMIT, "used": used,
            "remaining": max(0, HYPOTHESIS_RUN_LIMIT - used),
            "reached": used >= HYPOTHESIS_RUN_LIMIT}


def _require_hypothesis_capacity(case_id: str, *, conn=None) -> None:
    looks = s.query("rca_looks", case_id=case_id, conn=conn)
    executions = {look["look_id"]: execution for look in looks
                  if (execution := s.query_one("rca_look_executions", look_id=look["look_id"], conn=conn))}
    if _investigation_limit(looks, executions)["reached"]:
        raise TransitionError("Two-run hypothesis limit reached. Use available data chat or continue to closure.")


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


def _opening_summary(case_id: str) -> dict:
    opening = next((look for look in s.query("rca_looks", order_by="seq", case_id=case_id)
                    if look.get("kind") == "opening"), None)
    if not opening:
        return {}
    execution = s.query_one("rca_look_executions", look_id=opening["look_id"])
    return (execution or {}).get("summary_json") or {}


def _agent_investigation_history(case: dict) -> list[dict]:
    """Return a bounded same-generation history for follow-up planning."""
    interpretation_by_look = {}
    for event in rca_evidence.list_case_evidence(
        case["case_id"], int(case.get("workflow_generation") or 1)
    ):
        details = event.get("details") or {}
        if (event.get("evidence_kind") == "agent_interpretation"
                and event.get("status") == "completed" and details.get("look_id")):
            interpretation_by_look[details["look_id"]] = details.get("interpretation") or {}
    history = []
    for look in _case_planned_looks(case["case_id"]):
        fork = look.get("fork_json") or {}
        if not fork.get("agent_runtime"):
            continue
        execution = s.query_one("rca_look_executions", look_id=look["look_id"])
        result = ((execution or {}).get("summary_json") or {}).get("result") or {}
        reading = interpretation_by_look.get(look["look_id"], {})
        history.append({
            "look_id": look["look_id"],
            "question": (fork.get("plan") or {}).get("question"),
            "helper_id": look.get("sql_or_helper_ref"),
            "helper_params": fork.get("helper_params") or {},
            "result_summary": result.get("summary"),
            "result_metrics": result.get("metrics"),
            "assessment": reading.get("assessment"),
            "next_question": reading.get("next_question"),
        })
    return history[-5:]


def _agent_propose_driver_search(case: dict, selected: dict, actor: str, *,
                                 analysis_table: str, schema: dict,
                                 opening: dict, population_context: dict,
                                 declared_special_values: list,
                                 feature_state_snapshot: dict,
                                 psi_bin_definition: dict | None,
                                 source_ids: tuple[str, ...]) -> dict | None:
    """Create the first governed driver-discovery look when a valid target exists."""
    case_id = case["case_id"]
    rca_evidence.record_event(
        case, evidence_kind="driver_target_definition", stage="investigate", status="started",
        actor=actor, source_artifact_ids=source_ids,
        details={"hypothesis_id": selected["hypothesis_id"]},
    )
    user_context = [
        f"{row.get('question') or 'User context'}: {row['answer']}" for row in s.query(
            "rca_human_questions", order_by="created_at", case_id=case_id
        ) if row.get("answer")
    ][-5:]
    try:
        defined = investigation_agent.define_driver_target({
            "case": {key: case.get(key) for key in (
                "case_id", "test_name", "test_family", "table_name", "metric", "threshold"
            )},
            "selected_hypothesis": {key: selected.get(key) for key in (
                "hypothesis_id", "statement", "evidence_basis", "proposed_test"
            )},
            "observed_diagnostic": opening,
            "population_context": population_context,
            "available_schema": schema,
            "user_context": user_context,
        })
        target_spec = defined["output"]
        if target_spec.get("target_mode") == "unavailable":
            rca_evidence.record_event(
                case, evidence_kind="driver_target_definition", stage="investigate", status="blocked",
                actor=actor, source_artifact_ids=source_ids,
                details={"hypothesis_id": selected["hypothesis_id"], "target_spec": target_spec,
                         "model": defined["selected_model"], "attempts": defined["attempts"],
                         "prompt_version": defined["prompt_version"]},
            )
            return None
        frame = _read_analysis_table(case["item_id"], analysis_table)
        from domains.rca.driver_search import validate_target_spec
        target_spec = validate_target_spec(
            frame, target_spec, population_context, opening.get("diagnostic"),
            psi_bin_definition,
        )
    except Exception as exc:
        rca_evidence.record_event(
            case, evidence_kind="driver_target_definition", stage="investigate", status="failed",
            actor=actor, source_artifact_ids=source_ids,
            details={"hypothesis_id": selected["hypothesis_id"],
                     "error_type": type(exc).__name__, "error": str(exc)},
        )
        return None

    target_id = rca_evidence.record_event(
        case, evidence_kind="driver_target_definition", stage="investigate", status="completed",
        actor=actor, source_artifact_ids=source_ids,
        details={"hypothesis_id": selected["hypothesis_id"], "target_spec": target_spec,
                 "model": defined["selected_model"], "attempts": defined["attempts"],
                 "prompt_version": defined["prompt_version"]},
    )
    helper_params = {"target_spec": target_spec}
    plan = {
        "question": target_spec["problem_statement"],
        "rationale": target_spec["rationale"],
        "analysis_kind": "shallow_greedy_driver_search",
        "preferred_helper_ids": ["greedy_driver_search"],
        "helper_params": helper_params,
        "expected_output": ["validated feature importance", "bounded separating rules",
                            "validation AUC", "balanced accuracy"],
        "supports_hypothesis_when": (
            "One or more adequately supported inputs provide stable out-of-sample separation "
            "and yield a testable focused hypothesis."
        ),
        "rejects_hypothesis_when": (
            "No eligible input provides material validated separation or the apparent split "
            "depends on leakage or inadequate support."
        ),
    }
    execution_artifact = investigation_runtime.helper_execution_artifact(
        "greedy_driver_search", helper_params
    )
    fork = {
        "kind": "agent_driver_search", "agent_runtime": True,
        "hypothesis_id": selected["hypothesis_id"], "hypothesis": selected["statement"],
        "source_evidence_id": target_id, "plan": plan,
        "library_search": {"selected_helper_id": "greedy_driver_search",
                           "reason": "Governed target-driven discovery helper"},
        "execution_mode": "approved_helper", "helper_params": helper_params,
        "population_context": population_context,
        "declared_special_values": declared_special_values,
        "feature_state_snapshot": feature_state_snapshot,
        "psi_bin_definition": psi_bin_definition,
        "execution_artifact": execution_artifact, "generated_code": None,
        "target_spec": target_spec,
        "planner": {"model": defined["selected_model"], "attempts": defined["attempts"],
                    "prompt_version": defined["prompt_version"]},
    }
    look_id = _id("look")
    s.insert("rca_looks", {
        "look_id": look_id, "case_id": case_id,
        "seq": len(_case_planned_looks(case_id)) + 2, "kind": "planned",
        "proposed_by": "investigation_agent", "fork_json": fork,
        "sql_or_helper_ref": "greedy_driver_search", "budget_counted": 1,
        "created_at": s.now_ist(),
    })
    rca_evidence.record_event(
        case, evidence_kind="library_search", stage="investigate", status="completed",
        actor=actor, source_artifact_ids=(target_id,),
        details={"look_id": look_id, "hypothesis_id": selected["hypothesis_id"],
                 "selected_helper_id": "greedy_driver_search",
                 "reason": "Governed target-driven discovery helper"},
    )
    rca_evidence.record_event(
        case, evidence_kind="agent_investigation_plan", stage="investigate", status="completed",
        actor=actor, source_artifact_ids=(target_id,),
        details={"look_id": look_id, "hypothesis_id": selected["hypothesis_id"],
                 "plan": plan, "execution_mode": "approved_helper",
                 "target_spec": target_spec, "execution_artifact": execution_artifact,
                 "model": defined["selected_model"], "attempts": defined["attempts"],
                 "prompt_version": defined["prompt_version"]},
    )
    _audit(case["tenant_id"], actor, "agent_driver_search_plan", "rca_look", look_id,
           after={"hypothesis_id": selected["hypothesis_id"], "target_mode": target_spec["target_mode"]})
    return {"look_id": look_id, "fork": fork}


def _agent_propose_look(case: dict, selected: dict, actor: str, *,
                        confirmation: dict | None = None) -> dict:
    case_id = case["case_id"]
    case_file = s.query_one("rca_case_files", case_id=case_id)
    checklist = case_file["checklist_json"] or {}
    analysis_table = _case_analysis_table(case)
    schema = case_file["schema_snapshot_json"] or v2_service._inventory_map(
        case["item_id"], analysis_table
    )
    columns = list(checklist.get("columns") or [])
    opening = _opening_summary(case_id)
    diagnostic = opening.get("diagnostic") if isinstance(opening, dict) else None
    issue = issues_service.get_issue(case["issue_row_id"])
    source_evidence = issue.get("source_evidence") or {}
    population_context = source_evidence.get("population_context") or {}
    data_profile = source_evidence.get("data_profile") or {}
    declared_special_values = ((data_profile.get("declared_special_values")
                                or data_profile.get("special_values") or [])
                               if data_profile.get("special_values_confirmed") else [])
    feature_state_snapshot = checklist.get("feature_state_snapshot") or {}
    psi_bin_definition = None
    psi_metrics = source_evidence.get("metrics") or {}
    bin_artifact_id = psi_metrics.get("bin_artifact_id")
    if bin_artifact_id:
        try:
            from domains.test_lab.diagnostics.t4_d14_population_stability.runner import load_frozen_bin_definition
            psi_bin_definition = load_frozen_bin_definition({
                "artifact_id": bin_artifact_id,
                "payload_hash": psi_metrics.get("bin_payload_hash"),
                "allow_historical": True,
            }, str(psi_metrics.get("feature") or columns[0]))
        except (KeyError, FileNotFoundError, ValueError):
            psi_bin_definition = None
    history = _agent_investigation_history(case)
    catalog = investigation_runtime.helper_catalog(
        case.get("test_family") or checklist.get("test_family"), analysis_table, columns,
    )
    source_ids = ((selected.get("source_evidence_id"),)
                  if selected.get("source_evidence_id") else ())
    if not history and confirmation is None:
        discovery = _agent_propose_driver_search(
            case, selected, actor, analysis_table=analysis_table, schema=schema,
            opening=opening, population_context=population_context,
            declared_special_values=declared_special_values, source_ids=source_ids,
            feature_state_snapshot=feature_state_snapshot,
            psi_bin_definition=psi_bin_definition,
        )
        if discovery:
            return discovery
    rca_evidence.record_event(
        case, evidence_kind="agent_investigation_plan", stage="investigate",
        status="started", actor=actor, source_artifact_ids=source_ids,
        details={"hypothesis_id": selected["hypothesis_id"],
                 "hypothesis": selected["statement"]},
    )
    payload = {
        "case": {key: case.get(key) for key in (
            "case_id", "test_name", "test_family", "table_name", "metric", "threshold"
        )},
        "selected_hypothesis": {
            key: selected.get(key) for key in (
                "hypothesis_id", "statement", "evidence_basis", "proposed_test"
            )
        },
        "opening_evidence": opening,
        "diagnostic_context": {
            "population_context": population_context,
            "declared_special_values": declared_special_values,
            "feature_state_snapshot": feature_state_snapshot,
        },
        "investigation_history": history,
        "user_context": [f"{row.get('question') or 'User context'}: {row['answer']}" for row in s.query(
            "rca_human_questions", order_by="created_at", case_id=case_id
        ) if row.get("answer")][-5:],
        "available_schema": schema,
        "helper_catalog": catalog,
        "guardrails": {"one_analysis_per_plan": True, "read_only": True,
                       "library_first": True, "max_generated_code_chars": 12000,
                       "sandbox_timeout_seconds": 15},
    }
    payload["case"]["table_name"] = analysis_table
    if confirmation is not None:
        payload["discovery_confirmation"] = confirmation
    try:
        planned = investigation_agent.plan(payload)
        plan = planned["output"]
        plan["helper_params"] = {
            key: value for key, value in (plan.get("helper_params") or {}).items()
            if value not in (None, [], "")
        }
        if confirmation is not None and plan.get("confirmation_possible") is False:
            raise RcaError("Confirmation needs additional information: " + plan["rationale"])
    except Exception as exc:
        attempts = getattr(exc, "attempts", [])
        rca_evidence.record_event(
            case, evidence_kind="agent_investigation_plan", stage="investigate",
            status="failed", actor=actor, source_artifact_ids=source_ids,
            details={"hypothesis_id": selected["hypothesis_id"],
                     "error_type": type(exc).__name__, "error": str(exc),
                     "attempts": attempts},
        )
        if attempts:
            raise RcaAgentUnavailable(
                "The investigation agent could not create a plan. Model attempt details were retained in the AAR; retry after checking the configured deployment."
            ) from exc
        raise

    search = investigation_runtime.search_helpers(
        plan, catalog, has_retained_psi=bool(diagnostic and diagnostic.get("bins")),
        available_columns={str(value).split(".")[-1] for value in schema},
        has_population_context=bool((population_context.get("definition") or {}).get("method") == "split_snapshot"),
    )
    rca_evidence.record_event(
        case, evidence_kind="library_search", stage="investigate", status="completed",
        actor=actor, source_artifact_ids=source_ids,
        details={"hypothesis_id": selected["hypothesis_id"], **search},
    )
    generated = None
    validation_errors: list[str] = []
    helper_id = search.get("selected_helper_id")
    if confirmation is not None and helper_id == "greedy_driver_search":
        raise RcaError("Confirmation cannot repeat driver discovery")
    if not helper_id:
        rca_evidence.record_event(
            case, evidence_kind="code_generation", stage="investigate", status="started",
            actor=actor, source_artifact_ids=source_ids,
            details={"hypothesis_id": selected["hypothesis_id"], "plan": plan},
        )
        try:
            generated = investigation_agent.generate_code({
                "plan": plan, "selected_hypothesis": payload["selected_hypothesis"],
                "available_schema": schema, "retained_aggregate_evidence": opening,
                "diagnostic_context": payload["diagnostic_context"],
                "investigation_history": history,
                "discovery_confirmation": confirmation,
            })
            validation_errors = investigation_runtime.validate_generated_code(
                generated["output"]["python_code"]
            )
            if validation_errors:
                raise RcaError("Generated code failed guardrails: " + "; ".join(validation_errors))
        except Exception as exc:
            rca_evidence.record_event(
                case, evidence_kind="code_generation", stage="investigate", status="failed",
                actor=actor, source_artifact_ids=source_ids,
                details={"hypothesis_id": selected["hypothesis_id"],
                         "error_type": type(exc).__name__, "error": str(exc),
                         "generated_code": (generated or {}).get("output"),
                         "model": (generated or {}).get("selected_model"),
                         "attempts": (generated or {}).get("attempts") or [],
                         "validation_errors": validation_errors},
            )
            raise
        rca_evidence.record_event(
            case, evidence_kind="code_generation", stage="investigate", status="completed",
            actor=actor, source_artifact_ids=source_ids,
            details={"hypothesis_id": selected["hypothesis_id"],
                     "generated_code": generated["output"],
                     "model": generated["selected_model"], "attempts": generated["attempts"],
                     "prompt_version": generated["prompt_version"]},
        )

    helper_params = plan.get("helper_params") or {}
    runtime_params = ({
        "analysis_params": helper_params,
        "retained_evidence": opening,
        "diagnostic_context": payload["diagnostic_context"],
    } if not helper_id else None)
    execution_artifact = (
        investigation_runtime.helper_execution_artifact(helper_id, helper_params)
        if helper_id else investigation_runtime.generated_execution_artifact(
            generated["output"]["python_code"], runtime_params or {}
        )
    )
    fork = {
        "kind": "agent_hypothesis_test", "agent_runtime": True,
        "hypothesis_id": selected["hypothesis_id"], "hypothesis": selected["statement"],
        "source_evidence_id": selected.get("source_evidence_id"),
        "plan": plan, "library_search": search,
        "execution_mode": "approved_helper" if helper_id else "generated_code_sandbox",
        "helper_params": helper_params, "runtime_params": runtime_params,
        "population_context": population_context,
        "declared_special_values": declared_special_values,
        "feature_state_snapshot": feature_state_snapshot,
        "execution_artifact": execution_artifact,
        "generated_code": (generated or {}).get("output"),
        "planner": {"model": planned["selected_model"], "attempts": planned["attempts"],
                    "prompt_version": planned["prompt_version"]},
    }
    if confirmation is not None:
        fork["combined_parent_look_id"] = confirmation["look_id"]
        fork["discovery_confirmation"] = confirmation
    look_id = _id("look")
    planned_looks = _case_planned_looks(case_id)
    s.insert("rca_looks", {
        "look_id": look_id, "case_id": case_id, "seq": len(planned_looks) + 2,
        "kind": "planned", "proposed_by": "investigation_agent", "fork_json": fork,
        "sql_or_helper_ref": helper_id or "generated_code_sandbox",
        "budget_counted": 0 if confirmation is not None else 1, "created_at": s.now_ist(),
    })
    rca_evidence.record_event(
        case, evidence_kind="agent_investigation_plan", stage="investigate",
        status="completed", actor=actor, source_artifact_ids=source_ids,
        details={"look_id": look_id, "hypothesis_id": selected["hypothesis_id"],
                 "plan": plan, "library_search": search,
                 "execution_mode": fork["execution_mode"],
                 "diagnostic_context": payload["diagnostic_context"],
                 "investigation_history": history,
                 "discovery_confirmation": confirmation,
                 "execution_artifact": execution_artifact,
                 "model": planned["selected_model"], "attempts": planned["attempts"],
                 "prompt_version": planned["prompt_version"]},
    )
    _audit(case["tenant_id"], actor, "agent_investigation_plan", "rca_look", look_id,
           after={"hypothesis_id": selected["hypothesis_id"], "execution_mode": fork["execution_mode"]})
    return {"look_id": look_id, "fork": fork}


def _propose_alternative(case: dict, actor: str) -> dict:
    case_id = case["case_id"]
    case_file = s.query_one("rca_case_files", case_id=case_id) or {}
    prior = s.query("rca_hypotheses", case_id=case_id, order_by="created_at, rowid")
    payload = {
        "case_id": case_id, "opening_evidence": _opening_summary(case_id),
        "available_schema": case_file.get("schema_snapshot_json") or {},
        "feature_state_snapshot": (case_file.get("checklist_json") or {}).get("feature_state_snapshot"),
        "prior_hypotheses": [{key: row.get(key) for key in
                              ("hypothesis_id", "statement", "evidence_basis")} for row in prior],
        "investigation_history": _agent_investigation_history(case),
        "user_context": [{"scope": row.get("question"), "comment": row["answer"]}
                         for row in s.query("rca_human_questions", case_id=case_id,
                                            order_by="created_at") if row.get("answer")],
    }
    proposed = None
    try:
        proposed = investigation_agent.propose_alternative(payload)
        output = investigation_agent.AlternativeExplanation.model_validate(proposed["output"]).model_dump()
        if not output["available"]:
            raise RcaError("Another explanation needs additional information: " + output["distinction"])
        if any(" ".join(row["statement"].lower().split()) ==
               " ".join(output["statement"].lower().split()) for row in prior):
            raise RcaError("The proposed explanation duplicates an existing hypothesis")
    except Exception as exc:
        rca_evidence.record_event(case, evidence_kind="alternative_hypothesis", stage="investigate",
            status="failed", actor=actor, details={"error": str(exc),
                "input": payload, "output": (proposed or {}).get("output"),
                "model": (proposed or {}).get("selected_model"),
                "attempts": (proposed or {}).get("attempts") or getattr(exc, "attempts", [])})
        raise
    hypothesis_id = _id("hyp")
    source_id = rca_evidence.record_event(case, evidence_kind="alternative_hypothesis",
        stage="investigate", status="completed", actor=actor,
        details={"hypothesis_id": hypothesis_id, "output": output, "input": payload,
                 "model": proposed.get("selected_model"), "attempts": proposed.get("attempts"),
                 "prompt_version": proposed.get("prompt_version"),
                 "user_action": "explore_another_explanation"})
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        s.insert("rca_hypotheses", {
            "hypothesis_id": hypothesis_id, "case_id": case_id, "suspect_id": None,
            "statement": output["statement"], "label": "alternative explanation", "tier": "unassessed",
            "evidence_look_ids_json": [row["look_id"] for row in _case_planned_looks(case_id)],
            "confirm_check_json": {"proposed_test": output["proposed_test"]},
            "reject_condition_json": None, "owner": None, "created_at": s.now_ist(),
            "origin": ALTERNATIVE_HYPOTHESIS_ORIGIN, "lifecycle_status": "selected",
            "evidence_basis": output["evidence_basis"], "proposed_test": output["proposed_test"],
            "source_evidence_id": source_id, "candidate_rank": len(prior) + 1,
            "selected_by": actor, "selected_at": s.now_ist(),
        }, conn=conn)
        conn.execute("UPDATE rca_hypotheses SET lifecycle_status='not_selected' "
                     "WHERE case_id=? AND hypothesis_id<>? AND lifecycle_status='selected'",
                     (case_id, hypothesis_id))
        conn.commit()
    return s.query_one("rca_hypotheses", hypothesis_id=hypothesis_id)


@progress.action("Planning investigation")
def planner_propose_look(case_id: str, actor: str, tenant_id: str = DEFAULT_TENANT,
                         kill_target_suspect_id: str | None = None,
                         exploration: str | None = None) -> dict:
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
    if exploration not in (None, "follow_up", "alternative"):
        raise ValueError("Unknown exploration choice")
    _require_hypothesis_capacity(case_id)
    if exploration:
        selected = _active_investigation_hypothesis(case_id)
        if not selected:
            raise TransitionError("Select a hypothesis before exploring")
        planned = _case_planned_looks(case_id)
        if any((row.get("fork_json") or {}).get("combined_run_state") == "running" for row in planned):
            raise TransitionError("Wait for the current investigation to finish")
        pending = [row for row in planned if not s.query_one("rca_look_executions", look_id=row["look_id"])]
        if exploration == "follow_up" and pending:
            current = next((row for row in pending if (row.get("fork_json") or {}).get("hypothesis_id") == selected["hypothesis_id"]), None)
            if current:
                return {"look_id": current["look_id"], "fork": current["fork_json"]}
        if _budget_spent(case_id) >= _effective_look_budget(case_id):
            return {"dead_end": True, "reason": "budget already spent"}
        if exploration == "alternative":
            selected = _propose_alternative(case, actor)
            for row in pending:
                execution_id = _id("exec")
                s.insert("rca_look_executions", {"execution_id": execution_id,
                    "look_id": row["look_id"], "status": "cancelled", "crashed": 0, "retried": 0,
                    "executed_at": s.now_ist(), "summary_json": {"cancelled": True,
                    "reason": "Superseded by the user's choice to explore another explanation."}})
                s.update("rca_looks", {"look_id": row["look_id"]}, {"budget_counted": 0})
                rca_evidence.record_event(case, evidence_kind="investigation_plan_cancelled",
                    stage="investigate", status="cancelled", actor=actor,
                    details={"look_id": row["look_id"], "execution_id": execution_id,
                             "replacement_hypothesis_id": selected["hypothesis_id"]})
        return _agent_propose_look(case, selected, actor)

    if _budget_spent(case_id) >= _effective_look_budget(case_id):
        return {"dead_end": True, "reason": "budget already spent"}

    focused_candidates = _case_hypotheses(
        case_id, origin=DRIVER_SEARCH_HYPOTHESIS_ORIGIN
    )
    selected = next((row for row in focused_candidates
                     if row.get("lifecycle_status") == "selected"), None)
    alternative = next((row for row in _case_hypotheses(case_id, origin=ALTERNATIVE_HYPOTHESIS_ORIGIN)
                        if row.get("lifecycle_status") == "selected"), None)
    selected = alternative or selected
    if focused_candidates and selected is None:
        raise TransitionError("Select the focused driver hypothesis before planning the next analysis")
    selected = selected or next((row for row in _case_hypotheses(
        case_id, origin=INITIAL_REVIEW_HYPOTHESIS_ORIGIN
    ) if row.get("lifecycle_status") == "selected"), None)
    if selected:
        return _agent_propose_look(case, selected, actor)

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
    if s.query_one("rca_look_executions", look_id=look_id, status="cancelled"):
        raise TransitionError("This plan was superseded by context; create a new plan before running")
    fork = look["fork_json"] or {}
    if fork.get("agent_runtime"):
        with s.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = s.query_one("rca_looks", look_id=look_id, conn=conn)
            fork = current["fork_json"] or {}
            if fork.get("execution_started") or s.query_one("rca_look_executions", look_id=look_id, conn=conn):
                raise TransitionError("This analysis has already started; retain its evidence")
            parent_id = fork.get("combined_parent_look_id")
            if parent_id:
                parent = s.query_one("rca_looks", look_id=parent_id, case_id=case["case_id"], conn=conn)
                if not parent or (parent.get("fork_json") or {}).get("combined_run_state") != "running":
                    raise TransitionError("Confirmation requires an active parent hypothesis run")
            elif fork.get("combined_run_state") != "running":
                _require_hypothesis_capacity(case["case_id"], conn=conn)
            fork["execution_started"] = True
            s.update("rca_looks", {"look_id": look_id}, {"fork_json": fork}, conn=conn)
            conn.commit()
    frame = _read_analysis_table(case["item_id"], _case_analysis_table(case))
    if fork.get("agent_runtime"):
        source_id = fork.get("source_evidence_id")
        source_ids = ((source_id,) if source_id else ())
        rca_evidence.record_event(
            case, evidence_kind="sandbox_execution", stage="investigate", status="started",
            actor=actor, source_artifact_ids=source_ids,
            details={"look_id": look_id, "hypothesis_id": fork.get("hypothesis_id"),
                     "execution_mode": fork.get("execution_mode")},
        )
        if fork.get("execution_mode") == "approved_helper":
            try:
                result = investigation_runtime.run_helper(
                    look["sql_or_helper_ref"], frame, fork.get("helper_params") or {},
                    _opening_summary(case["case_id"]).get("diagnostic"),
                    fork.get("population_context") or {},
                    fork.get("declared_special_values") or [],
                    fork.get("psi_bin_definition") or {},
                    fork.get("feature_state_snapshot") or {},
                )
                runtime = {"status": "completed", "ok": True, "result": result,
                           "platform": "approved_helper_runtime"}
            except Exception as exc:
                runtime = {"status": "failed", "ok": False,
                           "error": f"{type(exc).__name__}: {exc}",
                           "platform": "approved_helper_runtime"}
        else:
            generated = fork.get("generated_code") or {}
            runtime = investigation_runtime.run_generated_code(
                generated.get("python_code") or "", frame, fork.get("runtime_params") or {},
                feature_state_snapshot=fork.get("feature_state_snapshot") or {},
            )
            runtime["platform"] = "isolated_process_sandbox"
        runtime.update(sandbox_capabilities.execution_metadata())
        output_artifact_id = None
        full_result = runtime.pop("full_result", None)
        if runtime.get("ok") and isinstance(full_result, dict):
            output_artifact_id = rca_evidence.record_event(
                case, evidence_kind="sandbox_output", stage="investigate", status="completed",
                actor=actor, source_artifact_ids=source_ids,
                details={"look_id": look_id, "hypothesis_id": fork.get("hypothesis_id"),
                         "execution_mode": fork.get("execution_mode"),
                         "result": full_result},
            )
            runtime["result"]["download_artifact_id"] = output_artifact_id
            runtime["result"]["download_filename"] = (
                f"rca-{case['case_id']}-{look_id}-sandbox-output.txt"
            )
        summary = {"found": bool(runtime.get("ok")), "agent_runtime": True,
                   "hypothesis_id": fork.get("hypothesis_id"), "runtime": runtime,
                   "result": runtime.get("result")}
        rca_evidence.record_event(
            case, evidence_kind="sandbox_execution", stage="investigate",
            status=runtime.get("status") or "failed", actor=actor,
            source_artifact_ids=source_ids + ((output_artifact_id,) if output_artifact_id else ()),
            details={"look_id": look_id, "hypothesis_id": fork.get("hypothesis_id"),
                     "execution_mode": fork.get("execution_mode"), "runtime": runtime},
        )
    elif look["sql_or_helper_ref"] == "segment_breakdown" and fork.get("segment_column"):
        case_file = s.query_one("rca_case_files", case_id=case["case_id"]) or {}
        snapshot = (case_file.get("checklist_json") or {}).get("feature_state_snapshot") or {}
        summary = _segment_breakdown(
            frame, fork["column"], fork["segment_column"], snapshot
        )
    else:
        summary = {"found": False, "reason": "no eligible segment column for this table"}
    execution_id = _id("exec")
    s.insert("rca_look_executions", {
        "execution_id": execution_id, "look_id": look_id,
        "status": (summary.get("runtime") or {}).get("status", "done"),
        "summary_json": summary, "crashed": int(bool(summary.get("runtime") and not summary["runtime"].get("ok"))),
        "retried": 0, "executed_at": s.now_ist(),
    })
    _audit(case["tenant_id"], actor, "look_execution", "rca_look_execution", execution_id,
          after={"look_id": look_id, "found": summary.get("found")})
    return {"execution_id": execution_id, "summary": summary}


@progress.action("Hypothesis investigation")
def run_investigation(look_id: str, actor: str, tenant_id: str = DEFAULT_TENANT) -> dict:
    """Run discovery and exactly one confirmation under the original hypothesis."""
    look = s.query_one("rca_looks", look_id=look_id)
    if not look:
        raise KeyError("Unknown look")
    case = require_case(look["case_id"], tenant_id)
    if s.query_one("rca_look_executions", look_id=look_id, status="cancelled"):
        raise TransitionError("This plan was superseded by context; create a new plan before running")
    fork = look.get("fork_json") or {}
    if fork.get("combined_parent_look_id"):
        raise TransitionError("Confirmation is executed as part of its parent hypothesis run")
    if fork.get("kind") != "agent_driver_search":
        result = runner_execute(look_id, actor, tenant_id)
        reader_interpret(result["execution_id"], actor, tenant_id)
        return get_case(case["case_id"], tenant_id)
    # Claim this bounded run once, including duplicate requests from another tab.
    already_finished = False
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = s.query_one("rca_looks", look_id=look_id, conn=conn)
        current_fork = current["fork_json"] or {}
        state = current_fork.get("combined_run_state")
        if state == "running":
            raise TransitionError("This hypothesis run is already in progress")
        if state in {"completed", "failed"}:
            already_finished = True
        elif s.query_one("rca_look_executions", look_id=look_id, conn=conn):
            raise TransitionError("This discovery already ran; retain its evidence and plan a new test")
        else:
            _require_hypothesis_capacity(case["case_id"], conn=conn)
            current_fork["combined_run_state"] = "running"
            s.update("rca_looks", {"look_id": look_id}, {"fork_json": current_fork}, conn=conn)
        conn.commit()
    if already_finished:
        return get_case(case["case_id"], tenant_id)

    def ensure_current() -> None:
        latest = require_case(case["case_id"], tenant_id)
        if latest.get("workflow_generation") != case.get("workflow_generation"):
            raise TransitionError("The RCA was started afresh; this run was superseded")

    confirmation_look_id = None
    outcome = {"assessment": "inconclusive", "rationale": "The hypothesis run did not complete."}
    completed = False
    try:
        execution = runner_execute(look_id, actor, tenant_id)
        ensure_current()
        if not execution["summary"].get("found"):
            raise RcaError("Discovery did not complete; no confirmation was attempted")
        if not ((execution["summary"].get("result") or {}).get("metrics") or {}).get("top_feature"):
            raise RcaError("Discovery found no usable separator; no confirmation was attempted")
        discovered = reader_interpret(
            execution["execution_id"], actor, tenant_id, persist_driver_candidate=False
        )["interpretation"]
        ensure_current()
        selected = s.query_one("rca_hypotheses", hypothesis_id=fork.get("hypothesis_id"))
        if selected is None:
            raise RcaError("The original hypothesis is unavailable")
        confirmation = {
            "look_id": look_id, "interpretation": discovered,
            "result": execution["summary"].get("result"),
            "constraint": "Test the original hypothesis using this separator; do not change the hypothesis or start another discovery.",
        }
        with progress.phase("Planning confirmation"):
            proposed = _agent_propose_look(case, selected, actor, confirmation=confirmation)
        confirmation_look_id = proposed["look_id"]
        ensure_current()
        with progress.phase("Running confirmation"):
            confirmed = runner_execute(confirmation_look_id, actor, tenant_id)
        ensure_current()
        if not confirmed["summary"].get("found"):
            raise RcaError("Confirmation did not complete; no final analytical assessment was accepted")
        with progress.phase("Interpreting confirmation"):
            outcome = reader_interpret(confirmed["execution_id"], actor, tenant_id)["interpretation"]
        completed = True
    except Exception as exc:
        ensure_current()
        outcome = {"assessment": "inconclusive", "rationale": str(exc),
                   "limitations": ["The bounded run stopped; no automatic retry was started."]}
    ensure_current()
    current_fork["combined_run_state"] = "completed" if completed else "failed"
    current_fork["confirmation_look_id"] = confirmation_look_id
    s.update("rca_looks", {"look_id": look_id}, {
        "fork_json": current_fork, "budget_counted": 1,
    })
    rca_evidence.record_event(
        case, evidence_kind="combined_hypothesis_run", stage="investigate",
        status="completed" if completed else "failed", actor=actor,
        details={"look_id": look_id, "confirmation_look_id": confirmation_look_id,
                 "hypothesis_id": fork.get("hypothesis_id"), "interpretation": outcome},
    )
    return get_case(case["case_id"], tenant_id)


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


def reader_interpret(execution_id: str, actor: str, tenant_id: str = DEFAULT_TENANT,
                     *, persist_driver_candidate: bool = True) -> dict:
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

    if fork.get("agent_runtime"):
        selected = s.query_one("rca_hypotheses", hypothesis_id=fork.get("hypothesis_id"))
        source_id = fork.get("source_evidence_id")
        source_ids = ((source_id,) if source_id else ())
        rca_evidence.record_event(
            case, evidence_kind="agent_interpretation", stage="investigate", status="started",
            actor=actor, source_artifact_ids=source_ids, details={"look_id": look["look_id"],
                                  "hypothesis_id": fork.get("hypothesis_id")},
        )
        try:
            reader = (investigation_agent.interpret_driver_search
                      if kind == "agent_driver_search" else investigation_agent.interpret)
            interpreted = reader({
                "selected_hypothesis": {
                    "hypothesis_id": (selected or {}).get("hypothesis_id"),
                    "statement": (selected or {}).get("statement"),
                    "evidence_basis": (selected or {}).get("evidence_basis"),
                },
                "plan": fork.get("plan") or {},
                "discovery_confirmation": fork.get("discovery_confirmation"),
                "analysis_result": summary.get("result"),
                "runtime": {key: value for key, value in (summary.get("runtime") or {}).items()
                            if key not in {"result", "stdout"}},
            })
        except Exception as exc:
            rca_evidence.record_event(
                case, evidence_kind="agent_interpretation", stage="investigate", status="failed",
                actor=actor, source_artifact_ids=source_ids, details={"look_id": look["look_id"],
                                      "hypothesis_id": fork.get("hypothesis_id"),
                                      "error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        reading = interpreted["output"]
        assessment = reading["assessment"]
        suspect = None
        if kind == "agent_driver_search":
            assessment = "inconclusive"
            reading["assessment"] = assessment
        if assessment != "inconclusive":
            status = "active" if assessment == "supported" else "ruled_out"
            suspect_id = _id("susp")
            s.insert("rca_suspects", {
                "suspect_id": suspect_id, "case_id": case["case_id"], "kind": "single",
                "member_cause_ids_json": [fork.get("hypothesis_id")], "status": status,
                "origin": "agent_hypothesis_test", "created_at": s.now_ist(),
                "updated_at": s.now_ist(),
            })
            s.insert("rca_suspect_history", {
                "id": _id("sh"), "suspect_id": suspect_id, "prev_status": None,
                "new_status": status, "reason": reading["rationale"],
                "look_id": look["look_id"], "ts": s.now_ist(),
            })
            suspect = s.query_one("rca_suspects", suspect_id=suspect_id)
        elif kind != "agent_driver_search":
            s.update("rca_looks", {"look_id": look["look_id"]}, {"budget_counted": 0})
        interpretation_evidence_id = rca_evidence.record_event(
            case, evidence_kind="agent_interpretation", stage="investigate",
            status="completed", actor=actor,
            source_artifact_ids=source_ids,
            details={"look_id": look["look_id"], "hypothesis_id": fork.get("hypothesis_id"),
                     "interpretation": reading, "model": interpreted["selected_model"],
                     "attempts": interpreted["attempts"]},
        )
        focused_candidate = (_persist_driver_hypothesis_candidate(
            case, look["look_id"], reading, interpretation_evidence_id
        ) if kind == "agent_driver_search" and persist_driver_candidate else None)
        _audit(case["tenant_id"], actor, "agent_interpretation", "rca_look_execution",
               execution_id, after={"assessment": assessment, "hypothesis_id": fork.get("hypothesis_id")})
        return {"suspect": suspect, "interpretation": reading,
                "focused_hypothesis_candidate": focused_candidate,
                "stop": evaluate_stop_condition(case["case_id"])}

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
            "origin": COMPOSER_HYPOTHESIS_ORIGIN, "lifecycle_status": "composed",
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
            "origin": COMPOSER_HYPOTHESIS_ORIGIN, "lifecycle_status": "composed",
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
    case_file = s.query_one("rca_case_files", case_id=case["case_id"]) or {}
    snapshot = (case_file.get("checklist_json") or {}).get("feature_state_snapshot") or {}
    member_ids = suspect["member_cause_ids_json"] if suspect["kind"] == "pair" else [suspect_id]
    results = []
    for member_id in member_ids:
        origin = _suspect_origin_look(member_id) if suspect["kind"] == "pair" else _suspect_origin_look(suspect_id)
        fork = (origin or {}).get("fork_json") or {}
        if not fork.get("segment_column"):
            results.append({"found": False})
            continue
        results.append(_segment_breakdown(
            frame, fork["column"], fork["segment_column"], snapshot
        ))
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
    for h in _case_hypotheses(case_id):
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
    for h in _case_hypotheses(case_id):
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
    for h in _case_hypotheses(case_id):
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
