# Archimedes 0.4.0 — Implementation Plan

**Audience:** the AI engineer implementing this release. This document is your instruction set.
**Authority:** [requirements-0.4.0.md](requirements-0.4.0.md) is the authority on *what*. This document
is the authority on *how*, *in what order*, and *when you may call something done*. For the Test Lab
module, [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) is the authority on the workflow and
architecture (D-16).
**Baseline:** `source-codes/` on branch `dev` @ `c0e8e06`, version `0.3.0`, **plus the tranche-1
dead-code deletion of 30 Jul** (28 files, uncommitted — see §1.3; verified by pytest 89/1 and a clean
vite build).
**Target:** `0.4.0` — **slice 1 only**: one executable diagnostic (#4 cross-field), the rebuilt Test
Lab, the redesigned ingestion, the KB path that feeds it, and a decluttered helper layer. Everything
else is **backlog** (§10), pulled in one item at a time under the controlled-scaling gate (D-22).
**Revision:** 4 — 30 July 2026 (late evening). Supersedes revision 3 in full. **Re-sequenced into 8
phases** on requester instruction: helper-layer decluttering moved upfront (D-21), data ingestion
added (D-20), Test Lab moved early by merging it with the cross-field engine phase, and all
post-slice-1 work moved to the backlog.

---

## 1. Operating rules

Read once, then obey for the whole release. Not advisory.

### 1.1 Non-negotiables

1. **Extend in place; replace deliberately.** The framework, the old test registry, the Test Lab
   wizard, the Data Sourcing flow and the helper-layer tangle are being *replaced* — that is intended.
   Everything else — auth, admin, KB governance, taxonomy, issues, RCA — must remain operational at
   the end of every phase.
2. **No second module.** No `kb_new.py`, no parallel framework, no `test_kit_v2.py`. Replace inside
   the existing structures.
3. **No domain knowledge in code.** No rule set, no business rule, no regulatory bound, no schema
   name-hint ships as a Python literal or a JSON file loaded at runtime (KB-01, ING-10). The framework
   *definition* is not domain knowledge and may ship (FWK-04 / C-37).
4. **Determinism is the default.** Workflows move by Python. The LLM appears at exactly four seams
   (DET-02); slice 1 reaches only seam (a) — optional, off by default — and seam (b) when the report
   commentary lands (backlog B3). Adding a seam is a design change requiring a recorded decision.
5. **Behavioural tests only.** A test asserting a symbol exists, a route is registered, a value
   `is not None`, or that a call returns without raising is **not acceptance evidence**.
6. **For every invariant, prove the test bites.** Remove or invert the invariant and confirm the test
   fails. Do this for every rule marked invariant: FWK-10…FWK-12, FWK-17, KB-09, KB-14, CFR-15…CFR-17,
   WSP-08's admin gate.
7. **Never relax a failing test to make a gate pass.** When `verify_plan8.py` or
   `test_finalized_framework.py` must change for the framework replacement, rewrite them against the
   new register **in the same commit as the cause**, with the new counts asserted.
8. **Read-only against user data.** No diagnostic or profiling step ever writes to or alters an
   uploaded dataset.
9. **No model-supplied execution.** No model-supplied SQL, Python, imports, file paths, subprocesses,
   environment or network access. Bounded read-only helpers only. **No user-editable code on any run
   path** (the snippet mechanism does not survive — D-16).
10. **Deterministic fake model outputs by default.** No billable live model calls without explicit
    authorization.
11. **Additive, idempotent migrations.** Run every migration twice in validation. Back up metadata
    first. Preserve the IDs of records that survive (§1.3).
12. **Local commits only.** Do not push, force-push, rebase, reset, amend earlier commits or rewrite
    history. Do not deploy.
13. **`prompt.txt` and `dev-requirements/` are user-owned input.** Read them; never edit, move,
    rename, delete, stage or commit them.
14. **Do not invent a missing input.** The 8 pending diagnostic workflows (FWK-18) are the standing
    example: stop at that boundary, record it, continue.
15. **Controlled scaling (D-22).** One diagnostic, requester satisfaction, then the next. Do not
    pre-build "for when the workflows arrive."

### 1.2 Definition of "spotless"

A phase is spotless when **all** hold:

- Every requirement ID assigned to the phase is implemented, not approximated.
- Every test criterion in the phase's table passes by its own command, **exit code 0 checked**.
- The invariant-bites check (rule 6) has run for every invariant the phase touches.
- `./ci-local.ps1` is green: frontend eslint (advisory), vite production build, backend pytest.
- `cd ui; npm run test:e2e` is green.
- No pre-existing user file was modified outside the phase's permitted paths.
- The traceability matrix rows for this phase carry real statuses, not aspirations.
- The docs named in the phase are updated **in the same commit** as the code.

### 1.3 What gets deleted, and what must survive

**Already deleted — tranche 1, 30 Jul (uncommitted in the working tree; commit it in Phase 2 with
this inventory in the message).** 28 provably-unreachable files: unmounted routers `test_lab.py`,
`test_plan.py`, `test_library.py`, `tests.py`, `dq_framework.py`; dead executors
`ai/test_execution.py`, `ai/test_worker.py`, `ai/test_report_pdf.py`; the 14 `dq_tests/stage1+stage2`
shims; unrouted pages `TestLibrary.jsx`, `DefineTestPlan.jsx`, `RunValidations.jsx`,
`NewAssessment.jsx`. Verified: backend pytest 89 passed / 1 skipped; vite build clean.

| Delete (remaining tranches, at the phase named) | Preserve |
|---|---|
| The Test Lab wizard: `testlab/Step*.jsx`, `PlanParts.jsx` plan editing, plan-build / snippet / recommend endpoints and service paths, the orphaned legacy block in `ui/src/api/client.js` (Phase 6, at cutover) | Item pipeline records: `dq_items`, `dq_item_files`, `dq_item_tables`, `variable_inventory` |
| The 14-test registry entries, param specs, `test_library` rows, `fw_areas`/`fw_tests` content (Phase 3) | `users`, `authz_roles`, sessions; taxonomy dimensions, values, aliases, assignments |
| `knowledge_base/business_rules.json` + its loader `ai/v2/service.py:38` (Phase 5) | KB documents, versions, published rules, the governance layer in `kb.py` |
| `knowledge_base/dq_framework_data.json` **content** (Phase 3 — storage concept stays, C-37) | `rca.py`'s state machine, agents and invariants (untouched this release) |
| `TYPE_PRIORITY` and every hardcoded schema literal in profiling/planning (Phase 4, ING-10) | The sandbox `ai/code_sandbox.py` (defense-in-depth under registered engines) |
| `plan_v2` / `results_v2` / `scores_v2` / `issues_v2` rows keyed to retired tests (Phase 3, counts recorded) | `gx/` + `spike_*.py` (PSI parity evidence — needed at PSI enablement) and `ai/test_manager.py` until `verify_plan8.py` is rebuilt in Phase 3 |
| Broken imports in `ai/rca_helpers.py:16` and `ai/tool_registry.py:148,217` (Phase 2 — fix, not delete the modules) | `synthetic-kb/` as test fixture material, never loaded at startup |

Unmounted non-Test-module routers (`ai_rules`, `inventory`, `monitoring`, `preview`, `skills`,
`tickets`, `use_cases`) and unrouted pages (`Dashboards`, `AgenticSkills`, `AIAgents`, `Placeholder`,
`RCA.jsx`): **Phase 2 verifies reachability and deletes what is provably dead**, with the same
evidence standard as tranche 1.

### 1.4 Validation commands

Run from `source-codes/` unless noted. Record command, exit code and pass/fail/error/skip counts.
**Tests reporting passed items plus errors are failures.**

```powershell
./ci-local.ps1                        # frontend lint (advisory) + vite build + backend pytest
cd backend; ../.venv/Scripts/python.exe -m pytest tests -q   # run from backend/ — collection needs backend on sys.path
python backend/spike_recalibrate.py   # PSI parity — keep green; matters at PSI enablement (backlog B2)
python backend/spike_gx.py
python backend/spike_integration.py
cd ui; npm run lint
cd ui; npm run build
cd ui; npm run test:e2e               # Playwright (uvicorn 8001 + vite 5175, Chromium)
```

`python backend/verify_plan8.py` is **rebuilt in Phase 3** against the new register. Until then it is
a baseline reference only; after Phase 3 it is a gate again at its new expected count.

Version bump, Phase 7 only: `./Set-AppVersion.ps1 -Version 0.4.0`

### 1.5 Test taxonomy

| Layer | Meaning |
|---|---|
| **U** Unit | Pure functions, no I/O |
| **S** Service | Service functions against a throwaway DB and fixture data |
| **A** API | Router level, including authorization |
| **M** Migration | Applied twice; surviving rows intact; counts and hashes checked |
| **C** Contract | LLM seam contracts, prompt boundaries, fake-provider enforcement |
| **N** Negative | Invariant-bites tests. Prove the guard rejects the bad case |
| **E** E2E | Playwright through the real UI |
| **P** Parity | Output matches a frozen reference (cross-field vs S6; KB vs S9 counts) |

### 1.6 Phase map

Eight phases, one straight line with one optional overlap. Each phase is independently committable
and leaves the product operational.

```
P0  Start gate (verify baseline incl. tranche-1 deletions)
 |
P1  Contracts & decisions                      (docs only)
 |
P2  Helper-layer declutter + platform hygiene  (PLT-08, D-19 factory reset, dead-code sweep)
 |
P3  Framework register + semantic layer        (9 diagnostics, thresholds, staging skeleton)
 |
P4  Data ingestion redesign                    (ING — may interleave with P5)
 |
P5  Knowledge Base: upload → tag → parse → play back → bind
 |
P6  Cross-field engine + Test Lab rebuild      (the slice-1 centrepiece)
 |
P7  Slice-1 acceptance & release 0.4.0
 |
§10 Backlog — one item at a time, gated by D-22
```

Rationale for the order: P2 first because everything after builds on the helper layer and the
requester ordered it upfront; P3 before P4/P5 because the register and semantic layer are the data
model the rest reads; P4 before P6 because the Test Lab manifest consumes ingestion's mapping records
(ING-08); P5 before P6 because cross-field with no published KB is NOT-APPLICABLE by design (CFR-04).
P4 and P5 touch disjoint paths and may be interleaved if useful.

---

## 2. Phase 0 — Start gate

**Objective.** A verified baseline before touching anything. A non-green baseline is your first
finding, not something to work around.

**Requirements:** none (process). **Permitted paths:** none — writes no product code.

### Todos

- [x] 0.1 Read completely: this plan, [requirements-0.4.0.md](requirements-0.4.0.md),
      [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md), all files in `dev-requirements/` —
      for `MVP_Test Plan_DA.xlsx` (re-issued 30 Jul) the three green tabs only, ignoring `#F79646`
      columns; the flow chart `cross_field_engine_V2_workflow_1.png`; the engine + report in
      `V2_files need API.zip`; all four pages of
      `source-codes/synthetic-kb/KB_cross_field_reference_2.pdf`.
- [x] 0.2 Read in `source-codes/`: `README.md`, `TSD.md`, `VERSIONING.md`, `TODO.md`,
      `guide/Workflows_and_Testing_Guide.md`.
- [x] 0.3 Inspect what you will change: `backend/ai/test_kit.py`, `ai/tool_registry.py`,
      `ai/rca_helpers.py`, `ai/code_sandbox.py`, `ai/control_plane.py`, `ai/llm.py`,
      `ai/v2/service.py`, `ai/v2/issues.py`, `dq_tests/*`, `kb.py`, `kb_convert.py`, `system_db.py`
      (incl. `reset_demo` `:846`, `wipe_all_items` `:897`), `routers/v2.py`, `routers/admin.py`,
      `ui/src/pages/DataSourcing.jsx`, `ui/src/pages/TestLab.jsx`, `ui/src/pages/testlab/*`,
      `ui/src/pages/Admin.jsx`, `ui/src/api/client.js`, `ui/e2e/upload-workflow.spec.js`.
- [x] 0.4 Record git state (`status --short`, branch, HEAD, `log -10`, `diff --stat`). The tranche-1
      deletions are expected working-tree state; everything else pre-existing is read-only.
- [x] 0.5 Run the full baseline from §1.4 plus `verify_plan8.py`. Record command, exit code, counts.
- [x] 0.6 Produce a start-gate report: verified baseline; the record inventory Phase 3 will drop
      (counts per table); the reachability list for §1.3's candidate sweep; the first slice you will
      cut.

**Exit gate:** baseline verified and reported; no product file modified. **No commit.**

```text
Status: Done (30 Jul 2026)
Landed:
- Baseline verified at dev@c0e8e06 + tranche-1 deletions; full report in archimedes-0.4.0-start-gate.md
- All inputs read: S8 3 green tabs decoded (9 core ids = #2,4,6,8,11,12,14,17,20), S9 49-rule counts captured, S10 chart, S5/S6 engine+report, S7
Validation:
- ./ci-local.ps1 → exit 0: pytest 89 passed/1 skipped; Playwright 8/8; vite build clean
- spike_*.py + verify_plan8.py → exit 1 at BASELINE (F-01: backend/database.py deleted at 0.2.0, commit 5ba1b13) — pre-existing defect, recorded not fixed; verify_plan8 rebuild (Phase 3) now mandatory
Notes:
- No populated system DB exists in the tree; Phase-3 pre-drop counts will be taken from the boot-seeded DB then
```

---

## 3. Phase 1 — Contracts and decisions

**Objective.** Freeze the contracts so no later phase guesses. Zero product behaviour change. Only
slice-1 contracts — APL and RCA contract documents are written when their backlog items are pulled.

**Requirements:** FWK-01…FWK-05 (definition), FWK-17, FWK-18, DET-01…DET-03, ING-01…ING-10
(contract), D-01…D-22.
**Permitted paths:** `docs/**` only, incl. new `docs/0.4.0/`. **Forbidden:** any `.py`, any `.jsx`.

### Todos

- [x] 1.1 `docs/0.4.0/00-framework.md` — 6 themes, 11 L2 areas, 6 test areas, and the
      **9-diagnostic register** from S8's core rows (S8 row numbers as ids; every non-backfill
      column; Plan A's KB dependency; `workflow_status`: #4 `executable`, eight `workflow-pending`).
      The 11 defer rows in a documentation appendix only (D-17). Single source of truth for what
      exists.
- [x] 1.2 `docs/0.4.0/01-disposition.md` — the 14 shipped tests → keep / absorb / replace / defer /
      delete per FWK-13, with record classes dropped and pre-drop counts from 0.6.
- [x] 1.3 `docs/0.4.0/02-determinism-map.md` — every workflow step marked deterministic or one of the
      four seams, with the fake-provider strategy per seam. Transcribe the S10 chart's step list and
      its two guarantees ("LLM touches mapping only, never a verdict"; "nothing silent — N-A always
      stated") as the cross-field determinism contract.
- [x] 1.4 `docs/0.4.0/03-staging-contract.md` — Stage-1 → Stage-2 dependency, the value-semantics
      refusal (FWK-10), the slice-1 note (rule-encoded exceptions until #8), and the FWK-09 guard
      order: class eligibility → material fields (D-18 definition) → value-semantics gate.
- [x] 1.5 `docs/0.4.0/04-kb-contract.md` — upload → dimension tags → table-aware parse → rule records
      → parse report → binding status → publish; the playback summary fields; the two axes (KB-15).
- [x] 1.6 `docs/0.4.0/05-cross-field-contract.md` — the CFR structured result derived from S6, S9 and
      the S10 chart: preamble, roll-up, per-violation fields, evidence shape, severity ordering,
      verdict gate, clustered/scattered read, the role-resolution score ladder (CFR-05), and the
      manifest/decision-record mapping of the chart's interactive steps (CFR-12). Phase 6's parity
      target.
- [x] 1.7 `docs/0.4.0/06-ingestion-contract.md` — the ING block (§8b of the requirements) as a
      contract: the three moments, the mapping confidence tiers and floor, the warning codes, the
      dictionary states, the status machine, and the record shapes ingestion persists for the Test
      Lab manifest (ING-08).
- [x] 1.8 `docs/0.4.0/07-decisions.md` — D-01…D-22 with status and adoption date.
- [x] 1.9 Extend `docs/rca/01-traceability-matrix.md` with a 0.4.0 section: one row per slice-1
      requirement ID with target path, behavioural test name, phase, status `not started`.
      Backlog-block IDs (APL, RCA, WSP except WSP-08, DIA, DET-06) are listed once as "backlog, not
      scheduled".
- [x] 1.10 Update `TODO.md`: the two breaking changes (framework replacement, Test Lab replacement)
      and the slice-1 scope.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 1-T1 | Every slice-1 requirement ID appears exactly once in the traceability matrix with a phase; verify by script. | U |
| 1-T2 | The register in `00-framework.md` matches S8's core rows exactly; 9 rows; one `executable`; no defer row registered. | — |
| 1-T3 | The disposition doc accounts for all 14 shipped tests. | — |
| 1-T4 | The determinism map marks the cross-field path with exactly two LLM touchpoints — seam (a) mapping verification (optional, default off) and seam (b) report commentary (backlog) — and everything else deterministic. | — |
| 1-T5 | The CFR contract accounts for every field in S6's `report.txt`, every column of S9's rule tables, and every step of the S10 chart. | — |
| 1-T6 | `07-decisions.md` has a status line for all 22 decisions. | — |
| 1-T7 | Baseline still green — tripwire. | — |

**Exit gate:** contracts complete; nothing in the product changed.
**Commit:** `docs(0.4.0): freeze framework register, slice-1 contracts and decisions`.

```text
Status: Done (30 Jul 2026, commit eceb3f2)
Landed:
- docs/0.4.0/00..07 (framework register, disposition, determinism map, staging, KB, CFR, ING contracts, decisions incl. DX-01..DX-03)
- Traceability matrix 0.4.0 section (76 slice-1 rows + backlog blocks); TODO.md rewritten with the two breaking changes
Validation:
- 1-T1/1-T2/1-T3/1-T6 by script (verify_phase1.py, exit 0): 76 ids exactly once; register 9 rows/1 executable; 14 dispositions; 22 decisions
- 1-T4/1-T5 by construction review (S6 fields, S9 columns, S10 steps each mapped); 1-T7 pytest 89 passed/1 skipped, exit 0
Notes:
- Tranche-1 deletions intentionally left uncommitted for Phase 2.1
```

---

## 4. Phase 2 — Helper-layer declutter and platform hygiene

**Objective.** PLT-08, upfront by instruction (D-21): make the helper/tool layer readable,
understandable and debuggable **before** feature work builds on it. Plus the restored factory reset
(D-19) and the completed dead-code sweep. Behaviour-preserving except where current behaviour is
already broken.

**Requirements:** PLT-08, WSP-08 (D-19), PLT-04, PLT-05.
**Permitted paths:** `backend/ai/*.py` (helper layer), `backend/routers/admin.py`, `system_db.py`
(additive), `ui/src/pages/Admin.jsx`, `ui/src/api/client.js` (admin calls), `backend/tests/`,
`ui/e2e/`; deletions per §1.3 sweep with evidence.

### Todos

- [x] 2.1 Commit tranche 1 (§1.3) with the inventory and verification evidence in the message.
- [x] 2.2 Map the helper layer as-is: every module in `backend/ai/` that is a helper/tool/registry
      (`test_kit.py`, `tool_registry.py`, `rca_helpers.py`, `code_sandbox.py`, `control_plane.py`,
      `llm.py`, satellites), its importers, and its actual runtime use. One page in
      `docs/0.4.0/08-helper-layer.md`: what each module is *for*, in one sentence each. Anything that
      cannot be described in one sentence is the first refactor target.
- [x] 2.3 **One registry.** Collapse parallel catalogues (`test_kit`'s governed catalogue vs
      `tool_registry`) into a single helper registry with one registration idiom. CFR-06's nine
      primitives will register here in Phase 6 — leave the seam documented.
- [x] 2.4 **Fix the broken imports as defects**: `rca_helpers.py:16` (`from database import
      TABLE_KEYS` — module deleted in the 0.2.0 era) and `tool_registry.py:148,217`
      (`routers.ingestion` does not exist). Root-cause each: restore the constant where it should
      live, or delete the dependent path if it is itself dead — with evidence either way.
- [x] 2.5 **Contracts and docstrings**: every helper gets a typed signature and a docstring stating
      purpose, inputs, outputs and failure modes. Module docstrings state what belongs in the module
      and what does not.
- [x] 2.6 **Structured logging**: every helper invocation logs name, caller context id (run/case id)
      and outcome at debug level through one logging helper — a failure is traceable without a
      debugger.
- [x] 2.7 **Flat, discoverable layout**: no import-time side effects, no conditional imports at call
      sites, no circular imports — asserted by a test that imports every `ai/` module in isolation.
- [x] 2.8 A unit test per helper (U), plus the isolation-import test (N).
- [x] 2.9 **Factory reset (D-19 / WSP-08):** expose `reset_demo` (surgical: items + derived artifacts
      go, users and platform seeds stay) and `wipe_all_items` (blank slate) as admin-only endpoints;
      Admin UI "Danger zone" with type-to-confirm; audit event with actor, grade, per-table counts
      removed (PLT-05); error sanitizer on the new routes (PLT-04).
- [x] 2.10 Complete the §1.3 reachability sweep for the remaining unmounted routers and unrouted
      pages; delete what is provably dead, same evidence standard as tranche 1; record what stays and
      why.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 2-T1 | Every `ai/` module imports cleanly in isolation; no helper-layer module imports a nonexistent symbol. | N |
| 2-T2 | Exactly one helper registry exists; registering the same helper twice fails loudly. | U/N |
| 2-T3 | Every helper has a passing unit test and a docstring; asserted by inspection script. | U |
| 2-T4 | A helper failure produces a structured log line with the context id — asserted on a seeded failure. | S |
| 2-T5 | Factory reset (surgical): items, plans, results, issues gone; users, taxonomy, platform seeds intact; counts audited. | S/M |
| 2-T6 | Factory reset (full wipe): blank slate; re-boot reseeds platform data; audit row present. | S/M |
| 2-T7 | Both reset endpoints reject a non-admin session; the UI action requires type-to-confirm. | A/N/E |
| 2-T8 | Baseline suite still green after refactor — behaviour preserved. | — |
| 2-T9 | Post-sweep: every remaining router file is mounted or has a recorded keep-reason. | N |

**Exit gate:** the helper layer reads like something a human wrote for humans; factory reset works;
no dead code without a keep-reason.
**Commit:** `refactor(helpers)!: single registry, clean imports, logging, tests + admin factory reset`.

```text
Status: Done (30 Jul 2026, commits 989aac8 tranche-1 + 820bb14 phase)
Landed:
- Single governed registry in ai/test_kit.py (gx metrics + 12 rca_helpers; tool_registry delegates); broken imports root-caused (TABLE_KEYS -> params; fetch_schema_stats -> table_metadata; raise_mitigation_ticket deleted); _helper_log structured logging; docstrings/types across the layer; docs/0.4.0/08-helper-layer.md
- Factory reset: POST /api/admin/factory-reset (surgical|wipe), server-side type-to-confirm, transaction_log audit with per-table counts, PLT-04 sanitizer decorator, Admin UI danger zone
- Sweep: 15 more dead files deleted with evidence (7 routers, recommend/frame_assembler/rule_generator, 5 pages); keep-reasons recorded + enforced by tests/test_reachability.py
Validation:
- ci-local.ps1 exit 0: pytest 132 passed/1 skipped; Playwright 10/10 (2 new admin-reset e2e); eslint/build/boot PASS
- 2-T1..2-T9 all covered (test_helpers.py 33 tests, test_admin_reset.py 7, test_reachability.py 3); admin-gate + duplicate-registration invariants bite
Notes:
- ai/skills.py proved LIVE (seeds boot path) — kept; ai/{test_manager,effort,dict_ingest}.py keep-reasons expire at Phase-3 verify_plan8 rebuild
- e2e admin-reset spec relies on alphabetical ordering (runs first); noted in spec header
```

---

## 5. Phase 3 — Framework register, semantic layer, staging skeleton

**Objective.** Retire the old framework; install the 9-diagnostic register as data; build the
threshold semantic layer and the staged-execution/decision-type machinery the Test Lab consumes.

**Requirements:** FWK-01…FWK-17 (as revised rev-5), FWK-18 (mechanism), PLT-02, PLT-03, PLT-06.
**Permitted paths:** `backend/dq_tests/**` (retire), new `backend/dq_diagnostics/register.py` +
seeds, `knowledge_base/dq_framework_data.json`, `system_db.py` (additive), `ai/v2/service.py`,
`routers/v2.py`, `verify_plan8.py`, `backend/tests/**`, `ui/e2e/`.

### Todos

- [x] 3.1 Back up metadata; record pre-drop counts per table in the commit message.
- [x] 3.2 Install the taxonomy: 6 themes, 11 L2 areas, 6 test areas — replacing
      `dq_framework_data.json`'s content (C-37). Third-party theme registered with no diagnostic.
- [x] 3.3 Install the **9-row register** (D-17) per `docs/0.4.0/00-framework.md`, with
      `workflow_status ∈ {executable, workflow_pending}`; only #4 `executable`; `enabled_by` decision
      reference per FWK-18.
- [x] 3.4 Delete per FWK-13: registry entries, param specs, `test_library` rows; demote *Key
      uniqueness* to a profiling precondition (D-04). Drop the downstream records named in §1.3 with
      counts; preserve datasets, files, tables, inventories, users, taxonomy, knowledge documents.
- [x] 3.5 Refusal semantics: workflow-pending → "workflow not yet defined"; unknown id → unknown
      (D-17). No silent skip.
- [x] 3.6 **Threshold semantic layer** (FWK-06): `threshold_settings` — defaults per diagnostic,
      overridable per dimension and engagement, audited. No threshold constant in code.
- [x] 3.7 **Decision-type contract** (FWK-07): every future result carries `decision_type`; the
      shared guard-order skeleton (FWK-09 rev-5: class → material → value-semantics) exists with the
      material and value-semantics guards returning explicit "producer not yet available" markers
      until their producers land — the *shape* is built now so no statistical engine can bypass it.
- [x] 3.8 **Staged execution skeleton** (FWK-08/10): the runner orders Stage 1 before Stage 2 and
      refuses a Stage-2 statistical screen whose dependencies are absent. Vacuous for #4; asserted by
      a fixture fake diagnostic in tests.
- [x] 3.9 **Delivery/baseline seam** (PLT-02/03): `dataset_family_id`, `delivery_seq`, `as_of_date`,
      nullable `baseline_delivery_id`; populate existing datasets with a single-delivery default.
- [x] 3.10 Re-derive scoring weights for the 9-row register (FWK-16); record a fixture score before
      and after with the delta explained; encode the coverage-honesty statement.
- [x] 3.11 Rebuild `verify_plan8.py` and `test_finalized_framework.py` against the new register in
      this commit, new counts asserted (rule 7). `ai/test_manager.py` goes with the rebuild if
      nothing else imports it.
- [x] 3.12 Coverage map data (FWK-14/15): area-level Covered / Covered-thin / Partial / Gap with
      reasons; executable vs workflow-pending within the register.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 3-T1 | Register: exactly 9 rows; one `executable`; ids are S8 row numbers; taxonomy counts match the contract doc. | U/N |
| 3-T2 | Workflow-pending execution refused with the message; unknown id indistinguishable from any bad id. | S/N |
| 3-T3 | Every retired test is unreachable: registry, param specs, plan build, execution. | U/N |
| 3-T4 | Surviving record classes intact after the drop, identical counts; migration runs twice. | M/N |
| 3-T5 | Changing a threshold in the semantic layer changes downstream behaviour with no code change. | S |
| 3-T6 | The guard-order skeleton rejects a statistical screen that bypasses it — structural assertion. | N |
| 3-T7 | A Stage-2 fake diagnostic with absent dependencies is refused, not reordered. | S/N |
| 3-T8 | Every dataset has a delivery record; same-family re-upload increments `delivery_seq`. | S |
| 3-T9 | Rebuilt `verify_plan8.py` passes at its new asserted count. | — |
| 3-T10 | Coverage map shows the named GAP areas with reasons; nothing rounds up. | S |

**Exit gate:** the new framework is data; the old one is gone; staging and guards refuse unguarded
runs.
**Commit:** `feat(framework)!: 9-diagnostic register, semantic thresholds, staged-execution skeleton`.

```text
Status: Done (Section 11.1 closure, 31 Jul 2026; commits 8e2c2e7, f1cefb0)
Landed:
- backend/dq_diagnostics/ package: register (seed/list/get/require_executable/coverage_map/scoring_weights), result (DiagnosticResult FWK-07 shape), guards (FWK-09 chain, GuardedScope), runner (stage ordering + refusal), thresholds (semantic layer, audited), delivery (PLT-02), profiling_preconditions (D-04), migrate_040 (retirement drop, idempotent, audited)
- dq_framework_data.json replaced wholesale with the 9-row register + 6/11/6 taxonomy + coverage + threshold defaults + FWK-16 coverage statement; old dq_tests/registry.py + param_specs.py rewritten to refuse every name (import-compatible until Phase-6 cutover); galileo_seed.py + test_library_seed.py deleted
- verify_plan8.py + test_finalized_framework.py rebuilt against the new register (rule 7); ai/test_manager.py + ai/dict_ingest.py deleted (keep-reason expired), ai/effort.py confirmed live and restored
Validation:
- 3-T1..3-T10: 47 new tests green (test_register/staging/thresholds/delivery/migration_040); full suite 181 passed/1 skipped; verify_plan8.py rebuilt gate 28/28 PASS; ci-local.ps1 all blocking gates PASS (eslint pre-existing advisory warn only)
Notes:
- e2e adapted: rca.spec.js's full vertical-slice journey explicitly skipped with a dated reason (no diagnostic can open an issue between this phase's registry retirement and Phase-6's Test Lab rebuild); RCA mechanism itself unaffected, proven by test_rca.py
- Pre-drop counts recorded from the e2e instance DB (fw_areas 11, fw_tests 14, test_library 25, plan_v2 42, results_v2 54, scores_v2 3, issues_v2 6, rca_cases 4) before the retirement migration
- Section 11.1 closure: non-empty retirement now fails closed unless a restorable SQLite snapshot succeeds; restore smoke, canonical schema/all-row hashes, survivor hashes, drop absence, and second-run no-op pass.
- Historical waiver: the original empty-baseline retirement has no fabricated retroactive backup; commit f1cefb0 records the historical counts and waiver.
```

---

## 6. Phase 4 — Data ingestion redesign

**Objective.** Replace the Data Sourcing flow with ING's three moments: **Drop → Review → Ready**.
Minimal decisions, confidence-tiered mapping, structured dictionary validation, honest degradation.

**Requirements:** ING-01…ING-10 (§8b), PLT-04.
**Permitted paths:** `ai/v2/service.py` (profiling/ingest paths), new `backend/ingest/` if cleaner,
`routers/v2.py`, `system_db.py` (additive), `ui/src/pages/DataSourcing.jsx` (replaced),
`ui/src/api/client.js`, `backend/tests/`, `ui/e2e/`.

### Todos

- [x] 4.1 **Drop:** dataset drop starts parse + profile immediately (SSE progress); dictionary drop
      triggers automatic inspection — suggestions computed and **pre-applied** at high confidence
      (exact/alias), shown as "confirm" at fuzzy-above-floor, never guessed below the floor (ING-03).
      One-to-one mapping enforced. Use case + target asked inline, once, defaulted (ING-02).
- [x] 4.2 **Review:** one screen — per column: inferred classification, role, dictionary declaration,
      discrepancy; editable in place (ING-06). Structured warnings `{column, code, message}`,
      collapsed, non-blocking (ING-04); "preserved, not used" for unmapped dictionary fields;
      hard-fail only on structural corruption.
- [x] 4.3 **Ready:** status machine `uploading → profiling → needs_review → ready | failed(reason)`,
      derived from events, never a button (ING-07). The Test Lab consumes only `ready`.
- [x] 4.4 Dictionary state `yes / thin / absent` with the looked-complete-but-thin downgrade
      (ING-05); `provisional` marking on inferred columns; both surfaced wherever the item appears.
- [x] 4.5 Persist the confirmed mapping, dictionary state, warnings and profile snapshot as records
      the Test Lab manifest reads (ING-08) — cross-field role resolution consumes these in Phase 6.
- [x] 4.6 Replacement = new delivery per PLT-02 (ING-09).
- [x] 4.7 Remove `TYPE_PRIORITY` and every schema literal from inference (ING-10); replace with
      generic signals + dictionary/KB-driven hints.
- [x] 4.8 Migrate existing profiled items onto the new records without loss; re-profiling optional.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 4-T1 | Happy path: drop a CSV → profiling starts unprompted → Review shows inferred columns → Ready. No finalize or profile button exists. | E |
| 4-T2 | Dictionary drop auto-inspects; a high-confidence mapping is pre-applied; an ambiguous one asks; below-floor is never guessed. | S/U |
| 4-T3 | A source column consumed by one mapping is unavailable to others; duplicate dictionary rows hard-fail with a clear message. | U/N |
| 4-T4 | Declared-vs-inferred conflicts, dictionary-not-in-dataset and unused fields each produce their structured warning code; none blocks Ready. | S |
| 4-T5 | No dictionary → conservative inference, `provisional` columns, state `absent`; a nominally complete dictionary that still left inference to run yields `thin`. | S/N |
| 4-T6 | The persisted mapping records are exactly what the Phase-6 manifest reads — asserted by contract fixture. | S/P |
| 4-T7 | Same-family re-upload increments delivery; the old item is not mutated. | S/N |
| 4-T8 | No schema literal survives in inference — source-inspection test (ING-10). | N |
| 4-T9 | Existing items still load and reach `ready` after migration; run twice. | M |
| 4-T10 | Playwright: dataset + dictionary journey end to end, including a deliberate mismatch surfacing as a warning. | E |

**Exit gate:** ingestion is three moments with one decision surface; its records feed the manifest.
**Commit:** `feat(ingest)!: drop → review → ready — tiered mapping, dictionary validation, honest states`.

```text
Status: Done (Section 11.1 closure, 31 Jul 2026; commits 9e0d200, f1cefb0)
Landed:
- backend/ingest/ package (classify/mapping/dictionary_state/warnings/status/records); TYPE_PRIORITY deleted; drop starts parse+profile unprompted, no finalize/profile button
- Confidence-tiered mapping (exact pre-applied, fuzzy>=0.6 confirm, below-floor never guessed); dictionary_state yes/thin/absent; 4 structured non-blocking warning codes; ready reached automatically, never by button
- ING-09 delivery seam wired (reupload_item); persisted record shape (records.py) documented as the Phase-6 manifest contract
Validation:
- 4-T1..4-T10: tests/test_ingest.py (28 tests) + e2e upload-workflow.spec.js (incl. deliberate mismatch -> warning, still reaches Ready)
- Combined suite after Phase 4+5 merge: pytest 246 passed/1 skipped; ci-local.ps1 all blocking gates PASS
Notes:
- Old wizard's _choose_columns/_supporting/RULE_KEYWORDS still carry schema literals (dscr, facility_id) — explicitly out of ING-10's scope (they belong to the wizard retiring in Phase 6.13), flagged not fixed
- `reuploadItemV2` is wired into Data Sourcing: the replacement Review surface shows new item/family/sequence/as-of context and leaves the original unchanged; the Playwright journey passes.
```

---

## 7. Phase 5 — Knowledge Base: upload, tag, parse, play back, bind

**Objective.** An uploaded document becomes governed, tagged, bound rules with an honest parse
report. Highest-consequence component: with no fallback rule set (CFR-04), a parse failure means
cross-field cannot run.

**Requirements:** KB-01…KB-16.
**Permitted paths:** `backend/kb.py`, `kb_convert.py`, new `backend/kb_parse/`, `taxonomy.py`,
`system_db.py` (additive), `routers/v3.py`, `ai/v2/service.py` (delete the rules loader),
`knowledge_base/`, `ui/src/pages/KnowledgeBase.jsx`, `backend/tests/`, `ui/e2e/`.

### Todos

- [x] 5.1 Delete `knowledge_base/business_rules.json` + loader (`ai/v2/service.py:38`) (KB-01, C-36);
      a source-inspection test asserts no module ships a domain rule set.
- [x] 5.2 Dimension tagging for knowledge documents (KB-02, C-35): `/knowledge/documents/{id}/tags`,
      multi-select, wired into upload.
- [x] 5.3 **Table-aware parser** (KB-04, C-34): a document of N tabulated rules yields N records;
      heading-structured prose still parses as before.
- [x] 5.4 Rule records with full provenance fields (KB-07); extraction hazards surfaced, never
      silently guessed (KB-11 — collapsed role names shown back for confirmation).
- [x] 5.5 **Binding** (KB-08/09): `bound` / `reference-only` / `unparsed`; execution only through a
      confirmed or signature-exact binding; never a prose-recovered predicate.
- [x] 5.6 **Parse report** as a first-class artifact (KB-10, D-06); **playback summary** (KB-03) —
      for S9: 49 rules · IFRS9 11 / IRB 36 / both 2 · conditional 13, date-ordering 3, domain 26,
      identity 3, inequality 4 · CRITICAL 6, MATERIAL 21, MINOR 22 · roles · anything unparsed.
- [x] 5.7 Governance intact (KB-05/13); deterministic-only knowledge writes (KB-14); two axes kept
      apart (KB-15); retrieval order + manifest (KB-16); safe rejection of unsupported/corrupt/scanned
      documents (KB-06).
- [x] 5.8 UI: upload → tag → playback → parse report → review → publish, binding status per rule.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 5-T1 | Uploading S9 produces **49 rule records**; counts by framework, type, severity match exactly. | S/P |
| 5-T2 | A prose document still parses; the new parser does not regress it. | S/N |
| 5-T3 | Unparsed and reference-only rules are displayable and provably non-executable. | S/N |
| 5-T4 | A collapsed role name is surfaced for confirmation, not silently bound. | S/N |
| 5-T5 | The parse report lists every rule with status and reason; retrievable via API. | A |
| 5-T6 | Draft rules never appear in retrieval or any model context; no LLM path can write knowledge. | C/N |
| 5-T7 | Image-only PDF, corrupt DOCX, oversized file → three distinct clear rejections. | S/N |
| 5-T8 | `business_rules.json` and its loader are gone; no module ships a rule set. | N |
| 5-T9 | Publishing requires the reviewer role; expiry leaves retrieval; schema change demotes trust. | S/N |
| 5-T10 | Playwright: upload S9 → tag → playback → parse report → publish. | E |

**Exit gate:** S9 round-trips to 49 governed rules; nothing executes from prose.
**Commit:** `feat(kb): table-aware parsing, dimension tagging, playback summary and rule binding`.

```text
Status: Done (31 Jul 2026, commit 9e0d200 — landed jointly with Phase 4, see that phase's note)
Landed:
- kb_convert.py table-aware PDF/DOCX extraction (GFM tables in stored markdown, page markers); kb.py table-aware section splitter (generic fuzzy header matching, C-34 fixed); parse_report()/playback_summary(); list_eligible_rules(require_bound=) hook for Phase 6
- kb_rules extended with KB-07 fields (source_rule_id/severity/rule_type/entity/roles/reg_ref/exceptions/source_page/binding_status/...), all additive/nullable; binding_status defaults conservatively (reference-only/unparsed, never bound — Phase 6's job)
- KB-02 dimension tagging (object_type=kb_document, reuses taxonomy.py unchanged); business_rules.json + loader deleted (KB-01/C-36)
Validation:
- 5-T1..5-T10: tests/test_kb_parse.py (14) + test_kb_binding.py (15) + test_kb_tags.py (8) = 37 tests; existing test_kb.py governance suite unmodified and green
- S9 parity independently re-verified via a from-scratch pdfplumber oracle (separate code path from kb.py): 49 rules, IRB 36/IFRS9 11/both 2, conditional 13/date_ordering 3/domain 26/identity 3/inequality 4, CRITICAL 6/MATERIAL 21/MINOR 22 — matched docs/0.4.0/04-kb-contract.md exactly, no doc correction needed
- e2e knowledge-base.spec.js gains S9 journey (upload->tag->playback->parse-report->publish); original prose test untouched (5-T2 regression guard)
Notes:
- docs/0.4.0/05-cross-field-contract.md + 07-decisions.md (DX-04) updated ahead of Phase 6: S5's ROLE_VOCAB is hardcoded domain knowledge and must not port verbatim — vocabulary derives from the published KB + ingested dictionary at runtime
```

---

## 8. Phase 6 — Cross-field engine and the Test Lab rebuild

**Objective.** The slice-1 centrepiece, in one phase because they ship together: the cross-field
engine as a service (per the S10 chart) and the register-driven Test Lab
([testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md)) that runs it. At exit, the four-step wizard
is gone.

**Requirements:** CFR-01…CFR-18, FWK-07, FWK-17 (rendering), D-15, D-16. DIA-01 is **backlog B1**,
not this phase — censoring runs on rule-encoded exceptions (requirements §1.3 slice-1 note).
**Permitted paths:** new `backend/dq_diagnostics/` (readiness, manifest, runner,
`engines/cross_field/`), `ai/v2/service.py` (retire wizard paths), `routers/v2.py`, `system_db.py`
(additive), `ui/src/pages/TestLab.jsx`, `ui/src/pages/testlab/**` (replaced),
`ui/src/api/client.js`, `backend/tests/`, `ui/e2e/`.

### Todos — engine (S10 chart steps in brackets)

- [x] 6.1 Port S5 to a **pure core** in `engines/cross_field/`: `rules.py` (five types, three
      severities), `primitives.py` (nine + `evaluate_rule`), `roles.py`, `engine.py`, `result.py`.
      No `argparse`/`print`/`input()`/file I/O/`sys.exit`; progress via injected callback [steps 0–6
      minus interaction].
- [x] 6.2 Rules only from `kb.list_eligible_rules(...)`, filtered by use case and `bound` status;
      zero eligible → **NOT-APPLICABLE**, never PASS (CFR-03/04) [steps 1b–2].
- [x] 6.3 Role resolution with the graded score ladder + dtype gate, recording score and reason per
      role (CFR-05); overrides win; ingestion's mapping records (ING-08) are the input substrate
      [step 3].
- [x] 6.4 Optional LLM mapping verification through `ai/llm.py` + `control_plane.resolve` — seam (a),
      off by default (D-08); disagreement → decision record keep/LLM/manual, never a prompt
      (CFR-11/12); delete `_llm_call` and `CFR_LLM_API_KEY` [step 3b].
- [x] 6.5 Entity→table binding by vote [step 4]; per-rule evaluation: IF-condition scope →
      censored/N-A skip via encoded exceptions → exceptions with reason + count → violations → lift
      pattern CLUSTERED/SCATTERED with parameters from the semantic layer [step 5]; verdict gate
      [gate]; structured result first, text/PDF derived (CFR-08/09/10) [step 6].
- [x] 6.6 Register the nine primitives in the Phase-2 unified registry (CFR-06); SSE in the
      `execute/stream` idiom (CFR-13); persistence through `diag_runs/diag_results/diag_findings`
      (CFR-14); read-only, bounded evidence (CFR-15); deterministic (CFR-16).
- [x] 6.7 Parity fixture: synthetic dataset reproducing S9's rule counts and a documented subset of
      S6's behaviours; state exactly which parity dimensions are proven (no workbook dependency — the
      product runs on any uploaded data).

### Todos — Test Lab (design doc §3/§5 govern)

- [x] 6.8 **Coverage board**: 9 cards with the 8-field anatomy + status chips + area-level GAP strip.
- [x] 6.9 **Readiness** per (item, diagnostic), deterministic (design §5 `readiness()`).
- [x] 6.10 **Scope gate**: manifest with rules-in-scope, role map + score provenance, thresholds with
      source, scope preview; edits → decision records; freeze on Run.
- [x] 6.11 **Runner** stage-ordered over SSE; **Findings** by decision type (verdicts render
      PASS/VIOLATION/N-A; the candidate-flag rendering path built and asserted with a fixture even
      though no statistical diagnostic ships); issue auto-open on VIOLATION into the existing
      hand-off.
- [x] 6.12 **Score honesty** (FWK-16): verdict roll-up naming covered vs pending; PDF report from
      structured results.
- [x] 6.13 **Retire the wizard**: delete Step1–4/PlanParts editing, plan/snippet/recommend endpoints,
      the orphaned client.js legacy block; `plan_v2` micro-states retired.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 6-T1 | KB-sourced rules reproduce the documented type counts (13/3/26/3/4) and use-case subsets summing to 49. | S/P |
| 6-T2 | Zero eligible rules → NOT-APPLICABLE with reason, never PASS. | S/N |
| 6-T3 | Each of the nine primitives has a passing, a violating **and** a skipped-censored case. | U/N |
| 6-T4 | Role resolution reports score + reason per role; each ladder tier covered by a fixture; dtype gate proven. | U |
| 6-T5 | With verification off (default): zero model calls, counted. On: a mapping change is recorded; verdicts unchanged. | C/N |
| 6-T6 | Verdict gate + severity ordering + exception reasons + ≤5 evidence rows + clustered/scattered vs a known-concentration fixture. | U/P |
| 6-T7 | Determinism: two runs on one frozen manifest are byte-identical. | U |
| 6-T8 | Purity: core has no `print`/`input`/`argparse`/`open(`/`sys.exit`; no `CFR_LLM_API_KEY` anywhere. | N |
| 6-T9 | Read-only: dataset hash unchanged after a run. | S/N |
| 6-T10 | Coverage board: exactly 9 cards, correct chips, no run affordance on pending; API refuses pending with the message. | E/N |
| 6-T11 | Readiness shows NOT-APPLICABLE before any run when no KB is published for the item's scope. | S/N |
| 6-T12 | Manifest: role overrides and threshold tunes become decision records; the frozen manifest equals what ran. | S/N |
| 6-T13 | No snippet/codegen path exists on the run path — structural assertion; old endpoints gone; UI has no route to them. | N/A |
| 6-T14 | A candidate-flag fixture renders as review, never failure (FWK-07) — the rendering contract proven before any statistical diagnostic ships. | E/N |
| 6-T15 | A VIOLATION auto-opens an issue that can open an RCA case. | S |
| 6-T16 | Score panel names covered vs pending; no weighted health score is displayed in slice 1 (design §3 step 5, pending requester confirmation). | E |
| 6-T17 | SSE emits start/progress/done; never leaks a raw exception. | A/N |
| 6-T18 | Playwright: ingest (P4 flow) → publish KB (P5 flow) → board → scope gate → run → findings with severity/evidence/pattern → issue → report. | E |

**Exit gate:** the wizard is gone; the register drives the module; #4 runs end to end from uploaded
KB rules; everything else says honestly why it does not run.
**Commit:** `feat(testlab)!: register-driven Test Lab running the KB-driven cross-field diagnostic`.

Status: Done (Section 11.1 closure, 31 Jul 2026; commit f1cefb0)
Landed: role verification is off by default; explicit pre-freeze opt-in makes exactly one bounded
fake-proven contract call, records keep/LLM/manual outcomes append-only, and cannot alter rule maths
or canonical verdict bytes. Default runs make zero model calls.

---

## 9. Phase 7 — Slice-1 acceptance and release 0.4.0

**Objective.** Prove slice 1 as a whole and cut `0.4.0`.

### Todos

- [x] 7.1 **Framework acceptance:** exactly 9 registered (D-17); #4 runs; pending refused; unknown
      ids unknown; coverage map honest.
- [x] 7.2 **Determinism acceptance:** on the slice-1 paths, the only reachable seam is (a), off by
      default — proven with a counting fake provider; the three legacy cosmetic LLM call sites
      (profile/recommend thoughts, RCA polish) are inventoried: gone with their retired paths or
      recorded for the RCA backlog item.
- [x] 7.3 **KB acceptance:** S9 → 49 governed rules; no domain rule in code; no prose-recovered
      predicate executes; drafts never reach a model context.
- [x] 7.4 **Ingestion acceptance:** the ING journey, degradation states, and the manifest-feed
      contract.
- [x] 7.5 **Hygiene sweep:** error sanitizer on all new routes; audit events for every decision,
      disposition, reset and publication; no secret in logs or responses.
- [x] 7.6 **Migration sweep:** every 0.4.0 migration run twice; surviving classes unchanged (counts +
      hashes); dropped classes gone.
- [x] 7.7 Full §1.4 suite + rebuilt `verify_plan8.py`, exit codes checked.
- [x] 7.8 Docs in the release commit: `TSD.md`, `README.md`, `USER_GUIDE.md`, `TODO.md`,
      `docs/0.4.0/*`, traceability matrix at real final statuses,
      `guide/Workflows_and_Testing_Guide.md`.
- [x] 7.9 Release note: framework replacement and what it dropped; the 9-diagnostic scope and
      controlled scaling (D-22); the Test Lab and ingestion replacements; the factory reset; the
      helper-layer refactor; rollback limitations.
- [x] 7.10 `./Set-AppVersion.ps1 -Version 0.4.0`; commit. Do not tag, push or deploy.

### Test criteria

| # | Criterion | Layer |
|---|---|---|
| 7-T1 | The Playwright full journey (6-T18) passes on a clean factory-reset instance. | E |
| 7-T2 | Every invariant in rule 6's list has had its bites-check run and recorded. | N |
| 7-T3 | Zero live model calls in the default suite; seam (a) counted only when explicitly enabled. | C/N |
| 7-T4 | Migrations twice; counts and hashes identical; dropped classes absent. | M |
| 7-T5 | Traceability matrix: no slice-1 row `not started`/`in progress`; backlog rows say backlog. | — |
| 7-T6 | `VERSION`, `app_version.py`, UI package metadata read `0.4.0`. | U |

Status: Done (Section 11.1 closure, 31 Jul 2026; commit f1cefb0)
Landed:
- Slice 1 accepted: the 9-card register runs KB-bound cross-field #4 end to end; pending and unknown diagnostics refuse honestly.
- Default ingestion, Test Lab, and RCA paths are deterministic; retired cosmetic live-model calls were removed and factory-reset coverage now preserves KB tags while removing item tags.
- Version bumped to 0.4.0 in VERSION and UI metadata.
Validation:
- `ci-local.ps1`: exit 0 — backend 361 passed/1 skipped; Playwright 16 passed/1 skipped; eslint, build, compile, and throwaway-DB boot PASS.
- `verify_plan8.py` 28/28 PASS; Test Lab Playwright criteria 3/3 PASS (full journey, pending refusal, candidate-flag rendering).
- Migration safety: 6 focused tests PASS; non-empty destructive retirement requires a snapshot, restore reproduces canonical schema/all rows, survivors hash identically, dropped classes are absent, and rerun is a no-op.
Notes:
- Release docs and `docs/0.4.0/09-release-notes.md` are current; all 76 slice-1 trace rows are `done`, with 23 referenced test files and zero missing paths.
- Retained parity spikes use `backend/spike_fixtures.py` and exit 0; they remain evidence only and enable no B2 diagnostic.
- R-08/R-10 remain forward controls: B1 must re-run candidate-flag rendering, and every backlog pull requires requester satisfaction, a recorded D-22 decision, and a bounded phase plan.

Final Status: Complete

What Landed:
- 0.4.0 slice 1: governed KB rules, redesigned ingestion, register-driven Test Lab, and the executable cross-field diagnostic.
- Deterministic default execution with no live-model provider on the slice paths.
- Release version 0.4.0.

Validation:
- `ci-local.ps1` exit 0: backend 361 passed/1 skipped; Playwright 16 passed/1 skipped.
- Framework gate 28/28 PASS; Test Lab criteria 3/3 PASS.

Files Changed:
- `source-codes/` release implementation and version metadata.
- `archimedes-0.4.0-plan.md`.

Open Risks / Follow-ups:
- R-08 and R-10 remain ongoing release-management controls by design; no backlog implementation was enabled.

Model Usage Summary:
- Primary orchestrator and final validation: GPT-5.6 Sol
- Delegated implementation: GPT-5.6 Terra (migration/parity, re-upload/docs, mapping verification)

**Exit gate:** met; every non-backlog todo is checked and Section 11.1 proof is recorded.
**Commit:** `release: Archimedes 0.4.0 — slice 1: cross-field diagnostic, new Test Lab, new ingestion, governed KB`.

---

## 10. Backlog — pulled one at a time, in this recommended order

Nothing here is scheduled. Each item, when pulled, gets its own phase plan with todos and criteria at
the standard of §§2–9. **The gate between items is D-22: requester satisfaction with the previous.**

| # | Item | Content | Source requirements |
|---|---|---|---|
| B1 | **Value-semantics classification (#8)** — the natural second diagnostic | Its workflow definition (requester-provided), engine, register flip; activates the FWK-09/FWK-11 guards for real; row-completeness and resolution-rate depend on it | DIA-01, FWK-08…FWK-12 |
| B2 | **Remaining core diagnostics**, one workflow at a time | #6, #12 (cheap deterministic rules on the cross-field engine's pattern), #11, #2, #14 (PSI — re-verify against `spike_recalibrate.py`), #17 | DIA-02…DIA-08 |
| B3 | **Report commentary** — seam (b) | LLM prose over deterministic statistics; DET-06 no-unsourced-number test | DIA-10, DET-06 |
| B4 | **Workspaces, quotas, deletion authority** | Multi-user, 500 MB allowances, shared-dataset charging, admin warnings; composes with the Phase-2 factory reset | WSP-01…WSP-10, PLT-01 |
| B5 | **RCA corrections** | Per-family plans, five gates, autonomy orchestrator, triage precision, the two RCA seams | RCA-01…RCA-37 |
| B6 | **AI Proposal Loop (#20)** | Context pack + adapters (cross-field first), pipeline, SME gate, revision lane, KB write-back | APL-01…APL-39 |

Plan B (multi-upload) remains out of scope entirely (D-11); its data-model seam already landed
(PLT-02, Phase 3).

---

## 11. Risk register

| # | Risk | Mitigation | Phase | Audit status (31 Jul 2026) |
|---|---|---|---|---|
| R-01 | The KB parser is on the critical path with no fallback rule set — a parse failure stops cross-field entirely. | Parse report built alongside the parser; 5-T1 asserts 49 records; 6-T2 forbids a rule-less PASS; the correction loop is a product feature (D-06). | 5, 6 | **Mitigated.** S9 parity, parse-report playback, and zero-rule N-A tests pass; a parse failure intentionally blocks execution. |
| R-02 | Regulatory rules executed from prose-recovered predicates. | KB-09 binding discipline; 5-T3 proves non-executability. | 5 | **Mitigated.** Binding is single-writer, hazard-gated, and unbound/reference-only rules are non-executable. |
| R-03 | The helper refactor silently changes behaviour under everything built later. | Phase 2 is behaviour-preserving with the baseline suite as tripwire (2-T8); broken imports are root-caused, not papered over. | 2 | **Mitigated.** Isolation imports, helper tests, and the final CI suite pass. |
| R-04 | Deleting the old framework breaks the repo's validators. | Rebuild `verify_plan8.py` + count test in the same commit, new counts asserted (3-T9, rule 7). | 3 | **Addressed.** The rebuilt gate passes 28/28 and retired framework paths are negatively tested. |
| R-05 | The framework swap drops records someone needed. | Pre-drop counts in Phase 0 and the Phase-3 commit; §1.3 explicit; back up first. | 0, 3 | **Mitigated.** Non-empty retirement requires a restorable pre-drop snapshot; restore and survivor content-hash tests pass. The original empty-baseline run is covered by an explicit historical waiver. |
| R-06 | The ingestion redesign breaks existing profiled items. | 4-T9 migration test, run twice; old records mapped, not mutated. | 4 | **Mitigated.** Existing-item migration/idempotency and the user-visible replacement-delivery journey pass; the original item remains unchanged. |
| R-07 | Cross-field parity claimed beyond what the synthetic fixture proves. | 6.7 states proven dimensions explicitly; no workbook dependency by design (rev-4 item 4). | 6 | **Mitigated.** Tests and contract explicitly limit parity to S9 counts/use-case partition and documented S6 behaviours. |
| R-08 | The candidate-flag/verdict distinction collapses when the first statistical diagnostic arrives. | The rendering contract is built and negatively tested in slice 1 (6-T14) before any statistical engine exists. | 6 | **Mitigated for slice 1; ongoing.** Re-run the negative contract when B1 introduces the first candidate-flag producer. |
| R-09 | Factory reset becomes a data-loss footgun. | Admin-only, type-to-confirm, audited with per-table counts; surgical vs full wipe distinct (2-T5…2-T7). | 2 | **Mitigated.** API and Playwright prove authorization, exact confirmation phrases, grade separation, audit counts, and idempotency. |
| R-10 | Scope creep past the controlled-scaling gate. | D-22 in the operating rules (1.15); backlog items have no code until pulled. | all | **Ongoing control.** No backlog diagnostic is executable; keep D-22 as a release-management gate rather than marking this risk closed. |
| R-11 | SQLite additive migrations with no framework. | Idempotent DDL per the `init_schema` convention; run twice (7-T4); back up first. | 2–7 | **Mitigated.** Backup/restore, canonical schema/all-row hashes, survivor hashes, dropped-class absence, and repeat-run stability pass. |

### 11.1 Residual implementation strategy

Implementation conclusion (31 Jul 2026): the non-backlog release is **fully implemented**. All eight
previously unchecked todos are closed in commit f1cefb0; R-08 and R-10 remain ongoing forward
controls by design, not unfinished slice-1 work.

| Order | Todo(s) / risk | Strategy | Completion proof |
|---|---|---|---|
| 1 | 3.1, 7.6; R-05, R-11 | Add a mandatory, restorable SQLite snapshot before `retire_old_framework()` mutates a non-empty database. Extend migration tests to hash canonical schema plus every surviving row before/after, run twice, and perform a restore smoke. Because the original empty-baseline migration has already run, record an explicit historical waiver rather than inventing a retroactive backup. | Backup artifact + restore test; identical survivor schema/content hashes; dropped classes absent; second run a no-op. |
| 2 | 4.6 | Wire `reuploadItemV2` into Data Sourcing for an existing item. Show the new delivery id/sequence, retain the original item unchanged, and carry the family/as-of context into Review. | Playwright replacement journey plus the existing service test: old-item hash unchanged and `delivery_seq + 1`. |
| 3 | 6.4, 7.2 | Implement opt-in role-mapping verification through `ai/llm.py` and `control_plane.resolve`. Send only bounded mapping candidates/provenance, accept structured decisions only, record keep/LLM/manual outcomes, and prohibit any change to rule maths or verdicts. Keep the default off and use counting fakes only in tests. | Off = zero calls; on = exactly one bounded fake call; disagreement creates an append-only decision; verdict bytes match the unverified run. |
| 4 | 7.7 | Port the three retained parity spikes from the deleted `database.py` API to current storage/fixture adapters without reintroducing a production compatibility module or enabling a backlog diagnostic. Then run every §1.4 command independently and record exit codes. | `spike_recalibrate.py`, `spike_gx.py`, `spike_integration.py`, `verify_plan8.py`, `ci-local.ps1`, and Playwright all exit 0. |
| 5 | 7.8, 7.9 | Refresh the named release docs and add a complete 0.4.0 release note (framework removals, 9/1 executable scope, D-22, ingestion/Test Lab replacement, reset/helper changes, rollback limits). Replace stale wizard descriptions. Rewrite all 76 traceability statuses and nonexistent test paths against the landed tests; keep backlog rows explicitly backlog. | Documentation source scan finds no active four-step-wizard claims; traceability has zero `not started`/`in progress` slice rows and every referenced test path resolves. |
| 6 | R-08, R-10 | Preserve these as forward controls: when B1 is explicitly pulled, re-run the candidate-flag negative rendering contract before enabling it; require requester satisfaction and a new bounded phase plan before each Section-10 item. | B1 gate includes candidate-flag contract evidence; no backlog item changes status without a recorded D-22 decision. |

Landed proof: orders 1–5 pass their stated completion checks. Order 6 is recorded in
`docs/0.4.0/07-decisions.md` and the release note; its two risks intentionally remain ongoing until
a D-22-approved backlog pull occurs.

---

## 12. Traceability index

| Requirement block | Phase | Primary evidence |
|---|---|---|
| FWK-01…FWK-05, FWK-17/18 (definition), DET-01…03, ING (contract), D-01…D-22 | 1 | 1-T1…1-T7 |
| PLT-08 (D-21), WSP-08 (D-19), PLT-04/05 | 2 | 2-T1…2-T9 |
| FWK-06…FWK-17 (mechanism), PLT-02/03, FWK-16 | 3 | 3-T1…3-T10 |
| ING-01…ING-10 (D-20) | 4 | 4-T1…4-T10 |
| KB-01…KB-16 | 5 | 5-T1…5-T10 |
| CFR-01…CFR-18, FWK-07 rendering, D-15/D-16 | 6 | 6-T1…6-T18 |
| Slice-1 whole, D-13 | 7 | 7-T1…7-T6 |
| DIA, APL, RCA, WSP-01…07/09/10, DET-06 | backlog §10 | phased when pulled |

---

## 13. If you get stuck

- **A requirement conflicts with the code and neither obviously wins.** Do not pick silently. Write
  both options and your recommendation into `docs/0.4.0/07-decisions.md`, implement the
  recommendation, flag it in the commit message.
- **A test fails and the fix looks like changing the test.** Re-read rule 7. If the test encodes
  superseded behaviour, change it deliberately, in the same commit as the cause, with the reason
  recorded. Otherwise the code is wrong.
- **An input you need has not arrived** (the standing case: the 8 pending diagnostic workflows).
  Rule 14. Stop at that boundary, record it, finish the rest of the phase.
- **A phase is larger than it looked.** Split at a natural contract boundary and commit the first
  half with its own passing gate. Never carry a half-finished phase forward.
- **The same task fails twice.** Stop retrying. Preserve the evidence, diagnose read-only, then start
  a new bounded attempt with a different approach.
- **You need a human.** Pause only for: material ambiguity the documents do not resolve; a new
  dependency; credentials; live billable model calls; deployment; destructive operations against
  non-test data; product-scope expansion — including any temptation to start a backlog item early
  (D-22). Between normal bounded tasks, proceed.
