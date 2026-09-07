# {{TITLE}} implementation checklist

Mark a box complete only when the implementation and its cited evidence exist.

## Handoff and process

- [ ] Experiment readiness record is approved.
- [ ] Deployment map accounts for every promoted or excluded asset.
- [ ] Production contract and known limitations are reviewed.
- [ ] Production runtime has no import or file dependency on `experiments/`.
- [ ] Operational owner, support notes, enablement, and rollback are recorded.

## Backend

- [ ] Register entry and readiness reason are implemented as `workflow_pending`.
- [ ] Draft manifest supports resume/discard and captures immutable data identity.
- [ ] Configuration patching validates roles, declarations, parameters, and candidates.
- [ ] Preview distinguishes runnable, blocked, partial, unscoped, and not-applicable coverage.
- [ ] Freeze records canonical fingerprint, actor, timestamp, versions, hashes, and lineage.
- [ ] Runner is deterministic/versioned, failure-safe, retry-safe, and progress-aware.
- [ ] Re-run creates a new run ID; completed-run replay cannot duplicate persisted outputs.
- [ ] Execution uses the shared persisted lease/heartbeat and expires safely to a visible failed state.
- [ ] Synchronous failure, stream failure, browser disconnect, duplicate execution, and process restart are tested.
- [ ] Compact results reference large evidence rather than embedding it.
- [ ] Report generation is reproducible from governed inputs and metadata.
- [ ] Diagnostic API routes enforce the required authentication and tenant boundary.

## Frontend

- [ ] Discover/readiness state and plain-language user question are visible.
- [ ] Intro/help explains intended use, outputs, non-goals, and limitations.
- [ ] Scope/configuration/preview/freeze/launch stages are represented.
- [ ] Suggestions disclose evidence; candidates require the contracted confirmation.
- [ ] Results show executive outcome, details, denominators, and partial coverage.
- [ ] Reports and artifacts have working view/download/error states.
- [ ] Findings require explicit disposition before issue/RCA handoff.
- [ ] Loading, empty, failure, resume, and accessibility behaviors are verified.
- [ ] Per-diagnostic run history exposes progress/results/evidence and a safe interruption reason.

## Knowledge, AI, and artifacts

- [ ] KB resources are validated, versioned, published, and historical versions retained, or N/A.
- [ ] Shared terminology impact on existing diagnostics is regression-tested, or N/A.
- [ ] LLM data projection, versions, schema, validation, audit, human gate, and fallback are implemented, or N/A.
- [ ] Same-input reruns reuse validated inference with source run/event lineage and zero duplicate provider calls, or N/A.
- [ ] Reuse is tenant-bound and invalidated by new/changed features, KB/prompt/schema changes, and failed/invalid responses, or N/A.
- [ ] Current-run provider calls and reused prior inferences are disclosed separately, or N/A.
- [ ] AI cannot silently change scope, execute rules, assign results, or create issues.
- [ ] Every material AAR output has a registered type/schema, identity, hash, lineage, media type, and bounded summary.
- [ ] Binary artifacts use governed filenames and integrity-checked downloads.
- [ ] Raw or sensitive values are excluded unless the contract explicitly requires and protects them.

## Actions and governance

- [ ] Candidate finding grouping and traceability are bounded and tested.
- [ ] Disposition captures actor, rationale, result, rule, and artifact references.
- [ ] Issue creation is human-controlled unless separate approval explicitly permits automation.
- [ ] RCA receives the required immutable diagnostic evidence.
- [ ] Audit events cover material inference, freeze, launch, report, disposition, and activation decisions.

## Verification and release

- [ ] Resource/KB schemas and hashes are tested.
- [ ] Rule, boundary, precedence, ambiguity, and invariant tests pass.
- [ ] Manifest draft/patch/freeze/immutability/tenancy tests pass.
- [ ] Runner failure/retry/reuse and artifact-integrity tests pass.
- [ ] AI reuse idempotency, invalidation, tenant isolation, and explicit-new-feature invocation tests pass, or N/A.
- [ ] Draft de-duplication, run history, re-run/replay, lease expiry, and recovery tests pass.
- [ ] API auth/tenant/launch/result/report/download tests pass.
- [ ] Frontend scope/results interactions, lint, unit tests, and production build pass.
- [ ] Finding/issue/RCA traceability tests pass.
- [ ] Existing diagnostic regression gates pass.
- [ ] Exact commands and results are recorded in `acceptance-evidence.md`.
- [ ] Acceptance owner approves register activation and rollback.
