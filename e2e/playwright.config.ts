import { defineConfig, devices } from "@playwright/test";

// Runs against the Compose stack (web on :8080 by default). Seed the dev admin
// first: `make e2e-seed`. Nothing here reaches an outside origin; a test that
// asserts on network activity fails if it does.
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } } },
  ],
});
