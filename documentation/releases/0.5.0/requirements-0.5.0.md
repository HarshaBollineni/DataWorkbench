# Archimedes 0.5.0 — Consolidated Requirements

**Status:** rev 1, rewritten 3 Aug 2026 from the raw feedback notes.
**Authority:** this document is the authority on *what* 0.5.0 must do. The executable plan
(build order, phases, test criteria) is written *from* this document and does not yet exist.
**Predecessor:** [requirements-0.4.0.md](../0.4.0/requirements-0.4.0.md) rev 5 · [archimedes-0.4.0-plan.md](../0.4.0/archimedes-0.4.0-plan.md) rev 4.

---

## Table of Contents

- [0. Preliminaries](#preliminaries)
  - [0.1 What this document is](#what-this-document-is)
  - [0.2 The shape of the release, in one picture](#the-shape-of-the-release)
  - [0.3 Source inventory](#source-inventory)
  - [0.4 Baseline — what 0.4.0 ships that 0.5.0 rests on, extends, or reverses](#baseline)
  - [0.5 ID scheme](#id-scheme)
- [1. The asset model (AST)](#asset-model)
  - [1.1 Vocabulary — fixed for the whole release](#vocabulary)
  - [1.2 Identity and naming](#identity-and-naming)
  - [1.3 Versions and snapshots](#versions-and-snapshots)
  - [1.4 The stored record](#stored-record)
  - [1.5 Comparing two versions](#comparing-two-versions)
  - [1.6 Metadata companions](#metadata-companions)
- [2. Data Sourcing landing page (SRC)](#landing-page)
- [3. The upload flow (UPL)](#upload-flow)
  - [3.0 The governing shape](#governing-shape)
  - [3.1 STEP 1 — Target](#step-1-target)
  - [3.2 STEP 2 — Upload the file](#step-2-upload)
  - [3.3 STEP 3 — Data type check](#step-3-data-type)
  - [3.4 STEP 4 — Intent and time period](#step-4-intent)
  - [3.5 STEP 5 — Schema conflict check](#step-5-schema-check)
  - [3.6 STEP 6 — Storage](#step-6-storage)
  - [3.7 STEP 7 — Output](#step-7-output)
- [4. Downstream consequences (CTX)](#downstream-consequences)
- [5. Reference data and taxonomy (TAX)](#taxonomy)
- [6. Admin and platform readability (ADM)](#admin)
- [7. Retirement of superseded code (RET)](#retirement)
- [8. Asset catalogue and usage analytics (ANL)](#catalogue-and-analytics)
- [9. Conflicts and resolutions](#conflicts)
  - [9.1 Resolved](#conflicts-resolved)
  - [9.2 Open — carried into §11](#conflicts-open)
- [10. Decision record](#decision-record)
- [11. Open questions for the requester](#open-questions)
- [12. Out of scope for 0.5.0](#out-of-scope)
- [13. Build sequence](#build-sequence)
- [14. Traceability](#traceability)
- [15. Functional code map (FNC)](#functional-code-map)
  - [15.1 What it covers today](#what-it-covers-today)

---

## 0. Preliminaries <a id="preliminaries"></a>

### 0.1 What this document is <a id="what-this-document-is"></a>

The raw 0.5.0 feedback arrived as eight loosely ordered notes (`Data_Sourcing_1` … `Data_Sourcing_6`,
an asset-catalogue brainstorm, an Admin defect, and one clarificatory question). Those notes overlap
each other, and three of them contradict requirements that 0.4.0 deliberately shipped. This rewrite
sequences them, merges the duplicates, resolves every contradiction explicitly in §9, and records
what could not be resolved without the requester in §11.

Nothing has been added that the notes did not ask for, with one exception: where a note says
*"Suggest what else"*, a proposal is written in full and marked `PROPOSED` so it can be accepted or
struck. Those are in §8 and §11.

### 0.2 The shape of the release, in one picture <a id="the-shape-of-the-release"></a>

0.4.0 made a single upload into a well-behaved pipeline (Drop → Review → Ready). 0.5.0 makes a
**dataset into a durable, versioned, reusable asset** that many uploads contribute to over time, and
that many workflows select from rather than recreate.

```
0.4.0 (today)                          0.5.0 (target)
─────────────                          ──────────────
Data Sourcing                          Data Sourcing
  └ 2 cards → type a free name           └ 2 cards, each with [View Existing] [Add New]
    └ drop file → profile → Ready             │
      └ Variable Inventory on landing         ├ View Existing → dropdown of active assets
                                              │    └ select → becomes the workflow context
Replace = a NEW item row, same "family"       │        └ "Go to Test Lab" carries it across
  → old item keeps its own name/results       │
  → dependent outputs still show the old      └ Add New  → Target · Upload · Types · Intent
                                                          · Schema check · Store · Summary
Test Lab auto-picks the first ready item                     │
                                                             ├ Fresh   → new dataset, v1
                                                             ├ Add period → +1 snapshot, same v
                                                             └ Replace → +1 version, old set
                                                                          superseded (retained)
```

The spine of the release is therefore the **asset model** (§1). Everything else — the landing page
(§2), the upload flow (§3), refresh behaviour (§4), the catalogue and usage analytics (§8) — is a
consequence of it, and must be built after it.

### 0.3 Source inventory <a id="source-inventory"></a>

| Raw note | Subject | Lands in |
|---|---|---|
| Clarificatory question | What can be deleted from the old API client | §7 (RET) |
| Bug — Admin full wipe | Reset message shows table names | §6 (ADM-01) |
| Brand new — Asset catalogue | Unique IDs, usage analytics, versioning, diffs, dictionary | §1 (AST), §8 (ANL), §11 (Q) |
| `Data_Sourcing_1` | Landing page: cards, View Existing dropdown, context handover | §2 (SRC) |
| `Data_Sourcing_2` | "Dataset Summary" header for rows/columns | §3 (UPL-06) — merged with `DS_6` STEP 2 |
| `Data_Sourcing_3` | Replace must refresh dependent outputs | §4 (CTX-01…03) |
| `Data_Sourcing_4` | Target Variable / Use Case default blank | §4 (CTX-04) |
| `Data_Sourcing_5` | Add CRE under Products | §5 (TAX-01) |
| `Data_Sourcing_6` | The seven-step upload flow | §1 (AST), §3 (UPL) |

Note: `Data_Sourcing_2` and `Data_Sourcing_6` STEP 2 are **the same requirement** stated twice. They
are merged into UPL-06 and not carried as two items.

### 0.4 Baseline — what 0.4.0 ships that 0.5.0 rests on, extends, or reverses <a id="baseline"></a>

Verified against the running product on 3 Aug 2026 (Playwright walkthrough, backend suite green at
361 passed / 1 skipped).

| 0.4.0 behaviour | Where | 0.5.0 disposition |
|---|---|---|
| Landing page: two cards, "Select to start →", then a full Variable Inventory per active item | [DataSourcing.jsx:543-563](../../../source-codes/ui/src/pages/DataSourcing.jsx#L543-L563) | **Reversed** — SRC-01, SRC-02 |
| Drop starts parse + profile immediately; no finalize/profile button (ING-01) | [DataSourcing.jsx:274-291](../../../source-codes/ui/src/pages/DataSourcing.jsx#L274-L291) | **Kept** — UPL-02 |
| Status machine `uploading → profiling → needs_review → ready \| failed`, derived not clicked (ING-07) | `backend/ingest/status.py` | **Kept and extended** — UPL-03, C-38 |
| Item names globally unique; duplicates rejected | [service.py:446-468](../../../source-codes/backend/ai/v2/service.py#L446-L468) | **Retired** — uniqueness moves to the system ID prefix (AST-02) |
| `item_id` generated as `item_<uuid4 hex[:12]>` — random, collision-free, no reset needed | [service.py:43-44](../../../source-codes/backend/ai/v2/service.py#L43-L44) | **Kept, untouched** — this is the internal primary key, not the new human-quotable ID; only the latter resets on factory reset (AST-01, D-29) |
| Both reset grades write their own audit row to `transaction_log` immediately after deleting | [admin.py:176-177](../../../source-codes/backend/routers/admin.py#L176-L177) | **Reused as the epoch boundary** that makes resetting the human-quotable ID safe (D-29) |
| Target defaults to `default_flag`; Use case defaults to `IFRS9` (ING-02) | [DataSourcing.jsx:176](../../../source-codes/ui/src/pages/DataSourcing.jsx#L176), [:203](../../../source-codes/ui/src/pages/DataSourcing.jsx#L203) | **Reversed** — CTX-04, C-41 |
| Replacement creates a **new item row** in the same family; the original is untouched (ING-09) | [service.py:482-507](../../../source-codes/backend/ai/v2/service.py#L482-L507) | **Re-shaped** — the new row becomes a *snapshot* of the same dataset, not a sibling item (AST-10, C-40) |
| `dq_items.dataset_family_id / delivery_seq / as_of_date / baseline_delivery_id` (PLT-02) | [system_db.py:766-767](../../../source-codes/backend/system_db.py#L766-L767) | **Reused as the physical seam** for dataset ↔ snapshot (AST-10) |
| Rows/columns shown inside the per-file upload progress card as a Tab/Rows/Columns table | [DataSourcing.jsx:418-433](../../../source-codes/ui/src/pages/DataSourcing.jsx#L418-L433) | **Moved** — UPL-06 |
| Test Lab auto-selects the first `ready` item | [TestLab.jsx:43-47](../../../source-codes/ui/src/pages/TestLab.jsx#L43-L47) | **Reversed for nav entry** — SRC-07 |
| Test Lab already has an item picker and a "Variable inventory of saved data" panel | [TestLab.jsx](../../../source-codes/ui/src/pages/TestLab.jsx) | **Kept** — SRC-06 deep-links into it |
| Taxonomy seeded by idempotent upsert on natural-key IDs | [taxonomy_seed.py:87-113](../../../source-codes/backend/seeds/taxonomy_seed.py#L87-L113) | **Kept** — makes TAX-01 additive, no migration |
| Reset messages render raw `table=count` pairs | [Admin.jsx:131-135](../../../source-codes/ui/src/pages/Admin.jsx#L131-L135) | **Replaced** — ADM-01 |

### 0.5 ID scheme <a id="id-scheme"></a>

| Prefix | Block |
|---|---|
| `AST-nn` | Asset model: identity, versions, snapshots, diffs (§1) |
| `SRC-nn` | Data Sourcing landing page and workflow context (§2) |
| `UPL-nn` | The upload flow (§3) |
| `CTX-nn` | Downstream refresh and dependent state (§4) |
| `TAX-nn` | Reference data and taxonomy (§5) |
| `ADM-nn` | Admin and platform readability (§6) |
| `RET-nn` | Retirement of superseded code (§7) |
| `ANL-nn` | Asset catalogue and usage analytics (§8) |
| `C-nn` | Conflict and its resolution (§9) — continues 0.4.0's series from C-38 |
| `D-nn` | Decision record (§10) — continues from D-23 |
| `Q-nn` | Open question for the requester (§11) |
| `OOS-nn` | Out of scope (§12) — continues from OOS-12 |
| `FNC-nn` | Functional code map — `functional_tools.xlsx` completeness (§15) |

**MUST** = in 0.5.0. **SHOULD** = in scope if it does not endanger a MUST. **LATER** = recorded and
deferred with a reason. **PROPOSED** = written in answer to a *"suggest what else"*; not yet accepted.

---

## 1. The asset model (AST) <a id="asset-model"></a>

> This section is the spine. It is written first because §2, §3, §4 and §8 are all unbuildable until
> the vocabulary below is fixed. `Data_Sourcing_1` and `Data_Sourcing_6` each describe a piece of it;
> this is the merged model.

### 1.1 Vocabulary — fixed for the whole release <a id="vocabulary"></a>

| Term | Meaning | Not to be confused with |
|---|---|---|
| **Asset** | A Database or a Dataset. The thing a user selects, reuses and cites. Has a stable ID and a stable name for its whole life. | An upload |
| **Snapshot** | The content of exactly one uploaded file. Immutable once stored. | A version |
| **Version** | A generation of the asset's **reference schema**. Bumps only when the schema is replaced. | A snapshot |
| **Reference schema** | The confirmed column names, column count and data types that later uploads are validated against. Belongs to the version. | The dictionary |
| **Active / superseded** | A snapshot's selectability. Superseded snapshots are retained for audit and rollback, never deleted. | Deleted |
| **Delivery** | 0.4.0's physical term for what 0.5.0 calls a snapshot. Same table rows (PLT-02). | — |

### 1.2 Identity and naming <a id="identity-and-naming"></a>

| ID | Priority | Requirement |
|---|---|---|
| AST-01 | MUST | **Every asset carries a system-generated, immutable, human-quotable ID**, distinct from its display name and never re-used while any live or superseded object can still reference it. The ID identifies the asset in logs, audit rows, reports, findings and RCA cases. **Exception: Admin's two factory-reset actions — surgical reset and full wipe — restart this ID's issuance from a clean baseline.** Both actions exist to give repeated demo/test cycles a clean slate; a human-quotable ID that only ever grows across months of resets defeats that purpose. See D-29 for why this is safe. (This is a new, user-facing ID — distinct from the internal `item_id` primary key, which is already a UUID fragment ([service.py:43](../../../source-codes/backend/ai/v2/service.py#L43)) and is untouched by this: it never needed a reset because it never collides.) |
| AST-02 | MUST | **Naming is `<SYSTEM-ID>-<user-alias>`.** The system suggests the alphanumeric `<SYSTEM-ID>` as a **read-only** prefix followed by a dash delimiter; the user types the alias after it. The alias accepts letters, digits, dashes and underscores only, and anything else is rejected **inline as it is typed**, not on submit. |
| AST-03 | MUST | **The system ID carries the uniqueness burden, not the alias.** Two users may both call an asset `retail-pd`; the full names differ by prefix. The current global name-uniqueness rejection ([service.py:446-468](../../../source-codes/backend/ai/v2/service.py#L446-L468)) and its landing-page pre-warning ([DataSourcing.jsx:197](../../../source-codes/ui/src/pages/DataSourcing.jsx#L197)) retire with it. |
| AST-04 | MUST | **The alias is editable; the ID and the prefix are not.** Renaming the alias never changes the ID, never breaks an existing reference, and is recorded as an audit event. |

### 1.3 Versions and snapshots <a id="versions-and-snapshots"></a>

| ID | Priority | Requirement |
|---|---|---|
| AST-05 | MUST | **One file per upload. One file = one snapshot.** Snapshots are stored separately and are **never merged or appended** to each other. |
| AST-06 | MUST | **A version is a reference-schema generation, not an upload counter.** Fresh Upload creates the asset at **version 1**. *Full replacement* creates version *n+1*. *Add period* does **not** bump the version. This is the single reading that satisfies both `Data_Sourcing_1` ("uploaded multiple times → a version") and `Data_Sourcing_6` ("Add period – no version bump") — see C-39. |
| AST-07 | MUST | **A version owns a reference schema** — the confirmed column names, column count and data type map of the snapshot that created it. Every later upload is validated against the *current* version's reference schema (UPL-13). |
| AST-08 | MUST | **A snapshot is `active` or `superseded`.** Superseded snapshots are **retained for audit and rollback, not deletable, not selectable in test configuration, and hidden from normal pickers**. Full replacement supersedes *all* previously active snapshots of the asset as one set. |
| AST-09 | MUST | **Superseded sets are restorable wholesale.** A "View / restore previous version" action reactivates a superseded version's snapshot set and makes its reference schema current again. Restoring is itself a version event and is audited. |
| AST-10 | MUST | **The physical seam is 0.4.0's PLT-02 delivery record, re-read — not a new one.** `dataset_family_id` becomes the asset identity, `delivery_seq` the snapshot sequence, `as_of_date` the snapshot's period anchor. What changes is the *semantics*: a re-upload is a snapshot of the same asset, no longer a sibling item with its own name and its own results (C-40). Migration is additive and idempotent per PLT-06. |
| AST-11 | MUST | **Ordering.** Snapshots order by period where dates exist, otherwise by upload timestamp — well-defined because AST-22 fixes a single time basis per asset; a set mixing dated and undated snapshots is impossible by construction, not merely avoided by convention. This ordering is what test configuration presents. |
| AST-12 | MUST | **Any active snapshot is selectable as reference or current in test configuration.** Period slicing *within* a snapshot happens in the test layer, never at upload. |

### 1.4 The stored record <a id="stored-record"></a>

| ID | Priority | Requirement |
|---|---|---|
| AST-13 | MUST | Each snapshot stores at least: `asset_id`, `asset_name`, `version_no`, `snapshot_id`, `snapshot_label`, `file_name`, `start_date`, `end_date`, `has_time_period`, `period_column`, `column_type_map`, `row_count`, `column_count`, `status (active/superseded)`, `intent`, `schema_override_flag`, `uploaded_by`, `uploaded_at`. |
| AST-14 | MUST | On Full replacement, the asset's reference schema is updated to the new snapshot's confirmed type map, and **the previous schema is retained against the superseded version** so AST-09 rollback is exact. |
| AST-15 | MUST | The 0.4.0 ingestion records — confirmed mapping, dictionary state, structured warnings, profile snapshot (ING-08) — continue to persist and continue to feed the Test Lab manifest. They now hang off the **snapshot**, not the item. |

### 1.5 Comparing two versions <a id="comparing-two-versions"></a>

> `Data_Sourcing`'s asset-catalogue note asks: *"He / she should know the exact differences between
> two versions. Suggest what constitutes the database or dataset differences and how will you measure
> such differences efficiently."* AST-16…AST-18 are the answer, and are `PROPOSED` pending Q-01.

| ID | Priority | Requirement |
|---|---|---|
| AST-16 | PROPOSED | **A difference has three tiers, and each is answerable without re-reading the data.** ① **Schema** — columns added, removed, likely-renamed, reordered, and type changes (`limit_amount: numeric → text`). ② **Shape** — row count, column count, period coverage, file size, snapshot count. ③ **Distribution** — per column: null rate, distinct count, min/max, mean and standard deviation for numerics, top-*k* categories for categoricals. |
| AST-17 | PROPOSED | **Efficiency comes from fingerprinting at ingest, never from row-by-row comparison at view time.** Each snapshot stores a small per-column fingerprint (the tier-③ statistics plus a fixed-bin histogram for numerics and a hash of the sorted distinct set for low-cardinality columns) computed once during the profiling pass that already runs. A version diff is then a fingerprint-to-fingerprint comparison — cost is O(columns), not O(rows), and is independent of dataset size. |
| AST-18 | PROPOSED | **Row-level diff is on demand and conditional.** It runs only when the user asks *and* a key column is declared, and reports key sets added / removed / changed using stored row digests. It is never the default view, because it is the only O(rows) operation in the model. |
| AST-19 | SHOULD | The diff is reachable from anywhere two versions are visible — the catalogue, the View Existing dropdown, and the restore-previous-version screen — and renders the same three tiers in the same order everywhere. |

### 1.6 Metadata companions <a id="metadata-companions"></a>

> The note asks: *"should a data asset come along with metadata like data dictionary? Pros: good from
> reuse. Cons: there will be a separate version."*

| ID | Priority | Requirement |
|---|---|---|
| AST-20 | PROPOSED | **Yes — and the "separate version" objection dissolves by binding it at the right level.** The dictionary belongs to the **asset**, not to the snapshot, and is versioned on its own track. Each snapshot records *which* dictionary version it was ingested against. Editing a dictionary therefore never forces a data version bump, and adding a snapshot never invalidates the dictionary. This keeps the reuse benefit and removes the cost. Confirmation needed — Q-02. |
| AST-21 | SHOULD | The existing `dictionary_state` of `yes / thin / absent` (ING-05) is a property of the **binding** of a dictionary version to a snapshot, and remains visible wherever the asset appears. |
| AST-22 | MUST | **Every asset has a single, fixed time basis for its whole life: period-based or no-time-period** (UPL-14). It is chosen once, at Fresh Upload, and is immutable afterward — including across a Full replacement, which changes the reference schema (AST-06) but never the time basis. Add period and Full replacement therefore never re-ask the STEP 4 mode question; they collect only whichever fields the asset's fixed basis requires (Start/End dates, or a unique snapshot label). This exists because AST-11's ordering rule has no defined answer for a set that mixes dated and undated snapshots — fixing the basis at creation makes the mixed case impossible rather than merely unhandled. Surfaced on review, 3 Aug 2026 — see C-48/D-30. |

---

## 2. Data Sourcing landing page (SRC) <a id="landing-page"></a>

> Source: `Data_Sourcing_1`. Expected outcome, in the requester's words: *"Users will no longer see
> the Variable Inventory directly on the Landing Page. Instead, they must select an existing active
> Database/Dataset through the 'View Existing' option, and the selected item will be used for all
> subsequent workflow steps."*

| ID | Priority | Requirement |
|---|---|---|
| SRC-01 | MUST | **The Variable Inventory is not on the landing page.** The "Active workflow variable inventory" block ([DataSourcing.jsx:558-563](../../../source-codes/ui/src/pages/DataSourcing.jsx#L558-L563)) is removed. It survives in two places that already exist: inside the upload flow's own review surface, and in the Test Lab's "Variable inventory of saved data" panel. |
| SRC-02 | MUST | **Both cards change identically.** "Database Upload" → **"Database"**; "Dataset Upload" → **"Dataset"**. The "Select to start →" affordance is deleted. Each card gains two links: **"View Existing"** and **"Add New"**. |
| SRC-03 | MUST | **"View Existing" opens a compact dropdown**, not a page navigation. |
| SRC-04 | MUST | **The landing page's "View Existing" dropdown lists assets that are still in play, and excludes completed ones.** An asset is excluded when its lifecycle status is `complete`, `archived` or `requires_reupload`. Superseded snapshots never appear (AST-08). This exclusion is scoped to this landing-page entry point only — SRC-13 carves out the upload flow's use of the same component. |
| SRC-05 | MUST | **Each row shows: full name `<SYSTEM-ID>-<alias>`, version, current status, snapshot count, last upload date, and a column count that fits the kind.** For a **Dataset**, that is the table's column count. For a **Database**, a single column count is meaningless once an asset spans more than one table — show **table count plus total columns across all tables** instead. This is the union of what `Data_Sourcing_1` and `Data_Sourcing_6` STEP 1(b) each asked for; it is **one component with one data source**, used from both entry points (C-42), though its exclusion filter is not identical at both — see SRC-13. |
| SRC-06 | MUST | **The dropdown is searchable** and, when reached from the upload flow's STEP 1(b), **selection is mandatory to continue**. |
| SRC-07 | MUST | **Status is shown for every listed asset, but only assets with at least one `ready` active snapshot are selectable.** A still-ingesting asset is listed with its live status and disabled, so the user can see it is coming without being able to pick something the Test Lab cannot consume (ING-07 holds). Resolution of C-43. |
| SRC-08 | MUST | **Selecting an asset sets the workflow context** — the selected asset (and, where relevant, the selected snapshot) is what every subsequent step of the workflow operates on. |
| SRC-09 | MUST | **The active context is visible and changeable** on the Data Sourcing screen: which asset, which version, how many active snapshots, and a way to clear or change it. A user must never be able to act on a context they cannot see. |
| SRC-10 | MUST | **A "Go to Test Lab" link sits at the bottom of the Data Sourcing screen** and opens the Test Lab **with the selected asset already chosen**. |
| SRC-11 | MUST | **Entering the Test Lab from the left-hand navigation opens it with nothing selected**, and the user must choose from its own picker. The current auto-selection of the first ready item ([TestLab.jsx:43-47](../../../source-codes/ui/src/pages/TestLab.jsx#L43-L47)) is removed for that entry path only. |
| SRC-12 | MUST | **"Add New" starts the upload flow of §3** for that card's kind. |
| SRC-13 | MUST | **The upload flow's STEP 1(b) "Existing" picker (UPL-05) is the same component as "View Existing" (SRC-05/06, C-42), with one deliberate difference: it does not apply SRC-04's `requires_reupload` exclusion.** It is the only path back to a `requires_reupload` asset, since dropping a new file against it is precisely how that status is resolved. STEP 1(b) additionally flags a `requires_reupload` row (e.g. a "Needs re-upload" badge) so its presence there is self-explanatory. Surfaced on review, 3 Aug 2026 — resolution of C-49/see D-31. |

---

## 3. The upload flow (UPL) <a id="upload-flow"></a>

> Source: `Data_Sourcing_6`, plus `Data_Sourcing_2` merged into UPL-06. The trigger is `Data_Sourcing_1`'s
> **"Add New"** link; the note's phrase *"Ready to Start"* names the same action and is not carried
> forward as a second label (C-44).

### 3.0 The governing shape <a id="governing-shape"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-01 | MUST | **The seven moments below are sections of one progressive surface, not a seven-screen wizard.** 0.4.0 retired the wizard deliberately (D-16, D-20) and that decision stands. Sections reveal as their inputs become available; the user can move back to any earlier section without losing work. |
| UPL-02 | MUST | **No button advances the pipeline.** Dropping the file still starts parsing and profiling immediately; there is still no "Upload", "Finalize" or "Profile" button (ING-01 holds unchanged). |
| UPL-03 | MUST | **Status stays derived, never set by a click** (ING-07 holds). The machine extends to `uploading → profiling → needs_review → ready \| failed`, where **`needs_review` now also covers "profiled, but a required confirmation is outstanding"**. `ready` is reached when every outstanding confirmation has been recorded — the confirmation is the fact; the status is read from it. This is how UPL-14's "Process" and UPL-12's replacement warning coexist with the buttonless rule: they commit *decisions with consequences*, they do not advance a pipeline. See C-38 and D-23. |
| UPL-04 | MUST | **Backing out never destroys work.** If a user leaves at any point after the file has landed, the file and the confirmed type map stay **staged** — never force a re-upload. |

### 3.1 STEP 1 — Target <a id="step-1-target"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-05 | MUST | **Two targets, chosen first.** ① **Fresh Upload** — creates a new asset, named per AST-02, assigned version 1; this is also where the asset's time basis is set (AST-22). ② **Existing** — the mandatory searchable dropdown of SRC-05/SRC-06, **including `requires_reupload` assets** (SRC-13); selection is required to continue. |

### 3.2 STEP 2 — Upload the file <a id="step-2-upload"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-06 | MUST | **One file picker (`.csv` / `.xlsx`), a progress indicator, then a preview under its own "Dataset Summary" heading** carrying row count and column count. This heading exists to separate *upload actions* from *dataset facts*; the counts move out of the per-file progress card where they sit today ([DataSourcing.jsx:418-433](../../../source-codes/ui/src/pages/DataSourcing.jsx#L418-L433)). Merges `Data_Sourcing_2` with `Data_Sourcing_6` STEP 2. |
| UPL-07 | MUST | **Blocking checks at this step are structural only**: file present, readable, non-empty, has a header row. Nothing about content blocks here. |
| UPL-08 | SHOULD | For a **Database** (multi-table workbook), Dataset Summary lists per-table row and column counts plus a total, and the label reads "Database Summary". Pending Q-03. |

### 3.3 STEP 3 — Data type check <a id="step-3-data-type"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-09 | MUST | **Infer the data type of every column** — numeric, date, categorical, boolean, text — using generic signals only. ING-10's ban on hardcoded schema literals stands. |
| UPL-10 | MUST | **A review table shows column name, inferred type, sample values, null count and distinct count, and the user can override any inferred type from a dropdown.** This table *is* the Variable Inventory surface, not a second one beside it. |
| UPL-11 | MUST | **Warn, do not block**: fully null columns; mixed-type columns; and columns where more than 10% of values fail to parse as the inferred type. Warnings use the existing structured `{column, code, message}` shape (ING-04). |
| UPL-12 | MUST | **Block**: blank column names, and duplicate column names **within the same table**. Both break downstream test configuration, so neither may be waved through. For a Database asset this uniqueness is checked per table, not across the whole workbook — the same column name recurring in different tables (e.g. `id` in both `loans` and `collateral`) is normal and must never trigger this block. Scope stated explicitly on review, 3 Aug 2026. |
| UPL-13 | MUST | **The confirmed type map is stored with the snapshot.** For a Fresh Upload it becomes the asset's reference schema (AST-07). |

### 3.4 STEP 4 — Intent and time period <a id="step-4-intent"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-14 | MUST | **Time period the data represents, one of two modes, chosen ONCE per asset, on Fresh Upload.** ① **Period-based** — Start Date and End Date, mandatory together, blocked if Start > End. A snapshot spanning many periods (e.g. Jan–Dec) is expected and correct. ② **No time period** (reference tables, mapping tables, one-off extracts) — the date fields are hidden and **Snapshot label becomes mandatory and unique within the asset**. Whichever is chosen becomes the asset's fixed time basis (AST-22). On **Add period** or **Full replacement** this mode is never re-offered — STEP 4 simply presents the fields the asset's existing basis requires. |
| UPL-15 | MUST | **Snapshot label is free text**, defaulting to the period range when dates are given. |
| UPL-16 | MUST | **Reporting period column is an optional dropdown listing every column in the file, unfiltered.** It is stored as metadata only. **No split happens at upload** — the test layer uses it to slice the snapshot into comparison windows. |
| UPL-17 | MUST | **Intent applies to existing assets only and is skipped for a Fresh Upload.** ① **Add period** — adds a snapshot alongside the existing ones; no version bump. ② **Full replacement** — this file becomes the source of truth; all existing snapshots are superseded per AST-08. |
| UPL-18 | MUST | **Full replacement requires an explicit, informed confirmation.** Before confirming, show **how many snapshots and which periods or labels will be superseded**. The confirmation is a distinct act, not a side effect of pressing Continue. |
| UPL-19 | MUST | **"View / restore previous version" is available** wherever a superseded set exists, and reactivates that set wholesale (AST-09). |

### 3.5 STEP 5 — Schema conflict check <a id="step-5-schema-check"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-20 | MUST | **The check runs for BOTH intents** — Add period and Full replacement alike — comparing the uploaded file against the asset's current reference schema: same column names, same column count, same data types. |
| UPL-21 | MUST | **On mismatch: warn, never block.** Show the exact difference — missing columns, extra columns, renamed columns, and type changes rendered as `limit_amount: numeric → text`. |
| UPL-29 | MUST | **For a Database asset, the schema conflict check also compares the table set, not only columns within matching tables.** A table present in the reference schema but absent from the new file, and a table in the new file with no counterpart in the reference schema, are each reported with their own vocabulary — **"table removed"** / **"table added"** — using the same warn-don't-block pattern as UPL-21, because UPL-21's column-level vocabulary has no way to express a whole table appearing or disappearing. Column-level comparison per UPL-21 still runs within every table that exists on both sides. Gap closed on review, 3 Aug 2026. |
| UPL-22 | MUST | **"Process" stays disabled until the user ticks an explicit confirmation**, and the override is logged on the snapshot (`schema_override_flag`, AST-13). |
| UPL-23 | MUST | **The message must state which consequence applies.** For **Add period**: *"This snapshot will not be comparable to existing snapshots on the differing columns. Tests such as PSI are column-wise and will skip or fail on them."* For **Full replacement**: *"This will REPLACE the dataset's reference schema. All future uploads will be validated against the new schema, and existing test configurations referencing removed or retyped columns will break."* — **and it must list the affected test configurations by name** when any exist. |
| UPL-24 | MUST | **Add period also warns on period overlap** with an existing active snapshot, naming the overlapping snapshot(s) and period(s). Warning only; it does not block. |
| UPL-25 | MUST | **Backing out here keeps the file and the confirmed type map staged** (UPL-04). |

### 3.6 STEP 6 — Storage <a id="step-6-storage"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-26 | MUST | Storage behaves exactly as AST-05, AST-08, AST-11, AST-13 and AST-14 specify. No storage rule is stated twice; §1 is the authority. |

### 3.7 STEP 7 — Output <a id="step-7-output"></a>

| ID | Priority | Requirement |
|---|---|---|
| UPL-27 | MUST | **A completion summary** covering: asset name, snapshot label, period covered (if any), rows loaded, columns and their confirmed types, schema change applied (if any), snapshots superseded (if a full replacement), and **every warning that was overridden**. |
| UPL-28 | SHOULD | The completion summary is retrievable later from the asset catalogue, not only in the moment. An upload that cannot be explained a week later is not auditable. |

---

## 4. Downstream consequences (CTX) <a id="downstream-consequences"></a>

> Source: `Data_Sourcing_3` and `Data_Sourcing_4`. These are the two notes that describe *defects
> caused by* the old replacement model, and they are the reason AST-10 re-shapes it.

| ID | Priority | Requirement |
|---|---|---|
| CTX-01 | MUST | **When the active snapshot set changes, everything derived from it refreshes.** Target Variable, Business Tags, profiling results and related analyses must recalculate against the new active set. The current behaviour — dependent sections continuing to display the previous dataset's name and figures — is a defect and is corrected, not worked around. |
| CTX-02 | MUST | **Derived artefacts bound to a superseded snapshot are marked stale, never silently shown as current.** A stale artefact states which snapshot it came from and offers to recompute. |
| CTX-03 | SHOULD | A **"Refresh"** control lets the user force recomputation of all asset-dependent information at any time. The note offered this as an *alternative* to CTX-01; it is accepted as a **complement** — CTX-01 makes the automatic case correct, and CTX-03 covers the case where an external input changed. |
| CTX-04 | MUST | **Target Variable and Use Case default to blank** on a Fresh Upload and on a Full replacement. Values from a previously loaded dataset are never carried forward automatically. This reverses today's defaults of `default_flag` and `IFRS9` ([DataSourcing.jsx:176](../../../source-codes/ui/src/pages/DataSourcing.jsx#L176), [:203](../../../source-codes/ui/src/pages/DataSourcing.jsx#L203)). |
| CTX-05 | MUST | **Blank does not block Ready — it blocks the tests that need it.** ING-02's rule that target and use case are never an ingestion gate stands; a missing target instead surfaces at the Test Lab scope gate, against the specific diagnostics that require one. This is where the absence actually matters, and it is where the user can act on it. Resolution of C-41. |
| CTX-06 | MUST | **Add period carries target and use case forward**, because it is the same asset with the same reference schema. Only Fresh Upload and Full replacement clear them (D-24). |

---

## 5. Reference data and taxonomy (TAX) <a id="taxonomy"></a>

| ID | Priority | Requirement |
|---|---|---|
| TAX-01 | MUST | **Add `CRE` (Commercial Real Estate) to the Product dimension.** The dimension currently holds Mortgage, Auto, Credit card, Personal loan, Working capital, Trade finance, Term deposits and Non-maturity deposits ([taxonomy_seed.py:27-32](../../../source-codes/backend/seeds/taxonomy_seed.py#L27-L32)). The seeder upserts on natural-key IDs, so this is purely additive: no migration, no taxonomy version bump, and it survives a full wipe because the wipe re-seeds. |

---

## 6. Admin and platform readability (ADM) <a id="admin"></a>

> Source: the Admin defect note. Observed message: *"Full wipe complete. Removed: dq_items=2,
> dq_item_files=7, dq_item_tables=2, variable_inventory=48, dq_item_mappings=48, tag_assignments=3,
> object_contexts=25, kb_retrieval_manifests=5, agent_skills=14, …"*

| ID | Priority | Requirement |
|---|---|---|
| ADM-01 | MUST | **Reset results are reported in business language, never in table names.** No database identifier appears in any user-facing reset message. |
| ADM-02 | MUST | **Counts are grouped into what the user recognises**, in this order: *your work* (assets, uploaded files, variables, tags, issues, RCA cases, knowledge-base documents), then *platform reference data* — which is stated as **removed and immediately re-seeded**, because it is, and a user reading "removed 14 agent skills" with no further explanation reasonably concludes the product is now broken. |
| ADM-03 | MUST | **Zero counts stay hidden**, as they already are, and a wholly empty result still reads as a sentence rather than a fragment. |
| ADM-04 | MUST | **The fix is applied once, in the shared helper** ([Admin.jsx:131-135](../../../source-codes/ui/src/pages/Admin.jsx#L131-L135)), so the surgical reset message ([Admin.jsx:346](../../../source-codes/ui/src/pages/Admin.jsx#L346)) and the full wipe message ([Admin.jsx:389](../../../source-codes/ui/src/pages/Admin.jsx#L389)) both improve. Any table that gains a count later must fail loudly if it has no human label, rather than leaking its name into the UI. |
| ADM-05 | SHOULD | The same rule applies to every other place raw counts surface to a user. The retained-data notice on the same screen already renders `Object.entries(...)` pairs and is covered by ADM-01. |
| ADM-06 | MUST | **Admin gains a version history view.** For any asset, show its full version sequence (AST-06 schema generations) and, within each version, its snapshots — active and superseded (AST-08) — each with timestamp, actor, intent, and a one-line summary of what changed (schema change applied, snapshots superseded, or none). This is the audit-facing counterpart to the catalogue's version diff (§1.5, ANL-06): the catalogue answers *what's different between two versions*; Admin's view answers *what happened, when, and by whom*, across the asset's whole life. Requested directly, 3 Aug 2026. |
| ADM-07 | SHOULD | **Admin's version history also lists the two factory-reset actions themselves** — surgical reset and full wipe — each with timestamp and the deleted/re-seeded counts already shown inline (ADM-01…04). D-29 relies on the reset's own `transaction_log` row as an "epoch boundary" for the ID space; surfacing that row in this view, not only via the API, is what makes D-29 legible to a human rather than merely correct in principle. See Q-06 for how far this list should extend. |

---

## 7. Retirement of superseded code (RET) <a id="retirement"></a>

> Source: the clarificatory question — *"The old /ingestion/... functions still exist in the API
> client, but the current DataSourcing.jsx uses only the /api/v2/items/... APIs. What does it mean?
> What from the old code can be deleted?"*

**What it means.** It means the client kept its old address book after the houses were demolished.
0.4.0 replaced the Data Sourcing backend with `/api/v2/items/*` and removed the routers that served
`/ingestion/*` and `/datasource/*`. The frontend helper functions that called them were never
deleted. Verified 3 Aug 2026 against the running backend: **79 routes exist**, mounted from exactly
four routers — `auth`, `admin`, `v2`, `v3` ([main.py:156-159](../../../source-codes/backend/main.py#L156-L159)).
There is no `/ingestion` route, no `/datasource` route, no `/tables`, no `/inventory`, no legacy
`/rca/*`, no `/tickets`, no `/skills`. Every one of those client functions is a call into nothing:
dead on the first line, not merely unused.

| ID | Priority | Requirement |
|---|---|---|
| RET-01 | MUST | **Delete the API-client functions that call routes which no longer exist.** Measured: **74 of the 153 exports** in [ui/src/api/client.js](../../../source-codes/ui/src/api/client.js) have no caller anywhere in `ui/src`; **65 of those 74 also target a route that is absent from the live OpenAPI surface**. Those 65 go. |
| RET-02 | MUST | **The remaining 9 unused-but-live exports are decided individually, not swept.** They are `uploadItemFileV2`, `patchItemV2`, `createIssueAnalysesV2`, `interpretIssueAnalysesV2`, `getFrameworkTestsV2`, `getAgentsV2`, `getResultTagsV3`, `getTestTagsV3`, and the local helper `setToken`. Each is either wired up, deleted, or kept with a recorded keep-reason — never left ambiguous. |
| RET-03 | MUST | **Delete the orphaned UI components left by the retired wizard.** Measured: **14 files under `ui/src` are referenced by nothing** — `AgentOrgChart.jsx`, `AgentTree.jsx`, `Erd/ErdPanel.jsx`, `ExecutionSwimlane.jsx`, `InfoHint.jsx`, `LimitedMarkdown.jsx`, `MarkdownEditor.jsx`, `PythonCodeBlock.jsx`, `TableDashboardDialog.jsx`, `WizardLayout.jsx`, `ui/dropdown-menu.jsx`, `ui/separator.jsx`, `ui/tabs.jsx`, `lib/appConfig.js`. Same rule as RET-02: delete, or keep with a reason. |
| RET-04 | MUST | **A frontend reachability test bites, mirroring the backend's.** `backend/tests/test_reachability.py` already fails the build when an unmounted router file appears; the frontend has no equivalent, which is why 74 dead exports accumulated unnoticed. The new test fails on an export with no caller and no recorded keep-reason. |
| RET-05 | SHOULD | **Fix the stale references in the AI helper layer while the ground is open.** `ai/tool_registry.py:206-207` still documents `routers.ingestion` as its data source. PLT-08 named this a standing defect in 0.4.0; it is still there. |
| RET-06 | MUST | **Deletion is evidence-led, one tranche, with the suite green before and after.** Baseline recorded 3 Aug 2026: 361 passed, 1 skipped. |

---

## 8. Asset catalogue and usage analytics (ANL) <a id="catalogue-and-analytics"></a>

> Source: the asset-catalogue note, which asks for unique IDs on everything and for usage analysis,
> twice ending in *"Suggest what else."* AST-01…AST-04 cover asset identity. This section covers the
> rest of the object graph and the measurement layer. Everything here is `PROPOSED` — it is the
> answer to a brainstorm, not yet an accepted scope.

| ID | Priority | Requirement |
|---|---|---|
| ANL-01 | PROPOSED | **Every addressable object carries a stable, typed, never-reused ID**, formed the same way across the product. The note lists database, dataset, tests, diagnostics, test plan, diagnostics in test plans, diagnostic outputs and test outputs. To that add, because each is already independently citable in the product: **snapshot, version, dictionary version, variable (asset + column), tag assignment, knowledge-base document, KB document version, KB rule, issue, RCA case, RCA hypothesis, fix proposal, run, finding, report, and user**. |
| ANL-02 | PROPOSED | **An ID is stable for the life of the object it names** — never re-issued to a *different* object while that object or anything derived from it still exists. Re-use within a live product would silently corrupt every historical usage record. This carries AST-01's factory-reset exception: a surgical reset or full wipe removes the object itself, so the ID it freed is no longer "in use" and issuance may restart clean (D-29). |
| ANL-03 | PROPOSED | **One append-only usage event log, written by every module**, is the single source for all analysis. Each event carries: event type, actor, timestamp, the object's typed ID, and the workflow context it happened in. Analytics are computed *from* this log, never from the live tables, so a deleted asset does not erase its own history. |
| ANL-04 | PROPOSED | **The measures the note asks for, and what they need.** Overall usage, usage per user, usage over time, test preferences, diagnostic preferences and use-case frequency all fall out of ANL-03 given the event types are chosen deliberately rather than added ad hoc. |
| ANL-05 | PROPOSED | **What else is worth capturing** — the answer to *"Suggest what else"*: reuse depth (how often an existing asset is selected versus a new one created), time-to-ready per upload, override rate (how often users overrule an inferred type or a schema warning — the honest measure of whether inference is trusted), abandonment points in the upload flow, snapshot cadence per asset, dictionary coverage trend, finding disposition split (confirmed versus dismissed, per diagnostic), rerun rate after a replacement, and superseded-set restore frequency. The last is the direct measure of whether Full replacement is being used correctly. |
| ANL-06 | PROPOSED | **The catalogue is a first-class screen**: every asset, its versions, its active and superseded snapshots, its dictionary binding, its usage summary, and a link into the version diff of AST-16. |
| ANL-07 | LATER | Analytics **reporting** surfaces — dashboards, trend charts, exports. The note says *"I want to track and analyse **in future**"*. 0.5.0 must therefore guarantee the data is captured correctly and completely; presenting it can follow. Capturing it late is unrecoverable, presenting it late is not. |

---

## 9. Conflicts and resolutions <a id="conflicts"></a>

### 9.1 Resolved <a id="conflicts-resolved"></a>

| ID | Conflict | Resolution |
|---|---|---|
| C-38 | `Data_Sourcing_6` STEP 5 requires a **"Process" button** and STEP 4 requires an explicit replacement confirmation. 0.4.0's **ING-01/ING-07** forbid a button per pipeline step and require status to be derived, "never a button". | **Both hold, because they govern different things.** The buttonless rule targets *pipeline progression* — no user should have to press "profile" to make profiling happen. UPL-22's Process and UPL-18's confirmation commit *irreversible decisions*, which ING-06 already locates on the Review surface. Status remains derived: it is read from whether the confirmation record exists, not set by the click. Recorded as **D-23**. |
| C-39 | `Data_Sourcing_1` says an asset uploaded multiple times "will come with a version". `Data_Sourcing_6` says **Add period does not bump the version**. Taken literally, every upload both is and is not a version. | **A version is a reference-schema generation** (AST-06). Fresh = v1; Full replacement = v*n+1*; Add period = same version, one more snapshot. `Data_Sourcing_1` was written before Add period existed as a distinct intent; under the merged model its statement is true of the replacement case, which is what "re-uploading the same asset" now means. |
| C-40 | 0.4.0's **ING-09**: replacement is "a new delivery, **never mutation**" — a new item row, the original untouched. `Data_Sourcing_6`: replacement supersedes existing snapshots **within the same dataset**, changing what that dataset resolves to. | **Immutability moves down one level, where it belongs.** Snapshots are immutable and permanently retained — superseding is a status change, not a deletion or an edit, so ING-09's guarantee is intact. What changes is that the *asset identity* stays stable instead of forking into a sibling item with its own name and its own results. That fork is precisely the cause of the `Data_Sourcing_3` defect. Physical seam unchanged (AST-10). Recorded as **D-25**. |
| C-41 | `Data_Sourcing_4` requires target and use case to be **blank and explicitly chosen**. 0.4.0's **ING-02** requires them **defaulted and never a gate**. | **Blank wins on the default; never-a-gate wins on the gating.** Both fields start empty (CTX-04) and neither blocks Ready; a missing target instead blocks the specific Test Lab diagnostics that need one, at the scope gate (CTX-05). The note's word "required" is satisfied where the requirement is real. |
| C-42 | Two dropdowns are specified with different columns: `Data_Sourcing_1`'s (name, version, status) and `Data_Sourcing_6` STEP 1(b)'s (name, column count, snapshot count, last upload date). | **One component, one data source, the union of the columns** (SRC-05), rendered from two entry points. Two pickers over the same data would drift within one release. |
| C-43 | `Data_Sourcing_1` says show only records that are **"Uploaded and In Progress"** and hide **"Completed"** — a third status vocabulary. The product already runs two: lifecycle (`sourcing`, `profiled`, `testlab_step1…4`, `complete`, `issues`, `requires_reupload`) and ingest (`uploading`, `profiling`, `needs_review`, `ready`, `failed`). | **No third vocabulary is introduced.** "Completed" maps to the existing lifecycle `complete` (plus `archived` / `requires_reupload`), which is exactly the split the Data Inventory screen already draws. "Uploaded / In Progress" is a *listing* concern: everything not completed is listed with its live status, and only assets with a `ready` active snapshot are selectable (SRC-07). |
| C-44 | `Data_Sourcing_6` triggers the flow from **"Ready to Start"**; `Data_Sourcing_1` names the trigger **"Add New"**. Neither string exists in the product today. | **"Add New"**, per `Data_Sourcing_1`, which is the more specific landing-page specification. "Ready to Start" is recorded here as a synonym so the note remains traceable, and is not shipped as a second label. |
| C-45 | `Data_Sourcing_3` offers a manual **"Refresh"** button as an *alternative* to automatic recalculation. | Taken as a **complement, not an alternative** (CTX-03). A manual refresh alone leaves the defect in place for every user who does not press it. |
| C-46 | `Data_Sourcing_1` removes the Variable Inventory from the landing page; 0.4.0 put it there deliberately as accepted feedback. | **Reversal accepted** (SRC-01). The inventory is not lost: it lives in the upload flow's type-review surface (UPL-10) and in the Test Lab panel that already exists. Recorded as **D-26**. |
| C-48 | As first drafted, `Data_Sourcing_6` STEP 4 offered the period-based/no-time-period choice on every upload, while AST-11's ordering rule ("by period where dates exist, otherwise by upload timestamp") has no defined answer if one asset's snapshots mix both. A per-upload choice and a per-asset ordering guarantee cannot both hold. | **Time basis becomes a fixed asset-level property, set once at Fresh Upload and immutable thereafter — even across Full replacement** (AST-22). This removes the mixed case by construction instead of asking the ordering rule to somehow interleave dated and undated snapshots. Surfaced on review, 3 Aug 2026, not from a raw note. Recorded as **D-30**. |
| C-49 | As first drafted, SRC-04 excluded `requires_reupload` assets from the dropdown, while UPL-05's "Existing" target requires selecting from that *same* dropdown (C-42) to attach a new file to an existing asset — and `requires_reupload` names exactly the asset a user would need to reach that way. A user told to re-upload had no path back to the asset. | **The exclusion is scoped to the entry point, not the component.** One dropdown, two call sites: the landing page's browse-to-continue view keeps the exclusion (there is nothing usable to continue with); the upload flow's attach-a-file view drops it, because attaching a file is precisely the resolution (SRC-13). Surfaced on review, 3 Aug 2026. Recorded as **D-31**. |

### 9.2 Open — carried into §11 <a id="conflicts-open"></a>

| ID | Conflict | Why it is not closed here |
|---|---|---|
| C-47 | `Data_Sourcing_6` is written entirely in Dataset language ("one file", "column count", "reference schema"), but `Data_Sourcing_1` applies the same changes to the **Database** card, where one upload is a multi-table workbook with an optional schema file. | Whether a database snapshot is one workbook or one table per snapshot changes the storage record, the diff model and the schema check. Requester input needed — **Q-03**. |

---

## 10. Decision record <a id="decision-record"></a>

| ID | Decision |
|---|---|
| D-23 | **The buttonless rule (ING-01/07) governs pipeline progression, not irreversible decisions.** Profiling, parsing and status transitions are never triggered by a click; schema overrides and full replacements always are. Status stays derived by being read from the confirmation record rather than set by the button that wrote it. |
| D-24 | **Target and use case clear on Fresh Upload and Full replacement, and carry forward on Add period.** Add period is the same asset with the same reference schema, so clearing them would be a per-snapshot re-entry tax with no safety benefit. |
| D-25 | **Asset identity is stable across replacement; snapshots carry the immutability.** This supersedes 0.4.0's "replacement = a new item row" shape while preserving ING-09's guarantee at the snapshot level. |
| D-26 | **The Variable Inventory leaves the Data Sourcing landing page**, reversing accepted 0.4.0 feedback, because the landing page's job in 0.5.0 is asset selection rather than column review. |
| D-27 | **0.5.0 captures usage data; it does not yet present it** (ANL-07). Uncaptured history cannot be reconstructed; unpresented history can be presented later. |
| D-28 | **`requirements-0.5.0.md` is the authority on *what*.** The executable plan is derived from it and does not exist yet. |
| D-29 | **Factory reset (surgical or full wipe) restarts the human-quotable asset ID from a clean baseline** (AST-01, ANL-02), even though the audit trail survives both reset grades. This is safe without disambiguating the ID itself (an epoch prefix, a global counter that never resets) because **the reset already writes its own row into `transaction_log` immediately after the delete** ([admin.py:176-177](../../../source-codes/backend/routers/admin.py#L176-L177): "Audit AFTER the delete … so the wipe grade's own audit row … survives it"). That row is the epoch boundary: any ID appearing in `transaction_log` after it belongs to the post-reset generation, any ID before it belongs to the one just wiped. Solving this any other way would add ID complexity for a tool whose reset exists to give repeated demo/test cycles a clean slate, not to preserve forensic continuity across a deliberate wipe. |
| D-30 | **Time basis is fixed once per asset, at Fresh Upload, and never revisited — not even by Full replacement**, even though replacement changes the reference schema (AST-06/AST-22). If the underlying data's time nature genuinely changes (e.g. a reference table starts being delivered period-by-period), the correct action is a new asset, not a basis change on the existing one: changing it in place would require deciding how to order or relate the old undated snapshots to the new dated ones — exactly the ambiguity AST-22 exists to remove. |
| D-31 | **`requires_reupload` assets are excluded from the landing-page picker but included in the upload flow's STEP 1(b) picker**, even though both are the same component (C-42). The component's exclusion filter is parameterized by which entry point is using it, not fixed once for the component as a whole. |

---

## 11. Open questions for the requester <a id="open-questions"></a>

These block or reshape specific requirements and cannot be answered from the codebase.

| ID | Question | Blocks | Recommendation if no answer arrives |
|---|---|---|---|
| Q-01 | Is the three-tier version diff of AST-16…AST-18 the right definition of "the exact differences between two versions"? In particular, is a **distribution** difference (a column whose values shifted without its type changing) something you want reported, or is schema + shape enough? | AST-16…AST-19 | Build tiers ① and ② as MUST and tier ③ as SHOULD. Tier ③ costs almost nothing extra because the profiling pass already computes most of it. |
| Q-02 | Should the data dictionary version independently of the data, as AST-20 proposes? | AST-20, AST-21 | Yes. It is the only binding that gets the reuse benefit without the version-coupling cost you flagged. |
| Q-03 | For a **Database** upload, is one snapshot one workbook (all tables together) or one table? | C-47, UPL-08, and the whole storage record for databases | One workbook = one snapshot, with per-table detail inside it. It matches "one file = one snapshot" and preserves cross-table relationships, which per-table snapshots would sever. |
| Q-04 | ANL-01 lists 16 further object types beyond the ones you named. Is the whole list in scope for 0.5.0, or should identity land only for the asset graph (asset, version, snapshot, variable, dictionary) with the rest following? | ANL-01, ANL-02 | Asset graph in 0.5.0; the rest as each module is next touched. A partial ID scheme is still coherent; a rushed complete one is not. |
| Q-05 | Is `CRE` to be added as a **Product** value only (TAX-01), or does it also need a Portfolio value? The Product dimension currently mixes retail and wholesale products, and CRE is typically a portfolio-level cut. | TAX-01 | Product only, exactly as the note says. Adding an unasked-for Portfolio value would change tag semantics for existing assessments. |
| Q-06 | Admin's version history (ADM-06) covers assets and, per ADM-07, the two factory-reset actions. Should it also list every other admin action with a durable effect — user creation/deletion, taxonomy edits — or stop at assets and resets? | ADM-06, ADM-07 | Start with assets + resets. Both already have a first-class audited record to render (PLT-02 deliveries; the reset's own `transaction_log` row) — user/taxonomy history would need its own audit-event design first, which is a bigger ask than what was requested. |

---

## 12. Out of scope for 0.5.0 <a id="out-of-scope"></a>

| ID | Item | Reason |
|---|---|---|
| OOS-12 | Analytics dashboards, trend charts and exports | D-27 / ANL-07 — capture now, present later |
| OOS-13 | Row-level diff as a default view | AST-18 — the only O(rows) operation in the model; on demand and key-gated only |
| OOS-14 | Merging or appending snapshots | AST-05 forbids it outright; comparison windows are a test-layer concern (AST-12) |
| OOS-15 | Deleting a superseded snapshot | AST-08 — retained for audit and rollback, permanently |
| OOS-16 | Splitting a snapshot by reporting period at upload | UPL-16 — stored as metadata; slicing belongs to the test layer |
| OOS-17 | Cross-asset lineage and impact analysis | Not requested; needs the ANL-01 ID scheme to exist first |
| OOS-18 | Deployment to Azure | Carried forward from OOS-11; local validation and local commits only |

---

## 13. Build sequence <a id="build-sequence"></a>

The order below is a dependency order, not a priority order. Each step is testable on its own, and
nothing in it can be moved earlier without building against a model that has not landed yet.

| # | Step | Why here |
|---|---|---|
| 1 | **§7 RET** — delete the dead client functions and orphaned components; add the frontend reachability test | Cheapest, lowest-risk, and it stops new work being written against retired helpers. Independent of everything else. |
| 2 | **§6 ADM-01…05** + **§5 TAX** | Self-contained fixes with no dependency on the asset model. They clear the defect list so it is not carried through the structural work. ADM-06/07 (version history) moves to step 3 — it renders data that doesn't exist until then. |
| 3 | **§1 AST** — asset identity, versions, snapshots, storage record, migration onto the PLT-02 seam; **plus ADM-06/07**, Admin's version history, since it renders exactly this data | The spine. Steps 4–7 are all unbuildable before it. Migration is additive and idempotent (PLT-06), run twice in validation. |
| 4 | **§3 UPL** — the upload flow, in step order 1 → 7 | Consumes the asset model directly and is where the model is first proven end to end. |
| 5 | **§2 SRC** — landing page, View Existing dropdown, workflow context, Test Lab handover | Needs real versioned assets to select from, so it follows step 4. |
| 6 | **§4 CTX** — refresh, staleness, blank defaults | Needs both the snapshot model (step 3) and the intents that change it (step 4). |
| 7 | **§1.5 AST-16…19** — version diff | Needs the fingerprints written during step 4's profiling pass; nothing to diff before that. |
| 8 | **§8 ANL** — event log and catalogue, subject to Q-04 | Instruments the flows built in steps 4–6. Instrumenting them earlier would mean instrumenting them twice. |
| 9 | **§15 FNC** — refresh the ingestion functional map (step 4 touches those functions); author the others opportunistically | Documentation, not code — never blocks a build step, but is cheapest to do right after the step that touches that area. |

---

## 14. Traceability <a id="traceability"></a>

Every ID in this document must map, in the plan derived from it, to a phase and a named behavioural
test. The 0.4.0 requirement IDs referenced throughout (`ING-*`, `PLT-*`, `D-*`) remain live and are
not restated here; where 0.5.0 changes one of them, the change is recorded as a conflict in §9 and a
decision in §10, never as a silent overwrite.

**Verification baseline captured 3 Aug 2026**, before any 0.5.0 work: `pytest` 361 passed / 1
skipped; 79 live backend routes from four mounted routers; 153 API-client exports of which 74 have
no caller; 14 unreferenced UI files.

---

## 15. Functional code map (FNC) <a id="functional-code-map"></a>

> Source: live requirements review, 3 Aug 2026 — not from the raw notes file. The requester pointed
> at `functional_tools.xlsx` and asked for its scope to grow. **`functional_tools.xlsx` is the
> artefact this section governs.** A `functional_tools.docx` also exists in the repo root with
> overlapping content (the same 13 functions, laid out as one flat table with the file path and
> purpose repeated on every row); it is a rendering, not the source, and is not what FNC-01…04 below
> update. An earlier draft of this section pointed at the `.docx` — corrected here.

### 15.1 What it covers today <a id="what-it-covers-today"></a>

Read and verified 3 Aug 2026, directly from the workbook. `functional_tools.xlsx` holds **one sheet
per mapped code file**, the sheet named after the file (currently one sheet, `service.py`). Each
sheet carries a two-line header — the file's path and its one-line purpose — then a six-column table:
**Sl. No., Important functions, Function purpose, Inputs, Processing, Outputs** (the function's
signature and its line numbers live inside the "Important functions" cell). The `service.py` sheet
holds exactly **13 rows**, i.e. `backend/ai/v2/service.py`'s ingestion functions: `_read_tabular`,
`_write_table`, `_parse_dictionary`, `_column_profile`, `create_item`, `save_file`, `reupload_item`,
`finalize_item`, `_classify`, `profile_item`, `get_inventory`, `put_inventory`, `ingest_summary`.

| ID | Priority | Requirement |
|---|---|---|
| FNC-01 | MUST | **Nine of the thirteen mapped functions go stale under 0.5.0 and must be re-documented alongside the code change that touches them (§13 step 4), not left for later.** `create_item` — implements the retiring global-uniqueness check and free-text naming; rewrite for `<SYSTEM-ID>-<alias>` (AST-02/03). `reupload_item` — today "creates a new item in the same dataset family" (a sibling row); 0.5.0 reshapes replacement into a snapshot on the **same** asset (AST-10, D-25/C-40) — the single most affected function in the map. `finalize_item` — needs the blank-by-default, intent-conditional carry-forward logic of CTX-04/CTX-06 in place of its current unconditional update. `save_file` — needs the schema-conflict-check hook (UPL-20/21/29) and the table-scoped, not workbook-scoped, duplicate-column check (UPL-12). `profile_item` — needs to compute and store the AST-17 fingerprint and to orchestrate the schema-conflict check for both intents. `_write_table` — needs to write against the asset/version/snapshot seam (AST-10/13), not a flat item row. `_parse_dictionary` — needs the AST-20 asset-level, independently-versioned dictionary. `_column_profile` — extends to produce the tier-③ statistics (AST-16) the fingerprint is built from. `ingest_summary` — needs the STEP-7 completion-summary fields of UPL-27. `_read_tabular`, `_classify`, `get_inventory` and `put_inventory` are presumed stable in shape; re-verify only. |
| FNC-02 | MUST | **Extend `functional_tools.xlsx` with one new sheet per mapped code file, in the workbook's existing pattern**, to cover the product's other main functional areas — today it documents ingestion alone, but ingestion is one of eight. Verified 3 Aug 2026: <br>• **Test Lab / diagnostics engine** — `backend/dq_diagnostics/` (delivery.py, engines/, guards.py, manifest.py, register.py, result.py, runner.py, runner_cross_field.py, thresholds.py), `backend/dq_tests/` (contracts.py, global_rules.py, param_specs.py, registry.py, stage1/, stage2/), `backend/gx/` (Great Expectations integration), `backend/scoring/` (health.py, criticality.py, categories.py) — surfaced through `TestLab.jsx` + `testlab/` (CoverageBoard, ScopeGate, RunConsole, FindingsPanel, ScorePanel, VariableInventory). <br>• **Knowledge Base** — `backend/kb.py`, `backend/kb_convert.py`, `backend/kb_storage/` — surfaced through `KnowledgeBase.jsx`. <br>• **RCA workflow** — `backend/rca.py`, `backend/ai/rca_checker.py`, `backend/ai/rca_helpers.py` — surfaced through `IssueRca.jsx`. <br>• **Issue Management** — `backend/ai/v2/issues.py` — surfaced through `IssueManagement.jsx`. <br>• **Taxonomy / tagging** — `backend/taxonomy.py`, `backend/seeds/taxonomy_seed.py` — surfaced through the cross-cutting `TagPicker.jsx`. <br>• **Admin / platform** — `backend/routers/admin.py`, `backend/system_db.py` (`reset_demo` / `wipe_all_items`), `backend/tenancy.py` — surfaced through `Admin.jsx`. <br>• **Auth** — `backend/routers/auth.py` — surfaced through `Login.jsx` / `Profile.jsx`. The cross-cutting AI helper/tool layer (`ai/control_plane.py`, `tool_registry.py`, `llm.py`, `code_sandbox.py`, `skills.py`) is not a ninth area to map here — 0.4.0's PLT-08 already required an explicit typed contract per helper in its own docstring, which is this map's job done in-place; duplicating it as a tenth artefact would only drift. Several of these areas span many files (Test Lab alone lists nine); group tightly-coupled small files onto one sheet where a single file's table would be too sparse to read on its own, rather than mechanically forcing one sheet per file. |
| FNC-03 | SHOULD | **Every new sheet uses the workbook's existing six-column shape** (Sl. No., Important functions, Function purpose, Inputs, Processing, Outputs) with the file path and purpose in the same two-line header block above the table, so `functional_tools.xlsx` stays one consistent workbook rather than seven sheets in seven shapes. |
| FNC-04 | MUST | **This is a documentation deliverable, not a code change, and never gates a build step.** The `service.py` sheet is refreshed as part of §13 step 4 (the step that actually touches those functions); the other areas get their sheets opportunistically, each the next time its module is worked, per §13 step 9. A stale sheet is a defect to fix, not a blocker to work around. |
