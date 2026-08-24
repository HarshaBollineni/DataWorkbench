# RCA Workflow — Design (plain English)

**Part A — Hypothesis creation** (sections 1–9) · **Part B — Main investigation & closure** (sections 10–14)

What this covers: how the system investigates a failed data quality test and comes up with possible root causes. Design only — no code, no technical contracts.

---

## 1. The big idea (5 rules that never break)

1. **No ready-made answers.** The system never starts with a list of possible causes. Causes are discovered by running small checks ("looks") and ruling things out.
2. **Suspects come from questions.** A possible cause only appears when a look names it — "if the result is X, then cause A is ruled out." At that moment, A goes on the suspect board.
3. **Ruled-out is not forever.** If later evidence contradicts an earlier ruling, the suspect can be brought back — with a written reason.
4. **No evidence, no hypothesis.** Every final hypothesis must point to at least one look that backs it.
5. **The trail is the proof.** The full record — what was checked, what was ruled out, and why — is kept as the case's evidence.

---

## 2. What starts an investigation

- **Tests with a threshold:** a FAIL starts a case automatically.
- **Tests where the user decides:** a case starts only when the user raises a concern. That concern is written down as the "complaint."
- **Complaint rule:** the complaint describes the *symptom* only. If the user also guesses a cause ("I think the source system changed"), that guess becomes just one suspect among others — it never steers the whole investigation.

---

## 3. Information flow — what each agent hands to the next

| From → To | What is passed |
|---|---|
| Test battery → Triage | List of failures: which test, which table, which time window, the failing value |
| Triage → Intake | One representative failure + its linked failures + findings from earlier cases in this run |
| Intake → Opening looks | The case file: test details, failing columns, periods, row counts, schema, data access, any user complaint |
| Opening looks → Planner | Case file + results of the first checks |
| Planner → Runner | One check to run + its fork (what each outcome will mean) + what the result summary must keep |
| Runner → Reader | Short summarized result (the fork travels with it, untouched) |
| Reader → Planner (loop) | Updated suspect board + "keep going, plan the next check" |
| Reader → Coverage challenge (on stop) | Final suspect board + full evidence trail — but the challenge itself is shown **raw evidence only**, not the board |
| Coverage challenge → Composer | Board (maybe with one newly added suspect) + trail |
| Composer → Main RCA | 3–6 hypotheses: cause + evidence + confirm check + label (defect / test flaw / genuine change) + owner |
| Planner ↔ Human gate | Question out; answer back, saved permanently |
| Opening looks → Composer (shortcut) | The proving check + case file, when the first checks alone explain the failure |

Two deliberate choices in this flow: the **fork passes through the Runner untouched** (code never interprets meaning — the Planner writes it, the Reader uses it), and the **coverage challenge is shown raw evidence only** (it must think independently of the board).

### 3a. The knowledge base behind the agents

A store of facts the system already knows, holding five kinds: structure (columns, tables, types), lineage (which source feeds which table), domain facts (rating-scale order, lifecycle order, definitions, use-case needs), ownership (who owns each table and test), and case history (closed cases, confirmed causes, "expected changes").

Rules that protect the design:
- **Trust levels:** every domain fact is tagged *human-confirmed* or *inferred*. Inferred facts can be challenged; a wrong fact is more dangerous than a missing one.
- **Shelf life:** every fact carries its last-confirmed date. Past its shelf life (default 12 months, tunable per fact type), or immediately after a schema change touches the related table, it drops back to *inferred* — making it challengeable again through the normal human gate. Re-confirmation happens only when a case actually needs the fact; no separate re-certification exercise.
- **Blame-back:** when a case ends Unresolved, or a Strong hypothesis is rejected in Part B, the facts that case relied on are flagged "under suspicion" for human review. Bad facts reveal themselves through the failures they cause.
- **Quarantine:** past causes are hidden from the Planner and Reader — otherwise the system starts copying old answers. Only Triage (grouping), Intake (suppressing known expected changes), and the coverage challenge may see case history.
- **Writes:** only code writes to it, at two moments — when a human answers a question mid-case, and when a case closes. LLM agents never write it directly.

---

## 4. The agents, in order

