import { expect, test } from "./support/test-fixture";
import { E2E_API_BASE } from "./support/endpoints";

// This is a taxonomy-seed acceptance test. The former version exercised a
// Business tags panel that is not part of the 0.5.0 Data Sourcing surface;
// TAX-01 requires the reference value and its Product-only placement.
const API_BASE = E2E_API_BASE;

test("taxonomy: CRE is present in Product and absent from Portfolio", async ({ page, request }) => {
  await page.goto("/login");
  await page.getByLabel("Password").fill("dqstudio");
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Data Inventory" })).toBeVisible();
  const token = await page.evaluate(() => localStorage.getItem("dq_token"));
  const response = await request.get(`${API_BASE}/v3/taxonomy/dimensions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const dimensions = await response.json();
  const product = dimensions.find((dimension) => dimension.key === "product");
  const portfolio = dimensions.find((dimension) => dimension.key === "portfolio");
  expect(product?.values.map((value) => value.key)).toContain("cre");
  expect(portfolio?.values.map((value) => value.key)).not.toContain("cre");
});
