import { expect, test } from "./support/test-fixture";

test("RCA candidate and report progress stay beside their controls on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const caseId = "rca-local-progress", issueId = "issue-local-progress";
  const candidate = { hypothesis_id: "hyp-local", statement: "Composition explains the change", evidence_basis: "Retained evidence", proposed_test: "Compare segments" };
  let bundle = { case_id: caseId, workflow_generation: 1, state: "initial_review_complete", case_file: { checklist_json: {} },
    looks: [{ look_id: "opening", kind: "opening" }], executions: {}, hypotheses: [], suspects: [], confirmation_checks: [],
    hypothesis_candidates: [candidate], aar_evidence: [{ artifact_id: "aar-local-review", evidence_kind: "llm_initial_review", status: "completed", details: { output: {} } }] };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: "Business rule", item_name: "Portfolio", table_name: "loans",
  } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  let polls = 0;
  await page.route(`**/api/v3/rca/cases/${caseId}/progress`, (route) => {
    polls++;
    return route.fulfill({ json: { workflow_generation: 1, operation: { sequence_no: 10, phase: "Planning analysis", status: "running" } } });
  });
  await page.route(`**/api/v3/rca/cases/${caseId}/hypotheses/*/review`, (route) => route.fulfill({ json: bundle }));
  await page.route(`**/api/v3/rca/cases/${caseId}/initial-review/continue`, (route) => route.fulfill({ json: bundle }));
  let release;
  let gate = new Promise((resolve) => { release = resolve; });
  await page.route(`**/api/v3/rca/cases/${caseId}/planner-look`, async (route) => { await gate; await route.fulfill({ json: bundle }); });
  await page.goto(`/issues/${issueId}?rca_view=initial-review`);
  await page.getByRole("button", { name: "Investigate this hypothesis", exact: true }).click();
  const review = page.getByTestId("rca-llm-initial-review");
  await expect(review.getByRole("button", { name: "Preparing investigation…", exact: true })).toBeDisabled();
  await expect(review.getByTestId("rca-progress")).toContainText("Planning analysis");
  await expect(page.getByTestId("rca-progress")).toHaveCount(1);
  release();
  await expect(page.getByTestId("rca-progress")).toHaveCount(0);
  bundle = { ...bundle, state: "closed", closure: { outcome: "unresolved" } };
  await page.reload();
  gate = new Promise((resolve) => { release = resolve; });
  await page.route(`**/api/v3/rca/cases/${caseId}/report?fmt=text`, async (route) => {
    await gate;
    await route.fulfill({ body: "Retained report", contentType: "text/plain", headers: { "Content-Disposition": "attachment; filename=report.txt" } });
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download text report", exact: true }).click();
  const reports = page.getByTestId("rca-report-actions");
  await expect(reports.getByRole("button", { name: "Preparing text report…", exact: true })).toBeDisabled();
  await expect(reports.getByTestId("rca-progress")).toBeInViewport();
  const pollsBeforeReport = polls;
  await expect(reports.getByTestId("rca-progress")).toContainText("1s elapsed");
  expect(polls).toBe(pollsBeforeReport);
  release();
  await download;
  await expect(page.getByTestId("rca-progress")).toHaveCount(0);
});

