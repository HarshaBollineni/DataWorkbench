# RCA — Stage 7 Rollout Gate

MP §17's acceptance gate, verbatim, each item mapped to the real evidence
that satisfies it. Written at Stage 7 start per the Stage 0 self-check note
("verified by the Stage 7 gate checklist directly ... rather than
duplicated [in the traceability matrix] per-rule").

| # | Acceptance criterion | Status | Evidence |
|---|---|---|---|
| 1 | all new cases use RCA | ✅ | `RCA_ENABLED=1` (default since Stage 3); `RCA_LEGACY_CREATION_RETIRED=1` (Stage 7, this gate) makes it a one-way floor — see `ai/v2/issues.py:sync_issues`, tested by `test_rca.py::LegacyCreationRetirementTests` |
| 2 | legacy cases remain intact and readable | ✅ | `issues_v2.workflow_version` additive column, default `'legacy-v1'`; `IssueRca.jsx` branches on it, legacy heuristic path (`ai/v2/issues.py:rca()`) untouched by any RCA stage |
| 3 | new cases have no pre-seeded causes | ✅ | `rca.py:create_case_from_issue` creates no hypothesis/suspect row; `test_rca.py::test_no_pre_seeded_causes` |
| 4 | every hypothesis cites evidence | ✅ | `compose_hypothesis` always populates `evidence_look_ids_json` (falls back to the full trail when a suspect has none); `test_04_composer_produces_tiered_hypotheses_with_evidence` |
| 5 | tags propagate immutably from data to RCA | ✅ | `taxonomy.inherit_tags()` snapshot chain, Stage 1; `test_taxonomy.py::test_downstream_snapshot_survives_source_tag_removal`, `::test_full_propagation_chain_item_to_issue` |
| 6 | Knowledge Base retrieval is tenant-scoped, governed, versioned, and audited | ✅ | `kb.py:list_eligible_rules` filters by `tenant_id`+category+`lifecycle_state='published'`; `kb_document_versions` versions every upload; every retrieval call persists a `kb_retrieval_manifests` row (which rules, to whom, for which case) — the audit trail for retrieval specifically, distinct from `rca_audit_events`' mutation audit |
| 7 | Planner and Reader cannot access historical causes | ✅ | `kb.py:AGENT_CATEGORY_MATRIX` excludes both from `case_history`; `test_kb.py::test_retrieval_respects_agent_category_matrix_and_quarantine` |
| 8 | coverage pass 1 is blind | ✅ | `coverage_challenge_pass1` reads only `rca_look_executions`, never suspects or KB; `test_03_loop_runs_to_a_stop_condition_then_coverage_challenge` |
| 9 | confirmation checks can reject hypotheses | ✅ | Gatekeeper requires `reject_condition_json` before a check can run (`test_gatekeeper_rejects_a_check_without_a_reject_condition`); `_judge_verdict` returns `rejected` on a real evidence band, not just theoretically (`JudgeVerdictTests`) |
| 10 | fixes require human approval | ✅ | `propose_fix` only proposes; `approve_fix` is a distinct human action; no code path applies a fix automatically; `test_06_human_approval_required_before_fix_application` |
| 11 | closure requires the original test to pass on rerun | ✅ (for the three fix-verified outcomes) | `close_case`'s confirmed/genuine_change/test_design_flaw path always calls `_rerun_original_test` (or an injected `rerun_fn` in tests) and only closes on `status=='pass'`; a failing rerun leaves the case at `closure_rerun` honestly, never fabricating success (`test_closure_without_a_real_fix_stays_honestly_open`). **Note:** the fourth outcome, Unresolved, is a give-up ending after the second-chance loop is exhausted — nothing was fixed, so there is nothing to rerun; `start_second_chance` writes that closure directly. This is the correct reading of the rule (it governs the outcomes where a fix was applied), not a gap. |
| 12 | second chance occurs no more than once | ✅ | `_second_chance_used` checked before granting; a second exhaustion escalates straight to Unresolved instead of looping again; `SecondChanceAndEscalationTests::test_second_chance_granted_once_then_escalates_to_unresolved` |
| 13 | all four endings work | ✅ | Confirmed/Unresolved were already reachable; Genuine Change had **no reachable path at all** until this stage (`compose_hypothesis`'s label inference could only ever produce `defect` or `test_design_flaw` — fixed to map the `policy_change` cause family to `genuine_change`); all three non-Unresolved outcomes now covered by `AllFourClosingStatesTests`, Unresolved by `SecondChanceAndEscalationTests` |
| 14 | uploaded sources and Markdown retain provenance | ✅ | `kb.py:upload_document`/`store_original` — original bytes + SHA-256 preserved, converted Markdown + its own SHA-256 stored separately, converter name/version recorded; `test_kb.py::test_upload_preserves_original_bytes_and_hash` |
| 15 | no synthetic fixture is installed as production knowledge/data | ✅ | `backend/seeds/synthetic_kb_seed.py:load_synthetic_kb` is never called from `seed_all()`/`main.py`; `test_kb.py::test_never_loaded_on_boot_then_explicit_load_produces_expected_states` |
| 16 | tenant, migration, security, backend, frontend, and browser tests pass | ✅ | Tenant: Stage 6 `TenantIsolationTests` (5 tests). Migration: Stage 7 `test_migration_idempotency.py` (2 tests, schema+seed stable across 2nd and 3rd runs). Security: Stage 6 `StaticSafetyTests`, `test_auth_security.py`. Backend: 87 passed / 1 skipped (`pytest tests -q`). Frontend/browser: `ci-local.ps1` — lint, build, all 8 Playwright tests across 4 spec files, green. |
| 17 | workflow, governance, migration, limits, and rollback are documented | ✅ | `00-contracts.md` (workflow/governance/migration/security contracts + §13 Stage 6 revisions), `01-traceability-matrix.md` (every rule → owner/path/test/status, 7 stage closure notes), this file (rollout gate), `00-contracts.md` §10 (migration + rollback plan) |

## Stage 7 actions taken, in order

1. Verified every row above against real code/tests (not re-asserted from
   memory) — the two genuine gaps found (`genuine_change` unreachable;
   `RCA_LEGACY_CREATION_RETIRED` not wired to any actual logic) were
   fixed first, before flipping any flag.
2. Added `backend/tests/test_migration_idempotency.py` — runs the real boot
   migration path (`init_schema()` + `seed_platform_and_taxonomy()`) twice
   and a third time against a throwaway DB, comparing the full table/row-
   count inventory each time (MP §9's "migration run twice ⇒ identical
   schema/row counts", generalized beyond the taxonomy-only idempotency
   check Stage 1 already had).
3. Fixed `compose_hypothesis`'s label inference (`rca.py`) so
   `genuine_change` has a real reachable path (mapped from the
   `policy_change` cause family) — added `AllFourClosingStatesTests` (3
   tests) proving all three fix-verified outcomes are individually
   reachable by construction, not just by accident of test data.
4. Fixed `ai/v2/issues.py:sync_issues` so `RCA_LEGACY_CREATION_RETIRED`
   is an actual one-way floor beneath `RCA_ENABLED`, not an inert flag
   nothing reads — added `LegacyCreationRetirementTests` (2 tests: retired
   holds even with the enabled flag off; neither flag on still falls back
   to legacy, proving the retirement flag is additive, not a silent
   always-on).
5. Full local CI gate run green (lint, build, backend compile+boot smoke+
   pytest — 87 passed/1 skipped, all 8 Playwright tests).
6. Flipped `RCA_LEGACY_CREATION_RETIRED` to `1` in
   `seeds/taxonomy_seed.py:_INITIAL_FLAG_STATE` — the last action, after
   every row above was independently verified true, per contracts.md §8's
   "never ahead of that stage's gate passing."

## Rollback

Unchanged from `00-contracts.md` §10: set `RCA_ENABLED=false` to stop
new RCA case creation (note: with `RCA_LEGACY_CREATION_RETIRED=1` now
also set, new issues will still route to `rca` per action #4 above —
rolling back case *creation* behavior all the way to legacy requires
flipping both flags together, which is the correct, deliberate two-key
behavior this stage built, not an oversight). A full schema-level revert
drops only `rca_*`-prefixed tables; legacy tables/data are never
touched. The two additive columns on shared tables
(`issues_v2.workflow_version`, `users.tenant_id`) remain as permanent,
harmless residue of even a full rollback (documented in `00-contracts.md`
§10 since Stage 0).

## Carried-forward honest scope limits (not fixed at Stage 7, by design)

These were each already documented at the stage that found them and are
listed here only as a single consolidated pointer, not repeated in detail:
Triage's structural-first *queue* ordering (no batch queue exists in this
product); the complaint-guess-is-just-one-suspect path (no complaint UI);
Intake's KB-based expected-change suppression; the symptom-size interaction
trigger (no reliable numeric "size" proxy across test families); crashed/
empty-check-retry (no live sandboxed check execution to crash); the Judge
gatekeeper's redesign-flow (no reachable caller — the Composer never
produces a check without a reject condition); cost-based confirmation
tie-breaks (no cost dimension in the data model); attached-failure
reconciliation's `mismatch_needs_own_case` branch (implemented, not
dedicated-tested); token rotation and the full secure-cookie+CSRF migration
(`00-contracts.md` §13.2); `dq_items`/`issues_v2`/`results_v2`/`plan_v2`
staying single-tenant permanently (`00-contracts.md` §13.1). None of these
block the MP §17 acceptance gate above — none is an item on that list.

**Gate: closed.** RCA migration complete.
