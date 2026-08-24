# Plan 8 - Object Context Memory Architecture

## Purpose

Build a system-owned context memory layer for Intelligent DQ that attaches reusable context to domain objects: logical databases, tables, variables, tests, validation runs, RCA findings, tickets, and human decisions. The layer must separate static demo/common memory from demo-created memory so the admin demo-delete reset can remove only demo-created artefacts.

This plan is architecture-only. It defines the contract that Plan 9 RCA must consume.

## Current Baseline

- Mutable platform state is in `backend/system_state.db`, managed by `backend/system_db.py`.
- Product input is supplied by user uploads; no bundled physical warehouse is used.
- Existing persisted context lives in scattered JSON columns: `ingested_databases`, `table_metadata`, `test_plan`, `run_results`, `tickets`, and `hitl_decisions`.
- Existing AI skill prompts live in `backend/skills/*.md`.
- Demo reset currently clears demo work-product, but there is no first-class object memory table.

## Architecture Decision

Add first-class memory tables to `system_state.db`. Keep all domain rows read-only. Treat context memory as application metadata indexed by stable object references.

### Data Model

Add table `object_contexts`:

| Column | Type | Rule |
| --- | --- | --- |
| `context_id` | TEXT PK | UUID/ULID string |
| `scope` | TEXT | `common` or `demo` |
| `logical_db` | TEXT | nullable for cross-db memories |
| `object_type` | TEXT | `database`, `table`, `variable`, `relationship`, `test`, `test_instance`, `run_result`, `rca`, `ticket`, `global` |
| `object_key` | TEXT | stable key, e.g. `retail_risk_db.retail_accounts.bureau_score` |
| `context_type` | TEXT | `business`, `schema`, `lineage`, `dq_rule`, `failure`, `rca_evidence`, `remediation`, `ticket_update`, `human_note`, `agent_summary` |
| `title` | TEXT | short display label |
| `content` | TEXT | compact natural-language or JSON text |
| `source` | TEXT | `seed`, `ingestion`, `human`, `agent`, `run`, `ticket` |
| `confidence` | REAL | 0.0 to 1.0 |
| `token_estimate` | INTEGER | approximate prompt cost |
| `priority` | INTEGER | 1 highest to 5 lowest |
| `tags` | TEXT JSON | small string array |
| `expires_at` | TEXT | nullable |
| `created_at` | TEXT | IST ISO-8601 with `+05:30` offset |
| `updated_at` | TEXT | IST ISO-8601 with `+05:30` offset |

Add table `context_links`:

| Column | Type | Rule |
| --- | --- | --- |
| `link_id` | TEXT PK | UUID/ULID string |
| `from_context_id` | TEXT | source context |
| `to_object_type` | TEXT | same enum as `object_type` |
| `to_object_key` | TEXT | stable object key |
| `relation` | TEXT | `explains`, `depends_on`, `derived_from`, `contradicts`, `remediates`, `ticketed_by` |
| `weight` | REAL | 0.0 to 1.0 |

Indexes:

```sql
CREATE INDEX IF NOT EXISTS ix_object_contexts_lookup
ON object_contexts(logical_db, object_type, object_key, context_type, scope, priority);

CREATE INDEX IF NOT EXISTS ix_object_contexts_tags
ON object_contexts(object_type, source, scope);

CREATE INDEX IF NOT EXISTS ix_context_links_object
ON context_links(to_object_type, to_object_key, relation);
```

System DB JSON configuration:

- Add `object_contexts.tags` to `_JSON_COLS`.
- All context timestamps must be generated in IST. Use one shared helper such as `now_ist()` returning ISO-8601 strings with the `+05:30` offset.

Demo reset:

- Delete `object_contexts` where `scope='demo'`.
- Delete `context_links` whose `from_context_id` no longer exists.
- Preserve all `scope='common'` rows.

### Object Key Canonicalization

| Object | Key format |
| --- | --- |
| Logical DB | `{logical_db}` |
| Table | `{logical_db}.{table}` |
| Variable | `{logical_db}.{table}.{column}` |
| Relationship | `{logical_db}.{left_table}->{right_table}:{join_keys}` |
| Test library item | `test_library.{test_id}` |
| Test instance | `{table}|{test_id}|{json_sorted_fields}` matching `test_plan.instance_key` |
| Run result | `run_result.{run_id}` |
| RCA session/result | `rca.{session_id}` or `rca_result.{context_id}` |
| Ticket | `ticket.{ticket_no}` |

### Retrieval API

Create `backend/ai/context_memory.py` with:

