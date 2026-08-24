# 0.4.0 Phase 2 — Helper-layer declutter (PLT-08)

D-21: the AI helper/tool layer is decluttered first, before feature work. This
page is the map of `backend/ai/` after that pass: one sentence per module,
the single-registry design, the two root-caused import fixes, and the
logging contract. Test criteria 2-T1..2-T4 (`backend/tests/test_helpers.py`)
enforce everything below behaviourally, not just descriptively.

## 0.4.0 completion addendum

The Phase-6 Test Lab cutover is complete: the old wizard has no live caller.
The shared helper boundary now also serves the optional role-mapping verifier.
That verifier is deliberately separate from the deterministic engine: it is
off by default, can run once before manifest freeze through `ai.llm` and
`ai.control_plane.resolve("role_mapping_verifier")`, records advisory mapping
decisions, and cannot alter a verdict. The normal run path remains zero-model-call.

## 1. Module map (one sentence each)

| Module | What it is for |
|---|---|
| `ai/test_kit.py` | THE single governed helper catalogue (CFR-06/APL-37) — registers gx metrics, RCA probes, and tool_registry tools under one flat namespace, callable through one logging path. |
| `ai/tool_registry.py` | Role-gated, schema-validated, egress-checked wrapper layer over a handful of existing engines; delegates its catalogue entirely to `ai/test_kit.py` (no store of its own). |
| `ai/rca_helpers.py` | Twelve deterministic pandas/numpy RCA probes (`fn(df, params) -> dict`) an agent or human can point at any loaded DataFrame instead of writing ad hoc analysis code. |
| `ai/code_sandbox.py` | AST-guarded sandbox that validates and executes pandas/numpy/scipy code against a DataFrame and returns `{ok, result/error, traceback}` — never a bare exception. LIVE (`ai/v2/service.py`). |
| `ai/control_plane.py` | System-owned `{model, house, temperature, effort}` config per agent role — users never pick these knobs. LIVE for normal effort routing and the opt-in `role_mapping_verifier` role used before a Test Lab run freezes. |
| `ai/llm.py` | Lazy Azure/OpenAI client construction + a schema/stats prompt-block builder; importing it never requires an API key. The optional Test Lab role-verification request calls it once through the control-plane role; normal runs make zero model calls. |
| `ai/_helper_log.py` | The one logging helper: every catalogue call is observed through here (DEBUG line: name, context id, outcome, duration). |
| `ai/skills.py` | Agent registry + the skills-`.md` file convention (topology, call names, prompt storage). LIVE — imported by `seeds/__init__.py`. |
| `ai/skill_form.py` | The governed skill-`.md` FORM parser/validator (system-owned vs. user-owned blocks). Live via `ai/skills.py`'s save/load path. |
| `ai/dict_ingest.py` | **Deleted in Phase 3** with the verify_plan8.py rebuild (its only remaining importer). |
| `ai/effort.py` | `EffortPolicy` — tiered-sampling emulation of "effort" over Azure gpt-4.1. Kept: imported (relative) by `ai/bayes.py`/`credit_risk_domain.py`/`db_understanding.py`/`rca_checker.py`, which `ai/skills.py` lazy-loads. |
| `ai/test_manager.py` | **Deleted in Phase 3** with the verify_plan8.py rebuild (plan §1.3: "goes with the rebuild"). |
| `ai/v2/service.py` | Galileo v2 deterministic workflow services — the current live data/test execution model. |
| `ai/v2/issues.py` | Galileo v2 issue management (spec §9). |
| `ai/v2/report.py` | Galileo v2 on-demand PDF reports (spec §10). |
| `ai/v2/agents_roster.py` | Galileo v2 agent roster — single source of truth for the AI Agents module UI. |
| `ai/v2/__init__.py` | Package marker for the Galileo v2 deterministic agent layer. |
| `ai/agents/__init__.py` | Package marker for the single-responsibility agents behind the (retired) New Test Manager loop. |
| `ai/agents/criticality_agent.py` | Criticality-ranking agent (call-name Pascal). |
| `ai/agents/global_consistency.py` | Cross-table relationship consistency-check agent (call-name Euler). |
| `ai/agents/relationship_discovery.py` | Relationship-candidate discovery agent (call-name Poincaré). |
| `ai/agents/relationship_join.py` | Relationship/ERD architect agent (call-name Codd) + its deterministic evidence harness. |
| `ai/agents/relationship_validation.py` | Independent relationship-validation agent (call-name Fermat). |
| `ai/agents/test_screening.py` | Library test screening with explicit Search / Deep Search modes (call-name Hypatia). |
| `ai/agents/variable_screening.py` | Deterministic (no-LLM) column-profiling utility feeding the screening agents. |
| `ai/bayes.py` | Test Lab HITL feedback adjudicator ("the shuttle" — call-name Bayes). |
| `ai/context_memory.py` | Object-scoped context memory (Plan 8) shared across agents/turns. |
| `ai/credit_risk_domain.py` | Credit-risk domain-expert agent (call-name Merton). |
| `ai/db_understanding.py` | Database-Understanding agent that produces the DB summary artifact (call-name Newton). |
| `ai/discovery_state.py` | Deterministic per-column profiling state machine feeding Newton's discovery phase. |
| `ai/framework_context.py` | The bridge between a database's declared framework mapping and the rest of the platform. |
| `ai/prompts.py` | Static system/user prompt text for both AI layers. |
| `ai/rca_checker.py` | RCA Checker agent that judges candidate root causes (call-name Noether). |
| `ai/report_pdf.py` | PDF report generator for the Database Understanding report. |
| `ai/frame_assembler.py` | **Deleted in the 2.10 sweep** — broken at baseline (module-level import of the deleted `database`), zero importers. |
| `ai/rule_generator.py` | **Deleted in the 2.10 sweep** — broken at baseline, zero importers. |

