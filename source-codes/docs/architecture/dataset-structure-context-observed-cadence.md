# Dataset Structure Context — Observed Cadence contract

**Status:** documentation-only post-Phase-C2 design contract. It defines no producer, resolver change, consumer adapter, diagnostic, API, or UI behaviour.

**Technical predicate:** `table.temporal/observed_cadence`

**Plain-language name:** Observed Cadence

## Outcome and scope

Observed Cadence publishes a bounded, aggregate-only observation of repeated, orderable time values *within one reviewed entity grouping*. It says only what the current immutable snapshot exhibits for that qualified axis/grouping pair. It does not select a reporting period, infer a business cycle, establish panel form, or make a D06 decision. The later [integrated Data Sourcing review contract](dataset-structure-context-data-sourcing-review.md) permits a separate user-confirmed expected-cadence assertion to be proposed from a regular observation; it never changes this observation or makes observed cadence expected frequency.

This contract is deliberately limited to one reviewed Identifier column as the grouping and one active single-column Date binding or exact recognised calendar-quarter Period binding as the axis. Empty/global grouping, composite entity grouping, other Period formats, intervals, instants, cross-table cadence, and relationships are later design work.

## Reconciled phase terminology

The Phase C1 document correctly says that grouping belongs to a later cadence observation and that cadence begins only after its temporal-binding gate. Its historical sentence “the next bounded design increment is observed cadence” now applies after the completed Phase C2 temporal-binding implementation. This document is that additive increment; it does not reopen Phase C1 or change its `period` amendment.

The v1 schema/protocol already reserves this predicate and its `observed_cadence` value shape. That reservation is not an implemented observer. Before this producer is enabled, runtime validation and JSON Schema must change together to require exactly one qualified `grouping` column and replace the unconstrained string `observed_interval_class` with this closed object:

```json
{"unit": "day|week|month|quarter|year", "step": 1}
```

`step` is a positive integer; the object is required only for `regular` and forbidden for `mixed`, `irregular`, and `unknown`. This is an unused cadence facet, so it may be an additive v1 amendment only while no endpoint or consumer emits/consumes its prior string form; otherwise it requires negotiated context versioning. A later broader grouping or interval taxonomy needs its own versioned contract.

## Atomic candidate identity and eligibility

One candidate is the ordered pair `(axis_id, grouping)`:

- `predicate`: `table.temporal/observed_cadence`
- `multiplicity`: `keyed_set`
- `subject`: the qualified table
- `axis_id`: exactly the active temporal-binding claim's mechanical `axis_id`
- `grouping`: exactly one qualified column from an active entity-binding claim
- `instance_key`: `axis_grouping:` followed by the lowercase SHA-256 hex digest of canonical JSON `{\"axis_id\": axis_id, \"grouping\": grouping}`. This avoids delimiter collisions in source column names; the claim retains the readable qualified values.

The Cartesian product is only between independently eligible current axes and groupings in the same table. It creates no primary axis, entity choice, composite key, or conflict merely because several pairs exist.

An axis/grouping pair is eligible only when all of the following are true at one consistent AAR read:

1. The selected snapshot and table are ready, active, hash-verified, and exact.
2. The axis has an active same-snapshot `table.temporal/temporal_binding` assertion whose effective resolution is `proposed` or `confirmed`, with exactly one single-column `date` claim, or one `period` claim backed by the Phase C2 exact recognised Gregorian calendar-quarter profile. Its `axis_id` and qualified column agree and it has no timezone.
3. A Date axis uses the closed scan-time parser below; it does **not** require C2 `calendar: gregorian` metadata. A Period axis has only the exact recognised Gregorian calendar-quarter normalizer. A marginal profile parse rate, a best-effort parser, name/dtype inference, profile bounds, or raw lexical order is not enough to establish cadence.
4. The grouping has an active same-snapshot `table.structure/entity_binding` assertion with one qualified column, effective resolution `proposed` or `confirmed`, and reviewed-Identifier provenance. A row-grain assertion is not a substitute for an entity grouping.
5. The axis and grouping have active, hash-verified role-bearing profiles and their inventory membership agrees with the retained exact table profile.

