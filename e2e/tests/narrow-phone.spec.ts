import { expect, test } from "@playwright/test";

import { controlsUnder44, expectNoSidewaysScroll, login, nextTag } from "./helpers";

// Names carry a per-run, per-worker stamp: repeated runs never collide on the
// case-insensitive unique index, and neither do the five projects this spec
// runs in, whose workers start within the same millisecond of each other.
const stamp = nextTag();

/** The widest thing on the purchase form is the Location option text, so the
 * regression needs a location whose name is genuinely long. Invented, and the
 * coordinates are the synthetic box from SECURITY.md. */
const LONG_LOCATION = `E2E Saltmarsh Provisions and Fishmonger ${stamp}`;

test("the purchase form fits the viewport even with a long location name", async ({ page }) => {
  await login(page);

  // Created through the API so the test is about layout, not about the map.
  const created = await page.request.post("/api/v1/vendor-locations", {
    data: {
      vendor: { name: `E2E Saltmarsh Provisions ${stamp}`, kind: "independent" },
      name: LONG_LOCATION,
      lat: "33.512345",
      lon: "-120.487654",
    },
    headers: { "Idempotency-Key": `e2e-narrow-${stamp}` },
  });
  expect(created.status()).toBe(201);

  await page.goto("/shop/purchases/new");
  // The select must actually carry the long option, or the assertion below is
  // passing for the wrong reason.
  await expect(page.getByLabel("Location")).toContainText(LONG_LOCATION);

  await expectNoSidewaysScroll(page, "/shop/purchases/new");
});

test("the product picker stays inside its column on the purchase form", async ({ page }, testInfo) => {
  // A 12rem minimum on the picker's search box pushed it into the Quantity
  // field beside it at 768 and 1024px (review of #67), widths no project here
  // runs at, so the test sets them itself and runs once. Where the two share a
  // row, the picker has to end before Quantity starts.
  test.skip(testInfo.project.name !== "desktop", "sets its own widths");
  await login(page);
  for (const width of [768, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/shop/purchases/new");
    const line = page.getByRole("group", { name: "Line 1" });
    const box = await line.getByRole("combobox", { name: "Product" }).boundingBox();
    const button = await line.getByRole("button", { name: "New product" }).boundingBox();
    const quantity = await line.getByLabel("Quantity 1").boundingBox();
    expect(box && button && quantity, "the picker and Quantity are on the page").toBeTruthy();
    const pickerRight = Math.max(box!.x + box!.width, button!.x + button!.width);
    const sameRow = Math.abs(quantity!.y - box!.y) < 40;
    if (sameRow) expect(pickerRight, `at ${width}px the picker runs into Quantity`).toBeLessThanOrEqual(quantity!.x);
  }
});

test("the receipt upload and map pages fit the viewport", async ({ page }) => {
  await login(page);

  await page.goto("/shop/receipts");
  await expect(page.getByRole("heading", { name: "Receipts", exact: true })).toBeVisible();
  await expectNoSidewaysScroll(page, "/shop/receipts");

  await page.goto("/catalog/vendors?view=map");
  await expect(page.getByRole("region", { name: /vendor locations/i })).toBeVisible();
  await expectNoSidewaysScroll(page, "/catalog/vendors?view=map");
});

/**
 * Below lg the shell is the header and tab bar, and every control on the page
 * is a 44px target (UI-4.1, UI-4.2). Measured on the pages people use in the
 * store: Home, Purchases and a list page with filters.
 */
test("the phone shell shows the tab bar and every control is a 44px target", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "desktop", "the phone shell is below lg");
  await login(page);
  const small: string[] = [];

  const tabs = page.getByRole("navigation", { name: "Tabs" });
  for (const path of ["/", "/shop/purchases", "/shop/receipts", "/catalog/products", "/catalog/ingredients", "/catalog/vendors", "/settings/kitchens"]) {
    await page.goto(path);
    await expect(tabs).toBeVisible();
    await expect(page.locator("aside")).toBeHidden();
    await page.waitForLoadState("networkidle");
    await expectNoSidewaysScroll(page, path);

    const found = await controlsUnder44(page, "header, nav[aria-label=Tabs], main");
    small.push(...found.map((f) => `${path}: ${f}`));
  }

  // The overlays render outside main: measure each one open.
  for (const [tab, dialog] of [["More", "More"], ["Search", "Search"], ["Capture", "Capture"]]) {
    await tabs.getByRole("button", { name: tab }).click();
    const open = page.getByRole("dialog", { name: dialog });
    await expect(open).toBeVisible();
    if (tab === "Search") {
      // With results showing, so the option rows are measured too.
      await page.keyboard.type("e2e");
      await expect(open.getByRole("option").first()).toBeVisible();
    }
    small.push(...(await controlsUnder44(page, "[role=dialog]")).map((f) => `${tab} sheet: ${f}`));
    await page.keyboard.press("Escape");
    await expect(open).toBeHidden();
  }
  expect(small, "controls under 44px").toEqual([]);
});
