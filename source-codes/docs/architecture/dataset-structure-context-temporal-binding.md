# Dataset Structure Context — Phase C1 Time-column Context contract

**Status:** documentation-only design contract. No temporal producer, consumer adapter, diagnostic, API, or UI behaviour is implemented by this phase.

**Technical predicate:** `table.temporal/temporal_binding`

**Plain-language name:** Time-column Context

## Outcome

Time-column Context publishes time-related columns that Data Sourcing has explicitly reviewed, together with enough exact supporting evidence for a later consumer to decide whether a candidate is usable. It does not choose a reporting period, infer frequency, or decide that a dataset is panel data.

Phase C2 may implement this contract only after the `temporal_type` representation described below is updated consistently in runtime validation and JSON Schema.

## Candidate eligibility

A column is eligible only when all of the following are true:

1. Its table and column profile artifacts are active, hash-verified, exact, and belong to the selected immutable snapshot.
2. Its inventory membership agrees with the retained table profile.
3. `role_reviewed=1` was recorded by the explicit Data Sourcing review path. `provisional=0`, a confident automatic mapping, a suggestive name, dtype, or parse rate is not review evidence.
4. The reviewed role is `Date` or `Period`, matched case-insensitively after trimming.

Legacy, missing, or null review provenance fails closed. A column may still be physically profiled, but it cannot become a shared time candidate.

## Atomic assertion and candidate identity

The producer emits one independently reusable keyed assertion for each eligible qualified column:

- `predicate`: `table.temporal/temporal_binding`
- `multiplicity`: `keyed_set`
- `subject`: the qualified table
- `instance_key`: `column:<column-name>`
- `axis_id`: `column:<column-name>`
- `columns`: exactly that one qualified column

`axis_id` is a mechanical candidate identifier, not a label such as reporting period, event time, vintage, or outcome window. Multiple eligible columns remain multiple assertions. The producer does not rank them, choose a primary axis, bundle them, or create a conflict merely because more than one exists.

## Typed value policy

| Reviewed information | `temporal_type` | `precision` | `calendar` | `timezone` | Result |
| --- | --- | --- | --- | --- | --- |
| Role `Date` | `date` | reviewed declaration when available; otherwise `unknown` | reviewed declaration when available; otherwise `unknown` | omitted | `proposed` |
| Role `Period` | `period` | reviewed declaration when available; an exact recognised calendar-quarter profile may support `quarter`; otherwise `unknown` | reviewed declaration or exact recognised calendar evidence; otherwise `unknown` | omitted | `proposed` |
| Explicit reviewed instant declaration | `instant` | reviewed declaration or `unknown` | reviewed declaration or `unknown` | only an explicitly reviewed timezone | later phase |
| Explicit reviewed start/end declaration | `interval` | reviewed declaration or `unknown` | reviewed declaration or `unknown` | only when valid for the declared endpoints | later phase |

The active v1 runtime and JSON Schema currently allow only `date|instant|interval`. Coercing a single reviewed `Period` column into any of those values would invent meaning. Before the producer is enabled, Phase C2 must add `period` as a single-column type with timezone prohibited in both validators. Since no endpoint or consumer adapter currently emits or consumes temporal assertions, this is an amendment to the unused v1 facet; runtime and schemas must change together and receive a compatibility test. If that precondition is no longer true when Phase C2 starts, introduce a negotiated context version instead.

`grouping` is omitted. Grouping belongs to later cadence observation or a consumer decision.

## Supporting evidence

Each candidate references the current table and column profile artifacts rather than copying profile content. Evidence is aggregate-only and records:

- total rows;
- physical-null rows;
- confirmed-special rows;
- usable regular rows;
- temporal parse-failure rows when the exact retained profile supplies that fact; and
- whether temporal parse evidence was available.

Evidence arithmetic must remain exact. When parse evidence is applied, `physical_null + confirmed_special + parse_failure + usable = total`. When no temporal parser evidence exists, parsing is not treated as an exclusion: the evidence explicitly reports that parse evidence is unavailable and does not present an invented parse-failure measurement.

