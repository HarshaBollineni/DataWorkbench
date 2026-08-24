# RCA — Stage 0 Contracts

Status: frozen for Stage 1+. Any change here is a new dated addendum, not a
silent edit — Stage 0 gate requires this document to be complete before any
implementation stage starts.

Authoritative sources: `RCA_Full_Workflow_v10.md` (design),
`RCA_v10_Migration_Knowledge_Base_and_Implementation_Plan.md` (migration plan),
`RCA_CODEBASE_COMPATIBILITY_INPUT.md` (as-built snapshot @ `d7529bd`).

## 0. Ground truth this contract is built on

Verified directly against the repo, not assumed from the docs above:

- `backend/main.py` mounts only `auth`, `admin`, `v2` (+ `/health`, `/api/config`).
  The legacy RCA stack (`backend/ai/rca_cases.py`, `backend/ai/rca_agent.py`,
  `rca_cases`/`rca_sessions`/`rca_steps`/`rca_evidence`/`rca_hypotheses`/
  `rca_remediation_plans` tables in `backend/system_db.py`) has **no reachable
  writer** from any mounted route. `grep` confirms nothing outside
  `ai/rca_cases.py` and `ai/rca_agent.py` touches those tables. **There is no
  live legacy RCA case data to preserve** — the tables are schema-present,
  data-empty vestiges of a prior product generation.
  **Addendum (post-Stage-7 naming cleanup):** this dead stack was later
  deleted outright rather than kept as reusable shape — `ai/rca_cases.py`,
  `ai/rca_agent.py`, and their nine tables are gone from the codebase. This
  was forced by a naming correction: the new case workflow's tables had been
  named `rca_v10_*` (copying "v10" from `RCA_Full_Workflow_v10.md`'s
  *filename* as if it were a real product version, which it never was) to
  avoid colliding with these bare `rca_*` legacy names. Once the legacy
  stack was confirmed truly dead by the same grep above, it was removed and
  the new tables/module renamed to the plain `rca_*`/`rca.py` names they
  should have had from the start — see the traceability matrix's naming-
  correction note for the full list of renamed identifiers.
- The live, user-facing RCA today is `backend/ai/v2/issues.py:rca()`, reached
  via `POST/GET /issues/{issue_row_id}/rca/stream` (`backend/routers/v2.py:355`)
  and rendered by `ui/src/pages/IssueRca.jsx`. It is a **template-driven
  heuristic** keyed off `test_name` substring matches (`_CAUSE_HEURISTICS`,
  `_AI_SOLUTIONS`), regenerated fresh on every open, **never persisted**. This
  is the actual "legacy" behavior RCA must keep working for pre-flag issues.
- No tenant/workspace concept exists anywhere in the schema. `users.username`
  is the PK with an `authz_roles` JSON list (`backend/routers/auth.py:34-36`);
  authorization today is admin-vs-not only. `dq_items.item_id` is the closest
  existing scoping boundary (all uploads, plans, results, issues hang off it).
  Sessions are opaque Bearer tokens in `app_fsm` (no CSRF, no rotation).
