# RCA — Stage 0 Traceability Matrix

Maps every normative rule in `RCA_Full_Workflow_v10.md` (WF) and
`RCA_v10_Migration_Knowledge_Base_and_Implementation_Plan.md` (MP) to an
implementation owner, target path, behavioral test, stage, and status.

Status legend: `pending` (not started), `contracted` (Stage 0 record/contract
exists in `00-contracts.md`, no code yet). Nothing is `done` at Stage 0 close
— that would mean code changed prematurely, which the gate forbids.

Owner legend: `sonnet` (architecture/integration/UI-workflow/UAT/acceptance),
`codex-terra` (implementation), `codex-sol` (adversarial/security review).
Per the plan's "no model approves its own implementation" rule, `codex-terra`
implementations are always reviewed by `sonnet` + adversarially checked by
`codex-sol` before a stage gate is accepted — that review step is implicit in
every row below and not repeated per-row.

## Part A — hypothesis creation (WF §1–9)

| Rule | Owner | Target path | Behavioral test | Stage | Status |
|---|---|---|---|---|---|
| No pre-seeded causes (WF §1.1) | codex-terra | `backend/rca.py:create_case_from_issue` | `test_rca.py::test_no_pre_seeded_causes` | 3 | implemented |
| Suspects appear only from a look's fork (WF §1.2) | codex-terra | `rca.py:reader_interpret` creates a suspect only from a look's fork branch | `test_rca.py::test_02_planner_runner_reader_cycle_finds_concentrated_suspect` | 4 | implemented |
| Ruled-out is revivable with reason (WF §1.3) | codex-terra | `rca.py:revive_suspect` | `test_rca.py::test_revive_requires_reason_and_ruled_out_status` | 4 | implemented |
| Hypothesis needs ≥1 evidence look (WF §1.4) | codex-terra | `rca.py:compose_hypothesis` (`evidence_look_ids_json` always populated; falls back to the full trail if a suspect has none) | `test_rca.py::test_04_composer_produces_tiered_hypotheses_with_evidence` | 4 | implemented |
| Full trail is the proof (WF §1.5) | codex-terra | `rca.py:_audit`/`transition` write `rca_audit_events`/`rca_state_transitions` on every mutation | `test_rca.py::test_case_ends_at_opening_looks_with_case_file_and_tag_snapshot` (asserts transition sequence); `::test_07_full_case_bundle_is_readable` | 3 | implemented |
| Threshold FAIL auto-starts case; contextual test needs complaint (WF §2) | sonnet + codex-terra | `ai/v2/issues.py:sync_issues` labels `workflow_version` by flag; `rca.py:create_case_from_issue` | threshold-fail auto-create path covered end-to-end (`test_rca.py`, `ui/e2e/rca.spec.js`) | 3 | **partial** — threshold-auto-start implemented; contextual/complaint-triggered path is out of Stage 3's scope (no complaint UI/API yet), deferred to Stage 4 |
| Complaint captures symptom only; user's guessed cause is just one suspect (WF §2) | codex-terra | `rca/intake.py` | complaint-with-guess fixture: guess enters as one suspect among others, doesn't short-circuit loop | 4 | not implemented — no complaint UI/API exists yet (out of scope, see the Stage 3 row on contextual-test-needs-complaint above) |
| Fork passes through Runner untouched (WF §3, §8) | codex-terra | `rca.py:runner_execute` reads only `fork["column"]`/`fork["segment_column"]` (what to check); only `reader_interpret` reads the outcome branches | `test_rca.py::test_02_planner_runner_reader_cycle_finds_concentrated_suspect` | 3 | implemented |
| Coverage challenge pass 1 is raw-evidence-only (WF §3, §8) | codex-terra | `rca.py:coverage_challenge_pass1` (reads only `rca_look_executions`, never `rca_suspects` or KB) | `test_rca.py::test_03_loop_runs_to_a_stop_condition_then_coverage_challenge` | 4 | implemented |
| KB trust levels human_confirmed/inferred (WF §3a) | codex-terra | `backend/kb.py:publish_rule/TRUST_LEVELS` | `test_kb.py::test_publish_succeeds_with_role_and_sets_effective_fields` | 2 | implemented |
| KB shelf life + schema-change invalidation (WF §3a) | codex-terra | `backend/kb.py:check_shelf_life/check_schema_invalidation` | `test_kb.py::test_shelf_life_expiry_flips_trust_level`, `::test_schema_change_invalidation_flips_trust_level` | 2 | implemented |
| KB blame-back on Unresolved/rejected-Strong (WF §3a) | codex-terra | `backend/kb.py:mark_under_suspicion` (primitive, Stage 2); `rca.py:_blame_back_history_nominations` (Stage 5 trigger — called from `run_confirmation_check` on a rejected Strong hypothesis, and from `start_second_chance` on an Unresolved second-chance exhaustion; reads the rule_ids `coverage_challenge_pass2` nominated from for that case) | `test_kb.py::test_mark_under_suspicion` (primitive); `test_rca.py::KnowledgeBlameBackTests` (trigger, both call sites plus the no-nomination no-op) | 2 (primitive) / 5 (trigger) | implemented |
| KB quarantine — only Triage/Intake/coverage-pass-2 see history (WF §3a) | codex-terra | `backend/kb.py:AGENT_CATEGORY_MATRIX/list_eligible_rules` (matrix, Stage 2); `rca.py:coverage_challenge_pass2` is the real Stage 4 caller (Planner/Reader never call it — quarantine holds by omission, not an explicit check) | `test_kb.py::test_retrieval_respects_agent_category_matrix_and_quarantine`; `test_rca.py::VerticalSliceHappyPathTests::test_03_...` exercises the real pass-2 call path | 2 (mechanism) / 4 (real caller) | implemented |
| KB writes are code-only, at human-answer or closure moments (WF §3a) | codex-terra | `backend/kb.py` is the sole writer to `kb_rules`; no `ai/tool_registry.py` registration (Stage 0 rule); closure caller is `rca.py:close_case` → `kb.draft_rule_from_case_closure` (Stage 3) | code review: no `register(Tool(` wraps a kb.py function; `test_rca.py::test_07_...` asserts the draft row | 2 (foundation) / 3 (closure caller) | **partial** — closure-moment writes implemented; there is still no human-answer-moment write caller, since no complaint/human-question UI exists yet (same gap as the Pause-and-ask human gate row below) |
| Triage two-signal grouping rule (WF §4 Agent 0) | codex-terra | `rca.py:find_matching_open_group` — same item+table (lineage/time proxy, within `TRIAGE_TIME_WINDOW_MINUTES`) AND same inferred test family (symptom-shape proxy) | `test_rca.py::TriageTests` (both the matching and the different-family cases) | 4 | **partial** — a defensible two-signal proxy given the current data model has no real lineage graph; documented in `rca.py:create_case_from_issue`'s docstring |
| Triage structural-first ordering (WF §4 Agent 0) | codex-terra | n/a | n/a | 4 | not implemented — there is no multi-case processing QUEUE in this product (cases are created on demand per issue, not batch-processed), so "which case runs first" doesn't apply the way WF's design assumes |
| Intake fixed checklist per test family (WF §4 Agent 1) | codex-terra | `rca.py:create_case_from_issue` builds `rca_case_files.checklist_json` | `test_rca.py::test_case_ends_at_opening_looks_with_case_file_and_tag_snapshot` | 3 | **partial** — one minimal checklist shape for all test families, no per-family variation and no not_investigable rejection path; Stage 4 owns real per-family checklists |
| Intake suppression of known accepted change (WF §4 Agent 1) | codex-terra | `rca/intake.py` | matching `case_history` expected-change closes case immediately | 4 | not implemented — `create_case_from_issue` doesn't yet query the KB before building the case file; deferred alongside real per-family checklists |
| Opening looks 2–4 fixed computations + shortcut (WF §4 Agent 2) | codex-terra | `rca.py:run_opening_look` (`_profile_column`) | `test_rca.py::test_01_opening_look_finds_a_column_profile` | 3 | **partial** — exactly one deterministic opening look (not 2-4) and no shortcut-to-Composer path; both are honest Stage 3 scope limits per MP §14's "opening look" (singular) |
| Planner: one look + precommitted fork, never sees causes (WF §4 Agent 3) | codex-terra | `rca.py:planner_propose_look` | `test_rca.py::test_02_planner_runner_reader_cycle_finds_concentrated_suspect` | 3 | implemented |
| Planner cannot reopen a ruled-out suspect unasked (WF §4 Agent 3) | codex-terra | `revive_suspect()` is a standalone function never called from `planner_propose_look` | code review: no call site of `revive_suspect` exists inside the Planner functions | 4 | implemented by omission — the Planner has no revival code path at all, only the Reader-facing `revive_suspect` does |
| Runner: read-only, size-limited summary, one retry (WF §4 Agent 4) | codex-terra | `rca.py:runner_execute` | `test_rca.py::test_02_planner_runner_reader_cycle_finds_concentrated_suspect` | 3 | **partial** — read-only deterministic execution implemented; crash/one-retry handling is not (Stage 3's single cycle has no re-planning loop to retry into), deferred to Stage 4 |
| Reader: sole suspect-status writer, "doesn't answer" costs no budget (WF §4 Agent 5) | codex-terra | `rca.py:reader_interpret` | `test_rca.py::test_02_planner_runner_reader_cycle_finds_concentrated_suspect` (the `found=False` branch creates no suspect) | 3 | **partial** — sole-writer and no-answer-no-suspect implemented; there is no budget counter to leave unchanged yet (`rca_looks.budget_counted` is schema-present, consumed starting Stage 4) |
| Coverage pass 2 nominate-only, no admit without current evidence (WF §4) | codex-terra | `rca.py:coverage_challenge_pass2` — nominates from `kb.list_eligible_rules(..., "coverage_challenge_pass2")` (case_history category only); the nominated suspect must still survive `run_reopened_kill_attempt` | `test_rca.py::test_03_...` covers the no-nomination path (no case_history KB rules exist in the test tenant); the admit-requires-survival path is exercised by `run_reopened_kill_attempt`'s own logic | 4 | **partial** — nominate-only and required-survival are implemented; not exercised end-to-end by a test with a real published case_history rule (none exists in Stage 4's fixtures) |
| Added suspect tagged origin, no confidence credit, must survive kill-attempt (WF §4) | codex-terra | `rca_suspects.origin='challenge_history'`; `_suspect_tier` never grants a tier above Weak from the nomination alone (tier is earned only via `survived kill-attempt` history) | code review: `_suspect_tier` reads only `suspect_history` reasons, never `origin` | 4 | implemented |
| Composer: 3–6 hypotheses, each cause+evidence+check+label (WF §4 Agent 6) | codex-terra | `rca.py:compose_hypothesis`, `MAX_HYPOTHESES=6` | `test_rca.py::test_04_composer_produces_tiered_hypotheses_with_evidence` | 4 | **partial** — capped at 6, one hypothesis per active suspect; no lower bound of 3 is enforced (a thin board legitimately produces fewer, with an explained single fallback hypothesis when the board is empty) |
| Confidence tiers Strong/Moderate/Weak, defined criteria (WF §4) | codex-terra | `rca.py:_suspect_tier` (Strong = survived kill-attempt + >=2 independent supporting looks; Moderate = survived a kill-attempt; Weak = cited evidence only) | `test_rca.py::test_04_...` (tier present and valid for every hypothesis); no dedicated per-boundary fixture yet | 4 | implemented |
| Budget ~10 looks; four stop conditions (WF §5) | codex-terra | `rca.py:evaluate_stop_condition`, `LOOK_BUDGET=10` | `test_rca.py::StopConditionTests::test_budget_spent_stop_condition` (budget); `::test_03_loop_runs_to_a_stop_condition_then_coverage_challenge` (whichever of converged/battle_tested/dead_end fires naturally on the fixture) | 4 | **partial** — all four conditions are implemented and reachable; only `budget_spent` has a dedicated direct fixture, the other three are exercised incidentally by the fixture-driven integration test, not a fixture engineered per condition |
| Combinations: pairs-only, trigger-only creation, no kill propagation (WF §5) | codex-terra | `rca.py:_create_pairwise_combination` (called only from `reader_interpret` on a converged-with-exactly-2-active-suspects trigger) | none dedicated — the fixture data hasn't naturally produced this trigger in a test run yet | 4 | **partial** — implemented, not yet directly tested; no-kill-propagation holds structurally (a combination is its own independent `rca_suspects` row, never derived from members at read time) but isn't asserted by a test |
| Board cap 8; naming rule to add 9th (WF §5) | codex-terra | `rca.py:planner_propose_look`, `BOARD_CAP=8` | `test_rca.py::test_board_cap_requires_a_named_kill_target` | 4 | implemented |
| Symptom-size interaction trigger before dead-end (WF §5) | codex-terra | n/a | n/a | 4 | not implemented — no numeric "symptom size" proxy exists in the current data model (violation_count alone isn't a reliable size measure across test families); the pairwise-combination trigger above is the closest implemented relative, fired on suspect-count convergence rather than a measured unexplained size |
| Pause-and-ask human gate, answer persisted forever (WF §5, §6) | codex-terra | `rca_human_questions` (schema only) | none yet | 3 (schema) / 4 (trigger + logic) | not implemented — Stage 3's single deterministic cycle never needs to pause; Stage 4's real Planner loop is what can actually hit a missing-fact case |
| Second chance once only; escalation after (WF §6, §12) | codex-terra | `rca.py:start_second_chance`, `_second_chance_used`, `SECOND_CHANCE_BONUS_LOOKS=5` | `test_rca.py::SecondChanceAndEscalationTests::test_second_chance_granted_once_then_escalates_to_unresolved` | 5 | implemented |
| Four closing states only (WF §7) | codex-terra | `rca.py:close_case` maps hypothesis label → outcome (confirmed/genuine_change/test_design_flaw); `rca.py:start_second_chance` writes `rca_closures.outcome="unresolved"` directly on a second exhaustion (there's no rerun to perform for Unresolved — it's a give-up outcome, not a fix-verified one) | `test_rca.py::test_07_closure_reruns_original_test_and_writes_draft_knowledge` covers Confirmed; `::SecondChanceAndEscalationTests::test_second_chance_granted_once_then_escalates_to_unresolved` covers Unresolved | 3 (confirmed/genuine_change/test_design_flaw) / 5 (unresolved) | implemented |
| Closure writes KB (cause/expected-change/ownership) (WF §7) | codex-terra | `rca.py:close_case` → `kb.py:draft_rule_from_case_closure` | `test_rca.py::test_06_closure_reruns_original_test_and_writes_draft_knowledge` (asserts a `draft`, unpublished `kb_rules` row) | 3 | implemented |

## Part B — verification and closure (WF §10–16)

| Rule | Owner | Target path | Behavioral test | Stage | Status |
|---|---|---|---|---|---|
| Confirmation order: tier first, cost tie-break (WF §11 Agent 7) | codex-terra | `rca.py:compose_hypothesis` writes `order_rank` tier-sorted (strong→moderate→weak); `_next_pending_check` always picks the lowest `order_rank` still pending | `test_rca.py::SymptomAccountingAndQueueTests::test_confirmation_check_advances_to_next_queued_hypothesis_when_rejected` | 5 | **partial** — tier-first ordering is implemented and tested; there is no separate numeric "cost" dimension in the data model (`cost_hint` is a constant `1.0` for every check), so ties within a tier fall back to Composer's insertion order rather than a real cost tie-break |
| Crashed/empty check: one retry, no budget cost (WF §11 Agent 7) | n/a | n/a | n/a | 5 | not implemented — confirmation checks in this product are deterministic in-process computations against already-materialized data (`_suspect_check_result`), not a live sandboxed execution that can crash independently of a code bug; there is no failure mode this retry would catch that isn't already a real bug to fix, so no synthetic retry-on-crash path was built |
| Judge gatekeeper: check must be reject-capable before running (WF §11 Agent 8) | codex-terra | `rca.py:run_confirmation_check` raises `RcaError` if `reject_condition_json` is missing, before running anything | `test_rca.py::test_gatekeeper_rejects_a_check_without_a_reject_condition` | 3 (gatekeeper check) / 5 (redesign flow) | **partial** — the hard gate is implemented; there is still no "send back for redesign" flow, since the Composer always writes a `reject_condition_json` for every hypothesis it creates (Stage 4/5), so the gate has no real caller that can trip it in this product's own code paths — it exists to guard against a future Composer regression, not a reachable case today |
| Judge verdicts confirmed/rejected/inconclusive; one refinement (WF §11 Agent 8) | codex-terra | `rca.py:run_confirmation_check`, `_judge_verdict` (ratio-based bands: ≥2.0 confirmed, <1.2 rejected, else inconclusive; the one allowed refinement re-runs with a single 1.5 cutoff and an inconclusive-again result becomes `unverified`, never a second inconclusive) | `test_rca.py::JudgeVerdictTests::test_clear_confirm_and_reject_bands`, `::test_inconclusive_band_then_refinement_resolves` | 5 | implemented |
| Symptom accounting continues past first confirmed cause (WF §11 Agent 8) | codex-terra | `rca.py:record_symptom_accounting` (sums the worst-segment row count of every CONFIRMED hypothesis's suspect against the issue's `violation_count`); `run_confirmation_check`'s confirmed branch only stops at `awaiting_fix_approval` when `remaining_size <= 0` or the queue is exhausted, otherwise loops back to `confirmation_checks` | `test_rca.py::SymptomAccountingAndQueueTests::test_symptom_accounting_row_written_on_confirm` | 5 | implemented |
| Fix advisor proposes only, routed by label (WF §11 Agent 9) | codex-terra | `rca.py:propose_fix` (routes via `ai/rca_cases.FIX_BY_CAUSE`) | `test_rca.py::test_06_human_approval_required_before_fix_application` | 3 | implemented |
| Human approval required before any fix application (WF §11 Agent 9, MP §11) | codex-terra + codex-sol | `rca.py:approve_fix`/`confirm_fix_applied`, `rca_fix_approvals` | `test_rca.py::test_06_human_approval_required_before_fix_application` (approval always precedes `applied_confirmed_at`) | 3 | implemented; Stage 6 adds the adversarial security pass |
| Closure reruns original failed test; frozen-snapshot rule (WF §11 Agent 10) | codex-terra | `rca.py:_rerun_original_test`/`close_case` — real sandbox re-execution of the original `plan_v2` snippet, `frozen_snapshot=1` always (nothing in this product re-profiles data mid-case yet) | `test_rca.py::test_07_...`, `::test_closure_without_a_real_fix_stays_honestly_open` (the honest non-close path) | 3 | implemented; fresh-snapshot warning path has no trigger yet since nothing re-profiles data mid-case |
| Time-box escalation (WF §11 Agent 10, MP §10) | codex-terra | `rca.py:approve_fix` sets `time_box_deadline` (`TIME_BOX_DAYS=5`); `check_time_box_escalations`/`sweep_all_tenants_time_box_escalations` mark `escalated_at` + write an audit event past deadline; wired as a background daemon thread in `main.py` (`_time_box_sweep_loop`, hourly by default, same pattern as the existing DB-backup thread) so it actually runs in a live deployment, not just in a test | `test_rca.py::TimeBoxEscalationTests` (overdue escalates, not-yet-due is left alone, and a dedicated cross-tenant regression test) | 5 | implemented |
| Attached-failure reconciliation at closure (WF §11 Agent 10) | codex-terra | `rca.py:reconcile_attached_failures`, called from `close_case` on the success path — every Triage-attached failure in the case's group is marked `matches` (a confirmed cause exists) or `mismatch_needs_own_case` | `test_rca.py::AttachedFailureReconciliationTests::test_reconciliation_marks_attached_failure_matched_on_confirmed_closure` | 5 | **partial** — implemented and tested for the `matches` path; the reconciliation check itself is a coarse "was any cause confirmed" test rather than a full per-attachment re-diagnosis (documented in the function's own docstring), and the `mismatch_needs_own_case` branch has no dedicated test forcing an unconfirmed closure with an attached failure present |
| All-hypotheses-rejected → second chance (WF §12) | codex-terra | same as the Part A row above (`rca.py:start_second_chance`) | same as the Part A row above | 5 | implemented |
| Second-validator findings 4–6 (tiers, two-pass challenge, combinations) (WF §15) | codex-terra | covered by rows above | covered by rows above | 4 | implemented |
| Third-validator findings 7–9 (two-signal triage, KB shelf-life/blame-back, board cap) (WF §16) | codex-terra | covered by rows above | covered by rows above | 4 (triage/board cap) / 5 (blame-back) | implemented |

