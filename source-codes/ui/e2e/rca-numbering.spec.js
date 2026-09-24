import { expect, test } from "./support/test-fixture";

test("RCA numbers the second candidate as the first investigation and preserves follow-ups", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const selectedId = "hyp_0c0d6d02c19b";
  const candidates = ["hyp-first", selectedId, "hyp-third"].map((id, index) => ({
    hypothesis_id: id, statement: `Explanation ${index + 1}`, evidence_basis: "Retained findings",
    proposed_test: "Compare the populations", lifecycle_status: index === 1 ? "selected" : "candidate",
  }));
  let bundle = { case_id: "rca-numbering", workflow_generation: 1, state: "investigation_loop",
    case_file: { checklist_json: {} }, hypotheses: [], suspects: [], confirmation_checks: [],
    hypothesis_candidates: candidates, hypothesis_catalog: candidates.map((row, index) => ({ ...row, number: index + 1 })),
    selected_initial_hypothesis: candidates[1], active_investigation_hypothesis: candidates[1],
    looks: [{ look_id: "opening", kind: "opening" },
      { look_id: "discovery", kind: "planned", fork_json: { hypothesis_id: selectedId, kind: "agent_driver_search", agent_runtime: true, plan: {} } }],
    executions: { opening: { execution_id: "exec-opening", look_id: "opening", status: "done", summary_json: {} } },
    aar_evidence: [{ artifact_id: "review", evidence_kind: "llm_initial_review", status: "completed", details: { output: { summary: "Three candidate explanations" } } }],
    data_chat: { unlocked: false, successful_investigation_count: 0 },
  };
  await page.route("**/api/v2/issues/numbering", (route) => route.fulfill({ json: {
    issue_row_id: "numbering", rca_case_id: bundle.case_id, workflow_version: "rca", test_name: "Population Stability Index review",
    item_name: "Retained portfolio", table_name: "loans", columns: ["property_value"],
  } }));
  await page.route("**/api/v3/issues/numbering/tags", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v3/rca/cases/rca-numbering", (route) => route.fulfill({ json: bundle }));
  await page.goto("/issues/numbering?rca_view=initial-review");
  for (const name of ["Candidate A", "Candidate B", "Candidate C"]) await expect(page.getByText(name, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "3. Investigate", exact: true }).click();
  const groups = page.getByTestId("rca-hypothesis-group");
  await expect(groups).toHaveCount(1);
  await expect(groups.first().getByText("Hypothesis 1", { exact: true })).toBeVisible();
  await expect(groups.first()).toContainText(selectedId);
  await groups.first().getByTestId("rca-hypothesis-tests").locator(":scope > summary").click();
  await expect(groups.first()).toContainText("1.1 Discovery");
  bundle.looks.push({ look_id: "confirmation", kind: "planned", fork_json: { combined_parent_look_id: "discovery" } },
    { look_id: "followup", kind: "planned", fork_json: { hypothesis_id: selectedId } },
    { look_id: "alternative", kind: "planned", fork_json: { hypothesis_id: "hyp-first" } });
  bundle = { ...bundle, active_investigation_hypothesis: candidates[0] };
  await page.reload();
  await expect(groups).toHaveCount(2);
  await expect(groups.nth(0)).toContainText("Hypothesis 1");
  await expect(groups.nth(1)).toContainText("Hypothesis 2");
  await expect(groups.nth(0)).toContainText("1.2 Confirmation");
  await expect(groups.nth(0)).toContainText("1.3 Follow-up");
  await expect(groups.nth(1)).toContainText("2.1 Hypothesis test");
  await expect(page.getByTestId("rca-data-chat-locked")).toContainText("2 more successful runs");
});
