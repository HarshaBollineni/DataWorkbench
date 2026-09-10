import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "./support/test-fixture";
import { confirmDatasetStructure } from "./support/dataset-structure";
import { selectPopoverOption } from "./support/select";

// 0.5.0 ADM-06/07 (plan Step 3c) — Admin's asset version-history view:
// pick any asset, see its version/snapshot timeline rendered in human
// language, never a raw status/intent enum token.
//
// This spec's own upload creates exactly the asset it then looks up in the
// picker (a unique, timestamped name), so it never depends on what any
// sibling spec in this shared-backend suite has or hasn't created first.

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "fixtures");

async function signIn(page) {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
}

// M-2 — no raw INTENT/STATUS TOKEN may ever be shown as a bare, unlabelled
// field (a badge, a dedicated status/intent value) in this view. This is
// deliberately NOT a whole-page substring ban: the underlying event-log
// `summary` text (written by S3b's `assets.service._write_event`, verbatim
// pass-through per this view's own read-only contract — never re-composed
// here) is ordinary English prose that legitimately uses words like
// "superseded" as a verb (exactly as ADM-06 itself does: "snapshots
// superseded, or none"). What must never happen is one of THESE specific,
// dedicated fields rendering the UNTRANSLATED, exact-case raw enum value —
// case-sensitive on purpose: the raw DB value is always lowercase
// (`active`/`fresh`/...), so a Title-Case English label like "Active" or
// "Fresh upload" is proof a label function ran, not a leak, even though it
// happens to share a root word with the enum it labels.
const RAW_TOKEN_RE = /^(superseded|active|fresh|add_period|full_replacement|current)$/;

test("admin version history: pick an asset, see its version/snapshot timeline in human language", async ({ page }) => {
  test.setTimeout(90_000);
  await signIn(page);

  // The typed name is sanitised into an alias on the way to a `display_name`
  // (spaces -> dashes — assets.identity.sanitize_alias_for_migration, the
  // lenient rule create_item's delegation into assets.service uses), so the
  // picker search below matches on the digits-only stamp, which survives
  // that sanitisation unchanged and is still unique to this test run.
  const stamp = String(Date.now());
  const name = `e2e-history-dataset-${stamp}`;
  await page.getByRole("link", { name: "Data Sourcing" }).click();
  await page.getByRole("button", { name: "Create New Dataset" }).click();
  await page.getByLabel("Alias").fill(name);
  await page.locator('input[type="file"]').nth(0).setInputFiles(path.join(fixtures, "assessment.csv"));
  await page.getByRole("button", { name: "Start sourcing" }).click();
  await expect(page.getByText(/Variable inventory is ready/)).toBeVisible();
  await page.getByLabel("Snapshot label", { exact: true }).fill(`snapshot-${stamp}`);
  await page.getByLabel("Target variable (optional)", { exact: true }).selectOption({ index: 1 });
  await selectPopoverOption(page, "Use case");
  await selectPopoverOption(page, "Product");
  await page.getByLabel(/I confirm this target/).check();
  await page.getByTestId("upl-step-5").getByRole("button", { name: "Save and Proceed" }).click();
  await confirmDatasetStructure(page);

  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "Asset version history" })).toBeVisible();

  await page.getByTestId("version-history-search").fill(stamp);
  const pickerRow = page.locator('[data-testid^="version-history-pick-"]', { hasText: stamp });
  await expect(pickerRow).toHaveCount(1);
  await pickerRow.first().click();

  await expect(page.getByTestId("version-history-timeline")).toBeVisible();
  const versionGroup = page.getByTestId("version-group-1");
  await expect(versionGroup).toBeVisible();
  // Human labels, never the raw tokens they stand for.
  await expect(versionGroup.getByText("Current version")).toBeVisible();
  await expect(versionGroup.getByText("Fresh upload")).toBeVisible();
  await expect(versionGroup.getByText("Active", { exact: true })).toBeVisible();

  // The dedicated status/intent fields (never the free-text summary
  // sentence) must never render the bare token — assert the snapshot's own
  // status badge's exact text is the human label, not the raw enum value.
  const snapshotStatusBadge = versionGroup.locator('[data-testid^="snapshot-status-"]').first();
  const badgeText = (await snapshotStatusBadge.innerText()).trim();
  expect(RAW_TOKEN_RE.test(badgeText)).toBe(false);
  expect(badgeText).toBe("Active");
});
