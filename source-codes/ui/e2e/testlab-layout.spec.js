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
    const rowCompleteness = area === "T2" && cardIndex === 0;
    const directionality = area === "T2" && cardIndex === 1;
    const diagnosticId = area === "T1" ? 2 : rowCompleteness ? 6 : directionality ? 11 : areaIndex * 10 + cardIndex + 1;
    const ready = area === "T1" || rowCompleteness || directionality || diagnosticId % 2 === 1;
    return {
      diagnostic_id: diagnosticId,
      area,
      area_name: areaName,
      name: area === "T1" ? "Single-feature target separation" : rowCompleteness
        ? "Row-completeness reconciliation" : directionality
          ? "Directional / monotonic consistency (segmented)" : `${areaName} diagnostic ${cardIndex + 1}`,
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

const rowCompletenessManifest = {
  manifest_kind: "row_completeness",
  run_id: "t2d06-browser",
  item_id: item.item_id,
  item_name: item.name,
  diagnostic_id: 6,
  status: "draft",
  table: "Data",
  table_options: [{ table: "Data", row_count: 30252, column_count: 43 }],
  available_columns: ["facility_id", "reporting_period", "segment"],
  roles: {
    facility_id: { column: "facility_id", source: "dsc_assist", reason: "Confirmed Dataset Structure default." },
    period: { column: "reporting_period", source: "dsc_assist", reason: "Confirmed Dataset Structure default." },
    segment: { column: "segment", source: "deterministic", reason: "Profiled segment candidate." },
  },
  configuration: {
    reporting_grain: {
      value: "quarterly", source: "confirmed Dataset Structure expected cadence", score: 1,
      reason: "Confirmed DSC expected cadence is every 1 quarter; mapped to quarterly reporting.",
    },
    continuity_floor: { value: 0.95, source: "default" },
  },
  dsc_assist: { state: "available", scope_confirmed: false, expected_cadence: { unit: "quarter", step: 1 } },
  role_verification: { status: "not_requested" },
  inference_disclosure: { llm_call_count: 0, deterministic_inferences: [] },
  source_artifact_references: [],
  kb: {
    configuration: { continuity_floor_label: "Required row coverage", continuity_floor_help: "Shared by the three coverage checks." },
    rules: Array.from({ length: 6 }, (_, index) => ({
      rule_id: `T2D6-0${index + 1}`,
      title: ["Valid row identifiers", "Reporting-period sequence", "Rows received by period", "Repeated facility-period rows", "Gaps in facility timelines", "Gaps by segment"][index],
      user_help: "Deterministic row-completeness check.",
      uses_continuity_floor: [2, 4, 5].includes(index),
    })),
  },
};

const directionalityManifest = {
  manifest_kind: "directional_monotonic_consistency",
  run_id: "t2d11-browser",
  item_id: item.item_id,
  item_name: item.name,
  diagnostic_id: 11,
  status: "draft",
  reference: {
    column: "default_flag", type: "binary", positive_class: "1",
    orientation: null, source: "confirmed Data Sourcing target",
  },
  reference_candidates: [{ column: "default_flag", is_saved_target: true }],
  analysis_sequence: { prior_completed_runs: 0, segment_analysis_available: false },
  prior_run_reuse: {},
  segment_column: null,
  segment_definition: null,
  segment_preview: null,
  segment_candidates: [],
  scope_features: [],
  ready_to_run: false,
  features: [
    {
      feature: "CURRENT_LTV", description: "Current indexed loan-to-value ratio",
      data_type: "float", role: "feature", numeric: true, scope_selected: false,
      classification_source: "KB_EXACT", canonical_feature: "current_ltv",
      expected_direction: "INCREASING", candidates: [],
    },
    {
      feature: "region", description: "Portfolio region", data_type: "string",
      role: "segment", numeric: false, scope_selected: false, candidates: [],
    },
  ],
};

const directionalityEvidence = {
  observed_direction: "INCREASING",
  evidence_strength: "MODERATE",
  status_reason: "Two of three directional evidence sources agree.",
  n_input: 30252,
  n_paired: 27026,
  n_dropped: 3226,
  n_special_value_dropped: 1860,
  event_value: "1",
  thresholds: {
    corr_floor: 0.2, regression_floor: 0.1,
    bin_range_floor_sd: 0.1, significance_level: 0.05,
  },
  spearman: { direction: "FLAT", value: 0.132, p_value: 0.001 },
  regression: { direction: "INCREASING", value: 1.564, p_value: 0.001, curve: [] },
  pearson: { direction: "INCREASING", value: 0.252, p_value: 0 },
  binned: {
    direction: "INCREASING", shape: "INCREASING", value: 0.3,
    bins: [
      { bin_number: 1, lower_bound: 50, upper_bound: 54.1, n_observations: 5455, feature_mean: 52, reference_mean: 0 },
      { bin_number: 2, lower_bound: 54.1, upper_bound: 58.2, n_observations: 5471, feature_mean: 56, reference_mean: 0.004 },
      { bin_number: 3, lower_bound: 58.2, upper_bound: 62.5, n_observations: 5315, feature_mean: 60, reference_mean: 0.008 },
      { bin_number: 4, lower_bound: 62.5, upper_bound: 66.6, n_observations: 5481, feature_mean: 64, reference_mean: 0.015 },
      { bin_number: 5, lower_bound: 66.6, upper_bound: 94.4, n_observations: 5304, feature_mean: 72, reference_mean: 0.031 },
    ],
  },
  chart_sample: [],
};

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function mockTestLabData(page, { featureTargetDraft = null, completedFeatureTarget = false, completedDirectionality = false, runningFeatureTarget = false, cardDelays = {} } = {}) {
  const requestCounts = { d06ManifestGets: 0, d11ManifestGets: 0 };
  let directionalityOrientation = null;
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
    if (path.includes(`/v2/items/${item.item_id}/diagnostics/board/cards/`)) {
      const diagnosticId = Number(path.split("/").at(-1));
      const card = cards.find((candidate) => candidate.diagnostic_id === diagnosticId);
      if (cardDelays[diagnosticId]) await new Promise((resolve) => setTimeout(resolve, cardDelays[diagnosticId]));
      await route.fulfill({ json: {
        ...card,
        open_draft: diagnosticId === 2 ? featureTargetDraft : null,
        ...(diagnosticId === 2 && completedFeatureTarget ? {
          run_count: 1,
          run_counts: { done: 1 },
          last_run: { run_id: "t1d02-complete", status: "done", finished_at: "2026-09-17T10:00:00Z", rollup: { features: 1 } },
        } : {}),
        ...(diagnosticId === 2 && runningFeatureTarget ? {
          run_count: 1,
          run_counts: { running: 1 },
          recent_runs: [{ run_id: "t1d02-running", status: "running", created_at: "2026-09-19T10:00:00Z" }],
        } : {}),
        ...(diagnosticId === 11 && completedDirectionality ? {
          run_count: 1,
          run_counts: { done: 1 },
          last_run: { run_id: "t2d11-complete", status: "done", finished_at: "2026-09-18T10:00:00Z", rollup: { features: 1 } },
        } : {}),
        loading: false,
      } });
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
      const diagnosticId = route.request().postDataJSON()?.diagnostic_id;
      await route.fulfill({ json: diagnosticId === 6 ? rowCompletenessManifest
        : diagnosticId === 11 ? directionalityManifest : featureTargetManifest });
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
    if (path.endsWith("/v2/diagnostics/manifests/t1d02-running")) {
      await route.fulfill({ json: {
        run: { run_id: "t1d02-running", item_id: item.item_id, diagnostic_id: 2, status: "running" },
        manifest: { ...featureTargetManifest, run_id: "t1d02-running", status: "running" },
        decisions: [],
      } });
      return;
    }
    if (path.endsWith("/v2/diagnostics/runs/t1d02-running/stream")) {
      const frames = [
        { phase: "start", agent: "feature_target_separation_engine", total: 4, thought: "Assessing two variables." },
        { phase: "progress", agent: "feature_target_separation_engine", done: 1, total: 4, task: "balance", feature: "balance", stage: "roc", thought: "balance ROC complete" },
        { phase: "progress", agent: "feature_target_separation_engine", done: 2, total: 4, task: "balance", feature: "balance", stage: "iv", thought: "balance IV complete" },
      ];
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache" },
        body: frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(""),
      });
      return;
    }
    if (path.endsWith("/v2/diagnostics/manifests/t2d06-browser")) {
      if (route.request().method() === "PATCH") {
        const value = route.request().postDataJSON()?.value;
        await route.fulfill({ json: {
          ...rowCompletenessManifest,
          configuration: {
            ...rowCompletenessManifest.configuration,
            continuity_floor: { value, source: "user-set (analyst, retained-in-audit)" },
          },
        } });
        return;
      }
      requestCounts.d06ManifestGets += 1;
      await route.fulfill({
        json: {
          run: { run_id: rowCompletenessManifest.run_id, item_id: item.item_id, diagnostic_id: 6, status: "draft" },
          manifest: rowCompletenessManifest,
          decisions: [],
        },
      });
      return;
    }
    if (path.endsWith("/v2/diagnostics/manifests/t2d11-browser")) {
      if (route.request().method() === "PATCH") {
        const patch = route.request().postDataJSON();
        if (patch?.kind === "reference_orientation") directionalityOrientation = patch.orientation;
        await route.fulfill({ json: {
          ...directionalityManifest,
          reference: { ...directionalityManifest.reference, orientation: directionalityOrientation },
        } });
        return;
      }
      requestCounts.d11ManifestGets += 1;
      await route.fulfill({ json: {
        run: { run_id: directionalityManifest.run_id, item_id: item.item_id, diagnostic_id: 11, status: "draft" },
        manifest: {
          ...directionalityManifest,
          reference: { ...directionalityManifest.reference, orientation: directionalityOrientation },
        },
        decisions: [],
      } });
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
      if (completedDirectionality && url.searchParams.get("run_id") === "t2d11-complete") {
        await route.fulfill({ json: {
          item_id: item.item_id,
          run: { run_id: "t2d11-complete", diagnostic_id: 11, status: "done" },
          manifest: directionalityManifest,
          decisions: [],
          results: [
            { result_id: "d11-summary-layout", metrics_json: { result_kind: "directionality_run_summary", rollup: { features_completed: 1, review_findings: 0, features_failed: 0 } }, findings: [] },
            { result_id: "d11-result-layout", metrics_json: {
              result_kind: "directionality_feature", feature: "CLTV", canonical_feature: "current_ltv",
              artifact_id: "art_directionality_layout", reference: { column: "default_12m" },
              analysis_view: { mode: "overall_only" }, evidence: directionalityEvidence,
              comparison: {
                expected_reference_direction: "INCREASING", observed_direction: "INCREASING",
                conclusion: "AGREEMENT", review_recommended: false,
              },
            }, findings: [] },
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
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/11/runs`)) {
      await route.fulfill({ json: { runs: [{ run_id: "t2d11-complete", status: "done", finished_at: "2026-09-18T10:00:00Z" }] } });
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
  return requestCounts;
}

test("coverage cards become usable progressively without the aggregate board request", async ({ page }) => {
  const slowDiagnosticId = cards.at(-1).diagnostic_id;
  let aggregateRequests = 0;
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith(`/v2/items/${item.item_id}/diagnostics/board`)) aggregateRequests += 1;
  });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page, { cardDelays: { [slowDiagnosticId]: 1500 } });
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const first = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="2"]');
  const last = page.locator(`[data-testid="diagnostic-card"][data-diagnostic-id="${slowDiagnosticId}"]`);
  await expect(first.getByRole("button", { name: "Launch workflow" })).toBeVisible({ timeout: 1000 });
  await expect(last).toHaveAttribute("aria-busy", "true");
  expect(aggregateRequests).toBe(0);
});

test("long diagnostic runs consistently show percent, completed, remaining, and current work", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await signIn(page);
  await mockTestLabData(page, { runningFeatureTarget: true });
  await page.goto(`/test-lab?item=${item.item_id}&diagnostic=2&run=t1d02-running&view=progress`);

  await expect(page.getByRole("heading", { name: "Run console" })).toBeVisible();
  const progress = page.getByRole("progressbar", { name: "Diagnostic run progress" });
  await expect(progress).toHaveAttribute("aria-valuenow", "50");
  await expect(page.getByTestId("diagnostic-completed-count")).toHaveText("2");
  await expect(page.getByTestId("diagnostic-remaining-count")).toHaveText("2");
  await expect(page.getByTestId("diagnostic-current-task")).toHaveText("balance");
  await expect(page.getByTestId("diagnostic-completed-work")).toContainText("Completed work (2) · Remaining (2)");
  await expect(page.getByTestId("diagnostic-completed-work").locator("..").getByText("IV / WOE binning", { exact: true })).toBeVisible();
});

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
  const firstDiagnosticArea = page.getByTestId("diagnostic-area").first();
  const firstDiagnosticAreaHandle = await firstDiagnosticArea.elementHandle();
  for (const overview of [repository, issues]) {
    expect(await overview.evaluate((element, diagnosticArea) => Boolean(
      element.compareDocumentPosition(diagnosticArea) & Node.DOCUMENT_POSITION_FOLLOWING
    ), firstDiagnosticAreaHandle)).toBeTruthy();
  }
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

test("T2-D06 launch presents the observed-span goal and consolidated setup", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  const requestCounts = await mockTestLabData(page);
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="6"]');
  await card.getByRole("button", { name: "Launch workflow" }).click();

  await expect(page.getByTestId("row-completeness-objective")).toContainText("continuity gaps");
  const guide = page.getByTestId("row-completeness-guide");
  for (const step of ["Confirm data structure & cadence", "Review six checks", "Run diagnostic", "Review results", "Raise issue"]) {
    await expect(guide.getByRole("listitem").filter({ hasText: step })).toBeVisible();
  }
  await expect(guide.getByText("Observed-span methodology")).toBeVisible();

  const scope = page.getByTestId("row-completeness-scope");
  await expect(scope.getByText("1. Confirm data structure and cadence")).toBeVisible();
  await expect(scope.getByText("Dataset Structure starting point")).toHaveCount(0);
  await expect(scope.getByLabel(/I reviewed the D06 scope/)).toHaveCount(0);
  await expect(scope.getByRole("button", { name: "Run diagnostic" })).toBeEnabled();
  await expect(scope.getByLabel("Reporting grain")).toHaveValue("quarterly");
  await expect(scope.getByText("Confirmed Dataset Structure · 100% confidence")).toBeVisible();
  await expect(scope.getByText(/Confirmed DSC expected cadence is every 1 quarter/)).toBeVisible();
  await expect(scope.getByLabel("Table to assess")).toHaveCount(0);
  await expect(scope.getByText("2. Review the six checks")).toBeVisible();
  await expect(scope.getByText("Configuration ready")).toHaveCount(0);
  await expect(scope.getByText(/immutable snapshot/)).toHaveCount(0);

  const manifestGetsBeforeFloorSave = requestCounts.d06ManifestGets;
  await scope.locator("#row-continuity-floor").fill("90");
  await expect(scope.getByText("Save the continuity floor change.")).toBeVisible();
  await scope.getByRole("button", { name: "Save" }).click();
  await expect(scope.getByText("Save the continuity floor change.")).toHaveCount(0);
  await expect(scope.getByText(/User-defined · Shared by the three coverage checks/)).toBeVisible();
  expect(requestCounts.d06ManifestGets).toBe(manifestGetsBeforeFloorSave);

  const about = page.getByTestId("row-completeness-about");
  await about.locator("summary").click();
  await expect(about.getByText(/Raise a governed issue from a confirmed finding/)).toBeVisible();
});

test("T2-D11 exposes feature selection before reference orientation and explains the evidence journey", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  const requestCounts = await mockTestLabData(page);
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="11"]');
  await card.getByRole("button", { name: "Launch workflow" }).click();

  await expect(page.getByTestId("directionality-objective")).toContainText("disagrees with the expected economic direction");
  const guide = page.getByTestId("directionality-guide");
  for (const step of ["Confirm reference", "Select features", "Resolve expected directions", "Review evidence", "Raise issue"]) {
    await expect(guide.getByRole("listitem").filter({ hasText: step })).toBeVisible();
  }

  const scope = page.getByTestId("directionality-scope-gate");
  await expect(scope.getByText("Select numeric features")).toBeVisible();
  const feature = scope.getByRole("button", { name: /CURRENT_LTV/ });
  await expect(feature).toBeVisible();
  await feature.click();

  const continueButton = scope.getByRole("button", { name: "Resolve expected directions" });
  await expect(continueButton).toBeDisabled();
  await expect(scope.getByText("Target risk direction is still required.")).toBeVisible();

  const manifestGetsBeforeOrientation = requestCounts.d11ManifestGets;
  await scope.getByRole("button", { name: "Higher value → Higher risk" }).click();
  await expect(continueButton).toBeEnabled();
  expect(requestCounts.d11ManifestGets).toBe(manifestGetsBeforeOrientation);

  const about = guide.getByTestId("directionality-about");
  await about.locator("summary").click();
  await expect(about.getByText(/Raise a governed issue from a confirmed contextual finding/)).toBeVisible();
  await expect(page.getByText(`${item.name} · review outcomes`, { exact: false })).toHaveCount(0);
});

test("T2-D11 evidence names each voting signal and presents readable bin columns", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await signIn(page);
  await mockTestLabData(page, { completedDirectionality: true });
  await page.goto(`/test-lab?asset=${item.asset_id}`);

  const card = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="11"]');
  await card.getByRole("button", { name: "View results" }).click();
  const results = page.getByTestId("directionality-results");
  await expect(results).toBeVisible();
  await results.getByRole("button", { name: "View evidence" }).click();

  const dialog = page.getByRole("dialog", { name: "CLTV directionality evidence" });
  await expect(dialog.getByText("2 of 3 voting signals meet the configured thresholds and point increasing.")).toBeVisible();
  await expect(dialog.getByText("Spearman is positive but does not vote because |ρ| 0.132 is below the 0.2 floor.", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Binned trend")).toBeVisible();
  await expect(dialog.getByText("Votes increasing", { exact: true })).toHaveCount(2);

  const headers = dialog.locator("table").first().locator("thead th");
  await expect(headers).toHaveText(["Bin number", "Bin label", "Pop count", "Target Avg"]);
  await expect(dialog.locator("table").first().locator("tbody tr").first()).toContainText("50–54.1");
  await expect(dialog.getByText("Two of three directional evidence sources agree.")).toHaveCount(0);
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
