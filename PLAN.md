# DataWorkbench Codex CLI Development Plan

**Status:** Active handover plan  
**Prepared:** 07 September 2026  
**Application:** Aegis Labs / DataWorkbench 0.5.2  
**Repository root:** `C:\Src\DataWorkbench`  
**Application root:** `source-codes/`

## 1. Purpose

This is the operating plan for continuing DataWorkbench development through Codex CLI. It turns
the current repository state into bounded tasks that can be delegated without losing product
intent, duplicating work, or allowing parallel agents to edit the same integration seams.

This document is an execution and coordination plan. Product behavior remains governed by the
current product specification, technical design, diagnostic contracts, and approved release
requirements linked below.

## 2. Product outcome

DataWorkbench is a governed data-quality workbench with this user journey:

```text
Data Sourcing
  -> immutable asset/version/snapshot
  -> profiling and dictionary review
  -> Test Lab diagnostic scope and execution
  -> findings and human disposition
  -> Issue Management and RCA
  -> governed reports and analytical artifacts
```

The application must preserve these principles:

- Source snapshots and frozen manifests are immutable.
- Deterministic calculations are authoritative.
- AI may propose semantic or configuration choices only at an explicit, auditable seam.
- Candidate and contextual findings do not become issues without human confirmation.
- The nine-diagnostic framework register remains canonical.
- Production code must not import from `experiments/`.
- Public routes and compatibility exports remain stable unless a requirement explicitly changes
  them.
- Tests use throwaway state and must not mutate `source-codes/backend/.runtime/system_state.db`.

## 3. Sources of truth

Read only the documents needed by a task, in this precedence order:

1. Current user request and accepted decisions.
2. [`documentation/product/aegis-labs-product-spec.md`](documentation/product/aegis-labs-product-spec.md).
3. [`source-codes/USER_GUIDE.md`](source-codes/USER_GUIDE.md) and
   [`source-codes/TSD.md`](source-codes/TSD.md).
4. Current architecture and diagnostic contracts in [`source-codes/docs/`](source-codes/docs/README.md).
5. Approved release requirements in `documentation/releases/`.
6. Historical release plans and inputs, as evidence only.

Important current references:

- [`source-codes/docs/architecture/phase0-baseline.md`](source-codes/docs/architecture/phase0-baseline.md)
- [`source-codes/docs/architecture/phase1a-integration-blueprint.md`](source-codes/docs/architecture/phase1a-integration-blueprint.md)
- [`source-codes/docs/diagnostics/_blueprints/diagnostic-lifecycle.md`](source-codes/docs/diagnostics/_blueprints/diagnostic-lifecycle.md)
- [`source-codes/docs/diagnostics/value-semantics/contract.md`](source-codes/docs/diagnostics/value-semantics/contract.md)
- [`source-codes/backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency/README.md`](source-codes/backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency/README.md)
- [`source-codes/docs/architecture/governed-analytics-artifact-repository.md`](source-codes/docs/architecture/governed-analytics-artifact-repository.md)

If two current sources conflict, stop implementation at that boundary, record the conflict in the
task handoff, and ask for a product decision. Do not silently choose whichever behavior is easier.

## 4. Current-state checkpoint

Repository inspection on 07 September 2026 found:

- branch `main` has only two repository commits;
- the working tree contains a large, mixed set of tracked and untracked changes;
- the largest active themes are T2-D08 Value Semantics, T2-D11 Directionality, shared diagnostic
  run state, AAR binary/Parquet support, sourcing/context behavior, navigation, and Test Lab UI;
- the D08 implementation record says its focused backend, frontend, build, and broader backend
  gates passed, but the implementation has not yet been committed;
- the working tree must therefore be treated as valuable in-progress work, not disposable output;
- there is no repository `AGENTS.md` yet.

The immediate goal is to turn this working state into a reproducible, reviewed checkpoint before
starting new feature development.

## 5. Execution strategy

### Stage 0 - Preserve and understand the current work

**Mode:** Serial. One integration owner. No feature edits.

- [ ] Capture `git status --short`, `git diff --stat`, untracked-file inventory, and the current test
  commands in a dated handoff note.
- [ ] Classify every changed path into: D08, D11, shared runtime/AAR, application shell/navigation,
  documentation, generated evidence, or unrelated.
- [ ] Confirm that secrets, `.env` files, runtime databases, test output, notebook output, and live
  provider responses are not staged.
