# Documentation index

This directory separates current product reference material from historical release records and
one-time inputs. Historical checkboxes and backlog notes describe the release at the time they were
written; they are not the current application backlog.

## Product reference

- [`product/aegis-labs-product-spec.md`](product/aegis-labs-product-spec.md) — handover-oriented
  current product and technical overview for application version 0.5.2.
- [`../source-codes/USER_GUIDE.md`](../source-codes/USER_GUIDE.md) — current user workflow.
- [`../source-codes/TSD.md`](../source-codes/TSD.md) — current technical design and boundaries.
- [`documentation-maintenance.md`](documentation-maintenance.md) — ownership,
  update triggers, and validation policy.

## Release records

### 0.5.0

- [`releases/0.5.0/requirements-0.5.0.md`](releases/0.5.0/requirements-0.5.0.md) — requirements
  authority for the 0.5.0 release.
- [`releases/0.5.0/archimedes-0.5.0-plan.md`](releases/0.5.0/archimedes-0.5.0-plan.md) — detailed
  implementation and validation plan. Retained as release evidence, not an active task tracker.

### 0.4.0

- [`releases/0.4.0/requirements-0.4.0.md`](releases/0.4.0/requirements-0.4.0.md) — consolidated
  requirements for the superseded 0.4.0 release.
- [`releases/0.4.0/archimedes-0.4.0-plan.md`](releases/0.4.0/archimedes-0.4.0-plan.md) — historical
  implementation plan.
- [`releases/0.4.0/archimedes-0.4.0-start-gate.md`](releases/0.4.0/archimedes-0.4.0-start-gate.md) —
  baseline verification captured before implementation.
- [`releases/0.4.0/testlab-redesign-0.4.0.md`](releases/0.4.0/testlab-redesign-0.4.0.md) — adopted Test
  Lab design for that release.
- [`releases/0.4.0/HANDOVER-phase5-kb.md`](releases/0.4.0/HANDOVER-phase5-kb.md) — completed Phase 5
  Knowledge Base handoff. Kept for implementation history.

## Historical inputs

- [`inputs/RCA_CODEBASE_COMPATIBILITY_INPUT.md`](inputs/RCA_CODEBASE_COMPATIBILITY_INPUT.md) — factual
  codebase snapshot taken before the current RCA implementation. It is historical and should not be
  treated as a current compatibility report.
- [`inputs/prompt.md`](inputs/prompt.md) — one-time multi-model orchestration prompt. It is retained
  for provenance and is not an instruction file for current development.

## Historical application documents

- [`history/TSD-pre-0.4.0.md`](history/TSD-pre-0.4.0.md) — retired technical
  design retained for implementation history; it is not current guidance.
- [`releases/0.4.0/TODO-0.4.0.md`](releases/0.4.0/TODO-0.4.0.md) — completed
  0.4.0 checklist and its carried-forward operational note.

## Related directories

- [`../source-codes/docs/README.md`](../source-codes/docs/README.md) — indexed documentation maintained with the application.
- [`../dev-requirements/`](../dev-requirements/) — original requirement inputs and workflow diagrams.
- [`../spec-assets/`](../spec-assets/) — screenshots used by the product specification.

## Validate documentation

From the workspace root, run `./documentation.ps1 check`. To see which
documents may need review for a set of changed paths, run, for example,
`./documentation.ps1 impact source-codes/ui/src/App.jsx`.
