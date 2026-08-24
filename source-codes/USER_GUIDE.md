# Aegis Labs user guide

**Status:** Current  
**Application version:** 0.5.2

## Start the application

From `source-codes/`, install once and then start both services:

```powershell
./setup.ps1
./app.ps1 start
```

Open `http://localhost:5175`. The backend API runs at
`http://localhost:8001`. Use `./app.ps1 status`, `stop`, or `restart`; add
`-Service Backend` or `-Service Frontend` to target one service.
The `stop` and `restart` actions force-stop any process using the selected
service port, even when that process was started outside `app.ps1`.

The seeded local-development account is `anirban` / `dqstudio`. It is not a
production credential. AI-backed features require the Azure OpenAI variables
listed in [`README.md`](README.md).

## Navigation

| Page | Purpose |
| --- | --- |
| Data Inventory | Search uploaded databases/datasets and see their workflow status |
| Data Sourcing | Create an asset or add data to an existing asset |
| Test Lab | Select a ready snapshot and run governed diagnostics |
| Issue Management | Review failed tests and open RCA cases |
| Knowledge Base | Upload, review, publish, or archive governed knowledge |
| DQ Framework | Inspect the diagnostic taxonomy and readiness |
| Admin | Manage users and administrative state; visible only to admins |
| Profile | Manage personal display and AI preferences |

The Asset Catalogue is opened from Data Sourcing and other asset selectors. The
Analytics Artifact Repository is available from Test Lab.

## Source and version data

Choose Database for a multi-table workbook or SQLite source. Choose Dataset for
a model-ready, single-table CSV or workbook input.

### Start or extend an asset

- **Fresh Upload** creates a new asset. Supply its alias and choose its fixed
  time basis (`Period` or `No time basis`).
- **Existing** adds data to an active asset. Select **Add period** to append a
  comparable snapshot to the current version, or **Full replacement** to create
  a new reference-schema version and supersede the current snapshot set.

The sourcing flow then asks you to:

1. choose the target asset and upload source files;
2. review deterministic validation and profile evidence;
3. normalize column types, roles, values, and special-value handling;
4. confirm target, period, use-case, product, and replacement intent;
5. review schema or period consequences;
6. commit the snapshot/version transition;
7. review completion and downstream refresh status.

Dictionary files are optional unless the selected mapping requires one. Review
all generated suggestions before commit. A staged upload can be resumed; Test
Lab will not offer it until it reaches `ready` and its snapshot is active.

### Catalogue and restore

The Asset Catalogue shows the immutable system ID, alias, kind, current
version, active and superseded snapshots, dictionary state, lifecycle state,
and captured usage facts. Expand an asset to compare versions or restore a
retained older version. Restore changes which retained version is active; it
does not recreate missing source data or undo external downstream work.

## Run a diagnostic

1. Open Test Lab and explicitly choose a ready active asset.
2. Review the nine-diagnostic Coverage board. Workflow-pending diagnostics are
   visible but cannot be run.
3. Open an executable diagnostic's scope.
4. Review resolved roles, tables, columns, exclusions, thresholds, and other
   diagnostic-specific inputs.
5. Run the diagnostic. Progress and evidence stream into the console.
6. Review Findings, record required dispositions, and inspect the deterministic
   Score/report.

A frozen manifest is the immutable record of what ran. Later edits create new
work rather than changing historical evidence. Supporting investigations in
Test Lab can create reusable analytics artifacts; exact compatible artifacts
may be reused, while near matches are not silently substituted.

## Manage findings and RCA

Issue Management lists failed diagnostic results, with filters for asset,
status, criticality, and table. Tracking or closing an issue records workflow
state; it does not modify source data or rerun a diagnostic.

Open an issue row to enter its RCA case. Review evidence and agent proposals,
answer human questions, approve or reject proposed work, and record closure.
AI-generated analysis can be incomplete. Executable fix proposals remain behind
the sandbox and explicit human approval; approval does not imply an automatic
change to the original source system.

## Govern knowledge

Knowledge Base accepts `.txt`, `.md`, `.docx`, and text-based `.pdf` files up to
20 MB. Choose a category, upload the document, inspect the converted Markdown,
apply taxonomy tags, and submit it for review. Review parsed sections, rule
bindings, and the parse report before publishing. Only authorized editors and
reviewers can advance governed content; model output cannot publish a rule
directly.

## Administration and local state

Admin visibility requires the `admin` authorization role. Factory reset is a
destructive administrative workflow and presents its own confirmation and
result evidence.

Local mutable state defaults to `backend/system_state.db`; uploaded and working
files use configured storage directories. In Azure, the database backup and
durable artifacts are stored on the configured persistent volume. Do not edit
SQLite state directly while the service is running.

For implementation and operational details, see [`TSD.md`](TSD.md).
