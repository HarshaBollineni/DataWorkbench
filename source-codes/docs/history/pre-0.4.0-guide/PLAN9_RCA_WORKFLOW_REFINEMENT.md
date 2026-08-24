# Plan 9 - Incremental RCA Workflow Refinement

## Purpose

Repair and refine the RCA workflow so failed validation tests are eligible for incremental root cause analysis, Feynman uses compact object context memory from Plan 8, Noether gates weak RCA conclusions, and Issue Management receives AI-assisted tickets with editable resolution paths.

This is an execution plan for implementation. It assumes Plan 8's context memory contract exists.

## Current Baseline

Existing files:

- `backend/ai/rca_agent.py`: RCA loop with in-memory sessions, SQL probe tool, HITL code approval, and declaration.
- `backend/ai/rca_checker.py`: Noether checker.
- `backend/routers/rca.py`: start/status/approve/result/check/sandbox/raise-ticket endpoints.
- `dq-studio/src/pages/RCA.jsx`: UI for failed validation items, agent approval, checker, SME notes, sandbox, and ticket creation.
- `dq-studio/src/api/client.js`: RCA client functions.
- `backend/routers/tickets.py`: Issue Management ticket lifecycle.

Known gaps:

- RCA sessions are memory-only and vanish on backend restart.
- RCA does not persist incremental analysis steps, accepted evidence, or final RCA as reusable object context.
- RCA start path accepts any supplied failed test; it does not enforce eligibility from persisted failed `run_results`.
- Feynman receives schema/stat context but not Plan 8 object context memory.
- Tickets are created after RCA, but the root cause can be omitted by the UI and resolution paths are minimal.
- Noether does not automatically block or annotate ticket creation.

## Target Workflow

1. Validation run writes `run_results`.
2. RCA eligibility endpoint lists only failed results:
   - `passed=0`
   - has `table`, `test_id`, `observed`, `expected`
   - joins `test_plan.instance_key` when available
   - excludes already-ticketed failures unless user chooses to reopen/analyze again
3. User starts RCA from a failed result.
4. Feynman builds a compact prompt:
   - failed result payload
   - table schema/stats
   - Plan 8 object context bundle
   - prior RCA/ticket context for the same table/variable/test
5. Feynman performs incremental analysis:
   - read-only SQL probes auto-run
   - proposed pandas/numpy code requires HITL approval
   - every approved/rejected step persists as an RCA step and context memory
6. Feynman declares root cause, evidence, remediation, and confidence.
7. Noether checks the RCA:
   - accept permits ticket flow
   - revise/reject keeps the ticket button gated unless user explicitly overrides with reason
8. Ticket dialog is prefilled with:
   - immutable issue context
   - RCA root cause
   - evidence summary
   - AI proposed remediation
   - editable owner, priority, mitigation, and resolution path
9. Ticket creation links back to RCA context and stores ticket update memory.

## Backend Contract

### RCA Persistence Tables

Add `rca_sessions`:

| Column | Type |
| --- | --- |
| `session_id` | TEXT PK |
| `run_id` | INTEGER nullable |
| `table` | TEXT |
| `test_id` | TEXT |
| `instance_key` | TEXT nullable |
| `status` | TEXT: `running`, `awaiting_approval`, `done`, `checker_review`, `ticketed`, `abandoned` |
| `failed_test` | TEXT JSON |
| `context_bundle` | TEXT JSON |
| `result` | TEXT JSON |
| `checker` | TEXT JSON |
| `created_at` | TEXT, IST ISO-8601 with `+05:30` offset |
| `updated_at` | TEXT, IST ISO-8601 with `+05:30` offset |

Add `rca_steps`:

| Column | Type |
| --- | --- |
| `step_id` | INTEGER PK |
| `session_id` | TEXT |
| `seq` | INTEGER |
| `step_type` | TEXT: `sql_probe`, `code_proposal`, `code_approved`, `code_rejected`, `code_output`, `root_cause`, `checker` |
| `payload` | TEXT JSON |
| `created_at` | TEXT, IST ISO-8601 with `+05:30` offset |

Add JSON columns to `_JSON_COLS`:

- `rca_sessions`: `failed_test`, `context_bundle`, `result`, `checker`
- `rca_steps`: `payload`

Demo reset:

- Clear `rca_sessions` and `rca_steps`.
- Plan 8 reset already clears demo contexts.

