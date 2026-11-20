import { defineConfig } from "@playwright/test";

// Runs against an already-running stack (backend + `next start`); see docs/TEST_REPORT.md for the commands.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: process.env.BASE_URL || "http://localhost:3000", viewport: { width: 1440, height: 900 }, trace: "retain-on-failure" },
});
