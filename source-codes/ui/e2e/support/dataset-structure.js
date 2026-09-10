import { expect } from "@playwright/test";

async function checkAll(locator) {
  for (let index = 0; index < await locator.count(); index += 1) {
    const checkbox = locator.nth(index);
    if (!await checkbox.isChecked()) await checkbox.check();
  }
}

// The assessment fixture has no evidence-backed entity, temporal, or row-grain
// candidate.  Acknowledge those explicit no-selection decisions (and no
// intended cadence when applicable), then confirm the real DSC authority.
export async function confirmDatasetStructure(page) {
  const review = page.getByTestId("upl-step-6");
  await expect(review).toBeVisible({ timeout: 30_000 });
  const confirm = review.getByRole("button", { name: "Confirm dataset structure" });
  await expect(confirm).toBeVisible({ timeout: 30_000 });
  await checkAll(review.locator('input[type="checkbox"]'));
  await expect(review.getByText("Draft saved. These choices are not confirmed decisions.")).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await expect(review).toBeVisible();
  await expect(confirm).toBeEnabled({ timeout: 30_000 });
  await confirm.click();
  await expect(review.getByText("These selections are supported by the dataset evidence.")).toBeVisible();
}
