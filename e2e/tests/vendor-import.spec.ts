import { expect, test } from "@playwright/test";

import { login, nextTag, vendorsAction } from "./helpers";

/**
 * Vendor files (spec 03 §1F): export, edit the file, dry run, confirm, and the
 * vendor's page shows the new fact. Each step has unit tests; this checks they
 * agree through the real app once.
 */
test("export, edit, dry run, import", async ({ page }) => {
  const stamp = nextTag();
  await login(page);
  const storeName = `E2E Lantern Grocer ${stamp}`;
  const created = await page.request.post("/api/v1/vendor-locations", {
    // The synthetic box from SECURITY.md.
    data: { name: "Elm St", lat: "33.640000", lon: "-120.530000", vendor: { name: storeName, kind: "chain" } },
    headers: { "Idempotency-Key": `e2e-import-loc-${stamp}` },
  });
  expect(created.status()).toBe(201);
  const location = (await created.json()) as { id: string; key: string; vendor: { id: string } };

  const exported = await page.request.get("/api/v1/vendors/export?format=json&mode=household");
  expect(exported.status()).toBe(200);
  const file = (await exported.json()) as { vendors: { name: string; locations: { key: string; phone?: string }[] }[] };
  // Keep only this test's vendor, and give its location a phone.
  file.vendors = file.vendors.filter((v) => v.name === storeName);
  file.vendors[0].locations[0].phone = "+1 555 0123";

  await page.goto("/catalog/vendors");
  await vendorsAction(page, "Import");
  const drawer = page.getByRole("dialog", { name: "Import vendors" });
  await drawer.getByLabel("Vendor file").setInputFiles({
    name: "vendors.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(file)),
  });
  await expect(drawer.getByText("0 to create · 1 to update · 0 need you · 1 unchanged")).toBeVisible();
  await expect(drawer.getByRole("region", { name: "Will change" })).toContainText("phone: empty → +1 555 0123");

  await drawer.getByRole("button", { name: "Import" }).click();
  await expect(page.getByText("Imported: 0 created, 1 updated.")).toBeVisible();

  await page.goto(`/catalog/vendors/${location.vendor.id}`);
  await expect(page.getByRole("link", { name: "+1 555 0123" })).toBeVisible();
  await page.getByText("Sources").click();
  // The file now vouches for the phone (and for the name it agrees with).
  const phone = page.getByRole("definition").filter({ hasText: "From the file vendors.json" });
  await expect(phone.first()).toBeVisible();
});
