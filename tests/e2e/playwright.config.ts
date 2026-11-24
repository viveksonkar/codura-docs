/**
 * Browser e2e against the running compose stack (M1-14 UI e2e, M2-09 web parity).
 *   docker compose up -d   # then:
 *   pnpm --dir tests/e2e install && pnpm --dir tests/e2e install-browsers && pnpm --dir tests/e2e test
 * Real local models answer, so timeouts are generous (6 GB GPU, ADR-0024).
 */
import { defineConfig, devices } from "@playwright/test";

export const BASE_URL = process.env.CODURA_E2E_BASE_URL ?? "http://localhost:8080";
export const MAILPIT_URL = process.env.CODURA_E2E_MAILPIT_URL ?? "http://localhost:8025";

export default defineConfig({
  testDir: ".",
  testMatch: ["web-channel/**/*.spec.ts", "admin/**/*.spec.ts"],
  globalSetup: "./global-setup.ts",
  timeout: 240_000,
  expect: { timeout: 90_000 },
  fullyParallel: false,
  workers: 1, // one local GPU: answers are serialised anyway
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
