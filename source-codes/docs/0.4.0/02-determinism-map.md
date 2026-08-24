# 0.4.0 Determinism map (DET-01…DET-03)

**Rule:** workflows move by Python. The LLM appears at exactly **four seams** (DET-02), each with
schema-validated structured output and a **deterministic fake provider used by default in tests**.
Anything not named here is deterministic; adding a seam is a design change requiring a recorded
decision (DET-03). Zero live/billable model calls in any default test run.

## 1. The four seams

| Seam | What the LLM does | Where it lands | Slice-1 status |
|---|---|---|---|
| (a) KB semantics → field/table mapping | Verifies the deterministic role→column resolution; may propose a different column. **Mapping only, never a verdict.** | Cross-field manifest option, **off by default** (D-08), via `ai/llm.py` + `control_plane.resolve` (CFR-11) | Implemented: one bounded advisory call only when explicitly requested before manifest freeze |
| (b) Report summary | Writes commentary over deterministically computed statistics; may not compute, alter or contradict a number (DET-06) | Report layer | **Backlog B3** — not reachable in slice 1 |
| (c) RCA incremental tests | Planner's next-look proposal; Coverage challenge | `rca.py` (existing) | Existing behaviour untouched this release (backlog B5) |
| (d) AI-proposal candidate drafting | T6/#20 candidate generation | APL pipeline | **Backlog B6** — not reachable in slice 1 |

**Slice-1 consequence:** on every slice-1 path the only *reachable* seam is (a), and it is off by
default. Phase 7 proves this with a counting fake provider (7-T2, 7-T3).

## 2. Fake-provider strategy per seam

- **Seam (a)** — a `FakeVerifier` injected at the `ai/llm.py` boundary returning a fixed,
  schema-valid verdict per role (`agree` / `disagree(column, reason)`) from fixture data. Tests
  count invocations: zero when verification is off (6-T5); deterministic mapping-change record when
  on. No network client is ever constructed in tests.
- **Seam (b)** — (when B3 lands) fake commentary provider echoing a template over the structured
  result; DET-06's no-unsourced-number test runs against real and fake alike.
- **Seams (c)/(d)** — existing/backlog; their fakes are specified with their backlog items.
- Legacy note: `ai/llm.py:get_client` has no fake today; the three legacy cosmetic call sites
  (`routers/v2.py:_azure_thought` used by profile/recommend streams, RCA polish) degrade to `None`
  without a key. Phase 7.2 inventories them: gone with retired paths or recorded for backlog.

## 3. The cross-field determinism contract (from the S10 chart)

Step list transcribed from `cross_field_engine_V2_workflow_1.png`, each step marked:

| S10 step | What happens | Class |
|---|---|---|
| 0–1 Discover & load | dataset (required), `dictionary.json`/`mapping.json` (optional) → tables | **Deterministic** (in-product: ingestion records, ING-08) |
| 1b Select use case | IRB / IFRS9 / Both filter (IRB 38 / IFRS9 13, shared rules in both) | **Deterministic decision record** (CFR-12) — the chart's `input()` prompt becomes a manifest field with documented default, never a blocking prompt |
| 2 Load KB | role-based rules for scope | **Deterministic** — `kb.list_eligible_rules(...)`, published+effective+bound only (CFR-03); zero rules → NOT-APPLICABLE (CFR-04) |
| 3 Resolve roles → columns | override wins; else graded ladder exact 1.0 · synonym .92 · dict-desc .88 · token .80 · fuzzy .55–.75, with dtype gate; score+reason recorded per role | **Deterministic** (CFR-05) |
| 3b LLM column verification | checks each role→column choice; disagreement → decision record keep/LLM/manual | **Seam (a)** — optional, off by default, mapping-only |
| 4 Bind entities → tables | each entity votes for its best table | **Deterministic** |
| 5 Evaluate each rule | IF-condition scope → drop censored/N-A → apply exceptions (reason + count) → count violations → lift pattern CLUSTERED/SCATTERED | **Deterministic** |
| Gate | empty scope → NOT-APPLICABLE · rate ≤ tolerance → PASS · rate > tolerance → VIOLATION | **Deterministic** |
| 6 Build report | preamble, roll-up, violations by severity, evidence, `resolution_map.json` | **Deterministic writer** (structured result first, CFR-10; text/PDF derived). LLM commentary on top is seam (b), backlog B3 |

**The chart's two guarantees, adopted verbatim as the cross-field determinism contract:**

1. **"LLM touches mapping only, never a verdict."** (= CFR-11; enforced structurally — the engine
   core takes a frozen manifest and has no LLM dependency at all.)
2. **"Nothing silent — N-A always stated."** (= CFR-04/CFR-08; every NOT-APPLICABLE carries its
   reason; every exception carries reason + count.)

Verification of the two-touchpoint claim (1-T4): on the cross-field path the LLM appears only at
step 3b (seam a, optional/off) and — once backlog B3 lands — at report commentary (seam b).
Everything else above is deterministic Python. Two runs on one frozen manifest are byte-identical
(CFR-16, 6-T7).

## 4. Other slice-1 workflows

- **Ingestion (ING)**: parse, profile, classification inference, mapping tiers, warnings, status
  machine — all deterministic. No seam. (Inference uses generic signals + dictionary/KB hints as
  *data*, ING-10.)
- **KB upload → parse → playback → bind (KB)**: converters, table-aware parser, rule records, parse
  report, binding — all deterministic (KB-14: only deterministic code writes knowledge). No seam.
- **Test Lab (board/readiness/manifest/runner/findings)**: all deterministic; seam (a) is reachable
  only as the manifest's optional verification toggle.
- **Statistical metrics** (when their diagnostics become executable): codified deterministically
  (DET-04). No LLM produces a metric, ever.
- **Execution safety** (DET-05): no model-supplied SQL/Python/imports/paths/subprocess/env/network
  anywhere; bounded read-only helpers only; no user-editable code on any run path (D-16).
