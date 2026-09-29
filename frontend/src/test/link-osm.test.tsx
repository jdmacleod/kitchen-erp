// Linking a location to OpenStreetMap, phones and source lines (spec 03 §1F,
// design D5, D7, D14, D17). Every place, number and street is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { LinkCandidate, VendorLocation } from "../api/geo";
import { describeSource, phoneError } from "../lib/sources";
import { chainLocation, chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const candidate: LinkCandidate = {
  osm_type: "node",
  osm_id: 301,
  name: "Millstone Market",
  kind_guess: "chain",
  address: "4 Pier Lane, Seaside", // pii-scan: allow invented street (synthetic fixture)
  opening_hours: "Mo-Su 08:00-21:00",
  phone: "+1 555 0100",
  website: null,
  distance_m: 12,
  linked_to: null,
  fills: ["address", "phone"],
  keeps: ["name"],
};

const taken: LinkCandidate = {
  ...candidate,
  osm_id: 302,
  name: "Millstone Express",
  distance_m: 180,
  linked_to: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009", name: "Millstone Pier" },
};

function routes(location: () => VendorLocation, extra: Record<string, Parameters<typeof mockApi>[0][string]> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, chainVendor),
    "GET /vendor-locations": () => jsonResponse(200, { items: [location()] }),
    ...extra,
  };
}

describe("link to OpenStreetMap", () => {
  it("previews what linking fills and keeps, then links", async () => {
    let location: VendorLocation = { ...chainLocation };
    const calls = mockApi(
      routes(() => location, {
        [`GET /vendor-locations/${chainLocation.id}/osm-candidates`]: () => jsonResponse(200, { items: [candidate, taken] }),
        [`POST /vendor-locations/${chainLocation.id}/link-osm`]: () => {
          location = {
            ...location,
            osm_type: "node",
            osm_id: 301,
            phone: "+1 555 0100",
            sources: { phone: { source: "osm", ref: "node/301", checked_at: "2026-09-29T12:00:00Z" } },
          };
          return jsonResponse(200, { ...location, stalls: [] });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    expect(await screen.findByText("Not linked to OpenStreetMap")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Link ${chainLocation.name} to OpenStreetMap` }));
    const dialog = await screen.findByRole("dialog", { name: `Link ${chainLocation.name} to OpenStreetMap` });
    const link = within(dialog).getByRole("button", { name: "Link" });
    expect(link).toBeDisabled();
    // A place already linked to another location cannot be chosen, and says where.
    expect(within(dialog).getByRole("radio", { name: /Millstone Express/ })).toBeDisabled();
    expect(within(dialog).getByText(/Linked to Millstone Pier/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("radio", { name: /Millstone Market/ }));
    expect(within(dialog).getByText("Will fill: address, phone · Keeps your: name (you edited it)")).toBeInTheDocument();
    await user.click(link);

    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ osm_type: "node", osm_id: 301 }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Linked to OpenStreetMap")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+1 555 0100" })).toHaveAttribute("href", "tel:+15550100");
    await user.click(screen.getByText("Sources"));
    expect(screen.getByText(/^From OpenStreetMap \(node 301\), checked/)).toBeInTheDocument();
  });

  it("says why when lookups are off, and when nothing is near", async () => {
    let answer = () => errorResponse(409, "integration_disabled", "Overpass is disabled.");
    mockApi(
      routes(() => chainLocation, {
        [`GET /vendor-locations/${chainLocation.id}/osm-candidates`]: () => answer(),
      }),
    );
    const user = userEvent.setup();
    const { client } = renderApp(`/catalog/vendors/${chainVendorId}`);

    await user.click(await screen.findByRole("button", { name: `Link ${chainLocation.name} to OpenStreetMap` }));
    expect(await screen.findByText(/OpenStreetMap lookups are off/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    answer = () => jsonResponse(200, { items: [] });
    client.clear();
    await user.click(await screen.findByRole("button", { name: `Link ${chainLocation.name} to OpenStreetMap` }));
    expect(await screen.findByText("No OpenStreetMap places within 250 m.")).toBeInTheDocument();
  });

  it("unlinks a linked location and keeps its values", async () => {
    let location: VendorLocation = { ...chainLocation, osm_type: "node", osm_id: 301, phone: "+1 555 0100" };
    const calls = mockApi(
      routes(() => location, {
        [`POST /vendor-locations/${chainLocation.id}/unlink-osm`]: () => {
          location = { ...location, osm_type: null, osm_id: null };
          return jsonResponse(200, { ...location, stalls: [] });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    await user.click(await screen.findByRole("button", { name: `Unlink ${chainLocation.name} from OpenStreetMap` }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/unlink-osm"))).toBe(true));
    expect(await screen.findByText("Not linked to OpenStreetMap")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+1 555 0100" })).toBeInTheDocument();
  });

  it("edits the phone, refusing a malformed one, and shows each field's source as a hint", async () => {
    let location: VendorLocation = {
      ...chainLocation,
      address: "4 Pier Lane, Seaside", // pii-scan: allow invented street (synthetic fixture)
      sources: { address: { source: "osm", ref: "node/301", checked_at: null } },
    };
    const calls = mockApi(
      routes(() => location, {
        [`PATCH /vendor-locations/${chainLocation.id}`]: (call) => {
          location = { ...location, ...(call.body as object) };
          return jsonResponse(200, { ...location, stalls: [] });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    await user.click(await screen.findByRole("button", { name: `Edit ${chainLocation.name}` }));
    const form = screen.getByRole("form", { name: `Edit location ${chainLocation.name}` });
    expect(within(form).getByLabelText("Address")).toHaveAccessibleDescription("From OpenStreetMap (node 301)");
    const phone = within(form).getByLabelText("Phone");
    expect(phone).toHaveAttribute("type", "tel");
    await user.type(phone, "555-01");
    expect(phone).toHaveAccessibleDescription("Needs 7 to 15 digits.");
    await user.click(within(form).getByRole("button", { name: "Save location" }));
    expect(within(form).getByText("Phone: Needs 7 to 15 digits.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);

    await user.type(phone, "42");
    await user.click(within(form).getByRole("button", { name: "Save location" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ phone: "555-0142" }));
  });
});

describe("source wording (D14)", () => {
  it("reads each source as one plain sentence", () => {
    expect(describeSource(undefined)).toBe("Entered by hand");
    expect(describeSource({ source: "osm", ref: "way/77", checked_at: null })).toBe("From OpenStreetMap (way 77)");
    expect(describeSource({ source: "enriched:enrich-tool", ref: "https://example.test", checked_at: null })).toBe("Suggested by enrich-tool");
    expect(describeSource({ source: "import", ref: "vendors-2026-09.yaml", checked_at: null })).toBe("From the file vendors-2026-09.yaml");
    expect(describeSource({ source: "osm", ref: "node/1", checked_at: "2026-09-03T12:00:00Z" })).toMatch(/^From OpenStreetMap \(node 1\), checked .+2026/);
  });

  it("accepts a phone as typed and refuses one that is not", () => {
    for (const ok of ["(555) 555-0100", "555 555 0100", "+1 555 555 0100", "", "5550100"]) expect(phoneError(ok)).toBeNull();
    expect(phoneError("5555.0100.0100.0100")).toBe("Needs 7 to 15 digits.");
    expect(phoneError("555-01")).toBe("Needs 7 to 15 digits.");
    expect(phoneError("call 555 0100")).toBe("Use digits, spaces and + ( ) - . only.");
  });
});