test("RCA investigation and chat show phases, stop on completion and navigation", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const caseId = "rca-run-progress", issueId = "issue-run-progress";
  let bundle = { case_id: caseId, workflow_generation: 1, state: "investigation_loop", case_file: { checklist_json: {} },
    looks: [{ look_id: "look-progress", kind: "planned", intent: "Test the explanation", fork_json: { agent_runtime: true, execution_mode: "approved_helper", plan: { question: "Does composition explain the change?" } } }],
    executions: {}, hypotheses: [], suspects: [], confirmation_checks: [], aar_evidence: [],
    data_chat: { unlocked: true, successful_investigation_count: 2, turns: [] } };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: "Business rule", item_name: "Portfolio", table_name: "loans",
  } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  let polls = 0;
  let phase = "Running confirmation · Analysing with approved helper";
  let operation = "Hypothesis investigation";
  await page.route(`**/api/v3/rca/cases/${caseId}/progress`, (route) => {
    polls += 1;
    return route.fulfill({ json: { workflow_generation: 1, operation: { sequence_no: 10, operation, phase, status: "running" } } });
  });
  let release;
  let gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/v3/rca/looks/look-progress/run", async (route) => {
    await gate;
    bundle = { ...bundle, executions: { "look-progress": { execution_id: "exec-progress", look_id: "look-progress", status: "completed", summary_json: { found: true } } } };
    await route.fulfill({ json: bundle });
  });
  await page.goto(`/issues/${issueId}?rca_view=investigate`);
  await page.getByRole("button", { name: "Run approved helper", exact: true }).click();
  const actionPanel = page.getByTestId("rca-investigation-action");
  await expect(actionPanel.getByRole("button", { name: "Running investigation…", exact: true })).toBeDisabled();
  await expect(actionPanel.getByTestId("rca-progress")).toBeInViewport();
  await expect(page.getByTestId("rca-progress")).toHaveCount(1);
  await expect(page.getByTestId("rca-progress")).toContainText("Running confirmation");
  phase = "Interpreting confirmation";
  await expect(page.getByTestId("rca-progress")).toContainText("Interpreting confirmation");
  release();
  await expect(page.getByTestId("rca-progress")).toHaveCount(0);
  const completedAt = polls;
  await page.waitForTimeout(1800);
  expect(polls).toBe(completedAt);
  gate = new Promise((resolve) => { release = resolve; });
  operation = "Data chat";
  phase = "Writing answer";
  await page.route(`**/api/v3/rca/cases/${caseId}/data-chat`, async (route) => {
    await gate;
    await route.fulfill({ json: bundle });
  });
  await page.getByLabel("Ask about this data").fill("Explain the retained evidence");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("rca-chat-action").getByTestId("rca-progress")).toBeVisible();
  await expect(page.getByTestId("rca-progress")).toContainText("Writing answer");
  await page.getByRole("button", { name: "1. Intake", exact: true }).click();
  await expect(page.getByTestId("rca-progress")).toHaveCount(0);
  const navigatedAt = polls;
  await page.waitForTimeout(1800);
  expect(polls).toBe(navigatedAt);
  release();
});

test("RCA progress shows opening evidence during a delayed request and stops after failure", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const caseId = "rca-progress", issueId = "issue-progress";
  const bundle = { case_id: caseId, workflow_generation: 1, state: "intake", case_file: { checklist_json: {} },
    looks: [], executions: {}, hypotheses: [], suspects: [], confirmation_checks: [], aar_evidence: [] };
  await page.route(`**/api/v2/issues/${issueId}`, (route) => route.fulfill({ json: {
    issue_row_id: issueId, rca_case_id: caseId, workflow_version: "rca", test_name: "Business rule",
    item_name: "Portfolio", table_name: "loans", columns: ["amount"],
  } }));
  await page.route(`**/api/v3/issues/${issueId}/tags`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/rca/cases/${caseId}`, (route) => route.fulfill({ json: bundle }));
  let polls = 0;
  let generation = 1;
  await page.route(`**/api/v3/rca/cases/${caseId}/progress`, (route) => {
    polls += 1;
    return route.fulfill({ json: { case_id: caseId, workflow_generation: generation,
      operation: { sequence_no: 10, operation: "Initial review", phase: "Interpreting initial evidence", status: "running" },
      opening_result: { profile: { total_count: 100 }, null_share: 0.1 },
    } });
  });
  let release;
  let gate = new Promise((resolve) => { release = resolve; });
  await page.route(`**/api/v3/rca/cases/${caseId}/opening-look`, async (route) => {
    await gate;
    await route.fulfill({ status: 500, json: { detail: "Interpretation failed" } });
  });
  await page.goto(`/issues/${issueId}`);
  await page.getByRole("button", { name: "Start investigation", exact: true }).click();
  await expect(page).toHaveURL(/rca_view=initial-review/);
  await expect(page.getByTestId("rca-initial-review-content").getByTestId("rca-progress")).toBeVisible();
  await expect(page.getByTestId("rca-progress")).toContainText("Interpreting initial evidence");
  await page.getByText("View retained findings").click();
  await expect(page.getByTestId("rca-opening-preview")).toContainText("Physical missing: 10.00%");
  release();
  await expect(page.getByTestId("rca-progress")).toHaveCount(0);
  await expect(page.getByText(/500: Interpretation failed/)).toBeVisible();
  const stoppedAt = polls;
  await page.waitForTimeout(1800);
  expect(polls).toBe(stoppedAt);

  gate = new Promise((resolve) => { release = resolve; });
  await page.getByRole("button", { name: "Start initial review", exact: true }).click();
  await expect(page.getByTestId("rca-progress")).toContainText("Interpreting initial evidence");
  generation = 2;
  await expect(page.getByTestId("rca-progress")).toContainText("started afresh");
  await expect(page.getByTestId("rca-opening-preview")).toHaveCount(0);
  const resetAt = polls;
  await page.waitForTimeout(1800);
  expect(polls).toBe(resetAt);
  release();
});
