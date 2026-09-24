import { expect, test } from "./support/test-fixture";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_API_BASE } from "./support/endpoints";
import { saveStagedStructure } from "./support/dataset-structure";

// RCA Stage 3 gate: browser coverage for the full vertical slice —
// case creation, opening look, one Planner/Runner/Reader cycle, suspect
// update, hypothesis, rejection-capable confirmation check, human-approved
// simulated fix, and the honest original-test rerun outcome — reached only
// through the existing Issue Management / Issue RCA screens (second-module
// guard, docs/rca/00-contracts.md §3).

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "fixtures");

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

// Stage 4: the loop runs multiple Planner/Runner/Reader cycles until a stop
// condition fires (converged/battle-tested/budget/dead-end — which can fire
// on the very first Planner call if the fixture's table has no usable
// segment column, in which case Planner creates no look at all and the case
// moves straight to the coverage-challenge stage). Click through cycles
// defensively, checking after every click rather than assuming a look was
// created. Reused for both the initial investigation and the Stage 5
// second-chance investigation (same loop, same budget mechanics).
async function runInvestigationCycles(page, plannerName = "Planner: propose next look", runnerName = "Runner + Reader: execute & interpret") {
  for (let cycle = 0; cycle < 12; cycle++) {
    const plannerBtn = page.getByRole("button", { name: plannerName });
    if (!(await plannerBtn.isVisible({ timeout: 3000 }).catch(() => false))) break;
    await plannerBtn.click();
    const runnerBtn = page.getByRole("button", { name: runnerName });
    if (!(await runnerBtn.isVisible({ timeout: 3000 }).catch(() => false))) break; // dead_end: no look created
    await runnerBtn.click();
  }
}

