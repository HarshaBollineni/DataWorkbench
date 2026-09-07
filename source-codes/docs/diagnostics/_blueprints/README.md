# Production diagnostic blueprints

This folder contains the canonical lifecycle contract and copy-ready production handoff templates
for Test Lab diagnostics.

- [`diagnostic-lifecycle.md`](diagnostic-lifecycle.md) defines the common experiment-to-operation
  contract.
- [`production-diagnostic/`](production-diagnostic/README.md) defines the deployment file map,
  production contract, implementation gates, acceptance evidence, and promotion record.
- [`test-lab-operational-capability-matrix.md`](test-lab-operational-capability-matrix.md) records
  the shared run/recovery/report baseline and the current nine-diagnostic audit.
- The matching experiment pack is
  [`experiments/test-lab/_blueprints/new-diagnostic/`](../../../../experiments/test-lab/_blueprints/new-diagnostic/README.md).

Completed diagnostic-specific documents belong in `docs/diagnostics/<slug>/`. Do not edit the
canonical templates to record one deployment.

## Worked reference

[Value Semantics](../value-semantics/contract.md) is the first completed reference implementation.
Its [promotion record](../value-semantics/promotion-record.md) documents experiment provenance,
production hardening, AAR/KB integration, acceptance evidence, enablement, and rollback.
