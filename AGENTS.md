DataWorkbench Agent Instructions
Mission
Deliver the smallest correct, verified change that satisfies the requested behavior.
Balance correctness, reliability, execution speed, and context efficiency. Prefer simple, local solutions over unnecessary redesign.
Architecture
DataWorkbench is a modular full-stack monolith consisting of:
•	React 19 / Vite feature-oriented SPA.
•	Versioned FastAPI REST APIs.
•	Python router-to-domain layering.
•	SQLite persistence.
•	Immutable dataset snapshots.
•	Durable local file storage.
•	Azure OpenAI integration.
Core backend domains include:
•	ingestion;
•	diagnostics;
•	RCA;
•	artifact governance.
Working Principles
•	Read relevant code before editing.
•	Follow existing repository patterns, naming, structure, and conventions.
•	Prefer the smallest cohesive change that addresses the root cause.
•	Reuse existing helpers and abstractions before introducing new ones.
•	Preserve existing behavior and contracts unless the task explicitly changes them.
•	Do not perform speculative cleanup, unrelated refactoring, dependency upgrades, or architecture expansion.
•	Do not silently expand scope or acceptance criteria.
•	Never revert, overwrite, or discard unrelated user changes.
•	Avoid broad exception handling, silent failures, and unsupported assumptions.
•	Inspect freely when necessary, but retain only context relevant to the current task.
Architectural Boundaries
Treat these as distinct boundaries:
•	React/UI;
•	FastAPI/API;
•	domain logic;
•	SQLite/persistence;
•	dataset snapshots;
•	durable file storage;
•	Azure OpenAI integration.
Stay within the relevant subsystem unless requested behavior crosses a real contract boundary.
When crossing boundaries, verify the affected contract end-to-end.
Preserve especially:
•	versioned API contracts;
•	router-to-domain separation;
•	SQLite persistence semantics;
•	dataset snapshot immutability;
•	durable storage behavior;
•	serialization and lifecycle behavior.
Planning and Execution
For non-trivial, ambiguous, cross-subsystem, or architectural work:
1.	Inspect enough repository evidence to understand current behavior.
2.	Establish the implementation boundary and validation seam.
3.	Produce a bounded plan identifying relevant files/symbols and acceptance criteria.
4.	Wait for plan agreement before modifying code.
5.	Execute the agreed plan through implementation and validation.
Skip formal planning for trivial, well-scoped changes where the implementation path is already clear.
An approved plan is guidance, not permission for silent scope expansion.
If implementation reveals materially different requirements, architecture, dependencies, risks, or unsupported assumptions, pause and surface the decision rather than expanding scope silently.
Behavioral Roles
These are behaviors for the current agent, not separate agents or models.
Discovery
Use bounded, read-only exploration when:
•	ownership is unclear;
•	root cause is unclear;
•	dependency flow or blast radius is unclear;
•	relevant files or symbols cannot be located confidently;
•	validation or test seams are unclear.
Prefer targeted and parallel reads when independent.
Stop discovery once the implementation boundary and validation approach are known.
Return verified conclusions using paths and symbols rather than exploration history or raw output.
Implementation
This is the default execution role.
•	Revalidate only facts relevant to the current change.
•	Modify only necessary files.
•	Preserve established conventions and behavior unless explicitly changed.
•	Address the root cause rather than only symptoms.
•	Add or update the smallest tests proving the requested behavior.
•	Review the final diff for unintended changes.
•	Complete implementation and validation before stopping when feasible.
Escalation
Pause implementation when proceeding requires a material unsupported assumption involving:
•	architecture;
•	public or versioned API contracts;
•	migrations;
•	snapshot immutability;
•	security;
•	data integrity;
•	concurrency;
•	compatibility;
•	durable storage semantics;
•	significant cross-subsystem behavior.
Present:
•	the uncertainty and why it matters;
•	bounded options with concise trade-offs;
•	the recommended option;
•	material risks.
Do not redesign merely because escalation occurred.
Model Escalation
Do not recommend model switching routinely.
If the current task materially exceeds normal implementation complexity, briefly recommend that the user consider switching model tier before continuing.
Typical escalation signals include:
•	difficult architecture or cross-subsystem decisions;
•	security- or concurrency-sensitive changes;
•	migrations or data-integrity decisions;
•	ambiguous public or API contracts;
•	repeated failure to establish or fix the root cause;
•	unusually long-horizon autonomous work requiring sustained judgment.
Do not switch models automatically.
Model selection and reasoning configuration belong to the Codex runtime configuration, not this repository policy.
Validation
Run the narrowest meaningful validation first.
Use applicable:
•	unit tests;
•	component tests;
•	contract tests;
•	formatter/linter checks;
•	type checks;
•	build/compile checks.
Add targeted integration validation when changes cross:
•	API boundaries;
•	persistence;
•	storage;
•	serialization;
•	lifecycle behavior;
•	security boundaries;
•	concurrency boundaries.
Run broader regression or E2E suites only when:
•	repository requirements require them;
•	the change is cross-cutting or high-risk;
•	focused failures indicate credible wider regression risk;
•	a release or final gate requires them.
•	run focused Playwright tests that are directly applicable and only run full Playwright suite unless those focused tests expose a broader defect.
Do not run unrelated suites merely because they exist.
User-Observed Defect Reproduction
For defects reported from an existing UI or runtime state:
1. Establish the exact reported behavior before editing using all available evidence, such as:
   - screenshots or recordings, when provided;
   - the user's description and reproduction steps;
   - visible error messages;
   - browser console or network results;
   - API responses;
   - persisted database or staged-storage state;
   - application logs.
