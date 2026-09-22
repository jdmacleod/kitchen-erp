import { defineConfig, devices } from "@playwright/test";

// Screenshot capture, separate from the test suite on purpose: it writes files
// into docs/, it points at the demo stack rather than the dev stack, and it must
// never be part of a run someone does against their own data. One project, one
// fixed viewport, so a rerun differs only where the UI differs.
export default defineConfig({
  testDir: "./capture",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.DEMO_BASE_URL ?? "http://127.0.0.1:8081",
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    trace: "retain-on-failure",
  },
  projects: [{ name: "capture", use: { ...devices["Desktop Chrome"] } }],
});
