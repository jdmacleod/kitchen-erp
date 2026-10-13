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
  await page
    .getByRole("option", { name: new RegExp(name, "i") })
    .first()
    .click();
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
  await page
    .getByRole("link", { name: /Extra virgin olive oil/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/products\/[0-9a-f-]{36}$/);
  await shot(page, "product-prices");

  // The purchase list, and one purchase opened.
  await page.goto("/purchases");
  await expect(page.getByRole("heading", { level: 1, name: "Purchases" })).toBeVisible();
  await shot(page, "purchases");

  // The date cell is the link into a purchase.
  await page
    .getByRole("link", { name: /\b20\d\d\b/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/purchases\/[0-9a-f-]{36}$/);
  await shot(page, "purchase-detail");
});

/**
 * The Cook pages, at desktop width and at 390px (UI-7.18). The demo stack
 * mounts examples/recipes, so a rescan indexes the fifteen example recipes; the
 * seed's spellings let most of the risotto's lines cost from the price book.
 */
test("capture the Cook screenshots", async ({ page }) => {
  await signIn(page);
  await assertDemoData(page);

  const scanned = await page.request.post("/api/v1/recipes/rescan");
  expect(scanned.status()).toBe(200);

  const cookPages: Array<[string, () => Promise<void>]> = [
    [
      "recipes",
      async () => {
        await page.goto("/cook/recipes");
        await expect(page.getByTestId("recipe-row").first()).toBeVisible();
      },
    ],
    [
      "recipe-cost",
      async () => {
        await page.goto("/cook/recipes");
        await page.getByRole("link", { name: "Risotto alla Milanese" }).click();
        await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();
      },
    ],
    [
      "recipes-resolve",
      async () => {
        await page.goto("/cook/recipes/resolve");
        await expect(
          page.getByRole("heading", { level: 1, name: "Resolve recipe names" }),
        ).toBeVisible();
      },
    ],
  ];

  for (const [name, open] of cookPages) {
    await page.setViewportSize({ width: 1280, height: 800 });
    await open();
    await shot(page, name);
    // The phone layout: stacked rows and cost-table blocks, no sideways scroll.
    await page.setViewportSize({ width: 390, height: 844 });
    await open();
    await shot(page, `${name}-phone`);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
});

/**
 * The Plan pages (17, 4A): the first kitchen's list as the seed fills it, at
 * desktop width and at 390px, and Compare under Plan with its kitchen control.
 * The demo household has two kitchens, so the switcher is in the sidebar.
 */
test("capture the Plan screenshots", async ({ page }) => {
  await signIn(page);
  await assertDemoData(page);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/plan/list");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Harbour Flat list");
  await expect(page.getByTestId("list-item").first()).toBeVisible();
  await shot(page, "plan-list");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/plan/list");
  await expect(page.getByTestId("list-item").first()).toBeVisible();
  await shot(page, "plan-list-phone");

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/plan/compare");
  for (const name of ["Olive oil", "Whole milk", "Coffee beans"]) {
    await pickIngredient(page, name);
  }
  await page
    .getByRole("group", { name: "Kitchen" })
    .getByRole("button", { name: "Harbour Flat" })
    .click();
  await expect(page.getByTestId("kitchen-tag")).toContainText("Harbour Flat");
  await expect(page.getByRole("table", { name: "Price comparison" })).toBeVisible();
  await shot(page, "plan-compare");
});

/**
 * Trips (17, 4B and 4C): a plan for the first kitchen's list, made and chosen
 * here through the API, since the seed plans nothing. Two shelf prices noticed
 * at the Saturday produce stall (invented, like the rest of the demo) give the
 * Cheapest plan a second stop, so the page shows a trade and a route. The demo
 * stack has no routing engine, so the legs are estimates and the cards say so.
 * The trip page at desktop width and at 390px, then its first stop on the
 * phone with one item in the basket.
 */
test("capture the Trip screenshots", async ({ page }) => {
  await signIn(page);
  await assertDemoData(page);

  const kitchens = (await (await page.request.get("/api/v1/home-bases")).json()) as {
    items: { id: string; name: string }[];
  };
  const kitchen = kitchens.items.find((k) => k.name === "Harbour Flat");
  expect(kitchen, "the demo's first kitchen").toBeTruthy();
  const locations = (await (await page.request.get("/api/v1/vendor-locations")).json()) as {
    items: { id: string; name: string }[];
  };
  const stall = locations.items.find((l) => l.name === "Bramble Row Produce");
  expect(stall, "the demo's produce stall").toBeTruthy();
  for (const [query, price] of [
    ["Lemons", "1.20"],
    ["Basil", "1.10"],
  ] as const) {
    const found = (await (
      await page.request.get(`/api/v1/products?q=${encodeURIComponent(query)}`)
    ).json()) as { items: { id: string }[] };
    expect(found.items.length, query).toBeGreaterThan(0);
    const noted = await page.request.post("/api/v1/price-observations", {
      data: { product_id: found.items[0].id, vendor_location_id: stall!.id, price, unit: "each" },
    });
    expect(noted.status(), await noted.text()).toBe(201);
  }
  // The coming Saturday at 10:00 in the household's zone (17:00 UTC in summer), when the stall is open.
  const when = new Date();
  when.setUTCDate(when.getUTCDate() + ((6 - when.getUTCDay() + 7) % 7 || 7));
  when.setUTCHours(17, 0, 0, 0);
  const made = await page.request.post("/api/v1/plan/trips", {
    data: {
      home_base_id: kitchen!.id,
      planned_at: when.toISOString(),
      max_stops: 3,
      value_of_time_per_hour: "5.00",
      mileage_rate: "0.67",
    },
  });
  expect(made.status(), await made.text()).toBe(201);
  const plan = (await made.json()) as {
    id: string;
    variants: { id: string; kind: string; stop_count: number }[];
  };
  const cheapest = plan.variants.find((v) => v.kind === "cheapest")!;
  expect(cheapest.stop_count, "the Cheapest plan visits the stall too").toBe(2);
  const chosen = await page.request.post(`/api/v1/plan/trips/${plan.id}/choose`, {
    data: { variant_id: cheapest.id },
  });
  expect(chosen.status(), await chosen.text()).toBe(200);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/plan/trips/${plan.id}`);
  await expect(page.getByTestId("variant-card").first()).toBeVisible();
  await expect(page.getByTestId("stop").first()).toBeVisible();
  await shot(page, "plan-trip");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/plan/trips/${plan.id}`);
  await expect(page.getByTestId("variant-card").first()).toBeVisible();
  await shot(page, "plan-trip-phone");

  await page.goto(`/plan/trips/${plan.id}/stop/1`);
  await expect(page.getByTestId("stop-item").first()).toBeVisible();
  await page.getByRole("list", { name: "To get here" }).getByRole("checkbox").first().click();
  await expect(page.getByTestId("stop-progress")).toContainText("1 of");
  await shot(page, "plan-stop-phone");
  await page.setViewportSize({ width: 1280, height: 800 });
});
