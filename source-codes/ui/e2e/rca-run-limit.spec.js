import { expect, test } from "./support/test-fixture";

test("two RCA runs disable exploration while retaining chat, closure and a pending third hypothesis", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const hypotheses = [1, 2, 3].map((number) => ({ hypothesis_id: `hyp-${number}`, statement: `Explanation ${number}` }));
  const bundle = { case_id: "rca-limit", workflow_generation: 1, state: "investigation_loop",
    case_file: { checklist_json: {} }, hypotheses: [], suspects: [], confirmation_checks: [],
    active_investigation_hypothesis: hypotheses[2], hypothesis_catalog: hypotheses,
    looks: hypotheses.map((row, index) => ({ look_id: `look-${index}`, kind: "planned",
      fork_json: { hypothesis_id: row.hypothesis_id, agent_runtime: true, kind: "agent_hypothesis_test", plan: {} } })),
    executions: { "look-0": { execution_id: "exec-0", look_id: "look-0", status: "completed", summary_json: {} } },
    aar_evidence: [], investigation_limit: { limit: 2, used: 1, remaining: 1, reached: false },
    data_chat: { unlocked: false, successful_investigation_count: 1, turns: [] },
  };
  await page.route("**/api/v2/issues/run-limit", (route) => route.fulfill({ json: {
    issue_row_id: "run-limit", rca_case_id: bundle.case_id, workflow_version: "rca",
    test_name: "Population stability review", item_name: "Portfolio", table_name: "loans",
  } }));
  await page.route("**/api/v3/issues/run-limit/tags", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v3/rca/cases/rca-limit", (route) => route.fulfill({ json: bundle }));
  await page.goto("/issues/run-limit?rca_view=investigate");
  const actions = page.getByTestId("rca-investigation-action");
  await expect(actions.getByRole("button", { name: "Continue exploring", exact: true })).toBeEnabled();
  await expect(actions.getByRole("button", { name: "Explore another explanation", exact: true })).toBeEnabled();
  bundle.executions["look-1"] = { execution_id: "exec-1", look_id: "look-1", status: "completed", summary_json: {} };
  bundle.investigation_limit = { limit: 2, used: 2, remaining: 0, reached: true };
  bundle.data_chat = { unlocked: true, successful_investigation_count: 2, turns: [] };
  await page.reload();
  await expect(actions.getByRole("button", { name: "Continue exploring", exact: true })).toBeDisabled();
  await expect(actions.getByRole("button", { name: "Explore another explanation", exact: true })).toBeDisabled();
  await expect(actions).toContainText("Two-run limit reached. Ask about this data or continue to closure.");
  await expect(page.getByTestId("rca-hypothesis-group")).toHaveCount(3);
  await expect(page.getByLabel("Ask about this data", { exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Continue to closure", exact: true })).toBeEnabled();
  bundle.data_chat = { unlocked: false, successful_investigation_count: 1, turns: [] };
  await page.reload();
  await expect(actions).toContainText("data chat requires two successful runs");
  await expect(page.getByTestId("rca-data-chat-locked")).toBeVisible();
  await page.getByRole("button", { name: "Continue to closure", exact: true }).click();
  await expect(page.getByLabel("Proposed root cause")).toBeVisible();
});
