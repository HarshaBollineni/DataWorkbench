# Stage 0 checkpoint — 2026-09-07

## Repository state

- Root: `C:\Src\DataWorkbench`; branch: `main` tracking `origin/main`.
- HEAD: `f49d52fda966112c81c43c9fce344b5264159637`.
- Pre-note inventory: 64 tracked modifications/deletions (2,955 insertions, 1,221 deletions) and 115 untracked paths. No baseline commit or tag was made. This note is the only Stage 0 write.

## Next-session resume — 2026-09-08

The primary goal is to complete Diagnostic 8 (Value Semantics) and improve the Knowledge Base workflow; WFCTX-01 remains parked for later. **First morning action:** perform a read-only map of D08 and Knowledge Base changed files, contract/acceptance gaps, and dependencies on shared AAR, run-state, and router seams; then propose a bounded task sequence. Do not modify application/code without first informing the user and receiving approval.

Use `gpt-5.6-luna` for reading/survey, `gpt-5.6-terra` for writing, and `gpt-5.6-sol` for edge cases/architecture; keep handoffs narrow and token-sensitive. Current evidence: documentation and diff checks pass; focused D08 backend is 32 passing tests; frontend unit is 66 passing tests; lint and build pass; reachability remains red and is parked; no changes are staged and no commit exists. Notebook outputs require deliberate disposition. The provider-indicator scan found zero hits.

## Changed and untracked inventory

The following directory groups cover every pre-note path in `git status --short`; `/**` means every changed or untracked descendant of that directory, not every repository file.

| Category | Paths |
| --- | --- |
| Plan and presentation evidence | `PLAN.md`; `documentation/Presentations/**` |
| Experiment blueprints | `experiments/README.md`; `experiments/test-lab/_blueprints/**` |
| D08 experiment | `experiments/test-lab/t2_d08_Value_semantics/**` (including deletion of `functions/.gitkeep` and `kb/valuesemantics_kb_combined.json`, modification of `kb/credit_risk_abbreviations_v0_2.yaml`, and all untracked descendants) |
| D08 production | `source-codes/backend/ai/agents/value_semantics_role_adjudication_v0_2.txt`; `source-codes/backend/domains/test_lab/diagnostics/t2_d08_value_semantics/**`; `source-codes/backend/knowledge_base/{credit_risk_abbreviations_v0_3.yaml,value_semantics_kb_v0_1.yaml,value_semantics_kb_v0_2.yaml}`; `source-codes/backend/tests/{integration/test_lab/diagnostics/t2_d08_value_semantics/**,unit/test_lab/diagnostics/t2_d08_value_semantics/**}`; `source-codes/ui/{e2e/fixtures/value-semantics-harness.jsx,e2e/testlab-layout.spec.js,e2e/value-semantics-decisions.spec.js,playwright.value-semantics.config.js,src/features/test-lab/diagnostics/t2-d08-value-semantics/**,tests/unit/test-lab/diagnostics/t2-d08-value-semantics/**}`; `source-codes/docs/diagnostics/value-semantics/**` |
| D11 | `source-codes/backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency/{README.md,knowledge.py,manifest.py,runner.py}`; `source-codes/backend/tests/integration/test_lab/diagnostics/t2_d11_directional_monotonic_consistency/test_directionality_integration.py`; `source-codes/ui/src/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/**`; `source-codes/ui/tests/unit/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/directionalityWorkflow.test.js` |
| Shared backend/runtime/AAR and adjacent diagnostics | `source-codes/backend/{ai/v2/service.py,analysis_runtime/contracts.py,assets/reads.py,assets/service.py,domains/aar/**,domains/test_lab/diagnostics/t2_d06_row_completeness/models.py,domains/test_lab/diagnostics/t4_d14_population_stability/manifest.py,domains/test_lab/shared/**,dq_diagnostics/{dispatch.py,inference_audit.py,readiness.py},knowledge_base/dq_framework_data.json,main.py,requirements.txt,routers/{analyses.py,diagnostics.py,sourcing.py,v2.py},seeds/__init__.py,system_db.py,tests/{integration/test_lab/diagnostics/t2_d04_cross_field_business_rule/test_testlab_diagnostics.py,test_finalized_framework.py,test_register.py,test_router_organization.py,test_s4a_upload.py}}` |
| UI shell, sourcing, AAR/RCA, D02, and shared Test Lab | `source-codes/ui/{e2e/sourcing-context.spec.js,src/App.jsx,src/api/**,src/components/{AssetPicker.jsx,Sidebar.jsx},src/context/AuthContext.jsx,src/features/aar/**,src/features/rca/IssueRca.jsx,src/features/test-lab/diagnostics/t1-d02-feature-target-separation/FeatureTargetResults.jsx,src/lib/navigationMemory.js,src/pages/{DQFramework.jsx,DataSourcing.jsx,Inventory.jsx,IssueManagement.jsx,KnowledgeBase.jsx,Login.jsx,TestLab.jsx},src/pages/testlab/**,tests/navigationMemory.test.js}` |
| Documentation blueprints/indexes | `source-codes/docs/{0.4.0/00-framework.md,README.md,diagnostics/_blueprints/**,diagnostics/diagnostic-lifecycle-blueprint.md}` |

