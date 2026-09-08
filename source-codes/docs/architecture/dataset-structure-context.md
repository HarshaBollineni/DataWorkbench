# Dataset Structure Context

**Status:** Step 1-a design contract, with the v1 validation/assembly and trusted-workspace persistence foundation active.

**Scope:** diagnostic-neutral platform context for immutable dataset snapshots.

**Refined by:** the normative Step 1-c [schema and protocol](dataset-structure-context-schema-protocol.md) resolves its assertion, persistence, and negotiation details.

## Decision and ownership

Dataset Structure Context (DSC) is a shared, evidence-backed description of one immutable dataset snapshot. It lets a consumer inspect physical and structural facts before applying its own semantics, readiness rules, or user experience. DSC is a proposal, provenance, and exact-reuse layer. It does not transform source values, certify data quality, select business meaning, or infer a business relationship without evidence.

| Owner | Accountable responsibility | Excluded responsibility |
| --- | --- | --- |
| Platform context/resolver (`analysis_runtime`, or a later dedicated platform domain) | Schema, facet registry, deterministic resolution, conflicts, dependency fingerprints, capability negotiation, exact-reuse evaluation | Diagnostic methodology, consumer readiness, wording, UI |
| Data Sourcing | Produce reviewed source metadata and physical-profile evidence; review and confirm source/dictionary declarations | Consumer semantic overlays or execution decisions |
| Analytics Artifact Repository (AAR) | Immutable payload storage, hashes, integrity checks, lifecycle events, safe summaries, directed lineage | Resolving facts or interpreting consumer semantics |
| Consumers (Test Lab diagnostics, RCA, reports, future callers) | Adapters, semantic overlays, required/optional choices, readiness, manifests, UI, reports, validation | Mutating shared facts or their provenance |

This follows current domain boundaries: Data Sourcing owns snapshot readiness, reviewed mapping, profile production, and metadata confirmation; AAR owns immutable artifact persistence and lineage. `analysis_runtime` is the documented shared home for analytical contracts, snapshot loading, target resolution, and capability registration.

## Snapshot context and facet boundaries

DSC is snapshot-level: asset, immutable snapshot, and its inspected table inventory. It publishes a separate **table facet** for each table; a request can select one or more. A table facet can describe dataset form, candidate row grain, qualified entity/time candidates, observed cadence, physical schema, and profile references. Facts never become global merely because tables have similarly named columns.

An optional **relationship facet** can describe qualified table bindings only when evidence supports a declared relationship. Evidence may be a reviewed source relationship/mapping, an explicit user-confirmed relationship decision, or a source-declared constraint retained with immutable provenance. Aggregate compatibility or overlap scans may be recorded as observations, but cannot create a join, select join keys, or claim business meaning. In the absence of relationship evidence, DSC returns no relationship facet.

| Shared DSC facts | Consumer-owned overlays |
| --- | --- |
| Snapshot/table identity, schema and profiles; dataset form; candidate/confirmed row grain; qualified entity/time bindings; observed cadence; reviewed source time metadata; evidence, conflicts, provenance | `reporting_grain`; thresholds; targets; selected segments; predicates/filters; role vocabulary; KB/terminology declarations; methodology; feature selection; run scope and presentation |

A time binding can be shared, but a consumer alone decides whether it is a reporting period, vintage, outcome window, event time, or irrelevant. DSC never derives `reporting_grain` from cadence.

## Authority, provenance, and conflicts

Authority is resolved per fact type, not by one global ranking. Verified observations govern physical presence. Reviewed metadata and explicit human decisions govern declared or intended structural meaning. Neither silently overwrites the other.

| Authority | Permitted evidence | DSC use |
| --- | --- | --- |
| User-confirmed frozen same-snapshot decision | Qualified table/column or relationship decision with manifest/run provenance | Exact reuse of that structural fact when dependencies match |
| Data Sourcing reviewed metadata | `time_basis`, `period_column`, bounds, dictionary mappings, confirmed column context, reviewed relationship declarations | Preferred proposal for declared source meaning |
| Verified observations | Active AAR profiles and versioned aggregate scans of the immutable snapshot | Authority for actual schema, values, bounds, uniqueness, repetition, cadence |
| Deterministic inference | Versioned rules over the above evidence | Candidate only, with evidence and confidence |
| Consumer/user input | Confirm, replace, clear, or mark a requested structural fact unavailable | Final decision when recorded by the responsible consumer |

Each facet value records qualified table/column(s), authority, source artifact or decision ID, payload/manifest hash, evidence summary, inference algorithm/version, confidence, resolution status, actor, and timestamp. DSC evidence contains no raw row values.