`gx/metrics.py` (outside `ai/`, referenced by `ai/test_kit.py`) is the calibrated
pandas/numpy metric library behind Great Expectations custom expectations —
listed here only because its ten functions are catalogue entries.

## 2. Single-registry design

Before this phase, `ai/test_kit.py` was itself a large "Test Lab Phase 1"
catalogue (`Tool` dataclass with shape/facets/framework-area tagging,
`list_tools`/`coverage_by_area`/`area_labels`, and a `_library_tools()` that
read the seeded `test_library` table from `system_db` at import time), while
`ai/tool_registry.py` kept a second, independent `_REGISTRY` dict of its own
role-gated `Tool` objects. That is two catalogues, one of which did I/O
(a DB read) at import time.

Phase 2 replaces both with one flat namespace:

* `ai/test_kit.py` defines `Helper` (name, fn, purpose, kind, inputs,
  outputs, failure_modes, version, plus an opaque `meta` extension bucket)
  and `register`/`get_helper`/`list_helpers`/`call`. At import time it
  registers, with **no I/O**: the ten `gx/metrics.py` primitives (kind=
  `"metric"`) and the twelve `ai/rca_helpers.HELPERS` probes (kind=
  `"rca_helper"`, wrapped so `call(name, df=..., **params)` still works
  against their native `fn(df, params)` shape).
* `ai/tool_registry.py` keeps its `Tool` dataclass and role/schema/egress
  checks (that concern belongs there, not in the generic catalogue), but
  `register()` now wraps every `Tool` into a `Helper` (kind=`"tool"`, with
  the role/egress/schema metadata stashed in `Helper.meta`) and calls
  `ai.test_kit.register()`. `list_tools()`/`get_tool()`/`call()` all read
  back through `ai.test_kit`. `tool_registry._REGISTRY` is now a **read-through
  alias onto `ai.test_kit._CATALOGUE`** (the same dict object, not a copy) —
  kept only because `ai/dict_ingest.py`'s pre-existing
  `"parse_data_dictionary" in tr._REGISTRY` idempotency check depends on it.
* The old `_library_tools()` (reading `system_db.test_library` at import
  time) is **retired**, not reintroduced. That catalogue belongs to the Test
  Lab redesign (D-16), which replaces the wizard outright; resurrecting a
  partial read of the old `test_library` table here would be exactly the kind
  of half-migrated state this phase is removing. `system_db.query()`-based
  reads are still the right idiom when that work resumes (see §3a).
* Seam: `# Phase 6 registers the nine cross-field primitives here (CFR-06)` is
  left as a comment at the bottom of `ai/test_kit.py`'s registration section.

