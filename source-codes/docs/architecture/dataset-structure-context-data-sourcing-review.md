# Dataset Structure Context — integrated Data Sourcing review

**Status:** normative phased implementation contract; no runtime, database, endpoint, or UI change is made by this document.

**Supersedes:** the UI/API/readiness/expected-frequency exclusions in the prior DSC contracts. Those exclusions applied to their completed bounded producer increments. They do not prohibit this separately versioned Data Sourcing adoption. Their evidence, privacy, atomic-candidate, lifecycle, and consumer-ownership rules remain in force.

## Outcome and ownership

Dataset Structure Context (DSC) becomes the final, resumable review stage of Data Sourcing after a governed snapshot has been made active, ready, and has published its hash-verified profile artifacts. It materializes structural evidence asynchronously, later presents ranked alternatives, and later records explicit Data-Sourcing-owned structural selections. It does not replace column review, alter source data, or run a diagnostic.

```text
upload / dictionary / column review
  -> active ready immutable snapshot + profile artifacts
  -> Slice 1: bounded existing-DSC-v1 materialization (async)
  -> Slice 2: Dataset Structure read/recommendation draft
  -> Slice 3: explicit confirmation
  -> Test Lab; initially unchanged legacy/shadow consumers
  -> later D06 assist reads confirmed DSC selections as defaults
```

| Owner | Responsibility | Never does |
| --- | --- | --- |
| Data Sourcing review | Drafts, recommendations, user confirmation, expected cadence, defaults and limited-state explanation | Reclassify source columns, infer diagnostic semantics, silently select a candidate |
| DSC producer/resolver | Existing atomic observations/candidates, evidence/reuse/dependency checks | Create a business entity from a row number, declare expected cadence, make a diagnostic runnable |
| AAR | Immutable assertions/context, hashes, lineage and supersession | Store mutable review drafts or resolve user workflow state |
| Diagnostics | Interpret confirmed DSC selections for their own methodology and request final execution confirmation | Mutate DSC or treat expected cadence as a reporting-grain decision |

`observed_cadence` is immutable measurement. `expected_cadence` is a separate, user-confirmed intended configuration that may be proposed only when the matching observed cadence is `regular`. A diagnostic reporting grain remains a consumer-owned semantic decision even when it is prefilled from a confirmed expected cadence.

## Delivery gates

The slices are strictly ordered. A later slice may not be started because a document describes its shape; the preceding acceptance gate must pass first.

| Slice | Scope and gate | Explicitly excluded |
| --- | --- | --- |
| **1 — Foundation** | After successful AAR profile publication, enqueue a durable existing-DSC-v1 job; add same-tenant authentication, polling, job recovery and corrected cleanup ownership. Gate: durable job/recovery and unchanged ready ingest/diagnostics are proven. | UI, drafts, decisions, DSC v2, expected cadence, backfill, metadata correction, technical IDs, D06 assist. |
| **2 — Review draft** | Read existing v1 assertions on `/data-sourcing?structure=`, rank/recommend them, persist resumable mutable drafts and show limited states. Gate: resume/recommendation/privacy tests pass. | AAR decision assertions/defaults/expected cadence, v2, diagnostic adoption. |
| **3 — Confirmed structure** | Add DSC v2 schemas/predicates and atomic source-confirmed decision batches. Gate: version negotiation and stale/batch atomicity pass. | Metadata role-edit workflow, technical IDs, consumer adoption. |
| **4 — Backfill** | Idempotently schedule Slice-1 work for eligible active-ready snapshots, then initialize Slice-2 drafts after its gate. | Automatic confirmation, AAR wipe, historical eager scans. |
| **Later** | Metadata correction round trip, pre-finalization technical row IDs, then separately approved D06 assist. | Any automatic diagnostic execution or reporting-grain authority. |

## Slice 3 evidence, candidates, and selections

This section is not a Slice-1 or Slice-2 schema. Slice 2 can show a recommendation and save it only as mutable draft data; it cannot publish any of the following predicates or treat a draft as a decision.

The UI retains every current valid `entity_binding`, `temporal_binding`, row-grain, and cadence candidate; it never destroys an alternative because a default is chosen. It may rank candidates and preselect a **draft recommendation**, but no recommendation is authoritative until the user confirms a batch.

The review's selection assertions are source-confirmed structural facts in DSC context version 2:

| Predicate | Multiplicity / instance key | Meaning |
| --- | --- | --- |
| `table.structure/default_entity_binding` | `single` / `default` | One selected, existing entity-binding candidate for a table |
| `table.temporal/default_temporal_binding` | `single` / `default` | One selected, existing temporal-binding candidate for a table |
| `table.structure/row_grain` | existing `single` / `primary` | User-selected evidence-backed key combination; confirmation promotes/replaces the existing candidate, it does not search arbitrary keys |
| `table.temporal/expected_cadence` | `single` / `<axis-id>:<grouping-digest>` | User-confirmed expected interval for the exact selected temporal axis and entity grouping |

The default predicates contain the selected candidate locator and its assertion pin/hash/dependency fingerprint, rather than duplicate a candidate value. A selected candidate must remain active, same-snapshot, and typed-identical at confirmation. A confirmed selection does not suppress the other proposal assertions.

Slice 3 records a row-grain choice as a pinned, confirmed selection in the
Data-Sourcing decision batch and retained draft, and validates it against the
selected entity/time bindings. It does **not** publish a separate DSC-v2
row-grain authority assertion: the negotiated v2 registry currently has only
the two default-binding predicates and `expected_cadence`. A separate
row-grain authority predicate is deferred to a later versioned registry change;
no implementation may invent one under v2 meanwhile.

### DSC v2 authority payload contract

Each authority selection is a separate immutable DSC v2 assertion. The later decision service commits its batch atomically, but no batch envelope is stored inside an assertion. For a `single` predicate/instance, the next active source-confirmed selection supersedes the prior active authority assertion; candidate alternatives remain active and queryable.

Default bindings never copy a candidate's raw value. Their one effective claim uses exactly one of these closed values:

```json
{"kind":"default_entity_binding","candidate_locator":{"predicate":"table.structure/entity_binding","instance_key":"column:customer_id"},"candidate_pin":{"artifact_id":"art_...","assertion_id":"dsca_...","payload_hash":"<sha256>","dependency_fingerprint":"<sha256>"}}
```

`default_temporal_binding` is identical except its `kind` and locator predicate are `default_temporal_binding` and `table.temporal/temporal_binding`. `candidate_locator` contains only predicate and instance key; the exact pin contains artifact ID, assertion ID, payload hash, and dependency fingerprint. There are no source values, columns, samples, or profile details in a default binding value.

An expected cadence selected value is closed as follows:

```json
{"kind":"expected_cadence","unit":"month","step":1,"axis":{"axis_id":"column:month","locator":{"predicate":"table.temporal/temporal_binding","instance_key":"column:month"},"pin":{"artifact_id":"art_...","assertion_id":"dsca_...","payload_hash":"<sha256>","dependency_fingerprint":"<sha256>"}},"grouping":null}
```

`grouping: null` is the explicit no-grouping choice. Otherwise it is the same closed `{locator,pin}` form with an `entity_binding` locator. The assertion instance key is exactly `<axis-id>:<full lowercase sha256(canonical JSON of grouping locator or null)>`; it does not hash a pin or source value. The v2 runtime rejects a mismatch.

Immutable authority actions are only `confirm`, `clear`, and `mark_not_applicable`. `replace` is a mutable-draft edit term only and is never written as a v2 AAR authority action. `clear` and `mark_not_applicable` use `{"kind":"...","selection":"none"}` with, respectively, `unknown / DSC_R_DECISION_CLEARED` and `not_applicable / DSC_R_NOT_APPLICABLE_CONFIRMED` resolution. A `confirm` has one selected value and `confirmed` resolution. All v2 authority claims are `source_confirmed_structural`; a later confirmation is the superseding active value.

`expected_cadence` has value `{ "unit": "day|week|month|quarter|year", "step": positive_integer }`, exact axis/grouping references, and a decision action `confirm|clear|mark_not_applicable`. It is proposed only from the exact matching regular `observed_cadence` class. Examples are daily (`day`, 1), weekly (`week`, 1), monthly (`month`, 1), quarterly (`month`, 3 or `quarter`, 1), semiannual (`month`, 6 or `quarter`, 2), and annual (`month`, 12, `quarter`, 4, or `year`, 1). The user may confirm that proposal, explicitly choose another supported expected cadence, or say none applies. For `mixed`, `irregular`, or `unknown`, no expected cadence is proposed; an explicit user declaration is still allowed and must show the observed state.