- [ ] Reconcile documentation claims with the actual code and focused test results.
- [ ] Run the fast non-destructive gates from Section 9.
- [ ] Create a recovery tag or commit only after reviewing the complete staged diff.
- [ ] Do not use reset, clean, checkout, or blanket formatting against the current tree.

**Exit gate:** The present work is recoverable, its provenance is understood, and a new task can
start from a named commit rather than from an anonymous dirty tree.

### Stage 1 - Codex CLI repository bootstrap

**Mode:** Serial integration task.

- [ ] Add a concise root `AGENTS.md` that captures repository boundaries, authority order, test
  commands, safe database handling, documentation impact rules, and the delegation policy in this
  plan.
- [ ] Add narrower `AGENTS.md` files only where backend, UI, experiments, or documentation need
  genuinely different instructions.
- [ ] Establish one branch and one Git worktree per implementation task.
- [ ] Use `codex/<task-id>-<slug>` for task branches and a sibling directory such as
  `..\DataWorkbench-wt-<task-id>` for its worktree.
- [ ] Keep one integration owner on the integration branch. Workers submit commits and a handoff;
  they do not merge their own work.
- [ ] Decide how dependencies are provisioned in worktrees. Prefer a reproducible setup per
  worktree; if a shared environment is used, document its exact path and prohibit dependency
  mutation by workers.
- [ ] Create a task ledger from the template in Section 8.

**Exit gate:** A fresh Codex CLI session can enter any worktree, discover its instructions, run a
focused check, and produce a commit without relying on hidden conversation history.

### Stage 2 - Consolidate the current diagnostic platform

Start these tasks only after Stage 0. Tasks 2A and 2B may run in parallel when their allowed paths
are disjoint. Task 2C is the integration seam and remains serial.

#### Task 2A - D08 Value Semantics acceptance closure

**Primary ownership:**

- `source-codes/backend/domains/test_lab/diagnostics/t2_d08_value_semantics/`
- D08-specific backend tests
- `source-codes/ui/src/features/test-lab/diagnostics/t2-d08-value-semantics/`
- D08-specific UI and Playwright tests
- `source-codes/docs/diagnostics/value-semantics/`
- `experiments/test-lab/t2_d08_Value_semantics/`

**Objectives:**

- [ ] Verify the production package matches its contract and promotion record.
- [ ] Verify General context, human role confirmation, partial `UNSCOPED` coverage, three governed
  tags, no automatic issue creation, reports, and JSON/Parquet artifacts.
- [ ] Confirm experimental notebooks, fixtures, checkpoints, and generated outputs are not runtime
  dependencies.
- [ ] Resolve any mismatch between claimed acceptance counts and repeatable tests.
- [ ] Deliver a clean D08 commit and a short acceptance handoff.

#### Task 2B - D11 Directionality acceptance closure

**Primary ownership:**

- `source-codes/backend/domains/test_lab/diagnostics/t2_d11_directional_monotonic_consistency/`
- D11-specific backend tests
- `source-codes/ui/src/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/`
- D11-specific UI tests

**Objectives:**

- [ ] Verify expected-risk direction remains distinct from observed empirical direction.
- [ ] Verify exact KB reuse, advisory AI suggestions, explicit confirmation, and proposal governance.
- [ ] Verify compatible reruns retain confirmed scope without repeating AI work.
- [ ] Verify overall and optional segment evidence, target orientation, findings, report, and RCA
  handoff.
- [ ] Add or update a D11 phase plan, promotion record, and implementation report if this diagnostic
  is being declared production-complete.
- [ ] Deliver a clean D11 commit and a short acceptance handoff.

#### Task 2C - Shared runtime and integration seams

**Exclusive ownership:**

- `source-codes/backend/main.py`
- `source-codes/backend/system_db.py`
- `source-codes/backend/routers/diagnostics.py`
- `source-codes/backend/routers/sourcing.py`
- `source-codes/backend/routers/v2.py`
- `source-codes/backend/domains/test_lab/shared/`
- `source-codes/backend/domains/aar/`
- `source-codes/backend/knowledge_base/dq_framework_data.json`
- shared UI shell, API transport, navigation, and Test Lab dispatch files

**Objectives:**

- [ ] Integrate D08 and D11 without duplicating run state, execution brokers, result dispatch,
  finding workflows, or AAR writes.
- [ ] Preserve route compatibility and exact artifact identity semantics.
- [ ] Validate tenant isolation, frozen manifests, leases/heartbeats, replay, safe retries, and
  completed-run idempotency.
