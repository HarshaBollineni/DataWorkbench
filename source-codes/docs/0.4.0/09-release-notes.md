# Archimedes 0.4.0 release notes

**Release state:** landed on `dev`  
**Scope:** slice 1 only; the backlog remains explicitly gated by D-22.

## Breaking workflow changes

- The former 14-test Galileo framework is retired and replaced by the seeded
  nine-diagnostic register. The register is the product boundary: it has six
  L1 themes, eleven L2 areas, and exactly nine diagnostics.
- Only diagnostic **#4, Cross-field business rule**, is executable in slice 1.
  The other eight cards are intentionally `workflow_pending`; they are neither
  failures nor hidden functionality.
- The former four-step Test Lab plan/snippet/approval wizard is retired. The
  live Test Lab is register-driven: **Coverage → Scope → Run → Findings**.
  Runs are deterministic and do not expose user-editable execution code.

## Data Sourcing and delivery history

Data Sourcing is now **Drop → Review → Ready**. Dropping the required source
starts parsing and profiling; Review is the editable variable-inventory and
dictionary-mapping surface; Ready is derived rather than clicked.

Replacing an upload never mutates the prior item. It creates a new delivery in
the same `dataset_family_id`, increments `delivery_seq`, accepts optional
`as_of_date`, and sends the new item through its own Drop → Review → Ready
path. The original delivery and its files remain available unchanged.

## Platform and governance changes

- The helper/tool layer was consolidated behind the documented registry and
  logging contract (PLT-08), with dead wizard-era reachability removed.
- Admin factory reset is restored as an audited, admin-only, type-to-confirm
  operation with surgical and full-wipe grades.
- Optional role-mapping verification is implemented as a pre-freeze,
  advisory-only request. It is **off by default**, makes one bounded call only
  when requested, uses `ai.llm` through the `role_mapping_verifier` role in
  `ai.control_plane`, records decisions, and never changes the deterministic
  verdict.
- `spike_recalibrate.py`, `spike_gx.py`, and `spike_integration.py` now use
  deterministic, spike-only `spike_fixtures.py` and exit 0. They are archived under
  `experiments/archive/pre-domain-gx-parity/` and remain
  B2 parity evidence only and do not enable PSI or any other pending diagnostic.

## Controlled expansion (D-22)

Every Section-10/backlog diagnostic is gated. Before any item is started, the
requester must record satisfaction with the prior enabled flow and its D-22
decision, and approve a bounded phase plan with acceptance tests, rollback
consideration, and a next decision point.

For B1 specifically, rerun the candidate-flag negative rendering proof before
enablement: candidate flags must remain review outcomes and never render as
verdict violations (R-08). See `07-decisions.md` for R-08/R-10 controls.

## Migration, rollback, and historical waiver

The retirement migration removes only records keyed to the old framework.
Before any non-empty destructive retirement run it takes a mandatory,
restorable SQLite online-backup snapshot at the configured retirement snapshot
path; a snapshot failure aborts before deletion. This is the rollback point.

The original retirement already ran against an empty baseline. It has a
historical waiver: no retroactive backup is fabricated, and the absence of an
old snapshot is not represented as a successful backup. The mandatory
pre-retirement snapshot control applies to future non-empty retirement runs.

## Validation evidence

The slice-1 traceability matrix (`docs/rca/01-traceability-matrix.md`) points
to the landed backend and Playwright tests. In particular it covers framework
retirement, the nine/one register boundary, Drop → Review → Ready, new-delivery
replacement, reset/helper controls, the deterministic cross-field run, and the
register-driven Test Lab journey.