Every call — metric, RCA probe, or tool — goes through the same
`ai.test_kit.call(name, *, context_id=None, **kwargs)`, so a duplicate name
across kinds cannot silently shadow another helper: `register()` raises
`ValueError` the instant a name collides (test 2-T2; the "invariant bites"
proof in `test_helpers.py` verifies the guard is load-bearing, not vacuous).

## 3. The two root-caused import defects

`backend/database.py` was deleted at 0.2.0. Two lazy imports in
`ai/tool_registry.py` were written against modules that no longer exist:

1. **`_fetch_schema_stats`** called `routers.ingestion.record_counts` +
   `database.load_table` — both gone (the Galileo *ingestion* router and the
   fixed-demo-warehouse loader). Root cause: the function was written against
   the deleted Galileo ingestion path. Fix: rewired to the live data model —
   `system_db.query("table_metadata", logical_db=...)` (the same `query()`
   idiom `system_db.py` itself documents), returning per-table row/col counts
   and per-column name/dtype/description straight from the seeded metadata.
   It no longer computes a live null%/n_distinct scan (there is no single
   fixed warehouse loader left to scan safely through a generic tool), which
   is a smaller, metadata-only surface — consistent with this tool's
   `metadata_only` egress contract.
2. **`_raise_mitigation_ticket`** called `routers.tickets.raise_ticket` —
   `routers/tickets.py` is unmounted dead code deleted this phase. Its only
   consumer was `ai/test_manager.py` (itself deleted in Phase 3 with the
   `verify_plan8.py` rebuild — see below). Fix: **deleted the tool entirely**
   (not patched) — there is nothing left to call it and nothing for it to
   reach.

`_execute_sandboxed_code` still contains a third, lazy `from database import
load_table` and would raise `ModuleNotFoundError` if actually invoked. It is
left as-is: the import is function-scoped (doesn't break importing the
module — 2-T1 only requires clean *module* import), and it was not one of
the two defects this phase's task named to root-cause. Re-wiring it to the
live per-item table store needs an `item_id` this tool's schema doesn't
carry yet; tracked with `commit_test_logic`/`infer_relationships` for a later
pass.