- [ ] Confirm Parquet media metadata and download behavior remain backward-compatible with JSON
  artifacts.
- [ ] Keep one canonical navigation-memory and authentication-expiry behavior across pages.
- [ ] Run cross-diagnostic regressions for D02, D04, D06, D08, D11, and D14.

**Stage 2 exit gate:** D08 and D11 are either explicitly accepted as executable or left
`workflow_pending` with a documented blocker; shared platform contracts and older diagnostics pass
their regression gates.

### Stage 3 - Product consistency and end-to-end workflow

These tasks can be investigated in parallel. Edits to shared UI shell files are applied by one UI
integration owner.

- [ ] Validate asset selection and context survive navigation among Data Sourcing, Inventory, Test
  Lab, AAR, Issue Management, RCA, Knowledge Base, and DQ Framework.
- [ ] Validate refresh and replacement semantics invalidate or retain downstream state exactly as
  documented.
- [ ] Validate every executable diagnostic supports understand, scope, configure, preview, freeze,
  execute, display, export, persist, act, history, and recovery behavior where applicable.
- [ ] Validate browser disconnect/reconnect, duplicate observers, expired sessions, process restart,
  queued work, and terminal failure behavior.
- [ ] Validate findings use the diagnostic decision type and never infer a violation from metric
  magnitude alone.
- [ ] Add only the smallest E2E scenarios needed to cover cross-boundary behavior that unit and API
  tests cannot prove.

**Exit gate:** A new asset can travel through the complete user journey without losing context,
mutating source evidence, duplicating execution, or bypassing human governance.

### Stage 4 - Documentation and release checkpoint

**Mode:** Documentation work can proceed in parallel with final test triage, followed by one serial
consistency review.

- [ ] Update `source-codes/USER_GUIDE.md` for user-visible behavior.
- [ ] Update `source-codes/TSD.md` for architecture, persistence, API, AI, or deployment changes.
- [ ] Update the application and workspace documentation indexes.
- [ ] Add implementation reports and promotion records for newly accepted diagnostics.
- [ ] Run documentation impact and consistency checks.
- [ ] Decide whether the completed change is patch, minor, or major under
  `source-codes/docs/development/versioning.md`.
- [ ] Update every version surface together.
- [ ] Run the full release gate once on the integrated branch.
- [ ] Review the final diff, known limitations, rollback instructions, and deployment impact before
  merge or deployment.

## 6. Parallelization map

| Lane | Good parallel work | Must remain serial |
|---|---|---|
| Product/contracts | Read-only contract audit, traceability, gap analysis | Resolving conflicting requirements |
| Backend diagnostics | One isolated diagnostic package and its focused tests | Shared routers, DB schema, register, run broker |
| Frontend diagnostics | One diagnostic's scope/results components and tests | `App.jsx`, Test Lab dispatch, shared navigation/API transport |
| Experiments | Fixtures, benchmark analysis, notebook evidence | Promotion into production and KB activation |
| Quality | Focused tests, test-gap review, documentation impact review | Full CI against the integration candidate |
| Documentation | Diagnostic-local records | Product version, TSD, indexes, release declaration |

Use at most three implementation workers plus one integration owner. Add workers only when each
has a distinct output and file boundary. Parallel agents must not share a writable worktree.

## 7. File ownership and integration rules

1. Every task declares allowed paths, prohibited shared paths, dependencies, acceptance commands,
   and expected deliverables before coding begins.
2. A worker edits only its allowed paths. Necessary changes outside them are reported in the
   handoff for the integration owner.
3. Shared seams have one owner at a time. Common hotspots include `main.py`, `system_db.py`, v2
   routers, diagnostic dispatch/readiness, framework JSON, `App.jsx`, API transport, navigation,
   and Test Lab shared components.
4. Workers do not clean, reset, reformat, or rewrite unrelated changes.
5. Each worker commits an internally coherent change with focused tests passing.
6. The integration owner reviews and cherry-picks commits in dependency order, resolves conflicts,
   runs cross-cutting tests, and records any contract decision.
7. Do not combine feature implementation, broad cleanup, dependency upgrades, and formatting in
   one task.

Recommended integration order:

```text
contracts and additive persistence
  -> domain engine/resources
  -> manifests and runners
  -> routers and dispatch
  -> diagnostic UI
  -> shared shell and navigation
  -> E2E tests
  -> documentation and version
```

## 8. Codex task packet

