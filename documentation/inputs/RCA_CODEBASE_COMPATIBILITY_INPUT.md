# RCA Codebase Compatibility Input

Source root: source-codes/. Factual snapshot only; no compatibility verdict or recommendations.

## 1. Repository state

Commands run in source-codes/:

    git status --short
    (empty)
    git branch --show-current
    dev
    git rev-parse HEAD
    d7529bdd4e303b8db55d3f343358beb3752442bd
    git log --oneline --decorate -10
    d7529bd (HEAD -> dev, origin/dev, origin/HEAD) remove retired framework concept note
    ce3608f retire historical strategy artifacts
    878c7f8 docs: begin 0.3.0 iteration
    41f7dad (tag: archimedes-v0.2.1, origin/main, main) merge: hotfix 0.2.1 durable upload cache
    f8147e6 fix durable upload execution cache
    5ba1b13 (tag: archimedes-v0.2.0) release Archimedes 0.2.0 independent workflow
    97d23a3 Handle ACR log streaming failures
    6c279bd (tag: archimedes-v0.1.0) Configure production frontend API base
    a4342de Allow deployment without legacy CA bundle
    c332411 Consolidate deployment commands
    git worktree list
    C:/Products/data-assessment/archimedes/source-codes d7529bd [dev]

Implemented product version is 0.2.1 (VERSION and backend/app_version.py). The current shipped path is the Galileo v2 upload-first workflow: item upload/profile/inventory, framework planning, approved snippets, execution, recommendations, scoring, issues, and RCA. The v10 Triage, Intake, Opening-look Planner/Runner/Reader, Coverage Challenge, Composer, and Part-B Judge/Fix/Closure state machines are not implemented as named product agents. Formal studies and Compare Results/Compare Tools are not implemented. Documented latest validation is verify_plan8.py 37/37; spike_recalibrate.py PASS (PSI I1=.2867, I3=.2052, clean=.0162); frontend build and ESLint clean; documented headless smoke 33 checks. The checked-in Playwright suite has three tests. No uncommitted product work exists.

## 2. Current product feature map

Routes are in ui/src/App.jsx; auth gate is ui/src/context/AuthContext.jsx; shell/nav is ui/src/components/Sidebar.jsx.

| Module | Route/component | Purpose, APIs, persistence, auth, coverage |
|---|---|---|
| Authentication/setup | /login, ui/src/pages/Login.jsx | /api/login, logout, me, config; users, app_fsm, transaction_log. Bearer session; login public. Upload E2E logs in. |
| Overview/Inventory | /, ui/src/pages/Inventory.jsx | Lists v2 database/dataset items and health/issues; /api/v2/items and inventory endpoints; dq_items, dq_item_*, scores_v2, issues_v2. |
| Assets/Data Sourcing | /data-sourcing, DataSourcing.jsx | Item creation, multipart upload, profile SSE, target/use-case/inventory editing, finalize; /api/v2/items*; dq_items, files/tables, variable_inventory. Covered by ui/e2e/upload-workflow.spec.js. |
| Datasets | same v2 flow | kind=database or dataset; files under backend/uploads/{item_id}. |
| Contexts | no dedicated route | backend/ai/context_memory.py: upsert_context, link_context, get_context_bundle; object_contexts/context_links; admin inspection only. Not the v10 knowledge base. |
| Experiment Design | inline /test-lab, TestLab.jsx | Legacy dossier/version/feedback/backtest code exists, but current main.py mounts only auth/admin/v2. No v10 experiment state machine. |
| Experiment execution | /test-lab | v2 plan finalization, snippet approval, execute/stream; results_v2; sequential execution. |
| Results & Drift | /test-lab | Results/evidence/score/issues through v2 APIs; no standalone drift page. |
| Compare Results | NONE | No route/component/API. |
| Compare Tools | NONE | No route/component/API. |
| Generated code | Test Lab snippets; ai/code_sandbox.py | Generate/edit/approve then sandbox execution; no arbitrary filesystem/network access. |
| Formal studies | NONE | No formal-study model or scheduler. |
| Admin | /admin, Admin.jsx | /api/admin/users and context-memory; admin Bearer session with authz_roles containing admin; users, transaction_log, object_contexts. |
| How It Works | NONE | ProductTour component exists but no route. |