A confirmed same-snapshot decision can outrank a new proposal, but cannot override contradictory verified observations. Declared monthly frequency and observed irregular cadence remain separate facts and create a conflict; a score never resolves it. Missing evidence is `unknown`, not a guessed confirmation. Confidence is proposal strength, never permission to bypass a consumer confirmation rule.

## Facet registry and capability negotiation

The platform publishes a versioned facet registry: stable name, shape, evidence requirements, and scope. It never prescribes who requires a facet.

| Facet family | Examples | Scope |
| --- | --- | --- |
| `snapshot.identity` | asset, immutable snapshot, table inventory | Snapshot |
| `table.physical` | schema, table profile, qualified column-profile references | Table |
| `table.structure` | dataset form, candidate row grain, entity/time bindings | Table |
| `table.temporal` | parsed coverage, bounds, observed cadence, reviewed time metadata | Table |
| `relationship.declared` | qualified endpoints, reviewed keys/constraint, relationship evidence | Optional relationship |
| `evidence.provenance` | authority, summary, hashes, conflict, dependencies | Every facet |

A consumer requests capabilities; it does not ask the resolver to decide readiness.

```json
// request
{
  "protocol_version": "1",
  "supported_context_versions": ["1"],
  "snapshot": {"asset_id": "...", "snapshot_id": "..."},
  "tables": ["applications"],
  "scope_shape": "multi_table",
  "facets": [
    {"name": "table.physical", "table": "applications", "requirement": "required",
     "accepted_resolution_states": ["observed", "confirmed"]},
    {"name": "table.structure", "table": "applications", "requirement": "advisory",
     "accepted_resolution_states": ["observed", "confirmed", "proposed"]},
    {"name": "relationship.declared", "between": ["applications", "payments"],
     "requirement": "optional", "accepted_resolution_states": ["confirmed"]}
  ],
  "consumer": {"id": "consumer-owned", "adapter_version": "..."}
}

// response
{
  "negotiated": {"protocol_version": "1", "context_version": "1"},
  "context_ref": {"artifact_id": "...", "payload_hash": "..."},
  "facets": [
    {"key": "table.structure/applications", "resolution_status": "proposed",
     "reuse_disposition": "not_reused", "dependency_fingerprint": "..."},
    {"key": "relationship.declared/applications:payments", "resolution_status": "unknown",
     "reason": "no evidence-backed relationship declaration"}
  ],
  "unavailable": [],
  "unsupported": [],
  "conflicts": []
}
```

The response reports available, unavailable, proposed, and conflicting facts with evidence. The consumer alone declares required versus optional facets, overlay completeness, and whether it may execute or present an action. The resolver has no diagnostic-specific gates, labels, or prompts.

## Deterministic observation and proposal contract

DSC may run bounded, read-only scans of selected immutable tables to produce aggregate joint-column evidence. It may retain counts, ratios, hashes, period bounds, and bounded format examples, but never raw identifiers or rows. Every scan and algorithm is versioned and fingerprinted.

Phase C1 defines the next producer boundary in the [Time-column Context contract](dataset-structure-context-temporal-binding.md). Only columns whose `Date` or `Period` role was explicitly reviewed in Data Sourcing are eligible. Each qualified column remains an independent proposed axis; names, dtypes, parsing and bounds cannot create a candidate or choose a winner. The contract also records the required `period` type amendment before implementation, because coercing a single period column to date, instant, or a two-column interval would invent meaning.

| Proposed fact | Deterministic evidence | Strong suggestion gate | Otherwise |
| --- | --- | --- | --- |
| Time field | Reviewed Data Sourcing selection, date-like type, parse success, plausible bounds | At least 95% populated values parse, no confirmed peer conflict, lead score >= 0.90 with >= 0.20 margin | Qualified alternatives or `unknown` |
| Entity field | Reviewed identifier metadata, repetition, null rate, candidate-key evidence | Lead score >= 0.90 with >= 0.20 margin and no confirmed conflict | Alternatives; uniqueness/high cardinality alone is insufficient |
| Observed cadence | Within-entity ordered time deltas, never global row order | One interval class covers >= 95% usable deltas and >= 90% of entities with three or more observations | Irregular/mixed or `unknown` |
| Dataset form | Entity/time bindings plus repetition evidence | One form is supported without a conflicting candidate | Alternatives; never business meaning |
| Candidate row grain | Uniqueness of bounded combinations of reviewed key candidates | Qualified combination unique, or exceptions counted and explained | `Needs a choice`; do not search arbitrary high-dimensional combinations |
| Relationship compatibility | Reviewed/source relationship declaration plus qualified endpoint/key evidence | Declaration and observations agree with explicit provenance | Conflict or absence; never infer a join from compatibility alone |

Dataset form, business row grain, entity/composite key, time meaning, and a relationship declaration are material choices. New proposals are never auto-confirmed. An unchanged structural decision may be marked reused and preselected by a consumer adapter, with lineage and a consumer-owned option to change it.

