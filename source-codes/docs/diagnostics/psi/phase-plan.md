# Diagnostic #14 PSI — D-22 bounded phase plan

## Authorization and scope

On 19 Aug 2026 the requester authorized implementation and conditional enablement of Diagnostic #14 under D-22. The bounded scope is one feature-level Population Stability Index vertical slice through the existing Coverage → Scope → Run → Findings workflow, using immutable baseline/current populations, reviewed frozen bins, deterministic arithmetic, and the governed Analytical Artifact Repository. No other pending diagnostic may be enabled.

Implementation owner: Codex, working in the DataWorkbench repository.

## Baseline and architecture assessment

The workspace distribution does not contain Git metadata, so a Git worktree status could not be established; implementation preserved files outside the bounded paths below. The backend baseline, run with a throwaway `SYSTEM_DB_PATH`, is **580 passed, 1 skipped**. Frontend ESLint passed; the unit runner initially could not spawn inside the restricted sandbox (`EPERM`) and passed when rerun with the required child-process permission.

Concrete extension points are:

- Diagnostic #2's dedicated manifest and runner establish the core-diagnostic adapter precedent.
- `routers/diagnostics.py` owns the existing public workflow and needs explicit #2/#4/#14 dispatch without new endpoint families.
- `analysis_runtime.artifact_types` is the producer-extension registry; repository identity, immutability, integrity, exact reuse, lineage, and impact remain in `AnalysisArtifactRepository`.
- `SnapshotRef` and stable canonical fingerprints are reused; PSI gets its own two-population manifest rather than being forced into `FrozenAnalysisManifest`.
- Test Lab already has diagnostic scope/result dispatch points; PSI receives explicit components and never uses the cross-field or feature-target renderers.

## Acceptance tests

Pure engine and bin validation; population predicate and schema compatibility; manifest immutability/readiness; runner and streaming; API routing; artifact registration, integrity, exact reuse/conflict, lineage/impact; contextual findings and explicit review-to-issue behavior; frontend component, reachability, build, and Playwright workflows; #2/#4 regression; exact-nine register invariants. Final gates are `ci-local.ps1 -SkipE2E -StrictLint`, the full backend suite with a throwaway database, and relevant Playwright specs.

## Rollback

Keep #14 `workflow_pending` until every gate passes. All contracts and repository types are additive. To revert enablement, change only #14's register status back to `workflow_pending`; #2 and #4 remain executable. Never delete or rewrite frozen manifests or immutable `psi`/`psi_bins` payloads, and do not destructively remove additive schema fields. Historical completed runs remain readable after disabling new runs.

## Next decision point

After targeted and full regression evidence is collected: enable #14 and record `enabled_by = D-22 / DX-06` only if every acceptance criterion passes; otherwise retain `workflow_pending`, document failures, and stop at the gate.

Decision reached: all blocking gates passed. Diagnostic #14 is executable and records
`enabled_by = D-22 / DX-06 (19 Aug 2026): PSI acceptance gate`.

## Initial traceability

| Requirement | Code boundary | Acceptance evidence |
|---|---|---|
| Deterministic PSI and canonical thresholds | `dq_diagnostics/engines/population_stability/` | Engine unit tests including 0.10/0.25 boundaries |
| Ordered immutable populations | PSI manifest + population module | One/two-snapshot and rejection tests |
| Reviewed frozen bins | PSI bins + artifact type registry | Validation/reuse/integrity tests |
| Exact reuse and lineage | Existing `AnalysisArtifactRepository` | Positive/negative identity and graph tests |
| Explicit diagnostic dispatch | `routers/diagnostics.py` + PSI runner | API and #2/#4 regression tests |
| Contextual findings | PSI result/runner | No violation or automatic issue tests |
| Explicit UI dispatch | Test Lab scope/findings components | Unit and Playwright tests |
| Conditional register enablement | Framework data/register tests | Nine total; executable exactly 2, 4, 14 |
