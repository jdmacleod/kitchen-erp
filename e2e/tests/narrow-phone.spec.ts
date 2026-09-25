import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Names carry a per-run stamp so repeated runs never collide on the
// case-insensitive unique index.
const stamp = Date.now().toString(36);

/** The widest thing on the purchase form is the Location option text, so the
 * regression needs a location whose name is genuinely long. Invented, and the
 * coordinates are the synthetic box from SECURITY.md. */
const LONG_LOCATION = `E2E Saltmarsh Provisions and Fishmonger ${stamp}`;

/**
 * A page that scrolls sideways on a phone is a bug, and it is the one class of
 * bug the rest of this suite cannot see: every assertion here passes at desktop
 * width. The purchase form shipped overflowing because its Location `select`
 * sized itself to the longest option and grid children default to
 * `min-width: auto`, so the column could not shrink. `phone` runs at 390px and
 * stayed green; the overflow started below ~427px. Hence `phone-375`.
 */
async function expectNoSidewaysScroll(page: import("@playwright/test").Page, where: string) {
  // Measure against the configured device width, NOT window.innerWidth. Under
  // Playwright's mobile emulation the page carries a meta viewport, so an
  // overflowing layout makes the browser zoom out and grow innerWidth to match
  // the content: innerWidth reported 460 on a 375px device while the form ran
  // off the screen, and `scrollWidth <= innerWidth` was satisfied the whole
  // time. Comparing to the real device width is what makes this fail.
  const deviceWidth = page.viewportSize()?.width;
  expect(deviceWidth, "the project must declare a viewport").toBeTruthy();

  const { scrollWidth, innerWidth, widest } = await page.evaluate(() => {
    const doc = document.documentElement;
    let widest: { tag: string; cls: string; width: number } | null = null;
    for (const el of document.querySelectorAll("main *")) {
      const width = el.getBoundingClientRect().width;
      if (!widest || width > widest.width) {
        widest = { tag: el.tagName, cls: String(el.className).slice(0, 80), width: Math.round(width) };
      }
    }
    return { scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth, widest };
  });

  expect(
    scrollWidth,
    `${where} is ${scrollWidth}px wide on a ${deviceWidth}px device` +
      ` (innerWidth ${innerWidth}px — inflated by zoom-out when content overflows)` +
      (widest ? `; widest element in main: <${widest.tag} class="${widest.cls}"> at ${widest.width}px` : ""),
  ).toBeLessThanOrEqual(deviceWidth as number);
}

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

  await page.goto("/purchases/new");
  // The select must actually carry the long option, or the assertion below is
  // passing for the wrong reason.
  await expect(page.getByLabel("Location")).toContainText(LONG_LOCATION);

  await expectNoSidewaysScroll(page, "/purchases/new");
});

test("the receipt upload and map pages fit the viewport", async ({ page }) => {
  await login(page);

  await page.goto("/receipts");
  await expect(page.getByRole("heading", { name: "Receipts" })).toBeVisible();
  await expectNoSidewaysScroll(page, "/receipts");

  await page.goto("/map");
  await expect(page.getByRole("region", { name: /vendor locations/i })).toBeVisible();
  await expectNoSidewaysScroll(page, "/map");
});
