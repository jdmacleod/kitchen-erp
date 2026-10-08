// The vendor page's posted-price checks: on or off, and a pause when a store's
// pages keep failing to load (issue 264). The vendors are invented.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Vendor } from "../api/geo";
import { chainLocation, chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

function routes(vendor: () => Vendor, extra: Record<string, Parameters<typeof mockApi>[0][string]> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, vendor()),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
    "GET /vendor-suggestions/summary": () => jsonResponse(200, { total: 0, vendors: [] }),
    ...extra,
  };
}

describe("posted prices on a vendor's page", () => {
  it("turns checking posted prices online on", async () => {
    let vendor: Vendor = { ...chainVendor };
    const calls = mockApi(
      routes(() => vendor, {
        [`PATCH /vendors/${chainVendorId}`]: (call) => {
          vendor = { ...vendor, ...(call.body as Partial<Vendor>) };
          return jsonResponse(200, vendor);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    expect(await screen.findByRole("heading", { name: "Posted prices" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Off" })).toBeChecked();
    expect(screen.getByText(/Some stores only work through Save to Kitchen ERP/)).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "On" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ fetch_policy: "server_fetch" }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "On" })).toBeChecked());
    expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
  });

  it("shows a pause and checks again on request", async () => {
    let vendor: Vendor = {
      ...chainVendor,
      fetch_policy: "server_fetch",
      refresh_unreachable_since: "2026-10-01T12:00:00Z",
      refresh_paused_until: "2026-10-08T12:00:00Z",
    };
    const calls = mockApi(
      routes(() => vendor, {
        [`POST /vendors/${chainVendorId}/check-prices`]: () => {
          vendor = { ...vendor, refresh_paused_until: null };
          return jsonResponse(200, { queued: 3, vendor });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    expect(await screen.findByText(/Posted prices haven't been reachable since/)).toBeInTheDocument();
    expect(screen.getByText(/checking again/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/check-prices"))).toBe(true));
    expect(await screen.findByText("Asked the lookup helper for 3 pages.")).toBeInTheDocument();
    expect(screen.queryByText(/haven't been reachable/)).not.toBeInTheDocument();
  });

  it("says nothing is checked when the store's pages are never saved", async () => {
    mockApi(routes(() => ({ ...chainVendor, fetch_policy: "none" })));
    renderApp(`/catalog/vendors/${chainVendorId}`);
    expect(await screen.findByText("Pages from this store aren't saved or checked.")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "On" })).not.toBeInTheDocument();
  });
});
