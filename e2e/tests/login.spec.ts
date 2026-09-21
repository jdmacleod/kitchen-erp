import { expect, test } from "@playwright/test";

import { ADMIN, login, watchExternalRequests } from "./helpers";

test("unauthenticated visit redirects to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("admin logs in and sees the ingredients page with no external requests", async ({ page, baseURL }) => {
  const external = watchExternalRequests(page, baseURL!);
  await login(page);
  await expect(page).toHaveURL(/\/ingredients$/);
  await expect(page.getByRole("heading", { name: "Ingredients" })).toBeVisible();
  expect(external).toEqual([]);
});

test("wrong password shows the server message inline", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText(/incorrect/i);
  await expect(page).toHaveURL(/\/login$/);
});
