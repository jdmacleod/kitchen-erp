import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Annotation, Trip, TripCreateInput } from "../api/trips";
import { tradeSentence } from "../api/trips";
import { NoticeProvider } from "../components/Notice";
import { createQueryClient } from "../lib/queryClient";
import { TripDetailPage } from "../pages/plan/TripDetailPage";
import { colourOutsideItsMeaning, type ColourMeanings } from "./colour-walk";
import { chainLocation, marketLocation } from "./geo-fixtures";
import {
  adminUser,
  errorResponse,
  jsonResponse,
  mockApi,
  type RecordedCall,
  type RouteHandler,
} from "./helpers";
import { instances } from "./maplibre-stub";
import { kitchen, planHealth, settings } from "./plan-fixtures";
import {
  balanced,
  balancedId,
  cheapest,
  cheapestId,
  chosenTrip,
  fastest,
  placeId,
  replannedTripId,
  trip,
  tripId,
} from "./trip-fixtures";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

/**
 * The trip page (17, 4B; 10, Trip page; UI-8.13–8.16, 8.19, 8.20). Mounted on
 * its own route with the API mocked: the planner endpoints come with #325.
 */

function baseRoutes(current: () => Trip): Record<string, RouteHandler> {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "HEAD /tiles/basemap.pmtiles": () => jsonResponse(404),
    [`GET /plan/trips/${tripId}`]: () => jsonResponse(200, current()),
  };
}

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname}</p>;
}

