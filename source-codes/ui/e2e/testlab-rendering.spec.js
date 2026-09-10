import { expect, test } from "./support/test-fixture";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { confirmDatasetStructure } from "./support/dataset-structure";
import { selectPopoverOption } from "./support/select";

const fixtures = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

test.describe.configure({ timeout: 90_000 });

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function uploadReadyDataset(page, alias) {
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Create New Dataset" }).click();
  await page.getByLabel("Alias").fill(alias);
  await page.locator('input[type="file"]').first().setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
  await page.getByLabel("Snapshot label", { exact: true }).fill(`snapshot-${alias}`);
  await page.getByLabel("Target variable (optional)", { exact: true }).selectOption({ index: 1 });
  await selectPopoverOption(page, "Use case");
  await selectPopoverOption(page, "Product");
  await page.getByLabel(/I confirm this target/).check();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await confirmDatasetStructure(page);
}

async function chooseTestLabAsset(page, alias) {
  await page.getByRole("link", { name: "Test Lab", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
  const option = page.locator("select").first().locator("option").filter({ hasText: alias }).first();
  await expect(option).toBeAttached();
  await page.locator("select").first().selectOption(await option.getAttribute("value"));
}

test("Coverage board: workflow-pending card has zero run affordance", async ({ page }) => {
  const alias = `e2e-rendering-pending-${Date.now()}`;
  await signIn(page);
  await uploadReadyDataset(page, alias);
  await chooseTestLabAsset(page, alias);

  const cards = page.getByTestId("diagnostic-card");
  await expect(cards).toHaveCount(9, { timeout: 30_000 });
  const pending = page.locator('[data-testid="diagnostic-card"][data-chip-status="workflow_pending"]');
  await expect(pending).not.toHaveCount(0);
  for (let index = 0; index < await pending.count(); index += 1) {
    const card = pending.nth(index);
    await expect(card.getByText("workflow not yet defined")).toBeVisible();
    await expect(card.getByRole("button")).toHaveCount(0);
  }
  const nonReadyWithButton = page.locator('[data-testid="diagnostic-card"]:not([data-chip-status="ready"])').filter({ has: page.getByRole("button") });
  await expect(nonReadyWithButton).toHaveCount(0);
});

// The current Test Lab intentionally keeps the legacy Findings pane dormant.
// Preserve this rendering contract for re-enablement without pretending the
// component is reachable through today's Coverage-only navigation.
test.skip("FindingsPanel: candidate_flag renders as review, never as a violation", async ({ page }) => {
  const alias = `e2e-rendering-candidate-flag-${Date.now()}`;
  await signIn(page);
  await uploadReadyDataset(page, alias);
  let findingReviewState = "open";
  await page.route("**/diagnostics/results*", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      item_id: "synthetic-item", run: { run_id: "synthetic-run", status: "done" }, manifest: null, decisions: [],
      results: [{
        result_id: "synthetic-result-1", run_id: "synthetic-run", diagnostic_id: 2,
        entity_or_table: "facilities", decision_type: "candidate_flag", verdict: null,
        review_state: "open", na_reason: null,
        findings: [{
          finding_id: "synthetic-finding-1", result_id: "synthetic-result-1", rule_id: "CAND-01",
          severity: null, rule_text: "Statistically flagged candidate, not SME-confirmed.", outcome: null,
          review_state: findingReviewState,
        }],
      }],
    }),
  }));
  await chooseTestLabAsset(page, alias);
  await page.getByRole("button", { name: "Findings" }).click();
  const reviewCard = page.getByTestId("review-card");
  await expect(reviewCard).toBeVisible();
  await expect(reviewCard.getByTestId("review-badge")).toContainText("FOR REVIEW");
  await expect(reviewCard.getByText("VIOLATION")).toHaveCount(0);
  await expect(reviewCard.getByText("FAIL", { exact: true })).toHaveCount(0);
  await expect(reviewCard.locator(".bg-red-50")).toHaveCount(0);
  await expect(reviewCard.locator(".text-red-700")).toHaveCount(0);
  await expect(reviewCard.locator(".border-red-200")).toHaveCount(0);
  await expect(reviewCard.getByRole("button", { name: /Confirm as issue/ })).toBeVisible();
  const dismissOpen = reviewCard.getByRole("button", { name: /Dismiss/ }).first();
  await dismissOpen.click();
  const dismissConfirm = reviewCard.getByRole("button", { name: "Dismiss", exact: true });
  await expect(dismissConfirm).toBeDisabled();
  await reviewCard.getByPlaceholder("Reason (required)").fill("Reviewed and expected.");
  await expect(dismissConfirm).toBeEnabled();
  await page.route("**/diagnostics/findings/**/disposition", (route) => {
    findingReviewState = "dismissed";
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ finding_id: "synthetic-finding-1", action: "dismiss", review_state: "dismissed", reason: "Reviewed and expected.", ts: new Date().toISOString() }),
    });
  });
  await dismissConfirm.click();
  await expect(reviewCard.getByText("dismissed")).toBeVisible();
  await expect(reviewCard.locator(".bg-red-50")).toHaveCount(0);
});
