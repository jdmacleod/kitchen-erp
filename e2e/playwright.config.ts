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
    // 375px is the narrowest phone still in common use, and it is narrower than
    // the 390 above. The Location select on the purchase form overflowed only
    // below ~427px, so `phone` was green while the form ran off the screen.
    { name: "phone-375", use: { ...devices["Pixel 7"], viewport: { width: 375, height: 812 } } },
    // The layout floor and the top of the tablet range (UI-4.2). Both use the
    // phone shell, so they run only the fit and touch-target checks.
    { name: "phone-360", testMatch: /narrow-phone\.spec\.ts/, use: { ...devices["Pixel 7"], viewport: { width: 360, height: 780 } } },
    { name: "tablet-1000", testMatch: /narrow-phone\.spec\.ts/, use: { ...devices["Pixel 7"], viewport: { width: 1000, height: 1280 } } },
  ],
});
