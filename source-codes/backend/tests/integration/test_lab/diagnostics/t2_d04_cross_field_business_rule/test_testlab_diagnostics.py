"""Phase 6 — the Test Lab API, run records and hand-offs (CFR-03..CFR-18).

Standalone-runnable (``python -m unittest tests.test_testlab_diagnostics``);
same SYSTEM_DB_PATH / KB_STORAGE_DIR / UPLOAD_DIR-at-import-time sandboxing
as tests/test_rca.py. Route functions are called directly (this repo's
established pattern — FastAPI's decorators return the undecorated callable,
so the real PLT-04 sanitizer and the real refusals are still exercised);
the one exception is the SSE endpoint, driven through a TestClient over a
throwaway app so the streamed frames are read exactly as a browser would.

6-T1  a KB published through the REAL parser reproduces the type-count split
      and the use-case subsets (the parity dimensions plan 6.7 claims — see
      docs/0.4.0/05-cross-field-contract.md §9; NO workbook parity is claimed).
6-T2  zero eligible rules -> NOT-APPLICABLE with a reason, never PASS.
6-T5  with role verification off (the default): a full run makes ZERO model
      calls, counted at runtime; an explicit opt-in is one bounded fake call.
6-T9  read-only: the dataset's tables hash identically before and after a run.
6-T10 the board returns exactly 9 cards with correct chips; a run request for
      a workflow-pending diagnostic is refused with the exact FWK-17 message.
6-T11 readiness is NOT-APPLICABLE (with its reason) before any run exists,
      when no KB is published for the item's scope.
6-T12 manifest edits become decision records; the FROZEN manifest re-derives
      exactly the persisted results (no drift between said and ran).
6-T15 a VIOLATION auto-opens an issue that can open an RCA case.
6-T16 the coverage summary names covered vs pending and carries NO weighted
      health-score field at all.
6-T17 SSE emits start/progress/done and never leaks a raw exception.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-testlab.db"
_TMP_KB = Path(tempfile.gettempdir()) / "archimedes-test-testlab-kb"
_TMP_UPLOADS = Path(tempfile.gettempdir()) / "archimedes-test-testlab-uploads"
_TMP_ITEMDB = Path(tempfile.gettempdir()) / "archimedes-test-testlab-itemdbs"
for _p in (_TMP_KB, _TMP_UPLOADS, _TMP_ITEMDB):
    if _p.exists():
        shutil.rmtree(_p)
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOADS)
os.environ["ITEM_DB_DIR"] = str(_TMP_ITEMDB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import kb  # noqa: E402
from domains.rca import service as rca  # noqa: E402
import system_db as s  # noqa: E402
from ai.v2 import issues as issues_svc  # noqa: E402
from ai.v2 import service as v2_service  # noqa: E402
from assets import staleness  # noqa: E402
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import manifest as manifest_mod  # noqa: E402
from dq_diagnostics import readiness as readiness_mod  # noqa: E402
from dq_diagnostics import register as register_mod  # noqa: E402
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import runner  # noqa: E402
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import binder  # noqa: E402
from routers import auth, v2, v3  # noqa: E402
from seeds import seed_framework_register  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
ACTOR = "tester"

# The same five-type rule table the binder suite uses, published here through
# the real upload -> submit_for_review -> publish_rule pipeline.
KB_MD = """## Cross-field rule catalogue

IRB (7 rules)

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| CF-01 | CRIT | Inequality | workout | exposure_at_default, drawn_balance | exposure_at_default >= drawn_balance (EAD cannot be below drawn balance) | CRR Art. 166 |
| CF-02 | CRIT | Date ordering | workout | possession_date, sale_date | possession_date <= sale_date | - |
| CF-03 | MATE | Identity/derivation | facility | original_ltv, original_balance, original_property_value | original_ltv == original_balance / original_property_value * 100 (+/- 0.5) | - |
| CF-04 | MINO | Domain/bound | workout | drawn_balance | drawn_balance >= 0 (logical bound) | - |
| CF-05 | MATE | Domain/bound | workout | workout_state | workout_state in {closed, open, written_off} | - |
| CF-06 | MATE | Domain/bound | facility | original_ltv | original_ltv in [0, 200] (logical bound) | - |
| CF-07 | MATE | Conditional | workout | workout_state, sale_date, cure_indicator | IF workout_state = closed THEN sale_date populated (exceptions: cure_indicator=1) | - |

IFRS9 (3 rules)

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| CF-08 | CRIT | Conditional | snapshot | days_past_due, ifrs9_stage | IF days_past_due > 90 THEN ifrs9_stage = 3 (90+ DPD backstop) | IFRS 9 5.5.11 |
| CF-09 | MINO | Conditional | snapshot | ifrs9_stage, days_past_due | IF ifrs9_stage in {2,3} THEN days_past_due present | - |
| CF-10 | MATE | Inequality | workout | exposure_at_default, original_balance | exposure_at_default <= original_balance * 1.5 (EAD bounded vs original) | CRR Art. 166 |

