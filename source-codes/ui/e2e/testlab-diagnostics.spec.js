import { expect, test } from "./support/test-fixture";
import { confirmDatasetStructure } from "./support/dataset-structure";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_API_BASE } from "./support/endpoints";
import { selectPopoverOption } from "./support/select";

// Phase 6 (0.4.0) — plan 6-T18: the full journey through the register-driven
// Test Lab: ingest -> publish KB (bound) -> Coverage board -> scope gate ->
// run -> findings (severity/evidence/pattern) -> issue -> report.
//
// The rule -> primitive binder (dq_diagnostics/engines/cross_field/
// binder.py) runs automatically inside kb.publish_rule (KB-08) — publishing
// a rule through the real KB API is itself the signature-exact binding
// trigger, no out-of-band step required. seedBoundCrossFieldRules below
// publishes every rule the table-aware parser extracts and then asks the
// API which ones landed 'bound', exactly mirroring what a real KB editor
// does through the UI.
//
// KB seeding uses the `request` fixture (upload/tag/submit/publish) rather
// than driving the KB upload UI a second time: knowledge-base.spec.js
// already proves that exact click-through flow against this same fixture
// (KB_cross_field_reference_2.pdf) end to end; re-driving it here would be
// redundant and slower for no additional coverage. The dataset ingestion
// half of the journey (Drop -> Ready) IS driven through the real UI below,
// since that is Test Lab's own precondition and not otherwise proven in
// this spec.

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "fixtures");

const API = E2E_API_BASE;
const USERNAME = "anirban";
const PASSWORD = "dqstudio";

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

async function apiLogin(request) {
  const res = await request.post(`${API}/login`, { data: { username: USERNAME, password: PASSWORD } });
  expect(res.ok(), await res.text()).toBeTruthy();
  const body = await res.json();
  return body.token;
}

async function seedBoundCrossFieldRules(request, token) {
  const authHeaders = { Authorization: `Bearer ${token}` };
  const fs = await import("node:fs");
  const buffer = fs.readFileSync(path.join(fixtures, "KB_cross_field_reference_2.pdf"));

  const uploadRes = await request.post(`${API}/v3/knowledge/documents`, {
    headers: authHeaders,
    multipart: { file: { name: "KB_cross_field_reference_2.pdf", mimeType: "application/pdf", buffer } },
  });
  expect(uploadRes.ok(), await uploadRes.text()).toBeTruthy();
  const uploaded = await uploadRes.json();
  const documentId = uploaded.document_id;
  const versionId = uploaded.version.version_id;

  // Tags the KB document with the IRB use case (organizational; mirrors the
  // real KB flow — kb.list_eligible_rules itself filters on category and
  // binding_status only, not on this tag, per kb.py:721-754).
  await request.post(`${API}/v3/knowledge/documents/${documentId}/tags`, {
    headers: authHeaders, data: { value_keys: ["use_case:irb"] },
  });

  const submitRes = await request.post(`${API}/v3/knowledge/versions/${versionId}/submit-for-review`, {
    headers: authHeaders, data: { category: "domain_fact" },
  });
  expect(submitRes.ok(), await submitRes.text()).toBeTruthy();
  const preview = await submitRes.json();
  expect(preview.rules.length).toBeGreaterThan(0);

  // Publish every extracted rule through the real API — publish_rule itself
  // attempts binding (KB-08); a hazard-flagged or unsupported-phrasing rule
  // simply stays reference-only/unparsed, exactly as a real KB editor would
  // see happen for the whole document at once.
  for (const rule of preview.rules) {
    const pubRes = await request.post(`${API}/v3/knowledge/rules/${rule.rule_id}/publish`, {
      headers: authHeaders, data: { category: "domain_fact" },
    });
    expect(pubRes.ok(), await pubRes.text()).toBeTruthy();
  }

  const boundRes = await request.get(
    `${API}/v3/knowledge/rules?lifecycle_state=published&binding_status=bound`, { headers: authHeaders });
  expect(boundRes.ok(), await boundRes.text()).toBeTruthy();
  const bound = (await boundRes.json()).filter((r) => r.version_id === versionId);
  expect(bound.length, "publishing the real S9 fixture must yield at least one bound rule").toBeGreaterThan(0);
  const byType = {};
  for (const r of bound) byType[r.rule_type] = (byType[r.rule_type] || 0) + 1;
  return { versionId, boundCount: bound.length, byType };
}

