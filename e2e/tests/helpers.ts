import { type Page, expect } from "@playwright/test";

// Local development credentials only; see README. Never real ones.
export const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL ?? "admin@example.com",
  password: process.env.E2E_ADMIN_PASSWORD ?? "local-dev-admin-pw",
};

/**
 * A per-run, per-worker tag for any name the backend holds a unique index on.
 *
 * `Date.now()` alone is not enough: the suite runs three projects (desktop,
 * phone, phone-375) in parallel against ONE backend, their workers start within
 * the same millisecond, and each file computes its tag once at module load. Two
 * workers then pick the same name and the second create comes back 409 instead
 * of 201 -- an intermittent failure with nothing wrong in the code under test.
 * The random suffix is what makes it per-worker.
 */
export const nextTag = (): string => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

export async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

/** Collects every request to an origin other than the app's own. */
export function watchExternalRequests(page: Page, baseURL: string): string[] {
  const own = new URL(baseURL).origin;
  const external: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (!url.startsWith(own) && !url.startsWith("data:") && !url.startsWith("blob:")) {
      external.push(url);
    }
  });
  return external;
}
