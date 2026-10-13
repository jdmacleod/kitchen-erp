import { expect, test, type Page } from "@playwright/test";

import { controlsUnder44, expectNoSidewaysScroll, login, loginAs, nextTag } from "./helpers";
import { makeTripWorld, type TripWorld } from "./trip-setup";

/**
 * Sub-phases 4B and 4C against the stack (17 criteria 11, 20, 22–28; UI-4.8,
 * UI-8.12–8.20): plan a trip from the list in the drawer, the three cards with
 * their "estimated" mark (the e2e stack has no routing engine, so every leg is
 * an estimate), Choose, then each stop on the phone: tick, edit a price, Get
 * here, Done here, the Notice's next stop, and back to the trip with predicted
 * beside actual.
 *
 * The projects run in parallel against one backend and the current kitchen is
 * a per-user setting, so each worker signs in as a member of its own and plans
 * for a kitchen of its own (`trip-setup.ts`: synthetic grid, invented stores).
 */

const stamp = nextTag();
const COOK = { email: `e2e-tripper-${stamp}@example.com`, password: "e2e-only-not-a-secret", name: `E2E Tripper ${stamp}` };

const isPhone = (project: string) => project !== "desktop";
const money = (text: string) => Number((/\$(\d+\.\d{2})/.exec(text) ?? ["", "NaN"])[1]);

async function setUp(page: Page): Promise<TripWorld> {
  await login(page);
  const world = await makeTripWorld(page.request, stamp);
  const user = await page.request.post("/api/v1/users", {
    data: { email: COOK.email, display_name: COOK.name, password: COOK.password, role: "member" },
  });
  expect([201, 409], "a member of this run's own").toContain(user.status());
  await page.context().clearCookies();
  await loginAs(page, COOK.email, COOK.password);
  // A new member has no setting, and the kitchen switcher writes back the one
  // the fallback chain resolves when it mounts; let that happen first, so it
  // cannot land after ours.
  await page.goto("/plan/trips");
  await expect(page.getByTestId("kitchen-tag")).toBeVisible();
  const settled = async () => ((await (await page.request.get("/api/v1/me/settings")).json()) as { current_home_base_id: string | null }).current_home_base_id;
  await expect.poll(settled, { timeout: 5_000 }).not.toBeNull().catch(() => undefined);
  const put = await page.request.put("/api/v1/me/settings", { data: { current_home_base_id: world.kitchenId } });
  expect(put.status(), await put.text()).toBe(200);
  return world;
}

/** The stop screen's checks on a phone: no sideways scroll, the footer on the bottom edge, no tab bar, 44px targets. */
async function checkStopOnPhone(page: Page, where: string): Promise<void> {
  await expectNoSidewaysScroll(page, where);
  await expect(page.getByRole("navigation", { name: "Tabs" })).toBeHidden();
  const footer = page.getByTestId("stop-footer");
  const viewport = page.viewportSize()!;
  for (const scroll of [0, 10_000]) {
    await page.evaluate((y) => window.scrollTo(0, y), scroll);
    const box = await footer.boundingBox();
    expect(box, "the footer is rendered").toBeTruthy();
    expect(Math.abs(box!.y + box!.height - viewport.height), `${where}: the footer sits on the bottom edge`).toBeLessThanOrEqual(1);
  }
  const done = footer.getByRole("button", { name: /^Done here/ });
  expect((await done.boundingBox())!.height, "Done here is 56px").toBeGreaterThanOrEqual(55.5);
  expect(await controlsUnder44(page, "main"), `${where}: controls under 44px`).toEqual([]);
  const circle = (await page.getByTestId("check-circle").first().boundingBox())!;
  expect(Math.round(circle.width), "check circles are 28px").toBe(28);
}

