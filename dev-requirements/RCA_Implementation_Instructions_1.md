# RCA Workflow — Implementation Instruction Set

Purpose: instructions for an agent implementing the RCA (root cause analysis) workflow described in the companion design document `RCA_Workflow_Design.md`. Implement that design using its exact agent names and vocabulary; do not invent a parallel stage model. This document tells you how to honor the design and corrects five deviations seen in a prior build. It is self-contained — every rule it relies on is stated here.

## The agents (use these names, in this order)

Triage → Intake → Opening looks → Planner → Runner → Reader → Coverage challenge → Composer → Check runner → Judge → Fix advisor → Closure.

- **Triage** — groups related failures, picks one representative per group, defers the rest.
- **Intake** — builds the case file for one issue.
- **Opening looks** — runs the standard first checks for that test type.
- **Planner** — proposes the next single check ("look") and states, in advance, what each outcome will mean.
- **Runner** — executes the look read-only and returns a summary.
- **Reader** — judges the result, updates the suspect board, decides whether to continue.
- **Coverage challenge** — independent review that asks "what cause fits the evidence but was never considered?"
- **Composer** — writes the candidate hypotheses, each with a confidence tier.
- **Check runner** — runs each hypothesis's confirm check.
- **Judge** — rules each hypothesis confirmed / rejected / inconclusive.
- **Fix advisor** — proposes fixes for confirmed causes (never applies them).
- **Closure** — re-runs the failed test as proof, records the outcome, closes the case.

---

## Deviation 1 — Execution must be issue-specific

**One issue = one full run of the workflow.** An issue is one failing test on one target (one test, one table, one column-set, one time window).

- If the battery yields N failures, Triage groups them into M representatives and defers the rest; you then run the whole workflow M times, independently.
- Never produce one plan, one suspect board, or one findings document that spans multiple failing tests.
- Each issue gets its own case file, its own suspect board, its own look budget, its own evidence trail, its own findings, and its own closure.
- The only thing shared across issues is Triage's grouping and a read-only summary of already-closed findings passed into a later issue's Intake. That is a reference for context, never a merge of investigations.

## Deviation 2 — Run the full loop; do not stop at hypothesis creation

The Composer producing hypotheses is NOT the end. A hypothesis is unvalidated until it has been tested. The complete path per issue is:

Intake → Opening looks → [ Planner → Runner → Reader ] repeated → Coverage challenge → Composer → Check runner → Judge → Fix advisor → Closure.

The behaviors a prior build was missing map onto existing agents as follows:
- "Evidence collection" = Runner executing a Planner-specified look, read-only.
- "Hypothesis validation / execution" = Check runner running each hypothesis's confirm check.
- "Result analysis and accept/reject" = Judge ruling confirmed / rejected / inconclusive.
- "Generate next hypothesis if required" = the Planner–Reader loop continuing until a stop condition is met (see below); and, if every hypothesis is rejected at the Judge, a one-time return to the loop with the rejection evidence added.
- "RCA closure" = the Closure agent, ending in one of the four end states below.

Never present a hypothesis as a finding unless its confirm check has actually been run and judged.

**Loop stop conditions** — the Planner–Reader loop ends when ANY one is true:
- Converged: three or fewer suspects remain and the last two looks ruled out nothing new.
- Battle-tested: every remaining suspect has survived at least one look designed to rule it out.
- Budget spent: about ten looks used (forced stop; weakly-supported hypotheses flagged lower confidence).
- Dead end: the Planner says no useful check exists and the Reader agrees, two turns running.
Guardrail: do not close while the size of the failure is still unexplained — require at least one two-factor ("interaction") look first.

## Deviation 3 — Plans must be per test

Each issue's investigation plan is built from its test family and is specific, not generic. A plan must name:
- **Relevant logs** — which pipeline/source logs to read for this table and window.
- **Data checks** — the concrete read-only computations this test type needs.
- **Dependency checks** — lineage: which source feeds this table, which columns this column is derived from.
- **Execution steps** — the ordered looks to run.
- **Validation criteria** — for every look, the fork stated in advance: what result rules a suspect out, what result supports one.

A drift test and a completeness test must yield visibly different plans. Reject any generic plan reused across test families.

## Deviation 4 — User feedback at decision gates

Human input is possible, but only at these decision gates:

| Gate | Trigger | Human decides |
|---|---|---|
| Knowledge gate | Planner needs a domain fact absent from the case file and knowledge base | Supplies the fact; the loop pauses, the budget clock stops, the answer is saved permanently |
| Hypothesis selection (optional) | Before verification begins | Which hypotheses to verify first; otherwise confidence tiers decide |
| Conclusion approval | Before closing a case | Approves or rejects the concluded root cause |
| Remediation approval | Before any fix | Approves the fix — fixes are never applied automatically |
| Escalation | Re-entry exhausted, or results stay inconclusive | Takes over or redirects |

## Deviation 4a — Autonomous mode minimizes interaction (continuation)

Run every non-decision activity automatically — never prompt to run a look, compute a metric, or summarize. Prompt only at the five gates above. Rule of thumb: pause only when the next action changes the real world (a fix), assigns accountability (a concluded cause), or needs a fact the system cannot derive. In fully autonomous runs the Conclusion-approval gate may be waived by policy; Remediation approval never is.

---

## The four case end states

Closure must end a case in exactly one of these:
1. **Confirmed** — a hypothesis verified by a reject-capable check, fix approved and applied, and the originally failed test re-run (on the same frozen data snapshot where possible) and passing.
2. **Genuine change** — the data is correct; the world changed. No fix; the knowledge base gets an "expected change" note so the same failure stops re-triggering.
3. **Test-design flaw** — the test itself was misconfigured; the test plan is amended and the re-run under the amended test passes.
4. **Unresolved** — the one-time return to hypothesis creation was exhausted; escalate to a human with the full trail.

After closure, Triage reconciles every deferred failure against the confirmed cause; any that do not match open as their own issues.

## Non-negotiables (do not optimize these away)

- One issue, one lifecycle, one findings artifact. Never batch.
- The suspect board starts EMPTY; suspects appear only when a look's fork names them. No pre-seeded list of causes.
- Every fork states both outcomes in advance. Every confirm check must be able to reject its hypothesis, not only support it.
- Planner and Reader never see past case causes (this prevents copying old answers). Only Triage, Intake, and the Coverage challenge may read case history.
- Ruling out a suspect is provisional — the Reader may revive it with a stated reason. At most eight active suspects; combinations limited to pairs; a three-way interaction escalates to a human.
- Findings are validated hypotheses, never raw ones.

## Acceptance test (run before declaring done)

1. N failures grouped into M representatives produce exactly M independent runs and M findings artifacts — not one combined output.
2. Two different test families produce two visibly different investigation plans.
3. For at least one issue, the log shows the full path from Intake through Closure, including a confirm check that could have rejected its hypothesis.
4. A hypothesis that fails its confirm check is shown rejected, not carried into findings.
5. In autonomous mode the only prompts that appear are the five decision gates — no "shall I run the next look?" prompts.
6. Cases reach at least one of each of the four end states across the test set, or there is a stated reason one is unreachable in the test data.
7. A deliberately wrong grouping is caught at Closure reconciliation and split into its own issue.
