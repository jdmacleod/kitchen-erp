import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { VendorLocation } from "../api/geo";
import { flour, flourProductId } from "./catalog-fixtures";
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
      [`GET /vendor-locations/${marketLocationId}/price-panel`]: () => jsonResponse(200, { last_visit: null, spend: "0", visits: 0, period_days: 30, recent: [] }),
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

  it("places the first pin from typed coordinates when there is nothing to aim at", async () => {
    // On a fresh deployment the map has no tiles and no pins, so clicking was
    // aiming at a featureless grey field (issue #18). Those coordinates then
    // drive nearest-store defaulting and the distance labels for the life of
    // the deployment.
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
    expect(screen.getByTestId("draft-point")).toHaveTextContent("No pin yet");
    await user.type(screen.getByLabelText("Coordinates"), "33.512345, -120.487654");

    // The pin exists without a click, and the view is brought to it: a point
    // chosen away from the map would otherwise land outside the viewport.
    await waitFor(() => expect(screen.getByTestId("draft-point")).toHaveTextContent("33.512345, -120.487654"));
    const jump = map.jumps.at(-1);
    expect(jump?.center).toEqual([-120.487654, 33.512345]);
    expect(jump?.zoom).toBe(13);

    const form = screen.getByRole("form", { name: "Add location here" });
    await user.type(within(form).getByRole("combobox", { name: "Vendor" }), "Coast stand");
    await user.click(await within(form).findByRole("option", { name: /Create vendor/ }));
    await user.type(within(form).getByLabelText("Name"), "Coast road stand");
    await user.click(within(form).getByRole("button", { name: "Create location" }));

    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.path === "/vendor-locations");
      // The digits reach the API as typed, not rounded through the map.
      expect(post?.body).toEqual({ name: "Coast road stand", lat: "33.512345", lon: "-120.487654", vendor: { name: "Coast stand", kind: "stand" } });
    });
  });

  // Regression: NEW-001 — ?location= opened a location's panel but left the map
  // wherever it was, so "show me this shop" answered with a panel about one place
  // and a map showing another.
  // Found by /qa on 2026-09-24
  // Report: .gstack/qa-reports/qa-report-localhost-8080-2026-09-24.md
  it("brings the map to a location opened by link", async () => {
    mockApi(baseRoutes(() => [chainLocation, marketLocation]));
    renderApp(`/map?location=${chainLocation.id}`);
    const map = await mapReady();

    await waitFor(() => {
      const jump = map.jumps.at(-1);
      expect(jump?.center).toEqual([Number(chainLocation.lon), Number(chainLocation.lat)]);
    });
    // Zoomed in far enough that a pin is findable, not left at the fit-everything zoom.
    expect(map.jumps.at(-1)?.zoom).toBeGreaterThanOrEqual(13);
  });

  it("brings the map to a location chosen from the list beside it", async () => {
    // The other way of selecting that is not a click on the pin itself.
    mockApi(baseRoutes(() => [chainLocation, marketLocation]));
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();

    const before = map.jumps.length;
    // Scoped to the list: the pin on the map carries the same name.
    const list = await screen.findByRole("list", { name: "Locations on the map" });
    await user.click(within(list).getByRole("button", { name: new RegExp(marketLocation.name) }));
    await waitFor(() => {
      expect(map.jumps.length).toBeGreaterThan(before);
      expect(map.jumps.at(-1)?.center).toEqual([Number(marketLocation.lon), Number(marketLocation.lat)]);
    });
  });

  // Greptile review on PR #29: editing a good pair into a bad one, or clearing the
  // field, left the old draft point in place and submittable.
  it("drops the pin when the coordinates stop reading as a pair", async () => {
    mockApi({ ...baseRoutes(() => []), "GET /vendors": () => jsonResponse(200, { items: [] }) });
    const user = userEvent.setup();
    renderApp("/map?place=location");
    await mapReady();

    // ?place=location already opened the draft form; clicking the button would
    // toggle placing back off.
    const field = await screen.findByLabelText("Coordinates");
    await user.type(field, "33.512345, -120.487654");
    await waitFor(() => expect(screen.getByTestId("draft-point")).toHaveTextContent("33.512345, -120.487654"));

    // Edited into something unreadable: the pin goes with it rather than
    // lingering as a point nobody chose.
    await user.type(field, "x");
    await waitFor(() => expect(screen.getByTestId("draft-point")).toHaveTextContent("No pin yet"));
    // And the half-typed text is not wiped by the pin disappearing.
    expect(field).toHaveValue("33.512345, -120.487654x");

    await user.clear(field);
    expect(screen.getByTestId("draft-point")).toHaveTextContent("No pin yet");
  });

  // Greptile review on PR #29: the deep link was applied once by a ref, so it
  // also never selected the panel and could not re-apply. It is now derived from
  // the parameter, which is what makes both work.
  //
  // The cross-navigation half of that review point (back/forward between two map
  // links while the page stays mounted) is not reachable here: renderApp mounts a
  // MemoryRouter, which does not read window.history. What is checked is that the
  // link drives the panel as well as the map, and that deriving it converges
  // instead of jumping on every render.
  it("selects the panel for a linked location, and settles", async () => {
    mockApi({
      ...baseRoutes(() => [chainLocation, marketLocation]),
      [`GET /vendor-locations/${marketLocationId}`]: () => jsonResponse(200, marketDetail),
      [`GET /vendor-locations/${marketLocationId}/is-open`]: () => jsonResponse(200, { id: marketLocationId, at: "x", is_open: null, effective_opening_hours: null }),
      [`GET /vendor-locations/${marketLocationId}/price-panel`]: () => jsonResponse(200, { last_visit: null, spend: "0", visits: 0, period_days: 30, recent: [] }),
    });
    renderApp(`/map?location=${marketLocation.id}`);
    const map = await mapReady();

    await waitFor(() => expect(map.jumps.at(-1)?.center).toEqual([Number(marketLocation.lon), Number(marketLocation.lat)]));
    expect(await screen.findByTestId("location-panel")).toHaveTextContent(marketLocation.name);

    const settled = map.jumps.length;
    await new Promise((r) => setTimeout(r, 120));
    expect(map.jumps.length).toBe(settled);
  });

  it("mirrors a map click back into the coordinates field", async () => {
    mockApi({ ...baseRoutes(() => []), "GET /vendors": () => jsonResponse(200, { items: [] }) });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();

    await user.click(screen.getByRole("button", { name: "Add location here" }));
    await user.type(screen.getByLabelText("Coordinates"), "33.1, -120.1");
    act(() => map.fire("click", { lngLat: { lat: 33.5, lng: -120.5 } }));
    // One pin, two ways of saying where it is; they do not disagree.
    await waitFor(() => expect(screen.getByLabelText("Coordinates")).toHaveValue("33.500000, -120.500000"));
    expect(screen.getByTestId("draft-point")).toHaveTextContent("33.500000, -120.500000");
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

describe("map price book (2E)", () => {
  it("labels pins with the best price per unit for a chosen ingredient and marks stale ones", async () => {
    const calls = mockApi({
      ...baseRoutes(() => [chainLocation, marketLocation]),
      "GET /ingredients": (call) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? [flour] : [], next_cursor: null }),
      "GET /price-book/cheapest": () =>
        jsonResponse(200, {
          unit: "g",
          items: [
            { location_id: chainLocation.id, location_name: chainLocation.name, lat: chainLocation.lat, lon: chainLocation.lon, vendor_id: chainLocation.vendor.id, vendor_name: chainLocation.vendor.name, kind: "chain", product_id: flourProductId, product_name: "All-Purpose Flour", quality_rating: 4, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a03", observed_at: "2026-09-18T15:00:00Z", is_promo: false, norm_unit_price: "0.002200", norm_unit: "g", stale: false },
            { location_id: marketLocationId, location_name: marketLocation.name, lat: marketLocation.lat, lon: marketLocation.lon, vendor_id: marketLocation.vendor.id, vendor_name: marketLocation.vendor.name, kind: "market", product_id: flourProductId, product_name: "All-Purpose Flour", quality_rating: 4, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a04", observed_at: "2026-05-20T15:00:00Z", is_promo: false, norm_unit_price: "0.002425", norm_unit: "g", stale: true },
          ],
        }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();
    await within(map.container).findByRole("button", { name: "Millstone Harbour (Chain)" });

    const form = screen.getByRole("form", { name: "Where is this cheapest" });
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));

    const chain = await within(map.container).findByRole("button", { name: /^Millstone Harbour \(Chain\), \$0\.0022\/g · \d+ days$/ });
    expect(chain).toHaveClass("kerp-pin--priced");
    expect(chain).not.toHaveClass("kerp-pin--stale");
    expect(chain.querySelector(".kerp-pin__label")).toHaveTextContent("$0.0022/g");
    const market = within(map.container).getByRole("button", { name: /^Pier Farmers Market \(Market\), \$0\.002425\/g · \d+ days, stale$/ });
    expect(market).toHaveClass("kerp-pin--stale");
    expect(market.querySelector(".kerp-pin__label")).toHaveTextContent(/^\$0\.002425\/g · \d+ days \(stale\)$/);
    expect(screen.getByTestId("cheapest-summary")).toHaveTextContent("best price per g for all-purpose flour at 2 locations");
    expect(calls.find((c) => c.path.startsWith("/price-book/cheapest"))?.query.get("ingredient_id")).toBe(flour.id);

    await user.click(within(form).getByRole("checkbox", { name: "Exclude stale" }));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith("/price-book/cheapest")).at(-1)?.query.get("exclude_stale")).toBe("true"));
  });

  it("shows last visit, spend over a chosen period, and recent prices in the location panel", async () => {
    const calls = mockApi({
      ...baseRoutes(() => [chainLocation]),
      [`GET /vendor-locations/${chainLocation.id}`]: () => jsonResponse(200, { ...chainLocation, stalls: [] }),
      [`GET /vendor-locations/${chainLocation.id}/is-open`]: () => jsonResponse(200, { id: chainLocation.id, at: "x", is_open: true, effective_opening_hours: "Mo-Su 07:00-22:00" }),
      [`GET /vendor-locations/${chainLocation.id}/price-panel`]: (call) =>
        jsonResponse(200, {
          last_visit: "2026-09-18T15:00:00Z",
          spend: call.query.get("days") === "90" ? "123.45" : "41.20",
          visits: call.query.get("days") === "90" ? 6 : 2,
          period_days: Number(call.query.get("days")),
          recent: [{ observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a03", observed_at: "2026-09-18T15:00:00Z", price: "4.99", qty: "1", unit: "each", is_promo: true, norm_unit_price: "0.002200", norm_unit: "g", norm_status: "ok", product_id: flourProductId, product_name: "All-Purpose Flour", brand: "Millstone" }],
        }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    const map = await mapReady();
    await user.click(await within(map.container).findByRole("button", { name: "Millstone Harbour (Chain)" }));

    const prices = await screen.findByTestId("location-prices");
    await waitFor(() => expect(prices).toHaveTextContent("$41.20 over 2 visits in 30 days"));
    expect(prices).toHaveTextContent("Last visit");
    const recent = within(prices).getByRole("list", { name: "Recent prices" });
    expect(within(recent).getByRole("link", { name: "Millstone All-Purpose Flour" })).toHaveAttribute("href", `/products/${flourProductId}`);
    expect(recent).toHaveTextContent("$4.99 / 1 each");
    expect(recent).toHaveTextContent("sale");
    expect(recent).toHaveTextContent("$0.0022/g");

    await user.selectOptions(within(prices).getByLabelText("Spend period"), "90");
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith(`/vendor-locations/${chainLocation.id}/price-panel`)).at(-1)?.query.get("days")).toBe("90"));
    await waitFor(() => expect(screen.getByTestId("location-prices")).toHaveTextContent("$123.45 over 6 visits in 90 days"));
  });
  it("arrives ready to place when sent from a blocked entry form", async () => {
    // MapView ignores map clicks unless it is placing, so a link to a bare /map
    // would land someone on a screen that swallows their first click. The guard
    // on the entry forms deep-links here instead.
    mockApi(baseRoutes(() => []));
    renderApp("/map?place=location");

    expect(await screen.findByRole("button", { name: "Add location here" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens in browse mode without the parameter", async () => {
    mockApi(baseRoutes(() => []));
    renderApp("/map");

    expect(await screen.findByRole("button", { name: "Add location here" })).toHaveAttribute("aria-pressed", "false");
  });

  it("tells a first-time visitor what the pin does, not what is missing", async () => {
    mockApi(baseRoutes(() => []));
    renderApp("/map");

    expect(await screen.findByText(/Use .Add location here., then click the map/)).toBeInTheDocument();
    expect(screen.getByText(/Naming the pin creates the vendor and the location together/)).toBeInTheDocument();
  });

  it("still says tiles are missing when there are no locations either", async () => {
    // CI caught this: folding the tiles sentence into the first-run notice made
    // it vanish on an empty database, and the e2e spec asserting it passed or
    // failed depending on whether another spec had created a location first.
    // Whether tiles are installed has nothing to do with whether you have shops.
    mockApi(baseRoutes(() => []));
    renderApp("/map");

    expect(await screen.findByText(/No locations yet/)).toBeInTheDocument();
    expect(screen.getByText(/Map tiles are missing; see docs\/tiles\.md/)).toBeInTheDocument();
  });

  it("does not call a filtered-out list an empty household", async () => {
    // The map's location query is filtered. An empty result under a filter means
    // "nothing matches", and telling an established household "No locations yet"
    // would invite them to re-create a shop they already have.
    mockApi({
      ...baseRoutes(() => [chainLocation]),
      "GET /vendor-locations": (call) =>
        jsonResponse(200, { items: call.query.get("kind") ? [] : [chainLocation] }),
    });
    const user = userEvent.setup();
    renderApp("/map");
    await mapReady();

    await user.selectOptions(screen.getByLabelText("Kind"), "market");

    await waitFor(() => expect(screen.queryByText(/No locations yet/)).not.toBeInTheDocument());
  });

  it("does not name a button the visitor never had to press", async () => {
    // Caught by looking at the rendered page rather than by a test: arriving
    // deep-linked, placing mode is already on and the status line already says
    // where to click, so "use Add location here" describes a step that did not
    // happen.
    mockApi(baseRoutes(() => []));
    renderApp("/map?place=location");

    expect(await screen.findByText(/Naming the pin creates the vendor and the location together/)).toBeInTheDocument();
    expect(screen.queryByText(/Use .Add location here., then click the map/)).not.toBeInTheDocument();
  });
});