Missing, stale, null, automatic-only, or mismatched prerequisite evidence fails closed. Cadence may not create a temporal or entity candidate to make itself eligible.

### Authoritative prerequisite projection

For each prerequisite locator—its table predicate and mechanical `instance_key`—the observer projects active same-snapshot assertions before forming pairs. Exactly one valid `confirmed` assertion wins; otherwise exactly one valid producer-owned `proposed` assertion is eligible. Duplicate valid confirmations or duplicate producer proposals make that locator unavailable with `DSC_R_AMBIGUOUS_CANDIDATES`. When both one confirmation and one producer proposal exist, their typed qualified values must agree exactly; otherwise the locator is likewise unavailable. This rule applies separately to each temporal axis and entity grouping, so a stale or ambiguous locator never leaks into a pair plan.

Cadence confirmation is a separate, independently sourced assertion at the cadence locator. The observation producer owns only its observed proposal/assertion and never supersedes, rewrites, or promotes a confirmation. The resolver may project a sole valid confirmation only when it references the exact agreeing observed candidate and dependency fingerprint; competing or non-agreeing cadence assertions are unavailable under the same ambiguity rule.

## Observation algorithm

The observer uses no source row order. For each eligible pair it applies these exact rules:

1. Read only the axis and grouping columns needed for the eligible, bounded candidate set. The returned columns and row count must exactly agree with the retained table profile.
2. Classify a row once, in this precedence: `physical_null` if either column is physically null; `confirmed_special` if neither is null and either is a confirmed special value; `parse_failure` if neither applies and the axis fails its approved exact normalizer; otherwise `usable`.
3. For each usable grouping value, normalize the axis to its approved opaque sortable key. Grouping values and normalized temporal values are used only in-memory; neither is retained.
4. Deduplicate equal `(grouping value, normalized temporal key)` pairs before calculating deltas. Multiple rows at one entity/time contribute one observation, never a zero-length delta. Count their excess rows only as an aggregate.
5. Sort each entity's distinct normalized keys in ascending temporal order. Its usable deltas are the positive differences between adjacent keys. An entity with `n` distinct usable observations contributes `n - 1` usable deltas.

An entity is **adequately observed** only with at least three distinct usable observations. The adequate-entity denominator is every entity with at least one distinct usable observation; entities with one or two observations remain in the denominator. This makes sparse entities visible rather than silently dropping them. No additional minimum entity count is invented beyond the documented thresholds below.

### Closed Date observation parser

The Date parser is established by the bounded scan, not by C2 calendar metadata. It accepts only: a native `date` that is not a `datetime`; a native `datetime` or pandas `Timestamp` that is timezone-naive and exactly `00:00:00.000000000`; or a string matching ASCII `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` with no trimming, locale handling, or inference. A string must be a valid proleptic Gregorian date from `0001-01-01` through `9999-12-31`. Every other value—including an offset-aware timestamp, non-midnight timestamp, whitespace-padded string, datetime-like number, locale string, or invalid calendar date—is a `parse_failure`.

## Interval classes and tolerances

An interval class is an observed, mechanical calendar difference, not an expected frequency. Its canonical value is the closed `{unit, step}` object defined above. For a normalised Date, classify each positive adjacent difference in this order: exact whole Gregorian years with the same month/day (`year`); exact positive quarter counts with the same day position (`quarter`); exact positive Gregorian month counts with the same day position (`month`); exact positive whole-week counts (`week`); then exact positive calendar-day counts (`day`). The `step` is the corresponding positive integer. For an exact recognised calendar-quarter Period, the class is `{unit:"quarter",step:<positive-quarter-difference>}`. The normalizer's original values and keys are never retained.

“Same day position” means exact day-of-month equality only. End-of-month clamping, nearest-date matching, daylight-saving adjustment, rounding, guessed period formats, and a ±N-day window are prohibited: `2021-01-31` → `2021-02-28` and `2020-02-29` → `2021-02-28` are not month/year classes; regular month-end series therefore fall through to exact week/day classes (or may become mixed/irregular), rather than being silently normalised. A normalised value that cannot be placed in this taxonomy is **unclassified**; it remains a usable delta and counts against class coverage.

