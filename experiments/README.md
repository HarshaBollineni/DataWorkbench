# Experiments

This directory contains non-production investigations and prototypes. Production application code
lives under [`../source-codes/`](../source-codes/).

## Rules

- Production code must not import modules from this directory.
- Experimental code is never included in deployment packages.
- Keep a README with each experiment's status, diagnostic identity, inputs, and promotion target.
- Commit reusable source and small synthetic fixtures when useful.
- Do not commit credentials, private datasets, generated outputs, caches, or virtual environments.
- Promote accepted behavior by implementing it under `source-codes`, with production contracts and
  verification; do not make production depend on the prototype.

The governing naming and placement decision is
[`../source-codes/docs/architecture/domain-folder-and-naming-convention.md`](../source-codes/docs/architecture/domain-folder-and-naming-convention.md).

New Test Lab diagnostics must begin with the copy-ready
[`test-lab/_blueprints/new-diagnostic/`](test-lab/_blueprints/new-diagnostic/README.md) experiment
pack and complete its promotion-readiness record before production handoff.
