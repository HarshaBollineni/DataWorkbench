# {{TITLE}} experiment process record

## Working agreement

- Diagnostic key: `{{DIAGNOSTIC_KEY}}`
- Contract version: `{{VERSION}}`
- Owner: `{{OWNER}}`
- Evidence branch/commit: `{{REFERENCE}}`
- Fixture policy: synthetic or approved de-identified data only

## Decision log

| Date | Decision | Reason | Contract/resource impact | Owner |
|---|---|---|---|---|
| {{YYYY-MM-DD}} | {{DECISION}} | {{RATIONALE}} | {{NONE_OR_VERSION_CHANGE}} | {{OWNER}} |

## Evidence runs

| Date | Command/notebook | Fixture set | Result | Output reference | Reviewer |
|---|---|---|---|---|---|
| {{YYYY-MM-DD}} | `{{COMMAND}}` | `{{FIXTURES}}` | {{PASS_OR_FAIL}} | `{{REFERENCE}}` | {{REVIEWER}} |

## Method changes

For every analytical change, record the prior behavior, new behavior, affected rules, fixture
changes, and version decision. Do not rely on notebook cell history as the decision record.

## AI evaluation, when applicable

Record model/deployment, prompt and schema versions, exact metadata projection, validation failures,
human-review results, and deterministic fallback. Report matching/adjudication accuracy separately
from deterministic diagnostic correctness.

Define and test the rerun reuse identity. Record the source run/event for every reused validated
inference, prove that reuse makes zero new provider calls, and report reused-inference counts
separately. Include negative fixtures for tenant, snapshot, field metadata, KB, prompt and schema
changes plus failed/invalid prior output. A field without reusable inference must require an explicit
user request before a new provider call.

## Open questions

| Question | Blocking? | Owner | Due/evidence needed |
|---|---|---|---|
| {{QUESTION}} | {{yes|no}} | {{OWNER}} | {{DETAIL}} |