### API Changes

Add:

```http
GET /api/rca/eligible?table={table?}
```

Returns:

```json
[
  {
    "run_id": 12,
    "table": "retail_accounts",
    "test_id": "psi",
    "instance_key": "retail_accounts|psi|[\"bureau_score\"]",
    "name": "PSI",
    "column": "retail_accounts.bureau_score",
    "expected": "PSI <= 0.20",
    "actual": "PSI = 0.287 (FAIL)",
    "category": "C1",
    "already_ticketed": false
  }
]
```

Modify:

```http
POST /api/rca/start-table
```

Accepts optional `run_id` and `instance_key`; rejects non-failed run IDs.

```json
{
  "table": "retail_accounts",
  "run_id": 12,
  "instance_key": "...",
  "failed_test": {}
}
```

Add:

```http
GET /api/rca/{session_id}/steps
```

Returns ordered persisted steps.

Modify:

```http
POST /api/rca/raise-ticket
```

Accepts `session_id`, includes RCA result and Noether checker in `resolution_path`, and rejects ticket creation when checker verdict is not accepted unless body has `override_reason`.

### Agent Changes

`backend/ai/rca_agent.py`:

- Keep the public response shape, but persist sessions and steps.
- On `start_session_table`, call Plan 8 `get_context_bundle`.
- Include the context bundle and its broker-provided `usage_requirements` in the RCA user prompt without overwriting Feynman's functional prompt.
- Persist each tool call and approval as `rca_steps`.
- Write object contexts:
  - code output -> `context_type='rca_evidence'`
  - final RCA -> `context_type='remediation'`
  - checker -> `context_type='agent_summary'`
- Add result fields: `root_cause`, `evidence`, `remediation_key`, `remediation`, `confidence`, and `supporting_context_ids`.
- Use IST ISO-8601 timestamps with `+05:30` for RCA sessions and RCA steps.

`backend/ai/prompts.py`:

- Update RCA system prompt to require incremental evidence.
- Require evidence source labels: `live_sql`, `approved_code`, `context_memory`, or `run_result`.
- Prefer new analysis over stale memory when conflict exists.

`backend/ai/rca_checker.py`:

- Require Noether to verify evidence sufficiency and issue-ticket readiness.
- Return strict JSON with:

```json
{"verdict":"accept|revise|reject","approved":true,"issues":[],"suggestion":"","ticket_ready":true}
```

Keep backward compatibility by mapping missing `ticket_ready` to `approved`.

## Frontend Contract

`dq-studio/src/api/client.js`:

- Add `rcaEligible(table)`.
- Add `rcaSteps(sessionId)`.
- Extend `rcaStartTable` to accept `run_id` and `instance_key`.
- Extend `rcaRaiseTicket` body with `session_id` and optional `override_reason`.

`dq-studio/src/pages/RCA.jsx`:

- Source failures from `/api/rca/eligible` first; fall back to wizard `validationResults` only when API returns empty and local results exist.
- Display failed tests as RCA-eligible work queue.
- Show incremental timeline from `rca_steps`.
- Gate `Raise ticket` until RCA result exists and Noether accepts, unless user supplies override reason.
- Ticket dialog must pass `root_cause` from `agents[item.id].result.root_cause`, not an empty string.
- Prefill editable resolution path from RCA remediation.

## Master Execution Plan

### Wave 0 - Orchestrator-Owned Design Lock

Owner: Orchestrator only.

Tasks:

- Lock `rca_sessions`/`rca_steps` schema.
- Define exact eligibility SQL and ticket gating rules.
- Confirm Plan 8 context bundle function signature.
- Confirm Context Memory Broker usage so Feynman's functional prompt remains intact.
- Confirm backward compatibility for existing UI wizard results.

### Wave 1 - Parallel Delegated Backend Units

Run independently after Wave 0:

- SA9-A: `system_db.py` RCA tables and reset.
- SA9-B: `routers/rca.py` eligibility endpoint and request models.
- SA9-C: `ai/rca_agent.py` step persistence hooks.
- SA9-D: `ai/prompts.py` and `ai/rca_checker.py` prompt/output contract update.
- SA9-E: ticket raise body/session/checker gate.

### Wave 2 - Parallel Delegated Frontend Units

Run after API contracts compile:

- SA9-F: `api/client.js` RCA helpers.
- SA9-G: `pages/RCA.jsx` eligible work queue and root cause ticket payload.
- SA9-H: `pages/RCA.jsx` incremental timeline and ticket gating UI.

### Wave 3 - Orchestrator Integration

Owner: Orchestrator only.

Tasks:

- Integrate Plan 8 context memory calls with Feynman and ticketing.
- Verify RCA prompts receive broker `usage_requirements`, not pasted full memory policy.
- Resolve any collisions between backend session IDs and UI item IDs.
- Run smoke tests:
  - create/seed DB
  - produce failed run result
  - eligible endpoint returns it
  - start RCA
  - approve code
  - result persists
  - Noether check persists
  - raise ticket creates ticket and context links
  - demo reset clears demo RCA/memory/tickets

## Quantitative Controls

- RCA prompt context budget: 1800 tokens for Plan 8 bundle, 1200 tokens for schema/stats, 1000 tokens for failed result and prior steps.
- Step limit: keep `_MAX_STEPS=6`; maximum 2 approved analysis snippets before declaration unless Noether asks for revision.
- Persisted step payload cap: 6000 chars per step; truncate stdout with head/tail preservation.
- Eligible queue cap: return newest 100 failed results by default.
- Ticket gating: Noether `accept` required for normal ticket flow; override requires non-empty human reason with at least 20 chars.
- Timestamps: RCA session/step/ticket-resolution additions created by Plan 9 must use IST ISO-8601 with `+05:30`.

## Execution Preferences for AI Builder

- Orchestrator retains RCA state-machine design, eligibility SQL, token budgets, and ticket gating policy.
- Orchestrator retains memory usage policy: functional RCA/checker prompts are not overwritten; shared memory instructions come from the Context Memory Broker.
- Sub-agents are myopic and file-scoped.
- Do not pass full guide/PDF/global plan to sub-agents.
- Parallelize by file ownership only; never assign two sub-agents to edit the same file in the same wave unless the second prompt is explicitly based on the first diff.
- Each delegated prompt must include this exact output constraint:

```text
Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.
```

## Sub-Agent Prompts

### SA9-A - RCA Persistence Tables

Role: backend SQLite schema implementer.

Objective: Modify only `backend/system_db.py` to add `rca_sessions` and `rca_steps`.

Context snippet:

```python
_JSON_COLS = {
    "tickets": {"issue_context", "linkage", "resolution_path", "resolution_updates"},
    "hitl_decisions": {"payload"},
    "transaction_log": {"payload"},
    "health_scores": {"category_scores"},
}

_WORKPRODUCT_TABLES = [
    "test_plan", "run_results", "health_scores", "hitl_decisions",
    "monitoring", "tickets",
]
```

Directives:

- Add DDL for `rca_sessions` and `rca_steps` exactly as Plan 9 specifies.
- Add JSON columns for both tables to `_JSON_COLS`.
- Add `rca_sessions` and `rca_steps` to demo reset work-product purge.
- Add indexes: `rca_sessions(run_id)`, `rca_sessions(table, test_id, status)`, `rca_steps(session_id, seq)`.
- Use the shared IST timestamp helper if present; otherwise add/use `datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()`.

Test criteria:

- `python backend/system_db.py` succeeds.
- SQLite table list includes both RCA tables.
- Demo reset clears both tables.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-B - RCA Eligibility Endpoint

Role: backend router implementer.

Objective: Modify only `backend/routers/rca.py` to add `GET /api/rca/eligible` and extend `StartTableRequest`.

Required behavior:

- Query `run_results` where `passed=0`.
- Optional table filter.
- Return newest 100.
- Include `run_id`, `table`, `test_id`, `instance_key`, `expected`, `actual`, `category`, and a best-effort `column`.
- `already_ticketed` is true when an open/closed ticket linkage or issue context references the run/test/table.
- `StartTableRequest` accepts optional `run_id` and `instance_key`.
- If `run_id` is provided and is not a failed run, return HTTP 400.

Test criteria:

- Backend imports with `python -c "import sys; sys.path.insert(0,'backend'); import main"`.
- Endpoint returns an array on an empty DB.
- Invalid passed run ID is rejected.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-C - RCA Step Persistence

Role: backend RCA agent implementer.

Objective: Modify only `backend/ai/rca_agent.py` to persist RCA sessions and steps.

Directives:

