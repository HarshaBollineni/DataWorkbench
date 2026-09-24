# RCA deployment requirements

**Status:** Current implementation reference; not production certification.

## Runtime

The backend image in `source-codes/Dockerfile` uses Python 3.12 and starts
`python -m uvicorn main:app --host 0.0.0.0 --port 8000` from the backend directory.
Install the complete `backend/requirements.txt`, including pandas, NumPy,
SciPy, scikit-learn, PyArrow and the model SDK. Direct dependencies are pinned
to the tested Python 3.12.4 Windows environment; setup and Docker consume the
same requirements file. This is not a full transitive or hash-verified lock.
Before production release, resolve and lock transitive dependencies on the
target platform, pin the container image digest, and validate that image.
No dependency upgrades are performed by this baseline change.

RCA execution evidence retains the sandbox contract version, Python version,
implementation, OS/architecture and analytical-library versions, including on
rejected, failed and timed-out sandbox runs. Package metadata is cached once
per backend process; restart after dependency changes. No filesystem paths,
environment variables or secrets are collected. Existing AAR records are not
backfilled with an environment they did not record.

The frontend is compiled with React/Vite and served as static files. Node is
needed at build time only. The installed Vite declares `^20.19.0 || >=22.12.0`.
Use `npm ci` with the retained lockfile and `npm run build`; serve `ui/dist` with
SPA fallback and route `/api` to the backend. `VITE_API_BASE` is a build setting.

Generated RCA analysis launches the same Python interpreter as a child process.
The deployment must allow subprocess creation and provide the same dependencies
to the child. `RCA_SANDBOX_TIMEOUT_SECONDS` defaults to 60. The existing runtime
performs code checks and terminates timed-out work; subprocess isolation alone
is not a hardened operating-system security boundary. No local GPU is required.

## Storage and topology

| Setting | Purpose |
| --- | --- |
| `SYSTEM_DB_PATH` | Active SQLite application state |
| `SYSTEM_DB_BACKUP_PATH`, `SYSTEM_DB_BACKUP_INTERVAL` | Optional durable database snapshots |
| `ITEM_DB_DIR` | Per-item working databases |
| `UPLOAD_DIR` | Retained uploads and source data |
| `KB_STORAGE_DIR` | Knowledge document files |
| `ANALYSIS_ARTIFACT_DIR` | AAR payload files, including retained RCA evidence |

Provide writable storage and preserve database state together with referenced
files across restarts and upgrades. Keep active SQLite databases on storage
with reliable SQLite locking; existing Azure guidance keeps working databases
local and uses durable backup/upload storage rather than opening SQLite on SMB.
Chat is reconstructed from AAR records and files, not browser storage.

The current SQLite/local-file architecture fits a single application instance.
Replica scaling and concurrent lifecycle operations need separate validation;
adding containers does not automatically provide shared durable application state.
RAM must accommodate loaded DataFrames, intermediate copies and subprocess
serialization. No measured production CPU/RAM or dataset-size minimum has been
established for these changes.

## Model configuration and network

Supply secrets at runtime. The governed model registry requires:

```text
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AI_PRIMARY_MODEL_ID
AI_PRIMARY_PROVIDER_ID
AI_PRIMARY_DEPLOYMENT
AI_PRIMARY_MODEL_NAME
AI_PRIMARY_MODEL_VERSION
```

The configured deployment must support the structured Responses calls used by
the application. Optional fallback requires corresponding `AI_FALLBACK_*`
deployment metadata, `AI_FALLBACK_WORKLOADS`, `AI_MAX_MODEL_FALLBACKS` and
`AI_FALLBACK_ON`. Chat workloads are `rca_data_chat_planner` and
`rca_data_chat_answer`; generated code uses `rca_code_generator`. The registry
permits at most one configured model fallback. `AI_REQUEST_TIMEOUT` and
`AI_MAX_RETRIES` control provider requests. `AI_RCA_LLM_ENABLED` controls
model-backed opening review; it is not a universal chat off switch.

Allow outbound HTTPS to the configured Azure endpoint. Terminate user HTTPS at
the hosting boundary, configure CORS when origins differ, and allow request
durations sufficient for planning, any calculation, answer generation and
configured retries. `/health` is the existing backend health endpoint.

## Packaging and verification limitations

The VM package templates live in `tools/deployment-package`; Azure deployment
scripts live under `source-codes`. The Compose template and backend `.env.example`
still show legacy Azure deployment settings and do not supply all the governed
`AI_PRIMARY_*` values. Inject those explicitly before using the RCA model path.
This document does not change those executable templates.

Backend integration tests exercise helper execution, the generated-code child
process, persistence and restart behavior with mocked model responses. The
focused Playwright chat test uses mocked API responses to verify rendering,
unlock state and reload. A live Azure end-to-end deployment test remains required.
Existing product-wide authentication and AAR authorization limitations are
documented in [the technical design](../../TSD.md).