## 3. Backend architecture

- Bootstrap: backend/main.py loads dotenv, restores/initializes/seeds system_db, reconciles v2 items, starts optional backup thread, configures CORS, mounts auth.router, admin.router and v2.router, and defines /health and /api/config.
- Configuration/provider: backend/app_config.py:public_config; backend/ai/llm.py:get_client/get_model; Azure OpenAI/gpt-4.1 from environment.
- Authentication/Principal: backend/routers/auth.py:current_user and is_admin; opaque UUID sessions in app_fsm, users in users.
- Workspace authorization: NOT IMPLEMENTED as a tenant/workspace service; item/logical-db/table keys provide scoping.
- Metadata: backend/system_db.py SQLite; get_conn, init_schema, insert, upsert, update, query, query_one, execute, delete, reset_demo, wipe_all_items.
- Azure metadata: NOT IMPLEMENTED. Azure Files is optional backup only.
- Artifacts: UPLOAD_DIR/{item_id}; metadata in dq_item_files; no object-store abstraction.
- Migrations: NOT IMPLEMENTED as a migration framework; additive DDL/idempotent backfills in init_schema.
- Backup: restore_from_backup at boot and backup_to_volume periodically/gracefully; hard crash may lose interval writes.
- v2 service: backend/ai/v2/service.py functions create_item, upload_file, finalize_item, profile_item, get_inventory, put_inventory, build_plan, execute_iter, recommend_iter, score_item.
- Context: backend/ai/context_memory.py. RCA-like services are backend/ai/rca_cases.py and rca_agent.py; legacy RCA router is not mounted. v2 issue RCA is deterministic in ai/v2/issues.py.
- Orchestration: service.execute_iter and backend/ai/test_execution.py; no named v10 Planner/Runner/Reader.
- AI: ai/control_plane.py:resolve, ai/effort.py:EffortPolicy, ai/prompts.py. Fake provider: NOT IMPLEMENTED.
- Deterministic tools: ai/test_kit.py, dq_tests/registry.py:run_registered, ai/rca_helpers.py, ai/v2/issues.py.
- Tool registry: ai/tool_registry.py role/schema/egress contracts. Scoring: scoring/health.py, scoring/categories.py, v2 score_item.
- Generated code: ai/code_sandbox.py:run and rca_agent.run_sandboxed; AST/import allowlist and bounded output.
- Formal-study scheduling: NOT IMPLEMENTED; Test Lab schedules are manual/on-demand and have no background runner/SMTP.
- Audit: transaction_log, rca_steps, evidence and hitl_decisions; no universal append-only audit service.
- Error handling: router _err maps errors; v2 may expose exception text; sandbox output is bounded.
- Quota accounting: NOT IMPLEMENTED; context token estimates are not quotas.

## 4. Current domain records

DDL and JSON columns are in backend/system_db.py.

| Table/record | Facts |
|---|---|
| users | username PK; profile/authz; no workspace/version/status. |
| app_fsm | entity type/id, state, context, timestamp; auth sessions active/ended. |
| dq_items | item_id, kind, name, status, target_variable/use_case; item-scoped, no owner/version. |
| dq_item_files, dq_item_tables, variable_inventory | Child file/table/column records; classification/type/role/profile fields. |
| ingested_databases, table_metadata | Legacy logical DB/table, PK/date/columns/types/counts; no version. |
| test_library | Reusable test_id, Python code, thresholds, applicability, version. |
| plan_v2, results_v2, scores_v2, issues_v2, tracked_issues_v2 | v2 plan/result/score/issue records with status, metric, evidence and owner/priority/target fields as applicable. |
| test_plan, test_db_links, test_dossiers, test_versions, design_workbench | Legacy Test Lab design; versions append snapshots with seq/parent/author/change note; dossier current_version/status; workbench owner/status/soft plan refs. |
| run_results, health_scores, monitoring, schedules, notifications | Execution/health and design-only monitoring; schedules enabled/next/last status; email notifications remain drafted. |
| tickets, hitl_decisions, transaction_log | Tickets/status/owner; human decisions; selected audit events. |
| object_contexts, context_links | Scoped notes/facts with source/confidence/priority/expiry and directed links; no enforced trust/version/quarantine. |
| rca_sessions, rca_steps, rca_cases, rca_case_failures, rca_case_sessions, rca_evidence, rca_evidence_links, rca_hypotheses, rca_remediation_plans | Existing RCA-like records. Cases start triage; linking session sets investigating; sessions running/awaiting_approval/done/ticketed; hypotheses are seeded proposed rows with numeric confidence; evidence has strength; remediation has status/owner/SLA. |

