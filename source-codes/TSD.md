# Aegis Labs technical design

**Status:** Current  
**Application version:** 0.5.2  
**Version source of truth:** [`VERSION`](VERSION)

This document describes the currently deployed design. Release-specific
decisions remain under [`docs/`](docs/); the retired pre-0.4.0 design is kept in
[`../documentation/history/TSD-pre-0.4.0.md`](../documentation/history/TSD-pre-0.4.0.md).

## System overview

Aegis Labs is a React single-page application backed by a FastAPI service. The
browser calls the API over HTTP and server-sent events (SSE). The backend owns
authentication, ingestion, versioned assets, diagnostics, issues, RCA,
knowledge-base governance, and persistence.

| Component | Location | Local port | Responsibility |
| --- | --- | --- | --- |
| React/Vite UI | [`ui/`](ui/) | 5175 | Browser routes, workflow state, API client |
| FastAPI backend | [`backend/`](backend/) | 8001 | Domain APIs, deterministic engines, AI orchestration |
| SQLite state | `backend/.runtime/system_state.db` by default | n/a | Ignored mutable application and audit state |
| Durable artifacts | Configured upload/KB/backup paths | n/a | Source snapshots, KB documents, database backup |

Production builds deploy the backend as an Azure Container App and the UI as an
Azure Static Web App. Azure Files holds durable artifacts and the state backup;
Azure OpenAI supplies model-backed features.

## Runtime and startup

[`backend/main.py`](backend/main.py) loads environment variables, restores a
configured state backup, initializes and migrates the schema, seeds static
reference data idempotently, reconciles persisted items, and mounts the API
routers. Its lifespan starts periodic state backup and RCA time-box sweep
threads and writes a final backup during a graceful shutdown.

The canonical application version is read from [`VERSION`](VERSION) by both
[`backend/app_version.py`](backend/app_version.py) and
[`ui/vite.config.js`](ui/vite.config.js). The health endpoint reports it at
`GET /health`.

## Backend boundaries

[`backend/main.py`](backend/main.py) mounts authentication, administration,
v2, v3, and supporting-analysis routers. The v2 compatibility surface is split
without changing its URLs:

| Module | Ownership |
| --- | --- |
| `routers/v2.py` | Compatibility aggregator and stable import surface |
| `routers/sourcing.py` | Asset creation, upload, ingestion review, profiling, inventory |
| `routers/asset_catalogue.py` | Catalogue, version comparison, restore, asset selection |
| `routers/diagnostics.py` | Coverage, manifests, execution, findings, scoring, reports |
| `routers/issues.py` | Issue register, tracking, analysis, closure, RCA streaming |
| `routers/framework.py` | Framework metadata/results and agent roster |
| `routers/v2_common.py` | Shared response and validation helpers |

`routers/v3.py` owns governed taxonomy, knowledge-base, and RCA case APIs.
`routers/analyses.py` owns supporting-analysis and reusable artifact APIs.
Private module movement must not change these public route contracts.

Domain implementations converge under `backend/domains`. The first migrated
slice is T2-D06 Row Completeness at
`backend/domains/test_lab/diagnostics/t2_d06_row_completeness`, containing its
API models, deterministic engine, manifest, runner, and governed knowledge
resolver. Compatibility aliases at the former `dq_diagnostics` paths preserve
existing imports while callers migrate. Shared result promotion, diagnostic
registration, readiness, thresholds, inference audit, and AAR services remain
outside the T2-D06 package because multiple diagnostics consume them.

T1-D02 Single-feature Target Separation is grouped under
`backend/domains/test_lab/diagnostics/t1_d02_feature_target_separation`.
Information Value and fine/coarse binning primitives are separately owned by
`backend/domains/test_lab/shared/binning` because T1-D02 produces their
artifacts and T4-D14 consumes their governed definitions. Diagnostic-specific
review orchestration remains with T1-D02.

T4-D14 Population Stability Index is grouped under
`backend/domains/test_lab/diagnostics/t4_d14_population_stability`. It owns its
population selection, PSI engine, manifest, and runner while consuming the
shared governed binning definitions above. Former `dq_diagnostics` imports are
exact module aliases during staged migration.

T2-D04 Cross-field Business Rule is grouped under
`backend/domains/test_lab/diagnostics/t2_d04_cross_field_business_rule`. It
owns governed rule binding, deterministic role resolution, manifest
construction, execution, and reporting. Generic run-state constants, lookup,
and append-only decisions live in `backend/domains/test_lab/shared/run_state.py`
for all diagnostics. Former cross-field paths are exact compatibility aliases.

T2-D11 Directional and Monotonic Consistency is grouped under
`backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency`.
It owns the empirical direction engine, KB v0.3 reference data, semantic
adjudication, governed scope manifest, execution, and evidence reporting. Broad
quantile bins determine relationship shape; Spearman correlation and
univariate regression confirm direction, while Pearson correlation is retained
for display only. Expected direction remains KB-driven and is compared with the
separately stored empirical direction after applying the user-confirmed target
orientation.

## Frontend boundaries

