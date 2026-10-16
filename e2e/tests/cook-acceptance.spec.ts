import { expect, test, type Page } from "@playwright/test";

import { controlsUnder44, focusRingsMissing, login, nextTag } from "./helpers";

/**
 * Phase 3 acceptance rows a browser has to show (07 criterion 31; UI-7.1, 7.7,
 * 7.12, 7.18): Cook under More on a phone, the recipe page's two columns
 * measured at 1024px, the Cook controls' 44px floor and focus ring, and a
 * ten-line recipe resolved and pinned from the keyboard alone.
 *
 * Like recipes.spec.ts, this runs against the example recipes the e2e stack
 * mounts read-only. Names resolved here are the Thai green curry's, which
 * recipes.spec.ts leaves alone; the ingredients and products they resolve to
 * are invented and carry the run's stamp.
 */

const stamp = nextTag();
const RISOTTO = "Risotto alla Milanese";
const CURRY = "Thai green curry";
const DESKTOP_ONLY = { tag: "@desktop-only" };
const BELOW_LG = { tag: "@below-lg" };

async function indexExamples(page: Page): Promise<void> {
  expect((await page.request.post("/api/v1/recipes/rescan")).status()).toBe(200);
}

/** Opens a recipe from the list and returns its path. */
async function openRecipe(page: Page, title: string): Promise<string> {
  await page.goto(`/cook/recipes?q=${encodeURIComponent(title.split(" ")[0])}`);
  await page.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(/\/cook\/recipes\/[0-9a-f-]{36}(\?.*)?$/);
  await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();
  await expect(page.getByTestId("cost-totals")).not.toContainText("Costing…");
  return new URL(page.url()).pathname;
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

// The 44px floor is below lg; projects below lg run it (playwright.config.ts).
test(
  "below 1024px every Cook control is a 44px target with a visible focus ring (UI-7.18)",
  BELOW_LG,
  async ({ page }) => {
    test.setTimeout(90_000);
    await login(page);
    await indexExamples(page);
    const small: string[] = [];
    const unringed: string[] = [];
    const recipePath = await openRecipe(page, RISOTTO);
    // Each page with its own content drawn: the list with its names-waiting line,
    // the resolve rows with their recipe links, the recipe with its cost.
    const pages = [
      ["/cook/recipes", page.getByTestId("names-waiting-line").getByRole("link")],
      ["/cook/recipes/resolve", page.getByTestId("resolve-row").first().getByRole("link").first()],
      [recipePath, page.getByTestId("cost-row").first()],
    ] as const;
    for (const [path, content] of pages) {
      await page.goto(path);
      await expect(content).toBeVisible();
      small.push(...(await controlsUnder44(page, "main")).map((f) => `${path}: ${f}`));
      unringed.push(...(await focusRingsMissing(page, "main")).map((f) => `${path}: ${f}`));
    }
    expect(small, "Cook controls under 44px").toEqual([]);
    expect(unringed, "Cook controls with no visible focus ring").toEqual([]);
  },
);

// One project: a decision applies to every recipe using the name, on the one shared backend.
test(
  "a ten-line recipe is resolved and pinned without a pointing device (criterion 31, UI-7.12)",
  DESKTOP_ONLY,
  async ({ page }) => {
    test.setTimeout(240_000);
    await login(page);
    await indexExamples(page);
    const curryPath = await openRecipe(page, CURRY);
    const table = page.getByRole("table", { name: "Cost by line" });
    const pickers = table.getByRole("combobox", { name: "Choose an ingredient" });
    const waiting = await pickers.count();
    expect(waiting, "the curry has at least ten lines to resolve").toBeGreaterThanOrEqual(10);

    // The names waiting, in the table's order, from the same cost the page shows.
    const cost = await page.request.get(`/api/v1${curryPath.replace("/cook", "")}/cost`);
    const body = (await cost.json()) as {
      lines: { status: string; line: { raw_name: string } }[];
    };
    const raws = body.lines.filter((l) => l.status === "unmapped").map((l) => l.line.raw_name);
    expect(raws).toHaveLength(waiting);

    // An invented ingredient and product for each name, made through the API.
    const ingredientFor = (raw: string) => `E2E Curry ${raw} ${stamp}`;
    for (const raw of raws) {
      const made = await page.request.post("/api/v1/products", {
        data: {
          allow_duplicate: true,
          ingredient: {
            name: ingredientFor(raw),
            canonical_unit: "g",
            category: "pantry",
            keep_name: true,
          },
          name: `${ingredientFor(raw)} pack`,
          pack_qty: "500",
          pack_unit: "g",
        },
      });
      expect(made.status(), await made.text()).toBe(201);
    }

    // Resolve: type, ArrowDown and Enter in the focused picker. Each decision
    // recomputes the cost; focus stays on the next line's picker through it.
    const focusedRow = table.locator("[data-testid=cost-row]:has(:focus)");
    await pickers.first().focus();
    for (const [i, raw] of raws.entries()) {
      const box = focusedRow.getByRole("combobox", { name: "Choose an ingredient" });
      await expect(box, `line ${i + 1} (${raw}) has focus`).toBeFocused();
      await expect(focusedRow).toContainText(raw);
      await page.keyboard.type(ingredientFor(raw));
      await expect(
        focusedRow.getByRole("option", { name: new RegExp(ingredientFor(raw)) }).first(),
      ).toBeVisible();
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("notice")).toContainText(`Resolved '${raw}'`);
      // The recomputed cost has rendered: the line now shows its ingredient.
      await expect(pickers).toHaveCount(waiting - i - 1);
    }
    await expect(pickers).toHaveCount(0);

    // Pin: Enter on "Pin product…", ArrowDown and Enter in the picker; focus
    // moves to the next line's "Pin product…" and stays there after the refetch.
    const pinButtons = table.getByRole("button", { name: "Pin product…" });
    // Every line resolved above offers a pin once the last decision's cost has arrived.
    await expect(pinButtons).toHaveCount(raws.length);
    const toPin = raws.length;
    await pinButtons.first().focus();
    for (let i = 0; i < toPin; i++) {
      await expect(page.locator(":focus"), `pin ${i + 1}`).toHaveAccessibleName("Pin product…");
      await page.keyboard.press("Enter");
      await expect(table.getByRole("combobox", { name: "Pin product" })).toBeFocused();
      await expect(
        table.getByRole("listbox", { name: "Products" }).getByRole("option"),
      ).toHaveCount(1);
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(table.getByTestId("pinned")).toHaveCount(i + 1);
    }
    await expect(pinButtons).toHaveCount(0);
    await expect(table.getByTestId("pinned")).toHaveCount(toPin);
  },
);