2. If no screenshot is available, reproduce or characterize the issue from the user's description and inspect the relevant runtime payload and state.
3. Do not replace missing evidence with an unverified happy-path assumption. State material assumptions when exact reproduction is not possible.
4. Trace the complete affected path when behavior crosses UI, API, persistence, staged storage, or serialization boundaries.
5. Add a regression test representing the reported payload, state transition, or closest verified failing condition.
6. For visible UI behavior, verify the rendered component or focused browser flow when feasible. Helper unit tests, lint, and builds alone are insufficient.
7. Do not report the defect as fixed until the original condition, or the closest evidence-backed reproduction, passes.
8. "Narrowest meaningful validation first" defines validation order; it does not permit omitting the end-to-end seam needed to prove the behavior.
Failure Handling
When validation fails:
1.	Capture the failing command and relevant error.
2.	Diagnose the changed seam first.
3.	Make bounded, evidence-based corrections.
4.	Re-run the narrowest validation that proves the correction.
5.	Avoid speculative retries, broad rediscovery, or repetitive loops.
If evidence conflicts, the required fix materially expands scope, or progress becomes repetitive, stop and report the blocker and safest next decision.
Context Discipline
Keep active working context focused on:
•	current objective;
•	acceptance criteria;
•	relevant paths and symbols;
•	verified repository facts;
•	decisions affecting the change;
•	implementation state;
•	validation state;
•	unresolved risks.
Prefer:
•	paths over whole files;
•	symbols and focused excerpts over large file contents;
•	summaries over raw tool output;
•	diffs over rewritten files;
•	verified conclusions over exploration history.
Do not repeatedly rediscover facts unless:
•	relevant code changed;
•	exact current implementation or configuration matters;
•	generated, environment, or runtime state may differ;
•	branch state changed;
•	new evidence conflicts with an existing fact.
If context becomes noisy, compact completed exploration into verified facts and retain only information needed for the current objective.
Final Response
Report concisely:
Implemented
What changed and where.
Validation
Exact relevant tests and checks executed.
Result
Pass, Fail, or Not Run, with reason when applicable.
Outstanding Risks
Only material unresolved concerns, assumptions, or blockers.
Reference paths and symbols where useful.
Do not expose hidden chain-of-thought or dump entire files, logs, command histories, or exploration history.
