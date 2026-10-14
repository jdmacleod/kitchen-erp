import { expect, test, type Page } from "@playwright/test";

import { controlsUnder44, expectNoSidewaysScroll, login, loginAs, nextTag } from "./helpers";

/**
 * Sub-phase 4A against the stack (17 §4A; UI-2.4–2.7, UI-8.1–8.3, 8.5–8.8,
 * 8.10, 8.11, 8.19): the Plan section, the kitchen switcher, the list page,
 * Add from recipe, a fresh list, and Compare under Plan with its kitchen control.
 *
 * The e2e stack is migrated and given one admin; nothing is seeded (`make
 * e2e-up`), so this spec makes what it needs through the API. The three
 * projects run in parallel against one backend and the current kitchen is a
 * per-user server setting, so each worker signs in as a member of its own:
 * switching here never moves another worker's kitchen. Every kitchen and
 * ingredient carries the run's stamp; the coordinates are the synthetic grid
 * from SECURITY.md, and every name is invented.
 */

const stamp = nextTag();
const COOK = {
  email: `e2e-cook-${stamp}@example.com`,
  password: "e2e-only-not-a-secret",
  name: `E2E Cook ${stamp}`,
};
const HARBOUR = `E2E Harbour kitchen ${stamp}`;
const RIDGE = `E2E Ridge kitchen ${stamp}`;
const RHUBARB = `E2E Rhubarb ${stamp}`;
const CHARD = `E2E Chard ${stamp}`;

/**
 * An example recipe (MIT) whose lines resolve once the ingredients exist by
 * name. Its third line, salt, is on the negligible list (07, criterion 16), so
 * Add from recipe lands two items, not three.
 */
const RECIPE = { title: "Self-raising flour", lines: ["flour", "baking powder", "salt"], added: 2 };

interface Setup {
  harbourId: string;
  ridgeId: string;
}

/** A kitchen by name, made on first use; the tests of one worker share it. */
async function ensureKitchen(page: Page, name: string, lat: string, lon: string): Promise<string> {
  const r = await page.request.post("/api/v1/home-bases", { data: { name, lat, lon } });
  if (r.status() === 201) return ((await r.json()) as { id: string }).id;
  expect(r.status(), `kitchen ${name}`).toBe(409);
  const list = await page.request.get("/api/v1/home-bases");
  const found = ((await list.json()) as { items: { id: string; name: string }[] }).items.find(
    (k) => k.name === name,
  );
  expect(found, `kitchen ${name} exists`).toBeTruthy();
  return found!.id;
}

/** As the admin: two kitchens, the ingredients and a member of this run's own; then signed in as that member. */
async function setUp(page: Page): Promise<Setup> {
  await login(page);
  const harbourId = await ensureKitchen(page, HARBOUR, "33.512345", "-120.487654");
  const ridgeId = await ensureKitchen(page, RIDGE, "33.887654", "-120.112345");
  // The run's own ingredients, and the recipe's names so its lines resolve
  // (criterion 14); a 409 means an earlier test or another worker made one
  // first, which is the same outcome.
  for (const [name, unit] of [
    [RHUBARB, "each"],
    [CHARD, "each"],
    ...RECIPE.lines.map((line) => [line, "g"]),
  ]) {
    const r = await page.request.post("/api/v1/ingredients", {
      data: { name, canonical_unit: unit, category: "pantry", keep_name: true },
    });
    expect([201, 409], `ingredient ${name}`).toContain(r.status());
  }
  const scanned = await page.request.post("/api/v1/recipes/rescan");
  expect(scanned.status()).toBe(200);
  const user = await page.request.post("/api/v1/users", {
    data: { email: COOK.email, display_name: COOK.name, password: COOK.password, role: "member" },
  });
  expect([201, 409], "a member of this run's own").toContain(user.status());
  await page.context().clearCookies();
  await loginAs(page, COOK.email, COOK.password);
  return { harbourId, ridgeId };
}

const isPhone = (project: string) => project !== "desktop";

