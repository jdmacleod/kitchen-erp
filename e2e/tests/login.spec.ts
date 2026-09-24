import { expect, test } from "@playwright/test";

import { ADMIN, login, watchExternalRequests } from "./helpers";

test("unauthenticated visit redirects to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("admin logs in and lands on the home route with no external requests", async ({ page, baseURL }) => {
  const external = watchExternalRequests(page, baseURL!);
  await login(page);

  // Asserts nothing about which of the two home modes renders. The suite shares
  // one backend and other specs create purchases in parallel, so whether this
  // household is "set up" flips mid-run. `home.spec.ts` seeds its own data to
  // assert the set-up branch deterministically.
  // The phone project keeps the sidebar behind a menu, so the main landmark is
  // the one marker of the authenticated shell that holds at both viewports.
  await expect(page).toHaveURL(new URL("/", baseURL!).toString());
  await expect(page.getByRole("main")).toBeVisible();
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
