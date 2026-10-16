import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StopScreen, Trip } from "../api/trips";
import { NoticeProvider } from "../components/Notice";
import { createQueryClient } from "../lib/queryClient";
import { StopPage } from "../pages/plan/StopPage";
import { TripDetailPage } from "../pages/plan/TripDetailPage";
import { units } from "./catalog-fixtures";
import { colourOutsideItsMeaning } from "./colour-walk";
import { chainLocation, marketLocation } from "./geo-fixtures";
import {
  adminUser,
  errorResponse,
  jsonResponse,
  mockApi,
  type RecordedCall,
  type RouteHandler,
} from "./helpers";
import { kitchen, planHealth, settings } from "./plan-fixtures";
import {
  doneResult,
  lentilItemId,
  milk,
  milkItemId,
  oatsItemId,
  purchaseId,
  stopItem,
  stopScreen,
} from "./stop-fixtures";
import { balanced, chosenTrip, tripId } from "./trip-fixtures";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

/**
 * At the store (17, 4C; 10, At the store; UI-4.8, UI-8.15, UI-8.17–8.20).
 * The stop endpoints are mocked: they come with the stop API (#331).
 */

const stop1 = `/plan/trips/${tripId}/stops/1`;
const stop2 = `/plan/trips/${tripId}/stops/2`;

function baseRoutes(current: () => StopScreen, trip: () => Trip): Record<string, RouteHandler> {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "HEAD /tiles/basemap.pmtiles": () => jsonResponse(404),
    [`GET /plan/trips/${tripId}`]: () => jsonResponse(200, trip()),
    [`GET ${stop1}`]: () => jsonResponse(200, current()),
    [`GET ${stop2}`]: () =>
      jsonResponse(
        200,
        stopScreen({
          seq: 2,
          next_seq: null,
          items: [],
          cheaper_elsewhere: [],
          location: {
            id: marketLocation.id,
            name: marketLocation.name,
            vendor_name: marketLocation.vendor.name,
            address: null,
          },
        }),
      ),
  };
}

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname}</p>;
}

interface MountOptions {
  routes?: Record<string, RouteHandler>;
  current?: () => StopScreen;
  trip?: () => Trip;
  at?: string;
}

function mount({
  routes = {},
  current = () => stopScreen(),
  trip = () => chosenTrip(balanced),
  at = `/plan/trips/${tripId}/stop/1`,
}: MountOptions = {}): RecordedCall[] {
  const calls = mockApi({ ...baseRoutes(current, trip), ...routes });
  const client = createQueryClient({ retry: false });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[at]}>
        <main>
          <NoticeProvider>
            <Routes>
              <Route path="/plan/trips/:id" element={<TripDetailPage />} />
              <Route path="/plan/trips/:id/stop/:seq" element={<StopPage />} />
            </Routes>
            <Where />
          </NoticeProvider>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

beforeEach(() => window.sessionStorage.clear());
afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const row = (id: string) => screen.getAllByTestId("stop-item").find((r) => r.dataset.id === id)!;
const footer = () => screen.getByTestId("stop-footer");

