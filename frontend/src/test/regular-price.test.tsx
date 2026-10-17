// The regular price beside what was paid (issue 381): shown in words, in neutral,
// next to the sale mark; cheapest stays on what was paid. Every amount is invented.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CompareResult, Offer, ProductPrices } from "../api/pricebook";
import {
  flour,
  flourId,
  flourProduct,
  flourProductId,
  ingredientMatch,
  units,
} from "./catalog-fixtures";
import {
  chainLocation,
  chainLocationId,
  chainVendorId,
  marketLocation,
  marketLocationId,
  marketVendorId,
} from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const sale = {
  observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c01",
  observed_at: "2026-09-18T15:00:00Z",
  price: "4.99",
  qty: "1",
  unit: "each",
  is_promo: true,
  regular_price: "5.49",
  norm_unit_price: "0.002200",
  norm_unit: "g",
  display_unit_price: "0.998",
  display_unit: "lb",
  regular_display_unit_price: "1.10",
  norm_status: "ok" as const,
  location_id: chainLocationId,
  location_name: chainLocation.name,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
  price_scope: "chain" as const,
};
const plain = {
  ...sale,
  observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c02",
  price: "5.20",
  is_promo: false,
  regular_price: null,
  norm_unit_price: "0.002293",
  display_unit_price: "1.04",
  regular_display_unit_price: null,
  location_id: marketLocationId,
  location_name: marketLocation.name,
  vendor_id: marketVendorId,
  vendor_name: marketLocation.vendor.name,
  price_scope: "location" as const,
};

const history: ProductPrices = {
  points: [
    { ...sale, source: "receipt", series: chainVendorId },
    { ...plain, source: "shelf", series: marketLocationId },
  ],
  latest: [
    { ...sale, age_days: "3", stale: false },
    { ...plain, age_days: "3", stale: false },
  ],
};

const offerBase = {
  product_id: flourProductId,
  product_name: flourProduct.name,
  brand: flourProduct.brand,
  quality_rating: 4,
  pack_qty: "5",
  pack_unit: "lb",
  age_days: "3",
  stale: false,
};
const offers: Offer[] = [
  { ...sale, ...offerBase },
  { ...plain, ...offerBase },
];

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
  };
}

describe("regular price beside what was paid", () => {
  it("shows the regular price on the product page's latest prices and chart", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, history),
    });
    renderApp(`/catalog/products/${flourProductId}`);

    const table = await screen.findByRole("table", { name: "Latest price per location" });
    const [saleRow, plainRow] = within(table).getAllByTestId("latest-price");
    expect(saleRow).toHaveTextContent("$4.99");
    expect(saleRow).toHaveTextContent("sale");
    expect(within(saleRow).getByTestId("regular-price")).toHaveTextContent("regular $5.49");
    expect(within(saleRow).getByTestId("regular-price")).toHaveClass("text-neutral-600");
    expect(within(plainRow).queryByTestId("regular-price")).not.toBeInTheDocument();

    const chart = screen.getByTestId("price-history-chart");
    expect(within(chart).getByTestId("promo-point")).toHaveTextContent(
      "$0.998/lb on Sep 18, 2026 (sale, regular $1.10/lb)",
    );
  });

  it("shows the regular unit price on an ingredient's offers", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
      [`GET /ingredients/${flourId}/offers`]: () =>
        jsonResponse(200, { items: offers, stale_after_days: 90 }),
    });
    renderApp(`/catalog/ingredients/${flourId}`);

    const table = await screen.findByRole("table", { name: "Prices by vendor" });
    const [saleRow, plainRow] = within(table).getAllByTestId("offer");
    expect(saleRow).toHaveTextContent("$0.998/lb");
    expect(within(saleRow).getByTestId("regular-price")).toHaveTextContent("regular $1.10/lb");
    expect(within(plainRow).queryByTestId("regular-price")).not.toBeInTheDocument();
  });

  it("keeps cheapest on what was paid in Compare and says the regular price", async () => {
    const result: CompareResult = {
      vendors: [
        { id: chainVendorId, name: chainLocation.vendor.name },
        { id: marketVendorId, name: marketLocation.vendor.name },
      ],
      rows: [
        {
          ingredient_id: flourId,
          ingredient_name: flour.name,
          canonical_unit: "g",
          cells: {
            [chainVendorId]: { ...sale, ...offerBase, cheapest: true },
            [marketVendorId]: { ...plain, ...offerBase, cheapest: false },
          },
        },
      ],
      stale_after_days: 90,
    };
    mockApi({
      ...baseRoutes(),
      "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
      "POST /price-book/compare": () => jsonResponse(200, result),
    });
    const user = userEvent.setup();
    renderApp("/shop/compare");
    await user.type(await screen.findByRole("combobox", { name: "Add an ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));

    const cheap = await screen.findByTestId(`cell-${flourId}-${chainVendorId}`);
    expect(cheap).toHaveTextContent("$0.998/lb");
    expect(cheap).toHaveTextContent("cheapest");
    expect(cheap).toHaveTextContent("sale, regular $1.10/lb");
    const other = screen.getByTestId(`cell-${flourId}-${marketVendorId}`);
    expect(other).not.toHaveTextContent("cheapest");
    expect(other).not.toHaveTextContent("regular");
  });
});
