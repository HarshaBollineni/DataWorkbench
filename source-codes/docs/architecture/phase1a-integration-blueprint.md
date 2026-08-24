# Phase 1A integration blueprint

**Status:** design checkpoint; no capability is enabled by this document.  
**Baseline:** `docs/architecture/phase0-baseline.md`.  
**Purpose:** define where reusable work from `C:\Src\missingChecks` and
`C:\Src\tempTestPath` belongs in DataWorkbench before code is moved.

## 1. Decisions

1. DataWorkbench remains the product shell and system of record for users,
   assets, snapshots, Test Lab runs, findings, issues, KB rules and RCA.
2. Prior projects contribute domain packages and presentation patterns. Their
   standalone FastAPI applications, root React applications and local identity
   schemes are not copied into DataWorkbench.
3. The nine-row diagnostic register remains the authoritative core framework.
   A capability may appear as a core diagnostic only when its computation,
   decision type and readiness contract match a registered row.
4. Supporting analyses are visible and governed, but never presented as an
   extra core diagnostic, a DQ violation, or evidence that a pending diagnostic
   has executed.
5. Every analytical run is read-only against source snapshots. Remediation or
   normalization must produce an explicit derived output or new snapshot; it
   must not mutate the assessed snapshot.
6. Deterministic calculations remain authoritative. AI may later summarize or
   propose an action, but it cannot silently change a calculation, threshold,
   classification, disposition or published artifact.
7. Phase 1 introduces no authentication redesign, distributed queue, database
   replacement or multi-tenant storage redesign.

## 2. Capability placement

| Source capability | Product location | Framework role | Initial treatment |
|---|---|---|---|
| Physical data profiling | Data Sourcing | Shared readiness evidence | Consolidate behind a snapshot/profile reader |
| Dictionary read, inspection and canonical mapping | Data Sourcing Review | Shared governed metadata | Extend current mapping; retain explicit user confirmation |
| Dictionary normalization and schema promotion | Data Sourcing / Asset history | Governance, not a diagnostic | Port review contracts; adapt identity to DataWorkbench assets |
| Explainable missing-value assessment from `missingChecks` | Test Lab Investigate | Supporting analysis for L2-05 Missingness Mechanism | Do not register as #6 or auto-create violations |
| ROC/AUC/Gini | Test Lab diagnostic #2 | Candidate-flag evidence | Port domain engine and adapt to the diagnostic manifest/result contract |
| Feature Target Separation | Test Lab diagnostic #2 | Candidate-flag classification using AUC and IV | Compose ROC and binning results; preserve partial outcomes |
| IV/WOE and supervised binning | Test Lab analysis workspace | Reusable evidence for #2 and baseline definitions for #14 | Not a separate core diagnostic |
| Population Stability Index | Test Lab diagnostic #14 | Contextual result requiring SME review | Use frozen bins and two immutable snapshot/population references |
| Dataset snapshot registry from `tempTestPath` | Backend reference design | Already owned by DataWorkbench assets/items | Reuse hashing/load ideas, not its separate snapshot IDs |
| Analytical artifact repository and impact analysis | Shared backend + Asset Catalogue | Reuse, audit and lineage foundation | Adapt to DataWorkbench IDs and persistence conventions |
| Schema comparison | Data Sourcing / Asset Catalogue | Pipeline/schema governance | Consolidate with existing version diff and schema checks |
| Normalization/transformation | Future remediation workspace | Remediation output | Out of first integration; always create derived data |

### Why missingness is not diagnostic #6

The framework defines #6, Row-completeness reconciliation, as expected versus
actual row coverage by segment and period using an anti-join. `missingChecks`
classifies field-level missing shares and searches for explainable patterns in
missing indicators. It is valuable evidence for L2-05, but it does not perform
the #6 computation. Renaming it as #6 would make the coverage board inaccurate.

The initial UI placement is therefore a **Supporting investigations** section
inside Test Lab. A later framework decision may add or remap a diagnostic, but
that requires an explicit register decision rather than an implementation
shortcut.

### Why IV/binning is not a diagnostic card

