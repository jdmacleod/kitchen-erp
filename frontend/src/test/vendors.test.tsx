import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Vendor, VendorLocation } from "../api/geo";
import { chainLocation, chainVendor, chainVendorId, homeBase, homeBaseId, marketLocation, marketVendor, stallLocation } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function baseRoutes(vendors: () => Vendor[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    // The list carries each card's counts (T16).
    "GET /vendors": () => jsonResponse(200, { items: vendors().map((v) => ({ location_count: 1, last_visit: null, ...v })) }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
  };
}

describe("vendors", () => {
  it("creates a vendor with a kind and price scope", async () => {
    let vendors: Vendor[] = [];
    const created: Vendor = { ...chainVendor, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5f09", name: "Riverbend Grocers" };
    const calls = mockApi({
      ...baseRoutes(() => vendors),
      "POST /vendors": () => {
        vendors = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");

    await screen.findByRole("heading", { name: "Vendors" });
    await user.click(screen.getAllByRole("button", { name: "Add vendor" })[0]);
    await screen.findByRole("dialog", { name: "Add vendor" });
    await user.type(screen.getByLabelText("Name"), "Riverbend Grocers");
    await user.click(screen.getByRole("radio", { name: "Chain" }));
    await user.click(screen.getByRole("radio", { name: "Same price at every location" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add vendor" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/vendors");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ name: "Riverbend Grocers", kind: "chain", price_scope: "chain" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    const list = await screen.findByRole("list", { name: "Vendors" });
    const card = within(list).getByRole("link", { name: "Riverbend Grocers" });
    expect(card).toHaveAttribute("href", `/catalog/vendors/${created.id}`);
    // The new card takes focus (G10), and the Notice still says what it needs next.
    await waitFor(() => expect(card).toHaveFocus());
    expect(screen.getByTestId("notice")).toHaveTextContent("until it has a location");
  });

  it("shows a quiet note when OpenStreetMap adoption is switched off", async () => {
    mockApi({
      ...baseRoutes(() => [chainVendor]),
      "GET /osm/candidates": () => errorResponse(409, "integration_disabled", "Overpass is disabled."),
    });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    await screen.findByRole("list", { name: "Vendors" });

    await user.click(screen.getByRole("button", { name: "Find nearby" }));
    const dialog = await screen.findByRole("dialog", { name: "Find nearby" });
    // The only home base is chosen already (UI-3.8).
    await waitFor(() => expect(within(dialog).getByLabelText("Home base")).toHaveValue(homeBaseId));
    await user.click(within(dialog).getByRole("button", { name: "Find candidates" }));

    expect(await screen.findByText("OpenStreetMap adoption is off; set ENABLE_OVERPASS=true.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists candidates and adopts one with a kind override", async () => {
    const calls = mockApi({
      ...baseRoutes(() => [chainVendor]),
      "GET /osm/candidates": () =>
        jsonResponse(200, {
          items: [
            { osm_type: "node", osm_id: 4242, name: "Pier Bakery", kind_guess: "independent", lat: "33.41", lon: "-120.61", address: "Pier 3", opening_hours: "Mo-Sa 07:00-15:00", already_adopted: false },
          ],
        }),
      "POST /osm/adopt": () => jsonResponse(201, { ...chainLocation, stalls: [] }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    await screen.findByRole("list", { name: "Vendors" });
    await user.click(screen.getByRole("button", { name: "Find nearby" }));
    const dialog = await screen.findByRole("dialog", { name: "Find nearby" });
    await waitFor(() => expect(within(dialog).getByLabelText("Home base")).toHaveValue(homeBaseId));
    await user.click(within(dialog).getByRole("button", { name: "Find candidates" }));

    const row = await screen.findByText("Pier Bakery");
    expect(row).toBeInTheDocument();
    expect(screen.getByText(/Monday–Saturday 07:00–15:00/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Kind", { selector: "#osm-kind-node-4242" }), "chain");
    await user.click(screen.getByRole("button", { name: "Adopt Pier Bakery" }));

    await waitFor(() => expect(screen.getByText("adopted")).toBeInTheDocument());
    const post = calls.find((c) => c.method === "POST" && c.path === "/osm/adopt");
    expect(post?.body).toEqual({ osm_type: "node", osm_id: 4242, home_base_id: homeBaseId, radius_m: 2000, vendor_kind: "chain" });
  });

  it("shows a vendor's locations with stalls nested and edits the vendor", async () => {
    let vendor = marketVendor;
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, vendor),
      [`PATCH /vendors/${marketVendor.id}`]: (call) => {
        vendor = { ...vendor, ...(call.body as object) };
        return jsonResponse(200, vendor);
      },
      "GET /vendor-locations": () => jsonResponse(200, { items: [marketLocation, { ...stallLocation, vendor: marketLocation.vendor }] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    expect(await screen.findByRole("heading", { name: "Pier Farmers Market" })).toBeInTheDocument();
    const stalls = await screen.findByRole("list", { name: "Stalls at Pier Farmers Market" });
    expect(within(stalls).getByText("Sandy's Stone Fruit")).toBeInTheDocument();
    expect(within(stalls).getByText(/Saturday 08:00–13:00 \(inherited\)/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit vendor" }));
    await user.click(screen.getByRole("radio", { name: "Same price at every location" }));
    await user.click(screen.getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ price_scope: "chain" }));
    expect(await screen.findByText("Same price at every location")).toBeInTheDocument();
  });

  it("creates a location for a vendor without going to the map", async () => {
    // useCreateLocation used to have one caller, the map's pin-drop flow, so a
    // vendor added on the Vendors page could not be used for a purchase until
    // someone found it on a map with no tiles (issue #20).
    let locations: VendorLocation[] = [];
    const created: VendorLocation = { ...marketLocation, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009", name: "Quay stand", lat: "33.512345", lon: "-120.487654" };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: locations }),
      "POST /vendor-locations": () => {
        locations = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    expect(await screen.findByText(/No locations yet, so this vendor cannot be chosen for a purchase/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add a location" }));
    const form = screen.getByRole("form", { name: `Add a location for ${marketVendor.name}` });

    await user.type(within(form).getByLabelText("Name"), "Quay stand");
    await user.click(within(form).getByRole("button", { name: "Create location" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Coordinates must be a latitude and a longitude/);

    await user.type(within(form).getByLabelText("Coordinates"), "33.512345, -120.487654");
    await user.click(within(form).getByRole("button", { name: "Create location" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/vendor-locations");
      expect(found).toBeDefined();
      return found;
    });
    // The vendor is known from the page, and the coordinates travel as the
    // strings they were typed as.
    expect(post?.body).toEqual({ vendor_id: marketVendor.id, name: "Quay stand", lat: "33.512345", lon: "-120.487654" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    const list = await screen.findByRole("list", { name: "Locations" });
    expect(within(list).getByText("Quay stand")).toBeInTheDocument();
  });

  // Regression: NEW-004 — the shared coordinates control told you as you typed on
  // the map and stayed silent on the vendor page, so the same field gave two
  // different answers depending on where it was used.
  // Found by /qa on 2026-09-24
  // Report: .gstack/qa-reports/qa-report-localhost-8080-2026-09-24.md
  it("says so while you type something that is not a coordinate pair", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    await user.click(await screen.findByRole("button", { name: "Add a location" }));
    const field = screen.getByLabelText("Coordinates");
    expect(screen.queryByText("Not a latitude and longitude yet.")).not.toBeInTheDocument();

    await user.type(field, "somewhere near the pier");
    expect(await screen.findByText("Not a latitude and longitude yet.")).toBeInTheDocument();

    // And it goes away once the pair reads.
    await user.clear(field);
    await user.type(field, "33.512345, -120.487654");
    await waitFor(() => expect(screen.queryByText("Not a latitude and longitude yet.")).not.toBeInTheDocument());
  });

  it("fills the coordinates from this device's position", async () => {
    const getCurrentPosition = vi.fn((ok: PositionCallback) =>
      ok({ coords: { latitude: 33.4123, longitude: -120.5987 } } as GeolocationPosition),
    );
    vi.stubGlobal("navigator", Object.assign(Object.create(Object.getPrototypeOf(navigator)), navigator, { geolocation: { getCurrentPosition } }));
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    await user.click(await screen.findByRole("button", { name: "Add a location" }));
    await user.click(screen.getByRole("button", { name: "Use my location" }));
    // Standing in the shop is the other way the coordinates are known.
    await waitFor(() => expect(screen.getByLabelText("Coordinates")).toHaveValue("33.4123, -120.5987"));
    vi.unstubAllGlobals();
  });

  // Greptile review on PR #29: a position fix can take up to eight seconds, and
  // anything typed in the meantime was overwritten when it landed, so a submit
  // could save the device position instead of the point that was chosen.
  it("drops a position fix that arrives after the coordinates were changed", async () => {
    let deliver: (() => void) | null = null;
    const getCurrentPosition = vi.fn((ok: PositionCallback) => {
      deliver = () => ok({ coords: { latitude: 33.9999, longitude: -120.9999 } } as GeolocationPosition);
    });
    vi.stubGlobal("navigator", Object.assign(Object.create(Object.getPrototypeOf(navigator)), navigator, { geolocation: { getCurrentPosition } }));
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    await user.click(await screen.findByRole("button", { name: "Add a location" }));
    await user.click(screen.getByRole("button", { name: "Use my location" }));
    // Still in flight; the person types the coordinates they actually want.
    await user.type(screen.getByLabelText("Coordinates"), "33.512345, -120.487654");

    deliver!();

    // The stale fix is discarded rather than replacing the newer choice.
    await waitFor(() => expect(screen.getByLabelText("Coordinates")).toHaveValue("33.512345, -120.487654"));
    vi.unstubAllGlobals();
  });

  it("creates a home base from the settings page and reports the in-use message on delete", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "HEAD /tiles/basemap.pmtiles": () => jsonResponse(404),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`DELETE /home-bases/${homeBaseId}`]: () =>
        jsonResponse(409, { error: { code: "home_base_in_use", message: "in use", details: { locations: [chainVendorId, marketVendor.id] } } }),
    });
    const user = userEvent.setup();
    renderApp("/settings/kitchens");

    const list = await screen.findByRole("list", { name: "Home bases" });
    expect(within(list).getByText("Harbour flat")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create home base" })).toBeDisabled();

    await user.click(within(list).getByRole("button", { name: "Delete Harbour flat" }));
    await user.click(within(list).getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This home base is the default for 2 locations. Reassign them first.");
  });
});

describe("the Vendors page (UI-3.7, UI-3.8, G11)", () => {
  const listed = (vendors: object[]) => ({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /vendors": () => jsonResponse(200, { items: vendors }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
  });

  it("shows a card per vendor with its kind, locations, last visit and pricing", async () => {
    mockApi(listed([
      { ...chainVendor, location_count: 3, last_visit: "2026-09-20T17:00:00Z" },
      { ...marketVendor, location_count: 0, last_visit: null },
    ]));
    renderApp("/catalog/vendors");
    const cards = await screen.findAllByTestId("vendor-card");
    expect(cards[0]).toHaveTextContent("Chain");
    expect(cards[0]).toHaveTextContent("3 locations");
    expect(cards[0]).toHaveTextContent("Last visit Sep 20, 2026");
    expect(cards[0]).toHaveTextContent("Same price at every location");
    expect(cards[1]).toHaveTextContent("No locations yet");
    expect(cards[1]).toHaveTextContent("Not visited yet");
  });

  it("narrows by kind, and names the filter when nothing matches", async () => {
    mockApi(listed([{ ...chainVendor, location_count: 1, last_visit: null }]));
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    await screen.findAllByTestId("vendor-card");
    await user.click(within(screen.getByRole("group", { name: "Kind" })).getByRole("button", { name: "Stands" }));
    const empty = await screen.findByRole("region", { name: "No vendors match among stands" });
    await user.click(within(empty).getByRole("button", { name: "Clear filters" }));
    expect(await screen.findAllByTestId("vendor-card")).toHaveLength(1);
  });

  it("points an empty directory at the map, where a pin makes a vendor", async () => {
    mockApi(listed([]));
    renderApp("/catalog/vendors");
    const empty = await screen.findByRole("region", { name: "No vendors yet. Drop a pin on the map" });
    expect(within(empty).getByRole("link", { name: "Open the map" })).toHaveAttribute("href", "/catalog/vendors?view=map&place=location");
  });

  it("switches to the map view and back, keeping the view in the URL", async () => {
    mockApi({ ...listed([]), "GET /vendor-locations": () => jsonResponse(200, { items: [] }) });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    const view = await screen.findByRole("group", { name: "View" });
    await user.click(within(view).getByRole("button", { name: "Map" }));
    expect(await screen.findByRole("button", { name: "Add location here" })).toBeInTheDocument();
    expect(within(view).getByRole("button", { name: "Map" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(view).getByRole("button", { name: "List" }));
    expect(await screen.findByRole("region", { name: "No vendors yet. Drop a pin on the map" })).toBeInTheDocument();
  });

  it("asks for a kitchen first when there is none to search from (UI-3.8)", async () => {
    mockApi({ ...listed([]), "GET /home-bases": () => jsonResponse(200, { items: [] }) });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    await user.click(await screen.findByRole("button", { name: "Find nearby" }));
    const dialog = await screen.findByRole("dialog", { name: "Find nearby" });
    expect(await within(dialog).findByRole("link", { name: "Settings → Kitchens" })).toHaveAttribute("href", "/settings/kitchens");
  });
});
