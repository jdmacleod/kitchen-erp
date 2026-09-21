import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { VendorLocation } from "../api/geo";
import { chainLocation, homeBase, marketDetail, marketLocation, marketLocationId, stallLocation, stallLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { instances } from "./maplibre-stub";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

function baseRoutes(locations: () => VendorLocation[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "HEAD /tiles/basemap.pmtiles": () => jsonResponse(404),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    "GET /vendor-locations": () => jsonResponse(200, { items: locations() }),
  };
}

async function mapReady() {
  await waitFor(() => expect(instances.length).toBeGreaterThan(0));
  return instances[instances.length - 1];
}

describe("map", () => {
  it("says tiles are missing when HEAD returns 404 and still places the pins", async () => {
    mockApi(baseRoutes(() => [chainLocation, marketLocation, stallLocation]));
    renderApp("/map");

    expect(await screen.findByText(/Map tiles are missing; see docs\/tiles\.md/)).toBeInTheDocument();
    const map = await mapReady();
    await waitFor(() => expect(within(map.container).getAllByRole("button", { name: /\(Chain\)|\(Market\)|\(home base\)/ })).toHaveLength(3));
    // Stalls share their market's pin.
    expect(within(map.container).queryByRole("button", { name: /Sandy/ })).not.toBeInTheDocument();
    const chain = within(map.container).getByRole("button", { name: "Millstone Harbour (Chain)" });
    expect(chain).toHaveClass("kerp-pin--chain");
    expect(chain.dataset.lat).toBe("33.45");
    expect(chain.dataset.lng).toBe("-120.55");
    expect(within(map.container).getByRole("button", { name: "Pier Farmers Market (Market)" })).toHaveClass("kerp-pin--market");
    expect(within(map.container).getByRole("button", { name: "Harbour flat (home base)" })).toHaveClass("kerp-pin--home");
    expect(screen.getByTestId("map-attribution")).toHaveTextContent("© OpenStreetMap contributors © Protomaps");
  });

  it("sends open_at only when the filter is on, at the chosen instant", async () => {
    const calls = mockApi(baseRoutes(() => [chainLocation]));
    const user = userEvent.setup();
    renderApp("/map");
    await screen.findByText("Millstone Harbour");
    expect(calls.filter((c) => c.path.startsWith("/vendor-locations")).every((c) => !c.query.has("open_at"))).toBe(true);

    fireEvent.change(screen.getByLabelText("Open at"), { target: { value: "2026-07-11T09:00" } });
    await user.click(screen.getByLabelText("Only open at"));

    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/vendor-locations") && c.query.has("open_at"))).toBe(true));
    const sent = calls.filter((c) => c.path.startsWith("/vendor-locations") && c.query.has("open_at")).at(-1);
    expect(sent?.query.get("open_at")).toBe(new Date("2026-07-11T09:00").toISOString());
  });

  it("lists a market's stalls when its pin is selected and shows a stall's inherited hours", async () => {
    mockApi({
      ...baseRoutes(() => [marketLocation, stallLocation]),
      [`GET /vendor-locations/${marketLocationId}`]: () => jsonResponse(200, marketDetail),
      [`GET /vendor-locations/${marketLocationId}/is-open`]: () => jsonResponse(200, { id: marketLocationId, at: "x", is_open: false, effective_opening_hours: "Sa 08:00-13:00" }),
      [`GET /vendor-locations/${stallLocationId}/is-open`]: () => jsonResponse(200, { id: stallLocationId, at: "x", is_open: false, effective_opening_hours: "Sa 08:00-13:00" }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();
    await user.click(await within(map.container).findByRole("button", { name: "Pier Farmers Market (Market)" }));

    const panel = await screen.findByTestId("location-panel");
    expect(within(panel).getByRole("heading", { name: "Pier Farmers Market" })).toBeInTheDocument();
    expect(within(panel).getByText("Saturday 08:00–13:00")).toBeInTheDocument();
    const stalls = within(panel).getByRole("list", { name: "Stalls" });
    await user.click(within(stalls).getByRole("button", { name: /Sandy's Stone Fruit/ }));

    const stall = await screen.findByTestId("stall-panel");
    expect(within(stall).getByText("inherited from the market")).toBeInTheDocument();
    expect(within(stall).getByText("Saturday 08:00–13:00")).toBeInTheDocument();
    await user.click(within(stall).getByRole("button", { name: /Back to Pier Farmers Market/ }));
    expect(await screen.findByTestId("location-panel")).toBeInTheDocument();
  });

  it("adds a location where the map was clicked, with an inline vendor and a name", async () => {
    let locations: VendorLocation[] = [];
    const calls = mockApi({
      ...baseRoutes(() => locations),
      "GET /vendors": () => jsonResponse(200, { items: [] }),
      "POST /vendor-locations": (call) => {
        const body = call.body as { name: string; lat: string; lon: string };
        const created: VendorLocation = { ...chainLocation, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009", name: body.name, lat: body.lat, lon: body.lon, vendor: { ...chainLocation.vendor, name: "Coast stand", kind: "stand" } };
        locations = [created];
        return jsonResponse(201, { ...created, stalls: [] });
      },
      "GET /vendor-locations/0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009": () => jsonResponse(200, { ...locations[0], stalls: [] }),
      "GET /vendor-locations/0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009/is-open": () => jsonResponse(200, { id: "x", at: "x", is_open: null, effective_opening_hours: null }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();

    await user.click(screen.getByRole("button", { name: "Add location here" }));
    expect(screen.getByText(/Click or tap the map/)).toBeInTheDocument();
    act(() => map.fire("click", { lngLat: { lat: 33.5, lng: -120.5 } }));
    expect(await screen.findByTestId("draft-point")).toHaveTextContent("33.500000, -120.500000");

    const form = screen.getByRole("form", { name: "Add location here" });
    await user.type(within(form).getByRole("combobox", { name: "Vendor" }), "Coast stand");
    await user.click(await within(form).findByRole("option", { name: /Create vendor/ }));
    expect(within(form).getByLabelText("Vendor kind")).toHaveValue("stand");
    await user.type(within(form).getByLabelText("Name"), "Coast road stand");
    await user.click(within(form).getByRole("button", { name: "Create location" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/vendor-locations");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ name: "Coast road stand", lat: "33.500000", lon: "-120.500000", vendor: { name: "Coast stand", kind: "stand" } });
    expect(post?.headers.get("Idempotency-Key")).toBeTruthy();
    // The new pin is selected and its detail shows.
    expect(await screen.findByTestId("location-panel")).toHaveTextContent("Coast road stand");
    await waitFor(() => expect(within(map.container).getByRole("button", { name: "Coast road stand (Stand)" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("adds a home base where the map was clicked", async () => {
    const calls = mockApi({
      ...baseRoutes(() => []),
      "POST /home-bases": (call) => jsonResponse(201, { ...homeBase, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e09", ...(call.body as object) }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();
    await user.click(screen.getByRole("button", { name: "Add home base here" }));
    act(() => map.fire("click", { lngLat: { lat: 33.25, lng: -120.75 } }));
    const form = screen.getByRole("form", { name: "Add home base here" });
    await user.type(within(form).getByLabelText("Name"), "The cabin");
    await user.click(within(form).getByRole("button", { name: "Create home base" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/home-bases")?.body).toEqual({ name: "The cabin", lat: "33.250000", lon: "-120.750000" }));
  });
});
