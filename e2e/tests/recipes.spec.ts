import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

/**
 * The Cook section (07, 3E; UI-7.3–7.9, 7.16, 7.18) against the example recipes.
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

test("the list shows every example with its title, path and badge, and filters live in the URL", async ({
  page,
}) => {
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
  await page
    .getByRole("group", { name: "Status" })
    .getByRole("button", { name: /^Missing/ })
    .click();
  await expect(page).toHaveURL(/[?&]status=missing/);
  await expect(page.getByText("No recipes match ‘risotto’ that are missing")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).not.toHaveURL(/[?&](q|status|completeness)=/);
  await expect(rows).toHaveCount(EXAMPLES);

  // A filter arriving by URL is honoured: ?completeness=incomplete selects that segment.
  await page.goto("/cook/recipes?completeness=incomplete");
  await expect(
    page.getByRole("group", { name: "Completeness" }).getByRole("button", { name: "Incomplete" }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("the recipe page shows the cost table with a line per ingredient and a basis control in the URL", async ({
  page,
}) => {
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
  await expect(basis.getByRole("button", { name: "Latest" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await basis.getByRole("button", { name: "Cheapest" }).click();
  await expect(page).toHaveURL(/[?&]basis=cheapest/);
  await page.reload();
  await expect(
    page.getByRole("group", { name: "Basis" }).getByRole("button", { name: "Cheapest" }),
  ).toHaveAttribute("aria-pressed", "true");
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
    tableWidth:
      document.querySelector('[role=table][aria-label="Cost by line"]')?.getBoundingClientRect()
        .width ?? 0,
  }));
  expect(scrollWidth, "the recipe page scrolls sideways").toBeLessThanOrEqual(390);
  expect(tableWidth, "the cost table is wider than the viewport").toBeLessThanOrEqual(390);
  // Each line is a block at this width: no column headers are rendered.
  await expect(page.getByRole("columnheader", { name: "Price used" })).toBeHidden();
});

/**
 * The resolve page (07 criterion 28; UI-7.16). One decision for a name applies
 * to every recipe using it, so this runs in one project only: every project
 * shares the backend, and two of them deciding the same name at once would race.
 * Names other specs rely on staying unresolved are left alone, and so are names that
 * are a standard entry's spelling ("butter" for unsalted butter): the picker offers
 * that entry instead of the name itself or "Create new ingredient".
 */
const LEAVE_UNRESOLVED = new Set(["arborio rice", "water"]);
const STANDARD_SPELLINGS = new Set([
  "butter",
  "flour",
  "sugar",
  "yeast",
  "cinnamon",
  "nutmeg",
  "cloves",
]);

