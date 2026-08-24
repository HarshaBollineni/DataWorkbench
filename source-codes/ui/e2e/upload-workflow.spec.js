import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_API_ORIGIN } from "./support/endpoints";
import { selectPopoverOption } from "./support/select";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "fixtures");
const API_ORIGIN = E2E_API_ORIGIN;

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function uploadFresh(page, alias, kindButton) {
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).nth(kindButton).click();
  await page.getByLabel("Alias").fill(alias);
  await expect(page.getByRole("button", { name: "Upload Files" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Finalize & Profile" })).toHaveCount(0);
  await page.locator('input[type="file"]').first().setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
  await page.getByLabel("Snapshot label").fill(`snapshot-${alias}`);
  if (kindButton === 1) {
    // Target is optional, but this helper selects one for downstream Test Lab journeys.
    await page.getByLabel("Target variable").selectOption({ index: 1 });
    await selectPopoverOption(page, "Use case");
    await selectPopoverOption(page, "Product");
    await page.getByLabel(/I confirm this target/).check();
  }
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByText(/Ready/)).toBeVisible();
  const step5 = page.getByTestId("upl-step-5");
  await expect(step5.getByRole("button", { name: "Saved and ready" })).toBeDisabled();
  await expect(step5.getByRole("link", { name: "Go to Test Lab" })).toBeVisible();
  await expect(page.getByTestId("completion-summary")).not.toHaveAttribute("open", "");
}

test("database upload profiles and remains available after reload", async ({ page }) => {
  const alias = `e2e-database-${Date.now()}`;
  await signIn(page);
  await uploadFresh(page, alias, 0);

  await page.getByRole("link", { name: "Test Lab", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
  const option = page.locator("select").first().locator("option").filter({ hasText: alias }).first();
  await expect(option).toBeAttached();
  await page.locator("select").first().selectOption(await option.getAttribute("value"));

  await page.reload();
  await page.getByRole("link", { name: "Data Inventory" }).click();
  await expect(page.getByText(new RegExp(`-${alias}$`))).toBeVisible();
});

test("a full replacement creates a new version on the same asset and retains prior snapshots", async ({ page, request }) => {
  const alias = `e2e-replacement-database-${Date.now()}`;
  await signIn(page);
  await uploadFresh(page, alias, 0);
  await page.waitForTimeout(1000);

  // This test retains the historical raw-port API calls intentionally. They
  // are category (a) under the validator's alternate-port setup.
  const itemsBefore = await (await request.get(`${API_ORIGIN}/api/v2/items`)).json();
  const original = itemsBefore.find((item) => item.name.endsWith(`-${alias}`));
  expect(original).toBeTruthy();

  await page.getByRole("button", { name: "Change kind" }).click();
  await page.getByRole("button", { name: "Add New" }).first().click();
  await page.getByRole("button", { name: "Existing" }).click();
  await page.getByRole("button", { name: original.name, exact: true }).click();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await page.getByText("Full replacement", { exact: true }).click();
  await expect(page.getByText(/This will supersede/)).toBeVisible();
  await page.getByLabel("Snapshot label").fill(`replacement-${Date.now()}`);
  await page.getByText("I understand this distinct full-replacement action.").locator("..").locator('input[type="checkbox"]').check();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByRole("heading", { name: "Completion summary" })).toBeVisible();

  const itemsAfter = await (await request.get(`${API_ORIGIN}/api/v2/items`)).json();
  const family = itemsAfter.filter((item) => item.dataset_family_id === original.dataset_family_id);
  expect(family.some((item) => item.item_id === original.item_id && item.snapshot_status === "superseded")).toBeTruthy();
  expect(family.some((item) => item.item_id !== original.item_id && item.version_no === 2 && item.snapshot_status === "active")).toBeTruthy();
});

test("dataset upload can proceed without a target after context confirmation", async ({ page, request }) => {
  const alias = `e2e-dataset-${Date.now()}`;
  await signIn(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).nth(1).click();
  await page.getByLabel("Alias").fill(alias);
  await page.locator('input[type="file"]').first().setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
  await expect(page.getByLabel("Target variable")).toBeVisible();
  await expect(page.getByLabel("Use case", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Product", { exact: true })).toBeVisible();
  await page.getByLabel("Target variable").selectOption("");
  await expect(page.getByLabel("Target variable")).toHaveValue("");
  await page.getByLabel("Snapshot label").fill(`snapshot-${alias}`);
  await expect(page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" })).toBeDisabled();
  await selectPopoverOption(page, "Use case");
  await selectPopoverOption(page, "Product");
  await page.getByLabel(/I confirm this target/).check();
  await expect(page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" })).toBeEnabled();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByText(/Ready/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Go to Test Lab" })).toBeVisible();
  const items = await (await request.get(`${API_ORIGIN}/api/v2/items`)).json();
  const saved = items.find((item) => item.name.endsWith(`-${alias}`));
  expect(saved.target_variable).toBeNull();
});

test("a source upload reaches Ready with the current one-file contract", async ({ page }) => {
  const alias = `e2e-source-${Date.now()}`;
  await signIn(page);
  await uploadFresh(page, alias, 1);
});

test("dictionary-backed quarter sourcing infers full calendar bounds and context", async ({ page }) => {
  const alias = `e2e-quarter-dictionary-${Date.now()}`;
  await signIn(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).nth(1).click();
  await page.getByLabel("Alias").fill(alias);
  await page.getByLabel("Time basis").selectOption("period");
  const files = page.locator('input[type="file"]');
  await files.nth(0).setInputFiles(path.join(fixtures, "quarterly-assessment.csv"));
  await files.nth(1).setInputFiles(path.join(fixtures, "quarterly-dictionary.csv"));
  await expect(page.getByText(/Detected dictionary headers are available below/)).toHaveCount(1);
  await page.getByText("Advanced parsing controls", { exact: true }).click();
  await page.getByLabel("Dictionary header for Column name").selectOption("Field Name");
  await page.getByLabel("Dictionary header for Column type").selectOption("Data Format");
  await page.getByLabel("Dictionary header for Role").selectOption("Requirement");
  await page.getByLabel("Dictionary header for Description").selectOption("Meaning");
  await page.getByLabel("Dictionary header for Comments").selectOption("Notes");
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();

  await expect(page.getByLabel("Start date")).toHaveValue("2006-01-01");
  await expect(page.getByLabel("End date")).toHaveValue("2007-12-31");
  await expect(page.getByLabel("Reporting period column (optional)")).toHaveValue("reporting_period");
  await expect(page.getByLabel("Target variable")).toHaveValue("default_flag");
  await expect(page.getByLabel("Use case", { exact: true })).toContainText("IFRS 9");
  await expect(page.getByLabel("Product", { exact: true })).toContainText("CRE");

  await page.getByLabel(/I confirm this target/).check();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByTestId("upl-step-5").getByRole("button", { name: "Saved and ready" })).toBeDisabled();
});

test("partially completed sourcing resumes from retained profiling", async ({ page }) => {
  const alias = `e2e-resume-${Date.now()}`;
  await signIn(page);
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).nth(1).click();
  await page.getByLabel("Alias").fill(alias);
  await page.locator('input[type="file"]').first().setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();

  await page.getByRole("link", { name: "Data Inventory" }).click();
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "View Existing" }).last().click();
  await page.getByRole("button", { name: new RegExp(`Continue sourcing .*${alias}`) }).click();
  await expect(page.getByRole("heading", { name: "Normalized column definitions" })).toBeVisible();

  await page.getByLabel("Snapshot label").fill(`resumed-${alias}`);
  await page.getByLabel("Target variable").selectOption("default_flag");
  await selectPopoverOption(page, "Use case", "IFRS 9");
  await selectPopoverOption(page, "Product", "CRE");
  await page.getByLabel(/I confirm this target/).check();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await expect(page.getByTestId("upl-step-5").getByRole("button", { name: "Saved and ready" })).toBeDisabled();
});

test("retired Galileo endpoints are unavailable while v2 remains available", async ({ request }) => {
  const legacy = await request.get(`${API_ORIGIN}/api/ingestion/catalog`);
  expect(legacy.status()).toBe(404);
  const overview = await request.get(`${API_ORIGIN}/api/v2/framework/overview`);
  expect(overview.ok()).toBeTruthy();
});

test("framework reference serves the 9-diagnostic register with honest gaps", async ({ request }) => {
  const areas = await (await request.get(`${API_ORIGIN}/api/v2/framework/areas`)).json();
  expect(areas.length).toBe(11);
  const themes = new Set(areas.map((area) => area.l1_theme));
  expect(themes.size).toBe(6);
  const gaps = areas.filter((area) => area.coverage_status === "gap");
  expect(gaps.length).toBe(5);
  for (const gap of gaps) expect(gap.coverage_reason).toBeTruthy();
  const overview = await (await request.get(`${API_ORIGIN}/api/v2/framework/overview`)).json();
  expect(overview.stage1.tests + overview.stage2.tests).toBeGreaterThanOrEqual(9);
});