The only non-zero tolerance in this increment is classification prevalence: up to 5% of usable deltas may fall outside a regular dominant class. It is not a date-value tolerance and does not convert a near interval into a class.

## Cadence states

Let `D` be the count of usable deltas, `A/E` the adequate-entity ratio, and `C(k)` the count of usable deltas in interval class `k`. A class is recognised only when it is one of the taxonomy values above. An observation has sufficient structural basis only when `D > 0` and `A/E >= 0.90`.

| State | Exact rule | `observed_interval_class` |
| --- | --- | --- |
| `regular` | Sufficient basis and one recognised class has `C(k) / D >= 0.95`. | Required; that sole dominant `{unit,step}` object. |
| `mixed` | Sufficient basis; not regular; at least two recognised classes each cover at least `0.05 * D`; and recognised classes collectively cover at least `0.95 * D`. | Omitted. |
| `irregular` | Sufficient basis but neither regular nor mixed. This includes materially unclassified deltas or dispersion that cannot support two materially represented recognised classes. | Omitted. |
| `unknown` | A completed candidate-specific scan cannot meet sufficient structural basis—for example no usable delta or inadequate entity coverage. | Omitted. |

Ratios are compared as exact integer cross-products, never floating-point rounded percentages. `regular` therefore directly implements the documented “one interval class covers at least 95% of usable deltas and at least 90% of entities have three or more observations” threshold. `mixed` is not a synonym for “not regular”: it requires at least two materially represented, mostly classifiable patterns. `irregular` is the observed residual when the evidence is sufficient; `unknown` means the evidence is not sufficient, not that irregularity was inferred.

## Aggregate evidence and privacy

Each assertion has one `bounded_scan` evidence record. Its source refs include the current table profile as `scan`, the current axis and grouping assertions as `dependency`, and the exact column/profile artifacts needed to verify null/special treatment. All references are same-snapshot, active, hash-matched, and sensitivity is their maximum.

The generic evidence basis is the table population and uses the row classification above:

```json
{
  "population": "<qualified-table>",
  "total_count": 0,
  "exclusions": {
    "physical_null": 0,
    "confirmed_special": 0,
    "parse_failure": 0
  },
  "usable_count": 0,
  "computation": "bounded_scan"
}
```

The counts always reconcile: `physical_null + confirmed_special + parse_failure + usable_count = total_count`. Required aggregate measurements are `distinct_entity_axis_observations`, `duplicate_entity_axis_excess_rows`, `entities_with_usable_observation`, `entities_with_three_or_more_observations`, `usable_delta_count`, `classified_delta_count`, `recognised_interval_class_count`, `dominant_interval_delta_count`, and `second_interval_delta_count`. The required ratios are `adequately_observed_entity_ratio` (`A/E`) and `dominant_interval_ratio` (`C(k)/D`); a zero denominator omits the ratio rather than manufacturing zero.

For auditability, class maxima are deterministic. Order recognised classes by descending count and then by canonical JSON of `{unit,step}` with sorted keys. `dominant_interval_delta_count` is the first count, or zero when no recognised class exists. `second_interval_delta_count` is the largest count of a distinct second class, or zero when fewer than two recognised classes exist. A tie therefore has a deterministic first class but does not manufacture a unique semantic winner; the count-based state rules still apply. In particular, `mixed` is auditable because its second count must satisfy `20 * second_interval_delta_count >= D`, while `regular` alone may publish the first class object.

No payload may include a grouping value, temporal value, sorted key, row position, bounds, samples, `top_k`, per-entity count, per-class histogram, raw parser exception, dictionary prose, or format example. The closed interval object is allowed only as the regular claim's `observed_interval_class`; it reveals a class, not a source value. This remains a trusted workspace contract, not RBAC or tenant isolation.

## Authority and resolution