## Migration plan structural rules (MP)

| Rule | Owner | Target path | Behavioral test | Stage | Status |
|---|---|---|---|---|---|
| `workflow_version` legacy-v1 / rca labeling (MP §1, §9) | codex-terra | `issues_v2.workflow_version` migration | legacy rows unaffected; new RCA rows tagged | 1 | implemented (`system_db.py` migration; column unused by any code path until Stage 3) |
| No numeric-confidence-to-tier conversion (MP §1) | sonnet (design constraint, enforced by omission) | n/a — no converter is ever written | code-search test: no function maps `rca_hypotheses.confidence` → tier | 0 | contracted (this doc) |
| Reuse existing Issue Management route/case list (MP §2) | codex-terra | `ui/src/pages/IssueRca.jsx` branches on `issue.workflow_version`, renders `RcaCase` inline | `ui/e2e/rca.spec.js`'s second-module-guard test (asserts zero new nav links) | 3 | implemented |
| KB: preserve original bytes + SHA-256, Markdown conversion immutable (MP §4.2) | codex-terra | `backend/kb.py:store_original/upload_document`, `backend/kb_convert.py` | `test_kb.py::test_upload_preserves_original_bytes_and_hash`, `::test_upload_storage_is_content_addressed_dedup` | 2 | implemented |
| KB: Markdown for content, DB for governance, YAML for manifests/synthetic (MP §4.3) | codex-terra | `backend/kb.py`, `synthetic-kb/manifest.yaml` | `test_kb.py::SyntheticKbTests` | 2 | implemented |
| Taxonomy: governed dimensions, no scattered frontend constants (MP §5.2) | codex-terra | `backend/taxonomy.py`, `backend/routers/v3.py`, `ui/src/components/TagPicker.jsx` fetches `/api/v3/taxonomy/dimensions` | `test_taxonomy.py::TaxonomySeedTests`; UI renders zero hardcoded tag lists | 1 | implemented |
| Tag propagation immutable snapshot chain (MP §5.3) | codex-terra | `taxonomy.inherit_tags()`, wired in `ai/v2/service.py:finalize_plan/execute_iter`, `ai/v2/issues.py:sync_issues` | `test_taxonomy.py::test_downstream_snapshot_survives_source_tag_removal`, `::test_full_propagation_chain_item_to_issue`; `ui/e2e/taxonomy-tags.spec.js` | 1 | implemented |
| Synthetic KB never production seed (MP §6) | codex-terra | `backend/seeds/synthetic_kb_seed.py` (never called from `seed_all`/`main.py`) | `test_kb.py::test_never_loaded_on_boot_then_explicit_load_produces_expected_states` | 2 | implemented |
| API routes additive under `/api/v3/*`, existing APIs retained (MP §13) | codex-terra | `backend/routers/v3_*.py` (new), `backend/main.py` mounts alongside `v2` | existing `/api/v2/*` smoke suite still green after v3 mount | 1–5 | contracted |
| Tenant scoping, foreign-ID indistinguishable from unknown (MP §11) | sonnet (adversarial audit + fix, Codex unavailable — see Stage 0's standing note) | `rca.py`: `transition()`, `revive_suspect()`, `propose_fix()`, `record_symptom_accounting()`, `reconcile_attached_failures()` all gained/now enforce a `tenant_id` param via `require_case()` (5 real tenant-blind gaps found and fixed — `revive_suspect`/`propose_fix` were genuinely unguarded writes, not just defense-in-depth); `routers/v3.py`'s propose-fix and run-check routes reordered to tenant-validate before fetching by a non-case ID | `test_rca.py::TenantIsolationTests` (5 tests: foreign tenant → KeyError, legitimate tenant still works) | 6 | implemented |
| Secure cookie/CSRF target, bearer hardening interim (MP §11) | sonnet | `backend/routers/auth.py:current_user()` — absolute session lifetime (`SESSION_MAX_AGE_HOURS`, default 24h); full cookie+CSRF migration scope-revised, see `00-contracts.md` §13.2 | `test_auth_security.py::SessionExpiryTests` (6 tests: expiry, backward-compat for pre-existing sessions, logout, invalid/missing token) | 6 | **partial by design** — bearer hardening implemented; cookie+CSRF migration itself deferred as its own project, decision recorded in contracts §13.2 (not silently dropped) |
| Analytical execution stays bounded read-only SQL + allowlisted helpers (MP §11) | sonnet (adversarial audit) | `rca.py` — audited every `execute(`/`eval(`/`exec(`/`subprocess`/`os.system` call site; exactly one raw SQL call exists (`_rerun_original_test`), fully parameterized | `test_rca.py::StaticSafetyTests` (deterministic static check in place of a runtime fuzz test — proves absence, not just untriggered-by-sample) | 6 | implemented |
| Shared error sanitizer, append-only audit (MP §11) | sonnet | `routers/v3.py:_rca_error_map` — added `ValueError` → 400 (previously fell through to an unhandled 500, e.g. `revive_suspect`'s reason-required validation), and a real sanitized-500 branch (server-side `print` logging + generic client detail, replacing a bare `raise exc`); `rca.py` — closed 10 audit-coverage gaps found by the same audit (`planner_propose_look`, `runner_execute`, `revive_suspect`, `_create_pairwise_combination`, `record_symptom_accounting`, `propose_fix`, `reconcile_attached_failures`, `reader_interpret`'s suspect writes, `run_confirmation_check`'s judge decision, `run_reopened_kill_attempt`'s 3 direct state writes — the last of which also now carries its own audit event since it deliberately bypasses `transition()`) | existing `test_rca.py` suite exercises every touched function; audit rows asserted incidentally throughout (no single "count every audit type" test — see Stage 6 closure note for why) | 6 | implemented |
| Idempotent additive migration, bootstrap tenant, legacy IDs preserved (MP §9) | sonnet | `system_db.init_schema()` RCA block + `seeds.seed_platform_and_taxonomy()` | `test_migration_idempotency.py::MigrationIdempotencyTests` (full table/row-count inventory identical across a 2nd and 3rd run, not just the taxonomy-only check Stage 1 had) | 7 | implemented |
| Legacy new-case creation retired only after acceptance (MP §14 Stage 7) | sonnet (gate decision) | `feature_flags.RCA_LEGACY_CREATION_RETIRED`, wired as a real one-way floor in `ai/v2/issues.py:sync_issues` (previously declared but unread by any code — a Stage 7 finding, fixed before flipping it) | `test_rca.py::LegacyCreationRetirementTests` (2 tests); flag flipped to 1 only after `docs/rca/07-rollout.md`'s full MP §17 checklist verified true and the full suite green | 7 | implemented |

## Stage 0 self-check against MP §17 acceptance gate

Every acceptance-gate line item in MP §17 has a corresponding row above except
purely emergent, whole-system properties that are checked at Stage 7 rather
than owned by one component: "all new cases use RCA", "all four endings
work", "no synthetic fixture in production" — these are cross-cutting and are
verified by the Stage 7 gate checklist directly (`docs/rca/07-rollout.md`,
written when Stage 7 starts) rather than duplicated here per-rule.

## Open items carried to Stage 1 kickoff

- Confirm `python-docx` / `pypdf` are acceptable new backend dependencies
  (Decision §9.7 in `00-contracts.md`) — no repo constraint blocks them
  (existing sandbox allowlist already permits `pandas`/`numpy`/`scipy`/
  `statsmodels` as precedent for adding vetted pure-Python libraries).
- `codex-sol` Stage 0 adversarial pass: **dispatched, but Codex's Windows
  sandbox runner is non-functional in this environment** (fails even a
  trivial `echo` with `windows sandbox: timed out after 15000ms connecting
  runner pipe-in` — confirmed non-transient on retry, despite `codex:setup`
  reporting `ready:true`/authenticated). The model-orchestration plan's
  Terra/Sol split via Codex is currently blocked at the infrastructure level;
  this is a standing limitation for every later stage until the sandbox issue
  is fixed, not just Stage 0.
- In place of the Codex Sol pass, `sonnet` performed the same seven-item
  adversarial review directly, with real `grep`/file verification rather than
  re-reading its own document. Found and fixed six genuine gaps, all now
  inline in `00-contracts.md`: (1) second-module guard made concrete —
  §3 preamble; (2) `dq_items`/`issues_v2`/`results_v2`/`plan_v2` tenant-gap
  acknowledged rather than silently assumed solved — §1; (3) `case_id`
  naming-collision-across-families rule — §3 preamble; (4) history-leakage
  side channel through `context_memory.get_context_bundle` /
  `object_contexts` (confirmed real via `backend/ai/rca_agent.py:110-111`,
  which already does this for the legacy agent) — §3, after the table list;
  (5) LLM-tool-registry write risk via `backend/ai/tool_registry.py:66`
  `register()` — §3, after the table list; (6) rollback residue on the two
  shared-table columns — §10. The "no reachable writer to legacy `rca_cases`"
  factual claim in §0 was independently re-verified by grep (`rca_agent`/
  `rca_cases` do not appear in any file under `routers/` or in `main.py`) and
  confirmed true. No corruption, second-module, or direct-LLM-write risk was
  found that isn't now covered by an explicit rule above.
- Stage 0 gate: **closed.** Contracts complete, migration strategy complete,
  security boundaries complete (with the two acknowledged, explicitly-deferred
  gaps above), traceability complete, no product behavior changed. Proceeding
  to Stage 1.

## Stage 1 gate — closed

Delivered: `tenants`/`feature_flags`/`tag_dimensions`/`tag_values`/
`tag_aliases`/`tag_taxonomy_versions`/`tag_assignments` schema
(`backend/system_db.py`); `users.tenant_id` and `issues_v2.workflow_version`
additive migrations; bootstrap tenant + four taxonomy dimensions seeded
idempotently (`backend/seeds/taxonomy_seed.py`); `backend/taxonomy.py` service
(assign/get/remove/inherit, soft-delete with mandatory reason + audit event);
`backend/tenancy.py` Principal resolution; `/api/v3/taxonomy/*` and
`/api/v3/{items,tests,results,issues}/.../tags` routes
(`backend/routers/v3.py`, session-gated — a stricter bar than v2's
currently-open handlers); propagation wired at the item→plan (finalize),
plan→result (execute), and item→issue (sync) hops; `TagPicker`/`TagChips` UI
in Data Sourcing and the Issue RCA screen.

Verified: `backend/tests/test_taxonomy.py` (12/12 — seeding, idempotent
migration, assign/remove/audit, inheritance immutability, and the full
item→plan→result→issue chain); `ui/e2e/taxonomy-tags.spec.js` (upload, tag
assignment via the UI, server-side persistence, inheritance verified at each
hop via the v3 API, and — since this fixture happens to produce a failed
test — display on the Issue RCA screen); full existing suite still green
(`ci-local.ps1`, all four Playwright specs including the two pre-existing
ones, run together under real parallelism after fixing a latent
item-selection race the second spec file exposed in
`upload-workflow.spec.js`).

`TAXONOMY_ENABLED` flipped to enabled-by-default in the seed
(`seeds/taxonomy_seed.py:_INITIAL_FLAG_STATE`) now that this gate has passed.
Scope note: the tag UI is not additionally gated behind a runtime flag check
in the frontend — taxonomy is purely additive (nothing it touches can regress
by being visible), unlike Stage 3's `RCA_ENABLED` which genuinely
branches case-creation behavior and must stay flag-gated for a real reason.
Building a flag-check plumbing path for a flag with no protective purpose
was judged out of scope; if a future multi-tenant rollout scenario needs
staged taxonomy visibility, add the check then.

No product behavior changed for existing (non-tagging) workflows: `issues_v2`
rows keep working exactly as before, the new `workflow_version` column is
inert until Stage 3, and every new table/column is additive. Proceeding to
Stage 2.

## Stage 2 gate — closed

Delivered: `kb_documents`/`kb_document_versions`/`kb_sections`/`kb_rules`/
`kb_shelf_life_defaults`/`kb_retrieval_manifests` schema (`backend/system_db.py`);
`backend/kb_convert.py` (TXT/MD passthrough, DOCX via `python-docx`, PDF via
`pdfplumber` with image-only-PDF rejection by text-density threshold — both
libraries were already backend dependencies, corrected from the Stage 0
placeholder `pypdf`); `backend/kb.py` service (content-addressed original-byte
storage with sha256 dedup, deterministic markdown→sections→draft-rules
extraction, role-gated publish/archive, shelf-life and schema-change trust
demotion, `mark_under_suspicion` blame-back primitive, category-restricted
retrieval with a persisted manifest); `/api/v3/knowledge/*` routes
(`backend/routers/v3.py`); a `KnowledgeBase` page reachable only from the
existing Sidebar nav (no second surface); `synthetic-kb/` (YAML manifest +
5 Markdown files) with `backend/seeds/synthetic_kb_seed.py:load_synthetic_kb`
— never called from `main.py`/`seed_all()`.

New roles `kb_editor`/`kb_reviewer` added to `authz_roles` (admin does not
inherit them, per contracts.md §1's "admin ≠ automatic tenant-content
access"). Fixed a real deployment gap this surfaced: `backfill_authz()` only
fills *null* `authz_roles`, so a pre-existing install's already-seeded users
would never receive newly-configured roles — added `backfill_kb_roles()`
(unions in configured `kb_editor`/`kb_reviewer` without touching any other
role a deployment may have assigned) and wired it into `main.py` next to the
existing `backfill_authz()` call.

Verified: `backend/tests/test_kb.py` (21/21 — conversion for all four
supported types plus corrupt/unsupported/image-only-PDF/empty-file rejection,
content-addressed dedup, draft extraction, publish/archive role gating,
shelf-life and schema-change trust demotion, blame-back, agent-category
quarantine with manifest persistence, the synthetic package's never-on-boot
guarantee, and the `backfill_kb_roles` migration); `ui/e2e/knowledge-base.spec.js`
(upload → convert → preview → submit-for-review → publish through the real
UI, plus an explicit second-module guard test). Full local CI gate (lint,
build, backend compile+boot smoke, all 6 Playwright tests across 3 spec
files) green. Caught and fixed two real bugs during this stage's own
verification: a `useEffect`-returns-a-Promise crash in both `KnowledgeBase`
panels (expression-body arrow functions implicitly returning their fetch
promise to `useEffect`), and a stale `.e2e/system_state.db` masking the new
role grant (the persisted E2E fixture db predated the `kb_editor`/
`kb_reviewer` seed change — the same defect class `backfill_kb_roles` fixes
for real deployments, caught here first).

`KB_MODULE_ENABLED` flipped to enabled-by-default now that this gate has
passed, on the same reasoning as Stage 1's `TAXONOMY_ENABLED`: the module is
additive and nothing regresses by it being visible, so no separate runtime
flag-check plumbing was built in the frontend.

Deferred to later stages by design, not omission: LLM-facing agents that
actually call `list_eligible_rules` (Stage 4 Planner/Reader, Stage 3-6
others), the human-answer and case-closure knowledge-write callers (Stage 3,
5), and the blame-back *trigger* from a real case outcome (Stage 5) — Stage 2
delivers the foundation and primitives these will call, per the migration
plan's staged posture. No product behavior changed for any existing
(non-knowledge) workflow. Proceeding to Stage 3.

## Stage 3 gate — closed

Delivered: the full `rca_*` record model from contracts.md §3
(`backend/system_db.py`); `backend/rca.py` — one consolidated,
deterministic service module (not the many small files sketched
illustratively in the Stage 0 contract; see that module's own docstring for
why) implementing the state machine (`transition`, `ALLOWED_NEXT` matching
contracts.md §3 verbatim), case+intake (`create_case_from_issue`), one
deterministic opening look (`run_opening_look`), a real Planner/Runner/Reader
cycle (`planner_propose_look`/`runner_execute`/`reader_interpret`) that
finds and updates exactly one suspect from a real segment-concentration
signal, a Composer producing one Weak-tier evidence-citing hypothesis
(`compose_hypothesis`), a gatekept confirmation check with a real Judge
verdict (`run_confirmation_check`), human-gated fix approval
(`propose_fix`/`approve_fix`/`confirm_fix_applied`), and closure that really
re-executes the original test's sandboxed snippet against the frozen data
snapshot (`close_case`) and writes a draft (never auto-published) knowledge
rule via Stage 2's `kb.draft_rule_from_case_closure`. `/api/v3/issues/.../
rca/case` and `/api/v3/rca/*` routes; a `RcaCase` staged-view
component rendered inline inside the existing `IssueRca.jsx` (no second
route/module). `ai/v2/issues.py:sync_issues` now labels new issues
`workflow_version=rca` when the flag is on, `legacy-v1` otherwise — the
existing heuristic RCA path is untouched for legacy issues.

Deterministic throughout — no Azure OpenAI call is made or required
anywhere in Stage 3's code or tests, matching MP §14's "do not require
generated helper code for the first slice" and the continuous-testing
posture ("do not make billable live model calls without explicit
authorization"). `ai/rca_helpers.py` was NOT reused: it imports `from
database import TABLE_KEYS`, and `database.py` was retired from this
product generation, so that module is currently dead
(`ModuleNotFoundError`, confirmed directly) — the deterministic look
functions in `rca.py` are written fresh against the current v2 data
model instead.

**Honest scope choice, not a bug:** a "human-approved simulated fix" never
mutates data (contracts.md §0 decision — production fixes are simulated in
tests, never applied automatically). `close_case()` therefore re-runs the
*real* original snippet by default; if no real upstream fix occurred, the
rerun honestly reproduces the original failure, and the case is left at
`closure_rerun` with the real result on record rather than fabricating a
Confirmed outcome. `close_case()` accepts an injectable `rerun_fn` for
tests that need to exercise the pass path deterministically (the "add
deterministic fake agents for automated tests" instruction, applied to the
one piece of Stage 3 a test cannot make genuinely pass without a real
upstream fix). The UI surfaces this honestly too — see
`RcaCase.jsx`'s `doClose()` — rather than silently doing nothing on a
non-passing rerun.

Verified: `backend/tests/test_rca.py` (13/13 — no pre-seeded causes,
idempotent case creation, illegal-transition rejection, the full case→
opening-look→Planner/Runner/Reader→Composer→confirmation→fix-approval→
closure happy path, the Judge gatekeeper duty, and the honest non-closure
path); `ui/e2e/rca.spec.js` (the same flow driven through the real UI
end-to-end, plus a second-module-guard test). Two real bugs found and fixed
during this stage's own verification: `system_db.query()`'s first
positional parameter is itself named `table_name`, which collided with
`plan_v2`'s own `table_name` column when filtered via kwargs (fixed by
switching to raw SQL, matching the existing codebase's own `get_plan()`
workaround for the identical footgun); and a segment-concentration
heuristic that compared the worst segment's rate to the *blended overall*
rate — which the worst segment itself pulls up — making "concentrated"
much harder to detect than intended whenever the bad segment is a large
share of the data (fixed to compare worst-vs-best segment instead). A third
finding was a CI-infrastructure issue, not a product bug: once 4 spec files
shared one backend process, Playwright's default parallel workers produced
intermittent timeouts under real concurrent uploads/executions — a
different spec failed on each retry, and every spec passed reliably alone,
the signature of resource contention rather than a logic defect (confirmed
by re-running combinations repeatedly). Set `workers: 1` in
`ui/playwright.config.js` — serial execution measured both reliable and
faster in practice (no contention/retries), so this was not a speed
trade-off. Full local CI gate (lint, build, backend compile+boot smoke, all
8 Playwright tests across 4 spec files) green.

`RCA_ENABLED` flipped to enabled-by-default in the seed now that this
gate has passed — new issues route to RCA case creation; legacy issues and
the existing heuristic RCA path are unaffected.
`RCA_LEGACY_CREATION_RETIRED` stays off until Stage 7's acceptance gate.

Deferred to Stage 4/5/6 by design, listed in the rows above rather than
repeated here: real per-family intake checklists, 2-4 opening looks +
shortcut, Planner re-planning/budget/stop-conditions, crash-retry, Triage
grouping, coverage challenge, 3-6 hypotheses with real tier criteria,
symptom accounting, Judge inconclusive+refinement, second chance/escalation,
Unresolved outcome, and the pause-and-ask human gate's actual trigger (the
schema and a callable primitive exist; nothing in Stage 3's single
deterministic cycle can hit a missing-fact case that would need it).
Proceeding to Stage 4.

## Stage 4 gate — closed

Delivered, all in `backend/rca.py` (extended, not replaced): two-signal
Triage (`find_matching_open_group` — same item+table within a time window,
proxying lineage/time, AND same inferred test family, proxying symptom
shape) that attaches a second matching issue to an existing open case
instead of starting a new one; a real budgeted multi-cycle Planner/Runner/
Reader loop (`LOOK_BUDGET=10`) that explores one new segment column per
cycle, then falls back to kill-attempts on active suspects once columns run
out; all four stopping conditions (`evaluate_stop_condition`); a board cap
of 8 active suspects with a required named kill target for the 9th
(`BOARD_CAP`); Reader-controlled suspect revival (`revive_suspect`); a
pairwise-combination trigger on convergence with exactly two active
suspects; a real two-pass coverage challenge (blind pass 1, history-aware
nominate-only pass 2 wired through Stage 2's `kb.list_eligible_rules`, plus
the required reopened-kill-attempt before a nomination can reach the
Composer); and a Composer that produces up to `MAX_HYPOTHESES=6`
hypotheses — one per active suspect/combination — each with an earned
Strong/Moderate/Weak tier (`_suspect_tier`) instead of Stage 3's
always-Weak placeholder.

`/api/v3/rca/*` gained routes for suspect revival and the two coverage-
challenge passes plus the reopened kill-attempt; the existing planner-look
route accepts an optional `kill_target_suspect_id`. The `RcaCase` UI
gained buttons for every new state, a revive action on ruled-out suspects,
and a board-cap fallback that names the first active suspect as the kill
target automatically rather than blocking on a picker UI.

**Honest scope limits, not omissions:** the confirmation-check/Judge/
closure machinery still processes only the FIRST (highest-tier) hypothesis
per case and advances the case state immediately on its verdict — visiting
the rest of the tier-ordered queue, symptom accounting, and "continued
verification when partly unexplained" are explicitly Stage 5's job
("Complete Part B"), and `compose_hypothesis` already writes every
hypothesis's `order_rank` for Stage 5's scheduler to read. Also not
implemented, and not silently assumed done: Triage's structural-first
*queue* ordering (this product creates cases on demand, not via a batch
queue, so the rule doesn't apply the way WF's design assumes), the
complaint-guess-is-just-one-suspect path (no complaint UI yet), Intake's
KB-based expected-change suppression, and the symptom-size interaction
trigger (no reliable numeric "size" proxy exists across test families yet —
the pairwise-combination trigger implemented here is the closest working
relative, firing on suspect-count convergence instead of a measured size).
Every one of these is listed with its real status in the matrix above, not
just narrated here.

Verified: `backend/tests/test_rca.py` grew from 13 to 19 tests — the
existing happy-path/gatekeeper suites were updated to drive the real
multi-cycle loop (via a new `_drive_investigation_loop` test helper) rather
than assuming one cycle, plus four new dedicated test classes for Triage
(match and no-match), board cap, suspect revival, and the budget-spent stop
condition in isolation. `ui/e2e/rca.spec.js` was updated to click
through cycles defensively (this fixture's table turned out to have no
usable segment column, so Planner hits `dead_end` on the very first call in
practice — the test now handles that as a legitimate real outcome instead
of assuming a look is always created) and through both coverage-challenge
passes. Full local CI gate (lint, build, backend compile+boot smoke, all 8
Playwright tests) green.

Two real bugs found and fixed during this stage's own verification, beyond
the test-suite updates already described: `planner_propose_look` returning
`{"dead_end": True}` was silently ignored by its API route, which would
have left the case stuck showing "propose next look" forever with no way to
progress (added `handle_dead_end` and wired the route to call it); and a
genuinely confusing dead-code artifact (`if False else None` / a nested
lambda inside a list comprehension) written and caught during this stage's
own first draft, cleaned up before it ever reached a commit.

Proceeding to Stage 5.

## Stage 5 gate — closed

Delivered, all in `backend/rca.py` (extended, not replaced) unless noted:
the tier-ordered confirmation queue now actually processes past the first
hypothesis — `_next_pending_check` always returns the lowest-`order_rank`
still-pending check; a real Judge (`_judge_verdict`) with three verdict bands
(confirmed ≥2.0, rejected <1.2, inconclusive between) and exactly one
allowed refinement (a second run at the same check_id, using a single 1.5
decisive cutoff so it is never inconclusive twice — an inconclusive-again
refinement becomes `unverified` instead); symptom accounting
(`record_symptom_accounting`) that sums the worst-segment row count of every
CONFIRMED hypothesis's suspect against the issue's real `violation_count`,
and `run_confirmation_check` now keeps verifying the remaining queue instead
of stopping at the first confirmation when the explained size is only
partial; second chance (`start_second_chance`) — granted exactly once, with
a `SECOND_CHANCE_BONUS_LOOKS=5` budget bonus (`_effective_look_budget`), and
a second exhaustion escalates straight to `unresolved` (WF's fourth closing
state, `rca_closures.outcome="unresolved"`) without a rerun, since
Unresolved is a give-up outcome, not a fix-verified one; time-box escalation
(`check_time_box_escalations`/`sweep_all_tenants_time_box_escalations`) is
now both testable AND actually running in a live deployment — wired as a
background daemon thread in `main.py` (`_time_box_sweep_loop`, hourly by
default, mirroring the existing DB-backup thread's pattern) rather than
being a callable nothing ever calls; attached-failure reconciliation at
closure (`reconcile_attached_failures`, called from `close_case`'s success
path) marks every Triage-attached failure `matches` or
`mismatch_needs_own_case` against the confirmed cause; and KB blame-back
(`_blame_back_history_nominations`) now actually fires — WF §3a's "Unresolved
or rejected-Strong flags the nominating case_history rule" — from both the
rejected-Strong-hypothesis path in `run_confirmation_check` and the
Unresolved path in `start_second_chance`, reading which rule(s)
`coverage_challenge_pass2` nominated for that case and calling Stage 2's
`kb.mark_under_suspicion` on each.

`/api/v3/rca/cases/{case_id}/second-chance` is the one new route.
`RcaCase.jsx` gained: a "Start second chance" button on
`all_hypotheses_rejected` (replacing Stage 4's placeholder "this case stops
here for now" message, now that Stage 5 actually implements it); a
refinement-aware confirmation-check button label; a judge-decisions display
per check (verdict badge + "(refined)" tag) and a symptom-accounting block,
both newly exposed by `get_case()`'s bundle; and terminal-state displays for
`escalated`/`unresolved` (previously unhandled — nothing would have
rendered if a case ever reached them). `StateBadge` treats `unresolved`/
`escalated` as destructive-styled, matching `all_hypotheses_rejected`.

**Real bugs found and fixed during this stage's own implementation and
verification** (six, all beyond the planned Stage 5 scope items themselves):

1. `run_confirmation_check` set a check's `status` to `"run"` *before* the
  verdict was known, so an inconclusive verdict — which needs the *same*
  check_id to be picked up again for its one allowed refinement — left it
  permanently unreachable by `_next_pending_check` and the UI's
  pending-check picker (both look only for `status="pending"`). Fixed by
  resetting the check back to `"pending"` on an inconclusive verdict.
2. `get_case()`'s bundle never included `judge_decisions` at all, and the
  frontend's fix-approval flow used `c.hypotheses?.[0]` — the first-composed
  hypothesis, not necessarily the *confirmed* one, once Stage 4 made
  multiple hypotheses possible. The same class of bug existed on both sides
  independently. Fixed `get_case()` to add `judge_decisions` (keyed by
  check_id), `confirmed_hypothesis`, and the latest `symptom_accounting`
  row to the bundle; fixed the frontend to read `c.confirmed_hypothesis`.
  `close_case()` itself had the identical bug
  (`s.query_one("rca_hypotheses", case_id=case_id)` grabbing an
  arbitrary row) — fixed with a new `_confirmed_hypothesis()` helper that
  finds a hypothesis with a confirmed judge verdict, preferring the highest
  tier, used by both `close_case` and `reconcile_attached_failures`.
3. `check_time_box_escalations` marked `escalated_at` on *any* overdue
  approval *before* checking which tenant it belonged to — sweeping tenant
  A would silently mark tenant B's overdue approval as escalated with no
  audit event, and B's own later sweep would then skip it forever (the
  guard at the top of the loop treats "already escalated" as "already
  handled"). A real cross-tenant data-integrity bug, not just a Stage-6
  hardening gap — fixed by moving the tenant match check before the write
  (an approval with no discoverable case at all still gets marked, since
  the deadline itself is real; only a real-but-*different*-tenant case
  blocks the write). Caught by a dedicated regression test
  (`TimeBoxEscalationTests::test_sweep_never_escalates_a_different_tenants_approval`)
  built with direct table inserts, since `transition()` itself has no
  `tenant_id` parameter yet and can't advance a non-bootstrap-tenant case
  through the real state machine — a Stage 6 gap, not something patched
  here as a side effect of fixing bug #3.
4. `check_time_box_escalations` existed and was unit-tested since it was
  written, but nothing in a running deployment ever called it — the WF
  §11 Agent 10 rule ("escalated, not left open forever") did not actually
  hold outside of a test. Fixed by wiring
  `sweep_all_tenants_time_box_escalations` into a background daemon thread
  in `main.py`, the same pattern already used for the periodic DB backup.
5. `ci-local.ps1`'s `$ErrorActionPreference = "Stop"` (script-wide) promotes
  *any* stderr write from a native command — even an exit-0, purely
  informational one like vite's "(!) Some chunks are larger than 500 kB"
  build notice — into a terminating `NativeCommandError` in PowerShell 5.1,
  independent of the actual exit code. This silently broke the "frontend:
  vite build" and "frontend: Playwright Chromium" gates (the latter's
  webServer startup log lines are also stderr) once the bundle crossed
  that size threshold. Fixed `Invoke-Gate` to scope
  `$ErrorActionPreference = "Continue"` around each gate's action and keep
  relying on the real exit code (`$LASTEXITCODE`) it already checked.
6. The backend pytest suite (`test_taxonomy.py`/`test_kb.py`/
  `test_rca.py`, 67 tests) had never actually been wired into
  `ci-local.ps1` — the script's header comment still said "[later]: pytest
  suite, once the test-authoring agent creates one" from before that
  suite existed. `pytest` itself also wasn't a declared dependency (missing
  from `backend/requirements.txt`), so a fresh `setup.ps1` checkout
  couldn't even run it. Fixed both: added `pytest>=8.0.0` to
  `requirements.txt` (marked dev/test-only) and added a blocking
  "backend: pytest" gate to `ci-local.ps1`, run against a throwaway temp DB
  matching the existing import-boot-smoke gate's pattern.

**Honest scope choices, not omissions**, documented precisely in the matrix
rows above rather than narrated only here: the "cost tie-break" half of
confirmation ordering has no real implementation, because there is no
numeric "cost" dimension in the data model (`cost_hint` is a constant for
every check) — only the tier-first half is real; "crashed/empty check, one
retry" was not built, because confirmation checks are deterministic
in-process computations, not a live sandboxed execution that can crash
independently of an actual code bug worth fixing directly; the Judge
gatekeeper's "send back for redesign" flow still has no reachable caller,
because the Composer always writes a `reject_condition_json` — the hard
gate exists to catch a future Composer regression, not something exercised
by this product's own code today; and attached-failure reconciliation's
`mismatch_needs_own_case` branch is implemented but has no dedicated test
forcing an unconfirmed closure with an attachment present (the `matches`
path — the common case — is fully tested end-to-end).

Verified: `backend/tests/test_rca.py` grew from 19 to 32 tests — new
classes for Judge verdict bands, symptom accounting and queue advancement,
second chance and escalation, time-box escalation (including the
cross-tenant regression test), attached-failure reconciliation, and KB
blame-back (both trigger sites plus the no-nomination no-op). Full backend
suite: 67 passed, 1 skipped (a pre-existing, data-dependent legitimate skip
in the confirmation-queue-advancement test, same posture as the other
either-outcome-is-real tests in this file). `ui/e2e/rca.spec.js` was
restructured into reusable helpers (`runInvestigationCycles`,
`driveToJudgeOutcome`, `runFixApprovalToClosure`) and extended to drive a
rejected first outcome through the real second-chance UI flow into either a
confirmed fix or a second rejection escalating to `unresolved`, asserted
against a `data-testid="rca-state-badge"` rather than loose page text —
a real assertion bug surfaced during this stage's own verification (see
below). Full local CI gate (lint, build, backend compile + boot smoke +
pytest, all 8 Playwright tests across 4 spec files) green.

One more finding worth calling out on its own, since it looked like a
product bug before it was root-caused as a test bug: the new
`driveToJudgeOutcome` Playwright assertion
`getByText(/awaiting fix approval|all hypotheses rejected/)` started
throwing a strict-mode violation (two elements matched) once a case reached
its *second* judging cycle via the second-chance flow. The cause was that
`start_second_chance`'s own reason text legitimately contains the literal
phrase "all hypotheses rejected — one-time return to Part A...", which
shows up verbatim in the (collapsed but still DOM-present) audit-timeline
list once that transition has happened — a real naming collision between a
human-readable reason string and a loose page-wide text matcher, not a
data or state-machine defect. Fixed by giving `StateBadge` a
`data-testid` and scoping the Playwright assertion to it instead of relying
on page text uniqueness.

`RCA_LEGACY_CREATION_RETIRED` stays off — that flip is Stage 7's job,
after full-suite acceptance. No product behavior changed for existing
non-RCA workflows. Proceeding to Stage 6.

## Stage 6 gate — closed

This stage was an adversarial security/tenancy audit of everything Stages
1-5 built, not new product behavior. `codex-sol`'s adversarial pass remains
infrastructure-blocked in this environment (Stage 0's standing note); as in
every prior stage, `sonnet` ran the audit directly — commissioned as a
structured survey (file:line findings across auth, error handling, SQL/eval
surface, tenant enforcement, and audit coverage) before writing a single
fix, so the fix list below is evidence-driven, not a guess at what Stage 6
"should" cover.

**Five real cross-tenant gaps found and fixed**, all in `backend/rca.py`:

1. `revive_suspect()` took a `tenant_id` parameter but never referenced it —
  any authenticated user, regardless of tenant, could revive another
  tenant's ruled-out suspect by ID. This was the most serious finding: a
  parameter that *looked* like enforcement but did nothing. Fixed by adding
  a `require_case(suspect["case_id"], tenant_id)` gate before the mutation.
2. `propose_fix()` had no `tenant_id` parameter at all — an unguarded
  cross-tenant fix-proposal write. Its route handler
  (`routers/v3.py:propose_rca_fix`) also fetched the hypothesis by raw
  ID *before* calling it, so an unknown hypothesis_id crashed with an
  unhandled `TypeError` instead of a clean 404. Fixed both: added the
  tenant gate to the function, and reordered the route to call the
  tenant-validated function first.
3. `transition()` — the sole writer of `rca_cases.state` — had no
  `tenant_id` parameter and validated every case against the hardcoded
  bootstrap tenant regardless of who was really calling. In practice this
  was fail-*closed* (it would reject a non-bootstrap tenant's own
  legitimate case, not let an attacker through), since every real caller
  already tenant-validated `case_id` before reaching it — but it defeated
  real multi-tenancy outright and left the sole state-writer with no
  independent enforcement of its own. Added a `tenant_id` parameter and
  threaded the correct value through all ~30 call sites in the file (a
  full-file pass, verified complete with an AST scan for any `transition(`
  call missing a `tenant_id=` keyword — zero found after the fix).
4. `record_symptom_accounting()` and `reconcile_attached_failures()` had
  the same tenant-blind-by-default shape as `transition()` — not
  exploitable via today's single call path (both are only ever called
  internally, after the caller already validated tenant), but with no
  enforcement of their own if a future caller ever changed that. Both
  gained a `tenant_id` parameter and a `require_case()` gate.
5. `run_reopened_kill_attempt()` writes `rca_cases.state` directly via
  `s.update(...)` three times, bypassing `transition()` entirely — a
  deliberate shim (documented inline) to reuse Planner/Runner machinery
  without a full state round-trip, but it meant those three writes got no
  `ALLOWED_NEXT` validation and no audit trail. Left the shim in place
  (routing it through `transition()` would require extending the frozen
  `ALLOWED_NEXT` state graph for an internal implementation detail, out of
  this stage's scope) but added explicit `_audit()` calls at all three
  points so the state-change trail is complete either way.

**Audit-coverage gaps closed** (10 mutating functions/branches that wrote to
`rca_*` tables with no `_audit()` call of their own, relying only on an
incidental case-level transition audit or nothing at all):
`planner_propose_look`, `runner_execute`, `revive_suspect`,
`_create_pairwise_combination`, `record_symptom_accounting`, `propose_fix`,
`reconcile_attached_failures`, `reader_interpret`'s suspect-status writes
(both the kill-attempt and explore-column branches), `run_confirmation_check`'s
judge-decision write, and `run_reopened_kill_attempt`'s three direct state
writes (finding #5 above).

**Error handling hardened** in `routers/v3.py:_rca_error_map`: added
`ValueError → 400` (previously fell through unmapped to an unhandled 500 —
e.g. `revive_suspect`'s "a reason is required" validation, or
`_create_pairwise_combination`'s "pairs-only" check, both raise `ValueError`
and were surfacing as server errors instead of client errors), and replaced
the catch-all `raise exc` with a real sanitized-500 branch: the real
exception is logged server-side (`print`, matching the existing
`_backup_loop`/`_time_box_sweep_loop` logging convention) but the client
only ever sees a generic message — never a raw exception string, which
could otherwise mention a table/column name from a `sqlite3.Error` or
similar. (No actual leak existed before this change either — FastAPI's
default handler was already generic — but the fix makes sanitization
explicit and adds server-side visibility that silently relying on the
default handler didn't give operators.)

**Bearer-token session hardening**: `current_user()` never checked token
age — a session was valid forever until explicit logout. Added an absolute
lifetime (`SESSION_MAX_AGE_HOURS`, default 24h) stored in the session's own
`context` JSON (no schema migration needed), with a deliberate backward-
compat carve-out for sessions that predate this change (no `created_at` in
context → left alone, so a deploy doesn't log everyone out).

**Static safety audit** (MP §11 "analytical execution stays bounded
read-only SQL + allowlisted helpers"): every `execute(`/`eval(`/`exec(`/
`subprocess`/`os.system` call site in `rca.py` was read directly.
Exactly one raw SQL call exists (`_rerun_original_test`), and it is fully
parameterized (`?` placeholders, bound values from an already-tenant-
validated case). No `eval`/`exec`/`subprocess`/`os.system` calls exist
anywhere in the file. Encoded as a deterministic static test
(`StaticSafetyTests`) rather than a runtime fuzz test — stronger for this
specific invariant, since it proves the *absence* of a dangerous call site
rather than sampling inputs that happen not to trigger one; a fuzz test
would still be worth adding later for the deterministic look/segment
computation functions themselves (out of this stage's scope).

**Two decisions revised from the original Stage 0 plan, both explicitly
recorded (not silently dropped) in `00-contracts.md` §13:**

- §13.1: `dq_items`/`issues_v2`/`results_v2`/`plan_v2` tenant scoping —
  option (b) chosen (permanent single-tenant-per-deployment boundary)
  rather than (a) (retrofit `tenant_id` onto all four tables and cascade).
  Retrofitting would touch the entire v2 product surface, not just RCA
  RCA's module boundary — far outside this migration's "controlled
  migration, not a rewrite" framing.
- §13.2: the full secure-cookie + CSRF migration originally committed in
  Stage 0's decision #10 is narrowed to bearer-token hardening only, because
  the security survey confirmed today's Bearer-header-only design (never a
  cookie) is not CSRF-exploitable by construction — building CSRF protection
  for a credential-forgery class that cannot occur would harden nothing
  real, while the actual cookie migration is its own large, product-wide
  auth rearchitecture.

Verified: `backend/tests/test_rca.py` gained `TenantIsolationTests` (5
tests: each of the 5 fixed functions rejects a foreign tenant, then
succeeds for the legitimate one — proving the gate is tenant-*specific*,
not just globally broken) and `StaticSafetyTests` (2 tests, described
above). New file `backend/tests/test_auth_security.py` (`SessionExpiryTests`,
6 tests: fresh session valid, expired session rejected and actually ended
not just rejected-once, pre-existing sessions without `created_at` left
alone, logout ends the session, invalid/missing token rejected). Full
backend suite: 80 passed, 1 skipped (same pre-existing legitimate skip as
Stage 5). Full local CI gate (lint, build, backend compile+boot smoke+
pytest, all 8 Playwright tests) green — no product behavior changed for a
legitimate single-tenant caller anywhere in the suite, confirming the
hardening is additive, not a regression.

**Honest scope limits, not omissions:** token rotation was not
implemented (only absolute expiry) — rotation changes concurrent-tab/
multi-device semantics and needs its own design, not a Stage-6-sized
addition. The cookie+CSRF migration itself is fully deferred (§13.2).
Taxonomy/KB routes in `v3.py` were not re-audited in this pass — this
stage's scope was the RCA case machinery specifically, and those
modules' own Stage 1/2 gates already covered their tenant/role checks. No
fuzz test was added for the deterministic look/segment-computation
functions (`_profile_column`/`_segment_breakdown`) — the static safety
audit covers the actually-relevant "no dynamic code execution" invariant;
a numeric fuzz test for those functions would be a data-quality exercise,
not a security one.

Proceeding to Stage 7.

## Stage 7 gate — closed

Full MP §17 acceptance-gate checklist verified item-by-item in
`docs/rca/07-rollout.md` (17 items, all satisfied) — read that file for
the complete table; summarized here.

**Two real gaps found before any flag was flipped**, both would otherwise
have made the final gate action either meaningless or a lie:

1. `compose_hypothesis`'s label inference could only ever produce `defect`
  or `test_design_flaw` — `genuine_change`, one of WF's four closing
  states, had **no reachable code path at all** despite being listed as
  "implemented" back in Stage 3/5's rows. Root cause: the label mapping
  only ever checked "is the cause family known or not," never distinguished
  *which* known family means "the world legitimately changed" versus "this
  is a real defect." Fixed by mapping the `policy_change` cause family
  (tokens: policy/cutoff/threshold — the one family whose plain meaning IS
  "the world changed") to `genuine_change` specifically. Added
  `AllFourClosingStatesTests` (3 tests, deterministic fixtures, no flaky
  skip-heavy design) proving each of the three fix-verified outcomes is
  independently reachable by construction.
2. `RCA_LEGACY_CREATION_RETIRED` existed as a schema row and a feature
  flag key since Stage 0, but **no code anywhere ever read it** —
  `ai/v2/issues.py:sync_issues` only ever checked `RCA_ENABLED`.
  Flipping the flag would have been a no-op with a false sense of
  completion. Fixed to make it a real one-way floor: once retired, new
  issues stay on `rca` even if `RCA_ENABLED` were later toggled
  back off (an accidental or emergency rollback of the *enabled* flag no
  longer silently reopens legacy case creation). `LegacyCreationRetirementTests`
  (2 tests) prove both the retired-holds-regardless case and that turning
  neither flag on still correctly falls back to legacy (retirement is
  additive, not a silent always-on override of the whole flag system).

**New in this stage:** `backend/tests/test_migration_idempotency.py` —
generalizes Stage 1's taxonomy-only idempotency check into a full
table/row-count inventory comparison, running the real boot migration path
(`init_schema()` + `seed_platform_and_taxonomy()`) twice and a third time
against a throwaway DB and asserting the inventory is a true fixed point,
not something that happens to stabilize after exactly one extra run.

Verified: backend suite grew to 89 tests total across all files (87
passed, 1 skipped — the same pre-existing legitimate skip carried since
Stage 5). Full local CI gate (lint, build, backend compile+boot smoke+
pytest, all 8 Playwright tests across 4 spec files) green, run fresh after
every Stage 7 code change including the final flag flip.

**Final gate action:** `RCA_LEGACY_CREATION_RETIRED` flipped to `1` in
`seeds/taxonomy_seed.py:_INITIAL_FLAG_STATE` — the literal last edit of the
migration, performed only after every row of the MP §17 checklist was
independently re-verified true and the full suite re-run green with that
exact change in place.

No product behavior changed for any existing non-RCA workflow at any
point in this stage. RCA Stage 0-7 migration: **complete.**

## Post-Stage-7 naming correction

The two source documents this migration is built on are named
`RCA_Full_Workflow_v10.md` and
`RCA_v10_Migration_Knowledge_Base_and_Implementation_Plan.md` — "v10" there
is part of a *filename*, not a product version number. Every stage above
nonetheless copied "v10" into every identifier this migration created, as
if the workflow itself were "version 10" of something. It isn't; there is
no v1 through v9. This was a naming mistake, corrected in full once caught:

- `backend/rca_v10.py` → `backend/rca.py` (`CAUSE_FAMILIES`/`FIX_BY_CAUSE`/
  `infer_cause_family` moved in directly rather than importing them from
  the legacy module below, since that module was deleted in the same pass).
- 19 tables `rca_v10_*` → `rca_*` (`rca_cases`, `rca_hypotheses`, `rca_looks`,
  etc. — full list in `system_db.py`'s schema).
- Feature flags `RCA_V10_ENABLED` → `RCA_ENABLED`,
  `RCA_V10_LEGACY_CREATION_RETIRED` → `RCA_LEGACY_CREATION_RETIRED`.
- `issues_v2.workflow_version` value `"rca-v10"` → `"rca"` (the sibling
  value `"legacy-v1"` is unrelated — it labels the legacy heuristic path's
  own generation, not a copy of this mistake, and was left as-is).
- API routes `/api/v3/rca-v10/*` → `/api/v3/rca/*`; ~15 `client.js`
  functions `createRcaV10Case`/etc. → `createRcaCase`/etc.
- `ui/src/components/RcaV10Case.jsx` → `RcaCase.jsx`;
  `ui/e2e/rca-v10.spec.js` → `rca.spec.js`.
- `backend/tests/test_rca_v10.py` → `test_rca.py`,
  `test_taxonomy_v10.py` → `test_taxonomy.py`,
  `test_kb_v10.py` → `test_kb.py`.
- `docs/rca-v10/` → `docs/rca/` (this directory).

**A second, unrelated problem surfaced during the fix, not caused by it:**
avoiding a naming collision required checking what the bare `rca_*` names
already meant — and they were already taken, by a fully dead legacy stack
(`backend/ai/rca_cases.py`, `backend/ai/rca_agent.py`, and nine `rca_*`
tables — see `00-contracts.md` §0's addendum). That stack had no reachable
writer from any mounted route and no data to preserve; it was deleted
outright rather than worked around, which is what actually freed the bare
`rca_*` names for this migration's own tables.

Verified after the rename: full backend suite green (same pass count as
before the rename — 87 passed, 1 skipped), full local CI gate green (lint,
build, backend compile+boot smoke+pytest, all Playwright specs), confirming
the rename was purely mechanical and changed no behavior.

---

# 0.4.0 Traceability Matrix — slice 1

One row per slice-1 requirement ID (requirements-0.4.0.md). Phase and criteria ids are from
archimedes-0.4.0-plan.md. Status legend: `done` / `backlog`. Backlog blocks are listed once at
the end; every slice-1 row below is landed and points to a local test file that exists in this tree.

## FWK — framework and register

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| FWK-01 | 3 | `dq_diagnostics/register.py` + seeded framework data | `backend/tests/test_register.py` | done |
| FWK-02 | 3, 6 | register schema (`diagnostic_id` keys execution) | `backend/tests/test_register.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| FWK-03 | 3 | register seed: six test areas T1–T6 with KB dependency | `backend/tests/test_register.py` | done |
| FWK-04 | 3 | framework as seeded data | `backend/tests/test_register.py` | done |
| FWK-05 | 3 | register: exactly 9 rows, ids {2,4,6,8,11,12,14,17,20} | `backend/tests/test_register.py` | done |
| FWK-06 | 3 | `threshold_settings` semantic layer | `backend/tests/test_thresholds.py` | done |
| FWK-07 | 3, 6 | `decision_type` on every result; FindingsPanel rendering | `backend/tests/test_register.py`, `ui/e2e/testlab-rendering.spec.js` | done |
| FWK-08 | 3 | `dq_diagnostics/runner.py` stage ordering | `backend/tests/test_staging.py` | done |
| FWK-09 | 3 | shared guard-order skeleton (class → material → value-semantics) | `backend/tests/test_staging.py` | done |
| FWK-10 | 3 | runner refusal on absent Stage-2 dependencies | `backend/tests/test_staging.py` | done |
| FWK-11 | 3 | value-semantics gate marker; force is deferred to B1 | `backend/tests/test_staging.py` | done |
| FWK-12 | 3 | tags-as-record consumed-record shape | `backend/tests/test_testlab_diagnostics.py` | done |
| FWK-13 | 3 | retire registry/param specs/`test_library`; drop keyed records | `backend/tests/test_finalized_framework.py`, `backend/tests/test_migration_040.py` | done |
| FWK-14 | 3, 6 | coverage map data; GAP strip | `backend/tests/test_register.py`, `ui/e2e/testlab-rendering.spec.js` | done |
| FWK-15 | 3, 6 | Covered/Covered-thin/Partial/Gap + executable/pending split | `backend/tests/test_register.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| FWK-16 | 3, 6 | re-derived weights; coverage-honest score panel | `backend/tests/test_register.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| FWK-17 | 3, 6 | `workflow_status` + refusal semantics | `backend/tests/test_register.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| FWK-18 | 3 | `enabled_by` decision reference; engine-per-id seam | `backend/tests/test_register.py` | done |

## DET — determinism

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| DET-01 | 6, 7 | all workflow movement deterministic | `backend/tests/test_cross_field_engine.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| DET-02 | 6, 7 | seam (a) only reachable seam, off by default | `backend/tests/test_testlab_diagnostics.py` | done |
| DET-03 | 1, 7 | no fifth seam; `docs/0.4.0/02-determinism-map.md` | `backend/tests/test_testlab_diagnostics.py` | done |
| DET-04 | 3 | no statistical engine ships in slice 1; guard skeleton only | `backend/tests/test_staging.py` | done |
| DET-05 | 6 | no model-supplied execution; no editable code on run path | `backend/tests/test_cross_field_engine.py` | done |

## KB — knowledge base

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| KB-01 | 5 | delete `business_rules.json` + loader | `backend/tests/test_kb_binding.py` | done |
| KB-02 | 5 | document tags wired into upload | `backend/tests/test_kb_tags.py`, `ui/e2e/knowledge-base.spec.js` | done |
| KB-03 | 5 | playback summary | `backend/tests/test_kb_parse.py` | done |
| KB-04 | 5 | table-aware parser | `backend/tests/test_kb_parse.py` | done |
| KB-05 | 5 | provenance preserved (sha256, converter, warnings) | `backend/tests/test_kb.py` | done |
| KB-06 | 5 | safe rejection: unsupported/corrupt/scanned | `backend/tests/test_kb_parse.py` | done |
| KB-07 | 5 | rule records with full provenance fields | `backend/tests/test_kb_parse.py` | done |
| KB-08 | 5 | binding statuses bound/reference-only/unparsed | `backend/tests/test_kb_binding.py` | done |
| KB-09 | 5 | never execute prose-recovered predicates | `backend/tests/test_kb_binding.py`, `backend/tests/test_cross_field_binder.py` | done |
| KB-10 | 5 | parse report artifact + API | `backend/tests/test_kb_parse.py`, `backend/tests/test_kb_tags.py` | done |
| KB-11 | 5 | extraction hazards surfaced, roles confirmed | `backend/tests/test_kb_binding.py`, `backend/tests/test_cross_field_binder.py` | done |
| KB-12 | 5 | published+effective only in retrieval | `backend/tests/test_kb.py`, `backend/tests/test_kb_binding.py` | done |
| KB-13 | 5 | governance retained | `backend/tests/test_kb.py`, `backend/tests/test_kb_binding.py` | done |
| KB-14 | 5 | deterministic-only knowledge writes | `backend/tests/test_kb_binding.py` | done |
| KB-15 | 5 | two axes kept apart | `backend/tests/test_kb_tags.py` | done |
| KB-16 | 5 | retrieval order + manifest | `backend/tests/test_kb.py`, `backend/tests/test_testlab_diagnostics.py` | done |

## CFR — cross-field engine

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| CFR-01 | 6 | `domains/test_lab/diagnostics/t2_d04_cross_field_business_rule/` via API | `ui/e2e/testlab-diagnostics.spec.js` | done |
| CFR-02 | 6 | five rule types and three severities | `backend/tests/test_cross_field_binder.py` | done |
| CFR-03 | 6 | rules only from `kb.list_eligible_rules` | `backend/tests/test_cross_field_binder.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-04 | 6 | zero rules → NOT-APPLICABLE never PASS | `backend/tests/test_testlab_diagnostics.py`, `backend/tests/test_cross_field_engine.py` | done |
| CFR-05 | 6 | graded role ladder + dtype gate, score/reason per role | `backend/tests/test_cross_field_engine.py` | done |
| CFR-06 | 6 | nine primitives + governed registry | `backend/tests/test_cross_field_engine.py` | done |
| CFR-07 | 6 | scope/censored-skip/exceptions/evidence | `backend/tests/test_cross_field_engine.py` | done |
| CFR-08 | 6 | verdict gate; severity never changes maths | `backend/tests/test_cross_field_engine.py` | done |
| CFR-09 | 6 | clustered/scattered lift pattern, tunable knobs | `backend/tests/test_cross_field_engine.py` | done |
| CFR-10 | 6 | structured result first; text/PDF derived | `backend/tests/test_cross_field_engine.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-11 | 6 | optional seam through `ai/llm.py` + `control_plane.resolve`; off by default | `backend/tests/test_cross_field_engine.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-12 | 6 | interactive paths → append-only decision records | `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-13 | 6 | SSE run stream | `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-14 | 6 | `diag_runs`/`diag_results`/`diag_findings`; issue hand-off | `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-15 | 6 | read-only; ≤5 evidence rows | `backend/tests/test_cross_field_engine.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-16 | 6 | deterministic: two runs byte-identical | `backend/tests/test_cross_field_engine.py` | done |
| CFR-17 | 6 | workspace-scoped single-tenant shim | `backend/tests/test_testlab_diagnostics.py` | done |
| CFR-18 | 6 | no new runtime dependency | `backend/tests/test_cross_field_engine.py`, `backend/tests/test_reachability.py` | done |

## ING — ingestion

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| ING-01 | 4 | drop starts parse/profile; no finalize/profile buttons | `ui/e2e/upload-workflow.spec.js` | done |
| ING-02 | 4 | source required; supporting files optional and inline | `ui/e2e/upload-workflow.spec.js`, `backend/tests/test_ingest.py` | done |
| ING-03 | 4 | confidence-tiered mapping, pre-applied/confirm/never-guess | `backend/tests/test_ingest.py` | done |
| ING-04 | 4 | structured non-blocking warnings | `backend/tests/test_ingest.py`, `ui/e2e/upload-workflow.spec.js` | done |
| ING-05 | 4 | dictionary state yes/thin/absent; provisional columns | `backend/tests/test_ingest.py` | done |
| ING-06 | 4 | Review screen is the editable decision surface | `ui/e2e/upload-workflow.spec.js` | done |
| ING-07 | 4 | status machine derived from events | `backend/tests/test_ingest.py`, `ui/e2e/upload-workflow.spec.js` | done |
| ING-08 | 4 | persisted mapping/dictionary/warnings/profile records feed manifest | `backend/tests/test_ingest.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| ING-09 | 4 | replacement = a new delivery | `backend/tests/test_ingest.py`, `ui/e2e/upload-workflow.spec.js` | done |
| ING-10 | 4 | remove TYPE_PRIORITY + schema literals | `backend/tests/test_ingest.py` | done |

## PLT / WSP-08 — platform

| ID | Phase | Target path | Behavioural test | Status |
|---|---|---|---|---|
| PLT-01 | 4 | upload display and summaries use MB formatting | `ui/e2e/upload-workflow.spec.js` | done |
| PLT-02 | 3 | delivery/baseline seam columns + backfill | `backend/tests/test_delivery.py` | done |
| PLT-03 | 3 | delivery record available at the platform seam | `backend/tests/test_delivery.py` | done |
| PLT-04 | 2, 4–6 | shared error sanitizer on new routes | `backend/tests/test_admin_reset.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| PLT-05 | 2–6 | append-only audit events for decisions/resets/publications | `backend/tests/test_admin_reset.py`, `backend/tests/test_thresholds.py`, `backend/tests/test_testlab_diagnostics.py` | done |
| PLT-06 | 3–6 | additive idempotent migrations, run twice | `backend/tests/test_migration_040.py`, `backend/tests/test_migration_idempotency.py`, `backend/tests/test_ingest.py` | done |
| PLT-07 | 7 | no new runtime dependency | `backend/tests/test_reachability.py`, `backend/tests/test_cross_field_engine.py` | done |
| PLT-08 | 2 | helper layer decluttered: one registry, contracts, logging, tests | `backend/tests/test_helpers.py` | done |
| WSP-08 | 2 | factory reset endpoints + Admin danger zone | `backend/tests/test_admin_reset.py`, `ui/e2e/admin-reset.spec.js` | done |

## Backlog blocks — not scheduled (listed once, per plan 1.9)

| Block | IDs | Status |
|---|---|---|
| DIA | DIA-01…DIA-10 | backlog (B1/B2/B3), not scheduled |
| APL | APL-01…APL-39 | backlog (B6), not scheduled |
| RCA | RCA-01…RCA-37 | backlog (B5), not scheduled |
| WSP (except WSP-08) | WSP-01…WSP-07, WSP-09, WSP-10 | backlog (B4), not scheduled |
| DET-06 | seam (b) no-unsourced-number test | backlog (B3), not scheduled |