## Independent state, reuse, eligibility, and lifecycle

These axes are independent and must not collapse into one `status`.

| Axis | Values / responsibility | Meaning |
| --- | --- | --- |
| Resolution status | `observed`, `proposed`, `confirmed`, `conflict`, `unknown` | State of a fact and its evidence; consumer may apply local UI labels |
| Reuse disposition | `fresh`, `exact_reused`, `reuse_candidate`, `not_reusable` | Per-facet dependency comparison, never consumer readiness |
| Consumer eligibility | Consumer-owned `eligible`, `ineligible`, `not_evaluated`, with reasons | Whether named consumer rules and overlays permit its action |
| Artifact lifecycle | Existing AAR `active` or `superseded` | Repository state of the immutable DSC payload, not truth/reuse state of each facet |

The AAR artifact payload is `dataset_structure_context`, at snapshot scope, with table and optional relationship facets. Identity includes asset, immutable snapshot, source table/profile artifact identities and hashes, dictionary/Data Sourcing metadata fingerprints, DSC schema, and resolver/inference version. `owner_id` is a provenance and exact-reuse namespace for consumer-local facts; it is not an access-control identity.

```text
identity: asset, immutable snapshot, created_from, payload_schema_version
table_facets: physical, structure, temporal facts by qualified table
relationship_facets: evidence-backed declarations only
evidence: authority, references, hashes, summary, confidence, resolution status
source_context: reviewed metadata and dictionary context
profile_context: table/column profile references and verified summaries
decisions: user/consumer structural-decision lineage and corrections
safe_summary: display-safe facts, uncertainty, conflicts
artifact_lifecycle: append-only AAR lifecycle references
```

Version axes are independent: payload schema, facet registry, resolver algorithm, source metadata, profile evidence, and every consumer adapter/overlay. Consumer methodology is not DSC identity. Each facet has its own dependency fingerprint over only its snapshot/table, qualified columns or relationship endpoints, evidence slices, source decisions, and algorithm version. It is unaffected by an unrelated profile or dictionary edit. When a dependency changes, the prior facet remains readable with reason and lineage but its reuse disposition becomes `not_reusable`; consumers separately re-evaluate their overlays and eligibility.

For a temporal candidate, exact reuse additionally requires the same reviewed-role provenance and hash, exact profile hash, qualified column, schema/context version, producer version, and evidence policy. A change replaces and supersedes only that atomic candidate; it does not invalidate unrelated schema, entity, grain, or time candidates.

Publication uses immutable canonical JSON, SHA-256 integrity verification, searchable bounded summaries, directed lineage, append-only lifecycle events, exact reuse, and same-identity/different-payload conflict. Mutable Data Sourcing inventory remains a working surface, not a published DSC substitute.

## Applicability matrix

This is an evidence-based placement matrix, not a roadmap promise. The listed diagnostics/domains are documented in the current diagnostic catalog and domain-ownership record. It describes only a possible adapter boundary, never a new workflow or readiness rule. **No special facet** means nothing beyond snapshot/table identity and verified physical evidence is asserted.

| Consumer/domain | Existing documented focus | DSC applicability in Step 1-a |
| --- | --- | --- |
| D02 | Single-feature target separation | May read snapshot/table physical facts; target and analytical settings remain consumer/Data Sourcing contracts. No D02 gate is defined. |
| D04 | Cross-field business rule | May request physical/structural facts and evidence-backed relationship facets for its selected tables; rules, KB declarations, predicates, and readiness remain D04-owned. |
| D06 | Row-completeness reconciliation | First intended adapter/adopter: may request table structure/temporal facets while preserving its own facility, period, segment, reporting-grain decisions. It does not define DSC. |
| D08 | Value-semantics classification | Optional advisory table physical/structural evidence only; roles, field scope, tags, KB/terminology, frozen identity, and confirmation remain D08-owned. |
| D11 | Directional monotonic consistency | May read physical structure; role adjudication, KB, direction engine, and readiness remain D11-owned. |
| D12 | Label-consistency rule | No special facet; target/label semantics and every gate stay consumer-owned. |
| D14 | Population Stability Index (PSI) | May request physical facts for each selected snapshot; populations, baseline/current choice, bins, thresholds, and review remain D14-owned. |
| D17 | Resolution/maturity rate by vintage | May request a time facet only if a later adapter is justified; vintage/outcome meaning and readiness remain consumer-owned. |
| D20 | AI-proposal workflow | May consume safe evidence summaries only; AI proposals, SME interaction, and execution/approval stay D20-owned. |
| Data Sourcing | Acquisition, review, profile, metadata, readiness | Producer/reviewer of source evidence/declarations; not DSC consumer-readiness authority. |
| AAR | Governed artifact persistence, integrity, lineage | Stores/serves DSC payloads and lifecycle; does not resolve or interpret context. |
| RCA | Investigation and remediation evidence | Optional consumer of pinned safe context references; does not recalculate or certify DSC. |
| Reports | Consumer-specific presentation | May project safe context references; filters, wording, thresholds, and claims remain report-owned. |