CF-07 - Cured workouts close without a property sale; no sale_date expected.
"""


def _bootstrap_once(*, enable_cross_field: bool = False) -> None:
    s.init_schema()
    seed_platform()
    seed_taxonomy()
    seed_framework_register()
    if enable_cross_field:
        # The production register keeps diagnostic 4 workflow-pending. These
        # implementation tests opt in locally so its dormant engine can still
        # be refined and regression-tested without exposing the workflow.
        s.update("diagnostic_register", {"diagnostic_id": 4}, {
            "workflow_status": "executable",
            "enabled_by": "test-only cross-field implementation fixture",
        })


def _wipe_kb() -> None:
    """Reset the knowledge base so a suite's starting state is explicit
    rather than inherited from whichever sibling ran first."""
    for table in ("kb_rules", "kb_sections", "kb_document_versions", "kb_documents",
                  "kb_retrieval_manifests"):
        for row in s.query(table):
            key = next(k for k in row if k.endswith("_id"))
            s.delete(table, **{key: row[key]})


def _publish_kb() -> str | None:
    """Upload -> parse -> bind -> publish, ONCE per sandboxed database.

    Every suite below calls this in setUpClass; publishing the same ten
    rules a second time would double the in-scope rule count and make the
    counts order-dependent, so it no-ops when a published bound rule set is
    already on file.
    """
    already = [r for r in s.query("kb_rules", tenant_id=TENANT, lifecycle_state="published")
               if r.get("binding_status") == "bound"]
    if already:
        return already[0]["version_id"]
    up = kb.upload_document(TENANT, "cf-rules.md", "text/markdown", KB_MD.encode("utf-8"),
                            None, ACTOR)
    version_id = up["version"]["version_id"]
    kb.submit_for_review(TENANT, version_id, ACTOR, "domain_fact")
    binder.bind_version(TENANT, version_id, ACTOR)
    for row in s.query("kb_rules", version_id=version_id):
        if row["binding_status"] == "bound":
            kb.publish_rule(TENANT, row["rule_id"], ACTOR, ["kb_reviewer"], "domain_fact")
    return version_id


def _dataset_frame() -> pd.DataFrame:
    """60 facilities; 10 CF-01 violations, ALL in region 'south' (20 rows of
    60) — the same known-concentration shape the engine suite computes the
    lift for by hand: (10/10) / (20/60) = 3.0 => CLUSTERED."""
    rows = []
    for i in range(1, 61):
        region = "north" if i <= 40 else "south"
        bad = region == "south" and i % 2 == 0
        rows.append({
            "facility_id": i, "region": region,
            "exposure_at_default": 80000.0 if bad else 100000.0,
            "drawn_balance": 90000.0,
            "original_balance": 100000.0, "original_property_value": 200000.0,
            "original_ltv": 50.0,
            "possession_date": "2024-01-10",
            "sale_date": "2024-03-15" if i % 3 else None,
            "workout_state": "closed" if i % 3 else "open",
            "cure_indicator": 1 if i % 3 == 0 else 0,
            "days_past_due": 120 if i % 10 == 0 else 5,
            "ifrs9_stage": 3 if i % 10 == 0 else 1,
        })
    return pd.DataFrame(rows)


def _make_item(name: str, use_case: str, frame: pd.DataFrame | None = None) -> str:
    item = v2_service.create_item("dataset", name)
    item_id = item["item_id"]
    frame = _dataset_frame() if frame is None else frame
    tmp = Path(tempfile.gettempdir()) / f"{name}-facilities.csv"
    frame.to_csv(tmp, index=False)
    v2_service.save_file(item_id, "data", "facilities.csv", tmp.read_bytes())
    v2_service.profile_item(item_id)
    v2_service.finalize_item(item_id, None, use_case)
    # Fresh upload intentionally clears intent fields (CTX-04). This
    # diagnostic fixture explicitly supplies a use case afterwards so its
    # existing scope assertions remain about diagnostic behaviour.
    s.update("dq_assets", {"asset_id": s.query_one("dq_items", item_id=item_id)["dataset_family_id"]},
             {"use_case": use_case})
    s.update("dq_items", {"item_id": item_id}, {"use_case": use_case})
    return item_id


def _tables_hash(item_id: str) -> str:
    """A content hash over every underlying table of an item (CFR-15)."""
    digest = hashlib.sha256()
    for row in s.query("dq_item_tables", item_id=item_id, order_by="table_name"):
        frame = v2_service._read_table(item_id, row["table_name"])
        digest.update(row["table_name"].encode())
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
#  6-T11 / 6-T2 — readiness and the NOT-APPLICABLE verdict with no KB
# ═══════════════════════════════════════════════════════════════════════════

class NoKnowledgeBaseTests(unittest.TestCase):
    """CFR-04 — a scope with nothing published behind it is NOT-APPLICABLE
    with a stated reason. Never PASS, never a generic failure.

    Starts by clearing the knowledge base so this suite's premise ("nothing
    is published") holds no matter which sibling suite ran first; the others
    re-publish through the idempotent ``_publish_kb``.
    """

    @classmethod
    def setUpClass(cls):
        _bootstrap_once(enable_cross_field=True)
        _wipe_kb()
        cls.item_id = _make_item("no-kb-item", "IRB / Basel")

    def test_readiness_is_not_applicable_before_anything_is_published(self):
        state = readiness_mod.readiness(self.item_id, 4, TENANT)
        self.assertEqual(state.status, "not_applicable")
        self.assertEqual(state.reason, "no knowledge base published for scope irb")

    def test_the_board_card_shows_that_reason_and_offers_no_run(self):
        board = v2.diagnostics_board(self.item_id)
        card = next(c for c in board["cards"] if c["diagnostic_id"] == 4)
        self.assertEqual(card["chip"]["status"], "not_applicable")
        self.assertIn("no knowledge base published", card["chip"]["reason"])
        self.assertFalse(card["can_run"])
        self.assertIsNone(card["last_run"])

    def test_building_a_manifest_is_refused_rather_than_producing_an_empty_pass(self):
        with self.assertRaises(HTTPException) as ctx:
            v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("no knowledge base published for scope irb", ctx.exception.detail)

    def test_an_empty_rule_set_verdicts_as_not_applicable_never_pass(self):
        from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule.result import StructuredResult
        empty = StructuredResult(preamble={"use_case": "irb"},
                                 rollup={"PASS": 0, "VIOLATION": 0, "NOT-APPLICABLE": 0},
                                 rules=[])
        verdict, reason = runner._dataset_verdict(empty)
        self.assertEqual(verdict, "not_applicable")
        self.assertIn("no knowledge base published for scope irb", reason)

    def test_a_draft_unpublished_rule_is_never_eligible(self):
        up = kb.upload_document(TENANT, "draft.md", "text/markdown",
                                KB_MD.encode("utf-8"), None, ACTOR)
        version_id = up["version"]["version_id"]
        kb.submit_for_review(TENANT, version_id, ACTOR, "domain_fact")
        binder.bind_version(TENANT, version_id, ACTOR)   # bound, but still draft
        rules, counts = readiness_mod.eligible_cross_field_rules(TENANT, "irb")
        self.assertEqual(rules, [])
        self.assertEqual(counts["published_bound"], 0)
        # cleanup so the later suites start from a clean KB
        for row in s.query("kb_rules", version_id=version_id):
            s.delete("kb_rules", rule_id=row["rule_id"])


# ═══════════════════════════════════════════════════════════════════════════
#  The happy path: board -> manifest -> run -> findings -> issue -> report
# ═══════════════════════════════════════════════════════════════════════════

class EndToEndRunTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _bootstrap_once(enable_cross_field=True)
        cls.version_id = _publish_kb()
        cls.item_id = _make_item("cf-run-item", "IRB / Basel")
        cls.hash_before = _tables_hash(cls.item_id)
        cls.manifest = v2.build_diagnostic_manifest(
            cls.item_id, v2.ManifestIn(diagnostic_id=4))
        cls.run_id = cls.manifest["run_id"]
        cls.done = v2.run_diagnostic_manifest(cls.run_id, v2.RunIn(stream=False))
        cls.results = v2.diagnostic_results(cls.item_id, cls.run_id)

    # --- 6-T1: rules in scope ----------------------------------------------
    def test_manifest_reports_the_rule_split_and_the_use_case_filter(self):
        summary = self.manifest["rules_summary"]
        # 10 rules published; the item's use case is IRB, so the 3 IFRS9-only
        # rules are filtered out by CFR-03's framework filter.
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["by_type"],
                         {"conditional": 1, "date_ordering": 1, "domain": 3,
                          "identity": 1, "inequality": 1})
        self.assertEqual(summary["by_severity"], {"CRITICAL": 2, "MATERIAL": 4, "MINOR": 1})
        self.assertEqual(summary["by_framework"], {"irb": 7})
        self.assertEqual(self.manifest["use_case"],
                         {"value": "irb", "source": "item use_case"})
        self.assertEqual(self.manifest["kb"]["counts"]["published_bound"], 10)
        self.assertEqual(self.manifest["kb"]["counts"]["filtered_out_by_framework"], 3)

    def test_manifest_carries_role_provenance_and_threshold_sources(self):
        for role, spec in self.manifest["roles"].items():
            self.assertIn(spec["via"], {"auto", "override", "unresolved"})
            self.assertTrue(spec["reason"])
            self.assertIsInstance(spec["score"], float)
        self.assertEqual(self.manifest["roles_summary"]["unresolved"], [])
        for key, spec in self.manifest["thresholds"].items():
            self.assertEqual(spec["source"], "default")
        self.assertEqual(self.manifest["thresholds"]["cluster_lift"]["value"], 3.0)
        self.assertFalse(self.manifest["role_verification"]["enabled"])

    def test_an_unattended_run_records_the_defaults_it_applied(self):
        kinds = [d["kind"] for d in manifest_mod.list_decisions(self.run_id)]
        self.assertIn("default_applied", kinds)

    # --- verdicts and findings ---------------------------------------------
    def test_the_run_produces_the_expected_verdict_and_rollup(self):
        self.assertEqual(self.done["phase"], "done")
        self.assertEqual(self.done["verdict"], "violation")
        self.assertEqual(self.done["rollup"],
                         {"PASS": 6, "VIOLATION": 1, "NOT-APPLICABLE": 0})

    def test_every_evaluated_rule_has_a_finding_row_not_just_the_failures(self):
        findings = self.results["results"][0]["findings"]
        self.assertEqual(len(findings), 7)
        self.assertEqual({f["outcome"] for f in findings}, {"PASS", "VIOLATION"})
        self.assertEqual(sorted(f["rule_id"] for f in findings),
                         ["CF-01", "CF-02", "CF-03", "CF-04", "CF-05", "CF-06", "CF-07"])

    def test_the_violation_finding_carries_evidence_pattern_and_provenance(self):
        finding = next(f for f in self.results["results"][0]["findings"]
                       if f["outcome"] == "VIOLATION")
        self.assertEqual(finding["rule_id"], "CF-01")
        self.assertEqual(finding["severity"], "CRITICAL")
        self.assertEqual(finding["violation_count"], 10)
        self.assertEqual(finding["pattern"], "CLUSTERED")
        self.assertIn("feed/segment fault", finding["pattern_detail"])
        self.assertEqual(finding["regulatory_ref"], "CRR Art. 166")
        self.assertLessEqual(len(finding["evidence_json"]), 5)
        self.assertEqual(list(finding["evidence_json"][0])[0], "facility_id")

    def test_the_result_row_is_decision_type_shaped(self):
        result = self.results["results"][0]
        self.assertEqual(result["decision_type"], "verdict")
        self.assertEqual(result["verdict"], "violation")
        self.assertIsNone(result["review_state"])
        self.assertIn("rows_evaluated", result["scope_counts_json"])
        self.assertIn("values", result["thresholds_used_json"])

    # --- 6-T9: read-only ----------------------------------------------------
    def test_the_dataset_is_byte_identical_after_the_run(self):
        self.assertEqual(_tables_hash(self.item_id), self.hash_before)

    # --- 6-T15: issue + RCA hand-off ---------------------------------------
    def test_a_violation_auto_opens_an_issue_keyed_by_diagnostic_and_rule(self):
        issues = s.query("issues_v2", item_id=self.item_id)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["diagnostic_id"], 4)
        self.assertEqual(issue["rule_id"], "CF-01")
        self.assertEqual(issue["run_id"], self.run_id)
        self.assertEqual(issue["criticality"], "Critical")     # CRITICAL -> Critical
        self.assertEqual(issue["status"], "Open")
        self.assertIsNotNone(issue["finding_id"])

    def test_a_passing_rule_never_opens_an_issue(self):
        rule_ids = {i["rule_id"] for i in s.query("issues_v2", item_id=self.item_id)}
        self.assertEqual(rule_ids, {"CF-01"})

    def test_re_running_updates_the_same_issue_rather_than_duplicating_it(self):
        second = v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        v2.run_diagnostic_manifest(second["run_id"], v2.RunIn(stream=False))
        issues = s.query("issues_v2", item_id=self.item_id)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["run_id"], second["run_id"])

    def test_the_issue_opens_an_rca_case_through_the_existing_v3_endpoint(self):
        issue = s.query("issues_v2", item_id=self.item_id)[0]
        s.upsert("users", {"username": "rca-user", "password": "pw", "name": "rca",
                           "email": "r@example.com", "function": "", "role": "",
                           "salutation": "", "call_name": "rca",
                           "ai_personality": "professional", "theme": "minimalist",
                           "authz_roles": ["user"], "tenant_id": TENANT})
        token = auth.login(auth.LoginRequest(username="rca-user", password="pw"))["token"]
        case = v3.create_rca_case(issue["issue_row_id"], authorization=f"Bearer {token}")
        self.assertEqual(case["issue_row_id"], issue["issue_row_id"])
        self.assertEqual(case["item_id"], self.item_id)
        self.assertIn(case["state"], {"opening_looks", "intake", "triage", "created"})
        self.assertIsNotNone(rca.require_case(case["case_id"], TENANT))

    # --- 6-T16: coverage honesty -------------------------------------------
    def test_coverage_summary_names_covered_and_pending_without_a_health_score(self):
        summary = v2.diagnostics_coverage_summary(self.item_id)
        self.assertEqual(summary["registered_total"], 9)
        self.assertEqual(summary["executable_total"], 6)
        self.assertEqual([d["diagnostic_id"] for d in summary["executable"]], [2, 4, 6, 8, 11, 14])
        self.assertEqual(len(summary["workflow_pending"]), 3)
        self.assertEqual([r["diagnostic_id"] for r in summary["ran"]], [4])
        self.assertIn("VIOLATION", summary["verdict_rollup"])
        self.assertIn("CRITICAL", summary["by_severity"])
        self.assertTrue(summary["coverage_statement"])
        # The absence is the assertion: no weighted score field exists at
        # all, so a frontend structurally cannot render a fabricated number.
        blob = json.dumps(summary).lower()
        for banned in ("health_score", "weighted_score", "final_score", "score_value"):
            self.assertNotIn(banned, blob)

    # --- report -------------------------------------------------------------
    def test_the_report_derives_from_the_persisted_structured_result(self):
        text = runner.report_text(self.run_id)
        self.assertIn("CROSS-FIELD BUSINESS RULE TEST", text)
        self.assertIn("CF-01", text)
        self.assertIn("CLUSTERED", text)
        self.assertTrue(runner.report_pdf(self.run_id).startswith(b"%PDF"))

    # --- disposition --------------------------------------------------------
    def test_dismissing_a_finding_requires_a_reason(self):
        finding = self.results["results"][0]["findings"][0]
        with self.assertRaises(HTTPException) as ctx:
            v2.disposition_finding(finding["finding_id"],
                                   v2.DispositionIn(action="dismiss", reason="  "))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("rationale is required", ctx.exception.detail)

    def test_a_disposition_is_recorded_append_only_with_its_review_state(self):
        finding = self.results["results"][0]["findings"][1]
        out = v2.disposition_finding(finding["finding_id"],
                                     v2.DispositionIn(action="confirm_issue", reason="Confirmed after evidence review"))
        self.assertEqual(out["review_state"], "confirmed")
        rows = s.query("diag_dispositions", target_type="finding",
                       target_id=finding["finding_id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "confirm_issue")

    def test_z_full_replacement_preserves_real_finding_disposition_and_issue_as_stale(self):
        """R-09/M-11: the real upload/run/issue path is never recomputed away."""
        finding = next(f for f in self.results["results"][0]["findings"]
                       if f["outcome"] == "VIOLATION")
        v2.disposition_finding(finding["finding_id"],
                               v2.DispositionIn(action="confirm_issue", reason="Confirmed after evidence review"))
        finding_before = s.query_one("diag_findings", finding_id=finding["finding_id"])
        issue_before = s.query_one("issues_v2", item_id=self.item_id, rule_id="CF-01")
        disposition_before = s.query_one("diag_dispositions", target_type="finding",
                                          target_id=finding["finding_id"])
        replacement = v2_service.reupload_item(
            self.item_id, intent="full_replacement", snapshot_label="negative full replacement")

        self.assertEqual(finding_before,
                         s.query_one("diag_findings", finding_id=finding["finding_id"]))
        self.assertEqual(issue_before,
                         s.query_one("issues_v2", issue_row_id=issue_before["issue_row_id"]))
        self.assertEqual(disposition_before,
                         s.query_one("diag_dispositions", id=disposition_before["id"]))
        self.assertEqual(s.query_one("dq_items", item_id=self.item_id)["snapshot_status"], "superseded")
        self.assertEqual(s.query_one("dq_items", item_id=replacement["item_id"])["snapshot_status"], "active")
        views = staleness.annotate_stale([
            {**finding_before, "run_id": self.run_id},
            {**disposition_before},
            {**issue_before},
        ])
        self.assertTrue(all(view["stale"] for view in views))
        self.assertEqual(views[0]["stale_sources"][0]["snapshot_id"], self.item_id)


# ═══════════════════════════════════════════════════════════════════════════
#  6-T10 — the coverage board and the workflow-pending refusal
# ═══════════════════════════════════════════════════════════════════════════

class CoverageBoardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _bootstrap_once()
        cls.item_id = _make_item("board-item", "IRB / Basel")

    def test_the_board_returns_exactly_nine_cards_with_the_full_anatomy(self):
        board = v2.diagnostics_board(self.item_id)
        self.assertEqual(len(board["cards"]), 9)
        self.assertEqual({c["diagnostic_id"] for c in board["cards"]},
                         {2, 4, 6, 8, 11, 12, 14, 17, 20})
        for card in board["cards"]:
            for field in ("name", "area", "mode", "stage", "det_stat", "decision_type",
                          "kb_dependency", "workflow_status", "chip", "can_run", "last_run",
                          "run_count", "run_counts", "recent_runs"):
                self.assertIn(field, card)

    def test_the_board_shell_and_cards_can_load_progressively(self):
        summary = v2.diagnostics_board_summary(self.item_id)
        self.assertEqual(len(summary["cards"]), 9)
        self.assertTrue(all(card["loading"] for card in summary["cards"]))
        self.assertTrue(all("chip" not in card for card in summary["cards"]))

        card = v2.diagnostics_board_card(self.item_id, 6)
        self.assertEqual(card["diagnostic_id"], 6)
        self.assertFalse(card["loading"])
        self.assertIn("chip", card)
        self.assertIn("recent_runs", card)

    def test_each_card_exposes_latest_completed_and_full_immutable_run_history(self):
        item_id = _make_item(f"history-{uuid.uuid4().hex[:8]}", "IRB / Basel")
        now = s.now_ist()
        run_ids = []
        for index, status in enumerate(("done", "failed", "draft")):
            run_id = f"drun_history_{uuid.uuid4().hex[:8]}"; run_ids.append(run_id)
            s.insert("diag_runs", {"run_id": run_id, "item_id": item_id, "diagnostic_id": 14,
                "manifest_json": {"manifest_fingerprint": f"fp-{index}"}, "status": status,
                "created_at": f"2026-08-20T00:00:0{index}+05:30", "started_at": now,
                "finished_at": now if status != "draft" else None})
            if status != "draft":
                s.insert("diag_results", {"result_id": f"dres_{uuid.uuid4().hex[:10]}",
                    "run_id": run_id, "diagnostic_id": 14, "entity_or_table": "portfolio",
                    "decision_type": "verdict", "verdict": "pass" if status == "done" else None,
                    "review_state": "open", "metrics_json": {"rollup": {"PASS": index + 1}},
                    "thresholds_used_json": {}, "scope_counts_json": {}, "na_reason": None,
                    "created_at": now})
        board = v2.diagnostics_board(item_id)
        card = next(row for row in board["cards"] if row["diagnostic_id"] == 14)
        self.assertEqual(card["run_count"], 3)
        self.assertEqual(card["run_counts"], {"draft": 1, "failed": 1, "done": 1})
        self.assertEqual(card["last_run"]["run_id"], run_ids[0])
        history = v2.diagnostic_run_history(item_id, 14)
        self.assertEqual(history["total"], 3)
        self.assertEqual([row["status"] for row in history["runs"]], ["draft", "failed", "done"])
        self.assertTrue(history["runs"][1]["has_results"])

    def test_selected_item_summary_separates_pending_reviews_from_issue_status(self):
        item_id = _make_item(f"summary-{uuid.uuid4().hex[:8]}", "IRB / Basel")
        now = s.now_ist()
        old_run = f"drun_old_{uuid.uuid4().hex[:8]}"
        latest_run = f"drun_latest_{uuid.uuid4().hex[:8]}"
        for run_id, created_at in ((old_run, "2026-08-20T00:00:00+05:30"),
                                   (latest_run, "2026-08-21T00:00:00+05:30")):
            s.insert("diag_runs", {"run_id": run_id, "item_id": item_id,
                "diagnostic_id": 14, "manifest_json": {}, "status": "done",
                "created_at": created_at, "started_at": created_at,
                "finished_at": created_at})
            result_id = f"dres_{uuid.uuid4().hex[:10]}"
            s.insert("diag_results", {"result_id": result_id, "run_id": run_id,
                "diagnostic_id": 14, "entity_or_table": "portfolio",
                "decision_type": "contextual", "review_state": "open",
                "metrics_json": {}, "thresholds_used_json": {},
                "scope_counts_json": {}, "created_at": now})
            for index, review_state in enumerate(("open", "confirmed")):
                s.insert("diag_findings", {"finding_id": f"dfnd_{uuid.uuid4().hex[:10]}",
                    "result_id": result_id, "run_id": run_id,
                    "rule_id": f"rule-{index}", "outcome": "CONTEXTUAL",
                    "review_state": review_state, "seq": index, "created_at": now})
        for index, status in enumerate(("Open", "In RCA", "Escalated", "Closed")):
            s.insert("issues_v2", {"issue_row_id": f"iss_{uuid.uuid4().hex[:10]}",
                "item_id": item_id, "table_name": "portfolio",
                "test_name": f"test-{index}", "status": status,
                "created_at": now, "updated_at": now})

        summary = issues_svc.list_issues(item_id)["summary"]
        self.assertEqual(summary, {
            "review_needed": 1, "open": 1, "in_review": 2, "closed": 1,
        })

    def test_shared_result_projection_hydrates_closed_issue_for_any_diagnostic(self):
        item_id = _make_item(f"issue-hydration-{uuid.uuid4().hex[:8]}", "IRB / Basel")
        now = s.now_ist()
        run_id = f"drun_{uuid.uuid4().hex[:10]}"
        result_id = f"dres_{uuid.uuid4().hex[:10]}"
        finding_id = f"dfnd_{uuid.uuid4().hex[:10]}"
        issue_id = f"iss_{uuid.uuid4().hex[:10]}"
        s.insert("diag_runs", {"run_id": run_id, "item_id": item_id,
            "diagnostic_id": 4, "manifest_json": {}, "status": "done",
            "created_at": now, "started_at": now, "finished_at": now})
        s.insert("diag_results", {"result_id": result_id, "run_id": run_id,
            "diagnostic_id": 4, "entity_or_table": "portfolio",
            "decision_type": "verdict", "verdict": "violation",
            "review_state": None, "metrics_json": {}, "thresholds_used_json": {},
            "scope_counts_json": {}, "created_at": now})
        s.insert("diag_findings", {"finding_id": finding_id, "result_id": result_id,
            "run_id": run_id, "rule_id": "rule-1", "outcome": "VIOLATION",
            "review_state": "confirmed", "seq": 0, "created_at": now})
        s.insert("issues_v2", {"issue_row_id": issue_id, "item_id": item_id,
            "table_name": "portfolio", "test_name": "rule-1", "status": "Closed",
            "finding_id": finding_id, "diagnostic_id": 4,
            "created_at": now, "updated_at": now})

        finding = runner.run_results(run_id)["results"][0]["findings"][0]
        self.assertEqual(finding["existing_issue"], {
            "issue_row_id": issue_id, "finding_id": finding_id,
            "status": "Closed", "same_finding": True,
        })

    def test_five_diagnostics_are_enabled_and_the_other_four_are_pending(self):
        board = v2.diagnostics_board(self.item_id)
        pending = [c for c in board["cards"] if c["chip"]["status"] == "workflow_pending"]
        self.assertEqual(len(pending), 4)
        for card in pending:
            self.assertFalse(card["can_run"])
            self.assertEqual(card["chip"]["reason"], register_mod.REFUSAL_WORKFLOW_PENDING)
            self.assertEqual(card["chip"]["reason"], "workflow not yet defined")

    def test_the_board_carries_the_area_level_gap_strip(self):
        board = v2.diagnostics_board(self.item_id)
        self.assertTrue(board["gap_areas"])
        for area in board["gap_areas"]:
            self.assertTrue(area["reason"])

    def test_a_manifest_for_a_pending_diagnostic_is_refused_with_the_exact_message(self):
        with self.assertRaises(HTTPException) as ctx:
            v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "workflow not yet defined")

    def test_an_unregistered_diagnostic_id_is_a_404_not_a_pending_refusal(self):
        with self.assertRaises(HTTPException) as ctx:
            v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=999))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_run_request_for_a_pending_diagnostic_is_refused(self):
        """The refusal is enforced at run time too, not only at manifest
        time — a run row hand-crafted for a pending diagnostic still cannot
        execute."""
        run_id = "drun_pending_cross_field_fixture"
        s.insert("diag_runs", {
            "run_id": run_id, "item_id": self.item_id, "diagnostic_id": 4,
            "manifest_json": {"run_id": run_id, "rules": []}, "status": "draft",
            "engine_versions_json": {}, "created_at": s.now_ist(),
            "started_at": None, "finished_at": None,
        })
        with self.assertRaises(HTTPException) as ctx:
            v2.run_diagnostic_manifest(run_id, v2.RunIn(stream=False))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "workflow not yet defined")


# ═══════════════════════════════════════════════════════════════════════════
#  6-T12 — decision records and manifest fidelity
# ═══════════════════════════════════════════════════════════════════════════

class ManifestDecisionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _bootstrap_once(enable_cross_field=True)
        _publish_kb()
        cls.item_id = _make_item("manifest-item", "IRB / Basel")

    def setUp(self):
        self.manifest = v2.build_diagnostic_manifest(
            self.item_id, v2.ManifestIn(diagnostic_id=4))
        self.run_id = self.manifest["run_id"]

    def test_a_role_override_writes_a_decision_record_and_wins_the_ladder(self):
        patched = v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
            kind="role_override", role="drawn_balance", column="facilities.original_balance"))
        self.assertEqual(patched["roles"]["drawn_balance"]["column"], "original_balance")
        self.assertEqual(patched["roles"]["drawn_balance"]["via"], "override")
        self.assertEqual(patched["roles_summary"]["override"], 1)
        decision = [d for d in manifest_mod.list_decisions(self.run_id)
                    if d["kind"] == "role_override"]
        self.assertEqual(len(decision), 1)
        self.assertEqual(decision[0]["payload_json"]["role"], "drawn_balance")
        self.assertEqual(decision[0]["payload_json"]["before"]["column"], "drawn_balance")

    def test_a_threshold_tune_writes_a_decision_record_and_changes_the_source(self):
        patched = v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
            kind="threshold_tune", key="cluster_lift", value=1.5))
        self.assertEqual(patched["thresholds"]["cluster_lift"]["value"], 1.5)
        self.assertIn("user-set", patched["thresholds"]["cluster_lift"]["source"])
        decision = [d for d in manifest_mod.list_decisions(self.run_id)
                    if d["kind"] == "threshold_tune"][0]
        self.assertEqual(decision["payload_json"]["before"]["source"], "default")

    def test_a_scope_exclusion_requires_a_reason_and_is_recorded(self):
        with self.assertRaises(HTTPException):
            v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
                kind="scope_exclusion", table="facilities", reason="  "))
        patched = v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
            kind="scope_exclusion", table="facilities", reason="stale delivery"))
        self.assertEqual(patched["scope"]["tables"], [])
        self.assertEqual(patched["scope"]["excluded_tables"][0]["reason"], "stale delivery")
        self.assertTrue([d for d in manifest_mod.list_decisions(self.run_id)
                         if d["kind"] == "scope_exclusion"])

    def test_opt_in_verification_is_one_bounded_call_and_is_advisory(self):
        """6-T5/7.2: off makes no call; explicit on makes one fake-only call.

        The fake returns one disagreement.  It is recorded append-only, but
        the advisory policy leaves executable mappings (and therefore the
        deterministic verdict bytes) untouched.
        """
        import ai.llm as llm
        off = manifest_mod.get_manifest(self.run_id)
        off_result = runner.evaluate_manifest(off)
        while True:
            try:
                next(off_result)
            except StopIteration as stop:
                off_structured = stop.value
                break

        calls = {"n": 0, "payload": None, "kwargs": None}

        class _FakeCompletions:
            def create(_self, **kwargs):
                calls["n"] += 1
                calls["kwargs"] = kwargs
                sent = json.loads(kwargs["messages"][1]["content"])
                calls["payload"] = sent
                decisions = []
                for index, entry in enumerate(sent["roles"]):
                    current = entry["deterministic_mapping"]
                    proposed = current
                    decision = "keep"
                    if index == 0:
                        proposed = next(c for c in entry["candidates"] if c != {
                            "table": current["table"], "column": current["column"]})
                        decision = "llm"
                    elif index == 1:
                        decision = "manual"
                    decisions.append({"role": entry["role"], "decision": decision,
                                      "table": proposed["table"], "column": proposed["column"]})
                message = type("Message", (), {"content": json.dumps({"decisions": decisions})})()
                return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

        class _FakeClient:
            chat = type("Chat", (), {"completions": _FakeCompletions()})()

        original = llm.get_client
        original_model = llm.get_model
        llm.get_client = lambda *_a, **_k: _FakeClient()
        llm.get_model = lambda: "test-deployment"
        try:
            patched = v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
                kind="role_verification_change", enabled=True))
        finally:
            llm.get_client = original
            llm.get_model = original_model

        self.assertEqual(calls["n"], 1)
        self.assertEqual(calls["kwargs"]["model"], "test-deployment")
        self.assertEqual(calls["kwargs"]["max_completion_tokens"], 1200)
        self.assertNotIn("max_tokens", calls["kwargs"])
        self.assertEqual(set(calls["payload"]), {"schema_version", "policy", "roles"})
        self.assertNotIn("rows", json.dumps(calls["payload"]).lower())
        self.assertTrue(patched["role_verification"]["enabled"])
        decisions = [d for d in manifest_mod.list_decisions(self.run_id)
                     if d["kind"] == "role_verification_change" and
                     d["payload_json"].get("event") == "decision"]
        self.assertEqual(len(decisions), patched["role_verification"]["roles_sent"])
        self.assertEqual({d["payload_json"]["outcome"] for d in decisions},
                         {"keep", "llm", "manual"})
        self.assertFalse([d for d in decisions if d["payload_json"]["outcome"] == "llm"][0]
                         ["payload_json"]["mapping_applied"])
        self.assertEqual(patched["roles"], off["roles"])
        frozen = manifest_mod.freeze(self.run_id, ACTOR)
        self.assertTrue(frozen["role_verification"]["enabled"])
        on_result = runner.evaluate_manifest(frozen)
        while True:
            try:
                next(on_result)
            except StopIteration as stop:
                # Verification provenance deliberately changes the preamble,
                # but the complete rule/verdict payload is byte-identical.
                off_verdict = json.dumps({"rollup": off_structured.rollup,
                                          "rules": off_structured.rules},
                                         sort_keys=True, separators=(",", ":"))
                on_verdict = json.dumps({"rollup": stop.value.rollup,
                                         "rules": stop.value.rules},
                                        sort_keys=True, separators=(",", ":"))
                self.assertEqual(off_verdict, on_verdict)
                break

    def test_verifier_rejects_noncanonical_keep_and_out_of_candidate_mappings(self):
        payload = {"roles": [{
            "role": "r", "deterministic_mapping": {"table": "t", "column": "c"},
            "candidates": [{"table": "t", "column": "c"}],
        }]}
        for decision in (
            {"role": "r", "decision": "keep", "table": "t", "column": "other"},
            {"role": "r", "decision": "llm", "table": "t", "column": "other"},
        ):
            with self.assertRaises(manifest_mod.ManifestError):
                manifest_mod._strict_verification_decisions(json.dumps({"decisions": [decision]}), payload)

    def test_a_frozen_manifest_cannot_be_edited(self):
        manifest_mod.freeze(self.run_id, ACTOR)
        with self.assertRaises(HTTPException) as ctx:
            v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
                kind="threshold_tune", key="tolerance", value=0.5))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("frozen", ctx.exception.detail)

    def test_the_frozen_manifest_alone_re_derives_exactly_what_ran(self):
        """6-T12's core claim: no drift between what the scope gate said
        would run and what actually ran. Re-execute from the persisted
        manifest_json ONLY and compare against the stored result."""
        v2.run_diagnostic_manifest(self.run_id, v2.RunIn(stream=False))
        persisted = runner.structured_result(self.run_id)
        frozen = manifest_mod.get_manifest(self.run_id)
        replay = manifest_mod.get_run(self.run_id)["manifest_json"]
        self.assertEqual(json.dumps(frozen, sort_keys=True, default=str),
                         json.dumps(replay, sort_keys=True, default=str))
        generator = runner.evaluate_manifest(replay)
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                rerun = stop.value
                break
        self.assertEqual(persisted.to_json(), rerun.to_json())

    def test_a_tuned_threshold_actually_reaches_the_engine(self):
        v2.patch_diagnostic_manifest(self.run_id, v2.ManifestPatch(
            kind="threshold_tune", key="tolerance", value=0.5))
        v2.run_diagnostic_manifest(self.run_id, v2.RunIn(stream=False))
        structured = runner.structured_result(self.run_id)
        self.assertEqual(structured.preamble["parameters"]["tolerance"], 0.5)
        # a 16.7% violation rate now sits under the tuned tolerance
        self.assertEqual(structured.rollup["VIOLATION"], 0)
        self.assertEqual(structured.rollup["PASS"], 7)


# ═══════════════════════════════════════════════════════════════════════════
#  6-T5 / 6-T17 — zero model calls, and the SSE contract
# ═══════════════════════════════════════════════════════════════════════════

class StreamAndModelCallTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _bootstrap_once(enable_cross_field=True)
        _publish_kb()
        cls.item_id = _make_item("stream-item", "IRB / Basel")
        cls.app = FastAPI()
        cls.app.include_router(v2.router)
        cls.client = TestClient(cls.app)

    def _frames(self, run_id: str) -> list[dict]:
        response = self.client.get(f"/api/v2/diagnostics/runs/{run_id}/stream")
        self.assertEqual(response.status_code, 200)
        return [json.loads(line[len("data: "):])
                for line in response.text.splitlines() if line.startswith("data: ")]

    def test_sse_emits_start_progress_and_done(self):
        manifest = v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        v2.run_diagnostic_manifest(manifest["run_id"], v2.RunIn(stream=True))
        frames = self._frames(manifest["run_id"])
        phases = [f["phase"] for f in frames]
        self.assertEqual(phases[0], "start")
        self.assertEqual(phases[-1], "done")
        progress = [f for f in frames if f["phase"] == "progress"]
        self.assertEqual(len(progress), 7)
        self.assertEqual(progress[-1]["done"], progress[-1]["total"])
        self.assertEqual(frames[-1]["rollup"]["VIOLATION"], 1)

    def test_the_stream_never_leaks_a_raw_exception(self):
        manifest = v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        run_id = manifest["run_id"]
        original = runner.run

        def _boom(*args, **kwargs):
            raise RuntimeError("secret internal detail: /etc/passwd")
            yield  # pragma: no cover - generator marker

        runner.run = _boom
        try:
            frames = self._frames(run_id)
        finally:
            runner.run = original
        error = [f for f in frames if f["phase"] == "error"]
        self.assertEqual(len(error), 1)
        self.assertEqual(error[0]["thought"], "internal error")
        for frame in frames:
            self.assertNotIn("secret internal detail", json.dumps(frame))
            self.assertNotIn("RuntimeError", json.dumps(frame))
        self.assertEqual(s.query_one("diag_runs", run_id=run_id)["status"], "failed")

    def test_an_unknown_run_streams_a_clean_error_frame(self):
        frames = self._frames("drun_does_not_exist")
        self.assertEqual(frames[0]["phase"], "error")

    def test_a_completed_run_is_replayed_not_re_executed(self):
        manifest = v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
        run_id = manifest["run_id"]
        v2.run_diagnostic_manifest(run_id, v2.RunIn(stream=False))
        before = len(s.query("diag_findings", run_id=run_id))
        frames = self._frames(run_id)
        self.assertEqual(frames[-1]["phase"], "done")
        self.assertEqual(len(s.query("diag_findings", run_id=run_id)), before)

    def test_a_full_run_makes_zero_model_calls_counted(self):
        """6-T5 — verification is off by default, so the run path
        cannot reach a model client."""
        import ai.llm as llm
        calls = {"n": 0}
        original_client, original_model = llm.get_client, llm.get_model

        def _count_client(*args, **kwargs):
            calls["n"] += 1
            raise AssertionError("the cross-field run path reached a model client")

        def _count_model(*args, **kwargs):
            calls["n"] += 1
            raise AssertionError("the cross-field run path reached a model client")

        llm.get_client, llm.get_model = _count_client, _count_model
        try:
            manifest = v2.build_diagnostic_manifest(self.item_id, v2.ManifestIn(diagnostic_id=4))
            v2.run_diagnostic_manifest(manifest["run_id"], v2.RunIn(stream=False))
            v2.diagnostic_results(self.item_id, manifest["run_id"])
            v2.diagnostics_coverage_summary(self.item_id)
            v2.diagnostics_board(self.item_id)
        finally:
            llm.get_client, llm.get_model = original_client, original_model
        self.assertEqual(calls["n"], 0)


if __name__ == "__main__":
    unittest.main()
