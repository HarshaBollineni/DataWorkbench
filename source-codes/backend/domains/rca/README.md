# Root Cause Analysis

This domain owns the governed RCA case lifecycle, deterministic analysis-helper catalogue, and
effective-challenge adapter. Taxonomy, Knowledge Base, issue management, authentication, and the
Analysis Artifact Repository remain separate capabilities because they serve workflows beyond RCA.

| File | Ownership |
| --- | --- |
| `service.py` | Cases, evidence, hypotheses, confirmation, fixes, closure, and reusable-knowledge hand-off |
| `initial_review.py` | Structured LLM interpretation of bounded deterministic opening evidence |
| `analysis_helpers.py` | Vetted deterministic RCA probes and generated-helper validation |
| `effective_challenge.py` | Noether effective-challenge adapter |

Shared provider configuration and primary/fallback execution live in `ai/model_registry.py` and
`ai/model_runtime.py`. Model identities and permitted fallback workloads are deployment configuration,
not hard-coded RCA requirements. Endpoint and key values remain backend-only. Opening review uses
bounded metadata and aggregates; deterministic evidence remains usable when model review fails.

## Current investigation workflow

Intake presents retained diagnostic findings and limitations before descriptive
profiles. Source-evidence responses include retained verdict, non-evaluation reason
and scope counts when available. These are display projections, not recalculations.
The existing investigation-context endpoint also accepts case-wide context in the
`intake` state (no hypothesis ID). It records an intake-stage AAR event and supplies
the saved context to the initial review as unverified background. Snapshot metadata
and diagnostic evidence are unchanged; start-afresh clears this generation's context.

The issue page presents Intake, Initial Review, Investigate, and Closure. Human selection of a
hypothesis precedes agent-planned investigations. One Run action executes driver discovery and
one confirmation under the original selected hypothesis, without intermediate user input.
Discovery identifies an associated separator; confirmation evaluates the original explanation.
The service retains two linked looks and one `combined_hypothesis_run` outcome. The pair costs
one budget unit and counts as one successful hypothesis run only after confirmation and its
interpretation complete. Missing information, no separator or failure stops the bounded run
without an automatic retry. Older retained focused candidates still support human review.
Each RCA generation permits two started agent hypothesis runs. The case response's
`investigation_limit` exposes limit, used, remaining and reached. Planning and
execution enforce the cap, including old pending plans; execution slots are claimed
in a SQLite transaction. Discovery/confirmation share one slot. Failed runs consume
a slot; opening evidence, unrun proposals and cancelled plans do not. This cap is
separate from the legacy look budget. Chat still unlocks only after two successful
runs; closure remains available. Start afresh clears the generation's run count
along with its other derived RCA state.
Investigation plans search
the helper catalogue before generating code for the guarded child-process runtime.

| File | Additional ownership |
| --- | --- |
| `initial_review_evidence.py` | Deterministic opening evidence and population summaries |
| `feature_states.py` | Frozen per-column governance, row-state classification and analysis frames |
| `driver_search.py` | Governed target definition, shallow tree discovery and segment attribution |
| `investigation_agent.py` | Structured planning, code generation and interpretation |
| `investigation_runtime.py`, `sandbox_worker.py` | Helper selection, code checks, subprocess execution and bounded results |
| `data_chat.py` | Structured question scope, answer planning and evidence-based answers |
| `evidence.py` | Immutable AAR events and current-generation continuation projection |

## Feature-state contract

Each analyzed feature distinguishes regular values, physical missing values and each confirmed
declared special value. Regular numerical calculations exclude missing and confirmed special
values; indicators/segments and reconciliation retain their populations. Threshold descriptions
identify regular values, for example `NOI regular <= 3050`, alongside `NOI physical missing` and
`NOI special: -999`. Proposed but unconfirmed special values remain regular, with governance
disclosed. Helpers and generated analyses share the deterministic feature-state boundary.

Case responses include a creation-ordered `hypothesis_catalog` for stable UI references
within an RCA generation. The investigation-context endpoint accepts an optional
`hypothesis_id`: it must be the active hypothesis in this case. Context retains its
hypothesis association in the AAR and planner input. Saving supersedes the pending
plan for that hypothesis, leaves completed evidence intact, and requires explicit
replanning. Cancelled plans cannot execute. Legacy case-wide context remains supported.

The planner route accepts optional `exploration=follow_up|alternative`. Follow-up
reuses an unexecuted current plan or plans a new test under the active ID. Alternative
uses the existing planner model policy to propose one distinct explanation from the
frozen schema, diagnostic, history and context; its model/input/output and selection
intent are retained in AAR. It creates an `alternative_explanation` hypothesis,
supersedes unexecuted prior plans, and preserves completed evidence. Case responses
expose `active_investigation_hypothesis`. The UI chains planning to the existing run
route once, without an autonomous loop. A failed later step reloads retained state.
Only successfully executed hypothesis tests increase the chat-unlock count; proposing
a new hypothesis does not, and an inconclusive assessment still counts as an execution.

Case creation freezes per-column metadata in `checklist_json.feature_state_snapshot` and case-context
evidence. Existing cases without this snapshot remain readable and do not infer unconfirmed
sentinels. Start afresh captures current metadata for a new generation. The previously discussed
generated 1% unallocated-value threshold is outside this change.

## Data chat and retention

`POST /api/v3/rca/cases/{case_id}/data-chat` accepts `{ "question": "..." }` and returns the case
bundle, including `data_chat`. Chat unlocks after two completed planned `agent_hypothesis_test`
executions with `runtime.ok == true`; a combined confirmation additionally requires its parent
run to be completed. Opening reviews, standalone driver discovery, failures, cancellations
and superseded unexecuted plans do not count.

