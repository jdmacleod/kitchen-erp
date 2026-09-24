import { expect, test, type Page } from "@playwright/test";

import { login, watchExternalRequests } from "./helpers";

// The default stack has no tiles file, so the map draws a plain ground and says
// so. Every coordinate used here sits in the synthetic Pacific box from
// SECURITY.md (lat 33–34, lon -121 to -120); ?center= opens the map there so a
// click on it lands in the box whatever else the dev database holds.
const stamp = Date.now().toString(36);
const BOX = "/map?center=33.500000,-120.500000&zoom=12";

// The household zone; the open-at instants below are Saturdays 09:00 there.
test.use({ timezoneId: "America/Los_Angeles" });

async function clickMap(page: Page, fx = 0.5, fy = 0.35) {
  const canvas = page.getByTestId("map-canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("map has no size");
  // Above the phone bottom sheet (which takes at most 45% of the height).
  await canvas.click({ position: { x: box.width * fx, y: box.height * fy } });
}

test("with no tiles: notice, pins, no external requests, create by pin drop, readable detail", async ({ page, baseURL }) => {
  const external = watchExternalRequests(page, baseURL ?? "http://127.0.0.1:8080");
  await login(page);
  await page.goto(BOX);

  await expect(page.getByText(/Map tiles are missing; see docs\/tiles\.md/)).toBeVisible();
  await expect(page.getByTestId("map-attribution")).toHaveText("© OpenStreetMap contributors © Protomaps");

  // A home base by pin drop plus a name.
  const homeName = `E2E cabin ${stamp}`;
  await page.getByRole("button", { name: "Add home base here" }).click();
  await clickMap(page, 0.45, 0.3);
  await expect(page.getByTestId("draft-point")).toHaveText(/^33\.\d+, -120\.\d+$/);
  await page.getByRole("form", { name: "Add home base here" }).getByLabel("Name").fill(homeName);
  await page.getByRole("button", { name: "Create home base" }).click();
  await expect(page.getByTestId("home-base-panel")).toContainText(homeName);
  await expect(page.getByRole("button", { name: `${homeName} (home base)` })).toBeVisible();

  // A location by pin drop, an inline new vendor, and a name.
  const vendorName = `E2E coast stand ${stamp}`;
  const locationName = `E2E stand by the pier ${stamp}`;
  await page.getByRole("button", { name: "Add location here" }).click();
  await clickMap(page, 0.55, 0.3);
  await expect(page.getByTestId("draft-point")).toHaveText(/^33\.\d+, -120\.\d+$/);
  const form = page.getByRole("form", { name: "Add location here" });
  await form.getByRole("combobox", { name: "Vendor" }).fill(vendorName);
  await page.getByRole("option", { name: /Create vendor/ }).click();
  await form.getByLabel("Name").fill(locationName);
  await page.getByRole("button", { name: "Create location" }).click();

  // The pin appears, is selected, and its detail reads without sideways scroll.
  const pin = page.getByRole("button", { name: `${locationName} (Stand)` });
  await expect(pin).toBeVisible();
  await expect(pin).toHaveAttribute("aria-pressed", "true");
  const panel = page.getByTestId("location-panel");
  await expect(panel).toContainText(locationName);
  await expect(panel).toContainText(vendorName);
  await expect(panel).toContainText("Hours unknown");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  // Selecting again from a fresh load also works by tapping the pin itself.
  await page.goto(BOX);
  await page.getByRole("button", { name: `${locationName} (Stand)` }).click();
  await expect(page.getByTestId("location-panel")).toContainText(locationName);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  expect(external).toEqual([]);
});

test("open-at hides a seasonal stall in January and shows it on a July Saturday morning", async ({ page }) => {
  await login(page);
  const headers = (key: string) => ({ "Idempotency-Key": `e2e-map-${stamp}-${key}` });

  const marketName = `E2E pier market ${stamp}`;
  const market = await page.request.post("/api/v1/vendor-locations", {
    data: { vendor: { name: marketName, kind: "market" }, name: marketName, lat: "33.520000", lon: "-120.480000", opening_hours: "Sa 08:00-13:00" },
    headers: headers("market"),
  });
  expect(market.status()).toBe(201);
  const marketId = ((await market.json()) as { id: string }).id;

  const stallName = `E2E stone fruit ${stamp}`;
  const stall = await page.request.post("/api/v1/vendor-locations", {
    data: {
      vendor: { name: stallName, kind: "stand" },
      name: stallName,
      lat: "33.520000",
      lon: "-120.480000",
      parent_location_id: marketId,
      opening_hours: "Apr-Oct Sa 08:00-13:00",
    },
    headers: headers("stall"),
  });
  expect(stall.status()).toBe(201);

  await page.goto(`/map?location=${marketId}&center=33.520000,-120.480000&zoom=13`);
  const panel = page.getByTestId("location-panel");
  await expect(panel).toContainText(marketName);
  await expect(panel.getByRole("list", { name: "Stalls" })).toContainText(stallName);

  // January: the market is open on Saturdays, the seasonal stall is not.
  await page.locator("#filter-open-at").fill("2026-01-10T09:00");
  await page.getByLabel("Only open at").check();
  await expect(page.getByRole("button", { name: `${marketName} (Market)` })).toBeVisible();
  await expect(panel).toContainText("No stalls open at the chosen time");
  await expect(panel).not.toContainText(stallName);

  // July: both.
  await page.locator("#filter-open-at").fill("2026-07-11T09:00");
  await expect(panel.getByRole("list", { name: "Stalls" })).toContainText(stallName);
  await panel.getByRole("button", { name: new RegExp(stallName) }).click();
  await expect(page.getByTestId("stall-panel")).toContainText("own hours");
  await expect(page.getByTestId("stall-panel")).toContainText("open");
});

test("the placing deep-link applies once and then leaves the URL alone", async ({ page }) => {
  // `place` is an instruction, not state. The route does not remount on a query
  // change, so a lingering parameter would describe a mode the user may already
  // have left, and revisiting the URL would silently re-enter it.
  await login(page);
  await page.goto("/map?place=location");

  await expect(page.getByRole("button", { name: "Add location here" })).toHaveAttribute("aria-pressed", "true");
  await expect(page).not.toHaveURL(/place=/);
});
