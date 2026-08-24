# Multivariate Recommendation Completion Notes

Timestamp: 2026-06-26 23:39:05 +05:30 IST

## Scope Completed

- Exposed multivariate tests in the Define Test Plan library by removing the UI filter that hid `input_spec.n_columns > 1` tests.
- Added AI recommendation modes:
  - `pairs`: deterministic discovery of eligible multivariate field pairs.
  - `deep_pairs`: Hypatia ranks discovered multivariate candidates.
  - `generate_pairs`: Gauss can generate a new multivariate test from the top pair candidate.
- Tightened pair discovery:
  - Excludes ID/key-like columns from multivariate candidates.
  - Prioritizes credit-risk relationships such as default/leverage, default/interest coverage, rating/leverage, and rating/interest coverage.
- Fixed a swallowed Plan 8 context-memory write defect in `backend/routers/test_plan.py`.
- Confirmed backend LLM loading reads Azure OpenAI variables from `backend/.env`.

## Evidence

- Environment load:
  - `AZURE_OPENAI_ENDPOINT`: present.
  - `AZURE_OPENAI_API_KEY`: present, masked.
  - `AZURE_OPENAI_DEPLOYMENT`: `gpt-4.1`.
  - `AZURE_OPENAI_API_VERSION`: `2025-01-01-preview`.
  - Client type: `AzureOpenAI`.
- Backend compile:
  - `python -m compileall backend` passed.
  - `python -m compileall backend/ai/agents/test_screening.py backend/routers/test_plan.py` passed after final ranking changes.
- Frontend build:
  - `npm run build` in `dq-studio` passed.
- Deterministic multivariate pair discovery for `obligors`:
  - `corr_stability`: `["leverage", "default_flag"]`.
  - `corr_stability`: `["interest_coverage", "default_flag"]`.
  - `corr_stability`: `["rating_num", "leverage"]`.
  - `monotonicity`: `["leverage", "default_flag"]`.
  - `corr_stability`: `["rating_num", "interest_coverage"]`.
  - `monotonicity`: `["interest_coverage", "default_flag"]`.
- Live Hypatia `deep_pairs` result:
  - Returned 6 recommendations.
  - Reason: correlation stability and monotonicity on leverage, interest coverage, revenue, rating, and default relationships best capture economically meaningful multivariate risk-driver monitoring.
- Executed multivariate library test:
  - Historical command path is now `python docs\history\pre-0.4.0-guide\scripts\run_library_test.py --test corr_stability --table obligors --columns rating_num,leverage`.
  - Result: PASS.
  - Metric: `0.007966`.
  - Threshold: `0.2`.
  - Detail: `corr(rating_num,leverage) early vs late`.
  - Rows: `3000`.
- Live Gauss `generate_pairs` route:
  - Returned 1 generated multivariate test.
  - `test_id`: `generated_corr_stability`.
  - Fields from the then-current top pair: `["rating_num", "revenue"]`.
  - Included executable `python_code`.
  - After evidence run, candidate ordering was tightened so future Gauss handoff starts from stronger credit-risk pairs.
- Live Noether RCA gate:
  - Verdict: `revise`.
  - `approved`: `false`.
  - `ticket_ready`: `false`.
  - Noether correctly rejected a plausible but under-evidenced RCA because it lacked quantitative drift evidence, upstream proof, alternative-cause analysis, and concrete remediation acceptance criteria.

## Prompt vs Code Conclusion

Prompt changes alone were not sufficient. Coding changes were required because the frontend hid multivariate library rows, the backend had no pair-discovery mode, Hypatia only ranked tests for already-selected fields, and the Gauss handoff route did not exist for no-library-match multivariate generation.
