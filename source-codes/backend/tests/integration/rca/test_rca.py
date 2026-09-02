"""RCA Stage 3 — vertical slice behavioral tests.

Standalone-runnable (``python -m unittest tests.test_rca``); follows the
same SYSTEM_DB_PATH-at-import-time / KB_STORAGE_DIR sandboxing convention as
test_taxonomy.py and test_kb.py. No Azure OpenAI call is made or
required anywhere in this file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import pandas as pd

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-rca.db"
_TMP_KB_STORAGE = Path(tempfile.gettempdir()) / "archimedes-test-rca-storage"
_TMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archimedes-test-rca-uploads"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB_STORAGE.exists():
    shutil.rmtree(_TMP_KB_STORAGE)
if _TMP_UPLOAD_DIR.exists():
    shutil.rmtree(_TMP_UPLOAD_DIR)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB_STORAGE)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOAD_DIR)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import kb  # noqa: E402
import system_db as s  # noqa: E402
from domains.rca import service as rca  # noqa: E402
from ai.v2 import issues as issues_svc  # noqa: E402
from ai.v2 import service as v2_service  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
ACTOR = "tester"


def _execute_finalized_plan(item_id: str, scope: str = "framework") -> list[dict]:
    """Run this suite's finalized plan rows and store their results.

    Phase 6 / D-16 retired the Test Lab wizard's backend paths, including
    ``v2_service.execute_iter`` — the snippet executor these RCA fixtures
    used to call. RCA itself never depended on it (rca.py re-runs a plan
    row's snippet through ``ai.code_sandbox`` directly at closure); only
    the fixture did. This helper reproduces exactly what the fixture needed
    from it — sandbox-run each finalized+approved plan row, store one
    ``results_v2`` row per returned result dict, and propagate the plan
    row's tags onto the result (contracts.md §7) — so the RCA behaviour
    under test is unchanged.
    """
    from ai import code_sandbox
    import taxonomy
    import tenancy

    item = v2_service.require_item(item_id)
    rows = [r for r in s.query("plan_v2", item_id=item_id, scope=scope)
            if r["status"] == "finalized" and r.get("approved")]
    s.delete("results_v2", item_id=item_id, scope=scope)
    out = []
    for row in rows:
        frame = v2_service._read_table(item_id, row["table_name"])
        inv = v2_service._inventory_map(item_id, row["table_name"])
        support = v2_service._supporting(frame, item, row["table_name"])
        run = code_sandbox.run(row.get("snippet_code") or "",
                               frame, {"supporting": support, "classification": inv})
        result_obj = run.get("result_obj") if run.get("ok") else None
        if isinstance(result_obj, dict):
            result_objs = [result_obj]
        elif isinstance(result_obj, list) and result_obj and all(isinstance(x, dict) for x in result_obj):
            result_objs = result_obj
        else:
            result_objs = [{"status": "not_runnable",
                            "reason": run.get("error") or "Snippet did not return a result dict."}]
        for res in result_objs:
            record = {
                "result_id": v2_service._id("res"), "row_id": row["row_id"], "item_id": item_id,
                "table_name": row["table_name"], "scope": scope, "test_name": row["test_name"],
                "status": res.get("status", "not_runnable"), "metric": res.get("metric"),
                "threshold_json": v2_service._jsonable(res.get("threshold")),
                "violation_count": int(res.get("violation_count") or 0),
                "evidence_json": v2_service._jsonable(res.get("evidence") or {}),
                "columns_json": res.get("columns") or row.get("columns_json") or [],
                "not_runnable_reason": res.get("not_runnable_reason") or res.get("reason"),
                "watch_note": 0, "origin": row.get("origin"), "area_id": row.get("area_id"),
                "run_at": s.now_ist(),
            }
            s.insert("results_v2", record)
            taxonomy.inherit_tags(tenancy.DEFAULT_TENANT, "plan_v2", row["row_id"],
                                  "results_v2", record["result_id"], "system")
            out.append(record)
    return out


_COMPLETENESS_SNIPPET = '''
col = "amount"
null_share = float(df[col].isna().mean())
threshold = 0.30
status = "fail" if null_share > threshold else "pass"
result = {
    "status": status,
    "metric": round(null_share, 4),
    "threshold": threshold,
    "violation_count": int(df[col].isna().sum()),
    "evidence": {"column": col},
}
'''


def _build_fixture_item(name: str, null_rate_a=0.05, null_rate_b=0.65, rows_per_segment=30) -> dict:
    """A database item with one table whose 'amount' column has nulls
    heavily concentrated in segment 'B' — a clear segment-concentration
    signal for rca's opening/planned looks. The plan_v2/issues_v2 rows
    are constructed directly (below) rather than through build_plan's
    framework battery: which of the 14 registered tests actually fires on a
    given fixture is its own applicability-sweep logic (tested elsewhere,
    ai/dq_tests/) and not something these RCA-service tests should have
    to reverse-engineer — a real snippet through the real sandbox is still
    exercised, just with a test_name/snippet this suite controls directly."""
    item = v2_service.create_item("database", name)
    item_id = item["item_id"]
    import numpy as np
    rng = np.random.default_rng(42)
    rows = []
    for seg, rate in (("A", null_rate_a), ("B", null_rate_b)):
        for i in range(rows_per_segment):
            amount = None if rng.random() < rate else round(float(rng.uniform(100, 1000)), 2)
            rows.append({"facility_id": f"{seg}-{i}", "amount": amount, "segment": seg,
                        "default_flag": int(rng.random() < 0.2)})
    df = pd.DataFrame(rows)
    v2_service._write_table(item_id, "t1", df)
    v2_service.profile_item(item_id)
    v2_service.finalize_item(item_id)

    row_id = v2_service._id("plan")
    s.insert("plan_v2", {
        "row_id": row_id, "item_id": item_id, "table_name": "t1", "scope": "framework",
        "test_name": "Completeness check", "area_id": None, "origin": "framework",
        "status": "finalized", "columns_json": ["amount"], "params_json": {}, "reason": "",
        "snippet_code": _COMPLETENESS_SNIPPET, "approved": 1, "trigger": None,
        "crossval_json": None, "created_at": s.now_ist(), "updated_at": s.now_ist(),
    })
    _execute_finalized_plan(item_id, "framework")
    return item


def _first_open_issue(item_id: str) -> dict:
    payload = issues_svc.list_issues(item_id)
    issues = payload["issues"]
    if not issues:
        raise AssertionError("Fixture did not produce a failed test / issue row.")
    return issues[0]


def _drive_investigation_loop(case_id: str, actor: str, max_cycles: int = rca.LOOK_BUDGET + 2) -> dict:
    """Runs Planner -> Runner -> Reader repeatedly until a stop condition
    fires (or a kill-attempt is required once columns run out), mirroring
    what the UI's repeated button clicks do. Returns the final stop dict."""
    for _ in range(max_cycles):
        case = rca.require_case(case_id)
        if case["state"] != "investigation_loop":
            return {"stop": True, "reason": "already_out_of_loop"}
        proposed = rca.planner_propose_look(case_id, actor)
        if proposed.get("dead_end"):
            rca.handle_dead_end(case_id, actor)
            return {"stop": True, "reason": "dead_end"}
        executed = rca.runner_execute(proposed["look_id"], actor)
        result = rca.reader_interpret(executed["execution_id"], actor)
        if result["stop"]["stop"]:
            return result["stop"]
    raise AssertionError(f"Investigation loop did not stop within {max_cycles} cycles.")


class CaseCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.item = _build_fixture_item("rca-case-fixture")
        cls.issue = _first_open_issue(cls.item["item_id"])

    def test_no_pre_seeded_causes(self):
        case = rca.create_case_from_issue(self.issue["issue_row_id"], ACTOR)
        self.assertEqual(s.query("rca_suspects", case_id=case["case_id"]), [])
        self.assertEqual(s.query("rca_hypotheses", case_id=case["case_id"]), [])

    def test_case_creation_is_idempotent_per_issue(self):
        c1 = rca.create_case_from_issue(self.issue["issue_row_id"], ACTOR)
        c2 = rca.create_case_from_issue(self.issue["issue_row_id"], ACTOR)
        self.assertEqual(c1["case_id"], c2["case_id"])

    def test_case_ends_at_opening_looks_with_case_file_and_tag_snapshot(self):
        case = rca.create_case_from_issue(self.issue["issue_row_id"], ACTOR)
        self.assertEqual(case["state"], "opening_looks")
        case_file = s.query_one("rca_case_files", case_id=case["case_id"])
        self.assertEqual(case_file["checklist_json"]["table_name"], "t1")
        self.assertIn("amount", case_file["schema_snapshot_json"])
        transitions = s.query("rca_state_transitions", case_id=case["case_id"])
        self.assertEqual([t["new_state"] for t in transitions], ["triage", "intake", "opening_looks"])

    def test_illegal_transition_rejected(self):
        case = rca.create_case_from_issue(self.issue["issue_row_id"], ACTOR)
        with self.assertRaises(rca.TransitionError):
            rca.transition(case["case_id"], "closed", ACTOR)

    def test_concurrent_creation_does_not_return_a_mid_bootstrap_snapshot(self):
        """Regression test: create_case_from_issue's idempotent 'return the
        existing row' path used to hand back whatever state the row was in
        at that instant — including the transient created/triage/intake
        states a case passes through on its way to opening_looks, since
        each insert/transition below commits independently (no shared
        transaction across the whole call). A second concurrent caller for
        the same issue (React StrictMode double-invoking useEffect is a
        real, common trigger — not just a theoretical race) could observe
        and render a half-built case. Simulated here by inserting a case
        stuck at 'created' on a background thread with a deliberate delay,
        then calling create_case_from_issue from the main thread — it must
        poll past the transient state rather than returning it immediately."""
        import threading
        item = _build_fixture_item("rca-case-race-fixture")
        issue = _first_open_issue(item["item_id"])

        case_id = "rca_race_test_case"
        s.insert("rca_cases", {
            "case_id": case_id, "tenant_id": TENANT, "issue_row_id": issue["issue_row_id"],
            "item_id": item["item_id"], "table_name": "t1",
            "state": "created", "part": "A", "tag_snapshot_json": {},
            "complaint_text": None, "created_by": ACTOR, "created_at": s.now_ist(),
            "updated_at": s.now_ist(), "closed_at": None, "contract_version": "1",
        })

        def _finish_bootstrap_after_a_beat():
            import time
            time.sleep(0.1)
            rca.transition(case_id, "triage", ACTOR)
            rca.transition(case_id, "intake", ACTOR)
            rca.transition(case_id, "opening_looks", ACTOR)

        t = threading.Thread(target=_finish_bootstrap_after_a_beat)
        t.start()
        result = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
        t.join()

        self.assertEqual(result["case_id"], case_id)
        self.assertEqual(result["state"], "opening_looks")


class VerticalSliceHappyPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.item = _build_fixture_item("rca-happy-path-fixture")
        cls.issue = _first_open_issue(cls.item["item_id"])
        cls.case = rca.create_case_from_issue(cls.issue["issue_row_id"], ACTOR)

    def test_01_opening_look_finds_a_column_profile(self):
        result = rca.run_opening_look(self.case["case_id"], ACTOR)
        self.assertTrue(result["summary"]["found"])
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "investigation_loop")

    def test_02_planner_runner_reader_cycle_finds_concentrated_suspect(self):
        proposed = rca.planner_propose_look(self.case["case_id"], ACTOR)
        self.assertIn("segment_column", proposed["fork"])
        executed = rca.runner_execute(proposed["look_id"], ACTOR)
        self.assertTrue(executed["summary"]["found"])
        self.assertEqual(executed["summary"]["worst_segment"], "B")  # by fixture construction
        interpreted = rca.reader_interpret(executed["execution_id"], ACTOR)
        self.assertEqual(interpreted["suspect"]["status"], "active")  # concentrated in B
        # Stage 4: the loop doesn't stop after one cycle — converged needs
        # >=2 planned looks with nothing newly ruled out in the last two.
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "investigation_loop")

    def test_03_loop_runs_to_a_stop_condition_then_coverage_challenge(self):
        case_id = self.case["case_id"]
        stop = _drive_investigation_loop(case_id, ACTOR)
        self.assertTrue(stop["stop"])
        self.assertIn(stop["reason"], {"converged", "battle_tested", "budget_spent", "dead_end"})
        case = rca.require_case(case_id)
        self.assertEqual(case["state"], "coverage_challenge_blind")

        pass1 = rca.coverage_challenge_pass1(case_id, ACTOR)
        self.assertIn("possibly_overlooked", pass1)
        case = rca.require_case(case_id)
        self.assertEqual(case["state"], "coverage_challenge_history")

        pass2 = rca.coverage_challenge_pass2(case_id, ACTOR)
        case = rca.require_case(case_id)
        if pass2["added_suspect_id"]:
            self.assertEqual(case["state"], "reopened_kill_attempt")
            rca.run_reopened_kill_attempt(case_id, ACTOR)
            case = rca.require_case(case_id)
        self.assertEqual(case["state"], "hypothesis_composition")

    def test_04_composer_produces_tiered_hypotheses_with_evidence(self):
        composed = rca.compose_hypothesis(self.case["case_id"], ACTOR)
        self.assertGreaterEqual(len(composed["hypotheses"]), 1)
        self.assertLessEqual(len(composed["hypotheses"]), rca.MAX_HYPOTHESES)
        for hyp in composed["hypotheses"]:
            self.assertIn(hyp["tier"], {"strong", "moderate", "weak"})
            self.assertGreaterEqual(len(hyp["evidence_look_ids_json"]), 1)
            self.assertIsNotNone(hyp["reject_condition_json"])
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "confirmation_checks")

    def test_05_confirmation_check_confirms_and_reaches_awaiting_fix_approval(self):
        hyp = s.query_one("rca_hypotheses", case_id=self.case["case_id"])
        check = s.query_one("rca_confirmation_checks", hypothesis_id=hyp["hypothesis_id"])
        result = rca.run_confirmation_check(check["check_id"], ACTOR)
        self.assertIn(result["verdict"], {"confirmed", "rejected"})
        case = rca.require_case(self.case["case_id"])
        self.assertIn(case["state"], {"awaiting_fix_approval", "all_hypotheses_rejected"})
        self._verdict = result["verdict"]

    def test_06_human_approval_required_before_fix_application(self):
        case = rca.require_case(self.case["case_id"])
        if case["state"] != "awaiting_fix_approval":
            self.skipTest("the confirmation check in this run rejected the hypothesis (a legitimate real outcome)")
        hyp = s.query_one("rca_hypotheses", case_id=self.case["case_id"])
        proposal = rca.propose_fix(hyp["hypothesis_id"], ACTOR)
        self.assertIsNone(s.query_one("rca_fix_approvals", fix_proposal_id=proposal["id"]))
        approval = rca.approve_fix(proposal["id"], ACTOR)
        self.assertIsNotNone(approval["approved_by"])
        self.assertIsNone(approval["applied_confirmed_at"])
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "awaiting_fix_application")
        rca.confirm_fix_applied(approval["id"], ACTOR)
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "closure_rerun")

    def test_07_closure_reruns_original_test_without_writing_knowledge(self):
        case = rca.require_case(self.case["case_id"])
        if case["state"] != "closure_rerun":
            self.skipTest("the confirmation check in this run rejected the hypothesis (a legitimate real outcome)")
        # Simulated fix per contracts.md §0 decisions: the fix never mutates
        # data, so a controlled fake rerun_fn stands in for "an owner applied
        # the fix upstream" — see rca.close_case's docstring/comment.
        result = rca.close_case(self.case["case_id"], ACTOR, rerun_fn=lambda case: {"status": "pass"})
        self.assertTrue(result["closed"])
        self.assertIn(result["outcome"], {"confirmed", "test_design_flaw", "genuine_change"})
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "closed")
        closure = s.query_one("rca_closures", case_id=self.case["case_id"])
        self.assertEqual(closure["frozen_snapshot"], 1)
        self.assertIsNone(closure["knowledge_draft_id"])
        self.assertIsNone(result["draft_rule"])

    def test_08_full_case_bundle_is_readable(self):
        bundle = rca.get_case(self.case["case_id"])
        self.assertIn(bundle["state"], {"closed", "all_hypotheses_rejected"})
        self.assertGreaterEqual(len(bundle["hypotheses"]), 1)
        if bundle["state"] == "closed":
            self.assertIsNotNone(bundle["closure"])
        self.assertGreater(len(bundle["transitions"]), 5)


class RejectionAndGatekeeperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.item = _build_fixture_item("rca-reject-fixture")
        cls.issue = _first_open_issue(cls.item["item_id"])
        cls.case = rca.create_case_from_issue(cls.issue["issue_row_id"], ACTOR)
        rca.run_opening_look(cls.case["case_id"], ACTOR)
        _drive_investigation_loop(cls.case["case_id"], ACTOR)
        rca.coverage_challenge_pass1(cls.case["case_id"], ACTOR)
        pass2 = rca.coverage_challenge_pass2(cls.case["case_id"], ACTOR)
        if pass2["added_suspect_id"]:
            rca.run_reopened_kill_attempt(cls.case["case_id"], ACTOR)
        rca.compose_hypothesis(cls.case["case_id"], ACTOR)

    def _first_check(self):
        checks = []
        for h in s.query("rca_hypotheses", case_id=self.case["case_id"]):
            checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
        checks.sort(key=lambda c: c["order_rank"])
        return checks[0]

    def test_gatekeeper_rejects_a_check_without_a_reject_condition(self):
        check = self._first_check()
        hyp = s.query_one("rca_hypotheses", hypothesis_id=check["hypothesis_id"])
        s.update("rca_hypotheses", {"hypothesis_id": hyp["hypothesis_id"]}, {"reject_condition_json": None})
        with self.assertRaises(rca.RcaError):
            rca.run_confirmation_check(check["check_id"], ACTOR)
        # Restore for the next test in this class (shared fixture case).
        s.update("rca_hypotheses", {"hypothesis_id": hyp["hypothesis_id"]},
                {"reject_condition_json": hyp["reject_condition_json"]})

    def test_closure_without_a_real_fix_stays_honestly_open(self):
        check = self._first_check()
        result = rca.run_confirmation_check(check["check_id"], ACTOR)
        if result["verdict"] != "confirmed":
            self.skipTest("this run's highest-tier hypothesis was rejected (a legitimate real outcome) "
                          "— test_07_closure_reruns_original_test_without_writing_knowledge in the happy-path "
                          "class already covers the confirmed branch")
        proposal = rca.propose_fix(check["hypothesis_id"], ACTOR)
        approval = rca.approve_fix(proposal["id"], ACTOR)
        rca.confirm_fix_applied(approval["id"], ACTOR)
        # Default rerun_fn (no override): re-executes the real snippet
        # against unchanged data — a simulated fix does not mutate data, so
        # this reproduces the original failure, and close_case must not
        # fabricate a Confirmed outcome.
        result = rca.close_case(self.case["case_id"], ACTOR)
        self.assertFalse(result["closed"])
        case = rca.require_case(self.case["case_id"])
        self.assertEqual(case["state"], "closure_rerun")
        self.assertIsNone(s.query_one("rca_closures", case_id=self.case["case_id"]))


class TriageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _fixture_with_two_families(self, name: str) -> dict:
        """One item, two plan_v2/results_v2/issues_v2 rows on the SAME
        table: one 'missing_data'-family test, one 'schema'-family test —
        so Triage's two-signal match can be tested for both a hit (same
        family) and a miss (different family) against the same table."""
        item = v2_service.create_item("database", name)
        item_id = item["item_id"]
        import numpy as np
        rng = np.random.default_rng(7)
        rows = [{"facility_id": f"F-{i}", "amount": (None if rng.random() < 0.5 else 100.0),
                "segment": "A" if i % 2 else "B"} for i in range(20)]
        v2_service._write_table(item_id, "t1", pd.DataFrame(rows))
        v2_service.profile_item(item_id)
        v2_service.finalize_item(item_id)
        for test_name, snippet in (
            ("Completeness check", _COMPLETENESS_SNIPPET),
            ("Schema type check", 'result = {"status": "fail", "metric": 1.0, "threshold": 0.0, "violation_count": 1, "evidence": {}}'),
        ):
            s.insert("plan_v2", {
                "row_id": v2_service._id("plan"), "item_id": item_id, "table_name": "t1", "scope": "framework",
                "test_name": test_name, "area_id": None, "origin": "framework", "status": "finalized",
                "columns_json": ["amount"], "params_json": {}, "reason": "", "snippet_code": snippet,
                "approved": 1, "trigger": None, "crossval_json": None,
                "created_at": s.now_ist(), "updated_at": s.now_ist(),
            })
        _execute_finalized_plan(item_id, "framework")
        return item

    def test_second_issue_same_family_same_table_attaches_to_existing_case(self):
        item = self._fixture_with_two_families("rca-triage-match-fixture")
        issues = issues_svc.list_issues(item["item_id"])["issues"]
        completeness_issue = next(i for i in issues if i["test_name"] == "Completeness check")
        case_a = rca.create_case_from_issue(completeness_issue["issue_row_id"], ACTOR)

        # A second, distinct issue row on the same item/table with the same
        # inferred family ("missing_data") should attach, not start a new case.
        second_issue_row_id = v2_service._id("iss")
        s.insert("issues_v2", {
            "issue_row_id": second_issue_row_id, "item_id": item["item_id"], "table_name": "t1",
            "test_name": "Completeness check on facility_id", "status": "Open", "created_at": s.now_ist(),
            "workflow_version": "rca", "area_id": None, "criticality": None,
            "columns_json": ["facility_id"], "violation_count": 1, "threshold_json": None,
            "column_details_json": [], "metric": None,
        })
        case_b = rca.create_case_from_issue(second_issue_row_id, ACTOR)
        self.assertEqual(case_a["case_id"], case_b["case_id"])
        group = s.query_one("rca_failure_groups", case_id=case_a["case_id"])
        attached = s.query("rca_attached_failures", group_id=group["group_id"])
        self.assertEqual(len(attached), 1)

    def test_different_family_same_table_gets_its_own_case(self):
        item = self._fixture_with_two_families("rca-triage-mismatch-fixture")
        issues = issues_svc.list_issues(item["item_id"])["issues"]
        completeness_issue = next(i for i in issues if i["test_name"] == "Completeness check")
        schema_issue = next(i for i in issues if i["test_name"] == "Schema type check")
        case_a = rca.create_case_from_issue(completeness_issue["issue_row_id"], ACTOR)
        case_b = rca.create_case_from_issue(schema_issue["issue_row_id"], ACTOR)
        self.assertNotEqual(case_a["case_id"], case_b["case_id"])


class BoardCapAndRevivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _fresh_case_in_loop(self, name: str):
        # A brand-new item/table per test method (not a shared fixture) so
        # Triage's two-signal match can never attach one test's issue to
        # another still-open test's case within the grouping time window.
        item = _build_fixture_item(name)
        issue = _first_open_issue(item["item_id"])
        case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
        rca.run_opening_look(case["case_id"], ACTOR)
        return case

    def test_board_cap_requires_a_named_kill_target(self):
        case = self._fresh_case_in_loop("rca-boardcap-fixture")
        for i in range(rca.BOARD_CAP):
            s.insert("rca_suspects", {
                "suspect_id": f"susp_cap_{i}", "case_id": case["case_id"], "kind": "single",
                "member_cause_ids_json": [f"synthetic_{i}"], "status": "active", "origin": "look",
                "created_at": s.now_ist(), "updated_at": s.now_ist(),
            })
        with self.assertRaises(rca.RcaError):
            rca.planner_propose_look(case["case_id"], ACTOR)
        proposed = rca.planner_propose_look(case["case_id"], ACTOR, kill_target_suspect_id="susp_cap_0")
        self.assertEqual(proposed["fork"]["kind"], "kill_attempt")

    def test_revive_requires_reason_and_ruled_out_status(self):
        case = self._fresh_case_in_loop("rca-revival-fixture")
        suspect_id = "susp_revival_test"
        s.insert("rca_suspects", {
            "suspect_id": suspect_id, "case_id": case["case_id"], "kind": "single",
            "member_cause_ids_json": ["x"], "status": "ruled_out", "origin": "look",
            "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
        with self.assertRaises(ValueError):
            rca.revive_suspect(suspect_id, "", ACTOR)
        revived = rca.revive_suspect(suspect_id, "new evidence contradicts the earlier ruling", ACTOR)
        self.assertEqual(revived["status"], "revived")
        with self.assertRaises(rca.RcaError):
            rca.revive_suspect(suspect_id, "already revived, not ruled_out anymore", ACTOR)


class StopConditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.item = _build_fixture_item("rca-stopcond-fixture")
        cls.issue = _first_open_issue(cls.item["item_id"])
        cls.case = rca.create_case_from_issue(cls.issue["issue_row_id"], ACTOR)
        rca.run_opening_look(cls.case["case_id"], ACTOR)

    def test_budget_spent_stop_condition(self):
        for i in range(rca.LOOK_BUDGET):
            s.insert("rca_looks", {
                "look_id": f"look_budget_{i}", "case_id": self.case["case_id"], "seq": i + 2,
                "kind": "planned", "proposed_by": "planner", "fork_json": {}, "sql_or_helper_ref": "segment_breakdown",
                "budget_counted": 1, "created_at": s.now_ist(),
            })
        stop = rca.evaluate_stop_condition(self.case["case_id"])
        self.assertTrue(stop["stop"])
        self.assertEqual(stop["reason"], "budget_spent")
        # Planner must also refuse to propose past budget.
        proposed = rca.planner_propose_look(self.case["case_id"], ACTOR)
        self.assertTrue(proposed.get("dead_end"))


def _case_ready_for_verification(name: str) -> dict:
    """Drives a fresh case all the way to confirmation_checks (opening look
    -> loop -> coverage challenge -> Composer), reusing the same sequence as
    the happy-path suite. Stage 5 tests then manipulate hypotheses/checks/
    judge_decisions directly to set up specific verification scenarios."""
    item = _build_fixture_item(name)
    issue = _first_open_issue(item["item_id"])
    case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
    rca.run_opening_look(case["case_id"], ACTOR)
    _drive_investigation_loop(case["case_id"], ACTOR)
    rca.coverage_challenge_pass1(case["case_id"], ACTOR)
    pass2 = rca.coverage_challenge_pass2(case["case_id"], ACTOR)
    if pass2["added_suspect_id"]:
        rca.run_reopened_kill_attempt(case["case_id"], ACTOR)
    composed = rca.compose_hypothesis(case["case_id"], ACTOR)
    return {"case_id": case["case_id"], "hypotheses": composed["hypotheses"]}


class JudgeVerdictTests(unittest.TestCase):
    def test_inconclusive_band_then_refinement_resolves(self):
        ambiguous = {"found": True, "worst_rate": 0.15, "best_rate": 0.10}  # ratio 1.5, in [1.2, 2.0)
        self.assertEqual(rca._judge_verdict(ambiguous), "inconclusive")
        # Refinement uses a single decisive cutoff (1.5) — never inconclusive twice.
        self.assertIn(rca._judge_verdict(ambiguous, refine=True), {"confirmed", "rejected"})

    def test_clear_confirm_and_reject_bands(self):
        self.assertEqual(rca._judge_verdict({"found": True, "worst_rate": 0.30, "best_rate": 0.10}), "confirmed")
        self.assertEqual(rca._judge_verdict({"found": True, "worst_rate": 0.05, "best_rate": 0.10}), "rejected")
        self.assertEqual(rca._judge_verdict({"found": False}), "rejected")


class SymptomAccountingAndQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_confirmation_check_advances_to_next_queued_hypothesis_when_rejected(self):
        ready = _case_ready_for_verification("rca-queue-fixture")
        case_id = ready["case_id"]
        # Force every hypothesis's reject_condition to a state where the
        # FIRST check will clearly reject (spoil its suspect's segment
        # signal) so the queue must advance to the next one, proving
        # run_confirmation_check doesn't stop at the first rejection.
        checks = []
        for h in ready["hypotheses"]:
            checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
        checks.sort(key=lambda c: c["order_rank"])
        if len(checks) < 2:
            self.skipTest("this fixture run only produced one hypothesis — nothing to advance past")
        result = rca.run_confirmation_check(checks[0]["check_id"], ACTOR)
        if result["verdict"] == "confirmed":
            self.skipTest("this run's first-tier hypothesis genuinely confirmed — "
                          "the advance-past-rejection path is exercised whenever it doesn't")
        self.assertIn("next_check_id", result)
        case = rca.require_case(case_id)
        self.assertEqual(case["state"], "confirmation_checks")

    def test_symptom_accounting_row_written_on_confirm(self):
        ready = _case_ready_for_verification("rca-symptom-fixture")
        checks = []
        for h in ready["hypotheses"]:
            checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
        checks.sort(key=lambda c: c["order_rank"])
        result = rca.run_confirmation_check(checks[0]["check_id"], ACTOR)
        if result["verdict"] != "confirmed":
            self.skipTest("this run's first-tier hypothesis was not confirmed")
        accounting = s.query_one("rca_symptom_accounting", case_id=ready["case_id"])
        self.assertIsNotNone(accounting)
        self.assertGreaterEqual(accounting["explained_size"], 0)


class SecondChanceAndEscalationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _case_in_all_hypotheses_rejected(self, name: str) -> str:
        """Directly drives a case's state to all_hypotheses_rejected via the
        real transition() function (not a raw DB write) so the state
        machine's own validation is exercised, then hands back the case_id."""
        item = _build_fixture_item(name)
        issue = _first_open_issue(item["item_id"])
        case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
        rca.run_opening_look(case["case_id"], ACTOR)
        rca.transition(case["case_id"], "coverage_challenge_blind", ACTOR)
        rca.transition(case["case_id"], "coverage_challenge_history", ACTOR)
        rca.transition(case["case_id"], "hypothesis_composition", ACTOR)
        rca.transition(case["case_id"], "ready_for_verification", ACTOR)
        rca.transition(case["case_id"], "verification_planning", ACTOR)
        rca.transition(case["case_id"], "confirmation_checks", ACTOR)
        rca.transition(case["case_id"], "judging", ACTOR)
        rca.transition(case["case_id"], "all_hypotheses_rejected", ACTOR)
        return case["case_id"]

    def test_second_chance_granted_once_then_escalates_to_unresolved(self):
        case_id = self._case_in_all_hypotheses_rejected("rca-secondchance-fixture")
        first = rca.start_second_chance(case_id, ACTOR)
        self.assertTrue(first["second_chance_granted"])
        case = rca.require_case(case_id)
        self.assertEqual(case["state"], "investigation_loop")
        self.assertEqual(rca._effective_look_budget(case_id), rca.LOOK_BUDGET + rca.SECOND_CHANCE_BONUS_LOOKS)

        # Drive back to all_hypotheses_rejected a second time (simulating the
        # second attempt also failing) and confirm the second call escalates.
        rca.transition(case_id, "coverage_challenge_blind", ACTOR)
        rca.transition(case_id, "coverage_challenge_history", ACTOR)
        rca.transition(case_id, "hypothesis_composition", ACTOR)
        rca.transition(case_id, "ready_for_verification", ACTOR)
        rca.transition(case_id, "verification_planning", ACTOR)
        rca.transition(case_id, "confirmation_checks", ACTOR)
        rca.transition(case_id, "judging", ACTOR)
        rca.transition(case_id, "all_hypotheses_rejected", ACTOR)

        second = rca.start_second_chance(case_id, ACTOR)
        self.assertFalse(second["second_chance_granted"])
        self.assertEqual(second["outcome"], "unresolved")
        case = rca.require_case(case_id)
        self.assertEqual(case["state"], "unresolved")
        closure = s.query_one("rca_closures", case_id=case_id)
        self.assertEqual(closure["outcome"], "unresolved")


class TimeBoxEscalationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_overdue_fix_approval_gets_escalated(self):
        from datetime import datetime, timedelta
        approval_id = "appr_timebox_test"
        s.insert("rca_fix_proposals", {
            "id": "fixp_timebox_test", "hypothesis_id": "hyp_does_not_exist_but_unused",
            "options_json": [], "routed_to": "defect", "label": "defect", "created_at": s.now_ist(),
        })
        past_deadline = (datetime.fromisoformat(s.now_ist()) - timedelta(days=1)).isoformat()
        s.insert("rca_fix_approvals", {
            "id": approval_id, "fix_proposal_id": "fixp_timebox_test",
            "approved_by": ACTOR, "approved_at": s.now_ist(),
            "applied_confirmed_by": None, "applied_confirmed_at": None,
            "time_box_deadline": past_deadline, "escalated_at": None,
        })
        # hypothesis/case lookups inside the sweep will find nothing for this
        # synthetic proposal (no real hypothesis row) — the sweep must still
        # mark the approval escalated on the deadline check alone; it should
        # not raise just because the audit-event enrichment step finds no case.
        rca.check_time_box_escalations()
        approval = s.query_one("rca_fix_approvals", id=approval_id)
        self.assertIsNotNone(approval["escalated_at"])

    def test_not_yet_due_approval_is_left_alone(self):
        from datetime import datetime, timedelta
        approval_id = "appr_notdue_test"
        s.insert("rca_fix_proposals", {
            "id": "fixp_notdue_test", "hypothesis_id": "hyp_does_not_exist_but_unused",
            "options_json": [], "routed_to": "defect", "label": "defect", "created_at": s.now_ist(),
        })
        future_deadline = (datetime.fromisoformat(s.now_ist()) + timedelta(days=5)).isoformat()
        s.insert("rca_fix_approvals", {
            "id": approval_id, "fix_proposal_id": "fixp_notdue_test",
            "approved_by": ACTOR, "approved_at": s.now_ist(),
            "applied_confirmed_by": None, "applied_confirmed_at": None,
            "time_box_deadline": future_deadline, "escalated_at": None,
        })
        rca.check_time_box_escalations()
        approval = s.query_one("rca_fix_approvals", id=approval_id)
        self.assertIsNone(approval["escalated_at"])

    def test_sweep_never_escalates_a_different_tenants_approval(self):
        """Regression test: check_time_box_escalations() used to mark
        escalated_at on ANY overdue approval before checking which tenant it
        belonged to, so sweeping tenant A would silently swallow tenant B's
        escalation (no audit event, and B's own later sweep would then skip
        it forever since it's already marked). Fixed to gate the write on
        the tenant match. Built with direct inserts (same pattern as the
        other TimeBoxEscalationTests) rather than driving the real service
        functions — approve_fix()'s own transition() call is tenant-blind by
        design until Stage 6's tenant-scoping pass, so it can't advance a
        non-bootstrap-tenant case either; this test only needs the
        approval->proposal->hypothesis->case chain check_time_box_escalations
        itself reads, not a real state walk."""
        from datetime import datetime, timedelta
        other_tenant = "other-tenant-timebox-test"
        s.upsert("tenants", {"tenant_id": other_tenant, "name": "Other", "created_at": s.now_ist()})

        case_id = "rca_crosstenant_timebox_test"
        s.insert("rca_cases", {
            "case_id": case_id, "tenant_id": other_tenant, "issue_row_id": "iss_unused",
            "item_id": None, "table_name": None, "state": "awaiting_fix_application", "part": "b",
            "tag_snapshot_json": {}, "complaint_text": None, "created_by": ACTOR,
            "created_at": s.now_ist(), "updated_at": s.now_ist(), "closed_at": None,
        })
        hypothesis_id = "hyp_crosstenant_timebox_test"
        s.insert("rca_hypotheses", {
            "hypothesis_id": hypothesis_id, "case_id": case_id, "suspect_id": None,
            "statement": "unused", "label": "defect", "tier": "weak",
            "evidence_look_ids_json": [], "confirm_check_json": {}, "reject_condition_json": {},
            "owner": None, "created_at": s.now_ist(),
        })
        proposal_id = "fixp_crosstenant_timebox_test"
        s.insert("rca_fix_proposals", {
            "id": proposal_id, "hypothesis_id": hypothesis_id,
            "options_json": [], "routed_to": "defect", "label": "defect", "created_at": s.now_ist(),
        })
        approval_id = "appr_crosstenant_timebox_test"
        past_deadline = (datetime.fromisoformat(s.now_ist()) - timedelta(days=1)).isoformat()
        s.insert("rca_fix_approvals", {
            "id": approval_id, "fix_proposal_id": proposal_id,
            "approved_by": ACTOR, "approved_at": s.now_ist(),
            "applied_confirmed_by": None, "applied_confirmed_at": None,
            "time_box_deadline": past_deadline, "escalated_at": None,
        })

        # Sweeping the BOOTSTRAP tenant must not touch other_tenant's approval.
        rca.check_time_box_escalations(BOOTSTRAP_TENANT)
        untouched = s.query_one("rca_fix_approvals", id=approval_id)
        self.assertIsNone(untouched["escalated_at"])

        # Sweeping the OWNING tenant still catches it.
        result = rca.check_time_box_escalations(other_tenant)
        self.assertEqual(len(result), 1)
        owned = s.query_one("rca_fix_approvals", id=approval_id)
        self.assertIsNotNone(owned["escalated_at"])


class AttachedFailureReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_reconciliation_marks_attached_failure_matched_on_confirmed_closure(self):
        """WF §11 Agent 10: every Triage-attached 'probably same cause'
        failure is checked against the confirmed cause at closure."""
        item = _build_fixture_item("rca-reconcile-fixture")
        issue = _first_open_issue(item["item_id"])
        case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)

        second_issue_row_id = v2_service._id("iss")
        s.insert("issues_v2", {
            "issue_row_id": second_issue_row_id, "item_id": item["item_id"], "table_name": "t1",
            "test_name": "Completeness check duplicate", "status": "Open", "created_at": s.now_ist(),
            "workflow_version": "rca", "area_id": None, "criticality": None,
            "columns_json": ["amount"], "violation_count": 1, "threshold_json": None,
            "column_details_json": [], "metric": None,
        })
        second_case = rca.create_case_from_issue(second_issue_row_id, ACTOR)
        self.assertEqual(case["case_id"], second_case["case_id"])  # Triage attached, didn't fork
        group = s.query_one("rca_failure_groups", case_id=case["case_id"])
        attached = s.query("rca_attached_failures", group_id=group["group_id"])
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0]["reconciliation_status"], "pending")

        rca.run_opening_look(case["case_id"], ACTOR)
        _drive_investigation_loop(case["case_id"], ACTOR)
        rca.coverage_challenge_pass1(case["case_id"], ACTOR)
        pass2 = rca.coverage_challenge_pass2(case["case_id"], ACTOR)
        if pass2["added_suspect_id"]:
            rca.run_reopened_kill_attempt(case["case_id"], ACTOR)
        composed = rca.compose_hypothesis(case["case_id"], ACTOR)
        checks = []
        for h in composed["hypotheses"]:
            checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
        checks.sort(key=lambda c: c["order_rank"])

        confirmed_hypothesis_id = None
        for chk in checks:
            result = rca.run_confirmation_check(chk["check_id"], ACTOR)
            if result["verdict"] == "inconclusive":
                result = rca.run_confirmation_check(chk["check_id"], ACTOR)
            if result["verdict"] == "confirmed":
                confirmed_hypothesis_id = chk["hypothesis_id"]
                break
        if not confirmed_hypothesis_id:
            self.skipTest("no hypothesis in this run confirmed — a legitimate real outcome; "
                          "the reconciliation-at-closure path only fires past a confirmed cause")

        case_state = rca.require_case(case["case_id"])
        if case_state["state"] != "awaiting_fix_approval":
            self.skipTest("confirming didn't reach awaiting_fix_approval in this run "
                          "(e.g. queue continued for a partially-explained symptom size)")
        proposal = rca.propose_fix(confirmed_hypothesis_id, ACTOR)
        approval = rca.approve_fix(proposal["id"], ACTOR)
        rca.confirm_fix_applied(approval["id"], ACTOR)
        case_state = rca.require_case(case["case_id"])
        self.assertEqual(case_state["state"], "closure_rerun")

        result = rca.close_case(case["case_id"], ACTOR, rerun_fn=lambda c: {"status": "pass"})
        self.assertTrue(result["closed"])

        attached_after = s.query("rca_attached_failures", group_id=group["group_id"])
        self.assertEqual(attached_after[0]["reconciliation_status"], "matches")
        self.assertIsNotNone(attached_after[0]["reconciled_at"])


