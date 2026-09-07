# 0.4.0 Framework — the single source of truth for what exists

**Source:** S8 `MVP_Test Plan_DA.xlsx` (re-issued 30 Jul 2026), three green tabs only —
*Plan A - single upload*, *Diagnostics detail*, *Detailed_DQ_Framework* — read ignoring every
`#F79646`-backfilled column (*Source binding*, *IRB example*, *Harsha's comments*,
*IRB dataset example(s)*, *In team's top-5?*). Governed by FWK-01…FWK-05, FWK-17, FWK-18, D-17.
**This document is what Phase 3 seeds as data (FWK-04).** The framework is *what we test*; the KB
is *domain truth we test against* (C-37). The framework ships with the product; domain rules never do.

## 1. Six L1 themes → eleven L2 assessment areas

Verbatim from *Detailed_DQ_Framework* (id = stable key seeded in Phase 3):

| L2 id | L1 theme | L2 assessment area |
|---|---|---|
| L2-01 | Sample & Representativeness | Portfolio Representativeness & Selection Bias |
| L2-02 | Sample & Representativeness | Historical Depth, Observation Windows & Censoring |
| L2-03 | Sample & Representativeness | Downturn & Regime Coverage |
| L2-04 | Target & Outcome Integrity | Target Definition & Label Consistency |
| L2-05 | Feature & Predictor Quality | Missingness Mechanism & Data Availability |
| L2-06 | Feature & Predictor Quality | Outliers, Extreme Values & Plausibility |
| L2-07 | Feature & Predictor Quality | Temporal Integrity & Feature Leakage |
| L2-08 | Feature & Predictor Quality | Feature Stability & Behavioral Consistency |
| L2-09 | Dataset Stability & Monitoring | Dataset Drift & Degradation Monitoring |
| L2-10 | Upstream Dataset & Pipeline Integrity | Pipeline Stability & Schema Consistency |
| L2-11 | Third party data quality and controls | External / vendor data robustness & change control |

Theme counts: Sample & Representativeness 3 · Target & Outcome Integrity 1 · Feature & Predictor
Quality 4 · Dataset Stability & Monitoring 1 · Upstream Dataset & Pipeline Integrity 1 · Third party
data quality and controls 1. **The third-party theme stays registered with no diagnostic reaching
it** — an honest area-level coverage gap, not a deletion (FWK-01).

## 2. Six test areas (Plan A), with KB dependency

"Test" and "diagnostic" are distinct levels (FWK-02): a test area groups diagnostics; the
user-facing unit of execution is the diagnostic.

| Area | Name | Stage | KB dep. | Plan-A framework dimension(s), verbatim |
|---|---|---|---|---|
| T1 | Feature-to-target leakage | Stage 2 | Low | Temporal Integrity & Leakage |
| T2 | KB-driven data validation (rules, distributional & relationship) | Both | **HIGH** | Pipeline & Schema Stability; Outliers & Plausibility; Monotonicity & Directional Consistency |
| T3 | Target definition & label consistency | Both | Medium | Target Definition & Label Consistency |
| T4 | Portfolio / population representativeness | Stage 2 | Low-Med | Portfolio Representativeness |
| T5 | Outcome window / censoring completeness (incl. seasoning/maturity) | Stage 2 | Medium | Outcome Window Consistency; Historical Depth & Censoring |
| T6 | KB relationship set (SME generated + AI proposed, SME validated) | Both | Reads & writes | AI-proposal layer (any dimension) |

T2 runs in three modes — hard / distributional / relational — and establishes the value-semantics
tags (FWK-03). Plan A's dimension strings are transcribed verbatim above; coverage computation
normalizes them to the eleven L2 names (§4).

## 3. The diagnostic register — exactly 9 rows (D-17)

`id` = S8 *Diagnostics detail* row number (diagnostic ordinal; sheet row − 1). All columns below are
non-backfill S8 columns plus Plan A's KB dependency and the FWK-17 `workflow_status`. **This table
is the register**; Phase 3 seeds it verbatim.

| id | Area | Mode | Diagnostic | What it computes | Metric / method (alternatives) | Threshold-based? | Threshold / decision rule (default; tunable) | Det./Stat. | Decision type | Stage | KB dep. | workflow_status | enabled_by |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | T1 | statistical | Single-feature target separation | How strongly does each feature separate the target alone? | univariate AUC; Information Value (alt: mutual information) | Yes - tunable | AUC > 0.90 OR IV > 0.5 → 'too good to be true' flag / AUC < 0.60 OR IV < 0.05 → 'poor discrimination' flag | Statistical | candidate flag | Stage 2 | Low | executable | Phase 1B Slice 4 acceptance gate |
| 4 | T2 | hard | Cross-field business rule | Does each row satisfy a hard cross-field rule (with exceptions)? | violation count / rate | No - deterministic rule | violation rate > ~0 after exceptions → flag | Deterministic | verdict | Both | HIGH | **workflow_pending** | — (paused 2 Sep 2026 for additional testing and refinement) |
| 6 | T2 | hard | Row-completeness reconciliation | Are all expected rows present (expected-vs-actual, segment × time)? | coverage % = actual/expected by segment × period (anti-join) | No - structural/existence | coverage materially below expected (e.g. <95% where neighbours ~100%) → flag | Deterministic | verdict | Stage 1 | HIGH | workflow_pending | — |
| 8 | T2 | hard | Value-semantics classification | Assign CENSORED / STALE_FROZEN / NOT_APPLICABLE cell tags and report NO_TAG / UNCLASSIFIED / UNSCOPED coverage | role-routed cell classification and coverage vs versioned KB | No - classification | tag per KB rule (no cutoff) — OUTPUT other tests consume | Deterministic | classification output | Stage 1 | HIGH | executable | T2D8 production acceptance gate (07 Sep 2026) |
| 11 | T2 | relational | Directional / monotonic consistency (segmented) | Do two variables relate in the expected direction, by segment? | Spearman rank corr; group-band median trend | Yes - tunable | wrong sign, OR \|corr\| < floor, OR inversions > allowed (per segment; min-sample guard) | Statistical | contextual | Both | HIGH | workflow_pending | — |
| 12 | T3 | hard | Label-consistency rule | Are label fields internally consistent by process? | violation count | No - deterministic rule | violation rate > ~0 → flag | Deterministic | verdict | Both | Medium | workflow_pending | — |
| 14 | T4 | statistical | Population Stability Index (PSI) | Sample distribution vs scoring/reference population | PSI per feature & segment (alt: JS divergence, Wasserstein) | Yes - tunable | PSI >= 0.10 watch; >= 0.25 investigate | Statistical | threshold+SME | Stage 2 | Low-Med | executable | D-22 / DX-06 (19 Aug 2026) |
| 17 | T5 | statistical | Resolution / maturity rate by vintage | Share of outcomes resolved vs open, per vintage | resolved count / total by vintage | Yes - tunable | resolution rate < X% for a vintage → flag censoring | Statistical | contextual | Stage 2 | Medium | workflow_pending | — |
| 20 | T6 | loop | AI-proposal workflow | AI profiles the data and proposes KB-absent candidate relationships; SME validates; accepted ones run as new test-2-mode checks (a workflow, not a computation — no test-6 executor) | confidence score (ranking only) — no data metric | No - human gate/workflow | confidence above surface-cutoff → present to SME; the SME decision is the gate | n/a (workflow) | SME gate | Both | Reads & writes | workflow_pending | — |

Register invariants (asserted by 3-T1 and the rebuilt `verify_plan8.py`):

- **Exactly 9 rows**; ids exactly {2, 4, 6, 8, 11, 12, 14, 17, 20}.
- **Exactly two `executable` rows: #2 and #4**. The other seven refuse execution with
  *"workflow not yet defined"* (FWK-17) — never rendered as broken, failed or missing.
- A request naming any other id — including an S8 defer row id — is an **unknown diagnostic**,
  indistinguishable from any bad id (FWK-05, D-17).
- Flipping `workflow_status` to `executable` requires a workflow definition + implementation + a
  recorded decision in `enabled_by`, one diagnostic at a time, after requester sign-off of the
  previous one (FWK-18, D-22).
- Thresholds shown are **defaults**; live values come from the semantic layer (FWK-06), never code.

## 4. Coverage semantics (FWK-14/15)

State definitions (do not round up):

- **Covered** — every S8-documented diagnostic for the area is registered. *(No area qualifies in 0.4.0.)*
- **Covered-thin** — the area's headline catch is in the 9-core register while richer S8-documented
  diagnostics stay out of scope (D-17).
- **Partial** — the area is reached only incidentally by a diagnostic homed elsewhere. *(None in 0.4.0.)*
- **Gap** — no registered diagnostic reaches the area; the reason is stated.

| L2 area | Status | Registered diagnostics / reason |
|---|---|---|
| L2-01 Portfolio Representativeness & Selection Bias | Covered-thin | #14 (defer rows #15 KS, #16 segment coverage out of scope) |
| L2-02 Historical Depth, Observation Windows & Censoring | Covered-thin | #17 (defer rows #18, #19 out of scope) |
| L2-03 Downturn & Regime Coverage | **GAP** | candidate diagnostic is OOS-06 (downturn identification — judgement-laden, out of product scope) |
| L2-04 Target Definition & Label Consistency | Covered-thin | #12 (defer row #13 target-rate break out of scope) |
| L2-05 Missingness Mechanism & Data Availability | **GAP** | candidate diagnostics are S8-deferred MCAR/MNAR (OOS-05 — must run only after value-semantics tagging) |
| L2-06 Outliers, Extreme Values & Plausibility | Covered-thin | #4 (domain/bound rules), #11 (defer rows #9 robust-Z, #10 boundary pile-up out of scope) |
| L2-07 Temporal Integrity & Feature Leakage | Covered-thin | #2 (defer rows #1 post-outcome, #3 categorical leakage out of scope, D-14) |
| L2-08 Feature Stability & Behavioral Consistency | **GAP** | requires multi-upload (Plan B — D-11, out of scope) |
| L2-09 Dataset Drift & Degradation Monitoring | **GAP** | requires multi-upload (Plan B — D-11, out of scope) |
| L2-10 Pipeline Stability & Schema Consistency | Covered-thin | #4, #6, #8 (defer rows #5 derivation identity, #7 disguised-missing out of scope) |
| L2-11 External / vendor data robustness & change control | **GAP** | third-party theme — no diagnostic by design (FWK-01) |

Within the register, the coverage board additionally distinguishes **executable vs
workflow-pending** (FWK-17) — a diagnostic state, not an area state. T6/#20 has no L2 of its own
("any dimension"): it reads and writes the KB across areas and is not counted in area coverage.

## 5. Appendix — S8's 11 `Defer (phase-2)` rows (documentation only, D-17)

Not registered, not displayed, not executable, not offered anywhere. Any return to scope is a
framework revision with its own decision record. Kept here so a later reader knows what S8
documents beyond product scope:

| id | Area | Diagnostic | One-line |
|---|---|---|---|
| 1 | T1 | Post-outcome field / look-ahead present | field availability vs as-of date |
| 3 | T1 | Categorical leakage (association strength / purity) | per-category outcome rate; Cramér's V / IV; volume-gated (D-14) |
| 5 | T2 | Derivation identity | abs(recorded − derived) vs tolerance |
| 7 | T2 | Disguised-missing / staleness | frozen/sentinel field over segment-period |
| 9 | T2 | Statistical outlier (robust-Z) | 0.6745·(x−median)/MAD; material fields; value-semantics-gated |
| 10 | T2 | Boundary / pile-up detection | share of mass at bounds |
| 13 | T3 | Target-rate break (change-point) | structural break on target-rate series |
| 15 | T4 | KS distance | Kolmogorov–Smirnov vs reference |
| 16 | T4 | Segment coverage | presence & share by segment |
| 18 | T5 | Resolved-only vs all differential | Δ(estimate resolved-only vs all) |
| 19 | T5 | Seasoning / maturity profile | cumulative outcome by months-on-book |
