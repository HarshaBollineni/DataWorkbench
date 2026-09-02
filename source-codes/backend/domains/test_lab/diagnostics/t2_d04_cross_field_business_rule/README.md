# T2-D04 Cross-field Business Rule

This package owns Diagnostic 4: governed knowledge-rule binding, deterministic role resolution,
the pure cross-field rule engine, manifest construction, execution, structured results, and report
rendering. Production callers use this package directly.

The former `dq_diagnostics.manifest`, `dq_diagnostics.runner_cross_field`, and
`dq_diagnostics.engines.cross_field` paths remain exact compatibility aliases. Generic diagnostic
run status and append-only decision access live in `domains/test_lab/shared/run_state.py` because
T1-D02, T2-D06, and T4-D14 consume the same persistence contract.

The binder's cross-field proposal workflow belongs here. Its governed registry-binding entrypoint
is also reused by T2-D06 until that small persistence seam is extracted independently.
