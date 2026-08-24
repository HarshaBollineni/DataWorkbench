import { expect, test } from "@playwright/test";
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
    test.setTimeout(150_000);
    const stamp = Date.now();
    const itemName = `e2e-cross-field-${stamp}`;

    const token = await apiLogin(request);
    const seed = await seedBoundCrossFieldRules(request, token);
    expect(seed.boundCount).toBeGreaterThan(0);

    // --- 1. Ingest (Drop -> Ready) via the real UI, tagged so an eligible
    // use-case scope exists (ING-01/02: use case asked once, inline). ------
    await signIn(page);
    await page.getByRole("link", { name: "Data Sourcing" }).click();
    await page.getByRole("button", { name: "Add New" }).last().click();
    await page.getByLabel("Alias").fill(itemName);
    const inputs = page.locator('input[type="file"]');
    await inputs.nth(0).setInputFiles(path.join(fixtures, "assessment.csv"));
    await page.getByRole("button", { name: "Start sourcing" }).click();
    await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
    await page.getByLabel("Snapshot label", { exact: true }).fill(`snapshot-${itemName}`);
    await page.getByLabel("Target variable (optional)", { exact: true }).selectOption({ index: 1 });
    await selectPopoverOption(page, "Use case");
    await selectPopoverOption(page, "Product");
    await page.getByLabel(/I confirm this target/).check();
    await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
    await expect(page.getByText(/Ready/)).toBeVisible();

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

    // A published + bound in-scope rule set exists now, so #4 reads ready.
    await expect(card4.locator('[data-testid="chip-status"][data-status="ready"]')).toBeVisible();
    await expect(card4.getByRole("button", { name: "Launch workflow" })).toBeVisible();

    // Five not-yet-implemented cards are workflow_pending and have ZERO run
    // affordance: no button element at all inside it (not a disabled one).
    const pendingCards = page.locator('[data-testid="diagnostic-card"][data-chip-status="workflow_pending"]');
    await expect(pendingCards).toHaveCount(5);
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
    await expect(rowScope.getByLabel("Minimum continuity coverage")).toHaveValue("95");
    await expect(rowScope.getByText("T2D6-06 · Gaps by segment")).toBeVisible();
    await expect(rowScope.getByRole("button", { name: "Request advisory review" })).toBeVisible();
    await expect(rowScope.getByRole("button", { name: "Run diagnostic" })).toBeEnabled();
    await rowScope.getByRole("button", { name: "Run diagnostic" }).click();
    await expect(page.getByText("Run console")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("row-completeness-results")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download external PDF" })).toBeVisible();
    await expect(page.getByText("LLM and inference disclosure")).toBeVisible();
    await page.getByRole("button", { name: "Back to Test Lab" }).click();

    // --- 3. Scope gate -------------------------------------------------
    await card4.getByRole("button", { name: "Launch workflow" }).click();
    await expect(page.getByText("Cross-field business rule · scope gate")).toBeVisible();
    await expect(page.getByText(/Rules in scope \(\d+\)/)).toBeVisible();
    await expect(page.getByText(/Roles resolved: \d+\/\d+/)).toBeVisible();
    await expect(page.getByText("Thresholds / parameters")).toBeVisible();
    await expect(page.getByText("Scope preview")).toBeVisible();
    // Sources are shown, not bare numbers (design/default vs user-set).
    await expect(page.getByText("default").first()).toBeVisible();

    // --- 4. Run, watching SSE progress ----------------------------------
    // The run console appears while the SSE stream is open (start/progress
    // captions via AgentConsole). The current workflow returns to Coverage
    // on completion and exposes the retained run from its diagnostic card.
    await page.getByRole("button", { name: "Run diagnostic" }).click();
    await expect(page.getByText("Run console")).toBeVisible();

    // --- 5. Retained result ------------------------------------------------
    await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Back to Test Lab" }).click();
    await expect(page.getByRole("heading", { name: "Test Lab" })).toBeVisible();
    await expect(card4.getByRole("button", { name: "View results" })).toBeVisible();
    await expect(card4.getByRole("button", { name: "Re-run" })).toBeVisible();

    const resultsRes = await request.get(`${API}/v2/items/${itemId}/diagnostics/results`,
      { headers: { Authorization: `Bearer ${token}` } });
    expect(resultsRes.ok(), await resultsRes.text()).toBeTruthy();
    const resultsPayload = await resultsRes.json();
    const runId = resultsPayload.run?.run_id;
    expect(runId).toBeTruthy();
    const result = resultsPayload.results?.find((entry) => entry.diagnostic_id === 4);
    expect(result).toBeTruthy();
    const verdict = result.verdict;
    expect(["pass", "violation", "not_applicable"]).toContain(verdict);

    if (verdict === "not_applicable") {
      // CFR-04 — NOT-APPLICABLE always states its reason, never silent.
      expect(result.na_reason).toBeTruthy();
    }

    const violations = (result.findings || []).filter((finding) => finding.outcome === "VIOLATION");
    if (violations.length > 0) {
      // A VIOLATION auto-opens an issue into the existing hand-off
      // (RCA-27) — checked via the API (6-T15). An RCA case could then be
      // opened from Issue Management; RCA's own e2e suite (rca.spec.js)
      // already covers case creation from an issue, not repeated here.
      const issuesRes = await request.get(`${API}/v2/items/${itemId}/issues`,
        { headers: { Authorization: `Bearer ${token}` } });
      expect(issuesRes.ok(), await issuesRes.text()).toBeTruthy();
      const issuesPayload = await issuesRes.json();
      const diagnosticIssues = (issuesPayload.issues || []).filter((i) => i.diagnostic_id === 4);
      expect(diagnosticIssues.length).toBeGreaterThan(0);
    }

    // --- 6. Coverage-honest summary and retained report --------------------
    const coverageRes = await request.get(`${API}/v2/items/${itemId}/diagnostics/coverage-summary`,
      { headers: { Authorization: `Bearer ${token}` } });
    expect(coverageRes.ok(), await coverageRes.text()).toBeTruthy();
    const coverage = await coverageRes.json();
    expect(coverage).not.toHaveProperty("health_score");

    const reportRes = await request.get(`${API}/v2/diagnostics/runs/${runId}/report?fmt=pdf`);
    expect(reportRes.ok(), await reportRes.text().catch(() => "")).toBeTruthy();
    expect(reportRes.headers()["content-type"]).toContain("pdf");
    const bytes = await reportRes.body();
    expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");

    // --- 7. Diagnostic #14 PSI: existing API family + explicit UI dispatch --
    const launchCard14 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="14"]');
    await launchCard14.getByRole("button", { name: "Launch workflow" }).click();
    await expect(page.getByTestId("psi-scope")).toBeVisible();
    const psiScope = page.getByTestId("psi-scope");
    await expect(psiScope.getByRole("button", { name: "Split this snapshot" })).toBeVisible();
    await expect(psiScope.getByRole("button", { name: "Compare two snapshots" })).toBeVisible();
    await expect(psiScope.getByText("Select variables for PSI")).toBeVisible();
    await expect(psiScope.getByText("Save at least one selected variable before choosing a binning source.")).toBeVisible();
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
    await page.locator("select").first().selectOption(await psiOption.getAttribute("value"));
    const card14 = page.locator('[data-testid="diagnostic-card"][data-diagnostic-id="14"]');
    await expect(card14.getByRole("button", { name: "View results" })).toBeVisible();
    await expect(card14.getByRole("button", { name: "Re-run" })).toBeVisible();
    await card14.getByRole("button", { name: "View results" }).click();
    await expect(page.getByTestId("psi-results")).toBeVisible();
    await expect(page.getByText(/Contextual PSI review/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm as issue" })).toBeVisible();
    await expect(page.getByText("VIOLATION", { exact: true })).toHaveCount(0);
  });
});
