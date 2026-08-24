# Phase 0 baseline

This document records the product boundary that must remain stable while the
codebase is cleaned and prepared for later capability integration. It describes
the running code, not the historical plans.

## Active user workflow

1. Data Sourcing creates or selects an asset, uploads a dataset or multi-table
   workbook, profiles it, reviews its dictionary/type evidence, and stores an
   immutable snapshot.
2. A snapshot whose derived ingest status is `ready` can enter Test Lab.
3. Test Lab presents the nine-row diagnostic register as Coverage, Scope, Run,
   and Findings.
4. Only diagnostic #4, Cross-field business rule, is executable. The remaining
   eight diagnostics are visible as `workflow_pending` and do not produce a pass
   or failure.
5. Cross-field violations create or update Issue Management records and can be
   investigated through the RCA workflow.

## Capability status

| Capability | Status | Active implementation |
|---|---|---|
| Asset identity, versions and snapshots | Active | `backend/routers/asset_catalogue.py`, `backend/assets/` |
| File ingestion and variable profiling | Active | `backend/routers/sourcing.py`, `backend/ai/v2/service.py`, `backend/ingest/` |
| Dictionary attachment and type review | Active, limited | Data Sourcing and variable inventory |
| DQ framework/register | Active | `backend/routers/framework.py`, `backend/dq_diagnostics/register.py` |
| Cross-field business rules | Executable | `backend/dq_diagnostics/engines/cross_field/` |
| Findings, dispositions and reports | Active | `backend/routers/diagnostics.py` and diagnostic runners |
| Issue Management and RCA | Active | `backend/routers/issues.py`, `backend/ai/v2/issues.py`, `backend/rca.py` |
| Knowledge Base rule governance | Active | `backend/kb.py`, `/api/v3/knowledge/*` |
| Target separation, completeness, PSI and maturity diagnostics | Registered, workflow pending | No executable product path |

> Historical baseline note: Diagnostic #14 PSI was subsequently authorized and
> enabled under D-22 / DX-06 on 19 Aug 2026. This table remains the Phase 0 fact.
| Former fourteen-test Galileo registry | Retired | Compatibility surfaces only; not a product capability |
| Monitoring schedules/background execution | Not an active user workflow | Legacy schema/data may remain |

## API boundaries

- `/api/login`, `/api/logout`, `/api/me`: current local authentication/profile.
- `/api/admin/*`: user, reset, asset-history and context-memory administration.
- `/api/v2/assets/*`, `/api/v2/items/*`: sourcing, snapshots, inventory and
  asset lifecycle.
- `/api/v2/diagnostics/*`: coverage, manifests, execution, findings and reports.
- `/api/v2/issues/*`: issue register, supporting analyses and disposition.
- `/api/v2/framework/*`: framework read models.
- `/api/v3/taxonomy/*` and tag endpoints: governed classification.
- `/api/v3/knowledge/*`: KB document, version and rule lifecycle.
- `/api/v3/rca/*`: governed RCA case workflow.

The exact OpenAPI document produced by the running FastAPI application is the
authoritative endpoint contract.

## Persistence boundaries

- `backend/system_state.db` is mutable local runtime data. It may contain users,
  assets, snapshots, KB records, findings and RCA state. It is ignored by source
  control and must not be deleted by cleanup scripts.
- `backend/uploads/` contains durable uploaded sources in local development.
- Per-item SQLite databases under the configured temporary item directory are
  regenerable execution caches.
- `ui/playwright-report/`, `ui/test-results/`, Python caches and test-runtime
  directories are generated and can be deleted safely when no test is running.

## Phase 0 regression gates

From `source-codes`:

```powershell
./ci-local.ps1 -SkipE2E -StrictLint
```

Individual fast checks:

```powershell
cd ui
npm run lint
npm run check:reachability
npm run build

cd ../backend
../.venv/Scripts/python.exe -m pytest tests -q
```

Backend gates must point `SYSTEM_DB_PATH` at a throwaway database, as
`ci-local.ps1` already does. Verification must never mutate the developer's
local `system_state.db`.

## Out of scope for Phase 0

- Authentication redesign or new authorization rules.
- Enabling a pending diagnostic.
- Integrating `missingChecks` or `tempTestPath`.
- Removing legacy database tables without a reviewed migration and rollback.
- Replacing SQLite or introducing distributed job infrastructure.