No new business roles are invented. A repeated identifier is normally valid in longitudinal data; uniqueness warnings are evaluated for the selected row grain (for example entity + period), not by requiring an entity column itself to be unique.

## Slice 2/3 state machine and draft guarantee

`dq_items.ingest_status` remains the governed snapshot readiness state. Slice 1 has only a materialization-job state. Slice 2 adds the independent review state below; a successful snapshot is never rolled back merely because DSC is unavailable.

```text
not_started -> materializing -> review_required -> confirmed
                    |                  |             |
                    v                  v             v
                  failed            limited <-> refreshing
                                      |       |
                                      +-> needs_reconfirmation

metadata correction: review_required|confirmed|limited -> metadata_review -> materializing
explicit restart only: any non-materializing state -> review_required (new draft revision)
```

`limited` means the Slice-2 review completed but has one or more structural absences. `failed` means the Slice-1 job could not safely obtain evidence. Neither changes snapshot readiness. `needs_reconfirmation` exists only in Slice 3: evidence changed after a user draft was saved, no immutable confirmation is written, and the draft remains editable. A user can leave and return to every nonterminal state. Only an explicit **Restart structure review** discards the current draft; prior immutable decisions (which first exist in Slice 3) remain historical AAR assertions.

The state response identifies effects by named diagnostic capability, never by a generic warning that a selection “affects eligibility.” Initial product copy is exactly:

| Situation | Required UI copy |
| --- | --- |
| Supported selection | “These selections are supported by the dataset evidence. They will be used to prefill applicable diagnostics, and you can review them before execution.” |
| Usable but confirmation still needed | “These selections can be used as diagnostic starting points. Some diagnostics will ask you to confirm their interpretation before execution.” |
| Limited evidence | “Some selected details have limited supporting evidence. They can be carried forward as suggestions, but additional confirmation may be required.” |
| Insufficient structure | “The selected details are insufficient for certain diagnostics: {diagnostic_names}. Resolve the following requirements to enable them: {requirements}. Other diagnostics are unaffected.” |
| Different from observation | “Your selection differs from the observed dataset structure. It will be preserved as your intended configuration, and applicable diagnostics will ask you to confirm the difference before execution.” |
| Missing time | “No confirmed Date or Period field is available. Temporal diagnostics cannot be prepared, but non-temporal diagnostics remain available.” |
| Missing entity | “No confirmed entity identifier is available. Entity-based and cadence diagnostics cannot be prepared. You may review suggested columns or confirm that no identifier exists.” |

## Slice 2 Data Sourcing UI contract

Add **Dataset Structure** after the current column/dictionary confirmation and governed-profile publication. Its route state is `/data-sourcing?structure=<item_id>`; it is not a new top-level page and AAR remains an advanced audit surface. Slice 2 is read/recommendation/draft only, and has no confirm action.

For Slice 2, the four panels are entity candidate, Date/Period candidate, row grain, and observed cadence. Slice 3 adds expected cadence and the confirmation action. The later metadata-correction and technical-row-identifier paragraphs in this section are explicitly deferred as stated in the delivery table; they are not Slice 2/3 acceptance scope.

For every table, show materialization state, retry action, evidence freshness, and four panels: entity candidate, Date/Period candidate, row grain, and observed/expected cadence. Controls only list candidates that meet their typed predicate prerequisites. The recommended option is visually labelled with concise aggregate evidence; users can select another listed option or choose “No applicable selection.” A confirm control is disabled until all selections are internally consistent.

Warnings are inline and decision-specific: unparseable/incompatible period, null/special/sparse identifier, selected entity-time combination not unique, insufficient repeated observations, expected cadence differing from observation, and diagnostic-specific missing requirements. They must not expose raw values, profile samples, identifiers, source paths, hashes, or an unbounded AAR payload.

If source metadata is incompatible, show a warning, retain the draft, and offer **Return to column definitions**. The deep link includes only item/table/column and return token, for example `/data-sourcing?item=<id>&table=<name>&column=<name>&return=dataset-structure`. Column review remains the sole authority to correct `Date`, `Period`, or `Identifier` role metadata. On its confirmed save, invalidate only dependent DSC candidates, materialize them again, and return to the preserved draft with changed evidence highlighted.

