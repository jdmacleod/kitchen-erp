import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { login, nextTag } from "./helpers";
import { makeChosenTrip, makeTripWorld } from "./trip-setup";

/**
 * UI-1.12: text contrast of at least 4.5:1 in both themes (docs/spec/08).
 *
 * axe computes contrast from the rendered colours, so this catches what the
 * class-level unit tests cannot: a theme shade that fails on the surface it
 * actually lands on. Desktop only; the phone projects render the same tokens.
 * Pages are checked as they stand in the shared stack, empty or not, since both
 * states render text that must pass.
 */
const PAGES = [
  { name: "Home", path: "/" },
  { name: "Products", path: "/catalog/products" },
  { name: "Ingredients", path: "/catalog/ingredients" },
  { name: "Vendors", path: "/catalog/vendors" },
  { name: "Purchases", path: "/shop/purchases" },
  // The Cook pages (UI-7.18): the list, the resolve queue, and one recipe page,
  // which recipes.spec.ts reaches by id; here the list's first row is followed.
  { name: "Recipes", path: "/cook/recipes" },
  { name: "Resolve recipe names", path: "/cook/recipes/resolve" },
  { name: "Recipe page", path: "/cook/recipes", follow: "recipe-row" },
  // The Plan pages (UI-8.19): the list as it stands (empty, or with another
  // spec's items), Trips, and Compare under Plan with its kitchen control.
  { name: "Shopping list", path: "/plan/list" },
  { name: "Trips", path: "/plan/trips" },
  { name: "Compare prices", path: "/plan/compare" },
  // A trip page and its first stop screen, on a chosen plan made here
  // (trip-setup.ts: invented stores on the synthetic grid).
  { name: "Trip page", path: "", trip: "" },
  { name: "Stop screen", path: "", trip: "/stop/1" },
] as const;

for (const scheme of ["light", "dark"] as const) {
  test.describe(`contrast in the ${scheme} theme`, () => {
    test.beforeEach(async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "desktop only: the phone projects render the same tokens");
      await page.emulateMedia({ colorScheme: scheme });
      await login(page);
    });

    for (const entry of PAGES) {
      const { name, path } = entry;
      const follow = "follow" in entry ? entry.follow : undefined;
      const trip = "trip" in entry ? entry.trip : undefined;
      test(`${name} passes axe colour contrast`, async ({ page }) => {
        if (follow) {
          // The example recipes are mounted on the e2e stack; one scan indexes them.
          expect((await page.request.post("/api/v1/recipes/rescan")).status()).toBe(200);
        }
        if (trip !== undefined) {
          const planId = await makeChosenTrip(page.request, await makeTripWorld(page.request, nextTag()));
          await page.goto(`/plan/trips/${planId}${trip}`);
          await expect(page.getByTestId(trip ? "stop-item" : "variant-card").first()).toBeVisible();
        } else {
          await page.goto(path);
        }
        if (follow) {
          await page.getByTestId(follow).first().getByRole("link").first().click();
          await expect(page.getByRole("table", { name: "Cost by line" })).toBeVisible();
        }
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        const results = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
        const failures = results.violations.flatMap((v) =>
          v.nodes.map((n) => `${n.target.join(" ")}: ${n.failureSummary?.split("\n").slice(1).join(" ")}`),
        );
        expect(failures, `${name} (${scheme})`).toEqual([]);
      });
    }
  });
}
