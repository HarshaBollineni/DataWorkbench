# {{TITLE}} acceptance evidence

## Environment

- Date: `{{YYYY-MM-DD}}`
- Commit/reference: `{{REFERENCE}}`
- OS/runtime: `{{DETAIL}}`
- Database/storage mode: `{{DETAIL}}`
- External provider mode: `{{none|stubbed|live-approved}}`

## Blocking gates

| Gate | Command or review reference | Result | Evidence owner |
|---|---|---|---|
| Resource and KB validation | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Rule/unit/invariant tests | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Manifest/API/integration tests | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| AAR/report/download integrity | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Findings/issue/RCA traceability | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Existing diagnostic regressions | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Frontend unit/lint/build | `{{COMMAND}}` | {{RESULT}} | {{OWNER}} |
| Security/tenancy/privacy review | `{{REFERENCE}}` | {{RESULT}} | {{OWNER}} |
| Documentation/process review | `{{REFERENCE}}` | {{RESULT}} | {{OWNER}} |

## Exceptions and reruns

Record every failure, whether it belongs to this change, its root cause, the exact isolated rerun,
and why acceptance is or is not still valid. Never summarize a partially failing suite as green.

## Acceptance decision

- Blocking gates passed: `{{true|false}}`
- Known limitations accepted: `{{true|false}}`
- Approved by: `{{OWNER}}`
- Approved at: `{{TIMESTAMP}}`
- Register status/value: `{{VALUE}}`
- Rollback verified: `{{true|false}}`
