import { expect, test } from "./support/test-fixture";

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

test("artifact repository tabs retain schema, details, lineage, and run behavior", async ({ page }) => {
  await signIn(page);
  await page.route("**/v2/analysis-artifacts**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body;
    if (path.endsWith("/overview")) {
      body = { active_artifacts: 2, retained_runs: 1, backfill_status: "complete" };
    } else if (path.endsWith("/runs")) {
      body = { runs: [{ run: { run_id: "run-1", capability_id: "missingness", status: "done", finished_at: "2026-08-19" } }] };
    } else if (path.endsWith("/artifact-1/payload")) {
      body = { payload: { feature: "income", score: 0.91, top_k: { high: 12 } } };
    } else if (path.endsWith("/artifact-1/lineage")) {
      body = { ancestors: [], dependants: [] };
    } else if (path.endsWith("/analysis-artifacts") && url.searchParams.get("artifact_type") === "column_profile") {
      body = { total: 1, artifacts: [{ artifact_id: "profile-1", feature: "income", summary: { fields: { data_type: "float", role: "feature", classification: "continuous", description: "Annual income", special_values: [] }, metrics: [{ metric_key: "distinct_count", value: 25 }] } }] };
    } else if (path.endsWith("/analysis-artifacts")) {
      body = { total: 1, artifacts: [{ artifact_id: "artifact-1", artifact_type: "feature_profile", feature: "income", scope: "local", snapshot_id: "snapshot-1", status: "active", payload_media_type: "application/json", summary: { metrics: [{ label: "IV", value: 0.42 }] } }] };
    } else {
      return route.fallback();
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/test-lab/artifacts?item=snapshot-1&asset=asset-1");
  await expect(page.getByRole("heading", { name: "Analytics Artifact Repository" })).toBeVisible();
  await expect(page.getByText("active artifacts")).toBeVisible();

  await page.getByRole("button", { name: "Saved Schema" }).click();
  await expect(page.getByText("Annual income")).toBeVisible();

  await page.getByRole("button", { name: "Artifacts", exact: true }).click();
  await expect(page.getByText("feature_profile")).toBeVisible();
  await page.getByRole("button", { name: "Details" }).click();
  await expect(page.getByText("0.91")).toBeVisible();
  await page.getByRole("button", { name: "Lineage", exact: true }).click();
  await expect(page.getByText("feature_profile · artifact-1")).toBeVisible();

  await page.getByRole("button", { name: "Analytical Runs" }).click();
  await expect(page.getByText("missingness · done")).toBeVisible();
});
