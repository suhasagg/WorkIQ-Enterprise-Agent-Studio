import { defineConfig } from "@playwright/test";

// Captures docs/screenshots/*.png from the running app: `npm run screenshots` (needs a freshly seeded demo stack).
export default defineConfig({
  testDir: "./screenshots",
  timeout: 600_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: process.env.BASE_URL || "http://localhost:3000", viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, actionTimeout: 30_000 },
});