### Agent 0 — Triage (sorts the failures)
- Groups failures that likely share one cause. **Two-signal rule:** grouping requires BOTH (a) same upstream lineage and time window, AND (b) a similar symptom shape — same direction, comparable size, related columns. One signal alone → keep the failures separate.
- Damage from a wrong grouping is capped by design: attached failures are never investigated themselves, and reconciliation at closure re-checks every one — a bad grouping costs delay, never a contaminated investigation.
- Picks one representative failure per group; the rest wait, tagged "probably same cause."
- After the case closes, it checks each waiting failure against the found cause. Any that don't match get their own case.
- Runs cases one at a time, structural problems first (schema → completeness → statistics), because structural problems often explain statistical ones.

### Agent 1 — Intake (builds the case file)
- Uses a fixed checklist per test family (drift / missing data / schema / ordering / series-break) listing the facts a case file must contain.
- Pulls what it can from the knowledge base automatically (schemas, feature lists, definitions). A case is rejected as "not investigable" only if a required fact is missing from both the upload and the knowledge base.
- **Suppression:** if the knowledge base says this exact pattern is a known, accepted change (e.g., expected vintage drift), the case is closed immediately with that note instead of re-investigating every period.
- Stage 2 cases must have their use-case context (feature list, target definition, etc.) at intake — no starting and hoping.
- Several columns failing the same test on one table = one case.

### Agent 2 — Opening looks (standard first checks)
- 2–4 fixed computations per test family (e.g., for drift: the metric per period, entity overlap, missing rates). Uses the knowledge base structure to pick the right period and join columns.
- **Shortcut:** if these first checks alone clearly explain the failure, the case can jump straight to the Composer — but only by naming the exact check that proves it.

### Agent 3 — Look planner (decides the next check)
- Proposes exactly ONE next look, and must say in advance what each outcome would mean: "if X, cause A is ruled out; if Y, cause B gains support."
- Must also say what detail the result summary has to keep, so the summary can't accidentally hide the answer.
- Reads the knowledge base's structure, lineage, and domain facts to design smarter checks. **Never sees past case causes.**
- If it needs business knowledge nobody recorded, it asks a human (see the pause rule). If no useful check exists anymore, it declares a dead end.
- Cannot re-open a ruled-out suspect on its own — it must ask the Reader, with a reason.

### Agent 4 — Look runner (runs the check)
- Runs the look read-only and returns a short, size-limited summary.
- A crashed or empty look does not count against the budget; one retry, then back to the Planner.

### Agent 5 — Reader (judges the result)
- The only agent allowed to change the suspect board (statuses: active / ruled out / revived).
- Compares the result with the promised fork and updates the board.
- May rule "this result doesn't answer the question" — that look costs no budget and goes back for re-planning.
- Uses knowledge base domain facts to judge correctly (e.g., the true rating-scale order). **Never sees past case causes.**
- Decides after each look: continue, or stop (converged / budget spent / dead end).

### Coverage challenge (safety check before finishing)
- Runs in **two passes**, once per case:
- **Pass 1 (fully blind):** a separate review that sees ONLY the raw evidence — not the suspect board, not case history — and answers: "Is there a cause that fits all this evidence but was never considered?"
- **Pass 2 (history check):** may consult past case history in the knowledge base — but history can only *nominate* a suspect, never admit one. A history-nominated suspect enters the board only if the raw evidence from this case is consistent with it.
- Any suspect added here is tagged by where it came from ("evidence-found" or "history-nominated"), gets **no confidence credit** for the tag, and must survive a kill-attempt look in the reopened loop (about 2 extra looks) before it can reach the Composer. History gets a voice, never a shortcut.

### Agent 6 — Composer (writes the hypotheses)
- Reads only the final suspect board and the evidence trail.
- Produces 3–6 hypotheses. Each = the suspected mechanism + the looks that back it (at least one, always) + a concrete check that would confirm it.
- Each hypothesis gets one label:
  - **Data defect** — something in the pipeline is broken → routed to the data owner (owner found in the knowledge base)
  - **Test-design flaw** — the test itself is set up wrong → routed to the test owner
  - **Genuine change** — the data is right; the world changed → no fix needed
