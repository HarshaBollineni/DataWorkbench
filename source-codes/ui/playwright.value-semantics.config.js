// UI-only D08 regressions: mock all APIs; do not start or modify the shared backend.
import { defineConfig } from "@playwright/test";
import appConfig from "./playwright.config.js";

const baseURL = "http://127.0.0.1:5191";

export default defineConfig({
  ...appConfig,
  testMatch: "value-semantics-decisions.spec.js",
  use: { ...appConfig.use, baseURL },
  webServer: [{
    ...appConfig.webServer[1],
    command: "npm run dev -- --host 127.0.0.1 --port 5191",
    url: baseURL,
    env: { ...appConfig.webServer[1].env, VITE_API_BASE: `${baseURL}/api` },
  }],
});