The framework definition for #2 explicitly names univariate AUC and Information
Value. Binning is the governed calculation and review mechanism that produces IV
and reusable population definitions. It is part of #2 evidence and can provide
the frozen definition consumed by #14. It should have a workspace and artifacts,
but not inflate the nine-card register.

## 3. Product ownership boundaries

### Data Sourcing owns

- Asset and snapshot selection.
- File/database acquisition and immutable storage.
- Table and column discovery.
- Physical profiles and source warnings.
- Dictionary inspection, canonical header/value mapping and human confirmation.
- Target, use case, product, time basis and reporting-period metadata.
- Schema comparison and explicit promotion/replacement decisions.
- The transition to derived `ready` status.

Data Sourcing does not calculate AUC, IV, PSI or missingness explanations. It
creates the governed evidence those capabilities consume.

### Test Lab owns

- Core diagnostic coverage and readiness.
- Frozen diagnostic manifests.
- Deterministic execution and progress.
- Decision-type-shaped results and findings.
- Supporting analytical investigations tied to the same snapshot and scope.
- Human disposition and issue/RCA hand-off.

### Asset Catalogue owns

- Snapshot/version history.
- Analytical artifact history and exact-reuse status.
- Artifact provenance and supersession.
- Downstream workflow impact.
- Links back to the Test Lab run that created or consumed an artifact.

### Issue Management and RCA own

- Confirmed diagnostic violations.
- SME-confirmed candidate/contextual concerns.
- Ownership, remediation and closure evidence.
- Investigation history; they do not recalculate analytics.

## 4. Shared backend contracts

The names below are design names. Phase 1B may refine Python details, but must
preserve their semantics.

### 4.1 SnapshotRef

The canonical analytical input identity uses existing DataWorkbench keys:

```json
{
  "asset_id": "asset_ab12cd34ef56",
  "system_id": "DS0123",
  "version_no": 2,
  "snapshot_id": "item_ab12cd34ef56",
  "snapshot_status": "active",
  "tables": ["applications"],
  "target_variable": "default_flag",
  "period_column": "observation_month",
  "use_case": "Model development",
  "source_fingerprints": {"applications": "sha256-or-existing-fingerprint"}
}
```

`snapshot_id` is the existing `dq_items.item_id`; no parallel snapshot identity
is introduced. A loader must accept selected tables/columns and return copies or
read-only frames. It must refuse missing, unready or superseded inputs unless a
historical-read use case explicitly permits them.

### 4.2 AnalysisScope

```json
{
  "primary_snapshot": {},
  "comparison_snapshot": null,
  "table": "applications",
  "target": "default_flag",
  "features": ["income", "bureau_score"],
  "period_column": "observation_month",
  "segment_columns": ["portfolio"],
  "filters": [],
  "excluded_columns": [],
  "missing_target_policy": "action_required"
}
```

The scope is explicit and ordered. An empty feature list, absent target or
incompatible comparison snapshot must produce a readiness/blocking reason, not
an implicit fallback.

### 4.3 FrozenAnalysisManifest

Every core diagnostic and supporting analysis freezes:

- Manifest schema version and stable run ID.
- Capability ID and optional registered diagnostic ID.
- One or two `SnapshotRef` values.
- `AnalysisScope`.
- Parameters and the source of each value: default, operator, KB or artifact.
- Dictionary/schema/KB references used.
- Engine name and version.
- Input, methodology and manifest fingerprints.
- Actor and timestamps.

A frozen manifest is immutable. Re-running it either replays the completed run
or produces a separately identified execution according to the capability's
declared policy; it never silently adopts new thresholds or KB content.

### 4.4 AnalysisArtifact

The reusable identity is adapted from `tempTestPath`:

```json
{
  "artifact_id": "art_...",
  "artifact_type": "roc_feature|fine_bins|coarse_bins|iv|psi|missingness_report",
  "asset_id": "asset_ab12cd34ef56",
  "snapshot_id": "item_ab12cd34ef56",
  "comparison_snapshot_id": null,
  "population_fingerprint": "...",
  "target_fingerprint": "...",
  "feature": "bureau_score",
  "methodology_fingerprint": "...",
  "scope": "universal|workflow_local",
  "workflow_id": null,
  "payload_hash": "...",
  "status": "active|superseded",
  "source_artifact_ids": [],
  "created_by": "analyst",
  "created_at": "..."
}
```