Create one packet per worker using this shape:

```markdown
# TASK <ID> - <title>

## Goal
One observable outcome.

## Read first
Only the relevant contract and implementation files.

## Allowed paths
Exact directories/files the worker may edit.

## Do not edit
Shared seams and unrelated dirty paths.

## Requirements
- Required behavior and invariants.
- Compatibility requirements.
- Explicit non-goals.

## Acceptance
- Focused tests to run.
- Expected result or observable behavior.
- Documentation impact.

## Handoff
- Summary and rationale.
- Files changed.
- Commands and results.
- Assumptions, unresolved risks, and follow-up outside allowed paths.
- Commit hash.
```

Suggested prompt for a worker:

```text
Read AGENTS.md and this task packet completely. Inspect the current implementation before editing.
Work only within Allowed paths. Preserve public contracts and unrelated changes. Implement the
smallest complete solution, run the listed focused checks, commit it, and return the required
handoff. If a requirement conflicts with current authority or needs a shared-seam edit, stop at
that boundary and report concrete evidence to the integration owner.
```

Suggested prompt for the integration owner:

```text
Review this worker commit against its task packet and current product contracts. Inspect the diff,
verify that it stayed inside ownership boundaries, run the relevant cross-cutting tests, and either
integrate it or return a bounded correction request. Do not rewrite a valid worker change merely
for style.
```

## 9. Verification ladder

Run the narrowest meaningful check during implementation. Broaden checks at integration gates.

### Documentation

From the repository root:

```powershell
./documentation.ps1 impact <changed-paths>
./documentation.ps1 check
```

### Focused backend

From `source-codes/` using the project virtual environment and a throwaway
`SYSTEM_DB_PATH`:

```powershell
./.venv/Scripts/python.exe -m compileall -q backend
./.venv/Scripts/python.exe -m pytest backend/tests/<focused-path> -q
```

### Focused frontend

From `source-codes/ui/`:

```powershell
npm run test:unit
npm run lint
npm run check:reachability
npm run build
```

Use a narrower `node --test <file>` command inside a worker task when the full unit suite is not
needed.

### Integrated application gate

From `source-codes/`:

```powershell
./ci-local.ps1 -StrictLint
```

During intermediate integration, `-SkipE2E` is acceptable when browser behavior is unaffected.
Before a release checkpoint, run the complete gate without `-SkipE2E`.

### Final diff checks

```powershell
git diff --check
git status --short
git diff --stat
```

Test results are evidence only when the command, working directory, environment assumptions, and
result are included in the handoff.

## 10. Token-use policy

- Give each worker a narrow task packet instead of the complete project history.
- Point workers to specific source-of-truth files; do not paste large historical plans into every
  prompt.
- Use inexpensive exploration for inventories and test-gap discovery; reserve deeper reasoning for
  architecture, contract conflicts, concurrency, migrations, and integration review.
- Reuse committed handoffs and implementation reports as context between sessions.
- Do not ask several agents to solve the same implementation unless explicitly running a design or
  review comparison.
- Prefer focused tests in worker loops and one full suite at an integration gate.
- Keep generated output out of prompts unless it contains failure evidence needed for diagnosis.

## 11. Definition of done

A task is done only when:

- the observable goal is complete;
- relevant contracts and compatibility boundaries are preserved;
- focused tests pass and results are recorded;
- no unrelated files were changed;
- product/TSD/documentation impact was addressed or explicitly marked none;
- the change is committed on its task branch;
- the handoff identifies assumptions, remaining risks, and integration needs.

A release checkpoint is done only when:

- all accepted worker commits are integrated and reviewed;
- all blocking local CI and documentation gates pass;
- runtime data and secrets are absent from the diff;
- the current version and documentation agree with executable behavior;
- rollback is documented for every newly enabled diagnostic or persistence change;
- the final diff is understandable as a sequence of bounded product decisions.

## 12. Immediate next actions

Execute these in order:

1. Complete Stage 0 and preserve the current dirty working tree.
2. Separate the present change inventory into D08, D11, shared integration, UI shell, and docs.
3. Repeat the documented focused acceptance gates and reconcile any failures.
4. Commit the recoverable baseline.
5. Add the repository `AGENTS.md` and task ledger.
6. Create isolated D08 and D11 acceptance-closure worktrees.
7. Integrate shared seams serially, then run the cross-diagnostic and full application gates.
8. Update current documentation and cut the next version checkpoint only after the integrated
   behavior is proven.