/** The chip that names the current kitchen: in the sidebar footer, or at the top of the More sheet (opened here). */
async function kitchenChip(page: Page, project: string) {
  if (isPhone(project)) {
    await page
      .getByRole("navigation", { name: "Tabs" })
      .getByRole("button", { name: "More" })
      .click();
    return page
      .getByRole("dialog", { name: "More" })
      .getByTestId("kitchen-switcher-row")
      .getByRole("button", { name: /^Kitchen:/ });
  }
  return page.getByRole("button", { name: /^Kitchen:/ });
}

/** The kitchen the chip names now. A new member has no setting, so it is whichever the fallback chain resolved. */
async function currentKitchen(page: Page, project: string): Promise<string> {
  const chip = await kitchenChip(page, project);
  const name = ((await chip.textContent()) ?? "").replace(/^\s*Kitchen:\s*/, "").trim();
  if (isPhone(project)) {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "More" })).toBeHidden();
  }
  return name;
}

/** Switches kitchens the way this layout offers: the sidebar menu, or the More sheet's radios. Choosing the current one changes nothing. */
async function switchTo(page: Page, project: string, name: string): Promise<void> {
  const chip = await kitchenChip(page, project);
  const already = ((await chip.textContent()) ?? "").includes(name);
  if (isPhone(project)) {
    const sheet = page.getByRole("dialog", { name: "More" });
    const row = sheet.getByTestId("kitchen-switcher-row");
    if (!already) {
      await chip.click();
      // A click, not check(): choosing a radio closes the list, so there is no checked state left to verify.
      await row.getByRole("radio", { name }).click();
    }
    // Switching is not a navigation: the sheet stays until it is dismissed.
    await expect(row.getByRole("button", { name: `Kitchen: ${name}` })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  } else {
    if (already) return;
    await chip.click();
    // Chosen by keyboard: the menu opens upward from the footer, and a stack that has run this spec many times has more kitchens than fit.
    await page
      .getByRole("menu", { name: "Switch kitchen" })
      .getByRole("menuitemradio", { name: `Switch to ${name}` })
      .focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: `Kitchen: ${name}` })).toBeVisible();
  }
}

/** The page's secondary actions: beside the title, or behind "More actions" below 1024px. */
async function listAction(page: Page, name: string): Promise<void> {
  const more = page.getByRole("button", { name: "More actions", exact: true });
  if (await more.isVisible()) {
    await more.click();
    await page.getByRole("dialog", { name: "More actions" }).getByRole("button", { name }).click();
  } else {
    await page.getByRole("button", { name, exact: true }).click();
  }
}

const row = (page: Page, name: string) => page.getByTestId("list-item").filter({ hasText: name });

