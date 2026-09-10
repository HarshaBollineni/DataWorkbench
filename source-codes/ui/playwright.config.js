import { defineConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");
const e2eRoot = path.join(repoRoot, ".e2e", `run-${Date.now()}-${process.pid}`);
fs.mkdirSync(e2eRoot, { recursive: true });
const python = path.join(repoRoot, ".venv", "Scripts", "python.exe");
const backendPort = process.env.E2E_BACKEND_PORT || "8001";
const frontendPort = process.env.E2E_FRONTEND_PORT || "5175";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  // All specs share one backend process and one invocation-specific E2E database.
  // The shared test fixture performs a surgical reset after every test, so
  // failed drafts cannot cascade into later serial journeys.
  // design — see the fixture-uniqueness comments in the specs themselves).
  // As the suite grew past 2 spec files, real concurrent uploads/executions
  // against that single process started intermittently timing out under the
  // default parallel workers — a different spec failed on each retry, and
  // every spec passed reliably alone, which is resource contention, not a
  // product bug. Serial execution measured faster in practice (no
  // contention/retries) as well as reliable, so it isn't a speed trade-off.
  workers: 1,
  reporter: [["list"], ["json", { outputFile: path.join(e2eRoot, "results.json") }], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: path.join(repoRoot, "backend"),
      url: `http://127.0.0.1:${backendPort}/health`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        SYSTEM_DB_PATH: path.join(e2eRoot, "system_state.db"),
        SYSTEM_DB_BACKUP_PATH: "",
        UPLOAD_DIR: path.join(e2eRoot, "uploads"),
        CORS_ORIGINS: `http://127.0.0.1:${frontendPort}`,
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      cwd: here,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      env: {
        ...process.env,
        VITE_API_BASE: `http://127.0.0.1:${backendPort}/api`,
      },
    },
  ],
});