If no credible identifier candidate exists, show suggestions first. The opt-in **Create technical row identifier** is available only before snapshot finalization/replacement, requires explicit acknowledgement, writes a deterministic technical column during the Data Sourcing transformation/review path with lineage, and creates a new immutable snapshot. It is labelled `technical_row_id`, never `Identifier` or business entity, and cannot satisfy entity continuity, cadence, relationships, joins, cross-snapshot matching, or entity-based diagnostics. It is not available to DSC after the snapshot is ready.

If no Date/Period candidate exists, finish the review as `limited`; no artificial time column is offered. Non-temporal diagnostics remain available. The screen must explain which diagnostic capability requirements are missing without promising a result for a future consumer.

## HTTP API contract

### Slice 1 foundation routes

All Slice-1 routes are under `/api/v2`, use authenticated users, and authorize only by exact equality of authenticated `tenant_id` to `dq_items.sourcing_tenant_id`. There is no client-supplied tenant/actor and no fallback tenant. A missing item, inactive/not-ready item, missing `sourcing_tenant_id`, and cross-tenant item all return the same `404`. Fine-grained roles are deferred; authenticated same-tenant access is the Foundation permission boundary.

Profile publication succeeds first. Its publication transaction writes a monotonic `profile_publication_generation` and exact `profile_publication_fingerprint` marker only after every required profile artifact is active and hash-verified. Only the successful post-commit hook enqueues the `post_ready` job bound to that exact marker; source finalization never invokes a producer or waits for DSC. If enqueueing fails, the ready ingest and published profiles remain successful; startup/status reconciliation creates the missing job from the marker. A retry route can enqueue a new eligible attempt but cannot cause source finalization to repeat:

| Route | Slice-1 contract |
| --- | --- |
| `POST /items/{item_id}/dataset-structure/materializations` | Idempotently requests a retry for a failed/expired job, or returns the current equivalent live/succeeded job. Body is `{ "reason": "retry" }`. `202` means queued; `200` means no new work. It never accepts tables, metadata changes, backfill, or a client tenant. |
| `GET /items/{item_id}/dataset-structure/materializations/{job_id}` | Returns closed status `queued|running|succeeded|retry_wait|failed|revoked`, aggregate progress, `retry_after_ms`, and closed error code only. It never returns assertion IDs/hashes, profile fingerprints, raw values, source paths, worker identity, or row-level data. The UI polls; there is no SSE/EventSource route. |

Startup and every materialization-status GET run bounded reconciliation: an eligible ready snapshot with successful profile publication but no job receives one idempotent `post_ready` job bound to the current marker; expired/revoked running leases become `retry_wait` or terminal `failed`; a queued/retryable eligible job is leased once; and a success whose bound generation/fingerprint is no longer exact is never treated as current. This reconciliation is idempotent and does not scan source data on the HTTP request thread.

### Slice 2/3 target routes

The following routes are not implemented in Slice 1. The table records the target contract so later work does not overload the Foundation API.

For Slice 2/3, routes remain item-scoped and retain the Slice-1 same-tenant `dq_items.sourcing_tenant_id` check. All write requests require an `Idempotency-Key`; a successful same method/path/body replay returns the original status/body and a mismatched reuse returns `409 idempotency_key_reused` with no write. Fine-grained role permissions are a later hardening increment, not a prerequisite invented for Slice 1.

| Route | Contract |
| --- | --- |
| `POST /items/{item_id}/dataset-structure/materializations` | Slice 1 has the closed retry-only form above. Slice 4 may add server-owned `backfill`; metadata change remains later. It never accepts a caller-selected table set and never waits for scans. |
| `GET /items/{item_id}/dataset-structure/review` | Returns current materialization/review state, bounded candidates, draft, immutable decision pins, recommendations, warnings, and diagnostic assistance summary. |
| `PATCH /items/{item_id}/dataset-structure/draft` | Autosaves a mutable draft revision. Body and response are below. `200` always preserves accepted draft input; stale evidence yields `needs_reconfirmation`, never a bare `409`. |
| `POST /items/{item_id}/dataset-structure/decisions` | Atomically validates and persists an explicit decision batch, or preserves it as a reconfirmation-required draft. `200` for `confirmed|needs_reconfirmation`; `422` only for invalid/inconsistent user input; `409` only idempotency-key misuse. |
| `POST /items/{item_id}/dataset-structure/restart` | Requires `{ "draft_revision": n, "confirm_restart": true }`; creates a new draft revision after retaining previous draft history. |
| `GET /items/{item_id}/dataset-structure/materializations/{job_id}` | Bounded progress/status; polling interval is server-provided. |

