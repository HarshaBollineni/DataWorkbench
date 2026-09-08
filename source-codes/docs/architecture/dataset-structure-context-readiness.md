# Dataset Structure Context — Step 1-b readiness and reuse assessment

**Status:** observed readiness record, subsequently resolved by the active v1 foundation and refined through Phase C1. This is the Step 1-b implementation-readiness and reuse assessment for the Step 1-a [DSC contract](dataset-structure-context.md). **Outcome:** the reusable platform seams now support DSC validation, assembly, trusted-workspace JSON persistence, physical schema, reviewed entity candidates, and singleton/composite row-grain evidence. Time-column Context is documented but not implemented; remaining producers and consumer adoption remain later work.

**Refined by:** Step 1-c [schema and protocol](dataset-structure-context-schema-protocol.md) resolves the ten pre-schema blockers; this document remains the observed readiness record.

## Evidence-backed placement matrix

“Observed” describes current code. “Required gap” records a proposed implementation need; it does not claim current behaviour.

| DSC facet/capability | Observed repository component | Owner | Classification | Required gap |
| --- | --- | --- | --- | --- |
| Snapshot identity and load | [`backend/analysis_runtime/snapshots.py:SnapshotLoader.reference/load_table`](../../backend/analysis_runtime/snapshots.py#L18) returns ready, active snapshot identity and table inventory and reads named tables. | `analysis_runtime` | reuse as-is | Add DSC request/context envelope and selected-table validation above this boundary. |
| Physical schema, profiles, fingerprint, reviewed metadata | [`backend/domains/aar/data_sourcing.py:persist_snapshot_profile_artifacts`](../../backend/domains/aar/data_sourcing.py#L114) publishes active table/column profiles with dictionary/profile fingerprints; [`_safe_profile`](../../backend/domains/aar/data_sourcing.py#L40) carries reviewed special-value evidence. | Data Sourcing + AAR | reuse with adapter | Define a DSC physical facet that references, rather than copies, qualified profile artifacts and reviewed metadata slices. |
| Entity/time evidence | Data Sourcing now publishes exact profiles with explicit `role_reviewed` provenance; the DSC producer publishes reviewed Identifier candidates. D06 still ranks local facility/period/segment choices independently. | Data Sourcing + DSC; D06 owns consumer meaning | extend | Implement the Phase C1 reviewed Date/Period candidate contract without promoting D06 reporting semantics. |
| Row grain and dataset form | DSC now publishes singleton and bounded reviewed Identifier × Date/Period grain evidence. D06's `RunScope` remains local and dataset form remains unimplemented. | DSC owns shared evidence; D06 owns local scope | partial | Add dataset form only after its evidence/decision contract is separately approved; do not derive it from a D06 scope. |
| Cadence | [`backend/domains/test_lab/diagnostics/t2_d06_row_completeness/periods.py`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/periods.py) is an execution-specific period interpretation seam. | D06 owns period interpretation; no shared observer yet | new required | Define observation, multiple-axis temporal representation, cadence evidence and ambiguity handling independently of reporting meaning. |
| Relationships | [`backend/analysis_runtime/snapshots.py:SnapshotLoader`](../../backend/analysis_runtime/snapshots.py#L18) exposes table inventory but no relationship contract. | No platform producer of record yet | new required | Establish the Data Sourcing review and DSC observation boundary, then add declaration-backed relationship facets with ordered key tuples, direction, cardinality, null policy and coverage. |
| Provenance and conflict | [`backend/domains/aar/repository.py:AnalysisArtifactRepository.save`](../../backend/domains/aar/repository.py#L165) hashes canonical payloads and rejects same-identity/different-payload writes. | AAR + DSC resolver | extend | Add typed, per-assertion evidence/conflict records and dependency fingerprints; existing artifact collision is not a fact-resolution model. |
| AAR persistence, type registry, lifecycle and lineage | [`backend/domains/aar/types.py:register_artifact_type`](../../backend/domains/aar/types.py#L336), [`repository.py:lineage/supersede`](../../backend/domains/aar/repository.py#L465) provide registration, immutable payloads, lineage and active/superseded lifecycle. | AAR | reuse with adapter | Register `dataset_structure_context`; align facet persistence/context envelope/as-of rules with AAR artifact identity without putting consumer methodology in DSC identity. |
| Runtime capabilities and manifests | [`backend/analysis_runtime/capabilities.py:SupportingAnalysisCapability`](../../backend/analysis_runtime/capabilities.py#L10) and [`register_capability`](../../backend/analysis_runtime/capabilities.py#L23) are a generic registration seam. | `analysis_runtime` | reuse with adapter | Define DSC version negotiation, required/advisory fulfillment, unavailable/unsupported/reason responses and canonicalization. |
| D06 adapter, UI and results | [`backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py:build_manifest`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py#L185), [`ui/.../RowCompletenessScopeGate.jsx`](../../ui/src/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate.jsx#L138), and [`runner.py`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/runner.py#L86) own selection, confirmation and reconciliation. | D06 | keep separate | Later shadow adapter may compare DSC structural evidence; preserve D06 manifest, reporting meaning, controls and result/finding workflow. |
| D08 adapter, UI and semantic pieces | [`backend/domains/test_lab/diagnostics/t2_d08_value_semantics/manifest.py:field_scope_decisions`](../../backend/domains/test_lab/diagnostics/t2_d08_value_semantics/manifest.py#L399), [`ValueSemanticsScopeGate.jsx`](../../ui/src/features/test-lab/diagnostics/t2-d08-value-semantics/ValueSemanticsScopeGate.jsx#L53), and AAR D08 types in [`types.py`](../../backend/domains/aar/types.py#L566). | D08 | keep separate | Remain advisory only: no rewriting D08 identities, field choices, roles, KB/terminology, confirmation, tags or reports. |
| Shared finding controls | [`backend/domains/test_lab/shared/run_state.py`](../../backend/domains/test_lab/shared/run_state.py#L1) owns diagnostic run state and append-only decisions; D06 persists findings in [`runner.py:_persist`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/runner.py#L115). | Test Lab | keep separate | DSC may expose safe evidence references only; it creates no findings, dispositions, issues, readiness gates or workflow transitions. |

## D06 minimum-sufficiency mapping

No canonical numbered Q1–Q4 was found in the inspected D06 implementation or documents. The following is a **functional mapping**, not a renamed or authoritative D06 questionnaire.

| Functional question | Inferable evidence | Existing UI | Missing work | Confirmation owner |
| --- | --- | --- | --- | --- |
| Q1 — Which table/snapshot? | Active/ready snapshot and table list from [`SnapshotLoader.reference`](../../backend/analysis_runtime/snapshots.py#L18); profile-backed table options in [`D06 build_manifest`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py#L185). | Table selector in [`RowCompletenessScopeGate`](../../ui/src/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate.jsx#L164). | DSC table facet and negotiated reference; retain D06’s table choice. | D06 user/adapter. |
| Q2 — Which entity/facility? | Inventory role/dictionary signals are ranked by [`_role_score`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py#L48). | Required Facility identifier control in [`RowCompletenessScopeGate`](../../ui/src/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate.jsx#L15). | Generic entity assertion plus portability/promotion decision; no automatic semantic confirmation. | D06 user; Data Sourcing reviews source metadata. |
| Q3 — Which time, and what does it mean for this consumer? | Profiled date/period metadata and `period_column` are candidate inputs; `reporting_grain` is a D06 default in [`manifest.py`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py#L28). | Required Reporting period control in [`RowCompletenessScopeGate`](../../ui/src/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate.jsx#L15). | DSC supplies structural temporal evidence only; D06 retains reporting-period meaning and reporting grain. | D06 user/adapter. |
| Q4 — What row key/grain, with optional segment? | D06 freezes facility + period and optional segment in [`_frozen_scope`](../../backend/domains/test_lab/diagnostics/t2_d06_row_completeness/manifest.py#L438). | Optional Segment control in [`RowCompletenessScopeGate`](../../ui/src/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessScopeGate.jsx#L15). | DSC needs generic keyed-grain evidence; D06 must choose whether its optional segment participates in its local rule scope. | D06 user/adapter. |

### Consumer-overlay boundary

The following remain outside DSC: `reporting_grain`, target, thresholds, roles/KB terminology, selected segments/fields, predicates, methodology, run scope, and presentation. A shared entity or time binding is structural evidence, not permission to give it a D06 reporting or a D08 semantic meaning.

## Pre-schema decision register

These are **proposed decisions to accept or amend before implementation**, ranked by blocker severity.

| Rank | Must resolve | Why it blocks schema/protocol |
| --- | --- | --- |
| 1 | Model atomic, independently keyed assertions rather than one state for bundled `table.structure`. | Evidence, reuse, conflict and invalidation cannot be precise if entity, time, grain and form share a single state. |
| 2 | Define decision portability: `source_confirmed_structural`, `consumer_local`, and explicit promotion. Never share D06/D08 semantic confirmation automatically. | Prevents a consumer-local choice from becoming platform truth. |
| 3 | Define typed conflict semantics. | “Conflict” needs incompatible authorities/evidence, affected assertion, resolution status and safe reason—not one untyped label. |
| 4 | Define relationship ordered key tuples, direction, cardinality, null policy, coverage and producer of record. | Avoids unsafe inferred joins and ambiguous endpoint bindings. |
| 5 | Support multiple temporal axes; date/instant/interval; precision/timezone/calendar; no-entity/event handling; and `not_applicable` distinct from `unknown`. | A single period field cannot faithfully represent events, intervals, or non-panel tables. |
| 6 | Define negotiation validation, fulfillment, reason codes, canonicalization, partial failure and version behaviour. In the Step 1-a example, the relationship names `payments` although the selected `tables` list contains only `applications`; canonical matching between request selectors (`table`/`between`) and response `key` is also undefined. | A client cannot safely validate scope, fingerprint requests or match responses until addressing is fixed. |
| 7 | Define facet-level persistence plus context-envelope/as-of consistency aligned with artifact-level AAR identity. | AAR currently persists whole artifacts; DSC requires independently valid, invalidatable facts. |
| 8 | Define evidence basis, denominators and special-value handling. | Profile counts and missingness treatment must be comparable without leaking values or silently changing the denominator. |
| 9 | Define sensitivity, redaction and a future authorization boundary. | Existing AAR types carry sensitivity; v1 provides bounded safe payloads and sensitivity maxima. Tenant/RBAC projection is deferred because the product has no authorization boundary yet. |
| 10 | Decide cross-snapshot compatibility explicitly: DSC extension or consumer-owned. | Do not accidentally turn same-snapshot exact reuse into cross-snapshot semantic compatibility. |

## Edge cases that the schema must represent

| Case | Required handling |
| --- | --- |
| Multi-table snapshot | Independent table facets; relationship only with declared, qualified evidence. |
| No stable entity | Preserve `not_applicable` or `unknown`; do not invent an entity key. |
| Event or interval data | Permit several named temporal axes and interval/instant types; do not coerce to reporting periods. |
| Mixed cadence | Record observed evidence by relevant binding; expose mixed/irregular rather than a guessed frequency. |
| Typed conflicts | Retain competing evidence and reason; no silent winner across authority types. |
| Snapshot metadata drift | Keep prior artifacts readable with lineage; invalidate only affected facets after fingerprint comparison. |
| Privacy | Store bounded safe evidence and sensitivity classifications; never expose raw rows/identifiers through DSC. Tenant/RBAC redaction/projection remains deferred. |

## Implementation disposition and next gate

**Observation at assessment time:** no DSC resolver, payload type, facet registry, protocol endpoint, or consumer adapter existed. The reusable seams above reduced implementation risk; the active physical-schema slice now supplies the first producer/resolver implementation.

**Current disposition:** the persistence foundation and atomic producers for physical schema, reviewed entity candidates, and singleton/composite row grain are active. Composite evaluation is bounded and aggregate-only; explicit `role_reviewed` provenance is distinct from automatic mapping confidence. Phase C1 now defines Time-column Context, but Phase C2 is gated on adding a truthful single-column `period` type and then implementing the profile-backed temporal producer. Cadence, dataset form, relationships, and shadow adoption remain later; D06 remains a later shadow adopter and D08 remains unchanged and advisory.

## Sources and evidence

- **Observed at assessment time:** [`dataset-structure-context.md`](dataset-structure-context.md) was the Step 1-a design contract. It is now refined by the active Step 1-c persistence foundation; consumer adoption remains design intent.
- **Observed:** snapshot access is centralized in [`backend/analysis_runtime/snapshots.py`](../../backend/analysis_runtime/snapshots.py); capability registration is in [`backend/analysis_runtime/capabilities.py`](../../backend/analysis_runtime/capabilities.py); immutable artifact persistence/type registration is in [`backend/domains/aar/repository.py`](../../backend/domains/aar/repository.py) and [`types.py`](../../backend/domains/aar/types.py).
- **Observed:** D06 and D08 own their manifests, UI, semantic confirmation, results and artifacts at the linked paths in the matrix.
- **Proposed:** all entries in the decision register, DSC adapters/facets/protocol, and the stated next gate are implementation decisions, not claims of existing behaviour.