```python
def upsert_context(row: dict) -> dict: ...
def get_context_bundle(
    *,
    logical_db: str | None,
    table: str | None,
    columns: list[str] | None,
    test_id: str | None,
    instance_key: str | None,
    run_id: int | None,
    ticket_no: str | None = None,
    max_tokens: int = 1800,
) -> dict: ...
def purge_demo_contexts() -> dict: ...
```

Bundle output:

```json
{
  "object_keys": ["retail_risk_db.retail_accounts"],
  "contexts": [
    {
      "context_id": "...",
      "object_type": "variable",
      "object_key": "retail_risk_db.retail_accounts.bureau_score",
      "context_type": "failure",
      "title": "Recent PSI breach",
      "content": "...",
      "source": "run",
      "confidence": 0.95
    }
  ],
  "omitted_count": 4,
  "token_estimate": 1640
}
```

Retrieval rules:

- Hard cap: 1800 tokens per agent prompt by default.
- Ordering score: exact object match +40, linked object +25, same table +15, same logical DB +8, priority bonus `(6 - priority) * 3`, recent demo context +5.
- Include at most 2 database-level contexts, 3 table-level contexts, 4 variable/test contexts, and 3 recent failure/RCA/ticket contexts.
- Deduplicate by content hash or same `(object_type, object_key, context_type, title)`.
- Never pass raw full schema JSON if compact `table_metadata` or `ai_summary` is enough.
- Return `retrieval_evidence` in every bundle: selected count by object type, selected count by context type, omitted count, token estimate, and the top 5 scoring reasons. This is the audit trail that proves pointed retrieval happened.
- Return `usage_requirements`: a compact instruction block telling the caller how to use the bundle. This keeps functional agent prompts intact and avoids copying memory instructions into every skill file.

### Effectiveness Assessment

The concept is effective if it is treated as a retrieval-augmented object graph, not as a generic chat memory. In this codebase it is a strong fit because the workflow already has stable object anchors: `logical_db`, table names, columns, `test_plan.instance_key`, `run_results.run_id`, RCA sessions, and `tickets.ticket_no`.

Expected effectiveness:

- High for RCA: failures usually need recent run facts, table/variable metadata, prior RCA evidence, and ticket history. These are exactly the objects Plan 8 indexes.
- Medium-high for test recommendation and criticality: table/variable context improves ranking, but live schema/statistics must still dominate.
- Medium for database understanding: Newton creates high-value summaries, but should not blindly consume old summaries during fresh ingestion.

Loose ends and tightenings:

| Loose end | Risk | Tightening decision |
| --- | --- | --- |
| Memory bloat | Agents receive stale/noisy context | Enforce token caps, per-type caps, dedupe, and `retrieval_evidence` on every bundle. |
| Stale common memory | Old seeded assumptions override new data | Treat memory as supporting evidence only; live probes and run output win on conflict. |
| Prompt pollution | Functional skill prompts get overwritten | Add a separate Context Memory Broker/Curator instruction and only add a one-line call hook to functional skills when needed. |
| Weak object keys | Retrieval misses or over-selects | Canonicalize keys and require exact table/variable/test_instance/run_result keys in write paths. |
| Unproven usage | Agents retrieve memory but ignore it | Require RCA/checker outputs to cite `supporting_context_ids` or state `none_used`. |
| Demo reset leakage | Demo-created memory survives reset | Scope every created row as `demo`; reset deletes `scope='demo'` and orphan links. |
| Timestamp confusion | Mixed audit trails | Store all Plan 8/9 timestamps in IST ISO-8601 with `+05:30`. |

Evidence from the existing system that this architecture fits:

- `backend/system_db.py` already centralizes mutable state and JSON encode/decode, so object memory can be added without touching domain DBs.
- `backend/system_db.py` already has scoped demo reset behavior, so common/demo memory separation fits the current retention model.
- `test_plan.instance_key` and `run_results.run_id` already provide stable anchors for test instance and failure memory.
- `backend/ai/rca_agent.py` already has an incremental HITL loop with SQL probes and approved sandbox code, so each RCA step has a natural persistence point.
- `backend/routers/tickets.py` already persists immutable `issue_context`, `resolution_path`, and `resolution_updates`, so ticket memory can be linked without changing Issue Management's core lifecycle.

### Context Creation Points

| Event | Context rows |
| --- | --- |
| Static seed load | `scope='common'` database/table/test/ticket background |
| Ingestion/Newton summary | `scope='demo'` database/table `agent_summary` |
| Table selection | `scope='demo'` table `business` or `human_note` if supplied |
| Test plan attach/finalize | `scope='demo'` test_instance `dq_rule` |
| Validation failure | `scope='demo'` run_result `failure` |
| RCA code approval/output | `scope='demo'` rca `rca_evidence` |
| RCA declaration | `scope='demo'` rca `remediation` |
| Noether check | `scope='demo'` rca `agent_summary` |
| Ticket raise/update | `scope='demo'` ticket `ticket_update`, linked to RCA and failed test |

