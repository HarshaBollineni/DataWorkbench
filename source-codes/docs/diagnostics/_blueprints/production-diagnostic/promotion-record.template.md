# {{TITLE}} experiment-to-production promotion record

## Source and identity

- Experiment: `experiments/test-lab/{{DIAGNOSTIC_KEY}}`
- Experiment contract version/hash: `{{REFERENCE}}`
- Readiness approval: `{{REFERENCE}}`
- Production diagnostic key: `{{DIAGNOSTIC_KEY}}`
- Production methodology version: `{{VERSION}}`

## Asset disposition

| Experiment asset | Treatment | Production destination | Version/hash | Reason |
|---|---|---|---|---|
| {{SOURCE}} | {{carried|rewritten|replaced|archived|excluded}} | {{DESTINATION_OR_NONE}} | {{REFERENCE}} | {{RATIONALE}} |

## Production additions and hardening

List launch workflow, manifest governance, analytical corrections, failure behavior, UI, reports,
AAR, KB publication/history, LLM boundaries, findings, issue/RCA integration, security, tenancy,
operations, and process documentation added during promotion.

## Data and storage compatibility

Record schema migrations, backfill/default behavior, payload/media changes, immutable historical
records, lineage, retention, and rollback compatibility.

## Acceptance evidence

Link `acceptance-evidence.md` and summarize exact results without hiding failures or isolated reruns.

## Enablement

- Decision: `{{workflow_pending|executable}}`
- Approved by: `{{OWNER}}`
- Date: `{{YYYY-MM-DD}}`
- Register value: `{{VALUE}}`

## Rollback

Specify the narrow reversible action that blocks new launches. Preserve frozen manifests, results,
reports, artifacts, KB history, audit events, issues, and RCA evidence unless a separately approved
retention action says otherwise.
