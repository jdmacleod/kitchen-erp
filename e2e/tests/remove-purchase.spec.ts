import { expect, test, type Page } from "@playwright/test";

import { login, nextTag } from "./helpers";

/**
 * Removing a purchase (#74) changes four screens at once: the purchase, the
 * purchases list, the Voided view and the product's price records. Each has
 * its own unit test with a mocked API; this clicks through the real app once
 * to see that they agree.
 */
async function seedPurchase(page: Page, stamp: string) {
  const ingredient = await page.request.post("/api/v1/ingredients", { data: { name: `E2E pearl barley ${stamp}` } });
  expect(ingredient.status()).toBe(201);
  const { id: ingredientId } = (await ingredient.json()) as { id: string };
  const productName = `E2E barley bag ${stamp}`;
  const product = await page.request.post("/api/v1/products", { data: { ingredient_id: ingredientId, name: productName } });
  expect(product.status()).toBe(201);
  const { id: productId } = (await product.json()) as { id: string };

  const storeName = `E2E Quarry Lane Grocer ${stamp}`;
  const location = await page.request.post("/api/v1/vendor-locations", {
    // The synthetic box from SECURITY.md.
    data: { name: storeName, lat: "33.620000", lon: "-120.510000", vendor: { name: storeName, kind: "independent" } },
    headers: { "Idempotency-Key": `e2e-remove-loc-${stamp}` },
  });
  expect(location.status()).toBe(201);
  const { id: locationId } = (await location.json()) as { id: string };

  const purchase = await page.request.post("/api/v1/purchases", {
    data: {
      vendor_location_id: locationId,
      purchased_at: new Date().toISOString(),
      lines: [{ product_id: productId, qty: "1", unit: "each", line_total: "3.25" }],
    },
    headers: { "Idempotency-Key": `e2e-remove-purchase-${stamp}` },
  });
  expect(purchase.status()).toBe(201);
  const { id: purchaseId } = (await purchase.json()) as { id: string };
  return { purchaseId, productId, storeName };
}

test("removing a recorded purchase voids it everywhere at once", async ({ page }) => {
  const stamp = nextTag();
  await login(page);
  const { purchaseId, productId, storeName } = await seedPurchase(page, stamp);

  await page.goto(`/shop/purchases/${purchaseId}`);
  const section = page.getByRole("region", { name: "Remove this purchase" });
  await expect(section).toContainText("It's in the price book, so its 1 price will be voided.");
  await section.getByRole("button", { name: "Remove purchase" }).click();
  const confirm = section.getByRole("group", { name: new RegExp(`Remove the ${storeName} purchase from`) });
  await expect(confirm.getByRole("button", { name: "Keep it" })).toBeFocused();
  await confirm.getByRole("button", { name: "Remove purchase" }).click();

  const notice = page.getByText(/^Removed .* Its 1 price no longer counts in the price book\.$/);
  await expect(notice).toBeFocused();
  await expect(page.getByRole("button", { name: "Reopen" })).toHaveCount(0);

  // Gone from All, there under Voided.
  await page.goto("/shop/purchases");
  await expect(page.getByRole("heading", { name: "All purchases" })).toBeVisible();
  await expect(page.getByText(storeName)).toHaveCount(0);
  await page.getByRole("link", { name: /^Show voided \(\d+\+?\)$/ }).click();
  await expect(page.getByRole("heading", { name: "Voided purchases" })).toBeVisible();
  await expect(page.getByText(storeName).first()).toBeVisible();

  // The product's price records say why, and lead back to the purchase.
  await page.goto(`/catalog/products/${productId}`);
  await page.getByLabel("Show voided").check();
  const record = page.getByText(/Voided: purchase removed/);
  await expect(record).toBeVisible();
  await record.getByRole("link", { name: "Open the purchase" }).click();
  await expect(page).toHaveURL(new RegExp(`/shop/purchases/${purchaseId}$`));
});