describe("the stop screen (UI-4.8, UI-8.17)", () => {
  it("shows the header, the store, the progress strip and the items to get here", async () => {
    mount();
    expect(
      await screen.findByRole("heading", { level: 1, name: `Stop 1 of 2 · ${kitchen.name} list` }),
    ).toBeInTheDocument();
    expect(screen.getByText(chainLocation.name)).toBeInTheDocument();
    expect(screen.getByText("Ground floor, Harbour arcade")).toBeInTheDocument();
    expect(screen.getByTestId("stop-progress")).toHaveTextContent("0 of 2 in the basket");
    expect(screen.getByTestId("estimated-here")).toHaveTextContent("About $17.20 here");
    expect(screen.getByRole("link", { name: /Back to the trip/ })).toHaveAttribute(
      "href",
      `/plan/trips/${tripId}`,
    );

    const m = row(milkItemId);
    expect(within(m).getByTestId("item-name")).toHaveTextContent("Oat milk");
    expect(within(m).getByTestId("item-packs")).toHaveTextContent("2 packs of 946 ml");
    expect(within(m).getByText("Moonfield oat milk")).toBeInTheDocument();
    expect(within(m).getByTestId("item-price")).toHaveTextContent("$8.50");

    const l = row(lentilItemId);
    expect(within(l).getByText("stale").className).toMatch(/amber/);
    // The claim chip is neutral, never herb.
    const claim = within(l).getByText(/is getting it/);
    expect(claim.className).toMatch(/neutral/);
    expect(claim.className).not.toMatch(/blue/);
  });

  it("gives the check circle 28px in a 44px hit area, and the price a 44px button", async () => {
    mount();
    const check = await screen.findByRole("checkbox", { name: "Check Oat milk" });
    expect(check.className).toMatch(/\bsize-11\b/);
    expect(within(check).getByTestId("check-circle").className).toMatch(/\bsize-7\b/);
    expect(within(row(milkItemId)).getByTestId("item-price").className).toMatch(/min-h-11/);
  });

  it("ticks an item into the basket, moves it to the foot, and back again", async () => {
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Check Oat milk" }));
    const basket = screen.getByRole("list", { name: "In the basket" });
    expect(within(basket).getByText("Oat milk")).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "To get here" })).queryByText("Oat milk"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("stop-progress")).toHaveTextContent("1 of 2 in the basket · $8.50");
    expect(screen.getByText("Checked Oat milk. 1 to get.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done here · record purchase" })).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Uncheck Oat milk" }));
    expect(screen.queryByRole("list", { name: "In the basket" })).not.toBeInTheDocument();
    expect(screen.getByTestId("stop-progress")).toHaveTextContent("0 of 2 in the basket");
  });

  it("holds an edited price with the neutral tint and edited, and clears it", async () => {
    const edited = stopScreen({
      estimated_here: "16.68",
      items: [
        stopItem({
          ...milk,
          actual_price: "3.99",
          actual_qty: "1",
          actual_unit: "each",
          edited: true,
          effective_cost: "7.98",
        }),
        stopScreen().items[1],
      ],
    });
    const calls = mount({
      routes: {
        [`PUT ${stop1}/items/${milkItemId}/price`]: () => jsonResponse(200, edited),
        [`DELETE ${stop1}/items/${milkItemId}/price`]: () => jsonResponse(200, stopScreen()),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Price of Oat milk, \$8\.50/ }));
    const editor = screen.getByTestId("price-editor");
    const price = within(editor).getByLabelText("Price");
    expect(price).toHaveFocus();
    expect(price).toHaveAttribute("inputmode", "decimal");
    await user.type(price, "3.99");
    await user.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(row(milkItemId).dataset.edited).toBe("true"));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ price: "3.99", qty: "1", unit: "each" });
    const m = row(milkItemId);
    expect(m.className).toMatch(/bg-neutral-100/);
    expect(m.className).not.toMatch(/blue|green|amber/);
    expect(within(m).getByText("edited")).toBeInTheDocument();
    expect(within(m).getByTestId("planned-caption")).toHaveTextContent("planned $8.50");
    expect(within(m).getByTestId("item-price")).toHaveTextContent("$7.98");
    expect(screen.getByTestId("estimated-here")).toHaveTextContent("About $16.68 here");
    expect(screen.queryByTestId("price-editor")).not.toBeInTheDocument();

    await user.click(within(m).getByTestId("item-price"));
    await user.click(screen.getByRole("button", { name: "Use the planned price" }));
    await waitFor(() => expect(row(milkItemId).dataset.edited).toBe("false"));
    expect(
      calls.some((c) => c.method === "DELETE" && c.path === `${stop1}/items/${milkItemId}/price`),
    ).toBe(true);
    expect(within(row(milkItemId)).queryByText("edited")).not.toBeInTheDocument();
  });

  it("refuses a price that is not a decimal without calling the server", async () => {
    const calls = mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Price of Oat milk/ }));
    await user.type(screen.getByLabelText("Price"), "four");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Enter the shelf price, such as 4.29.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});

