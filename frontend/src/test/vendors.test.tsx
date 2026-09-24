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
    "GET /vendors": () => jsonResponse(200, { items: vendors() }),
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
    renderApp("/vendors");

    expect(await screen.findByText("No vendors yet")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "Riverbend Grocers");
    await user.click(screen.getByRole("radio", { name: "Chain" }));
    await user.click(screen.getByRole("radio", { name: "Chain-wide" }));
    await user.click(screen.getByRole("button", { name: "Create vendor" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/vendors");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ name: "Riverbend Grocers", kind: "chain", price_scope: "chain" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    const list = await screen.findByRole("list", { name: "Vendors" });
    expect(within(list).getByRole("link", { name: "Riverbend Grocers" })).toHaveAttribute("href", `/vendors/${created.id}`);
  });

  it("shows a quiet note when OpenStreetMap adoption is switched off", async () => {
    mockApi({
      ...baseRoutes(() => [chainVendor]),
      "GET /osm/candidates": () => errorResponse(409, "integration_disabled", "Overpass is disabled."),
    });
    const user = userEvent.setup();
    renderApp("/vendors");
    await screen.findByRole("list", { name: "Vendors" });

    await user.selectOptions(await screen.findByLabelText("Home base"), homeBaseId);
    await user.click(screen.getByRole("button", { name: "Find candidates" }));

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
    renderApp("/vendors");
    await screen.findByRole("list", { name: "Vendors" });
    await user.selectOptions(await screen.findByLabelText("Home base"), homeBaseId);
    await user.click(screen.getByRole("button", { name: "Find candidates" }));

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
    renderApp(`/vendors/${marketVendor.id}`);

    expect(await screen.findByRole("heading", { name: "Pier Farmers Market" })).toBeInTheDocument();
    const stalls = await screen.findByRole("list", { name: "Stalls at Pier Farmers Market" });
    expect(within(stalls).getByText("Sandy's Stone Fruit")).toBeInTheDocument();
    expect(within(stalls).getByText(/Saturday 08:00–13:00 \(inherited\)/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit vendor" }));
    await user.click(screen.getByRole("radio", { name: "Chain-wide" }));
    await user.click(screen.getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ price_scope: "chain" }));
    expect(await screen.findByText("Prices chain-wide")).toBeInTheDocument();
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
    renderApp(`/vendors/${marketVendor.id}`);

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
    renderApp(`/vendors/${marketVendor.id}`);

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
    renderApp(`/vendors/${marketVendor.id}`);

    await user.click(await screen.findByRole("button", { name: "Add a location" }));
    await user.click(screen.getByRole("button", { name: "Use my location" }));
    // Standing in the shop is the other way the coordinates are known.
    await waitFor(() => expect(screen.getByLabelText("Coordinates")).toHaveValue("33.4123, -120.5987"));
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
    renderApp("/settings/home-bases");

    const list = await screen.findByRole("list", { name: "Home bases" });
    expect(within(list).getByText("Harbour flat")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create home base" })).toBeDisabled();

    await user.click(within(list).getByRole("button", { name: "Delete Harbour flat" }));
    await user.click(within(list).getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This home base is the default for 2 locations. Reassign them first.");
  });
});