`GET review` response (omitted arrays are empty, never `null`):

```json
{
  "review_contract_version": "1",
  "snapshot": {"asset_id": "asset_…", "snapshot_id": "item_…"},
  "structure_review_state": "review_required",
  "materialization": {"job_id": "dscj_…", "status": "succeeded", "generation": 3,
    "evidence_fingerprint": "sha256", "retry_after_ms": null},
  "draft": {"draft_id": "dscd_…", "revision": 7, "evidence_fingerprint": "sha256",
    "selections": {"tables": []}},
  "tables": [{"table": "applications", "state": "review_required",
    "candidates": {"entities": [], "temporals": [], "row_grains": [], "observed_cadences": []},
    "recommendations": {"default_entity": null, "default_temporal": null,
      "row_grain": null, "expected_cadence": null}, "warnings": []}],
  "diagnostic_assistance": [{"diagnostic_id": "D06", "state": "not_adopted",
    "message": "This diagnostic does not yet use Dataset Structure selections."}]
}
```

Every candidate has opaque `candidate_id`, predicate/instance key, typed safe display label, resolution status, assertion pin `{artifact_id,payload_hash,dependency_fingerprint}`, aggregate evidence summary, rank, `recommended`, and closed warning codes. `candidate_id` is request-local and cannot be accepted without its pin. `diagnostic_assistance.state` is `not_adopted|can_prefill|limited|unavailable`; before a consumer's approved assist adapter it is always `not_adopted`.

Draft request:

```json
{
  "draft_revision": 7,
  "evidence_fingerprint": "sha256",
  "selections": {
    "tables": [{"table": "applications", "default_entity_candidate_id": "…",
      "default_temporal_candidate_id": "…", "row_grain_candidate_id": "…",
      "expected_cadence": {"action": "confirm|replace|clear|mark_not_applicable",
        "axis_candidate_id": "…", "grouping_candidate_id": "…",
        "value": {"unit": "month", "step": 1}}}]
  }
}
```

The service canonicalizes selection order and returns `draft_revision + 1`. It validates table ownership, one choice per single-selection field, candidate pin exactness, expected-cadence axes/grouping, row-grain compatibility, and closed enum values before persisting. `clear` and `mark_not_applicable` require an explicit UI acknowledgement field; absence is not a decision.

Expected-cadence draft edits are mutable review state, not only a final-submit
field. The autosave request persists its normalized `confirm|replace|clear|
mark_not_applicable` action, supported interval and axis/grouping candidate
tokens, or acknowledgement for `clear`/`mark_not_applicable`; reopening the
review returns that draft unchanged until evidence becomes stale. The final
authority request rejects `replace` while retaining it as a draft-only edit.

Decision batch adds `{ "confirm": true, "decision_basis": {"evidence_fingerprint":"sha256"} }` to the same draft shape. `replace` is rejected in this authority request even though it remains accepted as a draft edit. In one transaction it re-reads current active dependencies, verifies every selected pin/hash/fingerprint and selection cross-reference, appends all source-confirmed DSC decision assertions, supersedes prior active values for the same predicate/instance, updates review state, and records the idempotency response. It publishes no partial selection set. If any dependency changed, it appends no immutable assertion, saves the normalized submitted selections to the draft, refreshes affected candidates, and returns:

```json
{"status":"needs_reconfirmation","draft_revision":8,
 "preserved_selections":{"tables":[]},"changed_dependencies":[],"latest_candidates":[]}
```

This is HTTP `200` because the workflow continues on the same screen. It prevents stale evidence from becoming a confirmed assertion without creating an orphaned user state.

## Persistence and transactions

### Slice 1 durable job control

