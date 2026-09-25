import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { login } from "./helpers";

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
] as const;

for (const scheme of ["light", "dark"] as const) {
  test.describe(`contrast in the ${scheme} theme`, () => {
    test.beforeEach(async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "desktop only: the phone projects render the same tokens");
      await page.emulateMedia({ colorScheme: scheme });
      await login(page);
    });

    for (const { name, path } of PAGES) {
      test(`${name} passes axe colour contrast`, async ({ page }) => {
        await page.goto(path);
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
