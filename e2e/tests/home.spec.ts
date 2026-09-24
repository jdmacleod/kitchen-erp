import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

// Every name carries a per-run stamp so repeated runs never collide. The vendor,
// the location (in the synthetic Pacific box from SECURITY.md) and the product
// are invented; nothing here resembles a real shop or a real receipt.
//
// Unique per run and per project: the two Playwright projects run in parallel
// against one backend, and the catalog rejects a duplicate ingredient name with
// a 409. One scenario, so one seed — every location this spec creates also lands
// on the shared map that `map.spec.ts` is interacting with at the same time.
const nextTag = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

/**
 * The landing route, for a household that is already set up.
 *
 * This spec establishes its own precondition rather than reading global state.
 * The suite runs two projects in parallel against one backend and `review.spec`
 * creates a committed purchase while this one runs, so "the database is empty"
 * and "the database is not empty" are both coin flips here. Seeding first makes
 * the assertion deterministic.
 *
 * The *empty* branch is deliberately not attempted: nothing in a shared stack can
 * remove the other specs' data. It is covered in `src/test/home.test.tsx` against
 * mocked responses instead.
 */
async function seedCommittedPurchase(page: Page): Promise<void> {
  const stamp = nextTag();
  const ingredient = await page.request.post("/api/v1/ingredients", {
    data: { name: `E2E barley ${stamp}` },
  });
  expect(ingredient.status()).toBe(201);
  const { id: ingredientId } = (await ingredient.json()) as { id: string };

  const product = await page.request.post("/api/v1/products", {
    data: { ingredient_id: ingredientId, name: `E2E barley sack ${stamp}`, pack_qty: "1", pack_unit: "kg" },
  });
  expect(product.status()).toBe(201);
  const { id: productId } = (await product.json()) as { id: string };

  const location = await page.request.post("/api/v1/vendor-locations", {
    data: {
      name: `E2E Longshore Provisions ${stamp}`,
      lat: "33.720000",
      lon: "-120.480000",
      vendor: { name: `E2E Longshore Provisions ${stamp}`, kind: "independent" },
    },
    headers: { "Idempotency-Key": `e2e-home-loc-${stamp}` },
  });
  expect(location.status()).toBe(201);
  const { id: locationId } = (await location.json()) as { id: string };

  const purchase = await page.request.post("/api/v1/purchases", {
    data: {
      vendor_location_id: locationId,
      purchased_at: "2026-09-22T17:10:00Z",
      total: "6.50",
      lines: [{ product_id: productId, qty: "1", unit: "each", unit_price: "6.50" }],
    },
    headers: { "Idempotency-Key": `e2e-home-purchase-${stamp}` },
  });
  expect(purchase.status()).toBe(201);
}

test("a set-up household lands on the home page and can start a purchase from it", async ({ page }) => {
  await login(page);
  await seedCommittedPurchase(page);

  await page.goto("/");

  await expect(page.getByLabel("Lately")).toBeVisible();
  await expect(page.getByRole("main").getByRole("heading", { name: "Home" })).toBeVisible();
  // The regression that matters: an established household must never be told to
  // go and add a shop it already has.
  await expect(page.getByText("Add somewhere you shop")).toHaveCount(0);

  await page.getByRole("main").getByRole("link", { name: "New purchase" }).click();
  await expect(page).toHaveURL(/\/purchases\/new$/);
  await expect(page.getByRole("button", { name: "Save purchase" })).toBeVisible();
});
