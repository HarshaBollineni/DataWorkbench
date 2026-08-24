# Archimedes 0.5.0 — Implementation Plan

**Audience:** the engineers (human or model) implementing this release, and the independent validator
who signs each step off. This documSplent is your instruction set. It is written so that a developer who
has **not** read the requirements prose can execute a step from this document alone; every
architectural choice cites the requirement ID that justifies it, and every requirement ID that the
step covers is named in its Scope table.
**Authority:** [requirements-0.5.0.md](requirements-0.5.0.md) rev 1 is the authority on *what*
(D-28). This document is the authority on *how*, *in what order*, and *when you may call something
done*. Where this plan makes a choice the requirements do not fix, it is recorded in §3 as `P-nn` and
is binding.
**Precedent:** [archimedes-0.4.0-plan.md](../0.4.0/archimedes-0.4.0-plan.md) rev 4 — same document shape, same
rigor bar, same "spotless" definition. 0.4.0's requirement IDs (`ING-*`, `PLT-*`, `KB-*`, `CFR-*`,
`FWK-*`, `D-01…D-22`) remain live; where 0.5.0 changes one, the change is already recorded as a
conflict in requirements §9 and a decision in §10 — never silently overwritten here.
**Baseline:** `source-codes/` at the 0.4.0 release state. Verified 3 Aug 2026, treated as ground
truth for this plan: **pytest 361 passed / 1 skipped**; **79 live backend routes** (89 path+method
operations) from exactly four mounted routers — `auth`, `admin`, `v2`, `v3`
([main.py:15](../../../source-codes/backend/main.py#L15)); **153 exports** in
[ui/src/api/client.js](../../../source-codes/ui/src/api/client.js) of which **74 have no caller** anywhere in
`ui/src` (**65** of those also target a route absent from the live surface); **14 unreferenced UI
files**.
**Target:** `0.5.0` — the asset model and everything that falls out of it. Nine build steps, in the
dependency order requirements §13 already justifies. **That ordering is not re-litigated here.**
**Revision:** 1 — 3 Aug 2026.

---

## Table of contents

- [1. Operating rules](#operating-rules)
- [2. Step map](#step-map)
- [3. Assumptions and decisions](#assumptions-and-decisions)
- [4. Risk register](#risk-register)
- [5. Success metrics](#success-metrics)
- [6. Step 0 — Start gate](#step-0)
- [7. Step 1 — RET: retirement of superseded code](#step-1)
- [8. Step 2 — ADM + TAX: business language and reference data](#step-2)
- [9. Step 3 — AST: the asset / version / snapshot model (the spine)](#step-3)
- [10. Step 4 — UPL: the seven-step upload flow](#step-4)
- [11. Step 5 — SRC: landing page, context, Test Lab handover](#step-5)
- [12. Step 6 — CTX: downstream refresh and staleness](#step-6)
- [13. Step 7 — AST-16…19: version diff and fingerprinting](#step-7)
- [14. Step 8 — ANL: usage event log and catalogue](#step-8)
- [15. Step 9 — FNC: functional code map](#step-9)
- [16. Step 10 — Release acceptance](#step-10)
- [17. Traceability index](#traceability-index)
- [18. If you get stuck](#if-you-get-stuck)

---

## 1. Operating rules <a id="operating-rules"></a>

Read once, then obey for the whole release. Not advisory. Rules 1–15 of the 0.4.0 plan §1.1 remain in
force verbatim; the rules below are the 0.5.0 additions and the ones this release stresses hardest.

### 1.1 Non-negotiables

1. **Extend in place; replace deliberately.** The asset model *replaces* the flat-item model — that is
   intended (AST-10, D-25, C-40). Everything else — auth, admin, KB governance, taxonomy, issues,
   RCA, the Test Lab, the cross-field diagnostic — must remain operational at the end of **every**
   step, not only at the end of the release.
2. **No second module.** No `service_v3.py`, no `assets/` package that shadows `ai/v2/service.py`, no
   parallel `DataSourcing2.jsx`. Reshape inside the existing structures. The one exception is a new
   *cohesive* package where the requirement genuinely introduces a new concern
   (`backend/assets/` for identity/version/snapshot mechanics, `backend/analytics/` for the ANL-03
   event log) — and each such package is named in its step's Permitted paths, nowhere else.
3. **Additive, idempotent migrations, and there is no Alembic.** Every schema change goes through
   `system_db.py`'s two existing mechanisms and no third one: a new table is a
   `CREATE TABLE IF NOT EXISTS` block appended to `_SCHEMA`; a new column is an entry in the
   `_MIGRATIONS` dict, applied by `init_schema()`'s introspection-guarded
   `ALTER TABLE … ADD COLUMN` loop ([system_db.py:718-781](../../../source-codes/backend/system_db.py#L718-L781),
   [:914-926](../../../source-codes/backend/system_db.py#L914-L926)). Backfills are `_backfill_*` functions
   called from `init_schema()`, written to touch **only rows that have never been backfilled**, so a
   second call is a no-op — exactly the shape of `_backfill_delivery_defaults` and
   `_backfill_ingest_defaults`. **Run every migration twice in validation.** Never drop a column;
   never rename one (PLT-06).
4. **Never delete a column another gate asserts.** `verify_plan8.py:136` asserts `dq_items` still
   carries `{dataset_family_id, delivery_seq, as_of_date, baseline_delivery_id}`. All four survive
   0.5.0 (see R-01 and P-04).
5. **Behavioural tests only.** A test asserting a symbol exists, a route is registered, a column is
   present, or that a call returns without raising is **not** acceptance evidence. The exceptions are
   the deliberately structural invariants (RET-04's reachability walk, the "no Process button on the
   pipeline" assertion, AST-17's "the diff never reads a data file") — each is labelled `N` and each
   must be shown to bite.
6. **For every invariant, prove the test bites.** Remove or invert the invariant and confirm the test
   fails. Mandatory for: RET-04, ADM-04's label completeness, AST-22's fixed time basis, AST-08's
   superseded-not-selectable rule, AST-11's ordering guarantee, UPL-12's duplicate/blank block,
   UPL-22's server-side confirmation check, CTX-02's staleness marking, AST-17's O(columns) diff,
   ANL-03's append-only log.
7. **Never relax a failing test to make a gate pass.** 0.4.0 tests that encode superseded behaviour
   (`test_ingest.py:325-340`'s sibling-row re-upload assertions, `test_delivery.py`'s family
   semantics, `ui/e2e/upload-workflow.spec.js:80-100`'s replacement-delivery testids) **must** be
   rewritten against the new model **in the same commit as the cause**, with the new assertions
   stated. Rewriting them is expected; deleting them to go green is not.
8. **Read-only against user data.** No profiling, fingerprinting or diff step ever writes to or alters
   an uploaded file or a stored snapshot. Superseding is a status change on a metadata row
   (C-40) — never a delete, never an edit of snapshot content (AST-08, OOS-15).
9. **Immutability is at the snapshot level.** Once a snapshot is stored it is never mutated —
   not its file, not its confirmed type map, not its fingerprint. Corrections happen by adding a
   snapshot or a version (AST-05, AST-08, D-25).
10. **No domain knowledge in code** (KB-01, ING-10 hold). Type inference stays generic-signal-only;
    no column-name literal enters inference, the fingerprint, or the diff.
11. **Deterministic. No new LLM seam.** 0.5.0 adds no model call anywhere. The four DET-02 seams are
    unchanged and seam (a) stays off by default (D-08). A step that "would be easier with a model"
    is a design error in that step.
12. **Local commits only.** No push, force-push, rebase, reset, amend, history rewrite, tag or deploy
    (OOS-18).
13. **One step, one commit, product operational at every commit.** A step too large to land in one
    commit splits at a *contract* boundary (see the explicit split points named in Step 3 and Step 4),
    each half with its own green gate.
14. **Do not invent a missing input.** The standing case in 0.5.0 is Q-01…Q-06: no requester is
    available, so §3 adopts the requirements' own §11 recommendations. Nothing beyond those
    recommendations is invented.
15. **Business language at every user-facing surface.** ADM-01 is stated for reset messages, but the
    rule generalises and this release is judged on it: **no database identifier, table name, column
    name of an internal table, status enum literal or ID prefix appears in user-facing prose**
    without a human label. See §5.

### 1.2 Definition of "spotless"

A step is spotless when **all** hold:

- Every requirement ID in the step's Scope table is implemented, not approximated.
- Every acceptance criterion passes, by its own named command, **exit code checked**.
- Every validation criterion has been executed by someone other than the implementer, and recorded.
- The invariant-bites check (rule 6) has run for every invariant the step touches.
- `./ci-local.ps1` is green: eslint (advisory), vite production build, byte-compile, boot smoke,
  backend pytest.
- `cd ui; npm run test:e2e` is green.
- `python backend/verify_plan8.py` exits 0 (it is a standing gate since 0.4.0 Phase 3).
- Migrations introduced by the step have been run **twice**, with schema and surviving-row hashes
  identical across the second run.
- No pre-existing file was modified outside the step's Permitted paths.
- The docs named in the step are updated **in the same commit** as the code.
- The traceability index (§17) row for the step carries a real status, not an aspiration.

### 1.3 Validation commands

Run from `source-codes/` unless noted. Record command, exit code, and pass/fail/error/skip counts.
**A run reporting passed items *plus errors* is a failure.**

```powershell
./ci-local.ps1                        # eslint (advisory) + vite build + byte-compile + boot + pytest
cd backend; ../.venv/Scripts/python.exe -m pytest tests -q    # run from backend/ — collection needs backend on sys.path
python backend/verify_plan8.py        # standing framework gate, 28/28 expected
cd ui; npm run lint
cd ui; npm run build
cd ui; npm run test:e2e               # Playwright (uvicorn 8001 + vite 5175, Chromium)
```

The pytest invocation must point `SYSTEM_DB_PATH` at a throwaway file, as `ci-local.ps1` does — never
at a developer's `system_state.db`.

New gates this release, each introduced by its own step and a gate from that step onward:

| Gate                                                                                    | Introduced          | What it blocks                                                                |
| --------------------------------------------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------- |
| `ui/src/__tests__/reachability` (or `node scripts/check-frontend-reachability.mjs`) | Step 1 (RET-04)     | an export with no caller and no keep-reason; a UI file reachable from nothing |
| `backend/tests/test_reset_language.py`                                                | Step 2 (ADM-01/04)  | a count key with no human label; a table name in reset prose                  |
| `backend/tests/test_asset_model.py` + `test_asset_migration.py`                     | Step 3 (AST)        | identity, version, snapshot and migration invariants                          |
| `backend/tests/test_upload_flow.py`                                                   | Step 4 (UPL)        | the seven-step blocking/warning contract                                      |
| `backend/tests/test_version_diff.py`                                                  | Step 7 (AST-16…19) | O(columns) diff; no data-file read                                            |
| `backend/tests/test_usage_events.py`                                                  | Step 8 (ANL)        | append-only log; measure derivability                                         |

Version bump, Step 10 only: `./Set-AppVersion.ps1 -Version 0.5.0`.

### 1.4 Test taxonomy

Carried from the 0.4.0 plan §1.5 unchanged, so the two releases' evidence reads the same way.

| Layer                 | Meaning                                                         |
| --------------------- | --------------------------------------------------------------- |
| **U** Unit      | Pure functions, no I/O                                          |
| **S** Service   | Service functions against a throwaway DB and fixture data       |
| **A** API       | Router level, including authorization                           |
| **M** Migration | Applied twice; surviving rows intact; counts and hashes checked |
| **N** Negative  | Invariant-bites tests. Prove the guard rejects the bad case     |
| **E** E2E       | Playwright through the real UI                                  |
| **P** Parity    | Output matches a frozen reference                               |

---

## 2. Step map <a id="step-map"></a>

Nine steps, in requirements §13's dependency order, bracketed by a start gate and a release step.
**S0 and S10 are process bookends, not a re-ordering of §13** — §13's nine steps are S1…S9 in exactly
its sequence, with its stated justification.

```
S0  Start gate — verify the baseline; take the census S1 acts on   (writes no product code)
 |
S1  RET   — dead client exports, orphaned components, frontend reachability test, tool_registry fix
 |          two-pass: delete components -> re-walk reachability -> only then decide RET-02's nine
S2  ADM+TAX — reset messages in business language; CRE under Product
 |
S3  AST   — asset identity, versions, snapshots, the stored record, the PLT-02 migration
 |          + ADM-06/07 version history (it renders exactly this data)          <- THE SPINE
S4  UPL   — the seven-step upload flow, steps 1 -> 7; writes the AST-17 fingerprint
 |          + FNC-01's service.py sheet refresh (same step that touches those functions)
S5  SRC   — landing page, View Existing dropdown, workflow context, Test Lab handover
 |
S6  CTX   — downstream refresh, staleness, blank defaults
 |
S7  AST-16..19 — version diff over S4's fingerprints
 |
S8  ANL   — append-only usage event log + catalogue screen (capture only, D-27/OOS-12)
 |
S9  FNC   — functional_tools.xlsx: the other seven areas
 |
S10 Release 0.5.0 acceptance
```

Why the order holds (requirements §13, restated for the implementer, not re-argued): S1 is cheapest
and stops new work being written against retired helpers. S2 clears the defect list before the
structural work runs through it. S3 is the spine — S4…S7 are unbuildable before it. S4 proves the
model end to end and is where the fingerprints get written. S5 needs real versioned assets to select
from. S6 needs both the snapshot model and the intents that change it. S7 needs S4's fingerprints.
S8 instruments S4…S6 — instrumenting earlier means instrumenting twice. S9 is documentation and never
gates a build step (FNC-04).

**Interleaving allowed:** none between S3 and S4 (S4 consumes S3's contract directly). S8's *schema*
(`usage_events` table + the single `record_event()` writer) may land early, at the end of S3, if — and
only if — doing so is what lets S4/S5/S6 call the writer as they are built rather than being
retro-fitted. The catalogue screen (ANL-06) does not move.

---

## 3. Assumptions and decisions <a id="assumptions-and-decisions"></a>

### 3.1 Adopted answers to the open questions (Q-01…Q-06)

No requester is available and the release must proceed autonomously (operating rule 14). This plan
therefore **formally adopts, as working decisions, the requirements document's own §11 "Recommendation
if no answer arrives" for every open question**. Nothing new is invented; each row below is the §11
recommendation, restated against its Q-id so this plan is self-contained and unblocked. If an answer
arrives later it supersedes the adoption and the affected step is re-planned.

| Adoption        | Question                                                                                                                                           | Adopted decision (= requirements §11 recommendation, verbatim in substance)                                                                                                                                                                                            | Binds                                                       |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| **A-Q01** | Is the three-tier version diff the right definition of "the exact differences between two versions"? Is a**distribution** difference wanted? | **Build tiers ① (schema) and ② (shape) as MUST, and tier ③ (distribution) as SHOULD.** Tier ③ costs almost nothing extra because the profiling pass already computes most of it.                                                                              | AST-16, AST-17, AST-18, AST-19 → S4 (capture), S7 (render) |
| **A-Q02** | Should the data dictionary version independently of the data (AST-20)?                                                                             | **Yes.** It is the only binding that gets the reuse benefit without the version-coupling cost. The dictionary belongs to the **asset** and is versioned on its own track; each snapshot records which dictionary version it was ingested against.           | AST-20, AST-21 → S3 (schema), S4 (binding)                 |
| **A-Q03** | For a**Database** upload, is one snapshot one workbook or one table?                                                                         | **One workbook = one snapshot, with per-table detail inside it.** It matches AST-05's "one file = one snapshot" and preserves cross-table relationships that per-table snapshots would sever.                                                                     | C-47, UPL-08, UPL-12, UPL-29, AST-13, SRC-05 → S3, S4, S5  |
| **A-Q04** | Is ANL-01's full 16-further-object-type list in scope for 0.5.0?                                                                                   | **Asset graph in 0.5.0 only** — asset, version, snapshot, variable (asset+column), dictionary version. The rest land as each module is next touched. A partial ID scheme is coherent; a rushed complete one is not.                                              | ANL-01, ANL-02 → S3 (asset graph IDs), S8 (scope boundary) |
| **A-Q05** | Is`CRE` a **Product** value only, or also a Portfolio value?                                                                               | **Product only, exactly as the note says.** Adding an unasked-for Portfolio value would change tag semantics for existing assessments.                                                                                                                            | TAX-01 → S2                                                |
| **A-Q06** | Should Admin's version history list every admin action with a durable effect, or stop at assets and resets?                                        | **Start with assets + resets.** Both already have a first-class audited record to render (PLT-02 deliveries; the reset's own `transaction_log` row). User/taxonomy history would need its own audit-event design first — a bigger ask than what was requested. | ADM-06, ADM-07 → S3                                        |

Consequences of the adoptions that the implementer must not miss:

- **A-Q01 makes tier ③ SHOULD to *render*, but its capture is MUST-grade in practice.** The
  fingerprint is written during S4's profiling pass or not at all: a snapshot ingested without one can
  never be retro-fingerprinted without re-reading its file, and superseded snapshots are immutable
  (rule 9). This is the same "capture now, present later" logic as D-27. **Therefore: S4 writes the
  full three-tier fingerprint for every snapshot; S7 renders tiers ① and ② unconditionally and tier ③
  behind the same surface.** Recorded as **P-01**.
- **A-Q03 fixes the Database vocabulary everywhere**: a Database snapshot's `row_count`/`column_count`
  are workbook totals, its type map is per-table, its duplicate-column check is per-table (UPL-12),
  its schema check compares the table *set* as well as columns within tables (UPL-29), and its
  dropdown row shows table count + total columns (SRC-05).
- **A-Q04 bounds S8**: exactly five typed ID families ship. Adding a sixth is scope expansion and
  needs a recorded decision.

### 3.2 Plan decisions (`P-nn`)

Choices this plan makes that the requirements leave to the implementer. Each is binding; each names
the requirement it serves. A developer who disagrees writes a superseding `P-nn` with a reason — they
do not silently do something else (0.4.0 plan §13 discipline).

| ID             | Decision                                                                                                                                                                                                                                                                                                                                                      | Why / which requirement it serves                                                                                                                                                                                                     |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P-01** | The three-tier fingerprint is**captured for every snapshot in S4**, even though A-Q01 makes tier-③ *rendering* a SHOULD.                                                                                                                                                                                                                             | AST-17 ("computed once during the profiling pass that already runs"); rule 9 makes retro-fitting impossible; D-27's capture-now logic.                                                                                                |
| **P-02** | The human-quotable system ID is**strictly alphanumeric, no dash**: `^(DS\|DB)[0-9]{4,}$` — e.g. `DS0007`, `DB0003`. The display name is `<SYSTEM-ID>-<alias>` → `DS0007-retail-pd`.                                                                                                                                                          | AST-02 calls the prefix "alphanumeric" and the dash the*delimiter*. A dash inside the ID would make `<SYSTEM-ID>-<alias>` ambiguous to split. `DS`/`DB` encodes the kind so the ID is self-describing in a log line (AST-01). |
| **P-03** | `system_id` and `alias` are stored as **separate columns**, and `display_name` is stored as a third, derived column. The display name is **never parsed back apart** anywhere in the product.                                                                                                                                               | AST-02/AST-04: the alias is editable and the ID is not; parsing is a bug waiting for an alias containing a dash (which AST-02 explicitly permits).                                                                                    |
| **P-04** | The asset ↔ snapshot seam is`dq_items.dataset_family_id`, reused literally as the asset FK. **No new `asset_id` column is added to `dq_items`.** `as_of_date` remains the snapshot's period anchor. `baseline_delivery_id` is left in place, unused, because `verify_plan8.py:136` asserts its presence.                                   | AST-10 ("the physical seam is 0.4.0's PLT-02 delivery record, re-read — not a new one"); operating rule 4.                                                                                                                           |
| **P-05** | `dq_items.name` becomes a **denormalised mirror** of `dq_assets.display_name`, with exactly one writer (`assets.rename_alias()` / `assets.create_asset()`). Every existing reader of `dq_items.name` (list_items, Inventory, TestLab, issues, reports) keeps working untouched.                                                               | AST-04 (rename must not break references); operating rule 1 (everything else stays operational at every step).                                                                                                                        |
| **P-06** | `snapshot_id` in AST-13's record is the existing `dq_items.item_id`, exposed under the name `snapshot_id` in every read model. `uploaded_at` is the existing `created_at`. No duplicate columns are added for either.                                                                                                                               | AST-10/PLT-06 additive-only; two columns holding the same fact is the drift AST-13 is trying to prevent.                                                                                                                              |
| **P-07** | Snapshot selectability lives in a**new** `dq_items.snapshot_status ∈ {active, superseded}` column — a third *axis*, not a third status *vocabulary*. `status` (lifecycle) and `ingest_status` (ingest machine) keep their exact current meanings and values.                                                                                | AST-08 names this axis explicitly; C-43 forbids a third**listing** vocabulary, which this is not. Any UI that renders all three must label them distinctly.                                                                     |
| **P-08** | Two event streams, deliberately distinct and never merged:**`dq_asset_events`** (S3) is the asset lifecycle audit-of-record that ADM-06/07 renders; **`usage_events`** (S8) is ANL-03's append-only measurement log. Where a single action is both, one helper writes both, in one transaction.                                               | ADM-06 is "what happened, when, by whom" over live objects; ANL-03 is "the single source for all analysis … never from the live tables". Collapsing them would make an analytics change an audit change.                             |
| **P-09** | UPL-07's structural checks and UPL-12's blank/duplicate column check run against the**raw header row** (csv reader / openpyxl first row), **never** against `pandas` column labels.                                                                                                                                                             | `pd.read_csv` silently rewrites a duplicate `id` to `id.1` and a blank name to `Unnamed: 0`, which would make UPL-12's block undetectable. This is the single most likely way to ship UPL-12 broken — see R-05.              |
| **P-10** | The ID counter lives in a new`id_sequences` table and is **deleted by both factory-reset grades**, restarting issuance at 1. The reset's existing `transaction_log` row ([admin.py:178-181](../../../source-codes/backend/routers/admin.py#L178-L181)) is extended with the pre-reset counter values and is the **epoch boundary**.                     | AST-01's factory-reset exception; D-29's "that row is the epoch boundary"; ADM-07 makes it legible to a human.                                                                                                                        |
| **P-11** | Superseding and restoring are**set operations on a version**, implemented as one service call each (`supersede_version_set`, `restore_version_set`), never as per-snapshot loops exposed to callers.                                                                                                                                                | AST-08 ("as one set"), AST-09 ("reactivates … wholesale"). A per-snapshot API would let a caller create a half-superseded set, which the model has no meaning for.                                                                   |
| **P-12** | The View Existing dropdown is**one component with one data source and a required `excludeRequiresReupload` boolean prop** — no default. A caller must state which entry point it is.                                                                                                                                                                 | SRC-05/SRC-13, C-42, D-31. A defaulted prop would let a new call site silently inherit the wrong exclusion.                                                                                                                           |
| **P-13** | RET-02's nine decisions are made**after** RET-03's deletions land and the reachability walk is re-run — not before.                                                                                                                                                                                                                                    | The confirmed transitive case:`patchRelations` is called only from `useErdModel.js`, which is used only by the orphaned `ErdPanel.jsx`. See R-02.                                                                               |
| **P-14** | `service.py`'s `create_item` and `reupload_item` are **rewritten in place** under new names (`create_asset` / `add_snapshot`) with the v2 route paths preserved (`POST /items`, `POST /items/{item_id}/reupload` → re-pointed). No compatibility shim is kept. 0.4.0 tests asserting the old semantics are rewritten in the same commit. | Operating rules 2 and 7; FNC-01 names`reupload_item` "the single most affected function". A shim would leave the C-40 sibling-row defect reachable.                                                                                 |
| **P-15** | 0.5.0 adds**no** analytics presentation surface: no chart, no trend line, no export, no aggregate dashboard. Capture completeness is proven by *computing* each ANL-04/ANL-05 measure in a test from a seeded event log.                                                                                                                              | D-27, ANL-07, OOS-12. Proving capture by computing rather than by presenting is what keeps OOS-12 honest.                                                                                                                             |

---

## 4. Risk register <a id="risk-register"></a>

| #              | Risk                                                                                                                                                                                                                                                                                                                                      | Why it is real (evidence)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Step  |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| **R-01** | **The AST-10 migration reshapes a seam whose current consumers are not all obvious**, and a wrong assumption about `as_of_date` / `baseline_delivery_id` breaks a passing gate or silently changes delivery ordering.                                                                                                           | Verified:`as_of_date` is **written** by `delivery.register_delivery` ([delivery.py:58-60](../../../source-codes/backend/dq_diagnostics/delivery.py#L58-L60)) and read by nothing in product logic — its only readers are `verify_plan8.py:136` (existence), `test_delivery.py:135-142`, `test_ingest.py:330`, `DataSourcing.jsx:365` (display) and `upload-workflow.spec.js:98`. `baseline_delivery_id` is **written and read nowhere** in product code; its only reference in the tree is `verify_plan8.py:136`'s presence assertion. `family_deliveries` orders by `delivery_seq`, **not** by `as_of_date` — so AST-11's period-first ordering is a *new* behaviour, not an existing one. There is also a live index on `(dataset_family_id, delivery_seq)` at [system_db.py:1056](../../../source-codes/backend/system_db.py#L1056). | **S3 task 3.1 is a mandatory read-and-record pass over `backend/dq_diagnostics/delivery.py`, `verify_plan8.py:120-150`, `system_db.py:766-768` and `:1050-1060`, producing a one-page consumer map committed as `docs/0.5.0/02-asset-model.md §Seam`, before any column is added.** P-04 keeps all four columns. AST-11's ordering is implemented as a *new* read-model function (`assets.ordered_snapshots`), leaving `family_deliveries` untouched so nothing that depends on seq-ordering changes underneath. Migration run twice with schema + surviving-row hashes (M).                                                                                           | 3     |
| **R-02** | **RET-02/RET-03 transitive dead code**: deciding RET-02's nine "live" exports before RET-03's deletions land produces a wrong answer, and a second tranche of dead code is left behind — exactly the failure mode RET-04 exists to prevent.                                                                                        | Confirmed:`patchRelations` is "live" only because `useErdModel.js` calls it; `useErdModel.js` is used only by `Erd/ErdPanel.jsx`, which is on RET-03's 14-file orphan list. Deleting ErdPanel makes the hook dead, which makes `patchRelations` dead. Any of RET-02's nine may have the same shape.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | S1 is explicitly**two-pass** (tasks 1.3 → 1.4 → 1.5): delete RET-03's files, **re-run the reachability walk**, *then* decide RET-02's nine and any newly-surfaced orphans. The walk is committed as the RET-04 gate first, so the second pass is machine-checked, not eyeballed. A third pass runs if the second surfaces new orphans, until the walk is a fixed point.                                                                                                                                                                                                                                                                                                       | 1     |
| **R-03** | **Steps 3 and 4 are far larger than any other step and will be estimated as one unit.** S3 introduces five tables, an ID scheme with a reset epoch, six changed service functions and a new Admin screen; S4 rewrites the entire Data Sourcing surface plus seven steps of validation. A half-finished S3 blocks S4…S7 completely. | Structural: requirements §13 itself calls S3 "the spine. Steps 4–7 are all unbuildable before it." FNC-01 lists nine service functions going stale, eight of them in S3/S4.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | **Pre-declared split points, each a contract boundary with its own green gate and commit:** S3a = schema + ID scheme + migration + read models (no behaviour change to the upload flow); S3b = `create_asset`/`add_snapshot`/version+supersede/restore service layer; S3c = ADM-06/07 version history UI. S4a = STEP 1–3 (target, upload, type check incl. UPL-12); S4b = STEP 4–5 (intent, time period, schema conflict incl. UPL-29); S4c = STEP 6–7 (storage, completion summary) + fingerprint write. **Each sub-step leaves the product operational.** If a sub-step overruns, stop at its boundary and commit — never carry a half-finished contract into the next. | 3, 4  |
| **R-04** | **`dq_items.name` denormalisation drifts** from `dq_assets.display_name` after an alias rename, so two surfaces show two names for one asset.                                                                                                                                                                                   | P-05 introduces the mirror deliberately to keep every 0.4.0 reader working.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Single-writer discipline (P-05) plus a test that renames an alias and asserts**every** `dq_items` row of that asset, and every read model, returns the new display name (S/N). A consistency check in `init_schema`'s backfill repairs drift idempotently.                                                                                                                                                                                                                                                                                                                                                                                                                          | 3     |
| **R-05** | **UPL-12 ships broken and undetected** because `pandas` mangles duplicate and blank headers before any check sees them.                                                                                                                                                                                                           | `pd.read_csv` renames a second `id` to `id.1` and a blank header to `Unnamed: 0`; `_read_tabular` ([service.py:153-168](../../../source-codes/backend/ai/v2/service.py#L153-L168)) is the only current reader.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | P-09: the check reads the**raw** header row. The adversarial test is literal and named in S4's validation: a CSV whose header line is `id,id,amount` must be blocked, and the test must fail if the check is moved onto `df.columns`.                                                                                                                                                                                                                                                                                                                                                                                                                                               | 4     |
| **R-06** | **Superseded retention grows unbounded** and someone "cleans it up", violating AST-08/OOS-15.                                                                                                                                                                                                                                       | AST-08 makes superseded snapshots permanently non-deletable; nothing in the product enforces that today.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | No delete path is built (OOS-15). A negative test asserts there is**no** service function or route that deletes a snapshot row or its file, and that a factory reset is the only thing that removes them (which is legitimate — D-29). Growth is recorded as a known, accepted 0.5.0 property in the release note.                                                                                                                                                                                                                                                                                                                                                                     | 3, 10 |
| **R-07** | **ADM-04's label map rots**: a later release adds a table to a reset's count dict, no label exists, and the table name leaks into the UI.                                                                                                                                                                                           | ADM-04 requires exactly this to "fail loudly". Today the leak is the*default* behaviour ([Admin.jsx:131-135](../../../source-codes/ui/src/pages/Admin.jsx#L131-L135), [:306-311](../../../source-codes/ui/src/pages/Admin.jsx#L306-L311)).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | The label map is validated by a backend test that enumerates the reset paths' actual key sets (`_WORKPRODUCT_TABLES`, the wipe list, `_rca_tables(conn)`, `tag_assignments`, `app_fsm`, `ingested_databases`, `table_metadata`, `object_contexts`, `context_links`, plus every reseed key) and fails on any key with no label. Bites-check: add a fake key, watch it fail.                                                                                                                                                                                                                                                                                                    | 2     |
| **R-08** | **The reset ID epoch confuses a reader**: two different assets both called `DS0001`, one pre-reset and one post-reset, appear in the audit trail with no visible boundary.                                                                                                                                                        | D-29 accepts this by design and relies on the`transaction_log` row as the boundary; ADM-07 exists precisely because "correct in principle" is not "legible to a human".                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | P-10 extends the reset audit payload with the pre-reset counter per scope; ADM-07 renders the reset rows**inline in the same chronological version-history list** so the boundary is visually unmissable. Test: create → reset → create, then assert the history view shows exactly one boundary row between the two `DS0001`s.                                                                                                                                                                                                                                                                                                                                                     | 3     |
| **R-09** | **CTX-01's recompute has an unbounded blast radius.** "Everything derived from it refreshes" touches target variable, tags, profiling results, and "related analyses" — which reaches into diagnostics runs and issues. An over-broad recompute could wipe user decisions.                                                         | CTX-01 corrects a real defect but does not bound the set. CTX-02 exists for what cannot be recomputed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | S6 must first**enumerate** the derived set in `docs/0.5.0/05-refresh-contract.md`, splitting it into *recompute* (deterministic from the active set: variable inventory, profile, fingerprint-derived summaries, dictionary binding state) and *mark stale* (anything carrying a human decision or a run outcome: diagnostic runs, findings, dispositions, issues, RCA cases — CTX-02). **Nothing carrying a human decision is ever recomputed silently.** A negative test asserts a superseded run's findings and dispositions survive a replacement, marked stale.                                                                                                       | 6     |
| **R-10** | **Playwright spec churn hides a regression.** S4 and S5 rewrite the surfaces `upload-workflow.spec.js` asserts, and it is the only e2e proof the ingestion journey works.                                                                                                                                                         | `upload-workflow.spec.js:80-100` asserts `replacement-family-id`, `replacement-delivery-seq`, `replacement-as-of-date` testids that S4 removes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Rule 7: the spec is rewritten in the same commit as the cause, and the**new** spec must cover strictly more than the old one — both Fresh Upload and Full replacement passes through all seven steps (see §5). The old assertions' intent (the prior delivery is untouched) is re-expressed against the new model (the prior snapshot is retained and superseded), not dropped.                                                                                                                                                                                                                                                                                                       | 4, 5  |
| **R-11** | **SQLite additive migrations with no migration framework.** Five new tables and ~15 new columns in one release, applied by a hand-rolled introspection loop.                                                                                                                                                                        | There is no Alembic in this codebase;`init_schema()` is the whole mechanism ([system_db.py:914-926](../../../source-codes/backend/system_db.py#L914-L926)).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Operating rule 3. Every migration run twice in validation, with canonical-schema and all-surviving-row hashes compared — the same standard 0.4.0's R-11 closed at. Back up the metadata DB before the first S3 run.                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | 3–8  |

---

## 5. Success metrics <a id="success-metrics"></a>

Measured at the end of every step where applicable, and all of them at Step 10. These are pass/fail,
not aspirations.

| #              | Metric                                                                                                                                                                                                                                                                                | How it is measured                                                                                                                                                                                                                                                                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **M-1**  | **The pytest suite is green at the start and end of every step, and never regresses below the baseline count.** Baseline: 361 passed / 1 skipped. Every step's own commit raises the count; none lowers it except by a rewrite recorded under rule 7 with the new count stated. | `cd backend; pytest tests -q`, exit 0, counts recorded per step                                                                                                                                                                                                                                                                                                          |
| **M-2**  | **Zero table names, column names of internal tables, or status enum literals leak into user-facing text.**                                                                                                                                                                      | A test that renders every reset message, every asset/snapshot status label, and every version-history summary, and asserts the rendered strings match no name in`sqlite_master` and no raw enum value (`needs_review`, `requires_reupload`, `superseded`, …) without a human label. Plus a Playwright text assertion on the Admin screen after both reset grades. |
| **M-3**  | **A Playwright walkthrough of the full seven-step upload flow passes for both a Fresh Upload pass and a Full replacement pass**, on a clean factory-reset instance, for a Dataset **and** for a Database (A-Q03's multi-table workbook).                                  | `cd ui; npm run test:e2e` — four journeys, each stepping 1→7 and asserting each step's own surface, ending on the UPL-27 completion summary                                                                                                                                                                                                                            |
| **M-4**  | **Every requirement ID in requirements-0.5.0.md maps to a step and at least one named behavioural test, with no ID unaccounted for.**                                                                                                                                           | Script over §17's traceability index; zero rows`not started` at Step 10; `PROPOSED`/`LATER` IDs carry their disposition explicitly                                                                                                                                                                                                                                  |
| **M-5**  | **Every migration is idempotent.** Second run changes nothing: identical canonical schema hash and identical hash over all surviving rows.                                                                                                                                      | `test_asset_migration.py` (M), run in CI                                                                                                                                                                                                                                                                                                                                 |
| **M-6**  | **The version diff never reads a snapshot's data.**                                                                                                                                                                                                                             | `test_version_diff.py` monkeypatches `_read_table`/`_read_tabular`/`pd.read_csv`/`pd.read_excel` to raise, then runs a full three-tier diff to completion (N)                                                                                                                                                                                                    |
| **M-7**  | **No dead export survives, and a new one cannot be added silently.**                                                                                                                                                                                                            | The RET-04 gate is green, and its bites-check (add an uncalled export → gate fails) is recorded                                                                                                                                                                                                                                                                           |
| **M-8**  | **Asset identity survives replacement.** After a Full replacement, the asset's `system_id`, `display_name` and every reference to it are unchanged, and the prior snapshots still exist on disk and in the DB, marked superseded.                                           | `test_asset_model.py` (S/N) + M-3's replacement journey                                                                                                                                                                                                                                                                                                                  |
| **M-9**  | **Zero new LLM calls.** The default suite makes no model call; seam (a) remains off.                                                                                                                                                                                            | The 0.4.0 counting-fake provider assertion, re-run unchanged                                                                                                                                                                                                                                                                                                               |
| **M-10** | **`verify_plan8.py` exits 0 at every step**, proving the 0.4.0 framework contract — including the four PLT-02 columns — survived the reshape.                                                                                                                               | `python backend/verify_plan8.py`, 28/28                                                                                                                                                                                                                                                                                                                                  |
| **M-11** | **Nothing carrying a human decision is silently recomputed.**                                                                                                                                                                                                                   | R-09's negative test: a replacement leaves prior findings/dispositions/issues intact and marked stale (N)                                                                                                                                                                                                                                                                  |
| **M-12** | **No analytics presentation surface ships** (OOS-12), yet every ANL-04/ANL-05 measure is computable from the captured log.                                                                                                                                                      | P-15: structural assertion (no chart/export component under the catalogue) +`test_usage_events.py` computing each named measure from a seeded log                                                                                                                                                                                                                        |

---

## 6. Step 0 — Start gate <a id="step-0"></a>

**Objective.** A verified baseline and the census S1 acts on, before anything is touched. A non-green
baseline is your first finding, not something to work around.

**Scope:** process. No requirement IDs. **Permitted paths:** `docs/0.5.0/**` only. **Writes no product
code. No commit of product files.**

### Todos

- [ ] 0.1 Read completely: this plan, [requirements-0.5.0.md](requirements-0.5.0.md),
  [archimedes-0.4.0-plan.md](../0.4.0/archimedes-0.4.0-plan.md) §1 and §11,
  `source-codes/docs/0.4.0/00-framework.md`, `06-ingestion-contract.md`, `07-decisions.md`,
  `08-helper-layer.md`.
- [ ] 0.2 Inspect what you will change: `backend/ai/v2/service.py` (all 13 FNC-01 functions),
  `backend/system_db.py` (`_SCHEMA` `dq_items` block at :239-257, `_MIGRATIONS` at :718-781,
  `init_schema` at :914-940, `_clear_item_pipeline` at :1222, `reset_demo` at :1241,
  `wipe_all_items` at :1302), `backend/dq_diagnostics/delivery.py`, `backend/ingest/*`,
  `backend/routers/v2.py`, `backend/routers/admin.py`, `backend/seeds/taxonomy_seed.py`,
  `backend/ai/tool_registry.py:195-220`, `backend/tests/test_reachability.py`,
  `ui/src/pages/DataSourcing.jsx`, `ui/src/pages/TestLab.jsx`, `ui/src/pages/Admin.jsx`,
  `ui/src/api/client.js`, `ui/e2e/upload-workflow.spec.js`.
- [ ] 0.3 Record git state (`status --short`, branch, HEAD, `log -10`, `diff --stat`). The tree must be
  clean before Step 1.
- [ ] 0.4 Run the full §1.3 command set. Record command, exit code, counts. Expected:
  pytest 361/1; `verify_plan8.py` 28/28; Playwright 16 passed / 1 skipped; vite build clean;
  eslint advisory warnings only.
- [ ] 0.5 **Take the RET census as machine-readable evidence, not prose.** Produce
  `docs/0.5.0/01-ret-census.md` containing: (a) the 153 client.js exports with, per export, its
  call sites in `ui/src` and its target route; (b) the 65 exports whose route is absent from the
  live OpenAPI surface (fetch `/openapi.json` from a booted instance, do not hand-list); (c) the 9
  unused-but-live exports; (d) the 14 unreferenced UI files with the import edges that *would*
  reference them; (e) the transitive chain `patchRelations → useErdModel.js → ErdPanel.jsx`, and
  any other chain of the same shape found by the same query.
- [ ] 0.6 Back up `system_state.db` (or note that none exists — it is boot-created and gitignored).
- [ ] 0.7 Produce `docs/0.5.0/00-start-gate.md`: verified baseline with exit codes and counts; the
  census summary; the seam consumer map stub R-01 will fill in S3.1; the split points from R-03
  restated as the actual commit plan.

**Exit gate:** baseline verified and reported; census committed; no product file modified.

---

## 7. Step 1 — RET: retirement of superseded code <a id="step-1"></a>

> Requirements §13 step 1. Cheapest, lowest-risk, and it stops new work being written against retired
> helpers. Independent of everything else.

### 7.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                             |
| ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RET-01 | MUST     | Delete the**65** `ui/src/api/client.js` exports that have no caller **and** target a route absent from the live surface.                                                                                                                                                                                                                                                                  |
| RET-02 | MUST     | Decide the**9** unused-but-live exports individually — `uploadItemFileV2`, `patchItemV2`, `createIssueAnalysesV2`, `interpretIssueAnalysesV2`, `getFrameworkTestsV2`, `getAgentsV2`, `getResultTagsV3`, `getTestTagsV3`, `setToken` — each wired up, deleted, or kept with a recorded keep-reason. Never left ambiguous.                                                      |
| RET-03 | MUST     | Delete the**14** orphaned UI files, or keep each with a reason: `AgentOrgChart.jsx`, `AgentTree.jsx`, `Erd/ErdPanel.jsx`, `ExecutionSwimlane.jsx`, `InfoHint.jsx`, `LimitedMarkdown.jsx`, `MarkdownEditor.jsx`, `PythonCodeBlock.jsx`, `TableDashboardDialog.jsx`, `WizardLayout.jsx`, `ui/dropdown-menu.jsx`, `ui/separator.jsx`, `ui/tabs.jsx`, `lib/appConfig.js`. |
| RET-04 | MUST     | A frontend reachability test that**bites**, mirroring `backend/tests/test_reachability.py`. Fails on an export with no caller and no recorded keep-reason.                                                                                                                                                                                                                                      |
| RET-05 | SHOULD   | Fix the stale AI-helper-layer reference at`ai/tool_registry.py:206`.                                                                                                                                                                                                                                                                                                                                  |
| RET-06 | MUST     | Evidence-led, one tranche, suite green before and after. Baseline 361 passed / 1 skipped.                                                                                                                                                                                                                                                                                                               |

### 7.2 Architecture and implementation approach

**RET-04 is built first**, because it is the instrument the rest of the step is measured with, and
because R-02 makes the second pass machine-checked rather than eyeballed.

**RET-04 is a genuinely new mechanism, not a port.** `backend/tests/test_reachability.py` is purely
static and textual: it regexes `app.include_router(x.router)` out of `main.py`'s source, globs
`routers/*.py`, and cross-checks a `KEEP_REASONS` dict against `docs/0.4.0/08-helper-layer.md`
(:30-61). That shape does not transfer — the frontend has no "mounted router" list. The frontend
equivalent must be an **import-graph reachability walk from the JS entry point**:

- Location: `ui/scripts/check-reachability.mjs`, invoked by `npm run check:reachability`, and wired
  into `ci-local.ps1` as a **blocking** gate next to the vite build. Also runnable standalone.
- Roots: `ui/index.html` → `ui/src/main.jsx`, plus every `ui/e2e/**` spec's imports, plus
  `vite.config.*` / `tailwind.config.*` referenced files. Anything reachable from a root is live.
- Walk: parse each reachable module's static `import`/`export … from` specifiers and `import()`
  expressions; resolve them through the project's alias map (`@/` → `ui/src/`) and the extension
  resolution vite uses. Record, per module, the set of **named bindings actually imported**.
- Two failure classes, both blocking:
  1. **Unreachable file** — a file under `ui/src/**` that no reachable module imports, with no
     keep-reason.
  2. **Uncalled export** — a named export of a reachable module that no reachable module imports, with
     no keep-reason. Applied to `ui/src/api/client.js` at minimum; the check is written generically and
     enabled for `client.js` and `ui/src/lib/**` in this release (widening it further is a later
     decision, not a silent scope grab).
- `KEEP_REASONS` lives in a single JSON/JS map beside the script, keyed by `path` or
  `path#exportName`, each value a non-empty reason string. Mirroring the backend's three tests, the
  script also fails when: a keep-reason names a path/export that no longer exists (the keep-list must
  not rot), and when a keep-reason string is empty.
- Cross-check, mirroring `test_keep_reasons_are_recorded_in_the_helper_doc`: every keep-reason must
  also be recorded in `docs/0.5.0/01-ret-census.md`, so the reason is reviewable outside the code.

**Sequencing — the two-pass rule (P-13, R-02). Do not compress this.**

```
1.2  Land the RET-04 walker + keep-list mechanism, with today's tree passing
     (i.e. the current 74 + 14 recorded as TEMPORARY keep-reasons that this step removes).
1.3  PASS 1 — delete RET-01's 65 exports and RET-03's 14 files, in one tranche.
1.4  RE-RUN the walker. It will now report the transitively-dead set
     (confirmed at minimum: useErdModel.js -> patchRelations).
     Delete / decide that set. Re-run again. Repeat until the walk is a FIXED POINT.
1.5  ONLY NOW decide RET-02's nine, against the fixed-point graph.
```

**RET-02 decision rubric** — apply per export, record the outcome and the reason in
`docs/0.5.0/01-ret-census.md`:

| Export                                                  | Signal to check                                                                       | Likely disposition (verify, do not assume)                                                                                                                                          |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uploadItemFileV2`                                    | `DataSourcing.jsx` imports `uploadItemFileV2WithProgress`, not this one           | **Wire or delete.** If the progress variant fully supersedes it, delete; S4's upload step should use exactly one uploader.                                                    |
| `patchItemV2`                                         | `PATCH /items/{item_id}` exists ([v2.py:87](../../../source-codes/backend/routers/v2.py#L87)) | **Wire.** AST-04's alias rename needs a PATCH; S3 is its caller. Keep with the reason "AST-04 rename lands in S3" if S1 lands first.                                          |
| `createIssueAnalysesV2`, `interpretIssueAnalysesV2` | routes live at v2.py:250, :258                                                        | Decide: wired into`IssueRca.jsx`/`IssueManagement.jsx` or a recorded keep-reason.                                                                                               |
| `getFrameworkTestsV2`, `getAgentsV2`                | routes live at v2.py:315, :325                                                        | Likely keep-with-reason (framework/agent surfaces are read-only diagnostics), or delete if nothing plans to call them.                                                              |
| `getResultTagsV3`, `getTestTagsV3`                  | v3 tag routes                                                                         | Decide against`TagPicker.jsx`'s actual usage.                                                                                                                                     |
| `setToken`                                            | called by the login flow? verify                                                      | If`login()` sets the token internally, `setToken` is a **local helper that should stop being exported**, not a deletion — un-export it and the walker stops counting it. |

**RET-05.** Verified 3 Aug 2026: `ai/tool_registry.py:206` sits **inside the docstring of
`_fetch_schema_stats`**, in a paragraph headed "Root-cause fix (Phase 2 / PLT-08)" that explains the
function *used to* call `routers.ingestion.record_counts` and was rewired away from it. A grep of the
whole backend confirms **no live import or call of `routers.ingestion` exists anywhere** — the only
other hits are `main.py:15`'s real four-router import and test imports. So RET-05 is **not a broken
dependency; it is a docstring a reader can mistake for a current data source.** The fix is therefore:
(a) re-verify the grep and record it; (b) rewrite the paragraph so the historical note is
unambiguously historical (one sentence: "Historical note — before 0.4.0 Phase 2 this read from a
router and a warehouse loader that no longer exist; it now reads `system_db.table_metadata`."); (c)
add the same one-line check to the RET-04 census: no docstring in `backend/**` names a module that
does not exist. Do **not** silently delete the note — the root-cause record is worth keeping.

**RET-06.** One tranche for the deletions (1.3), then the fixed-point passes. `pytest` before and
after, `vite build` before and after, Playwright before and after. Commit message carries the full
inventory: every deleted export, every deleted file, every keep-reason.

**Permitted paths:** `ui/src/api/client.js`, the 14 named files (deletion), any file the fixed-point
pass proves dead, `ui/scripts/**` (new), `ui/package.json` (script entry), `ci-local.ps1` (gate),
`backend/ai/tool_registry.py` (docstring only), `docs/0.5.0/**`.
**Forbidden:** any change to backend behaviour; any change to a live component's rendering.

### 7.3 Acceptance criteria

| #    | Criterion                                                                                                                                                                                          | Layer | ID             |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------------- |
| 1-A1 | `ui/src/api/client.js` has exactly `153 − 65 − (deletions from RET-02) = N` exports, N stated in the commit message; none of the removed names appears anywhere in `ui/src` or `ui/e2e`. | N     | RET-01         |
| 1-A2 | All 9 RET-02 exports have a recorded outcome — wired (with the call site named), deleted, or kept (with a reason). Zero ambiguous.                                                                | —    | RET-02         |
| 1-A3 | All 14 RET-03 files are deleted, or kept with a reason recorded in both the keep-list and the census.                                                                                              | N     | RET-03         |
| 1-A4 | `npm run check:reachability` exits 0 on the final tree, and exits **non-zero** when an uncalled export or unreachable file is introduced.                                                  | N     | RET-04         |
| 1-A5 | The walk is a**fixed point**: re-running it after the last deletion reports nothing new. The number of passes taken is recorded.                                                             | N     | RET-02, RET-03 |
| 1-A6 | `ai/tool_registry.py`'s docstring no longer reads as a current dependency on `routers.ingestion`; a grep proves no live reference exists anywhere in `backend/**`.                           | N     | RET-05         |
| 1-A7 | pytest 361 passed / 1 skipped before**and** after; vite build clean before and after; Playwright 16/1 before and after.                                                                      | —    | RET-06         |

### 7.4 Validation criteria (independent validator)

**Functional.** Boot the app and click through every route in the left nav plus every route reachable
from Data Sourcing, Test Lab, Knowledge Base, Issue Management, RCA, Inventory, Admin, Profile. Nothing
renders a blank panel, a missing-component error, or a console `Failed to resolve import`. A deletion
that breaks a lazily-rendered branch will not show up in a build — it shows up here.

**Technical / architecture.** (a) Confirm the walker resolves the `@/` alias, extensionless imports,
index files, and dynamic `import()` — a walker that silently fails to resolve a specifier reports a
false orphan, which is worse than no walker. Verify by pointing it at a known-live component and
confirming it is reported reachable. (b) Confirm the keep-list is a *single* source, cross-checked
against the census doc (mirroring the backend's `test_keep_reasons_are_recorded_in_the_helper_doc`).
(c) Confirm the gate is **blocking** in `ci-local.ps1`, not advisory.

**Automated tests to run.** `npm run check:reachability`; `npm run build`; `npm run lint`;
`cd backend; pytest tests -q`; `python backend/verify_plan8.py`; `npm run test:e2e`.

**Playwright scenarios.** No new spec is required (this step changes no behaviour), but the full
existing suite must pass unchanged — it is the proof that no runtime-only reference (a string route, a
dynamic import, a lazily-mounted dialog) was missed by the static walk. If any spec needs a change,
that is a finding: it means something live was deleted.

**Adversarial / negative-path cases.**

1. **Bites-check, uncalled export:** add `export const zzUnused = () => {};` to `client.js` → the gate
   must fail. Remove it.
2. **Bites-check, unreachable file:** add `ui/src/components/ZzOrphan.jsx` importing nothing and
   imported by nothing → the gate must fail.
3. **Bites-check, rotten keep-reason:** add a keep-reason for `ui/src/components/DoesNotExist.jsx` →
   the gate must fail (the backend's `test_kept_dead_modules_still_exist` equivalent).
4. **Bites-check, empty reason:** a keep-reason with an empty string → the gate must fail.
5. **Delete something live:** temporarily delete a genuinely-used export and confirm the vite build
   fails — establishing that the build is a real second net under the walker. Restore.
6. **Transitive proof:** verify explicitly that after `ErdPanel.jsx` is deleted, the walker reports
   `useErdModel.js`, and after that is deleted it reports `patchRelations`. If it does not report both,
   the walker is under-approximating and must be fixed before the step is accepted.
7. **False-orphan probe:** confirm the walker does **not** report `VariableInventory` (imported via the
   `@/pages/testlab/...` alias from `DataSourcing.jsx:9`) as unreachable.

### 7.5 Dependencies

None. This step depends on nothing and nothing depends on it, which is why it is first. Its only
prerequisite is Step 0's census (0.5) and a clean tree (0.3).

**Commit:** `chore(ret)!: retire 65 dead client exports + 14 orphaned components; add frontend reachability gate`

---

## 8. Step 2 — ADM + TAX: business language and reference data <a id="step-2"></a>

> Requirements §13 step 2. Self-contained fixes with no dependency on the asset model. They clear the
> defect list so it is not carried through the structural work. **ADM-06/07 are NOT here** — they
> render data that does not exist until Step 3.

### 8.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                               |
| ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ADM-01 | MUST     | Reset results reported in business language.**No database identifier appears in any user-facing reset message.**                                                                                                                                                    |
| ADM-02 | MUST     | Counts grouped into what a user recognises, in this order:*your work* (assets, uploaded files, variables, tags, issues, RCA cases, knowledge-base documents), then *platform reference data*, stated as **removed and immediately re-seeded**.                  |
| ADM-03 | MUST     | Zero counts stay hidden; a wholly empty result still reads as a**sentence**, not a fragment.                                                                                                                                                                        |
| ADM-04 | MUST     | The fix is applied**once, in the shared helper** at [Admin.jsx:131-135](../../../source-codes/ui/src/pages/Admin.jsx#L131-L135) so both reset messages improve. A table that gains a count later and has no human label must **fail loudly** rather than leak its name. |
| ADM-05 | SHOULD   | The same rule applies to the retained-data notice on the same screen, which renders raw`Object.entries(...)` pairs at [Admin.jsx:306-311](../../../source-codes/ui/src/pages/Admin.jsx#L306-L311).                                                                                |
| TAX-01 | MUST     | Add`CRE` (Commercial Real Estate) to the **Product** dimension. Product only (A-Q05).                                                                                                                                                                             |

### 8.2 Architecture and implementation approach

**ADM — the helper already exists; change its output shape, do not build a second one.**
`summarizeCounts(deleted)` at `Admin.jsx:131-135` is already the one shared helper that **both** reset
messages call (`Admin.jsx:346` surgical, `:389` full wipe). ADM-04 is therefore satisfied by changing
that function's contract, not by introducing a new abstraction. There is a **third** call site that
must also be fixed: the retained-data notice at `Admin.jsx:306-311` inlines
`Object.entries(result.deleted).map(([k,v]) => `${k}=${v}`)` and `result.reseeded` the same way — that
is ADM-05, and it is fixed by routing both through the same helper.

New helper contract:

```
summarizeReset(counts) -> {
  yourWork:  [{ label, count }],   // ADM-02 order, zero-count rows already filtered
  platform:  [{ label, count }],
  sentence:  string                // the full, human-readable message (ADM-03)
}
```

- **Label map.** A single `RESET_LABELS` map, `tableName -> { label, group: "yourWork" | "platform" }`,
  living in **one** module (`ui/src/lib/resetLabels.js` — note `lib/appConfig.js` is being deleted in
  Step 1, so do not add to it). Grouping per ADM-02:
  - *your work*: `dq_items` → "assets", `dq_item_files` → "uploaded files", `dq_item_tables` →
    (fold into "assets" detail, or "uploaded tables"), `variable_inventory` /
    `dq_item_mappings` / `dq_item_warnings` → "variables and their profiles", `tag_assignments` →
    "tags", `issues_v2` / `tracked_issues_v2` → "issues", `rca_*` → "RCA cases",
    `kb_documents` / `kb_document_versions` / `kb_sections` / `kb_rules` /
    `kb_retrieval_manifests` → "knowledge-base documents", `plan_v2` / `results_v2` / `scores_v2` /
    `diag_runs` / `diag_results` / `diag_findings` / `diag_run_decisions` / `diag_dispositions` /
    `run_results` / `health_scores` → "test results and findings", `object_contexts` /
    `context_links` → "saved workflow context".
  - *platform reference data*: `agent_skills`, `test_library`, `fw_areas`, `fw_tests`,
    `fw_family_weights`, `dq_framework_areas`, `dq_framework_families`, `diagnostic_register`,
    `framework_taxonomy`, `framework_test_areas`, `threshold_settings`, `tenants`, `tag_dimensions`,
    `tag_values`, `tag_aliases`, `tag_taxonomy_versions`, `ingested_databases`, `table_metadata`,
    `app_fsm`, `monitoring`, `tickets`, `notifications`, `schedules`, `test_plan`, `test_db_links`,
    `test_dossiers`, `test_versions`, `design_workbench`, `hitl_decisions`.
  - **Multiple tables map to one label; counts sum.** The user sees "48 variables and their profiles",
    never three rows that happen to be three tables.
- **Platform group wording (ADM-02's actual complaint).** The platform section must state
  *removed and immediately re-seeded* — because it is
  ([wipe_all_items](../../../source-codes/backend/system_db.py#L1361-L1370) calls the same seeders the boot path
  runs). Wording to ship: *"Platform reference data (framework, taxonomy, agent skills and thresholds)
  was cleared and immediately restored — the product is ready to use."* A user reading "removed 14
  agent skills" with no further explanation reasonably concludes the product is broken; that is the
  defect ADM-02 names.
- **ADM-03.** Zero counts filtered (already the case at :132). A wholly-empty result renders the
  sentence *"Nothing needed removing — there was no work data to clear."* — a sentence, not
  "nothing to remove" appended to a fragment.
- **ADM-04's "fail loudly".** Two layers, because a UI-only check cannot fail a build:
  1. **Build-time (the real gate):** `backend/tests/test_reset_language.py` enumerates every key the
     reset paths can actually produce — `_WORKPRODUCT_TABLES`
     ([system_db.py:590-601](../../../source-codes/backend/system_db.py#L590-L601)), the wipe's static list
     ([:1347-1353](../../../source-codes/backend/system_db.py#L1347-L1353)), `_rca_tables(conn)` read live off
     `sqlite_master`, plus `tag_assignments`, `ingested_databases`, `table_metadata`,
     `object_contexts`, `context_links`, `app_fsm`, and every key of the `reseeded` dict — and asserts
     each has a label in `RESET_LABELS`. The map is read from the JS module by parsing it, or the map
     is defined once in a shared JSON both sides read. **Prefer the shared JSON**
     (`ui/src/lib/resetLabels.json`, read by the JS module and by the test) so there is one source and
     no parser to maintain.
  2. **Runtime:** an unlabelled key is rendered under a generic "other platform records" line and
     logged via `console.error` in dev — it is **never** printed as `tableName=count`. The build gate
     is what stops it existing; the runtime path is what stops it leaking if it ever does.
- **Backend is not required to change.** The reset endpoint keeps returning per-table counts (PLT-05
  needs them in `transaction_log` for audit). ADM-01 governs the **user-facing message**, and the
  labelling happens where the message is composed. If the validator prefers server-side grouping, that
  is a superseding `P-nn`, not a free choice.

**TAX-01 — one additive tuple.** `backend/seeds/taxonomy_seed.py`'s `TAXONOMY_DIMENSIONS` Product entry
at [:27-32](../../../source-codes/backend/seeds/taxonomy_seed.py#L27-L32) holds exactly 8 values today. Add
`("cre", "CRE")` as a ninth. The seeder upserts on natural-key IDs ([:87-113]), so this is purely
additive: **no migration, no taxonomy version bump**, and it survives a full wipe because
`wipe_all_items` clears `tag_values` and then re-runs `seed_platform_and_taxonomy`. Per A-Q05, the
`portfolio` dimension is **not** touched — it keeps exactly its four values.

**Permitted paths:** `ui/src/pages/Admin.jsx`, `ui/src/lib/resetLabels.{js,json}` (new),
`backend/seeds/taxonomy_seed.py`, `backend/tests/**`, `ui/e2e/admin-reset.spec.js`,
`ui/e2e/taxonomy-tags.spec.js`, `docs/0.5.0/**`.
**Forbidden:** `system_db.py` (no reset-behaviour change in this step), `backend/routers/admin.py`
(the ADM-07 payload extension belongs to Step 3, P-10).

### 8.3 Acceptance criteria

| #    | Criterion                                                                                                                                                                        | Layer | ID             |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------------- |
| 2-A1 | After a surgical reset and after a full wipe, the rendered message contains**no** name present in `sqlite_master`, and no `table=count` pair.                          | N/E   | ADM-01         |
| 2-A2 | The message shows*your work* counts first, then a *platform reference data* line that states the data was cleared **and immediately restored**.                        | E     | ADM-02         |
| 2-A3 | A second consecutive reset (all-zero counts) renders a complete sentence.                                                                                                        | E     | ADM-03         |
| 2-A4 | Exactly one helper composes all three messages (surgical :346, wipe :389, retained notice :306-311). Grep proves no`Object.entries(...)=` pair-join survives in `Admin.jsx`. | N     | ADM-04, ADM-05 |
| 2-A5 | `test_reset_language.py` passes, and fails when a key is added to a reset path with no label.                                                                                  | N     | ADM-04         |
| 2-A6 | The Product dimension has exactly**9** values including `CRE`; the Portfolio dimension still has exactly **4**.                                                    | S     | TAX-01, A-Q05  |
| 2-A7 | Seeding twice yields 9 Product values, not 10. A full wipe followed by a boot yields 9 including`CRE`.                                                                         | S/M   | TAX-01         |
| 2-A8 | pytest count rises;`verify_plan8.py` 28/28; Playwright green.                                                                                                                  | —    | —             |

### 8.4 Validation criteria (independent validator)

**Functional.** On a seeded instance: run the surgical reset, read the message aloud — it must make
sense to someone who has never seen the schema. Repeat for the full wipe. Then confirm the product is
still usable immediately after the wipe (that is the claim the platform line makes). Open the tag
picker on any object and confirm `CRE` is selectable under Product and **absent** from Portfolio.

**Technical / architecture.** (a) Confirm there is one label source, not two. (b) Confirm the
label→group mapping is data, not a chain of `if`s. (c) Confirm the counts *sum* across tables that
share a label — a user must not see the same concept twice. (d) Confirm no backend behaviour changed:
`git diff` touches no `.py` outside `seeds/` and `tests/`.

**Automated tests to run/add.** New: `backend/tests/test_reset_language.py` (label completeness,
grouping order, empty-result sentence, no-table-name assertion over the composed strings);
`backend/tests/test_taxonomy_seed.py` extension (9 Product values, 4 Portfolio values, idempotence,
survives wipe+reseed). Run: full pytest, `verify_plan8.py`, `npm run test:e2e`,
`npm run check:reachability` (Step 1's gate must stay green).

**Playwright scenarios.** Extend `ui/e2e/admin-reset.spec.js`: (1) seed an item, surgical reset,
assert the message matches `/assets/i` and does **not** match `/dq_items|variable_inventory|kb_rules|=/`;
(2) reset again, assert the empty sentence; (3) full wipe, assert the platform line contains
"restored" and the app still navigates to Test Lab without error. Extend
`ui/e2e/taxonomy-tags.spec.js` to assert `CRE` appears in the Product dropdown.

**Adversarial / negative-path cases.**

1. **Unlabelled key:** add `zz_future_table` to `_WORKPRODUCT_TABLES` in a scratch branch → the ADM-04
   test must fail. Also confirm the UI, given a response containing `zz_future_table: 3`, renders it
   under "other platform records" and **never** as `zz_future_table=3`.
2. **All-zero response:** stub the API to return `{deleted: {}}` → sentence, not fragment.
3. **A count of exactly 1:** confirm singular/plural reads correctly ("1 asset", not "1 assets") —
   business language means grammatical language.
4. **A label collision:** two tables mapped to one label with counts 5 and 3 → the UI shows 8 once.
5. **TAX-01 idempotence:** call the seeder three times → still 9 Product values.
6. **TAX-01 scope:** assert `Portfolio` has no `cre` value (A-Q05's negative).
7. **Existing tag assignments:** an object already tagged with `mortgage` still resolves after CRE is
   added — the addition must not renumber or re-key anything.

### 8.5 Dependencies

- Step 1 complete and its reachability gate green — because `ui/src/lib/appConfig.js` is deleted there
  and this step must not add to it (that is why the label map goes in a new `resetLabels` module).
- Nothing from Step 3 onwards. This step must not touch `system_db.py`.

**Commit:** `fix(admin): report resets in business language; feat(taxonomy): add CRE to Product`

---

## 9. Step 3 — AST: the asset / version / snapshot model (the spine) <a id="step-3"></a>

> Requirements §13 step 3. **The spine. Steps 4–7 are all unbuildable before it.** Migration is
> additive and idempotent per PLT-06, run twice in validation. ADM-06/07 are here because they render
> exactly this data.

**Split into three commits (R-03), each leaving the product operational:**
**S3a** schema + ID scheme + migration + read models · **S3b** the service layer (create / add
snapshot / version / supersede / restore / rename) · **S3c** ADM-06/07 version history UI.

### 9.1 Scope

| ID        | Priority                  | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AST-01    | MUST                      | Every asset carries a system-generated, immutable, human-quotable ID, distinct from its display name, never re-used while any live or superseded object can reference it.**Exception:** both factory-reset grades restart issuance from a clean baseline (D-29). Distinct from `item_id`, which is untouched.                                                                                                                                                                                       |
| AST-02    | MUST                      | Naming is`<SYSTEM-ID>-<user-alias>`. The system ID is a **read-only** prefix; the user types the alias. Alias accepts letters, digits, dashes and underscores only; anything else is rejected **inline as typed**, not on submit.                                                                                                                                                                                                                                                             |
| AST-03    | MUST                      | The system ID carries the uniqueness burden, not the alias.**The global name-uniqueness rejection at [service.py:446-468](../../../source-codes/backend/ai/v2/service.py#L446-L468) and its landing-page pre-warning at [DataSourcing.jsx:191-197](../../../source-codes/ui/src/pages/DataSourcing.jsx#L191-L197) retire.**                                                                                                                                                                                               |
| AST-04    | MUST                      | The alias is editable; the ID and prefix are not. Renaming never changes the ID, never breaks a reference, and is an audited event.                                                                                                                                                                                                                                                                                                                                                                         |
| AST-05    | MUST                      | One file per upload; one file = one snapshot. Snapshots are stored separately and are**never merged or appended**.                                                                                                                                                                                                                                                                                                                                                                                    |
| AST-06    | MUST                      | A version is a**reference-schema generation**. Fresh = v1; Full replacement = v*n+1*; Add period does **not** bump.                                                                                                                                                                                                                                                                                                                                                                           |
| AST-07    | MUST                      | A version owns a reference schema: confirmed column names, column count, data type map. Later uploads validate against the**current** version's schema.                                                                                                                                                                                                                                                                                                                                               |
| AST-08    | MUST                      | A snapshot is`active` or `superseded`. Superseded snapshots are retained for audit and rollback, **not deletable, not selectable in test configuration, hidden from normal pickers**. Full replacement supersedes all previously active snapshots **as one set**.                                                                                                                                                                                                                           |
| AST-09    | MUST                      | Superseded sets are restorable**wholesale**; restoring makes that version's reference schema current again and is itself an audited version event.                                                                                                                                                                                                                                                                                                                                                    |
| AST-10    | MUST                      | The physical seam is 0.4.0's PLT-02 delivery record, re-read:`dataset_family_id` = asset identity, `delivery_seq` = snapshot sequence, `as_of_date` = period anchor. Additive, idempotent migration.                                                                                                                                                                                                                                                                                                  |
| AST-11    | MUST                      | Snapshots order by**period where dates exist, otherwise by upload timestamp** — well-defined because AST-22 fixes one time basis per asset. A mixed set is impossible **by construction**.                                                                                                                                                                                                                                                                                                     |
| AST-12    | MUST                      | Any**active** snapshot is selectable as reference or current in test configuration. Period slicing within a snapshot is a test-layer concern.                                                                                                                                                                                                                                                                                                                                                         |
| AST-13    | MUST                      | Each snapshot stores at least:`asset_id`, `asset_name`, `version_no`, `snapshot_id`, `snapshot_label`, `file_name`, `start_date`, `end_date`, `has_time_period`, `period_column`, `column_type_map`, `row_count`, `column_count`, `status (active/superseded)`, `intent`, `schema_override_flag`, `uploaded_by`, `uploaded_at`.                                                                                                                                         |
| AST-14    | MUST                      | On Full replacement the reference schema updates to the new snapshot's confirmed type map, and**the previous schema is retained against the superseded version** so AST-09 rollback is exact.                                                                                                                                                                                                                                                                                                         |
| AST-15    | MUST                      | 0.4.0's ingestion records (confirmed mapping, dictionary state, structured warnings, profile snapshot — ING-08) continue to persist and continue to feed the Test Lab manifest. They now hang off the**snapshot**, not the item.                                                                                                                                                                                                                                                                     |
| AST-20    | PROPOSED→adopted (A-Q02) | The dictionary belongs to the**asset** and is versioned on its own track; each snapshot records which dictionary version it was ingested against.                                                                                                                                                                                                                                                                                                                                                     |
| AST-21    | SHOULD                    | `dictionary_state ∈ {yes, thin, absent}` (ING-05) is a property of the **binding** of a dictionary version to a snapshot, visible wherever the asset appears.                                                                                                                                                                                                                                                                                                                                      |
| AST-22    | MUST                      | Every asset has a**single, fixed time basis for its whole life**: period-based or no-time-period. Chosen once at Fresh Upload, immutable afterwards — **including across a Full replacement** (C-48/D-30). Add period and Full replacement never re-ask the mode question. This exists because AST-11's ordering rule has **no defined answer** for a set mixing dated and undated snapshots: fixing the basis at creation makes the mixed case impossible rather than merely unhandled. |
| ADM-06    | MUST                      | Admin gains a**version history view**: for any asset, its full version sequence and, within each version, its snapshots (active and superseded), each with timestamp, actor, intent, and a one-line summary of what changed.                                                                                                                                                                                                                                                                          |
| ADM-07    | SHOULD                    | The same view lists the two factory-reset actions with timestamp and counts, making D-29's epoch boundary legible (A-Q06 bounds it to assets + resets).                                                                                                                                                                                                                                                                                                                                                     |
| ANL-01/02 | PROPOSED→adopted (A-Q04) | Typed, stable, never-reused IDs for the**asset graph only**: asset, version, snapshot, variable, dictionary version.                                                                                                                                                                                                                                                                                                                                                                                  |

### 9.2 Architecture — the schema

**Task 3.1 first, and it is mandatory: the seam consumer map (R-01).** Before a single column is
added, read `backend/dq_diagnostics/delivery.py` in full, `verify_plan8.py:120-150`,
`system_db.py:766-768` and `:1050-1060`, and grep the tree for `as_of_date`, `baseline_delivery_id`,
`dataset_family_id`, `delivery_seq`. Record every consumer in `docs/0.5.0/02-asset-model.md §Seam`.
The facts this plan already establishes, and which the map must confirm rather than re-derive:
`as_of_date` is written by `register_delivery` and read by no product logic; `baseline_delivery_id` is
written and read **nowhere** but is asserted present by `verify_plan8.py:136`; `family_deliveries`
orders by `delivery_seq`, **not** by `as_of_date` — so AST-11's period-first ordering is new behaviour.
If the map contradicts any of these, **stop and re-plan this step** rather than proceeding.

**New tables — appended to `_SCHEMA` as `CREATE TABLE IF NOT EXISTS` blocks (operating rule 3).**

```sql
-- AST-01..04, AST-22, A-Q02, A-Q04. asset_id is the SAME value as
-- dq_items.dataset_family_id (P-04) — the PLT-02 seam, re-read as identity.
CREATE TABLE IF NOT EXISTS dq_assets (
    asset_id TEXT PRIMARY KEY,          -- == dq_items.dataset_family_id (AST-10)
    system_id TEXT NOT NULL,            -- AST-01/P-02: DS0007 / DB0003, UNIQUE index below
    alias TEXT NOT NULL,                -- AST-02: [A-Za-z0-9_-]+, user-editable (AST-04)
    display_name TEXT NOT NULL,         -- P-03: '<system_id>-<alias>', derived, never parsed apart
    kind TEXT NOT NULL,                 -- 'database' | 'dataset'
    time_basis TEXT NOT NULL,           -- AST-22: 'period' | 'none'. IMMUTABLE (D-30)
    current_version_no INTEGER NOT NULL,
    lifecycle_status TEXT,              -- C-43: the EXISTING lifecycle vocabulary, no new values
    target_variable TEXT,               -- CTX-04/06 live here at asset level
    use_case TEXT,
    current_dictionary_version_id TEXT, -- AST-20
    created_by TEXT, created_at TEXT, updated_at TEXT
);
-- AST-06/07/14. One row per reference-schema generation.
CREATE TABLE IF NOT EXISTS dq_asset_versions (
    version_id TEXT PRIMARY KEY,        -- typed ID, A-Q04
    asset_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,               -- 'current' | 'superseded'
    reference_schema_json TEXT,         -- AST-07: {tables:{<t>:{columns:[...], column_count:n,
                                        --   types:{col:type}}}} — per-table for a Database (A-Q03)
    created_from_snapshot_id TEXT,      -- the snapshot whose confirmed map became this schema
    supersedes_version_no INTEGER,      -- AST-14: what this generation replaced
    restored_from_version_no INTEGER,   -- AST-09: set when this row exists because of a restore
    created_by TEXT, created_at TEXT,
    superseded_at TEXT
);
-- AST-20/21 (A-Q02). The dictionary versions on its OWN track.
CREATE TABLE IF NOT EXISTS dq_asset_dictionaries (
    dictionary_version_id TEXT PRIMARY KEY,   -- typed ID, A-Q04
    asset_id TEXT NOT NULL,
    dict_version_no INTEGER NOT NULL,
    source_file_id TEXT,                      -- dq_item_files.file_id it was parsed from
    parsed_json TEXT,                         -- _parse_dictionary's output, frozen
    created_by TEXT, created_at TEXT
);
-- ADM-06/07, AST-04/08/09 audit. The asset lifecycle audit-of-record (P-08).
CREATE TABLE IF NOT EXISTS dq_asset_events (
    event_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL, version_no INTEGER, snapshot_id TEXT,
    event_type TEXT NOT NULL,   -- asset_created | alias_renamed | snapshot_added |
                                -- version_created | version_superseded | version_restored |
                                -- schema_override | dictionary_version_bound
    actor TEXT, at TEXT NOT NULL,
    summary TEXT,               -- ADM-06's one-line "what changed", human language (M-2)
    detail_json TEXT
);
-- AST-01 / D-29 / P-10. The human-quotable ID counter, per scope.
CREATE TABLE IF NOT EXISTS id_sequences (
    scope TEXT PRIMARY KEY,     -- asset_dataset | asset_database | snapshot | version |
                                -- dictionary_version   (exactly five — A-Q04)
    next_value INTEGER NOT NULL,
    epoch_started_at TEXT
);
```

Plus unique indexes, created in `init_schema()` beside the existing `CREATE … IF NOT EXISTS` index
block ([system_db.py:927-940](../../../source-codes/backend/system_db.py#L927-L940)):

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_assets_system_id     ON dq_assets(system_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_asset_versions_no    ON dq_asset_versions(asset_id, version_no);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_asset_dict_no        ON dq_asset_dictionaries(asset_id, dict_version_no);
CREATE        INDEX IF NOT EXISTS ix_dq_asset_events_asset   ON dq_asset_events(asset_id, at);
-- AST-22 + UPL-14: a snapshot label must be unique WITHIN an asset when the
-- asset's basis is 'none'. Partial index on the family, not global (AST-03).
CREATE UNIQUE INDEX IF NOT EXISTS ux_dq_items_label_in_family
    ON dq_items(dataset_family_id, snapshot_label) WHERE snapshot_label IS NOT NULL;
```

The unique index on `system_id` is the belt to the counter's braces: a race in ID allocation must fail
loudly, not produce two `DS0007`s (AST-01).

**New `dq_items` columns — one `_MIGRATIONS["dq_items"]` entry, extending the existing dict at
[system_db.py:766-768](../../../source-codes/backend/system_db.py#L766-L768). Nothing is dropped or renamed
(P-04, rule 4).**

| Column                       | DDL         | Requirement    | Note                                                                                  |
| ---------------------------- | ----------- | -------------- | ------------------------------------------------------------------------------------- |
| `version_no`               | `INTEGER` | AST-06, AST-13 | which reference-schema generation this snapshot belongs to                            |
| `snapshot_status`          | `TEXT`    | AST-08, P-07   | `active` \| `superseded`. A third **axis**, not a third vocabulary          |
| `snapshot_label`           | `TEXT`    | AST-13, UPL-15 | unique within the asset when basis is`none` (partial index above)                   |
| `start_date`               | `TEXT`    | AST-13, UPL-14 | period-based only                                                                     |
| `end_date`                 | `TEXT`    | AST-13, UPL-14 | period-based only                                                                     |
| `has_time_period`          | `INTEGER` | AST-13         | denormalised from`dq_assets.time_basis`; AST-22 guarantees it is constant per asset |
| `period_column`            | `TEXT`    | AST-13, UPL-16 | metadata only;**no split at upload** (OOS-16)                                   |
| `column_type_map_json`     | `TEXT`    | AST-13, UPL-13 | the**confirmed** map, per table for a Database (A-Q03)                          |
| `row_count`                | `INTEGER` | AST-13         | workbook total for a Database (A-Q03)                                                 |
| `column_count`             | `INTEGER` | AST-13         | workbook total for a Database; per-table detail stays in`dq_item_tables`            |
| `file_name`                | `TEXT`    | AST-13         | the uploaded file's name                                                              |
| `intent`                   | `TEXT`    | AST-13, UPL-17 | `fresh` \| `add_period` \| `full_replacement`                                   |
| `schema_override_flag`     | `INTEGER` | AST-13, UPL-22 | set when the user ticked UPL-22's confirmation                                        |
| `uploaded_by`              | `TEXT`    | AST-13         | actor                                                                                 |
| `dictionary_version_id`    | `TEXT`    | AST-20         | which dictionary version this snapshot was ingested against                           |
| `superseded_at`            | `TEXT`    | AST-08         | audit                                                                                 |
| `superseded_by_version_no` | `INTEGER` | AST-08         | which generation superseded this set                                                  |

**Deliberately NOT added** (P-06): no `snapshot_id` column (`item_id` **is** the snapshot id, exposed
under that name in read models), no `uploaded_at` column (`created_at` is it), no `asset_id` column on
`dq_items` (`dataset_family_id` is it — AST-10), no `asset_name` column (`name` already mirrors
`display_name` per P-05). AST-13's field list is satisfied by the **read model**, and the read model's
field names must match AST-13's names exactly so a reviewer can check them one-to-one.

**Backfill — `_backfill_asset_model(conn)`, called from `init_schema()` after
`_backfill_ingest_defaults` (system_db.py:926).** Same discipline as its two predecessors: touch only
rows that have never been backfilled, so a second run is a no-op.

For every distinct `dataset_family_id` in `dq_items` with no `dq_assets` row:

1. Pick the family's lowest-`delivery_seq` row as the origin. Create the `dq_assets` row:
   `asset_id = dataset_family_id`; `kind`, `name`, `target_variable`, `use_case` from the origin row;
   `alias` = the origin row's existing `name`, **sanitised** to `[A-Za-z0-9_-]+` (spaces → `-`, other
   characters dropped; if the result is empty, `asset`); `system_id` allocated from `id_sequences`
   for that kind; `display_name = f"{system_id}-{alias}"`; `current_version_no = 1`;
   `time_basis` = `'period'` if **any** row in the family has a non-null `as_of_date`, else `'none'`
   (AST-22 — a pre-0.5.0 family cannot have mixed intent, so this is a total function);
   `lifecycle_status` from the origin row's `status`.
2. Update **every** row in the family: `version_no = 1`, `snapshot_status = 'active'`,
   `intent = 'fresh'` for the lowest seq and `'add_period'` for the rest (P-05's most honest reading:
   pre-0.5.0 siblings were additional deliveries, and reading them as replacements would retroactively
   supersede data the user can still see), `has_time_period` from the asset's basis,
   `start_date = as_of_date`, `snapshot_label` = `as_of_date` or `f"Delivery {delivery_seq}"`,
   `name = display_name` (P-05), `row_count`/`column_count` summed from `dq_item_tables`,
   `file_name` from the newest `dq_item_files` row with `role='data'`, `uploaded_by = NULL`
   (unknowable — do not invent an actor).
3. Create one `dq_asset_versions` row: `version_no = 1`, `status = 'current'`,
   `reference_schema_json` reconstructed from `dq_item_tables.columns` + `variable_inventory.data_type`
   for the origin snapshot (that is the honest available answer; if `variable_inventory` is empty,
   leave `reference_schema_json` NULL and record it — a never-profiled item has no reference schema
   and pretending otherwise would make UPL-20 compare against a fiction).
4. Create one `dq_asset_events` row: `event_type='asset_created'`, `summary='Migrated from the 0.4.0 item model.'`, `actor=NULL`.
5. **Drift repair (R-04):** for every family that already has a `dq_assets` row, re-assert
   `dq_items.name = dq_assets.display_name`. Idempotent by construction.

**Task 3.2 — the ID scheme (AST-01, AST-02, P-02, P-10, D-29).** New module
`backend/assets/identity.py`:

- `allocate(scope) -> str`: inside one `get_conn()` transaction, `INSERT OR IGNORE` the scope row with
  `next_value=1`, `SELECT next_value`, `UPDATE next_value = next_value + 1`, return the formatted ID.
  Formats: `asset_dataset → DS%04d`, `asset_database → DB%04d`, `snapshot → SN%06d`,
  `version → VR%05d`, `dictionary_version → DV%05d`. Width grows automatically past the pad
  (`DS10000` is fine) — the regex in P-02 is `{4,}` for exactly this reason.
- `validate_alias(alias) -> str`: `^[A-Za-z0-9_-]+$`, non-empty, length-bounded (say 1–60). Raises a
  message the UI can show verbatim. The UI enforces the same rule **as the user types** (AST-02) and
  the server re-validates — never trust the client (the same discipline as the factory reset's
  server-side confirm check, admin.py:166-170).
- `compose_display_name(system_id, alias) -> str`: `f"{system_id}-{alias}"`. There is **no**
  `parse_display_name` (P-03). If you find yourself writing one, you have a design error.
- **Epoch reset (P-10).** Add `id_sequences` to the delete lists of **both**
  `reset_demo()` and `wipe_all_items()`. Surgical reset removes items, so its asset IDs are freed and
  issuance restarts — that is AST-01's exception verbatim. Then extend the reset endpoint's audit row
  ([admin.py:178-181](../../../source-codes/backend/routers/admin.py#L178-L181)) so its payload carries
  `id_epoch: {scope: <next_value immediately before the delete>}`. The audit row is written **after**
  the delete, as it already is, and that is precisely what makes it the epoch boundary (D-29). Read
  the counters before the delete and pass them through.

**Task 3.3 — the service layer (S3b).** New module `backend/assets/service.py`, and the rewrites
FNC-01 names. The v2 route paths are preserved; the handlers and the service functions are rewritten
in place (P-14). No compatibility shim.

| Function                                                                                                    | Today                                                                                                                                                              | 0.5.0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Requirement                                        |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `create_item(kind, name)` [service.py:443](../../../source-codes/backend/ai/v2/service.py#L443)                     | Rejects a globally duplicate case-insensitive name (:451-468); resumes a`requires_reupload` row in place (:453-467)                                              | **→ `assets.create_asset(kind, alias, time_basis, actor)`.** Allocates `system_id`, validates the alias, inserts `dq_assets` + `dq_asset_versions` v1 (`reference_schema_json` NULL until STEP 3 confirms a map) + an `asset_created` event. **The whole uniqueness loop at :451-468 is deleted** (AST-03). The `requires_reupload` resume branch is **not** re-created here — reaching such an asset is now STEP 1(b)'s job (SRC-13), and its resolution is `add_snapshot`. `time_basis` is a **required** argument and is written once, never updated (AST-22, D-30).                                                                                                                                                                                                                                       | AST-01, AST-02, AST-03, AST-22                     |
| `reupload_item(existing_item_id, as_of_date)` [service.py:482](../../../source-codes/backend/ai/v2/service.py#L482) | Inserts a**sibling item row** with its own `item_id` and the same `kind`+`name`, then `register_delivery` — "a new item in the same dataset family" | **→ `assets.add_snapshot(asset_id, intent, actor, **period_fields)`.** FNC-01's single most affected function. Inserts a `dq_items` row that is a **snapshot of the same asset**: `dataset_family_id = asset_id` (unchanged seam), `delivery_seq` from `delivery.register_delivery` (**that function is not modified** — R-01), `name = asset.display_name` (P-05), `version_no` = `asset.current_version_no` for `add_period` or `current+1` for `full_replacement`, `snapshot_status='active'`, `intent`, `has_time_period` from the asset's basis. For `full_replacement` it additionally calls `supersede_version_set` and inserts a new `dq_asset_versions` row (AST-06, AST-08, AST-14). The old snapshot's files, inventory, mappings and warnings are **untouched** (AST-08, rule 8). | AST-05, AST-06, AST-08, AST-10, AST-13, C-40, D-25 |
| `finalize_item(item_id, target, use_case)` [service.py:630](../../../source-codes/backend/ai/v2/service.py#L630)    | Unconditionally updates target/use_case on the item row; re-profiles if the target changed                                                                         | **Writes target/use_case to `dq_assets`, not to the snapshot** (they are asset properties), and becomes **intent-conditional**: cleared on `fresh` and `full_replacement`, carried forward on `add_period`. Full logic lands in Step 6 (CTX-04/CTX-06); Step 3 moves the *storage location* and keeps behaviour identical so nothing breaks in between.                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | AST-13, CTX-04, CTX-06, D-24                       |
| `_write_table` [service.py:142](../../../source-codes/backend/ai/v2/service.py#L142)                                | Writes the frame to the item's sqlite cache and upserts`dq_item_tables` keyed by `(item_id, table_name)`                                                       | Key semantics change from "the item's tables" to "**this snapshot's** tables" — the key is already `(item_id, table_name)` and `item_id` is now the snapshot id (P-06), so **no schema change is needed**; what changes is that it must also contribute `row_count`/`column_count` totals up to the snapshot row (AST-13) and must never overwrite a **superseded** snapshot's cache (rule 9 — assert `snapshot_status='active'` before writing).                                                                                                                                                                                                                                                                                                                                                                           | AST-05, AST-13, AST-15                             |
| `_parse_dictionary` [service.py:222](../../../source-codes/backend/ai/v2/service.py#L222)                           | Parses the item's dictionary file into a nested dict, hard-failing on a duplicate declaration within one table (:205-219)                                          | Its output is now**frozen into a `dq_asset_dictionaries` row** (a new dictionary version) rather than recomputed per profile, and the snapshot records `dictionary_version_id`. Editing the dictionary creates a new dictionary version and **never bumps the data version** (AST-20, A-Q02). The duplicate-declaration hard-fail at :205-219 stays exactly as it is — note it is already correctly **per-table**, which is the same shape UPL-12 needs.                                                                                                                                                                                                                                                                                                                                                                           | AST-20, AST-21                                     |
| `_column_profile` [service.py:336](../../../source-codes/backend/ai/v2/service.py#L336)                             | Returns dtype, cardinality, null_share, min/max/mean, top-5                                                                                                        | Extended in**Step 4** to the tier-③ statistic set (adds `stddev`, a fixed-bin histogram for numerics, a hash of the sorted distinct set for low-cardinality columns) — see P-01. Step 3 only records the contract in `docs/0.5.0/02-asset-model.md` so Step 4 has a target.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | AST-16, AST-17                                     |
| `profile_item` [service.py:668](../../../source-codes/backend/ai/v2/service.py#L668)                                | Parses, maps, warns, writes`variable_inventory`, sets dictionary state and derived status                                                                        | Step 3: reads its item as a**snapshot** and refuses to profile a superseded one (rule 9). Step 4: computes and stores the fingerprint and orchestrates the schema-conflict check for both intents.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | AST-15, AST-17                                     |
| `ingest_summary` [service.py:877](../../../source-codes/backend/ai/v2/service.py#L877)                              | Returns the ING-06/07/08 payload                                                                                                                                   | Step 4: extended with the UPL-27 completion-summary fields. Step 3: unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | UPL-27                                             |
| `list_items` [service.py:595](../../../source-codes/backend/ai/v2/service.py#L595)                                  | One row per item                                                                                                                                                   | **New read models beside it, not instead of it** (rule 1): `assets.list_assets(kind, exclude_lifecycle=[...])` returns one row per **asset** with the SRC-05 columns; `assets.ordered_snapshots(asset_id, only_active=True)` implements AST-11's ordering. `list_items` itself keeps working for every 0.4.0 caller.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | SRC-05, AST-11, AST-12                             |

**`supersede_version_set` / `restore_version_set` (P-11).**

- `supersede_version_set(asset_id, version_no, actor)`: sets every `dq_items` row of the asset whose
  `snapshot_status='active'` to `'superseded'`, with `superseded_at` and
  `superseded_by_version_no = <new version>`; sets the old `dq_asset_versions` row `status='superseded'`
  with `superseded_at`. **One statement per table, one transaction — never a per-snapshot loop exposed
  to callers.** Writes one `version_superseded` event whose `summary` names how many snapshots and
  which periods/labels (this is also exactly what UPL-18 must show *before* confirming, so the same
  helper computes the preview).
- `restore_version_set(asset_id, version_no, actor)`: reactivates that version's snapshot set
  wholesale, supersedes whatever set is currently active, sets `dq_asset_versions.status='current'` on
  the restored row, and — because AST-09 says restoring **is itself a version event** — writes a
  `version_restored` event and, per AST-09's "makes its reference schema current again", points
  `dq_assets.current_version_no` at the restored generation. It does **not** invent a new version
  number: restoring v1 makes v1 current again, and the audit trail (not a renumbering) is what records
  that it happened twice.
- `rename_alias(asset_id, new_alias, actor)`: validates, updates `dq_assets.alias` + `display_name`,
  mirrors to every `dq_items.name` of the asset (P-05), writes an `alias_renamed` event. **`system_id`
  is never touched** (AST-04).

**AST-11's ordering, and why it is safe.** `assets.ordered_snapshots` sorts by `start_date` when the
asset's `time_basis='period'` and by `created_at` when it is `'none'`. There is no branch for a mixed
set because AST-22 makes one impossible **by construction** — and that is an invariant to *prove*, not
to trust: the negative test force-inserts a dated snapshot into a `time_basis='none'` asset directly in
SQL and asserts the read model **raises** rather than silently interleaving (see §9.5).

**AST-12 / AST-08 enforcement point.** Superseded snapshots must be "not selectable in test
configuration, and hidden from normal pickers". The single enforcement point is
`assets.ordered_snapshots(only_active=True)` plus a guard in the Test Lab's manifest creation path
(`POST /items/{item_id}/diagnostics/manifest`, [v2.py:471](../../../source-codes/backend/routers/v2.py#L471))
that refuses a snapshot whose `snapshot_status='superseded'` with an explicit message. **Read
`backend/dq_diagnostics/manifest.py` and `readiness.py` before wiring this** — the exact guard location
depends on where the item id is resolved, and this plan deliberately does not assert their internals.

**Task 3.4 — ADM-06/07 version history (S3c).**

- API: `GET /api/admin/assets/{asset_id}/history` → `{asset: {...}, versions: [{version_no, status, reference_schema_summary, created_at, created_by, snapshots: [{snapshot_id, snapshot_label, period, intent, snapshot_status, uploaded_at, uploaded_by, change_summary}]}], resets: [...]}`. Admin-only,
  through the existing `_require_admin` + `_plt04_sanitize` pattern.
- The version/snapshot content comes from `dq_asset_versions` + `dq_items` + `dq_asset_events`; the
  `change_summary` is the event's `summary` string, which is written in human language at write time
  (M-2) — the API never composes prose from enum values at read time.
- **ADM-07 / R-08:** `resets` comes from `transaction_log WHERE event='factory_reset'`, each with
  timestamp, actor, grade, the ADM-02-grouped counts (reuse Step 2's label map — do **not** build a
  second one) and the `id_epoch` payload P-10 added. They are rendered **inline, in one chronological
  list with the version events**, so the epoch boundary between two `DS0001`s is visually unmissable.
- UI: a new section in `ui/src/pages/Admin.jsx` (or `ui/src/pages/admin/VersionHistory.jsx` imported
  by it) with an asset picker and the chronological list. Every status renders through a human label
  (M-2) — `superseded` shows as "Superseded (retained for audit)", never as the raw token.
- A-Q06 bounds it: assets + resets only. No user-creation or taxonomy-edit history.

**Permitted paths:** `backend/system_db.py` (additive only), new `backend/assets/**`,
`backend/ai/v2/service.py`, `backend/routers/v2.py`, `backend/routers/admin.py`,
`backend/dq_diagnostics/manifest.py` + `readiness.py` (guard only), `ui/src/pages/Admin.jsx`,
`ui/src/pages/admin/**` (new), `ui/src/api/client.js`, `backend/tests/**`, `ui/e2e/**`,
`docs/0.5.0/**`.
**Forbidden:** `backend/dq_diagnostics/delivery.py` (R-01 — it stays as it is);
`ui/src/pages/DataSourcing.jsx` (that is Step 4/5); any `DROP`/`RENAME` DDL.

### 9.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                                               | Layer | ID                     |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ---------------------- |
| 3-A1  | A fresh asset gets a`system_id` matching `^(DS\|DB)[0-9]{4,}$`, a `display_name` of `<system_id>-<alias>`, and `version_no=1`. Two assets created with the **same alias** both succeed and differ only by prefix.                                                                                                                                        | S/N   | AST-01, AST-02, AST-03 |
| 3-A2  | The global name-uniqueness rejection is**gone**: `service.py` contains no case-insensitive scan of `dq_items.name`, and creating two assets with identical aliases raises nothing.                                                                                                                                                                            | N     | AST-03                 |
| 3-A3  | An alias containing anything outside`[A-Za-z0-9_-]` is rejected by the service with a showable message; an empty alias is rejected.                                                                                                                                                                                                                                   | N     | AST-02                 |
| 3-A4  | Renaming an alias changes`display_name` and every `dq_items.name` of that asset, leaves `system_id`, `asset_id` and every `item_id` untouched, and writes an `alias_renamed` event.                                                                                                                                                                         | S/N   | AST-04, P-05           |
| 3-A5  | `add_snapshot(intent='add_period')` creates one new `dq_items` row on the **same** `dataset_family_id`, `delivery_seq+1`, **same** `version_no`, and leaves the prior snapshot byte-identical.                                                                                                                                                    | S/N   | AST-05, AST-06, AST-10 |
| 3-A6  | `add_snapshot(intent='full_replacement')` creates `version_no+1`, a new `dq_asset_versions` row carrying the new reference schema, retains the previous schema against the superseded version, and supersedes **all** previously active snapshots as one set with one shared `superseded_by_version_no`. Nothing is deleted; every file is still on disk. | S/N/M | AST-06, AST-08, AST-14 |
| 3-A7  | `restore_version_set` reactivates a superseded set wholesale, makes its reference schema current, supersedes the previously-active set, and writes a `version_restored` event.                                                                                                                                                                                      | S     | AST-09                 |
| 3-A8  | A superseded snapshot cannot be selected in test configuration: the manifest endpoint refuses it with an explicit message, and it is absent from`ordered_snapshots(only_active=True)`.                                                                                                                                                                                | A/N   | AST-08, AST-12         |
| 3-A9  | `time_basis` is set once and is immutable: any attempt to change it — service call or API — is refused, including as part of a `full_replacement`.                                                                                                                                                                                                                | N     | AST-22, D-30           |
| 3-A10 | `ordered_snapshots` orders a `period` asset by `start_date` and a `none` asset by `created_at`; a force-constructed mixed set **raises**.                                                                                                                                                                                                               | U/N   | AST-11                 |
| 3-A11 | The read model for a snapshot exposes**every** AST-13 field name, one-to-one, with `snapshot_id`←`item_id` and `uploaded_at`←`created_at`.                                                                                                                                                                                                              | U     | AST-13, P-06           |
| 3-A12 | 0.4.0's ingestion records (`dq_item_mappings`, `dq_item_warnings`, `variable_inventory`, profile JSON) still persist and still feed the Test Lab manifest, now keyed to the snapshot. The 0.4.0 manifest contract test still passes.                                                                                                                              | S/P   | AST-15                 |
| 3-A13 | Editing a dictionary creates a new`dq_asset_dictionaries` row and does **not** change any `dq_asset_versions.version_no`; each snapshot records the dictionary version it was ingested against.                                                                                                                                                               | S/N   | AST-20, A-Q02          |
| 3-A14 | Migration is idempotent: run`init_schema()` twice on a populated pre-0.5.0 DB — identical canonical schema hash and identical hash over all surviving rows. Every pre-existing family becomes exactly one asset with one v1 and all its rows active.                                                                                                                 | M     | AST-10, PLT-06         |
| 3-A15 | `verify_plan8.py` exits 0 — all four PLT-02 columns still present.                                                                                                                                                                                                                                                                                                   | —    | R-01, rule 4           |
| 3-A16 | Surgical reset and full wipe both clear`id_sequences`; the next asset created afterwards is `DS0001` again; the `transaction_log` `factory_reset` row carries the pre-reset counters.                                                                                                                                                                           | S/M   | AST-01, D-29, P-10     |
| 3-A17 | ADM-06: for an asset with 2 versions × 3 snapshots the history view shows 2 version groups with every snapshot, each carrying timestamp, actor, intent and a one-line change summary; superseded snapshots appear, labelled as retained.                                                                                                                               | A/E   | ADM-06                 |
| 3-A18 | ADM-07: both reset actions appear in the same chronological list with timestamp and ADM-02-grouped counts, and exactly one boundary row sits between two same-ID assets from different epochs.                                                                                                                                                                          | A/E   | ADM-07, R-08           |
| 3-A19 | Suite green; count recorded; the 0.4.0 tests that encoded sibling-row re-upload (`test_ingest.py:325-340`, `test_delivery.py` family semantics) are **rewritten in this commit** with the new assertions stated.                                                                                                                                              | —    | rule 7, RET-06         |

### 9.4 Validation criteria (independent validator)

**Functional.** On a **populated pre-0.5.0** DB (restore the Step 0 backup, or seed one): boot, and
confirm every existing item still appears in Inventory and Test Lab with a sensible name, still reaches
`ready`, still runs the cross-field diagnostic, and still shows its issues. Then via the API: create an
asset, add a period snapshot, do a full replacement, restore the previous version, rename the alias.
After each, open Admin's version history and confirm it reads as a plain-English account of what you
just did. Finally: surgical reset, create an asset, confirm `DS0001` reissues and the history shows the
boundary.

**Technical / architecture.** (a) Confirm **no** column was dropped or renamed and all four PLT-02
columns survive (`PRAGMA table_info(dq_items)` diffed against the pre-migration capture). (b) Confirm
`delivery.py` is unmodified. (c) Confirm the new tables are in `_SCHEMA` and the new columns in
`_MIGRATIONS` — not created by ad-hoc DDL somewhere in a service function. (d) Confirm exactly one
writer for `display_name`/`name` (grep for assignments). (e) Confirm `supersede`/`restore` are set
operations in one transaction, not loops. (f) Confirm no `parse_display_name`-shaped function exists
(P-03). (g) Confirm the read model's field names match AST-13's list literally. (h) Confirm
`id_sequences` allocation is transactional and that the `UNIQUE` index on `system_id` exists.
(i) Confirm ADM-06's `summary` strings are written in human language at **write** time, not composed
from enums at read time.

**Automated tests to run/add.** New: `backend/tests/test_asset_identity.py` (ID format, alias
validation, uniqueness burden, epoch reset), `test_asset_model.py` (versions, snapshots, supersede,
restore, ordering, AST-13 read model, time-basis immutability),
`test_asset_migration.py` (M — twice, hashes, per-family mapping, drift repair),
`test_admin_version_history.py` (A — payload shape, admin-only, reset rows).
Rewrite: `test_ingest.py`'s re-upload block, `test_delivery.py`'s family assertions.
Run: full pytest; `verify_plan8.py`; `npm run check:reachability`; `npm run build`; `npm run test:e2e`.

**Playwright scenarios.** (E-3.1) Admin → version history: pick an asset, assert two version groups
and the superseded snapshots visible with a human label. (E-3.2) Admin → surgical reset → create an
asset via the API → version history shows the reset row between the two epochs. (E-3.3) The existing
`upload-workflow.spec.js` must still pass at this step (Step 3 changes no upload UI) — if it fails,
Step 3 broke something it should not have touched.

**Adversarial / negative-path cases.**

1. **Same alias twice:** two assets both aliased `retail-pd` → both created, distinct `system_id`,
   distinct `display_name`, and both listable side by side (AST-03's whole point).
2. **Alias injection:** aliases `retail pd`, `retail/pd`, `retail.pd`, `../etc`, `retail%20pd`, `''`,
   a 500-character alias, and an emoji → each rejected with a showable message. Then confirm the
   accepted forms `a`, `retail-pd`, `retail_pd_2`, `RETAIL-PD` all pass.
3. **Time basis mutation (AST-22/D-30 bites-check):** attempt `PATCH` of `time_basis`; attempt a
   `full_replacement` that supplies the other mode; attempt a direct `UPDATE dq_assets SET time_basis=...` followed by a read → the service must refuse the first two, and the third must be
   caught by the ordering invariant in case 4.
4. **Mixed-basis set (AST-11 bites-check):** force-insert, in raw SQL, a snapshot with a `start_date`
   into a `time_basis='none'` asset → `ordered_snapshots` must **raise**, not interleave. Invert the
   guard and confirm the test fails.
5. **Half-superseded set:** attempt to supersede one snapshot of a three-snapshot set through any
   public surface → no such surface exists (P-11). Assert by API/route inspection.
6. **Delete a superseded snapshot:** no service function and no route does it (R-06). Assert
   negatively, and assert that a full replacement leaves the prior files present on disk.
7. **Restore twice:** restore v1, then restore v1 again → the second is an explicit no-op or a clear
   refusal, never a corrupted double-active state. Restore a nonexistent version → 404.
8. **ID reuse within an epoch:** create 3 assets, supersede everything, create a 4th → it must be
   `DS0004`, never a recycled `DS0002` (AST-01/ANL-02).
9. **Concurrent allocation:** two `create_asset` calls in parallel → two distinct `system_id`s, or one
   clean failure. Never a duplicate (the `UNIQUE` index must be the last line of defence, and must be
   shown to fire).
10. **Migration on a hostile DB:** a family whose origin row has `name = "  "` (whitespace) → alias
    sanitises to `asset`, never to an empty string that breaks the display name. A family with two rows
    sharing `delivery_seq` → the migration must still pick a deterministic origin. A `dq_items` row
    with `dataset_family_id IS NULL` → `_backfill_delivery_defaults` runs first and gives it a family,
    so the asset backfill sees it; verify the call order in `init_schema()`.
11. **Migration on an empty DB and on a fresh boot:** both must succeed and produce zero assets, no
    error.
12. **Admin authorization:** the history endpoint must 401/403 for a non-admin session.

### 9.5 Dependencies

- Step 1 complete (the client.js surface is stable before new calls are added; `patchItemV2` is the
  RET-02 export AST-04's rename needs, so its decision must be "wire" or "keep with reason — S3").
- Step 2 complete (the ADM-02 label map exists and ADM-07 reuses it rather than building a second one).
- Step 0's baseline capture and DB backup (3-A14 diffs against it).
- **Task 3.1's seam consumer map must be committed before task 3.2 begins.** This is the R-01
  mitigation and it is not optional.

**Commits:** `feat(assets)!: asset/version/snapshot schema, human-quotable IDs, PLT-02 seam migration`
(S3a) · `feat(assets)!: create/add-snapshot/supersede/restore/rename service layer` (S3b) ·
`feat(admin): asset version history incl. factory-reset epoch boundary` (S3c)

---

## 10. Step 4 — UPL: the seven-step upload flow <a id="step-4"></a>

> Requirements §13 step 4. Consumes the asset model directly and is where the model is first proven end
> to end. Also the step FNC-01 pins the `service.py` sheet refresh to.

**Split into three commits (R-03):** **S4a** STEP 1–3 · **S4b** STEP 4–5 · **S4c** STEP 6–7 +
fingerprint write. Each leaves the upload flow usable end to end for the intents it has reached.

### 10.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UPL-01 | MUST     | The seven moments are**sections of one progressive surface, not a seven-screen wizard.** 0.4.0 retired the wizard deliberately (D-16, D-20) and that stands. Sections reveal as their inputs become available; the user can move back to any earlier section without losing work.                                                                                                                                                                                                                             |
| UPL-02 | MUST     | **No button advances the pipeline.** Dropping the file still starts parsing and profiling immediately; no "Upload", "Finalize" or "Profile" button (ING-01 holds).                                                                                                                                                                                                                                                                                                                                            |
| UPL-03 | MUST     | Status stays**derived**, never set by a click (ING-07). `needs_review` now also covers "profiled, but a required confirmation is outstanding"; `ready` is reached when every outstanding confirmation is recorded. The confirmation is the fact; the status is read from it (C-38, D-23).                                                                                                                                                                                                                 |
| UPL-04 | MUST     | Backing out never destroys work. After the file has landed, the file**and the confirmed type map** stay **staged** — never force a re-upload.                                                                                                                                                                                                                                                                                                                                                          |
| UPL-05 | MUST     | **STEP 1 — Target.** ① **Fresh Upload** — new asset, named per AST-02, version 1, and this is where the time basis is set (AST-22). ② **Existing** — the mandatory searchable dropdown of SRC-05/06, **including `requires_reupload` assets** (SRC-13); selection required to continue.                                                                                                                                                                                              |
| UPL-06 | MUST     | **STEP 2.** One file picker (`.csv`/`.xlsx`), a progress indicator, then a preview under its own **"Dataset Summary"** heading carrying row and column count. The counts **move out of** the per-file progress card where they sit today ([DataSourcing.jsx:418-433](../../../source-codes/ui/src/pages/DataSourcing.jsx#L418-L433)).                                                                                                                                                                   |
| UPL-07 | MUST     | Blocking checks at STEP 2 are**structural only**: file present, readable, non-empty, has a header row. Nothing about content blocks here.                                                                                                                                                                                                                                                                                                                                                                     |
| UPL-08 | SHOULD   | For a**Database**, the summary lists per-table row and column counts plus a total, labelled **"Database Summary"** (A-Q03).                                                                                                                                                                                                                                                                                                                                                                             |
| UPL-09 | MUST     | **STEP 3.** Infer the type of every column — numeric, date, categorical, boolean, text — using **generic signals only** (ING-10 stands).                                                                                                                                                                                                                                                                                                                                                              |
| UPL-10 | MUST     | A review table shows column name, inferred type, sample values, null count and distinct count, and any inferred type is overridable from a dropdown.**This table IS the Variable Inventory surface**, not a second one beside it.                                                                                                                                                                                                                                                                             |
| UPL-11 | MUST     | **Warn, do not block**: fully null columns; mixed-type columns; columns where **more than 10%** of values fail to parse as the inferred type. Warnings use the existing structured `{column, code, message}` shape (ING-04).                                                                                                                                                                                                                                                                          |
| UPL-12 | MUST     | **Block**: blank column names, and duplicate column names **within the same table**. For a Database this is checked **per table** — the same name in two different tables (`id` in both `loans` and `collateral`) is normal and must never trigger the block.                                                                                                                                                                                                                              |
| UPL-13 | MUST     | The confirmed type map is stored**with the snapshot**. For a Fresh Upload it becomes the asset's reference schema (AST-07).                                                                                                                                                                                                                                                                                                                                                                                   |
| UPL-14 | MUST     | **STEP 4.** Time period, one of two modes, chosen **ONCE per asset at Fresh Upload**. ① **Period-based** — Start and End Date, mandatory together, blocked if Start > End. A snapshot spanning many periods is expected and correct. ② **No time period** — date fields hidden, **snapshot label becomes mandatory and unique within the asset**. Whichever is chosen becomes the fixed basis (AST-22). On Add period or Full replacement the mode is **never re-offered**. |
| UPL-15 | MUST     | Snapshot label is free text, defaulting to the period range when dates are given.                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| UPL-16 | MUST     | Reporting period column is an**optional dropdown listing every column, unfiltered**. Metadata only — **no split at upload** (OOS-16).                                                                                                                                                                                                                                                                                                                                                                  |
| UPL-17 | MUST     | **Intent applies to existing assets only and is skipped for a Fresh Upload.** ① Add period — adds a snapshot, no version bump. ② Full replacement — this file becomes the source of truth; all existing snapshots superseded (AST-08).                                                                                                                                                                                                                                                                    |
| UPL-18 | MUST     | Full replacement requires an**explicit, informed confirmation**, showing **how many snapshots and which periods or labels** will be superseded. A distinct act, not a side effect of Continue.                                                                                                                                                                                                                                                                                                          |
| UPL-19 | MUST     | "View / restore previous version" is available wherever a superseded set exists, reactivating it wholesale (AST-09).                                                                                                                                                                                                                                                                                                                                                                                                |
| UPL-20 | MUST     | **STEP 5.** The schema check runs for **BOTH intents**, comparing the file against the asset's **current** reference schema: same column names, same column count, same data types.                                                                                                                                                                                                                                                                                                               |
| UPL-21 | MUST     | On mismatch:**warn, never block.** Show the exact difference — missing, extra, renamed columns, and type changes rendered as `limit_amount: numeric → text`.                                                                                                                                                                                                                                                                                                                                              |
| UPL-29 | MUST     | For a**Database**, the check also compares the **table set**: **"table removed"** / **"table added"**, same warn-don't-block pattern, because UPL-21's column vocabulary cannot express a whole table appearing or disappearing. Column comparison still runs within every table present on both sides.                                                                                                                                                                                     |
| UPL-22 | MUST     | **"Process" stays disabled until the user ticks an explicit confirmation**, and the override is logged on the snapshot (`schema_override_flag`, AST-13).                                                                                                                                                                                                                                                                                                                                                    |
| UPL-23 | MUST     | The message states**which consequence applies** — the Add-period wording and the Full-replacement wording verbatim from the requirement — **and lists the affected test configurations by name** when any exist.                                                                                                                                                                                                                                                                                      |
| UPL-24 | MUST     | Add period also warns on**period overlap** with an existing active snapshot, naming the overlapping snapshot(s) and period(s). Warning only.                                                                                                                                                                                                                                                                                                                                                                  |
| UPL-25 | MUST     | Backing out at STEP 5 keeps the file and the confirmed type map staged (UPL-04).                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| UPL-26 | MUST     | **STEP 6.** Storage behaves exactly as AST-05/08/11/13/14 specify. §1 of the requirements is the authority; no storage rule is restated.                                                                                                                                                                                                                                                                                                                                                                     |
| UPL-27 | MUST     | **STEP 7.** A completion summary covering: asset name, snapshot label, period covered (if any), rows loaded, columns and their confirmed types, schema change applied (if any), snapshots superseded (if a replacement), and **every warning that was overridden**.                                                                                                                                                                                                                                     |
| UPL-28 | SHOULD   | The completion summary is retrievable later from the asset catalogue, not only in the moment.                                                                                                                                                                                                                                                                                                                                                                                                                       |
| FNC-01 | MUST     | Re-document the nine stale`service.py` functions in `functional_tools.xlsx` **in this step**, alongside the code change (FNC-04).                                                                                                                                                                                                                                                                                                                                                                         |

### 10.2 Architecture and implementation approach

**The surface.** `ui/src/pages/DataSourcing.jsx`'s `UploadFlow` component
([:167-524](../../../source-codes/ui/src/pages/DataSourcing.jsx#L167-L524)) is reshaped into seven **sections of
one scrolling page**, each with a stable `data-testid` (`upl-step-1` … `upl-step-7`) and each revealing
when its inputs exist (UPL-01). It is **not** a wizard: no step navigation, no next/back chrome, and
`WizardLayout.jsx` is already deleted in Step 1 — if you find yourself reaching for it, re-read D-16.
Sections already-completed remain visible and editable (UPL-01's "move back to any earlier section
without losing work").

**What must NOT change (UPL-02, UPL-03).** The drop → upload → profile chain at
[:218-296](../../../source-codes/ui/src/pages/DataSourcing.jsx#L218-L296) — `uploadOne`, `ensureItem`,
`queueProfile`, `doDrop` — is the buttonless mechanism ING-01 requires and it stays. Its SSE serialisation
via `chainRef` stays. The status badge stays derived from `ingest.status`. **A structural test asserts no
button labelled Upload / Finalize / Profile / Continue-to-profile exists in the file** (N).

**UPL-03's extension is a data change, not a UI change.** `needs_review` must now also mean "profiled,
but a required confirmation is outstanding". Implement in `backend/ingest/status.py`'s `derive()` by
adding an `outstanding_confirmations: int` input, and compute it in `profile_item` as: the count of
schema-conflict warnings needing UPL-22's tick, plus (for `full_replacement`) whether UPL-18's
confirmation record exists. `ready` is `profiling_complete and outstanding_confirmations == 0`. The
confirmation is stored as a row (a `dq_item_warnings`-style record or a `schema_override_flag` +
`confirmation` field on the snapshot); the status is **read** from it (D-23). **Never set the status from
the click handler.**

**STEP 1 (UPL-05).** Two targets as a radio/segmented choice:

- **Fresh Upload** → alias input with the read-only `<SYSTEM-ID>-` prefix rendered *inside* the field
  (AST-02), inline validation on every keystroke against `^[A-Za-z0-9_-]*$` with the offending character
  named (AST-02: "rejected inline as it is typed, not on submit") — **and the current duplicate-name
  pre-warning at [DataSourcing.jsx:191-197](../../../source-codes/ui/src/pages/DataSourcing.jsx#L191-L197) is
  deleted** (AST-03). Plus the time-basis choice, because AST-22 puts it here and only here.
  - The system ID must be **shown before the asset exists**. Reserve it by allocating on first keystroke?
    **No** — that leaks IDs on abandonment. Instead: show `<next ID>` as a **preview** from
    `GET /api/v2/assets/next-id?kind=…` and allocate for real inside `create_asset`. If the previewed and
    allocated IDs differ (a concurrent creation), the UI shows the real one on the STEP 7 summary. Record
    this as the shipped behaviour in `docs/0.5.0/03-upload-flow.md`.
- **Existing** → the SRC-05/06 dropdown component with `excludeRequiresReupload={false}` (P-12, SRC-13),
  selection **mandatory** to continue. The component is built in Step 5; **Step 4 builds it** because
  UPL-05 needs it first, and Step 5 then consumes the same component for the landing page with
  `excludeRequiresReupload={true}`. Note the ordering carefully: §13 puts SRC after UPL, so the *component*
  lands in Step 4 and the *landing page* in Step 5. There is one component either way (C-42).

**STEP 2 (UPL-06, UPL-07, UPL-08).** One picker, `.csv`/`.xlsx` only. The rows/columns table currently
inside the per-file progress card ([:418-433](../../../source-codes/ui/src/pages/DataSourcing.jsx#L418-L433))
**moves** into its own section headed **"Dataset Summary"** (a Dataset) or **"Database Summary"** (a
Database, A-Q03/UPL-08) — the progress card keeps only progress. `save_file`
([service.py:537](../../../source-codes/backend/ai/v2/service.py#L537)) already returns the per-tab
`{tab, rows, columns}` summary and already hard-fails on unreadable/columnless files; UPL-07 adds
**"has a header row"** to that structural set and nothing else. Content never blocks here (UPL-07) —
which means UPL-12's block is **not** a STEP 2 check even though the header is read there.

**STEP 3 (UPL-09…UPL-13) — and the trap that will break UPL-12 if you miss it (P-09, R-05).**
`_read_tabular` uses `pd.read_csv` / `pd.read_excel`, which **silently rewrite** a duplicate header `id`
to `id.1` and a blank header to `Unnamed: 0`. A duplicate/blank check written against `df.columns` will
therefore **never fire**. The check must read the **raw header row**: for `.csv`, `csv.reader` over the
first line with the same dialect sniffing; for `.xlsx`, `openpyxl`'s first row per sheet. Implement as
`backend/ingest/headers.py::read_raw_headers(path) -> dict[table, list[str]]`, called from `save_file`,
and:

- **Block** (UPL-12) if any header is blank after `.strip()`, or if any table's headers contain a
  duplicate **within that table**. The comparison is on the stripped, case-**sensitive** string; record
  that choice — `Id` and `id` are two columns to SQL and to the test layer, so blocking them would be
  wrong, and the requirement says "duplicate column names", not "confusable names".
- **Never** compare across tables (UPL-12's explicit Database carve-out). Note the codebase already has
  a correct precedent for exactly this shape: `_set_dict_entry`
  ([service.py:205-219](../../../source-codes/backend/ai/v2/service.py#L205-L219)) hard-fails on a duplicate
  within one declared table and deliberately allows the same name across two tables. Mirror its
  reasoning and its message style.
- The block surfaces as a **blocking** structural error on the STEP 3 surface, listing the exact table
  and column names, with the file left staged (UPL-04) so the user can fix and re-drop.

The review table (UPL-10) **is** the existing `VariableInventory` panel
(`ui/src/pages/testlab/VariableInventory.jsx`, already imported at
[DataSourcing.jsx:9](../../../source-codes/ui/src/pages/DataSourcing.jsx#L9)), extended with **sample values**
and made the section's own content — not a second table beside it (UPL-10's explicit instruction, and
SRC-01/D-26's "it survives … inside the upload flow's own review surface"). Type override writes through
the existing `put_inventory` ([service.py:841](../../../source-codes/backend/ai/v2/service.py#L841)); the
**confirmed** map (post-override) is what gets stored as `column_type_map_json` (UPL-13) and, for a
Fresh Upload, becomes `dq_asset_versions.reference_schema_json` (AST-07).

UPL-11's three warnings extend `backend/ingest/warnings.py` with three codes —
`column_all_null`, `column_mixed_type`, `column_parse_failure_rate` — in the existing
`{column, code, message}` shape (ING-04). The 10% threshold is a **named constant with a comment citing
UPL-11**, not a bare literal, and it does not go in the semantic threshold layer (that layer is for
diagnostics, FWK-06 — do not widen it here).

**STEP 4 (UPL-14…UPL-19).**

- The mode question renders **only** when the target is Fresh Upload (UPL-14/AST-22). For an existing
  asset the section reads the asset's `time_basis` and renders **only** the fields that basis needs:
  Start/End for `period`, a mandatory unique label for `none`. **There is no code path that offers the
  mode for an existing asset** — assert it structurally (N).
- Period validation: both dates or neither (mandatory together); `start > end` blocks. Blocking here is
  a **field validation**, not a pipeline gate — the file stays staged (UPL-04/25).
- Label uniqueness within the asset is enforced by the partial unique index from Step 3
  (`ux_dq_items_label_in_family`) **and** pre-checked in the service so the user gets a message rather
  than a constraint error.
- UPL-15's default: when dates are given, the label defaults to the rendered period range (e.g.
  `2026-01-01 → 2026-12-31`), editable.
- UPL-16's dropdown lists **every** column of the file, unfiltered — no type filtering, no name
  heuristic (ING-10). Stored as `period_column`. **No split happens** (OOS-16); a test asserts the stored
  snapshot still has its full row count after a period column is chosen.
- UPL-17: intent radio, rendered **only** for an existing asset.
- UPL-18: choosing Full replacement reveals a distinct confirmation block that calls the **same** preview
  helper `supersede_version_set` uses (Step 3, P-11) to state how many snapshots and which
  periods/labels will be superseded, then requires its own explicit act (a checkbox plus a confirm
  button, or a typed confirmation — mirror the factory reset's discipline: **the server re-checks it**,
  never trusting the disabled control).
- UPL-19: a "View / restore previous version" link wherever a superseded set exists, calling Step 3's
  `restore_version_set`. It appears on this surface, and Step 7/Step 8's catalogue surfaces reuse the
  same component (AST-19's "same three tiers in the same order everywhere" is about the diff, but the
  restore affordance should likewise be one component).

**STEP 5 (UPL-20…UPL-25, UPL-29).** New module `backend/assets/schema_check.py`:

```
compare(reference_schema, incoming_schema, kind) -> {
  tables_added: [t], tables_removed: [t],                   # UPL-29, Database only
  per_table: { t: { columns_missing: [c], columns_extra: [c],
                    columns_renamed: [{from, to, confidence}],
                    type_changes: [{column, from, to}] } },
  column_count_change: {from, to},
  is_match: bool
}
```

- Runs for **both** intents (UPL-20). Called from `profile_item` after the confirmed map exists
  (FNC-01: "`profile_item` … needs to orchestrate the schema-conflict check for both intents").
- **Warn, never block** (UPL-21). Rendering: `limit_amount: numeric → text` exactly as specified.
- Rename detection is **evidence-based and labelled**: a removed column and an added column are reported
  as "likely renamed" only when name similarity **and** type agreement pass a stated threshold; otherwise
  they are reported as one removed and one added. The confidence is shown. Never assert a rename without
  evidence.
- UPL-29's table-set comparison for a Database uses its own vocabulary — **"table removed" / "table
  added"** — and column comparison still runs inside every table present on both sides.
- **UPL-23's test-configuration list.** The consequence message for Full replacement must name the
  affected test configurations. Source: the asset's snapshots' diagnostic manifests. **Read
  `backend/dq_diagnostics/manifest.py` and the `diag_runs` schema before implementing this** — this plan
  deliberately does not assert their internals. The functional contract is: given the set of removed +
  retyped columns, return the distinct names of every stored manifest/run of this asset whose referenced
  columns intersect that set. When the set is empty, the message says so explicitly ("no existing test
  configuration references the affected columns") rather than rendering an empty list.
- UPL-24's overlap warning: for `add_period` on a `period` asset, compare `[start, end]` against every
  **active** snapshot's range; report each overlap by snapshot label and period. Warning only.
- UPL-22: `Process` disabled until the tick; **the server re-validates** and returns 400 with nothing
  stored if the confirmation is absent; the override writes `schema_override_flag=1` on the snapshot.

**STEP 6 (UPL-26).** No new rules. `add_snapshot` from Step 3 does the storage; this step wires it,
passing `intent`, the period fields, the confirmed map, the label, the period column, `uploaded_by`, and
the override flag.

**STEP 7 (UPL-27, UPL-28) and the fingerprint (P-01, AST-17).**

- `ingest_summary` ([service.py:877](../../../source-codes/backend/ai/v2/service.py#L877)) is extended with the
  UPL-27 field set. Every listed field is present or explicitly "not applicable" — never silently
  absent. **"Every warning that was overridden"** means the stored override records, not a re-derivation.
- UPL-28: the same payload is retrievable later by snapshot id, which is what Step 8's catalogue renders.
- **The fingerprint is written here, in this step.** New table (added to `_SCHEMA`, rule 3):

```sql
CREATE TABLE IF NOT EXISTS dq_snapshot_fingerprints (
    snapshot_id TEXT, table_name TEXT, column_name TEXT,
    dtype TEXT, confirmed_type TEXT,
    null_rate REAL, distinct_count INTEGER,
    min_value TEXT, max_value TEXT, mean_value REAL, stddev_value REAL,
    histogram_json TEXT,        -- AST-17: fixed-bin, numerics only
    distinct_set_hash TEXT,     -- AST-17: hash of the sorted distinct set, low-cardinality only
    top_k_json TEXT,            -- AST-16 tier 3, categoricals
    computed_at TEXT,
    PRIMARY KEY (snapshot_id, table_name, column_name)
);
```

  `_column_profile` ([service.py:336](../../../source-codes/backend/ai/v2/service.py#L336)) is extended to
  produce `stddev`, the fixed-bin histogram and the distinct-set hash **inside the pass it already
  runs** (AST-17: "computed once during the profiling pass that already runs"), and `profile_item`
  persists one fingerprint row per column. Existing profile keys are **kept alongside** the new ones —
  additive, not a breaking rename, exactly as the 0.4.0 docstring at :336-342 established. The
  low-cardinality threshold and the bin count are named constants with citations, not bare literals.

**FNC-01 (in this step, per FNC-04).** Refresh the `service.py` sheet of `functional_tools.xlsx` for the
nine changed functions: `create_item`→`create_asset`, `reupload_item`→`add_snapshot`, `finalize_item`,
`save_file`, `profile_item`, `_write_table`, `_parse_dictionary`, `_column_profile`, `ingest_summary`.
Re-verify `_read_tabular`, `_classify`, `get_inventory`, `put_inventory` and mark them verified.
Keep the workbook's existing six-column shape and two-line header (FNC-03). `functional_tools.docx` is a
**rendering, not the source** — do not edit it (requirements §15's correction).

**Permitted paths:** `ui/src/pages/DataSourcing.jsx` (replaced), new
`ui/src/pages/datasourcing/**` if the seven sections read better as components,
`ui/src/pages/testlab/VariableInventory.jsx` (sample values), `ui/src/components/AssetPicker.jsx` (new,
the SRC-05/06 component), `ui/src/api/client.js`, `backend/ai/v2/service.py`,
new `backend/ingest/headers.py`, `backend/ingest/warnings.py`, `backend/ingest/status.py`,
new `backend/assets/schema_check.py`, `backend/assets/service.py`, `backend/routers/v2.py`,
`backend/system_db.py` (additive), `backend/tests/**`, `ui/e2e/**`, `functional_tools.xlsx`,
`docs/0.5.0/**`.
**Forbidden:** `backend/dq_diagnostics/delivery.py`; any change to the diagnostic engines;
`functional_tools.docx`.

### 10.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                   | Layer | ID                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------------------- |
| 4-A1  | Seven sections on**one** surface, each with its own testid, revealing progressively. No wizard chrome, no step router.                                                                                                                | E/N   | UPL-01               |
| 4-A2  | No button labelled Upload / Finalize / Profile exists; dropping the file starts parse+profile unprompted. Structural assertion over the source**plus** an E2E proof.                                                                  | N/E   | UPL-02               |
| 4-A3  | With an outstanding schema confirmation the status reads`needs_review`; the moment the confirmation record exists it reads `ready` — **without** any code path setting the status from a click handler.                          | S/N   | UPL-03, D-23         |
| 4-A4  | Leaving the flow after the file landed and re-entering restores the staged file**and** the confirmed type map.                                                                                                                        | E/S   | UPL-04, UPL-25       |
| 4-A5  | STEP 1 offers exactly two targets; Fresh shows the read-only prefix + alias field + time-basis choice; Existing shows the mandatory searchable dropdown including`requires_reupload` rows. Continue is disabled until a selection exists. | E/N   | UPL-05, SRC-13       |
| 4-A6  | An invalid alias character is rejected**as typed** (before any submit), naming the character. The old duplicate-name pre-warning is gone.                                                                                             | E/N   | AST-02, AST-03       |
| 4-A7  | Row/column counts appear under a**"Dataset Summary"** heading (Dataset) or **"Database Summary"** with per-table rows plus a total (Database), and **no longer** inside the progress card.                                | E     | UPL-06, UPL-08       |
| 4-A8  | STEP 2 blocks only on: absent, unreadable, empty, or headerless file. A file with content problems reaches STEP 3.                                                                                                                          | S/N   | UPL-07               |
| 4-A9  | Every column gets an inferred type from generic signals; a source-inspection test proves no column-name literal drives inference.                                                                                                           | S/N   | UPL-09, ING-10       |
| 4-A10 | The review table shows name, inferred type,**sample values**, null count, distinct count, and an override dropdown. There is exactly **one** inventory table on the surface.                                                    | E/N   | UPL-10               |
| 4-A11 | Fully null, mixed-type, and >10%-parse-failure columns each produce their structured warning and**none** blocks `ready`.                                                                                                            | S     | UPL-11               |
| 4-A12 | A CSV whose raw header line is`id,id,amount` is **blocked**; a workbook where `loans` and `collateral` each have an `id` is **not**. A blank header is blocked. The check reads the raw header, not `df.columns`.     | S/N   | UPL-12, P-09         |
| 4-A13 | The confirmed (post-override) map is stored on the snapshot, and on a Fresh Upload becomes the asset's reference schema.                                                                                                                    | S     | UPL-13, AST-07       |
| 4-A14 | The mode question appears**only** on Fresh Upload. For an existing asset STEP 4 renders only the fields its fixed basis requires, and no code path can re-offer the mode.                                                             | E/N   | UPL-14, AST-22, D-30 |
| 4-A15 | Both dates or neither;`start > end` blocked; `none`-basis label mandatory and unique within the asset.                                                                                                                                  | S/N   | UPL-14               |
| 4-A16 | Label defaults to the period range when dates are given, and is editable.                                                                                                                                                                   | E     | UPL-15               |
| 4-A17 | The period-column dropdown lists**every** column, unfiltered, and choosing one changes no stored row count.                                                                                                                           | E/S/N | UPL-16, OOS-16       |
| 4-A18 | Intent appears only for an existing asset; Add period does not bump the version; Full replacement does.                                                                                                                                     | E/S   | UPL-17               |
| 4-A19 | Full replacement's confirmation states the snapshot count and each period/label to be superseded, and is a distinct act; the server refuses a replacement whose confirmation is absent.                                                     | E/A/N | UPL-18               |
| 4-A20 | "View / restore previous version" appears wherever a superseded set exists and restores wholesale.                                                                                                                                          | E     | UPL-19, AST-09       |
| 4-A21 | The schema check runs for both intents and reports missing/extra/renamed columns and type changes as`col: from → to`, never blocking.                                                                                                    | S/E   | UPL-20, UPL-21       |
| 4-A22 | For a Database, a table missing from the new file reports**"table removed"** and a new table reports **"table added"**; column comparison still runs inside tables present on both sides.                                       | S     | UPL-29               |
| 4-A23 | `Process` is disabled until the tick; the override sets `schema_override_flag=1` on the snapshot; a direct API call without the confirmation is a 400 that stores nothing.                                                              | E/A/N | UPL-22               |
| 4-A24 | The Add-period and Full-replacement consequence messages match the requirement's wording, and the Full-replacement message names the affected test configurations when any exist (and says so when none do).                                | S/E   | UPL-23               |
| 4-A25 | An overlapping Add period names the overlapping snapshot(s) and period(s) and does not block.                                                                                                                                               | S/E   | UPL-24               |
| 4-A26 | The completion summary carries every UPL-27 field, including every overridden warning.                                                                                                                                                      | E/S   | UPL-27               |
| 4-A27 | The same summary is retrievable later by snapshot id.                                                                                                                                                                                       | A     | UPL-28               |
| 4-A28 | Every column of every stored snapshot has a`dq_snapshot_fingerprints` row with tier-③ statistics, written during the existing profiling pass.                                                                                            | S/M   | AST-17, P-01         |
| 4-A29 | `functional_tools.xlsx`'s `service.py` sheet is refreshed for the nine changed functions in this commit.                                                                                                                                | —    | FNC-01, FNC-04       |
| 4-A30 | Suite green, count recorded;`upload-workflow.spec.js` rewritten in this commit and covering strictly more than before (rule 7, R-10).                                                                                                     | —    | RET-06               |

### 10.4 Validation criteria (independent validator)

**Functional.** Drive the real UI four times: Dataset/Fresh, Dataset/Add-period, Dataset/Full-replacement,
Database/Fresh (multi-sheet workbook). In each pass, stop at every one of the seven sections and confirm
it shows what its requirement says it shows and nothing more. Then: abandon a flow mid-way, navigate
away, come back — the staged file and confirmed map must be there. Then: read the STEP 7 summary and
check it against what you actually did, field by field against UPL-27's list.

**Technical / architecture.** (a) Confirm UPL-12's check reads raw headers — open the code and look; a
`df.columns`-based check is an automatic fail (R-05). (b) Confirm the status is never assigned in a
click handler (grep for `setStatus`/`set_status` in the confirmation paths). (c) Confirm the schema check
is one module called from `profile_item` for both intents, not duplicated per intent. (d) Confirm the
UPL-22 confirmation is re-checked **server-side**. (e) Confirm the fingerprint is computed inside the
existing profiling pass (one traversal per column), not in a second pass over the data. (f) Confirm the
asset picker is one component with the exclusion as a required prop (P-12). (g) Confirm the 10% threshold
and the histogram bin count are named constants with citations. (h) Confirm no domain literal entered
inference, warnings, or the fingerprint.

**Automated tests to run/add.** New: `backend/tests/test_upload_flow.py` (the seven steps' blocking and
warning contract, both intents, both kinds), `test_schema_check.py` (UPL-20/21/29 including rename
confidence), `test_headers.py` (P-09's raw-header reader, incl. `.xlsx`),
`test_fingerprint.py` (AST-17 write, one row per column, values correct on a known fixture).
Rewrite: `test_ingest.py`'s re-upload block; `ui/e2e/upload-workflow.spec.js`.
Run: full pytest; `verify_plan8.py`; `npm run check:reachability`; `npm run lint`; `npm run build`;
`npm run test:e2e`.

**Playwright scenarios (this is M-3's core).**

- (E-4.1) **Dataset · Fresh Upload, 1→7:** target Fresh, alias typed with one rejected character proving
  inline validation, time basis `period`, drop a CSV, assert "Dataset Summary" with rows/columns, review
  table with sample values, override one type, set Start/End, pick a period column, no intent section
  visible, no schema-conflict section (nothing to compare against), storage, then the STEP 7 summary
  showing the overridden type.
- (E-4.2) **Dataset · Add period:** target Existing, search the dropdown, select, drop a CSV with one
  column removed and one retyped → assert the exact `col: numeric → text` rendering, assert the Add-period
  consequence wording, tick the confirmation, assert `Process` becomes enabled, complete, then assert the
  asset now has 2 active snapshots at the **same** version.
- (E-4.3) **Dataset · Full replacement:** assert the confirmation block names the snapshot count and each
  period, complete, then assert version 2, all prior snapshots superseded and still listed as retained,
  and the summary's "snapshots superseded" section.
- (E-4.4) **Database · Fresh + Add period:** multi-sheet workbook; assert "Database Summary" with
  per-table rows and a total; on the add-period pass drop a workbook missing one sheet and containing one
  new sheet → assert **"table removed"** and **"table added"** appear.
- (E-4.5) **Back-out:** drop a file, confirm the map, navigate to Test Lab, return → staged state intact.

**Adversarial / negative-path cases.**

1. **`id,id,amount` (the literal case):** a CSV with two columns named `id` → blocked, naming both.
   Then the same names in **two different tables** of one workbook → **not** blocked.
2. Header `id, ,amount` (blank middle) → blocked. Header `id,"  ",amount` (whitespace only) → blocked.
3. `Id,id` in one table → **not** blocked (case-sensitive by decision) — and the decision is recorded.
4. A 0-row CSV with a valid header → STEP 2 does **not** block (non-empty means the file, not the data),
   STEP 3 warns on all-null columns. A 0-byte file → blocked. A file with data but no header row →
   blocked at STEP 2 (UPL-07).
5. `start > end` → blocked; only Start given → blocked; only End given → blocked; equal dates → allowed
   (a single-day period is a period).
6. Duplicate snapshot label on a `none`-basis asset → blocked with a message, not a constraint traceback.
   The same label on a **different** asset → allowed (AST-03's scoping).
7. **UPL-22 server-side (bites-check):** POST the process endpoint with `schema_override=false` while a
   mismatch exists → 400, nothing stored, no snapshot row created. Invert the server check and confirm the
   test fails.
8. **UPL-14 mode re-offer (bites-check):** call the add-snapshot API for a `period` asset passing
   `time_basis='none'` → refused. Assert no UI path can send it.
9. **A mismatch on Add period must warn, not block** — a file with a **missing** column still reaches
   `ready` after the tick. A mismatch that blocks is a failure of UPL-21.
10. **UPL-23 empty case:** a Full replacement on an asset with **no** test configurations → the message
    must say no configuration is affected, not render an empty list.
11. **Overlap edge cases:** ranges that touch at a boundary (`…-01-31` then `02-01-…`) → **no** overlap;
    ranges sharing one day → overlap named. A `none`-basis asset → no overlap check at all.
12. **Concurrent drops:** drop the data file and a dictionary within 200 ms → exactly one profile pass
    resolves last and the final state is correct (the existing `chainRef` guarantee must survive the
    rewrite).
13. **Superseded snapshot:** attempt to re-profile or overwrite a superseded snapshot's table cache →
    refused (rule 9).
14. **Unsupported file type** (`.txt`, `.parquet`, a `.csv` that is actually HTML) → three distinct clear
    rejections, none a traceback.
15. **A 200-column file** → the review table renders and the fingerprint writes 200 rows; confirm the
    profiling pass does not degrade to O(columns²).

### 10.5 Dependencies

- **Step 3 complete, all three commits**, migration verified idempotent. Step 4 calls
  `assets.create_asset`, `assets.add_snapshot`, `assets.supersede_version_set`,
  `assets.restore_version_set`, `assets.ordered_snapshots` and the AST-13 read model — none of which
  exists before Step 3.
- Step 3's `docs/0.5.0/02-asset-model.md` fingerprint contract (task 3.3's `_column_profile` row).
- `backend/dq_diagnostics/manifest.py` read before UPL-23's test-configuration list is implemented.
- Step 1's reachability gate green, so new client.js exports are immediately covered.

**Commits:** `feat(upload)!: STEP 1-3 — target, dataset summary, type check with per-table duplicate block`
(S4a) · `feat(upload)!: STEP 4-5 — intent, fixed time basis, schema conflict check` (S4b) ·
`feat(upload)!: STEP 6-7 — storage, completion summary, snapshot fingerprints + FNC-01 map refresh` (S4c)

---

## 11. Step 5 — SRC: landing page, context, Test Lab handover <a id="step-5"></a>

> Requirements §13 step 5. Needs real versioned assets to select from, so it follows step 4.

### 11.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                                                                                                             |
| ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SRC-01 | MUST     | **The Variable Inventory is not on the landing page.** The "Active workflow variable inventory" block ([DataSourcing.jsx:558-563](../../../source-codes/ui/src/pages/DataSourcing.jsx#L558-L563)) is **removed**. It survives inside the upload flow's review surface (UPL-10) and in the Test Lab's "Variable inventory of saved data" panel.        |
| SRC-02 | MUST     | Both cards change identically. "Database Upload" →**"Database"**; "Dataset Upload" → **"Dataset"**. **"Select to start →" is deleted** ([:554](../../../source-codes/ui/src/pages/DataSourcing.jsx#L554)). Each card gains **"View Existing"** and **"Add New"**.                                                                |
| SRC-03 | MUST     | "View Existing" opens a**compact dropdown**, not a page navigation.                                                                                                                                                                                                                                                                               |
| SRC-04 | MUST     | The landing page's dropdown lists assets still in play and**excludes** those whose lifecycle status is `complete`, `archived` or `requires_reupload`. Superseded snapshots never appear (AST-08). **Scoped to this entry point only** — SRC-13 carves out the upload flow.                                                           |
| SRC-05 | MUST     | Each row shows: full name`<SYSTEM-ID>-<alias>`, version, current status, snapshot count, last upload date, and a column count that **fits the kind** — for a Dataset the table's column count; for a **Database, table count plus total columns across all tables**. One component, one data source, used from both entry points (C-42). |
| SRC-06 | MUST     | The dropdown is**searchable**, and from the upload flow's STEP 1(b) **selection is mandatory to continue**.                                                                                                                                                                                                                                 |
| SRC-07 | MUST     | Status is shown for**every** listed asset, but only assets with at least one `ready` **active** snapshot are **selectable**. A still-ingesting asset is listed with its live status and **disabled** (C-43).                                                                                                                  |
| SRC-08 | MUST     | Selecting an asset**sets the workflow context** — the selected asset (and, where relevant, snapshot) is what every subsequent step operates on.                                                                                                                                                                                                  |
| SRC-09 | MUST     | The active context is**visible and changeable**: which asset, which version, how many active snapshots, and a way to clear or change it. A user must never act on a context they cannot see.                                                                                                                                                      |
| SRC-10 | MUST     | A**"Go to Test Lab"** link at the bottom of Data Sourcing opens the Test Lab **with the selected asset already chosen**.                                                                                                                                                                                                                    |
| SRC-11 | MUST     | Entering the Test Lab**from the left-hand navigation opens it with nothing selected**. The auto-selection at [TestLab.jsx:44-48](../../../source-codes/ui/src/pages/TestLab.jsx#L44-L48) is removed **for that entry path only**.                                                                                                                     |
| SRC-12 | MUST     | "Add New" starts §3's upload flow for that card's kind.                                                                                                                                                                                                                                                                                                |
| SRC-13 | MUST     | STEP 1(b)'s picker is the**same component**, with one deliberate difference: it does **not** apply SRC-04's `requires_reupload` exclusion, and it **flags** such a row (e.g. a "Needs re-upload" badge). It is the only path back to such an asset (D-31).                                                                          |

### 11.2 Architecture and implementation approach

**The component (built in Step 4, consumed here).** `ui/src/components/AssetPicker.jsx`:

- Data source: `GET /api/v2/assets?kind=…` → `assets.list_assets()` from Step 3, one row per **asset**,
  never per snapshot. Columns exactly SRC-05's union: `display_name`, `current_version_no`,
  `lifecycle_status` (rendered through a human label — M-2), `active_snapshot_count`,
  `last_upload_date`, and the kind-appropriate count:
  - Dataset → `column_count` (the table's).
  - Database → `table_count` **and** `total_column_count`, rendered as e.g. `3 tables · 14 columns`.
    **Never a bare single number** — SRC-05 says a single column count is meaningless once an asset spans
    more than one table.
- `selectable` per row is computed **server-side** (so both entry points agree): true iff the asset has
  at least one snapshot with `snapshot_status='active'` **and** `ingest_status='ready'` (SRC-07). Rows
  that are not selectable render disabled **but present**, with their live status (C-43) — never hidden.
- Required prop `excludeRequiresReupload: boolean` (P-12, no default). Landing page passes `true`
  (SRC-04); STEP 1(b) passes `false` (SRC-13) and renders a **"Needs re-upload"** badge on those rows.
  `complete` and `archived` are excluded at **both** entry points — SRC-04 names all three, and SRC-13
  carves out only `requires_reupload`.
- Searchable: client-side filter over `display_name` (which contains both the system ID and the alias,
  so a user can search either — a direct benefit of P-03's stored display name).
- Compact dropdown/popover (SRC-03): **no route change**. An E2E assertion on `page.url()` before and
  after proves it.

**The landing page.** `DataSourcing.jsx`'s default export
([:526-575](../../../source-codes/ui/src/pages/DataSourcing.jsx#L526-L575)):

- `KIND_CARDS` titles become `"Database"` / `"Dataset"` (SRC-02). The `Select to start →` paragraph at
  :554 is deleted. Each card becomes a container with two actions — `View Existing` (opens the picker
  popover) and `Add New` (enters the upload flow for that kind, SRC-12) — rather than one big button.
- The `activeItems` fetch at :531-534 and the whole "Active workflow variable inventory" section at
  :558-563 are **deleted** (SRC-01, D-26). `VariableInventory` remains imported only by the upload flow's
  review section and by the Test Lab.
- **Workflow context (SRC-08, SRC-09).** A new `WorkflowContextBar` rendered at the top of Data Sourcing
  whenever a context is set, showing: `display_name`, `v{n}`, `{k} active snapshots`, the asset's status,
  and **Change** / **Clear** actions. Storage: a React context provider (`ui/src/lib/workflowContext.jsx`)
  persisted to `sessionStorage` under one key, so a refresh does not silently drop the context — with the
  bar as the single rendering of it. **Never store the context only in a component's local state**: SRC-09
  says a user must never act on a context they cannot see, and local state means it can be lost while a
  downstream screen still holds it.
- **"Go to Test Lab" (SRC-10).** A link at the bottom of the Data Sourcing screen, enabled only when a
  context is set, navigating to the Test Lab with the asset carried across. Mechanism: navigate with the
  asset id in the route/query (`/test-lab?asset=<asset_id>`) **and** leave the shared workflow context
  set — the query parameter is what distinguishes this entry from a left-nav entry (SRC-11), and it must
  be explicit, not inferred from "is a context set", because a context can persist across a later left-nav
  entry.

**Test Lab (SRC-11).** `TestLab.jsx`'s `load()` at [:44-48](../../../source-codes/ui/src/pages/TestLab.jsx#L44-L48)
currently ends with `ready[0]?.item_id || ""` — an unconditional auto-select of the first ready item on
mount. Change to:

- Read the `asset` query parameter. If present **and** it resolves to a selectable asset, pre-select it
  (SRC-10). If present and it does not resolve, select nothing and show why.
- If absent (left-nav entry), select **nothing** — `setItemId("")` — and the picker prompts the user
  (SRC-11).
- Keep the existing "preserve the current selection across a reload" behaviour (`ready.some(...) ? current : …`) so an in-session refresh does not clear a deliberate choice; only the *initial* selection changes.
- The picker itself, and the "Variable inventory of saved data" panel, are **kept** (requirements §0.4:
  SRC-06 deep-links into it).
- **The Test Lab must offer snapshots, not just assets** (AST-12): only `active` snapshots are
  selectable, ordered per AST-11. Step 3's guard already refuses a superseded snapshot at the manifest
  endpoint; this step makes the UI consistent with it.

**Permitted paths:** `ui/src/pages/DataSourcing.jsx`, `ui/src/pages/TestLab.jsx`,
`ui/src/components/AssetPicker.jsx`, new `ui/src/components/WorkflowContextBar.jsx`, new
`ui/src/lib/workflowContext.jsx`, `ui/src/api/client.js`, `backend/routers/v2.py` (the assets list
endpoint if it did not land in Step 4), `backend/assets/service.py`, `backend/tests/**`, `ui/e2e/**`,
`docs/0.5.0/**`.
**Forbidden:** the diagnostic engines; `system_db.py` (no schema change is needed for this step).

### 11.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                               | Layer | ID                 |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ------------------ |
| 5-A1  | The landing page renders**no** variable inventory; a grep proves `VariableInventory` is not imported by the landing-page component path.                                                                                        | E/N   | SRC-01, D-26       |
| 5-A2  | Card titles are exactly "Database" and "Dataset"; "Select to start →" appears nowhere; each card has "View Existing" and "Add New".                                                                                                    | E/N   | SRC-02             |
| 5-A3  | "View Existing" opens a dropdown with**no** URL change.                                                                                                                                                                           | E/N   | SRC-03             |
| 5-A4  | The landing-page dropdown excludes`complete`, `archived` and `requires_reupload` assets, and shows no superseded snapshot anywhere.                                                                                               | S/E/N | SRC-04, AST-08     |
| 5-A5  | Each row shows all six SRC-05 facts, and a**Database** row shows table count **plus** total columns (e.g. `3 tables · 14 columns`), never a single bare count.                                                           | S/E   | SRC-05, A-Q03      |
| 5-A6  | The dropdown filters on typed text matching either the system ID or the alias; from STEP 1(b), Continue stays disabled until a row is selected.                                                                                         | E     | SRC-06             |
| 5-A7  | An asset with no`ready` active snapshot is **listed with its live status and disabled**, not hidden.                                                                                                                            | S/E/N | SRC-07, C-43       |
| 5-A8  | Selecting an asset sets a context that every subsequent step reads; the context survives a page refresh.                                                                                                                                | E     | SRC-08             |
| 5-A9  | The context bar shows asset, version, active snapshot count and status, with working Change and Clear.                                                                                                                                  | E     | SRC-09             |
| 5-A10 | "Go to Test Lab" opens the Test Lab with that asset already chosen.                                                                                                                                                                     | E     | SRC-10             |
| 5-A11 | Entering the Test Lab from the left nav selects**nothing**, even when exactly one ready asset exists.                                                                                                                             | E/N   | SRC-11             |
| 5-A12 | "Add New" enters the upload flow for that card's kind, at STEP 1 with Fresh Upload available.                                                                                                                                           | E     | SRC-12             |
| 5-A13 | **One** component serves both entry points, with the exclusion as a required prop; a `requires_reupload` asset is **absent** from the landing dropdown and **present with a "Needs re-upload" badge** in STEP 1(b). | S/E/N | SRC-13, C-42, D-31 |
| 5-A14 | Suite green; count recorded;`upload-workflow.spec.js` and any Test Lab spec touching auto-selection rewritten in this commit.                                                                                                         | —    | rule 7             |

### 11.4 Validation criteria (independent validator)

**Functional.** Land on Data Sourcing with a seeded instance containing: one `ready` dataset, one still
`profiling` dataset, one `complete` dataset, one `requires_reupload` dataset, one multi-table database.
Open View Existing on both cards and check the row set and the enabled/disabled state against SRC-04 and
SRC-07 by hand. Select an asset, confirm the context bar, refresh the page, confirm it is still there,
clear it, confirm it is gone. Click "Go to Test Lab" and confirm the asset is pre-chosen. Then enter the
Test Lab from the left nav and confirm nothing is chosen. Finally, from STEP 1(b), confirm the
`requires_reupload` asset is reachable and badged.

**Technical / architecture.** (a) Confirm there is exactly one picker component and one data source —
two implementations is an automatic fail (C-42). (b) Confirm `selectable` is computed server-side.
(c) Confirm `excludeRequiresReupload` has **no default value** (P-12). (d) Confirm the Test Lab's
pre-selection is driven by an explicit query parameter, not by "a context happens to be set".
(e) Confirm the workflow context has one provider and one bar, not context state duplicated per page.
(f) Confirm no status enum leaks into the rendered rows (M-2).

**Automated tests to run/add.** New: `backend/tests/test_assets_list.py` (SRC-05 row shape for both
kinds, SRC-04/SRC-13 exclusion parameterisation, SRC-07 selectability). Frontend: a unit/DOM test for
`AssetPicker` proving both exclusion modes from one component. Run: full pytest; `verify_plan8.py`;
`npm run check:reachability`; `npm run build`; `npm run test:e2e`.

**Playwright scenarios.**

- (E-5.1) Landing page: assert no inventory table, exact card titles, absence of "Select to start",
  presence of both links.
- (E-5.2) View Existing: assert the exact row set for the seeded fixture, the disabled row with its live
  status, and that `page.url()` is unchanged.
- (E-5.3) Database row shows `N tables · M columns`.
- (E-5.4) Select → context bar → refresh → still set → Clear → gone.
- (E-5.5) "Go to Test Lab" → asset pre-selected; then left-nav entry → **nothing** selected (with exactly
  one ready asset present, so a naive auto-select would pass a weaker test).
- (E-5.6) STEP 1(b) shows the `requires_reupload` asset with its badge; the landing dropdown does not.

**Adversarial / negative-path cases.**

1. **One ready asset only** (SRC-11's trap): left-nav entry must still select nothing. This is the case a
   careless fix passes accidentally, which is why the fixture must have exactly one.
2. **`asset=` pointing at a superseded-only asset** (all snapshots superseded) → Test Lab selects nothing
   and says why (AST-08/AST-12).
3. **`asset=` pointing at a nonexistent id, or at an asset of the wrong kind** → no selection, clear
   message, no crash.
4. **Context set, then the asset is superseded/replaced in another tab** → the context bar must not show a
   stale version silently (this is the CTX-02 seam; at minimum the bar re-reads on focus).
5. **Zero assets** → both dropdowns render an empty state that says what to do ("Add New"), not a blank
   popover.
6. **An asset whose only active snapshot is `failed`** → listed, disabled, live status shown.
7. **A very long alias (60 chars) + long system id** → the row truncates without breaking layout, and the
   full name is available on hover/title.
8. **Exclusion bites-check:** flip `excludeRequiresReupload` on the landing page to `false` → the SRC-04
   test must fail. Flip it to `true` on STEP 1(b) → the SRC-13 test must fail. Both directions must bite,
   because D-31 is about a parameter that is easy to get backwards.

### 11.5 Dependencies

- **Step 4 complete** — `AssetPicker` and the assets list endpoint land there (UPL-05 needs them first),
  and this step needs real versioned assets with real snapshot counts to render.
- Step 3's `ordered_snapshots` and the AST-08 superseded semantics.

**Commit:** `feat(sourcing)!: asset-first landing page, one View Existing picker, workflow context, Test Lab handover`

---

## 12. Step 6 — CTX: downstream refresh and staleness <a id="step-6"></a>

> Requirements §13 step 6. Needs both the snapshot model (step 3) and the intents that change it
> (step 4). These are the two notes that describe **defects caused by** the old replacement model.

### 12.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                                                                                                                                        |
| ------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CTX-01 | MUST     | **When the active snapshot set changes, everything derived from it refreshes.** Target Variable, Business Tags, profiling results and related analyses recalculate against the new active set. Dependent sections continuing to display the previous dataset's name and figures is a **defect** and is corrected, not worked around.                                   |
| CTX-02 | MUST     | Derived artefacts bound to a**superseded** snapshot are **marked stale**, never silently shown as current. A stale artefact **states which snapshot it came from** and **offers to recompute**.                                                                                                                                                            |
| CTX-03 | SHOULD   | A**"Refresh"** control lets the user force recomputation of all asset-dependent information at any time — a **complement** to CTX-01, not an alternative (C-45).                                                                                                                                                                                                      |
| CTX-04 | MUST     | **Target Variable and Use Case default to blank** on a Fresh Upload and on a Full replacement. Values from a previously loaded dataset are never carried forward automatically. **Reverses** today's `default_flag` ([DataSourcing.jsx:203](../../../source-codes/ui/src/pages/DataSourcing.jsx#L203)) and `IFRS9` ([:176](../../../source-codes/ui/src/pages/DataSourcing.jsx#L176)). |
| CTX-05 | MUST     | **Blank does not block Ready — it blocks the tests that need it.** ING-02's "never an ingestion gate" stands; a missing target surfaces at the **Test Lab scope gate**, against the specific diagnostics that require one (C-41).                                                                                                                                     |
| CTX-06 | MUST     | **Add period carries target and use case forward** — same asset, same reference schema. Only Fresh Upload and Full replacement clear them (D-24).                                                                                                                                                                                                                           |

### 12.2 Architecture and implementation approach

**Task 6.1 is mandatory and comes first (R-09): bound the derived set.** Write
`docs/0.5.0/05-refresh-contract.md` enumerating, table by table, everything derived from an asset's
active snapshot set, split into exactly two lists:

| Class                                                             | Contents                                                                                                                                                                                                                                    | On active-set change                                                                                                |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Recompute** — deterministic, no human decision inside    | `variable_inventory` (classification, role, profile_json), `dq_item_mappings`, `dq_item_warnings`, `dq_snapshot_fingerprints`, `dictionary_state`, the derived `ingest_status`, and every read-model summary computed from them | **Recomputed automatically** (CTX-01)                                                                         |
| **Mark stale** — carries a human decision or a run outcome | `diag_runs`, `diag_run_decisions`, `diag_results`, `diag_findings`, `diag_dispositions`, `issues_v2`, `tracked_issues_v2`, `rca_*`, `plan_v2`, `results_v2`, `scores_v2`, `tag_assignments`                         | **Never recomputed silently.** Marked stale with the snapshot they came from, plus a recompute offer (CTX-02) |

**Nothing carrying a human decision is ever recomputed automatically.** That is the line, and it is the
one thing this step can get catastrophically wrong (R-09, M-11).

**Staleness mechanism (CTX-02).** Add, via `_MIGRATIONS`, a nullable `bound_snapshot_id TEXT` to each
mark-stale table that does not already carry the item id in a way that survives supersession — in
practice most already carry `item_id`, which **is** the snapshot id (P-06), so the mechanism is mostly a
**read-time derivation**, not new columns:

```
is_stale(artefact) := dq_items[artefact.item_id].snapshot_status == 'superseded'
```

That is deliberately the cheapest correct definition: it needs no new column, cannot drift, and is exactly
CTX-02's meaning. Where an artefact spans several snapshots (a run over a reference + current pair), it is
stale if **any** bound snapshot is superseded. Expose it through one helper
(`assets.staleness.is_stale(...)` / `annotate_stale(rows)`) used by **every** read model — never
re-implemented per screen. The stale badge renders: "From snapshot `<label>` (superseded on `<date>`)" plus
a **Recompute** action.

**CTX-01's automatic path.** `supersede_version_set` and `add_snapshot` (Step 3) both end by calling one
new function `assets.refresh_derived(asset_id, actor, reason)`, which recomputes the *Recompute* class for
the asset's current active set and leaves the *Mark stale* class alone. Because staleness is derived at
read time, nothing needs to be written to mark it. `refresh_derived` is:

- **Idempotent** — running it twice produces identical rows (it is a recompute, not an append).
- **Read-only against user data** (rule 8) — it re-reads the snapshot's stored table cache, never the
  original upload, and never writes to either.
- **The single implementation** that CTX-03's Refresh button also calls, so the manual and automatic paths
  cannot diverge (C-45's "complement, not alternative").

**CTX-03.** A "Refresh" control on the Data Sourcing context bar and on the asset catalogue row (Step 8),
calling `POST /api/v2/assets/{asset_id}/refresh` → `refresh_derived`. It must be usable even when nothing
changed (its purpose is the case where an *external* input changed) and must report what it recomputed.

**CTX-04 / CTX-06 — the intent-conditional carry-forward.** This is FNC-01's `finalize_item` change.
Step 3 already moved target/use_case storage onto `dq_assets`; Step 6 adds the conditional:

```
on add_snapshot(intent):
  fresh             -> target_variable = NULL, use_case = NULL        (CTX-04)
  full_replacement  -> target_variable = NULL, use_case = NULL        (CTX-04)
  add_period        -> leave both exactly as they are                 (CTX-06, D-24)
```

Frontend: `DataSourcing.jsx`'s `useState("IFRS9")` at :176 becomes `useState("")`, and the
`if (!target && cols.includes("default_flag")) setTarget("default_flag")` at :203 is **deleted** — that
line is CTX-04's literal target. The use-case dropdown gains an explicit blank first option
(`<option value="">Not selected</option>`), and there is **no** pre-selected value.

**CTX-05 — where blankness bites.** Ingestion must **not** gate on a blank target (ING-02 holds, and
`finalize_item`'s current contract already says "never a gate"). Instead the **Test Lab scope gate**
refuses the specific diagnostics that need a target, naming the field and offering the place to set it.
**Read `backend/dq_diagnostics/readiness.py` and `guards.py` before implementing** — the refusal must go
through the existing readiness/refusal machinery (FWK-09's guard chain and its "producer not yet
available"/refusal vocabulary), **not** a new ad-hoc check, and it must read as a *not-applicable with a
reason*, never as a failure (FWK-07/FWK-17's honesty rule).

**Permitted paths:** new `backend/assets/refresh.py` + `staleness.py`, `backend/assets/service.py`,
`backend/ai/v2/service.py` (`finalize_item`), `backend/dq_diagnostics/readiness.py` +
`guards.py` (CTX-05 refusal only), `backend/routers/v2.py`, `backend/system_db.py` (additive, only if the
read-time derivation proves insufficient — justify it if so), `ui/src/pages/DataSourcing.jsx`,
`ui/src/pages/TestLab.jsx`, `ui/src/pages/testlab/**` (stale badges, scope-gate message),
`ui/src/components/WorkflowContextBar.jsx`, `ui/src/api/client.js`, `backend/tests/**`, `ui/e2e/**`,
`docs/0.5.0/**`.

### 12.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                 | Layer | ID           |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ------------ |
| 6-A1  | After a Full replacement, every*Recompute*-class artefact reflects the **new** active set, and **no** dependent section anywhere shows the previous dataset's name or figures.                                              | S/E/N | CTX-01       |
| 6-A2  | Every*Mark stale*-class artefact bound to a superseded snapshot renders a stale badge naming its source snapshot and offering Recompute — and is **never** shown as current.                                                     | S/E/N | CTX-02       |
| 6-A3  | Staleness is derived through**one** helper used by every read model; a grep proves no screen re-implements it.                                                                                                                      | N     | CTX-02       |
| 6-A4  | The Refresh control recomputes on demand, works when nothing changed, reports what it recomputed, and calls the**same** function the automatic path calls.                                                                          | A/E/N | CTX-03, C-45 |
| 6-A5  | On Fresh Upload and on Full replacement, target and use case are**blank**; the `default_flag` auto-pick and the `IFRS9` default are gone from the source.                                                                       | E/N   | CTX-04       |
| 6-A6  | On Add period, target and use case are carried forward unchanged.                                                                                                                                                                         | S/E   | CTX-06, D-24 |
| 6-A7  | A blank target never blocks`ready`; the Test Lab scope gate refuses **only** the diagnostics that need one, with a message naming the field and where to set it, rendered as not-applicable-with-a-reason and never as a failure. | S/E/N | CTX-05, C-41 |
| 6-A8  | `refresh_derived` is idempotent (twice → identical rows) and writes nothing to any uploaded file or snapshot content.                                                                                                                  | S/M/N | rule 8       |
| 6-A9  | A replacement leaves prior findings, dispositions, issues and RCA cases**intact** and marked stale — never deleted, never recomputed.                                                                                              | S/N   | R-09, M-11   |
| 6-A10 | Suite green; count recorded.                                                                                                                                                                                                              | —    | —           |

### 12.4 Validation criteria (independent validator)

**Functional.** Take an asset through: upload → profile → run the cross-field diagnostic → get a finding
→ open an issue. Then Full-replace it. Now walk every screen that shows anything about that asset —
Data Sourcing, Inventory, Test Lab coverage board, scope gate, findings, Issue Management, RCA — and
confirm: recomputed things show the new data; decision-bearing things are present, intact, and badged
stale with the snapshot they came from; nothing anywhere shows the old dataset's figures as current. Then
press Refresh and confirm it reports what it did. Then create a fresh asset and confirm target and use
case start blank, and that leaving them blank still reaches `ready` but refuses exactly the diagnostics
that need a target at the scope gate.

**Technical / architecture.** (a) Confirm the two classes are enumerated in
`docs/0.5.0/05-refresh-contract.md` and that the code's behaviour matches the document table-for-table.
(b) Confirm `refresh_derived` is the **only** recompute implementation and that CTX-03 calls it.
(c) Confirm staleness is read-time derived (or, if a column was added, that the justification is written
down). (d) Confirm CTX-05 goes through the existing readiness/guard machinery rather than a new check.
(e) Confirm nothing in the Mark-stale class is written by `refresh_derived` — inspect the code, then prove
it with 6-A9.

**Automated tests to run/add.** New: `backend/tests/test_refresh.py` (CTX-01 recompute set, idempotence,
read-only), `test_staleness.py` (CTX-02 derivation, multi-snapshot artefacts, one-helper assertion),
`test_ctx_defaults.py` (CTX-04/CTX-06 per intent), `test_scope_gate_target.py` (CTX-05 refusal shape).
Run: full pytest; `verify_plan8.py`; `npm run check:reachability`; `npm run test:e2e`.

**Playwright scenarios.**

- (E-6.1) Full journey: upload → run → finding → issue → Full replacement → assert the stale badge on the
  finding **with its snapshot label**, assert the recomputed inventory shows the new data, assert the old
  figures appear nowhere as current.
- (E-6.2) Fresh Upload: target and use case both blank on arrival; `ready` still reached; scope gate
  refuses the target-dependent diagnostic with a readable reason.
- (E-6.3) Add period: target and use case still populated afterwards.
- (E-6.4) Refresh with nothing changed: reports success and what it recomputed.

**Adversarial / negative-path cases.**

1. **CTX-02 bites-check:** remove the staleness derivation → E-6.1 must fail. Restore.
2. **A finding bound to two snapshots**, one superseded, one active → stale (any-bound-superseded rule).
3. **Restore the previous version** (AST-09) → artefacts bound to the restored set become **not** stale
   again, and artefacts bound to the now-superseded set become stale. Staleness must follow the model,
   not a one-way flag — this is the case a boolean column gets wrong and a derivation gets right.
4. **Refresh during an in-flight diagnostic run** → refuse or queue; never corrupt a run's frozen
   manifest (0.4.0's freeze-on-Run guarantee must hold).
5. **`refresh_derived` on an asset with zero active snapshots** (everything superseded, nothing restored)
   → clean no-op with a stated reason, not a crash.
6. **Blank target + a diagnostic that does NOT need a target** → must still run. CTX-05 blocks only what
   needs one; blocking more is a failure.
7. **A target column that no longer exists after a replacement** → the asset's `target_variable` must be
   cleared (CTX-04 clears on replacement anyway) and the scope gate must not reference a phantom column.
8. **Idempotence under concurrency:** two Refresh calls at once → identical final rows, no duplicate
   inventory rows.

### 12.5 Dependencies

- **Step 3** (snapshot statuses, supersede/restore) and **Step 4** (the intents that change the active
  set, and `add_snapshot`'s call site for `refresh_derived`).
- **Step 5** (the context bar hosts CTX-03's Refresh control).
- `docs/0.5.0/05-refresh-contract.md` (task 6.1) committed before any recompute code is written.
- `backend/dq_diagnostics/readiness.py` and `guards.py` read before CTX-05 is implemented.

**Commit:** `fix(context)!: recompute derived data on active-set change, mark superseded-bound artefacts stale, blank target/use-case defaults`

---

## 13. Step 7 — AST-16…19: version diff and fingerprinting <a id="step-7"></a>

> Requirements §13 step 7. Needs the fingerprints written during step 4's profiling pass; nothing to diff
> before that. Governed by **A-Q01**: tiers ① and ② are MUST, tier ③ is SHOULD.

### 13.1 Scope

| ID     | Priority (after A-Q01)                   | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AST-16 | ① ②**MUST**, ③ **SHOULD** | A difference has three tiers, each answerable**without re-reading the data**. ① **Schema** — columns added, removed, likely-renamed, reordered, and type changes (`limit_amount: numeric → text`). ② **Shape** — row count, column count, period coverage, file size, snapshot count. ③ **Distribution** — per column: null rate, distinct count, min/max, mean and standard deviation for numerics, top-*k* categories for categoricals. |
| AST-17 | MUST                                     | **Efficiency comes from fingerprinting at ingest, never from row-by-row comparison at view time.** A version diff is a fingerprint-to-fingerprint comparison — cost **O(columns), not O(rows)**, independent of dataset size.                                                                                                                                                                                                                                   |
| AST-18 | MUST (as a bound)                        | **Row-level diff is on demand and conditional.** It runs only when the user asks **and** a key column is declared, and reports key sets added / removed / changed using stored row digests. **Never the default view** (OOS-13) — it is the only O(rows) operation in the model.                                                                                                                                                                          |
| AST-19 | SHOULD                                   | The diff is reachable from**everywhere two versions are visible** — the catalogue, the View Existing dropdown, and the restore-previous-version screen — and renders **the same three tiers in the same order everywhere**.                                                                                                                                                                                                                                    |

### 13.2 Architecture and implementation approach

**The write already happened.** Step 4 (P-01) wrote `dq_snapshot_fingerprints` for every column of every
snapshot, inside the profiling pass. **This step reads only.** If a snapshot has no fingerprint row (a
pre-0.5.0 migrated snapshot), the diff must say so honestly — "distribution comparison unavailable for
this snapshot (ingested before fingerprinting)" — and still render tiers ① and ②, which are derivable from
`dq_asset_versions.reference_schema_json` and the snapshot row. **Never** silently re-read the file to
backfill: that would break AST-17's whole premise and rule 8.

**New module `backend/assets/diff.py`.**

```
diff_versions(asset_id, version_a, version_b) -> {
  tier1_schema: {                                    # AST-16 ①  (MUST)
    tables_added: [t], tables_removed: [t],          # A-Q03: Database asset
    per_table: { t: { added: [c], removed: [c],
                      likely_renamed: [{from, to, confidence, evidence}],
                      reordered: [{column, from_index, to_index}],
                      type_changes: [{column, from, to}] } } },
  tier2_shape: {                                     # AST-16 ②  (MUST)
    row_count: {a, b, delta}, column_count: {a, b, delta},
    period_coverage: {a: {start, end}, b: {...}},    # or labels for a 'none'-basis asset
    file_size: {a, b}, snapshot_count: {a, b} },
  tier3_distribution: {                              # AST-16 ③  (SHOULD, A-Q01)
    available: bool, unavailable_reason: str | None,
    per_column: { "t.c": { null_rate: {a,b}, distinct_count: {a,b},
                           min: {a,b}, max: {a,b}, mean: {a,b}, stddev: {a,b},
                           top_k: {a,b}, histogram_shift: float | None } } },
  row_level: { available: bool, reason: str }         # AST-18: never computed here
}
```

- **Tier ① reuses `schema_check.compare`** from Step 4 — it already computes added/removed/renamed/type
  changes and the Database table-set vocabulary (UPL-29). **Do not write a second comparator.** The diff
  adds `reordered` (index comparison, which the upload-time check does not need) on top of it. One
  comparator, two callers.
- **Tier ② reads the version's snapshot rows** — `row_count`, `column_count` (already on the snapshot per
  AST-13), the period span from `min(start_date)`/`max(end_date)` across the version's snapshots (or the
  label set for a `none`-basis asset), `file_size` from the stored file's size on disk (a `stat()`, not a
  read), and `snapshot_count`.
- **Tier ③ is a join over `dq_snapshot_fingerprints`** for the two versions' snapshot sets. Where a
  version has several snapshots, define the comparison basis explicitly and document it: **compare the
  version's most recent snapshot per AST-11's ordering**, and state that in the rendered output ("comparing
  v1's latest snapshot `Jan–Mar` with v2's latest snapshot `Apr–Jun`"). Do **not** silently average across
  snapshots — an unstated aggregation is a lie about what the number means.
- `histogram_shift` is a single scalar computed from the two stored fixed-bin histograms (a bin-wise
  absolute difference sum, or PSI-shaped if the bins align). **Name it for what it is** and do not present
  it as a statistical test — there is no diagnostic here (FWK-07's honesty rule; this step ships no
  diagnostic and must not look like one).

**AST-17's efficiency is an invariant, not a claim (M-6).** The diff path must never open a data file. Prove
it: `test_version_diff.py` monkeypatches `service._read_table`, `service._read_tabular`, `pd.read_csv`,
`pd.read_excel` and `sqlite3.connect` on the item-DB path to raise, then runs a complete three-tier diff on
a fixture with 5 tables × 40 columns × 100k rows and asserts it completes. Additionally assert the query
count scales with columns, not rows: run the same diff against a 100-row and a 100k-row fixture and assert
identical query counts.

**AST-18 — build the bound, not the feature.** OOS-13 puts row-level diff out of scope as a **default
view**; AST-18 keeps it as an on-demand, key-gated capability. This step ships:

- The **refusal**: `row_level.available=false` with a reason when no key column is declared, and no UI
  affordance that runs it by default.
- Row digests: **only if Step 4 stored them.** Step 4's fingerprint table has no row-digest column, so the
  honest disposition for 0.5.0 is: **AST-18's row-level diff is not implemented; the refusal path and the
  key-column declaration are.** Record that explicitly in the traceability index as `AST-18: bound implemented, computation deferred` with the reason (OOS-13 makes it non-default; no digest substrate was
  captured; capturing digests for a 100k-row snapshot is an O(rows) write that AST-17 explicitly rules out
  of the ingest path). **Do not silently drop it and do not silently build it** — rule 14.

**AST-19 — one component, three mount points.** `ui/src/components/VersionDiff.jsx` renders the three tiers
in the fixed order ①②③ with the same headings, and is mounted from: the asset catalogue (Step 8), the View
Existing dropdown row (a "compare versions" affordance), and the restore-previous-version screen (Step 4's
UPL-19 surface). One component, one order, everywhere — asserted by a test that all three mount points
import the same module.

**Permitted paths:** new `backend/assets/diff.py`, `backend/assets/schema_check.py` (the `reordered`
extension), `backend/routers/v2.py`, new `ui/src/components/VersionDiff.jsx`,
`ui/src/components/AssetPicker.jsx` (the compare affordance), `ui/src/pages/DataSourcing.jsx` (the restore
screen mount), `ui/src/api/client.js`, `backend/tests/**`, `ui/e2e/**`, `docs/0.5.0/**`.
**Forbidden:** `backend/ai/v2/service.py` (this step reads; it must not touch the ingest path), any new
write to `dq_snapshot_fingerprints`.

### 13.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                      | Layer | ID               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ---------------- |
| 7-A1  | Tier ① reports added, removed, likely-renamed (with confidence and evidence), reordered, and type changes rendered as`col: from → to`. For a Database it also reports table added/removed. | S/U   | AST-16 ①        |
| 7-A2  | Tier ② reports row count, column count, period coverage, file size and snapshot count for both versions with deltas.                                                                          | S/U   | AST-16 ②        |
| 7-A3  | Tier ③ reports per-column null rate, distinct count, min/max, mean and stddev for numerics and top-*k* for categoricals, and **states which snapshots it compared**.                  | S/U   | AST-16 ③, A-Q01 |
| 7-A4  | Tier ③ on a pre-0.5.0 migrated snapshot renders`available=false` with an honest reason, and tiers ① and ② still render.                                                                   | S/N   | AST-17           |
| 7-A5  | **The diff never reads snapshot data.** With every data reader monkeypatched to raise, a full three-tier diff completes.                                                                 | N     | AST-17, M-6      |
| 7-A6  | Query count is identical for a 100-row and a 100k-row fixture — O(columns), not O(rows).                                                                                                      | N     | AST-17           |
| 7-A7  | Row-level diff refuses with a reason when no key column is declared, and there is**no** default-view path to it.                                                                         | A/N   | AST-18, OOS-13   |
| 7-A8  | The same`VersionDiff` component is mounted from the catalogue, the View Existing row, and the restore screen, rendering tiers in the same order.                                             | E/N   | AST-19           |
| 7-A9  | Tier ① is computed by the**same comparator** Step 4's schema check uses — one implementation, two callers.                                                                             | N     | AST-16, DRY      |
| 7-A10 | Suite green; count recorded.                                                                                                                                                                   | —    | —               |

### 13.4 Validation criteria (independent validator)

**Functional.** Build an asset with two versions differing in every dimension the tiers name: one column
dropped, one added, one renamed, one retyped, two reordered, a different row count, a different period
span, a shifted distribution on one numeric column, and (for a Database) one table added and one removed.
Open the diff from all three mount points and confirm the three tiers read identically in all three, and
that every one of those nine differences is reported correctly and nothing is invented.

**Technical / architecture.** (a) Confirm the diff reads only `dq_asset_versions`,
`dq_items`, `dq_snapshot_fingerprints`, `dq_item_tables` and `stat()` — no data reads. (b) Confirm tier ①
delegates to `schema_check.compare`. (c) Confirm the multi-snapshot comparison basis is **stated in the
output**, not implicit. (d) Confirm `histogram_shift` is named descriptively and is not presented as a
diagnostic verdict. (e) Confirm the AST-18 disposition is recorded in the traceability index with its
reason, not silently absent.

**Automated tests to run/add.** New: `backend/tests/test_version_diff.py` (all three tiers on a crafted
fixture; the no-data-read invariant; the O(columns) query-count invariant; the pre-0.5.0 unavailable case;
the row-level refusal). Run: full pytest; `verify_plan8.py`; `npm run check:reachability`;
`npm run test:e2e`.

**Playwright scenarios.**

- (E-7.1) Open the diff from the catalogue and assert all three tier headings in order, with the nine
  crafted differences visible.
- (E-7.2) Open the diff from the View Existing row and from the restore screen; assert byte-identical tier
  headings and order.
- (E-7.3) Assert no control anywhere triggers a row-level diff without a declared key column.

**Adversarial / negative-path cases.**

1. **No-data-read bites-check:** un-patch one reader and add a data read into the diff path → 7-A5 must
   fail. Restore.
2. **A rename that is really a drop + an add** (`limit_amount` removed, `unrelated_flag` added, different
   types) → must be reported as one removed + one added, **not** as a rename. A false rename claim is worse
   than none.
3. **A pure reorder** (identical names and types, different positions) → reported as reordered only, never
   as added + removed.
4. **Diff a version against itself** → all tiers report no differences, cleanly.
5. **Diff v1 against v3 (non-adjacent)** → works; the diff is between two versions, not between
   consecutive ones.
6. **A version with zero snapshots** (possible only via a hostile DB state) → clean refusal with a reason.
7. **A column that is numeric in v1 and text in v2** → tier ① reports the type change and tier ③ must
   **not** compare mean/stddev across incomparable types; it must state that the distribution is not
   comparable.
8. **A 100% null column in one version** → null_rate 1.0 reported, no division-by-zero, no crash.
9. **A `none`-basis asset** → tier ② reports the label set, not a period span, and does not render an empty
   date range.
10. **A 2000-column asset** → the diff completes and the UI renders without freezing (paginate or
    virtualise; do not render 2000 rows eagerly).

### 13.5 Dependencies

- **Step 4** — the fingerprints and `schema_check.compare` must exist.
- **Step 3** — `dq_asset_versions.reference_schema_json` (both the current and the retained superseded
  schema, AST-14) is the tier-① substrate.
- Step 8's catalogue is one of AST-19's mount points; if Step 8 has not landed, mount the other two and
  add the catalogue mount in Step 8 (record it as a Step 8 task, do not leave it unmounted silently).

**Commit:** `feat(assets): three-tier version diff over ingest-time fingerprints (O(columns), never O(rows))`

---

## 14. Step 8 — ANL: usage event log and catalogue <a id="step-8"></a>

> Requirements §13 step 8. Instruments the flows built in steps 4–6; instrumenting them earlier would mean
> instrumenting them twice. **Capture only** — D-27, ANL-07, OOS-12, P-15. Bounded by **A-Q04** to the
> asset graph.

### 14.1 Scope

| ID     | Priority (after A-Q04)                       | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------ | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ANL-01 | PROPOSED→adopted,**asset graph only** | Every addressable object in the asset graph carries a**stable, typed, never-reused ID**, formed the same way across the product: **asset, version, snapshot, variable (asset + column), dictionary version**. The other 16 object types land as each module is next touched (A-Q04).                                                                                                                                                                                                   |
| ANL-02 | PROPOSED→adopted                            | An ID is stable for the life of the object it names — never re-issued to a**different** object while that object or anything derived from it still exists. Carries AST-01's factory-reset exception (D-29).                                                                                                                                                                                                                                                                                 |
| ANL-03 | PROPOSED→adopted                            | **One append-only usage event log, written by every module**, is the single source for all analysis. Each event carries: **event type, actor, timestamp, the object's typed ID, and the workflow context** it happened in. Analytics are computed **from this log, never from the live tables**, so a deleted asset does not erase its own history.                                                                                                                              |
| ANL-04 | PROPOSED→adopted                            | The measures the note asks for —**overall usage, usage per user, usage over time, test preferences, diagnostic preferences, use-case frequency** — all fall out of ANL-03 given the event types are chosen **deliberately** rather than added ad hoc.                                                                                                                                                                                                                                |
| ANL-05 | PROPOSED→adopted                            | Also capture:**reuse depth** (existing asset selected vs new one created), **time-to-ready per upload**, **override rate** (inferred type overruled, schema warning overridden), **abandonment points** in the upload flow, **snapshot cadence per asset**, **dictionary coverage trend**, **finding disposition split** (confirmed vs dismissed, per diagnostic), **rerun rate after a replacement**, **superseded-set restore frequency**. |
| ANL-06 | PROPOSED→adopted                            | The catalogue is a**first-class screen**: every asset, its versions, its active and superseded snapshots, its dictionary binding, its usage summary, and a link into the AST-16 version diff.                                                                                                                                                                                                                                                                                                |
| ANL-07 | **LATER**                              | Analytics**reporting** surfaces — dashboards, trend charts, exports — are **out of scope** (OOS-12, D-27, P-15). 0.5.0 guarantees the data is captured correctly and completely; presenting it can follow.                                                                                                                                                                                                                                                                           |

### 14.2 Architecture and implementation approach

**The event log (ANL-03). One table, one writer, append-only.**

```sql
CREATE TABLE IF NOT EXISTS usage_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,      -- from the closed vocabulary below (ANL-04's "deliberately")
    actor TEXT NOT NULL,           -- username; 'system' for machine-initiated
    at TEXT NOT NULL,              -- IST ISO with offset, via db.now_ist()
    object_type TEXT NOT NULL,     -- asset | version | snapshot | variable | dictionary_version
    object_id TEXT NOT NULL,       -- the TYPED, human-quotable id (ANL-01/02)
    workflow_context TEXT,         -- the asset/snapshot context the action happened in
    detail_json TEXT               -- event-specific payload; never required for a measure to work
);
CREATE INDEX IF NOT EXISTS ix_usage_events_type_at ON usage_events(event_type, at);
CREATE INDEX IF NOT EXISTS ix_usage_events_object  ON usage_events(object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_usage_events_actor   ON usage_events(actor, at);
```

- **Append-only is enforced, not documented.** `backend/analytics/events.py::record_event(...)` is the
  **only** writer; it inserts and never updates. A negative test greps the whole backend for
  `UPDATE usage_events` / `DELETE FROM usage_events` and fails on a hit; a second test asserts
  `record_event` is the only function that inserts into the table. The single exception is a factory reset,
  which removes the rows along with the objects (ANL-02/D-29) — and that exception is asserted explicitly,
  so it cannot be widened by accident.
- **The five required fields are never null.** A test asserts no row has a NULL in `event_type`, `actor`,
  `at`, `object_type` or `object_id`. `record_event` raises rather than writing a partial event — a
  half-recorded event is worse than none because it silently skews every measure computed from it.
- **P-08's two streams.** `dq_asset_events` (Step 3) is the audit-of-record ADM-06 renders;
  `usage_events` is the measurement log. Where an action is both (asset created, snapshot added, version
  superseded, version restored, alias renamed), **one helper writes both, in one transaction**, so they
  cannot drift. Everything else — a selection, an override, an abandonment, a run, a disposition — is
  `usage_events` only.

**The event-type vocabulary — closed, chosen deliberately (ANL-04's condition).** Derive it *from the
measures*, not from the code: an event type exists because a named measure needs it.

| Event type                                                          | Written by                                            | Measures it feeds                                                                    |
| ------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `asset_created`                                                   | Step 3`create_asset`                                | overall usage, usage per user, usage over time, reuse depth (denominator)            |
| `asset_selected`                                                  | Step 5`AssetPicker` selection                       | **reuse depth** (numerator), usage per user                                    |
| `alias_renamed`                                                   | Step 3`rename_alias`                                | overall usage                                                                        |
| `snapshot_added`                                                  | Step 4`add_snapshot`                                | **snapshot cadence per asset**, usage over time                                |
| `snapshot_ready`                                                  | Step 4 status derivation reaching`ready`            | **time-to-ready** (paired with `upload_started`)                             |
| `upload_started`                                                  | Step 4 STEP 2 first byte accepted                     | time-to-ready,**abandonment points** (denominator)                             |
| `upload_step_reached` (`detail.step ∈ 1..7`)                   | Step 4, each section becoming active                  | **abandonment points**                                                         |
| `upload_abandoned`                                                | Step 4, on leaving with a staged-but-uncompleted flow | **abandonment points**                                                         |
| `type_override`                                                   | Step 4 STEP 3 override                                | **override rate** (inference trust)                                            |
| `schema_warning_overridden`                                       | Step 4 UPL-22 tick                                    | **override rate**                                                              |
| `version_created` / `version_superseded` / `version_restored` | Step 3                                                | **superseded-set restore frequency**, **rerun rate after a replacement** |
| `dictionary_version_created` / `dictionary_bound`               | Step 3/4                                              | **dictionary coverage trend**                                                  |
| `refresh_requested`                                               | Step 6 CTX-03                                         | overall usage                                                                        |
| `diagnostic_run_started` (`detail.diagnostic_id`)               | Test Lab run path                                     | **test preferences**, **diagnostic preferences**, rerun rate             |
| `finding_disposed` (`detail.disposition`)                       | Test Lab disposition path                             | **finding disposition split per diagnostic**                                   |
| `use_case_set` (`detail.use_case`)                              | Step 6`finalize_item`                               | **use-case frequency**                                                         |
| `catalogue_viewed` / `version_diff_viewed`                      | Step 7/8 surfaces                                     | overall usage                                                                        |

Adding a type later is fine; adding one **without a measure that needs it** is the ad-hoc accretion ANL-04
warns against, so the vocabulary lives in one module with the measure named in a comment per type.

**Proving capture without presenting (P-15, M-12).** `backend/analytics/measures.py` implements every
measure named in ANL-04 and ANL-05 as a **pure query function** over `usage_events` —
`overall_usage()`, `usage_per_user()`, `usage_over_time(bucket)`, `test_preferences()`,
`diagnostic_preferences()`, `use_case_frequency()`, `reuse_depth()`, `time_to_ready()`,
`override_rate()`, `abandonment_points()`, `snapshot_cadence()`, `dictionary_coverage_trend()`,
`finding_disposition_split()`, `rerun_rate_after_replacement()`, `restore_frequency()`.
`test_usage_events.py` seeds a synthetic event log with known answers and asserts each function returns
them. **No route exposes these functions and no UI calls them** (OOS-12) — they exist so that "capture is
complete" is a proven claim rather than a hope, and so that when ANL-07 is pulled the presentation layer
has nothing to compute.

**The catalogue screen (ANL-06).** New `ui/src/pages/AssetCatalogue.jsx`, a routed page:

- One row per asset: `display_name` (system ID + alias), kind, current version, active snapshot count,
  superseded snapshot count, dictionary binding state (AST-21's `yes`/`thin`/`absent`, rendered through a
  human label — M-2), last upload, lifecycle status.
- Expanding a row shows its versions and, within each, its snapshots — **active and superseded, both
  visible** (this is the one surface where superseded snapshots are meant to be seen; AST-08's "hidden from
  normal pickers" is about pickers, and the catalogue is the audit-facing view).
- A **usage summary** per asset, computed from `usage_events`: times selected, snapshots added, last
  activity, restore count. This is a **summary, not a dashboard** — counts and dates in a table, no chart,
  no trend line, no export (OOS-12, P-15). If it grows an axis, it has become a dashboard.
- **UPL-28's retrievable completion summary** is reachable here, per snapshot.
- A **link into the AST-16 version diff** (AST-19's third mount point) and the **UPL-19 restore** action.
- Backend: `GET /api/v2/catalogue` and `GET /api/v2/catalogue/{asset_id}`, reading the Step 3 read models
  plus `measures.py`'s per-asset summary.

**ANL-01/ANL-02's typed IDs — what is actually new here.** Steps 3 and 4 already allocate asset, version,
snapshot and dictionary-version IDs (`DS`/`DB`, `VR`, `SN`, `DV`). This step adds the fifth — **variable
(asset + column)** — as a *derived, deterministic* typed identifier rather than a counter row, because a
variable has a natural key: `VAR:<asset system_id>:<table>:<column>`. Deterministic derivation satisfies
ANL-02 (stable for the life of the object; a renamed column is a *different* variable, which is correct)
without a sixth counter to reset. Record this as the shipped form. **The other 16 ANL-01 types are
explicitly out of scope** (A-Q04) and a test asserts no ID is minted for them in 0.5.0 — the scope boundary
must be enforceable, not merely intended.

**Permitted paths:** new `backend/analytics/**`, `backend/system_db.py` (additive),
`backend/assets/service.py` + `backend/ai/v2/service.py` + `backend/routers/v2.py` (the `record_event`
call sites), `backend/dq_diagnostics/runner*.py` (run/disposition call sites only),
new `ui/src/pages/AssetCatalogue.jsx`, `ui/src/App.jsx` (route), `ui/src/api/client.js`,
`backend/tests/**`, `ui/e2e/**`, `docs/0.5.0/**`.
**Forbidden:** any charting library; any export endpoint; `ANL-07` in any form.

### 14.3 Acceptance criteria

| #     | Criterion                                                                                                                                                                                      | Layer | ID                   |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------------------- |
| 8-A1  | Every event row carries a non-null event type, actor, timestamp, typed object ID and object type;`record_event` raises rather than writing a partial event.                                  | S/N   | ANL-03               |
| 8-A2  | The log is**append-only**: no `UPDATE`/`DELETE` against `usage_events` exists anywhere except the factory-reset path, and `record_event` is the only inserter.                   | N     | ANL-03               |
| 8-A3  | Every one of the ANL-04 and ANL-05 measures is computable from a seeded log, with the expected answers asserted.                                                                               | U/P   | ANL-04, ANL-05, M-12 |
| 8-A4  | Every event type in the closed vocabulary is actually written by a real code path — a test drives each flow and asserts the event appears. An event type with no writer is a failure.         | S/N   | ANL-03, ANL-04       |
| 8-A5  | Superseding an asset's snapshots does not remove any of its events; a factory reset does (and that is the only removal path).                                                                  | S/N   | ANL-03, ANL-02, D-29 |
| 8-A6  | Exactly**five** typed ID families exist; a test asserts no ID is minted for any of ANL-01's other 16 object types in 0.5.0.                                                              | N     | ANL-01, A-Q04        |
| 8-A7  | The catalogue lists every asset with versions, active**and** superseded snapshots, dictionary binding state, and a usage summary; it links into the version diff and the restore action. | E     | ANL-06, AST-19       |
| 8-A8  | UPL-28's completion summary is retrievable from the catalogue per snapshot.                                                                                                                    | A/E   | UPL-28               |
| 8-A9  | **No presentation surface ships**: a structural assertion that no chart/graph/export component or endpoint exists under the catalogue or analytics paths.                                | N     | ANL-07, OOS-12, P-15 |
| 8-A10 | Every status and state in the catalogue renders through a human label.                                                                                                                         | E/N   | M-2                  |
| 8-A11 | Suite green; count recorded.                                                                                                                                                                   | —    | —                   |

### 14.4 Validation criteria (independent validator)

**Functional.** Drive every instrumented flow once — create an asset, select an existing one, add a period,
replace, restore, override a type, override a schema warning, abandon an upload at step 3, run a
diagnostic, dispose of a finding, press Refresh, view the catalogue, view a diff. Then read the raw
`usage_events` table and confirm every one of those actions produced exactly one well-formed event with the
right actor and the right typed ID. Then open the catalogue and confirm it reads as an inventory, not a
dashboard.

**Technical / architecture.** (a) Confirm one writer and one table. (b) Confirm the two streams (P-08) are
distinct and that dual-write actions write both in one transaction. (c) Confirm the event vocabulary lives
in one module with a named measure per type. (d) Confirm `measures.py` reads **only** `usage_events` — a
measure that falls back to a live table breaks ANL-03's guarantee and is an automatic fail. (e) Confirm
the variable ID is deterministic and needs no counter. (f) Confirm no chart library entered
`ui/package.json`.

**Automated tests to run/add.** New: `backend/tests/test_usage_events.py` (field completeness,
append-only, every-type-has-a-writer, factory-reset removal), `test_measures.py` (each ANL-04/ANL-05
measure against a seeded log), `test_catalogue.py` (A — payload shape, superseded visibility, per-asset
summary). Run: full pytest; `verify_plan8.py`; `npm run check:reachability`; `npm run build`;
`npm run test:e2e`.

**Playwright scenarios.**

- (E-8.1) Catalogue: assert an asset row with its versions expanded, both active and superseded snapshots
  visible and labelled, and the dictionary state shown as a human label.
- (E-8.2) From the catalogue, open the version diff (AST-19's third mount point) and assert the same three
  tier headings in the same order as Step 7's other two mounts.
- (E-8.3) From the catalogue, open a snapshot's completion summary (UPL-28).
- (E-8.4) Assert **no** chart, canvas, svg-chart or "Export" control exists on the catalogue (OOS-12).

**Adversarial / negative-path cases.**

1. **Append-only bites-check:** add an `UPDATE usage_events` statement anywhere → 8-A2 must fail. Remove.
2. **Partial event:** call `record_event` with a missing actor → raises; assert no row was written.
3. **Event type with no writer:** add a type to the vocabulary with no call site → 8-A4 must fail.
4. **A measure that cheats:** point one measure at a live table instead of the log → the review must catch
   it, and a test asserting the measures module imports no live-table accessor should catch it too.
5. **Deleted asset, surviving history:** supersede everything and confirm the asset's events are all still
   present and its measures still compute (ANL-03's whole point).
6. **Factory reset:** confirm events **are** removed, and that this is the only removal path.
7. **Scope creep probe:** attempt to mint an ID for an `issue` or `run` → 8-A6 must fail (A-Q04's boundary
   must be enforceable).
8. **A concurrent double-write:** the same action triggered twice quickly → two events, not one and not
   three; each with a distinct `event_id`.
9. **An event for an object that no longer exists** (a superseded snapshot) → the catalogue and measures
   handle it without a join failure — the log is independent of the live tables by design.
10. **Catalogue with 200 assets** → renders paginated/virtualised; the per-asset summary must not issue one
    query per asset (N+1). Assert the query count.

### 14.5 Dependencies

- **Steps 3, 4, 5 and 6 complete** — every instrumented call site lives in them. Instrumenting before they
  exist means instrumenting twice (§13's own justification).
- **Step 7** — the catalogue is AST-19's third diff mount point.
- A-Q04's boundary must be recorded before the ID work starts, so the scope test can be written first.

**Commit:** `feat(analytics): append-only usage event log + asset catalogue (capture only, no reporting)`

---

## 15. Step 9 — FNC: functional code map <a id="step-9"></a>

> Requirements §13 step 9. **Documentation, not code. Never blocks a build step** (FNC-04). Cheapest to do
> right after the step that touched that area.

### 15.1 Scope

| ID     | Priority | What this step must achieve                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FNC-01 | MUST     | The nine stale`service.py` functions are re-documented **alongside the code change that touches them (Step 4)** — so this is already done by the time Step 9 starts. Step 9 **verifies** it and closes the remaining four (`_read_tabular`, `_classify`, `get_inventory`, `put_inventory`) as re-verified.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| FNC-02 | MUST     | Extend`functional_tools.xlsx` with **one new sheet per mapped code file**, in the workbook's existing pattern, covering the product's other main functional areas: **Test Lab / diagnostics engine** (`backend/dq_diagnostics/*`, `backend/dq_tests/*`, `backend/gx/`, `backend/scoring/*` → `TestLab.jsx` + `testlab/*`); **Knowledge Base** (`kb.py`, `kb_convert.py`, `kb_storage/` → `KnowledgeBase.jsx`); **RCA workflow** (`rca.py`, `ai/rca_checker.py`, `ai/rca_helpers.py` → `IssueRca.jsx`); **Issue Management** (`ai/v2/issues.py` → `IssueManagement.jsx`); **Taxonomy / tagging** (`taxonomy.py`, `seeds/taxonomy_seed.py` → `TagPicker.jsx`); **Admin / platform** (`routers/admin.py`, `system_db.py`'s `reset_demo`/`wipe_all_items`, `tenancy.py` → `Admin.jsx`); **Auth** (`routers/auth.py` → `Login.jsx`/`Profile.jsx`). Group tightly-coupled small files onto one sheet rather than mechanically forcing one sheet per file. |
| FNC-03 | SHOULD   | Every new sheet uses the workbook's existing**six-column shape** — *Sl. No., Important functions, Function purpose, Inputs, Processing, Outputs* — with the file path and purpose in the same **two-line header block** above the table, so the workbook stays one consistent artefact rather than seven sheets in seven shapes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| FNC-04 | MUST     | This is a documentation deliverable and**never gates a build step**. A stale sheet is a defect to fix, not a blocker to work around.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

### 15.2 Architecture and implementation approach

- **The artefact is `functional_tools.xlsx`, not `functional_tools.docx`.** The `.docx` in the repo root is
  a *rendering* with overlapping content (the same 13 functions as one flat table); it is not the source and
  is **not** updated by FNC-01…04 (requirements §15's explicit correction of an earlier draft). **Do not
  edit the `.docx`.**
- **The workbook's shape, verified 3 Aug 2026:** one sheet per mapped code file, the sheet named after the
  file (currently one sheet, `service.py`); a two-line header — the file's path, then its one-line purpose;
  then a six-column table whose "Important functions" cell carries the signature and line numbers. The
  `service.py` sheet holds exactly 13 rows.
- **Do not invent a new format.** FNC-03 exists because seven sheets in seven shapes is worse than one
  sheet. Copy the `service.py` sheet's structure literally.
- **New area sheets, one per sheet-worth of content:**

| Sheet                                                           | Files it maps                                                                                                                                                                                                             | Grouping note                                                                                                                                                                                                                                  |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dq_diagnostics.md`-worth → `diagnostics.py` (or per-file) | `register.py`, `runner.py`, `runner_cross_field.py`, `guards.py`, `manifest.py`, `readiness.py`, `result.py`, `thresholds.py`, `delivery.py`, `profiling_preconditions.py`, `engines/cross_field/*` | Nine+ files; split into**two** sheets if one table becomes unreadable — `diagnostics-core` (register/runner/guards/manifest/readiness/result/thresholds) and `diagnostics-engines` (engines/cross_field/*, delivery, preconditions) |
| `dq_tests`                                                    | `contracts.py`, `global_rules.py`, `param_specs.py`, `registry.py`, `stage1/`, `stage2/`                                                                                                                      | Mostly retirement shims since 0.4.0 Phase 3 — document them**as** shims, honestly                                                                                                                                                       |
| `gx` + `scoring`                                            | `backend/gx/*`, `scoring/health.py`, `criticality.py`, `categories.py`                                                                                                                                            | Two small areas, one sheet                                                                                                                                                                                                                     |
| `kb`                                                          | `kb.py`, `kb_convert.py`, `kb_storage/`                                                                                                                                                                             |                                                                                                                                                                                                                                                |
| `rca`                                                         | `rca.py`, `ai/rca_checker.py`, `ai/rca_helpers.py`                                                                                                                                                                  |                                                                                                                                                                                                                                                |
| `issues`                                                      | `ai/v2/issues.py`                                                                                                                                                                                                       |                                                                                                                                                                                                                                                |
| `taxonomy`                                                    | `taxonomy.py`, `seeds/taxonomy_seed.py`                                                                                                                                                                               |                                                                                                                                                                                                                                                |
| `admin`                                                       | `routers/admin.py`, `system_db.py` (`reset_demo`, `wipe_all_items`, `init_schema`, `_MIGRATIONS`), `tenancy.py`                                                                                             | Include`init_schema`/`_MIGRATIONS` — 0.5.0 makes them the release's central mechanism                                                                                                                                                     |
| `auth`                                                        | `routers/auth.py`                                                                                                                                                                                                       |                                                                                                                                                                                                                                                |
| **new for 0.5.0** `assets`                              | `backend/assets/identity.py`, `service.py`, `schema_check.py`, `diff.py`, `refresh.py`, `staleness.py`                                                                                                        | The spine this release added; documenting it is not optional                                                                                                                                                                                   |
| **new for 0.5.0** `analytics`                           | `backend/analytics/events.py`, `measures.py`                                                                                                                                                                          |                                                                                                                                                                                                                                                |

- **The AI helper/tool layer is deliberately NOT a sheet** (`ai/control_plane.py`, `tool_registry.py`,
  `llm.py`, `code_sandbox.py`, `skills.py`): 0.4.0's PLT-08 already required an explicit typed contract per
  helper in its own docstring, which is this map's job done in-place. A duplicate artefact would only drift
  (requirements FNC-02, verbatim).
- **A checker, so the sheets cannot silently rot.** `backend/tests/test_functional_map.py` (or a standalone
  script if a test dependency on `openpyxl` is unwelcome — check whether it is already a dependency before
  adding one) asserts: every sheet has the two-line header block; every sheet's table has exactly the six
  named columns in order; every sheet name resolves to a file or a documented group; and every function
  named in a sheet still exists in the file it claims. **This checker is advisory-reported but
  non-blocking** (FNC-04) — it must never fail a release gate; it produces a defect list.

**Permitted paths:** `functional_tools.xlsx`, `backend/tests/test_functional_map.py` (advisory),
`docs/0.5.0/**`.
**Forbidden:** `functional_tools.docx`; any product code.

### 15.3 Acceptance criteria

| #    | Criterion                                                                                                                                                                             | Layer | ID                |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------------- |
| 9-A1 | The`service.py` sheet's nine changed functions are current (already refreshed in Step 4) and the remaining four are marked re-verified.                                             | —    | FNC-01            |
| 9-A2 | A sheet exists for each of the seven named areas plus the two new 0.5.0 areas (`assets`, `analytics`).                                                                            | —    | FNC-02            |
| 9-A3 | Every sheet has the two-line header block and exactly the six named columns in order.                                                                                                 | U     | FNC-03            |
| 9-A4 | Every function named in every sheet exists in the file it claims, at a line number within ±10 of the recorded one (or with no line number, if the sheet chooses not to record them). | U     | FNC-01, FNC-02    |
| 9-A5 | `functional_tools.docx` is **unmodified**.                                                                                                                                    | N     | requirements §15 |
| 9-A6 | The checker is**advisory** — it reports and does not fail `ci-local.ps1`.                                                                                                    | N     | FNC-04            |

### 15.4 Validation criteria (independent validator)

**Functional.** Open the workbook. Pick three functions at random from three different new sheets and read
their Inputs/Processing/Outputs against the actual code. If a cell says something the code does not do,
that sheet fails. Then check that a reader who has never seen the codebase could use the `assets` sheet to
find where an asset is created.

**Technical / architecture.** (a) Confirm the six-column shape and the two-line header are identical across
every sheet including the pre-existing `service.py` one. (b) Confirm no sheet documents a deleted function
(Step 1 deleted a lot). (c) Confirm the checker is advisory in `ci-local.ps1`, per FNC-04. (d) Confirm the
helper layer is **absent** by design, with the reason recorded, not absent by omission.

**Automated tests to run.** The advisory checker; then the full blocking set (`ci-local.ps1`,
`verify_plan8.py`, `npm run test:e2e`) purely as a tripwire — this step changes no code, so any failure is
a finding about something else.

**Playwright scenarios.** None. This step touches no UI.

**Adversarial / negative-path cases.**

1. **A sheet documenting a function deleted in Step 1** → the checker must flag it.
2. **A sheet with five columns, or the six in a different order** → flagged.
3. **A sheet whose header block is one line** → flagged.
4. **The checker made blocking** → FNC-04 violated; 9-A6 must fail.
5. **The `.docx` edited** → 9-A5 must fail.
6. **A function that moved line numbers by 200** (a plausible outcome of Step 4) → flagged, so the map is
   refreshed rather than quietly wrong.

### 15.5 Dependencies

- **Step 4** for FNC-01's `service.py` refresh (it happens there, not here — FNC-04).
- All other steps, for the areas they touched. This step is last precisely so nothing it documents is still
  moving.
- It **blocks nothing** (FNC-04). If the release must cut before it completes, cut — and record the
  remaining sheets as a defect list, not as a release blocker.

**Commit:** `docs(fnc): extend functional_tools.xlsx to every functional area + the 0.5.0 asset/analytics layers`

---

## 16. Step 10 — Release acceptance <a id="step-10"></a>

**Objective.** Prove 0.5.0 as a whole and cut the version. No new behaviour.

### Todos

- [ ] 10.1 **Asset-model acceptance:** identity, versions, snapshots, supersede, restore, fixed time
  basis, ordering, retained schema — every AST-01…AST-22 criterion re-run on a **clean factory-reset**
  instance and on a **migrated pre-0.5.0** instance.
- [ ] 10.2 **Upload-flow acceptance (M-3):** all four Playwright journeys (Dataset/Fresh,
  Dataset/Add-period, Dataset/Full-replacement, Database) green on a clean instance.
- [ ] 10.3 **Migration sweep (M-5):** every 0.5.0 migration run twice; canonical schema hash and all
  surviving-row hashes identical; `verify_plan8.py` 28/28; the four PLT-02 columns present.
- [ ] 10.4 **Retirement acceptance:** the reachability gate green and its bites-check recorded; the census
  doc's keep-reasons all still valid.
- [ ] 10.5 **Language sweep (M-2):** run the leak assertion across reset messages, asset/snapshot statuses,
  version-history summaries, stale badges, schema-conflict messages and the catalogue.
- [ ] 10.6 **Analytics acceptance (M-12):** every ANL-04/ANL-05 measure computes from the captured log; no
  presentation surface exists.
- [ ] 10.7 **Determinism acceptance (M-9):** zero model calls in the default suite; seam (a) still off.
- [ ] 10.8 **Hygiene sweep:** the PLT-04 error sanitizer on every new route; an audit event for every
  version event, override, restore and reset; no secret in a log or a response.
- [ ] 10.9 **Invariant-bites ledger:** every invariant in rule 6 has a recorded bites-check with the
  commit it was run against.
- [ ] 10.10 **Docs in the release commit:** `TSD.md`, `README.md`, `USER_GUIDE.md`, `TODO.md`,
  `docs/0.5.0/*`, the traceability matrix at real final statuses,
  `guide/Workflows_and_Testing_Guide.md`, `functional_tools.xlsx`.
- [ ] 10.11 **Release note** `docs/0.5.0/09-release-notes.md`: the asset-model replacement and what it
  supersedes (C-40/D-25); the seven-step upload flow; the retirement tranche with counts; the adopted
  Q-01…Q-06 answers **flagged as adopted-without-requester-confirmation**; the AST-18 deferral; the
  accepted unbounded growth of retained superseded snapshots (R-06); rollback limitations.
- [ ] 10.12 `./Set-AppVersion.ps1 -Version 0.5.0`; commit. **Do not tag, push or deploy** (OOS-18).

### Test criteria

| #     | Criterion                                                                                                                                                   | Layer |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| 10-T1 | M-1…M-12 all pass, each with its command and exit code recorded.                                                                                           | —    |
| 10-T2 | Every requirement ID in requirements-0.5.0.md has a status in §17; zero`not started`; `PROPOSED`/`LATER`/deferred IDs carry an explicit disposition. | —    |
| 10-T3 | The full Playwright suite passes on a clean factory-reset instance.                                                                                         | E     |
| 10-T4 | Migrations twice; hashes identical;`verify_plan8.py` 28/28.                                                                                               | M     |
| 10-T5 | `VERSION`, `app_version.py` and the UI package metadata all read `0.5.0`.                                                                             | U     |
| 10-T6 | A pre-0.5.0 DB restored from the Step 0 backup boots, migrates, and every pre-existing item is still usable.                                                | M/E   |

**Exit gate:** every step spotless; every metric green; the release note states what was adopted without a
requester and what was deferred.
**Commit:** `release: Archimedes 0.5.0 — the asset model: versioned, reusable Databases and Datasets`

---

## 17. Traceability index <a id="traceability-index"></a>

Every requirement ID, its step, and its primary evidence. Maintained as work lands; **zero rows may read
`not started` at Step 10** (M-4). `PROPOSED`/`LATER` IDs carry an explicit disposition, never a blank.

| Requirement block                                                       | Step      | Primary evidence                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| RET-01…RET-06                                                          | 1         | 1-A1…1-A7;`check-reachability` gate; `docs/0.5.0/01-ret-census.md`                                                                                                                                                                                      |
| ADM-01…ADM-05, TAX-01                                                  | 2         | 2-A1…2-A8;`test_reset_language.py`; `test_taxonomy_seed.py`                                                                                                                                                                                             |
| AST-01…AST-15, AST-20…AST-22, ADM-06, ADM-07, ANL-01/02 (asset graph) | 3         | 3-A1…3-A19;`test_asset_identity.py`, `test_asset_model.py`, `test_asset_migration.py`, `test_admin_version_history.py`                                                                                                                              |
| UPL-01…UPL-29, AST-17 (capture), FNC-01                                | 4         | 4-A1…4-A30;`test_upload_flow.py`, `test_schema_check.py`, `test_headers.py`, `test_fingerprint.py`; E-4.1…E-4.5                                                                                                                                    |
| SRC-01…SRC-13                                                          | 5         | 5-A1…5-A14;`test_assets_list.py`; E-5.1…E-5.6                                                                                                                                                                                                            |
| CTX-01…CTX-06                                                          | 6         | 6-A1…6-A10;`test_refresh.py`, `test_staleness.py`, `test_ctx_defaults.py`, `test_scope_gate_target.py`; E-6.1…E-6.4                                                                                                                                |
| AST-16, AST-17 (render), AST-19                                         | 7         | 7-A1…7-A10;`test_version_diff.py`; E-7.1…E-7.3                                                                                                                                                                                                           |
| **AST-18**                                                        | 7         | **Bound implemented, computation deferred** — refusal path and key-column declaration ship; row digests are not captured (AST-17 forbids an O(rows) ingest write; OOS-13 makes it non-default). Disposition recorded, not silently dropped (rule 14). |
| ANL-03…ANL-06, UPL-28                                                  | 8         | 8-A1…8-A11;`test_usage_events.py`, `test_measures.py`, `test_catalogue.py`; E-8.1…E-8.4                                                                                                                                                              |
| **ANL-07**                                                        | —        | **LATER / out of scope** — OOS-12, D-27, P-15. Capture proven by `test_measures.py`; no presentation surface ships (8-A9).                                                                                                                          |
| FNC-02, FNC-03, FNC-04                                                  | 9         | 9-A1…9-A6; advisory`test_functional_map.py`                                                                                                                                                                                                               |
| **OOS-12…OOS-18**                                                | —        | Negative assertions: 8-A9 (dashboards), 7-A7 (row-level default), 3-A6/R-06 (no merge, no snapshot delete), 4-A17 (no split at upload), no lineage work, no deployment                                                                                       |
| Q-01…Q-06                                                              | 3 (§3.1) | **Adopted** per requirements §11; recorded as A-Q01…A-Q06; flagged in the release note as adopted without requester confirmation                                                                                                                     |
| C-38…C-49, D-23…D-31                                                  | —        | Resolutions and decisions carried into the steps that implement them; cited inline throughout §§7–15                                                                                                                                                      |
| P-01…P-15                                                              | —        | Plan decisions; §3.2                                                                                                                                                                                                                                        |

---

## 18. If you get stuck <a id="if-you-get-stuck"></a>

- **A requirement conflicts with the code and neither obviously wins.** Do not pick silently. Write both
  options and your recommendation into `docs/0.5.0/07-decisions.md` as a new `P-nn`, implement the
  recommendation, and flag it in the commit message.
- **A test fails and the fix looks like changing the test.** Re-read operating rule 7. If the test encodes
  superseded 0.4.0 behaviour (the standing cases: `test_ingest.py`'s sibling re-upload, `test_delivery.py`'s
  family semantics, `upload-workflow.spec.js`'s delivery testids), change it deliberately, in the same
  commit as the cause, with the new assertions stated. Otherwise the code is wrong.
- **A migration looks like it needs a `DROP` or a `RENAME`.** It does not. Operating rules 3 and 4. Add a
  column, backfill it, and leave the old one — `baseline_delivery_id` is the worked example.
- **The seam map (task 3.1) contradicts this plan's stated facts about `as_of_date` /
  `baseline_delivery_id` / `family_deliveries`.** Stop. Re-plan Step 3 before adding a single column. That
  is what R-01 is for.
- **You are about to write a second implementation of something** — a second picker, a second comparator, a
  second reachability walk, a second label map, a second recompute path, a second event log. Stop. Every
  one of those has exactly one implementation by design (C-42, 7-A9, ADM-04, CTX-03/C-45, P-08), and the
  duplicate is how a release drifts inside itself.
- **A step is larger than it looked.** Split at one of the pre-declared boundaries (S3a/b/c, S4a/b/c) and
  commit the first half with its own passing gate. Never carry a half-finished contract forward.
- **An input you need has not arrived.** The standing case is Q-01…Q-06: §3.1 has already adopted §11's
  recommendations, so proceed on those and flag it in the release note. For anything else: stop at the
  boundary, record it, finish the rest of the step.
- **The same task fails twice.** Stop retrying. Preserve the evidence, diagnose read-only, then start a new
  bounded attempt with a different approach.
- **You need a human.** Pause only for: material ambiguity these documents do not resolve; a new
  dependency; credentials; live billable model calls; deployment; destructive operations against non-test
  data; product-scope expansion — including any temptation to build ANL-07, a row-level diff by default, or
  a snapshot delete path. Between normal bounded tasks, proceed.
