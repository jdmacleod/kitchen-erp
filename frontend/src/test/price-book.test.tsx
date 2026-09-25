import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NeedsBridgeItem, Offer, ProductPrices } from "../api/pricebook";
import { flour, flourId, flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, chainVendorId, marketLocation, marketLocationId, marketVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

const thresholds = { fresh: 14, refrigerated: 45, shelf_stable: 120 };

const chainPoint = {
  price: "4.99",
  qty: "1",
  unit: "each",
  source: "shelf" as const,
  norm_unit: "g",
  norm_status: "ok" as const,
  location_id: chainLocationId,
  location_name: chainLocation.name,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
  price_scope: "chain" as const,
  // Chain-scoped: the series is the vendor, whichever branch saw the price.
  series: chainVendorId,
};

const history: ProductPrices = {
  points: [
    { ...chainPoint, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01", observed_at: "2026-07-01T15:00:00Z", is_promo: false, norm_unit_price: "0.002200" },
    { ...chainPoint, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02", observed_at: "2026-08-01T15:00:00Z", is_promo: true, price: "3.99", norm_unit_price: "0.001760" },
    { ...chainPoint, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a03", observed_at: "2026-09-18T15:00:00Z", is_promo: false, norm_unit_price: "0.002200", location_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6009", location_name: "Millstone Cove" },
    {
      ...chainPoint,
      observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a04",
      observed_at: "2026-05-20T15:00:00Z",
      is_promo: false,
      price: "5.50",
      norm_unit_price: "0.002425",
      location_id: marketLocationId,
      location_name: marketLocation.name,
      vendor_id: marketVendorId,
      vendor_name: marketLocation.vendor.name,
      price_scope: "location",
      series: marketLocationId,
    },
    // Not normalized: charted nowhere, counted in the caption.
    { ...chainPoint, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a05", observed_at: "2026-09-19T15:00:00Z", is_promo: false, unit: "cup", norm_unit: null, norm_unit_price: null, norm_status: "no_density" },
  ],
  latest: [
    { location_id: chainLocationId, location_name: chainLocation.name, vendor_id: chainVendorId, vendor_name: chainLocation.vendor.name, price_scope: "chain", observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a03", observed_at: "2026-09-18T15:00:00Z", price: "4.99", qty: "1", unit: "each", is_promo: false, norm_unit_price: "0.002200", norm_unit: "g", norm_status: "ok", age_days: "3", stale: false },
    { location_id: marketLocationId, location_name: marketLocation.name, vendor_id: marketVendorId, vendor_name: marketLocation.vendor.name, price_scope: "location", observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a04", observed_at: "2026-05-20T15:00:00Z", price: "5.50", qty: "1", unit: "each", is_promo: false, norm_unit_price: "0.002425", norm_unit: "g", norm_status: "ok", age_days: "124", stale: true },
  ],
};

const offerBase = {
  pack_qty: "5",
  pack_unit: "lb",
  location_id: chainLocationId,
  location_name: chainLocation.name,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
  price_scope: "chain" as const,
  observed_at: "2026-09-18T15:00:00Z",
  qty: "1",
  unit: "each",
  is_promo: false,
  norm_unit: "g",
  norm_status: "ok" as const,
  age_days: "3",
  stale: false,
};
const offers: Offer[] = [
  { ...offerBase, product_id: hits[1].id, product_name: hits[1].name, brand: hits[1].brand, quality_rating: 2, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b01", price: "3.50", norm_unit_price: "0.001750", is_promo: true },
  { ...offerBase, product_id: flourProductId, product_name: flourProduct.name, brand: flourProduct.brand, quality_rating: 4, observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b02", price: "4.99", norm_unit_price: "0.002200", age_days: "130", stale: true },
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

describe("product price history", () => {
  it("charts one series per chain vendor or location, marks promotional points, and badges stale latest prices", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, history),
    });
    renderApp(`/catalog/products/${flourProductId}`);

    const chart = await screen.findByTestId("price-history-chart");
    expect(chart).toHaveAccessibleName("Price per g over time, 2 series");
    const series = within(chart).getAllByTestId("price-series");
    expect(series.map((s) => s.dataset.series)).toEqual([chainVendorId, marketLocationId]);
    // Three normalized chain points, at two branches, fall in one series.
    expect(within(series[0]).getAllByTestId(/price-point|promo-point/)).toHaveLength(3);
    const promo = within(chart).getAllByTestId("promo-point");
    expect(promo).toHaveLength(1);
    expect(promo[0]).toHaveTextContent("sale");
    expect(promo[0].querySelector("path")).not.toBeNull();
    expect(within(chart).getAllByTestId("price-point").every((p) => p.querySelector("circle") !== null)).toBe(true);
    const figure = chart.closest("figure")!;
    expect(figure).toHaveTextContent("Millstone Market");
    expect(figure).toHaveTextContent("Pier Farmers Market");
    expect(figure).toHaveTextContent("1 not normalized and not charted");

    const table = screen.getByRole("table", { name: "Latest price per location" });
    const rows = within(table).getAllByTestId("latest-price");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("3 days");
    expect(within(rows[0]).queryByText("stale")).not.toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("chain price");
    expect(rows[1]).toHaveTextContent("124 days");
    expect(within(rows[1]).getByText("stale")).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("$0.002425/g");
  });

  it("says so when there is no history yet", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    });
    renderApp(`/catalog/products/${flourProductId}`);
    expect(await screen.findByText("No prices recorded yet.")).toBeInTheDocument();
  });
});

describe("ingredient offers", () => {
  it("lists offers cheapest first with age and stale marks, and sends the minimum quality filter", async () => {
    const calls = mockApi({
      ...baseRoutes(),
      [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
      [`GET /ingredients/${flourId}/offers`]: (call: RecordedCall) => {
        const min = call.query.get("min_quality");
        return jsonResponse(200, { items: min ? offers.filter((o) => (o.quality_rating ?? 0) >= Number(min)) : offers, stale_thresholds: thresholds });
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    const table = await screen.findByRole("table", { name: "Offers" });
    let rows = within(table).getAllByTestId("offer");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Riverbend Bread Flour");
    expect(rows[0]).toHaveTextContent("$0.00175/g");
    expect(rows[0]).toHaveTextContent("sale");
    expect(rows[0]).toHaveTextContent("2/5");
    expect(rows[1]).toHaveTextContent("130 days");
    expect(within(rows[1]).getByText("stale")).toBeInTheDocument();
    expect(screen.getByText("Prices older than 120 days count as stale for a shelf-stable ingredient.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith(`/ingredients/${flourId}/offers`)).at(-1)?.query.has("min_quality")).toBe(false);

    await user.selectOptions(screen.getByLabelText("Minimum quality"), "4");
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith(`/ingredients/${flourId}/offers`)).at(-1)?.query.get("min_quality")).toBe("4"));
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Offers" })).getAllByTestId("offer")).toHaveLength(1));
    rows = within(screen.getByRole("table", { name: "Offers" })).getAllByTestId("offer");
    expect(rows[0]).toHaveTextContent("Millstone All-Purpose Flour");

    await user.click(screen.getByRole("checkbox", { name: "Exclude stale" }));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith(`/ingredients/${flourId}/offers`)).at(-1)?.query.get("exclude_stale")).toBe("true"));
  });
});

describe("needs a bridge", () => {
  it("links each failed normalization to where it is fixed", async () => {
    const items: NeedsBridgeItem[] = [
      { ingredient: { id: flourId, name: flour.name, canonical_unit: "g" }, product: { id: flourProductId, name: flourProduct.name, brand: flourProduct.brand, pack_qty: "5", pack_unit: "lb" }, status: "no_density", observation_count: 2, latest_observed_at: "2026-09-19T15:00:00Z" },
      { ingredient: { id: hits[1].ingredient.id, name: hits[1].ingredient.name, canonical_unit: "g" }, product: { id: hits[1].id, name: hits[1].name, brand: hits[1].brand, pack_qty: null, pack_unit: null }, status: "no_pack", observation_count: 1, latest_observed_at: "2026-09-10T15:00:00Z" },
    ];
    mockApi({ ...baseRoutes(), "GET /price-book/needs-bridge": () => jsonResponse(200, { items }) });
    renderApp("/catalog/bridges");

    const table = await screen.findByRole("table", { name: "Needs a bridge" });
    const rows = within(table).getAllByTestId("needs-bridge");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("no density");
    expect(within(rows[0]).getByRole("link", { name: "Add a density" })).toHaveAttribute("href", `/catalog/ingredients/${flourId}#density-heading`);
    expect(rows[1]).toHaveTextContent("no pack size");
    expect(within(rows[1]).getByRole("link", { name: "Set the pack" })).toHaveAttribute("href", `/catalog/products/${hits[1].id}`);
  });
});
