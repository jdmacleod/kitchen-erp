import { expect, test, type Page } from "@playwright/test";

import { login, nextTag } from "./helpers";

// Every name carries a per-run stamp so repeated runs never collide. The
// vendor, the location (in the synthetic Pacific box from SECURITY.md), and
// the product are invented; nothing here resembles a real receipt.
const stamp = nextTag();

interface Seeded {
  purchaseId: string;
  productId: string;
  locationId: string;
}

/**
 * A purchase to review. Receipt ingest needs the OCR worker, so the spec
 * seeds a manual purchase through the API and reopens it: reopening puts
 * any committed purchase back into review, which is the screen under test.
 */
async function seedReviewedPurchase(page: Page): Promise<Seeded> {
  const ingredient = await page.request.post("/api/v1/ingredients", { data: { name: `E2E semolina ${stamp}` } });
  expect(ingredient.status()).toBe(201);
  const { id: ingredientId } = (await ingredient.json()) as { id: string };

  const product = await page.request.post("/api/v1/products", {
    data: { ingredient_id: ingredientId, name: `E2E semolina bag ${stamp}`, pack_qty: "1", pack_unit: "kg" },
  });
  expect(product.status()).toBe(201);
  const { id: productId } = (await product.json()) as { id: string };

  const location = await page.request.post("/api/v1/vendor-locations", {
    data: { name: `E2E Cove Grocer ${stamp}`, lat: "33.610000", lon: "-120.390000", vendor: { name: `E2E Cove Grocer ${stamp}`, kind: "independent" } },
    headers: { "Idempotency-Key": `e2e-review-loc-${stamp}` },
  });
  expect(location.status()).toBe(201);
  const { id: locationId } = (await location.json()) as { id: string };

  const purchase = await page.request.post("/api/v1/purchases", {
    data: {
      vendor_location_id: locationId,
      purchased_at: "2026-09-20T18:05:00Z",
      total: "3.99",
      lines: [{ product_id: productId, qty: "1", unit: "each", unit_price: "3.99" }],
    },
    headers: { "Idempotency-Key": `e2e-review-purchase-${stamp}` },
  });
  expect(purchase.status()).toBe(201);
  const { id: purchaseId } = (await purchase.json()) as { id: string };

  // A discount line to attach, so the review has two lines to move between.
  const discount = await page.request.post(`/api/v1/purchases/${purchaseId}/reopen`);
  expect(discount.status()).toBe(200);
  const added = await page.request.post(`/api/v1/purchases/${purchaseId}/lines`, {
    data: { raw_text: "COUPON", line_kind: "discount", line_total: "-0.50" },
  });
  expect(added.status()).toBe(201);
  return { purchaseId, productId, locationId };
}

test("review a reopened purchase by keyboard: move between lines, reattach a discount, commit, reopen", async ({ page }) => {
  await login(page);
  const { purchaseId } = await seedReviewedPurchase(page);
  await page.goto(`/shop/purchases/${purchaseId}`);

  // The review screen, with its key legend and the receipt-less two-line table.
  await expect(page.getByLabel("Keyboard shortcuts")).toContainText("accept the top suggestion");
  const rows = page.getByTestId("review-line");
  await expect(rows).toHaveCount(2);

  // Focus is moved programmatically (no pointer), then driven by keys alone.
  await rows.nth(0).focus();
  await page.keyboard.press("j");
  await expect(rows.nth(1)).toBeFocused();
  await page.keyboard.press("k");
  await expect(rows.nth(0)).toBeFocused();

  // Reattach the coupon to the item. Only the current line shows its controls
  // (#35), so j makes line 2 current; its select is then reachable by Tab.
  await page.keyboard.press("j");
  await expect(rows.nth(1)).toBeFocused();
  const attach = page.getByRole("combobox", { name: "Attach line 2 to" });
  await attach.focus();
  const patched = page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes(`/purchases/${purchaseId}/lines/`));
  await attach.selectOption({ index: 1 });
  expect((await patched).status()).toBe(200);
  await expect(page.getByRole("combobox", { name: "Attach line 2 to" })).not.toHaveValue("");

  // c opens the confirmation with Commit focused; Enter commits.
  await rows.nth(0).focus();
  await page.keyboard.press("c");
  const dialog = page.getByRole("dialog", { name: "Commit this purchase?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Commit" })).toBeFocused();
  const committed = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith(`/purchases/${purchaseId}/commit`));
  await page.keyboard.press("Enter");
  expect((await committed).status()).toBe(200);

  // Committed: the plain view, with Reopen; the item line has its observation.
  await expect(page.getByRole("button", { name: "Reopen" })).toBeVisible();
  const lines = page.getByRole("table", { name: "Lines" }).getByRole("row");
  await expect(lines.nth(1)).toContainText("observed");
  await page.getByRole("button", { name: "Reopen" }).click();
  // Back in review. (The shortcut legend is desktop-only, so the line filter is the marker.)
  await expect(page.getByRole("group", { name: "Show lines" })).toBeVisible();
});

test("the receipts page uploads nothing by itself and lists jobs; the to-identify queue loads", async ({ page }) => {
  await login(page);
  await page.goto("/shop/receipts");
  await expect(page.getByRole("heading", { name: "Upload a receipt" })).toBeVisible();
  // A PDF is named because the picker accepts one: an emailed receipt is not a photo.
  await expect(page.getByText("A photo or a PDF. It stays on this deployment; nothing is sent elsewhere.")).toBeVisible();
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toContainText("Choose a photo of the receipt.");

  await page.goto("/shop/receipts/identify");
  await expect(page.getByRole("heading", { name: "To identify" })).toBeVisible();
});
