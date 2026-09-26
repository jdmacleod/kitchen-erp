import { expect, test, type Page } from "@playwright/test";

import { login, nextTag } from "./helpers";

const LAST_LOCATION = "kerp.lastVendorLocationId";

/**
 * A barcode no real product carries: the 2-prefix is GS1's in-store range, and
 * the digits are random per run so the unique index never collides.
 */
function syntheticBarcode(): string {
  let digits = "2";
  while (digits.length < 13) digits += Math.floor(Math.random() * 10).toString();
  return digits;
}

async function seedStoreAndProduct(page: Page, stamp: string) {
  const ingredient = await page.request.post("/api/v1/ingredients", { data: { name: `E2E rolled oats ${stamp}` } });
  expect(ingredient.status()).toBe(201);
  const { id: ingredientId } = (await ingredient.json()) as { id: string };

  const barcode = syntheticBarcode();
  const product = await page.request.post("/api/v1/products", {
    data: { ingredient_id: ingredientId, name: `E2E oats tub ${stamp}`, pack_qty: "1", pack_unit: "kg", barcode },
  });
  expect(product.status()).toBe(201);

  const storeName = `E2E Tidewater Grocer ${stamp}`;
  const location = await page.request.post("/api/v1/vendor-locations", {
    // The synthetic box from SECURITY.md.
    data: { name: storeName, lat: "33.610000", lon: "-120.530000", vendor: { name: storeName, kind: "independent" } },
    headers: { "Idempotency-Key": `e2e-shelf-loc-${stamp}` },
  });
  expect(location.status()).toBe(201);
  const { id: locationId } = (await location.json()) as { id: string };
  return { barcode, storeName, locationId, productName: `E2E oats tub ${stamp}` };
}

/**
 * The shelf-price stream (G2–G4): the remembered store is labelled as a guess,
 * a typed barcode matches without choosing, and "Save and scan another" keeps
 * the store, clears the rest and says what was saved where.
 */
test("logs two shelf prices in a row at the remembered store", async ({ page }) => {
  const stamp = nextTag();
  await login(page);
  const { barcode, storeName, locationId, productName } = await seedStoreAndProduct(page, stamp);
  await page.evaluate(([key, id]) => localStorage.setItem(key, id), [LAST_LOCATION, locationId]);

  await page.goto("/shop/shelf-prices");
  // No geolocation in the test browser: a guess, never "Near".
  await expect(page.getByRole("button", { name: `Last used: ${storeName}. Change store` })).toBeVisible();

  const entry = page.getByRole("combobox", { name: "Barcode or product name" });
  await entry.fill(barcode);
  await entry.press("Enter");
  await expect(page.getByTestId("shelf-product-choice")).toContainText(productName);

  const price = page.getByLabel("Price on the shelf");
  await expect(price).toBeFocused();
  await price.fill("3.29");
  await page.getByRole("button", { name: "Save and scan another" }).click();

  await expect(page.getByRole("main").getByText(`Saved $3.29 at ${storeName}`)).toBeVisible();
  await expect(entry).toBeFocused();
  await expect(entry).toHaveValue("");
  await expect(page.getByRole("button", { name: `Last used: ${storeName}. Change store` })).toBeVisible();

  // The product just logged is now the first of this store's recent five.
  const recent = page.getByRole("region", { name: `Recently logged at ${storeName}` });
  await recent.getByRole("button", { name: new RegExp(productName) }).click();
  await price.fill("3.09");
  await page.getByRole("checkbox", { name: "On sale" }).check();
  await price.press("Enter");
  await expect(page.getByRole("main").getByText(`Saved $3.09 at ${storeName}`)).toBeVisible();

  const listed = await page.request.get(`/api/v1/price-observations?vendor_location_id=${locationId}`);
  const { items } = (await listed.json()) as { items: { price: string; is_promo: boolean; source: string }[] };
  // Prices come back as the API stores them, at four places.
  expect(items.map((o) => [o.price, o.is_promo, o.source])).toEqual([
    ["3.0900", true, "shelf"],
    ["3.2900", false, "shelf"],
  ]);
});

test("below lg the shelf price is a task screen: no tab bar, Save in the thumb zone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "desktop", "the task screen is the phone layout");
  await login(page);
  await page.goto("/shop/shelf-prices");

  await expect(page.getByRole("button", { name: "Back" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Tabs" })).toHaveCount(0);
  const save = page.getByRole("button", { name: "Save price" });
  const box = await save.boundingBox();
  const viewport = page.viewportSize();
  expect(box && viewport && box.height >= 56 && box.y > viewport.height * 0.7).toBe(true);
});

test("at lg Capture opens the shelf price in a drawer over the page (G14)", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "the drawer is the desktop layout");
  await login(page);
  await page.goto("/shop/purchases");
  await page.locator("aside").getByRole("button", { name: "Capture" }).click();
  await page.getByRole("dialog", { name: "Capture" }).getByRole("link", { name: /Log a shelf price/ }).click();

  const drawer = page.getByRole("dialog", { name: "Log a shelf price" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("combobox", { name: "Barcode or product name" })).toBeVisible();
  // The page underneath stays where Capture was opened.
  await expect(page).toHaveURL(/\/shop\/purchases$/);
  await drawer.getByRole("button", { name: "Cancel" }).click();
  await expect(drawer).toHaveCount(0);
});