test("Plan appears with the feature, the switcher lists the kitchens, and switching writes the setting and changes the tag", async ({
  page,
}, testInfo) => {
  const { harbourId, ridgeId } = await setUp(page);
  const project = testInfo.project.name;

  // UI-8.1 / UI-8.10: the section in the sidebar, or the List tab with Purchases under More.
  await page.goto("/");
  if (isPhone(project)) {
    const tabs = page.getByRole("navigation", { name: "Tabs" });
    await expect(tabs.getByRole("link", { name: "List" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Purchases" })).toHaveCount(0);
    await tabs.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    await expect(sheet.getByRole("link", { name: "Purchases" })).toBeVisible();
    await expect(sheet.getByTestId("kitchen-switcher-row")).toBeVisible();
    await page.keyboard.press("Escape");
    await tabs.getByRole("link", { name: "List" }).click();
    await expect(page).toHaveURL(/\/plan\/list$/);
  } else {
    // The sidebar is an accordion: Plan sits between Cook and Shop, and only the open section lists its pages (UI-2.1).
    const sidebar = page.getByRole("navigation", { name: "Main" }).first();
    await expect(sidebar.getByRole("list").first().getByRole("link")).toHaveText([
      /^Home/,
      /^Cook/,
      /^Plan/,
      /^Shop/,
    ]);
    await sidebar.getByRole("link", { name: "Plan" }).click();
    await expect(page).toHaveURL(/\/plan\/list$/);
    await expect(sidebar.getByRole("link", { name: "Shopping list" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Trips" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Compare prices" })).toHaveCount(1);
  }

  // UI-2.4 / UI-2.5 / UI-8.7: both kitchens are offered; a choice PUTs the setting and the tag follows.
  // A new member has no setting, so the chain picked one; switch to the other first, so both switches are real.
  const current = await currentKitchen(page, project);
  const [first, second, firstId, secondId] =
    current === HARBOUR
      ? [RIDGE, HARBOUR, ridgeId, harbourId]
      : [HARBOUR, RIDGE, harbourId, ridgeId];
  const puts: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "PUT" && req.url().includes("/api/v1/me/settings"))
      puts.push(req.postData() ?? "");
  });
  await switchTo(page, project, first);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${first} list`);
  await expect(page.getByTestId("kitchen-tag")).toContainText(first);
  await expect.poll(() => puts.some((body) => body.includes(firstId))).toBe(true);

  await switchTo(page, project, second);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${second} list`);
  await expect(page.getByTestId("kitchen-tag")).toContainText(second);
  await expect.poll(() => puts.some((body) => body.includes(secondId))).toBe(true);

  // UI-2.5: the setting is what a reload reads back.
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${second} list`);
  const settings = await page.request.get("/api/v1/me/settings");
  expect(((await settings.json()) as { current_home_base_id: string }).current_home_base_id).toBe(
    secondId,
  );
});

test("the list: add by name, claim and check, add from a recipe, then start a fresh list that carries the unchecked item", async ({
  page,
}, testInfo) => {
  await setUp(page);
  const project = testInfo.project.name;
  await page.goto("/plan/list");
  await switchTo(page, project, HARBOUR);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${HARBOUR} list`);

  // UI-8.3: an item by name with an amount; the row shows the amount as written.
  const add = page.getByRole("form", { name: "Add an item" });
  await add.getByRole("combobox", { name: "What do you need?" }).fill(RHUBARB);
  await page.getByRole("option", { name: new RegExp(RHUBARB) }).click();
  await add.getByLabel("Amount", { exact: true }).fill("3");
  await add.getByLabel("Unit", { exact: true }).selectOption("each");
  await add.getByRole("button", { name: "Add", exact: true }).click();
  await expect(row(page, RHUBARB)).toBeVisible();
  await expect(row(page, RHUBARB).getByTestId("item-amounts")).toContainText("3");
  await expect(page.getByRole("heading", { name: "To get (1)" })).toBeVisible();

  // UI-8.6: claim, then check; the row moves to Checked and the live region counts down.
  await row(page, RHUBARB).getByRole("button", { name: "I'll get it" }).click();
  await expect(row(page, RHUBARB)).toContainText("You're getting it");
  await add.getByRole("combobox", { name: "What do you need?" }).fill(CHARD);
  await page.getByRole("option", { name: new RegExp(CHARD) }).click();
  await add.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("heading", { name: "To get (2)" })).toBeVisible();
  await row(page, RHUBARB)
    .getByRole("checkbox", { name: `Check ${RHUBARB}` })
    .click();
  await expect(page.getByRole("heading", { name: "To get (1)" })).toBeVisible();
  await expect(page.getByTestId("list-announcement")).toHaveText(`Checked ${RHUBARB}. 1 to get.`);
  const checked = page.locator("details").filter({ hasText: "Checked (1)" });
  await expect(checked).toBeVisible();
  await checked.locator("summary").click();
  await expect(checked.getByTestId("list-item").filter({ hasText: RHUBARB })).toHaveAttribute(
    "data-checked",
    "true",
  );

  // UI-8.5: a recipe's lines land with their (amount, unit) pairs as written.
  await listAction(page, "Add from recipe");
  const drawer = page.getByRole("dialog", { name: "Add from recipe" });
  await drawer.getByRole("combobox", { name: "Recipe" }).fill("Self-raising");
  await page.getByRole("option", { name: new RegExp(RECIPE.title) }).click();
  await drawer.getByRole("button", { name: "Add to list" }).click();
  await expect(drawer).toBeHidden();
  await expect(page.getByTestId("notice")).toHaveText(
    `Added ${RECIPE.added} items from ${RECIPE.title}.`,
  );
  await expect(page.getByRole("heading", { name: "To get (3)" })).toBeVisible();
  // "1 cup" of flour cannot become grams without a density, so the pair stays as written.
  await expect(row(page, "flour").first().getByTestId("item-amounts")).toContainText(/1.*cup/);
  await expect(row(page, "baking powder").getByTestId("item-amounts")).toContainText(/tsp/);
  await expect(row(page, "flour").first()).toContainText("from recipe");

  // UI-8.2: a fresh list confirms inline, carries the unchecked items and leaves the checked one behind.
  await listAction(page, "Start a fresh list");
  const confirm = page.getByTestId("fresh-list-confirm");
  await expect(confirm).toContainText("3 unchecked items carry over.");
  await expect(confirm.getByRole("button", { name: "Cancel" })).toBeFocused();
  await expect(confirm.getByRole("checkbox", { name: "Carry unchecked items over" })).toBeChecked();
  await confirm.getByRole("button", { name: "Start a fresh list" }).click();
  await expect(page.getByTestId("notice")).toContainText("Started a fresh list");
  await expect(page.getByRole("heading", { name: "To get (3)" })).toBeVisible();
  await expect(row(page, CHARD)).toBeVisible();
  await expect(page.locator("details").filter({ hasText: /^Checked/ })).toHaveCount(0);

  // The old list is readable at ?list_id= and offers nothing to change.
  await page.getByTestId("notice").getByRole("link", { name: "Open the old one" }).click();
  await expect(page).toHaveURL(/\/plan\/list\?list_id=/);
  await expect(page.getByText("Archived", { exact: true })).toBeVisible();
  await expect(page.getByRole("form", { name: "Add an item" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to the current list" })).toBeVisible();
});

test("Compare under Plan offers the kitchen control, All by default, and keeps a chosen kitchen in the URL", async ({
  page,
}, testInfo) => {
  const { harbourId } = await setUp(page);
  await page.goto("/shop/compare");
  await expect(page).toHaveURL(/\/plan\/compare$/);
  await expect(page.getByRole("heading", { level: 1, name: "Compare" })).toBeVisible();
  const control = page.getByRole("group", { name: "Kitchen" });
  await expect(control.getByRole("button", { name: "All" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("kitchen-tag")).toHaveCount(0);
  await control.getByRole("button", { name: HARBOUR }).click();
  await expect(page).toHaveURL(new RegExp(`home_base_id=${harbourId}`));
  await expect(page.getByTestId("kitchen-tag")).toContainText(HARBOUR);
  // UI-8.19: the control's targets below 1024px.
  if (isPhone(testInfo.project.name)) {
    expect(await controlsUnder44(page, "main"), "controls under 44px on Compare").toEqual([]);
  }
});

test("at 390px the list, Trips and Compare fit the viewport and every control is a 44px target", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "the 390px viewport is the phone project");
  await setUp(page);
  await page.goto("/plan/list");
  await switchTo(page, "phone", HARBOUR);
  const add = page.getByRole("form", { name: "Add an item" });
  await add.getByRole("combobox", { name: "What do you need?" }).fill(RHUBARB);
  await page.getByRole("option", { name: new RegExp(RHUBARB) }).click();
  await add.getByRole("button", { name: "Add", exact: true }).click();
  await expect(row(page, RHUBARB)).toBeVisible();
  await row(page, RHUBARB).getByRole("button", { name: "I'll get it" }).click();
  await expect(row(page, RHUBARB)).toContainText("You're getting it");

  const small: string[] = [];
  for (const path of ["/plan/list", "/plan/trips", "/plan/compare"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expectNoSidewaysScroll(page, path);
    small.push(
      ...(await controlsUnder44(page, "header, nav[aria-label=Tabs], main")).map(
        (f) => `${path}: ${f}`,
      ),
    );
  }
  // The list's own sheets render outside main: More actions, and the row's actions.
  await page.goto("/plan/list");
  await expect(row(page, RHUBARB)).toBeVisible();
  await page.getByRole("button", { name: "More actions", exact: true }).click();
  small.push(...(await controlsUnder44(page, "[role=dialog]")).map((f) => `More actions: ${f}`));
  await page.keyboard.press("Escape");
  await row(page, RHUBARB)
    .getByRole("button", { name: `More actions for ${RHUBARB}` })
    .click();
  small.push(...(await controlsUnder44(page, "[role=dialog]")).map((f) => `row actions: ${f}`));
  await page.keyboard.press("Escape");
  expect(small, "controls under 44px").toEqual([]);
});
