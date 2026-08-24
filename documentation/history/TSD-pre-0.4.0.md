# Technical Specification Document (TSD) — Aegis Labs / Intelligent DQ Studio

> **Archive note (19 Aug 2026):** moved from `source-codes/TSD.md` so the
> application entry point can hold the current technical design. Paths and
> statements below are preserved as historical evidence and are relative to
> their original location unless explicitly stated otherwise.

> **Historical Galileo document (retired).** It is retained for pre-0.4.0
> context only. Do not use its framework, Data Sourcing, or Test Lab wizard
> descriptions as current behaviour. The active 0.4.0 contract is `TODO.md`
> plus `docs/0.4.0/` (especially `00-framework.md`, `06-ingestion-contract.md`,
> and `09-release-notes.md`).
>
> Historical snapshot last verified: 2026-07-01 (**Test Lab Phases 2+3+4+5** — Designer (Bayes/Merton/dossiers), framework-
> tagged Test Kit, the `/test-lab` **5-step module**: Scope&Domains → Recommendations (2 sections + AI
> runner + coverage) → Designer → **Catalogue + Consolidated PDF report + Workbench persistence** →
> **Monitoring Schedules (config + run/tick, email preview — no SMTP, no background runner)**;
> live Azure smoke PASSED, npm build clean) · Maintainer: Chief Architect (orchestrator) + delegated
> sub-agents.
>
> **Active 0.4.0 snapshot:** the nine-diagnostic register replaces that
> framework; only #4 Cross-field business rule is executable. Data Sourcing is
> Drop → Review → Ready, and Test Lab is Coverage → Scope → Run → Findings.

---

## 1. What this is (executive summary)

**Aegis Labs** (product name; subtitle *"Agentic Feature Validation & Guardrail Workspace"*; formerly
"Intelligent DQ Studio") is an **agentic data-quality platform** for analytical / model-feature data.
Users ingest a logical database, the system profiles it, AI agents recommend and **execute real
statistical DQ tests**, and an agentic Root-Cause-Analysis (RCA) workflow diagnoses failures with a
human-in-the-loop (HITL) code-approval gate.

Two deployable parts:

| Part | Stack | Role | Port |
|------|-------|------|------|
| `backend/` | FastAPI + Great Expectations 1.x (dormant) + Azure OpenAI | API, agents, test execution, scoring, state | 8001 |
| `ui/` | Vite 8 + React 19 + Tailwind 4 + radix-ui | Web UI (auth-gated SPA) | 5175 |

**LLM provider is Azure OpenAI (deployment `gpt-4.1`)** — auto-detected via `AZURE_OPENAI_ENDPOINT`.
Do **not** swap to Claude/OpenAI-public. Key is supplied at runtime, never committed.

---

## 2. Run it

```powershell
# one-time
./setup.ps1                 # creates .venv + pip install + npm install
# each session
./app.ps1 start              # starts backend (:8001) and frontend (:5175)
./app.ps1 status             # reports managed services without touching others
```

