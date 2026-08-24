# Archimedes 0.5.0 release notes

## Diagnostic #14 — Population Stability Index

Diagnostic #14 is enabled under D-22 / DX-06 after its acceptance gates passed.
It supports ordered one-snapshot predicate/complement and two-snapshot population
modes, baseline-only deterministic draft bins with explicit review/freeze,
canonical contextual thresholds (`>= 0.10` watch, `>= 0.25` investigate), and
explicit epsilon smoothing (`1e-6`). Feature PSI and bin contributions are stored
as immutable `psi` artifacts with lineage to frozen `psi_bins` (or compatible
reviewed coarse bins). Exact reuse includes populations, bin hashes, thresholds,
epsilon, bindings, methodology, and engine version. The legacy early-40%/ten-
quantile/binary-0.20 spike behavior is superseded. PSI never creates a violation,
score penalty, or issue without the explicit review-to-issue action. Segmented PSI
is deferred until the core scope contract gains a governed segment selector.

## What changed

0.5.0 replaces the flat upload-item identity with an asset model. An asset has
one immutable human-quotable system ID, an editable alias/display name, a
reference-schema version history, and retained snapshots. A Fresh upload makes
v1; Add period adds an active snapshot to the current version; Full
replacement makes the next version and supersedes the old snapshot set as one
operation. This supersedes the 0.4.0 sibling-item re-upload behaviour while
preserving the `dq_items.dataset_family_id` / delivery seam and existing
downstream readers.

The seven-step upload flow is now explicit and resumable:

1. choose Fresh or an existing asset and its fixed time basis;
2. upload the data (and optional dictionary);
3. profile and review the inventory;
4. inspect the schema and dictionary warnings;
5. confirm schema or period consequences;
6. commit the snapshot/version transition;
7. review the completion summary and downstream refresh state.

The catalogue is capture-only analytics, not a dashboard. It lists one row per
asset, expands to current and superseded versions/snapshots, shows dictionary
and lifecycle labels, usage counts/dates, restore controls, version diff, and
the retrievable UPL-28 completion summary. Usage measures read only the
append-only `usage_events` log; no chart, export, or dashboard route ships.

## Retirement tranche

The release retirement tranche recorded 65 unused API exports, 14 orphaned UI
files, and 9 unused-but-live exports whose outcomes were individually decided.
The reachability gate and its negative bites-check remain part of the release
verification. Existing delivery, diagnostic, and issue seams were preserved.

## Adopted decisions without requester confirmation

The following plan §3.1 answers are recorded as adopted without requester
confirmation: A-Q01 (schema and shape diff are MUST; distribution is rendered
as SHOULD), A-Q02 (dictionary versions are independent and asset-bound), A-Q03
(one Database workbook is one snapshot with per-table detail), A-Q04 (exactly
five typed ID families), A-Q05 (CRE is Product-only), and A-Q06 (admin history
starts with assets and resets). These are the plan's named answers, not new
unreviewed scope.

## Deliberate limits and operational notes

AST-18 is deferred as a bound-only release decision: `row_level.available` is
false with an explicit reason because no row-digest substrate was captured in
Step 4 and row-level diff is not the default view (OOS-13). No UI control
triggers a row-level diff.

Superseded snapshots are retained and never cleaned up by normal product
flows. Their unbounded growth is accepted for 0.5.0 so rollback and audit stay
exact; only the documented factory-reset path removes the associated objects
and usage history. Restore is wholesale by version set. Rollback is limited to
retained snapshots and stored reference schemas: it does not reconstruct a
source file that was never retained, undo external downstream work, or provide
row-level conflict resolution.

Distribution comparison is honest about its basis: it compares the latest
snapshot of each requested version using the Step 4 fingerprints. A migrated
pre-fingerprinting snapshot renders schema and shape but marks distribution
unavailable with its reason. File size is obtained with `stat`, and the diff
never opens a source file or writes fingerprints.