An exact recognised calendar-quarter profile may support precision `quarter`, calendar `gregorian`, and zero period-format failures because that profiler accepts the format only when every regular value matches. No other precision, calendar, or timezone may be inferred from a name, dtype, minimum, maximum, or a best-effort parser.

Profile bounds remain in the referenced AAR profile. The current DSC measurement shape carries counts and ratios, not typed bounds; Phase C2 must not copy minima, maxima, raw period labels, examples, or `top_k` values into the assertion.

## Authority and plain-language outcomes

| Protocol state | Plain-language meaning | Rule |
| --- | --- | --- |
| `proposed` | Candidate available | Reviewed Date/Period metadata identifies the column, but no shared structural decision selects its meaning. |
| `confirmed` | Confirmed time column | Requires a separate `source_confirmed_structural` decision; profile evidence alone cannot confirm it. |
| `unknown` or unavailable | More information needed | Required source, review provenance, or exact supporting evidence is missing or invalid. |
| `conflict` | Conflicting information | Used only for typed contradictory claims, not simply because several candidates exist. |

A consumer may show all proposed candidates for confirmation or correction. DSC does not convert that choice into reporting meaning.

## Dependency, reuse, and lifecycle

Each assertion dependency includes the snapshot, qualified table/column, reviewed-role profile artifact and hash, exact profile artifact and hash, review flag, schema/context version, producer version, and evidence-policy version.

- An exact dependency and payload match may return `exact_reused` without reading raw table rows.
- Any role, review provenance, profile hash, schema, or producer-policy change makes the prior candidate non-reusable.
- A replacement is a new immutable assertion; the prior artifact remains readable and is marked superseded through AAR lineage.
- Invalidating one candidate must not invalidate unrelated schema, entity, grain, or temporal candidates.
- Optional selectors never materialise candidates. Without an exact retained assertion they return `DSC_R_OPTIONAL_NOT_MATERIALIZED`.

## Privacy and trust boundary

Allowed payload content is limited to qualified names, stable IDs, hashes, sensitivity, counts, ratios, enumerated types, and safe algorithm/version labels. Raw rows, cell values, identifiers, descriptions, dictionary prose, prompts, samples, `top_k`, and unrestricted format examples are prohibited.

This remains a trusted single-application/workspace contract. `owner_id` is provenance and a reuse namespace, not RBAC or tenant isolation.

## Phase C2 acceptance gate

Phase C2 is complete only when tests prove:

1. Runtime and JSON Schema agree on the single-column `period` type and reject timezone for Date/Period.
2. A reviewed Date and a reviewed Period each produce one valid `proposed` atomic assertion.
3. Unreviewed, automatically mapped, name-only, missing-provenance, or non-exact candidates are not published.
4. Multiple eligible candidates remain separate and no winner or ambiguity conflict is invented.
5. Exact null, confirmed-special, parse-failure, and usable counts reconcile; unavailable parse evidence is not represented as an observed zero.
6. Precision and calendar remain `unknown` unless permitted reviewed or exact calendar-quarter evidence exists.
7. Optional resolution performs no observation; exact reuse performs no raw scan.
8. A changed dependency creates a fresh assertion and supersedes only the affected candidate.
9. Missing or tampered sources fail closed with stable reason codes and no path, value, or existence leakage.
10. Existing schema, entity, singleton/composite grain, D06, and D08 behaviour remains unchanged.

## Explicit exclusions

Phase C1 and the proposed Phase C2 producer do not include cadence or frequency, expected frequency, reporting meaning or reporting grain, panel/dataset-form classification, entity-time grouping selection, relationship/join inference, cross-snapshot carry-forward, D06/D08 adapters, manifests, findings, readiness gates, UI changes, or KB changes.

After Phase C2 passes this gate, the next bounded design increment is observed cadence, followed later by a consumer-owned D06 shadow adapter.
