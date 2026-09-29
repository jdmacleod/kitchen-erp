// Vendor export (spec 03 §1F, design D11 and D12). Invented vendors only.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { VendorLocation } from "../api/geo";
import { chainLocation, chainVendor, chainVendorId, homeBase, stallLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const base = {
  "GET /auth/me": () => jsonResponse(200, adminUser),
  "GET /health": () => jsonResponse(200, { status: "ok" }),
  "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
};

describe("export vendors", () => {
  it("says how much of the list is public before downloading", async () => {
    mockApi({
      ...base,
      "GET /vendors": () => jsonResponse(200, { items: [{ ...chainVendor, location_count: 1, last_visit: null }] }),
      "GET /vendors/export-summary": () => jsonResponse(200, { locations: 39, public: 3 }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/vendors");
    await user.click(await screen.findByRole("button", { name: "Export" }));
    const dialog = await screen.findByRole("dialog", { name: "Export vendors" });
    expect(await within(dialog).findByRole("heading", { name: "Public — 3 of 39 locations shared" })).toBeInTheDocument();
    expect(within(dialog).getByText(/tick Share on a location to include more/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Keep it private/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Download the public file as YAML" })).toHaveAttribute("href", "/api/v1/vendors/export?format=yaml&mode=public");
    expect(within(dialog).getByRole("link", { name: "Download the household file as JSON" })).toHaveAttribute("href", "/api/v1/vendors/export?format=json&mode=household");
  });
});

describe("share in public export", () => {
  function render(location: VendorLocation) {
    let current = location;
    const calls = mockApi({
      ...base,
      [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, chainVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: [current] }),
      [`PATCH /vendor-locations/${location.id}`]: (call) => {
        current = { ...current, ...(call.body as object) };
        return jsonResponse(200, { ...current, stalls: [] });
      },
    });
    renderApp(`/catalog/vendors/${chainVendorId}`);
    return calls;
  }

  it("saves the checkbox with the form", async () => {
    const calls = render(chainLocation);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `Edit ${chainLocation.name}` }));
    const box = screen.getByRole("checkbox", { name: "Share in public export" });
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription(/Notes and home base never do/);
    await user.click(box);
    await user.click(screen.getByRole("button", { name: "Save location" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ publishable: true }));
    expect(await screen.findByText(/Shared in public export/)).toBeInTheDocument();
  });

  it("shows a linked location as shared and a stand as never shared, fixed", async () => {
    render({ ...chainLocation, osm_type: "node", osm_id: 301 });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `Edit ${chainLocation.name}` }));
    const box = screen.getByRole("checkbox", { name: "Share in public export" });
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
    expect(box).toHaveAccessibleDescription("Shared because it's linked to OpenStreetMap");
  });

  it("never shares a stand", async () => {
    render({ ...stallLocation, parent_location_id: null, publishable: true, vendor: { ...stallLocation.vendor, id: chainVendorId } });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `Edit ${stallLocation.name}` }));
    const box = screen.getByRole("checkbox", { name: "Share in public export" });
    expect(box).not.toBeChecked();
    expect(box).toBeDisabled();
    expect(box).toHaveAccessibleDescription("Stands are never shared");
    expect(screen.queryByText(/Shared in public export/)).not.toBeInTheDocument();
  });
});