Exact reuse requires matching snapshot/population, target interpretation,
feature and methodology. A near match is reported as a mismatch requiring
review, never silently reused. Payload integrity is verified on read.

Large payloads should be immutable files under a configurable artifact root;
SQLite should hold searchable metadata, lineage and status. This avoids placing
tree structures, bin tables or large evidence arrays inside the central state
database while keeping catalogue queries efficient.

### 4.5 Results and findings

Core diagnostics continue to use `DiagnosticResult`:

- #2: `candidate_flag`, initially `review_state=open`; never a violation badge.
- #4: `verdict`, with pass/violation/not-applicable.
- #14: `contextual`, initially `review_state=open`; PSI thresholds guide review
  but do not create an automatic DQ violation.

Supporting analyses use a separate `SupportingAnalysisResult` envelope:

```json
{
  "capability_id": "missingness_explanation",
  "status": "complete|partial|action_required|not_applicable|failed",
  "summary": {},
  "artifact_ids": [],
  "observations": [],
  "warnings": [],
  "methodology": {},
  "review_state": "open"
}
```

An observation becomes an issue only after an explicit SME confirmation or when
a registered verdict diagnostic produces a violation. Execution errors remain
run errors and never become data-quality findings.

## 5. Capability adapters

### Missingness adapter

- Import the `missing_checks` domain package or vendor it as a clearly owned
  package; do not copy its FastAPI application.
- Build its typed assessment command from a frozen DataWorkbench snapshot,
  confirmed dictionary metadata, period column and parameters.
- Preserve co-missingness blocks, structural-break evidence, predictor
  inclusion/exclusion reasons, untestable outcomes and methodology.
- Persist the complete report as a `missingness_report` artifact.
- Present column classifications as supporting observations awaiting review.
- Do not translate "above threshold" directly into a `violation`.

### Diagnostic #2 adapter

- Port `roc_gini`, `information_value` and `feature_target_separation` domain
  packages without the prior API/global application state.
- Require a confirmed target and one or more eligible independent variables.
- Preserve localized-leakage evidence separately from global AUC/IV category.
- Produce one candidate-flag result per feature plus a run summary.
- Persist feature-level ROC, fine-bin, coarse-bin and IV artifacts so partial
  reruns can reuse exact work.
- Retain per-feature failures; one failed IV calculation must not erase valid ROC
  evidence.

### Diagnostic #14 adapter

- Require a reviewed/frozen bin definition and two non-overlapping population
  references: baseline/current snapshots or disjoint populations from one
  snapshot.
- Validate column/type/bin compatibility before execution.
- Preserve missing, special, unseen and guard populations explicitly.
- Emit feature PSI and bin contributions as contextual results requiring review.
- Store source artifact lineage from bin definitions to PSI outputs.

### Dictionary adapter

- Extend current Data Sourcing mapping rather than mounting a second wizard.
- Port normalization, workbook inspection, reconciliation and review contracts.
- Never auto-apply fuzzy column matches, inferred critical roles, sentinel
  meanings or breaking schema changes.
- Version confirmed dictionary/schema evidence against the DataWorkbench asset.

## 6. UI integration

### Data Sourcing

Add progressively, without creating a parallel application:

1. Dictionary inspection and header/value mapping within the current Review
   surface.
2. Normalized definitions, warnings and reconciliation evidence.
3. Explicit schema/dictionary confirmation and version history.

### Test Lab

- Keep the nine-card Coverage board unchanged.
- Add a **Supporting investigations** section below or beside the core workflow;
  the first tool is Missingness Mechanism.
- Diagnostic #2 later uses the existing Coverage → Scope → Run → Findings path.
- IV/bin review appears inside #2 details and as an artifact workspace.
- Diagnostic #14 uses the existing core path with a baseline/current selector in
  its scope gate.
- Findings visuals continue to be driven by `decision_type`, not metric size.

### Asset Catalogue

Add artifact filters for type, feature, target, run and status; show exact reuse,
supersession, source artifacts and directly affected workflows. Retained source
versions and analytical artifacts remain distinct concepts in the UI.

