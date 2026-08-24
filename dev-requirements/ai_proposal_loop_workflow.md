# AI Proposal Loop — Workflow Specification

**Scope:** MVP core diagnostic 9 / Test area 6 in `MVP_Test_Plan_DA.xlsx` (Diagnostics detail tab).
**Status:** Workflow agreed. Build process/plan not yet written.
**Date:** 29 July 2026

---

## 1. Purpose

The eight diagnostics are deterministic. Each one tests only what it was told to test:

- A fixed knowledge base of rules (the cross-field engine ships ~49 hard-coded rules against a fixed vocabulary of ~40 semantic roles).
- A fixed column list, resolved by string and synonym matching.

Consequence: **any column that does not map to a known role is invisible to the engine.** In the sample report only ~36 columns resolved to roles; everything else was never examined.

The AI proposal loop closes that gap. It runs *after* a diagnostic has completed, looks at what that diagnostic actually covered versus what was available, and proposes new sub-diagnostics — new columns, new segments, new rules — that no one thought to configure.

**It is not a new test engine.** It proposes and then reuses the existing engines' own functions to execute.

---

## 2. Architectural principles

Four rules, all agreed:

| # | Principle |
|---|---|
| 1 | **One pipeline, not eight.** After a test feeds in, all suggestion logic runs through a single shared path. No per-test branching in pipeline code. |
| 2 | **The adapter is the only per-test code.** Eight thin adapters, each with one job: read its engine's output and emit the same standard context pack. |
| 3 | **Per-test variation lives in data, not code.** The KB is namespaced per diagnostic. Prompt framing is a template pulled from that namespace, never an `if test == 2` branch. |
| 4 | **Nothing runs on data before the SME gate.** Execution happens only on accepted candidates. |

**Acceptance test for the architecture:** adding a ninth diagnostic should require one new adapter and one new KB namespace, and no change inside the pipeline. If it forces a pipeline change, the context pack is under-specified.

---

## 3. Knowledge base dependency by test

Every diagnostic touches the KB — the loop is not limited to the rule-heavy ones. From the Plan A tab:

| Test | Diagnostic | KB dependency | KB object type the loop would write |
|---|---|---|---|
| 1 | Leakage | Low | As-of tags, declared feature list, known-proxy list |
| 2 | Cross-field / validation | **High** | Rules, exceptions, expected relationships, value-semantics tags |
| 3 | Label consistency | Medium | Label rules, definition effective dates |
| 4 | Representativeness | Low–Medium | Reference population, segment keys, declared feature set |
| 5 | Censoring / maturity | Medium | Intended outcome window, resolution indicator, censored tags |
| 6 | **AI proposal loop** | Reads and writes | — (this workflow) |

So stage 6 does not branch on "has a KB / has no KB." It always writes a KB object; only the **type tag** differs by namespace.

---

## 4. The context pack (input contract)

One JSON per diagnostic run, produced by that test's adapter. This is the interface that makes the pipeline generic.

| Field | Contents |
|---|---|
| `diagnostic_id` | e.g. `T2_cross_field` |
| `columns` | Every column in the dataset: name, dtype, sample values, data-dictionary description |
| `covered` | What this run actually touched. For T2, the resolved role-to-column map. For others, the feature/segment list consumed. |
| `uncovered` | Present but never touched — the primary mining ground |
| `kb_slice` | Existing KB entries for this namespace, with plain-English descriptions, so nothing is re-proposed |
| `results_summary` | Pass and violation counts from the run |
| `primitives` | Which executable functions this engine exposes (see §8) |

**Design note.** The pack deliberately does *not* ask "give me your KB rules" — some tests have no rule library. It asks the question every test can answer: *what did you touch, and what was available but untouched?*

**Known risk.** The eight engines describe coverage differently — T2 has roles, PSI has features, completeness has segment × period. Validate the pack by hand against the two least-similar engines (T2 and PSI) before writing any code.

---

## 5. The candidate (proposal object)

| Field | Notes |
|---|---|
| `candidate_id` | e.g. `C-014` |
| `diagnostic_id` | Owning test |
| `attempt` | 1, 2 or 3 |
| `target_columns` | Columns involved |
| `scope` | Which rows the check applies to |
| `logic` | The check itself, as structured fields |
| `thresholds` | Any parameters |
| `rationale` | Plain English, for the SME |
| `confidence` | Ranking only — **not** a threshold, not an auto-accept |
| `executable` | Whether a primitive exists that can run it |
| `provenance` | Which gap in `uncovered` triggered it |

**Structured fields, not prose.** `scope`, `logic` and `thresholds` must be discrete fields. Diffing free text produces noisy word-level changes; diffing fields produces the clean line-level diff the gate screen needs (§7).

---

## 6. Pipeline stages

