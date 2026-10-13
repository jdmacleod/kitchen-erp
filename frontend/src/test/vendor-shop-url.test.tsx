// A vendor's online shop on another site (13, 3.4). Every vendor and address here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Vendor } from "../api/geo";
import { chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const SHOP = "https://cardinal-foods.storefronts.example.test";

function routes(vendor: () => Vendor, onPatch: (body: unknown) => Vendor) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, vendor()),
    "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    "GET /brand-families": () => jsonResponse(200, { items: [] }),
    [`PATCH /vendors/${chainVendorId}`]: (c: { body: unknown }) =>
      jsonResponse(200, onPatch(c.body)),
  };
}

describe("a vendor's online shop", () => {
  it("is saved from the edit form and linked on the vendor's page", async () => {
    let vendor: Vendor = { ...chainVendor, shop_url: null };
    const calls = mockApi(
      routes(
        () => vendor,
        (body) => {
          vendor = { ...vendor, ...(body as Partial<Vendor>) };
          return vendor;
        },
      ),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);
    await user.click(await screen.findByRole("button", { name: "Edit vendor" }));
    const form = await screen.findByRole("form", { name: "Edit vendor" });
    await user.type(within(form).getByLabelText("Online shop"), SHOP);
    await user.click(within(form).getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ shop_url: SHOP });
    expect(await screen.findByRole("link", { name: "online shop" })).toHaveAttribute("href", SHOP);
  });

  it("is not shown when a vendor has none", async () => {
    mockApi(
      routes(
        () => ({ ...chainVendor, shop_url: null }),
        () => chainVendor,
      ),
    );
    renderApp(`/catalog/vendors/${chainVendorId}`);
    await screen.findByRole("button", { name: "Edit vendor" });
    expect(screen.queryByRole("link", { name: "online shop" })).toBeNull();
  });
});