The observer emits one `verified_observation` claim and `observed` resolution for a `regular`, `mixed`, or `irregular` result. It does not publish a deterministic cadence proposal: reviewed Date/Period metadata authorizes the axis, but does not declare cadence.

`confirmed` is possible only through a same-snapshot `source_confirmed_structural` decision that confirms this exact axis, grouping, cadence value, and evidence dependency fingerprint. It cannot replace a verified observed value, provide an expected frequency, select reporting meaning, or confirm a different axis/grouping. A consumer-local decision remains owner-bound and cannot satisfy portable exact reuse. A source declaration that says “monthly” is outside this predicate until a separately designed declared-frequency facet exists; it is neither silently merged nor made a conflict with observed cadence here.

`unknown` carries no effective claim. `not_applicable` is not emitted: absence of a qualifying grouping or axis is lack of evidence, not affirmative proof that cadence is inapplicable. Multiple eligible pairs and different observed results across pairs are independent assertions, never an ambiguity or a typed conflict.

## Bounded work, dependencies, and reuse

Before a raw read, the observer forms the complete eligible pair plan in canonical axis/grouping order. It uses the existing bounded reviewed-candidate policy values: at most 32 grouping candidates, 32 temporal axes, and 256 axis/grouping pairs. It additionally requires the exact retained table row count to be at most `MAX_CADENCE_ROWS = 1,000,000`. If any candidate or row bound is exceeded, it performs no raw read, does not truncate to a lexical prefix or publish a partial set, and returns unavailable `DSC_R_INSUFFICIENT_BASIS`.

For an in-bounds plan, a materialization may make at most one all-row `load_table` call for the table, projecting the union of required axis and grouping columns. Per-pair state is calculated in memory and only the aggregate evidence above is persisted. Immediately before any save, the producer re-reads and revalidates every planned prerequisite locator, assertion/artifact ID, payload hash, active lifecycle status, table/inventory/profile hash, and the complete candidate plan. Any mismatch fails closed with no save, no supersession, and no mixed-era publication. This one-read limit is an execution bound, not a dependency: batching an unrelated pair must not alter another pair's fingerprint or assertion payload.

Each candidate fingerprint includes the snapshot/table, axis and grouping assertion IDs and hashes, their role-bearing profile refs/hashes, exact table/inventory profile refs/hashes, normalizer and classifier versions, candidate-limit policy values, schema/context/registry versions, and evidence-policy version. It deliberately excludes other axis/grouping candidates in the batch.

- An exact matching active assertion is `exact_reused` with no raw scan.
- A changed axis dependency invalidates only pairs for that axis; a changed grouping dependency invalidates only pairs for that grouping. A changed table/snapshot, policy, normalizer, or relevant profile invalidates only pairs that cite it.
- A fresh replacement is immutable and supersedes only its own prior producer-owned assertion through AAR lineage. If a currently active prerequisite disappears, producer-owned observed assertions for the affected pair are withdrawn/superseded; independently owned confirmation artifacts remain readable but cannot fulfill this contract without the prerequisite.
- A candidate with sufficient prerequisites but insufficient **post-scan** observation basis persists an immutable `unknown` assertion with its safe aggregate evidence and `DSC_R_INSUFFICIENT_BASIS`, allowing exact reuse without re-scanning unchanged data. A **pre-scan** refusal (normalizer unavailable or row/candidate cap) publishes no assertion because it has no truthful candidate-local aggregate observation; the predicate's required/advisory result is unavailable `DSC_R_INSUFFICIENT_BASIS`. A source/profile/row-count inconsistency is instead an integrity failure, never an `unknown` assertion. Only the post-scan lack-of-basis assertion is selectable as `unknown`.

## Requirements, failure mapping, and fail-closed behaviour

Required and advisory selectors first attempt exact reuse, then may materialize this supported predicate. A failed required selector makes the context unfulfilled; an advisory failure does not. Optional selectors never observe, infer, publish, or supersede. They return only a complete exact retained materialization, otherwise `DSC_R_OPTIONAL_NOT_MATERIALIZED`.

