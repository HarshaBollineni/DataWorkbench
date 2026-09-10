import { expect, test } from "./support/test-fixture";

const reason = "CLTV measures current loan-to-value; no governed D08 role applies.";
const field = (column) => ({
  column, description: column === "CLTV" ? "Current Loan-to-Value (%)" : column,
  selected: false, review_required: true, confirmed_roles: [],
  binding_source: "unconfirmed_candidate",
  candidates: [{ role: "static_attributes", definition: "Stable attributes" }],
  adjudication: { status: "not_requested", attempts: [] },
});

test("per-feature and batch AI preserve explicit applicability, with reasons visible in results", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1800, height: 1100 });
  const manifest = {
    manifest_kind: "value_semantics", run_id: "vs-browser", table: "portfolio", tables: ["portfolio"],
    introduction_acknowledged: true, fields: [
      { ...field("DPD"), selected: true, review_required: false,
        confirmed_roles: ["arrears_measure"], binding_source: "deterministic_exact" },
      field("CLTV"), field("STATUS"), field("NOTE"),
    ],
    context: { selected: ["GENERAL"], suggestions: [] }, blockers: [], ready_to_run: false,
    role_catalog: [
      { role: "arrears_measure", definition: "Days past due or similar arrears measure" },
      { role: "static_attributes", definition: "Stable attributes" },
    ],
  };
  const requests = [];
  let releaseAi;
  const pendingAi = new Promise((resolve) => { releaseAi = resolve; });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    if (!new URL(route.request().url()).pathname.endsWith("/diagnostics/manifests/vs-browser")) {
      throw new Error(`Unexpected API request: ${route.request().url()}`);
    }
    if (route.request().method() === "PATCH") {
      const patch = route.request().postDataJSON();
      requests.push(patch);
      const row = manifest.fields.find((value) => value.column === patch.feature);
      if (patch.kind === "request_ai_role_review") {
        await pendingAi;
        const output = { decision: "NO_CANDIDATE_MATCH", reason, primary_role: null, secondary_roles: [] };
        row.adjudication = { status: "proposal_ready", output, attempts: [output] };
      } else if (patch.kind === "field_applicability") {
        row.selected = false;
        row.confirmed_roles = [];
        row.review_required = patch.value === "review";
        row.binding_source = patch.value === "review" ? "human_reopened" : "human_not_applicable";
        row.applicability = patch.value === "review" ? null : {
          status: "not_applicable", reason: patch.reason,
          confirmed_by: "reviewer", confirmed_at: "2026-09-07 12:00:00",
        };
      } else if (patch.kind === "role_binding") {
        row.confirmed_roles = patch.value;
        row.selected = true;
        row.review_required = false;
        row.binding_source = "human_confirmed";
        row.applicability = null;
      }
      await route.fulfill({ json: manifest });
    } else {
      await route.fulfill({ json: { run: { run_id: manifest.run_id, status: "draft" }, manifest, decisions: [] } });
    }
  });
  // Exercise the real shared scope gate and result components without creating live data or calling AI.
  await page.route("**/__value-semantics-test", async (route) => {
    const response = await route.fetch();
    const html = (await response.text()).replace("/src/main.jsx", "/e2e/fixtures/value-semantics-harness.jsx");
    await route.fulfill({ response, body: html });
  });
  await page.goto("/__value-semantics-test");
  await page.getByRole("button", { name: /3. Role bindings/ }).click();
  const cltv = page.getByTestId("role-field-CLTV");
  const status = page.getByTestId("role-field-STATUS");
  const dpd = page.getByTestId("role-field-DPD");
  await expect(page.getByRole("button", { name: /^Ask AI for (CLTV|STATUS|NOTE)$/ })).toHaveCount(3);
  await expect(dpd.getByRole("button", { name: "Ask AI for DPD", exact: true })).toHaveCount(0);
  await expect(dpd).toContainText("deterministic exact");
  await expect(dpd.getByRole("button", { name: "Reopen role review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ask AI for all pending fields (3)" })).toBeEnabled();
  await expect(cltv.getByLabel("Include CLTV")).not.toBeChecked();
  await expect(cltv.getByLabel("Include CLTV")).toBeEnabled();

  await cltv.getByRole("button", { name: "Ask AI for CLTV", exact: true }).click();
  await expect(page.getByRole("button", { name: /4. Rules & launch/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Ask AI for all pending fields (3)" })).toBeDisabled();
  releaseAi();
  await expect(cltv.getByTestId("ai-role-review-CLTV")).toContainText("No applicable Value Semantics role");
  await expect(cltv.getByTestId("ai-role-review-CLTV")).toContainText(reason);
  await expect(cltv.getByLabel("Include CLTV")).toBeEnabled(); // No-match has not confirmed applicability.
  await cltv.getByRole("button", { name: "Mark CLTV not applicable" }).click();
  await expect(cltv.getByLabel("Not applicable reason for CLTV")).toHaveValue(reason);
  await cltv.getByRole("button", { name: "Confirm not applicable" }).click();
  await expect(cltv.getByLabel("Include CLTV")).toBeDisabled();
  await expect(cltv.getByTestId("field-applicability-CLTV")).toContainText(reason);
  await expect(cltv.getByRole("button", { name: "Ask AI for CLTV", exact: true })).toHaveCount(0);

  // Manual not-applicable decisions require a reason and are excluded from the pending batch.
  await status.getByRole("button", { name: "Mark STATUS not applicable" }).click();
  await expect(status.getByRole("button", { name: "Confirm not applicable" })).toBeDisabled();
  await status.getByLabel("Not applicable reason for STATUS").fill("Operational status, not a governed semantic role.");
  await status.getByRole("button", { name: "Confirm not applicable" }).click();
  await page.getByRole("button", { name: "Ask AI for all pending fields (1)" }).click();
  await expect(page.getByRole("button", { name: "No pending AI reviews" })).toBeVisible();
  expect(requests.filter((patch) => patch.kind === "request_ai_role_review").map((patch) => patch.feature))
    .toEqual(["CLTV", "NOTE"]);

  await expect(cltv.getByRole("button", { name: "Ask AI for CLTV", exact: true })).toHaveCount(0);
  await expect(cltv.getByLabel("Include CLTV")).toBeDisabled();
  await cltv.getByRole("button", { name: "Reopen review" }).click();
  await expect(cltv.getByRole("button", { name: "Ask AI for CLTV", exact: true })).toHaveCount(0);
  await expect(cltv.getByTestId("ai-role-review-CLTV")).toContainText(reason);
  await expect(cltv.getByLabel("Include CLTV")).toBeEnabled();
  await expect(cltv.getByLabel("Include CLTV")).not.toBeChecked();
  await cltv.getByLabel("Role for CLTV").selectOption("static_attributes");
  await expect(cltv.getByLabel("Include CLTV")).toBeChecked();
  await expect(cltv.getByRole("button", { name: "Ask AI for CLTV", exact: true })).toHaveCount(0);
  await dpd.getByRole("button", { name: "Reopen role review" }).click();
  await expect(dpd.getByLabel("Include DPD")).not.toBeChecked();
  await expect(dpd.getByRole("button", { name: "Ask AI for DPD", exact: true })).toBeVisible();
  expect(requests.some((patch) => patch.kind === "field_applicability" && patch.reason === reason)).toBe(true);

  await page.evaluate((savedReason) => {
    window.__valueSemanticsResults = [{ metrics_json: {
      result_kind: "value_semantics_run_summary", rollup: {}, field_summary: [], artifacts: {},
      field_scope_decisions: [
        { column: "CLTV", status: "not_applicable", reason: savedReason, confirmed_by: "reviewer", confirmed_at: "2026-09-07 12:00:00" },
        { column: "NOTE", status: "excluded" },
      ],
    } }];
  }, reason);
  await page.getByRole("button", { name: "Show fixture results" }).click();
  const decisions = page.getByTestId("value-semantics-field-decisions");
  await expect(decisions).toContainText("Not applicable");
  await expect(decisions).toContainText("Excluded");
  await expect(decisions).toContainText(reason);
  await expect(decisions).toContainText("reviewer");
  await expect(page.getByRole("button", { name: "Download analysis report" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download text report" })).toBeVisible();
  expect(errors).toEqual([]);
});
