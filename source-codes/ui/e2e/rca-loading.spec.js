import { expect, test } from "./support/test-fixture";

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

function issue(id) {
  return { issue_row_id: id, rca_case_id: `rca-${id}`, workflow_version: "rca",
    test_name: `Check ${id}`, item_name: "Portfolio", table_name: "loans", columns: ["amount"] };
}

function bundle(id) {
  return { case_id: `rca-${id}`, state: "intake", workflow_generation: 1,
    case_file: { checklist_json: {} }, looks: [], executions: {}, hypotheses: [],
    suspects: [], confirmation_checks: [], aar_evidence: [] };
}

test("RCA launch overlaps module, issue and tags and deduplicates StrictMode requests", async ({ page }) => {
  await signIn(page);
  const counts = { me: 0, issue: 0, tags: 0, case: 0, module: 0 };
  let releaseIssue;
  const heldIssue = new Promise((resolve) => { releaseIssue = resolve; });
  await page.route("**/api/me", (route) => { counts.me++; return route.continue(); });
  await page.route("**/src/features/rca/components/RcaCase.jsx*", (route) => { counts.module++; return route.continue(); });
  await page.route("**/api/v2/issues/launch", async (route) => {
    counts.issue++;
    await heldIssue;
    await route.fulfill({ json: issue("launch") });
  });
  await page.route("**/api/v3/issues/launch/tags", (route) => { counts.tags++; return route.fulfill({ json: [] }); });
  await page.route("**/api/v3/rca/cases/rca-launch", (route) => { counts.case++; return route.fulfill({ json: bundle("launch") }); });
  await page.goto("/issues/launch?return=");
  // Both requests and the module start while the issue response is still held.
  await expect.poll(() => counts.issue).toBe(1);
  await expect.poll(() => counts.tags).toBe(1);
  await expect.poll(() => counts.module).toBe(1);
  expect(counts.case).toBe(0);
  releaseIssue();
  await expect(page.getByTestId("rca-intake-evidence")).toBeVisible();
  expect(counts).toEqual({ me: 1, issue: 1, tags: 1, case: 1, module: 1 });
  await page.reload();
  await expect(page.getByTestId("rca-intake-evidence")).toBeVisible();
  expect(counts.issue).toBe(2);
  expect(counts.case).toBe(2); // A new mount performs fresh reads.
});

test("RCA launch ignores an old issue response and retries failed case reads on reload", async ({ page }) => {
  await signIn(page);
  let releaseOld;
  const oldResponse = new Promise((resolve) => { releaseOld = resolve; });
  let oldRequested = false;
  let oldCaseReads = 0;
  let failCase = true;
  await page.route("**/api/v2/issues/old", async (route) => {
    oldRequested = true;
    await oldResponse;
    await route.fulfill({ json: issue("old") });
  });
  await page.route("**/api/v2/issues/new", (route) => route.fulfill({ json: issue("new") }));
  await page.route("**/api/v3/issues/*/tags", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v3/rca/cases/rca-old", (route) => { oldCaseReads++; return route.fulfill({ json: bundle("old") }); });
  await page.route("**/api/v3/rca/cases/rca-new", (route) => route.fulfill(failCase
    ? { status: 500, json: { detail: "Case temporarily unavailable" } } : { json: bundle("new") }));
  await page.goto("/issues/old");
  await expect.poll(() => oldRequested).toBe(true);
  await page.evaluate(() => { history.pushState({}, "", "/issues/new"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByText("500: Case temporarily unavailable", { exact: true })).toBeVisible();
  releaseOld();
  failCase = false;
  await page.reload();
  await expect(page.getByTestId("rca-intake-evidence")).toBeVisible();
  await expect(page.getByText("Check new", { exact: true }).first()).toBeVisible();
  expect(oldCaseReads).toBe(0);
});