| Condition | Result / stable code |
| --- | --- |
| No eligible temporal-binding or entity-binding pair | unavailable `DSC_R_NO_EVIDENCE` |
| Required prerequisite/profile/table source missing | unavailable `DSC_R_SOURCE_MISSING` |
| Hash, lifecycle, snapshot, inventory, row-count, or payload-integrity failure | unavailable `DSC_R_SOURCE_INTEGRITY_FAILED` |
| No usable delta, <90% adequate-entity coverage, or a completed bounded scan cannot support a state | persisted candidate `unknown` with `DSC_R_INSUFFICIENT_BASIS` |
| Exact normalizer unavailable, row/candidate cap, or scan cannot start | unavailable `DSC_R_INSUFFICIENT_BASIS`; no assertion |
| Payload would require a forbidden value-level field | unavailable `DSC_R_EVIDENCE_FORBIDDEN` |
| Optional without a complete exact retained cadence set | unavailable `DSC_R_OPTIONAL_NOT_MATERIALIZED` |

Failure text exposes no path, raw value, source existence detail, parser text, candidate count beyond the selector result, or scan exception. `DSC_R_DEPENDENCY_CHANGED` describes a reuse comparison only; it is never used to mask a fresh observation or to claim an observed cadence.

When an `unknown` assertion exists and the selector accepts `unknown`, it is a fulfilled pin. When the selector excludes that state, the selector is unavailable with `DSC_R_STATE_NOT_ACCEPTED`; required/advisory semantics then follow the normal protocol rule above. This separates a truthful observed lack of basis from a consumer's acceptance policy.

## Acceptance gate

The observed-cadence implementation is complete only when tests prove:

1. Only active reviewed Date bindings, active exact recognised calendar-quarter Period bindings, and active reviewed Identifier bindings form independently keyed axis/grouping candidates; no global, composite, name-only, automatically mapped, or row-grain-derived grouping is emitted.
2. Raw input order and duplicate entity/time rows do not change the result; duplicate pairs never add zero deltas.
3. The closed scan-time Date parser accepts only the documented native/date-midnight/exact-ASCII forms, counts every other form as a parse failure, and does not require C2 Date calendar metadata; exact recognised calendar-quarter Period remains the only supported Period form.
4. The exact row-exclusion arithmetic and every aggregate measurement—including deterministic dominant/second counts and their zero/omission semantics—reconcile, and no forbidden raw or per-entity content is persisted.
5. The closed class taxonomy, exact day-of-month rule, and zero date tolerance are enforced, including Jan-31/Feb-28 and leap-day consequences.
6. Regular uses exact >=95% dominant-delta and >=90% adequate-entity thresholds; fixtures distinguish regular, mixed, irregular, and unknown.
7. A sole valid confirmation projects over one agreeing producer proposal; duplicate or non-agreeing prerequisite/cadence assertions are unavailable and do not enter a pair plan.
8. A cap breach performs zero raw reads and produces no partial candidate set; an in-bounds fresh plan performs at most one projected table read.
9. A prerequisite/profile/candidate-plan change after the scan and before persistence creates no assertion or supersession.
10. Exact reuse performs no raw read; changing one axis/grouping/profile supersedes only its affected cadence assertions and preserves unrelated temporal/entity/grain facts.
11. Optional resolution performs no observation; required/advisory and every stable failure mapping behave as specified without leakage.
12. A confirmed cadence cannot contradict, relabel, or replace the underlying verified observation, and no declared/expected-frequency meaning is introduced.
13. Existing physical schema, entity, row-grain, temporal-binding, D06, D08, manifest, API, and UI behaviour remains unchanged.

## Explicit exclusions and next step

This increment excludes expected/declared frequency, reporting period or grain, panel/dataset-form classification, global cadence, composite grouping, timezone/DST handling, intervals/instants, joins, cross-snapshot comparison/carry-forward, findings, readiness, D06/D08 adapters, manifests, UI, and KB changes.

After this gate, the next bounded design increment is the consumer-owned D06 shadow adapter. It may read cadence only as advisory structural evidence and must retain D06's reporting-period meaning, confirmation, manifest, controls, execution, and findings contracts.