Slice 1 adds a publication-complete marker to the governed snapshot (`profile_publication_generation`, `profile_publication_fingerprint`, and timestamp) and only `dataset_structure_materialization_jobs`: `job_id`, `snapshot_id`, `asset_id`, `tenant_id`, `reason=post_ready|retry`, `dsc_context_version="1"`, `profile_publication_generation`, `profile_publication_fingerprint`, `status=queued|running|succeeded|retry_wait|failed|revoked`, `attempt_count`, `max_attempts=3`, `available_at`, `lease_owner`, `lease_expires_at`, `heartbeat_at`, `claim_fence`, `cancel_requested_at`, `revoked_at`, `started_at`, `finished_at`, `closed_error_code`, and timestamps. The marker fingerprint is canonical over the exact active published profile artifact IDs/hashes and source-review revision, but is never exposed by the job API. The job has a unique active-work constraint per snapshot/generation, an index on `(status, available_at)`, and a same-tenant/snapshot foreign-key-equivalent service check against `dq_items.sourcing_tenant_id`.

Every atomic claim increments `claim_fence`; this monotonically increasing fencing token, the bound publication generation/fingerprint, current lease owner, unexpired lease, and absent cancellation/revocation are required predicates on every heartbeat and every producer/AAR publication precommit. A worker atomically claims only an unleased eligible row, heartbeats at least every 30 seconds, uses a 90-second lease, and releases/marks success only when the precommit fence proves that exact bound generation remains current. A lost lease, newer fence, cancellation/revocation, or changed publication marker aborts the unit before write and becomes `retry_wait`/`revoked`, never success. Lease expiry recovery uses retry delays of 30 seconds, 5 minutes, then 30 minutes; three failed/expired attempts are terminal.

The worker invokes only existing DSC v1 materialization/resolution contracts. It creates no dsc-v2 assertion, decision, draft, expected cadence, synthetic identifier, metadata update, or diagnostic record. It has a 10-minute cooperative job deadline and only closed error codes. The present v1 producers may each perform their documented bounded scan. A cross-predicate guarantee of one source-table scan is **deferred** until a later job-scoped verified input/cache contract exists; Slice 1 must neither claim nor simulate that optimization.

The durable queue permits one in-process poller per API process; it is not daemon-only or in-memory work. Concurrent API processes are safe because claims and all write precommits use the database fence predicates above. Process shutdown first stops new claims, requests cancellation/revocation for its active fenced claims, signals cooperative stop, and joins the poller. Backup/quiesce is not permitted to begin until that join completes and a final transaction proves no active unrevoked claim owned by the process can write; if the process cannot establish this, backup is failed/blocked rather than permitting a post-backup write.

These rows are owned by **Data Sourcing**, not diagnostics or a diagnostic-local AAR owner. Diagnostic-only reset/cleanup preserves them. Item/snapshot deletion deletes their rows in the same tenant-scoped transaction; a full Data-Sourcing/factory reset deletes them together with the selected source snapshot and only its AAR descendants. No reset performs a broad AAR wipe.

### Slice 2/3/4 additive review records

Additive tables, all tenant-scoped, use server-generated IDs and UTC timestamps:

| Table | Required columns and constraints |
| --- | --- |
| `dataset_structure_review_states` | `snapshot_id` PK, `tenant_id`, `asset_id`, `state`, `current_generation`, `current_evidence_fingerprint`, `current_draft_id`, `review_contract_version`, `updated_at`; unique `(tenant_id, snapshot_id)`. |
| `dataset_structure_materialization_jobs` | `job_id` PK, tenant/asset/snapshot, `generation`, `requested_tables_json`, `reason`, `status`, `attempt_count`, `max_attempts`, `evidence_fingerprint`, `started_at`, `finished_at`, `error_code`; unique live `(snapshot_id, generation)`; no raw error text. |
| `dataset_structure_review_drafts` | `draft_id` PK, tenant/snapshot, `revision`, `base_evidence_fingerprint`, `selections_json`, `state`, `superseded_by`, `created_by`, timestamps; unique `(snapshot_id, revision)`. History is retained. |
| `dataset_structure_review_decision_batches` | `batch_id` PK, tenant/snapshot, draft/revision, `idempotency_key`, `request_digest`, `outcome`, `decision_assertion_refs_json`, actor/time; unique `(tenant_id, idempotency_key)` and `(snapshot_id, batch_id)`. |
| `dataset_structure_backfill_runs` | `run_id` PK, migration version, tenant/snapshot, status, generation/job reference, source fingerprint, timestamps, closed error code; unique `(migration_version, tenant_id, snapshot_id)`. |

