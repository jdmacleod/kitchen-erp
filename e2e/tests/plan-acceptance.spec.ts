import { expect, test, type Browser, type Page } from "@playwright/test";

import {
  controlsUnder44,
  expectNoSidewaysScroll,
  focusRingsMissing,
  login,
  loginAs,
  nextTag,
} from "./helpers";
import { makeChosenTrip, makeTripWorld, type TripWorld } from "./trip-setup";

/**
 * Phase 4 acceptance rows a browser has to show (17 criteria 7, 8, 10, 20, 27;
 * UI-2.6, UI-8.3, 8.6, 8.7, 8.9, 8.11, 8.16, 8.18, 8.19): the list worked by
 * keyboard alone, "Use product…", a second person's check arriving without a
 * reload, the phone Home without the list panel, the kitchen tag on scoped
 * pages only, a switch that leaves household-wide figures alone, Plan measured
 * at 360 and 1000px, "Done here" from the keyboard, and the trip map drawn on
 * the plain ground the stack shows without tiles.
 *
 * Each test makes its own world (`trip-setup.ts`: a kitchen, two invented
 * stores, four priced items on the list, on the synthetic grid) and signs in as
 * a member of its own, because the current kitchen is a per-user setting and
 * the projects run in parallel against one backend. Every name is invented.
 */

interface Setup {
  world: TripWorld;
  stamp: string;
  /** A second kitchen, so the switcher is shown. */
  other: { id: string; name: string };
  member: { email: string; password: string; name: string };
}

/**
 * Tests for one side of lg carry a tag, and the projects on the other side
 * leave them out (`playwright.config.ts`): `@desktop-only` runs on `desktop`,
 * `@below-lg` on the phone and tablet projects.
 */
const DESKTOP_ONLY = { tag: "@desktop-only" };
const BELOW_LG = { tag: "@below-lg" };

/**
 * Home is "Set up your kitchen" until the household has a committed purchase,
 * and only the regular Home has the list panel; on a fresh stack this test may
 * be the first to need one.
 */
async function makePurchase(page: Page, world: TripWorld, stamp: string): Promise<void> {
  const product = await page.request.post("/api/v1/products", {
    data: {
      allow_duplicate: true,
      ingredient: {
        name: `E2E Dulse ${stamp}`,
        canonical_unit: "g",
        category: "pantry",
        keep_name: true,
      },
      name: `E2E Dulse ${stamp} pouch`,
      pack_qty: "100",
      pack_unit: "g",
    },
  });
  expect(product.status(), await product.text()).toBe(201);
  const purchase = await page.request.post("/api/v1/purchases", {
    data: {
      vendor_location_id: world.north.id,
      purchased_at: "2026-09-22T17:10:00Z",
      total: "3.20",
      lines: [
        {
          product_id: ((await product.json()) as { id: string }).id,
          qty: "1",
          unit: "each",
          unit_price: "3.20",
        },
      ],
    },
    headers: { "Idempotency-Key": `e2e-plan-purchase-${stamp}` },
  });
  expect(purchase.status(), await purchase.text()).toBe(201);
}

async function setUp(page: Page, { purchase = false } = {}): Promise<Setup> {
  const stamp = nextTag();
  await login(page);
  const world = await makeTripWorld(page.request, stamp);
  if (purchase) await makePurchase(page, world, stamp);
  const otherName = `E2E Bluff kitchen ${stamp}`;
  const other = await page.request.post("/api/v1/home-bases", {
    data: { name: otherName, lat: "33.881234", lon: "-120.123456" },
  });
  expect(other.status(), await other.text()).toBe(201);
  const member = {
    email: `e2e-plan-${stamp}@example.com`,
    password: "e2e-only-not-a-secret",
    name: `E2E Planner ${stamp}`,
  };
  const user = await page.request.post("/api/v1/users", {
    data: {
      email: member.email,
      display_name: member.name,
      password: member.password,
      role: "member",
    },
  });
  expect(user.status(), await user.text()).toBe(201);
  await page.context().clearCookies();
  await signInAt(page, member, { id: world.kitchenId, name: world.kitchenName });
  return {
    world,
    stamp,
    other: { id: ((await other.json()) as { id: string }).id, name: otherName },
    member,
  };
}