describe("Cheaper at stop 2 (UI-8.17)", () => {
  it("lists a later stop's item with the difference, and Get here moves it in", async () => {
    let current = stopScreen();
    const moved = stopItem({
      id: oatsItemId,
      label: "Rolled oats",
      got_here: true,
      line_cost: "11.30",
      effective_cost: "11.30",
    });
    const calls = mount({
      current: () => current,
      routes: {
        [`POST ${stop1}/get-here`]: () => {
          current = stopScreen({
            items: [...stopScreen().items, moved],
            cheaper_elsewhere: [],
            estimated_here: "28.50",
          });
          return jsonResponse(200, current);
        },
      },
    });
    const user = userEvent.setup();
    const section = await screen.findByTestId("cheaper-section");
    expect(within(section).getByRole("heading", { name: "Cheaper at stop 2" })).toBeInTheDocument();
    expect(within(section).getByTestId("cheaper-difference")).toHaveTextContent("+$0.80 here");
    expect(
      within(section).getByText(/\$11\.30 here, \$10\.50 at Pier Farmers Market/),
    ).toBeInTheDocument();

    await user.click(within(section).getByRole("button", { name: "Get Rolled oats here" }));
    await waitFor(() => expect(screen.queryByTestId("cheaper-section")).not.toBeInTheDocument());
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ item_id: oatsItemId });
    expect(post.headers.get("Idempotency-Key")).toBeTruthy();
    expect(within(row(oatsItemId)).getByText("moved here")).toBeInTheDocument();
    expect(screen.getByTestId("stop-progress")).toHaveTextContent("0 of 3 in the basket");
    expect(screen.getByTestId("estimated-here")).toHaveTextContent("About $28.50 here");
  });

  it("says Not sold here on a 422, inline, and offers no second Get here", async () => {
    mount({
      routes: {
        [`POST ${stop1}/get-here`]: () =>
          errorResponse(422, "not_offered_here", "This location has no price for it."),
      },
    });
    const user = userEvent.setup();
    const section = await screen.findByTestId("cheaper-section");
    await user.click(within(section).getByRole("button", { name: "Get Rolled oats here" }));
    expect(await within(section).findByText("Not sold here")).toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: "Get Rolled oats here" }),
    ).not.toBeInTheDocument();
  });

  it("reuses the same key when a failed Get here is retried", async () => {
    let attempts = 0;
    const calls = mount({
      routes: {
        [`POST ${stop1}/get-here`]: () =>
          ++attempts === 1
            ? errorResponse(503, "unavailable", "Try again.")
            : jsonResponse(200, stopScreen({ cheaper_elsewhere: [] })),
      },
    });
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Get Rolled oats here" });
    await user.click(button);
    expect(await screen.findByText(/Couldn't move it/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Get Rolled oats here" }));
    await waitFor(() => expect(screen.queryByTestId("cheaper-section")).not.toBeInTheDocument());
    const keys = calls
      .filter((c) => c.method === "POST")
      .map((c) => c.headers.get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });
});

describe("Done here (UI-8.18)", () => {
  it("confirms inline with focus on Not yet, records the checked ids, and offers the next stop", async () => {
    let current = stopScreen();
    let attempts = 0;
    const calls = mount({
      current: () => current,
      routes: {
        [`POST ${stop1}/done`]: () => {
          if (++attempts === 1) return errorResponse(503, "unavailable", "The database is busy.");
          current = stopScreen({
            done_at: "2026-10-17T16:30:00Z",
            purchase_id: purchaseId,
            lines_recorded: 1,
            actual_cost: "8.50",
            items: [{ ...milk, checked_at: "2026-10-17T16:30:00Z" }, stopScreen().items[1]],
          });
          return jsonResponse(200, doneResult());
        },
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Check Oat milk" }));
    await user.click(screen.getByRole("button", { name: "Done here · record purchase" }));
    const confirm = screen.getByTestId("done-confirm");
    expect(confirm).toHaveTextContent(
      `Record 1 item at ${chainLocation.name} for $8.50? Prices you edited but didn't buy are saved as shelf prices.`,
    );
    expect(within(confirm).getByRole("button", { name: "Not yet" })).toHaveFocus();

    // A failed record keeps the ticks and shows its alert in the panel.
    await user.click(within(confirm).getByRole("button", { name: "Record" }));
    expect(await within(confirm).findByText(/Couldn't record this stop/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Uncheck Oat milk" })).toBeInTheDocument();

    // The retry carries the same key, so a lost answer replays rather than records twice.
    await user.click(
      within(screen.getByTestId("done-confirm")).getByRole("button", { name: "Record" }),
    );
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent(`Recorded 1 line at ${chainLocation.name} · $8.50.`);
    const posts = calls.filter((c) => c.method === "POST" && c.path === `${stop1}/done`);
    expect(posts).toHaveLength(2);
    expect(posts[0].body).toEqual({ checked_item_ids: [milkItemId] });
    expect(posts[0].headers.get("Idempotency-Key")).toBeTruthy();
    expect(posts[1].headers.get("Idempotency-Key")).toBe(posts[0].headers.get("Idempotency-Key"));

    // The done view: no controls, the purchase linked.
    expect(await screen.findByTestId("stop-done")).toHaveTextContent("1 line recorded · $8.50");
    expect(screen.getByRole("link", { name: "Open the purchase" })).toHaveAttribute(
      "href",
      `/shop/purchases/${purchaseId}`,
    );
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stop-footer")).not.toBeInTheDocument();

    const next = within(notice).getByRole("link", { name: `Next stop: ${marketLocation.name}` });
    expect(next).toHaveFocus();
    await user.click(next);
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(`/plan/trips/${tripId}/stop/2`),
    );
    expect(
      await screen.findByRole("heading", { level: 1, name: `Stop 2 of 2 · ${kitchen.name} list` }),
    ).toBeInTheDocument();
  });

  it("offers Back to the trip after the last stop, where predicted sits beside actual", async () => {
    let current = stopScreen({ seq: 2, next_seq: null, cheaper_elsewhere: [] });
    let trip = chosenTrip(balanced);
    mount({
      at: `/plan/trips/${tripId}/stop/2`,
      current: () => current,
      trip: () => trip,
      routes: {
        [`GET ${stop2}`]: () => jsonResponse(200, current),
        [`POST ${stop2}/done`]: () => {
          current = {
            ...current,
            done_at: "2026-10-17T16:50:00Z",
            purchase_id: purchaseId,
            lines_recorded: 1,
            actual_cost: "8.50",
          };
          const [s1, s2] = balanced.stops;
          trip = {
            ...chosenTrip(balanced),
            status: "done",
            predicted_item_cost: "27.70",
            actual_item_cost: "25.10",
            variants: [
              {
                ...balanced,
                stops: [
                  {
                    ...s1,
                    done_at: "2026-10-17T16:30:00Z",
                    actual_cost: "16.60",
                    purchase_id: purchaseId,
                  },
                  { ...s2, done_at: "2026-10-17T16:50:00Z", actual_cost: "8.50" },
                ],
              },
            ],
          };
          return jsonResponse(200, doneResult({ seq: 2, next_seq: null, status: "done" }));
        },
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Check Oat milk" }));
    await user.click(screen.getByRole("button", { name: "Done here · record purchase" }));
    await user.click(screen.getByRole("button", { name: "Record" }));
    const back = await within(await screen.findByTestId("notice")).findByRole("link", {
      name: "Back to the trip",
    });
    await user.click(back);
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(`/plan/trips/${tripId}`),
    );
    expect(await screen.findByText(/predicted \$27\.70 · actual \$25\.10/)).toBeInTheDocument();
    const actuals = screen.getAllByTestId("stop-actual");
    expect(actuals[0]).toHaveTextContent("predicted $17.20 · actual $16.60");
    expect(actuals[1]).toHaveTextContent("predicted $10.50 · actual $8.50");
  });

  it("moves on with nothing bought after asking, and sends no ids", async () => {
    const calls = mount({
      routes: {
        [`POST ${stop1}/done`]: () =>
          jsonResponse(
            200,
            doneResult({ purchase_id: null, lines_recorded: 0, checked: 0, observation_ids: [] }),
          ),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Done here · nothing bought" }));
    expect(screen.getByTestId("done-confirm")).toHaveTextContent(
      "Move on without buying anything here?",
    );
    await user.click(screen.getByRole("button", { name: "Move on" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      `Moved on from ${chainLocation.name}; nothing bought.`,
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ checked_item_ids: [] });
  });

  it("Not yet closes the panel and records nothing", async () => {
    const calls = mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Done here · nothing bought" }));
    await user.click(screen.getByRole("button", { name: "Not yet" }));
    expect(screen.queryByTestId("done-confirm")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("refreshes into the read-only done view on a 409 stop_done", async () => {
    let current = stopScreen();
    mount({
      current: () => current,
      routes: {
        [`POST ${stop1}/done`]: () => {
          current = stopScreen({
            done_at: "2026-10-17T16:30:00Z",
            purchase_id: purchaseId,
            lines_recorded: 2,
            actual_cost: "17.20",
          });
          return errorResponse(409, "stop_done", "This stop is already done.");
        },
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Done here · nothing bought" }));
    await user.click(screen.getByRole("button", { name: "Move on" }));
    expect(await screen.findByTestId("stop-done")).toHaveTextContent("2 lines recorded · $17.20");
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "This stop was already recorded; showing it.",
    );
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Get .* here/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: `Next stop: ${marketLocation.name}` })).toHaveAttribute(
      "href",
      `/plan/trips/${tripId}/stop/2`,
    );
  });

  it("explains a 409 plan_ended in the panel with a way back to the trip", async () => {
    mount({
      routes: {
        [`POST ${stop1}/done`]: () => errorResponse(409, "plan_ended", "This plan is abandoned."),
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Done here · nothing bought" }));
    await user.click(screen.getByRole("button", { name: "Move on" }));
    const confirm = screen.getByTestId("done-confirm");
    expect(await within(confirm).findByText(/This trip has ended/)).toBeInTheDocument();
    expect(within(confirm).getByRole("link", { name: "Open the trip" })).toHaveAttribute(
      "href",
      `/plan/trips/${tripId}`,
    );
  });
});

describe("the stop screen's states", () => {
  it("says Choose a plan first on 409 plan_not_chosen, with a link to the trip", async () => {
    mount({
      routes: {
        [`GET ${stop1}`]: () => errorResponse(409, "plan_not_chosen", "Choose a plan first."),
      },
    });
    const msg = await screen.findByTestId("plan-not-chosen");
    expect(msg).toHaveTextContent("Choose a plan first.");
    expect(within(msg).getByRole("link", { name: "Open the trip" })).toHaveAttribute(
      "href",
      `/plan/trips/${tripId}`,
    );
    expect(screen.queryByTestId("stop-footer")).not.toBeInTheDocument();
  });

  it("shows an error with Try again, never an empty stop", async () => {
    mount({
      routes: { [`GET ${stop1}`]: () => errorResponse(500, "internal", "Something broke.") },
    });
    expect(await screen.findByText(/Couldn't load the stop/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps the ticks on this device across a reload of the screen", async () => {
    window.sessionStorage.setItem(
      `kerp.stop.${tripId}.1`,
      JSON.stringify([milkItemId, "not-on-this-stop"]),
    );
    mount();
    expect(await screen.findByRole("checkbox", { name: "Uncheck Oat milk" })).toBeInTheDocument();
    expect(screen.getByTestId("stop-progress")).toHaveTextContent("1 of 2 in the basket");
  });
});

describe("the stop screen on a 390px phone (UI-8.19, 17 criterion 27)", () => {
  it("anchors Done here at the bottom and keeps every control at 44px or more", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    });
    mount();
    await screen.findByTestId("stop-footer");
    expect(footer().className).toMatch(/\bfixed\b/);
    expect(footer().className).toMatch(/\bbottom-0\b/);
    expect(footer().className).toMatch(/env\(safe-area-inset-bottom\)/);
    const done = within(footer()).getByRole("button", { name: "Done here · nothing bought" });
    expect(done.className).toMatch(/min-h-14/);
    // Every button and link on the screen clears 44px below lg.
    for (const el of [
      ...screen.getAllByRole("button"),
      ...screen.getAllByRole("checkbox"),
      ...screen.getAllByRole("link"),
    ]) {
      expect(el.className, el.textContent ?? "").toMatch(/min-h-1[14]|size-11/);
    }
    // Nothing on the stop screen is blue except actions: no info tint.
    expect(document.querySelector("main")!.innerHTML).not.toMatch(/bg-blue-(50|100|200|300)\b/);
  });
});

describe("the trip page at the store (UI-8.15)", () => {
  it("offers Open this stop on each stop of the chosen plan, and Start the trip to the first", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: true,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    });
    mount({ at: `/plan/trips/${tripId}` });
    const start = await screen.findByRole("link", { name: "Start the trip" });
    expect(start).toHaveAttribute("href", `/plan/trips/${tripId}/stop/1`);
    const opens = screen.getAllByRole("link", { name: "Open this stop" });
    expect(opens.map((a) => a.getAttribute("href"))).toEqual([
      `/plan/trips/${tripId}/stop/1`,
      `/plan/trips/${tripId}/stop/2`,
    ]);
  });

  it("continues at the first stop not yet done, with predicted beside actual", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: true,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    });
    const [s1, s2] = balanced.stops;
    const underway: Trip = {
      ...chosenTrip(balanced),
      status: "underway",
      predicted_item_cost: "27.70",
      actual_item_cost: "16.60",
      variants: [
        {
          ...balanced,
          stops: [
            {
              ...s1,
              done_at: "2026-10-17T16:30:00Z",
              purchase_id: purchaseId,
              actual_cost: "16.60",
              lines_recorded: 2,
            },
            s2,
          ],
        },
      ],
    };
    mount({ at: `/plan/trips/${tripId}`, trip: () => underway });
    expect(await screen.findByRole("link", { name: "Continue at stop 2" })).toHaveAttribute(
      "href",
      `/plan/trips/${tripId}/stop/2`,
    );
    expect(screen.getByText(/predicted \$27\.70 · actual \$16\.60/)).toBeInTheDocument();
    expect(screen.getAllByTestId("stop-actual")[0]).toHaveTextContent(
      "predicted $17.20 · actual $16.60",
    );
  });

  it("has no Open this stop or Start the trip before a plan is chosen", async () => {
    mount({
      at: `/plan/trips/${tripId}`,
      trip: () => ({ ...chosenTrip(balanced), chosen_variant_id: null, chosen_at: null }),
    });
    await screen.findAllByTestId("variant-card");
    expect(screen.queryByRole("link", { name: "Open this stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Start the trip" })).not.toBeInTheDocument();
  });
});

describe("colour on the stop screen (UI-8.20)", () => {
  it("walks every element: blue only on actions, squash only on stale, nothing olive or tomato", async () => {
    mount();
    await screen.findByRole("checkbox", { name: "Check Oat milk" });
    expect(
      colourOutsideItsMeaning(screen.getByRole("main"), {
        squash: (_, text) => text === "stale",
      }),
    ).toEqual([]);
  });
});
