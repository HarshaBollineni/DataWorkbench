# Test Lab experiment blueprints

This folder is the canonical starting point for a new diagnostic experiment. It is deliberately
inside `experiments/test-lab` so intake, analytical evidence, process records, and promotion
readiness are created with the experiment rather than reconstructed later.

Use [`new-diagnostic/`](new-diagnostic/README.md) for every new Test Lab diagnostic. The matching
production handoff pack is maintained in
[`source-codes/docs/diagnostics/_blueprints/production-diagnostic/`](../../../source-codes/docs/diagnostics/_blueprints/production-diagnostic/README.md).

## Governing principles

- An experiment proves the method; production reimplements the accepted contract.
- Production code must never import from `experiments/`.
- Use synthetic or approved de-identified fixtures only.
- Keep generated output, secrets, caches, checkpoints, and live provider responses out of source control.
- A notebook is supporting evidence, not the deployable interface.
- Promotion starts only after `promotion/readiness.yaml` records every blocking gate as passed.

## Stable naming

Use `t<test>_d<two-digit-diagnostic>_<snake_case_slug>` for experiment and backend package names.
Use `t<test>-d<two-digit-diagnostic>-<kebab-case-slug>` for the frontend feature directory.

Example: `t2_d08_value_semantics` and `t2-d08-value-semantics`.

## Worked reference

The completed [Value Semantics experiment](../t2_d08_Value_semantics/README.md) and its
[production promotion record](../../../source-codes/docs/diagnostics/value-semantics/promotion-record.md)
show how experimental evidence is hardened into the deployed Test Lab workflow.