Azure env (set before backend boot): `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
`AZURE_OPENAI_DEPLOYMENT` (`gpt-4.1`), `AZURE_OPENAI_API_VERSION` (`2025-01-01-preview`).
`main.py` auto-loads `backend/.env` via python-dotenv. Demo login: **anirban / dqstudio**
(admin gate = authz-role on the session, the old admin password is retired).

---

## 3. Repository map

```
Intelligent DQ/
├── TSD.md                  ← you are here (agent entry point)
├── README.md, USER_GUIDE.md
├── setup.ps1, app.ps1
├── backend/                FastAPI service (see §4)
├── dq-studio/              React SPA (see §5)
└── guide/                  supporting docs
```

---

## 4. Backend (`backend/`)

### 4.0 Galileo v2 demo path (verified 2026-07-08)

The active demo path is upload-first and lives under `/api/v2`. Users create a
`database` or `dataset` item, upload files into `backend/uploads/{item_id}/`,
profile the item, build a framework plan, approve snippets, execute tests,
request incremental recommendations, and view the final score. The fixed
`portfolio_alt_master.db` warehouse is retired from this path.

Boot behavior:
- `system_db.init_schema()` creates additive v2 tables: `dq_items`,
  `dq_item_files`, `dq_item_tables`, `variable_inventory`, `fw_areas`,
  `fw_tests`, `fw_family_weights`, `plan_v2`, `results_v2`, and `scores_v2`.
- `seeds.galileo_seed.seed_all()` reloads the client workbook and rules on every
  boot: 11 areas, **14 tests** (5 Systemic + 9 Specific), 99 family-weight rows,
  CRE 50 rules, IFRS9 10, IRB/Basel 8, Stress Testing 7. Source of truth since
  2026-07-08: original framework workbook (retired after curation)
  — its reduced `Test_Detail_Library` (41→16) defines the executable scope; the
  seed filters each area's Stage 1/2 test lists to library membership (the
  Detailed sheet still narrates broader diagnostics). Downturn & Regime Coverage
  intentionally has no automated test. Registry: `backend/dq_tests/registry.py`
  (14 implementations + finalized type/role specs), thin wrappers in
  `dq_tests/stage1|stage2/` (5+9 modules), catalogue in `dq_tests/README.md`.
  `Cross-field business rule check` and `Variable-to-variable relationship check`
  are recommender-only generated-code templates, never `fw_tests`.

**Finalized-14 build (2026-07-13):** the shared `dq_tests/global_rules.py`
applies constant, calendar-axis, discrete-numeric, zero-denominator, and
outcome-sibling exclusions before framework/recommended selection; plan rows
carry selected/excluded columns and reasons. `TestResult` has evidence,
`not_runnable_reason`, and `watch_note` (no `example_rows`). The MCAR, MNAR,
single-feature AUC, MAD, tail, PSI, plausibility, post-outcome, and score
roll-up implementations now follow the finalized specs. Results remain
per-column for issue work while `scoring.categories.roll_up_results` gives
each test plan one explicit health-score unit. Data Sourcing now exposes an
editable active-workflow inventory on return visits.
- `system_db.wipe_all_items()` clears v2 uploaded inventory and generated work on
  every backend import/launch while preserving framework/library/rules.

Mounted v2 endpoints:
- `/api/v2/items` item creation/listing/status and upload/finalize.
- `/api/v2/items/{id}/profile/stream` deterministic Data Profiling Agent SSE.
- `/api/v2/items/{id}/inventory` editable variable inventory.
- `/api/v2/items/{id}/plan/*` framework/incremental plan build, patch, finalize.
- `/api/v2/plan/{row_id}/snippet` generate/edit/approve sandbox snippets.
- `/api/v2/items/{id}/execute/stream` sequential execution; table failures do not
  stop later tables.
- `/api/v2/items/{id}/recommend/stream` deterministic Recommendation Agent plus
  cross-validation duplicate check.
- `/api/v2/items/{id}/score` Stage-1 mean table score or Stage-2 use-case-weighted
  score, with Critical=5, High=3, Medium=1.
- `/api/v2/framework/overview|areas|tests|matrix` workbook-backed reference pages.

Legacy routers unmounted in `backend/main.py` with `# GALILEO-UNMOUNTED:`
comments: `monitoring`, `test_plan`, old `test_lab`, old `dq_framework`,
`skills`, `use_cases`, `preview`, `tests`, and `ai_rules`. Auth, admin,
inventory, ingestion, datasource, upload, test_library, tickets, and RCA remain
mounted.

**Feedback build 2026-07-08 evening (all gates green — 16/16 registry self-check,
33-check headless E2E, npm build + eslint clean):**
- **Published parameter specs** — `backend/dq_tests/param_specs.py` is the single
  source of truth per test: `test_type` / `main_input` / `reference` + every
  tunable param `{key,label,default,description}`; defaults mirror the registry
  fallbacks. Surfaced as `param_spec` on `GET /framework/tests` (which also now
  returns `python_code` via `registry.get_impl_source` and `stage_label`) and on
  every plan row from `GET /items/{id}/plan`. `service._default_params` reads the
  spec (no more `source_text` in plan params).
- **Stage display names** — user-facing labels are **Systemic** (stage1) /
  **Specific** (stage2) everywhere: overview titles, `stage_label` fields, score
  stage, PDF header, roster; internal keys stay `stage1`/`stage2`.
  Frontend helper: `dq-studio/src/lib/stages.js`.
- **Finalize gating** — `finalize_plan` skips rows with blank `columns_json`
  (returned in `skipped`), no longer finalizes un-accepted `proposed` incremental
  rows, and auto-generates the snippet for each newly finalized row. Snippets
  carry a documented header (test/type/table/main input/reference/params);
  `snippet(action="regenerate")` resets `approved`. `plan_status` labels
  Empty/Pending/Under review/Finalized/Executed + per-scope
  `{total,finalized,approved,executed}` counts.
- **Sourcing** — `save_file` returns a per-tab `summary` (rows/columns) for data
  AND support files; `_parse_dictionary` accepts JSON dictionaries
  (nested/flat/list forms); `get_inventory` preserves original file column order
  and adds a prose `profile_summary`; Completeness/Post-outcome plans span all
  columns.
- **Frontend restructure** — Data Inventory: static Database/Dataset explainer
  cards + separate sections, no Add Item. Data Sourcing: card-first single-item
  flow (excel-only data; dict/schema excel+json; "Schema details" row), chosen
  file shows name only, per-file progress + summary tables, explicit
  target-variable prompt gate, and the **Variable inventory moved here** from
  Test Lab. The historical 0.3 Test Lab was **4 steps** (Framework Application → Execute
  Framework Tests → AI Recommendation → Execute Incremental Tests); item
  dropdown lists only profiled items; Step 1/3 show the ParamEditor table
  (label/description/default/current) via `testlab/PlanParts.jsx`; Steps 2/4
  group rows per table with a table filter, status strip, and Show Current Code /
  Regenerate / Approved (green `success` button variant, click-again-to-unlock)
  — only finalized tests appear. Test Library publishes the param table + the
  pre-built python per test; nav order moves it below DQ Framework. DQ Framework
  Areas tab = Systemic/Specific sub-tabs grouped under bold L1 themes ("Both"
  areas in each); Matrix uses fixed-width heat chips + column borders. Issue
  register load failures degrade to a friendly empty state.

**Feedback build 2026-07-08 night (release `galileo` #2 — 16/16 registry
self-check, 20/20 g18 headless smoke, CI gate green):**
- **Planner column gating (4.4)** — PSI blocks `datetime` (allowed:
  numerical/ordinal/categorical); Correlation stability allows numerical only.
  `_choose_columns` no longer appends date columns to PSI/Correlation/Drift
  selections — the date/segment reference resolves from `supporting` at run
  time. Every plan row now carries `column_spec` (`allowed_classes` /
  `blocked_classes`); `GET /framework/tests` adds the same as `column_rules`.
- **User add-test (4.3)** — `POST /items/{id}/plan/add` →
  `service.add_plan_row`: validates registry membership, table/columns, the
  runs-on/not-suitable type rules, and duplicates; origin `user_added`, status
  `pending`. UI: Step 1 "Add a test" panel + `ColumnPicker` dropdowns (eligible
  types only) inside `ParamEditor` (Steps 1 & 3).
- **Execution progress (4.5)** — `service.execute_iter` yields per-test
  `{done,total,...}`; `/execute/stream` emits `phase:"progress"` SSE events;
  Step 2/4 render a progress bar with an ETA. `execute()` wraps the iterator.
- **Bulk actions (4.1/4.2)** — "Finalize All Tables" (Step 1, multi-table) and
  "Approve All" (Step 2/4). **Skip (4.6)** — Step 2 "Skip Steps 3 & 4" patches
  status to `testlab_step4` and jumps to the roll-up.
- **RCA analyses fixed + interpreted (5.1)** — `create_analyses` rows are
  `pending` (the earlier `proposed` exclusion in `finalize_plan` silently
  blocked execution); new `POST /issues/{id}/analysis/interpret` →
  `interpret_analyses`: deterministic per-analysis summaries + LLM-polished
  overall (fallback-safe). UI shows AI summary per result + overall panel.
- **Tracked issues (5.2/5.3)** — copy is just "For escalation"; raising keeps
  the user on the workflow and shows a `TrackedEditor`
  (owner/priority/target-date/status) backed by `PATCH /issues/tracked/{id}`.
- **Sourcing (2.1-2.7)** — dictionary + schema mandatory and Excel-only; name
  starts blank; `uploadItemFileV2WithProgress` (XHR) gives per-file progress %
  + ETA with a non-blocking UI; re-upload allowed until profiling; inventory
  `notes` start blank; button says "Save".
- **deploy.ps1** — always rolls the backend with a unique `--revision-suffix` and
  gates on the *serving revision* matching it (kills both stale-deploy modes).

**Feedback builds 2026-07-08 11PM (`215c02b`), midnight (`02d4e06`) and
2026-07-09 2AM (`310b83b`) — releases `galileo` #3/#4/#5, smokes g19/g20/g21:**
- **11PM — Inventory rewrite (1.1-1.8)**: `list_items` enriched with
  `health_score` (scores_v2 final), `active_issues` (open/In-RCA/escalated
  count) and `target_description`; table shows Name / Status (Title Case) /
  Last Active Module / Health Score / Active Issues / Use case / Target
  Variable (description, name on hover); live in-progress/completed counts on
  the two kind cards; one "Search" over both sections. **Duplicate names
  (2.5)**: `create_item` rejects case-insensitively; DataSourcing warns beside
  the field and blocks Upload. **Layout (4.1/4.2)**: `min-w-0` grid children +
  `max-w-full` pre keeps Show Code scrollbars internal; Step-4 area breakdown
  table deleted.
- **Midnight — univariate split (4.1)**: `_SPEC_OVERRIDES` marks MAD / Tail /
  MNAR / Leakage / PSI `"univariate": True`; `build_plan` + `add_plan_row`
  split multi-column selections into one plan row per column;
  `column_spec.max_columns=1` caps the pickers; results carry their plan
  columns and the Results table shows a Columns column. **Processing ETA
  (2.1)**: after upload bytes finish, a countdown estimated from file size
  (~1.5 MB/s, 2 s floor) ticks until the server responds.
- **2AM — per-column completeness (1.1/1.2)**: Completeness / missing-rate is
  univariate too — one test per applicable column; `_completeness` judges every
  column it gets (any over threshold fails; `failed_columns` + per-column
  rates in evidence; no worst-of reduction). MCAR's pattern-rate spread is
  inherently joint and stays multivariate. **Full-stage scope (2.2)**:
  `_candidate_areas` returns all 11 areas and `build_plan` unions
  `stage1_tests_json` + `stage2_tests_json` for BOTH kinds — datasets and
  databases each consider all 16 tests; target-dependent tests are removed
  with reasons by the feasibility probe. PDF label is now "Full Framework
  Assessment Report"; copy updated in Step 1, DQ Framework, Data Sourcing and
  `framework_overview`.

**Feedback build 2026-07-09 10AM (`03985cd`) — release `galileo` #6, smoke g22
21/21:**
- **Rename (3)**: "Hard plausibility rule check" → "Plausibility rule check"
  in the registry, param specs, stage1 module and rule router; the workbook's
  original name maps over via a `TEST_ALIASES` entry at seed time.
- **Granularity model (4.1) — supersedes the midnight plan-row split**: the
  plan keeps ONE row per test per table listing ALL applicable columns; the
  per-variable split moved to EXECUTION. Univariate snippets loop
  `for column in columns` and return a list; `execute_iter` stores one
  `results_v2` row per entry with its own `columns_json` (new column +
  migration) and progress counts executions. Results, report, score, issues
  and `_executed_pairs` read the per-execution columns — issues list only the
  actually-failing variables. `_column_rules` now exposes
  `per_column` instead of `max_columns=1` (UI chip "runs per column — one
  result each").
- **Applicability sweep (4.1)**: `_choose_columns` evaluates every variable —
  `_feature_columns` applies business judgment (classification `numerical`
  AND no name-part in `_NON_FEATURE_TOKENS`: id/year/date/time/…), so
  PSI/MAD/Tail/MNAR/Leakage/MCAR get EVERY meaningful model feature and never
  Year/ID/Timestamp fields.
- **Recommender (4.2)**: every rule × variable candidate across all tables is
  gathered, cross-validated (univariate candidates trimmed to columns not yet
  executed/planned — same test on a new variable stays incremental), scored
  by `_candidate_score` (failure-driven +30, combined-rule coverage +20,
  use-case/CRE/GEN bucket +15/10/5, +2 per matched field) and ranked;
  `TOP_RECOMMENDATIONS = 5` become proposals, each carrying `score` in
  `crossval_json`.

**Feedback build 2026-07-10 11AM (`1c0c07f`) — release `galileo` #7, smoke g23
22/22:**
- **Real snippet code (4.1)**: `service.snippet` inlines the registered
  implementation source (`get_impl_source`) verbatim into every generated
  snippet with the actual column list and params — the sandbox executes the
  inlined function, so the visible code IS the code that runs (no opaque
  `run_registered` call; that path remains only as a fallback for unknown
  tests). `_SNIPPET_HELPERS` scan imports only the result-shaping helpers the
  source actually uses. Sandbox safe builtins gained `next`/`iter` (the
  inlined PSI/drift implementations need them).
- **Per-column issues (5.1)**: one issue per failed test still, but
  `issues_v2.column_details_json` (DDL + migration) now stores every failing
  column's own metric/threshold/violation count; `get_issue` attaches
  per-column example rows + evidence; the RCA cause enumerates each failing
  execution, `_profile_facts` is uncapped, `_suggest_analyses` emits segment
  breakdown / deep profile / cross-check for EVERY failing column, and AI
  solutions name all columns. `IssueRca.jsx` renders a "Per-column results"
  table (the single example-rows block remains for legacy rows only).
- **Saved-inventory UX (2.1/2.2)**: the inventory "Saved" label persists
  after Save and clears on any edit; the Test Lab gained a collapsible
  "Variable inventory of saved data" section (pick any saved item →
  read-only `VariableInventory` via the new `readOnly` prop).
- **Offline deliverables (7)**: retired historical lineage document
  (module/code lineage tables + module-level and per-module mermaid charts)
  and `Test_Detail_Library_Input_Specifications.xlsx` (one entry per test:
  every input with description / value-source type / default, actual
  statistical method, pass/fail threshold; flat + JSON-per-test sheets,
  generated from live `PARAM_SPECS`/`TEST_SPECS`).

**Entry:** `main.py` — loads Azure config, runs `system_db.init_schema()`, seeds agents + static
content on first boot (backfills authz on existing systems), mounts 13 routers, CORS for
`localhost:5173/5174/5175`, exposes `GET /health`.

### 4.1 Data layer (two databases — do not confuse them)
- **`system_db.py` → `backend/system_state.db`** (SQLite): ALL platform mutations. Tables: `users`,
  `app_fsm`, `ingested_databases`, `table_metadata`, `test_library`, `test_plan`, `monitoring`,
  `run_results`, `health_scores`, `tickets`, `hitl_decisions`, `agent_skills`, `transaction_log`,
  `object_contexts`, `context_links`, `rca_sessions`, `rca_steps`, `dq_framework_areas`,
  `dq_framework_families` (static DQ-Framework taxonomy — seeded every boot, reset-preserved),
  `test_db_links` (**Test Lab P1** — DB↔test catalogue, one row per (plan_id, logical_db); drives
  cascade delete + catalogue views). `test_plan` now carries an additive JSON `operands` col
  (`[{logical_db,table,field}]`; `table` stays the canonical first operand — backward compatible).
  **Test Lab P2A:** `test_dossiers` (one row per designed test — the Dossier: rationale /
  quantitative_outcome / contextualization / strategy / monitor_recommended / monitor_frequency +
  `dialogue` JSON for the HITL shuttle + `current_version` pointer) and `test_versions` (append-only
  snapshots `{operands,params,thresholds,python_code,dossier}` powering undo/redo + HITL history).
  Both purged by the DB-delete cascade and reset. **Test Lab P4:** `design_workbench` (a named,
  resumable design session — `scope` JSON {dbs,families} + `plan_ids` JSON soft-refs into test_plan
  + notes/status; work-product, cleared on reset). **Test Lab P5:** `schedules` (a monitoring schedule
  over designed tests — `plan_ids` JSON + frequency + `delivery_channel` (download|email) + recipients +
  enabled + next_run/last_run/last_status; NO background runner — run/tick on demand) and `notifications`
  (the design-only email OUTBOX — subject + `body_md`; status stays `drafted`, never sent). Both
  work-product, reset-cleared.
  JSON cols encoded on insert / decoded on read. `reset_demo` is **surgical** (clears only demo-DB
  inventory + work-product; preserves users + static `test_library` + the DQ-Framework tables);
  cold boot uses `seed_all`.
- **Retired `database.py` → historical analytical warehouse** (read-only,
  **the only target** — has `_planted_issues` answer key; I1=PSI/bureau_score, I3, I4=leakage).
  Logical DBs: `retail_fraud_db`, `wholesale_irb_db`, `retail_risk_db`. Physical tables:
  `customers`, `retail_accounts`, `transactions`, `obligors`, `macro_scenarios`.

### 4.2 Routers (prefix → key endpoints)
| File | Prefix | Endpoints (abridged) |
|------|--------|----------------------|
| auth.py | `/api` | login, logout, me (GET/PUT) |
| admin.py | `/api/admin` | users (GET/POST), users/{u} DELETE, reset, context-memory |
| inventory.py | `/api/inventory` | list, tables |
| ingestion.py *(Test Lab P1)* | `/api/ingestion` | **DELETE `/{logical_db}` now CASCADES** — purges every test referencing the DB (test_plan + run_results + monitoring + health_scores + test_db_links + **test_dossiers + test_versions (P2A)**; resolves plan_ids via `test_db_links` OR table-name legacy fallback) and returns `tests_deleted` + `tests_shared_other_dbs` for a UI warning. No more orphaned tests (brief pt 13). |
| test_lab.py *(Test Lab P2/P3/P4)* | `/api/test-lab` | **Designer + Recommendations + Catalogue + Report + Workbench backend.** P2B: `GET /tools` (Test Kit catalogue + framework area tags + coverage), `GET /scope` (designable tables+columns). P3: `GET /domains` (loaded DBs + canonical families + declared metadata — the Scope&Domains selector), `POST /recommend` (deterministic framework-prioritized recs merged across selected DBs → `{univariate, multivariate, coverage}`), `GET /recommend/stream` (SSE — Merton `suggest_hypotheses` + Poincaré `discover`, streamed, → column-bound recs). P2A: `POST /design`, `GET/DELETE /design/{id}`, `GET /design/{id}/run/stream` (console-before-result), `POST /design/{id}/interpret` (Merton Dossier), `POST /design/{id}/feedback` (HITL shuttle — Bayes), `POST /design/{id}/restore` (undo/redo), `GET /backtest`. Interpret + feedback consume upstream DB context + framework_brief. **P4:** `GET /catalogue` (index of every designed test — one row per dossier, w/ shape+framework areas from Test Kit + `logical_dbs` from links + filters db/area/shape/status + facets), `POST /report/synthesize` (fresh-run each selected design → stats + Merton `consolidate_dossiers` editable exec narrative), `POST /report/pdf` (→ `ai/test_report_pdf.generate_consolidated_pdf` — native fpdf charts + narrative + evidence table, StreamingResponse application/pdf), `POST/GET /workbench`, `GET/PATCH/DELETE /workbench/{id}` (save/resume design sessions). **P5:** `POST/GET /schedules`, `GET/PATCH/DELETE /schedules/{id}`, `POST /schedules/{id}/run` (manual run — fresh-runs the tests, drafts an email notification into the outbox on the email channel; nothing is sent), `POST /schedules/tick` (runs enabled schedules whose next_run is due — stands in for the absent background runner; `list_schedules` returns `scheduler_active:false`), `GET /notifications` (design-only outbox). **#4:** `POST /join/suggest` (dry-run the multi-source frame assembly for a set of operands → join plan + evidence + fan-out warnings, for cross-table/cross-DB design + HITL join-confirm). |
| ingestion.py | `/api/ingestion` | available, catalog, schema, ingest, record-counts, summary, **summary/stream (SSE, 15s heartbeat; returns key_anomalies_md/suggested_hypotheses_md)**, **{db}/state (resume bundle, R5/R6)**, summary-text (PUT — saves summary + 2 markdown sections + context-memory), select-tables |
| datasource.py | `/api/datasource` | sources, browse, dictionary/parse, dictionary/generate, **dictionary/generate/stream (SSE, R5)**, {db}/relation-template, relation-suggestions, **relation-suggestions/stream (SSE, R6)**, relations/validate, report |
| test_library.py | `/api/test-library` | list, get, create, update, generate, **generate/stream (SSE)** |
| test_plan.py | `/api/test-plan` | list, summary, CRUD, finalize, screen, criticality, instances, **run + run/stream (SSE)**, health |
| monitoring.py | `/api/monitoring` | list, recommend, get, create, backtest |
| tickets.py | `/api/tickets` | list, get, create, update |
| rca.py | `/api/rca` | start-table, eligible, status, approve-code, result, steps, check, sandbox, raise-ticket |
| skills.py | `/api/skills` | list, architecture/map, get, update |
| dq_framework.py | `/api/dq-framework` | overview, areas, families, family/{id}, matrix (static reference taxonomy → Framework module UI) |
| *(legacy, harmless)* tests.py / ai_rules.py / use_cases.py / preview.py | `/api` | old use-case contract; unused |

### 4.3 AI layer (`ai/`)
`llm.py` (lazy Azure client; `get_client(house=…)` seam reserved for future multi-endpoint routing;
**R5: bounded `timeout=90s`/`max_retries=1` via `AI_REQUEST_TIMEOUT`/`AI_MAX_RETRIES` so a slow call can't hang an SSE worker**),
`control_plane.py` (**Plan 8** — system-owned role→{model,house,temp_bucket,effort} config;
`resolve(agent_key)`; all roles = gpt-4.1 today, `future_model` records cross-house intent; 3-bucket
temp policy deterministic/low_variance/diverse), `prompts.py`, `effort.py` (**EffortPolicy: low/med/high
— no fast path**; accepts `agent_key` → pulls model+temp from control_plane; `_one_pass` streams
tokens), `tool_registry.py` (**Plan 8** — role-gated, schema-validated tool surface wrapping existing
engines; egress contracts metadata_only/id_only/score_only; tools: fetch_schema_stats,
calculate_health_score, calculate_criticality, execute_sandboxed_code, raise_mitigation_ticket,
repair_and_retry, parse_data_dictionary), `skill_form.py` + `skills.py` (**Plan 8** — governed `.md`
FORM: SYSTEM-OWNED block [role/IO/break/tool-whitelist/output] validated strictly at load,
USER-OWNED block editable; `save_prompt` locks the SYSTEM block; registry over `skills/*.md`),
`test_manager.py` (**Gauss** — multi-agent test-gen loop ≤3 iters, SSE, dry-run-validates generated
code, **bounded sandbox auto-repair ≤2 attempts** with retryable-vs-surfaced error classifier before
fallback), `test_execution.py` / `test_worker.py` (real code exec per instance), `code_sandbox.py`
(ast + import allowlist: pandas/numpy/scipy/math/statistics/datetime + **statsmodels/sklearn (Test
Lab P1 — unlocks ADF/KPSS/VIF/AUC-Gini gap tests)**; AST/attribute guards unchanged), `dict_ingest.py`
(data-dictionary extract[xlsx/pdf/docx/csv/txt/json]+synthesize → strict {table:{col:desc}} JSON;
route `POST /api/datasource/dictionary/parse`), `rca_agent.py`, `rca_checker.py` (**Noether**),
`rca_helpers.py`, `db_understanding.py` (**Newton** — profiling/summary, was Lovelace; now emits
deterministic, framework-tagged **Key Anomalies** in place of "Assumptions"), `credit_risk_domain.py`
(**Merton** — 'potential usages' paragraph + framework-grounded **Suggested Test Hypotheses** in place
of "Open Questions"; hypotheses are schema-validated + deep-link into Define Test Plan),
`framework_context.py` (**DQ Framework service** — the bridge that maps a DB's declared
`model_families`/`use_cases` → prioritized in-scope assessment areas + runnable diagnostics; shared by
Newton, Merton and Poincaré so all three reason against the same family-weighted rubric;
`use_case→family` fallback for finer-grained regulatory labels), `context_memory.py`,
`recommend.py` (**Test Lab P3** — recommendation engine: `framework_recommendations(logical_db,
families)` DETERMINISTIC (in-scope prioritized areas × framework-tagged Test Kit tools → univariate/
multivariate + coverage/gap; `_suggest_targets` binds each tool to concrete TABLE.FIELD targets via
`table_metadata.datatypes` + `test_screening._kind` + `_VALUE_HINTS` weighting — fans to ≤3 field-bound
cards, multivariate→column pairs), `merge_recommendations` (across DBs, de-dup by tool+target),
`ai_recommendations(logical_db,
families, section, on_event)` (loads Newton DiscoveryState → Merton `suggest_hypotheses` + Poincaré
`discover`, column-bound, streamed); `_DIAGNOSTIC_TEST` maps a diagnostic → a library test_id so recs
open in the Designer).
`bayes.py` (**Test Lab P2A** — `adjudicate(test_state, feedback, db_context, framework_brief)` HITL
shuttle: weighs human feedback, returns strict-JSON `accommodate`(+proposed_changes) /
`push_back`(+empathetic message); locked `BAYES_SYSTEM` + editable `skills/bayes.md` overlay; no tools
— the router applies changes as a new version). Merton gained
`interpret_result(test, result, framework_brief, db_context)` in `credit_risk_domain.py`
(contextualizes the deterministic run into the Dossier — never invents metrics). **Both consume the
UPSTREAM DB CONTEXT** the Designer assembles via `test_lab._db_context_for` (Newton's `ai_summary` +
`key_anomalies_md` and Merton's `suggested_hypotheses_md` off `ingested_databases`) **and the
DQ-Framework `framework_brief`** (`_framework_brief_for` → `framework_context`), so a designed test is
reasoned about in light of what we already know about the DB (brief pt 1). The design bundle surfaces
both as `db_context` + `framework_brief`.
`test_kit.py` (**Test Lab P1** — ONE federated, versioned **Test Kit registry**: catalogues every
statistical *tool* the platform composes tests from by **reference, not relocation** — 10 calibrated
`gx/metrics` fns + the 12 vetted `rca_helpers.HELPERS` + the **25 seeded `test_library` rows** = 47
available. The 13 credit-risk gap tools (rolling-PSI trajectory, IV/WoE stability, Gini/AUC decay, VIF,
ADF, KPSS, Ljung-Box, Mann-Kendall, changepoint/CUSUM, cohort-curve monotonicity, special-value/sentinel,
Benford, business-rule) were **implemented in Test Lab #1 (2026-07-01)** as real runnable library rows —
`_PLANNED_TOOLS` is now empty. `shape`∈{univariate,multivariate}; **time-series is a FACET** not a
3rd shape. **Every tool is tagged to its DQ-Framework `area_id`(s)** via the curated `_TOOL_AREAS` map
(validated against `dq_framework_areas`); `list_tools(shape/facet/source/area, include_planned)`,
`get_tool`, `get_callable`, `coverage_by_area()` (area→tools; surfaces gap areas e.g. external-vendor),
`area_labels()`. Exposed via `GET /api/test-lab/tools` (P2B)).
`recommend.py` (**Test Lab P3**) — deterministic `framework_recommendations` (area × framework-tagged
Test Kit tool → uni/multi + coverage; **round 6** `_suggest_targets` binds each card to concrete
TABLE.FIELD via `table_metadata.datatypes` + `test_screening._kind` + `_VALUE_HINTS`) + `merge_recommendations`
(de-dup by logical_db+test_id+table+columns) + `ai_recommendations` (Merton+Poincaré streamed).
`test_report_pdf.py` (**Test Lab P4**) — `generate_consolidated_pdf`: the consolidated multi-test report,
drawn with **native fpdf primitives** (stacked outcome bar + coverage-by-area + criticality bars — NO
matplotlib dependency), reusing `report_pdf`'s markdown/Latin-1 render engine; embeds Merton's four-section
narrative + a per-test evidence table. Merton's `credit_risk_domain.consolidate_dossiers` writes that
narrative (deterministic fallback if the model is unavailable, so the download never fails).
`frame_assembler.py` (**Test Lab #4** — multi-source frame assembler for cross-table / cross-DB tests):
`assemble(operands, join_spec)` builds ONE working frame from operands spanning tables/DBs — join edges
resolved by priority **confirmed `join_spec` → validated `ingested_databases.relations` (same DB) →
deterministic value-overlap key probe** (`_infer_key`, same name-grounded/dtype/containment logic as
`relationship_join.compute_evidence`); collision columns get a `__<table>` suffix; returns the join plan
+ fan-out/low-overlap/no-path warnings. `suggest_joins` is the df-less dry-run. Wired into `_run_design`
+ `run/stream` via `_assembled_frame`/`_assembly_note` (single table = unchanged plain load; the join
plan prints to the terminal before the result). Reads come from the one physical warehouse, so cross-DB
is logical.
`discovery_state.py` anomalies are now **tiered** (null ≥20% / ≥50% / =100%, cardinality=1, numeric
range/plausibility: negative_values / range_violation / extreme_outliers) with stable code-token
strings consumed by `framework_context.ANOMALY_AREA_MAP`. **Plan 8 gate:** `verify_plan8.py` (36 asserts:
control plane, registry gating/schema/egress, repair classifier, governed FORM).

### 4.4 Agent roster (`ai/agents/` + `skills/*.md`)
Ordered (`agent_skills.seq`): **Gauss** (orchestrator) · **Poincaré** (relationship discovery; now framework-aware —
`test_manager` injects the DB-family `framework_brief` so candidates favour the highest-priority in-scope
areas) · **Fermat** (relationship validation) · **Euler** (cross-table consistency) · **Hypatia**
(library screening; Search / Deep Search — wired into the Gauss loop) · **Pascal** (criticality) ·
Feynman (display) · **Noether** (RCA check) · **Newton** (DB understanding + Key Anomalies) · **Merton**
(`credit_risk_domain.py` — credit-risk SME; usage paragraph + Suggested Test Hypotheses; **+ Test Lab
P2A `interpret_result` → writes the Dossier**; **+ P4 `consolidate_dossiers` → the consolidated
report's executive narrative**) · Laplace (display) · **Bayes** (`ai/bayes.py` — Test
Lab P2A HITL feedback adjudicator / "shuttle"; parent=gauss, seq 15).

### 4.5 Key dependencies
`fastapi>=0.115`, `uvicorn[standard]>=0.32`, `pandas>=2.0`, `numpy>=1.26`, `scipy>=1.13`,
`great-expectations>=1.3` (dormant), `openai>=1.50`, `python-dotenv>=1.0`,
`statsmodels>=0.14`, `scikit-learn>=1.4` (**Test Lab P1** — sandbox-allowlisted for the gap battery).

### 4.6 Test execution & calibration (the core contract)
Each `test_library` row carries self-contained parameterised `python_code` that **is the single
source of truth** — executed for real in the sandbox, printing **CLI diagnostics (`console` frame)
before the structured `result`**. 12 tests / 4 categories (C1 psi/ks/vintage/maturity/regime,
C2 trend, C3 missing/outlier/corr_stability/feature_drift/monotonicity, C4 drift_decomp). PSI inlines
`gx/metrics.compute_psi` verbatim so calibration holds. **Calibration gate: `spike_recalibrate.py`**
must show PSI threshold 0.20 → I1 0.2867 / I3 0.2052 FAIL, clean 0.0162 PASS. `_planted_issues` is
the invisible oracle. Run this gate after any change touching DQ logic.

---

## 5. Frontend (`dq-studio/`)

**React 19 + Vite 8 + Tailwind 4**; routing via react-router-dom 7; UI via radix-ui + shadcn +
lucide-react; charts via recharts; product tour via driver.js. Scripts: `dev`, `build`, `lint`,
`preview`.

- **API gateway:** `src/api/client.js` — `API_BASE = http://localhost:8001/api`; Bearer token from
  localStorage (`dq_token`); ~60 functions; SSE via EventSource (`summaryStreamUrl`,
  `generateStreamUrl`, `runStreamUrl`, `runInstanceStreamUrl`).
- **Contexts (`src/context/`):** `AuthContext` (session/token), `WizardContext` (scope: logicalDb +
  tables + selected tests + results), `TourContext` (tour flags).
- **DataSourcing wizard (R5/R6/R7, 5 steps):** Source → Browse → **Context & Dictionary** (`WorkingOn` banner names the DB; criticality/use-cases/model-families + optional description + data dictionary, with streamed Newton "thinking" + Stop) → **AI Understanding** (Newton+Merton stream via `AgentConsole`, Stop; **three markdown editor+preview blocks** — Summary (**## Findings / ## Potential Usages**, bolded keywords), Key Anomalies, **Potential** Test Hypotheses — saved with the DB via `MarkdownEditor`/`lib/markdown.js`; Download Report) → **Relationships** (Codd **streamed** on demand with Stop — NOT auto; upload .json; **inline live ERD**; **Save & Continue at top**; optional). "Your databases" rows are clickable to **resume** a loaded DB (prefills every step incl. the 3 markdown blocks via `/ingestion/{db}/state`, no re-ingest). Terminal Save sets wizard scope but does NOT redirect (manual nav). The 2 markdown sections persist to `ingested_databases` cols + `object_contexts` (`key_anomalies`/`suggested_hypotheses`) for the test module to retrieve later. No per-item "Add to plan" deep-link (R6 decision). **R7:** picking a different DB / resuming fully resets per-DB wizard state (`resetWorkflowState` — fixes stale pre-selection + the `ingested`-flag leak that 404'd "Update & re-run"); the browse list clears after a fetch; AI Understanding shows a neutral **"Aegis Labs AI"** credit (internal agent code-names hidden there). The **report is MARKDOWN-DRIVEN + Latin-1-sanitized** (`ai/report_pdf.py::_ascii`) so it can't crash on em-dashes/curly-quotes (the "download does nothing" bug); opens with a Tables/Total-records headline. Agent style now lives in `skills/newton.md` + `skills/merton*.md` (runtime prompt), NOT the `.py` `_SYSTEM` fallbacks — **bold keywords, single-digit-numbers-as-words, and readable prose are set there**.
- **Pages (`src/pages/` → route):** Login `/login` · Inventory `/` · DataSourcing `/data-sourcing` ·
  DefineTestPlan `/define-test-plan` · RunValidations `/run-validations` · RCA `/rca` · TestLibrary
  `/test-library` · **TestLab `/test-lab` (Test Lab — a 5-step module: (1) Scope & Domains selector
  (multi-DB + domain/family pills with "All" toggles, P3); (2) Recommendations — two sections
  Univariate|Multivariate, each with a framework-prioritized deterministic grid + a "Recommend with AI"
  runner (AgentConsole+Stop over `/recommend/stream`) + a coverage strip (covered/gap), P3; (3) the
  Designer — code+terminal SSE run (console-before-result) → backtest mini-viz → AI Dossier (Merton) →
  HITL feedback shuttle (Bayes) with version Undo/Redo + upstream-DB-context panel, P2B; (4) **Catalogue
  (P4)** — index of every designed test with db/shape/area/status filters + multi-select → **Consolidated
  report** panel (fresh-run + Merton editable exec narrative → Download PDF via `/report/pdf`) **and
  "Schedule selected"**; (5) **Schedules (P5)** — monitoring schedules over designed tests (cadence +
  delivery channel download|email + recipients), Run now / Run due (tick), enable toggle, and a
  **notification preview + outbox** (email is drafted, never sent — honest "no background runner / no SMTP"
  banner). Each recommendation "Add to Designer" grows the Designer **inline** (no screen swap, round 6); a
  header **Workbench** control saves/resumes a design session (scope + selected tests) via `/workbench`)** · ScheduledRuns
  `/scheduled-runs` · IssueManagement `/issues` · AgenticSkills
  `/agentic-skills` · Dashboards `/dashboards` (mock) · **DQFramework `/dq-framework`** (reference
  taxonomy: Explorer / Model Family Lens / Importance Matrix) · Admin `/admin` · Profile `/profile`.
- **Notable components:** `AgentConsole.jsx` (live streamed agent "thinking" + dark CLI panel),
  `AgentOrgChart/AgentTree.jsx`, `BrandLogo.jsx`, `WizardLayout/WizardStepper.jsx`, `ui/` (shadcn).
- **Themes:** Minimalist (default) / Digital / Bold.

Inventory + Dashboards run on mock data; everything else is wired live to the backend.

---

## 6. Runtime source-of-truth artifacts

Archimedes uses `backend/knowledge_base/dq_framework_data.json` for framework
taxonomy, `backend/knowledge_base/business_rules.json` for rules, and
`backend/seeds/data/users_config.json` for seed users. Assessment data is
provided by user uploads. The historical `strategy-docs/` directory was retired;
its full lineage is recorded in `docs/strategy-docs-lineage.md`.

---

## 7. How agents should work in this repo (operating policy)

1. **Read this TSD first.** Use it to locate the right file/route/module. Only fall back to
   codebase search when the TSD doesn't resolve the question — then update the TSD if you learned
   something durable.
2. **Model routing (cost discipline):**
   - *Searching / locating code in the repo* → cheap/primitive model, **low** thinking (e.g. Explore agents).
   - *Hard reasoning* — architecture, quantitative/calibration logic, cross-component integration,
     RCA design → **frontier model, high / very-high** effort.
   - *Mundane work* — doc updates, simple single-file edits, rote wiring → **sub-agents**, not the
     top-tier model.
3. **Chief-Architect delegation:** the orchestrator retains quantitative/integration/stateful logic
   and delegates myopic single-file coding to context-firewalled sub-agents (one file each, no
   overlap; return only the diff + a pass/fail line). Always re-verify delegated work through real
   integration, not just imports.
4. **Verification gates before declaring done:** backend boot · `spike_recalibrate.py` PASS ·
   `npm run build` clean · live Azure SSE token smoke (the historically-pending gate).

---

## 8. Status snapshot (point-in-time — verify before relying)

Plans 1–6 built & verified. Plan 6 (Feedback Round 3) = code complete; open gate = **live Azure SSE
token smoke**. **Plan 8 (Agent Orchestration Optimization) = code complete:** control-plane role
config, typed tool registry, bounded sandbox auto-repair, and governed `.md` FORM — all on gpt-4.1
(cross-house model swap deferred to a follow-up; `llm.py` multi-endpoint router parked). Gates green:
`verify_plan8.py` 36/36, `spike_recalibrate.py` PASS, `npm run build` clean, full backend import OK.
**Feature: DQ Framework (2026-06-30) = built & verified.** New module between Dashboards and Admin
(Explorer / Family Lens / Importance Matrix) backed by `dq_framework_*` tables + `dq_framework_seed.py`;
`framework_context.py` activates the previously-dead `model_families`/`use_cases` so Newton emits
deterministic Key Anomalies, Merton emits framework-grounded Suggested Test Hypotheses (deep-linked into
Define Test Plan), and Poincaré discovery is family-prioritized. **Live Azure SSE token smoke EXERCISED
& PASSED** on 2026-06-30 (real `understand()` end-to-end: Newton + Merton on gpt-4.1) — the
historically-open gate is now closed. Optional cleanups: code-split the ~1.21 MB bundle; register the
two deferred tools (`commit_test_logic`, `infer_relationships`). Full handoff:
`~/.claude/plans/Plan-6-PROGRESS-HANDOFF.md`. Live build state lives in memory `intelligent-dq-status`.

**Test Lab redesign (2026-06-30) — Phase 1 foundations BUILT & VERIFIED.** Overhaul of the test
section into a bigger **"Test Lab"** module + persistent **"Design Workbench"** session (design doc:
historical Test Module Redesign Proposal; decisions in memory `test-lab-redesign`). Phase 1
shipped the substrate only (no UI yet): (1) `ai/test_kit.py` federated Tool registry; (2) additive
`test_plan.operands` JSON (Tools-vs-Tests operand model → cross-table/cross-DB ready); (3) `test_db_links`
catalogue + **cascade delete** fixing the confirmed DB-delete orphan bug; (4) `statsmodels`/`scikit-learn`
allowlisted. **All gates green:** `spike_recalibrate.py` PASS (I1 0.2867 / I3 0.2052 / clean 0.0162 —
unchanged), `verify_plan8.py` 36/36, full backend import OK, operand round-trip + cascade verified via
TestClient, `npm run build` clean, `reset_demo` idempotent. Deferred to later phases: Designer UI +
multi-source frame assembler, Recommendations/Scope&Domains, Workbench persist/resume, PDF charts +
consolidated report, Fisher retirement, Monitoring scheduler/email, the 13 planned
gap-test implementations.

**Test Lab Phase 2A (2026-07-01) — Designer BACKEND substrate BUILT & VERIFIED.** Backend-only slice
(UI is 2B): `test_dossiers` + `test_versions` tables (Dossier + undo/redo + HITL history); new agent
**Bayes** (`ai/bayes.py`, registered control_plane+skills+`skills/bayes.md`+`BAYES_SYSTEM`) for the HITL
"shuttle"; **Merton** `interpret_result` writes the Dossier from the deterministic run; new
`/api/test-lab` router (create design → `run/stream` console-before-result → `interpret` → `feedback`
(accommodate/push-back) → `restore` undo/redo → `backtest`). **All gates green:** backend boot +
migration, `spike_recalibrate.py` PASS (unchanged 0.2867/0.2052/0.0162), `verify_plan8.py` **37/37**,
full TestClient design→run→interpret→accommodate→push-back→undo→backtest flow, **live Azure SSE smoke
PASSED** (real Merton interpret + Bayes accommodate & push-back, parseable JSON), DB-delete cascade now
purges dossiers/versions, `reset_demo` idempotent. **Context grounding (post user double-check):** both
Merton interpret AND Bayes adjudicate now consume the upstream DB context (Newton `ai_summary`+Key
Anomalies, Merton Suggested Hypotheses) + the DQ-Framework brief — verified live (Merton cites "flagged
anomalies", Bayes reasons from "IRB context").

**Test Lab Phase 2B (2026-07-01) — Designer UI + framework-tagged Test Kit BUILT & VERIFIED.**
(a) Every Test Kit tool now carries `framework_areas` (curated `_TOOL_AREAS`, validated vs
`dq_framework_areas`; `coverage_by_area` flags the 1 gap area); (b) the **Designer screen**
`dq-studio/src/pages/TestLab.jsx` (route `/test-lab`, nav "Test Lab") — picker (DB→table→field→test
with shape/facet/framework chips) then the deep-dive: PythonCodeBlock + live SSE terminal
(console-before-result) + backtest mini-viz + AI Dossier + Bayes feedback shuttle (dialogue with
verdict badges) + version Undo/Redo + collapsible upstream-context panel; new `GET /test-lab/tools`
+ `/scope` catalogue endpoints + client fns. **Gates green:** `npm run build` clean, eslint 0
errors/0 warnings on new files, `verify_plan8.py` 37/37, backend catalogue + UI data-contract smoke
passed. Memory: `test-lab-redesign`.

**Test Lab Phase 3 (2026-07-01) — Scope & Domains + AI Recommendations BUILT & VERIFIED.** New
`ai/recommend.py` (deterministic framework skeleton + Merton/Poincaré AI enrichment) + endpoints
`/domains`, `POST /recommend`, `GET /recommend/stream`. `TestLab.jsx` is now a **3-step module**
(Scope&Domains → Recommendations → Designer): multi-DB + domain pills with "All" toggles; two sections
(Univariate/Multivariate) each with a deterministic framework-prioritized grid + a "Recommend with AI"
runner (AgentConsole+Stop) + a coverage strip (covered/gap); every card "Open in Designer" pre-fills
Phase-2 create. **Gates green:** backend boot, `verify_plan8.py` 37/37, `spike_recalibrate.py` PASS
(untouched), deterministic `/recommend` headless smoke (12 uni + 2 multi, 9 coverage areas w/ real
priorities, 1 gap flagged, all/all path), **live Azure SSE `/recommend/stream` smoke** (3207 Merton
frames → column-bound recs), open-in-Designer round-trip, `npm run build` clean + eslint 0/0. **Next:
Phase 4** (Catalogue + consolidated multi-test PDF; Workbench persistence) then Phase 5 (Monitoring
scheduler/email). Fisher retirement still pending. Memory: `test-lab-redesign`.

**Test Lab feedback round 6 (2026-07-01) — UI/UX polish, all gates green.** (1) Recommendation cards
now name concrete TABLE.FIELD targets (see `_suggest_targets` above) instead of listing every table;
card shows `table.field` (× for pairs). (2) "Open in Designer" → **"Add to Designer"**; the Designer no
longer swaps screens — it renders **inline below the recommendations** on the same page (auto-scroll via
`designerRef`, `createFrom(body,{inline})`). (3) HITL button "Send to Bayes" → **"Send feedback"**.
(4) informal "pushes back" removed — placeholder reworded + verdict badges via `VERDICT_LABEL`
(accommodate→"Change adopted", push_back→"Reasoned counterpoint"). Gates: deterministic `/recommend`
headless (30 uni + 6 multi, field-bound, 0 unbound, 1 gap), `verify_plan8.py` 37/37,
`spike_recalibrate.py` PASS (untouched), `npm run build` clean + eslint 0/0.

**Test Lab Phase 4 (2026-07-01) — Catalogue + Consolidated report + Workbench BUILT & VERIFIED.**
(1) **Catalogue** — `GET /test-lab/catalogue` indexes every designed test (one row per dossier; shape +
framework areas from the Test Kit, `logical_dbs` from `test_db_links`, version count; filters
db/area/shape/status + facets). New `Catalogue` step (4th) with client-side filters, multi-select, and
per-row "Open" into the Designer. (2) **Consolidated report** — `POST /report/synthesize` fresh-runs each
selected design (deterministic sandbox) → aggregate stats + Merton `consolidate_dossiers` editable exec
narrative (4 fixed `##` sections); `POST /report/pdf` → `ai/test_report_pdf.generate_consolidated_pdf`
(**native fpdf vector charts — no matplotlib dep**; narrative + evidence table; Latin-1-safe). Frontend
`ReportPanel` (synthesize → edit title/narrative → Download PDF blob). (3) **Workbench** — `design_workbench`
table + `POST/GET /workbench` + `GET/PATCH/DELETE /workbench/{id}`; header `WorkbenchBar` saves the current
scope + selected tests and resumes them onto the Catalogue. **Gates green:** backend boot + import,
`verify_plan8.py` 37/37, `spike_recalibrate.py` PASS (PSI untouched: I1=0.2867/I3=0.2052/clean=0.0162),
PDF generator + catalogue + workbench CRUD headless smoke, `npm run build` clean + eslint 0/0. Memory:
`test-lab-redesign`.

**Test Lab Phase 5 (2026-07-01) — Monitoring Schedules BUILT & VERIFIED (design-only per proposal).**
New `schedules` + `notifications` tables (both work-product). `schedules` binds designed tests (plan_ids)
to a cadence + `delivery_channel` (download|email) + recipients; **there is deliberately NO background
runner** — `POST /schedules/{id}/run` (manual) + `POST /schedules/tick` (runs enabled+due schedules) reuse
`_report_data` to fresh-run the tests, then stamp last_run/last_status + compute `next_run` (`_next_run`
handles month-end clamp + year rollover). On the **email** channel a notification is COMPOSED into the
`notifications` outbox (deterministic `_compose_notification`, instant) with a plain "not actually sent (no
SMTP)" line — the delivery seam is modelled, not wired. `list_schedules` returns `scheduler_active:false`
(honesty flag). Frontend: Catalogue gains **"Schedule selected"** (→ `ScheduleDialog`), a 5th **Schedules**
step (`SchedulesPanel`) lists schedules with Run now / Run due / enable-toggle / delete + a notification
preview + outbox, all under an amber design-note banner. **Gates green:** app import + 5 new routes,
`verify_plan8.py` 37/37, `spike_recalibrate.py` PASS (PSI untouched: I1=0.2867/I3=0.2052/clean=0.0162),
schedule create/run/tick/notification headless smoke (incl. next_run edge cases), `npm run build` clean +
eslint 0/0. **The Test Lab redesign (Phases 1–5) is now feature-complete.** Real implementations of the
13 `planned` gap tools remain the open follow-up. Memory: `test-lab-redesign`.

**Fisher fully retired (2026-07-01).** The agent was already gone from the roster (`seeds` deletes the
`agent_skills` row; not in `skills.AGENTS`); this pass removed the residual wiring: `control_plane`
role config, egress `allowed_roles` in `tool_registry` + `dict_ingest`, `skills/fisher.md` (deleted +
seed cleanup), and the `verify_plan8` role loop (replaced with a `"fisher" not in ROLE_CONFIG`
assertion — still 37/37). The legacy test-gen loop (`test_manager`) no longer streams a "Fisher" step
(profiling comes from Newton's DiscoveryState, or a de-branded pandas profile under Gauss as a
no-DiscoveryState fallback). The pure-pandas profiler `ai/agents/variable_screening.py` **survives as a
neutral utility** — `GET /ingestion/{db}/profile` (DataSourcing browse-step "Column statistics")
still uses it before a DiscoveryState exists (its internal `fisher_subprocess` key → `subprocess`).
**Also fixed:** the DataSourcing "Download Report" (and the new consolidated-report download) were
revoking the blob URL synchronously right after `a.click()`, which aborts the download in stricter
browsers / embedded webviews — now revoked on a 15s delay.

**Gap battery implemented — Test Lab #1 (2026-07-01).** All 13 credit-risk gap tools promoted from
`status="planned"` to REAL, runnable `test_library` rows (`seeds/test_library_seed.py`, 12→**25** rows):
rolling-PSI trajectory, IV/WoE stability, Gini/AUC decay (rank-AUC, no sklearn dep), VIF (numpy lstsq),
ADF·KPSS·Ljung-Box (statsmodels — sandbox-allowlisted), Mann-Kendall, changepoint/CUSUM, cohort-curve
monotonicity, special-value/sentinel, Benford (scipy chi-square), business-rule (`df.eval`). Same
appendix-style contract (`run_<id>(df,columns,params)` → console + `result`), executed in `code_sandbox`.
`test_kit._PLANNED_TOOLS` now empty; all 13 surface via `_library_tools()` with framework areas (already
in `_TOOL_AREAS`) and appear in the deterministic recommender. **Verified:** each of the 13 smoke-run
through the real sandbox against seeded data (sensible verdicts — rolling-PSI FAILs on the planted
`bureau_score` drift, business-rule catches 10.8% over-limit rows, VIF≈2.2, ADF stationary…),
`verify_plan8.py` 37/37, `spike_recalibrate.py` PASS (calibration untouched). **Remaining open follow-up:
none of the original proposal's headline gaps — the module is feature-complete**; nice-to-haves are the
per-test PDF and the metadata reuse-recommender.

**Feedback 2026-07-02 — recommendation grading + report UX + per-test PDF + reuse recommender.**
(1) **Confidence scoring** in `recommend.py`: `_confidence` (framework priority × field value × bound-fit
× data-readiness, +15 AI-evidence) → `grade` (High≥70 / Med≥45 / Low); `merge_recommendations` ranks by
confidence and `_trim` marks a diversity-capped **default view** (`default_hidden`; ≤15 uni / ≤10 multi,
≤2 per field, ≤3 per test) with `shown`/`total` counts — the raw list is combinatorial (112 uni / 36
multi across 3 DBs); the practical shortlist is ~15+10. UI: grade+confidence chip on each `RecCard`,
**foldable** sections with a "Show all N (incl. lower-confidence)" toggle, and a flow-helper strip
(recs → Add to Designer → Catalogue → Workbench). (2) **Consolidated report** now streams via
`GET /report/synthesize/stream` (SSE: per-test run lines + Merton narrative) into an `AgentConsole`
runner, and the narrative edits in a **side-by-side `MarkdownEditor`** (editor left / preview right).
(3) **Per-test PDF** `test_report_pdf.generate_test_pdf` + `POST /design/{id}/report/pdf` + a Designer
"PDF" button (Dossier + outcome + code + console). (4) **Reuse recommender** `recommend.reuse_suggestions`
+ `GET /test-lab/reuse` + a Catalogue "Reuse suggestions" panel (a designed test → same-kind high-value
fields elsewhere, cross-DB first). **Gates:** `verify_plan8.py` 37/37, `spike_recalibrate.py` PASS,
per-test PDF + reuse + report-stream headless smoke, `npm run build` + eslint clean. **Test Lab redesign
is fully feature-complete.**

**Cross-table / cross-DB join execution — #4 backend (2026-07-01).** `ai/frame_assembler.py` assembles a
single working frame from operands that span tables or DBs: join edges resolved **confirmed join_spec →
validated `relations` (same DB) → deterministic value-overlap key probe**; `__<table>`-suffixed collision
handling; fan-out / low-overlap / no-path warnings. Wired into `_run_design` + `run/stream` (join plan
prints to the terminal); single-table designs are the unchanged plain-load path. `POST /test-lab/join/suggest`
dry-runs the plan for the HITL confirm surface. **Verified:** single-table (backward compatible — PSI still
0.2867 FAIL on the planted defect), cross-table (retail_accounts⋈customers on customer_id, containment 1.0,
`corr_stability` ran on the joined frame), cross-DB (retail_fraud_db.customers ⋈ retail_risk_db.retail_accounts),
`POST /join/suggest`, `verify_plan8.py` 37/37, `spike_recalibrate.py` PASS. *Remaining:* the multi-operand
Designer UI (today `create_design` is single-table; the execution engine already runs multi-operand designs).
