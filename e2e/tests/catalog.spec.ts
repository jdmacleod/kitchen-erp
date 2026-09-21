import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Each run invents its own names so repeated runs against the same dev
// database never collide on the case-insensitive unique index.
const stamp = Date.now().toString(36);

test("create an ingredient with only a name, then a product for it", async ({ page }) => {
  await login(page);
  await page.goto("/ingredients");
  const name = `E2E rigatoni ${stamp}`;
  await page.getByLabel(/^name/i).first().fill(name);
  await page.getByRole("button", { name: /create ingredient/i }).click();
  await expect(page.getByText(name).first()).toBeVisible();

  await page.goto("/products");
  await page.getByLabel(/^name/i).first().fill(`E2E rigatoni box ${stamp}`);
  const picker = page.getByRole("combobox", { name: "Ingredient" });
  await picker.fill(name);
  await page.getByRole("option", { name: new RegExp(name) }).first().click();
  await page.getByRole("button", { name: /create product/i }).click();
  await expect(page.getByText(`E2E rigatoni box ${stamp}`).first()).toBeVisible();
});