## Sensitive/generated-state audit

Filename audit found the tracked `backend/.env.example` only among environment-like files; no untracked names matching env, database/SQLite, PEM/key/secret/credential, `test-results`, or `playwright-report` were found. A content scan of all changed/untracked text files for private-key headers, OpenAI/AWS/GitHub/Google key formats, and bearer JWTs found 0 hits. The inventory contains generated-looking presentation files and D08 notebooks/fixtures, but no runtime-state filenames identified by that audit. The content scan does not establish absence of live provider responses or other sensitive material; that review remains before any staging or commit.

Both untracked D08 notebooks have saved execution evidence: `value_semantics_action_focused_v0_2.ipynb` has 23 outputs/10 executed cells and `value_semantics_end_to_end_v0_1.ipynb` has 37 outputs/10 executed cells. They are not staged and must be deliberately excluded from a recovery commit or reviewed as approved experiment evidence.

## Verification recorded

| Command / scope | Result |
| --- | --- |
| `./documentation.ps1 check` | PASS |
| `git diff --check` | PASS |
| `./.venv/Scripts/python.exe -m compileall -q backend` | PASS |
| Focused D08 backend unit + integration plus shared run-state tests, with unique `SYSTEM_DB_PATH` and `--basetemp` under `source-codes/.runtime` | PASS — 32 tests in 65.20s. First sandbox attempt was blocked by temporary-directory permissions; this run was outside the sandbox. |
| `npm run test:unit` | PASS — 66 tests |
| `npm run lint` | PASS; continued to reachability |
| `npm run check:reachability` | FAIL — 2 unreachable files: `src/components/WorkflowContextBar.jsx`, `src/lib/workflowContext.jsx`; 11 uncalled exports: `client.js` (`promotePsiResultV2`, `promoteFeatureTargetResultV2`, `downloadDiagnosticKbTemplateV3`, `uploadDiagnosticKbPackageV3`, `proposeRcaFix`, `approveRcaFix`, `confirmRcaFixApplied`, `closeRcaCase`, `reviveRcaSuspect`, `startRcaSecondChance`) and `navigationMemory.js` (`NAVIGATION_SECTIONS`). |
| `npm run build` | PASS |

## Documentation reconciliation and blockers

The historical D08 implementation claim implicitly says frontend reachability/all frontend gates passed. Current evidence contradicts that: reachability fails, and the current unit count is 66 rather than the documented 56. No claim was updated in this checkpoint. The blocking Stage 0 items are the failed reachability gate, pending live-provider-response audit, complete provenance review, deliberate notebook disposition, and review of the whole dirty diff before a recovery baseline can be created.

## Parked follow-up

**WFCTX-01 — workflow-context disposition.** A product acceptance/design decision is required between persistent workflow context and direct Data Sourcing-to-Test-Lab handoff. `workflowContext.jsx` and `WorkflowContextBar.jsx` are implemented but unreachable; do not wire, delete, or add reachability exemptions yet. The related reachability findings remain acknowledged and the gate remains red until a disposition is made. Revisit before claiming acceptance for SRC-08 (selecting an asset sets workflow context), SRC-09 (visible/changeable context), SRC-10 (Test Lab handoff), or SRC-11 (distinct left-navigation entry). This parked item must not silently block unrelated read-only planning.

## Next action

Perform the live-provider-response audit, deliberately disposition the saved notebook outputs, and triage/reconcile the reachability failure and D08 evidence; then review the complete diff and create a named recovery commit or tag. Do not reset, clean, or otherwise discard this working tree.
