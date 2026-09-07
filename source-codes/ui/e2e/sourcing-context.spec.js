import { expect, test } from "@playwright/test";

const ready = {
  asset_id: "asset_ready", kind: "dataset", display_name: "DS0001-ready_asset",
  current_version_no: 1, lifecycle_status: "sourcing", active_snapshot_count: 1,
  snapshot_count: 1, last_upload_date: "2026-08-01T00:00:00Z", kind_count_label: "2 columns", selectable: true,
};
const requires = { ...ready, asset_id: "asset_reupload", display_name: "DS0002-reupload_asset", lifecycle_status: "requires_reupload", selectable: false };
const complete = { ...ready, asset_id: "asset_complete", display_name: "DS0003-complete_asset", lifecycle_status: "complete", selectable: false };

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function mockAssetData(page) {
  await page.route("**/api/v2/assets**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/next-id")) {
      await route.fulfill({ json: { kind: "dataset", scope: "asset_dataset", system_id: "DS0009" } });
      return;
    }
    await route.fulfill({ json: [ready, requires, complete] });
  });
  await page.route("**/api/v2/items**", async (route) => {
    await route.fulfill({ json: [{ item_id: "snapshot_ready", dataset_family_id: ready.asset_id, name: ready.display_name, ingest_status: "ready", snapshot_status: "active", kind: "dataset" }] });
  });
  await page.route("**/api/v2/items/*/diagnostics/board", async (route) => {
    await route.fulfill({ json: { cards: [], gap_areas: [], coverage: { executable: [], workflow_pending: [] } } });
  });
}

test("View Existing is a URL-stable searchable picker and context survives refresh/clear", async ({ page }) => {
  await signIn(page);
  await mockAssetData(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  const before = page.url();
  await page.getByRole("button", { name: "View Existing" }).last().click();
  expect(page.url()).toBe(before);
  await expect(page.getByText(ready.display_name)).toBeVisible();
  await expect(page.getByText(requires.display_name)).toHaveCount(0);
  await expect(page.getByText(complete.display_name)).toHaveCount(0);
  await page.getByRole("button", { name: ready.display_name }).click();
  await expect(page.getByTestId("workflow-context-bar")).toContainText("v1");
  await expect(page.getByTestId("workflow-context-bar")).toContainText("1 active snapshots");
  await page.reload();
  await expect(page.getByTestId("workflow-context-bar")).toContainText(ready.display_name);
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByTestId("workflow-context-bar")).toHaveCount(0);
});

test("sidebar navigation returns to the last Test Lab workspace", async ({ page }) => {
  await signIn(page);
  await mockAssetData(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "View Existing" }).last().click();
  await page.getByRole("button", { name: ready.display_name }).click();
  await expect(page).toHaveURL(/\/test-lab\?item=snapshot_ready$/);
  await expect(page.locator("select").first()).toHaveValue("snapshot_ready");
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("link", { name: "Test Lab", exact: true }).click();
  await expect(page).toHaveURL(/\/test-lab\?item=snapshot_ready$/);
  await expect(page.locator("select").first()).toHaveValue("snapshot_ready");
});

test("sidebar navigation remembers a Knowledge Base tab", async ({ page }) => {
  await signIn(page);
  await mockAssetData(page);
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await page.getByRole("button", { name: "Rules" }).click();
  await expect(page).toHaveURL(/\/knowledge-base\?tab=rules$/);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await expect(page).toHaveURL(/\/knowledge-base\?tab=rules$/);
  await expect(page.getByRole("button", { name: "Rules" })).toHaveClass(/bg-dq-purple/);
});

test("sidebar navigation remembers the active Data Sourcing screen", async ({ page }) => {
  await signIn(page);
  await mockAssetData(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Create New Dataset" }).click();
  await expect(page).toHaveURL(/\/data-sourcing\?new=dataset$/);
  await expect(page.getByText("STEP 1 — Sourcing data")).toBeVisible();
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await expect(page).toHaveURL(/\/data-sourcing\?new=dataset$/);
  await expect(page.getByText("STEP 1 — Sourcing data")).toBeVisible();
});

test("the same picker includes requires-reupload only in upload STEP 1(b)", async ({ page }) => {
  await signIn(page);
  await mockAssetData(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).last().click();
  await page.getByRole("button", { name: "Existing" }).click();
  await expect(page.getByText(requires.display_name)).toBeVisible();
  await expect(page.getByText("Needs re-upload")).toBeVisible();
});