Naming collisions: case, hypothesis, complaint, test, result, evidence, context, workflow, agent, tool, trace, audit event, human interpretation, commentary, owner, status, knowledge, and history are not all represented as v10 concepts. rca_hypotheses.confidence is numeric rather than v10 tiers; test_versions are design snapshots; object_contexts are context notes.

## 5. State machines and human gates

Dataset lifecycle is item create -> upload -> profile stream -> inventory edit -> finalize -> plan/finalize/approve -> execute -> results/score/issues. Legacy dossier starts designing; v2 plan rows are pending/finalized/approved/executed. RCA sessions are running -> awaiting_approval for proposed code -> running after human decision -> done (or ticketed). Admin deletion requires admin authz and blocks self/last-admin. Demo reset/wipe is table-list based.

Implemented gates are snippet approval, RCA proposed-code approval, legacy Test Lab feedback, admin authz and tracked-issue editing. Pause/resume/stop/retry are stream/UI or bounded retry behaviors, not an RCA v10 protocol. Human business question/answer, hypothesis approval/rejection, coverage challenge, fix approval, frozen-snapshot closure rerun and time-box escalation are NOT IMPLEMENTED.

## 6. Data access and execution

Uploads are item-isolated; service loads pandas dataframes and persists schema/inventory. The finalized registry has 14 tests: completeness, plausibility, date ordering, post-outcome, uniqueness, PSI, maturity, MCAR, MNAR, MAD/outlier, tail, leakage, correlation stability and trend/drift decomposition. RCA helpers cover drift, missingness, outliers, relationships, joins, duplicates, freshness, leakage, reconciliation and schema.

RCA SQL permits one read-only SELECT and returns at most 50 rows. Sandbox AST/import guards allow pandas/numpy/scipy and selected standard modules; filesystem/network/subprocess/writes are unavailable. Results persist metric, threshold, violation count, evidence JSON and timestamps. No v10 positive-class/analytical-reference record exists.

## 7. Knowledge, memory, history

