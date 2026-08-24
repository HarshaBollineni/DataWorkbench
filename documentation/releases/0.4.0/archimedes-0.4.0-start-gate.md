# Archimedes 0.4.0 — Phase 0 Start-Gate Report

**Date:** 30 July 2026 (late evening) · **Executor:** autonomous plan-driven orchestrator
**Verdict: baseline VERIFIED — proceed to Phase 1.** No product file was modified in Phase 0.

## 1. Git state (0.4)

- Repo: `source-codes/`, branch `dev`, HEAD `c0e8e0672d6094f80ac53704ca8640af715724da` (= plan baseline `c0e8e06`, version 0.3.0).
- Working tree: exactly the expected tranche-1 state — 28 deleted files (5 unmounted routers, 3 dead `ai/` executors, 14 `dq_tests/stage1+stage2` shims, 4 unrouted UI pages; `git diff --stat`: 28 files, 4,101 deletions) plus untracked `synthetic-kb/KB_cross_field_reference_2.pdf` (S9, relocated per rev-3 item 5). Nothing else.
- Recent history: c0e8e06 (rca boot fix) ← d93aeca (0.3.0 release) ← RCA v10 stages.

## 2. Baseline validation (0.5)

| Command | Exit | Result |
|---|---|---|
| `./ci-local.ps1` | 0 (all gates PASS) | eslint PASS (advisory) · vite build PASS · backend compile PASS · import-boot smoke PASS (10 routes) · **pytest 89 passed / 1 skipped** · **Playwright 8/8 passed** |
| `python backend/spike_recalibrate.py` | 1 | **BROKEN AT BASELINE** — `ModuleNotFoundError: No module named 'database'` |
| `python backend/spike_gx.py` | 1 | Same root cause |
| `python backend/spike_integration.py` | 1 | Same root cause |
| `python backend/verify_plan8.py` | 1 | Same root cause via `ai/test_manager.py:19` |

**Finding F-01 (baseline, pre-existing):** `backend/database.py` was deleted in the 0.2.0 release
(commit `5ba1b13`), which broke `spike_recalibrate.py`, `spike_gx.py`, `spike_integration.py`,
`verify_plan8.py`, `ai/test_manager.py:19`, and `ai/rca_helpers.py:16` — the same standing defect
PLT-08 names. Consequences for this release:
- `verify_plan8.py` was already scheduled for a Phase-3 rebuild (plan §1.4); its baseline value is
  reference-only and it is *currently not even runnable* — the Phase-3 rebuild is mandatory, not optional.
- The three `spike_*.py` PSI-parity scripts cannot be "kept green" (plan §1.4) because they are not
  green at baseline. They are preserved untouched as PSI parity **evidence** (§1.3) and must be
  repaired when backlog item B2 (PSI) is pulled. Recorded; not fixed in slice 1 (rule 14 — do not
  invent scope; PSI is backlog).
- `rca_helpers.py:16` / `tool_registry.py:148,217` broken imports are Phase 2 work items (2.4), as planned.

## 3. Record inventory for the Phase-3 drop (0.6)

**There is no populated system DB in the baseline working tree.** `backend/system_state.db` does not
exist (the app creates and seeds it at boot; it is gitignored). `.e2e/system_state.db` is Playwright
scratch state. One orphan upload folder exists (`backend/uploads/item_818d58b19941`) with no DB
referencing it. Therefore:
- Pre-drop record counts per table will be recorded **at Phase 3 execution time** from the DB as it
  exists then (after Phase-2 boots), per plan 3.1 — expected to be platform seeds only
  (fw_areas/fw_tests/test_library from `dq_framework_data.json`, taxonomy seeds, admin user).
- No customer/user data is at risk in the framework swap on this instance. The §1.3 preserve list is
  enforced by migration tests regardless (3-T4).

## 4. Inputs read (0.1–0.3)

