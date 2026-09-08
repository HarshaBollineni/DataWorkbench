# Dataset Structure Context — Step 1-c schema and protocol

**Status:** normative v1 validation, assembly, and trusted-workspace persistence foundation. The two DSC AAR descriptors are active. Their descriptor write validators are enforced by the generic AAR repository, so generic writes cannot bypass DSC integrity checks. This release creates no endpoint, consumer adapter, finding, readiness gate, or UI behaviour.

**Outcome:** DSC v1 resolves independently immutable assertion *payloads* for one immutable snapshot, then publishes a separately immutable context *payload* from one consistent `resolved_as_of` read. The atomic resolution unit is `(snapshot, subject, predicate, instance_key)`; a facet is registry grouping only. Assertion multiplicity is `single|keyed_set`.

The machine-readable Draft 2020-12 contracts are [`common.schema.json`](schemas/dsc-v1/common.schema.json), [`assertion.schema.json`](schemas/dsc-v1/assertion.schema.json), [`context.schema.json`](schemas/dsc-v1/context.schema.json), [`request.schema.json`](schemas/dsc-v1/request.schema.json), and [`response.schema.json`](schemas/dsc-v1/response.schema.json). They use stable `https://dataworkbench.invalid/` identifiers and locally resolvable references; this does not claim a live endpoint.

## AAR boundary and artifact model

The AAR, not a DSC payload, assigns `art_…` artifact IDs, computes the SHA-256 `payload_hash` over canonical payload bytes, records identity/lineage, and owns lifecycle `active|superseded`. Therefore assertion and context payload schemas contain neither `artifact_id`, `payload_hash`, nor lifecycle, and no payload self-hashes. References, context pins, source references, and response `context_ref` use the AAR metadata pair `art_…` plus payload hash.

Logical DSC IDs are distinct: `dsca_…` assertion, `dscc_…` claim, `dsce_…` evidence, and `dscx_…` conflict. They are opaque; JSON Schema does not recompute hashes or force equality between an AAR artifact ID and an assertion ID.

The AAR registration mapping is explicit:

| Payload | AAR scope and owner | Identity inputs |
| --- | --- | --- |
| Portable `dataset_structure_assertion` | `universal`; no owner/workflow | Assertion locator, dependency fingerprint, schema/context/registry versions, producer algorithm version where applicable, and role-bearing evidence sources |
| Consumer-local `dataset_structure_assertion` | `diagnostic_local`; `owner_id` is the decision owner/reuse namespace | The portable inputs plus decision ID and owner; it cannot satisfy another consumer's exact-reuse request |
| `dataset_structure_context` | `diagnostic_local`; `owner_id` is request `consumer_id`/reuse namespace | Recomposable request fingerprint, protocol/context version, consistent-read time, and ordered assertion pins |

The descriptors are active for JSON persistence. A DSC-aware adapter derives identity and lineage, while the generic repository invokes each descriptor's mandatory write validator. The validator checks the ready/active snapshot, active source and pin roles/hashes, same-snapshot provenance, promotion provenance, assertion contents for every pin, context sensitivity maximum, owner/consumer binding, dependency/reuse semantics, and AAR identity inputs. DSC blob writes are rejected. `owner_id` is provenance and an exact-reuse namespace, not tenant or authorization data.

AAR's required `methodology_fingerprint` is a fixed DSC adapter fingerprint over schema, registry, and envelope-assembler versions—not consumer diagnostic methodology. Producer-observer versioning belongs in each assertion's `dependency_fingerprint`, together with its source state. Its required `population_fingerprint` identifies the assertion evidence basis or, for an envelope, its canonical selected snapshot/tables. `comparison_snapshot_id`, target and feature semantics are empty for DSC v1.

