import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// WSP-08 / D-19 (plan Phase 2.9) — Admin "Danger zone" browser coverage:
// server-enforced type-to-confirm gating, and one real surgical reset that
// actually completes. All specs in this suite share one backend process and
// one .e2e/system_state.db, run serially (see playwright.config.js) — this
// file's name is deliberately chosen to sort alphabetically first among
// ui/e2e/*.spec.js, so the real surgical-reset click below runs before any
// other spec has created item/plan/RCA data worth losing (a surgical reset
// never touches users/taxonomy/knowledge base, so even if that ordering
// assumption ever changed, no other spec's login or KB fixtures would break).
//
// ADM-01..04 (0.5.0 plan Step 2) — the reset message must read in business
// language: no raw table name, no `table=count` pair, your-work counts
// before the platform line, and a full sentence even when nothing needed
// removing. A tiny dataset is uploaded first so the surgical reset actually
// has something to report ("assets"/"uploaded files"), rather than only ever
// exercising the (also-covered) all-zero case.

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "fixtures");

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

// A raw table/enum identifier a business-language message must never contain
// (ADM-01/M-2). Deliberately snake_case-only so a legitimate English word
// that happens to coincide with a single-word table name (e.g. "monitoring")
// is never a false positive here.
const RAW_IDENTIFIER_RE = /\b(dq_items|dq_item_files|dq_item_tables|variable_inventory|dq_item_mappings|dq_item_warnings|tag_assignments|issues_v2|tracked_issues_v2|rca_cases|kb_documents|kb_retrieval_manifests|plan_v2|results_v2|scores_v2|object_contexts|context_links|app_fsm|agent_skills|tag_dimensions|threshold_settings)\b/;
const TABLE_COUNT_PAIR_RE = /[a-zA-Z_][a-zA-Z0-9_]*=\d+/;

test("admin danger zone: surgical reset is gated by exact type-to-confirm text, reports business language, and succeeds", async ({ page }) => {
  await signIn(page);

  // Seed one small dataset so the reset below has real "your work" counts to
  // report, not only the all-zero case (that's covered separately below).
  const name = `e2e-reset-language-dataset-${Date.now()}`;
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Add New" }).last().click();
  await page.getByLabel("Alias").fill(name);
  await page.locator('input[type="file"]').nth(0).setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();

  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Danger zone" })).toBeVisible();

  const surgicalButton = page.getByRole("button", { name: "Surgical reset" });
  const surgicalInput = page.getByLabel("Type RESET to confirm");
  await expect(surgicalButton).toBeDisabled();

  // Wrong case / partial text must not arm the button — the disabled state
  // tracks an exact match, not just "non-empty".
  await surgicalInput.fill("reset");
  await expect(surgicalButton).toBeDisabled();
  await surgicalInput.fill("RES");
  await expect(surgicalButton).toBeDisabled();

  await surgicalInput.fill("RESET");
  await expect(surgicalButton).toBeEnabled();

  await surgicalButton.click();
  await expect(page.getByText(/Surgical reset complete/)).toBeVisible();

  // ADM-01/02/03: business language, "assets" (not "dq_items"), no raw
  // identifier, no `table=count` pair.
  const message = await page.getByText(/Surgical reset complete/).innerText();
  expect(message).toMatch(/assets?/i);
  expect(message).not.toMatch(RAW_IDENTIFIER_RE);
  expect(message).not.toMatch(TABLE_COUNT_PAIR_RE);

  // The confirm field is cleared after a successful reset, re-disabling the
  // button (never left armed for an accidental second click).
  await expect(surgicalButton).toBeDisabled();

  // ADM-03: a second consecutive reset (nothing left to clear) still reads
  // as a full sentence, never a bare fragment like "nothing to remove".
  await surgicalInput.fill("RESET");
  await surgicalButton.click();
  const secondMessage = await page.getByText(/Surgical reset complete/).innerText();
  expect(secondMessage).toMatch(/Nothing needed removing.*no work data to clear\./i);
});

test("admin danger zone: full wipe button is also gated by its own exact phrase", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "Danger zone" })).toBeVisible();

  const wipeButton = page.getByRole("button", { name: "Full wipe" });
  const wipeInput = page.getByLabel("Type WIPE EVERYTHING to confirm");
  await expect(wipeButton).toBeDisabled();

  await wipeInput.fill("WIPE EVERYTHING NOW");
  await expect(wipeButton).toBeDisabled();
  await wipeInput.fill("wipe everything");
  await expect(wipeButton).toBeDisabled();

  await wipeInput.fill("WIPE EVERYTHING");
  await expect(wipeButton).toBeEnabled();
  // Deliberately not clicked — a full wipe would clear every other spec's
  // fixtures for the rest of this suite run; the gating behavior itself is
  // exercised end-to-end by the surgical reset test above.
});