`ai/test_manager.py`, `ai/frame_assembler.py`, and `ai/rule_generator.py` all
shared the exact same root cause (a top-level `from database import ...`).
The latter two had zero importers and were deleted in the Phase-2 sweep
(2.10); `ai/test_manager.py` was preserved through Phase 2 per plan §1.3 and
**deleted in Phase 3** together with `ai/dict_ingest.py` (its only remaining
importer) once `verify_plan8.py` was rebuilt against the new register — the
allowlists that named them (`backend/tests/test_helpers.py`'s
`_IMPORT_ALLOWLIST`, `tests/test_reachability.py`'s `KEEP_REASONS`) are now
empty of those three rows; only `ai/effort.py` (kept — imported relatively
by `ai/bayes.py`/`credit_risk_domain.py`/`db_understanding.py`/
`rca_checker.py`), `ai/skill_form.py`, `verify_plan8.py` itself, and the
PSI-parity `spike_*.py` scripts remain in the keep-list. Historically, F-01
("references the deleted `database.py`") applied to three of them at the
baseline. That condition is resolved: `spike_recalibrate.py`, `spike_gx.py`,
and `spike_integration.py` now use the deterministic, spike-only
`backend/spike_fixtures.py` adapter and exit 0. They remain parity evidence
only; they do not enable PSI or any B2 diagnostic.

## 4. Logging contract

`ai/_helper_log.py` is the one place that formats the structured log line.
Every call through `ai.test_kit.call()` (and therefore every call through
`ai.tool_registry.call()`, which routes through it) logs exactly one line at
DEBUG on logger `"archimedes.helpers"`:

```
helper=<name> context_id=<id-or--> outcome=ok|error [error=<ExceptionClass>] duration_ms=<float>
```

`context_id` is whatever the caller passes (a run/case id) or `"-"` when it
has none. On success, `outcome=ok`. On an exception, `outcome=error` plus the
exception's class name is logged, and the exception is **always re-raised
unchanged** — this module only observes, it never swallows an error or
changes what the caller sees. `code_sandbox.run` logs through the same
helper (`name="code_sandbox.run"`) even though it never raises itself (every
failure path is an `{"ok": False, ...}` return), so every invocation is still
observable the same way regardless of kind.

## Reachability sweep record (2.10)

Executed 30 Jul 2026, same evidence standard as tranche 1 (every deletion
verified unreachable from the mounted app: only `auth`, `admin`, `v2`, `v3`
routers are mounted, `main.py:150-153`; `App.jsx` imports exactly 10 routed
pages; grep across the tree found zero import sites for each deleted file).

**Deleted (15 files):**

| File | Evidence |
|---|---|
| `routers/ai_rules.py`, `routers/inventory.py`, `routers/monitoring.py`, `routers/preview.py`, `routers/skills.py`, `routers/tickets.py`, `routers/use_cases.py` | Not mounted in `main.py`; no `include_router` or import anywhere live. `routers/tickets.py` had one lazy importer — `ai/tool_registry.py:217`'s `_raise_mitigation_ticket` — whose only consumer was the preserved-dead `ai/test_manager.py`; that tool path was deleted this phase (see §3). e2e already asserted retired endpoints 404 (`upload-workflow.spec.js`) |
| `ai/recommend.py` | Zero importers (its import of the pre-rebuild `ai.test_kit` was itself broken at baseline) |
| `ai/frame_assembler.py`, `ai/rule_generator.py` | Zero importers; module-level import of `database` (deleted 0.2.0) — broken at baseline |
| `ui/src/pages/Dashboards.jsx`, `AgenticSkills.jsx`, `AIAgents.jsx`, `Placeholder.jsx`, `RCA.jsx` | Absent from `App.jsx` routes and `Sidebar.jsx` nav; zero import sites (only comment mentions in `lib/markdown.js`, `data/mock.js`); `rca.spec.js` asserts no RCA nav surface |

**Kept, with reasons** (mirrored in `tests/test_reachability.py::KEEP_REASONS`):

| File | Keep-reason |
|---|---|
| `ai/skills.py` | **Live** — imported by `seeds/__init__.py:97` (`seed_agents`, boot path) |
| `ai/skill_form.py` | Imported lazily by live `ai/skills.py` (`save_prompt`/load validation) |
| `ai/effort.py` | **Live** — imported (relative) by `ai/bayes.py`/`credit_risk_domain.py`/`db_understanding.py`/`rca_checker.py`, which `ai/skills.py` lazy-loads |
| `verify_plan8.py` | Validation gate; rebuilt against the new register **in Phase 3** (rule 7) — see the Phase-3 addendum below |
| `gx/` + `spike_gx.py`, `spike_integration.py`, `spike_metrics.py`, `spike_recalibrate.py`, `spike_fixtures.py` | PSI parity evidence only. The three former F-01 spikes (`spike_gx.py`, `spike_integration.py`, `spike_recalibrate.py`) now use deterministic spike-only fixtures and exit 0; none enables PSI or a B2 diagnostic. |
| `ui/src/api/client.js` retired wizard block | Removed at the Phase-6 cutover. The live v2 client now serves Data Sourcing and the register-driven Test Lab; no retired wizard caller remains. |

**Phase-3 addendum (30–31 Jul 2026).** `ai/test_manager.py` and
`ai/dict_ingest.py` — kept above "until the Phase-3 rebuild" — were deleted
in Phase 3 once `verify_plan8.py` was rebuilt against the new register (their
only remaining importer/consumer). `skill_form.py`'s `_tool_whitelist` no
longer force-imports either module to complete the tool catalogue (it never
needed to: `ai.tool_registry` self-registers on import). Both allowlists
(`test_helpers.py::_IMPORT_ALLOWLIST`, `test_reachability.py::KEEP_REASONS`)
are updated accordingly and are now empty of preserved-broken rows — every
`ai/` module imports cleanly. `dq_tests/registry.py` and
`dq_tests/param_specs.py` were rewritten (not deleted, so `ai/v2/service.py`
stays importable until the Phase-6 cutover) to refuse every test name —
FWK-13's registry retirement, and the new 9-diagnostic register lives in
`dq_diagnostics/register.py` + `knowledge_base/dq_framework_data.json`.

The 2-T9 gate is `tests/test_reachability.py`: every remaining `routers/*.py`
file must be mounted, and every KEEP_REASONS row must both exist on disk and
be recorded in this document.
