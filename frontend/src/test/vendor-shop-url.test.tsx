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

  it("saves a search page, and clears it when emptied", async () => {
    const search = "https://cardinal-foods.example.test/search?q={q}";
    let vendor: Vendor = { ...chainVendor, shop_url: null, search_url: null };
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
    let form = await screen.findByRole("form", { name: "Edit vendor" });
    // "{{" types one brace: user-event reads each "{" as the start of a key name.
    await user.type(within(form).getByLabelText("Search page"), search.replaceAll("{", "{{"));
    await user.click(within(form).getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ search_url: search });

    await user.click(await screen.findByRole("button", { name: "Edit vendor" }));
    form = await screen.findByRole("form", { name: "Edit vendor" });
    await user.clear(within(form).getByLabelText("Search page"));
    await user.click(within(form).getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({ search_url: null });
  });
});