[`ui/src/App.jsx`](ui/src/App.jsx) defines authenticated routes and lazy-loads
their pages. [`ui/src/api/client.js`](ui/src/api/client.js) is the endpoint
catalog; [`ui/src/api/transport.js`](ui/src/api/transport.js) owns API origin,
session authorization, JSON transport, and error parsing. Route pages compose
feature components, while shared display/workflow helpers live in `ui/src/lib`.
Domain-owned UI code lives under `ui/src/features`; T2-D06 is grouped at
`ui/src/features/test-lab/diagnostics/t2-d06-row-completeness`. Compatibility
exports preserve its former `ui/src/pages/testlab` module paths.
T1-D02 scope/results live under the matching feature path, while binning
evidence shared with T4-D14 and RCA lives under
`ui/src/features/test-lab/shared/binning`.
T4-D14 scope, results, binning workspace, and workflow helpers live under
`ui/src/features/test-lab/diagnostics/t4-d14-population-stability`; its common
bin-label presentation stays in the shared binning package.
T2-D04 scope and verdict/result rendering live under
`ui/src/features/test-lab/diagnostics/t2-d04-cross-field-business-rule`; the
route-level scope and results modules now only load state and dispatch to the
matching diagnostic component.
T2-D11 scope adjudication and its chart-backed result board live under
`ui/src/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency`.
The scope workflow supports explicit direction classes, exclusion, optional
segmentation, cautious progressive fuzzy candidates, and optional draft KB
proposals without allowing empirical output to rewrite the governed KB.

The governed RCA lifecycle lives under `backend/domains/rca`, with its UI at
`ui/src/features/rca`. Taxonomy, Knowledge Base, and Issue Management remain
separate because they serve workflows beyond RCA. The Analysis Artifact
Repository lives under `backend/domains/aar` and `ui/src/features/aar`;
snapshot and target contracts remain shared in `analysis_runtime`.

| Route | Current surface |
| --- | --- |
| `/` | Data Inventory |
| `/data-sourcing` | Fresh/existing asset upload and review |
| `/asset-catalogue` | Version and snapshot catalogue |
| `/test-lab` | Diagnostic coverage and workflow entry |
| `/test-lab/artifacts` | Reusable analytics artifact repository |
| `/issues` | Cross-asset issue register |
| `/issues/:issueRowId` | RCA case workflow |
| `/knowledge-base` | Governed document and rule lifecycle |
| `/dq-framework` | Diagnostic framework reference |
| `/admin` | Admin-only users, reset, asset, and context oversight |
| `/profile` | User preferences |
| `/login` | Authentication |

`/new-assessment` is a compatibility redirect to `/data-sourcing` and is not a
separate workflow.

## Core data workflows

### Assets and ingestion

An asset has an immutable system identifier, editable alias, fixed time basis,
versions, and snapshots. A fresh upload creates version 1. Adding a period adds
an active snapshot to the current version. Full replacement creates a new
version and supersedes the previous snapshot set. The upload remains staged
until validation, inventory normalization, metadata review, and commit finish.

Test Lab only lists active snapshots whose derived ingestion status is `ready`.
The Asset Catalogue exposes captured lifecycle and usage facts; it does not
compute a separate dashboard metric layer.

### Diagnostics and findings

The governed framework contains nine registered diagnostics. Readiness is data:
a workflow-pending diagnostic remains visible but cannot be executed. Opening a
ready diagnostic creates a scope manifest. The user reviews and freezes scope,
execution streams over SSE, and results become findings with explicit
dispositions and deterministic scoring. Supporting investigations produce
immutable, reusable analysis artifacts.

### Issues and RCA

Failed diagnostic results appear as one issue row per failed test and table.
Issue tracking records owner, priority, target date, and state without mutating
source data or rerunning tests. The v3 RCA workflow stores cases, evidence,
hypotheses, human decisions, proposed fixes, reruns, closure, and audit events.
Executable proposals run only through the sandbox and human-approval gates.

### Knowledge base

Knowledge documents are uploaded, versioned, converted, tagged, parsed into
sections/rules, reviewed, and then published or archived by authorized users.
Model output does not directly publish governed rules.

## Persistence

[`backend/system_db.py`](backend/system_db.py) owns the SQLite schema and shared
query helpers. Important table families include users/sessions, asset versions
and snapshots, ingestion inventory, diagnostic manifests/results/findings,
issues, RCA cases and audit records, knowledge documents/rules, taxonomy,
analysis artifacts, usage events, and context memory.

The local SQLite database is snapshotted to `SYSTEM_DB_BACKUP_PATH` when that
setting is present. Per-item working databases remain on local storage because
SMB-backed SQLite locking is unsafe; they can be rebuilt from durable uploads.

## Configuration and security boundary

The setup guide lists the required Azure OpenAI variables. Important optional
settings include `CORS_ORIGINS`, `VITE_API_BASE`, `SYSTEM_DB_PATH`,
`SYSTEM_DB_BACKUP_PATH`, `SYSTEM_DB_BACKUP_INTERVAL`, `UPLOAD_DIR`,
`ITEM_DB_DIR`, `KB_STORAGE_DIR`, `SKILLS_DIR`, `RCA_TIME_BOX_SWEEP_INTERVAL`,
and `SESSION_MAX_AGE_HOURS`.

Authentication currently uses opaque bearer sessions stored in SQLite. The
seeded local account is for development only. Password storage is a known
prototype limitation and must be replaced with a production identity provider
or a modern password-hashing scheme before broader deployment.

## Verification and documentation

Run application gates with `./ci-local.ps1`. The script covers documentation
consistency, UI lint/build/reachability, backend compilation/import/pytest, and
Playwright unless skipped.
Run workspace documentation validation from the parent directory with
`./documentation.ps1 check`. Documentation ownership and update triggers are in
[`../documentation/documentation-maintenance.md`](../documentation/documentation-maintenance.md).
