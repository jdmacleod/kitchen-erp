import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Names carry a per-run stamp so repeated runs never collide on the
// case-insensitive unique index.
const stamp = Date.now().toString(36);

test("create a vendor, open it, and change its price scope", async ({ page }) => {
  await login(page);
  await page.goto("/vendors");

  const name = `E2E Riverbend Grocers ${stamp}`;
  await page.getByLabel(/^name/i).first().fill(name);
  await page.getByRole("radio", { name: "Chain", exact: true }).check();
  await page.getByRole("button", { name: /create vendor/i }).click();

  const list = page.getByRole("list", { name: "Vendors" });
  await expect(list.getByRole("link", { name })).toBeVisible();
  await list.getByRole("link", { name }).click();

  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("Prices per location")).toBeVisible();
  await page.getByRole("button", { name: "Edit vendor" }).click();
  await page.getByRole("radio", { name: "Chain-wide" }).check();
  await page.getByRole("button", { name: "Save vendor" }).click();
  await expect(page.getByText("Prices chain-wide")).toBeVisible();
  await expect(page.getByText("No locations yet")).toBeVisible();

  // A location without the map (issue #20): until this existed, a vendor made
  // here could not be used for a purchase until someone dropped a pin for it.
  await page.getByRole("button", { name: "Add a location" }).click();
  const form = page.getByRole("form", { name: `Add a location for ${name}` });
  await form.getByLabel(/^name/i).fill(`${name} quay`);
  // The synthetic box from SECURITY.md, never a real place.
  await form.getByLabel("Coordinates").fill("33.512345, -120.487654");
  await form.getByRole("button", { name: "Create location" }).click();

  const locations = page.getByRole("list", { name: "Locations" });
  await expect(locations.getByText(`${name} quay`)).toBeVisible();
  await expect(locations.getByText("33.512345, -120.487654")).toBeVisible();
});

test("the OpenStreetMap panel says the integration is off rather than failing", async ({ page }) => {
  await login(page);
  // A home base to search from; the box in SECURITY.md, never a real place.
  const created = await page.request.post("/api/v1/home-bases", {
    data: { name: `E2E base ${stamp}`, lat: "33.300000", lon: "-120.700000" },
    headers: { "Idempotency-Key": `e2e-vendors-${stamp}` },
  });
  expect(created.status()).toBe(201);
  const home = (await created.json()) as { id: string };

  await page.goto("/vendors");
  await page.getByLabel("Home base").selectOption(home.id);
  await page.getByRole("button", { name: "Find candidates" }).click();
  await expect(page.getByText("OpenStreetMap adoption is off; set ENABLE_OVERPASS=true.")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