| Registry grouping | Initial predicate | Atomic assertion |
| --- | --- | --- |
| `table.physical` | `table.physical/schema_column` | qualified column; normally `keyed_set` |
| `table.structure` | `table.structure/dataset_form` | one form; `single` |
| `table.structure` | `table.structure/entity_binding` | qualified binding; `keyed_set` |
| `table.structure` | `table.structure/row_grain` | selected structural grain; `single` |
| `table.temporal` | `table.temporal/temporal_binding` | named axis; `keyed_set` |
| `table.temporal` | `table.temporal/observed_cadence` | axis plus grouping; `keyed_set` |
| `relationship.declared` | `relationship.declared/relationship` | ordered-endpoint declaration; `keyed_set` |

`subject` is discriminated: `{kind:"table",table}` or `{kind:"relationship",from_table,to_table}`. Runtime enforces predicate/subject/value/multiplicity matching, canonical table naming, source/snapshot scope, and uniqueness of the atomic key.

## Assertion payloads: claims, evidence, and conflicts

An assertion payload requires `schema_version: 1`, type, logical assertion ID, context version, snapshot, subject, predicate, instance key, multiplicity, `claims`, evidence, conflicts, resolution object, dependency fingerprint, and sensitivity. It has no intrinsic reuse state: the same immutable assertion can be `fresh` for one request and `exact_reused` for another.

Claims permit competing structural statements. Each has `claim_id`, authority, typed value, and its evidence IDs; an optional decision and optional inference algorithm/confidence. Authority is `verified_observation|source_reviewed_metadata|source_confirmed_structural|consumer_local_decision|deterministic_inference`. Algorithm and confidence are allowed only for deterministic inference.

```json
{
  "schema_version": 1,
  "artifact_type": "dataset_structure_assertion",
  "assertion_id": "dsca_row_grain",
  "context_version": "1",
  "snapshot": {"asset_id": "asset_1", "snapshot_id": "snapshot_7"},
  "subject": {"kind": "table", "table": "applications"},
  "predicate": "table.structure/row_grain",
  "instance_key": "primary",
  "multiplicity": "single",
  "claims": [{"claim_id": "dscc_grain", "authority": "source_confirmed_structural", "value": {"kind": "row_grain", "key_columns": [{"table": "applications", "column": "application_id"}]}, "evidence_ids": ["dsce_profile"]}],
  "evidence": [{"evidence_id": "dsce_profile", "kind": "physical_profile", "source_refs": [{"artifact_id": "art_profile_1", "role": "profile", "payload_hash": "0000000000000000000000000000000000000000000000000000000000000000"}], "basis": {"population": "applications", "total_count": 100, "exclusions": {"physical_null": 0, "confirmed_special": 0, "parse_failure": 0}, "usable_count": 100, "computation": "exact"}, "measurements": [{"name": "unique_key_rows", "count": 100}], "sensitivity": "internal"}],
  "conflicts": [],
  "resolution": {"status": "confirmed", "effective_claim_ids": ["dscc_grain"], "reason_codes": [], "conflict_ids": []},
  "dependency_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
  "sensitivity": "internal"
}
```

A decision is append-only and contains decision ID, action `confirm|replace|clear|mark_not_applicable|promote`, and scope `source_confirmed_structural|consumer_local`. `consumer_local_decision` claims require a consumer-local decision and owner ID; `source_confirmed_structural` claims require a source-confirmed decision. Other authorities cannot carry consumer-local decisions. A promotion is source-confirmed and its `promotion_of` is an earlier opaque decision ID, never a claim ID.

Resolution is an object with `status` (`observed|proposed|confirmed|conflict|unknown|not_applicable`), effective claim IDs, reason codes, and conflict IDs. Observed, proposed, and confirmed carry exactly one effective claim; unknown, not-applicable, and conflict carry none. Conflict must cite conflict IDs, while all non-conflict statuses cite none. Not-applicable additionally carries `DSC_R_NOT_APPLICABLE_CONFIRMED` and affirmative reviewed/decision evidence; absence is unknown. No score or confidence overrides an authoritative contradiction.