- Plan rev 4, requirements rev 5 (all 13 sections), testlab-redesign (all 7 sections): read in full.
- **S8** `MVP_Test Plan_DA.xlsx` (re-issued 30 Jul): three green tabs decoded, `#F79646` columns
  ignored. Confirmed: *Diagnostics detail* row numbering gives diagnostic id = sheet row − 1; the 9
  `MVP Scope = Core` rows are exactly #2, #4, #6, #8, #11, #12, #14, #17, #20; footnote R46 fixes
  "MVP CORE is 9 buildable diagnostics"; *Detailed_DQ_Framework* = 6 L1 themes → 11 L2 areas
  (Sample & Representativeness ×3, Target & Outcome Integrity ×1, Feature & Predictor Quality ×4,
  Dataset Stability & Monitoring ×1, Upstream Dataset & Pipeline Integrity ×1, Third party data
  quality and controls ×1); *Plan A* = 6 test areas T1–T6 with KB dependencies Low/HIGH/Medium/
  Low-Med/Medium/reads-writes.
- **S10** `cross_field_engine_V2_workflow_1.png`: steps 0–6 + verdict gate; inputs dataset required,
  dictionary.json/mapping.json optional; use-case IRB 38 / IFRS9 13; score ladder exact 1.0 ·
  synonym .92 · dict-desc .88 · token .80 · fuzzy .55–.75 + dtype gate; LLM step 3b optional/off by
  default/mapping-only; guarantees "LLM touches mapping only, never a verdict", "Nothing silent —
  N-A always stated".
- **S9** `KB_cross_field_reference_2.pdf` (4 pages): 49 rules — IRB 36 / IFRS9 11 / both 2;
  types conditional 13, date-ordering 3, domain/bound 26, identity/derivation 3, inequality 4;
  severities CRITICAL 6 / MATERIAL 21 / MINOR 22; page-4 encoded exceptions (IRB-03/04/33/35) and
  the verdict-logic block. These are the 5-T1/6-T1 parity numbers.
- **S5** `cross_field_rule_engine.py` (1,374 lines): structure mapped — 5 rule types, 9 primitives
  (`ineq, dateorder, identity, dom_range, dom_set, presence, eq_cond, in_cond, present_number`) +
  `evaluate_rule` + `eq_cond_num` internal helper; `resolve_roles` ladder at :690–757; interactive
  seams `choose_use_case` :1084, `_resolve_conflict_with_user` :1212, `_llm_call` :1131
  (`CFR_LLM_API_KEY`). Full line-level port happens in Phase 6.
- **S6** `report.txt`: full output contract captured (preamble fields, roll-up 41/7/1, violations
  ordered CRITICAL→MATERIAL→MINOR, per-violation resolved-roles/scope/rate/tolerance/exceptions-with-
  reason-and-count/≤5 evidence rows, PASS lines, N-A stated with reason).
- **S7** `requirements_1.txt`: pandas>=2.0 + openpyxl>=3.1 (already satisfied); `anthropic>=0.40`
  optional — rejected per C-04/CFR-18.
- **S1/S2/S3/S4** (RCA + APL, backlog B5/B6): read via the rev-5 consolidation §6/§7; a fidelity
  check of the consolidation against the raw sources was run separately (result: see note below).
  Full re-read is scheduled when B5/B6 are pulled — they produce no slice-1 code.
- Repo docs (`README.md`, `TSD.md`, `VERSIONING.md`, `TODO.md`, `guide/Workflows_and_Testing_Guide.md`)
  and the 0.3 change-target inspection: mapped (module map recorded for Phase-2 use).

## 5. Reachability candidates for the Phase-2 sweep (0.6)

Candidates (redesign §4.1 + plan §1.3): unmounted routers `ai_rules`, `inventory`, `monitoring`,
`preview`, `skills`, `tickets`, `use_cases`; unrouted pages `Dashboards.jsx`, `AgenticSkills.jsx`,
`AIAgents.jsx`, `Placeholder.jsx`, `RCA.jsx`. Only `auth`, `admin`, `v2`, `v3` routers are mounted
(`main.py`). Evidence-standard verification (imports, lazy routes, client.js references) executes in
Phase 2.10.

## 6. First slice to cut

Phase 1 (contracts, docs only) immediately; then Phase 2 beginning with the tranche-1 commit (2.1).

## 7. Assumptions / risks carried forward

- A-01: Spike scripts stay broken through slice 1 (baseline defect, PSI is backlog B2). Risk: none to
  slice-1 gates — `ci-local.ps1` does not invoke them.
- A-02: DB record counts for 3.1 will be taken from the boot-seeded DB at Phase-3 time (no user data
  exists on this instance).
- A-03: `ci-local.ps1`'s Playwright stage runs against `.e2e/` scratch state; `backend/uploads/item_*`
  orphans are e2e leftovers, not user data.
