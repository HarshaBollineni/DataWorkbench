# Archimedes - Getting Started

DQ Studio: a FastAPI backend and a Vite/React frontend.

## Current release: 0.5.2

The live workflow begins in Data Sourcing. A fresh upload creates a versioned
asset; an existing asset accepts an additional period or a full replacement.
Every upload is profiled and reviewed before its active snapshot becomes
available in Test Lab. Test Lab presents the nine-diagnostic coverage register
and opens an isolated **Scope → Run → Findings → Score** workflow for an
executable diagnostic.

The 0.5.0 release introduced the current asset, version, snapshot, catalogue,
and seven-step upload model. See `docs/0.5.0/09-release-notes.md`. The earlier
0.4.0 framework transition remains documented in `docs/0.4.0/` as release
history.

The concise active-code and persistence baseline used for Phase 0 maintenance is
recorded in `docs/architecture/phase0-baseline.md`.

The approved placement and contract proposal for integrating prior analytics
capabilities is recorded in `docs/architecture/phase1a-integration-blueprint.md`.

The implemented Phase 1B Missingness Mechanism supporting investigation is
documented in `docs/architecture/phase1b-missingness.md`.

The application documentation tree is indexed in `docs/README.md`.

## Code organization

The FastAPI entry point mounts domain routers from `backend/routers`. The v2
asset-sourcing boundary is organized as follows:

- `routers/v2.py` is the compatibility aggregator that mounts and re-exports
  the v2 domain routers.
- `routers/sourcing.py` owns asset creation, uploads, ingestion review,
  abandonment, profiling, and inventory routes. Its URLs remain under
  `/api/v2`.
- `routers/issues.py` owns issue tracking, analysis, closure, and RCA streaming
  routes. Its URLs remain under `/api/v2`.
- `routers/diagnostics.py` owns the Test Lab coverage, scope manifest, run,
  findings, binning review, and diagnostic-report routes.
- `routers/asset_catalogue.py` owns asset selection, catalogue reads, and version
  comparison routes.
- `routers/framework.py` owns framework metadata, framework results, generated
  reports, and agent-roster routes.
- `routers/v2_common.py` contains response helpers shared by v2 router modules.

Production capabilities are being grouped incrementally under `backend/domains`
without changing the stable router or import contracts. T2-D06 Row Completeness
is the first domain-aligned package at
`backend/domains/test_lab/diagnostics/t2_d06_row_completeness`; its former
`dq_diagnostics` paths remain compatibility imports during the migration.
T1-D02 follows the same pattern at
`backend/domains/test_lab/diagnostics/t1_d02_feature_target_separation`, while
cross-diagnostic Information Value/binning lives under
`backend/domains/test_lab/shared/binning` for reuse by T4-D14.
T4-D14 Population Stability Index is domain-aligned at
`backend/domains/test_lab/diagnostics/t4_d14_population_stability`; its former
engine, manifest, and runner imports remain compatibility aliases.
T2-D04 Cross-field Business Rule is domain-aligned at
`backend/domains/test_lab/diagnostics/t2_d04_cross_field_business_rule`, with
generic run-state and decision access under `backend/domains/test_lab/shared`.
RCA is grouped under `backend/domains/rca`, while the governed Analysis
Artifact Repository is grouped under `backend/domains/aar`. Compatibility
aliases preserve their former flat and `analysis_runtime` imports.

The frontend API catalog and transport boundary are documented in `ui/README.md`.
The current user and technical references are `USER_GUIDE.md` and `TSD.md`.
Workspace-wide documentation maintenance rules and validation are indexed by
`../documentation/README.md`.

## Prerequisites

- Python 3.12+ (`python` on PATH)
- Node 18+ (`npm` on PATH)

> If PowerShell blocks scripts, run this first in your terminal:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## 1. Install (run once)

```powershell
./setup.ps1
```

This creates `.venv`, installs backend deps from `backend/requirements.txt`, and runs `npm install` in `ui`.

## 2. Configure Azure OpenAI

Set these variables in `backend/.env` (auto-loaded by the backend):

```env
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=...
AZURE_OPENAI_API_VERSION=...
```

## 3. Run

```powershell
./app.ps1 start
```

Use `./app.ps1 status`, `./app.ps1 stop`, or `./app.ps1 restart` to manage
both parts. Add `-Service Backend` or `-Service Frontend` to manage one part.
`stop` and `restart` force-stop any process tree listening on the selected
service ports, including processes that were started outside this script.

## 4. Open

- Frontend UI: http://localhost:5175
- Backend API: http://localhost:8001

## 5. Verify

From the workspace root (`C:\Src\DataWorkbench`), validate documentation with:

```powershell
./documentation.ps1 check
```

Run the application quality gates from this directory with `./ci-local.ps1`;
the documentation check is included as a blocking gate.