### Skill Prompt Contract

Do not overwrite functional prompts such as `newton.md`, `feynman.md`, `noether.md`, `gauss.md`, `hypatia.md`, or `pascal.md`. Those files contain the agent's operating role and output contract. Memory behavior must be provided through a separate Context Memory Broker/Curator instruction that agents call as a small function-like dependency.

Add one new skill prompt:

- `backend/skills/context_memory_broker.md`: owns the reusable instruction for retrieving, ranking, compressing, and citing object context memory.

Relevant functional skills should receive at most a small hook, not a rewrite:

```text
When object context is available, call the Context Memory Broker before final reasoning.
Use its compact bundle only as supporting evidence and cite supporting_context_ids
when memory materially influences the output.
```

The Context Memory Broker must instruct agents to consume object context memory:

- `newton.md`: write compact database/table summaries into context memory.
- `hypatia.md`, `gauss.md`, `pascal.md`: retrieve table/variable/test context before recommending or ranking.
- `feynman.md`: retrieve failed test, variable, table, prior RCA, and ticket context before analysis; write each accepted diagnostic observation as evidence memory.
- `noether.md`: retrieve the same bundle plus Feynman's evidence memories and verify the conclusion is supported.

Required wording for `context_memory_broker.md`:

```text
Before reasoning, request the compact object context bundle for the database, table,
variables, test instance, run result, prior RCA, and linked tickets in scope. Use
only the highest-value contexts within the provided token budget. Treat context
memory as supporting evidence, not ground truth; prefer live run output and
read-only data probes when they conflict.
```

Required broker output contract:

```json
{
  "bundle": {},
  "usage_requirements": ["..."],
  "retrieval_evidence": {},
  "supporting_context_ids": []
}
```

## Master Execution Plan

### Wave 0 - Orchestrator-Owned Architecture

Owner: Orchestrator only.

Tasks:

- Freeze the `object_contexts` and `context_links` schema above.
- Confirm object key formats against `test_plan.instance_key` and ticket IDs.
- Keep token allocation/retrieval policy centralized in `context_memory.py`.
- Keep timestamp semantics centralized: every Plan 8 timestamp is IST ISO-8601 with `+05:30`.
- Create the Context Memory Broker contract; do not duplicate large memory instructions across functional skill prompts.

Exit criteria:

- No sub-agent changes the schema or retrieval scoring.
- All sub-agent work compiles against this contract.

### Wave 1 - Parallel Delegated Implementation

Run these independently:

- SA8-A: system DB schema/migration/reset support.
- SA8-B: `ai/context_memory.py` repository functions.
- SA8-C: seed/common context generation from existing static files.
- SA8-D: Context Memory Broker skill plus minimal hook updates.
- SA8-E: API/admin smoke endpoint only if needed for inspection.

### Wave 2 - Orchestrator Integration

Owner: Orchestrator only.

Tasks:

- Wire context creation at ingestion, test-plan finalization, run failure, RCA, and ticketing boundaries.
- Verify reset deletes `scope='demo'` contexts and preserves `scope='common'`.
- Add focused tests or smoke scripts for insert/retrieve/purge behavior.
- Verify retrieval evidence appears in bundles and that RCA/checker outputs cite `supporting_context_ids` when memory influenced the answer.

## Execution Preferences for AI Builder

- Orchestrator retains schema decisions, retrieval scoring, object key rules, and cross-workflow integration.
- Orchestrator retains memory usage policy: functional prompts stay functional; the broker owns shared memory instructions.
- Sub-agents receive only narrow snippets and exact file targets.
- No sub-agent receives full docs, PDF extracts, or global architecture beyond the snippet needed for its file.
- Parallelize Wave 1. Do not parallelize Wave 2.
- Each delegated prompt must include this exact output constraint:

```text
Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.
```

## Sub-Agent Prompts

### SA8-A - System DB Memory Tables

Role: backend SQLite schema implementer.

Objective: Modify only `backend/system_db.py` to add `object_contexts` and `context_links`, JSON handling for `object_contexts.tags`, additive migrations, and demo reset purge for demo memory.

Context snippet:

```python
_JSON_COLS = {
    ...
    "health_scores": {"category_scores"},
}

_SCHEMA = """
...
CREATE TABLE IF NOT EXISTS transaction_log (...);
"""

_WORKPRODUCT_TABLES = [
    "test_plan", "run_results", "health_scores", "hitl_decisions",
    "monitoring", "tickets",
]
```

Directives:

