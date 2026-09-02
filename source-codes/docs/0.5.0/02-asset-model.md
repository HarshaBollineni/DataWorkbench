# 0.5.0 — The asset model (AST), Step 3a

Step 3a scope only: schema + ID scheme + migration + basic read models (schema, migration,
`backend/assets/identity.py`, `backend/assets/reads.py`). No service-layer functions
(`create_asset` / `add_snapshot` / `supersede_version_set` / `restore_version_set` /
`rename_alias` — S3b) and no Admin UI (S3c) land in this pass.

## Seam

Mandatory read-and-record pass (plan R-01, task 3.1), completed **before** any column was added
to `_SCHEMA`/`_MIGRATIONS`. Read in full: `backend/dq_diagnostics/delivery.py`,
`backend/verify_plan8.py:100-166`, `backend/system_db.py:700-1075` (the `_MIGRATIONS` dict, both
backfill functions, `init_schema()`'s full index block), plus a whole-tree grep for `as_of_date`,
`baseline_delivery_id`, `dataset_family_id`, `delivery_seq`.

### `as_of_date`

- **Written by** `dq_diagnostics/delivery.py:register_delivery` (lines 44-62): set into
  `dq_items.as_of_date` via `s.update(...)`, only when the caller passes a non-`None` value.
- **Read by product/service logic:** none found. `ai/v2/service.py:898`
  (`_supporting()`) checks whether the string `"as_of_date"` appears as a **column name in the
  uploaded dataframe** (alongside `"observation_date"`, `"reporting_period"`, `"load_date"`) —
  this is matching a candidate column header in the user's data, an entirely different thing from
  reading the `dq_items.as_of_date` row value. No other backend module reads the column.
- **Read by tests/UI/specs:** `verify_plan8.py:136` (existence only, via `PRAGMA table_info`),
  `backend/tests/test_delivery.py:135-142` (`test_as_of_date_persists`),
  `backend/tests/test_ingest.py:330` (passes `as_of_date="2026-07-30"` into `reupload_item`),
  `ui/src/pages/DataSourcing.jsx:319,365` (sends it on replacement, displays it in a "As-of date"
  `<dd>` with `data-testid="replacement-as-of-date"`), `ui/e2e/upload-workflow.spec.js:98`
  (asserts that testid's rendered value).
- **Verdict: matches the plan's stated fact exactly** — written by `register_delivery`, read by no
  product logic, only by tests/UI/the framework gate.

### `baseline_delivery_id`

- **Written by:** nothing. Grepped the whole tree; the only occurrences of the literal string are
  `system_db.py:767` (the `_MIGRATIONS["dq_items"]` DDL entry that adds the column) and
  `verify_plan8.py:136` (the presence assertion). No `INSERT`/`UPDATE` anywhere sets it, no `SELECT`
  or dict access anywhere reads it.
- **Verdict: matches the plan's stated fact exactly** — present, asserted present by
  `verify_plan8.py`, otherwise inert. Left in place, untouched, per operating rule 4 and P-04.

### `family_deliveries` ordering

- `dq_diagnostics/delivery.py:38-41`: `family_deliveries(dataset_family_id)` calls
  `s.query("dq_items", order_by="delivery_seq", dataset_family_id=dataset_family_id)` — ordered by
  `delivery_seq`, **not** `as_of_date`.
- **Verdict: matches the plan's stated fact exactly.** AST-11's period-first ordering
  (`assets.reads.ordered_snapshots`, this commit) is therefore genuinely **new** behaviour, built
  as its own read-model function; `family_deliveries` itself is untouched and keeps its existing
  callers working exactly as before.

### The existing seq/family index

- `system_db.py:1053-1057`: `CREATE INDEX IF NOT EXISTS ix_dq_items_family ON
  dq_items(dataset_family_id, delivery_seq)` exists exactly as the plan describes. Untouched by
  this step; the new `ux_dq_items_label_in_family` partial index added in this commit is additive
  and does not overlap it.

### `dq_items`'s existing PLT-02 migration entry

- `system_db.py:766-768` — the `_MIGRATIONS["dq_items"]` dict entry already carries
  `dataset_family_id`, `delivery_seq`, `as_of_date`, `baseline_delivery_id` plus the Phase 4
  ingest columns (`ingest_status`, `ingest_fail_reason`, `dictionary_state`). This step's new
  columns (task 3.3) are added as **more keys in this same dict literal**, not a second
  `_MIGRATIONS["dq_items"]` entry (Python dict literals can't have two keys with the same name
  anyway — the instruction is about not scattering a second ad-hoc migration mechanism elsewhere).

### Conclusion

**No contradiction was found.** Every fact the plan asserts about this seam was independently
verified against the current source and matches. The mandatory gate (task 3.1 / R-01) is
satisfied: proceeding to schema changes is safe. `backend/dq_diagnostics/delivery.py` is not
modified anywhere in this commit (confirmed by diff after the fact — see the commit itself).

## Schema added this step (S3a)

Five new tables (`dq_assets`, `dq_asset_versions`, `dq_asset_dictionaries`, `dq_asset_events`,
`id_sequences`), appended to `system_db.py`'s `_SCHEMA` as `CREATE TABLE IF NOT EXISTS` blocks —
verbatim against the plan's §9.2 SQL, adapted only in registering the JSON-typed columns
(`reference_schema_json`, `parsed_json`, `detail_json`) in `_JSON_COLS` so `system_db.query`/
`insert`/`update` auto-encode/decode them the same way every other JSON column in this file does.

New `dq_items` columns (task 3.3) land as additional keys inside the **existing**
`_MIGRATIONS["dq_items"]` dict entry — no second entry, no ad-hoc DDL. Nothing already in
`dq_items` was dropped, renamed, or repurposed; `dataset_family_id`, `delivery_seq`, `as_of_date`,
`baseline_delivery_id` are untouched.

New unique/plain indexes added beside the existing index-creation block in `init_schema()`:
`ux_dq_assets_system_id`, `ux_dq_asset_versions_no`, `ux_dq_asset_dict_no`,
`ix_dq_asset_events_asset`, `ux_dq_items_label_in_family` (partial, `WHERE snapshot_label IS NOT
NULL`).

## Backfill (`_backfill_asset_model`, task 3.4)

Called from `init_schema()` immediately after `_backfill_ingest_defaults(conn)`, using the SAME
open connection/transaction (raw `conn.execute` throughout — never `system_db.insert/update/query`,
which each open their OWN connection and would deadlock against the still-open outer transaction).
For every distinct `dataset_family_id` with no `dq_assets` row yet: picks the lowest-`delivery_seq`
row (tie-broken by `item_id` for determinism) as the origin, allocates a `system_id` via
`assets.identity.allocate(scope, conn=conn)` (same transaction, no separate commit), sanitises the
origin's `name` into an alias, derives `time_basis` from whether any family member has a non-null
`as_of_date`, writes one `dq_assets` row, marks every family member `version_no=1`,
`snapshot_status='active'`, `intent='fresh'` (origin) / `'add_period'` (the rest), reconstructs
`reference_schema_json` from the origin's `dq_item_tables.columns` + `variable_inventory.data_type`
(left `NULL` when `variable_inventory` is empty — never invented), and writes one
`asset_created` `dq_asset_events` row. Families that already have a `dq_assets` row are
drift-repaired only (`dq_items.name` re-asserted to `dq_assets.display_name`) — idempotent by
construction, proven in `backend/tests/test_asset_migration.py`.

## ID scheme (`backend/assets/identity.py`, task 3.5)

`allocate(scope, conn=None)` — exactly five scopes (`asset_dataset`, `asset_database`, `snapshot`,
`version`, `dictionary_version`), formats `DS%04d` / `DB%04d` / `SN%06d` / `VR%05d` / `DV%05d`.
Atomic: `INSERT OR IGNORE` the counter row, `SELECT` it, `UPDATE` it to `next+1`, all inside one
transaction (either the caller's, when `conn` is supplied, or a fresh one this function opens and
commits). `validate_alias` / `sanitize_alias_for_migration` / `compose_display_name` per P-02/P-03
— no `parse_display_name` exists anywhere.

Epoch reset (P-10): `id_sequences` is now in the delete list of both `system_db.reset_demo()` and
`system_db.wipe_all_items()`. `routers/admin.py`'s `factory_reset` reads
`assets.identity.epoch_snapshot()` **before** calling either reset function and adds it to the
existing post-delete `transaction_log` audit row's payload as `id_epoch`.

**Bug found and fixed during validation:** both reset grades already clear every `dq_items` row
(via `_clear_item_pipeline`/`_WORKPRODUCT_TABLES`), but `dq_assets`/`dq_asset_versions`/
`dq_asset_dictionaries`/`dq_asset_events` are 1:1 derived from those rows and were not initially
in either delete list. Left alone, a reset would strand orphaned `dq_assets` rows (naming families
with zero surviving snapshots) while `id_sequences` reset to a clean baseline underneath them —
so the very next `allocate()` would collide against a stale `system_id` still sitting in
`dq_assets` (`ux_dq_assets_system_id` fires, but only after the fact, and only for the unlucky
first collision). Caught by running the full suite (`test_admin_reset.py`'s reset tests interleave
with unrelated test classes that create fresh items in the same shared throwaway DB), not by the
new tests in isolation. Fixed by adding explicit `DELETE FROM` statements for the four asset tables
alongside `id_sequences` in both `reset_demo()` and `wipe_all_items()` — deliberately **not** by
adding them to `_WORKPRODUCT_TABLES`, because that list is read live by Step 2's
`test_reset_language.py` (the ADM-04 label-completeness gate) and would have failed it on four
newly-unlabeled keys; `ui/src/lib/resetLabels.json` is out of this step's permitted paths. Both
`dq_assets`-family tables and `id_sequences` end up in the reset payload's `deleted` dict as
keys with no label yet — safe (the existing `summarizeReset` code buckets an unknown key into the
generic "other platform records" group rather than leaking its raw name — ADM-01 still holds) but
stale, and worth labelling properly the next time `resetLabels.json` is touched (naturally S3c,
which builds the Admin version-history screen this same data feeds).

---

# Step 3b — the service layer

New module `backend/assets/service.py`: `create_asset`, `add_snapshot`, `supersede_version_set`,
`restore_version_set`, `rename_alias`. Rewrites in `backend/ai/v2/service.py`: `create_item`,
`reupload_item`, `finalize_item`, `_write_table` (P-14's "the four named function rewrites").
`backend/routers/v2.py`'s `POST /items` and `POST /items/{item_id}/reupload` route PATHS are
unchanged; their Pydantic bodies (`ItemIn`, `ReuploadIn`) gained optional fields
(`time_basis`, `intent`/`snapshot_label`/`end_date`) with defaults that reproduce the exact
pre-0.5.0 behaviour, so the current (pre-Step-4) frontend keeps working unchanged while the routes
are ready for Step 4/5 to wire real intent/time-basis UI without a second contract change.

## Design choice: `ai.v2.service`'s old entry points become thin delegates

`create_item(kind, name, time_basis="none")` and `reupload_item(existing_item_id, as_of_date=None,
intent="add_period", ...)` keep their OLD two/three-argument shapes and return dicts (so every
existing caller — `routers/v2.py`, `tests/test_rca.py`, `tests/test_testlab_diagnostics.py` — needed
zero changes) but their BODIES now do nothing except: sanitise `name` into a valid alias
(`assets.identity.sanitize_alias_for_migration` — the same lenient, never-raising rule the S3a
migration backfill uses, deliberately NOT the strict `validate_alias` AST-02 uses for a real
Fresh-Upload alias field) and delegate into `assets.service.create_asset` / `add_snapshot`. This
was the only design that satisfied rule 1 (everything else stays operational at every step): the
CURRENT frontend still POSTs `{kind, name}` / `{as_of_date}` and must keep working verbatim until
Step 4 rewrites the upload flow; a hard signature change here would have broken that mid-release.
`intent` defaults to `'add_period'` for exactly the same reason — 0.4.0 never had a
`'full_replacement'` concept, so every existing caller's observable behaviour (no version bump, the
prior snapshot's row untouched) is reproduced exactly unless a caller opts in explicitly.

## Seam re-confirmed (no new column, no `delivery.py` change)

`add_snapshot` calls `dq_diagnostics.delivery.register_delivery(item_id, asset_id,
as_of_date=start_date)` exactly as `reupload_item` always did — `delivery.py` is untouched (`git
diff --stat` against this commit shows zero lines). The only thing that changed is WHO the family
root is: before, `dataset_family_id` was the origin item's own `item_id`; now it is `asset_id`, a
value `assets.service` mints fresh and which is never equal to any snapshot's own `item_id` (see
`create_asset`/`add_snapshot`, and `test_asset_reads.py`'s own fixtures from S3a, which already used
this convention).

## `finalize_item`'s two writes

Per the S3b brief ("just relocate the write target... don't over-build"), `finalize_item` now writes
target/use case to `dq_assets` (the new canonical, asset-level location — AST-13) and ALSO keeps
writing the identical value onto the snapshot's own `dq_items` row, exactly as it always did. The
second write is not legacy debt: `profile_item`, `_supporting`, `dq_diagnostics.readiness`, and
`dq_diagnostics.manifest` all read `item.get('target_variable')`/`.get('use_case')` directly off the
`dq_items` row (never `dq_assets`), and none of those four functions were in this step's
permitted-paths list. Writing to `dq_assets` only and leaving the mirror out would have silently
broken target-based reclassification and diagnostics readiness for every existing test/flow. The
intent-conditional blank/carry-forward logic (CTX-04/CTX-06) is explicitly Step 6's job.

## The manifest guard (AST-08/AST-12)

`domains/test_lab/diagnostics/t2_d04_cross_field_business_rule/manifest.py`'s
`build_manifest(item_id, diagnostic_id, ...)` already resolved
`item = s.query_one("dq_items", item_id=item_id)` partway through the function (for the
`use_case_override` branch). The guard is placed at the TOP of the function, reusing that same
query (removing the second, later `s.query_one` call that used to re-fetch the same row) — refusing
with `ManifestError` before `require_executable`/`readiness()` even run, so the refusal is never
shadowed by an unrelated readiness verdict (e.g. "blocked: dataset is not ingested yet"). No route
changed — `POST /items/{item_id}/diagnostics/manifest` already threads any exception from
`build_manifest` through `_diag_err`, which already maps `ManifestError` to HTTP 400.
`dq_diagnostics/readiness.py` is untouched, per this step's permitted-paths list (manifest.py only).

## `_write_table`'s superseded guard — a known, accepted limitation

`_write_table` now refuses (raises `ValueError`) when the target snapshot's `snapshot_status` is
`'superseded'`. This protects the write path (`save_file` → `_write_table`) absolutely. It ALSO
applies to `_rebuild_item_db`'s read-path cache reconstruction (used by `_read_table` when a
snapshot's ephemeral sqlite cache has been evicted, e.g. after a restart) — `_rebuild_item_db` was
not in this step's four-function permitted list, so its call site was not touched to special-case
superseded snapshots. Consequence: if a FUTURE step ever re-reads a superseded snapshot's tables
after its cache has been evicted, `_read_table` would raise rather than silently regenerating the
cache. Nothing in the current product exercises that path today (verified: no caller reads a
superseded snapshot's tables anywhere in the tree), so this is a safe default for now, not a
regression — flagged here for whichever step (S7's version diff is the most likely candidate) first
needs to read a superseded snapshot's data back out.

## Enforcement points proven with tests, not assumed

- **No half-supersede surface** (`tests/test_asset_service.py::NoHalfSupersedeSurfaceTests`) —
  `supersede_version_set`/`restore_version_set` are asset+version-keyed by signature (grep-checked),
  and neither `routers/v2.py` nor `routers/admin.py` contains the words "supersede" or "restore" at
  all.
- **No snapshot-delete surface** (`NoSnapshotDeleteSurfaceTests`) — no `assets.service` export looks
  like a delete/purge/remove function; `routers/v2.py` has no `DELETE /items...` route; a behavioural
  test confirms a full replacement leaves the prior snapshot's file on disk and its rows queryable.
- **Time-basis immutability** (`TimeBasisImmutabilityTests`) — `add_snapshot` refuses a `time_basis`
  kwarg outright (bites-check verified: removing the guard makes the test fail); a legitimate
  `full_replacement` with no `time_basis` argument succeeds and `dq_assets.time_basis` is unchanged
  afterwards; a whole-tree grep confirms no `.update("dq_assets", ...)` call anywhere touches
  `time_basis`.
- Every guard above (plus the manifest guard and the `_write_table` guard and the double-restore
  no-op) was verified to **bite**: the guard condition was temporarily neutered, the corresponding
  test was confirmed to FAIL, and the guard was restored — per plan operating rule 6.

## Rule 7 — rewritten tests

`tests/test_ingest.py`'s `ReuploadDeliveryTests` (formerly `4-T7`, asserting `new_row["name"] ==
name` — the OLD sibling-row behaviour) is rewritten, not deleted: the surviving assertions (prior
row byte-identical, family ordering `[old, new]`) are kept because they were already correct; the
`name`-equality assertion is REPLACED with assertions that both snapshots share the same
`dataset_family_id`, the same system-ID-prefixed `display_name` (and that this is NOT the raw
literal passed to `create_item`), and the same `version_no` — proving C-40/D-25's actual guarantee
(the asset identity never forks) instead of the 0.4.0 behaviour this step retires. A second test,
`test_full_replacement_supersedes_the_old_snapshot_and_bumps_the_version`, was added to the same
class to cover the intent 0.4.0 never had at all.

`tests/test_delivery.py` was inspected in full and needed NO changes: every one of its assertions
operates on `dq_diagnostics.delivery`/raw `dq_items` inserts directly, never on `ai.v2.service`, and
`delivery.py` itself is untouched by this step (confirmed by the empty `git diff --stat`  above) —
so none of its 0.4.0 family-semantics assertions describe behaviour this step changed. Verified by
running the full suite before and after: `test_delivery.py` passes unchanged both times.

## Before / after

Full suite: **432 passed / 1 skipped** (S3a baseline) → **471 passed / 1 skipped** (S3b, +39: 38 new
in `test_asset_service.py`, +1 rewritten-and-extended in `test_ingest.py`'s `ReuploadDeliveryTests`).
`python backend/verify_plan8.py`: 28/28. `npm run check:reachability`: PASS, 0 unreachable files, 0
uncalled exports (no frontend touched this pass).

# Step 3c — ADM-06/07 version history (the last sub-commit of Step 3)

New module `backend/assets/history.py`: one function, `asset_history(asset_id)`, aggregating
`dq_asset_versions` + `dq_items` + `dq_asset_events` into ADM-06's payload, plus every
`transaction_log` row with `event='factory_reset'` for ADM-07. Two new routes in
`routers/admin.py`, both behind the existing `_require_admin` + `_plt04_sanitize` pattern (no new
auth mechanism): `GET /api/admin/assets` (the picker — deliberately unfiltered, reusing
`assets.reads.list_assets()` from S3a with no `exclude_lifecycle` argument, since this is an audit
surface, not the SRC-04 workflow picker) and `GET /api/admin/assets/{asset_id}/history`.

## Endpoint shape

```
GET /api/admin/assets/{asset_id}/history ->
{
  asset: {asset_id, system_id, alias, display_name, kind, time_basis,
          current_version_no, lifecycle_status, created_at, created_by},
  versions: [{version_no, status, reference_schema_summary, created_at,
              created_by, change_summary,
              snapshots: [{snapshot_id, snapshot_label, start_date, end_date,
                           intent, snapshot_status, uploaded_at, uploaded_by,
                           change_summary}]}],
  resets: [{at, actor, grade, deleted, id_epoch}]
}
```

Matches the plan's Task 3.4 shape exactly. `versions`/`snapshots` are ordered ascending by
`version_no`, then AST-11's own ordering (`start_date` for a `period`-basis asset, `created_at` for
`none`) WITHIN a version — deliberately over active-and-superseded snapshots alike, never routed
through `reads.ordered_snapshots(only_active=True)` (that function is the AST-12 test-configuration
picker guard, a different concern; this view is explicitly the one place superseded snapshots stay
visible, per AST-08 and the plan's Task 3.4 brief).

## `change_summary` is read, never composed

Every `change_summary` (snapshot- and version-level) is read VERBATIM from a `dq_asset_events.summary`
string that `assets.service._write_event` already wrote in human language at snapshot/version-creation
time (M-2, S3b). `history.py` only picks the right event row (`snapshot_added` for a snapshot,
whichever of `asset_created` / `version_created` / `version_restored` brought a version generation
into being) and never builds a sentence out of an intent/status enum at read time. Confirmed by
`TwoVersionsThreeSnapshotsTests.test_two_versions_three_snapshots_all_present_with_full_fields`, which
asserts every `change_summary` is non-blank and, explicitly, is NOT equal to the raw `intent` token.

## Resets are asset-agnostic, deliberately unfiltered

`resets` is every `factory_reset` row in `transaction_log`, regardless of which asset's history is
being viewed — a reset is a whole-product event, not scoped to one family, and this is what lets
ADM-07's epoch boundary render for ANY asset, including one created strictly after the boundary (the
`asset_id` the boundary itself relates to may no longer even exist — both reset grades delete
`dq_assets`/`dq_asset_versions`/`dq_asset_events` for the epoch they close, per S3a/S3b's
`reset_demo`/`wipe_all_items`). `deleted` counts are returned RAW (table-name keys, exactly as
`transaction_log.payload['deleted']` stored them) — `history.py` does not re-label them. That is the
frontend's job, through the ONE existing map, `ui/src/lib/resetLabels.js`'s `summarizeReset()`
(built in Step 2) — a second backend-side label map was explicitly out of scope for this task.

## The epoch boundary, proven end to end

`EpochBoundaryTests.test_exactly_one_boundary_row_between_two_same_id_assets_from_different_epochs`:
reset (for a clean baseline within the test), create asset 1 (`system_id` captured), reset again,
create asset 2 (asserted to reissue the IDENTICAL `system_id` — D-29), then read asset 2's history and
assert exactly ONE new reset row appears versus the count seen from asset 1's own pre-reset view, that
its `id_epoch` names the counter value the SECOND reset saw (>= 2, since asset 1 had already consumed
one value), and that its timestamp sorts before asset 2's own `asset_created` event. A follow-up
assertion confirms asset 1's OWN history is now a 404 — the reset row in `transaction_log` is the
ONLY surviving trace of asset 1's existence, which is precisely what makes it "the boundary" (D-29).

## Frontend

`ui/src/pages/admin/VersionHistory.jsx` (new), imported into `ui/src/pages/Admin.jsx`: a searchable
asset picker (`GET /admin/assets`, filtered client-side by name/alias/system ID) followed by ONE
merged, chronological list — version groups (each with its own snapshots, active and superseded
alike) and reset rows interleaved by timestamp, ascending. `ui/src/lib/assetHistoryLabels.js` (new) —
a label map for `snapshot_status` / version `status` / `intent` / `kind`, entirely separate from
`resetLabels.js` (which only ever labels reset DELETE COUNTS by table name): every raw token from the
API is routed through one of its five label functions before it reaches JSX; reset rows' counts are
rendered through `summarizeReset()` from the EXISTING `resetLabels.js`, reused rather than duplicated.
`ui/src/api/client.js` gained exactly two exports: `getAdminAssetsList`, `getAdminAssetHistory`.

## Before / after

Full suite: **471 passed / 1 skipped** (S3b baseline) → **481 passed / 1 skipped** (S3c, +10, all new
in `backend/tests/test_admin_version_history.py`). `python backend/verify_plan8.py`: 28/28.
`npm run check:reachability`: PASS — 51/51 `ui/src` files reachable, 0 uncalled exports. `npm run
build`: clean (single-chunk warning only, pre-existing). `npm run lint`: clean. A whole-tree grep of
the new UI files confirms no raw `superseded`/`active`/`fresh`/`add_period`/`full_replacement` token
is ever interpolated bare into rendered text — every occurrence is either a label-map key (the ONE
legitimate place the token appears, mapping FROM it), a `===` comparison choosing a Badge variant, or
plain-English prose.