backend/knowledge_base/*.json and object_contexts provide framework/business/context data. NOT IMPLEMENTED: typed v10 knowledge base; human-confirmed versus inferred trust; shelf-life enforcement; schema-change invalidation; blame-back; quarantine from planning; immutable fact versions; separate lineage/ownership/history records. Agent context is written by rca_agent._write_context; SME notes use add_sme_feedback. Audit history is transaction/RCA-step based.

## 8. Model and agent orchestration

Azure OpenAI is lazy-loaded; control-plane role settings resolve model/temperature/effort. Legacy RCA creates a fresh per-session message list containing system prompt, failed-test contract, schema/stats and context bundle, then appends tool calls/results. Tools include read-only SQL, deterministic helper listing/runs, generated-helper validation, approved-code proposal and root-cause declaration. Budgets/retries are bounded in LLM client, test manager and sandbox. Persisted session/step/result/checker records exist; no model-invocation table. Fake provider, cross-session fresh-state guarantee test, v10 fork/kill-attempt loop, confidence tiers and human-question protocol are NOT IMPLEMENTED.

## 9. Mounted API inventory

Mounted routers are auth, admin and v2 (plus main health/config). v2 request models are ItemIn, FinalizeIn, PlanPatch, SnippetAction, SnippetPatch, FinalizePlanIn, ItemPatch, CloseIssueIn, RaiseIssueIn, AnalysesIn, PlanAddIn and TrackedPatch. CSRF is not implemented; v2 handlers do not declare a Principal dependency.

- Main: GET /health; GET /api/config.
- Auth: POST /api/login, POST /api/logout, GET /api/me, PUT /api/me.
- Admin: GET/POST /api/admin/users, DELETE /api/admin/users/{username}, GET /api/admin/context-memory.
- Items: POST/PATCH /api/v2/items, POST /api/v2/items/{item_id}/files, POST /api/v2/items/{item_id}/finalize, GET /api/v2/items, GET /api/v2/items/{item_id}/tables, GET /api/v2/items/{item_id}/columns, GET /api/v2/items/{item_id}/profile/stream, GET/PUT /api/v2/items/{item_id}/inventory.
- Plans/snippets: POST /api/v2/items/{item_id}/plan/build, GET /plan and /plan/status, PATCH /api/v2/plan/{row_id}, POST /items/{item_id}/plan/add, POST /items/{item_id}/plan/finalize, POST/PATCH /api/v2/plan/{row_id}/snippet.
- Execution/results: GET /api/v2/items/{item_id}/execute/stream, GET /results, GET/POST /recommend/stream, GET /score.
- Issues/RCA: GET /items/{item_id}/issues, GET /issues/register, GET /issues/{issue_row_id}, GET/POST /issues/{issue_row_id}/rca/stream, POST /issues/{issue_row_id}/analysis and /analysis/interpret, PATCH /issues/tracked/{issue_id}, POST /issues/{issue_row_id}/close and /raise, GET /items/{item_id}/report.
- Reference: GET /api/v2/framework/overview, /areas, /tests, /matrix, /api/v2/agents.

Legacy router files expose ingestion/datasource/test-lab/test-plan/test-library/monitoring/tickets/dq-framework/skills/use-cases/preview/tests/ai-rules routes, but current main.py does not include them.

## 10. Frontend constraints

App.jsx uses BrowserRouter and auth/wizard/tour providers. ui/src/api/client.js is the fetch gateway: VITE_API_BASE or localhost:8001/api, Bearer token in localStorage key dq_token, JSON headers, HTTP error parsing and EventSource SSE helpers. Tailwind/Radix/shadcn provide responsive layout. Forms, code approval dialogs, dossier/editor, execution progress and issue evidence cards are in TestLab.jsx, testlab/*, IssueRca.jsx, Step2Execute.jsx and ExecutionSwimlane.jsx. No suspect-board/evidence-trail timeline or v10 workflow visualization exists. ProductTour exists without a How-It-Works route.

## 11. Storage/migration constraints

SQLite JSON columns/tables are in system_db.py; no workspace partition, conditional-write abstraction, general immutable insert, migration framework, quota ledger or Azure metadata implementation. test_versions is append-only by application convention. Backups copy local SQLite to optional volume. Reset/wipe preserves users/framework/library and clears documented work-product tables. Audit is partial (transaction_log, RCA steps, HITL decisions).

## 12. Test infrastructure

Backend commands/locations: python backend/verify_plan8.py, python backend/spike_recalibrate.py, python backend/spike_gx.py, python backend/spike_integration.py; unittest backend/tests/test_finalized_framework.py. Frontend: npm run build, npm run lint, npm run test:e2e in ui/. Playwright config starts uvicorn on 8001 and Vite on 5175, Chromium, .e2e/system_state.db, .e2e/uploads, retain-on-failure traces. Fixtures are ui/e2e/fixtures/schema.csv, dictionary.csv, assessment.csv; current spec is ui/e2e/upload-workflow.spec.js with three tests. Fake-provider, isolation/security, migration and clean-room UAT suites are NOT IMPLEMENTED. No separate retained-artifact policy is documented.

## 13. RCA-relevant contract matrix

| Concept | Existing equivalent / meaning / persistence / UI / tests |
|---|---|
| failed test | results_v2/issues_v2; same broad meaning; metric/status/violation; Results/Issues UI; upload E2E only. |
| failure group; complaint | NONE; no grouping or symptom-only complaint record. |
| case / case file | rca_cases plus dossier, session failed_test/context_bundle; partial overlap; RCA tables and IssueRca; no v10 intake checklist. |
| opening look | rca_helpers.py; helper analysis, not fixed opening-check record. |
| planner / reader / coverage challenge | NONE. |
| runner | v2 execute_iter and RCA tool loop; different meaning; results/steps. |
| suspect board/status | rca_hypotheses status/confidence; partial; seeded hypotheses, no active/ruled-out/revived board. |
| evidence trail | rca_evidence, rca_steps, links; partial; IssueRca evidence cards. |
| human question/answer | hitl_decisions and SME feedback; partial, not planner business questions. |
| hypothesis | rca_hypotheses.create_hypothesis; partial; cause_family/statement/numeric confidence/supporting/contradicting/next_probes. |
| confirmation check / judge | rca_checker.check and v2 interpretation; different; no reject-capable Part-B gate. |
| fix proposal | rca_remediation_plans/FIX_BY_CAUSE; partial; actions/owner/status. |
| human approval | snippet/code/admin/Test Lab feedback gates; no fix approval. |
| closure rerun | issue close/retest-related APIs; different; no frozen snapshot. |
| knowledge fact/history | object contexts/JSON and RCA tables; different; no trust/shelf-life/quarantine. |
| owner/escalation | users, tickets, tracked issues, remediation owner/SLA; partial; no v10 routing/time-box. |
| audit event | transaction_log, rca_steps, hitl_decisions; partial, not universal immutable audit. |

## 14. Guarantees and invariants

Item/table keys provide scoping; Playwright uses separate .e2e DB/uploads. Test Lab versions are append-only snapshots. Each RCA session builds a fresh message list. Sandbox/tool registry allowlist imports, AST operations, roles, schemas and egress; SQL is read-only and bounded. Results/evidence/session/transaction rows preserve structured outputs and timestamps. Azure secrets are environment-loaded. Snippet and proposed-code execution requires human approval. Sandbox errors are bounded, although v2 router details may expose exception text. No universal raw-provider preservation, deterministic result hash, quota, complete audit, prompt-prohibition or v10 closure guarantee exists. Strong documented validator is backend/verify_plan8.py; strongest E2E path is ui/e2e/upload-workflow.spec.js.

## 15. File index (under 40)

Models/storage: backend/system_db.py; backend/ai/v2/service.py; backend/ai/v2/issues.py; backend/ai/rca_cases.py; backend/ai/context_memory.py; backend/dq_tests/contracts.py.

Services/tools: backend/ai/rca_agent.py; backend/ai/rca_helpers.py; backend/ai/rca_checker.py; backend/ai/llm.py; backend/ai/control_plane.py; backend/ai/tool_registry.py; backend/ai/code_sandbox.py; backend/ai/test_kit.py; backend/ai/test_execution.py; backend/dq_tests/registry.py; backend/dq_tests/param_specs.py.

APIs/bootstrap: backend/main.py; backend/routers/auth.py; backend/routers/admin.py; backend/routers/v2.py.

Frontend: ui/src/App.jsx; ui/src/api/client.js; ui/src/context/AuthContext.jsx; ui/src/components/Sidebar.jsx; ui/src/pages/DataSourcing.jsx; ui/src/pages/TestLab.jsx; ui/src/pages/IssueRca.jsx; ui/src/pages/IssueManagement.jsx.

Tests/docs: backend/tests/test_finalized_framework.py; backend/verify_plan8.py; backend/spike_recalibrate.py; ui/e2e/upload-workflow.spec.js; ui/playwright.config.js; TSD.md; guide/Workflows_and_Testing_Guide.md; RCA_Full_Workflow_v10.md.