- Use `system_db` helpers.
- Persist a `rca_sessions` row on start.
- Persist `rca_steps` rows for SQL probes, code proposals, approvals/rejections, code output, root cause declaration, and checker result.
- Keep existing public response shape.
- Do not remove in-memory `_SESSIONS` yet; use persistence as durable audit trail.
- If Plan 8 `ai.context_memory` exists, write code output and final RCA contexts; if import fails, continue without breaking RCA.
- Include context bundle `usage_requirements` as a compact user-message section; do not edit or replace `RCA_SYSTEM`.
- Use IST ISO-8601 timestamps with `+05:30`.

Test criteria:

- `docs/history/pre-0.4.0-guide/scripts/run_rca.py --reject` still reaches a terminal result or known Azure-key failure path.
- Starting a session inserts one `rca_sessions` row.
- Approving/rejecting inserts ordered `rca_steps`.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-D - RCA Prompt and Checker Contract

Role: prompt/checker implementer.

Objective: Modify only `backend/ai/prompts.py` and `backend/ai/rca_checker.py`.

Directives:

- Update RCA prompt to use object context memory as supporting evidence.
- Require incremental analysis and evidence source labels.
- Add `confidence` and `supporting_context_ids` to root cause tool schema.
- Update Noether checker to return strict JSON with `verdict`, `approved`, `issues`, `suggestion`, and `ticket_ready`.
- Preserve backward compatibility for callers that expect `approved`.

Test criteria:

- `python -c "import sys; sys.path.insert(0,'backend'); from ai.prompts import RCA_TOOLS; from ai.rca_checker import check; print(RCA_TOOLS[-1]['function']['parameters']['properties'].keys())"` succeeds.
- `check({}, {})` returns a dict containing `approved`.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-E - RCA Ticket Gate

Role: backend ticket handoff implementer.

Objective: Modify only `backend/routers/rca.py` ticket raise flow.

Directives:

- Extend `RaiseTicketRequest` with optional `session_id` and `override_reason`.
- If `session_id` is provided, load RCA result/checker from session memory or `rca_sessions`.
- Reject ticket creation if checker verdict is not accepted/ticket-ready and `override_reason` is missing or shorter than 20 chars.
- Include root cause, evidence, remediation, checker verdict, and override reason in `resolution_path`.
- Include `session_id`, `run_id`, and `instance_key` in ticket `linkage` when available.

Test criteria:

- Existing `/api/rca/raise-ticket` payload without `session_id` still works.
- Payload with rejected checker and no override returns HTTP 400.
- Payload with accepted checker creates a ticket.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-F - RCA API Client Helpers

Role: frontend API implementer.

Objective: Modify only `dq-studio/src/api/client.js`.

Directives:

- Add `rcaEligible(table)`.
- Add `rcaSteps(id)`.
- Allow `rcaStartTable` to accept either `(table, failed_test)` or a single object containing `table`, `failed_test`, `run_id`, and `instance_key`.
- Do not change existing exports' names or remove backward compatibility.

Test criteria:

- `npm run build` in `dq-studio` succeeds.
- Existing imports in `RCA.jsx` still resolve.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-G - RCA Eligible Work Queue

Role: frontend RCA page implementer.

Objective: Modify only `dq-studio/src/pages/RCA.jsx` to load eligible failures from the backend.

Directives:

- Import and call `rcaEligible`.
- On table change, load eligible failures.
- Use API failures first; fall back to wizard `validationResults` only if API returns empty.
- Preserve current cards and controls.
- Pass `run_id` and `instance_key` to `rcaStartTable`.
- Fix ticket creation to pass `root_cause: agents[item.id]?.result?.root_cause || ""`.

Test criteria:

- `npm run build` succeeds.
- RCA page still renders with no backend failures.
- Clicking Run Agent Analysis sends run metadata when present.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA9-H - RCA Timeline and Ticket Gating

Role: frontend RCA page implementer.

Objective: Modify only `dq-studio/src/pages/RCA.jsx` after SA9-G has landed.

Directives:

- Import and call `rcaSteps`.
- Show a compact incremental timeline under each active RCA result.
- Disable normal Raise ticket until Noether accepts.
- Provide an override reason text area only when Noether does not accept.
- Pass `session_id` and `override_reason` to `rcaRaiseTicket`.

Test criteria:

- `npm run build` succeeds.
- Without checker approval, Raise ticket is gated.
- With checker approval, ticket dialog works as before.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.
