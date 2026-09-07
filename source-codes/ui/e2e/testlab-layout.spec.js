import { expect, test } from "@playwright/test";

const item = {
  item_id: "snapshot-layout",
  asset_id: "asset-layout",
  dataset_family_id: "asset-layout",
  name: "DS0099-layout-review",
  snapshot_label: "Layout review snapshot",
  ingest_status: "ready",
  snapshot_status: "active",
  kind: "dataset",
};

const areas = [
  ["T1", "Feature suitability", 1],
  ["T2", "Data integrity", 4],
  ["T3", "Representativeness", 1],
  ["T4", "Stability", 1],
  ["T5", "Model readiness", 1],
  ["T6", "Governance", 1],
];

const cards = areas.flatMap(([area, areaName, count], areaIndex) =>
  Array.from({ length: count }, (_, cardIndex) => {
    const diagnosticId = areaIndex * 10 + cardIndex + 1;
    const ready = diagnosticId % 2 === 1;
    return {
      diagnostic_id: diagnosticId,
      area,
      area_name: areaName,
      name: `${areaName} diagnostic ${cardIndex + 1}`,
      mode: "Hard",
      stage: "Both",
      det_stat: "Deterministic",
      decision_type: "verdict",
      kb_dependency: "None",
      chip: {
        status: ready ? "ready" : "workflow_pending",
        reason: ready ? "Scope is ready" : "workflow not yet defined",
      },
      can_run: ready,
      run_count: 0,
      run_counts: {},
      recent_runs: [],
      last_run: null,
      open_draft: null,
    };
  }),
);

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function mockTestLabData(page) {
  await page.route("**/api/v2/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/v2/items")) {
      await route.fulfill({ json: [item] });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/board`)) {
      await route.fulfill({
        json: {
          item_id: item.item_id,
          item: { use_case: "Credit risk" },
          cards,
          gap_areas: [],
          coverage: {
            executable: cards.filter((card) => card.can_run).map((card) => card.diagnostic_id),
            workflow_pending: cards.filter((card) => !card.can_run).map((card) => card.diagnostic_id),
          },
        },
      });
      return;
    }
    if (path.endsWith("/v2/analysis-artifacts/overview")) {
      await route.fulfill({
        json: {
          active_artifacts: 4,
          universal_artifacts: 2,
          diagnostic_local_artifacts: 2,
          represented_features: 12,
          integrity_warnings: 0,
          backfill_status: "complete",
        },
      });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/issues`)) {
      await route.fulfill({ json: { summary: { review_needed: 2, open: 1, in_review: 1, closed: 3 } } });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/analyses/catalog`)) {
      await route.fulfill({ json: { snapshot: { tables: [] }, capabilities: [] } });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/analyses/results`)) {
      await route.fulfill({ json: { runs: [] } });
      return;
    }
    await route.abort();
  });
}

test("selected Test Lab asset uses the compact overview and explicit test-area hierarchy", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page);
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
  await expect(page.getByText("Variable inventory of saved data", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Coverage", exact: true })).toHaveCount(0);

  const repository = page.locator('section[aria-labelledby="artifact-repository-title"]');
  const issues = page.locator('section[aria-labelledby="review-issues-title"]');
  await expect(repository.getByRole("link", { name: "Open repository" })).toBeVisible();
  await expect(issues.getByRole("link", { name: "View issues" })).toBeVisible();
  for (const label of ["Active", "Universal", "Diagnostic local", "Features", "Integrity warnings"])
    await expect(repository.getByText(label, { exact: true })).toBeVisible();
  for (const label of ["Review needed", "Open", "In review", "Closed"])
    await expect(issues.getByText(label, { exact: true })).toBeVisible();

  const repositoryBox = await repository.boundingBox();
  const issuesBox = await issues.boundingBox();
  expect(repositoryBox.height).toBeLessThan(130);
  expect(issuesBox.height).toBeLessThan(130);

  await expect(page.getByTestId("diagnostic-card")).toHaveCount(9);
  await expect(page.getByTestId("diagnostic-area")).toHaveCount(6);
  for (const [area, , count] of areas) {
    const group = page.locator(`[data-testid="diagnostic-area"][data-area-id="${area}"]`);
    await expect(group.getByText(area, { exact: true })).toBeVisible();
    await expect(group.getByTestId("diagnostic-card")).toHaveCount(count);
  }
});
