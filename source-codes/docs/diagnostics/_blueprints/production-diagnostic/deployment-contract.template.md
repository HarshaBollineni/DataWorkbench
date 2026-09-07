# {{TITLE}} production contract

- Status: `workflow_pending`
- Diagnostic key: `{{DIAGNOSTIC_KEY}}`
- Framework location: Test {{TEST_ID}}, Diagnostic {{DIAGNOSTIC_ID}}
- Source experiment: `experiments/test-lab/{{DIAGNOSTIC_KEY}}`
- Accepted experiment contract: `{{VERSION}}`
- Accountable owner: `{{OWNER}}`

## User decision and intended use

State the one question answered, intended users, supported decisions, and what the diagnostic must
never be used to claim. Explain whether an information/acknowledgement page is required.

## Scope and launch workflow

Define snapshot/table/field/population selection, general-context behavior, optional contexts,
semantic roles, declarations, defaults, blockers, partial coverage, preview, freeze, launch, retry,
resume, and discard behavior. Define re-run as a new immutable run, completed-run replay,
per-diagnostic history, the persisted worker lease/heartbeat, stale-run recovery, and which
interrupted evidence remains viewable.

## Frozen manifest

List all frozen identities: tenant, actor, item, snapshots, table/fields/population, role evidence,
parameters, declarations, KB/terminology/prompt/schema/engine/method versions and hashes, upstream
artifacts, inference disclosure, coverage, and canonical fingerprint rules.

## Deterministic execution

Document stable rule IDs, prerequisites, grain, equations/logic, precedence, missing evidence,
failure behavior, idempotency, and scale envelope. State explicitly which outcomes are not passes.

## AI/LLM boundary

State `not applicable` or define purpose, optional/required invocation, exact data projection,
provider/model/deployment/API/prompt/schema versions, validation, timeout/retry, audit, human gate,
fallback, and every decision the model is prohibited from making. Define a tenant-safe rerun reuse
identity across snapshot, feature metadata, KB, prompt and schema versions; source run/event lineage;
separate new-call and reused-inference counts; invalidation rules; and the explicit action required
before a genuinely new or changed feature may invoke the provider.

## Results and display

Define the executive outcome, metrics, denominators, detailed views, coverage/unscoped disclosure,
limitations, inference disclosure, and accessibility/empty/error states.

## Reports

Define formats, required sections, authentication/tenant boundary, reproducibility, filenames,
retention, and how partial coverage is disclosed. State whether the governed report artifact is
created before `done` or deterministically on first download; either path must never advertise an
unavailable report as part of a successful completion package.

## Analytics Artifact Repository

For every artifact define stable type, schema version, JSON/Parquet media, identity, payload hash,
lineage, bounded summary, sensitivity, access boundary, exact reuse/collision behavior, and retention.
Avoid raw data duplication; use masked row references when row evidence is necessary.

## Knowledge Base

State `not applicable` or list published documents, active/historical versions, validation, manifest
hashing, shared terminology impact, user-visible KB behavior, and rollback.

## Findings, issues, and RCA

Define which outcomes can become candidate findings, grouping bounds, required human disposition,
issue traceability, RCA handoff, and which coverage/configuration outcomes must never auto-create an
issue.

## Security, privacy, and failure behavior

Define authentication, tenancy, sensitive data, logs, prompts, reports, artifacts, audit events,
failure atomicity, recovery, and deletion/retention constraints.

## Acceptance and enablement

Reference `acceptance-evidence.md`. Keep the register `workflow_pending` until every blocking gate
passes. Record the approver, date, enablement value, and narrow rollback.

## Known limitations

List accepted functional, analytical, data-shape, scale, provider, artifact, and operational limits
and how each is visible to users.
