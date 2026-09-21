import { type Page, expect } from "@playwright/test";

// Local development credentials only; see README. Never real ones.
export const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL ?? "admin@example.com",
  password: process.env.E2E_ADMIN_PASSWORD ?? "local-dev-admin-pw",
};

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
