import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const testDataDir =
  process.env.SIVAX_E2E_DATA_DIR || mkdtempSync(join(tmpdir(), "sivax-ui-"));
const env = {
  PAYMENT_MODE: "mock",
  RISK_MODE: "off",
  DATA_DIR: testDataDir,
  DEMO_ACCESS_KEY: "local-demo-only",
  SERVICE_KEY: "local-service-only",
  MOCK_PAYMENT_SECRET: "local-mock-only",
  PUBLIC_API_URL: "http://localhost:8010",
  PUBLIC_WEB_URL: "http://localhost:5173",
  ORDERS_URL: "http://127.0.0.1:8012",
  NO_PROXY: "localhost,127.0.0.1",
  no_proxy: "localhost,127.0.0.1",
};

export default defineConfig({
  testDir: "./tests",
  timeout: 90000,
  expect: { timeout: 12000 },
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? {
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
          args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        }
      : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"], defaultBrowserType: "chromium" } },
  ],
  webServer: [
    {
      command: ".venv/bin/python -m uvicorn agentic.api:create_app --factory --host 127.0.0.1 --port 8010",
      cwd: root,
      url: "http://127.0.0.1:8010/health",
      env,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
      timeout: 30000,
    },
    {
      command: ".venv/bin/python -m uvicorn agentic.merchant:app_factory --factory --host 127.0.0.1 --port 8012",
      cwd: root,
      url: "http://127.0.0.1:8012/health",
      env,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
      timeout: 30000,
    },
    {
      command: "npm --prefix demo/web run dev",
      cwd: root,
      url: "http://localhost:5173",
      env,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
      timeout: 30000,
    },
  ],
});
