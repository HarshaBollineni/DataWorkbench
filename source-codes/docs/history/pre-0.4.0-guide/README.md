# Pre-0.4.0 guide — historical implementation and testing records

**Status:** Historical. The plans, scripts, generated guide, and completion
notes in this directory describe pre-0.4.0 workflows and are retained for
lineage only. They are not a current testing kit. Use
[`../../../USER_GUIDE.md`](../../../USER_GUIDE.md) for current operation,
[`../../../TSD.md`](../../../TSD.md) for current architecture, and
`../../../ci-local.ps1` for
current verification.

| File | What it is |
| --- | --- |
| [`PLAN8_CONTEXT_MEMORY_ARCHITECTURE.md`](PLAN8_CONTEXT_MEMORY_ARCHITECTURE.md) | Chief-architect plan for object context memory across databases, tables, variables, tests, RCA, and tickets, including demo-delete retention rules and sub-agent prompts. |
| [`PLAN9_RCA_WORKFLOW_REFINEMENT.md`](PLAN9_RCA_WORKFLOW_REFINEMENT.md) | Chief-architect plan to repair incremental RCA, failed-test eligibility, Noether gating, and RCA-to-Issue Management handoff, including sub-agent prompts. |
| [`PLAN8_9_COMPLETION_NOTES.md`](PLAN8_9_COMPLETION_NOTES.md) | Completion notes for the Plan 8 and Plan 9 build, including verification evidence and operational notes. |
| [`Workflows_and_Testing_Guide.md`](Workflows_and_Testing_Guide.md) | Retired end-to-end workflow lineage and former Python testing instructions. |
| [`scripts/run_library_test.py`](scripts/run_library_test.py) | Historical direct-library-test helper; its seeded-test dependencies were retired. |
| [`scripts/recommend_tests.py`](scripts/recommend_tests.py) | Historical recommendation-layer helper. |
| [`scripts/run_rca.py`](scripts/run_rca.py) | Historical headless RCA helper. |

Do not run these scripts against current state without first reviewing and
porting their imports and persistence assumptions. They are intentionally
excluded from the current verification contract.