async function findItemIdByName(request, token, name) {
  const res = await request.get(`${API}/v2/items`, { headers: { Authorization: `Bearer ${token}` } });
  expect(res.ok(), await res.text()).toBeTruthy();
  const rows = await res.json();
  const row = rows.find((r) => r.name.endsWith(`-${name}`));
  expect(row, `item named ${name} not found in /v2/items`).toBeTruthy();
  return row.item_id;
}

test.describe("Test Lab diagnostics — the register-driven journey (plan 6-T18)", () => {
  test("ingest -> publish bound KB rules -> board -> scope gate -> run -> retained result/report", async ({ page, request }) => {
    test.setTimeout(300_000);
    const stamp = Date.now();
    const itemName = `e2e-cross-field-${stamp}`;

    const token = await apiLogin(request);
    const seed = await seedBoundCrossFieldRules(request, token);
    expect(seed.boundCount).toBeGreaterThan(0);

    // --- 1. Ingest (Drop -> Ready) via the real UI, tagged so an eligible
    // use-case scope exists (ING-01/02: use case asked once, inline). ------
    await signIn(page);
    await page.getByRole("link", { name: "Data Sourcing" }).click();
    await page.getByRole("button", { name: "Create New Dataset" }).click();
    await page.getByLabel("Alias").fill(itemName);
    const inputs = page.locator('input[type="file"]');
    await inputs.nth(0).setInputFiles(path.join(fixtures, "assessment.csv"));
    await page.getByRole("button", { name: "Start sourcing" }).click();
    await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
    await page.getByLabel("Snapshot label", { exact: true }).fill(`snapshot-${itemName}`);
    await page.getByLabel("Target variable (optional)", { exact: true }).selectOption("default_flag");
    await selectPopoverOption(page, "Use case");
    await selectPopoverOption(page, "Product");
    await page.getByLabel(/I confirm this target/).check();
    await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
    await confirmDatasetStructure(page);

    const itemId = await findItemIdByName(request, token, itemName);

    // --- 2. Coverage board -------------------------------------------------
    await page.getByRole("link", { name: "Test Lab", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
    const option = page.locator("select").first().locator("option").filter({ hasText: itemName }).first();
    await expect(option).toBeAttached();
    await page.locator("select").first().selectOption(await option.getAttribute("value"));

    const cards = page.getByTestId("diagnostic-card");
    // Generous timeout: the coverage board's first fetch is the run's first
    // real backend round trip after ingestion, on a shared dev server (see
    // playwright.config.js's note on serial-worker resource contention).
    await expect(cards).toHaveCount(9, { timeout: 30_000 }); // D-17 — exactly 9 register rows, always.

    const card4 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="4"]');
    await expect(card4).toBeVisible();
    await expect(card4.getByText("Cross-field business rule")).toBeVisible();
    await expect(card4.getByText("#4")).toBeVisible();
    // The 8-field anatomy: name+id, mode, stage, det/stat, decision type, KB
    // dependency, status chip, (last run only once one exists).
    await expect(card4.getByText(/hard/i)).toBeVisible();
    await expect(card4.getByText(/Stage: Both/)).toBeVisible();
    await expect(card4.getByText(/Decision: verdict/)).toBeVisible();
    await expect(card4.getByText(/KB dependency:/)).toBeVisible();

    // #4 remains visible but cannot be launched while refinement is underway.
    await expect(card4.locator('[data-testid="chip-status"][data-status="workflow_pending"]')).toBeVisible();
    await expect(card4.getByText("workflow not yet defined")).toBeVisible();
    await expect(card4.getByRole("button", { name: "Launch workflow" })).toHaveCount(0);

    // Five disabled or not-yet-implemented cards are workflow_pending and have ZERO run
    // affordance: no button element at all inside it (not a disabled one).
    const pendingCards = page.locator('[data-testid="diagnostic-card"][data-chip-status="workflow_pending"]');
    await expect(pendingCards).not.toHaveCount(0);
    const pendingCount = await pendingCards.count();
    for (let i = 0; i < pendingCount; i++) {
      const card = pendingCards.nth(i);
      await expect(card).toHaveAttribute("data-chip-status", "workflow_pending");
      await expect(card.getByText("workflow not yet defined")).toBeVisible(); // FWK-17 exact refusal text
      await expect(card.getByRole("button")).toHaveCount(0);
    }

    // The area-level GAP strip is present and reasoned (FWK-14), rendered as
    // text lines, never as cards.
    await expect(page.getByText(/GAP by design/)).toBeVisible();

    // Diagnostic 6 uses its dedicated configuration surface inside the
    // existing Test Lab journey; segment remains optional.
    const card6 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="6"]');
    await expect(card6.locator('[data-testid="chip-status"][data-status="ready"]')).toBeVisible();
    await card6.getByRole("button", { name: "Launch workflow" }).click();
    const rowScope = page.getByTestId("row-completeness-scope");
    await expect(rowScope).toBeVisible();
    await expect(rowScope.getByLabel("Table to assess")).toBeVisible();
    await expect(rowScope.getByLabel("Facility identifier")).toBeVisible();
    await expect(rowScope.getByLabel("Reporting period")).toBeVisible();
    await expect(rowScope.getByLabel("Segment")).toBeVisible();
    await expect(rowScope.getByLabel("Reporting grain")).toBeVisible();
    await expect(rowScope.locator("#row-continuity-floor")).toHaveValue("95");
    await expect(rowScope.getByText("T2D6-06 · Segment-period row coverage")).toBeVisible();
    await expect(rowScope.getByRole("button", { name: "Request advisory review" })).toBeVisible();
    const dscScopeConfirmation = rowScope.getByLabel(/I reviewed the D06 scope/);
    await dscScopeConfirmation.click();
    await expect(dscScopeConfirmation).toBeChecked();
    await expect(rowScope.getByText("D06 scope confirmation recorded")).toBeVisible();
    await expect(rowScope.getByRole("button", { name: "Run diagnostic" })).toBeEnabled();
    await rowScope.getByRole("button", { name: "Run diagnostic" }).click();
    await expect(page.getByText("Run console")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("row-completeness-results")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download analysis report" })).toBeVisible();
    await expect(page.getByText("LLM and inference disclosure")).toBeVisible();
    await page.getByRole("button", { name: "Back to Test Lab" }).click();

    // --- 3. Coverage-honest summary ----------------------------------------
    const coverageRes = await request.get(`${API}/v2/items/${itemId}/diagnostics/coverage-summary`,
      { headers: { Authorization: `Bearer ${token}` } });
    expect(coverageRes.ok(), await coverageRes.text()).toBeTruthy();
    const coverage = await coverageRes.json();
    expect(coverage).not.toHaveProperty("health_score");

    // --- 4. Diagnostic #14 PSI: existing API family + explicit UI dispatch --
    const launchCard14 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="14"]');
    await launchCard14.getByRole("button", { name: "Launch workflow" }).click();
    await expect(page.getByTestId("psi-scope")).toBeVisible();
    const psiScope = page.getByTestId("psi-scope");
    await expect(psiScope.getByRole("button", { name: "Split this snapshot" })).toBeVisible();
    await expect(psiScope.getByRole("button", { name: "Compare two snapshots" })).toBeVisible();
    await expect(psiScope.getByText("Select variables for PSI")).toBeVisible();
    await expect(psiScope.getByText("Save at least one selected variable to prepare bins.")).toBeVisible();
    await expect(page.getByTestId("psi-binning-workspace")).toHaveCount(0);
    await page.getByRole("button", { name: "Back to Test Lab" }).click();

    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const psiManifestRes = await request.post(`${API}/v2/items/${itemId}/diagnostics/manifest`, {
      headers, data: { diagnostic_id: 14, start_afresh: true },
    });
    expect(psiManifestRes.ok(), await psiManifestRes.text()).toBeTruthy();
    const psiManifest = await psiManifestRes.json();
    expect(psiManifest.manifest_kind).toBe("population_stability_index");
    const psiRunId = psiManifest.run_id;
    for (const data of [
      { kind: "population_split", feature: "default_flag", value: { operator: "=", value: 0 }, null_policy: "exclude" },
      { kind: "target_choice", value: { mode: "target_free" } },
      { kind: "scope_selection", features: ["pd"] },
      { kind: "binning_source_choice", value: { mode: "generate_new" } },
    ]) {
      const response = await request.patch(`${API}/v2/diagnostics/manifests/${psiRunId}`, { headers, data });
      expect(response.ok(), await response.text()).toBeTruthy();
    }
    const draftRes = await request.post(`${API}/v2/diagnostics/manifests/${psiRunId}/psi-bins/pd/draft`, { headers });
    expect(draftRes.ok(), await draftRes.text()).toBeTruthy();
    const draft = await draftRes.json();
    expect(draft.payload.governance_state).toBe("draft");
    const freezeRes = await request.post(`${API}/v2/diagnostics/manifests/${psiRunId}/psi-bins/pd/freeze`, {
      headers, data: { draft_artifact_id: draft.artifact.artifact_id },
    });
    expect(freezeRes.ok(), await freezeRes.text()).toBeTruthy();
    const psiRunRes = await request.post(`${API}/v2/diagnostics/manifests/${psiRunId}/run`, {
      headers, data: { stream: false },
    });
    expect(psiRunRes.ok(), await psiRunRes.text()).toBeTruthy();
    const psiResultsRes = await request.get(`${API}/v2/items/${itemId}/diagnostics/results?run_id=${psiRunId}`, { headers });
    const psiResults = await psiResultsRes.json();
    const psiFeature = psiResults.results.find((entry) => entry.metrics_json?.result_kind === "psi_feature");
    expect(psiFeature.decision_type).toBe("contextual");
    expect(psiFeature.verdict).toBeNull();
    const postPsiIssues = await (await request.get(`${API}/v2/items/${itemId}/issues`, { headers })).json();
    expect((postPsiIssues.issues || []).filter((issue) => issue.diagnostic_id === 14)).toHaveLength(0);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
    const psiOption = page.locator("select").first().locator("option").filter({ hasText: itemName }).first();
    await expect(psiOption).toBeAttached();
    const itemSelector = page.locator("select").first();
    const itemValue = await psiOption.getAttribute("value");
    if (await itemSelector.inputValue() !== itemValue) await itemSelector.selectOption(itemValue);
    const card14 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="14"]');
    await expect(card14.getByRole("button", { name: "View results" })).toBeVisible({ timeout: 45_000 });
    await expect(card14.getByRole("button", { name: "Re-run" })).toBeVisible();
    await card14.getByRole("button", { name: "View results" }).click();
    await expect(page.getByTestId("psi-results")).toBeVisible();
    await expect(page.getByText("Contextual review — no automatic pass/fail")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download analysis report" })).toBeVisible();
    await page.getByRole("button", { name: "View evidence" }).first().click();
    const psiEvidence = page.getByRole("dialog", { name: /population stability evidence/ });
    await expect(psiEvidence.getByText("Issue decision", { exact: false })).toBeVisible();
    await expect(psiEvidence.getByText("Baseline feature profile", { exact: true })).toBeVisible();
    await expect(psiEvidence.getByText("Baseline-to-Current bin contributions", { exact: true })).toBeVisible();
    await psiEvidence.getByRole("button", { name: "Close" }).click();
    await expect(page.getByText("VIOLATION", { exact: true })).toHaveCount(0);
  });
});
