import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TripCreateInput } from "../api/trips";
import { NoticeProvider } from "../components/Notice";
import { nextHourLocal } from "../components/plan/PlanTripDrawer";
import { availableActions, matchActions } from "../lib/paletteActions";
import { createQueryClient } from "../lib/queryClient";
import { TripsPage } from "../pages/plan/TripsPage";
import { colourOutsideItsMeaning } from "./colour-walk";
import { chainLocation, homeBase, marketLocation } from "./geo-fixtures";
import {
  adminUser,
  errorResponse,
  jsonResponse,
  mockApi,
  type RecordedCall,
  type RouteHandler,
} from "./helpers";
import { kitchen, kitchenId, noKitchenSettings, planHealth, settings } from "./plan-fixtures";
import { chosenTrip, olderTripId, summary, trip, tripId } from "./trip-fixtures";

/**
 * The trips list and the Plan a trip drawer (17, 4B; 10, Trips; UI-8.12, 8.19,
 * 8.20). Mounted on its own routes with the API mocked: the planner endpoints
 * come with #325.
 */

const secondKitchen = {
  ...homeBase,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e02",
  place_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7009",
  name: "Ridge cabin",
  lat: "33.700000",
  lon: "-120.300000",
};
const defaults = {
  value_of_time_per_hour: "26.50",
  max_stops_default: 3,
  candidate_radius_km: "25",
};

const older = summary({
  ...chosenTrip(),
  id: olderTripId,
  planned_at: "2026-10-03T17:00:00Z",
  status: "done",
  value_of_time_per_hour: "24.00",
});
const newer = summary(trip({ value_of_time_per_hour: "26.50" }));

function baseRoutes(): Record<string, RouteHandler> {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /plan/trips": () => jsonResponse(200, { items: [older, newer] }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    "GET /plan/defaults": () => jsonResponse(200, defaults),
  };
}

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function mount(routes: Record<string, RouteHandler> = {}, path = "/plan/trips"): RecordedCall[] {
  const calls = mockApi({ ...baseRoutes(), ...routes });
  const client = createQueryClient({ retry: false });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <main>
          <NoticeProvider>
            <Routes>
              <Route path="/plan/trips" element={<TripsPage />} />
              <Route path="/plan/trips/:id" element={<h1>Trip page</h1>} />
              <Route path="/plan/list" element={<h1>List page</h1>} />
              <Route path="/settings/kitchens" element={<h1>Kitchens</h1>} />
            </Routes>
            <Where />
          </NoticeProvider>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
  vi.useRealTimers();
});

const rows = () => screen.findAllByTestId("trip-row");

describe("the trips list (UI-8.12, UI-8.20)", () => {
  it("heads the page, tags the kitchen and lists the kitchen's trips newest first", async () => {
    const calls = mount();
    expect(await screen.findByTestId("kitchen-tag")).toHaveTextContent(kitchen.name);
    expect(screen.getByRole("heading", { level: 1, name: /Trips/ })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Where to shop for the list, in what order, and what the alternatives cost.",
      ),
    ).toBeInTheDocument();
    const [first, second] = await rows();
    expect(first).toHaveAttribute("data-id", tripId);
    expect(second).toHaveAttribute("data-id", olderTripId);
    expect(within(first).getByText("Planned")).toBeInTheDocument();
    expect(within(first).getByTestId("trip-row-figures")).toHaveTextContent(
      "Not chosen yet · Balanced $38.60 · 50 min · 2 stops",
    );
    expect(within(second).getByText("Done")).toBeInTheDocument();
    expect(within(second).getByTestId("trip-row-figures")).toHaveTextContent(
      "Cheapest · $36.25 · 75 min · 2 stops",
    );
    expect(within(first).getByRole("link")).toHaveAttribute("href", `/plan/trips/${tripId}`);
    // The stores in visiting order: Balanced's until a plan is chosen, then the chosen one's.
    const names = (t: typeof newer) => t.stops.map((x) => x.name).join(" → ");
    expect(newer.stops.length).toBeGreaterThan(0);
    expect(within(first).getByTestId("trip-row-stores")).toHaveTextContent(names(newer));
    expect(within(second).getByTestId("trip-row-stores")).toHaveTextContent(names(older));
    // Scoped to the current kitchen, never inferred by the server.
    const list = calls.find((c) => c.method === "GET" && c.path.startsWith("/plan/trips"));
    expect(list?.query.get("home_base_id")).toBe(kitchenId);
  });

  it("asks for the current kitchen's trips after a switch", async () => {
    const calls = mount({
      "GET /home-bases": () => jsonResponse(200, { items: [kitchen, secondKitchen] }),
      "GET /me/settings": () =>
        jsonResponse(200, { ...settings, current_home_base_id: secondKitchen.id }),
      "GET /plan/trips": () => jsonResponse(200, { items: [] }),
    });
    expect(await screen.findByText("No trips yet")).toBeInTheDocument();
    expect(screen.getByTestId("kitchen-tag")).toHaveTextContent("Ridge cabin");
    expect(
      calls.filter((c) => c.path.startsWith("/plan/trips")).map((c) => c.query.get("home_base_id")),
    ).toEqual([secondKitchen.id]);
  });

  it("offers Plan a trip from the empty state", async () => {
    mount({ "GET /plan/trips": () => jsonResponse(200, { items: [] }) });
    const empty = await screen.findByRole("region", { name: "No trips yet" });
    expect(empty).toHaveTextContent(`Plan one from the ${kitchen.name} list.`);
    await userEvent.setup().click(within(empty).getByRole("button", { name: "Plan a trip" }));
    expect(await screen.findByRole("dialog", { name: "Plan a trip" })).toBeInTheDocument();
  });

  it("shows an error with Try again, never the empty state", async () => {
    mount({ "GET /plan/trips": () => errorResponse(500, "internal", "Something broke.") });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the trips");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("No trips yet")).not.toBeInTheDocument();
  });

  it("says where to add a kitchen when there is none", async () => {
    mount({
      "GET /home-bases": () => jsonResponse(200, { items: [] }),
      "GET /me/settings": () => jsonResponse(200, noKitchenSettings),
    });
    expect(await screen.findByText("No kitchen yet")).toBeInTheDocument();
  });

  it("colours only a Done trip olive, and every other status neutral (UI-8.20)", async () => {
    mount();
    const [first, second] = await rows();
    expect(within(second).getByText("Done").className).toMatch(/green/);
    expect(within(first).getByText("Planned").className).not.toMatch(/green|blue|amber/);
  });
});

