import { expect, test } from "./support/test-fixture";

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
    const diagnosticId = area === "T1" ? 2 : areaIndex * 10 + cardIndex + 1;
    const ready = area === "T1" || diagnosticId % 2 === 1;
    return {
      diagnostic_id: diagnosticId,
      area,
      area_name: areaName,
      name: area === "T1" ? "Single-feature target separation" : `${areaName} diagnostic ${cardIndex + 1}`,
      mode: "Hard",
      stage: "Both",
      det_stat: "Deterministic",
      decision_type: "verdict",
      kb_dependency: "None",
      chip: {
        status: ready ? "ready" : "workflow_pending",
        reason: ready ? "Scope is ready" : "workflow not yet defined",
      },
      can_run: area === "T1" || ready,
      run_count: 0,
      run_counts: {},
      recent_runs: [],
      last_run: null,
      open_draft: null,
    };
  }),
);

const featureTargetManifest = {
  manifest_kind: "feature_target_separation",
  run_id: "t1d02-browser",
  item_id: item.item_id,
  item_name: item.name,
  diagnostic_id: 2,
  diagnostic: { name: "Single-feature target separation" },
  status: "draft",
  target: {
    table: "portfolio",
    column: "default_flag",
    target_type: "binary",
    positive_class: "1",
    missing_target_action: "drop",
    source: "confirmed in Data Sourcing",
  },
  scope: {
    eligible_features: ["balance"],
    recommended_features: ["balance"],
    selected_features: ["balance"],
    feature_metadata: [{
      column: "balance", role: "Feature", classification: "numerical",
      recommendation_reason: "Recommended for this diagnostic",
    }],
  },
  execution: { completed_exact: {} },
  thresholds: Object.fromEntries([
    ["leakage_auc", 0.9], ["leakage_iv", 0.5], ["poor_auc", 0.6], ["poor_iv", 0.05],
    ["suspicious_auc", 0.9], ["suspicious_iv", 0.5], ["strong_auc", 0.7], ["strong_iv", 0.3],
    ["medium_auc", 0.55], ["medium_iv", 0.05],
  ].map(([key, value]) => [key, { value, source: "default" }])),
};

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function mockTestLabData(page, { featureTargetDraft = null, completedFeatureTarget = false } = {}) {
  await page.route("**/api/v2/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/v2/items")) {
      await route.fulfill({ json: [item] });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/board/summary`)) {
      await route.fulfill({
        json: {
          item_id: item.item_id,
          item: { use_case: "Credit risk" },
          cards: cards.map((card) => ({ ...card, loading: true })),
          gap_areas: [],
          coverage: { executable: [], workflow_pending: [] },
        },
      });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/board`)) {
      const boardCards = cards.map((card) => card.diagnostic_id === 2
        ? { ...card, open_draft: featureTargetDraft,
          ...(completedFeatureTarget ? {
            run_count: 1,
            run_counts: { done: 1 },
            last_run: { run_id: "t1d02-complete", status: "done", finished_at: "2026-09-17T10:00:00Z", rollup: { features: 1 } },
          } : {}) }
        : card);
      await route.fulfill({
        json: {
          item_id: item.item_id,
          item: { use_case: "Credit risk" },
          cards: boardCards,
          gap_areas: [],
          coverage: {
            executable: cards.filter((card) => card.can_run).map((card) => card.diagnostic_id),
            workflow_pending: cards.filter((card) => !card.can_run).map((card) => card.diagnostic_id),
          },
        },
      });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/manifest`)) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({ json: featureTargetManifest });
      return;
    }
    if (path.endsWith("/v2/diagnostics/manifests/t1d02-browser")) {
      // A new launch already returned this manifest. Keep this deliberately
      // slow to prove the scope does not refetch before its first render.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({
        json: {
          run: { run_id: featureTargetManifest.run_id, item_id: item.item_id, diagnostic_id: 2, status: "draft" },
          manifest: featureTargetManifest,
          decisions: [],
        },
      });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/results`)) {
      if (completedFeatureTarget && url.searchParams.get("run_id") === "t1d02-complete") {
        await route.fulfill({ json: {
          item_id: item.item_id,
          run: { run_id: "t1d02-complete", diagnostic_id: 2, status: "done" },
          manifest: featureTargetManifest,
          decisions: [],
          results: [
            { result_id: "summary-layout", metrics_json: { result_kind: "run_summary", target: { target: "default_flag" }, status: "complete", rollup: { features: 1, categories: { strong: 1 } } }, findings: [] },
            { result_id: "result-layout", metrics_json: { result_kind: "feature", feature: "balance", rows_evaluated: 100, missing_rows: 0, auc: 0.8, gini: 0.6, iv: 0.32, category: "strong" }, findings: [] },
          ],
        } });
        return;
      }
      // Deliberately slower than manifest creation: prior results must not
      // block entry into a newly prepared scope.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({ json: { results: [] } });
      return;
    }
    if (path.endsWith("/v2/diagnostics/results/result-layout")) {
      await route.fulfill({ json: {
        result_id: "result-layout",
        run_id: "t1d02-complete",
        metrics_json: {
          result_kind: "feature", feature: "balance", target: "default_flag",
          target_type: "binary", rows_evaluated: 100, missing_rows: 0,
          auc: 0.8, gini: 0.6, iv: 0.32, category: "strong",
          roc_detail: { feature_type: "numeric", leaves: [] },
          binning_detail: { feature_type: "numeric", coarse_bins: [] },
        },
      } });
      return;
    }
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/2/runs`)) {
      await route.fulfill({ json: { runs: [{ run_id: "t1d02-complete", status: "done", finished_at: "2026-09-17T10:00:00Z" }] } });
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

test("T1-D02 launch gives immediate feedback and explains the workflow", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page);
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="2"]');
  await card.getByRole("button", { name: "Launch workflow" }).click();
  await expect(card.getByRole("button", { name: "Preparing workflow…" })).toBeDisabled();

  await expect(page.getByTestId("feature-target-objective")).toContainText(
    "predict the target unusually well", { timeout: 1000 },
  );
  const guide = page.getByTestId("feature-target-guide");
  await expect(guide).toContainText("Target and analysis settings");
  await expect(guide.getByText("Review findings")).toBeVisible();

  const featureScope = page.getByTestId("feature-target-scope");
  await expect(featureScope).toBeVisible();
  await expect(featureScope.locator("details")).not.toHaveAttribute("open", "");
  const aboutDiagnostic = page.getByTestId("feature-target-about");
  await expect(aboutDiagnostic).not.toHaveAttribute("open", "");
  expect(await aboutDiagnostic.evaluate((about, scope) => Boolean(
    about.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING
  ), await featureScope.elementHandle())).toBeTruthy();
  const variableGrid = page.getByTestId("feature-target-variable-grid");
  await expect(variableGrid).toBeVisible();
  expect(await variableGrid.evaluate((element) => element.scrollHeight === element.clientHeight)).toBeTruthy();
  await aboutDiagnostic.locator("summary").click();
  await expect(aboutDiagnostic.getByText(/review signal, not proof of leakage/i)).toBeVisible();
});

test("T1-D02 resumed draft stays off the workflow page until its scope is ready", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page, {
    featureTargetDraft: {
      run_id: featureTargetManifest.run_id,
      last_saved_at: "2026-09-17T10:00:00Z",
      completed_steps: 1,
      total_steps: 2,
    },
  });
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="2"]');
  await card.getByRole("button", { name: "Launch workflow" }).click();
  await page.getByRole("button", { name: "Continue setup" }).click();
  await expect(page.getByRole("button", { name: "Preparing workflow…" })).toBeDisabled();
  await expect(page.getByText("Loading scope gate…")).toHaveCount(0);

  await expect(page.getByTestId("feature-target-scope")).toBeVisible();
  await expect(page.getByText("Loading scope gate…")).toHaveCount(0);
});

test("T1-D02 results load summaries before retained evidence", async ({ page }) => {
  let detailRequests = 0;
  let lightweightResultsRequested = false;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/v2/diagnostics/results/result-layout")) detailRequests += 1;
    if (url.pathname.endsWith(`/v2/items/${item.item_id}/diagnostics/results`)
        && url.searchParams.get("include_evidence") === "false") lightweightResultsRequested = true;
  });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page, { completedFeatureTarget: true });
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="2"]');
  await card.getByRole("button", { name: "View results" }).click();
  await expect(page.getByTestId("feature-target-results")).toBeVisible();
  expect(lightweightResultsRequested).toBeTruthy();
  expect(detailRequests).toBe(0);

  await page.getByRole("button", { name: "View evidence" }).click();
  await expect(page.getByRole("dialog", { name: /balance target-separation evidence/i })).toBeVisible();
  expect(detailRequests).toBe(1);
});
