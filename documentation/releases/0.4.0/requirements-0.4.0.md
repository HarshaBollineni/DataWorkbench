# Archimedes 0.4.0 — Consolidated Requirements

**Status:** Requirements consolidated; conflicts resolved except where §6 says otherwise. The
cross-field workflow is now provided (see §0.1 rev-3 item 4); flow charts for the other core
diagnostics remain outstanding.
**Target version:** `0.4.0` — see §0.4. Released is `0.3.0`; this release is the next minor. The
earlier `0.5.0` target was a documentation error, corrected on requester instruction (30 Jul).
Filenames and version now agree.
**Baseline:** `0.3.0` on branch `dev` @ `c0e8e06`
**Revision:** 5 — 30 July 2026 (late evening). Supersedes revision 4 in full. Rationalized, not appended.
**Companion:** [archimedes-0.4.0-plan.md](archimedes-0.4.0-plan.md) — the build instruction ·
[testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) — the Test Lab redesign

---

## 0. Preliminaries

### 0.1 What changed in this revision

**Revision 5 (30 July 2026, late evening) — six requester instructions:**

1. **The cross-field flow chart arrived** — S10 is now
   `dev-requirements/cross_field_engine_V2_workflow_1.png`, reconciled against the Test Lab redesign
   with no design change (C-32 fully closed for cross-field). It confirms **exactly two LLM
   touchpoints** on the cross-field path: optional mapping verification after deterministic role
   resolution (seam a, off by default), and report commentary over deterministically computed
   statistics (seam b — the chart's own report step is a deterministic writer; commentary is the
   product layer, DET-06).
2. **The material-fields filter is back** (D-18, superseding D-05) with the data-driven definition in
   FWK-09.
3. **Admin factory reset is restored** (D-19, WSP-08): surgical reset and full wipe, admin-only,
   audited, exposed in the Admin UI.
4. **The data ingestion workflow is redesigned** (D-20): new §8b ING block — Drop → Review → Ready.
5. **The helper/tool layer is decluttered first** (D-21, PLT-08) — an upfront phase in the re-cut
   plan.
6. **The plan is re-sequenced into fewer phases** with the Test Lab early, and **controlled scaling**
   is an explicit gate: one diagnostic, requester satisfaction, then the next (D-22).

**Revision 4 (30 July 2026, evening) — requester corrections.** Five points from the requester's
review of revision 3:

1. **Version is `0.4.0`, not `0.5.0`.** `0.3.0` is released; this release is the next minor. Pure
   documentation fix, applied throughout (D-13 confirmed).
2. **Product scope is the 9 core diagnostics only — not 20.** Only the *Diagnostics detail* rows with
   `MVP Scope = Core` are in scope. The 11 `Defer (phase-2)` rows are **S8 documentation, not product
   scope**: not registered, not displayed, not executable, not offered anywhere (D-17, superseding the
   rev-2/3 "registered but deferred" reading). The register holds exactly 9 rows.
3. **No cross-field flow chart was received.** What exists is the working engine (S5), its output
   contract (S6) and the register row (S8) — the workflow was reconstructed from those and specified
   in [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) §3. C-32's wording is corrected to say
   so; if a chart arrives later, reconcile against the redesign.
4. **`mortgage_irb_-_database.xlsx` is withdrawn as an ask.** The product must work on any uploaded
   or ingested data; the workbook was only ever a *parity test fixture*. Parity is proven on a
   synthetic fixture instead (plan 6.7).
5. **"Material fields" has no definition in S8.** Recorded under FWK-09: if the filter ever returns,
   materiality must be a data-driven definition (KB-role-mapped / declared feature-target set /
   dictionary-flagged), decided then — never a hardcoded list. D-05 (filter dropped) stands.

The dead Test-module code identified in the redesign §1 was deleted the same evening (28 files:
5 unmounted routers, 3 dead `ai/` executors, the 14 `dq_tests/stage1+stage2` shims, 4 unrouted UI
pages) — verified by backend pytest (89 passed, 1 skipped) and a clean vite build. The redesign §4.1
carries the inventory.