- Mixed outcomes are allowed. If the loop stopped because the budget ran out, thinly-supported hypotheses are clearly flagged as lower confidence.
- **Confidence tiers (defined criteria, no invented scores):** every hypothesis gets one of three tiers —
  - **Strong** — survived at least one look designed to kill it AND has 2+ independent supporting looks
  - **Moderate** — survived a kill-attempt look
  - **Weak** — has cited evidence only (typical when the loop stopped on budget)
  Part B uses these tiers to order its verification work.

---

## 5. The loop and when it stops

Planner → Runner → Reader, repeat. Budget: about 10 looks.

**The loop stops when any one of these is true:**
1. **Converged** — 3 or fewer suspects remain AND the last 2 looks ruled out nothing new.
2. **Battle-tested** — every remaining suspect has survived at least one look that was designed to rule it out. (Just collecting friendly evidence doesn't count — surviving an attack is stronger proof than gathering support.)
3. **Budget spent** — 10 looks used; forced stop.
4. **Dead end** — the Planner says no useful check exists AND the Reader agrees, two turns in a row.

**Combination rules (for causes that only hurt together):**
- A combination is a legal suspect: "A and B together" can sit on the board like any single cause — but **pairs only** in this version; a suspected three-way interaction goes to a human instead.
- A combination may only be created when its trigger fires (board converged but failure size unexplained) — never speculatively mid-loop.
- **Board cap: 8 active suspects.** To add a ninth, the Planner must first name which existing suspect the next look will try to kill.
- **Ruling out "A alone" never rules out combinations that contain A** — kills do not spread to combinations.
- **Symptom-size check inside the loop:** if the board has converged but no surviving suspect plausibly explains the *size* of the failure, the Planner must propose at least one interaction look (a check that conditions on two factors jointly) before a dead end may be declared.

**Pause-and-ask rule (human gate):** when the Planner needs business knowledge that is in neither the case file nor the knowledge base, the loop pauses and asks a human. It never guesses. The clock stops while paused. The answer is saved into the knowledge base as a human-confirmed fact — so it is never asked twice.

---

## 6. Special paths

| Path | When | Rule |
|---|---|---|
| Shortcut | Opening checks alone explain the failure | Must name the proving check |
| Pause-and-ask | Missing business knowledge | Loop pauses; answer saved to knowledge base forever |
| Second chance | The downstream investigation disproves ALL hypotheses | Case returns once, with the disproof added, and ~5 fresh looks. Once only. |
| Escalation | Second chance also fails | Goes to a human, tagged unresolved, full trail attached |

---

## 7. How a case may end (only four ways)

1. **Confirmed** — one hypothesis verified, a fix assigned, and the originally failed test re-run and passing. The passing re-run is the proof.
2. **Genuine change** — no fix; the knowledge base gets an "expected change" note so the same failure doesn't trigger a new investigation every period.
3. **Test-design flaw** — the test plan is amended; the re-run under the fixed test passes.
4. **Unresolved** — second chance used up; a human takes over with the full trail.

Every closing writes back to the knowledge base: the confirmed cause, any expected-change notes, and ownership routing that happened. That is the system's memory — quarantined from future planning, but available for triage, suppression, and the coverage challenge.

---

## 8. Known limitations (accepted for now)

- Findings shared between back-to-back cases could nudge a later case toward the earlier case's answer without real evidence. Watch for it.
- The system does not yet learn to plan better looks from its closed cases. Planned for a later version.
- A wrong fact in the knowledge base can quietly damage every case that uses it — which is why facts carry a "human-confirmed vs inferred" tag and inferred facts can be challenged.

---

## 9. Sanity check — the reference example

DSCR distribution shift (PSI 2.57, 2009 vs 2025) on a CRE loan database: opening checks showed a smooth year-by-year curve (ruled out a sudden break), zero loan overlap between the periods (ruled out same-loan deterioration), and rising DSCR by origination year (supported vintage-driven drift). Final output: **Genuine change** (vintage drift — knowledge base gets an expected-change note) plus **Test-design flaw** (the reference period was a poor choice — routed to the test owner). Matches end states 2 and 3.

---
---

# PART B — Main investigation & closure

Takes the 3–6 hypotheses from Part A and turns them into a verified root cause, an applied fix, and a properly closed case.

## 10. What arrives from Part A

For each hypothesis: the suspected cause + the looks that back it + a concrete confirm check + a label (data defect / test-design flaw / genuine change) + the responsible owner. Plus the full case file and evidence trail.

## 11. The agents, in order

### Agent 7 — Check runner (code)
- Runs each hypothesis's confirm check, read-only, one at a time.
- Order rule: confidence tier first (Strong verified before Moderate before Weak), cost as the tie-break. Strong causes get verified fastest; Weak candidates consume budget only if symptoms remain unexplained.
- A crashed or empty check gets one retry, then goes back for re-design; it costs no budget.

### Agent 8 — Judge (LLM)
- **Gatekeeper duty (before any check runs):** every confirm check must state in advance what result would REJECT the hypothesis. A check that can only confirm is sent back to be redesigned. This stops the system from writing itself easy exams.
- Reads each result and rules per hypothesis: **confirmed / rejected / inconclusive**.
- **Inconclusive rule:** one refinement of the check is allowed; if still inconclusive, the hypothesis counts as unverified and is flagged for the human, never silently confirmed.
- **Symptom accounting:** confirmed causes must together explain the observed size of the failure (e.g., the full PSI of 2.57, not half of it). If they explain only part, verification continues on the remaining hypotheses — one confirmed cause does not end the job early.

### Agent 9 — Fix advisor (LLM)
- For each confirmed cause, proposes fix options in plain terms, routed by label:
  - **Data defect** → fix options to the data owner (repair the feed, backfill, correct the join)
  - **Test-design flaw** → amendment options to the test owner (new reference logic, new threshold, changed eligibility)
  - **Genuine change** → no fix; drafts the "expected change" note for the knowledge base
- **Human approval rule:** fixes are never applied automatically. A human approves and applies; the system only tracks status.

### Agent 10 — Closure (code)
- After the fix is applied: **re-runs the originally failed test.** A pass is the closure proof (under the amended test, if the test itself was fixed).
- **Frozen-snapshot rule:** where possible, the re-run uses the same data snapshot that originally failed — otherwise a pass might come from the data changing, not the fix working. If a fresh snapshot must be used, that is flagged on the closure record.
- Writes to the knowledge base: confirmed cause, expected-change notes, links to amended tests.
- Hands back to Triage for reconciliation: every attached "probably same cause" failure is checked against the confirmed cause; mismatches open their own cases.
- **Time-box rule:** a case waiting on an owner's fix beyond its time-box is escalated, not left open forever.

## 12. Part B flow and exits

| Situation | What happens |
|---|---|
| At least one hypothesis confirmed, symptoms fully explained | Fix path → re-run → close **Confirmed** (or **Genuine change** / **Test-design flaw** per label) |
| Causes confirmed but symptoms only partly explained | Verification continues on remaining hypotheses |
| ALL hypotheses rejected | One-time return to Part A with the rejection evidence + ~5 fresh looks (already defined in §6) |
| Return also fails, or checks stay inconclusive | Escalate to human, tagged **Unresolved**, full trail attached |
| Fix approved but not applied within its time-box | Escalate to owner's escalation contact |

Budget: roughly 2 checks per hypothesis, about 10 checks total per case.

## 13. Part B validation record

**Tested against three walkthroughs:**
1. **PSI reference case** — vintage drift + reference flaw both confirmed via rejecting-capable checks (within-vintage PSI; rolling-reference PSI); symptom accounting satisfied; closed under end states 2 + 3. Passed.
2. **Missing-data case** — "channel stopped capturing" confirmed by channel-level missing rates (would have been rejected if uniform); data owner fix; re-run passed. Passed.
3. **Failure path** — all hypotheses rejected; one-time return to Part A with the disproofs produced a new board and a confirmed cause. Return contract works. Passed.

**Validator findings, resolved:**
1. Self-confirmation risk (Composer writes both the hypothesis and its exam) → Judge gatekeeper duty: every check must be able to reject, approved before running.
2. False closure proof (re-run passes because the data moved on) → frozen-snapshot rule.
3. Multi-cause blindness (stopping at the first confirmed cause) → symptom-accounting rule.

**Accepted residual risks:** owners applying fixes outside the system means fix quality itself is not verified, only the re-run outcome; and symptom accounting depends on the failure having a measurable size — purely qualitative complaints (contextual tests) rely on the human's judgment that the concern is resolved.

## 14. End-to-end picture

Test battery → **Part A**: Triage → Intake → Opening looks → elimination loop (Planner/Runner/Reader, human gate) → coverage challenge → Composer → **Part B**: Judge-approved confirm checks → confirmed causes → human-approved fixes → re-run proof → knowledge base updated → attached failures reconciled → case closed in one of four end states.

One sentence version: *the system earns its hypotheses by elimination, earns its conclusions by surviving rejection-capable checks, and earns its closure by re-running the test that started it all.*

---

## 15. Validator round two — findings 4–6

4. **No hypothesis ranking** → confidence tiers with defined criteria (Strong / Moderate / Weak, based on kill-attempts survived and independent support), and Part B verification ordered by tier. No invented numeric scores — tiers are earned from the evidence trail only.
5. **Coverage challenge as a history backdoor** → split into two passes: pass 1 fully blind (no board, no history); pass 2 may consult history but can only nominate — the suspect enters solely if this case's raw evidence is consistent with it, carries a "history-nominated" tag, gets no confidence credit, and must survive a kill-attempt before reaching the Composer.
6. **Interacting / multi-cause failures** → combinations are legal suspects; killing a single factor never kills combinations containing it; and symptom-size checking moved into Part A's loop — a dead end cannot be declared while the failure's size is unexplained until at least one interaction look has been tried.

---

## 16. Validator round three — findings 7–9

7. **Triage misgrouping** → two-signal rule: grouping needs matching lineage/time AND similar symptom shape; one signal keeps failures separate. Damage already capped: attachments are never investigated and are re-checked at closure.
8. **Knowledge-base fact rot** → shelf life (facts expire back to *inferred* after 12 months or a related schema change, re-confirmed only when a case needs them) + blame-back (facts behind Unresolved cases or rejected Strong hypotheses get flagged for review).
9. **Suspect-board explosion** → pairs-only combinations (three-way goes to a human), combinations created only on their trigger, and a board cap of 8 active suspects — adding a ninth requires naming which existing suspect the next look will attack.

---

## 17. Per-issue independence

This was always implied but is now explicit, because implementations tend to batch.

**One issue = one full run of this workflow.** An issue is a single failing test on a single target (one test, one table, one column-set, one time window). If the battery produces many failures, this workflow runs many times — once per representative issue — never once with a combined plan.

- Triage is the ONLY step that looks across issues, and it only **groups and defers** — it never merges failures into a shared investigation.
- Each issue has its own case file, its own suspect board, its own budget, its own evidence trail, and its own findings artifact.
- The only thing shared between issues is the read-only run-context of already-closed findings (input to a later issue's Intake). That is a reference, never a merge.
- A single plan, a single suspect board, or a single findings document spanning multiple failing tests is a design violation.

## 18. The investigation plan is per test

Each issue's plan (produced at Intake + first Planner turns) is specific to its test family, not generic. A plan names: the relevant pipeline/source logs for this table and window; the data checks this test type needs; the dependency/lineage checks (which source feeds this table, which columns this column derives from); the ordered looks to run; and, for every look, the fork stated in advance (what result kills a suspect, what supports one). Two different test families must yield visibly different plans.

## 19. Autonomy contract — when the workflow pauses for a human

The workflow runs every non-decision activity automatically. It never asks permission to run a look, compute a metric, or summarize. It pauses for a human ONLY at these decision gates:

| Gate | Trigger | Human decides |
|---|---|---|
| Knowledge gate (§5) | Planner needs a domain fact absent from case file + knowledge base | Supplies the fact; loop pauses, budget clock stops, answer saved to KB |
| Hypothesis selection (optional) | Before Part B verification | Which hypotheses to verify first; otherwise confidence tiers decide |
| Conclusion approval | Before closing a case | Approves / rejects the concluded root cause |
| Remediation approval | Before any fix | Approves the fix — fixes are never auto-applied |
| Escalation | Re-entry exhausted or persistent inconclusive | Takes over / redirects |

Principle: pause only when the next action changes the real world (a fix), assigns accountability (a concluded cause), or needs knowledge the system cannot derive (a domain fact). Everything else is automatic. In fully autonomous runs the Conclusion-approval gate may be waived per policy, but Remediation approval never is.

This does not add stages — it labels which existing transitions are human gates and confirms all others run unattended.
