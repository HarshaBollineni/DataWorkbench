# Diagnostic 8 — implementation report

## Outcome

Value Semantics is deployed as an executable Test Lab diagnostic. Users first receive a plain-language intended-use page, may proceed in General context, confirm semantic roles and available declarations, preview partial coverage, freeze an immutable manifest, execute the deterministic rules, review results and download reports/artifacts.

## Production components

| Boundary | Implementation |
|---|---|
| Discovery/readiness | Framework register, Diagnostic 8 dispatch and profiled-field readiness |
| Scope | Dedicated resumable/frozen `value_semantics` manifest |
| Execution | Versioned safe primitive engine; no free-form YAML execution |
| AI | Optional metadata-only batch role review, bounded catalog expansion, visible per-field outcomes and human confirmation |
| Results | Compact run summary, field/rule/group coverage and recommended actions |
| Reports | Authenticated PDF and text diagnostic report routes |
| AAR | Bindings/report JSON plus tags/assessment-ledger Parquet |
| KB | Value Semantics v0.2 active with v0.1 history; terminology v0.3 active with v0.2 history |
| Action | Grouped candidate findings, explicit disposition, issue/RCA handoff |
| UI | Four-step scope gate and dedicated result renderer in shared Test Lab |

## Key behavior

- General/unspecified is the default and requires no PD/LGD/EAD choice.
- Optional context is non-enforcing and cannot activate or suppress rules.
- Rules route from confirmed target roles and explicit prerequisites.
- Exact deterministic bindings can be accepted; other proposals require a user decision.
- One batch action reviews all pending eligible fields sequentially; matches, no-match, ambiguity,
  insufficient-context and retryable failure outcomes remain visible on their field rows.
- Missing/ambiguous indicators and missing declarations remain visible as `UNSCOPED`.
- Only `CENSORED`, `STALE_FROZEN` and `NOT_APPLICABLE` are persisted as tags.
- Raw values are omitted from the Parquet tags and assessment ledger.
- Findings never create issues automatically.

## Storage change

The AAR metadata gained additive `payload_media_type` and `payload_filename` fields. Existing records continue to default to JSON. The repository accepts only governed Parquet binary writes, derives its own storage path, sanitizes the download filename, verifies hashes on access and retains the existing exact-identity collision behavior.

## Verification

- focused backend acceptance: 92 passed, 2 subtests passed;
- frontend unit: 56 passed;
- frontend lint and production build: passed;
- full backend observation: 771 passed, 1 skipped; five unrelated order/cwd harness cases passed in isolated reruns;
- compilation, JSON validation and diff whitespace checks: passed.

## Rollback

Change the D08 register row to `workflow_pending`. Keep additive storage fields and all immutable KB versions, manifests, results and artifacts so completed analysis remains auditable.