/**
 * Signs in and makes `kitchen` the current kitchen. A new member has no
 * setting, and the switcher may write back the one the fallback chain resolves
 * when the first page mounts; if that lands after ours, the list names the
 * other kitchen, and the setting is written again until it holds.
 */
async function signInAt(
  page: Page,
  who: { email: string; password: string },
  kitchen: { id: string; name: string },
): Promise<void> {
  await loginAs(page, who.email, who.password);
  await expect(async () => {
    const put = await page.request.put("/api/v1/me/settings", {
      data: { current_home_base_id: kitchen.id },
    });
    expect(put.status(), await put.text()).toBe(200);
    await page.goto("/plan/list");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(`${kitchen.name} list`, {
      timeout: 3_000,
    });
  }).toPass({ timeout: 20_000 });
}

const row = (page: Page, name: string) => page.getByTestId("list-item").filter({ hasText: name });

/** Presses Tab (or Shift+Tab) until the focused element has `name`; fails after `max` presses. */
async function tabTo(page: Page, name: string, { back = false, max = 60 } = {}): Promise<void> {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press(back ? "Shift+Tab" : "Tab");
    const label = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return (el?.getAttribute("aria-label") ?? el?.textContent ?? "").trim();
    });
    if (label === name) return;
  }
  throw new Error(
    `no control named "${name}" within ${max} presses of ${back ? "Shift+Tab" : "Tab"}`,
  );
}

test("an item is added, claimed, checked and unchecked without a pointing device (criterion 10, UI-8.11)", async ({
  page,
}) => {
  const { world, stamp } = await setUp(page);
  // A fresh ingredient of this test's own, so the row is the only one by that name.
  const name = `E2E Samphire ${stamp}`;
  const made = await page.request.post("/api/v1/ingredients", {
    data: { name, canonical_unit: "each", category: "pantry", keep_name: true },
  });
  expect(made.status(), await made.text()).toBe(201);
  await page.goto("/plan/list");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(world.kitchenName);
  await expect(page.getByRole("heading", { name: "To get (4)" })).toBeVisible();

  // Add: type in the picker, choose with ArrowDown and Enter (focus moves to the amount), Enter adds.
  const picker = page
    .getByRole("form", { name: "Add an item" })
    .getByRole("combobox", { name: "What do you need?" });
  await picker.focus();
  await page.keyboard.type(name);
  await expect(page.getByRole("option", { name: new RegExp(name) })).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(row(page, name)).toBeVisible();
  await expect(page.getByRole("heading", { name: "To get (5)" })).toBeVisible();

  // Claim: Tab to the row's "I'll get it" and press Enter.
  await row(page, name)
    .getByRole("checkbox", { name: `Check ${name}` })
    .focus();
  await tabTo(page, "I'll get it");
  await expect(row(page, name).locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(row(page, name)).toContainText("You're getting it");

  // Check: back to the row's check circle and Enter; the row moves and the live region counts.
  await tabTo(page, `Check ${name}`, { back: true });
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "To get (4)" })).toBeVisible();
  await expect(page.getByTestId("list-announcement")).toHaveText(`Checked ${name}. 4 to get.`);

  // Uncheck: open Checked from the keyboard, Tab to Uncheck and Enter; the row comes back.
  const checked = page.locator("details").filter({ hasText: "Checked (1)" });
  await checked.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(checked).toHaveAttribute("open", "");
  await tabTo(page, `Uncheck ${name}`);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "To get (5)" })).toBeVisible();
  await expect(row(page, name)).toHaveAttribute("data-checked", "false");
});