// Drives from wherever the investigation loop stopped through coverage
// challenge, hypothesis composition, and one confirmation check. Returns the
// resulting state text ("awaiting fix approval" or "all hypotheses
// rejected") — both are legitimate real outcomes depending on what the
// deterministic looks actually found.
async function driveToJudgeOutcome(page) {
  await expect(page.getByText(/coverage challenge/)).toBeVisible();

  await page.getByRole("button", { name: "Coverage challenge — pass 1 (blind)" }).click();
  await expect(page.getByText("coverage challenge history", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Coverage challenge — pass 2 (history-aware)" }).click();
  const reopened = await page.getByRole("button", { name: /Kill-attempt on the history-nominated suspect/ })
    .isVisible({ timeout: 3000 }).catch(() => false);
  if (reopened) {
    await page.getByRole("button", { name: /Kill-attempt on the history-nominated suspect/ }).click();
  }
  await expect(page.getByText("hypothesis composition", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Composer: build hypotheses" }).click();
  await expect(page.getByText("confirmation checks", { exact: true })).toBeVisible();
  // At least one real tier badge (exact match — the fallback hypothesis's
  // statement text can contain "weakly attributed to", which a substring
  // regex would also match).
  const tierBadge = page.locator("div", { hasText: /^(strong|moderate|weak)$/ }).first();
  await expect(tierBadge).toBeVisible();

  // The button reads differently on a refinement re-run of an inconclusive
  // check (Stage 5), but the same click target works for the very first run.
  const checkBtn = page.getByRole("button", { name: /Run (rejection-capable confirmation check|refinement)/ });
  await checkBtn.click();
  // Scoped to the state badge itself (data-testid), not free text on the
  // page — the audit timeline's transition reasons can legitimately contain
  // these same words (e.g. start_second_chance's reason text literally says
  // "all hypotheses rejected"), which would otherwise collide with a loose
  // page-wide text match once the timeline has enough entries.
  const badge = page.getByTestId("rca-state-badge");
  await expect(badge).toHaveText(/awaiting fix approval|all hypotheses rejected/);
  return (await badge.textContent()) || "";
}

async function runFixApprovalToClosure(page) {
  await page.getByRole("button", { name: "Propose fix" }).click();
  await expect(page.getByRole("button", { name: "Human: approve fix" })).toBeVisible();
  await page.getByRole("button", { name: "Human: approve fix" }).click();
  await expect(page.getByRole("button", { name: /Confirm owner applied the fix/ })).toBeVisible();
  await page.getByRole("button", { name: /Confirm owner applied the fix/ }).click();
  await expect(page.getByText("closure rerun", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Closure: rerun the original test" }).click();
  // Honest scope limit: a simulated fix never mutates data, so the rerun
  // reproduces the original failure — the UI must say so, not silently
  // pretend nothing happened or fabricate a Confirmed outcome.
  await expect(page.getByText(/did not pass/)).toBeVisible();
  await expect(page.getByText("closure rerun", { exact: true })).toBeVisible(); // state unchanged
}

test("RCA vertical slice: case through the staged view to an honest closure attempt", async ({ page, request }) => {
  // Phase-3 boundary (plan rule 14 — do not invent a missing input): this
  // journey's SETUP depended on driving the old wizard's Build Plan ->
  // Execute to produce a real failing test, which then auto-opened the
  // issue this test drives into RCA. FWK-13 (Phase 3) deliberately emptied
  // the 14-test registry the wizard planned from — `fw_areas`/`fw_tests`
  // content is gone, so Build Plan now produces zero plan rows by
  // construction (dq_diagnostics/register.py is the replacement, but no
  // engine is wired to the wizard's plan/execute path, and won't be — the
  // wizard itself retires at the Phase-6 cutover, plan 6.13). There is no
  // legitimate way to manufacture a diagnostic failure through the product
  // surface until Phase 6 wires diag_runs VIOLATION -> issue auto-open
  // (CFR-14/RCA-27, plan 6-T15/6-T18), which is when this journey's setup
  // is rebuilt against the new Test Lab. The RCA mechanism itself (state
  // machine, gates, agents) is untouched this release and stays covered
  // directly by backend/tests/test_rca.py (all green) and the second test
  // below (no second UI surface).
  test.skip(true, "blocked: no diagnostic can produce a failing issue between " +
    "the Phase-3 registry retirement and the Phase-6 Test Lab rebuild (plan 6-T18)");

  const name = `e2e-rca-database-${Date.now()}`;
  await signIn(page);

  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Create New Database" }).click();
  await page.getByLabel("Alias").fill(name);
  const inputs = page.locator('input[type="file"]');
  await inputs.nth(0).setInputFiles(path.join(fixtures, "assessment.csv"));
  await expect(page.getByText("Received", { exact: true })).toHaveCount(1);
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
  await page.getByLabel("Snapshot label").fill(`snapshot-${name}`);
  await saveStagedStructure(page);
  await page.getByTestId("upl-step-6").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByText(/Ready/)).toBeVisible();

  const token = await page.evaluate(() => localStorage.getItem("dq_token"));
  const authHeaders = { Authorization: `Bearer ${token}` };
  const items = await (await request.get(`${E2E_API_BASE}/v2/items`, { headers: authHeaders })).json();
  const itemId = items.find((i) => i.name.endsWith(`-${name}`))?.item_id;
  expect(itemId).toBeTruthy();

  await page.getByRole("link", { name: "Test Lab", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
  await page.locator('select').first().selectOption({ label: name });
  await page.getByRole("button", { name: "Build Plan" }).click();
  await expect(page.getByText("Framework filter completed.")).toBeVisible();
  await page.getByRole("button", { name: "Finalize Plan" }).click();
  await expect(page.getByText(/Snippets are ready/)).toBeVisible();
  await page.getByRole("button", { name: /2\. Execute Framework Tests/ }).click();
  await page.getByRole("button", { name: "Approve All" }).click();
  await page.getByRole("button", { name: "Run Approved Tests" }).click();
  await expect(page.getByText("Execution finished", { exact: true })).toBeVisible();

  const issuesResp = await (await request.get(`${E2E_API_BASE}/v2/items/${itemId}/issues`, { headers: authHeaders })).json();
  expect(issuesResp.issues?.length).toBeGreaterThan(0);
  const issue = issuesResp.issues[0];
  expect(issue.workflow_version).toBe("rca"); // RCA_ENABLED is on

  await page.goto(`/issues/${issue.issue_row_id}`);
  // Case auto-creates on mount and lands at opening_looks.
  await expect(page.getByText(/RCA case/)).toBeVisible();
  await expect(page.getByText("opening looks", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Run opening look" }).click();
  await expect(page.getByText("investigation loop", { exact: true })).toBeVisible();
  await expect(page.getByText("Evidence trail")).toBeVisible();

  await runInvestigationCycles(page);
  const outcome1 = await driveToJudgeOutcome(page);

  if (outcome1.includes("awaiting")) {
    await runFixApprovalToClosure(page);
    return;
  }

  // Stage 5: rejected — one second chance is available. Take it and drive
  // the fresh (budget-boosted) investigation loop through to its own
  // outcome. Either a confirmation this time (finish the fix flow) or a
  // second rejection, at which point "Start second chance" is offered again
  // but the backend has already spent the one-time grant and escalates
  // straight to Unresolved instead of reopening investigation.
  expect(outcome1).toContain("all hypotheses rejected");
  await page.getByRole("button", { name: /Start second chance/ }).click();
  await expect(page.getByText("investigation loop", { exact: true })).toBeVisible();

  await runInvestigationCycles(page);
  const outcome2 = await driveToJudgeOutcome(page);

  if (outcome2.includes("awaiting")) {
    await runFixApprovalToClosure(page);
    return;
  }

  expect(outcome2).toContain("all hypotheses rejected");
  await page.getByRole("button", { name: /Start second chance/ }).click();
  await expect(page.getByTestId("rca-state-badge")).toHaveText("unresolved");
});

test("RCA case is reached only through the existing Issue RCA screen, no second surface", async ({ page }) => {
  await signIn(page);
  // Second-module guard: no new top-level nav entry for RCA.
  const navLinks = await page.locator("nav a").allTextContents();
  expect(navLinks.filter((t) => /rca/i.test(t))).toHaveLength(0);
});

test("RCA runs discovery and confirmation with one user action", async ({ page }) => {
  await signIn(page);
  const caseId = "rca-combined-ui";
  const issueId = "issue-combined-ui";
  let calls = 0;
  let bundle = {
    case_id: caseId, issue_row_id: issueId, state: "investigation_loop",
    case_file: { checklist_json: {} }, suspects: [], hypotheses: [],
    confirmation_checks: [], aar_evidence: [], executions: {},
    focused_hypothesis_candidates: [],
    selected_initial_hypothesis: { hypothesis_id: "hyp-original", statement: "Population composition explains missingness." },
    looks: [{ look_id: "look-discovery", kind: "planned", sql_or_helper_ref: "greedy_driver_search",
      fork_json: { hypothesis_id: "hyp-original", kind: "agent_driver_search", agent_runtime: true, execution_mode: "approved_helper",
        plan: { question: "Discover a separator and confirm the original hypothesis." } } }],
    data_chat: { unlocked: false, successful_investigation_count: 0, turns: [] },
  };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca",
    test_name: "Completeness", table_name: "applications", columns: ["amount"],
  } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  await page.route("**/api/v3/rca/looks/look-discovery/run", async (route) => {
    calls += 1;
    bundle = { ...bundle,
      looks: [{ ...bundle.looks[0], fork_json: { ...bundle.looks[0].fork_json, combined_run_state: "completed" } },
        { look_id: "look-confirmation", kind: "planned", sql_or_helper_ref: "population_segment_profile",
          fork_json: { kind: "agent_hypothesis_test", agent_runtime: true, combined_parent_look_id: "look-discovery",
            execution_mode: "approved_helper", plan: { question: "Confirm the population explanation within groups." } } }],
      executions: { "look-discovery": { execution_id: "exec-discovery", look_id: "look-discovery", status: "completed",
        summary_json: { runtime: { ok: true }, result: { summary: "Property value missingness separates the records." } } },
        "look-confirmation": { execution_id: "exec-confirmation", look_id: "look-confirmation", status: "completed",
          summary_json: { runtime: { ok: true }, result: { summary: "The residual within-group movement is small." } } } },
      aar_evidence: [{ artifact_id: "art-discovery", evidence_kind: "agent_interpretation", status: "completed",
        details: { look_id: "look-discovery", interpretation: { assessment: "inconclusive", rationale: "Discovery identifies an association." } } },
        { artifact_id: "art-combined", evidence_kind: "combined_hypothesis_run", status: "completed",
        details: { look_id: "look-discovery", confirmation_look_id: "look-confirmation", hypothesis_id: "hyp-original",
          interpretation: { assessment: "supported", rationale: "Within-group confirmation supports the population explanation." } } }],
      data_chat: { unlocked: false, successful_investigation_count: 1, turns: [] },
    };
    await route.fulfill({ json: bundle });
  });
  await page.goto(`/issues/${issueId}?rca_view=investigate`);
  await page.getByRole("button", { name: "Run hypothesis (discovery + confirmation)" }).click();
  await expect(page.getByTestId("rca-hypothesis-group").first()).toContainText("supported");
  await expect(page.getByTestId("rca-hypothesis-finding")).toContainText("Within-group confirmation");
  await expect(page.getByTestId("rca-focused-hypotheses")).toHaveCount(0);
  await expect(page.getByTestId("rca-agent-interpretation")).toContainText("Discovery stage");
  await expect(page.getByTestId("rca-agent-interpretation")).not.toContainText("inconclusive");
  await expect(page.getByTestId("rca-investigation-record").first()).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("rca-investigation-record").last()).toHaveAttribute("open", "");
  await page.getByTestId("rca-hypothesis-tests").locator(":scope > summary").click();
  await page.getByTestId("rca-investigation-record").first().locator(":scope > summary").click();
  await expect(page.getByTestId("rca-agent-interpretation")).toBeVisible();
  await expect(page.getByText("The residual within-group movement is small.", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("rca-hypothesis-group").last()).toContainText("Population composition explains missingness.");
  await expect(page.getByTestId("rca-data-chat-locked")).toContainText("1 more successful run");
  expect(calls).toBe(1);
  await page.reload();
  await expect(page.getByTestId("rca-hypothesis-group").first()).toContainText("supported");
  expect(calls).toBe(1);
});

test("RCA data chat unlocks after two successful tests and restores retained turns", async ({ page }) => {
  await signIn(page);
  const issueRowId = "issue-ui-data-chat";
  const caseId = "rca-ui-data-chat";
  let currentCase = {
    case_id: caseId, issue_row_id: issueRowId, item_id: "item-chat",
    table_name: "applications", state: "investigation_loop", workflow_generation: 1,
    created_at: "2026-09-24T10:00:00+05:30", created_by: "tester",
    case_file: { checklist_json: { test_name: "Row completeness", table_name: "applications" } },
    looks: [], executions: {}, suspects: [], hypotheses: [], confirmation_checks: [],
    judge_decisions: {}, closure: null, transitions: [], audit_events: [], aar_evidence: [],
    data_chat: {
      unlocked: false, successful_investigation_count: 1,
      required_successful_investigations: 2, turns: [], workflow_generation: 1,
    },
  };
  await page.route(`**/api/v2/issues/${issueRowId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueRowId, rca_case_id: caseId, workflow_version: "rca",
    test_name: "Row completeness", item_name: "Credit applications",
    table_name: "applications", columns: ["amount"], criticality: "High",
  } }));
  await page.route(`**/api/v3/issues/${issueRowId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: currentCase }));
  await page.route(`**/api/v3/rca/cases/${caseId}/data-chat`, (route) => {
    const question = route.request().postDataJSON().question;
    currentCase = {
      ...currentCase,
      data_chat: {
        ...currentCase.data_chat,
        turns: [{
          turn_id: "chat-ui-1", question, asked_at: "2026-09-24T10:10:00+05:30",
          answer: "Segment B contains 70% of the retained missing rows.",
          answered_at: "2026-09-24T10:10:02+05:30", status: "completed",
          plan: { rationale: "Use the retained segment evidence before calculating more." },
          model: { model_name: "gpt-5.6-sol", model_version: "2026-07-09" },
          library_search: { decision: "reuse_existing_helper", selected_helper_id: "segment_attribution" },
          evidence_references: ["art-chat-analysis"],
          assistant_evidence_artifact_id: "art-chat-answer",
          limitations: ["This association does not establish causality."],
          execution: { status: "completed", result: { evidence_rows: [
            { segment: "B", rows: 40, missing_rows: 28, missing_rate: 0.7 },
          ] } },
          events: [{ artifact_id: "art-chat-answer", recorded_at: "2026-09-24T10:10:02+05:30" }],
        }],
      },
    };
    return route.fulfill({ json: currentCase });
  });

  await page.goto(`/issues/${issueRowId}`);
  await expect(page.getByTestId("rca-data-chat-locked")).toContainText("1 more successful run is required");

  currentCase = {
    ...currentCase,
    data_chat: { ...currentCase.data_chat, unlocked: true, successful_investigation_count: 2 },
  };
  await page.reload();
  await expect(page.getByTestId("rca-data-chat")).toBeVisible();
  await page.getByLabel("Ask about this data").fill("Which segment contains the missing rows?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Segment B contains 70% of the retained missing rows.")).toBeVisible();
  await expect(page.getByTestId("rca-evidence-table").last()).toContainText("0.7");
  await page.getByText("Evidence and method").click();
  await expect(page.getByTestId("rca-chat-method")).toContainText("Approved helper: segment_attribution");
  await expect(page.getByTestId("rca-chat-method")).toContainText("art-chat-analysis");
  await expect(page.getByTestId("rca-chat-code")).toHaveCount(0);

  await page.reload();
  await expect(page.getByText("Which segment contains the missing rows?")).toBeVisible();
  await expect(page.getByText("Segment B contains 70% of the retained missing rows.")).toBeVisible();
  const pythonCode = 'result = {"note": "<script>not executable</script>", "rows": len(df)}';
  const originalTurn = currentCase.data_chat.turns[0];
  currentCase.data_chat.turns.push(...["completed", "failed"].map((status) => ({
    ...originalTurn, turn_id: `chat-generated-${status}`, status,
    question: `Generated analysis (${status})`, answer: `Analysis ${status}.`,
    library_search: { decision: "generate_code" },
    generated_code: { python_code: pythonCode, rationale: "A bounded custom calculation." },
    execution: { status, result: {} },
  })));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  const codeViews = page.getByTestId("rca-chat-code");
  await expect(codeViews).toHaveCount(2);
  await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
  for (const codeView of await codeViews.all()) {
    await expect(codeView.locator("pre")).toBeHidden();
    await codeView.locator("summary").click();
    await expect(codeView.locator("pre")).toBeVisible();
    await expect(codeView.locator("pre")).toHaveText(pythonCode);
    await expect(codeView.locator("script")).toHaveCount(0);
    expect(await codeView.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await codeView.locator("summary").click();
    await expect(codeView.locator("pre")).toBeHidden();
  }
});

test("RCA intake is issue-first across diagnostic families and retains optional context", async ({ page }, testInfo) => {
  await signIn(page);
  const examples = [
    { name: "Population stability", id: 14, outcome: "INVESTIGATE", metric: .3,
      metrics: { result_kind: "psi_feature", psi: .3, classification: "investigate", thresholds: { investigate: .25 } },
      rule: "The retained populations differ materially.", status: "Investigation recommended" },
    { name: "Row completeness", id: 6, outcome: "FAIL", metric: .72,
      rule: "Expected facility-period pairs are missing.", status: "Failed" },
    { name: "Business rule", id: 4, outcome: "FAIL", metric: 12,
      rule: "Twelve observations do not meet the declared cross-field rule.", status: "Failed" },
    { name: "Directionality", id: 11, outcome: "CONTEXTUAL", metric: null,
      metrics: { result_kind: "directionality_feature", limitations: ["Correlation does not establish causation."] },
      rule: "The declared direction requires contextual review.", status: "Review required" },
    { name: "Separation", id: 2, outcome: "NOT_EVALUATED", metric: null,
      na_reason: "Insufficient target observations.", rule: "Evaluation was not possible.", status: "Could not evaluate" },
    { name: "Legacy check", legacy: true, metric: null, status: "Outcome not specified" },
  ];
  for (const [index, example] of examples.entries()) {
    const issueId = `issue-intake-${index}`, caseId = `rca-intake-${index}`;
    const issue = { issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: example.name,
      item_name: "Retained portfolio", table_name: "loans", columns: ["feature_x"], metric: example.metric,
      ...(example.legacy ? {} : { source_evidence: { diagnostic_id: example.id, result_id: `result-${index}`,
        metrics: example.metrics || {}, na_reason: example.na_reason,
        finding: { rule_text: example.rule, outcome: example.outcome, evidence: [{ note: "Retained observation" }] },
        scope_counts: { rows_evaluated: 100, rows_skipped: 5 },
        data_profile: { artifact_id: `profile-${index}`, classification: "numeric", total_count: 105, mean: 5000 },
      } }),
    };
    let bundle = { case_id: caseId, state: "intake", case_file: { checklist_json: {} }, looks: [], executions: {},
      hypotheses: [], suspects: [], confirmation_checks: [], aar_evidence: [], investigation_context: [] };
    await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: issue }));
    await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
    await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
    await page.route(`**/api/v3/rca/cases/${caseId}/investigation/context`, (route) => {
      const body = route.request().postDataJSON();
      expect(body.hypothesis_id).toBeNull();
      bundle = { ...bundle, investigation_context: [{ id: "ctx-intake", answer: body.comment, answered_by: "Reviewer", answered_at: "2026-09-24" }] };
      return route.fulfill({ json: bundle });
    });
    await page.goto(`/issues/${issueId}`);
    const intake = page.getByTestId("rca-intake-evidence");
    await expect(intake.getByText(example.status, { exact: true })).toBeVisible();
    await expect(page.getByTestId("rca-intake-summary")).toBeVisible();
    await expect(page.getByTestId("rca-intake-limitations")).toContainText("does not establish its cause");
    for (const section of ["profile", "diagnostic", "provenance"]) {
      await expect(page.getByTestId(`rca-intake-${section}`)).not.toHaveAttribute("open", "");
    }
    await expect(page.getByRole("button", { name: "Start investigation", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Conclude from available evidence", exact: true })).toBeVisible();
    if (example.na_reason) {
      await expect(page.getByTestId("rca-intake-limitations")).toContainText(example.na_reason);
      await expect(page.getByTestId("rca-intake-key-evidence")).toHaveCount(0);
    }
    if (example.legacy) await expect(intake).toContainText("Detailed source evidence was not retained");
    if (index === 1) {
      await page.getByTestId("rca-intake-context").getByText("Add context", { exact: true }).click();
      await page.getByLabel("Your context", { exact: true }).fill("A new source feed began last quarter.");
      await page.getByRole("button", { name: "Save context", exact: true }).click();
      await expect(page.getByTestId("rca-intake-context")).toContainText("Context saved for the initial review.");
      await page.reload();
      await page.getByText("Saved context (1)", { exact: true }).click();
      await expect(page.getByTestId("rca-intake-context")).toContainText("A new source feed began last quarter.");
      await page.screenshot({ path: testInfo.outputPath("rca-issue-first-intake.png"), fullPage: true });
      await page.getByTestId("rca-intake-profile").locator(":scope > summary").click();
      await expect(page.getByText("Retained profile for the affected feature at intake.")).toBeVisible();
      await page.getByTestId("rca-intake-provenance").locator(":scope > summary").click();
      await expect(page.getByTestId("rca-intake-provenance")).toContainText("profile-1");
      await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
      await intake.hover();
      await expect(page.locator("aside[data-collapsed]")).toHaveAttribute("data-collapsed", "true");
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: testInfo.outputPath("rca-intake-mobile.png"), fullPage: true });
      expect(await intake.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      await page.setViewportSize({ width: 1280, height: 900 });
    }
  }
});

test("RCA uses four pages and warns before starting afresh", async ({ page }) => {
  await signIn(page);

  const issueRowId = "issue-ui-four-page";
  const baseCase = {
    case_id: "rca-ui-four-page",
    issue_row_id: issueRowId,
    table_name: "applications",
    state: "intake",
    workflow_generation: 1,
    created_at: "2026-09-15T10:00:00+05:30",
    created_by: "anirban",
    case_file: { checklist_json: { test_name: "Row completeness", table_name: "applications" } },
    looks: [], executions: {}, suspects: [], hypotheses: [], confirmation_checks: [],
    judge_decisions: {}, closure: null, transitions: [], audit_events: [],
    aar_evidence: [{
      artifact_id: "art-context-g1", artifact_type: "rca_case_context",
      evidence_kind: "case_context_created", stage: "intake", status: "recorded",
      recorded_at: "2026-09-15T10:00:00+05:30", payload: { actor: "anirban" },
    }],
  };
  let currentCase = structuredClone(baseCase);

  await page.route(`**/api/v2/issues/${issueRowId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueRowId,
    workflow_version: "rca",
    test_name: "Row completeness",
    item_name: "Credit applications",
    table_name: "applications",
    columns: ["facility_id"],
    criticality: "High",
    metric: 0.72,
    source_evidence: {
      result_id: "result-four-page",
      diagnostic_id: 6,
      entity_or_table: "applications",
      finding: { rule_text: "Expected facility-period rows are missing.", violation_count: 28 },
      metrics: { observed_completeness: 0.72 },
    },
  }}));
  await page.route(`**/api/v3/issues/${issueRowId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/issues/${issueRowId}/rca/case`, (route) => route.fulfill({ json: currentCase }));
  await page.route("**/api/v3/rca/cases/rca-ui-four-page/opening-look", (route) => {
    currentCase = {
      ...currentCase,
      state: "initial_review_complete",
      looks: [{ look_id: "look-initial", kind: "opening", sql_or_helper_ref: "profile_column" }],
      executions: { "look-initial": { execution_id: "exec-initial", look_id: "look-initial", executed_at: "2026-09-15T10:01:00+05:30", summary_json: { found: true, column: "facility_id", distinct: 72, null_share: 0 } } },
      aar_evidence: [
        ...currentCase.aar_evidence,
        { artifact_id: "art-static-start", evidence_kind: "static_initial_review", stage: "initial_review", status: "started", recorded_at: "2026-09-15T10:00:30+05:30", payload: { actor: "anirban" } },
        { artifact_id: "art-static-complete", evidence_kind: "static_initial_review", stage: "initial_review", status: "completed", recorded_at: "2026-09-15T10:01:00+05:30", payload: { actor: "anirban" } },
        { artifact_id: "art-llm-start", evidence_kind: "llm_initial_review", stage: "initial_review", status: "started", recorded_at: "2026-09-15T10:01:01+05:30", payload: { actor: "anirban" } },
        { artifact_id: "art-llm-complete", evidence_kind: "llm_initial_review", stage: "initial_review", status: "completed", recorded_at: "2026-09-15T10:01:10+05:30", details: {
          output: {
            summary: "The deterministic evidence shows the expected facility identifier is present in the retained snapshot.",
            observed_signals: ["The opening profile found 72 distinct identifiers."],
            candidate_hypotheses: [{ statement: "The row gap may originate before this snapshot.", evidence_basis: "The retained column itself is populated.", testable_next_step: "Compare expected and observed business keys." }],
            limitations: ["A column profile cannot establish upstream lineage."],
            recommended_next_steps: ["Run a governed key-coverage comparison."],
          },
          selected_model: { model_name: "gpt-5.6-sol", model_version: "2026-07-09" },
        } },
      ],
    };
    return route.fulfill({ json: currentCase });
  });
  await page.route("**/api/v3/rca/cases/rca-ui-four-page/start-afresh", (route) => {
    currentCase = {
      ...baseCase, workflow_generation: 2, audit_events: [{ event_type: "rca_reset" }],
      aar_evidence: [
        { artifact_id: "art-context-g2", evidence_kind: "case_context_created", stage: "intake", status: "recorded", recorded_at: "2026-09-15T10:02:00+05:30", payload: { actor: "anirban" } },
        { artifact_id: "art-reset-g2", evidence_kind: "workflow_reset", stage: "system", status: "completed", recorded_at: "2026-09-15T10:02:01+05:30", payload: { actor: "anirban" } },
      ],
    };
    return route.fulfill({ json: currentCase });
  });

  await page.goto(`/issues/${issueRowId}`);
  for (const label of ["1. Intake", "2. Initial Review", "3. Investigate", "4. Closure"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }
  await expect(page.getByTestId("rca-intake-evidence")).toBeVisible();

  await page.getByRole("button", { name: "Start investigation", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Initial review" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Profile the affected feature" })).toBeVisible();
  await expect(page.getByTestId("rca-llm-initial-review")).toContainText("gpt-5.6-sol");
  await expect(page.getByTestId("rca-llm-initial-review")).toContainText("The row gap may originate before this snapshot.");
  await page.getByText("Activity (5)").click();
  await expect(page.getByText("AAR art-static-complete", { exact: false })).toBeVisible();

  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("permanently lost");
    await dialog.dismiss();
  });
  await page.getByTestId("rca-header-details").locator("summary").click();
  await page.getByRole("button", { name: "Start afresh" }).click();
  await expect(page.getByRole("heading", { name: "Initial review" })).toBeVisible();

  page.once("dialog", async (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Start afresh" }).click();
  await expect(page.getByRole("button", { name: "1. Intake" })).toHaveAttribute("aria-current", "step");
  await expect(page.getByText("The initial review has not run yet.")).toHaveCount(0);
});

test("RCA result prioritizes the explanation and keeps raw metrics in the audit record", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await signIn(page);
  const caseId = "rca-compact-result";
  const issueId = "issue-compact-result";
  const rationale = "Debt missingness increased because a larger share of current records have missing scores. Within each score-completeness group, debt missingness is unchanged. This does not explain why scores are missing.";
  const look = { look_id: "look-compact", sql_or_helper_ref: "population_segment_profile",
    fork_json: { agent_runtime: true, execution_mode: "approved_helper",
      hypothesis: "Population composition explains the increase.",
      plan: { question: "Does score completeness explain the change?", rationale: "Compare debt missingness within score-completeness groups." } } };
  const bundle = { case_id: caseId, issue_row_id: issueId, state: "investigation_loop",
    case_file: { checklist_json: {} }, suspects: [], hypotheses: [], confirmation_checks: [],
    looks: [look], executions: { "look-compact": { look_id: look.look_id, execution_id: "exec-compact", status: "completed",
      presentation: { version: 1, metrics: [{ label: "Physical missing rate", unit: "ratio", value: .089259, value_label: "Current", comparison: { label: "Baseline", value: .050946 }, evidence_reference: "exec-compact" }] },
      summary_json: { runtime: { ok: true }, result: {
        summary: "Physical nulls in debt: baseline 991; current 964.",
        metrics: { column: "debt", segment_col: "score", population_fingerprint: "fingerprint-for-audit-only", baseline: { rows: 19452, physical_null_rate: 0.050946 }, current: { rows: 10800, physical_null_rate: 0.089259 } },
        evidence_rows: [{ population: "baseline", segment: "score physical missing", rows: 991, physical_null_count: 991 }, { population: "current", segment: "score physical missing", rows: 964, physical_null_count: 964 }],
      } } } },
    aar_evidence: [{ artifact_id: "art-compact-interpretation", evidence_kind: "agent_interpretation", status: "completed",
      details: { look_id: look.look_id, interpretation: { assessment: "supported", rationale, evidence_points: ["Missingness rose from 5.09% to 8.93% with no change within either group."] } } }],
  };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: { issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: "Completeness", table_name: "loans", columns: ["debt"] } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  await page.goto(`/issues/${issueId}`);
  await page.getByTestId("rca-background-evidence").locator(":scope > summary").click();
  await expect(page.getByTestId("rca-agent-interpretation")).toContainText(rationale);
  await expect(page.getByRole("heading", { name: "Explanation supported by evidence" })).toBeVisible();
  await expect(page.getByTestId("rca-key-metrics")).toContainText("5.09%");
  await expect(page.getByTestId("rca-key-metrics")).toContainText("8.93%");
  await expect(page.getByTestId("rca-key-metrics")).toContainText("+3.83 percentage points");
  await expect(page.getByTestId("rca-executed-code")).not.toHaveAttribute("open", "");
  await page.screenshot({ path: testInfo.outputPath("rca-compact-desktop.png"), fullPage: true });
  await expect(page.getByTestId("rca-analysis-result")).not.toContainText("population fingerprint");
  await expect(page.getByText("fingerprint-for-audit-only", { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("rca-evidence-table")).not.toBeVisible();
  await page.getByText("Supporting evidence", { exact: true }).click();
  await expect(page.getByTestId("rca-evidence-table")).toBeVisible();
  await expect(page.getByTestId("rca-evidence-table").locator("tbody tr")).toHaveCount(2);
  await page.getByText("Supporting evidence", { exact: true }).click();
  await page.getByRole("button", { name: "View audit record" }).click();
  await expect(page.getByRole("dialog")).toContainText("fingerprint-for-audit-only");
  await expect(page.getByRole("dialog")).toContainText("art-compact-interpretation");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
  await page.getByTestId("rca-analysis-result").hover();
  await expect(page.locator("aside[data-collapsed]")).toHaveAttribute("data-collapsed", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTestId("rca-investigation-record").screenshot({ path: testInfo.outputPath("rca-compact-mobile.png"), animations: "disabled" });
  const result = await page.getByTestId("rca-analysis-result").boundingBox();
  const method = await page.getByTestId("rca-executed-code").boundingBox();
  expect(result.y).toBeLessThan(method.y);
  expect(Math.abs(result.x - method.x)).toBeLessThan(2);
  expect(Math.abs(result.width - method.width)).toBeLessThan(2);
  expect(await page.getByTestId("rca-analysis-result").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  delete bundle.executions["look-compact"].presentation;
  await page.reload();
  await page.getByTestId("rca-background-evidence").locator(":scope > summary").click();
  await expect(page.getByTestId("rca-agent-interpretation")).toBeVisible();
  await expect(page.getByTestId("rca-key-metrics")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Download RCA report" })).toHaveCount(0);
  for (const [label, value, unit] of [["Outlier rate", .02, "ratio"], ["Duplicated records", 17, "count"], ["Correlation", -.5, "correlation"], ["PSI", .3, "number"], ["Reference match rate", .99, "ratio"]]) {
    bundle.executions["look-compact"].presentation = { version: 1, metrics: [{ label, value, unit }] };
    await page.reload();
    await expect(page.getByTestId("rca-key-metrics")).toContainText(label);
    await expect(page.getByTestId("rca-key-metrics")).not.toContainText("Physical missing rate");
  }
  bundle.state = "closed";
  bundle.closure = { outcome: "unresolved" };
  bundle.conclusion = { root_cause: "", approved_by: "Reviewer", confidence: "Low" };
  await page.route(`**/api/v3/rca/cases/${caseId}/report?fmt=pdf`, (route) => route.fulfill({ contentType: "application/pdf", body: "%PDF-mocked-download" }));
  await page.reload();
  await expect(page.getByTestId("rca-workspace-header")).toContainText("Unresolved");
  await expect(page.getByRole("button", { name: "Dataset report" })).toBeVisible();
  const completedDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download RCA report", exact: true }).click();
  expect((await completedDownload).suggestedFilename()).toBe("rca-completion-report.pdf");
  await page.route(`**/api/v3/rca/cases/${caseId}/report?fmt=text`, (route) => route.fulfill({ status: 400, json: { detail: "Report unavailable" } }));
  await page.getByRole("button", { name: "Download text report", exact: true }).click();
  await expect(page.getByTestId("rca-workspace-header").getByRole("alert")).toContainText("Report unavailable");
});

test("RCA groups numbered hypotheses and retains context for a follow-up", async ({ page }, testInfo) => {
  await signIn(page);
  const caseId = "rca-grouped", issueId = "issue-grouped";
  const hypothesis = { hypothesis_id: "hyp-second", statement: "Population composition explains the increase.", lifecycle_status: "selected" };
  const look = (id, hypothesisId, kind = "agent_hypothesis_test", parent = null) => ({ look_id: id, kind: "planned", sql_or_helper_ref: "population_segment_missingness",
    fork_json: { agent_runtime: true, execution_mode: "approved_helper", hypothesis_id: hypothesisId, kind, combined_parent_look_id: parent, plan: { question: `Question for ${id}` } } });
  let bundle = { case_id: caseId, issue_row_id: issueId, state: "investigation_loop", case_file: { checklist_json: {} },
    suspects: [], hypotheses: [], confirmation_checks: [], aar_evidence: [],
    selected_initial_hypothesis: hypothesis,
    hypothesis_catalog: [{ hypothesis_id: "hyp-first", number: 1, statement: "An earlier explanation." }, { ...hypothesis, number: 2 }],
    looks: [{ look_id: "opening", kind: "opening", sql_or_helper_ref: "profile_column" }, look("old", "hyp-first"),
      look("discovery", "hyp-second", "agent_driver_search"), look("confirmation", "hyp-second", "agent_hypothesis_test", "discovery"), look("followup", "hyp-second")],
    executions: Object.fromEntries(["opening", "old", "discovery", "confirmation"].map((id) => [id, { execution_id: `exec-${id}`, look_id: id, status: "completed", summary_json: { runtime: { ok: true }, result: { summary: `Retained ${id} result.` } } }])),
  };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: { issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: "Completeness", columns: ["debt"] } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  let attempts = 0;
  await page.route(`**/api/v3/rca/cases/${caseId}/investigation/context`, async (route) => {
    const body = route.request().postDataJSON();
    expect(body.hypothesis_id).toBe("hyp-second");
    attempts += 1;
    if (attempts === 1) return route.fulfill({ status: 400, json: { detail: "Context could not be saved yet" } });
    bundle = { ...bundle, aar_evidence: [{ artifact_id: "context-1", evidence_kind: "human_context", status: "recorded", recorded_at: "2026-09-24", details: { hypothesis_id: body.hypothesis_id, comment: body.comment } }],
      executions: { ...bundle.executions, followup: { look_id: "followup", execution_id: "cancelled-followup", status: "cancelled", summary_json: { cancelled: true, reason: "New context requires replanning" } } } };
    await route.fulfill({ json: bundle });
  });
  await page.goto(`/issues/${issueId}?rca_view=investigate`);
  const groups = page.getByTestId("rca-hypothesis-group");
  await expect(groups).toHaveCount(2);
  await expect(groups.first()).not.toHaveAttribute("open", "");
  await expect(groups.last()).toHaveAttribute("open", "");
  await expect(groups.first().locator(":scope > summary")).toContainText("Hypothesis 1");
  await expect(groups.last().locator(":scope > summary")).toContainText("hyp-second");
  await expect(groups.last()).toContainText("2.1 Discovery");
  await expect(groups.last()).toContainText("2.2 Confirmation");
  await expect(groups.last()).toContainText("2.3 Follow-up");
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await groups.last().getByRole("button", { name: "Copy hypothesis ID hyp-second" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("hyp-second");
  await groups.last().locator(":scope > summary").click();
  await expect(groups.last().getByTestId("rca-investigation-record").last()).not.toBeVisible();
  await groups.last().locator(":scope > summary").click();
  await groups.last().getByTestId("rca-add-hypothesis-context").locator("summary").click();
  await page.getByLabel("Hypothesis context", { exact: true }).fill("A source policy changed in 2024.");
  await page.getByRole("button", { name: "Save context", exact: true }).click();
  await expect(groups.last().getByRole("alert")).toContainText("Context could not be saved yet");
  await expect(page.getByLabel("Hypothesis context", { exact: true })).toHaveValue("A source policy changed in 2024.");
  await page.getByRole("button", { name: "Save context", exact: true }).click();
  await expect(groups.last()).toContainText("Context saved.");
  await expect(groups.last().getByTestId("rca-investigation-record").last()).toContainText("cancelled");
  await page.reload();
  await expect(groups.last()).toContainText("2.3 Follow-up");
  await page.getByText("User-added context (1)", { exact: true }).click();
  await expect(page.getByTestId("rca-hypothesis-context")).toContainText("A source policy changed in 2024.");
  await expect(groups.last()).toContainText("Retained confirmation result.");
  await expect(page.getByTestId("rca-selected-investigation-hypothesis")).toHaveCount(0);
  await expect(page.getByTestId("rca-combined-result")).toHaveCount(0);
  await expect(page.getByTestId("rca-background-evidence")).not.toHaveAttribute("open", "");
  await expect(groups.last().getByTestId("rca-hypothesis-tests")).not.toHaveAttribute("open", "");
  const calls = [];
  await page.route(`**/api/v3/rca/cases/${caseId}/planner-look?exploration=*`, async (route) => {
    const choice = new URL(route.request().url()).searchParams.get("exploration");
    calls.push(choice);
    if (choice === "alternative") {
      const alternative = { hypothesis_id: "hyp-third", statement: "Source availability explains the change.", origin: "alternative_explanation", lifecycle_status: "selected" };
      bundle = { ...bundle, active_investigation_hypothesis: alternative,
        hypothesis_catalog: [...bundle.hypothesis_catalog, { ...alternative, number: 3 }] };
    }
    bundle = { ...bundle, looks: [...bundle.looks, look(`direct-${choice}`, choice === "alternative" ? "hyp-third" : "hyp-second")] };
    await route.fulfill({ json: bundle });
  });
  await page.route("**/api/v3/rca/looks/direct-*/run", async (route) => {
    const id = route.request().url().split("/looks/")[1].split("/")[0];
    calls.push(id);
    const hypothesisId = bundle.looks.find((row) => row.look_id === id).fork_json.hypothesis_id;
    bundle = { ...bundle, executions: { ...bundle.executions, [id]: { look_id: id, execution_id: `exec-${id}`, status: "completed", summary_json: { runtime: { ok: true }, result: { summary: "Bounded evidence retained." } } } },
      aar_evidence: [...bundle.aar_evidence, { artifact_id: `finding-${id}`, evidence_kind: "agent_interpretation", status: "completed", details: { look_id: id, hypothesis_id: hypothesisId, interpretation: { assessment: "inconclusive", rationale: "The evidence narrows the explanation but does not establish a cause." } } }] };
    await route.fulfill({ json: bundle });
  });
  await page.getByRole("button", { name: "Continue exploring", exact: true }).click();
  await expect(groups).toHaveCount(2);
  await expect(groups.last().getByTestId("rca-hypothesis-finding")).toBeVisible();
  expect(calls).toEqual(["follow_up", "direct-follow_up"]);
  await page.getByRole("button", { name: "Explore another explanation", exact: true }).click();
  await expect(groups).toHaveCount(3);
  await expect(groups.last().locator(":scope > summary")).toContainText("hyp-third");
  await expect(groups.last()).toContainText("3.1 Hypothesis test");
  expect(calls).toEqual(["follow_up", "direct-follow_up", "alternative", "direct-alternative"]);
  await page.reload();
  await expect(groups.last().getByTestId("rca-hypothesis-finding")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("rca-consolidated-exploration.png"), fullPage: true });
});

test("RCA Initial Review renders governed PSI driver and special-value profile", async ({ page }) => {
  await signIn(page);

  const issueRowId = "issue-ui-psi-evidence";
  const summary = {
    column: "debt_yield",
    found: true,
    evidence_authority: "governed_aar",
    null_share: 0.1189,
    distinct: 771,
    mean: 8.522427780452576,
    profile_population_scope: "combined_baseline_and_current_source_snapshot",
    profile: {
      mean: 8.522427780452576,
      profile_basis: "confirmed_regular_values",
      special_values_confirmed: true,
      declared_special_values: ["-999"],
    },
    diagnostic: {
      kind: "population_stability_index",
      feature: "debt_yield",
      psi: 2.2918188252948624,
      dominant_driver: {
        bin: "missing",
        bin_label: "Missing",
        baseline_proportion: 0.18496812667077936,
        current_proportion: 0,
        contribution: 2.2432699924509776,
        absolute_contribution_share: 0.9788164612716982,
      },
    },
  };
  let currentCase = {
    case_id: "rca-ui-psi-evidence",
    issue_row_id: issueRowId,
    table_name: "Data",
    state: "initial_review_complete",
    workflow_generation: 1,
    created_at: "2026-09-22T11:44:35+05:30",
    created_by: "tester",
    case_file: { checklist_json: { test_name: "Population Stability Index review", table_name: "Data" } },
    looks: [{ look_id: "look-psi", kind: "opening", sql_or_helper_ref: "profile_column" }],
    executions: { "look-psi": { execution_id: "exec-psi", look_id: "look-psi", executed_at: "2026-09-22T11:44:45+05:30", summary_json: summary } },
    suspects: [], hypotheses: [], confirmation_checks: [], judge_decisions: {},
    hypothesis_candidates: [{
      hypothesis_id: "hyp-psi-missingness",
      statement: "The disappearance of missing values may be driving the PSI.",
      evidence_basis: "The missing bin contributes 2.2433 of total PSI 2.2918.",
      proposed_test: "Investigate why baseline rows were missing while current rows are complete.",
      lifecycle_status: "candidate",
      candidate_rank: 1,
    }],
    selected_initial_hypothesis: null,
    focused_hypothesis_candidates: [], selected_focused_hypothesis: null,
    closure: null, transitions: [], audit_events: [], investigation_context: [],
    aar_evidence: [{
      artifact_id: "art-psi-static", evidence_kind: "static_initial_review",
      stage: "initial_review", status: "completed", recorded_at: "2026-09-22T11:44:45+05:30",
    }, {
      artifact_id: "art-psi-llm", evidence_kind: "llm_initial_review",
      stage: "initial_review", status: "completed", recorded_at: "2026-09-22T11:45:10+05:30",
      details: {
        output: { summary: "Missingness is the dominant observed PSI driver." },
        selected_model: { model_name: "gpt-5.6-sol", model_version: "2026-07-09" },
      },
    }],
  };

  await page.route(`**/api/v2/issues/${issueRowId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueRowId,
    rca_case_id: currentCase.case_id,
    workflow_version: "rca",
    test_name: "Population Stability Index review",
    item_name: "CRE baseline",
    table_name: "Data",
    columns: ["debt_yield"],
    criticality: "Medium",
    metric: 2.2918188252948624,
  } }));
  await page.route(`**/api/v3/issues/${issueRowId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${currentCase.case_id}`, (route) => route.fulfill({ json: currentCase }));
  await page.route(`**/api/v3/rca/cases/${currentCase.case_id}/hypotheses/*/review`, (route) => {
    const body = route.request().postDataJSON();
    const hypothesisId = route.request().url().split("/hypotheses/")[1].split("/review")[0];
    const initial = currentCase.hypothesis_candidates.find((candidate) => candidate.hypothesis_id === hypothesisId);
    const focused = currentCase.focused_hypothesis_candidates.find((candidate) => candidate.hypothesis_id === hypothesisId);
    const source = initial || focused;
    const selected = {
      ...source, ...body, lifecycle_status: "selected", selected_by: "tester",
      selected_at: "2026-09-22T11:46:00+05:30",
    };
    if (initial) currentCase = { ...currentCase, hypothesis_candidates: [selected], selected_initial_hypothesis: selected };
    if (focused) currentCase = { ...currentCase, focused_hypothesis_candidates: [selected], selected_focused_hypothesis: selected };
    if (body.comment) currentCase = { ...currentCase, aar_evidence: [...currentCase.aar_evidence, {
      artifact_id: `art-hctx-${hypothesisId}`, evidence_kind: "human_context",
      stage: initial ? "initial_review" : "investigate", status: "recorded",
      recorded_at: "2026-09-22T11:46:00+05:30", created_by: "tester",
      details: { hypothesis_id: hypothesisId, reviewed_hypothesis_id: selected.hypothesis_id, comment: body.comment },
    }] };
    return route.fulfill({ json: currentCase });
  });
  await page.route(`**/api/v3/rca/cases/${currentCase.case_id}/initial-review/continue`, (route) => {
    currentCase = { ...currentCase, state: "investigation_loop" };
    return route.fulfill({ json: currentCase });
  });
  await page.route(`**/api/v3/rca/cases/${currentCase.case_id}/investigation/context`, (route) => {
    const comment = route.request().postDataJSON().comment;
    const retained = {
      id: "hctx-ui", answer: comment, answered_by: "tester",
      answered_at: "2026-09-22T11:46:30+05:30",
    };
    currentCase = {
      ...currentCase,
      investigation_context: [...(currentCase.investigation_context || []), retained],
      aar_evidence: [...currentCase.aar_evidence, {
        artifact_id: "art-human-context", evidence_kind: "human_context",
        stage: "investigate", status: "recorded",
        recorded_at: retained.answered_at, created_by: "tester",
      }],
    };
    return route.fulfill({ json: currentCase });
  });
  let plannerCalls = 0;
  await page.route(`**/api/v3/rca/cases/${currentCase.case_id}/planner-look*`, (route) => {
    plannerCalls += 1;
    if (plannerCalls > 1) {
      if (currentCase.looks.some((row) => row.look_id === "look-focused-followup")) {
        return route.fulfill({ json: currentCase });
      }
      currentCase = { ...currentCase, looks: [...currentCase.looks, {
        look_id: "look-focused-followup", kind: "planned", proposed_by: "investigation_agent",
        sql_or_helper_ref: "population_segment_profile",
        fork_json: {
          agent_runtime: true, execution_mode: "approved_helper",
          hypothesis_id: currentCase.selected_focused_hypothesis?.hypothesis_id,
          plan: { question: "Does the reporting-quarter segment explain the measured PSI movement?" },
        },
      }] };
      return route.fulfill({ json: currentCase });
    }
    currentCase = { ...currentCase, looks: [...currentCase.looks, {
      look_id: "look-agent-psi", kind: "planned", proposed_by: "investigation_agent",
      sql_or_helper_ref: "greedy_driver_search",
      fork_json: {
        kind: "agent_driver_search", agent_runtime: true, execution_mode: "approved_helper",
        hypothesis_id: "hyp-psi-missingness",
        hypothesis: "The disappearance of missing values may be driving the PSI.",
        execution_artifact: { implementation_source: "def greedy_driver_search(df, params):\n    return analyze_drivers(df, params)" },
        plan: {
          question: "Which eligible row attributes explain movement in the dominant Missing PSI bin?",
          rationale: "Model the measured Missing-bin symptom and attribute its baseline-to-current movement.",
          analysis_kind: "shallow greedy driver search",
          helper_params: { target_spec: { target_mode: "missingness", affected_column: "debt_yield", candidate_columns: ["reporting_quarter", "product_type"] } },
          expected_output: ["Baseline/current symptom attribution", "Focused root-cause candidate"],
          supports_hypothesis_when: "The missing bin dominates PSI contribution.",
          rejects_hypothesis_when: "Non-missing bins dominate PSI contribution.",
        },
      },
    }] };
    return route.fulfill({ json: currentCase });
  });
  await page.route("**/api/v3/rca/looks/look-agent-psi/run", (route) => {
    currentCase = {
      ...currentCase,
      focused_hypothesis_candidates: [{
        hypothesis_id: "hyp-focused-quarter", origin: "driver_search",
        lifecycle_status: "candidate",
        statement: "The Missing-bin movement may be concentrated in pre-2013 reporting quarters.",
        evidence_basis: "The leading governed segment contains the measured Missing-bin movement.",
        proposed_test: "Compare the Missing-bin rate within pre-2013 and later reporting quarters.",
      }],
      selected_focused_hypothesis: null,
      executions: { ...currentCase.executions, "look-agent-psi": {
        execution_id: "exec-agent-psi", look_id: "look-agent-psi", status: "completed",
        executed_at: "2026-09-22T11:48:00+05:30",
        summary_json: {
          found: true, agent_runtime: true, hypothesis_id: "hyp-psi-missingness",
          runtime: { status: "completed", ok: true, platform: "approved_helper_runtime" },
          result: {
            summary: "Missing changed from 18.50% in baseline to 0.00% in current and contributed 97.9% of absolute PSI. The leading reporting-quarter segment accounts for the measured Missing-bin movement.",
            metrics: { target_mode: "missingness", top_feature: "reporting_quarter", validation_auc: 0.91 },
            evidence_rows: [
              { population: "baseline", driver_segment: "reporting_quarter year <= 2012", population_rows: 19452, symptom_count: 3598, symptom_population_share: 0.185 },
              { population: "current", driver_segment: "reporting_quarter year <= 2012", population_rows: 10800, symptom_count: 0, symptom_population_share: 0 },
            ],
            model_validation: { validation_auc: 0.91, validation_balanced_accuracy: 0.82, tree_depth: 3, minimum_leaf_rows: 424, feature_importance: [{ rank: 1, feature: "reporting_quarter", importance: 0.73 }] },
            download_artifact_id: "art-sandbox-output",
            download_filename: "rca-psi-full-output.txt",
          },
        },
      } },
      conclusion_draft: {
        conclusion_type: "root_cause_identified",
        root_cause: "The reporting-quarter break is concentrated in the baseline population.\n\nObserved result: all 3,598 physical nulls occur in five baseline quarters.",
        confidence: "Moderate",
        limiting_evidence: "The evidence establishes the observed pattern but does not confirm remediation.",
        alternatives_considered: "Population composition and special-value handling were considered.",
        related_failures: "Reviewed against the retained PSI failure.",
        owner: "",
        approval_rationale: "Five affected baseline quarters have 100% physical-null rates; current has zero physical nulls.",
      },
      aar_evidence: [...currentCase.aar_evidence, {
        artifact_id: "art-agent-reading", evidence_kind: "agent_interpretation",
        stage: "investigate", status: "completed", recorded_at: "2026-09-22T11:48:03+05:30",
        details: { look_id: "look-agent-psi", interpretation: {
          assessment: "inconclusive", rationale: "The reporting-quarter segment concentrates the baseline-to-current Missing-bin movement that produced 97.9% of absolute PSI; confirmatory testing is still required before treating it as causal.",
          evidence_points: ["Missing changed from 18.50% in baseline to 0.00% in current."],
          next_question: "Cross-tab missing debt_yield by reporting quarter and frozen diagnostic population.",
        } },
      }],
    };
    return route.fulfill({ json: currentCase });
  });
  await page.route("**/api/v2/analysis-artifacts/art-sandbox-output/download", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ details: { result: { evidence_rows: [{ bin_label: "Missing", baseline_count: 3598 }] } } }),
  }));

  await page.goto(`/issues/${issueRowId}`);
  await expect(page.getByRole("heading", { name: "Initial review" })).toBeVisible();
  await expect(page.getByText(/largest contribution is Missing/)).toBeVisible();
  await expect(page.getByText(/baseline 18\.50% versus current 0\.00%/)).toBeVisible();
  await expect(page.getByText(/mean is 8\.5224 after excluding confirmed special values -999/)).toBeVisible();
  await expect(page.getByText(/combined baseline and current source snapshot/)).toBeVisible();
  await page.getByRole("button", { name: "Add context or revise" }).click();
  await page.getByLabel("Hypothesis context or comment").fill("A servicing policy changed during 2012-Q2.");
  await page.getByRole("button", { name: "Investigate this hypothesis" }).click();
  await expect(page.getByTestId("rca-hypothesis-group").last()).toContainText("disappearance of missing values");
  await page.getByText("User-added context (1)").click();
  await expect(page.getByTestId("rca-hypothesis-context")).toContainText("A servicing policy changed during 2012-Q2.");
  await expect(page.getByText("Add investigation context")).toHaveCount(0);
  await page.getByTestId("rca-hypothesis-tests").locator(":scope > summary").click();
  await expect(page.getByText("Question: Which eligible row attributes explain movement in the dominant Missing PSI bin?", { exact: true })).toBeVisible();
  await expect(page.getByTestId("rca-agent-plan")).toContainText("Approved helper · greedy_driver_search");
  await page.getByRole("button", { name: "Run hypothesis (discovery + confirmation)" }).click();
  await expect(page.getByTestId("rca-executed-code")).toContainText("No fresh code was generated");
  await expect(page.getByTestId("rca-executed-code")).not.toHaveAttribute("open", "");
  await page.getByTestId("rca-executed-code").locator(":scope > summary").click();
  await expect(page.getByTestId("rca-executed-code")).toContainText("Investigation method");
  await expect(page.getByTestId("rca-executed-code")).toContainText("Model the measured Missing-bin symptom");
  await expect(page.getByTestId("rca-executed-code")).toContainText("missingness target on debt yield; 2 eligible predictors");
  await expect(page.getByText("Technical implementation and parameters")).toHaveCount(0);
  const codeDetails = page.getByTestId("rca-execution-code");
  await expect(codeDetails).not.toHaveAttribute("open", "");
  await expect(codeDetails.locator("pre")).not.toBeVisible();
  await codeDetails.locator("summary").click();
  await expect(codeDetails.locator("pre")).toBeVisible();
  await expect(codeDetails.locator("pre")).toContainText("def greedy_driver_search(df, params):");
  await codeDetails.locator("summary").click();
  await expect(codeDetails.locator("pre")).not.toBeVisible();
  await expect(page.getByText("Technical evidence", { exact: true })).toHaveCount(0);
  const supportingEvidence = page.getByTestId("rca-supporting-evidence");
  await expect(supportingEvidence).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("rca-evidence-table").first()).not.toBeVisible();
  await expect(page.getByTestId("rca-agent-interpretation")).toBeVisible();
  await supportingEvidence.getByText("Supporting evidence", { exact: true }).click();
  await expect(page.getByTestId("rca-evidence-table").first()).toBeVisible();
  const resultBox = await page.getByTestId("rca-analysis-result").boundingBox();
  const methodBox = await page.getByTestId("rca-executed-code").boundingBox();
  expect(resultBox.y).toBeLessThan(methodBox.y);
  expect(Math.abs(resultBox.width - methodBox.width)).toBeLessThan(2);
  expect(Math.abs(resultBox.x - methodBox.x)).toBeLessThan(2);
  await page.getByRole("button", { name: "View audit record" }).last().click();
  await expect(page.getByRole("dialog")).toContainText("look-agent-psi");
  await expect(page.getByRole("dialog")).toContainText("validation_auc");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByTestId("rca-analysis-result")).toContainText("contributed 97.9% of absolute PSI");
  await expect(page.getByTestId("rca-analysis-result")).toContainText("Baseline/current symptom attribution");
  await expect(page.getByTestId("rca-evidence-table").first()).toContainText("symptom population share");
  const evidenceRows = page.getByTestId("rca-evidence-table").first().locator("tbody tr");
  await expect(evidenceRows).toHaveCount(2);
  expect(await evidenceRows.allTextContents()).toEqual(expect.arrayContaining([expect.stringContaining("baseline"), expect.stringContaining("current")]));
  expect(await page.getByTestId("rca-executed-code").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.getByTestId("rca-analysis-result").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.getByTestId("rca-analysis-result")).not.toContainText("The runtime returned structured evidence");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download full output (.txt)" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("rca-psi-full-output.txt");
  await expect(page.getByTestId("rca-agent-interpretation")).toContainText("inconclusive");
  await expect(page.getByTestId("rca-agent-interpretation")).toContainText("concentrates the baseline-to-current Missing-bin movement");
  await expect(page.getByText("Model validation details")).toBeVisible();
  await expect(page.getByTestId("rca-focused-hypotheses")).toContainText("pre-2013 reporting quarters");
  await expect(page.getByTestId("rca-focused-hypotheses")).toContainText("Next step");
  const resultThenHypothesis = await page.locator('[data-testid="rca-analysis-result"], [data-testid="rca-focused-hypotheses"]').evaluateAll(
    (elements) => elements.map((element) => element.dataset.testid)
  );
  expect(resultThenHypothesis.slice(-2)).toEqual(["rca-analysis-result", "rca-focused-hypotheses"]);
  await page.getByTestId("rca-focused-hypotheses").getByRole("button", { name: "Add context or revise" }).click();
  await page.getByTestId("rca-focused-hypotheses").getByLabel("Hypothesis context or comment").fill("Confirm the pre-2013 boundary using governed quarters.");
  await page.getByTestId("rca-focused-hypotheses").getByRole("button", { name: "Investigate this hypothesis" }).click();
  await expect(page.getByTestId("rca-hypothesis-group").last()).toContainText("pre-2013 reporting quarters");
  await page.getByTestId("rca-hypothesis-tests").last().locator(":scope > summary").click();
  await expect(page.getByText("Question: Does the reporting-quarter segment explain the measured PSI movement?", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue exploring" })).toBeVisible();
  await page.route("**/api/v3/rca/looks/look-focused-followup/run", (route) => route.fulfill({
    status: 400, json: { detail: "The governed execution request could not be validated." },
  }));
  await page.getByRole("button", { name: "Continue exploring" }).click();
  await expect(page.getByTestId("rca-investigation-action").getByRole("alert")).toContainText(
    "The governed execution request could not be validated."
  );
  await page.getByRole("button", { name: "4. Closure" }).click();
  await expect(page.getByLabel("Proposed root cause")).toHaveValue(/all 3,598 physical nulls/);
  await expect(page.getByLabel("Contradicting or limiting evidence")).toHaveValue(/does not confirm remediation/);
  await expect(page.getByLabel("Alternative explanations considered")).toHaveValue(/Population composition/);
  await expect(page.getByLabel("Approval rationale")).toHaveValue(/100% physical-null rates/);
  await expect(page.getByRole("button", { name: "Approve root cause" })).toBeEnabled();

  currentCase = {
    ...currentCase,
    state: "investigation_loop",
    looks: [...currentCase.looks, {
      look_id: "look-agent-timeout", kind: "planned", proposed_by: "investigation_agent",
      sql_or_helper_ref: "generated_code",
      fork_json: {
        agent_runtime: true, execution_mode: "generated_code_sandbox",
        hypothesis_id: "hyp-focused-quarter",
        hypothesis: "Test a generated analysis.",
        plan: { question: "Can the sandbox produce bounded evidence?" },
        generated_code: { python_code: "result = {}" },
      },
    }],
    executions: { ...currentCase.executions, "look-agent-timeout": {
      execution_id: "exec-agent-timeout", look_id: "look-agent-timeout", status: "timed_out",
      executed_at: "2026-09-22T11:49:00+05:30",
      summary_json: {
        found: false, agent_runtime: true,
        runtime: { status: "timed_out", ok: false, platform: "isolated_process_sandbox", error: "The generated analysis reached the sandbox's 60-second safety limit. No result was accepted; narrow the analysis or revise the generated method before retrying." },
        result: null,
      },
    } },
  };
  await page.goto(`/issues/${issueRowId}?rca_view=investigate`);
  const timedOutResult = page.getByTestId("rca-analysis-result").last();
  await expect(timedOutResult).toContainText("Execution failed");
  await expect(timedOutResult).toContainText("60-second safety limit");
  await expect(timedOutResult).toContainText("No analytical conclusion was created");
  await expect(timedOutResult).not.toContainText("invalid RCA evidence status");
  const generatedCode = page.getByTestId("rca-execution-code").last();
  await page.getByTestId("rca-hypothesis-tests").last().locator(":scope > summary").click();
  await page.getByTestId("rca-executed-code").last().locator(":scope > summary").click();
  await expect(generatedCode).not.toHaveAttribute("open", "");
  await generatedCode.locator("summary").click();
  await expect(generatedCode.locator("pre")).toHaveText("result = {}");
});
