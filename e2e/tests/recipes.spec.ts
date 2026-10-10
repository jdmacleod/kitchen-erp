import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

/**
 * The Cook section (07, 3E; UI-7.3–7.9, 7.18) against the example recipes.
 *
 * The e2e stack mounts examples/recipes read-only at /data/recipes
 * (compose.e2e.yaml; CI exports RECIPES_PATH to the same directory), so the
 * fifteen MIT-licensed examples are what gets indexed. Nothing is created here:
 * every spec in the suite shares one backend, and a scan is idempotent, so each
 * test asks for one and goes on. The directory has no .git, which the indexer
 * reads as a repository with no commits: every file is "Uncommitted".
 */

/** One example, by its front-matter title; its path is what the list shows beneath. */
const RISOTTO = { title: "Risotto alla Milanese", path: "mains/risotto.cook" };
const EXAMPLES = 15;

async function indexExamples(page: Page): Promise<void> {
  const scanned = await page.request.post("/api/v1/recipes/rescan");
  expect(scanned.status()).toBe(200);
  const status = await page.request.get("/api/v1/recipes/status");
  expect(status.status()).toBe(200);
  const body = (await status.json()) as { mounted: boolean; total: number; counts: { ok: number } };
  expect(body.mounted, "the e2e stack mounts examples/recipes at /data/recipes").toBe(true);
  expect(body.counts.ok).toBeGreaterThanOrEqual(EXAMPLES);
}

test("the list shows every example with its title, path and badge, and filters live in the URL", async ({ page }) => {
  await login(page);
  await indexExamples(page);
  await page.goto("/cook/recipes");

  await expect(page.getByRole("heading", { level: 1, name: "Recipes" })).toBeVisible();
  const rows = page.getByTestId("recipe-row");
  await expect(rows).toHaveCount(EXAMPLES);
  const risotto = rows.filter({ hasText: RISOTTO.title });
  await expect(risotto.getByRole("link", { name: RISOTTO.title })).toBeVisible();
  await expect(risotto).toContainText(RISOTTO.path);
  // Badges are words (UI-7.4): a mount without .git is uncommitted work.
  await expect(risotto).toContainText("Uncommitted");

  // Search goes to ?q= after the debounce and the server filters.
  await page.getByLabel("Search recipes").fill("risotto");
  await expect(page).toHaveURL(/[?&]q=risotto/);
  await expect(rows).toHaveCount(1);

  // The status control writes ?status=; no example is missing, so the list says what it filtered.
  await page.getByRole("group", { name: "Status" }).getByRole("button", { name: /^Missing/ }).click();
  await expect(page).toHaveURL(/[?&]status=missing/);
  await expect(page.getByText("No recipes match ‘risotto’ that are missing")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).not.toHaveURL(/[?&](q|status|completeness)=/);
  await expect(rows).toHaveCount(EXAMPLES);

  // A filter arriving by URL is honoured: ?completeness=incomplete selects that segment.
  await page.goto("/cook/recipes?completeness=incomplete");
  await expect(page.getByRole("group", { name: "Completeness" }).getByRole("button", { name: "Incomplete" })).toHaveAttribute("aria-pressed", "true");
});

test("the recipe page shows the cost table with a line per ingredient and a basis control in the URL", async ({ page }) => {
  await login(page);
  await indexExamples(page);
  await page.goto("/cook/recipes?q=risotto");
  await page.getByRole("link", { name: RISOTTO.title }).click();
  await expect(page).toHaveURL(/\/cook\/recipes\/[0-9a-f-]{36}(\?.*)?$/);
  await expect(page.getByRole("heading", { level: 1, name: RISOTTO.title })).toBeVisible();

  // The totals and the table (UI-7.9): the risotto names eleven ingredient lines
  // (butter twice), of which salt and water are negligible by default.
  const totals = page.getByTestId("cost-totals");
  await expect(totals).toBeVisible();
  await expect(totals).not.toContainText("Costing…");
  const table = page.getByRole("table", { name: "Cost by line" });
  await expect(table).toBeVisible();
  const lines = table.getByTestId("cost-row");
  await expect(lines).toHaveCount(11);
  // The line as written, then its state in words: nothing is resolved on a fresh stack.
  await expect(lines.filter({ hasText: "arborio rice" })).toContainText("Needs an ingredient");
  await expect(lines.filter({ hasText: "water" }).first()).toContainText("Not costed");

  // The basis control (UI-7.8): Latest is the default and absent from the URL;
  // another basis is kept in ?basis= and survives a reload.
  const basis = page.getByRole("group", { name: "Basis" });
  await expect(basis.getByRole("button", { name: "Latest" })).toHaveAttribute("aria-pressed", "true");
  await basis.getByRole("button", { name: "Cheapest" }).click();
  await expect(page).toHaveURL(/[?&]basis=cheapest/);
  await page.reload();
  await expect(page.getByRole("group", { name: "Basis" }).getByRole("button", { name: "Cheapest" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();

  // The rendered recipe is there too, with its sections as headings.
  await expect(page.getByRole("heading", { name: "Cook the rice" })).toBeVisible();
});

test("the cost table reads without horizontal scroll at 390px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "criterion 32 is about the 390px viewport");
  await login(page);
  await indexExamples(page);
  await page.goto("/cook/recipes?q=risotto");
  await page.getByRole("link", { name: RISOTTO.title }).click();
  await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();
  await page.waitForLoadState("networkidle");

  // Against the device width, not innerWidth: an overflowing layout makes the
  // mobile browser zoom out and grow innerWidth (see narrow-phone.spec.ts).
  const deviceWidth = page.viewportSize()?.width;
  expect(deviceWidth).toBe(390);
  const { scrollWidth, tableWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    tableWidth: document.querySelector('[role=table][aria-label="Cost by line"]')?.getBoundingClientRect().width ?? 0,
  }));
  expect(scrollWidth, "the recipe page scrolls sideways").toBeLessThanOrEqual(390);
  expect(tableWidth, "the cost table is wider than the viewport").toBeLessThanOrEqual(390);
  // Each line is a block at this width: no column headers are rendered.
  await expect(page.getByRole("columnheader", { name: "Price used" })).toBeHidden();
});
