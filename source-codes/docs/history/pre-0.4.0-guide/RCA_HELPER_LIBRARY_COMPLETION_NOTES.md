# RCA Helper Library Completion Notes

Timestamp: 2026-06-27 01:16:13 +05:30 IST

## Completed

- Added governed RCA Helper Registry in `backend/ai/rca_helpers.py`.
- Added vetted helpers for:
  - time-window drift
  - PSI/KS decomposition
  - segment attribution
  - missingness analysis
  - outlier profile
  - relationship drift
  - join/reference integrity
  - duplicate/key integrity
  - freshness/gap checks
  - target leakage checks
  - cross-table reconciliation profile
  - schema/type anomaly checks
- Added generated-helper quarantine validation:
  - validates required spec keys
  - requires at least 3 parameter examples
  - parses Python syntax
  - rejects blocked imports/names
  - executes the candidate against the first 3 parameter examples on a sample dataframe
  - verifies required helper output keys for each example
  - never promotes automatically
- Extended Feynman RCA tools:
  - `list_rca_helpers`
  - `run_rca_helper`
  - `propose_generated_helper`
- RCA loop now handles multiple tool calls in one model message.
- Feynman now uses the locked `RCA_SYSTEM` contract plus editable `feynman.md` overlay.
- Added Agentic Skills architecture map:
  - `.py` nodes are locked executors/orchestrators
  - `.md` nodes are editable prompts/policies
  - exposed by `GET /api/skills/architecture/map`
  - rendered in `dq-studio/src/pages/AgenticSkills.jsx`

## Evidence

- Backend compile passed:
  - `python -m compileall backend/ai/rca_helpers.py backend/ai/rca_agent.py backend/ai/prompts.py backend/ai/skills.py backend/routers/skills.py backend/routers/rca.py`
- Frontend build passed:
  - `npm run build`
- Helper registry test on `retail_accounts.bureau_score`:
  - `time_window_drift`: mean increased from `663.1792594071264` to `701.9877177758494`; delta `38.808458368722995`.
  - `psi_ks_decomposition`: PSI `0.283590987776543`; KS gap `0.2160293679776264`.
  - `segment_attribution`: top regional deltas around `38.7` to `39.2`.
  - `schema_type_anomaly`: prepared `open_date` dtype `datetime64[ns]`.
- Generated-helper quarantine:
  - valid candidate returned `status=valid`, `promoted=false`.
  - invalid candidate rejected for insufficient parameter examples, missing schemas, blocked import `os`, and blocked name `open`.
  - invalid runtime candidate rejected when parameter-example executions returned incomplete output schemas.
- Architecture map:
  - 16 nodes, 14 edges.
  - 7 locked `.py` nodes.
  - 9 editable `.md` nodes.
- Live RCA smoke test:
  - session `3d8f133e9ccf`
  - steps: `helper_list`, `rca_helper`, `rca_helper`, `root_cause`
  - Feynman used `time_window_drift` and `psi_ks_decomposition`
  - RCA evidence cited `rca_helper`
  - Noether returned `revise`, correctly asking for more evidence before ticket readiness.

## Notes

- Helpers are deterministic Python functions, not unrestricted bash.
- Generated helpers remain quarantined and are not written to the reusable registry.
- Follow-up tests are still recommended in helper output, not automatically created as test-plan items.
