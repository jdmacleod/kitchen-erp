/**
 * Screenshots for the public README, captured from the synthetic demo stack.
 *
 * This never runs against the household's deployment. The default base URL is the
 * demo stack's port, the demo credentials are the only ones it knows, and the
 * capture refuses to run unless the database it finds is the seeded synthetic one.
 * See compose.demo.yaml and backend/app/services/demo.py.
 *
 *   make demo-up && make demo-seed && make screenshots
 *
 * Pages are driven into a state worth photographing rather than loaded cold: an
 * empty "Add an ingredient" form is what the app looks like before anyone uses
 * it, which is the least interesting thing about it.
 */
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(__dirname, "../../docs/screenshots");

// The demo household, created by `kerp seed demo`. Not credentials for anything.
const DEMO = {
  email: process.env.DEMO_EMAIL ?? "demo@example.com",
  password: process.env.DEMO_PASSWORD ?? "demo-only-not-a-real-password",
};

// A vendor that exists only in the synthetic data. If it is not there, this is
// not the demo database and nothing should be photographed.
const SENTINEL = "Tideline Market";

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true });
});

async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DEMO.email);
  await page.getByLabel("Password").fill(DEMO.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

/** Refuses to photograph anything but the synthetic household. */
async function assertDemoData(page: Page): Promise<void> {
  await page.goto("/vendors");
  await expect(
    page.getByText(SENTINEL).first(),
    `This is not the demo database: no vendor named "${SENTINEL}". ` +
      "Point DEMO_BASE_URL at the demo stack and run `make demo-seed`.",
  ).toBeVisible({ timeout: 15_000 });
}

async function shot(page: Page, name: string): Promise<void> {
  // Let fonts settle and any chart finish drawing, so reruns differ only where
  // the UI does.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

/** Types into an ingredient combobox and picks the named option. */
async function pickIngredient(page: Page, name: string): Promise<void> {
  const box = page.getByRole("combobox", { name: "Add an ingredient" });
  await box.fill(name.slice(0, 6));
  await page.getByRole("option", { name: new RegExp(name, "i") }).first().click();
  await expect(page.getByRole("list", { name: "Chosen ingredients" })).toContainText(name);
}

test.describe.configure({ mode: "serial" });

test("capture the README screenshots", async ({ page }) => {
  await signIn(page);
  await assertDemoData(page);

  // The price book: several ingredients side by side, one column per vendor.
  await page.goto("/compare");
  for (const name of ["Olive oil", "Whole milk", "Coffee beans", "Bread flour"]) {
    await pickIngredient(page, name);
  }
  await expect(page.getByRole("table", { name: "Price comparison" })).toBeVisible();
  await shot(page, "compare");

  // A product with eight months of observations behind it, for the history chart.
  await page.goto("/products");
  await page.getByRole("link", { name: /Extra virgin olive oil/ }).first().click();
  await expect(page).toHaveURL(/\/products\/[0-9a-f-]{36}$/);
  await shot(page, "product-prices");

  // The purchase list, and one purchase opened.
  await page.goto("/purchases");
  await expect(page.getByRole("heading", { level: 1, name: "Purchases" })).toBeVisible();
  await shot(page, "purchases");

  // The date cell is the link into a purchase.
  await page.getByRole("link", { name: /\b20\d\d\b/ }).first().click();
  await expect(page).toHaveURL(/\/purchases\/[0-9a-f-]{36}$/);
  await shot(page, "purchase-detail");
});