**Revision 3 (30 July 2026).** `MVP_Test Plan_DA.xlsx` was re-issued on 30 July and walked through
with the requester. The authoritative sheets are the three green tabs — *Plan A - single upload*,
*Diagnostics detail*, *Detailed_DQ_Framework* — read ignoring every column back-filled `#F79646`
(dataset-specific bindings and reviewer comments: *Source binding*, *IRB example*, *Harsha's comments*,
*IRB dataset example(s)*, *In team's top-5?*). *Plan B - multi upload* is *completely out of scope*;
the two red tabs are not authoritative for this revision. Five things moved:

1. **MVP core is 9 diagnostics, not 10.** The re-issued sheet marks *Categorical leakage* (#3)
   `Defer (phase-2)`; its footnote block states the core outright: "the MVP CORE is 9 buildable
   diagnostics." This reverses D-09 (recorded as D-14). Core = 9, Phase-2 = 11, proposal-loop
   adapters = 8.
2. **The L1/L2 taxonomy is anchored to the `Detailed_DQ_Framework` tab**: **6 L1 themes → 11 L2
   assessment areas**, *including* *Third party data quality and controls* — revision 2 recorded that
   theme as retired; it is not. It stays in the framework as a theme no single-upload diagnostic
   reaches (a by-design coverage gap), not a deleted one.
3. **Build slicing: only diagnostic #4 (Cross-field business rule) is executable in the first build
   slice.** It is the one diagnostic whose workflow has been provided (S5/S6 engine + S8 register
   row). The other 8 core diagnostics are **registered and visible** in the framework but **not
   executable** until each receives its own workflow definition. See FWK-17/FWK-18.
4. **The Test Lab module is redesigned from scratch** around the diagnostic register —
   [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) replaces the shipped four-step
   plan-and-execute wizard. The workflow in that document is the execution shape C-32 was waiting for,
   for the cross-field diagnostic; flow charts for the other core diagnostics remain outstanding.
5. **S9 relocated.** `KB_cross_field_reference_2.pdf` now lives at
   `source-codes/synthetic-kb/KB_cross_field_reference_2.pdf` (untracked), consistent with KB-01's
   rule that it is test fixture material, never a shipped rule set.

**Revision 2 (29 July 2026)** — retained context. Revision 1 was written before the framework and
knowledge-base inputs arrived. Four things moved:

1. **The DQ framework is replaced, not extended.** `MVP_Test Plan_DA.xlsx` is the new framework
   (structure now per rev-3 items 1–2 above). The shipped 11-area / 14-test framework is retired. Old
   tests are deleted where not repurposed, and downstream records are rewired to the new diagnostics.
2. **Determinism is the default, not the fallback.** Revision 1 proposed converting the RCA agents to
   LLM calls. That is reversed. Workflows move by Python; the LLM is confined to four named seams.
3. **The knowledge base is uploaded, never hardcoded.** No domain rules ship in code — not in
   `build_generic_kb()`, not in `business_rules.json`. The user uploads a document, tags it with
   taxonomy dimensions, and the product parses it and plays back a summary.
4. **Multi-user workspaces with quotas** are in scope, with shared datasets and admin-only deletion.

Requirement IDs from earlier revisions are preserved where the requirement survives. Conflict IDs are
**never renumbered** — closed ones are listed with their disposition in §10.1 so a reference to "C-03"
still resolves.

### 0.2 Source inventory

| # | Source | Form | What it contributed |
|---|---|---|---|
| S1 | `dev-requirements/RCA_Workflow_Design 1.md` | 291-line design, Parts A+B, 19 sections | Full RCA functional design incl. three validator rounds, per-issue independence, per-test plans, autonomy contract |
| S2 | `dev-requirements/RCA_Implementation_Instructions_1.md` | 111-line instruction set | Corrections to **five deviations seen in a prior build** (that build is `dev` @ 0.3.0), canonical 12-agent order, 7-point acceptance test |
| S3 | `dev-requirements/ai_proposal_loop_workflow.md` | 191-line workflow spec | AI Proposal Loop: four architecture principles, context pack, candidate object, 6 stages, SME gate screen, 9 invariants, 5 open items |
| S4 | `dev-requirements/ai_proposal_loop_architecture_1.png` | Flow diagram | Confirms S3's topology: adapter → single pipeline → SME gate → revise/park → compile → execute → optional KB write |
| S5 | `V2_files need API.zip` → `cross_field_rule_engine.py` | 1,374-line CLI | Working cross-field engine: 5 rule types, 9 primitives, role resolver, violation clustering, text report |
| S6 | `V2_files need API.zip` → `report.txt` | 161-line sample output | The engine's output contract, from a run on `mortgage_irb_-_database.xlsx` |
| S7 | `dev-requirements/requirements_1.txt` | 16-line pip file | Dependency set for S5 — already satisfied; see C-17 |
| **S8** | `dev-requirements/MVP_Test Plan_DA.xlsx` — **re-issued 30 Jul 2026** | 6 sheets. Authoritative (green tabs): *Plan A - single upload*, *Diagnostics detail* (20 diagnostic rows, of which the **9 `MVP Scope = Core` rows are product scope** — D-17 — plus footnotes), *Detailed_DQ_Framework* (6 L1 themes × 11 L2 areas). Non-authoritative for this revision (red tabs): *Framework coverage map*, *Decisions & rationale*. Out of scope: *Plan B - multi upload*. Columns back-filled `#F79646` (source bindings, IRB examples, reviewer comments) are ignored per the walkthrough | **The new DQ framework.** The 9-diagnostic register, thresholds/decision types, staging, and the cross-cutting build rules |
| **S9** | `source-codes/synthetic-kb/KB_cross_field_reference_2.pdf` (relocated from `dev-requirements/`) | 4 pages, tabular, 49 rules | The worked example of an uploaded knowledge document: rule IDs, severity, type, entity, semantic roles, rule text, regulatory references, encoded exceptions, verdict logic |
| **S10** | `dev-requirements/cross_field_engine_V2_workflow_1.png` — **received 30 Jul 2026 (evening)** | The cross-field flow chart: inputs (dataset required; `dictionary.json` and `mapping.json` optional) → discover/load → use-case selection (IRB 38 / IFRS9 13, shared rules in both) → load role-based KB → deterministic role→column resolution (graded score ladder + dtype gate) → **optional LLM column verification, off by default, mapping-only** → entity→table binding by vote → per-rule evaluation (IF-condition scope → drop censored/N-A → exceptions → violations → lift pattern) → verdict gate (N-A / PASS / VIOLATION) → deterministic report + auditable `resolution_map.json`. Reconciled against [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) §3 — see C-32. Charts for the **other 8 core diagnostics** remain outstanding (FWK-18) |

### 0.3 Baseline — what 0.3.0 ships, and what survives

The RCA workflow is largely built. The framework and KB are not what the new inputs describe.

| Ships today | Evidence | Disposition in 0.4.0 |
|---|---|---|
| 27-state RCA machine, transition guard, audit events | `rca.py:103-182` | **Keep** |
| Triage two-signal grouping, attached failures, reconciliation | `rca.py:203-311`, `1337` | Keep; tighten grouping (C-12) |
| Intake case file + per-family checklist | `rca.py:298-309` | Keep; add closed-findings context, suppression |
| Opening look, budgeted Planner/Runner/Reader loop, board cap 8, revival, pairwise combinations | `rca.py:376-702` | Keep; make plans per family (C-07) |
| Coverage challenge pass 1 + 2 + mandatory kill attempt | `rca.py:708-841` | Keep |
| Composer with earned Strong/Moderate/Weak tiers | `rca.py:847-943` | Keep |
| Check runner, Judge, symptom accounting, blame-back, second chance | `rca.py:944-1173` | Keep |
| Fix advisor, approval, time-box escalation, closure re-run, reconciliation | `rca.py:1176-1411` | Keep |
| Governed KB: documents → versions → sections → rules, lifecycle, shelf life, schema invalidation, blame-back, eligibility retrieval | `kb.py`, `routers/v3.py:116-233` | **Keep the governance; replace the parser and add dimension tagging** (C-34, C-35) |
| Taxonomy with immutable tag propagation | `taxonomy.py`, `routers/v3.py:37-92` | Keep; extend to knowledge documents |
| 14-test registry, 11-area framework, weights | `dq_tests/registry.py`, `knowledge_base/dq_framework_data.json` | **Replaced** (§1) |
| `knowledge_base/business_rules.json` loaded at runtime | `ai/v2/service.py:38` | **Deleted** — hardcoded domain rules (C-36) |
| Per-step RCA UI, one button per agent | `RcaCase.jsx:246-312` | **Replaced** by autonomous run (C-06) |
| Single bootstrap tenant, no quota, no dataset deletion | `tenancy.py:12`, nothing else | **Replaced** by workspaces (§7) |
| RCA behavioural tests | `tests/test_rca.py` (1,107 lines) | Keep; adapt where behaviour deliberately changes |

**Old code needs no preservation.** The Galileo version is retained separately, so deleted tests,
retired framework records and superseded results require no compatibility layer, no dual-read and no
remap.

### 0.4 Version rationale

Released is **`0.3.0`** (`dev` @ `c0e8e06`). Pre-1.0, `VERSIONING.md` says use `0.MINOR.0` for a
meaningful feature milestone, so this release is **`0.4.0`** — the next minor. It is nonetheless
breaking (the framework is replaced, old test records are dropped, the Test Lab workflow changes, RCA
moves from per-step to autonomous), and the release note must say so. Revisions 2–3 of this document
targeted `0.5.0` by mistake; corrected per requester instruction, 30 Jul. 1.0.0 stays reserved for
post-deployment acceptance.

### 0.5 ID scheme

| Prefix | Block |
|---|---|
| `FWK-nn` | The new DQ framework and diagnostic register (§1) |
| `DET-nn` | Determinism policy (§2) |
| `KB-nn` | Knowledge base: upload, parse, tag, playback, binding (§3) |
| `CFR-nn` | Cross-Field Business Rule Engine (§4) |
| `DIA-nn` | The other core diagnostics (§5) |
| `APL-nn` | AI Proposal Loop (§6) |
| `RCA-nn` | RCA workflow corrections (§7) |
| `WSP-nn` | Workspaces, quotas, deletion authority (§8) |
| `ING-nn` | Data ingestion / sourcing (§8b) |
| `PLT-nn` | Platform and cross-cutting (§9) |
| `C-nn` | Conflict (§10) |
| `D-nn` | Decision record (§11) |
| `OOS-nn` | Out of scope (§12) |

**MUST** = in 0.4.0. **SHOULD** = in scope if it does not endanger a MUST. **LATER** = recorded,
deferred with a reason.

---

## 1. The new DQ framework (FWK)

Source: S8. This replaces the shipped framework outright.

### 1.1 Structure

| ID | Priority | Requirement |
|---|---|---|
| FWK-01 | MUST | The framework is **6 L1 themes → 11 L2 assessment areas → 6 test areas → 9 core diagnostics**, per S8's *Detailed_DQ_Framework*, *Plan A* and the `MVP Scope = Core` rows of *Diagnostics detail* (D-17). S8 documents 20 diagnostic rows; **only the 9 core are product scope** — the 11 `Defer (phase-2)` rows stay in the source sheet as documentation and appear nowhere in the product. Themes: Sample & Representativeness (3 areas); Target & Outcome Integrity (1); Feature & Predictor Quality (4); Dataset Stability & Monitoring (1); Upstream Dataset & Pipeline Integrity (1); **Third party data quality and controls (1)** — revision 2 recorded this theme as retired; the re-issued sheet keeps it. It stays in the taxonomy, with no diagnostic reaching it: an honest area-level coverage gap, not a deletion. |
| FWK-02 | MUST | **"Test" and "diagnostic" are distinct levels.** A test area contains one or more diagnostics. The product's user-facing unit of execution is the **diagnostic**; the test area is its grouping. Naming must not blur them. |
| FWK-03 | MUST | The six test areas per *Plan A - single upload*: **T1** Feature-to-target leakage · **T2** KB-driven data validation (three modes: hard / distributional / relational; establishes the value-semantics tags) · **T3** Target definition & label consistency · **T4** Portfolio / population representativeness · **T5** Outcome window / censoring completeness (incl. seasoning/maturity) · **T6** KB relationship set — SME-generated + AI-proposed, SME-validated (the AI-proposal loop). Plan A also records each area's **KB dependency**: T1 Low · T2 **HIGH** · T3 Medium · T4 Low-Med · T5 Medium · T6 reads & writes. |
| FWK-04 | MUST | The framework definition is **data, not code** — a seeded taxonomy record set, replacing `dq_framework_data.json`'s content. Note the distinction from the knowledge base: the framework is *what we test*; the KB is *domain truth we test against*. The framework may ship with the product; domain rules may not (C-37). |

### 1.2 The diagnostic register — the 9 core (product scope, D-17)

`Det.` = Deterministic or Statistical. `Decision` = verdict (deterministic pass/fail) · candidate flag /
contextual (statistical "look here", SME disambiguates, **never auto-fail**) · classification output ·
SME gate. `#` keeps S8's *Diagnostics detail* row number for traceability. `Workflow` = FWK-17 status
in this release.

| # | Area | Mode | Diagnostic | Metric / method | Threshold (default; tunable) | Det. | Decision | Stage | Workflow |
|---|---|---|---|---|---|---|---|---|---|
| 2 | T1 | statistical | Single-feature target separation | univariate AUC; IV (alt: mutual information) | AUC > 0.90 or IV > 0.5 → "too good to be true"; AUC < 0.60 or IV < 0.05 → "poor discrimination" | Stat | candidate flag | 2 | pending |
| 4 | T2 | hard | Cross-field business rule | violation count / rate | rate > ~0 after exceptions | Det | verdict | Both | **EXECUTABLE** |
| 6 | T2 | hard | Row-completeness reconciliation | coverage % = actual/expected by segment × period (anti-join) | materially below expected (e.g. <95% where neighbours ~100%) | Det | verdict | 1 | pending |
| 8 | T2 | hard | **Value-semantics classification** | rule-based classification vs KB | none — classification; output other diagnostics consume | Det | classification output | 1 | pending (next to enable) |
| 11 | T2 | relational | Directional / monotonic consistency (segmented) | Spearman rank corr; group-band median trend | wrong sign, or \|corr\| < floor, or inversions > allowed (per segment; min-sample guard) | Stat | contextual | Both | pending |
| 12 | T3 | hard | Label-consistency rule | violation count | rate > ~0 | Det | verdict | Both | pending |
| 14 | T4 | statistical | Population Stability Index (PSI) | PSI per feature & segment (alt: JS divergence, Wasserstein) | > 0.10 watch; > 0.25 investigate | Stat | threshold + SME | 2 | pending |
| 17 | T5 | statistical | Resolution / maturity rate by vintage | resolved count / total by vintage | resolution rate < X% for a vintage | Stat | contextual | 2 | pending |
| 20 | T6 | loop | AI-proposal workflow (a workflow, not a computation — see §6) | confidence score (ranking only) — no data metric | none — human gate | n/a | SME gate | Both | pending |

**Documented but out of product scope** (S8's 11 `Defer (phase-2)` rows, kept in the source sheet only
— D-17): #1 post-outcome field / look-ahead (T1) · #3 categorical leakage (T1, D-14) · #5 derivation
identity (T2) · #7 disguised-missing / staleness (T2) · #9 robust-Z outlier (T2) · #10 boundary
pile-up (T2) · #13 target-rate break (T3) · #15 KS distance (T4) · #16 segment coverage (T4) ·
#18 resolved-only vs all differential (T5) · #19 seasoning / maturity profile (T5). They are not
registered, not displayed and not executable; any of them enters scope only through a future framework
revision with its own decision record.

| ID | Priority | Requirement |
|---|---|---|
| FWK-05 | MUST | **The register contains exactly the 9 core diagnostics**: #2, #4, #6, #8, #11, #12, #14, #17, #20 — S8's footnote names them outright ("the MVP CORE is 9 buildable diagnostics", spanning all six areas, keeping both differentiators: leakage + the AI-proposal loop). The 11 `Defer (phase-2)` rows are **out of product scope entirely** (D-17): not registered, not displayed, not executable. A request naming one is an unknown diagnostic, not a "deferred" one. |
| FWK-06 | MUST | Thresholds are **configurable defaults held in a semantic layer**, tunable per dimension and engagement with the SME — never code constants. |
| FWK-07 | MUST | **Every statistical result is a candidate flag for SME judgement, not an auto-fail.** Only deterministic rules and structural checks are near-verdicts. The UI must make the distinction visible; a candidate flag must never render as a failure. |
| FWK-08 | MUST | **Stage 1 runs first and writes the value-semantics tags that Stage 2 and RCA consume.** This is a hard execution dependency (see FWK-10). |
| FWK-09 | MUST | Three guards scope every statistical screen, in order: **(1) class-based eligibility** (a metric never runs on an incompatible column class); **(2) the material-fields filter** — reinstated by D-18, superseding D-05: a field is material when it **maps to a KB semantic role**, or sits in the **declared feature/target set**, or is **dictionary-flagged for the use case**; immaterial otherwise (identifiers, free text, audit/system metadata). Materiality is derived from KB + dictionary + profiling at runtime — never a hardcoded list — and the exclusion of immaterial fields is reported, not silent; **(3) the value-semantics gate** (FWK-11). Deterministic hard rules are not filtered by materiality — a KB rule runs wherever its roles resolve. |
| FWK-17 | MUST | **Registered vs executable are distinct register states.** Every registered diagnostic carries `workflow_status ∈ {executable, workflow-pending}`. In the **first build slice, exactly one diagnostic is executable: #4 Cross-field business rule** — the only one whose workflow exists. The other 8 display as **workflow-pending**: visible in the register and coverage board, honestly labelled, refusing execution with "workflow not yet defined" — never rendered as broken, failed or missing. (Area-level gaps are a coverage-map concern, FWK-14 — not a diagnostic state.) |
| FWK-18 | MUST | **A diagnostic becomes executable only when its workflow is provided and implemented** — the pattern set by cross-field (S5/S6/S10 + register row + the Test Lab workflow in [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md)). Flipping `workflow_status` to `executable` is a data + code change with a recorded decision, one diagnostic at a time, **and only after the requester has signed off the previous diagnostic's flow** (D-22). No blanket enablement. |

### 1.3 Staged execution and the value-semantics gate

| ID | Priority | Requirement |
|---|---|---|
| FWK-10 | MUST | Execution is **staged with a dependency contract**, not a flat sequential run. A Stage-2 diagnostic whose target columns have no value-semantics tags **refuses to run** and reports the missing dependency. It must never run unguarded. |
| FWK-11 | MUST | Statistical screens are **gated on value-semantics**: never computed over values tagged censored, sentinel or N-A. Scope exclusion is reported, not silent. |
| FWK-12 | MUST | The tags are a **first-class record** consumed by other diagnostics, the report and RCA — not a transient intermediate. |

**Worked example (the reason this is a MUST).** `recovery_workout.realised_lgd`, governed by KB rule
IRB-03: *"IF workout closed/written_off THEN realised_lgd populated (open = censored)"*, with S9's note
*"'open' workouts are censored and out of scope by construction."*

Diagnostic #8 tags `workout_status = open` → **censored**; `closed` with a null → **missing** (a real
defect). Without the tags: row-completeness reports ~25% missing on recent vintages and raises a false
defect; IRB-35's bound `realised_lgd in [-0.10, 1.50]` returns violations that are not violations,
contradicting S9's own verdict rule that scope excludes censored participants; robust-Z computes
median/MAD across a mixed population, or treats a sentinel as a value and masks the genuine 2011 tail
clip; diagnostic #17 has nothing to count, because censoring *is* what it measures; and RCA opens a
case that sends the Planner hunting a pipeline fault for something correct by construction — worst case
closing **Confirmed — data defect** against an innocent data owner.

**Slice-1 note (FWK-17).** Until #8 ships, the only executable diagnostic — #4 cross-field — handles
censoring through each rule's **encoded exceptions and verdict logic**, exactly as S5 already does
(S9's own verdict rule excludes censored participants per rule). That is rule-local scope handling,
not the general gate; FWK-10/11 bite the moment the first Stage-2 statistical screen becomes
executable, and #8 must be in place before that happens.

### 1.4 Test disposition — all 14 shipped tests accounted for

| ID | Priority | Requirement |
|---|---|---|
| FWK-13 | MUST | Apply exactly this disposition. Delete means delete — the code, the registry entry, the param spec and the downstream records. Under D-17, **"DEFER → #n" now means: the shipped test is deleted, and its successor #n exists only as S8 documentation** — nothing is registered in the product for it. |

| Shipped test | Disposition | Rationale |
|---|---|---|
| PSI (Population Stability Index) | **KEEP** → #14 | Core, unchanged in intent |
| Leakage detection (single-feature AUC) | **KEEP** → #2 | Core, unchanged in intent |
| Plausibility rule check | **ABSORB** into T2 hard | Univariate plausibility becomes domain/bound rules (26 of S9's 49) |
| Date-ordering / event-sequence check | **ABSORB** into T2 hard | Becomes date-ordering rules (3 of 49) |
| Completeness / missing-rate profile | **REPLACE** → #6 | Different computation: expected-vs-actual anti-join by segment × period, not a per-column missing rate. Missing-rate survives as **profiling input**, not a diagnostic |
| Post-outcome field inventory | **DEFER** → #1 | Phase-2 |
| Maturity profile analysis | **DEFER** → #19 | Phase-2 |
| MCAR test (Little's) | **DEFER** | S8 defers MCAR/MNAR explicitly: honest output is a caveated escalation, and it must run only on genuinely-missing fields *after* value-semantics tagging or it mislabels censoring as MNAR |
| Missingness-vs-target (MNAR) test | **DEFER** | Same |
| Robust z-score outlier test (MAD) | **DEFER** → #9 | Phase-2 within T2 |
| Key uniqueness check | **DELETE as a diagnostic, RETAIN as a profiling precondition** | No home in the new framework, but #6's anti-join needs a unique grain key. Deleting it outright removes a prerequisite for a core diagnostic (D-04) |
| Tail analysis | **DELETE** | Nearest is #10 boundary pile-up (deferred); different computation |
| Correlation stability analysis | **DELETE** | Nearest is #11 (segmented sign check — different) and Feature Drift (multi-upload only) |
| Drift decomposition | **DELETE** | Nearest is Plan B's delivery-to-delivery drift; multi-upload only |

Net: **keep 2, absorb 3, replace 1, defer 5, delete 3 + 1 demoted.** Eventually build 7 new core
diagnostics (9 core − PSI − leakage AUC); in the first slice, exactly one of them — cross-field —
becomes executable (FWK-17).

### 1.5 Coverage honesty

| ID | Priority | Requirement |
|---|---|---|
| FWK-14 | MUST | Coverage honesty is **area-level**: an L2 area no registered diagnostic reaches displays as **GAP**, with its reason, never as unbuilt. Using the *Detailed_DQ_Framework* names: *Feature Stability & Behavioral Consistency* and *Dataset Drift & Degradation Monitoring* (require multi-upload — Plan B, out of scope); *External / vendor data robustness & change control* (no diagnostic — third-party theme); *Downturn & Regime Coverage* and *Missingness Mechanism & Data Availability* (their candidate diagnostics are S8 defer rows / OOS-05 / OOS-06 — out of product scope, D-17). |
| FWK-15 | MUST | The coverage map distinguishes **Covered · Covered-thin · Partial · Gap**, with "Covered-thin" meaning the area's headline catch is in the 9-core register while richer S8-documented diagnostics stay out of scope. Do not round these up. Within the register the board additionally distinguishes **executable vs workflow-pending** (FWK-17). |
| FWK-16 | MUST | Scoring weights are re-derived for the new register and recorded. A score movement is a reviewed decision, never a surprise. A score computed while most core diagnostics are workflow-pending must say what it covers — a one-diagnostic score presented as a health score is a lie of omission. |

---

## 2. Determinism policy (DET)

| ID | Priority | Requirement |
|---|---|---|
| DET-01 | MUST | **Workflows move by Python.** Orchestration, state machines, gates, budgets, staging, scoring, verdicts and every statistical computation are deterministic code. |
| DET-02 | MUST | The LLM is confined to exactly **four seams**, each with a schema-validated structured output and a deterministic fake provider used by default in tests: <br>**(a) KB semantics → field/table mapping** — role resolution and its verification. <br>**(b) Report summary** — statistics-driven, LLM writes commentary only; it may not compute, alter or contradict a number. <br>**(c) RCA incremental tests** — the Planner's next-look proposal, and the Coverage challenge. <br>**(d) AI-proposal loop candidate drafting** — T6, which is generative by design. |
| DET-03 | MUST | Anything not named in DET-02 is deterministic. Adding a fifth seam is a design change requiring a recorded decision, not an implementation choice. |
| DET-04 | MUST | Statistical tests — PSI, KS, AUC/Gini, IV, Cramér's V, Spearman, robust-Z, change-point — are codified deterministically. No LLM is involved in producing a metric. |
| DET-05 | MUST | No model-supplied SQL, Python, imports, file paths, subprocesses, environment access or network access, anywhere. Bounded read-only SQL and allowlisted analytical helpers only. |
| DET-06 | MUST | Seam (b) must be verifiable: every number in a generated summary traces to a computed result, and a test asserts the summary contains no number absent from the structured result. |

---

## 3. Knowledge base (KB)

Source: S9, plus the standing instruction that no KB ships in code.

### 3.1 Upload, tag, parse, play back

| ID | Priority | Requirement |
|---|---|---|
| KB-01 | MUST | **No domain rules in code.** `build_generic_kb()`'s 49 rules do not ship, not even as an install seed. `knowledge_base/business_rules.json` is deleted along with its loader at `ai/v2/service.py:38`. The `synthetic-kb/` package stays test-only and never loads at startup. |
| KB-02 | MUST | The user uploads a knowledge document (TXT, Markdown, DOCX, text-based PDF) and **tags it with taxonomy dimensions** — risk type, portfolio, product, use case. Multi-select. Tagging knowledge documents does not exist today (C-35). |
| KB-03 | MUST | The product **parses the document and plays back a short, nicely formatted summary** for confirmation: what kind of knowledge it found, how many rules, the breakdown by framework / type / severity, the semantic roles referenced, and anything it could not parse. For S9 the summary must reach: 49 rules · IFRS9 11 / IRB 36 / both 2 · conditional 13, date-ordering 3, domain 26, identity 3, inequality 4 · CRITICAL 6, MATERIAL 21, MINOR 22. |
| KB-04 | MUST | **The parser must be table-aware.** Today `_split_sections` splits on `## ` H2 headings only and creates one rule per section — so a table-shaped PDF like S9 yields **exactly one** rule containing all four pages. That is the current defect (C-34); a document of N tabulated rules must produce N rule records. |
| KB-05 | MUST | Preserve provenance as today: original bytes stored content-addressed by SHA-256, converted Markdown immutable with its own hash, converter name and version, warnings, review and publication record. |
| KB-06 | MUST | Reject unsupported, corrupt and scanned/image-only documents safely and with a clear message. OCR stays out of scope. |

### 3.2 Rule records and executable binding

| ID | Priority | Requirement |
|---|---|---|
| KB-07 | MUST | A parsed rule record carries: rule id, severity, type, entity, semantic roles, rule text, regulatory reference, encoded exceptions and notes, plus source page/line for traceability. |
| KB-08 | MUST | **Parsing does not make a rule executable.** Each parsed rule is **bound** to a verified implementation from the registered primitive set. Binding status is one of: **bound** (executable), **reference-only** (parsed and displayable, no implementation), **unparsed** (with the reason and the offending source line). |
| KB-09 | MUST | **Never execute a predicate recovered from prose.** These are regulatory rules; a mis-parsed one is worse than a missing one. A rule reaches execution only through a binding that a human confirmed or that matches a known primitive signature exactly. |
| KB-10 | MUST | The **parse report is a first-class artifact**, shown to the uploader: per-rule status with reasons, so they can revise the document and re-upload. This is the intended correction loop (D-06). |
| KB-11 | MUST | Known extraction hazards must be handled and surfaced, not silently guessed: PDF text extraction collapses `exposure_at_default` to `exposureatdefault` and wraps rule expressions across lines. Recovered role names are shown back for confirmation. |
| KB-12 | MUST | Only **published and effective** rules enter production retrieval. Draft knowledge never reaches a model context. |

### 3.3 Governance — keep what 0.3.0 built

| ID | Priority | Requirement |
|---|---|---|
| KB-13 | MUST | Retain trust levels (`human_confirmed` / `inferred`), lifecycle (draft → pending_review → published → expired / superseded / under_suspicion / archived), shelf life (12 months default, tunable per category; immediate demotion on a related schema change), blame-back, supersession, version history, publication approval, rule-level citations, retrieval manifests. |
| KB-14 | MUST | **Only deterministic code writes knowledge**, at two moments: when a human answers a question mid-case, and when a case closes. No LLM creates, modifies, publishes or archives knowledge. |
| KB-15 | MUST | **Two axes, kept apart** (C-27): the **user-facing axis is the taxonomy dimensions** — the only thing shown at upload. The **internal axis is fact kind** (`structure`, `lineage`, `domain_fact`, `ownership`, `case_history`), system-derived and never surfaced in the upload UX. Fact kind exists because it *is* the agent knowledge-access matrix at `kb.py:32-42` — the server-side enforcement of the Planner/Reader history quarantine — plus per-category shelf life. It is plumbing, not classification the user should perform. |
| KB-16 | MUST | Retrieval order is fixed: authorize principal and workspace → filter published/effective/non-archived → match tags and explicit table/column relationships → apply the agent's allowed fact kinds → enforce history quarantine → apply trust, expiry and suspicion → optionally rank → persist the retrieval manifest with an eligibility reason per rule. Tags connect records; they never prove relevance or causality. |

---

## 4. Cross-Field Business Rule Engine (CFR)

Source: S5, S6, S9. The engine works; the requirement is integration without behavioural loss — with
its rules now coming exclusively from the uploaded KB.

| ID | Priority | Requirement |
|---|---|---|
| CFR-01 | MUST | A first-class diagnostic (#4), invocable through the API against an uploaded dataset. No CLI, no stdin, no local file paths on the product path. |
| CFR-02 | MUST | Preserve the rule model exactly: five types `inequality`, `date_ordering`, `identity`, `conditional`, `domain`; three severities `CRITICAL`, `MATERIAL`, `MINOR` (`cross_field_rule_engine.py:264-286`). |
| CFR-03 | MUST | **Rules come only from the published KB.** `build_generic_kb()` does not ship (KB-01). Framework filtering by use case (`irb` \| `ifrs9` \| `both`) is retained but applied to KB-sourced rules. |
| CFR-04 | MUST | **No fallback rule set.** With zero eligible published rules in scope, the run returns **NOT-APPLICABLE** with "no knowledge base published for scope <x>" — never PASS. This replaces revision 1's frozen-fallback proposal, which KB-01 forbids. |
| CFR-05 | MUST | Preserve role resolution exactly as S10 charts it and `cross_field_rule_engine.py:690-757` implements it: **override wins; otherwise the graded auto-match ladder** — exact canonical name 1.0 · synonym match 0.92 · dictionary-description phrase 0.88 · synonym token-subset 0.80 · fuzzy character similarity 0.55–0.75 — **with a dtype gate**. Each resolved role records its score and reason (the auditable `resolution_map.json` becomes manifest provenance). Report the resolved count and the auto/override split, as in S6 (`Roles resolved: 36/36 (0 override, 36 auto)`). The **optional LLM verification of these mappings** is seam (a) — off by default (D-08), mapping-only, never a verdict. |
| CFR-06 | MUST | Expose the nine primitives — `ineq`, `dateorder`, `identity`, `dom_range`, `dom_set`, `eq_cond`, `in_cond`, `presence`, `present_number` — plus `evaluate_rule()`, registered in `ai/test_kit.py`'s governed catalogue. These are what KB-08 binds to and what APL-20 compiles to. |
| CFR-07 | MUST | Preserve evaluation semantics per rule: rows evaluated; rows skipped as censored / N-A **per value-semantics** (FWK-11, and S9's own verdict rule); violation count; rate vs tolerance; exceptions applied **with their stated reason and count** — S9: *"silent exceptions are how rules rot"*; up to 5 evidence rows keyed by grain. |
| CFR-08 | MUST | Preserve the verdict gate: empty scope → NOT-APPLICABLE; rate ≤ tolerance → PASS; rate > tolerance → VIOLATION. Severity orders the report and labels the finding; **it never changes pass/fail maths**. |
| CFR-09 | MUST | Preserve the pattern read on each violation: lift-based **CLUSTERED vs SCATTERED** (clustered ⇒ feed/segment fault; scattered ⇒ capture error), with `grain_role` (default `facility_id`), `segment_role` (default `region`), `cluster_lift` 3.0 and `cluster_coverage` 0.5 as configurable parameters. |
| CFR-10 | MUST | Reproduce S6's report as **structured data first** — preamble, roll-up of PASS / VIOLATION / NOT-APPLICABLE, violations ordered by severity — with text and PDF rendering derived from it. |
| CFR-11 | MUST | Role verification routes through `ai/llm.py` + `ai/control_plane.resolve`. `_llm_call()` and `CFR_LLM_API_KEY` are deleted. Off by default (D-08); every mapping change it causes is recorded. |
| CFR-12 | MUST | Interactive paths become gates: the use-case question and the role-mapping conflict (`_resolve_conflict_with_user`, currently a stdin read) produce **decision records**, never block a request. An unattended run applies the documented default and records that it did. |
| CFR-13 | MUST | Progress becomes SSE events in the existing `execute/stream` idiom. |
| CFR-14 | MUST | Results persist through the standard result/evidence records so the diagnostic appears in results, score, issues and the report like any other — and so a cross-field failure can open an RCA case. |
| CFR-15 | MUST | Read-only against the dataset; bounded output (5 evidence rows per rule); no unbounded row dumps into responses or logs. |
| CFR-16 | MUST | Deterministic given (data, rule set, parameters). Two runs on one snapshot produce identical structured results. Role verification may change mappings, never verdicts. |
| CFR-17 | MUST | Workspace-scoped: rule sets and results never readable across workspaces. |
| CFR-18 | MUST | No new runtime dependency. S7's two hard requirements are already met by `backend/requirements.txt`; `anthropic>=0.40` is rejected (C-04). |

---

## 5. The other core diagnostics (DIA)

**Slicing (FWK-17/18).** Every diagnostic below stays a 0.4.0 requirement, but none is executable in
the first build slice — each is **registered as workflow-pending** and becomes executable only when
its workflow definition arrives, one at a time, following the cross-field pattern. Priorities below
read against the full release, not the slice.

| ID | Priority | Requirement |
|---|---|---|
| DIA-01 | MUST | **#8 Value-semantics classification** (T2 hard, Stage 1). Rule-based classification against the KB, tagging each field/null as `valid` / `missing` / `censored` / `stale` / `N-A`. No cutoff. Output is a record other diagnostics consume (FWK-12). Build this **before** any Stage-2 statistical screen becomes executable — it is the natural second workflow to define. |
| DIA-02 | MUST | **#6 Row-completeness reconciliation** (T2 hard, Stage 1). Coverage % = actual/expected by segment × period, via anti-join. Requires a unique grain key (D-04). Reference case: the Midlands H2-2014 snapshot gap. |
| DIA-03 | MUST | **#12 Label-consistency rule** (T3 hard). Violation count on label fields' internal consistency by process. Reference case: `delinquency_stage` vs `default_events`. |
| DIA-04 | MUST | **#11 Segmented directional / monotonic consistency** (T2 relational). Spearman rank correlation and group-band median trend, per segment, with a minimum-sample guard. Flags a wrong sign, a correlation below floor, or inversions above the allowance. Reference case: BTL indexed-LTV → LGD inversion −0.33 vs residential +0.44. |
| DIA-05 | MUST | **#2 Single-feature target separation** (T1). Univariate AUC and Information Value. Reference case: the planted `ever_forborne` look-ahead — P(default \| ever_forborne=1) = 0.028 vs 0.001. |
| DIA-06 | — | **Out of product scope** (D-14, then D-17): #3 Categorical leakage is neither core nor registered. The requirement text is preserved here for its possible return: per-category outcome rate vs base rate, Cramér's V, IV; **not** chi-square — effect size only; **volume-gated** on a minimum category count. |
| DIA-07 | MUST | **#14 PSI** (T4). Per feature and segment, thresholds 0.10 watch / 0.25 investigate, tunable. Retained from the shipped implementation; re-verify against `spike_recalibrate.py`. |
| DIA-08 | MUST | **#17 Resolution / maturity rate by vintage** (T5). Resolved count / total by vintage. Depends on DIA-01's censored tag. Reference case: recent vintages 75–90% resolved vs 100% pre-2018, resolved-only LGD 0.14 biased low. |
| DIA-09 | MUST | Every core diagnostic declares its **decision type** (FWK-07) and its **stage** (FWK-08), and honours the value-semantics gate (FWK-10/11). |
| DIA-10 | MUST | The **report summary** is statistics-driven with LLM commentary — seam (b), constrained by DET-06. |

---

## 6. AI Proposal Loop (APL)

Source: S3, S4, and S8's *Diagnostics detail* Test-6 build note. Entirely new — no `context_pack`,
`adapter`, `candidate` or `uncovered` code exists.

### 6.1 Purpose and boundary

The core diagnostics test only what they were configured to test. In S6's sample run 36 columns
resolved to roles; **everything else was never examined.** The loop closes that gap.

| ID | Priority | Requirement |
|---|---|---|
| APL-01 | MUST | A **separate second pass**, deliberately invoked after a completed diagnostic run. No recurring cycle; no "the engine picks it up next run". |
| APL-02 | MUST | **Not a new test engine.** S8 is explicit: there is no test-6 executor. Accepted candidates **route into the T2 mode they belong to** — identity/rule → hard, bound → distributional, relationship → relational — inheriting that mode's metric and threshold. Zero new executors. |
| APL-03 | MUST | Four build steps per S8: **PROPOSE** (profile, read KB to avoid duplicates, output ranked candidates with plain-English rationale and provenance-typed confidence) → **VALIDATE** (SME accepts / rejects / modifies; nothing runs on data until accepted; log rejections with reason) → **PERSIST** (write accepted candidates to the KB tagged by type: identity \| cross-field rule \| directional/relationship \| plausibility bound, with `origin = AI-proposed, SME-validated`) → **RUN** (execute through the matching T2 mode). |

### 6.2 Architecture

| ID | Priority | Requirement |
|---|---|---|
| APL-04 | MUST | **One pipeline, not eight.** All suggestion logic runs through a single shared path. No per-diagnostic branching in pipeline code. *(With 9 core diagnostics minus the loop itself, the adapter count is back to S3's original eight — D-14.)* |
| APL-05 | MUST | **The adapter is the only per-diagnostic code.** One thin adapter per non-loop core diagnostic; its single job is to read its engine's output and emit the standard context pack. |
| APL-06 | MUST | **Per-diagnostic variation lives in data, not code.** The KB is namespaced per diagnostic; prompt framing is a template pulled from that namespace. An `if diagnostic_id == …` branch in the pipeline is a defect. |
| APL-07 | MUST | **Nothing runs on data before the SME gate.** |
| APL-08 | MUST | *Architecture acceptance:* adding one more diagnostic requires one adapter and one KB namespace, and **no change inside the pipeline**. If it forces a pipeline change, the context pack is under-specified and must be fixed, not worked around. |

### 6.3 The context pack

| ID | Priority | Requirement |
|---|---|---|
| APL-09 | MUST | Fields: `diagnostic_id`; `columns` (name, dtype, sample values, data-dictionary description); `covered` (what this run touched — the resolved role→column map for cross-field, the feature/segment list for others); `uncovered` (present but untouched — the primary mining ground); `kb_slice` (existing KB entries for this namespace with plain-English descriptions, so nothing is re-proposed); `results_summary`; `primitives`. |
| APL-10 | MUST | The pack never asks an engine for "your KB rules" — some diagnostics have no rule library. It asks only what every engine can answer: *what did you touch, and what was available but untouched?* |
| APL-11 | MUST | Validate the pack **by hand** against the two least-similar engines — cross-field (roles) and PSI (features/periods) — **before any pipeline code is written.** S3 flags this as a known risk: the engines describe coverage differently. |

### 6.4 The candidate

| ID | Priority | Requirement |
|---|---|---|
| APL-12 | MUST | Fields: `candidate_id`, `diagnostic_id`, `attempt` (1–3), `target_columns`, `scope`, `logic`, `thresholds`, `rationale`, `confidence`, `executable`, `provenance`. |
| APL-13 | MUST | `scope`, `logic`, `thresholds` are **discrete structured fields, never prose** — diffing free text yields word-level churn; diffing fields yields the clean line-level diff the gate screen needs. |
| APL-14 | MUST | `confidence` controls **display order only** — provenance-typed, not a threshold, never auto-accepting. |
| APL-15 | MUST | Every candidate carries `provenance` back to the specific gap in `uncovered` that produced it. |

### 6.5 Pipeline stages

| ID | Priority | Requirement |
|---|---|---|
| APL-16 | MUST | **Stage 1 — profile and find gaps.** Compare `covered` against `columns`; produce the untouched set plus profiling (type, cardinality, null rate, apparent semantics); cross-reference `kb_slice` to exclude known territory. |
| APL-17 | MUST | **Stage 2 — propose candidates.** LLM drafts typed candidates against the gap list, ranked by confidence — seam (d). |
| APL-18 | MUST | **Stage 3 — SME gate.** No data touched. The SME decides on rationale alone: accept → Stage 4; reject → revision lane; **edit** → a third action (D-01). |
| APL-19 | MUST | **Revision lane.** Cap **3 attempts per candidate**, counted per candidate (A may be on attempt 3 while B is on attempt 1). After 3, the candidate is **parked** — retained with all three rejection reasons. |
| APL-20 | MUST | **The rejection reason is mandatory, enforced server-side.** It is the only input to the revision; a blank reason gives the model nothing to change and burns an attempt for free. |
| APL-21 | MUST | An **SME edit does not consume an attempt** (D-02) and is recorded `authored_by: sme`. |
| APL-22 | MUST | **Stage 4 — compile to primitives.** Bind to a real callable from the engine's `primitives` list. No primitive fits → mark **non-executable**, still show it to the SME, and **park it as a build request** with provenance (D-03), not discarded — it is the backlog of primitives the platform lacks. |
| APL-23 | MUST | **Stage 5 — execute and produce evidence.** Hit rate plus sample offending rows. **Read-only.** |
| APL-24 | MUST | **Stage 6 — integrate into KB?** A separate explicit ask, **after** the SME has seen results. Yes → a governed draft object in that diagnostic's KB namespace, typed per APL-03 (D-10). No → a one-off look; nothing persists. |

### 6.6 The SME gate screen

| ID | Priority | Requirement |
|---|---|---|
| APL-25 | MUST | Four required elements: **attempt badge** ("Attempt 2 of 3") visible before anything else; **the previous rejection reason shown back**, so the SME can see whether their objection was addressed rather than recall it; **line-level diff against the previous attempt** (removed / added / unchanged-in-grey — a cosmetic revision shows as almost entirely grey, which is the tell); **the executable flag at gate time**, so accepting something unbuildable is a visible choice, not a Stage 4 surprise. |
| APL-26 | MUST | Rationale to preserve: the SME may see the same idea three times. If revisions are shallow, that is review fatigue and they start rubber-stamping. The badge and the diff make a lazy revision visible at a glance. |

### 6.7 Invariants (do not optimise away)

| ID | Priority | Requirement |
|---|---|---|
| APL-27 | MUST | Nothing executes on data before the SME gate. |
| APL-28 | MUST | Confidence ranks; humans decide. |
| APL-29 | MUST | Execution is read-only. |
| APL-30 | MUST | **Two distinct decisions:** *accept* (Stage 3) then *persist* (Stage 6). Declining at Stage 3 means the check never ran. Declining at Stage 6 means it ran and you saw the answer, but it does not become a permanent rule. |
| APL-31 | MUST | Every candidate carries provenance to its gap. |
| APL-32 | MUST | Parked candidates and their rejection reasons are retained — that log shows what the model keeps getting wrong about this dataset. |

### 6.8 Recorded deviations from S8's Diagnostics detail tab

Intentional; documented so a later reader does not "fix" them back.

| ID | Sheet says | 0.4.0 does | Why |
|---|---|---|---|
| APL-33 | Persist to KB, then run | Run first; persist as a separate optional decision | The SME should see results before committing anything permanent |
| APL-34 | Engine picks rules up on a subsequent run | No next run — a separate second pass, executed directly | There is no recurring cycle |
| APL-35 | No revision path | Rejected candidates revised, capped at 3 attempts | Turns a rejection into signal instead of a dead end |

Not a deviation: nothing runs on data until accepted. That holds — the gate sits before execution
rather than before persistence.

### 6.9 Primitive inventory

| ID | Priority | Requirement |
|---|---|---|
| APL-36 | MUST | Cross-field's nine primitives are confirmed (CFR-06). Each other engine's `primitives` list **must be established before its adapter is written.** |
| APL-37 | MUST | Reuse `ai/test_kit.py`'s governed, versioned catalogue as the registry. Do not create a parallel one. |
| APL-38 | MUST | Cross-field's role-mapping verification stays a **separate concern** from this loop. Do not merge them. |
| APL-39 | MUST | 0.4.0 ships **two adapters** — cross-field and PSI, the two S3 demands be validated first. (The PSI *adapter* can only run once PSI is executable — a later slice; the pack is still hand-validated against PSI's shape in Phase 1 per APL-11.) The other six are added one adapter at a time, which APL-08 must make trivial. |

---

## 7. RCA workflow — corrections and completions (RCA)

Source: S1, S2. S2 corrects the build now on `dev`. Each row states its verified 0.3.0 status.

### 7.1 Vocabulary and per-issue independence

| ID | Priority | Requirement | 0.3.0 |
|---|---|---|---|
| RCA-01 | MUST | Agent names and order: Triage → Intake → Opening looks → Planner → Runner → Reader → Coverage challenge → Composer → Check runner → Judge → Fix advisor → Closure. No parallel stage model. | Satisfied |
| RCA-02 | MUST | **One issue = one full run.** An issue is one failing diagnostic on one target (one diagnostic, one table, one column-set, one time window). N failures → Triage groups into M representatives → the workflow runs M times, independently. | Satisfied |
| RCA-03 | MUST | Never one plan, one suspect board, or one findings document spanning multiple failures. Each issue gets its own case file, board, budget, trail, findings and closure. | Satisfied |
| RCA-04 | MUST | The only cross-issue sharing is Triage's grouping plus a **read-only summary of already-closed findings** into a later issue's Intake — a reference, never a merge. | **Not implemented** |

### 7.2 Run the full loop

| ID | Priority | Requirement | 0.3.0 |
|---|---|---|---|
| RCA-05 | MUST | Composer output is not the end. Never present a hypothesis as a finding unless its confirm check has been run and judged. | Satisfied |
| RCA-06 | MUST | Stop conditions, any one sufficient: **converged** (≤3 suspects, last two looks ruled out nothing new); **battle-tested** (every remaining suspect survived a kill-attempt look); **budget spent** (~10 looks, forced, weak hypotheses flagged); **dead end** (Planner says no useful check exists **and** the Reader agrees, **two turns running**). | Partial — verify the two-turn and Reader-agreement conditions |
| RCA-07 | MUST | **Guardrail:** no closure while the failure's size is unexplained — at least one two-factor interaction look first. | Partial — verify the pre-dead-end requirement |

### 7.3 Plans must be per diagnostic family  ⚠ VIOLATED

| ID | Priority | Requirement | 0.3.0 |
|---|---|---|---|
| RCA-08 | MUST | Each issue's plan is built from its **diagnostic family** and names: **relevant logs** (which pipeline/source logs for this table and window); **data checks** (the concrete read-only computations this family needs); **dependency checks** (lineage — which source feeds this table, which columns this column derives from); **execution steps** (the ordered looks); **validation criteria** (per look, the fork stated in advance — what result kills a suspect, what supports one). | **Violated** — no plan artifact exists |
| RCA-09 | MUST | Two different families must yield **visibly different** plans. Reject any generic plan reused across families. | **Violated** — see C-07 |
| RCA-10 | MUST | Families for the new framework: `leakage`, `cross_field_hard`, `distributional`, `relational`, `label_consistency`, `representativeness`, `censoring`, plus an explicit `generic` fallback that **flags the case** as family-unrecognised rather than silently pretending. | New |

### 7.4 The five human gates

| ID | Priority | Gate | Trigger | Human decides | 0.3.0 |
|---|---|---|---|---|---|
| RCA-11 | MUST | **Knowledge gate** | Planner needs a domain fact absent from case file and KB | Supplies it; loop pauses, **budget clock stops**, answer saved permanently | **Declared, never reachable** — `awaiting_human_answer` in `ALLOWED_NEXT` (`rca.py:108-109`) with no function, endpoint, UI or test. See C-08 |
| RCA-12 | SHOULD | **Hypothesis selection** (optional) | Before verification | Which hypotheses first; otherwise tiers decide | Not implemented |
| RCA-13 | MUST | **Conclusion approval** | Before closing | Approves / rejects the concluded root cause | Not implemented |
| RCA-14 | MUST | **Remediation approval** | Before any fix | Approves — fixes are never auto-applied | Satisfied (`rca.py:1197-1283`) |
| RCA-15 | MUST | **Escalation** | Re-entry exhausted or persistent inconclusive | Takes over / redirects | Partial — states exist, no hand-off record |

### 7.5 Autonomy  ⚠ VIOLATED

| ID | Priority | Requirement | 0.3.0 |
|---|---|---|---|
| RCA-16 | MUST | Run **every non-decision activity automatically.** Never prompt to run a look, compute a metric or summarize. Prompt only at the five gates. | **Violated** — a button per agent step (`RcaCase.jsx:246-312`), each with its own POST (`routers/v3.py:273-433`). Every look costs two clicks — exactly the *"shall I run the next look?"* pattern S2 names |
| RCA-17 | MUST | Pause only when the next action **changes the real world** (a fix), **assigns accountability** (a concluded cause), or **needs a fact the system cannot derive**. | Violated (same cause) |
| RCA-18 | MUST | Conclusion approval **may be waived by policy**; remediation approval **never**. | Not implemented |

### 7.6 End states, non-negotiables, and the rest of the design

| ID | Priority | Requirement | 0.3.0 |
|---|---|---|---|
| RCA-19 | MUST | Exactly four end states: **Confirmed** (verified by a reject-capable check, fix approved and applied, original diagnostic re-run on the frozen snapshot where possible and passing); **Genuine change** (no fix; KB gets an expected-change note so it stops re-triggering); **Test-design flaw** (plan amended, re-run under the amended test passes); **Unresolved** (one-time return exhausted; escalate with the full trail). | Satisfied |
| RCA-20 | MUST | After closure, Triage reconciles every deferred failure against the confirmed cause; mismatches open their own issues. | Satisfied |
| RCA-21 | MUST | One issue, one lifecycle, one findings artifact. Never batch. | Satisfied |
| RCA-22 | MUST | The suspect board starts **EMPTY**; a suspect appears only when a look's fork names it. No pre-seeded causes. | Satisfied |
| RCA-23 | MUST | Every fork states both outcomes in advance. Every confirm check must be able to **reject**, not only support. | Satisfied |
| RCA-24 | MUST | Planner and Reader **never see past case causes.** Only Triage, Intake and the Coverage challenge may read history. Must remain true after RCA-04 adds closed findings to **Intake only**. | Satisfied |
| RCA-25 | MUST | Ruling out is provisional — the Reader may revive with a stated reason. Max 8 active suspects; pairs only; three-way escalates to a human. | Satisfied |
| RCA-26 | MUST | Findings are validated hypotheses, never raw ones. | Satisfied |
| RCA-27 | MUST | **Complaint-triggered cases.** Threshold diagnostics auto-open on FAIL. Judgement diagnostics open only on a raised concern, recorded as the complaint — **symptom only**. A user's guessed cause becomes one suspect among others and never steers the investigation. | Partial — columns exist, always `None`; no raising path |
| RCA-28 | MUST | **Intake suppression.** A KB-recorded accepted change closes the case immediately with that note instead of re-investigating every period. | Not implemented |
| RCA-29 | MUST | **Opening looks: 2–4 fixed computations per family**, using KB structure facts to pick period and join columns. | **Violated** — one computation, family-independent |
| RCA-30 | SHOULD | **Opening-look shortcut** to the Composer, only by naming the exact proving check. | Not implemented |
| RCA-31 | MUST | Crashed, empty and "doesn't answer the question" looks cost **no budget**; one retry then back to the Planner. | Partial — enforce and test |
| RCA-32 | MUST | Triage grouping needs **both** signals: same upstream lineage **and** time window, **and** similar symptom shape (same direction, comparable size, related columns). One signal alone keeps failures separate. | Partial — see C-12 |
| RCA-33 | MUST | Coverage challenge in two passes; a history-nominated suspect enters only if this case's raw evidence is consistent with it, is tagged, gets **no confidence credit**, and must survive a kill attempt before the Composer. | Satisfied |
| RCA-34 | MUST | Part B: verification ordered by tier (Strong → Moderate → Weak), cost as tie-break; one refinement on inconclusive; **symptom accounting** — confirmed causes must together explain the observed size of the failure. | Satisfied |
| RCA-35 | MUST | Closure re-runs the original diagnostic on the **same frozen snapshot** where possible; a fresh-snapshot re-run is flagged. Time-box: a case waiting on a fix beyond its box escalates. | Satisfied |
| RCA-36 | MUST | **Determinism (DET-02c).** Only the Planner's next-look proposal and the Coverage challenge use the LLM. Reader, Judge, Composer assembly, symptom accounting, budget and Closure are deterministic. The Composer **assembles** from the board; it does not invent. | New — reverses revision 1 |
| RCA-37 | LATER | S1 §8 limitations left alone: closed findings could nudge a later case (RCA-04 makes this live — keep the warning visible); the system does not yet learn to plan better looks from closed cases. | Deferred with reason |

### 7.7 Acceptance tests (run before declaring RCA done)

| ID | Test |
|---|---|
| RCA-A1 | N failures grouped into M representatives produce exactly M independent runs and M findings artifacts — not one combined output. |
| RCA-A2 | Two different diagnostic families produce two **visibly different** investigation plans. |
| RCA-A3 | For at least one issue, the log shows the full path Intake → Closure, including a confirm check that **could have rejected** its hypothesis. |
| RCA-A4 | A hypothesis that fails its confirm check is shown rejected, not carried into findings. |
| RCA-A5 | In autonomous mode the **only** prompts are the five decision gates — no "shall I run the next look?". |
| RCA-A6 | Cases reach at least one of each of the four end states, or an unreachable one is named with a reason. |
| RCA-A7 | A deliberately wrong grouping is caught at Closure reconciliation and split into its own issue. |

---

## 8. Workspaces, quotas and deletion authority (WSP)

None of this exists: `tenancy.py` is a 26-line shim with `DEFAULT_TENANT = "bootstrap"`; there is no
quota accounting anywhere (only a 20 MB per-document cap in `kb_convert.py`); and there is **no delete
path for datasets at all**, for anyone.

| ID | Priority | Requirement |
|---|---|---|
| WSP-01 | MUST | Multiple users, each with **their own workspace**. `workspace_id` becomes the concrete `tenant_id` already threaded through `rca.py`, `kb.py` and `taxonomy.py`; the `bootstrap` tenant migrates to the first admin's workspace, preserving IDs. |
| WSP-02 | MUST | **Default allowance: 500 MB per user.** Admin can raise or lower it **for one user or for all users**. |
| WSP-03 | MUST | **Databases and datasets are accessible to every user**, and their size **consumes every user's allowance** (D-07, as specified). Consumed = (all shared datasets) + (that user's own private artifacts). |
| WSP-04 | MUST | The quota ledger is **derived from actual sizes**, recomputable from storage, never an incremented counter that can drift. |
| WSP-05 | MUST | **Over-quota blocks writes, never reads.** New uploads and new work that produces artifacts are refused with a clear message; existing work stays readable and re-runnable. |
| WSP-06 | MUST | Because a shared upload charges everyone, an upload can push several users over at once. Before committing such an upload, **admin sees a warning naming the affected users**. |
| WSP-07 | MUST | **Only an admin may delete a database or dataset** — not the uploader, unless they are an admin. Deletion is audited. |
| WSP-08 | MUST | **Factory reset is a supported, admin-only feature** (D-19 — restored on requester instruction after earlier removal from the surface). Two grades, both already implemented in `system_db.py` and currently exposed nowhere: **surgical reset** (`reset_demo`, `system_db.py:846` — clears databases, datasets and all derived artifacts; preserves user accounts and platform seeds) and **full wipe to blank slate** (`wipe_all_items`, `system_db.py:897`). Exposed through the Admin page with a type-to-confirm guard, admin session required, an audit event recording actor + grade + record counts removed. It composes with WSP-07 when workspaces land — it is the sanctioned deletion path, not a route around it. |
| WSP-09 | MUST | Define the isolation boundary explicitly and test it: **shared** = datasets, files, profiles, inventories, published knowledge, taxonomy. **Private to a workspace** = assessments, plans, results, scores, issues, RCA cases, proposal-loop candidates, reports. Shared datasets are a deliberate cross-workspace exception; every other cross-workspace read is indistinguishable from an unknown ID. |
| WSP-10 | MUST | Admin manages users, allowances and deletion **without automatic access to another workspace's private content**. |

---

## 8b. Data ingestion / sourcing (ING)

Source: requester instruction, 30 Jul evening (D-20): *"simplify the data ingestion workflow… simple
and minimalist… good field mapping, data dictionary validation."* Redesigned on first principles —
the reference product (`missingness-atlas`) informed the principles, nothing is copied. Replaces the
shipped Data Sourcing flow (create item → upload per role → finalize → profile button → inventory
grid), whose defects mirror the old Test Lab's: hardcoded schema name-hints (`TYPE_PRIORITY`,
`service.py:40-48`), a button per pipeline step, and dictionary discrepancies buried in a grid.

| ID | Priority | Requirement |
|---|---|---|
| ING-01 | MUST | **Three moments, one flow: Drop → Review → Ready.** Dropping a file starts parsing and profiling immediately — no separate finalize step, no profile button. The user's only decision surface is the Review screen. |
| ING-02 | MUST | **Dataset required; everything else optional and labelled so.** Data dictionary optional; use case and target asked once, inline, with sensible defaults. The minimum happy path is: drop dataset → glance at Review → Ready. |
| ING-03 | MUST | **Confidence-tiered field mapping, pre-applied.** Dictionary headers map to canonical fields by exact/alias match (high confidence — applied silently), fuzzy match above a floor (shown as "confirm suggestion"), or not at all (never guessed below the floor). One-to-one enforced; a source column consumed by one mapping is unavailable to others. |
| ING-04 | MUST | **Dictionary validation is structured and non-blocking.** Findings surface as `{column, code, message}` warnings, collapsed by default: dictionary-variable-not-in-dataset, unused dictionary fields, declared-vs-inferred type conflicts, unsupported type/role values. Hard-fail only on structural corruption (e.g. duplicate dictionary rows). Unmapped dictionary content is framed **"preserved, not used"** — never silently discarded. |
| ING-05 | MUST | **Graceful degradation without a dictionary.** Types inferred conservatively; affected columns marked `provisional`; the item carries a dictionary state `yes / thin / absent` — where `thin` includes a dictionary that looked complete but still left columns to inference. The state is visible wherever the item is consumed. |
| ING-06 | MUST | **The Review screen is the single decision surface**: per column — inferred classification, role, dictionary declaration and any discrepancy — editable in place. Everything not touched keeps its default. |
| ING-07 | MUST | **One status machine**: `uploading → profiling → needs_review → ready` (or `failed` with reason). The Test Lab consumes only `ready` items. Status is derived from what has actually happened, never set by a button. |
| ING-08 | MUST | **Ingestion output is the substrate for everything downstream**: the confirmed mapping, dictionary state, warnings and profile snapshot persist with the item and feed the Test Lab manifest — cross-field role resolution (CFR-05) reads the same dictionary and mapping records. |
| ING-09 | MUST | **Replacement is a new delivery, never mutation** — a re-upload of the same logical dataset increments the PLT-02 delivery record. |
| ING-10 | MUST | **No hardcoded schema knowledge** (KB-01 family): the `TYPE_PRIORITY` name-hint table and literal column names (`facility_id`, `origination_date`, …) do not survive. Inference uses generic signals (dtype, cardinality, patterns) plus the dictionary and KB — data, not code. |

---

## 9. Platform and cross-cutting (PLT)

| ID | Priority | Requirement |
|---|---|---|
| PLT-01 | MUST | **Sizes display in MB**, one decimal, with the allowance — e.g. `142.6 MB of 500 MB`. Never bytes, never a raw large number. Large counts use thousands separators. This applies everywhere sizes surface: uploads, datasets, quota views, admin, reports. |
| PLT-02 | MUST | **Land the delivery/baseline data-model seam now**, even though Plan B's diagnostics are deferred (D-11): `dataset_family_id`, `delivery_seq`, `as_of_date`, nullable `baseline_delivery_id`. Cheap now; retrofitting later migrates every dataset plus every result, issue and RCA case keyed to it, and would rework RCA closure — the most safety-critical code in the release. |
| PLT-03 | MUST | RCA's frozen-snapshot closure re-run and Triage's data-window grouping both read the delivery/as-of record from PLT-02 rather than wall-clock time. |
| PLT-04 | MUST | A shared error sanitizer on every new route. The existing v2 handlers can expose exception text; do not replicate that. |
| PLT-05 | MUST | Append-only audit events for every new decision, gate, deletion, quota change and knowledge publication. |
| PLT-06 | MUST | Additive, idempotent migrations; run twice in validation; back up metadata first. |
| PLT-07 | MUST | No new runtime dependency unless a requirement cannot be met without one, and then only with a recorded decision. |
| PLT-08 | MUST | **The AI helper/tool layer is decluttered for human readability, understandability and debuggability** (D-21) — scheduled **upfront**, before feature work builds on it. Scope: `ai/test_kit.py`, `ai/tool_registry.py`, `ai/rca_helpers.py`, `ai/code_sandbox.py`, `ai/control_plane.py`, `ai/llm.py` and their satellites. Required end-state: **one** helper registry (no parallel catalogues); explicit typed contracts per helper (purpose, inputs, outputs, failure modes in the docstring); no circular, conditional-at-call-site or broken imports — `rca_helpers.py:16` (`database`, deleted in 0.2.0) and `tool_registry.py:148,217` (`routers.ingestion` does not exist) are standing defects; flat, discoverable module layout; structured logging with a run/case id on every helper call, so a failure is traceable without a debugger; a unit test per helper. Behaviour-preserving except where the current behaviour is already broken. |

---

## 10. Conflicts

### 10.1 Closed

| ID | Original conflict | Disposition |
|---|---|---|
| C-01 | "Eight diagnostics" vs 14 tests / 11 areas | **Resolved by S8.** The framework is replaced (§1). "Core diagnostic 9 / test area 6" decodes as: the loop is core diagnostic #9 in area T6. "Eight adapters" = core diagnostics minus the loop — **eight** again, since D-14 returned categorical leakage to Phase-2 |
| C-02 | 15th test vs `len(TEST_NAMES) == 14` | **Superseded.** The registry is rebuilt, not appended to. The count assertion is rewritten for the new register, deliberately, not deleted |
| C-03 | Hardcoded 49 rules vs governed KB | **Resolved, more strictly than proposed.** Rules ship nowhere in code (KB-01); no seed, no fallback (CFR-04) |
| C-04 | `CFR_LLM_API_KEY` / `anthropic` vs Azure OpenAI | Route through `ai/llm.py` + `control_plane`; reject the dependency (CFR-11, CFR-18) |
| C-05 | Interactive CLI vs FastAPI | Pure core → service → router; interactive paths become decision records (CFR-12, CFR-13) |
| C-11 | Deterministic vs LLM agents | **Reversed.** Determinism is the default; four named seams only (§2, RCA-36) |
| C-13 | Three confidence semantics | Legacy numeric and earned tier kept separate, never converted; APL confidence is display-order only, with a test that a high-confidence candidate still needs an explicit accept |
| C-14 | "One pipeline" will erode | Architecture test: no `diagnostic_id` branch in the pipeline, plus a throwaway extra adapter proving the source is unchanged (APL-08) |
| C-15 | Five APL open items | All five answered — D-01 … D-03, D-10, APL-36 |
| C-16 | Residual "v10" naming | Product stays unversioned; requirement docs may keep "v10" as provenance, with a mapping note in `docs/rca/00-contracts.md` |
| C-17 | A second requirements file | Discard. Net dependency change: none |
| C-20 | "Delete" vs "defer" | **Re-resolved by D-17 (30 Jul):** deferred diagnostics are not registered at all — they exist only as S8 documentation. The shipped tests are deleted per FWK-13 regardless. (Rev 2–3 had them "registered but non-executable"; the requester corrected this.) |
| C-21 | Determinism vs generative RCA agents | Accepted: two LLM seams (Planner's next look, Coverage challenge); Reader/Judge/Composer deterministic (RCA-36) |
| C-22 | Value-semantics dependency | FWK-10 … FWK-12, with the worked example in §1.3, and a hard gate rather than documentation |
| C-23 | Material-fields filter absent | **Reinstated** (D-18, superseding D-05) with the data-driven definition in FWK-09: KB-role-mapped ∨ declared feature/target ∨ dictionary-flagged. Lands with the first statistical workflow — no slice-1 code impact |
| C-24 | Thresholds as code constants | FWK-06: semantic layer, tunable |
| C-26 | KB PDF round-trip is lossy | KB-08 … KB-11: parse to records, bind to verified implementations, never execute a prose-recovered predicate, surface the parse report |
| C-27 | KB categories vs dimensions | KB-15: two axes — dimensions are user-facing, fact kind is internal plumbing that enforces the quarantine matrix |
| C-28 | Shared datasets charged to every user | **Kept as specified** (D-07). WSP-05/06 make the consequence predictable |
| C-31 | Workspace vs single-tenant model | WSP-01: `workspace_id` *is* `tenant_id`; shared datasets are the one deliberate exception (WSP-09) |
| C-33 | Categorical leakage in / out | **Out** — reopened and re-closed by the 30 Jul S8 re-issue (D-14 supersedes D-09): the sheet marks #3 `Defer (phase-2)` and its footnote fixes core at 9. Numbers restated: 9 registered (D-17), 11 documentation-only defer rows, 8 adapters. *(A reviewer comment in the ignored `#F79646` column argues for phase-1; if that lands, it lands as a new decision, not by editing this one.)* |

### 10.2 Open

**C-06 — Per-step RCA buttons vs the autonomy contract.** 🔴
The largest behavioural conflict. **Resolution:** a server-side orchestrator `run_to_next_gate(case_id)`
drives everything and halts only at a gate, a terminal state or an error, streaming over SSE; the
per-step endpoints survive behind `RCA_STEP_MODE` (default **off**) for UAT and debugging — the
requirement is that *the product* does not prompt, not that the capability cannot exist; the UI becomes
one Run action, a live trail, a read-only board, and gate cards that appear only when a gate is open;
an autonomy policy carries `conclusion_approval: required|waived` and a hard-coded
`remediation_approval: required`.

**C-07 — All families produce the same plan.** 🔴
`_infer_test_family` returns `missing_data` for anything unmatched, with the code's own admission that
it is *"the only family Stage 4's opening looks implement content for"* (`rca.py:200`); the opening look
is `_profile_column` on the first column; the Planner always proposes `segment_breakdown`. **Resolution:**
an `rca_investigation_plans` record with the five named sections, versioned per case; a per-family
template library (RCA-10) with 2–4 opening looks each and a family-specific look library the Planner
selects from; an explicit flagged `generic` fallback; and the behavioural test that *is* RCA-A2.

**C-08 — The Knowledge gate is a declared state with no implementation.** 🔴
**Resolution:** an `rca_questions` record; the Planner creates a question and transitions to
`awaiting_human_answer`; the budget clock stops across the pause; the answer is written by the
deterministic KB service as a `human_confirmed` draft requiring approval, linked to the case; the same
fact is never asked twice; and a negative test proves the case pauses rather than the Planner inventing
an answer.

**C-09 — Two gates and half of escalation are missing.** 🟠
**Resolution:** one `rca_gates` record type (`gate_kind`, `state`, actor, reason, timestamps) rather
than four bespoke tables. Migrate the existing fix-approval flow onto it without changing behaviour or
losing records.

**C-10 — Plans must name pipeline logs; the product has no log source.** 🟠
**Resolution:** declarative and honest. The plan names the log source it *would* read; with none
registered it records `log_source: unavailable — no log source registered for <table>` and the case
carries `log_evidence_unavailable` into the findings artifact. Never fabricate log reading; never drop
the section — a reader must see that the line of enquiry was unavailable rather than unexamined.

**C-12 — Triage groups on wall-clock, not the data window.** 🟡
`rca.py:210-228` uses same `item_id` + `table_name` within 30 wall-clock minutes — a proxy for "same
battery run", not "same data period". **Resolution:** signal (a) becomes the failing result's data
period from PLT-02's delivery record, enriched with KB `lineage` facts; the 30-minute window survives
only as a tie-break for concurrent runs; signal (b) tightens to family plus direction and comparable
magnitude.

**C-18 — Replacing the framework drops downstream records.** 🟠 *Accepted, per instruction.*
Dropped: plans, results, scores, issues, tracked issues and RCA cases keyed to old tests; the old
registry, param specs and test library; `verify_plan8.py` and `test_finalized_framework.py` are rebuilt
against the new register. **Kept:** datasets, files, tables, variable inventories, users and roles,
taxonomy tags, knowledge documents. No compatibility layer, no dual-read, no remap — the Galileo
version is retained separately.

**C-19 — Deleting validated tests breaks two validators.** 🔴
`verify_plan8.py` (documented 37/37) and `test_finalized_framework.py` both assert against the
fourteen. **Resolution:** rebuild both against the new register in the same commit as the deletion,
with the new counts asserted. Never relax an assertion to make a gate pass.

**C-25 — Plan A vs Plan B.** 🟠 *Deferred with a seam.*
Plan B's diagnostics (delivery-to-delivery drift, feature-drift-over-time) are out of 0.4.0 pending SME
input. The **data model lands now** (PLT-02) because retrofitting it later migrates everything and
reworks RCA closure. Two dimensions display as GAP by design (FWK-14). No other delivery is blocked.

**C-29 — `reset_demo` / `wipe_all_items` bypass admin-only deletion.** 🟠 → WSP-08.

**C-30 — "Own workspace" while the main asset is shared.** 🟠 → WSP-09 names the boundary explicitly;
without that, isolation cannot be tested.

**C-32 — The cross-field diagnostic flow chart has not arrived.** ✅ **CLOSED, 30 Jul evening — the
chart arrived** (S10, `cross_field_engine_V2_workflow_1.png`) and was reconciled against
[testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md) §3. The mapping holds with no design change:
chart steps 0–4 (load, use case, KB load, role resolution, optional LLM verification, entity binding)
= the redesign's **readiness + scope-gate manifest**; the chart's two interactive prompts (use-case
`input()`, per-conflict keep/LLM/manual pause) become **decision records with documented defaults**
per CFR-12, never blocking prompts; step 5 + the per-rule gate = the **run**; step 6's report +
`resolution_map.json` = the **findings pane + persisted manifest provenance**. The chart's own design
guarantees — "LLM touches mapping only, never a verdict"; "nothing silent — N-A always stated" —
were already CFR-11/CFR-04. **Still open for the other 8 core diagnostics:** each stays
`workflow-pending` (FWK-17/18) until its own workflow definition arrives. Do not invent one.

**C-34 — The KB parser cannot see 49 rules; it would create one.** 🔴
`_split_sections` (`kb.py:169-192`) splits on `## ` H2 headings only, one section = one rule. S9 is a
PDF of tables with no Markdown headings, so it yields **exactly one** "Overview" rule containing all
four pages — and nothing to play back as a summary. **Resolution:** a table-aware parser (KB-04) with
the per-rule parse report (KB-10). This is now on the critical path for **two** requirements (KB-03 and
CFR-03) and, with no fallback rule set (CFR-04), a parse failure means the cross-field diagnostic cannot
run at all. Highest-consequence component in the release.

**C-35 — Knowledge documents cannot be tagged.** 🟠
`taxonomy.py` tags items, tests, results and issues (`routers/v3.py:37-92`) but there is no
`/knowledge/documents/{id}/tags`. The exact interaction the requirement describes — upload, then tag the
right dimensions — is the one piece of wiring missing. → KB-02.

**C-36 — `business_rules.json` is a hardcoded KB loaded at runtime.** 🟠
`ai/v2/service.py:38`. Delete the file and its loader; anything depending on it moves to KB retrieval.

**C-37 — Framework definition conflated with knowledge base.** 🟡
`knowledge_base/dq_framework_data.json` is the *framework* (areas, tests, weights), not domain
knowledge. It is **replaced** by the new framework, not deleted as hardcoded KB. Keep the two concepts
and their storage separate, and say so in the docs (FWK-04) — otherwise a later contributor deletes the
framework in the name of KB-01.

### 10.3 Summary

| ID | Severity | Theme | Owning requirement |
|---|---|---|---|
| C-34 | 🔴 | KB parser sees 1 rule, not 49 | KB-04, KB-10 |
| C-06 | 🔴 | Per-step buttons vs autonomy | RCA-16 … RCA-18 |
| C-07 | 🔴 | One generic plan for all families | RCA-08 … RCA-10 |
| C-08 | 🔴 | Knowledge gate unimplemented | RCA-11 |
| C-19 | 🔴 | Deleting tests breaks two validators | FWK-13 |
| C-09 | 🟠 | Two gates missing | RCA-12, RCA-13, RCA-15 |
| C-10 | 🟠 | Plans need logs; no log source | RCA-08 |
| C-18 | 🟠 | Framework swap drops records | FWK-13 |
| C-25 | 🟠 | Plan A vs Plan B | PLT-02, FWK-14 |
| C-29 | 🟠 | Reset/wipe bypass | WSP-08 |
| C-30 | 🟠 | Workspace boundary undefined | WSP-09 |
| C-32 | ✅ closed for cross-field | Workflow definitions: cross-field provided 30 Jul; 8 core diagnostics still pending | FWK-17, FWK-18 |
| C-35 | 🟠 | Knowledge documents untaggable | KB-02 |
| C-36 | 🟠 | Hardcoded `business_rules.json` | KB-01 |
| C-12 | 🟡 | Wall-clock grouping | RCA-32, PLT-03 |
| C-37 | 🟡 | Framework vs KB conflation | FWK-04 |

---

## 11. Decision record

Answered decisions. A default silently assumed is a defect — every row here is either confirmed or
explicitly adopted with its date.

| ID | Decision | Status |
|---|---|---|
| D-01 | The SME **can edit** a candidate at the gate — a third action alongside accept and reject. | Confirmed |
| D-02 | An SME edit **does not** consume one of the 3 attempts; it is recorded `authored_by: sme`. | Confirmed |
| D-03 | An accepted-but-non-executable candidate is **parked as a build request** with provenance, not discarded. | Confirmed |
| D-04 | *Key uniqueness* is **deleted as a diagnostic but retained as a profiling precondition**, because #6's anti-join needs a unique grain key. | Adopted 29 Jul 2026 |
| D-05 | ~~The "material fields only" filter is dropped.~~ | **Superseded by D-18** — the requester took the earlier decision back (30 Jul evening) |
| D-06 | KB parse failures are **surfaced to the uploader** so they can revise and re-upload; the parse report is a first-class artifact. | Confirmed |
| D-07 | Shared datasets **consume every user's allowance**; admin can raise the limit for one user or for all. | Confirmed |
| D-08 | Cross-field LLM role verification is **off by default**, opt-in per run, with every mapping change recorded. | Adopted 29 Jul 2026 |
| D-09 | ~~Categorical leakage is in core. Core = 10, deferred = 10, adapters = 9.~~ | **Superseded by D-14** |
| D-10 | Closure-generated and loop-persisted KB objects are **drafts requiring human approval**, never auto-published. | Adopted 29 Jul 2026 |
| D-11 | **Plan A only** in 0.4.0; Plan B's diagnostics deferred pending SME input; the delivery/baseline data model lands now (PLT-02). | **Confirmed 30 Jul 2026** — the walkthrough declared Plan B "completely out of scope for now" |
| D-12 | In autonomous runs, conclusion approval is **required by default**; waiving is an explicit per-workspace choice. Remediation approval is never waivable. | Adopted 29 Jul 2026 |
| D-13 | Target version **`0.4.0`**, breaking, with the framework replacement, the Test Lab replacement and the RCA workflow change called out in the release note. (Earlier revisions said `0.5.0` — a documentation error.) | **Confirmed 30 Jul 2026** — requester correction |
| D-14 | **Categorical leakage returns to Phase-2** per the 30 Jul S8 re-issue (`Defer (phase-2)` + "MVP CORE is 9"). Core = 9, deferred = 11, adapters = 8. Supersedes D-09. | Adopted 30 Jul 2026 |
| D-15 | **First build slice: only #4 Cross-field business rule is executable in the Test module.** The other 8 core diagnostics are registered `workflow-pending` (FWK-17/18); each is enabled by its own workflow definition, one at a time. | Adopted 30 Jul 2026 — requester instruction |
| D-16 | **The Test Lab module is replaced, not patched** — the shipped four-step plan/snippet/approve wizard retires in favour of the register-driven workflow in [testlab-redesign-0.4.0.md](testlab-redesign-0.4.0.md). | Adopted 30 Jul 2026 — requester instruction ("challenge the current Test Lab module and design it completely fresh"); deletion of the dead code **confirmed and executed 30 Jul** (redesign §4.1) |
| D-17 | **The register holds only the 9 core diagnostics.** The 11 S8 `Defer (phase-2)` rows are documentation, not product scope: not registered, not displayed, not executable. Scope re-entry requires a framework revision with its own decision. Supersedes the rev-2/3 "registered but deferred" reading of FWK-05/FWK-13/C-20. | Adopted 30 Jul 2026 — requester correction ("consider only 9 rows where MVP Scope = Core… our scope is 9 diagnostics and not 20") |
| D-18 | **The material-fields filter is reinstated** with the data-driven definition in FWK-09: a field is material when it maps to a KB semantic role, sits in the declared feature/target set, or is dictionary-flagged for the use case. Never a hardcoded list; exclusions reported. Supersedes D-05. | Adopted 30 Jul 2026 (evening) — requester: "I will go with your recommendations… I take back my earlier decision" |
| D-19 | **Admin factory reset restored** (WSP-08): surgical reset (`reset_demo`) and full wipe (`wipe_all_items`), admin-only, type-to-confirm, audited. | Adopted 30 Jul 2026 (evening) — requester instruction ("bring it back") |
| D-20 | **The data ingestion workflow is redesigned** per §8b — Drop → Review → Ready, confidence-tiered mapping, structured dictionary validation, graceful degradation, no hardcoded schema knowledge. Principles informed by `missingness-atlas`; nothing copied. | Adopted 30 Jul 2026 (evening) — requester instruction |
| D-21 | **The AI helper/tool layer is decluttered first** (PLT-08) — readability, understandability and debuggability at the core, scheduled as an upfront phase before feature work. | Adopted 30 Jul 2026 (evening) — requester instruction |
| D-22 | **Controlled scaling gate:** the second diagnostic's workflow is defined and built **only after the requester declares satisfaction with the cross-field flow** in the rebuilt Test Lab. FWK-18's one-at-a-time enablement carries an explicit requester sign-off per diagnostic. | Adopted 30 Jul 2026 (evening) — requester instruction ("Once this is successful and I am satisfied with the flow, I will create the second diagnostic") |

**Still needed from the requester:**

| # | Item | Why it matters |
|---|---|---|
| 1 | ~~The cross-field diagnostic flow chart~~ | **Closed 30 Jul without a chart** — no chart was ever received; the workflow stands as S5/S6 + S8 row #4, reconstructed in the redesign §3 (C-32). If a chart surfaces, reconcile |
| 2 | ~~Confirmation of D-11~~ | **Confirmed 30 Jul** — Plan B completely out of scope for now |
| 3 | ~~Confirmation of D-13~~ | **Confirmed 30 Jul** — version is `0.4.0` (requester correction) |
| 4 | ~~`mortgage_irb_-_database.xlsx`~~ | **Withdrawn 30 Jul** — the product must work on any uploaded or ingested data; the workbook was only a parity *test fixture*. Parity is proven on a synthetic fixture (plan 6.7) |
| 5 | **Workflow definitions for the remaining 8 core diagnostics**, one at a time, when ready | Each flips one diagnostic from `workflow-pending` to `executable` (FWK-18). Nothing else is blocked while they are pending |

---

## 12. Out of scope for 0.4.0

| ID | Item | Reason |
|---|---|---|
| OOS-01 | The 11 S8 `Defer (phase-2)` diagnostics (incl. #3 categorical leakage, D-14) | **D-17: out of product scope entirely** — not registered, not displayed, not executable. They live in S8 as documentation; any return is a new framework revision with its own decision |
| OOS-02 | Plan B diagnostics — delivery-to-delivery drift, feature-drift-over-time | D-11; the data-model seam lands (PLT-02) |
| OOS-03 | Proposal-loop adapters beyond cross-field and PSI | Primitive inventories unestablished (APL-36); APL-08 must make each one cheap |
| OOS-04 | Pipeline/source log ingestion | No log source exists; C-10 handles the gap declaratively |
| OOS-05 | MCAR / MNAR mechanism testing | S8 defers it: honest output is a caveated escalation, and it must run only after value-semantics tagging |
| OOS-06 | Downturn identification | S8: judgement-laden; identify the regime from macro/outcome series rather than reading a label |
| OOS-07 | Learning better looks from closed cases | S1 §8 defers it explicitly |
| OOS-08 | Three-way suspect interactions | Pairs only; three-way escalates to a human |
| OOS-09 | OCR / image-only document ingestion | Rejected with a clear message |
| OOS-10 | Automatic application of production fixes | Never. Human-approved only; simulated in tests |
| OOS-11 | Deployment to Azure | Local validation and local commits only |

---

## 13. Traceability

Every ID above maps to a phase and a named test in
[archimedes-0.4.0-plan.md](archimedes-0.4.0-plan.md) §12. This document is the authority on *what*; the
plan on *how* and *in what order*.

`docs/rca/01-traceability-matrix.md` covers the 0.3.0 RCA rules. 0.4.0 adds a section per ID above with
implementation owner, target path, behavioural test, phase and status.
