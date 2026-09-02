# T2-D06 row-completeness experiment

| Field | Value |
| --- | --- |
| Status | Experimental; not production code |
| Test family | T2 — KB-driven data validation |
| Diagnostic | D06 — Row-completeness reconciliation |
| MVP | Yes |
| Production dependency | Forbidden |
| Production implementation | `source-codes/backend/dq_diagnostics/` |
| Promotion target | `source-codes/backend/domains/test_lab/diagnostics/t2_d06_row_completeness/` |

This is the former `source-codes/localSandbox/Test2Diag6_RowCompleteness` prototype. It remains
available for investigation and comparison, but the application must not import or deploy it.
`COMPLETENESS_INTEGRATION.md` is the original experimental handoff and may describe an earlier rule
set than the governed production implementation.

## Environment

The scripts currently require packages already present in `source-codes/.venv`. They can use that
interpreter without installing anything into it:

```powershell
cd experiments/test-lab/t2-d06-row-completeness
../../../source-codes/.venv/Scripts/python.exe run_tests.py --help
```

If future work needs different packages, create an experimental environment under
`experiments/.venv` or this folder's `.venv` and declare the additional dependencies separately.

## Local configuration

`agent_config.json` is local-only and ignored because it may contain credentials. Use
`agent_config.example.json` as the safe template. Prefer environment variables supported by
`agent.py`; never place a real key in a tracked file.