- Migrations today are additive idempotent DDL inside `system_db.init_schema()`
  — there is no separate migration framework or version table. RCA continues
  this pattern rather than introducing a new migration runner (out of scope
  per the plan's "controlled migration, not a rewrite" posture).

## 1. Tenant / principal contract

**Decision (conservative default per the migration plan §16.1, no repo
constraint contradicts it):** tenant = deployment instance, represented by one
bootstrap tenant row. This is the minimum viable definition that satisfies
"explicit tenant ownership" without inventing an org-chart the product has no
evidence of needing yet.

- New table `tenants(tenant_id TEXT PRIMARY KEY, name TEXT, created_at TEXT)`.
  Seed exactly one row: `tenant_id='bootstrap'`.
- Add `tenant_id TEXT NOT NULL DEFAULT 'bootstrap'` to every RCA-owned table
  (taxonomy, knowledge, RCA records) at creation time — never retrofitted
  onto legacy tables in a way that changes their existing primary keys.
- `users` gains `tenant_id TEXT NOT NULL DEFAULT 'bootstrap'` via additive
  migration (existing rows backfilled to `'bootstrap'`, ids unchanged).
- A `Principal` = `{username, tenant_id, authz_roles}`, resolved once in
  `current_user()` and threaded explicitly into every RCA service call — never
  re-derived from a mutable global. No RCA endpoint may accept a caller-supplied
  `tenant_id`; it is always taken from the resolved Principal.
- Server-side authorization rule (Stage 6 enforces, Stage 0 freezes the rule):
  every RCA list/read/write/knowledge-retrieval/export checks
  `record.tenant_id == principal.tenant_id`. A foreign-tenant ID returns the
  same 404 as an unknown ID (no existence disclosure). Admin (`is_admin`)
  grants platform administration (user/taxonomy management) but **not**
  automatic access to another tenant's case/knowledge content — this is a new
  invariant relative to today's `is_admin` behavior and is called out
  explicitly so Stage 6 tests it as a negative case.

**Acknowledged gap (not fixed at Stage 0, must not be silently assumed
solved):** `dq_items`, `issues_v2`, `results_v2`, `plan_v2` — the *source*
records a RCA case points to via `item_id`/`issue_row_id` — carry no
`tenant_id` today and are not scoped by this contract. A RCA case is
tenant-scoped, but the item/issue/result data it references is not. This is
harmless while exactly one tenant (`bootstrap`) exists, but it means Stage 0
does not deliver full tenant isolation end-to-end, only the RCA-owned overlay
tables are isolated. Stage 6 must either (a) add `tenant_id` to `dq_items`
and cascade it to `issues_v2`/`results_v2`/`plan_v2`, or (b) explicitly
document single-tenant-only as a permanent product boundary — this decision
is deferred to Stage 6, not resolved here, flagged so it is not mistaken for
solved by the traceability matrix.

## 2. Legacy vs RCA coexistence contract

- `issues_v2` (existing table) gains one additive column:
  `workflow_version TEXT NOT NULL DEFAULT 'legacy-v1'`.
- A new feature flag `RCA_ENABLED` (config row, default `false` until
  Stage 7) gates whether opening an issue's RCA screen creates/continues a RCA
  case or falls back to the existing `ai/v2/issues.py:rca()` heuristic.
- When the flag is on, **new** issue rows get `workflow_version='rca'` at
  creation (`sync_issues()` insert path) and their RCA screen routes to the new
  `rca_cases` state machine (§3). Issue rows created before the flag
  flipped, or while it is off, keep `workflow_version='legacy-v1'` and continue
  rendering through the untouched existing heuristic path — that code path is
  not deleted or altered by RCA work, only additively wrapped.
- No historical numeric confidence is ever converted into a RCA tier. The
  dormant `rca_hypotheses.confidence` column and any future legacy scoring are
  simply not read by RCA code.
- `issues_v2.rca()`, `IssueRca.jsx`, and the `/issues/{id}/rca/stream` contract
  stay byte-for-byte reachable for `workflow_version='legacy-v1'` rows for the
  lifetime of the migration (retired only in Stage 7, behind its own flag,
  after acceptance).

## 3. RCA record model and state machine

**Naming note (superseded — kept for history):** this section originally
warned about a real collision risk: the legacy dormant tables (§0) used
`case_id`/`evidence_id`/`hypothesis_id` as primary-key names, and the new
tables below were then named `rca_v10_*` specifically to avoid colliding
with the legacy bare `rca_*` names while reusing the same column-naming
convention. That collision risk no longer exists — §0's addendum records
that the legacy stack was later deleted (confirmed dead, no reachable
writer, no data), at which point the `v10` disambiguator was removed too:
the tables below are named plain `rca_*`, and the service module is
`backend/rca.py`. There is now only one `rca_*` table family, so the
original "never join across the two families" rule is moot — recorded here
so a future reader of old commit history understands why the tables were
briefly named `rca_v10_*` before settling on their current names.

New tables, all `tenant_id`-scoped, additive (no existing table is dropped or
renamed):

```
rca_cases            (case_id PK, tenant_id, issue_row_id FK issues_v2,
                           item_id, table_name, state, part, tag_snapshot_json,
                           complaint_text, created_by, created_at, updated_at,
                           closed_at, contract_version)
rca_state_transitions (id PK, case_id, prev_state, new_state, actor,
                           reason, evidence_ids_json, ts, workflow_version,
                           contract_version)
rca_failure_groups    (group_id PK, tenant_id, case_id, representative_run_id,
                           two_signal_evidence_json, created_at)
rca_attached_failures (id PK, group_id, run_id, reconciliation_status,
                           reconciled_at)
rca_case_files        (case_id PK/FK, checklist_json, schema_snapshot_json,
                           tags_snapshot_json, complaint_json, created_at)
rca_looks             (look_id PK, case_id, seq, kind [opening|planned],
                           proposed_by, fork_json, sql_or_helper_ref,
                           budget_counted INTEGER, created_at)
rca_look_executions   (execution_id PK, look_id, status, summary_json,
                           crashed INTEGER, retried INTEGER, executed_at)
rca_suspects          (suspect_id PK, case_id, kind [single|pair],
                           member_cause_ids_json, status
                           [active|ruled_out|revived], origin
                           [look|challenge_evidence|challenge_history],
                           created_at, updated_at)
rca_suspect_history   (id PK, suspect_id, prev_status, new_status, reason,
                           look_id, ts)
rca_human_questions   (id PK, case_id, question, asked_by_look_id, answer,
                           answered_by, answered_at, knowledge_rule_id_out)
rca_coverage_passes   (id PK, case_id, pass [1|2], raw_evidence_ref_json,
                           history_nominations_json, added_suspect_id, ts)
rca_hypotheses        (hypothesis_id PK, case_id, suspect_id, statement,
                           label [defect|test_flaw|genuine_change],
                           tier [strong|moderate|weak], evidence_look_ids_json,
                           confirm_check_json, reject_condition_json, owner,
                           created_at)
rca_confirmation_checks (check_id PK, hypothesis_id, order_rank, cost_hint,
                           status [pending|run|crashed], executed_at)
rca_judge_decisions   (id PK, check_id, verdict
                           [confirmed|rejected|inconclusive], refined INTEGER,
                           reasoning, ts)
rca_symptom_accounting (id PK, case_id, total_symptom_size,
                           explained_size, remaining_size, computed_at)
rca_fix_proposals     (id PK, hypothesis_id, options_json, routed_to,
                           label, created_at)
rca_fix_approvals     (id PK, fix_proposal_id, approved_by, approved_at,
                           applied_confirmed_by, applied_confirmed_at,
                           time_box_deadline, escalated_at)
rca_closures          (case_id PK/FK, outcome
                           [confirmed|genuine_change|test_design_flaw|unresolved],
                           rerun_run_id, frozen_snapshot INTEGER,
                           fresh_snapshot_warning TEXT, knowledge_draft_id,
                           closed_at)
```

**History-leakage side channel (found during Stage 0 adversarial review):**
the existing legacy agent (`backend/ai/rca_agent.py:110-111`) builds its
prompt context via `context_memory.get_context_bundle(...)` — an unscoped,
un-categorized free-text store (`object_contexts` /
`backend/ai/context_memory.py`) that mixes prior findings, SME notes, and
historical narrative with no trust/category/quarantine controls at all. If
RCA's Planner/Reader prompt-building code ever reused that helper for
convenience, historical causes would leak in through this side channel,
silently defeating the entire quarantine design in §4/§5 (which only
constrains the *typed* `kb_rules` retrieval path). **Hard rule:** RCA
Planner/Reader/Coverage-pass-1 prompt builders must be new functions that
construct their input exclusively from the manifest defined in §4 —
`context_memory.get_context_bundle` / `object_contexts` must not appear
anywhere in the RCA agent code path (`rca/planner.py`, `rca/reader.py`,
`rca/coverage_challenge.py`). This is a Stage 4 code-review gate, not just
a design intention.

**LLM-callable write risk (found during Stage 0 adversarial review):** the
codebase already has a real mechanism for exposing functions to LLM agents as
callable tools (`backend/ai/tool_registry.py:66` `register()`,
role-gated via `allowed_roles`). §6.3 below asserts "no LLM call is ever the
last step before a `kb_rules` write" — that assertion only holds if the KB
write functions (and the `rca_suspects`/state-transition writers) are
never registered through `tool_registry.register()`. **Hard rule:** none of
`kb/write.py`, `rca/reader.py`'s suspect-status writer, or the
state-transition service may ever be wrapped as a `Tool` and registered —
these are called only from deterministic service code, never exposed as an
agent-invocable tool. Stage 2/3/4 code review must check this explicitly;
`grep -n "register(Tool(" backend/ai/tool_registry.py` should never show one
of these functions as the target.

State machine (verbatim from `RCA_Full_Workflow_v10.md` §7 / plan §7), stored
as the `state` column enum on `rca_cases` plus rows in
`rca_state_transitions` for every move:

```
Part A: created -> triage -> intake -> opening_looks -> investigation_loop
        -> awaiting_human_answer (optional) -> coverage_challenge_blind
        -> coverage_challenge_history -> reopened_kill_attempt (optional)
        -> hypothesis_composition -> ready_for_verification

Part B: verification_planning -> confirmation_checks -> judging
        -> symptom_accounting -> awaiting_fix_approval
        -> awaiting_fix_application -> closure_rerun -> reconciliation
        -> closed

Alt:    all_hypotheses_rejected -> second_chance_part_a -> ready_for_verification
        second_chance_failed -> escalated -> unresolved
```

Rule: **deterministic workflow services own every transition.** Agents
(Planner/Reader/Composer/Judge/Fix-advisor) return recommendations only; a
plain-Python service function validates the recommendation against the current
`state` and either performs the transition (writing both tables above) or
rejects it. No agent output is ever written straight to `state`.

**Second-module guard:** the RCA case view is reached exclusively through the
existing Issue Management surface — `/issues/:issueRowId` branches on
`issues_v2.workflow_version` inside the existing `IssueRca.jsx` route (or a
RCA sub-view it renders), never a new top-level nav entry or standalone route.
`ui/src/App.jsx`'s route table gains no new page-level route for RCA case
detail; Stage 3 must not introduce one. This is the concrete, testable form of
"do not create a second RCA module."

## 4. Agent input/output contracts

Every agent call is built from an explicit, persisted **input manifest** — no
agent scans a directory, a repo, or "current" mutable state implicitly.

| Agent | Reads | Never reads | Writes (via service, not directly) |
|---|---|---|---|
| Triage | failure list, lineage/time window, symptom shape | case history for anything but grouping | `rca_failure_groups` |
| Intake | fixed checklist for test family, KB structure/lineage/definitions/ownership/accepted-changes | past case causes | `rca_case_files` |
| Opening looks | case file, KB structure | past case causes | `rca_looks` (kind=opening), `rca_look_executions` |
| Planner | case file, current suspect board, evidence trail, eligible KB (structure/lineage/domain facts), remaining budget | **historical causes (hard quarantine)** | `rca_looks` (kind=planned, includes precommitted `fork_json`) |
| Runner | one look + fork (opaque, untouched) | fork semantics (never interprets) | `rca_look_executions` |
| Reader | fork + result summary, KB domain facts | **historical causes (hard quarantine)** | `rca_suspects`, `rca_suspect_history` (sole writer of suspect status) |
| Coverage pass 1 | raw evidence trail only | suspect board, case history | `rca_coverage_passes` (pass=1) |
| Coverage pass 2 | raw evidence trail + permitted KB case-history category | — | `rca_coverage_passes` (pass=2), may propose one nominated suspect (enters board only if current-case evidence supports it) |
| Composer | final suspect board, evidence trail | — | `rca_hypotheses` |
| Judge | one confirmation check's result + its precommitted `reject_condition_json` | — | `rca_judge_decisions` |
| Fix advisor | confirmed hypothesis, label, ownership rule | — | `rca_fix_proposals` (options only, never applies) |
| Closure service (code, not LLM) | fix-application confirmation, frozen snapshot ref | — | `rca_closures`, reruns the original test, writes the knowledge draft via the KB write path (§6) |

Prompt isolation: every LLM-facing agent call persists an input manifest
`{record_ids, versions, hashes}` before the call (reuses the "safe input
manifest" requirement from the migration plan §11) — Stage 6 makes this a
hard gate; Stage 3–5 populate it as each agent ships.

## 5. Agent knowledge-access matrix

| Category → Agent | structure | lineage | domain_fact | ownership | case_history |
|---|---|---|---|---|---|
| Intake | yes | yes | yes | yes | yes (suppression only) |
| Opening looks | yes | yes | yes | no | no |
| Planner | yes | yes | yes | no | **no** |
| Reader | yes | yes | yes | no | **no** |
| Coverage pass 1 | no (raw evidence only) | no | no | no | no |
| Coverage pass 2 | no | no | no | no | yes (nominate only) |
| Composer | no (board + evidence only) | no | no | no | no |
| Judge | no | no | no | no | no |
| Fix advisor | no | no | no | yes | no |

Enforced in the retrieval service (§6.3), not by agent self-restraint — the
retrieval call takes a `requesting_agent` parameter and the category filter is
applied server-side before any rule reaches the prompt.

## 6. Knowledge Base contracts

### 6.1 Hierarchy and storage

```
KnowledgeDocument -> KnowledgeDocumentVersion -> KnowledgeSection -> KnowledgeRule
```

New tables (all `tenant_id`-scoped):

```
kb_documents          (document_id PK, tenant_id, title, category_hint,
                       created_by, created_at)
kb_document_versions  (version_id PK, document_id, version_seq,
                       original_sha256, original_media_type, original_bytes_ref,
                       converted_markdown_ref, converted_markdown_sha256,
                       converter_name, converter_version, conversion_warnings_json,
                       conversion_report_json, reviewer, review_state, created_at)
kb_sections           (section_id PK, version_id, heading, order_seq)
kb_rules              (rule_id PK, section_id, tenant_id, rule_hash, rule_text,
                       category [structure|lineage|domain_fact|ownership|case_history],
                       trust_level [human_confirmed|inferred],
                       lifecycle_state [draft|pending_review|published|expired|
                                        superseded|under_suspicion|archived],
                       effective_date, last_confirmed_date, shelf_life_months,
                       owner, reviewer, approver, related_tables_json,
                       related_columns_json, superseded_by_rule_id,
                       under_suspicion_reason, created_at, updated_at)
kb_retrieval_manifests (manifest_id PK, tenant_id, requesting_agent, case_id,
                       rule_ids_json, eligibility_reasons_json, ranked INTEGER,
                       created_at)
```

Original bytes are stored content-addressed under a tenant-scoped artifact
key (Stage 6 formalizes the key scheme); never inline in SQLite.

### 6.2 Trust, lifecycle, shelf life

- Only `lifecycle_state='published'` AND effective (not expired/archived/
  superseded) rules are retrievable.
- Shelf life default 12 months, overridable per `category`, configured in one
  table (`kb_shelf_life_defaults(category PK, months)`), not hardcoded.
- Past shelf life, or immediately after a schema change touches a
  `related_tables_json` member (detected via `table_metadata` change hash),
  the rule's `trust_level` flips `human_confirmed -> inferred` and
  `lifecycle_state` stays `published` but becomes challengeable through the
  normal human gate at next use — no forced re-certification sweep.
- Blame-back: on `rca_closures.outcome='unresolved'`, or a Strong
  hypothesis rejected by the Judge, every `kb_rules` row referenced in that
  case's retrieval manifest is set `lifecycle_state='under_suspicion'` with
  `under_suspicion_reason` citing the case id.

### 6.3 Writes — code only

**No LLM call is ever the last step before a `kb_rules` INSERT/UPDATE.** Two
and only two code paths write knowledge:

1. Human-answer path: `rca_human_questions.answer` populated →
   deterministic service creates a `draft` `kb_rules` row
   (`trust_level='human_confirmed'`, `category` inferred from the question
   context) citing the case as source.
2. Case-closure path: `rca_closures` reaching `confirmed` /
   `genuine_change` / `test_design_flaw` → deterministic service drafts the
   corresponding `case_history` or `domain_fact` rule.

Both land in `lifecycle_state='draft'` (or `pending_review` if the tenant's
configured review policy requires it — see Decisions Required §9.3) and
require the configured human approval before `published`.

## 7. Taxonomy and tag propagation contract

Tables (per migration plan §5.2), tenant-scoped, versioned:

```
tag_dimensions   (dimension_id PK, tenant_id, key, label, created_at)
tag_values       (value_id PK, dimension_id, key, label, deprecated INTEGER,
                  created_at)
tag_aliases      (alias_id PK, value_id, alias_key)
tag_taxonomy_versions (version_id PK, tenant_id, seq, published_at)
tag_assignments  (assignment_id PK, tenant_id, object_type, object_id,
                  value_id, taxonomy_version_id, origin
                  [inherited|user_added|system_derived|migrated],
                  source_object_type, source_object_id, actor, ts,
                  override_reason)
```

Seed dimensions/values exactly as listed in the plan §5.1 (risk_type,
portfolio, product, use_case) as the first `tag_taxonomy_versions` row.

Propagation chain (immutable snapshot at each hop, never rewritten by later
source changes):

```
Database/Dataset Version (dq_items / ingested_databases)
  -> Test Design Version (plan_v2 row)
  -> Test Execution/Result (results_v2 row)
  -> Issue (issues_v2 row)
  -> RCA Case (rca_cases.tag_snapshot_json)
```

Each downstream record stores its own `tag_assignments` rows with
`origin='inherited'` at creation time, referencing the taxonomy version live
at that moment. Removing an inherited tag requires `override_reason` and an
audit event (§9). Agents read `rca_cases.tag_snapshot_json` /
`rca_case_files`, never the live mutable `tag_assignments` of the source
item — this is what "tags never rewrite history" means concretely.

## 8. Feature flags

Config rows in a new `feature_flags(key PK, tenant_id, enabled, updated_at)`
table (not environment variables, so per-tenant control is possible later
without redeploy):

- `RCA_ENABLED` — gates new-case creation into the RCA state machine (§2).
- `RCA_LEGACY_CREATION_RETIRED` — once true, blocks any new issue from
  taking the legacy heuristic path even as a fallback; flipped only in
  Stage 7 after acceptance.
- `KB_MODULE_ENABLED` — gates Knowledge Base nav/routes going live (Stage 2).
- `TAXONOMY_ENABLED` — gates tag UI on Data Sourcing (Stage 1).

All default `false`; Stage 1/2/3/7 each flip exactly the flag their gate
requires, never ahead of the corresponding stage's own gate passing.

## 9. Audit-event contract

Single append-only table, superseding the partial
`transaction_log`/`rca_steps`/`hitl_decisions` coverage for everything RCA
touches (those existing tables are untouched for non-RCA flows):

```
rca_audit_events (event_id PK, tenant_id, actor, event_type, object_type,
                      object_id, before_json, after_json, reason, ts)
```

`event_type` values, minimum set (extended as stages implement the underlying
action): `upload`, `tag_assign`, `tag_remove`, `knowledge_publish`,
`knowledge_archive`, `state_transition`, `agent_invocation`, `look_execution`,
`suspect_status_change`, `human_answer`, `fix_approval`, `fix_applied`,
`closure`, `export`, `admin_action`. Every Stage 3+ service call that mutates
a RCA record writes exactly one row here in the same transaction as the
mutation — never best-effort/fire-and-forget.

## 10. Migration and rollback

- `system_db.init_schema()` gains the RCA `CREATE TABLE IF NOT EXISTS`
  statements (additive, idempotent — matches the existing pattern, no new
  migration framework introduced).
- A one-time backfill step (idempotent, safe to re-run) sets
  `issues_v2.workflow_version='legacy-v1'` for all existing rows and inserts
  the `tenants` bootstrap row + backfills `users.tenant_id='bootstrap'`.
- Rollback: since all RCA tables are new and `issues_v2.workflow_version`
  defaults safely, rollback = set `RCA_ENABLED=false` (new issues stop
  entering RCA) and, if a full revert is needed, drop only the RCA-prefixed
  tables — legacy tables and data are never touched by a RCA rollback. This
  limitation (RCA-created cases become unreachable, not un-created) is
  documented here and repeated in the Stage 7 rollout doc.
- **Rollback residue (found during Stage 0 adversarial review):** the two
  additive columns placed on *existing* shared tables — `issues_v2
  .workflow_version` and `users.tenant_id` — are not part of the "drop
  RCA-prefixed tables" rollback path, because they live on tables RCA doesn't
  own. SQLite can drop a column (3.35+), but this repo's rollback story
  intentionally never attempts destructive schema surgery on shared tables.
  A full RCA rollback therefore leaves these two harmless, always-defaulted
  columns in place permanently — this is the one acknowledged, permanent
  residue of even a full rollback, and is not a data-loss or corruption risk
  since both columns are additive-with-default and read only by RCA code.
- Backup: reuse the existing `system_db.backup_to_volume()` mechanism; run it
  manually before applying the Stage 1 migration in any environment with data
  worth keeping (local dev has none yet — no `system_state.db` exists in this
  checkout).

## 11. UAT strategy

Browser UAT (Playwright) is added incrementally per stage against the
existing `.e2e` fixture pattern (`ui/e2e/fixtures/*.csv`, isolated
`.e2e/system_state.db`) — never against a shared dev database. Each stage's
gate (per the migration plan §14) adds its own spec file under `ui/e2e/`
rather than growing one monolithic spec. Synthetic KB and synthetic tag data
are loaded only through supported UI/API calls inside test setup, never
preloaded into the seed path — `KB_MODULE_ENABLED`/synthetic fixtures must
never appear on a normal `npm run dev` boot (Stage 2 gate asserts this with a
negative test).

## 12. Decisions carried forward from plan §16 (defaults adopted, no repo
constraint overrides them)

1. Tenant = deployment instance (bootstrap tenant), per §1 above.
2. Knowledge upload/review/publish roles: reuse `authz_roles` — add
   `kb_editor` (upload/draft) and `kb_reviewer` (publish/archive) roles to the
   existing `users.authz_roles` list; `admin` may assign roles but does not
   inherit publish rights automatically (consistent with §1's "admin ≠
   automatic tenant-content access").
3. Closure-generated knowledge requires the same `kb_reviewer` approval as
   uploaded documents — no separate lighter-weight path (simplicity over a
   bespoke exception).
4. Shelf life: 12 months default for all categories at launch; per-category
   overrides live in `kb_shelf_life_defaults` from day one so this is a data
   change, not a code change, when a real value is needed later.
5. OCR: out of scope (per plan default). Image-only PDFs rejected with a
   clear message at upload.
6. Max document size: 20 MB / 200 pages — a placeholder ceiling chosen to be
   generous for policy-doc-sized inputs while bounding conversion cost;
   revisit if a real document exceeds it.
7. DOCX/PDF libraries: `python-docx` (DOCX -> structured text) and
   `pdfplumber` + a text-layer check (reject if extracted text density is
   near zero, i.e. image-only) for PDF. Both are **already backend
   dependencies** (`backend/requirements.txt`, already used by
   `ai/dict_ingest.py` for data-dictionary extraction) — no new dependency
   needed. Corrected from the original `pypdf` placeholder once the actual
   installed library was confirmed during Stage 2 implementation.
8. Malware scanning: seam only (a named hook function that no-ops today and
   logs `not_configured`) — no scanning vendor is available in this
   environment; documented as a residual gap, not silently skipped.
9. Ownership hierarchy: explicit record owner → published ownership rule →
   dataset/test metadata owner → configured fallback queue (a single
   `fallback_owner_queue` config value). Time-box default 5 business days
   (matches the existing `rca_remediation_plans.sla` default in
   `ai/rca_cases.py`).
10. Authentication migration timing: bearer tokens remain through Stage 6;
    secure cookie + CSRF is scoped inside Stage 6 itself, not deferred beyond
    this migration.
11. Legacy RCA creation cutoff: retired only after the Stage 7 acceptance gate
    passes (§8, `RCA_LEGACY_CREATION_RETIRED`).
12. Retention: originals/Markdown/prompts/evidence/cases retained
    indefinitely at launch (no deletion job exists in the product today for
    any comparable artifact) — revisit if/when a real retention requirement
    appears.

## 13. Stage 6 addendum — two decisions revised from §1/§12.10 above

Both decisions below are explicitly permitted alternatives §1 itself already
named ("Stage 6 must either (a)... or (b) explicitly document..."), not
silent scope cuts. Recorded here, dated, rather than edited into the frozen
sections above.

**13.1 Multi-tenant table scoping — option (b) chosen.** §1's acknowledged
gap (`dq_items`/`issues_v2`/`results_v2`/`plan_v2` carry no `tenant_id`) is
resolved as a **permanent single-tenant-per-deployment boundary**, not
retrofitted. Reasoning: exactly one tenant (`bootstrap`) exists in every real
deployment of this product today; retrofitting `tenant_id` onto these four
tables would cascade into every v2 API route, UI data-fetch, and existing
test in the entire product (not just RCA's module boundary) — a
product-wide schema and query-layer change far outside "controlled
migration, not a rewrite" (MP's own framing) and far riskier to execute
correctly in one pass than the RCA work itself. The RCA-owned overlay
tables (`rca_*`, `kb_*`, `tag_*`) remain fully tenant-scoped and
enforced (Stage 6 closed the enforcement gaps found in those — see the
traceability matrix's Stage 6 closure note); a future genuine multi-tenant
requirement would need its own dedicated migration project against the core
v2 schema, not a byproduct of this one.

**13.2 Secure cookie + CSRF — narrowed to bearer-token hardening only.**
§12.10 originally committed Stage 6 to a full cookie+CSRF migration. Revised
after Stage 6's own security survey confirmed: auth today is Bearer-header
only (`Authorization: Bearer <token>`, `ui/src/api/client.js`), stored in
`localStorage`, never a cookie. Classic CSRF requires an *ambient* browser
credential (a cookie the browser attaches automatically to a cross-origin
request); a Bearer header is never sent automatically by the browser, so
today's design is **not CSRF-exploitable by construction** — adding a CSRF
token mechanism on top of it would harden a vulnerability class that does
not exist yet, while the actual conversion to cookie-based sessions (which
*would* need CSRF protection) is a product-wide auth rearchitecture (every
route, every client call, session refresh semantics) — the same "too large
a blast radius to rush" reasoning as §13.1. What Stage 6 delivered instead,
matching the real gap the survey found (`backend/routers/auth.py`'s
`current_user()` never checked token age — a session was valid forever
until explicit logout): an absolute session lifetime
(`SESSION_MAX_AGE_HOURS`, default 24h, `backend/routers/auth.py`), tested in
`backend/tests/test_auth_security.py`. Token rotation and the cookie+CSRF
migration itself remain open, to be scoped as their own dedicated
authentication-hardening project, not bundled into the RCA migration.
