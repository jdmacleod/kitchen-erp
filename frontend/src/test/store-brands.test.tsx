// Store brands on products and vendors (spec 16, 2R-1). Every family, brand and
// store here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { HouseBrand, Product } from "../api/catalog";
import type { BrandFamily, Vendor } from "../api/geo";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const larkspur: BrandFamily = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01", key: "larkspur", name: "Larkspur Markets", kind: "retailer" };
const meadowline: BrandFamily = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02", key: "meadowline", name: "Meadowline Cooperative", kind: "cooperative" };

const select: HouseBrand = {
  key: "larkspur/select",
  name: "Larkspur Select",
  tier: "standard",
  current: true,
  family: { key: "larkspur", name: "Larkspur Markets", kind: "retailer" },
};

function productRoutes(product: Product) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
  };
}

describe("store brand on a product", () => {
  it("says whose brand it is and its tier", async () => {
    mockApi(productRoutes({ ...flourProduct, brand: "Larkspur Select", house_brand: select }));
    renderApp(`/catalog/products/${flourProductId}`);
    expect(await screen.findByText("Larkspur Markets brand · standard")).toBeInTheDocument();
  });

  it("names a retired brand's replacement, and a wholesale label as a store label", async () => {
    const retired: HouseBrand = { ...select, key: "larkspur/farms", name: "Larkspur Farms", current: false, replaced_by_name: "Larkspur Select" };
    mockApi(productRoutes({ ...flourProduct, house_brand: retired }));
    renderApp(`/catalog/products/${flourProductId}`);
    expect(await screen.findByText("Larkspur Markets brand · standard · now Larkspur Select")).toBeInTheDocument();
  });

  it("shows nothing for a national brand", async () => {
    mockApi(productRoutes({ ...flourProduct, house_brand: null }));
    renderApp(`/catalog/products/${flourProductId}`);
    await screen.findByRole("form", { name: "Edit product" });
    expect(screen.queryByText(/ brand · /)).not.toBeInTheDocument();
  });
});

describe("a vendor's brand family", () => {
  function vendorRoutes(vendor: () => Vendor, onPatch?: (body: unknown) => Vendor) {
    return {
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, vendor()),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
      "GET /brand-families": () => jsonResponse(200, { items: [larkspur, meadowline] }),
      [`PATCH /vendors/${chainVendorId}`]: (c: { body: unknown }) => jsonResponse(200, onPatch ? onPatch(c.body) : vendor()),
    };
  }

  it("shows the family and lets a person choose another, from retailers only", async () => {
    let vendor: Vendor = { ...chainVendor, brand_family: null };
    const calls = mockApi(
      vendorRoutes(
        () => vendor,
        () => {
          vendor = { ...vendor, brand_family: larkspur };
          return vendor;
        },
      ),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);
    await user.click(await screen.findByRole("button", { name: "Edit vendor" }));
    const form = await screen.findByRole("form", { name: "Edit vendor" });
    const picker = await within(form).findByLabelText("Store brands");
    await waitFor(() => expect(within(picker).getAllByRole("option")).toHaveLength(2));
    expect(within(picker).queryByRole("option", { name: "Meadowline Cooperative" })).not.toBeInTheDocument();
    await user.selectOptions(picker, larkspur.id);
    await user.click(within(form).getByRole("button", { name: "Save vendor" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ brand_family_id: larkspur.id });
    expect(await screen.findByTestId("vendor-brand-family")).toHaveTextContent("Sells Larkspur Markets brands");
  });
});
