# 0.4.0 Cross-field diagnostic contract (CFR-01…CFR-18) — implemented slice-1 scope

Derived from S5 (`cross_field_rule_engine.py`), S6 (`report.txt`), S9
(`KB_cross_field_reference_2.pdf`) and the S10 chart. The engine becomes a **pure core** under
`backend/dq_diagnostics/engines/cross_field/`; rules come **only** from the published KB (CFR-03);
the structured result is primary and every rendering derives from it (CFR-10).

## 1. Rule model (CFR-02) — every S9 column accounted for

Five types: `inequality`, `date_ordering`, `identity`, `conditional`, `domain`.
Three severities: `CRITICAL`, `MATERIAL`, `MINOR`.

S9 rule-table columns → KB rule record fields (KB-07):

| S9 column | Rule record field |
|---|---|
| ID | `rule_id` (e.g. `IRB-01`, `IFRS9-02`) |
| Sev (CRIT/MATE/MINO) | `severity` ∈ {CRITICAL, MATERIAL, MINOR} |
| Type | `rule_type` ∈ {inequality, date_ordering, identity, conditional, domain} (S9 spellings "Domain/bound"→domain, "Identity/derivation"→identity, "Date ordering"→date_ordering) |
| Entity | `entity` (workout, facility, snapshot, borrower, valuation, default) |
| Roles (semantic) | `roles` — list of semantic role names (never physical columns) |
| Rule | `rule_text` + structured predicate parameters for binding |
| Reg reference | `regulatory_ref` (nullable) |
| (page-4 notes) | `encoded_exceptions` (reason text; e.g. IRB-04 cure exception) + `notes` |
| (framework section) | `framework` ∈ {irb, ifrs9, both} |
| (page/line) | `source_ref` (traceability, KB-07) |

## 2. The nine primitives (CFR-06)

`ineq`, `dateorder`, `identity`, `dom_range`, `dom_set`, `eq_cond`, `in_cond`, `presence`,
`present_number` — plus `evaluate_rule()`. Registered in the Phase-2 unified helper registry;
these are what KB-08 binds to and what APL-20 will compile to. (`eq_cond_num` stays an internal
detail of `eq_cond`.) Each primitive gets a passing, a violating and a skipped-censored fixture
case (6-T3).

## 3. Role resolution (CFR-05) — S10 step 3

Override wins; otherwise the graded auto-match ladder, **with a dtype gate**. The chart names five
tiers; the actual S5 code (`cross_field_rule_engine.py:690-757`, authoritative per CFR-05's "exactly
as implemented") has **six**, plus an unresolved floor — port all of it:

| Tier | Match | Score |
|---|---|---|
| 1 | exact canonical name | 1.00 |
| 2 | synonym match | 0.92 |
| 3 | dictionary-description phrase | 0.88 |
| 4 | synonym token-subset | 0.80 |
| 5 | fuzzy character similarity | 0.55–0.75 (graded) |
| 6 | partial token overlap (Jaccard) | 0.35–0.60 (graded) — not in the S10 chart's summary; present in code |

A best score **below 0.70 is reported unresolved**, never auto-accepted at a weak score
(`resolve_roles`'s threshold — also not in the chart, verified in code).

**DX-04 (docs/0.4.0/07-decisions.md) — what does NOT port:** S5's `ROLE_VOCAB` is a hardcoded
dict of role→`{entity, dtype, synonyms}` — domain knowledge in code, the same class of KB-01
violation as `build_generic_kb()`. The *algorithm* above ports verbatim; the *vocabulary* is
derived at runtime, never hardcoded:
- **role + entity**: from each in-scope rule's own `semantic_roles_json`/`entity` (KB-07, Phase 5's
  parser output) — the published KB is the vocabulary.
- **tier 3 (dict-desc)**: reads the *ingested dataset's* dictionary descriptions (ING-08's mapping
  records), generic and per-dataset.
- **tier 2 (synonym)**: normalized-form variants of the role's own recorded name (e.g. a collapsed
  PDF-extraction form vs. its human-confirmed recovery, KB-11) — not a curated synonym table; a
  role with no such variant simply never hits this tier, which is correct, not a gap.
- **dtype gate**: inferred structurally from how a role is used in its own rule(s) at KB-binding
  time (KB-08) — e.g. `date_ordering` → date-like — never a per-role dtype table.
A source-inspection test (mirroring 5-T8) asserts no per-role synonym/entity/dtype literal survives
in the ported engine.

Each resolved role records **score and reason**; the auditable resolution map becomes **manifest
provenance**. Report the resolved count and auto/override split (S6: `Roles resolved: 36/36
(0 override, 36 auto)`). Ingestion's mapping records (ING-08) are the input substrate. Optional
LLM verification of the mapping = seam (a), off by default (D-08), mapping-only, never a verdict
(CFR-11); a disagreement becomes a decision record keep/accept-LLM/manual — never a prompt (CFR-12).

## 4. Evaluation semantics (CFR-07) — S10 step 5

