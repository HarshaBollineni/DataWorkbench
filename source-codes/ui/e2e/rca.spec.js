import { expect, test } from "./support/test-fixture";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_API_BASE } from "./support/endpoints";

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
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
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