Evidence contains its kind, role-bearing AAR source refs (`artifact_id`, role, hash), population/count basis, sensitivity, and aggregate measurements. A ratio measurement must carry numerator and denominator. Exclusions are explicit: `physical_null`, `confirmed_special`, and `parse_failure`; computation is `exact|bounded_scan|sampled`. Raw values, samples, examples, and raw identifiers never appear.

## Typed value rules

The closed payload shape permits only typed values: schema column, dataset form, entity binding, row grain, temporal binding, cadence, or relationship. Runtime rejects a predicate/value mismatch.

- Dataset form: `cross_sectional|panel|event|interval|repeated_cross_section|unclassified`.
- Temporal bindings are keyed sets of named axes and use `date|instant|interval`, precision `year|quarter|month|day|hour|minute|second|millisecond|microsecond|unknown`, and calendar `gregorian|other_declared|unknown`. A date omits timezone; an interval has exactly start/end columns at runtime. Cadence is separate, bound to axis plus grouping, and is `regular|mixed|irregular|unknown`, with optional observed interval class.
- Phase C1 refines this rule in the [Time-column Context contract](dataset-structure-context-temporal-binding.md): one reviewed qualified column is one keyed candidate and multiple candidates are never silently selected. Reviewed `Date` maps to `date`; reviewed `Period` requires a new single-column `period` enum value with timezone prohibited before Phase C2 can emit it. Runtime validation and JSON Schema must change together. Precision/calendar stay `unknown` unless permitted reviewed or exact calendar-quarter evidence exists; grouping is omitted.
- Relationships are created only by source constraint, reviewed declaration, or promoted structural decision. They carry ordered key pairs, direction `from_references_to|to_references_from|undirected`, cardinality (including `unknown`), and null policy `nulls_never_match|nulls_equal`. Scans validate declarations but never create a relationship, select a join, or claim business meaning.

## Context payload and resolution protocol

A context additionally persists `request_as_of`, `supported_context_versions`, and `consumer_id`, together with the snapshot, selected tables, and normalized selectors. Those request inputs make `request_fingerprint` independently recomputable. Sensitivity is the maximum of pinned assertions, not an authorization projection.

Each persisted context selector result embeds the complete normalized request selector: selector ID, subject, predicate, requirement, and accepted resolution states, in addition to its result and pins or reasons. Runtime therefore recomputes the context overall result and verifies fulfilled pin resolutions against the persisted accepted states; response selector results exactly mirror the context. Known predicates enforce their subject-kind rules. A syntactically valid future predicate is accepted for negotiation only and must return `unsupported` with `DSC_R_UNSUPPORTED_FACET`, never fulfilled or unavailable.

A context payload requires `schema_version`, type, protocol/context versions, request fingerprint, snapshot, consistent-read time, sensitivity, selected tables, overall result, and selector results. It may have zero fulfilled pins. A fulfilled selector result has pins holding AAR `art_…` artifact ID, `dsca_…` assertion ID, payload hash, resolution status, and request-relative `fresh|exact_reused|reuse_candidate|not_reusable` disposition. An unavailable/unsupported selector has prefixed reason codes. Sensitivity is the monotonic maximum (`external_safe < internal < confidential`) of accessible pinned evidence.

```json
{
  "protocol_version": "1",
  "supported_context_versions": ["1"],
  "snapshot": {"asset_id": "asset_1", "snapshot_id": "snapshot_7"},
  "as_of": "latest",
  "tables": ["applications", "payments"],
  "consumer_id": "consumer_example",
  "selectors": [
    {"selector_id": "columns", "subject": {"kind": "table", "table": "applications"}, "predicate": "table.physical/schema_column", "requirement": "required", "accepted_resolution_states": ["observed", "confirmed"]},
    {"selector_id": "declared_link", "subject": {"kind": "relationship", "from_table": "applications", "to_table": "payments"}, "predicate": "relationship.declared/relationship", "requirement": "advisory", "accepted_resolution_states": ["confirmed"]}
  ]
}
```