test("resolving a name on the resolve page writes the spelling and the recipe page shows the ingredient", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "one decision per name across the shared backend");
  await login(page);
  await indexExamples(page);
  await page.goto("/cook/recipes/resolve");
  await expect(page.getByRole("heading", { level: 1, name: "Resolve recipe names" })).toBeVisible();

  // The first group not reserved by another spec: most-used first, with its recipes as links.
  const rows = page.getByTestId("resolve-row");
  await expect(rows.first()).toBeVisible();
  const names = await rows.evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-resolve-row") ?? ""),
  );
  // The curry's names are cook-acceptance.spec.ts's to resolve, from the keyboard.
  const curry = await rows.evaluateAll((els) =>
    els
      .filter((el) => el.textContent?.includes("Thai green curry"))
      .map((el) => el.getAttribute("data-resolve-row") ?? ""),
  );
  const name = names.find(
    (n) => !LEAVE_UNRESOLVED.has(n) && !STANDARD_SPELLINGS.has(n) && !curry.includes(n),
  );
  expect(name, "a queued name no other spec relies on").toBeTruthy();
  const row = page.locator(`[data-resolve-row="${name}"]`);
  await expect(row).toContainText(/in \d+ recipes?:/);
  const recipeLink = row.getByRole("link").first();
  const recipeHref = await recipeLink.getAttribute("href");
  expect(recipeHref).toMatch(/^\/cook\/recipes\/[0-9a-f-]{36}$/);
  const before = names.length;

  // Choose through the picker. The e2e catalog starts empty, so the option is the
  // standard-list entry (created on choice) or, failing that, "Create new ingredient".
  const picker = row.getByRole("combobox", { name: "Choose an ingredient" });
  await picker.fill(name!);
  const options = page.getByRole("listbox", { name: "Ingredients" }).getByRole("option");
  await expect(options.first()).toBeVisible();
  // The option whose name is exactly this one (the list also ranks longer
  // standard names that start with it), never the first that merely begins with it.
  const named = options.filter({
    has: page.locator("span.font-medium", { hasText: new RegExp(`^${name}$`, "i") }),
  });
  if ((await named.count()) > 0) {
    await named.first().click();
  } else {
    await options.filter({ hasText: "Create new ingredient" }).first().click();
    const drawer = page.getByRole("form", { name: "Add ingredient" });
    await expect(drawer.getByLabel("Name")).toHaveValue(name!);
    await drawer.getByLabel("Category", { exact: true }).selectOption("pantry");
    await page.getByRole("button", { name: "Add ingredient" }).click();
  }

  // The group leaves (a new ingredient's own name may settle another waiting
  // name too), the count strip follows the rows, and the Notice names the count.
  await expect(row).toHaveCount(0);
  const counts = page.getByTestId("resolve-counts");
  await expect.poll(async () => (await rows.count()) < before).toBe(true);
  await expect
    .poll(async () => (await counts.textContent())?.startsWith(`${await rows.count()} name`))
    .toBe(true);
  await expect(page.getByTestId("notice")).toContainText(
    new RegExp(`Resolved '${name}' as .+ in \\d+ recipes?\\. \\d+ left\\.`, "i"),
  );

  // That recipe's page shows the ingredient on the line, in words.
  // The decision recomputed every recipe using the name; the page costs the rest inline.
  await page.goto(recipeHref!);
  await expect(page.getByTestId("cost-totals")).not.toContainText("Costing…", { timeout: 30_000 });
  const table = page.getByRole("table", { name: "Cost by line" });
  await expect(table).toBeVisible();
  // The row whose ingredient cell links the ingredient by that exact name.
  const line = table
    .getByTestId("cost-row")
    .filter({ has: page.getByRole("link", { name: new RegExp(`^${name}$`, "i") }) })
    .first();
  await expect(line).toBeVisible();
  await expect(line).toContainText(name!);
  await expect(line).not.toContainText("Needs an ingredient");
  await expect(line).not.toHaveAttribute("data-status", "unmapped");
});

test("resolve rows stack at 390px with the count strip pinned", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "UI-7.18 is about the 390px viewport");
  await login(page);
  await indexExamples(page);
  await page.goto("/cook/recipes/resolve");
  const rows = page.getByTestId("resolve-row");
  await expect(rows.first()).toBeVisible();
  await page.waitForLoadState("networkidle");

  expect(page.viewportSize()?.width).toBe(390);
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth, "the resolve page scrolls sideways").toBeLessThanOrEqual(390);

  // Every row fits the device width, and its controls are 44px targets.
  for (const box of await rows.evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect().width),
  )) {
    expect(box).toBeLessThanOrEqual(390);
  }
  const first = rows.first();
  for (const control of [
    first.getByRole("combobox", { name: "Choose an ingredient" }),
    first.getByRole("button", { name: "Not an ingredient" }),
  ]) {
    const box = await control.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }

  // The count strip is pinned: scrolled down, it is still at the top of the viewport.
  const counts = page.getByTestId("resolve-counts");
  await expect(counts).toHaveCSS("position", "sticky");
  await page.mouse.wheel(0, 1200);
  await expect(counts).toBeInViewport();
  const top = (await counts.boundingBox())?.y ?? -1;
  expect(top).toBeGreaterThanOrEqual(0);
  expect(top).toBeLessThan(60);
});
