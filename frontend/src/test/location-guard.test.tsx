import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { chainLocation } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

/**
 * The guard exists because issue #15 reported a dead end: with no locations the
 * purchase form renders, the picker holds only "Choose a location", and nothing
 * says a place has to exist first.
 *
 * The narrower point these tests protect is *when* it intervenes. An empty array
 * is what the query hands back while it is still asking and when it could not
 * ask at all, so blocking on the array alone would hide the form on every visit
 * and would tell people their data is missing during an outage.
 */
function mount(path: string, locations: () => Response) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /vendor-locations": locations,
    "GET /units": () => jsonResponse(200, { items: [] }),
  });
  renderApp(path);
}

describe("LocationGuard", () => {
  it("blocks the purchase form and points at the map when there are none", async () => {
    mount("/purchases/new", () => jsonResponse(200, { items: [] }));

    expect(await screen.findByText("No locations yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save purchase" })).not.toBeInTheDocument();

    // Deep-linked into placing mode: MapView swallows clicks in browse mode, so a
    // bare /map would send someone to a screen that ignores their first click.
    expect(screen.getByRole("link", { name: "Open the map" })).toHaveAttribute(
      "href",
      "/catalog/vendors?view=map&place=location",
    );
  });

  it("blocks the shelf price form the same way", async () => {
    mount("/prices/new", () => jsonResponse(200, { items: [] }));

    expect(await screen.findByText("No locations yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save price" })).not.toBeInTheDocument();
  });

  it("renders the form untouched once a location exists", async () => {
    mount("/purchases/new", () => jsonResponse(200, { items: [chainLocation] }));

    expect(await screen.findByRole("button", { name: "Save purchase" })).toBeInTheDocument();
    expect(screen.queryByText("No locations yet")).not.toBeInTheDocument();
  });

  it("keeps the form on screen while the list is still loading", async () => {
    // The weekly hot path. Hiding the form until this query resolves would make
    // every visit wait on it, so the form must be there before the answer is.
    mount("/purchases/new", () => new Promise<Response>(() => {}) as unknown as Response);

    expect(await screen.findByRole("button", { name: "Save purchase" })).toBeInTheDocument();
    expect(screen.queryByText("No locations yet")).not.toBeInTheDocument();
  });

  it("does not claim the locations are missing when the lookup fails", async () => {
    // An outage must never be reported as "you have none" with an invitation to
    // go and re-create a location the household already has.
    mount("/purchases/new", () => errorResponse(500, "unavailable", "down"));

    expect(await screen.findByRole("button", { name: "Save purchase" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("Locations could not be loaded.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("No locations yet")).not.toBeInTheDocument();
  });
});