## Security and privacy

DSC v1 runs within the trusted application/workspace boundary and uses source sensitivity classification. Payloads contain identifiers, hashes, qualified column names, bounded metadata, and safe evidence summaries only. They exclude raw rows, cell values, credentials, unrestricted dictionary text, model prompts/responses, and inferred relationship claims. The persistence foundation derives DSC identity and lineage, validates active same-snapshot sources and pins, and rejects blob writes. Detail reads verify hashes; missing or tampered payloads fail closed as unavailable evidence. Tenant isolation, RBAC, and authorization-aware projections are deferred product capabilities; `owner_id` does not substitute for them.

Time-column assertions also exclude copied minima/maxima, period labels, samples, `top_k`, and unrestricted format examples. Exact bounds remain in their sensitivity-classified source profile until DSC has a typed safe representation.

## Representative fixtures and acceptance criteria

| Fixture | Expected result |
| --- | --- |
| Ready entity-period table with same-snapshot confirmed structural bindings | Exact-reuse dispositions for unchanged facets; consumer decides use |
| Same snapshot with no segment column | Applicable shared table facts; no segment fact invented |
| Dictionary `period_column` disagrees with an older proposed candidate | Reviewed Data Sourcing proposal or a conflict with confirmed peer evidence |
| Changed snapshot/profile hash | Only affected facets become `not_reusable`; re-observe as appropriate |
| Ambiguous two-date event table | Qualified candidates and `conflict`/`unknown`; no silent selection |
| Repeated entity IDs with unique entity-period pairs | Panel suggestion with repetition/uniqueness evidence; confirmation required |
| Duplicate entity-period pairs | No confirmed grain; bounded reviewed composite key or `Needs a choice` |
| Composite entity key | Retain reviewed qualified combination; validate joint uniqueness |
| One row per entity | Cross-sectional suggestion, not panel |
| Irregular or mixed-frequency entities | Observed cadence irregular/mixed; no expected-frequency inference |
| Several reviewed Date/Period columns | One proposed time-column assertion per candidate; no automatic primary axis |
| Reviewed Period with no truthful v1 type | Phase C2 cannot emit it until `period` is supported; never coerce it to date/instant/interval |
| Missing temporal parse evidence | Preserve candidate provenance without inventing a zero parse-failure observation |
| Two similarly named keys across tables without declaration | No relationship facet; no join/business relationship inferred |
| Reviewed relationship conflicts with endpoint evidence | Conflict; no exact reuse |
| Missing/tampered profile payload | Fail closed; cannot satisfy a consumer-required facet |
| Sensitive identifiers/descriptions | Safe summaries and external projections contain no raw values/secrets |

Acceptance requires that DSC:

- resolves exact valid same-snapshot structural decisions before new inference;
- uses reviewed Data Sourcing metadata and verified AAR profiles with authority/provenance;
- expresses snapshot, table, and evidence-backed relationship facts without inferred joins;
- keeps shared facts separate from consumer overlays/readiness;
- separates resolution, reuse, eligibility, and artifact lifecycle;
- fingerprints dependencies per facet with independent version axes;
- exposes generic capability negotiation and a versioned facet registry;
- surfaces conflicts, uncertainty, freeze inputs, and invalidation;
- publishes/reuses immutable AAR payloads with hashes and lineage; and
- leaves consumer application contracts and code unchanged.

## Staged adoption and compatibility

Platform work begins with registry/payload and additive AAR registration; the profile-backed physical-schema resolver is now active. Remaining structural producers can follow before consumers introduce **shadow adapters** that read DSC alongside existing structural inputs and compare results without changing readiness, manifests, identity, or UI. D06 is the first intended adopter because its documented reconciliation scope uses structural inputs, but it is not the design center and has no privileged platform gate.

Only after shadow evidence supports consumer-specific approval may a consumer adopt the adapter, pin DSC references in new manifests, and define its own readiness/UI change. Backfill is allowed only when source artifacts truthfully reconstruct provenance. Presence in the matrix implies no implementation, workflow, or migration.

### Initial D08 migration compatibility

This contract does not change D08 v0.2, its code, frozen identity, selected-field roles, `field_scope_decisions`, tags, KB/terminology versions, AI boundary, AAR artifact set, report/privacy contract, or confirmation rules. A future D08 shadow adapter may read DSC only as advisory structural evidence with explicit versioned lineage. No existing D08 artifact may be rewritten or treated as DSC evidence.
