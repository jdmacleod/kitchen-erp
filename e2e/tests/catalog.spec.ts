import { expect, test } from "@playwright/test";

import { login, nextTag } from "./helpers";

// Each run invents its own names so repeated runs against the same dev
// database never collide on the case-insensitive unique index.
const stamp = nextTag();

test("create an ingredient with a name and category, then a product for it", async ({ page }) => {
  await login(page);
  await page.goto("/catalog/ingredients");
  const name = `E2E rigatoni ${stamp}`;
  // Creation happens in a drawer opened by the header's primary action (UI-3.2).
  await page.getByRole("main").getByRole("button", { name: "Add ingredient" }).first().click();
  let drawer = page.getByRole("dialog", { name: "Add ingredient" });
  await drawer.getByLabel(/^name/i).fill(name);
  // The name would be saved lowercase (issue 345); this one is kept as typed.
  await drawer.getByRole("button", { name: `Keep “${name}”` }).click();
  // Every new ingredient has a category (issue 344).
  await drawer.getByLabel("Category", { exact: true }).selectOption("pantry");
  await drawer.getByRole("button", { name: "Add ingredient" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(name).first()).toBeVisible();

  await page.goto("/catalog/products");
  await page.getByRole("main").getByRole("button", { name: "Add product" }).first().click();
  drawer = page.getByRole("dialog", { name: "Add product" });
  await drawer.getByLabel(/^name/i).fill(`E2E rigatoni box ${stamp}`);
  const picker = drawer.getByRole("combobox", { name: "Ingredient" });
  await picker.fill(name);
  await page
    .getByRole("option", { name: new RegExp(name) })
    .first()
    .click();
  await drawer.getByRole("button", { name: "Add product" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Found by the server-side search, whichever page it sorts onto (D12).
  await page.getByLabel("Search products").fill(`E2E rigatoni box ${stamp}`);
  await expect(
    page.getByRole("table", { name: "Products" }).getByText(`E2E rigatoni box ${stamp}`),
  ).toBeVisible();
});
