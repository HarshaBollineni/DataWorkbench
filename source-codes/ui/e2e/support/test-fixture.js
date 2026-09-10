import { expect, test as base } from "@playwright/test";

import { E2E_API_BASE } from "./endpoints.js";

export const test = base;

test.afterEach(async ({ request }) => {
  const login = await request.post(`${E2E_API_BASE}/login`, {
    data: { username: "anirban", password: "dqstudio" },
  });
  if (!login.ok()) throw new Error(`E2E surgical cleanup login failed: ${login.status()} ${await login.text()}`);
  const { token } = await login.json();
  const reset = await request.post(`${E2E_API_BASE}/admin/factory-reset`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { grade: "surgical", confirm: "RESET" },
  });
  if (!reset.ok()) throw new Error(`E2E surgical cleanup failed: ${reset.status()} ${await reset.text()}`);
});

export { expect };
