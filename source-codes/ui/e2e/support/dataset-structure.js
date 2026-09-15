import { expect } from "@playwright/test";

async function checkAll(locator) {
  for (let index = 0; index < await locator.count(); index += 1) {
    const checkbox = locator.nth(index);
    if (!await checkbox.isChecked()) await checkbox.check();
  }
}

// Verify the cadence presentation that follows the materialized candidates,
// acknowledge any explicit no-selection decisions, then confirm authority.
export async function confirmDatasetStructure(page) {
  const review = page.getByTestId("upl-step-6");
  await expect(review).toBeVisible({ timeout: 30_000 });
  await expect(review.getByRole("heading", { name: "Cadence for the Date/Period candidate" })).toBeVisible();
  const temporalOptions = review.getByRole("combobox", { name: /Date or Period candidate/ }).locator("option");
  if (await temporalOptions.count() > 1) {
    await expect(review.getByRole("heading", { name: "Observed", exact: true })).toBeVisible();
    await expect(review.getByRole("heading", { name: "Expected", exact: true })).toBeVisible();
  } else {
    await expect(review.getByText("Not applicable — no supported Date or Period column is available.")).toBeVisible();
  }
  const confirm = review.getByRole("button", { name: "Confirm dataset structure" });
  await expect(confirm).toBeVisible({ timeout: 30_000 });
  await checkAll(review.locator('input[type="checkbox"]'));
  await expect(review.getByText("Draft saved. These choices are not confirmed decisions.")).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await expect(review).toBeVisible();
  await expect(confirm).toBeEnabled({ timeout: 30_000 });
  await confirm.click();
  await expect(page).toHaveURL(/\/test-lab\?item=[^&]+$/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
}