test("plan a trip, choose it, and work through every stop", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const phone = isPhone(testInfo.project.name);
  const world = await setUp(page);

  // --- Plan a trip (UI-8.12) ------------------------------------------------------
  await page.goto("/plan/trips");
  await expect(page.getByRole("heading", { level: 1, name: /Trips/ })).toBeVisible();
  await expect(page.getByTestId("kitchen-tag")).toContainText(world.kitchenName);
  await page.getByRole("button", { name: "Plan a trip" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Plan a trip" });
  await expect(drawer.getByLabel("Starting from")).toHaveValue("kitchen");
  // The household default from /plan/defaults.
  await expect(drawer.getByLabel("Value of your time")).toHaveValue("20.00");
  await expect(drawer.getByLabel("Stops at most")).toHaveValue("3");
  // The stores are offered as places to start or end.
  await expect(drawer.getByLabel("Starting from").locator("option", { hasText: world.north.name })).toHaveCount(1);
  if (phone) await expectNoSidewaysScroll(page, "the Plan a trip sheet");
  await drawer.getByRole("button", { name: "Plan", exact: true }).click();

  // --- The three cards, each estimated (criterion 11, UI-8.13) ----------------------
  await expect(page).toHaveURL(/\/plan\/trips\/[0-9a-f-]+$/);
  await expect(page.getByTestId("notice")).toContainText("Choose one of the three plans.");
  const cards = page.getByTestId("variant-card");
  await expect(cards).toHaveCount(3);
  for (const kind of ["fastest", "cheapest", "balanced"]) {
    const card = page.locator(`[data-testid=variant-card][data-kind=${kind}]`);
    await expect(card.getByText("estimated", { exact: true })).toBeVisible();
    await expect(card.getByText("Drive times are estimates: the routing engine wasn't available.")).toBeVisible();
    await expect(card.getByRole("list", { name: /stops$/ }).getByRole("listitem")).toHaveCount(2);
  }
  await expect(page.locator("[data-testid=variant-card][data-kind=cheapest]").getByText("cheapest", { exact: true })).toBeVisible();
  if (phone) {
    // Balanced first on a phone (UI-8.19).
    const first = await cards.evaluateAll((els) => els.map((e) => [e.getAttribute("data-kind"), e.getBoundingClientRect().top] as const).sort((a, b) => a[1] - b[1])[0][0]);
    expect(first).toBe("balanced");
    await expectNoSidewaysScroll(page, "the trip page");
  }
  const predicted = money((await page.locator("[data-testid=variant-card][data-kind=balanced]").getByTestId("variant-items").textContent()) ?? "");
  expect(predicted).toBe(14);

  // --- Choose (UI-8.15) ------------------------------------------------------------
  await page.locator("[data-testid=variant-card][data-kind=balanced]").getByRole("button", { name: "Choose" }).click();
  await expect(page.getByText(/^Chosen /)).toBeVisible();
  await expect(page.getByText("Show the other plans")).toBeVisible();
  const start = page.getByRole("link", { name: "Start the trip" });
  await expect(start).toBeVisible();
  await start.click();

  // --- Stop 1 (UI-4.8, UI-8.17) ------------------------------------------------------
  await expect(page).toHaveURL(/\/stop\/1$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`Stop 1 of 2 · ${world.kitchenName} list`);
  const items = page.getByTestId("stop-item");
  await expect(items).toHaveCount(2);
  const cheaper = page.getByTestId("cheaper-item");
  await expect(page.getByRole("heading", { name: "Cheaper at stop 2" })).toBeVisible();
  await expect(cheaper).toHaveCount(1);
  await expect(cheaper.getByTestId("cheaper-difference")).toHaveText("+$0.60 here");
  if (phone) await checkStopOnPhone(page, "stop 1");

  // Edit the first item's price: held, tinted, "edited", the plan's figure kept.
  const firstItem = items.first();
  const editedName = (await firstItem.getByTestId("item-name").textContent())!.trim();
  await firstItem.getByTestId("item-price").click();
  const editor = page.getByTestId("price-editor");
  await editor.getByLabel("Price").fill("3.25");
  await editor.getByRole("button", { name: "Save" }).click();
  const edited = page.locator("[data-testid=stop-item][data-edited=true]");
  await expect(edited).toHaveCount(1);
  await expect(edited.getByText("edited", { exact: true })).toBeVisible();
  await expect(edited.getByTestId("planned-caption")).toContainText("planned $");
  await expect(edited.getByTestId("item-name")).toHaveText(editedName);

  // Get here: the later stop's item moves to this stop at this store's price.
  const movedName = `${(await cheaper.locator(".font-medium").first().textContent())!.trim()}`;
  await cheaper.getByRole("button", { name: /^Get .* here$/ }).click();
  await expect(page.getByTestId("notice")).toContainText(`Moved ${movedName} here from stop 2.`);
  await expect(items).toHaveCount(3);
  await expect(page.getByTestId("cheaper-item")).toHaveCount(0);
  await expect(page.locator("[data-testid=stop-item]", { hasText: "moved here" })).toHaveCount(1);

  // Tick all three: they move under the rule and the strip counts them.
  for (let i = 0; i < 3; i++) {
    await page.getByRole("list", { name: "To get here" }).getByRole("checkbox").first().click();
  }
  await expect(page.getByTestId("stop-progress")).toContainText("3 of 3 in the basket");
  await expect(page.getByRole("list", { name: "In the basket" }).getByTestId("stop-item")).toHaveCount(3);
  if (phone) await checkStopOnPhone(page, "stop 1 with the basket full");

  // --- Done here (UI-8.18): confirm inline, the Notice offers the next stop ------------
  await page.getByRole("button", { name: "Done here · record purchase" }).click();
  const confirm = page.getByTestId("done-confirm");
  await expect(confirm.getByRole("button", { name: "Not yet" })).toBeFocused();
  await confirm.getByRole("button", { name: "Record" }).click();
  const notice = page.getByTestId("notice");
  await expect(notice).toContainText(/Recorded 3 lines at .* · \$\d+\.\d{2}\./);
  const firstTotal = money((await notice.textContent()) ?? "");
  // The screen stays on the done view; the Notice offers the way on.
  await expect(page.getByTestId("stop-done")).toContainText("3 lines recorded");
  await expect(page.getByRole("link", { name: "Open the purchase" })).toBeVisible();
  await notice.getByRole("link", { name: /^Next stop: / }).click();

  // --- Stop 2 ---------------------------------------------------------------------
  await expect(page).toHaveURL(/\/stop\/2$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`Stop 2 of 2 · ${world.kitchenName} list`);
  await expect(page.getByTestId("stop-item")).toHaveCount(1);
  // Nothing is offered from an earlier stop.
  await expect(page.getByTestId("cheaper-section")).toHaveCount(0);
  if (phone) await checkStopOnPhone(page, "stop 2");
  await page.getByRole("list", { name: "To get here" }).getByRole("checkbox").first().click();
  await page.getByRole("button", { name: "Done here · record purchase" }).click();
  await page.getByTestId("done-confirm").getByRole("button", { name: "Record" }).click();
  await expect(notice).toContainText(/Recorded 1 line at .* · \$\d+\.\d{2}\./);
  const secondTotal = money((await notice.textContent()) ?? "");
  await notice.getByRole("link", { name: "Back to the trip" }).click();

  // --- Back on the trip: done, predicted beside actual ------------------------------------
  await expect(page).toHaveURL(/\/plan\/trips\/[0-9a-f-]+$/);
  await expect(page.getByText("Done", { exact: true }).first()).toBeVisible();
  const actual = (firstTotal + secondTotal).toFixed(2);
  await expect(page.getByText(`predicted $14.00 · actual $${actual}`)).toBeVisible();
  // Each stop's own line; on a phone the stops fold, so it is read on desktop.
  if (!phone) await expect(page.getByTestId("stop-actual")).toHaveCount(2);
  if (phone) await expectNoSidewaysScroll(page, "the done trip");

  // The trips list names the stores in order and shows predicted beside actual.
  await page.goto("/plan/trips");
  const row = page.getByTestId("trip-row").first();
  await expect(row.getByTestId("trip-row-stores")).toContainText(" → ");
  await expect(row.getByTestId("trip-row-figures")).toContainText(`predicted $14.00 · actual $${actual}`);
});