- Add DDL for `object_contexts` and `context_links` exactly as Plan 8 specifies.
- Add the three indexes from Plan 8 inside `init_schema()`.
- Add `"object_contexts": {"tags"}` to `_JSON_COLS`.
- Add a reusable `now_ist()` helper and use it for Plan 8 rows whenever this file creates timestamps.
- In `reset_demo()`, delete `object_contexts` where `scope='demo'`, then delete orphaned `context_links`.
- Do not alter existing tables except additive DDL.

Test criteria:

- `python backend/system_db.py` succeeds.
- `object_contexts` and `context_links` appear in SQLite table list.
- Running `reset_demo()` twice is idempotent.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA8-B - Context Memory Repository

Role: backend repository implementer.

Objective: Create only `backend/ai/context_memory.py` with functions `upsert_context`, `get_context_bundle`, and `purge_demo_contexts`.

Required API:

```python
def upsert_context(row: dict) -> dict: ...
def get_context_bundle(*, logical_db=None, table=None, columns=None, test_id=None,
                       instance_key=None, run_id=None, ticket_no=None,
                       max_tokens=1800) -> dict: ...
def purge_demo_contexts() -> dict: ...
```

Directives:

- Use `system_db` helpers only.
- Generate `context_id` when missing.
- Estimate tokens as `max(1, len(content) // 4)`.
- Implement Plan 8 ordering, inclusion caps, and dedupe.
- Return dict shape exactly as Plan 8 specifies.
- Include `retrieval_evidence` and `usage_requirements` in every returned bundle.
- Use IST ISO-8601 timestamps with `+05:30` for created/updated rows.
- No LLM calls.

Test criteria:

- Module imports from repo root with `python -c "import sys; sys.path.insert(0,'backend'); from ai.context_memory import get_context_bundle"`.
- Upsert then retrieve returns the inserted context.
- `purge_demo_contexts()` removes demo contexts and leaves common contexts.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA8-C - Common Context Seeder

Role: seed-data implementer.

Objective: Add common memory seeding without changing runtime behavior.

File targets:

- `backend/seeds/__init__.py`
- optional new file `backend/seeds/context_memory_seed.py`

Directives:

- Seed `scope='common'` contexts for static test library rows and pre-seeded database/table metadata.
- Use `upsert_context`.
- Keep generated content compact: max 500 chars per context.
- Do not seed demo contexts.
- Ensure reseeding is idempotent.

Test criteria:

- `python -c "import sys; sys.path.insert(0,'backend'); from seeds import seed_all; print(seed_all())"` succeeds.
- Re-running the command does not duplicate common context rows for the same object/title/type.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA8-D - Context Memory Broker Skill

Role: prompt file editor.

Objective: Create `backend/skills/context_memory_broker.md` and add only a minimal hook to `backend/skills/newton.md`, `backend/skills/hypatia.md`, `backend/skills/gauss.md`, `backend/skills/pascal.md`, `backend/skills/feynman.md`, and `backend/skills/noether.md`.

Broker required text:

```text
Before reasoning, request the compact object context bundle for the database, table,
variables, test instance, run result, prior RCA, and linked tickets in scope. Use
only the highest-value contexts within the provided token budget. Treat context
memory as supporting evidence, not ground truth; prefer live run output and
read-only data probes when they conflict.
```

Minimal hook text for functional skills:

```text
When object context is available, call the Context Memory Broker before final reasoning.
Use its compact bundle only as supporting evidence and cite supporting_context_ids
when memory materially influences the output.
```

Directives:

- Preserve each file's role and output contract.
- Do not rewrite any functional skill.
- Do not place the full broker instruction in functional skills.
- Add only the minimal hook under a heading named `Context Memory Hook`.
- The full memory instruction belongs only in `context_memory_broker.md`.

Test criteria:

- `rg -n "Context Memory Broker|compact object context bundle|Context Memory Hook" backend/skills` shows the broker file and the six functional hooks.
- Direct file inspection shows only small hook additions in functional prompts, not rewritten roles or output contracts.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.

### SA8-E - Context Inspection Endpoint

Role: backend route implementer.

Objective: Add a minimal admin/debug read endpoint only if the orchestrator asks for it.

File target:

- New router or existing admin router, orchestrator will decide.

Directives:

- Expose `GET /api/admin/context-memory?object_key=...`.
- Return matching contexts only; no mutation.
- Require the same admin/session guard pattern already used by `backend/routers/admin.py`.

Test criteria:

- Endpoint returns JSON array for a known seeded context.
- Unauthenticated request fails consistently with admin routes.

Output constraint:

Return ONLY the necessary code diff and a brief test pass/fail summary. Do not output the entire file. Do not explain the code.
