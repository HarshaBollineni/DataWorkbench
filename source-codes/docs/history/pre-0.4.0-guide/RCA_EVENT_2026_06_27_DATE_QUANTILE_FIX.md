# RCA Event: Date Quantile TypeError Fix

Timestamp: 2026-06-27 00:36:42 +05:30 IST

## Isolated Event

Two RCA sessions reproduced the same failure:

- Session `7d0772444aca`, run `78`, test `retail_accounts|ks|["bureau_score"]`, table `retail_accounts`.
- Session `35397dd23e33`, run `75`, test `retail_accounts|psi|["bureau_score"]`, table `retail_accounts`.

Both failed at RCA step `code_output` after approving code that called `df["open_date"].quantile(0.4)` while `open_date` was still object/string typed.

## Root Cause

The RCA agent correctly inferred an early-vs-recent split by `open_date`, but its first code proposal assumed the date column was already datetime typed. Pandas `Series.quantile()` on string/object dates attempted string subtraction during interpolation and raised:

`TypeError: unsupported operand type(s) for -: 'str' and 'str'`

## Fix

- Added `prepare_rca_frame()` in `backend/ai/rca_agent.py`.
- RCA approved-code execution now passes a prepared dataframe where the table date column and date-like object columns are converted to datetime when at least 90% of non-null values parse.
- The RCA sandbox endpoint now uses the same prepared dataframe so SME mitigation/follow-up code gets consistent behavior.
- RCA prompt now reminds the agent to use `pd.to_datetime(..., errors="coerce")` for copied or derived date columns.

## Verification

- `python -m compileall backend/ai/rca_agent.py backend/routers/rca.py backend/ai/prompts.py` passed.
- Original KS RCA step-1 code now runs successfully:
  - `early_count`: `10019`.
  - `recent_count`: `14981`.
  - `early_mean`: `663.1792594071264`.
  - `recent_mean`: `701.9877177758494`.
- Original PSI RCA step-1 code now runs successfully:
  - `mean_early`: `663.1792594071264`.
  - `mean_late`: `701.9877177758494`.
  - `diff`: `38.808458368722995`.
- RCA sandbox endpoint for the same date quantile pattern returned `ok=True`.
- Raw `retail_accounts.open_date` dtype is `object`; prepared RCA dtype is `datetime64[ns]`.

## Workflow Usage and Gap

Current workflow uses RCA outcomes to:

- Persist incremental RCA steps in `rca_steps`.
- Persist RCA result and Noether checker verdict in `rca_sessions`.
- Gate ticket creation through Noether unless the user gives an override reason.
- Create Issue Management tickets with immutable failure context, RCA evidence, remediation path, and linkage to session/run/instance.
- Store ticket updates in context memory.

Current workflow does not yet automatically run targeted follow-up tests after an RCA. Follow-up testing is currently manual: use the RCA sandbox/mitigation code workbench, raise a ticket, update ticket resolution, then rerun validations. A future improvement should add an explicit follow-up test plan action that converts RCA findings into one or more focused validation checks and links their results back to the RCA/ticket.

## Follow-up Fix: Logical DB SQL Qualification

Timestamp: 2026-06-27 00:44:18 +05:30 IST

New RCA sessions showed SQL probes failing with `no such table` when the agent queried logical database-qualified names such as `retail_risk_db.retail_accounts` and `retail_fraud_db.retail_accounts`.

Isolated sessions:

- Session `538478205b9e`, run `81`, test `retail_accounts|psi|["bureau_score"]`.
- Session `9a0cef4048e2`, run `78`, test `retail_accounts|ks|["bureau_score"]`.

Root cause:

- The RCA agent treated logical database labels as SQL schemas.
- The physical warehouse is a single SQLite database with tables such as `retail_accounts`; no SQLite schemas named `retail_risk_db` or `retail_fraud_db` are attached.

Fix:

- Added SQL normalization in `backend/ai/rca_agent.py` so known logical-qualified table references are rewritten to physical table names before read-only SQL execution.
- Updated the RCA prompt to instruct Feynman to query physical table names only.
- Added a step-budget guard: after enough SQL probes, the agent is nudged to declare a root cause or propose one focused approved-code analysis.
- Fixed the terminal step-budget fallback so sessions receive a non-null inconclusive RCA result instead of `result: null`.

Verification:

- `retail_risk_db.retail_accounts` normalizes to `retail_accounts`.
- `retail_fraud_db.retail_accounts` normalizes to `retail_accounts`.
- Quoted `"retail_risk_db"."retail_accounts"` normalizes to `retail_accounts`.
- `SELECT COUNT(*) AS n FROM retail_risk_db.retail_accounts` returned `[{"n":25000}]`.
- A live RCA start for run `78` created session `dcb01a845951`; it used physical `retail_accounts` in SQL probes, returned valid SQL output, and moved to `code_proposal` rather than repeating `no such table`.