`dataset_structure_materialization_jobs` is the Slice-1 table, extended additively in Slice 2+ only if an evidence fingerprint/generation is needed. It is not a second queue. `dataset_structure_review_states`, drafts, and decision batches do not exist in Slice 1; `dataset_structure_backfill_runs` does not exist before Slice 4.

The job queue is durable database state and may be polled by one in-process worker per API process under the fencing/shutdown rules above; it is never daemon-only or in-memory work. Materialization uses the existing predicate producers and the immutable snapshot/profile-publication marker bound into the claim. It has the Slice-1 deadline/retry/lease rules above, no unbounded candidate expansion, and a closed error code. It must checkpoint only between atomic candidate batches; candidates are published through existing atomic producer lifecycle rules. A job cannot change source inventory, profile artifacts, user roles, drafts, or diagnostic state.

The review service derives source assertions from AAR only in a consistent read and records a canonical evidence fingerprint over selected active assertion/profile pins plus review metadata revision. A job may be retried only when no equal fingerprint/materialization is active. Replacing a stale producer assertion follows its current narrow supersession rules; independent confirmed decisions are never overwritten. Decision assertions are written atomically with state/draft/idempotency rows after all validations succeed.

## Authorization, privacy, and audit

Slice 1 enforces authenticated same-tenant access through `dq_items.sourcing_tenant_id` exactly as defined above. The following fine-grained RBAC matrix is a later authorization-hardening contract (no earlier than Slice 3); it must not delay or weaken that Foundation boundary.

The current DSC v1 trusted-workspace assumption is explicitly insufficient for this Data Sourcing API. This adoption requires authenticated tenant/RBAC enforcement at router and service boundaries:

| Action | Minimum permission |
| --- | --- |
| Read review/job/draft | `data_sourcing.structure.read` |
| Start/retry materialization, save draft | `data_sourcing.structure.edit` |
| Confirm decisions/restart | `data_sourcing.structure.confirm` |
| Run backfill/inspect failed jobs | `data_sourcing.structure.admin` |

Actor and tenant are never client body fields. Cross-tenant existence, source columns, assertion IDs/hashes and job diagnostics are not disclosed. Drafts contain only selections and opaque candidate/pin identities; assertion payload restrictions still prohibit raw values, samples, row data, dictionary prose, or source paths. AAR's immutable decision provenance contains actor ID under the existing access policy, while the UI receives the minimal authorized projection.

## Backfill, rollout, and rollback

Backfill is additive and does not wipe AAR. After the schema migration, select each active, ready snapshot with valid governed profiles. Create/reuse review-state row, materialize DSC idempotently, and create a `review_required` draft whose recommendations are based on existing reviewed roles; do not auto-confirm selections. No historical or inactive snapshot is eagerly scanned; it is materialized on open. Existing confirmed DSC assertions remain readable and are used only when exact dependencies match. Active profiles missing/tampered during backfill result in `failed` with retry, never invented evidence.

The Slice-4 operator endpoint is `POST /api/v2/dataset-structure/backfill` with
`{ "cursor": "after:<tenant-private keyset cursor>" | null, "limit": 1..50 }`. It is
bounded to the authenticated tenant and requires the exact
`data_sourcing.structure.admin` role. The existing admin role does not imply
this scope: until a tenant administrator explicitly grants it through the
normal role bootstrap/migration process, the endpoint fails closed with `403`.
It returns aggregate counts and a closed error code only. It is never run by
startup reconciliation or an ordinary review GET.

Pagination cursors are signed and tenant-bound. Production requires
`DSC_BACKFILL_CURSOR_SECRET`; the deterministic fallback is available only
when `APP_ENV=development|test` (or the automated test harness), and a missing
production secret fails closed before the migration performs a write.

This is the sole migration exception to the normal publication-proof rule. If
the retained profile set is complete and hash-verified, it may create a
completion proof marked `proof_source=backfill` and the migration version; a
normal publication proof remains marked `publication`. The resulting job uses
the exact normal generation, fingerprint, completion token, queue fencing, and
retry rules. Incomplete/tampered profiles create neither proof, job, review
draft, nor AAR decision and remain safely retryable.

