# Galileo v2 Data Quality Tests

Scope: the finalized 14-test executable library — 5 Systemic tests + 9 Specific tests. Cross-field business rules and variable-to-variable relationships are AI-recommender templates, not fixed framework tests.

Each module exports `SPEC` and `run(df, columns, supporting, params, classification)`. Results follow the `TestResult` contract: status (`pass` / `fail` / `not_runnable`), metric, threshold, violation count, evidence, `watch_note`, and `not_runnable_reason`. Column-type gating mirrors the finalized inventory vocabulary.

## Stage 1 — Systemic

### Completeness / missing-rate profile
- Tests: % missing per column and segment.
- Runs on: all column types.
- Output: one missing-rate result per column, each judged against the threshold.
- Caveat: measures how much is missing, not why.

### Plausibility rule check
- Tests: impossible values (negative income, out-of-domain codes).
- Runs on: numeric, date, categorical codes. Not suitable: free text.
- Supporting: data-dictionary details when available.
- Caveat: over-strict rules flag genuine tails.

### Date-ordering / event-sequence check
- Tests: events in impossible order (origination after maturity).
- Runs on: date/timestamp columns only.
- Caveat: time-zone and month-end conventions cause false positives.

### Post-outcome field inventory
- Tests: flags columns populated only after the outcome (recovery, write-off).
- Runs on: all columns; needs a dictionary or recognizable post-outcome fields.
- Caveat: inventory only — whether a field actually leaks depends on the Stage 2 target.

### Key uniqueness check
- Tests: duplicate or null primary keys.
- Runs on: key/ID columns (declared or inferred `*_id`).
- Caveat: composite keys must be declared, not inferred.

## Stage 2 — Fitness-for-purpose

### PSI (Population Stability Index)
- Tests: distribution shift vs a reference sample.
- Runs on: numeric continuous, ordinal, low-cardinality categorical. Not suitable: binary flags, free text, high-cardinality IDs.
- Params: 10 quantile bins on reference; thresholds 0.10 / 0.25.
- Caveat: insensitive to shifts within a bin; reliable only at large samples.

### Maturity profile analysis
- Tests: share of the book old enough to observe outcomes.
- Runs on: date columns (origination + observation).
- Params: required seasoning window per use case.
- Caveat: threshold is use-case specific (IFRS9 lifetime vs 12m PD).

### MCAR test (Little's)
- Tests: whether missingness is completely at random.
- Runs on: numeric features (recode categoricals first).
- Caveat: rejecting MCAR does not separate MAR from MNAR — pair with the missingness-vs-target test.

### Missingness-vs-target (MNAR) test
- Tests: whether missingness is associated with the target.
- Runs on: missingness indicators derived from any column type; requires the target.
- Caveat: the key MNAR check — high association means naive imputation will bias the model.

### Robust z-score outlier test (MAD)
- Tests: statistical outliers vs the modeling distribution.
- Runs on: numeric continuous. Not suitable: flags, categoricals, heavily skewed data without transform.
- Params: |z| > 3.5 (MAD-based).
- Caveat: genuine tails (e.g. wholesale exposures) are not defects — investigate before capping.

### Tail analysis
- Tests: behaviour and validity of distribution extremes.
- Runs on: numeric continuous. Not suitable: categoricals, flags.
- Params: percentile cutoffs (p99 / p99.9).
- Caveat: stress models NEED tails — never blind-cap.

### Leakage detection (single-feature AUC)
- Tests: features that predict the target implausibly well.
- Runs on: any feature; requires the target and as-of dating context.
- Params: AUC suspicion threshold (~0.90–0.95).
- Caveat: high AUC can be legitimate (e.g. bureau score) — confirm with availability timing.

### Correlation stability analysis
- Tests: feature-pair correlations stable across time windows.
- Runs on: numeric feature pairs + a date column. Not suitable: categoricals.
- Caveat: instability can be regime-driven rather than a data defect.

### Drift decomposition
- Tests: which segments/sources drive observed drift.
- Runs on: feature + segment columns + a date column.
- Caveat: only as good as the segmentation columns available.