### Stage 1 — Profile and find gaps
Compare `covered` against `columns`. Produce the untouched set, plus profiling on those columns (types, cardinality, null rate, apparent semantics). Cross-reference `kb_slice` so known territory is excluded.

### Stage 2 — Propose candidates
The LLM drafts typed candidates against the gap list. Output is ranked by confidence. Ranking controls display order only.

### Stage 3 — SME gate
**No data has been touched at this point.** The SME decides on rationale alone. See §7 for the review screen.

Outcomes: **accept** → stage 4. **Reject** → revision lane.

### Revision lane
- Rejected candidates are redrafted using the SME's rejection reason, then re-enter the gate.
- **Cap: 3 attempts per candidate.** Counter is per candidate, not per run — candidate A can be on attempt 3 while B is on attempt 1.
- After 3 attempts the candidate is **parked**: retained, not deleted, along with all three rejection reasons. That log is independently useful — it shows what the model keeps getting wrong about this dataset.
- **The rejection reason is mandatory.** It is the only input to the revision. A blank reason gives the model nothing to change and attempt 2 returns near-identical, burning the budget for free.

### Stage 4 — Compile to primitives
The generic-to-specific boundary. The candidate is bound to a real callable drawn from the `primitives` list the calling engine exposed. No new executor is written.

If no primitive fits: mark **non-executable**. The candidate is still shown to the SME, just with no evidence behind it.

### Stage 5 — Execute and produce evidence
Run the compiled check. Output: hit rate plus sample offending rows.

**Read-only.** The check counts violations; it never writes to or alters the dataset.

### Stage 6 — Integrate into KB?
A **separate, explicit ask**, made only after the SME has seen results.

- **Yes** → write a typed object into the diagnostic's KB namespace.
- **No** → the result stands as a one-off look; nothing persists.

---

## 7. The SME gate screen

Four elements, all required:

1. **Attempt badge** — "Attempt 2 of 3", visible before anything else.
2. **Previous rejection reason, shown back** — so the SME can see whether their objection was actually addressed, rather than having to remember it.
3. **Line-level diff against the previous attempt** — removed lines, added lines, unchanged lines left grey. A cosmetic revision shows as almost entirely grey, which is the tell.
4. **Executable flag at gate time** — so accepting something unbuildable is a visible choice, not a surprise at stage 4.

**Why this matters:** the SME may see the same idea three times. If revisions are shallow, that is review fatigue and they will start rubber-stamping. The badge and diff make a lazy revision visible at a glance.

---

## 8. Reuse of existing engine functions

Confirmed for `cross_field_rule_engine.py`. Its reusable primitives:

`ineq`, `dateorder`, `identity`, `dom_range`, `dom_set`, `eq_cond`, `presence`, plus `evaluate_rule()`.

A candidate expressible through any of these becomes a `Rule` object and runs with **zero new code**.

**Not yet verified:** the equivalent primitive sets for the other seven engines. `cross_field_rule_engine.py` is the only script reviewed so far. Each engine's `primitives` list must be established before its adapter is written.

**Unchanged:** the engine's existing `_llm_call()` stays exactly as it is. That function verifies role-to-column mappings and is a separate concern from this loop.

---

## 9. Invariants

- Nothing executes on data before the SME gate.
- Confidence scores rank; humans decide.
- Execution is read-only.
- Two distinct decisions: *accept the candidate* (stage 3), then *persist it* (stage 6). Declining at stage 3 means the check never ran. Declining at stage 6 means it ran and you saw the answer, but it does not become a permanent rule.
- Every candidate carries provenance back to the gap that produced it.
- Parked candidates and rejection reasons are retained.

---

## 10. Deviations from the Diagnostics detail tab

The sheet describes the order as propose → validate → persist → run. Three intentional changes:

| Sheet | This workflow | Why |
|---|---|---|
| Persist to KB, then run | Run first, persist as a separate optional decision | The SME should see results before committing anything permanent |
| Implies engine picks rules up on a subsequent run | No next run. The loop is a **separate second pass**, executed directly. | There is no recurring cycle; the loop is invoked deliberately after an initial run |
| No revision path described | Rejected candidates revised, capped at 3 attempts | Turns a rejection into signal instead of a dead end |

Not a deviation: the sheet's rule that nothing runs on data until accepted. That still holds — the gate simply sits before execution rather than before persistence.

---

## 11. Open items

1. **Can the SME edit a candidate directly** at the gate — a third button alongside accept and reject?
2. **Does an SME edit consume an attempt** from the budget of 3?
3. **Primitive inventory for engines 1 and 3–8** — required before their adapters can be built.
4. **Where the "not runnable" candidate goes** after the SME accepts it: parked as a build request, or discarded?
5. **KB object schema per namespace** — the five type families in §3 need concrete field definitions.
