# Application documentation

This directory contains documentation maintained with the application source.
Current user and technical entry points remain at
[`../USER_GUIDE.md`](../USER_GUIDE.md) and [`../TSD.md`](../TSD.md).

## Architecture and implemented designs

- [`architecture/phase0-baseline.md`](architecture/phase0-baseline.md) — active-code and persistence baseline.
- [`architecture/phase1a-integration-blueprint.md`](architecture/phase1a-integration-blueprint.md) — analytics integration placement and contracts.
- [`architecture/phase1b-dictionary-integration.md`](architecture/phase1b-dictionary-integration.md) — dictionary integration record.
- [`architecture/phase1b-feature-target-separation.md`](architecture/phase1b-feature-target-separation.md) — feature/target separation record.
- [`architecture/phase1b-missingness.md`](architecture/phase1b-missingness.md) — missingness investigation design.
- [`architecture/governed-analytics-artifact-repository.md`](architecture/governed-analytics-artifact-repository.md) — reusable analysis-artifact design.
- [`architecture/data-sourcing-profiling-aar.md`](architecture/data-sourcing-profiling-aar.md) — end-to-end sourcing, confirmed-special-value profiling, Mermaid flows, and hybrid AAR storage.

## Diagnostic implementation records

The canonical diagnostic identifiers, names, MVP catalog, and placement rules are recorded in
[`architecture/domain-folder-and-naming-convention.md`](architecture/domain-folder-and-naming-convention.md).

- [`diagnostics/row-completeness/contract.md`](diagnostics/row-completeness/contract.md) — Diagnostic #6 observed-span continuity contract, rule semantics, governed configuration, and artifact integration.
- [`diagnostics/psi/phase-plan.md`](diagnostics/psi/phase-plan.md) — bounded D-22 PSI plan and acceptance gates.
- [`diagnostics/psi/implementation-report.md`](diagnostics/psi/implementation-report.md) — PSI implementation and verification evidence.

## Release and RCA records

- [`0.5.0/`](0.5.0/) — 0.5.0 implementation contracts and release evidence.
- [`0.4.0/`](0.4.0/) — 0.4.0 framework-transition contracts and decisions.
- [`rca/`](rca/) — RCA contracts, traceability, and rollout records.

These are evidence for their named release or workstream. Do not treat completed
checklists as the current backlog.

## Development references

- [`development/git-guide.md`](development/git-guide.md) — branch, merge, and release workflow.
- [`development/versioning.md`](development/versioning.md) — semantic-version policy and version update procedure.

Commands in these guides assume the working directory is `source-codes/`.

## History

- [`history/demo-db-lineage.md`](history/demo-db-lineage.md) — retired demo-database lineage.
- [`history/strategy-docs-lineage.md`](history/strategy-docs-lineage.md) — retired strategy-document lineage.
- [`history/pre-0.4.0-guide/`](history/pre-0.4.0-guide/) — historical plans, generated guide, and former standalone testing scripts.

Historical documents can mention their original paths. Their archive status is
part of the record; current behavior belongs in the user guide or TSD.