## 7. API direction

Existing diagnostic endpoints remain authoritative for registered diagnostics.
Phase 1B should add a small supporting-analysis surface rather than overload the
diagnostic register:

- `GET /api/v2/items/{item_id}/analyses/catalog`
- `POST /api/v2/items/{item_id}/analyses/manifests`
- `GET/PATCH /api/v2/analyses/manifests/{run_id}`
- `POST /api/v2/analyses/manifests/{run_id}/run`
- `GET /api/v2/analyses/runs/{run_id}/stream`
- `GET /api/v2/items/{item_id}/analyses/results`
- `GET /api/v2/artifacts` and `GET /api/v2/artifacts/{artifact_id}`
- `GET /api/v2/artifacts/{artifact_id}/impact`

This is a proposed contract family, not authorization to add all endpoints at
once. The first vertical slice should implement only what the missingness tool
needs, with artifact listing added when its first artifact is persisted.

## 8. Phase 1B implementation slices

### Slice 1: foundation only

1. Add typed `SnapshotRef`, `AnalysisScope`, manifest and artifact metadata
   contracts.
2. Add a read-only snapshot loader over existing `dq_items`, `dq_item_tables`
   and per-item cache rebuild behavior.
3. Add artifact metadata/payload repositories with integrity and exact-match
   tests.
4. Add the supporting-analysis protocol/registry and minimal API routes.
5. Do not add a UI tool or enable a diagnostic in this slice.

### Slice 2: missingness vertical slice

1. Add the `missingChecks` adapter.
2. Add manifest readiness and parameter handling.
3. Persist a complete report artifact.
4. Add Test Lab Supporting investigations UI.
5. Add explicit review-to-issue hand-off.

### Slice 3: dictionary integration

Port guided inspection, normalization and review into Data Sourcing, then bind
confirmed versions to snapshots and later analytical manifests.

### Slice 4: diagnostic #2

Port ROC/Gini, IV/binning and Feature Target Separation, add candidate-flag
findings, and flip register row #2 to executable only after its acceptance gate.

### Slice 5: diagnostic #14

Port frozen-bin PSI and artifact impact lineage, then flip #14 only after its
two-population scope and contextual review flow pass acceptance.

## 9. Acceptance gates

Every slice must satisfy:

- Existing 520-pass backend baseline does not regress.
- Frontend lint, reachability and production build pass.
- The core register stays at exactly nine rows.
- Only explicitly approved diagnostic rows change from `workflow_pending`.
- Cross-field #4 results and UI remain behaviorally unchanged.
- Source snapshots and superseded versions remain immutable.
- Manifests capture all inputs, parameters, versions and fingerprints needed to
  reproduce a result.
- Exact artifact reuse has positive and mismatch tests.
- Candidate/contextual/supporting results never render as automatic violations.
- Not-applicable, action-required, partial and failed are distinguishable.
- No engine imports FastAPI or React concerns.
- No LLM call is required for a deterministic calculation or verdict.

Additional missingness gates:

- Co-missing block members are excluded from one another's feature pools.
- Untestable is not collapsed into no-pattern-found.
- Zero remains a valid value unless confirmed as a missing-value code.
- Report and UI disclose that the shallow-tree evidence is descriptive, not
  causal or proof of MCAR/MNAR.

## 10. Explicit non-goals

- Replacing the current asset/version model with `tempTestPath` snapshot IDs.
- Copying either prior frontend as a nested application.
- Copying `tempTestPath/backend/src/workbench/api/main.py`.
- Treating every analytical artifact as a DQ finding.
- Enabling #6 with field-level missingness logic.
- Automatic normalization or mutation of assessed source data.
- Authentication redesign, enterprise identity, distributed execution or a
  database-platform migration.

## 11. Phase 1B start gate

Implementation may begin when the following are accepted:

1. Missingness is a supporting investigation, not diagnostic #6.
2. DataWorkbench IDs remain canonical.
3. Artifacts use exact-match fingerprints and immutable payloads.
4. Supporting observations require review before issue creation.
5. The first implementation slice is foundation-only and does not change the
   visible framework coverage.
