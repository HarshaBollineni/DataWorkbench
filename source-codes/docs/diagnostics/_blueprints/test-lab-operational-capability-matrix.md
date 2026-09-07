# Test Lab operational capability matrix

## Decision

Re-run, per-diagnostic history, duplicate-draft control, interruption recovery, downloadable
reports, and governed output retention are platform requirements for every deployable Test Lab
diagnostic. Analytical methods may differ; the operational lifecycle must remain consistent.

Workflow-pending diagnostics do not need dormant user controls, but they must pass this contract
before their register entry can become `executable`.

## Shared contract

| Capability | Required behavior |
|---|---|
| Re-run | Create a new run ID and manifest; never mutate a completed run |
| Replay | Opening/executing a completed run reads persisted results without duplicating outputs |
| History | Show draft, running, done and failed runs with timestamps, roll-up and safe status detail |
| Draft control | Offer resume or explicit discard/start-afresh; archive duplicate drafts for audit |
| Execution ownership | Persist one worker lease and heartbeat; refuse a second live executor |
| Interruption recovery | Browser disconnect does not cancel work; expired/unclaimed runs become failed with a safe reason |
| Report | Provide an authenticated downloadable report derived from the frozen manifest and results |
| Repository | Retain material structured/large outputs in AAR with identity, hash, lineage and media type |
| Actions | Keep findings separate from issues; require the contracted human disposition before RCA |

## Register audit — 7 September 2026

| ID | Diagnostic | Register state | Operational assessment |
|---:|---|---|---|
| 2 | Single-feature target separation | Executable | Shared re-run/history/recovery; PDF/text report and governed report/evidence artifacts |
| 4 | Cross-field business rule | Workflow pending | Dormant implementation has shared run/history and PDF/text generation; governed report-artifact parity and order-independent binding regression isolation remain enablement gates |
| 6 | Row-completeness reconciliation | Executable | Shared re-run/history/recovery; PDF/text report and governed reconciliation/report artifacts |
| 8 | Value-semantics classification | Executable | Shared re-run/history/recovery; PDF/text report plus governed bindings, tags, ledger and report artifacts; `done` follows report persistence |
| 11 | Directional/monotonic consistency | Executable | Shared re-run/history/recovery; PDF/text report and governed feature/report artifacts |
| 12 | Label-consistency rule | Workflow pending | No deployable adapter; every shared capability is required before enablement |
| 14 | Population Stability Index | Executable | Shared re-run/history/recovery; PDF/text report and governed PSI/bin/report artifacts |
| 17 | Resolution/maturity rate by vintage | Workflow pending | No deployable adapter; every shared capability is required before enablement |
| 20 | AI-proposal workflow | Workflow pending | No deployable adapter; needs the lifecycle plus an explicit proposal/confirmation and KB-write contract before enablement |

## Implementation notes

The shared Test Lab router and run-state module own history, completed-run replay, leases,
heartbeats, failure finalization and recovery. Diagnostic packages remain responsible for frozen
manifest correctness, deterministic/idempotent persistence, report content, AAR artifact types and
finding semantics.

An expired execution is failed closed rather than resumed in place because current diagnostic
runners do not provide a transactional checkpoint contract. The user may inspect any persisted
evidence and launch a new run. A future resumable executor would require diagnostic-specific
checkpoint identities and idempotent commit tests before changing this policy.