Requests use exact `protocol_version: "1"`, advertise context version `"1"` among the client's unique supported versions, have unique selected tables and runtime-unique selector IDs, canonical subjects, requirement `required|advisory|optional`, and nonempty accepted states. Each table subject and both relationship endpoints must be selected. `optional` means already materialized only; it never causes observation, inference, or publication. `as_of` is literal `latest`; historical input is rejected. The closed request schema prohibits `comparison_snapshot_id`: consumers resolve two envelopes for cross-snapshot work.

The server chooses its preferred mutual context version. Each response selector is `fulfilled|unavailable|unsupported`; any failed required selector makes overall `unfulfilled`, while advisory/optional failure does not. A response `context_ref` is an AAR artifact ID/hash pair. At runtime its selector results must exactly equal the pinned context results; a malformed request is rejected, not represented as fulfillment.

| Rejection code | Meaning |
| --- | --- |
| `DSC_E_INVALID_REQUEST` | Shape or semantic validation failed. |
| `DSC_E_PROTOCOL_VERSION_UNSUPPORTED` | Protocol is not supported. |
| `DSC_E_CONTEXT_VERSION_NO_MATCH` | No mutual context version. |
| `DSC_E_SELECTOR_SCOPE`, `DSC_E_DUPLICATE_SELECTOR` | Invalid selected-table/selector scope or duplicate selector ID. |
| `DSC_E_SNAPSHOT_MISMATCH`, `DSC_E_CROSS_SNAPSHOT_UNSUPPORTED`, `DSC_E_AS_OF_UNSUPPORTED` | Snapshot, comparison, or as-of rule was violated. |
| `DSC_E_TENANT_SCOPE` | Reserved for a future tenant/authorization boundary; DSC v1 does not implement it. |

| Stable reason family | Codes |
| --- | --- |
| Basis/state | `DSC_R_NO_EVIDENCE`, `DSC_R_AMBIGUOUS_CANDIDATES`, `DSC_R_INSUFFICIENT_BASIS`, `DSC_R_STATE_NOT_ACCEPTED`, `DSC_R_NOT_APPLICABLE_CONFIRMED`, `DSC_R_DECISION_CLEARED` |
| Relationship/conflict | `DSC_R_RELATIONSHIP_UNDECLARED`, `DSC_R_CONFLICT_DECLARED_OBSERVED`, `DSC_R_CONFLICT_COMPETING_CONFIRMATIONS`, `DSC_R_CONFLICT_RELATIONSHIP_ENDPOINT`, `DSC_R_CONFLICT_TEMPORAL_INTERPRETATION` |
| Dependency/capability | `DSC_R_DEPENDENCY_CHANGED`, `DSC_R_SOURCE_MISSING`, `DSC_R_SOURCE_INTEGRITY_FAILED`, `DSC_R_EVIDENCE_FORBIDDEN`, `DSC_R_UNSUPPORTED_FACET`, `DSC_R_OPTIONAL_NOT_MATERIALIZED` |

## Canonicalisation, security, and validation boundary

Assertion and context payloads must already be NFC-normalized, including object keys. Their validators reject NFD rather than silently changing persisted data, which guarantees DSC canonical bytes equal the generic AAR JSON serialization for valid JSON-native payloads. Requests may still be normalized defensively.

DSC v1 has no tenant or RBAC boundary. It relies on the trusted application/workspace boundary. Its active descriptors use repository-enforced validators, so direct generic repository JSON writes receive the same source, pin, identity, lineage, promotion, sensitivity, and owner/consumer checks as the dedicated adapters. AAR list/detail/payload/lineage routes are not authorization-aware; no claim of tenant isolation or access control is made. DSC types reject blob writes to prevent a non-JSON persistence path bypassing the payload contract.

Canonical payload JSON is JSON-native, UTF-8, NFC-normalised, sorted by object key, and rejects duplicate keys. Ordered semantic arrays remain ordered, especially relationship pairs and endpoint order. AAR hashes these canonical payload bytes with SHA-256; a payload never contains its own hash.