A question uses retained evidence or one calculation. The service searches compatible helpers
first and generates code only when none fits; it never starts an autonomous analysis loop or
creates investigation looks, hypothesis assessments or budget entries. Scope classification is
model-based; dataset access is constrained to the active case's analysis table.

AAR events retain the question and supplied evidence, plan/model attempts, library decision,
generated code when accepted, execution outcome and bounded result, answer, references and
limitations. Full generated output is a separate downloadable artifact. Failed planning and
answer turns remain visible after reload. `rca_aar_links` reconstructs the current generation;
chat needs no separate conversation database. Start afresh removes RCA-owned evidence and chat
with the other derived case state, preserving source diagnostic artifacts.

## Validation and deployment

### Shared result presentation and completion reports

`presentation.py` adds a read-only versioned presentation to returned executions:
labelled metric values, explicit units, optional comparison populations, and an
execution evidence reference. The UI does not select cards by diagnostic name or
column name. Unknown metrics stay in supporting evidence/audit output; invalid or
unavailable values never become a fabricated comparison. Existing stored results
are adapted on read without rewriting evidence. This does not change which
diagnostic targets the discovery engine supports.

`GET /api/v3/rca/cases/{case_id}/report?fmt=pdf|text` requires the existing authenticated
tenant-scoped case access and a closed workflow. `report.py` uses retained evidence
and the approved conclusion, not a new analysis or model call. `report_content.py`
projects current-workflow hypotheses, milestones and artifact references without
changing assessments. The report leads with the problem, approved root-cause
conclusion, rationale, confidence and limitations, followed by numbered hypotheses,
supporting evidence, recorded next steps and the closure timeline. Approval is
explicitly distinguished from causal proof. It follows Test Lab A4/slate/teal styling.
Evidence uses real tables with repeated headers, aligned values and explicit units;
wide results are split into linked panels. At most 12 retained rows per analysis are
shown, with explicit sample notices. Long table cells are marked as excerpts; full
values remain in the text export/AAR. Narrative conclusions are not truncated.
The timeline uses the current generation's start, review, root hypothesis runs,
approval and closure; discovery plus confirmation is one milestone. Timezones are
normalized to UTC only when known, and missing dates/durations are not inferred.
Supplementary chat answers and the grouped artifact index are separate appendices.
Artifact IDs and hypothesis identities appear in the index, not in the main
narrative; integrity fingerprints, raw code and internal timing checkpoints remain
in the AAR. Unresolved, failed and legacy records remain explicit.
The dataset assessment report remains separate. Export does not update case state
or create a new AAR artifact; it reflects current-generation retained evidence at
the labelled export time. PDF uses the existing Latin-1-safe font convention;
the UTF-8 text export preserves characters outside that font's coverage.

`ai/sandbox_capabilities.py` owns the versioned built-in/import capability registry.
Code generation receives its public contract; preflight validation and the worker
use the same policy. Python symbol-table analysis rejects unresolved global names
before launching a subprocess while recognizing local arguments, closures, aliases
and comprehension scopes. Class definitions, relative imports and wildcard imports
are rejected explicitly. RCA retains its additional I/O, loop and output restrictions.
Generated execution artifacts retain the contract and runtime outcomes include its
version and cached Python/analytical-library environment metadata. Investigation
and chat execution AAR records retain these details for success, rejection,
failure and timeout. Rejected chat code is retained before validation executes.
Direct dependencies are pinned to the tested baseline; see the deployment guide
for target-platform release-lock requirements.
This check is not definite-assignment analysis, library API validation or
proof of analytical correctness; data-dependent failures can still occur.

Compatibility examples in `tests/unit/rca/test_sandbox_capabilities.py` exercise
dates, grouping, binning, joins, statistics and modelling, plus blocked operations.
No extra model request, dry-run analysis or automatic repair loop is introduced.

From the workspace root:

```powershell
& .\source-codes\.venv\Scripts\python.exe -m pytest source-codes/backend/tests/unit/rca -q
& .\source-codes\.venv\Scripts\python.exe -m pytest source-codes/backend/tests/integration/rca/test_rca.py -q
```

From `source-codes/ui`, the focused browser
flow is `npx playwright test e2e/rca.spec.js --grep "RCA data chat"`.

See [deployment requirements](../../../docs/rca/deployment.md) for runtime, storage, model
configuration and packaging limitations. Automated model responses are mocked; passing tests
does not establish live deployment readiness or an untrusted-code security boundary.

Former `rca`, `ai.rca_helpers`, and `ai.rca_checker` imports are exact compatibility aliases.

### Progress and performance

Initial review, investigation planning/execution, data chat and conclusion approval
retain generation-scoped `operation_progress` AAR checkpoints. The authenticated,
tenant-scoped `GET /api/v3/rca/cases/{case_id}/progress` reads only the latest
checkpoint and opening-result artifact; it does not rerun analysis or load the full
case history. The UI polls sequentially while an action is pending and aborts reads
when its progress view unmounts. A changed generation stops polling and discards
the old progress. Initial Review opens immediately and exposes retained opening
findings while model interpretation is pending.

Timings use a monotonic clock. Total service-operation time includes dataset
loading and model/client preparation. Phase timings are **inclusive** (nested
phases must not be added together); evidence persistence is an overlapping subtotal
and excludes the final checkpoint's own write. HTTP transit/browser rendering are
not included. Sandbox results additionally separate child analysis time from
process overhead (startup/imports, serialization and shutdown—not pure startup).
Report downloads remain read-only: total report time is logged at INFO by
`domains.rca.progress`, not retained as a new evidence artifact. Configure that
logger's INFO output in deployment to collect report timings. Timing does not change hypothesis budgets, chat eligibility,
model choices, sandbox limits or retries. It is measurement, not a speed guarantee.