test("Use product… switches the picker to products, and the row shows the product with its pack (UI-8.3)", async ({
  page,
}) => {
  const { world } = await setUp(page);
  await page.goto("/plan/list");
  const form = page.getByRole("form", { name: "Add an item" });
  const product = `${world.items.oats} bag`;

  await form.getByRole("button", { name: "Use product…" }).click();
  await expect(form.getByRole("combobox", { name: "What do you need?" })).toHaveCount(0);
  const search = form.getByRole("combobox", { name: "Which product?" });
  await search.fill(product);
  await page
    .getByRole("option", { name: new RegExp(product) })
    .first()
    .click();
  await expect(form.getByTestId("add-product-choice")).toHaveText(product);
  await form.getByRole("button", { name: "Add", exact: true }).click();

  // The new row is the product by name, with its pack, beside the ingredient item already there.
  const added = page.getByTestId("list-item").filter({ hasText: product });
  await expect(added).toHaveCount(1);
  await expect(added).toContainText("1 kg");
  await expect(page.getByRole("heading", { name: "To get (5)" })).toBeVisible();
  // The switch goes back to the ingredient picker.
  await form.getByRole("button", { name: "Use ingredient…" }).click();
  await expect(form.getByRole("combobox", { name: "What do you need?" })).toBeVisible();
});

// Two browsers and a wait: once is enough.
test(
  "a check by a second person moves the row within 15 seconds, with no reload (UI-8.6)",
  DESKTOP_ONLY,
  async ({ page, browser }) => {
    test.setTimeout(90_000);
    const { world, stamp } = await setUp(page);
    const second = await secondPerson(browser, stamp, {
      id: world.kitchenId,
      name: world.kitchenName,
    });
    const oats = world.items.oats;

    await page.goto("/plan/list");
    await expect(row(page, oats)).toBeVisible();
    let navigations = 0;
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) navigations += 1;
    });

    // The other person claims, then checks; this page only listens.
    await second.page.goto("/plan/list");
    await row(second.page, oats).getByRole("button", { name: "I'll get it" }).click();
    await expect(row(page, oats)).toContainText(`${second.name} is getting it`, {
      timeout: 16_000,
    });
    const checkedAt = Date.now();
    await row(second.page, oats)
      .getByRole("checkbox", { name: `Check ${oats}` })
      .click();
    await expect(second.page.getByRole("heading", { name: "To get (3)" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "To get (3)" })).toBeVisible({
      timeout: 16_000,
    });
    expect(Date.now() - checkedAt, "the row moved within the 15-second interval").toBeLessThan(
      16_000,
    );
    const checked = page.locator("details").filter({ hasText: "Checked (1)" });
    await expect(checked).toBeVisible();
    // The claim chip went with the check.
    await expect(page.getByText(`${second.name} is getting it`)).toHaveCount(0);
    expect(navigations, "no reload").toBe(0);
    await second.context.close();
  },
);

/** A second member in a browser context of their own, signed in at the same kitchen. */
async function secondPerson(
  browser: Browser,
  stamp: string,
  kitchen: { id: string; name: string },
) {
  const who = {
    email: `e2e-plan-two-${stamp}@example.com`,
    password: "e2e-only-not-a-secret",
    name: `E2E Second ${stamp}`,
  };
  // The first page is signed in as a member, so the admin makes the user from here.
  const context = await browser.newContext();
  const page = await context.newPage();
  await login(page);
  const user = await page.request.post("/api/v1/users", {
    data: { email: who.email, display_name: who.name, password: who.password, role: "member" },
  });
  expect(user.status(), await user.text()).toBe(201);
  await context.clearCookies();
  await signInAt(page, who, kitchen);
  return { context, page, name: who.name };
}