function mount(
  routes: Record<string, RouteHandler> = {},
  current: () => Trip = () => trip(),
): RecordedCall[] {
  const calls = mockApi({ ...baseRoutes(current), ...routes });
  const client = createQueryClient({ retry: false });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/plan/trips/${tripId}`]}>
        <main>
          <NoticeProvider>
            <Routes>
              <Route path="/plan/trips/:id" element={<TripDetailPage />} />
            </Routes>
            <Where />
          </NoticeProvider>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

function phoneWidth() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const cards = () => screen.findAllByTestId("variant-card");
const card = async (kind: string) => (await cards()).find((c) => c.dataset.kind === kind)!;

describe("the three variants (UI-8.13, UI-8.20)", () => {
  it("shows Fastest, Cheapest and Balanced with their figures and trades", async () => {
    mount();
    expect(await screen.findByRole("heading", { level: 1, name: /Trip, / })).toBeInTheDocument();
    expect(await screen.findByTestId("kitchen-tag")).toHaveTextContent(kitchen.name);
    expect(
      screen.getByText("There and back · time at $20.00 an hour · up to 3 stops"),
    ).toBeInTheDocument();
    expect((await cards()).map((c) => c.dataset.kind)).toEqual(["fastest", "cheapest", "balanced"]);

    const fastest = await card("fastest");
    expect(within(fastest).getByTestId("variant-total")).toHaveTextContent("$41.80");
    expect(within(fastest).getByTestId("variant-items")).toHaveTextContent("$33.10");
    expect(within(fastest).getByTestId("variant-driving")).toHaveTextContent("21 min");
    expect(within(fastest).getByTestId("variant-shopping")).toHaveTextContent("10 min");
    expect(within(fastest).getByTestId("variant-trade")).toHaveTextContent(
      "$3.20 more, 14 minutes less",
    );
    expect(within(fastest).queryByTestId("variant-mileage")).not.toBeInTheDocument();

    const cheap = await card("cheapest");
    expect(within(cheap).getByTestId("variant-trade")).toHaveTextContent(
      "$2.35 less, 19 minutes more",
    );
    expect(within(cheap).getByRole("list", { name: "Cheapest stops" })).toHaveTextContent(
      /1\.\s*Millstone Harbour\$14\.40.*2\.\s*Pier Farmers Market\$10\.50/,
    );

    const balanced = await card("balanced");
    expect(within(balanced).getByTestId("variant-trade")).toHaveTextContent(
      "The reference the others are measured against.",
    );
  });

  it("puts the olive cheapest word on the Cheapest heading only, and colours no figure", async () => {
    mount();
    const cheap = await card("cheapest");
    const badge = within(cheap).getByText("cheapest");
    expect(badge.className).toMatch(/green/);
    expect(within(cheap).getByTestId("variant-total").className).not.toMatch(
      /green|blue|amber|red/,
    );
    for (const c of await cards()) {
      if (c.dataset.kind !== "cheapest")
        expect(within(c).queryByText("cheapest")).not.toBeInTheDocument();
    }
  });

  it("marks a card with an estimated leg in squash, with the hint", async () => {
    mount();
    const cheap = await card("cheapest");
    expect(within(cheap).getByText("estimated").className).toMatch(/amber/);
    expect(
      within(cheap).getByText("Drive times are estimates: the routing engine wasn't available."),
    ).toBeInTheDocument();
    expect(within(await card("fastest")).queryByText("estimated")).not.toBeInTheDocument();
  });

  it("shows mileage when the plan has a rate", async () => {
    mount({}, () => trip({ mileage_rate: "0.6700" }));
    expect(within(await card("fastest")).getByTestId("variant-mileage")).toHaveTextContent("$0.00");
    expect(screen.getByText(/mileage at \$0\.67 a mile/)).toBeInTheDocument();
  });

  it("states a trade in both currencies, or the same as Balanced", () => {
    expect(tradeSentence({ money: "0", minutes: "0" })).toBe("the same as Balanced");
    expect(tradeSentence({ money: "1.05", minutes: "0" })).toBe("$1.05 more, the same time");
    expect(tradeSentence({ money: "0.00", minutes: "-1" })).toBe("the same cost, 1 minute less");
  });
});

describe("notes and stops (UI-8.14)", () => {
  // One note of every kind the planner writes, as sentences naming the item or store.
  const everyKind: Annotation[] = [
    {
      kind: "forced_stop",
      location_id: marketLocation.id,
      text: "Only Pier Farmers Market sells Larkspur rolled oats.",
    },
    {
      kind: "defer_suggestion",
      item_id: "li-oats",
      amount: "2.10",
      text: "Larkspur rolled oats alone needs this stop; deferring it saves $2.10.",
    },
    {
      kind: "stale_dependent",
      amount: "6.40",
      text: "$6.40 of the item cost rests on stale prices.",
    },
    {
      kind: "unsourceable",
      item_id: "li-saffron",
      reason: "no_price",
      text: "No store has a price for Moonglow saffron.",
    },
    {
      kind: "estimated_legs",
      text: "Two legs are estimates: the routing engine didn't answer for them.",
    },
    // Names only the location by id: the page adds the store's name after the sentence.
    { kind: "hours_unknown", location_id: chainLocation.id, text: "Opening hours unknown." },
    {
      kind: "closed_excluded",
      location_id: marketLocation.id,
      text: "Pier Farmers Market is closed at every arrival and was left out.",
    },
  ];
  const squash = new Set(["stale_dependent", "unsourceable", "estimated_legs"]);
  const words: Record<string, string> = {
    forced_stop: "forced stop",
    defer_suggestion: "could defer",
    stale_dependent: "stale",
    unsourceable: "unsourceable",
    estimated_legs: "estimated",
    hours_unknown: "hours unknown",
    closed_excluded: "closed",
  };

  it("says every kind of note as a neutral sentence with its word, squash only for stale, estimated and unsourceable (UI-8.14, criterion 28)", async () => {
    mount({}, () =>
      trip({ variants: [{ ...balanced, annotations: everyKind }, cheapest, fastest] }),
    );
    await cards();
    const notes = screen.getAllByTestId("annotation");
    expect(notes.map((n) => n.dataset.kind)).toEqual(everyKind.map((a) => a.kind));
    for (const [i, note] of notes.entries()) {
      const { kind, text } = everyKind[i];
      const word = within(note).getByText(words[kind], { exact: true });
      if (squash.has(kind)) expect(word.className, kind).toMatch(/amber/);
      else expect(word.className, kind).not.toMatch(/amber|green|blue|red/);
      // The sentence itself is neutral text, whatever the kind.
      const sentence = within(note).getByText(
        (_, el) => el?.tagName === "SPAN" && el.textContent.startsWith(text),
      );
      expect(sentence.className, kind).not.toMatch(/amber|green|blue|red/);
    }
    // A note that names only the location's id gets the store's name.
    expect(notes[5]).toHaveTextContent(`Opening hours unknown. — ${chainLocation.name}`);
    // A sentence that already names the store is not given it twice.
    expect(notes[6].textContent.match(/Pier Farmers Market/g)).toHaveLength(1);
  });

  it("lists the shown variant's notes as words, squash for stale and estimated", async () => {
    mount();
    await cards();
    const notes = screen.getAllByTestId("annotation");
    expect(notes.map((n) => n.dataset.kind)).toEqual(["stale_dependent", "forced_stop"]);
    expect(within(notes[0]).getByText("stale").className).toMatch(/amber/);
    expect(notes[0]).toHaveTextContent("rests on prices older than 90 days");
    expect(within(notes[1]).getByText("forced stop").className).not.toMatch(/amber|green|blue/);

    await userEvent.setup().click(
      within(screen.getByRole("group", { name: "Show the stops of" })).getByRole("button", {
        name: "Cheapest",
      }),
    );
    const shown = screen.getAllByTestId("annotation");
    expect(shown.map((n) => n.dataset.kind)).toEqual(["estimated_legs"]);
    expect(within(shown[0]).getByText("estimated").className).toMatch(/amber/);
  });

  it("shows each stop's leg, items, stale prices and subtotal for Balanced until one is chosen", async () => {
    mount();
    await cards();
    expect(screen.getByRole("heading", { name: "Stops · Balanced" })).toBeInTheDocument();
    const stops = screen.getAllByTestId("stop");
    expect(stops.map((s) => s.dataset.seq)).toEqual(["1", "2"]);
    // 600 s free flow × 1.20 traffic = 12 min.
    expect(within(stops[0]).getByTestId("stop-leg")).toHaveTextContent(/12 min · 6\.4 km · arrive/);
    const items = within(stops[0]).getAllByTestId("stop-item");
    expect(items[0]).toHaveTextContent("Moonfield oat milk2 × $4.25$8.50");
    expect(within(items[1]).getByText("stale").className).toMatch(/amber/);
    expect(within(stops[0]).getByTestId("stop-subtotal")).toHaveTextContent("$17.20");
    const navigate = await within(stops[0]).findByRole("link", { name: "Navigate" });
    expect(navigate).toHaveAttribute("target", "_blank");
    expect(navigate.getAttribute("href")).toContain(
      encodeURIComponent(`${chainLocation.lat},${chainLocation.lon}`),
    );
  });

  it("marks an estimated leg on its stop", async () => {
    mount();
    await cards();
    await userEvent.setup().click(
      within(screen.getByRole("group", { name: "Show the stops of" })).getByRole("button", {
        name: "Cheapest",
      }),
    );
    const [first] = screen.getAllByTestId("stop");
    expect(within(within(first).getByTestId("stop-leg")).getByText("estimated").className).toMatch(
      /amber/,
    );
  });
});

describe("the map (UI-8.16)", () => {
  it("pins the kitchen and the numbered stops, joined in order", async () => {
    mount();
    await cards();
    const map = within(await screen.findByTestId("trip-map"));
    await waitFor(() => expect(map.getAllByRole("button", { name: /^Stop / })).toHaveLength(2));
    const pins = map.getAllByRole("button", { name: /^Stop / });
    expect(pins.map((p) => p.getAttribute("aria-label"))).toEqual([
      "Stop 1: Millstone Harbour",
      "Stop 2: Pier Farmers Market",
    ]);
    expect(pins.map((p) => p.textContent)).toEqual(["1", "2"]);
    expect(pins[0].className).toMatch(/kerp-pin--stop/);
    expect(map.getByRole("button", { name: `${kitchen.name} (kitchen)` }).className).toMatch(
      /kerp-pin--home/,
    );

    const stub = instances[instances.length - 1];
    await waitFor(() => expect(stub.sources.get("kerp-path")).toBeTruthy());
    const line = stub.sources.get("kerp-path")?.data as { geometry: { coordinates: number[][] } };
    expect(line.geometry.coordinates).toEqual([
      [Number(kitchen.lon), Number(kitchen.lat)],
      [Number(chainLocation.lon), Number(chainLocation.lat)],
      [Number(marketLocation.lon), Number(marketLocation.lat)],
      [Number(kitchen.lon), Number(kitchen.lat)],
    ]);
  });

  it("selects a stop from its pin or its button, and the pin shows selected", async () => {
    mount();
    await cards();
    const user = userEvent.setup();
    const map = within(await screen.findByTestId("trip-map"));
    const pin = await map.findByRole("button", { name: "Stop 2: Pier Farmers Market" });
    await user.click(pin);
    expect(pin).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByTestId("stop")[1]).toHaveAttribute("data-selected", "true");
    await user.click(
      within(screen.getAllByTestId("stop")[0]).getByRole("button", { name: "Show on the map" }),
    );
    expect(map.getByRole("button", { name: "Stop 1: Millstone Harbour" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(pin).toHaveAttribute("aria-pressed", "false");
  });
});

describe("choose, replan and abandon (UI-8.15)", () => {
  it("chooses a variant, then shows it first as Chosen with the others folded", async () => {
    let current = trip();
    const calls = mount(
      {
        [`POST /plan/trips/${tripId}/choose`]: () => {
          current = chosenTrip();
          return jsonResponse(200, current);
        },
      },
      () => current,
    );
    const user = userEvent.setup();
    await user.click(within(await card("cheapest")).getByRole("button", { name: "Choose" }));
    await waitFor(() => expect(screen.getAllByTestId("variant-card")).toHaveLength(1));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ variant_id: cheapestId });
    const chosen = screen.getByTestId("variant-card");
    expect(chosen).toHaveAttribute("data-kind", "cheapest");
    expect(within(chosen).getByText(/^Chosen /)).toBeInTheDocument();
    expect(within(chosen).queryByRole("button", { name: "Choose" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Stops · Cheapest" })).toBeInTheDocument();
    expect(await screen.findByTestId("notice")).toHaveTextContent("Chose Cheapest.");
    // "Start the trip" becomes the primary action while stops remain (4C); Replan steps back.
    expect(screen.getByRole("link", { name: "Start the trip" }).className).toMatch(/bg-blue/);
    expect(screen.getByRole("button", { name: "Replan" }).className).not.toMatch(/bg-blue/);

    await user.click(screen.getByText("Show the other plans"));
    expect(screen.getAllByTestId("other-plan").map((p) => p.dataset.kind)).toEqual([
      "fastest",
      "balanced",
    ]);
    expect(screen.getAllByTestId("other-plan")[0]).toHaveTextContent("$3.20 more, 14 minutes less");
  });

  it("refreshes into the chosen view when a second Choose is refused", async () => {
    let current = trip();
    mount(
      {
        [`POST /plan/trips/${tripId}/choose`]: () => {
          current = trip({ chosen_variant_id: balancedId, chosen_at: "2026-10-16T09:20:00Z" });
          return errorResponse(
            409,
            "already_chosen",
            "A variant was already chosen for this plan.",
          );
        },
      },
      () => current,
    );
    await userEvent
      .setup()
      .click(within(await card("fastest")).getByRole("button", { name: "Choose" }));
    await waitFor(() =>
      expect(screen.getByTestId("variant-card")).toHaveAttribute("data-kind", "balanced"),
    );
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "A plan was already chosen for this trip; showing it.",
    );
  });

  it("replans from the same inputs in the drawer, and opens the new plan", async () => {
    let body: TripCreateInput | null = null;
    mount({
      "POST /plan/trips": (call) => {
        body = call.body as TripCreateInput;
        return jsonResponse(201, trip({ id: replannedTripId }));
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Replan" }));
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    expect(within(drawer).getByLabelText("Starting from")).toHaveDisplayValue(
      "Where the last plan started",
    );
    expect(within(drawer).getByLabelText("Ending at")).toHaveDisplayValue("Same as the start");
    expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("20.00");
    expect(within(drawer).getByLabelText("Stops at most")).toHaveValue("3");
    expect(within(drawer).getByLabelText("Minutes per stop")).toHaveValue("10");
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(`/plan/trips/${replannedTripId}`),
    );
    expect(body).toMatchObject({
      origin_place_id: placeId,
      value_of_time_per_hour: "20.00",
      max_stops: 3,
      stop_overhead_min: 10,
    });
    expect(body).not.toHaveProperty("destination_place_id");
  });

  it("confirms Abandon inline with focus on Cancel", async () => {
    let current = trip();
    const calls = mount(
      {
        [`POST /plan/trips/${tripId}/abandon`]: () => {
          current = trip({ status: "abandoned", finished_at: "2026-10-16T10:00:00Z" });
          return jsonResponse(200, current);
        },
      },
      () => current,
    );
    const user = userEvent.setup();
    await cards();
    await user.click(screen.getByRole("button", { name: "Abandon" }));
    const confirm = screen.getByTestId("abandon-confirm");
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("abandon-confirm")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Abandon" }));
    await user.click(
      within(screen.getByTestId("abandon-confirm")).getByRole("button", { name: "Abandon trip" }),
    );
    expect(await screen.findByTestId("notice")).toHaveTextContent("Abandoned the trip.");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Abandoned");
    expect(screen.queryByRole("button", { name: "Choose" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abandon" })).not.toBeInTheDocument();
  });
});

describe("the trip page's states", () => {
  it("says a trip is gone, and offers Try again on any other error", async () => {
    mount({
      [`GET /plan/trips/${tripId}`]: () => errorResponse(404, "not_found", "No such trip plan."),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("There is no such trip.");
  });

  it("offers Try again when the trip could not load", async () => {
    mount({
      [`GET /plan/trips/${tripId}`]: () => errorResponse(500, "internal", "Something broke."),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the trip");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("the trip page on a phone (UI-8.19)", () => {
  it("stacks Balanced first, folds the stops, and keeps Abandon under More actions", async () => {
    phoneWidth();
    mount();
    const user = userEvent.setup();
    const balanced = await card("balanced");
    expect(balanced.className).toMatch(/max-lg:order-first/);
    expect(within(balanced).getByRole("button", { name: "Choose" }).className).toMatch(/min-h-11/);
    // Stops fold behind their headings; the first is open.
    const stops = screen.getAllByTestId("stop");
    expect(stops[0].querySelector("details")).toHaveAttribute("open");
    expect(stops[1].querySelector("details")).not.toHaveAttribute("open");
    expect(
      within(stops[1]).getByText(/Stop 2 · Pier Farmers Market · \$10\.50/),
    ).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Abandon" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      within(await screen.findByRole("dialog", { name: "More actions" })).getByRole("button", {
        name: "Abandon",
      }),
    );
    await waitFor(() =>
      expect(
        within(screen.getByTestId("abandon-confirm")).getByRole("button", { name: "Cancel" }),
      ).toHaveFocus(),
    );
    // The map comes after the stops.
    const map = await screen.findByTestId("trip-map");
    expect(stops[1].compareDocumentPosition(map) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("colour on the trip page (UI-8.20)", () => {
  // Olive for the cheapest word, squash for the warn words and the estimate hint,
  // tomato for the destructive Abandon (08; UI-8.20).
  const meanings: ColourMeanings = {
    olive: (_, text) => text === "cheapest",
    squash: (_, text) =>
      ["stale", "estimated", "unsourceable"].includes(text) ||
      text.startsWith("Drive times are estimates"),
    tomato: (el, text) => el.tagName === "BUTTON" && text === "Abandon",
  };

  it("walks every element of a planned trip with every kind of note, and finds colour only where it means something", async () => {
    const notes: Annotation[] = [
      {
        kind: "unsourceable",
        item_id: "li-saffron",
        text: "No store has a price for Moonglow saffron.",
      },
      {
        kind: "forced_stop",
        location_id: marketLocation.id,
        text: "Only Pier Farmers Market sells Larkspur rolled oats.",
      },
      {
        kind: "stale_dependent",
        amount: "6.40",
        text: "$6.40 of the item cost rests on stale prices.",
      },
    ];
    mount({}, () => trip({ variants: [{ ...balanced, annotations: notes }, cheapest, fastest] }));
    await cards();
    await screen.findAllByTestId("annotation");
    expect(colourOutsideItsMeaning(screen.getByRole("main"), meanings)).toEqual([]);
  });

  it("walks every element of a chosen trip the same way", async () => {
    mount({}, () => chosenTrip());
    await screen.findByText(/^Chosen /);
    expect(colourOutsideItsMeaning(screen.getByRole("main"), meanings)).toEqual([]);
  });
});
