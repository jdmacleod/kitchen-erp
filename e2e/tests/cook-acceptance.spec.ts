import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

/**
 * Phase 3 acceptance rows a browser has to show (UI-7.1, UI-7.7): Cook under
 * More on a phone, and the recipe page's two columns measured at 1024px.
 *
 * Like recipes.spec.ts, this runs against the example recipes the e2e stack
 * mounts read-only, and creates nothing.
 */

const RISOTTO = "Risotto alla Milanese";

async function indexExamples(page: Page): Promise<void> {
  expect((await page.request.post("/api/v1/recipes/rescan")).status()).toBe(200);
}

async function openRecipe(page: Page, title: string): Promise<void> {
  await page.goto(`/cook/recipes?q=${encodeURIComponent(title.split(" ")[0])}`);
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(/\/cook\/recipes\/[0-9a-f-]{36}(\?.*)?$/);
  await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();
  await expect(page.getByTestId("cost-totals")).not.toContainText("Costing…");
}

// The More sheet is the phone shell's; projects below lg run it (playwright.config.ts).
test(
  "Cook is under More on a phone, before Shop, and opens the recipes (UI-7.1)",
  {
    tag: "@below-lg",
  },
  async ({ page }) => {
    await login(page);
    await page.goto("/");
    const tabs = page.getByRole("navigation", { name: "Tabs" });
    // The sheet lists the Phase 1-2 sections until /health answers; the List tab
    // (Plan, the phase after Cook) says the features have arrived.
    await expect(tabs.getByRole("link", { name: "List" })).toBeVisible();
    await tabs.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    const sections = sheet.getByRole("navigation", { name: "More" }).locator("section h3");
    const labels = (await sections.allTextContents()).map((t) => t.trim());
    expect(labels).toContain("Cook");
    expect(labels).toContain("Shop");
    expect(labels.indexOf("Cook"), `More lists ${labels.join(", ")}`).toBeLessThan(
      labels.indexOf("Shop"),
    );
    const cook = sheet.getByRole("region", { name: "Cook" });
    await expect(cook.getByRole("link", { name: "Recipes" })).toBeVisible();
    await cook.getByRole("link", { name: "Recipes" }).click();
    await expect(page).toHaveURL(/\/cook\/recipes$/);
    await expect(page.getByRole("heading", { level: 1, name: "Recipes" })).toBeVisible();
  },
);

// Sets its own widths, so it runs once, on desktop.
test(
  "at 1024px the recipe sits beside the cost table, the table in the wider column; below, they stack (UI-7.7)",
  {
    tag: "@desktop-only",
  },
  async ({ page }) => {
    await login(page);
    await indexExamples(page);
    await page.setViewportSize({ width: 1024, height: 900 });
    await openRecipe(page, RISOTTO);

    const recipe = page.getByRole("region", { name: "Recipe" });
    const table = page.getByRole("table", { name: "Cost by line" });
    await expect(recipe).toBeVisible();
    const r = (await recipe.boundingBox())!;
    const t = (await table.boundingBox())!;
    // Side by side: the file on the left, the table on the right, both starting near the same height.
    expect(r.x + r.width, "the recipe ends before the table starts").toBeLessThanOrEqual(t.x);
    expect(t.width, "the table has the wider column").toBeGreaterThan(r.width);
    // 1 : 1.4, give or take the gap and rounding.
    expect(t.width / r.width).toBeGreaterThan(1.25);
    expect(t.width / r.width).toBeLessThan(1.55);
    // The rendered recipe is read-only and says where to edit it.
    await expect(recipe.getByRole("textbox")).toHaveCount(0);
    await expect(recipe).toContainText("Edit this recipe in its file");

    // Below lg the columns stack: the table above, the recipe beneath, each full width.
    await page.setViewportSize({ width: 1000, height: 900 });
    const r2 = (await recipe.boundingBox())!;
    const t2 = (await table.boundingBox())!;
    expect(r2.y, "the recipe follows the table").toBeGreaterThanOrEqual(t2.y + t2.height);
    expect(Math.abs(r2.width - t2.width)).toBeLessThanOrEqual(2);
  },
);
