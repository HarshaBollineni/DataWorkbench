# Phase 1B — Missingness Mechanism vertical slice

## Product boundary

Missingness Mechanism is a **supporting investigation** in Test Lab. It does
not add a diagnostic-register row, does not implement diagnostic #6, and does
not automatically create a DQ violation. An above-tolerance observation must
be explicitly confirmed before an `issues_v2` record is created.

## Flow

1. Test Lab loads the supporting-analysis catalogue for a ready, active snapshot.
2. The user selects a table and optional missingness parameters.
3. The backend validates readiness and freezes the snapshot, scope, inventory
   fingerprint, parameters, period column, confirmed missing-value codes and
   claim boundary in an `analysis_manifests` record.
4. The deterministic engine reads the snapshot through `SnapshotLoader`, builds
   missing masks and co-missingness blocks, applies the seven classification
   gates, and runs shallow-tree descriptive analysis where valid.
5. The complete report is stored as an immutable `missingness_report` artifact.
6. Above-tolerance column observations remain `open` until a user confirms an
   issue or dismisses them with a reason. Dispositions are append-only records.

## Reused implementation

The analytical domain modules in
`backend/supporting_analyses/missingness_engine` are a byte-for-byte port of
`C:\Src\missingChecks\backend\src\missing_checks` version 0.2.0. Its FastAPI
application, upload layer, job manager and React application were deliberately
not copied. DataWorkbench owns snapshot access, execution, persistence and UI.

Preserved controls include:

- co-missing block members excluded from one another's predictor pools;
- zero treated as valid unless explicitly supplied as a missing-value code;
- structural period breaks reported separately from missingness mechanisms;
- `untestable` kept distinct from `no_explainable_pattern`;
- deterministic parameters and random seed frozen in the manifest;
- explicit disclosure that results are descriptive and do not prove MCAR, MAR,
  MNAR or causality.

## APIs

- `GET /api/v2/items/{item_id}/analyses/catalog`
- `POST /api/v2/items/{item_id}/analyses/manifests`
- `GET /api/v2/analyses/manifests/{run_id}`
- `POST /api/v2/analyses/manifests/{run_id}/run`
- `GET /api/v2/items/{item_id}/analyses/results`
- `POST /api/v2/analysis-observations/{observation_id}/disposition`
- Existing `/api/v2/analysis-artifacts` read APIs expose reports and lineage.

Execution is synchronous in this first vertical slice. A later scalability
slice can add streaming/background execution without changing the engine,
manifest, artifact or observation contracts.
