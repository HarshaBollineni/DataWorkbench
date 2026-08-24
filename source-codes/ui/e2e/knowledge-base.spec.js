import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// RCA Stage 2 gate: browser coverage for upload, conversion preview,
// review submission, publication, and the never-a-second-module guard (the
// module lives under the existing Sidebar nav, not a standalone surface).

const here = path.dirname(fileURLToPath(import.meta.url));

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

test("knowledge base: upload, convert, review, and publish a rule", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await expect(page.getByRole("heading", { name: "Knowledge Base" })).toBeVisible();

  const stamp = Date.now();
  const docName = `synthetic-e2e-doc-${stamp}.txt`;
  // Unique text per run: .e2e/system_state.db persists across separate test
  // invocations (by design, like the rest of this suite), so a fixed rule
  // body would collide with residue from an earlier run of this same spec.
  const docContent = `## Uniqueness rule\nA facility_id must be unique across the portfolio (run ${stamp}).\n`;
  const ruleFragment = `A facility_id must be unique across the portfolio \\(run ${stamp}\\)`;
  const filePath = path.join(here, "fixtures", docName);
  const fs = await import("node:fs");
  fs.writeFileSync(filePath, docContent, "utf-8");

  try {
    await page.locator('input[type="file"]').setInputFiles(filePath);
    await expect(page.getByText("Converted Markdown preview")).toBeVisible();
    await expect(page.getByText(/facility_id must be unique/)).toBeVisible();

    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(page.getByText("Extracted rules")).toBeVisible();
    await expect(page.getByText("Uniqueness rule", { exact: true })).toBeVisible();

    // Publish from the Rules tab.
    await page.getByRole("button", { name: "Rules" }).click();
    await page.getByRole("combobox").first().selectOption("draft");
    // The PublishRow root div (not an inner wrapper — .last() on a plain
    // hasText filter would grab the innermost matching div, which is the
    // header row, not the one containing the Publish button as a sibling).
    const ruleRow = page.locator("div.rounded-md.border.border-slate-200.p-3")
      .filter({ hasText: new RegExp(ruleFragment) });
    await expect(ruleRow).toBeVisible();
    await ruleRow.getByRole("button", { name: "Publish" }).click();

    // The rule appearing in the lifecycle_state=published filter is itself
    // the proof of the transition (a stray literal-text check for
    // "published" collides with the filter <option> of the same name).
    await page.getByRole("combobox").first().selectOption("published");
    await expect(page.getByText(new RegExp(ruleFragment))).toBeVisible();
  } finally {
    fs.unlinkSync(filePath);
  }
});

// Phase 5 (docs/0.4.0/04-kb-contract.md) — table-aware parse + tag + playback
// + parse report + binding-status parity, driven against the real S9
// fixture (the parity target for 5-T1/5-T10). The prose test above stays
// exactly as it was (5-T2's regression guard: a prose document still parses
// unaffected by the table-aware changes) — this is a second, additive test.
test("knowledge base: S9 table-aware parse — tag, playback, parse report, publish", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await expect(page.getByRole("heading", { name: "Knowledge Base" })).toBeVisible();

  const fixturePath = path.join(here, "fixtures", "KB_cross_field_reference_2.pdf");
  await page.locator('input[type="file"]').setInputFiles(fixturePath);
  await expect(page.getByText("Converted Markdown preview")).toBeVisible();

  // Tag with a taxonomy dimension (KB-02) before submitting.
  await expect(page.getByText("Taxonomy dimensions")).toBeVisible();
  await page.getByRole("button", { name: "IRB", exact: true }).click();
  await page.getByRole("button", { name: "Save tags" }).click();
  await expect(page.getByText("Use case: IRB")).toBeVisible();

  // Table-aware parse -> playback summary (KB-03): exactly 49 rules for S9.
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByText("Playback summary")).toBeVisible();
  await expect(page.getByText(/49 rules? found/)).toBeVisible();
  await expect(page.getByText(/reference-only 49/)).toBeVisible();

  // Parse report (KB-10) — a first-class, retrievable per-rule artifact.
  await page.getByRole("button", { name: /Parse report/ }).click();
  await expect(page.getByText("Parse report (49)")).toBeVisible();
  await expect(page.getByText("IRB-01").first()).toBeVisible();

  // Extracted rules list shows binding status distinctly (KB-08/09).
  await expect(page.getByText("Extracted rules (49)")).toBeVisible();

  // Publish one domain/bound-type rule (simplest to publish) from the Rules
  // tab. .e2e/system_state.db persists across separate spec runs (by design),
  // so an earlier run's own "IRB-18" rule may still be sitting there,
  // published; list_kb_rules orders updated_at DESC, so .first() always
  // resolves to THIS run's row (freshly extracted, then freshly published).
  await page.getByRole("button", { name: "Rules" }).click();
  await page.getByRole("combobox").first().selectOption("draft");
  const domainRuleRow = page.locator("div.rounded-md.border.border-slate-200.p-3")
    .filter({ hasText: /IRB-18 —/ }).first();
  await expect(domainRuleRow).toBeVisible();
  await expect(domainRuleRow.getByText("reference-only")).toBeVisible();
  await domainRuleRow.getByRole("button", { name: "Publish" }).click();

  // KB-08: publishing is the automatic, signature-exact binding trigger
  // (kb.publish_rule -> the cross-field binder). IRB-18 ("term_months > 0")
  // is a clean domain/bound rule with no parse hazard, so it binds cleanly —
  // this is the real, product-visible proof that a published rule can reach
  // 'bound' through the UI alone, with no separate manual step.
  await page.getByRole("combobox").first().selectOption("published");
  const publishedRow = page.locator("div.rounded-md.border.border-slate-200.p-3")
    .filter({ hasText: /IRB-18 —/ }).first();
  await expect(publishedRow).toBeVisible();
  await expect(publishedRow.getByText("bound", { exact: true })).toBeVisible();
});

test("knowledge base module is reached only through the existing nav, no second surface", async ({ page }) => {
  await signIn(page);
  // Second-module guard (docs/rca/00-contracts.md §3): exactly one nav
  // entry, and the module renders inside the same Shell/Sidebar layout as
  // every other module — not a standalone page outside the app shell.
  await expect(page.getByRole("link", { name: "Knowledge Base" })).toHaveCount(1);
  await page.getByRole("link", { name: "Knowledge Base" }).click();
  await expect(page.getByRole("link", { name: "Data Inventory" })).toBeVisible(); // Sidebar still present
});