Backfill processes small transactions per snapshot. It records its migration/run row only after successful state/job scheduling so retry is safe. A deployment rollback leaves additive tables and AAR artifacts intact; disabling the new route/UI leaves all present diagnostic behavior unchanged. A repair command may retry an individual snapshot or start an explicit new review draft; it may not delete AAR broadly. Test data can be rebuilt operationally, but rebuild is not a migration requirement.

Until a separately approved consumer adoption contract exists, every diagnostic remains `legacy` or its current `shadow` behavior: no manifest, readiness, result, report, or UI output changes. The first intended adopter is a D06 **assist** adapter: it can prefill eligible scope values from confirmed default entity/temporal/expected-cadence selections, clearly show the source and evidence, and require D06's own final confirmation. It must not reuse a DSC selection as an execution decision, modify completed runs, or redefine `reporting_grain`.

### D06 assist ownership proof

D06 resolves the requested item through the authenticated tenant's authoritative
`dq_items.sourcing_tenant_id` ownership boundary; a cross-tenant or tenantless
item is returned as the same 404 as an unknown item. AAR assertion payloads do
not carry a duplicate tenant field. After that server-side binding, D06 accepts
an authority assertion and each pinned source candidate only when both AAR
metadata and payload carry the exact owned `{asset_id, snapshot_id}`. A foreign
asset or foreign snapshot is ignored and leaves D06 on its legacy manual path.

## Versioning and acceptance tests

Slice 1 uses only the existing DSC v1 schema/context/producer contracts and needs no DSC version change. Slice 2's mutable review response is `review_contract_version: "1"` and is not an AAR assertion schema. Slice 3 adds DSC context/registry version `"2"` for the two default predicates and `expected_cadence`; v1 assertions/contexts stay readable. A v2 request may pin compatible v1 observed/candidate assertions only after runtime validates their typed v1 payload and current dependency fingerprint. A v1 request cannot select a v2-only predicate and receives `DSC_R_UNSUPPORTED_FACET`. Any change to selection shape, cadence taxonomy, or confirmation authority creates a new negotiated context/review version; display copy alone is versioned with the UI release.

Acceptance tests must prove:

1. **Slice 1 gate:** only successful post-commit governed profile publication enqueues materialization; source finalization never runs or waits for a producer. Failed/expired/retried DSC work does not change `ingest_status=ready`, AAR profile publication, or legacy/shadow diagnostic behavior.
2. **Slice 1 gate:** authenticated same-tenant access compares identity to `dq_items.sourcing_tenant_id`; unknown/missing/not-ready/cross-tenant all return `404`; GET polling and startup/GET lease reconciliation recover abandoned jobs without scanning on the request thread.
3. **Slice 1 gate:** only DSC v1 materialization runs; every job success is exact-bound to a completed profile-publication generation/fingerprint; enqueue failure is isolated from ready ingest and recovered by reconciliation.
4. **Slice 1 gate:** heartbeat/lease/retry/deadline/closed-error behavior is durable and idempotent; monotonic fencing, cancellation/revocation and producer/AAR precommit fence checks prevent a stale multi-process worker from writing; shutdown/backup proves no post-backup write. Diagnostic reset preserves Data-Sourcing-owned jobs while tenant-scoped item/full-source reset cleans them correctly.
5. **Slice 2 gate:** entity/time alternatives remain separately queryable; rank/recommendation writes only mutable draft data; route-state resume, limited states, user copy, and safe projection hold.
6. **Slice 3 gate:** a regular observed cadence produces only the specified expected-cadence proposal; mixed/irregular/unknown does not; explicit differing expected cadence is retained with its warning.
7. **Slice 3 gate:** row-grain uniqueness is evaluated on the selected key combination; repeated entity IDs are not falsely rejected; v1 remains compatible and v2 predicates negotiate correctly.
8. **Slice 3 gate:** atomic success, invalid-input rejection, idempotent replay, explicit restart, and stale `200 needs_reconfirmation` preserve draft history and create no partial decision batch.
9. **Slice 4 gate:** backfill is interruption/retry idempotent, preserves AAR/profile/diagnostic records, does not auto-confirm, and leaves inactive snapshots lazy.
10. **Later gates:** metadata deep-link retention and technical identifiers each require their separately stated evidence/lineage constraints. D06 and all other consumers remain byte/row behaviorally unchanged until their own assist contracts are enabled.
