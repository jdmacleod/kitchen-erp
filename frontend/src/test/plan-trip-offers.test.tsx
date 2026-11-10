// Weekly flyers 2X (spec 18, X1, X5): "With this week's offers" in the Plan a trip
// drawer, and each stop's expected saving on the trip page. Every store and offer here
// is invented.
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Trip, TripCreateInput } from "../api/trips";
import { NoticeProvider } from "../components/Notice";
import { createQueryClient } from "../lib/queryClient";
import { TripDetailPage } from "../pages/plan/TripDetailPage";
import { TripsPage } from "../pages/plan/TripsPage";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, type RouteHandler } from "./helpers";
import { kitchen, planHealth, settings } from "./plan-fixtures";
import { trip, tripId } from "./trip-fixtures";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const anOffer = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c51" };

function mount(path: string, routes: Record<string, RouteHandler>) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /plan/trips": () => jsonResponse(200, { items: [] }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /plan/defaults": () =>
      jsonResponse(200, {
        value_of_time_per_hour: "20.00",
        max_stops_default: 3,
        candidate_radius_km: "25",
      }),
    "HEAD /tiles/basemap.pmtiles": () => jsonResponse(404),
    ...routes,
  });
  render(
    <QueryClientProvider client={createQueryClient({ retry: false })}>
      <MemoryRouter initialEntries={[path]}>
        <main>
          <NoticeProvider>
            <Routes>
              <Route path="/plan/trips" element={<TripsPage />} />
              <Route path="/plan/trips/:id" element={<TripDetailPage />} />
            </Routes>
          </NoticeProvider>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

describe("planning with this week's offers (X1)", () => {
  it("offers the switch while the kitchen has offers, and sends it when on", async () => {
    let body: TripCreateInput | null = null;
    mount("/plan/trips?new=1", {
      "GET /flyer-offers/current": () => jsonResponse(200, { items: [anOffer] }),
      "POST /plan/trips": (call) => {
        body = call.body as TripCreateInput;
        return jsonResponse(201, trip());
      },
    });
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    const toggle = await within(drawer).findByRole("switch", { name: "With this week's offers" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(body).toMatchObject({ with_offers: true }));
  });

  it("has no switch when the kitchen has no offers (X5)", async () => {
    let body: TripCreateInput | null = null;
    mount("/plan/trips?new=1", {
      "GET /flyer-offers/current": () => jsonResponse(200, { items: [] }),
      "POST /plan/trips": (call) => {
        body = call.body as TripCreateInput;
        return jsonResponse(201, trip());
      },
    });
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Plan a trip" });
    await waitFor(() =>
      expect(within(drawer).getByLabelText("Value of your time")).toHaveValue("20.00"),
    );
    expect(within(drawer).queryByRole("switch")).not.toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).not.toHaveProperty("with_offers");
  });

  it("shows each stop's expected saving and marks the items an offer priced", async () => {
    const base = trip();
    const withOffers: Trip = {
      ...base,
      with_offers: true,
      variants: base.variants.map((v) => ({
        ...v,
        stops: v.stops.map((s, i) =>
          i === 0
            ? {
                ...s,
                expected_saving: "2.0000",
                items: s.items.map((it, j) =>
                  j === 0 ? { ...it, flyer_offer_id: anOffer.id } : it,
                ),
              }
            : s,
        ),
      })),
    };
    mount(`/plan/trips/${tripId}`, {
      [`GET /plan/trips/${tripId}`]: () => jsonResponse(200, withOffers),
    });
    const saving = (await screen.findAllByTestId("stop-saving"))[0];
    expect(saving).toHaveTextContent(
      "This week's offers save about $2.00 against the usual prices here",
    );
    expect(saving).toHaveClass("text-neutral-600");
    expect(screen.getAllByText("this week's offer").length).toBeGreaterThan(0);
  });

  it("shows no saving line on a plan made without offers (X5)", async () => {
    mount(`/plan/trips/${tripId}`, {
      [`GET /plan/trips/${tripId}`]: () => jsonResponse(200, trip()),
    });
    await screen.findAllByTestId("stop-subtotal");
    expect(screen.queryByTestId("stop-saving")).not.toBeInTheDocument();
    expect(screen.queryByText("this week's offer")).not.toBeInTheDocument();
  });
});
