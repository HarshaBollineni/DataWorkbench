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

Dataset Structure review includes entity, temporal axis, row grain and expected
cadence. Before finalization, selections are saved against the staged evidence
and can be resumed. Changed evidence requires review again. Staged choices are
not published authority: matching choices seed the subsequent governed review,
where confirmation is still required. Explicit no-selection decisions remain
available when structure cannot be established. A technical row identifier,
when offered, identifies rows; it is not a business entity or temporal key.

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

Confirmed Dataset Structure decisions assist D06, D08 and D11. D06 uses entity,
period and compatible expected cadence in its normal scope review; it no longer
requires a separate structure-acknowledgement step. D08 refreshes structural
bindings in open drafts while preserving human decisions. D11 excludes the
confirmed entity and temporal columns from analytical feature/segment choices.
Manual diagnostic decisions and frozen historical runs retain their own scope.

## Manage findings and RCA

Issue Management lists failed diagnostic results, with filters for asset,
status, criticality, and table. Tracking or closing an issue records workflow
state; it does not modify source data or rerun a diagnostic.

Open an issue row to enter the four-page RCA flow: Intake, Initial Review,
Investigate and Closure. Select a proposed hypothesis, add relevant context,
review the planned method and run the investigation. When discovery is planned,
one Run action performs discovery and one confirmation of the original hypothesis.
No intermediate input is required. The final assessment is supported, rejected or
inconclusive; both stages remain visible in the evidence. Missing information or
execution failure stops the run with a limitation. Older cases that already contain
a driver-focused candidate retain their review step.
Approve a supported conclusion or record an unresolved outcome at Closure.
Approval does not confirm remediation; a tracked remediation handoff is separate.

Numerical evidence distinguishes regular values from physical missing values
and confirmed special categories. For example, `NOI regular <= 3050` excludes a
confirmed `-999` sentinel, which appears as `NOI special: -999`. Unconfirmed
proposals remain regular values. Metadata is frozen when RCA starts; use Start
afresh when new metadata needs to be captured.

**Ask about this data** unlocks after two successful planned hypothesis-test
runs. A completed discovery-plus-confirmation pair counts once and consumes one
budget unit. Opening reviews, standalone discovery, failed and cancelled runs do not count.
Ask about retained RCA evidence or request one bounded calculation. Chat searches
the governed helper library before using generated analysis and does not consume
the normal hypothesis-test budget or silently revise hypotheses. Out-of-scope
questions are rejected. Expand Evidence and method for the supporting references
and use the download action when full generated output is available.

Reloading restores retained case evidence and chat. Start afresh requires
confirmation and removes the current RCA work, including chat, while retaining
the source diagnostic evidence. This is a destructive restart, not a chat reset.

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
result evidence. A full operational wipe removes sourced datasets and generated
DSC, diagnostic, AAR, issue, and RCA work. Governed Knowledge Base documents,
versions, rules, packages, and source files are preserved so diagnostics remain
usable; the completion message identifies the protected boundary and shows the
verified built-in KB baseline fingerprint. Normal production upgrades install
new built-in versions in place and do not require a historical wipe.

The MVP does not yet claim fine-grained AAR RBAC or an untrusted multi-tenant
security boundary. Deploy it only within the currently controlled application
boundary. A later product-wide authorization review will add tenant and access
policy enforcement without changing retained artifact payloads or identities.

Local mutable state defaults to `backend/.runtime/system_state.db`; uploaded and working
files use configured storage directories. In Azure, the database backup and
durable artifacts are stored on the configured persistent volume. Do not edit
SQLite state directly while the service is running.

For implementation and operational details, see [`TSD.md`](TSD.md).
