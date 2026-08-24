# Test Lab Redesign — 0.4.0 (Slice 1)

**Status:** Design for build. Adopted by D-16; scoped by D-15 (slice 1 executes only diagnostic #4)
and D-17 (the register holds only the 9 core diagnostics — S8's 11 defer rows are documentation, not
product scope).
**Authority:** [requirements-0.4.0.md](requirements-0.4.0.md) governs *what*;
[archimedes-0.4.0-plan.md](archimedes-0.4.0-plan.md) Phase 6 governs *when and done-means*; this
document governs the Test Lab's **workflow, architecture, records and API**.
**Baseline module being replaced:** `ui/src/pages/TestLab.jsx` + `ui/src/pages/testlab/*` (four-step
wizard) and the plan/snippet/recommend paths in `backend/ai/v2/service.py` + `backend/routers/v2.py`.
**Workflow source:** the cross-field V2 flow chart
(`dev-requirements/cross_field_engine_V2_workflow_1.png`, received 30 Jul evening) — §3 maps onto it
step for step: chart steps 0–4 = readiness + scope-gate manifest; the chart's interactive prompts
(use-case, mapping-conflict pause) = decision records with documented defaults (CFR-12); step 5 + the
per-rule gate = the run; step 6 + `resolution_map.json` = findings + persisted manifest provenance.
**Date:** 30 July 2026.

---

## 1. The challenge — why the current Test Lab cannot be patched

The shipped module is a four-step wizard: **1 Framework Application → 2 Execute Framework Tests →
3 AI Recommendation → 4 Execute Incremental + roll-up** (`TestLab.jsx:14`). Each defect below is
structural — fixing any one of them inside the current shape breaks the shape.

### 1.1 It plans the wrong unit

The wizard plans **tests from the retired registry** (14 tests × 11 `fw_areas`, seeded from
`dq_framework_data.json` every boot). The new framework's unit is the **diagnostic** inside a **test
area** (FWK-02), carrying mode, stage, Det./Stat., decision type, KB dependency and MVP scope. None of
those fields exists in `plan_v2` or the UI. There is no mapping from the old unit to the new one —
FWK-13 deletes or absorbs most of the fourteen.

### 1.2 It is a micro-approval ladder — the pattern the RCA correction already outlawed

To run one assessment the user performs, per table: Build Plan → repair auto-chosen columns per row →
Finalize per table (or Finalize All) → generate snippet per row → **Approve** per row (or Approve All)
→ Run → repeat for the incremental scope. That is the *"shall I run the next look?"* anti-pattern
C-06 removed from RCA, rebuilt in another module. Approve-All buttons are the confession: when the
designed decision is so uninformative users need a bypass-all control, it was never a decision.

### 1.3 Snippet-as-source-of-truth is a governance hole

`snippet()` inlines the registry function source verbatim into an editable text blob
(`service.py:1121`, `registry.py:243`); execution runs **the blob**, not the registry
(`execute_iter`, `service.py:1237`), and edited params are written back over the plan
(`_resync_params`, `service.py:1224`). So the executable artifact is user-editable Python at runtime —
against the spirit of DET-05 (bounded, allowlisted helpers only) and against the release's own KB
discipline (KB-09 forbids executing prose-recovered predicates while this module executes
paste-recovered ones). "Approve" approves code text, not a decision; "Regenerate" regenerates what the
registry already holds. Sandbox AST checks reduce the blast radius; they do not make it a contract.

### 1.4 Eligibility is name-substring folklore, and domain knowledge is hardcoded

Column selection dispatches on **substrings of the test name** (`"PSI" in test_name`,
`_choose_columns`, `service.py:738-787`) — renaming a test silently changes what it runs on. The
selection heuristics carry literal `facility_id`, `origination_date`, `observation_date`; plausibility
bounds are inferred from column-name substrings (`registry.py:87-88`); `TYPE_PRIORITY` encodes a CRE
mortgage schema (`service.py:40-48`). All of it violates the no-domain-knowledge-in-code rule the rest
of 0.4.0 enforces (KB-01 family).

### 1.5 Step 3 is dead on arrival — and its territory now belongs to APL

The recommender keyword-matches `knowledge_base/business_rules.json` — the hardcoded KB that C-36
deletes. It also has a casing bug: rows are written `origin="ai_recommended"` (`service.py:1555`) but
snippet generation and score roll-up gate on `"AI_recommended"` (`service.py:1127`,
`categories.py:37`) — recommended tests fall through to an empty implementation lookup and execute as
`not_runnable`, and are never scored. **The flow demonstrably never worked end to end**, which is
evidence it was never load-bearing. The AI-proposal loop (§6 of the requirements, T6/#20) replaces
this properly: gap-driven, SME-gated, revision-laned, provenance-typed. Step 3 must not survive as a
parallel, weaker proposal path.

### 1.6 The result model cannot say what the framework needs said

`results_v2.status ∈ {pass, fail, not_runnable, info}` and the UI renders fail as a red destructive
badge. FWK-07 requires candidate flags and contextual results to **never render as failure** — the
current model has no `decision_type`, no NOT-APPLICABLE-with-reason (CFR-04/08), no scope accounting
(evaluated / skipped-censored / excluded, CFR-07), no severity, no exceptions-with-reasons, no
clustered/scattered read (CFR-09/10). Cross-field's structured result cannot be projected into it
without loss.

### 1.7 No staging, no KB linkage, no honesty

Nothing orders Stage 1 before Stage 2 (FWK-08/10) — the wizard's steps are UI steps, not execution
stages. No plan row knows which knowledge powers it; a rule-driven test with zero rules would pass or
fail generically instead of returning NOT-APPLICABLE (CFR-04). Two `fw_areas` map to zero tests yet
render as covered framework; scoring weights are the retired ones; the provisional/final split is an
artifact of wizard steps, not of what ran. `sync_issues` mutates an existing issue when new columns
fail (natural key is item+table+test), quietly rewriting history.

**Verdict: replace the module.** The engine registry, sandbox, SSE idiom, item/inventory pipeline and
Issue Management hand-off survive as platform; the workflow, records and UI are rebuilt.

---

## 2. Design principles

1. **The register drives the module** (FWK-04/05, D-17): what appears, what runs, what refuses — all
   data from the 9-diagnostic register. Adding or enabling a diagnostic is a register change plus an
   engine, never a UI change (FWK-18).
2. **Two human decisions, everything else automatic** — the same autonomy contract as RCA (C-06):
   a **scope gate** before running, an **SME disposition** after. No per-row approvals, no code
   review theater.
3. **What the user approves is the manifest, not code.** Engines are registered, versioned,
   deterministic code (DET-01/05). The manifest — roles, rules, thresholds, exclusions — is the
   entire human-controllable surface, and it persists as run provenance.
4. **Decision types are first-class** (FWK-07): verdict / candidate flag / contextual /
   classification / SME gate render as different things, not colors of pass-fail.
5. **Honesty over completeness** (FWK-14/15/16/17): workflow-pending, NOT-APPLICABLE, blocked and
   the area-level GAP strip are all visible, distinct, and never dressed as coverage or failure.
6. **No domain knowledge in code** (KB-01): rules, roles, thresholds and semantics come from the
   published KB and the semantic layer. The module knows *how to run diagnostics*, never *what a
   mortgage is*.

---

## 3. The new workflow

One page, one item, three panes — **Coverage → Run → Findings** — progressing left to right, always
re-enterable.

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Test Lab                       [item selector ▾]  [variable inventory ▸]  │
├────────────────────────────────────────────────────────────────────────────┤
│  ① COVERAGE                ② RUN                      ③ FINDINGS           │
│  register board            scope gate → execute        results by decision │
│  + readiness               (manifest, SSE stream)      type + disposition  │
│                                                        + score & report    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Step 1 — Coverage board (no decisions)

**Exactly 9 diagnostic cards** — one per register row (D-17) — grouped under their six test-area
headers, plus one **area-level GAP strip** at the bottom for the L2 assessment areas no registered
diagnostic reaches (FWK-14). Nothing else exists on the board: no cards for S8's defer rows.

**Card anatomy.** Every card renders the same eight fields, all from the register plus the readiness
evaluation — no free text, no per-card variation:

1. **Name + S8 row id** — e.g. "Cross-field business rule · #4"
2. **Mode** — hard / relational / statistical / loop
3. **Stage** — 1 / 2 / Both
4. **Det./Stat.** — deterministic or statistical
5. **Decision type** — verdict · candidate flag · contextual · classification output · SME gate
6. **KB dependency** — Low / Medium / HIGH / reads & writes (from Plan A)
7. **Status chip** (below) with its reason line
8. **Last run** for this item, when one exists — verdict roll-up + timestamp, linking to Findings

| Chip | Meaning | Slice 1 |
|---|---|---|
| **Ready** | executable + preconditions met for this item | #4 (once a KB is published for the item's scope) |
| **Not applicable — <reason>** | executable, but no eligible bound rules for this item's tags | #4 (until then) |
| **Blocked — <dependency>** | executable, but a named precondition fails (not profiled, no grain key…) | — |
| **Workflow pending** | registered, no workflow definition yet (FWK-17) | the other 8 |

**Example cards (slice 1, item tagged `retail mortgage / IRB`, KB published):**

```
┌─ T2 · KB-driven data validation ────────────────────────────────┐
│ Cross-field business rule                                   #4  │
│ hard · Stage: Both · Deterministic · Decision: verdict          │
│ KB dependency: HIGH                                             │
│ ● READY — 36 bound rules in scope (IRB), roles resolvable       │
│ Last run: today 21:40 — 33 PASS · 2 VIOLATION · 1 N-A  [view]   │
│                                                    [Open scope] │
└─────────────────────────────────────────────────────────────────┘

┌─ T2 · KB-driven data validation ────────────────────────────────┐
│ Value-semantics classification                               #8  │
│ hard · Stage: 1 · Deterministic · Decision: classification      │
│ KB dependency: HIGH                                             │
│ ◌ WORKFLOW PENDING — workflow not yet defined; next to enable   │
│   (no run affordance)                                           │
└─────────────────────────────────────────────────────────────────┘

┌─ T4 · Portfolio / population representativeness ────────────────┐
│ Population Stability Index (PSI)                            #14 │
│ statistical · Stage: 2 · Statistical · Decision: threshold+SME  │
│ KB dependency: Low-Med                                          │
│ ◌ WORKFLOW PENDING — workflow not yet defined                   │
└─────────────────────────────────────────────────────────────────┘
```

And the area-level strip (not cards — one line per unreached L2 area):

```
GAP by design: Feature Stability & Behavioral Consistency (multi-upload) ·
Dataset Drift & Degradation Monitoring (multi-upload) · External / vendor data
robustness (no diagnostic) · Downturn & Regime Coverage · Missingness Mechanism
```

Readiness is evaluated **automatically and deterministically** per executable diagnostic
(§5 `readiness()`): dataset profiled; published + effective + bound rules exist for the item's
taxonomy scope (use case `irb`/`ifrs9`/`both`); roles resolvable. This replaces plan-build,
eligibility heuristics and the dry-run probe. The user *reads* this pane; they decide nothing here.

### Step 2 — Scope gate, then run (human decision #1)

Selecting a **Ready** diagnostic builds its **run manifest** — the single pre-run decision surface:

- **Rules in scope**: count by severity and type, source KB document + version, framework filter
  applied (`irb`/`ifrs9`/`both`).
- **Resolved role → column map** with the auto/override split (S6's `Roles resolved: 36/36 (0
  override, 36 auto)`) and, per role, the **match provenance from the deterministic score ladder**
  (exact 1.0 · synonym 0.92 · dictionary-description 0.88 · token-subset 0.80 · fuzzy 0.55–0.75, with
  the dtype gate — CFR-05 / chart step 3), unresolved roles named. **Optional LLM verification of the
  mapping** (chart step 3b, seam a) is **off by default** (D-08); turning it on is a manifest option,
  every mapping change it causes is recorded, and a disagreement becomes a decision record
  (keep / accept-LLM / manual) — never a blocking prompt. The LLM touches mapping only, never a
  verdict — the chart's own guarantee.
- **Thresholds/parameters** from the semantic layer (FWK-06) with their source: `default` or
  `tuned (by, when)` — e.g. tolerance, `cluster_lift` 3.0, `cluster_coverage` 0.5, `grain_role`,
  `segment_role`.
- **Scope preview**: tables/entities included, rows expected, exclusions with reasons (rule-encoded
  exceptions in slice 1; the value-semantics gate from #8 in slice 2 — requirements §1.3 slice-1 note).

The user may: **override a role mapping**, **tune a threshold**, **exclude a table/entity**. Each edit
writes a **decision record** (CFR-12) — never a blocking prompt; an unattended run applies the
documented default and records that it did. Then **Run**. The manifest freezes and persists as the
run's provenance: two runs from one manifest on one snapshot are byte-identical (CFR-16).

### Step 3 — Execution (no decisions)

Stage-ordered (Stage 1 diagnostics before Stage 2 — vacuous in slice 1, enforced by the runner, not
the UI), streamed over SSE in the existing `execute/stream` idiom (CFR-13): `start` → per-diagnostic
`progress` (rules evaluated / total) → `done`. Errors surface sanitized (PLT-04). Read-only against
the data (CFR-15); no user-editable code exists anywhere on this path.

### Step 4 — Findings, by decision type (human decision #2)

Results grouped **area → diagnostic → finding**, rendered by `decision_type`:

- **Verdicts** (cross-field, and later #6/#12): **PASS / VIOLATION / NOT-APPLICABLE**. A VIOLATION
  shows severity (CRITICAL → MATERIAL → MINOR ordering), rule id + regulatory reference, violation
  count and rate vs tolerance, **exceptions applied with their stated reason and count** (CFR-07 —
  "silent exceptions are how rules rot"), up to 5 evidence rows keyed by grain, and the
  **CLUSTERED vs SCATTERED** pattern read with its implication (feed/segment fault vs capture error)
  (CFR-09). NOT-APPLICABLE states its reason ("no knowledge base published for scope irb").
- **Candidate flags / contextual** (later slices): "for review" cards — never a failure badge
  (FWK-07). The SME **disposition** is the decision: *confirm as issue* or *dismiss with reason*;
  both recorded.
- **Classification output** (#8, slice 2): a tag summary, no pass/fail at all.

Issue creation: a verdict VIOLATION **auto-opens** an issue (threshold diagnostics auto-open on FAIL —
RCA-27); candidate flags open issues **only through SME confirmation**. Issues carry diagnostic id,
run id and finding reference into the existing Issue Management / RCA hand-off (CFR-14).

### Step 5 — Score & report (no decisions)

The score panel is **coverage-honest** (FWK-16): it states which diagnostics ran, which are pending,
and what the number covers. In slice 1 that means a **cross-field verdict roll-up** (rules passed /
violated / N-A, by severity) — *proposed:* no weighted "health score" is displayed until at least two
core diagnostics are executable, because a one-diagnostic health score misleads (needs requester
confirmation; recorded as an open item, not assumed). The PDF report derives from the structured
results (CFR-10); LLM commentary, when added, is seam (b) under DET-06.

### The proposal loop (T6/#20) — adjacent, not inside

The Coverage board shows #20 as core; its **Propose** action activates after a completed run and
routes to the APL module (Phases 11–12). The Test Lab never grows its own suggestion path — §1.5's
lesson.

---

## 4. What retires, what survives

| Retires | Survives |
|---|---|
| Four-step wizard (`TestLab.jsx` steps, `Step1Framework/Step2Execute/Step3Recommend/Step4Rollup.jsx`, `PlanParts.jsx` plan editing) | Item selector; read-only `VariableInventory` viewer |
| `plan_v2` micro-states (`pending/proposed/accepted/finalized/removed/rejected`), per-table finalize, Add-a-test panel | `dq_items`, `dq_item_files`, `dq_item_tables`, `variable_inventory`, profiling |
| Snippet generate/regenerate/approve/edit endpoints and `snippet_code` execution | The sandbox (`ai/code_sandbox.py`) as defense-in-depth for registered engines |
| `recommend/stream` + `business_rules.json` matching (C-36) and the `RULE_KEYWORDS`/`_test_for_rule` machinery | SSE streaming idiom; `AgentConsole` |
| `_choose_columns`/`TYPE_PRIORITY` name heuristics as *planning*; hardcoded schema literals | Column classification as *profiling metadata* (KB/roles decide usage) |
| Old scoring path (`WEIGHTS` 5/3/1 over `fw_family_weights`) mid-wizard | Issue Management, RCA hand-off (rewired to diagnostic results) |
| `fw_areas`/`fw_tests` content and both legacy seeds' content (storage concept stays — C-37) | Report download endpoint (re-sourced from structured results) |

User-added tests: the old "Add a test the AI might have missed" panel disappears. Its two legitimate
uses have proper homes — *my rule isn't checked* → upload/extend the KB (the readiness pane reflects
it immediately); *the AI should look for more* → the proposal loop with its SME gate.

### 4.1 Deletion inventory

**Tranche 1 — deleted 30 Jul 2026** (requester instruction; every file verified unreachable from the
mounted app before deletion; verified after by backend pytest 89 passed / 1 skipped and a clean vite
build). 28 files:

- Unmounted legacy routers (only `auth`, `admin`, `v2`, `v3` are mounted — `main.py:150-153`):
  `routers/test_lab.py`, `routers/test_plan.py`, `routers/test_library.py`, `routers/tests.py`,
  `routers/dq_framework.py`
- Their private executors, reachable from nowhere else: `ai/test_execution.py`, `ai/test_worker.py`,
  `ai/test_report_pdf.py`
- The 14 `dq_tests/stage1/` + `dq_tests/stage2/` shim modules (registry.py never imports them)
- Unrouted UI pages (absent from `App.jsx` routes): `pages/TestLibrary.jsx`,
  `pages/DefineTestPlan.jsx`, `pages/RunValidations.jsx`, `pages/NewAssessment.jsx`

**Tranche 2 — deleted at cutover (plan Phase 6.13 / Phase 3), when the replacement lands**, because the
live app still calls them today: the wizard components (`testlab/Step1Framework.jsx`,
`Step2Execute.jsx`, `Step3Recommend.jsx`, `Step4Rollup.jsx`, `PlanParts.jsx` plan editing), the
plan-build / snippet / recommend endpoints and service paths, the now-orphaned legacy block in
`ui/src/api/client.js` (`getTestLibrary`, `getTestPlan`, `getTestKit`, plan-item CRUD, …), the
14-test registry + param specs (FWK-13), `knowledge_base/business_rules.json` + loader (C-36), and
`fw_areas`/`fw_tests` content.

**Kept deliberately, with the reason:** `ai/test_manager.py` (imported by `verify_plan8.py`, a live
validation gate — goes when `verify_plan8.py` is rebuilt in Phase 3); `gx/` + `spike_*.py` (the PSI
parity evidence — 3-T11/6-T9 depend on `spike_recalibrate.py`); `ai/code_sandbox.py` (defense-in-depth
under registered engines).

**Found while verifying, out of Test-module scope — flagged for a separate sweep decision:** seven
more unmounted routers (`ai_rules`, `inventory`, `monitoring`, `preview`, `skills`, `tickets`,
`use_cases`); unrouted pages (`Dashboards.jsx`, `AgenticSkills.jsx`, `AIAgents.jsx`,
`Placeholder.jsx`, `pages/RCA.jsx`); and two **pre-existing broken imports** on unmounted paths:
`ai/rca_helpers.py:16` imports a `database` module deleted in the 0.2.0 era (breaks `ai.test_kit`
imports), and `ai/tool_registry.py:148,217` imports `routers.ingestion` / `routers.tickets` — the
former does not exist. Neither is reachable from the mounted app; both should be fixed when their
modules are next touched (test_kit is needed by CFR-06).

---

## 5. Architecture

### 5.1 Backend package

```
backend/dq_diagnostics/
  register.py        # load/query the seeded register; refusal messages per workflow_status
  readiness.py       # deterministic precondition evaluation per (item, diagnostic)
  manifest.py        # build, patch (decision records), freeze, persist
  runner.py          # stage-ordered orchestration, SSE events, persistence of results/findings
  engines/
    base.py          # the DiagnosticEngine protocol (the FWK-18 seam)
    cross_field/     # slice 1: port of S5 per plan Phase 6.1 (rules, primitives, roles, engine, result)
```

The **engine protocol** is the extension seam every future workflow lands on:

```python
class DiagnosticEngine(Protocol):
    diagnostic_id: int                      # register key — the S8 row id of one of the 9 core
    def readiness(self, item, ctx) -> Readiness            # ready | not_applicable(reason) | blocked(dep)
    def build_manifest(self, item, ctx) -> ManifestSection # rules, roles, thresholds, scope preview
    def execute(self, manifest, ctx, emit) -> StructuredResult  # deterministic, read-only, SSE via emit
```

Registered per diagnostic id; a workflow-pending diagnostic simply **has no engine**, and
`register.py` answers for it with the refusal message. Enabling #8 later = one engine + one register
flip (FWK-18) — no runner, manifest or UI change (mirror of APL-08's architecture acceptance).

Engines use `kb.list_eligible_rules(...)` (published + effective + bound, workspace/tag-scoped —
KB-12/16, CFR-03), the semantic layer for thresholds (FWK-06), and `v2_service._read_table` for data.
LLM appears only at seam (a) — role verification, off by default (CFR-11/D-08) — through
`ai/llm.py` + `control_plane.resolve`.

### 5.2 Records (additive; `system_db.py` conventions)

| Record | Key fields | Notes |
|---|---|---|
| `diagnostic_register` | **9 rows** (D-17). `diagnostic_id` (S8 row number: 2, 4, 6, 8, 11, 12, 14, 17, 20), `area (T1..T6)`, `name`, `mode`, `what_it_computes`, `metric`, `threshold_based`, `threshold_rule_default`, `det_stat`, `decision_type`, `stage`, `kb_dependency`, `workflow_status {executable, workflow_pending}`, `l2_areas_json`, `enabled_by` | Seeded data (FWK-04); replaces `fw_tests`/`fw_areas` content. `enabled_by` records the FWK-18 decision. No defer row is seeded |
| `threshold_settings` | `diagnostic_id`, `key`, `value`, `scope {default, dimension, engagement}`, `actor`, `ts` | The semantic layer (FWK-06). No threshold constant in code |
| `diag_runs` | `run_id`, `item_id`, `manifest_json` (frozen), `status`, `engine_versions_json`, `started_at`, `finished_at` | Manifest = provenance; later carries PLT-02's delivery reference |
| `diag_run_decisions` | `run_id`, `kind {role_override, threshold_tune, scope_exclusion, default_applied, role_verification_change}`, `payload_json`, `actor`, `ts` | CFR-12; append-only (PLT-05) |
| `diag_results` | `result_id`, `run_id`, `diagnostic_id`, `entity/table`, `decision_type`, `verdict {pass, violation, not_applicable}` *or* `review_state {open, confirmed, dismissed}`, `metrics_json`, `thresholds_used_json`, `scope_counts_json` (evaluated / skipped-with-reason / excluded), `na_reason` | One per diagnostic × entity; decision-type-shaped, not pass/fail-shaped |
| `diag_findings` | `finding_id`, `result_id`, `rule_id`, `severity`, `violation_count`, `rate`, `tolerance`, `exceptions_json` (reason + count), `evidence_json` (≤5 rows, grain-keyed), `pattern {clustered, scattered}`, `regulatory_ref` | The cross-field per-rule detail (CFR-07…CFR-10); shape reused by #12 later |
| `diag_dispositions` | `target (result/finding)`, `action {confirm_issue, dismiss}`, `reason`, `actor`, `ts` | SME decision #2; mandatory reason on dismiss |

`issues_v2` gains `diagnostic_id`, `run_id`, `finding_id` (natural key extended — fixes §1.7's
mutation) and is populated from verdicts/dispositions instead of `sync_issues` over `results_v2`.

### 5.3 API surface (replacing the plan/snippet/recommend family)

| Endpoint | Purpose |
|---|---|
| `GET  /api/v2/items/{id}/diagnostics/board` | Register + per-diagnostic readiness for this item |
| `POST /api/v2/items/{id}/diagnostics/manifest` | Build the manifest for the selected diagnostic(s) |
| `PATCH /api/v2/diagnostics/manifests/{id}` | Role override / threshold tune / exclusion — each a decision record |
| `POST /api/v2/diagnostics/manifests/{id}/run` → `GET …/runs/{run_id}/stream` | Freeze + execute, SSE |
| `GET  /api/v2/items/{id}/diagnostics/results?run_id=` | Results + findings, decision-type-shaped |
| `POST /api/v2/diagnostics/findings/{id}/disposition` | SME confirm/dismiss (reason enforced server-side) |
| `GET  /api/v2/items/{id}/diagnostics/coverage-summary` | Coverage-honest roll-up for score panel & report |

All new routes behind the shared error sanitizer (PLT-04); workspace scoping as per WSP-09 when P2
lands (single-tenant shim until then, same as the rest of the product).

### 5.4 UI structure

```
ui/src/pages/TestLab.jsx            # shell: item selector, pane tabs, inventory viewer (kept)
ui/src/pages/testlab/
  CoverageBoard.jsx                 # areas × diagnostics, status chips, readiness reasons
  ScopeGate.jsx                     # manifest: rules, roles, thresholds, scope; edit → decision record
  RunConsole.jsx                    # SSE progress (reuses stream.js + AgentConsole)
  FindingsPanel.jsx                 # by decision type; verdict vs review rendering; dispositions
  ScorePanel.jsx                    # coverage-honest summary + report download
```

Rendering rules that carry requirements: a candidate flag never uses the failure badge variant
(FWK-07 / 6-T14); workflow-pending cards have no run affordance (6-T10); NOT-APPLICABLE always shows
its reason (CFR-04); severity orders findings and never changes counts (CFR-08).

---

## 6. Requirement traceability (module-level)

| Requirement | Where it lands |
|---|---|
| FWK-02 (diagnostic = unit) | Register board, manifest, results all keyed by `diagnostic_id` |
| FWK-05/17/18 (9 core, executable vs pending, workflow-gated enablement) | `workflow_status` chip + refusals; engine protocol seam |
| FWK-06 (semantic-layer thresholds) | `threshold_settings` + manifest display with source |
| FWK-07 (decision types) | `decision_type` on every result; FindingsPanel rendering rules |
| FWK-08/10/11 (staging + gate) | `runner.py` stage ordering; refusal when Stage-2 lacks tags (slice 2) |
| FWK-14/15/16 (honesty) | Coverage board chips; ScorePanel coverage statement |
| CFR-01…CFR-18 | `engines/cross_field/` + manifest (roles, D-08 toggle), findings shape, SSE, persistence, read-only, determinism |
| KB-01/12/16 | Rules only via `kb.list_eligible_rules`; no rule set in module code |
| DET-01/02/05 | Deterministic movement; LLM only at seam (a) in manifest role verification; no editable code on the run path |
| RCA-27 seam | Verdict auto-opens issue; candidate flag opens only via disposition |
| PLT-04/05 | Sanitizer on new routes; append-only decisions/dispositions |

---

## 7. Open items (not assumed)

1. **Health score display in slice 1** — proposal in §3 Step 5 (verdict roll-up only, no weighted
   score until ≥2 diagnostics executable). Needs requester confirmation; FWK-16 honesty applies
   either way.
2. **Module name** — "Test Lab" is kept for continuity while the internal unit becomes the
   diagnostic (FWK-02 naming discipline applies to labels inside the module: areas group,
   diagnostics run).
3. **Multi-diagnostic manifests** — the manifest model supports selecting several Ready diagnostics
   into one run (the runner stage-orders them). Slice 1 exercises it with one; no UI batching work
   beyond the checkbox.

*(Withdrawn 30 Jul: the `mortgage_irb_-_database.xlsx` ask. The product runs on any uploaded or
ingested data; the workbook was only a parity test fixture. Cross-field parity is proven on a
synthetic fixture — plan 6.7 — asserting rule counts, verdict gate, severity ordering, exception and
skip accounting independently of any specific dataset.)*
