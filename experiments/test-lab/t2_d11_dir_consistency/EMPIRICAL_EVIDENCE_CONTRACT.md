# Empirical directionality evidence contract v0.1

## Scope

This layer measures how one feature behaves against one explicitly selected reference. It does not
read or modify the economic directionality knowledge base and does not decide whether a disagreement
is a data-quality defect.

The approved integration run uses `default_12m` from snapshot `item_d982dc3d4a34` as a binary
`HIGHER_IS_WORSE` target. A proof-of-concept continuous-reference run uses `net_interest_margin` as
an explicit `HIGHER_IS_BETTER` target. The NIM run validates mechanics only: its observed signs are
not treated as economic findings or user-adjudication inputs. References are run separately and are
never combined.

## Input

- one feature vector;
- one reference vector;
- an explicit reference kind (`BINARY` or `CONTINUOUS`);
- an immutable threshold configuration;
- any confirmed special/missing values from saved column metadata.

Only paired, finite, non-missing observations are used. Physical nulls and metadata-confirmed
special values are excluded and counted separately; values are never guessed to be sentinels from
their magnitude. Binary references must contain exactly two classes; the greater class is treated
as the event. Numeric directionality is not computed for categorical features.

## Component evidence

Each result retains the following evidence independently:

1. Pearson correlation and p-value;
2. Spearman rank correlation and p-value;
3. a standardized univariate regression coefficient and model p-value (logistic likelihood-ratio
   test for a binary reference; standardized linear regression for a continuous reference);
4. equal-frequency feature bins with counts, reference means, range, trend, and reversals.

Unavailable or unsuitable components have an explicit status and reason. No component is silently
imputed and no majority vote is used.

## Controlled observed states

- `increasing`
- `decreasing`
- `non_monotonic`
- `weak_no_relationship`
- `conflicting_evidence`
- `insufficient_evidence`
- `not_applicable`

Synthesis is deterministic and evidence-preserving. Opposing material component directions produce
`conflicting_evidence`. Material two-way movement across bins produces `non_monotonic` when global
directional evidence is absent, and `conflicting_evidence` when it coexists with a global trend.
If no component clears both practical and statistical thresholds, the result is
`weak_no_relationship`.

## Default decision thresholds

- at least 30 paired observations and 10 observations in each binary class;
- two-sided significance level 0.05;
- absolute correlation at least 0.05;
- absolute standardized logistic coefficient at least 0.10;
- absolute standardized linear coefficient at least 0.05;
- five quantile bins requested, with at least 10 observations per realized bin;
- for binary references, material bin-reference change at least 0.002 and material bin range at
  least 0.01 in event-rate units;
- for continuous references, material adjacent-bin change at least 0.05 reference standard
  deviations and material bin range at least 0.10 reference standard deviations.

Thresholds are recorded with every run and can be sensitivity-tested later. They are diagnostic
settings, not universal statistical truths.