describe("the Plan a trip drawer (UI-8.12)", () => {
  it("opens from ?new=1, consumes the parameter, and fills the defaults", async () => {
    mount({}, "/plan/trips?new=1");
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/plan\/trips$/));
    expect(within(drawer).getByLabelText("Starting from")).toHaveDisplayValue(
      `${kitchen.name} (kitchen)`,
    );
    expect(within(drawer).getByLabelText("Ending at")).toHaveDisplayValue("Same as the start");
    expect(within(drawer).getByLabelText("When")).toHaveValue(nextHourLocal());
    // The household's value of time, from /plan/defaults.
    await waitFor(() =>
      expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("26.50"),
    );
    expect(
      within(drawer).getByText(
        "Dollars per hour. What an hour of driving and shopping is worth to you.",
      ),
    ).toBeInTheDocument();
    expect(within(drawer).getByLabelText("Stops at most")).toHaveValue("3");
    expect(
      within(within(drawer).getByLabelText("Stops at most"))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["1", "2", "3", "4"]);
  });

  it("takes the value of time and stop count from the household defaults, not the last plan", async () => {
    mount(
      {
        "GET /plan/defaults": () =>
          jsonResponse(200, { ...defaults, value_of_time_per_hour: "31.00", max_stops_default: 2 }),
      },
      "/plan/trips?new=1",
    );
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("31.00");
    expect(within(drawer).getByLabelText("Stops at most")).toHaveValue("2");
  });

  it("falls back to 20.00 an hour when the defaults cannot be read", async () => {
    mount(
      { "GET /plan/defaults": () => errorResponse(500, "internal", "Something broke.") },
      "/plan/trips?new=1",
    );
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("20.00");
  });

  it("offers the other kitchens and the vendor locations by place, and sends the chosen places", async () => {
    let body: TripCreateInput | null = null;
    mount(
      {
        "GET /home-bases": () => jsonResponse(200, { items: [kitchen, secondKitchen] }),
        "GET /vendor-locations": () =>
          jsonResponse(200, { items: [chainLocation, marketLocation] }),
        "POST /plan/trips": (call) => {
          body = call.body as TripCreateInput;
          return jsonResponse(201, trip());
        },
      },
      "/plan/trips?new=1",
    );
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    const start = within(drawer).getByLabelText("Starting from");
    await waitFor(() =>
      expect(
        within(start)
          .getAllByRole("option")
          .map((o) => o.textContent),
      ).toEqual([
        `${kitchen.name} (kitchen)`,
        "Ridge cabin (kitchen)",
        chainLocation.name,
        marketLocation.name,
      ]),
    );
    await user.selectOptions(start, chainLocation.name);
    await user.selectOptions(within(drawer).getByLabelText("Ending at"), "Ridge cabin (kitchen)");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      origin_place_id: chainLocation.place_id,
      destination_place_id: secondKitchen.place_id,
    });
  });

  it("checks the fields before planning", async () => {
    const calls = mount({}, "/plan/trips?new=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    const value = within(drawer).getByLabelText("Value of your time");
    await user.clear(value);
    await user.type(value, "lots");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent(
      "Value of your time must be dollars per hour, 0 or more.",
    );

    await user.clear(value);
    await user.type(value, "18");
    await user.click(within(drawer).getByText("More"));
    await user.type(within(drawer).getByLabelText("Mileage rate"), "-1");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent(
      "Mileage rate must be dollars per mile, 0 or more, or blank.",
    );

    await user.clear(within(drawer).getByLabelText("Mileage rate"));
    await user.type(within(drawer).getByLabelText("Minutes per stop"), "7.5");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent(
      "Minutes per stop must be a whole number, or blank.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("posts the inputs as strings and opens the new trip with a Notice", async () => {
    let body: TripCreateInput | null = null;
    const calls = mount(
      {
        "POST /plan/trips": (call) => {
          body = call.body as TripCreateInput;
          return jsonResponse(201, trip());
        },
      },
      "/plan/trips?new=1",
    );
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    await waitFor(() =>
      expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("26.50"),
    );
    await user.selectOptions(within(drawer).getByLabelText("Stops at most"), "2");
    await user.click(within(drawer).getByText("More"));
    await user.type(within(drawer).getByLabelText("Mileage rate"), "0.67");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));

    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(`/plan/trips/${tripId}`),
    );
    expect(body).toMatchObject({
      home_base_id: kitchenId,
      value_of_time_per_hour: "26.50",
      max_stops: 2,
      mileage_rate: "0.67",
    });
    // The kitchen is the default start, sent as nothing; the end is the start.
    expect(body).not.toHaveProperty("origin_place_id");
    expect(body).not.toHaveProperty("destination_place_id");
    expect(body).not.toHaveProperty("stop_overhead_min");
    expect(typeof (body as unknown as TripCreateInput).planned_at).toBe("string");
    expect((body as unknown as TripCreateInput).planned_at).toMatch(/Z$/);
    expect(calls.find((c) => c.method === "POST")?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      /Planned a trip for .*Choose one of the three plans\./,
    );
  });

  it("refuses an empty list with a link to it", async () => {
    mount(
      {
        "POST /plan/trips": () =>
          errorResponse(422, "empty_list", "The list has no unchecked items to plan for."),
      },
      "/plan/trips?new=1",
    );
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    const alert = await within(drawer).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing on the list to plan for.");
    expect(within(alert).getByRole("link", { name: "Open the list" })).toHaveAttribute(
      "href",
      "/plan/list",
    );
  });

  it("asks before discarding typed input", async () => {
    mount({}, "/plan/trips?new=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    await waitFor(() =>
      expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("26.50"),
    );
    // Untouched, Cancel just closes.
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Plan a trip" })).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Plan a trip" }));
    const again = await screen.findByRole("dialog", { name: "Plan a trip" });
    await user.selectOptions(within(again).getByLabelText("Stops at most"), "4");
    await user.click(within(again).getByRole("button", { name: "Cancel" }));
    expect(within(again).getByText(/Discard this trip\?/)).toBeInTheDocument();
    expect(within(again).getByRole("button", { name: "Keep editing" })).toHaveFocus();
  });
});

describe("Plan a trip in the palette (UI-8.12; 09)", () => {
  it("goes to the drawer and is offered only once Plan is built", () => {
    const withPlan = availableActions(["plan"], false);
    const action = matchActions("plan a trip", withPlan)[0];
    expect(action?.label).toBe("Plan a trip");
    expect(action?.route).toBe("/plan/trips?new=1");
    expect(availableActions(["catalog", "shop", "cook"], false).map((a) => a.label)).not.toContain(
      "Plan a trip",
    );
  });
});

describe("the trips list on a phone (UI-8.19)", () => {
  it("keeps Plan a trip and opens rows as links", async () => {
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
    const [first] = await rows();
    expect(within(first).getByRole("link").className).toMatch(/min-h-11/);
    expect(screen.getByRole("button", { name: "Plan a trip" }).className).toMatch(/min-h-11/);
  });
});

describe("colour on the trips list (UI-8.20)", () => {
  it("walks every element: blue only on actions, olive only on Done", async () => {
    mount();
    await rows();
    expect(
      colourOutsideItsMeaning(screen.getByRole("main"), { olive: (_, text) => text === "Done" }),
    ).toEqual([]);
  });
});