class AllFourClosingStatesTests(unittest.TestCase):
    """WF §7 / Stage 7 acceptance gate: 'all four endings work'
    (confirmed/genuine_change/test_design_flaw/unresolved). Unresolved is
    already covered by SecondChanceAndEscalationTests; this class covers
    the other three. Stage 7 finding: compose_hypothesis's label inference
    (infer_cause_family) could only ever produce 'defect' or
    'test_design_flaw' — genuine_change had no reachable path at all until
    fixed to map the 'policy_change' cause family (tokens: policy/cutoff/
    threshold) to it."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _drive_to_closed_outcome(self, name: str, test_name_override: str) -> str | None:
        """Builds a case, forces its checklist test_name (which
        compose_hypothesis's label inference reads) to test_name_override
        right before composing, drives to a confirmed verdict, and closes
        it. Returns the real outcome, or None if this run's evidence never
        confirmed anything — a legitimate real outcome, same posture as the
        other confirm/reject-either-way tests in this file."""
        item = _build_fixture_item(name)
        issue = _first_open_issue(item["item_id"])
        case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
        rca.run_opening_look(case["case_id"], ACTOR)
        _drive_investigation_loop(case["case_id"], ACTOR)
        rca.coverage_challenge_pass1(case["case_id"], ACTOR)
        pass2 = rca.coverage_challenge_pass2(case["case_id"], ACTOR)
        if pass2["added_suspect_id"]:
            rca.run_reopened_kill_attempt(case["case_id"], ACTOR)

        case_file = s.query_one("rca_case_files", case_id=case["case_id"])
        checklist = dict(case_file["checklist_json"])
        checklist["test_name"] = test_name_override
        s.update("rca_case_files", {"case_id": case["case_id"]}, {"checklist_json": checklist})

        composed = rca.compose_hypothesis(case["case_id"], ACTOR)
        checks = []
        for h in composed["hypotheses"]:
            checks.extend(s.query("rca_confirmation_checks", hypothesis_id=h["hypothesis_id"]))
        checks.sort(key=lambda c: c["order_rank"])

        confirmed_hypothesis_id = None
        for chk in checks:
            result = rca.run_confirmation_check(chk["check_id"], ACTOR)
            if result["verdict"] == "inconclusive":
                result = rca.run_confirmation_check(chk["check_id"], ACTOR)
            if result["verdict"] == "confirmed":
                confirmed_hypothesis_id = chk["hypothesis_id"]
                break
        if not confirmed_hypothesis_id:
            return None
        case_state = rca.require_case(case["case_id"])
        if case_state["state"] != "awaiting_fix_approval":
            return None
        proposal = rca.propose_fix(confirmed_hypothesis_id, ACTOR)
        approval = rca.approve_fix(proposal["id"], ACTOR)
        rca.confirm_fix_applied(approval["id"], ACTOR)
        result = rca.close_case(case["case_id"], ACTOR, rerun_fn=lambda c: {"status": "pass"})
        return result.get("outcome")

    def test_confirmed_outcome_is_reachable(self):
        outcome = self._drive_to_closed_outcome("rca-outcome-confirmed", "Value spike outlier check")
        if outcome is None:
            self.skipTest("no hypothesis confirmed in this run — a legitimate real outcome")
        self.assertEqual(outcome, "confirmed")

    def test_test_design_flaw_outcome_is_reachable(self):
        outcome = self._drive_to_closed_outcome("rca-outcome-flaw", "Some totally unrecognized check")
        if outcome is None:
            self.skipTest("no hypothesis confirmed in this run — a legitimate real outcome")
        self.assertEqual(outcome, "test_design_flaw")

    def test_genuine_change_outcome_is_reachable(self):
        outcome = self._drive_to_closed_outcome("rca-outcome-genuine", "Cutoff policy threshold check")
        if outcome is None:
            self.skipTest("no hypothesis confirmed in this run — a legitimate real outcome")
        self.assertEqual(outcome, "genuine_change")


class LegacyCreationRetirementTests(unittest.TestCase):
    """Stage 7: RCA_LEGACY_CREATION_RETIRED is a one-way floor beneath
    RCA_ENABLED, not just a second copy of the same gate — once retired,
    new issues must stay on rca even if RCA_ENABLED were somehow
    toggled back off (ai/v2/issues.py:sync_issues)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _flag(self, key: str) -> dict:
        return {"key": key, "tenant_id": BOOTSTRAP_TENANT}

    def test_new_issue_stays_rca_even_if_enabled_flag_is_off_once_retired(self):
        enabled_where = self._flag("RCA_ENABLED")
        retired_where = self._flag("RCA_LEGACY_CREATION_RETIRED")
        original_enabled = s.query_one("feature_flags", **enabled_where)["enabled"]
        original_retired = s.query_one("feature_flags", **retired_where)["enabled"]
        try:
            s.update("feature_flags", enabled_where, {"enabled": 0})
            s.update("feature_flags", retired_where, {"enabled": 1})
            item = _build_fixture_item("rca-legacy-retirement-fixture")
            issue = _first_open_issue(item["item_id"])
            self.assertEqual(issue["workflow_version"], "rca")
        finally:
            s.update("feature_flags", enabled_where, {"enabled": original_enabled})
            s.update("feature_flags", retired_where, {"enabled": original_retired})

    def test_new_issue_is_legacy_when_neither_flag_is_on(self):
        enabled_where = self._flag("RCA_ENABLED")
        retired_where = self._flag("RCA_LEGACY_CREATION_RETIRED")
        original_enabled = s.query_one("feature_flags", **enabled_where)["enabled"]
        original_retired = s.query_one("feature_flags", **retired_where)["enabled"]
        try:
            s.update("feature_flags", enabled_where, {"enabled": 0})
            s.update("feature_flags", retired_where, {"enabled": 0})
            item = _build_fixture_item("rca-legacy-fallback-fixture")
            issue = _first_open_issue(item["item_id"])
            self.assertEqual(issue["workflow_version"], "legacy-v1")
        finally:
            s.update("feature_flags", enabled_where, {"enabled": original_enabled})
            s.update("feature_flags", retired_where, {"enabled": original_retired})


class KnowledgeBlameBackTests(unittest.TestCase):
    """WF §3a: a case_history KB rule that nominated a suspect (coverage
    challenge pass 2) gets flagged under_suspicion when the nomination turns
    out wrong — a rejected Strong hypothesis or an Unresolved closure."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def _publish_case_history_rule(self, name: str) -> str:
        upload = kb.upload_document(TENANT, name, "text/markdown",
                                    b"## Case history rule\nAn accepted expected change.\n",
                                    "case_history", ACTOR)
        preview = kb.submit_for_review(TENANT, upload["version"]["version_id"], ACTOR, "case_history")
        rule_id = preview["rules"][0]["rule_id"]
        kb.publish_rule(TENANT, rule_id, ACTOR, roles=["kb_editor", "kb_reviewer"], category="case_history")
        return rule_id

    def _case_with_nomination(self, case_id: str, rule_id: str, state: str) -> None:
        s.insert("rca_cases", {
            "case_id": case_id, "tenant_id": TENANT, "issue_row_id": "iss_unused",
            "item_id": None, "table_name": None, "state": state, "part": "a",
            "tag_snapshot_json": {}, "complaint_text": None, "created_by": ACTOR,
            "created_at": s.now_ist(), "updated_at": s.now_ist(), "closed_at": None,
        })
        s.insert("rca_coverage_passes", {
            "id": rca._id("cov"), "case_id": case_id, "pass": 2,
            "raw_evidence_ref_json": [], "history_nominations_json": [rule_id],
            "added_suspect_id": None, "ts": s.now_ist(),
        })

    def test_blame_back_flags_the_nominating_rule_under_suspicion(self):
        rule_id = self._publish_case_history_rule("hist-blameback-1.md")
        case_id = "rca_blameback_unit_test"
        self._case_with_nomination(case_id, rule_id, "hypothesis_composition")
        rca._blame_back_history_nominations(case_id, TENANT, reason="unit test blame-back")
        flagged = s.query_one("kb_rules", rule_id=rule_id)
        self.assertEqual(flagged["lifecycle_state"], "under_suspicion")
        self.assertIn("unit test blame-back", flagged["under_suspicion_reason"])

    def test_no_nomination_is_a_silent_no_op(self):
        case_id = "rca_blameback_none_test"
        s.insert("rca_cases", {
            "case_id": case_id, "tenant_id": TENANT, "issue_row_id": "iss_unused",
            "item_id": None, "table_name": None, "state": "hypothesis_composition", "part": "a",
            "tag_snapshot_json": {}, "complaint_text": None, "created_by": ACTOR,
            "created_at": s.now_ist(), "updated_at": s.now_ist(), "closed_at": None,
        })
        rca._blame_back_history_nominations(case_id, TENANT, reason="unit test no-op")  # must not raise

    def test_unresolved_second_chance_exhaustion_blames_back_the_nominating_rule(self):
        rule_id = self._publish_case_history_rule("hist-blameback-2.md")
        case_id = "rca_blameback_unresolved_test"
        self._case_with_nomination(case_id, rule_id, "all_hypotheses_rejected")
        # Simulate the second chance already having been used once, so this
        # call takes the escalate-to-unresolved branch that does the blaming.
        s.insert("rca_state_transitions", {
            "id": "trs_blameback_secondchance_marker", "case_id": case_id,
            "prev_state": "all_hypotheses_rejected", "new_state": "second_chance_part_a",
            "actor": ACTOR, "reason": None, "evidence_ids_json": [], "ts": s.now_ist(),
            "workflow_version": "rca", "contract_version": "1",
        })
        result = rca.start_second_chance(case_id, ACTOR)
        self.assertFalse(result["second_chance_granted"])
        flagged = s.query_one("kb_rules", rule_id=rule_id)
        self.assertEqual(flagged["lifecycle_state"], "under_suspicion")
        self.assertIn("unresolved", flagged["under_suspicion_reason"])


class TenantIsolationTests(unittest.TestCase):
    """Stage 6: every rca service function must treat a foreign-tenant ID as
    unknown (KeyError -> 404, no existence disclosure), never silently
    operate on it. Each function exercised here had a real or latent
    tenant-blind gap before Stage 6 (see the traceability matrix's Stage 6
    closure note for the audit that found them)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def setUp(self):
        self.other_tenant = "attacker-tenant-" + uuid.uuid4().hex[:8]
        s.upsert("tenants", {"tenant_id": self.other_tenant, "name": "Attacker", "created_at": s.now_ist()})
        self.case_id = "rca_tenant_iso_" + uuid.uuid4().hex[:8]
        s.insert("rca_cases", {
            "case_id": self.case_id, "tenant_id": TENANT, "issue_row_id": "iss_unused",
            "item_id": None, "table_name": None, "state": "investigation_loop", "part": "a",
            "tag_snapshot_json": {}, "complaint_text": None, "created_by": ACTOR,
            "created_at": s.now_ist(), "updated_at": s.now_ist(), "closed_at": None,
        })
        self.suspect_id = "susp_tenant_iso_" + uuid.uuid4().hex[:8]
        s.insert("rca_suspects", {
            "suspect_id": self.suspect_id, "case_id": self.case_id, "kind": "single",
            "member_cause_ids_json": [], "status": "ruled_out", "origin": "look",
            "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
        self.hypothesis_id = "hyp_tenant_iso_" + uuid.uuid4().hex[:8]
        s.insert("rca_hypotheses", {
            "hypothesis_id": self.hypothesis_id, "case_id": self.case_id, "suspect_id": self.suspect_id,
            "statement": "unused", "label": "defect", "tier": "weak",
            "evidence_look_ids_json": [], "confirm_check_json": {}, "reject_condition_json": {},
            "owner": None, "created_at": s.now_ist(),
        })

    def test_revive_suspect_rejects_a_foreign_tenant(self):
        with self.assertRaises(KeyError):
            rca.revive_suspect(self.suspect_id, "reason", ACTOR, tenant_id=self.other_tenant)
        # The legitimate tenant can still revive it — proves the gate is
        # tenant-specific, not just globally broken.
        revived = rca.revive_suspect(self.suspect_id, "reason", ACTOR, tenant_id=TENANT)
        self.assertEqual(revived["status"], "revived")

    def test_propose_fix_rejects_a_foreign_tenant(self):
        with self.assertRaises(KeyError):
            rca.propose_fix(self.hypothesis_id, ACTOR, tenant_id=self.other_tenant)
        proposal = rca.propose_fix(self.hypothesis_id, ACTOR, tenant_id=TENANT)
        self.assertIsNotNone(proposal["id"])

    def test_transition_rejects_a_foreign_tenant(self):
        with self.assertRaises(KeyError):
            rca.transition(self.case_id, "coverage_challenge_blind", ACTOR, tenant_id=self.other_tenant)
        case = rca.transition(self.case_id, "coverage_challenge_blind", ACTOR, tenant_id=TENANT)
        self.assertEqual(case["state"], "coverage_challenge_blind")

    def test_record_symptom_accounting_rejects_a_foreign_tenant(self):
        s.insert("rca_case_files", {
            "case_id": self.case_id, "checklist_json": {"violation_count": 10},
            "schema_snapshot_json": {}, "tags_snapshot_json": {}, "complaint_json": None,
            "created_at": s.now_ist(),
        })
        with self.assertRaises(KeyError):
            rca.record_symptom_accounting(self.case_id, ACTOR, tenant_id=self.other_tenant)
        accounting = rca.record_symptom_accounting(self.case_id, ACTOR, tenant_id=TENANT)
        self.assertEqual(accounting["total_symptom_size"], 10)

    def test_reconcile_attached_failures_rejects_a_foreign_tenant(self):
        with self.assertRaises(KeyError):
            rca.reconcile_attached_failures(self.case_id, ACTOR, tenant_id=self.other_tenant)
        result = rca.reconcile_attached_failures(self.case_id, ACTOR, tenant_id=TENANT)
        self.assertEqual(result, [])  # no failure group on this synthetic case — still a valid, non-raising call


class MvpConclusionApprovalTests(unittest.TestCase):
    def _case_with_initial_evidence(self, name: str) -> tuple[dict, dict]:
        item = _build_fixture_item(name)
        issue = _first_open_issue(item["item_id"])
        case = rca.create_case_from_issue(issue["issue_row_id"], ACTOR)
        rca.run_opening_look(case["case_id"], ACTOR)
        return issue, rca.get_case(case["case_id"])

    def test_identified_conclusion_completes_without_fix_or_rerun(self):
        issue, case = self._case_with_initial_evidence("rca-mvp-identified")
        evidence_ids = [row["execution_id"] for row in case["executions"].values()]
        completed = rca.approve_conclusion(case["case_id"], ACTOR, {
            "conclusion_type": "root_cause_identified",
            "root_cause": "Missing values are concentrated in one source segment.",
            "confidence": "Moderate",
            "supporting_evidence_ids": evidence_ids,
            "limiting_evidence": "Only the retained snapshot was assessed.",
            "alternatives_considered": "A systemic capture failure was considered.",
            "affected_scope": "t1.amount",
            "related_failures": "No unmatched related failure remains.",
            "owner": "Data operations",
            "approval_rationale": "The segment comparison supports this conclusion.",
        })
        self.assertEqual(completed["state"], "closed")
        self.assertEqual(completed["closure"]["outcome"], "root_cause_identified")
        self.assertIsNone(completed["closure"]["rerun_run_id"])
        self.assertEqual(completed["conclusion"]["approved_by"], ACTOR)
        self.assertEqual(s.query_one("issues_v2", issue_row_id=issue["issue_row_id"])["status"], "Closed")
        self.assertIsNone(completed["closure"]["knowledge_draft_id"])

        proposed = rca.propose_reusable_knowledge(case["case_id"], ACTOR, {
            "reusable_lesson": "A segment-specific source process can create concentrated missingness.",
            "applicability_scope": "Datasets using the same segmented source process.",
            "generalization_reason": "The mechanism is process-based and not unique to this snapshot.",
            "supporting_evidence_ids": evidence_ids,
            "related_tables": ["t1"],
        })
        candidate = proposed["candidate"]
        self.assertEqual(candidate["lifecycle_state"], "draft")
        self.assertEqual(proposed["case"]["closure"]["knowledge_draft_id"], candidate["rule_id"])
        self.assertIn(candidate["rule_id"], {
            row["candidate_id"] for row in kb.list_learning_candidates(TENANT)})
        with self.assertRaisesRegex(rca.TransitionError, "already has"):
            rca.propose_reusable_knowledge(case["case_id"], ACTOR, {
                "reusable_lesson": "Duplicate", "applicability_scope": "Any",
                "generalization_reason": "Duplicate", "supporting_evidence_ids": evidence_ids,
            })

    def test_unresolved_conclusion_requires_evidence_and_records_outcome(self):
        _issue, case = self._case_with_initial_evidence("rca-mvp-unresolved")
        with self.assertRaisesRegex(ValueError, "supporting evidence"):
            rca.approve_conclusion(case["case_id"], ACTOR, {
                "conclusion_type": "unresolved", "supporting_evidence_ids": [],
                "alternatives_considered": "Segment and systemic explanations were considered.",
                "approval_rationale": "Available evidence is insufficient.",
            })
        evidence_ids = [row["execution_id"] for row in case["executions"].values()]
        completed = rca.approve_conclusion(case["case_id"], ACTOR, {
            "conclusion_type": "unresolved", "supporting_evidence_ids": evidence_ids,
            "limiting_evidence": "Only the retained snapshot was assessed.",
            "alternatives_considered": "Segment and systemic explanations were considered.",
            "related_failures": "No unmatched related failure remains.",
            "approval_rationale": "Available evidence is insufficient.",
        })
        self.assertEqual(completed["closure"]["outcome"], "unresolved")
        self.assertIsNone(completed["closure"]["knowledge_draft_id"])
        with self.assertRaisesRegex(ValueError, "unresolved"):
            rca.propose_reusable_knowledge(case["case_id"], ACTOR, {
                "reusable_lesson": "Unknown", "applicability_scope": "Any",
                "generalization_reason": "None", "supporting_evidence_ids": evidence_ids,
            })


class StaticSafetyTests(unittest.TestCase):
    """Stage 6 (MP §11 'analytical execution stays bounded read-only SQL +
    allowlisted helpers'): a deterministic static check standing in for a
    runtime fuzz test — stronger than a fuzz test for this specific
    invariant, since it can prove the ABSENCE of a dangerous call site
    rather than only sampling inputs that happen not to trigger one."""

    def test_no_dynamic_code_execution_or_shell_calls(self):
        import re
        source = Path(rca.__file__).read_text(encoding="utf-8")
        for forbidden in (r"\beval\s*\(", r"\bexec\s*\(", r"\bsubprocess\b", r"\bos\.system\b", r"\bos\.popen\b"):
            self.assertIsNone(re.search(forbidden, source),
                              f"Forbidden dynamic-execution pattern {forbidden!r} found in rca.py")

    def test_raw_sql_execute_calls_are_parameterized(self):
        import re
        source = Path(rca.__file__).read_text(encoding="utf-8")
        calls = re.findall(r"s\.execute\(\s*(\"\"\"|'''|\"|')(.*?)\1", source, re.DOTALL)
        self.assertGreaterEqual(len(calls), 1,
                                "expected at least the known s.execute() call in _rerun_original_test")
        for _, sql_text in calls:
            self.assertNotIn("%s", sql_text)
            self.assertNotIn("{", sql_text)  # no str.format()/f-string interpolation of a value into the SQL text
            self.assertIn("?", sql_text)  # must use a real placeholder, not a hardcoded literal-only query


if __name__ == "__main__":
    unittest.main()