`owner_id` is derived from the consumer-local decision owner or the context `consumer_id`; it is not a request authorization claim. Every source and pin must be active, hash-matched, and from the same ready immutable snapshot. Schema validation checks only shape. Runtime and persistence validation additionally check canonicalisation/hash recomputation, snapshot readiness, evidence count integrity, source integrity, all cross references, claim/decision authority, promotion provenance, conflict pairing, temporal interval semantics, selector uniqueness/scope, accepted states, reuse comparison, consistent-read construction, response/context equality, and sensitivity maximum. AAR lifecycle remains metadata external to payload.

## Step 1-b blocker resolutions

| Step 1-b blocker | v1 resolution |
| --- | --- |
| 1. Atomic assertions | Explicit `(snapshot, subject, predicate, instance_key)` identity and multiplicity. |
| 2. Decision portability | Source-confirmed versus owner-bound consumer-local decisions; append-only promotion. |
| 3. Typed conflict | Assertion-local claim/evidence conflict records and four stable conflict reasons. |
| 4. Relationships | Declaration-only relationships with ordered pairs, direction, cardinality, null policy, and scan boundary. |
| 5. Multiple temporal axes | Keyed date/instant/interval bindings plus independently axis/grouping-bound cadence. |
| 6. Negotiation and fulfillment | Closed request/response shapes, table scope, exact versions, result rules, codes, and canonicalisation. |
| 7. Persistence/as-of | Immutable assertion payloads plus consistent-read context payload pinning AAR refs/hashes. |
| 8. Evidence basis | Typed aggregate basis, denominator-aware ratios, and explicit special-value exclusions. |
| 9. Privacy/trust boundary | Sensitivity maximum, same-snapshot source scope checks, and bounded safe payloads. Tenant/RBAC authorization and projection remain deferred. |
| 10. Cross-snapshot | Explicit exclusion; consumers compose separate envelopes. |

## Acceptance fixtures, exclusions, and gate

The active foundation includes validation, assembly, trusted-workspace JSON persistence, and producers for `table.physical/schema_column`, `table.structure/entity_binding`, and `table.structure/row_grain`. Schema and singleton grain use retained exact profiles; composite grain performs at most one bounded all-row read across reviewed Identifier × Date/Period candidates and persists aggregates only. Required/advisory selectors materialize supported predicates, optional selectors only reuse a valid complete materialization, and changed dependencies supersede atomic assertions. Temporal binding remains documentation-only under Phase C1.

It intentionally adds no endpoint, consumer adapter, D06/D08 integration, readiness gate, or UI.

| Fixture | Required v1 result |
| --- | --- |
| Unchanged confirmed row grain | Context pin may be `exact_reused`; assertion payload itself has no reuse state. |
| Two plausible temporal axes | Keyed candidates or local conflict/unknown; no reporting-period selection. |
| No stable entity | `not_applicable` only with affirmative evidence/decision; otherwise `unknown`. |
| Irregular deltas | Separate cadence assertion; no expected frequency. |
| Declared relationship and agreeing scan | Declaration plus validation evidence; no inferred join. |
| Required unavailable / optional unmaterialized | Overall unfulfilled only for required; optional returns `DSC_R_OPTIONAL_NOT_MATERIALIZED`. |
| Sensitive, missing, or tampered source | Fail closed with the applicable `DSC_R_*` code and no existence disclosure. |

v1 excludes inferred joins, arbitrary composite search, raw/bounded values, expected frequency, reporting meaning/grain, business ontology/roles/KB, consumer readiness/findings/UI, and cross-snapshot compatibility/carry-forward. D06 is only a later shadow adopter. D08 remains unchanged and advisory.

The next implementation phase is Phase C2: make the documented `period` representation valid in runtime and JSON Schema, then add the profile-backed `table.temporal/temporal_binding` producer. It must satisfy the Phase C1 acceptance gate before cadence or a consumer shadow adapter begins. D06/D08 and UI work remain later.
