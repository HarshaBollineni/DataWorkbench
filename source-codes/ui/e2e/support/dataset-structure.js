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
  const review = page.getByTestId("upl-step-7");
  await expect(review).toBeVisible({ timeout: 30_000 });
  await expect(review.getByRole("heading", { name: "Cadence for the Date/Period candidate" })).toBeVisible({ timeout: 45_000 });
  for (const name of [/Entity candidate for/, /Date or Period candidate for/, /Row grain for/]) {
    const select = review.getByRole("combobox", { name }).first();
    if (await select.locator("option").count() > 1) await select.selectOption({ index: 1 });
  }
  const temporal = review.getByRole("combobox", { name: /Date or Period candidate for/ }).first();
  if (await temporal.inputValue()) {
    await expect(review.getByRole("heading", { name: "Observed", exact: true })).toBeVisible();
    await expect(review.getByRole("heading", { name: "Expected", exact: true })).toBeVisible();
  } else {
    await expect(review.getByText("Not applicable — no supported Date or Period column is available.")).toBeVisible();
  }
  const confirm = review.getByRole("button", { name: "Confirm dataset structure" });
  await expect(confirm).toBeVisible({ timeout: 30_000 });
  await checkAll(review.locator('input[type="checkbox"]'));
  await expect(review.getByText("Draft saved. These choices are not confirmed decisions.")).toBeVisible({ timeout: 30_000 });
  await expect(confirm).toBeEnabled({ timeout: 30_000 });
  await confirm.click();
  await expect(review.getByText("Dataset structure is confirmed.")).toBeVisible({ timeout: 30_000 });
  await expect(review.getByRole("button", { name: "Return to Data Sourcing home" })).toBeVisible();
  await review.getByRole("button", { name: "Open in Test Lab" }).click();
  await expect(page).toHaveURL(/\/test-lab\?item=[^&]+$/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
}

export async function saveStagedStructure(page) {
  const review = page.getByTestId("upl-step-5");
  await expect(review).toBeVisible({ timeout: 30_000 });
  const entity = review.getByRole("combobox", { name: /Entity identifier for/ }).first();
  await expect(entity).toBeVisible({ timeout: 30_000 });
  await entity.selectOption({ index: 1 });
  await review.getByRole("combobox", { name: /Date or Period feature for/ }).first().selectOption({ index: 1 });
  await review.getByRole("combobox", { name: /Row grain for/ }).first().selectOption({ index: 1 });
  const cadenceAction = review.getByRole("combobox", { name: /Expected cadence action for/ }).first();
  const canDeclareCadence = await cadenceAction.locator('option[value="confirm"]').count();
  await cadenceAction.selectOption(canDeclareCadence ? "confirm" : "mark_not_applicable");
  await checkAll(review.locator('input[type="checkbox"]'));
  await review.getByRole("button", { name: "Save structure review" }).click();
  await expect(review.getByText("Saved against current evidence")).toBeVisible();
}