Per rule: entity→table binding by vote (step 4) → IF-condition scope → rows skipped as
censored/N-A per value-semantics (slice 1: the rule's own encoded exceptions and verdict logic —
S9 page 4) → **exceptions applied with stated reason and count** ("silent exceptions are how rules
rot") → violation count → rate vs tolerance → lift-based pattern read.

## 5. Verdict gate (CFR-08) and pattern read (CFR-09)

- Gate: **empty scope → NOT-APPLICABLE** (with reason, e.g. "no rows meet IF-condition" or "no
  knowledge base published for scope irb" — never PASS, CFR-04) · **rate ≤ tolerance (default 0) →
  PASS** · **rate > tolerance → VIOLATION**.
- Severity orders the report and labels the finding; **it never changes pass/fail maths**.
- Pattern on each violation: lift-based **CLUSTERED** (⇒ feed/segment fault) vs **SCATTERED**
  (⇒ capture error), with `grain_role` (default `facility_id`), `segment_role` (default `region`),
  `cluster_lift` 3.0, `cluster_coverage` 0.5 — all from the semantic layer (FWK-06).

## 6. The structured result (CFR-10) — every S6 field accounted for

**Preamble** (S6 lines → fields): rule-set source → `kb_document/version`; framework scope →
`use_case`; dataset → `item/table refs`; rules loaded → `rules_loaded`; rules by type →
`by_type counts`; roles resolved n/m + override/auto split → `roles_resolved`,
`roles_total`, `override_count`, `auto_count`; pattern knobs → `parameters {cluster_lift,
cluster_coverage}` (+ `grain_role`, `segment_role`, `tolerance` with their source per FWK-06).

**Roll-up**: `{PASS: n, VIOLATION: n, NOT_APPLICABLE: n}` (S6: 41/7/1).

**Per-rule result** (all rules, three shapes):
- VIOLATION: `severity`, `rule_id`, `framework`, `rule_type`, `rule_text`, `regulatory_ref`
  (when present), `resolved_roles` (role→column map used), `scope_rows_evaluated`,
  `scope_rows_skipped` (+ reason, e.g. "missing/censored" — S6 IRB-35 line), `violation_count`,
  `rate`, `tolerance`, `exceptions_applied` (count + stated reason — S6 IRB-04), `evidence`
  (**≤5 rows keyed by grain**, CFR-15), `pattern` (CLUSTERED/SCATTERED with parameters).
- PASS: `severity`, `rule_id`, `scope_rows_evaluated`, `violation_count=0`, `rule_text`.
- NOT-APPLICABLE: `rule_id`, `framework`, `na_reason` (stated, never hidden — S6's final section).

Ordering: violations first, by severity CRITICAL → MATERIAL → MINOR (then rule id); passes; N-A.
Text and PDF renderings derive from this structure; byte-identical across two runs on one frozen
manifest (CFR-16, 6-T7).

## 7. S10 chart — interactive steps → manifest/decision records (CFR-12)

| Chart interaction | Product mechanism |
|---|---|
| Step 1b use-case `input()` (IRB 38 / IFRS9 13, shared in both) | Manifest field `use_case` ∈ {irb, ifrs9, both}; unattended run applies the documented default (`both`) and records `default_applied` |
| Step 3b per-conflict pause (keep/LLM/manual) | `diag_run_decisions` record `role_verification_change`; never blocks a request |
| Role override (`mapping.json`) | Manifest `role_overrides` → decision record `role_override`; override wins in the ladder |
| Threshold tune | Decision record `threshold_tune` with actor + source (FWK-06) |
| Scope exclusion | Decision record `scope_exclusion` |

Chart outputs → product records: `report.txt` → structured result + derived renderings;
`resolution_map.json` → manifest role-provenance (persisted with the frozen manifest, CFR-14).

## 8. Integration constraints

- API-invoked; no CLI, stdin, print, argparse, file paths, `sys.exit` in the core (CFR-01, 6-T8).
- SSE progress in the existing `execute/stream` idiom: `start` → `progress` (rules evaluated/total)
  → `done`; sanitized errors (CFR-13, PLT-04).
- Persistence through `diag_runs` / `diag_results` / `diag_findings`; a VIOLATION auto-opens an
  issue into the existing Issue Management/RCA hand-off (CFR-14, RCA-27).
- Read-only against the dataset; bounded evidence (CFR-15). Deterministic (CFR-16). Workspace-scoped
  (CFR-17 — single-tenant shim in slice 1, same as the rest of the product). No new runtime
  dependency; `anthropic` rejected (CFR-18, C-04); `_llm_call` and `CFR_LLM_API_KEY` do not survive
  the port (CFR-11).

## 9. Parity dimensions proven in slice 1 (plan 6.7 — no workbook dependency)

Proven on a synthetic fixture reproducing S9's rule counts and a documented subset of S6's
behaviours: rule counts by framework/type/severity (6-T1); verdict gate incl. NOT-APPLICABLE
reasons (6-T2); severity ordering, exception reason+count accounting, ≤5 evidence rows,
clustered/scattered against a known-concentration fixture (6-T6); ladder tiers + dtype gate (6-T4);
determinism (6-T7). **Not claimed:** numeric equality with S6's `mortgage_irb_-_database.xlsx` run
(the workbook was withdrawn as an ask, 30 Jul; rev-4 item 4).
