import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "./support/test-fixture";

const here = path.dirname(fileURLToPath(import.meta.url));
const version = fs.readFileSync(path.resolve(here, "../../VERSION"), "utf8").trim();
const releaseLabel = `Archimedes ${version}`;

test("login and authenticated shell display the repository release version", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tourEnabled", "false"));
  await page.goto("/login");
  await expect(page.getByText(releaseLabel, { exact: true })).toBeVisible();

  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();

  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  await expect(page.getByText(releaseLabel, { exact: true })).toBeVisible();
});