test(
  "Home shows the list panel, with the kitchen tag, at 1024px and wider (UI-8.9)",
  DESKTOP_ONLY,
  async ({ page }) => {
    const { world } = await setUp(page, { purchase: true });
    await page.goto("/");
    await expect(page.getByRole("region", { name: "Recent purchases" })).toBeVisible();
    const panel = page.getByTestId("shopping-list-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(`${world.kitchenName} list · 4 to get`);
    await expect(panel.getByTestId("kitchen-tag")).toContainText(world.kitchenName);
  },
);

test("Home has no list panel below 1024px (UI-8.9)", BELOW_LG, async ({ page }) => {
  const { world } = await setUp(page, { purchase: true });
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Recent purchases" })).toBeVisible();
  // Rendered for the larger layout but not shown: no box, nothing to read or tab to.
  const panel = page.getByTestId("shopping-list-panel");
  await expect(panel).toHaveCount(1);
  await expect(panel).toBeHidden();
  await expect(
    page.getByRole("heading", { name: new RegExp(`${world.kitchenName} list`) }),
  ).toHaveCount(0);
});

test("the kitchen tag is on the list and Home only, never on a household-wide page (criterion 7, UI-2.6)", async ({
  page,
}) => {
  const { world } = await setUp(page);
  await page.goto("/plan/list");
  await expect(page.getByTestId("kitchen-tag").first()).toContainText(world.kitchenName);
  // Each page once it has stopped loading, so a tag drawn from its data would be there.
  for (const path of [
    "/shop/purchases",
    "/shop/receipts",
    "/catalog/products",
    "/catalog/ingredients",
    "/catalog/vendors",
    "/cook/recipes",
    "/plan/compare",
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("main")).not.toContainText("Loading…");
    await expect(page.locator("main").getByTestId("kitchen-tag"), path).toHaveCount(0);
  }
});

test(
  "switching kitchens refetches the list and changes nothing on Compare, Purchases or the catalog (criterion 8, UI-8.7)",
  DESKTOP_ONLY,
  async ({ page }) => {
    // Switches from the sidebar.
    const { world, other } = await setUp(page);
    const api: string[] = [];
    page.on("request", (req) => {
      const url = new URL(req.url());
      if (url.pathname.startsWith("/api/v1/") && req.method() === "GET") api.push(url.pathname);
    });
    /** Switches, and returns once the setting is written and read back: by then any refetch the switch causes has been asked for. */
    const switchTo = async (name: string) => {
      const readBack = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/me/settings") && r.request().method() === "GET",
      );
      await page.getByRole("button", { name: /^Kitchen:/ }).click();
      await page
        .getByRole("menu", { name: "Switch kitchen" })
        .getByRole("menuitemradio", { name: `Switch to ${name}` })
        .focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("button", { name: `Kitchen: ${name}` })).toBeVisible();
      await readBack;
    };

    // Compare with the filter on All, two stores' prices on screen.
    await page.goto("/plan/compare");
    await page.getByRole("combobox", { name: "Add an ingredient" }).fill(world.items.millet);
    await page
      .getByRole("option", { name: new RegExp(world.items.millet) })
      .first()
      .click();
    const table = page.getByRole("table", { name: "Price comparison" });
    await expect(table).toContainText(world.north.name);
    await expect(table).toContainText(world.south.name);
    const url = page.url();
    const figures = await table.textContent();
    await switchTo(other.name);
    expect(page.url(), "the switch leaves Compare's URL alone").toBe(url);
    await expect(
      page.getByRole("group", { name: "Kitchen" }).getByRole("button", { name: "All" }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(table, "no figure on Compare changed").toHaveText(figures ?? "");

    // Purchases and the catalog: nothing they show is fetched again. Each switch
    // goes to the kitchen not current, so every one is real.
    for (const [path, own, to] of [
      ["/shop/purchases", "/api/v1/purchases", world.kitchenName],
      ["/catalog/products", "/api/v1/products", other.name],
    ] as const) {
      await page.goto(path);
      await expect(page.locator("main").getByRole("table").first()).toBeVisible();
      await expect(page.locator("main")).not.toContainText("Loading…");
      const before = await page.locator("main").textContent();
      api.length = 0;
      await switchTo(to);
      expect(
        api.filter((p) => p.startsWith(own)),
        `${path} fetched again`,
      ).toEqual([]);
      await expect(page.locator("main"), `${path} changed`).toHaveText(before ?? "");
    }

    // The list is scoped: a switch fetches the other kitchen's list and the page follows.
    await page.goto("/plan/list");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(other.name);
    api.length = 0;
    await switchTo(world.kitchenName);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(world.kitchenName);
    expect(api.filter((p) => p.startsWith("/api/v1/plan/list")).length).toBeGreaterThan(0);
  },
);

test(
  "Plan reads with no sideways scroll at 360 to 1000px, and every Plan page keeps 44px targets and a visible focus ring (UI-8.11, UI-8.19)",
  BELOW_LG,
  async ({ page }) => {
    test.setTimeout(150_000);
    const { world } = await setUp(page);
    const tripId = await makeChosenTrip(page.request, world);
    // Each page with its own content drawn, so what is measured is what a person sees.
    const pages = {
      list: ["/plan/list", page.getByTestId("list-item").first()],
      trips: ["/plan/trips", page.getByTestId("trip-row").first()],
      compare: ["/plan/compare", page.getByRole("combobox", { name: "Add an ingredient" })],
      trip: [`/plan/trips/${tripId}`, page.getByTestId("stop").first()],
      stop: [`/plan/trips/${tripId}/stop/1`, page.getByTestId("stop-item").first()],
    } as const;
    const small: string[] = [];
    for (const [path, content] of Object.values(pages)) {
      await page.goto(path);
      await expect(content).toBeVisible();
      await expectNoSidewaysScroll(page, path);
    }
    // Every control a 44px target with a focus ring a keyboard user can see. The
    // trip page's breadcrumb and stop rows' product links were short (issue 282).
    const unringed: string[] = [];
    for (const [path, content] of Object.values(pages)) {
      await page.goto(path);
      await expect(content).toBeVisible();
      small.push(...(await controlsUnder44(page, "main")).map((f) => `${path}: ${f}`));
      unringed.push(...(await focusRingsMissing(page, "main")).map((f) => `${path}: ${f}`));
    }
    expect(small, "Plan controls under 44px").toEqual([]);
    expect(unringed, "Plan controls with no visible focus ring").toEqual([]);
  },
);

test(
  "A trip stop's item keeps its total on the name's first line below 1024px (ruling D1, 2026-10-16)",
  BELOW_LG,
  async ({ page }) => {
    const { world } = await setUp(page);
    const tripId = await makeChosenTrip(page.request, world);
    await page.goto(`/plan/trips/${tripId}`);
    const items = page.getByTestId("stop").first().getByTestId("stop-item");
    await expect(items.first()).toBeVisible();
    // The 44px product link once pushed the total onto a row of its own.
    for (const item of await items.all()) {
      const name = await item.getByRole("link").first().boundingBox();
      const total = await item.locator(":scope > span").last().boundingBox();
      expect(name && total, "item parts drawn").toBeTruthy();
      expect(total!.y, "total on the name's row").toBeLessThan(name!.y + name!.height);
      expect(total!.x, "total to the right of the name").toBeGreaterThan(name!.x);
    }
  },
);

test("Done here is reached and confirmed from the keyboard (criterion 27, UI-8.18)", async ({
  page,
}) => {
  const { world } = await setUp(page);
  const tripId = await makeChosenTrip(page.request, world);
  await page.goto(`/plan/trips/${tripId}/stop/1`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Stop 1 of 2");

  // Tick the first item by keyboard, then Tab on to Done here and press Enter.
  const first = page.getByRole("list", { name: "To get here" }).getByRole("checkbox").first();
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("stop-progress")).toContainText("1 of");
  await tabTo(page, "Done here · record purchase", { max: 80 });
  await page.keyboard.press("Enter");
  const confirm = page.getByTestId("done-confirm");
  await expect(confirm.getByRole("button", { name: "Not yet" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(confirm.getByRole("button", { name: "Record" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("notice")).toContainText(/Recorded 1 line at /);
  await expect(page.getByTestId("stop-done")).toContainText("1 line recorded");
});

/**
 * The share of points along each segment, between `from` and `to` (fractions of
 * its length), with a dark pixel within 3px: the path is a dashed dark line on
 * the plain ground the map draws without tiles. Read from a screenshot of the
 * map, since a WebGL canvas cannot be read back once drawn.
 */
async function darkAlong(
  page: Page,
  png: Buffer,
  cssWidth: number,
  segments: { a: { x: number; y: number }; b: { x: number; y: number } }[],
): Promise<number[]> {
  return page.evaluate(
    async ({ src, cssWidth, segments }) => {
      const img = new Image();
      img.src = src;
      await img.decode();
      const canvas = document.createElement("canvas");
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      const scale = img.width / cssWidth;
      const dark = (x: number, y: number) => {
        const r = Math.round(3 * scale);
        const data = ctx.getImageData(
          Math.round(x * scale) - r,
          Math.round(y * scale) - r,
          2 * r + 1,
          2 * r + 1,
        ).data;
        for (let i = 0; i < data.length; i += 4) {
          if (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2] < 110) return true;
        }
        return false;
      };
      return segments.map(({ a, b }) => {
        let hits = 0;
        let samples = 0;
        for (let t = 0.3; t <= 0.7; t += 0.025) {
          samples += 1;
          if (dark(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)) hits += 1;
        }
        return hits / samples;
      });
    },
    { src: `data:image/png;base64,${png.toString("base64")}`, cssWidth, segments },
  );
}

test(
  "the trip map draws the kitchen pin, numbered stop pins joined by segments, and selects a stop (criterion 20, UI-8.16)",
  DESKTOP_ONLY,
  async ({ page }) => {
    // The map beside the stops, at desktop width.
    const { world } = await setUp(page);
    const tripId = await makeChosenTrip(page.request, world);
    await page.goto(`/plan/trips/${tripId}`);
    const map = page.getByTestId("trip-map");
    const canvas = map.getByTestId("map-canvas");
    await expect(canvas).toBeVisible();

    // The kitchen pin and the stops, numbered in order, each a button naming its store.
    const kitchen = map.getByRole("button", { name: `${world.kitchenName} (kitchen)` });
    await expect(kitchen).toBeVisible();
    await expect(kitchen).toHaveClass(/kerp-pin--home/);
    const stops = map.getByRole("button", { name: /^Stop \d: / });
    await expect(stops).toHaveCount(2);
    await expect(stops).toHaveText(["1", "2"]);
    for (const pin of await stops.all()) await expect(pin).toHaveClass(/kerp-pin--stop/);
    const stores = new Set([world.north.name, world.south.name]);
    for (const label of await stops.evaluateAll((els) =>
      els.map((el) => (el.getAttribute("aria-label") ?? "").replace(/^Stop \d: /, "")),
    )) {
      expect(stores.has(label), `${label} is one of the trip's stores`).toBe(true);
    }

    // Straight segments kitchen -> 1 -> 2 -> kitchen, drawn on the map; nothing off them.
    // The map fits the trip into view after the pins first appear, so the pins'
    // places are read again on every look: read once, they could be where the
    // camera was before it moved, and every sample then misses the line.
    await expect
      .poll(
        async () => {
          const frame = (await canvas.boundingBox())!;
          const centre = async (pin: typeof kitchen) => {
            const b = (await pin.boundingBox())!;
            return { x: b.x + b.width / 2 - frame.x, y: b.y + b.height / 2 - frame.y };
          };
          const k = await centre(kitchen);
          const one = await centre(stops.nth(0));
          const two = await centre(stops.nth(1));
          // A control: the kitchen-to-stop-1 segment moved 40px aside, where no line runs.
          const dx = one.x - k.x;
          const dy = one.y - k.y;
          const length = Math.hypot(dx, dy);
          const aside = (p: { x: number; y: number }) => ({
            x: p.x - (dy / length) * 40,
            y: p.y + (dx / length) * 40,
          });
          const [first, second, back, off] = await darkAlong(
            page,
            await canvas.screenshot(),
            frame.width,
            [
              { a: k, b: one },
              { a: one, b: two },
              { a: two, b: k },
              { a: aside(k), b: aside(one) },
            ],
          );
          return first > 0.6 && second > 0.6 && back > 0.6 && off < 0.2;
        },
        // CI draws WebGL in software, and each look takes and decodes a
        // screenshot, so the default 5 s allowed only a few looks there.
        { timeout: 20_000 },
      )
      .toBe(true);

    // Selecting a stop's pin marks the pin pressed and its stop selected.
    await stops.nth(1).click();
    await expect(stops.nth(1)).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("stop").nth(1)).toHaveAttribute("data-selected", "true");
    await expect(stops.nth(0)).toHaveAttribute("aria-pressed", "false");
  },
);
