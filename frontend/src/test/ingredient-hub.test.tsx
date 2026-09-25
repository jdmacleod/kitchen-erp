import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { IngredientPricePoint, Offer } from "../api/pricebook";
import { bestRecent } from "../components/pricebook/IngredientSummary";
import { flour, flourId, flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, chainVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

const base = {
  pack_qty: "5",
  pack_unit: "lb",
  location_id: chainLocationId,
  location_name: chainLocation.name,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
  price_scope: "chain" as const,
  qty: "1",
  unit: "each",
  is_promo: false,
  norm_unit: "g",
  norm_status: "ok" as const,
  age_days: "3",
  stale: false,
  quality_rating: 3,
};

const offer = (over: Partial<Offer>): Offer => ({
  ...base,
  product_id: flourProductId,
  product_name: flourProduct.name,
  brand: flourProduct.brand,
  observation_id: `obs-${Math.random()}`,
  observed_at: daysAgo(3),
  price: "4.99",
  norm_unit_price: "0.002200",
  ...over,
});

// Cheapest first, as the API sends them; the uncomparable one sorts last (G8).
const oldCheap = offer({ norm_unit_price: "0.001000", observed_at: daysAgo(120), product_name: "Old sack" });
const recent = offer({ norm_unit_price: "0.002200", product_name: "Everyday flour" });
const noDensity = offer({ product_id: hits[1].id, product_name: "Flour by the cup", brand: null, norm_unit_price: null, norm_unit: null, norm_status: "no_density", price: "3.25", qty: "2", unit: "cup" });

const point = (n: number, price: string): IngredientPricePoint => ({
  observation_id: `p-${n}`,
  observed_at: daysAgo(n),
  norm_unit_price: price,
  norm_unit: "g",
  is_promo: false,
  source: "shelf",
  product_id: flourProductId,
  product_name: flourProduct.name,
  location_id: chainLocationId,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
});

function mount({ offers, history }: { offers: Offer[]; history: RouteHandler }) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
    [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: offers, stale_thresholds: { fresh: 14, refrigerated: 45, shelf_stable: 120 } }),
    [`GET /ingredients/${flourId}/price-history`]: history,
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
  });
  renderApp(`/catalog/ingredients/${flourId}`);
}

describe("bestRecent (G8)", () => {
  it("takes the cheapest comparable offer from the last 90 days", () => {
    expect(bestRecent([oldCheap, recent, noDensity])?.product_name).toBe("Everyday flour");
    expect(bestRecent([oldCheap, noDensity])).toBeNull();
  });
});

describe("the ingredient hub (UI-3.9, UI-3.10)", () => {
  it("has the breadcrumb, the chip and meta line, and Log shelf price as the primary action", async () => {
    mount({ offers: [recent], history: () => jsonResponse(200, { days: 90, points: [], low: null, high: null }) });
    expect(await screen.findByRole("heading", { level: 1, name: flour.name })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Ingredients" })).toHaveAttribute("href", "/catalog/ingredients");
    expect(screen.getByText("Measured in g")).toBeInTheDocument();
    expect(screen.getByText("pantry")).toHaveClass("cat-pantry");
    expect(screen.getAllByRole("link", { name: "Log shelf price" })[0]).toHaveAttribute("href", "/shop/shelf-prices");
    expect(within(await screen.findByRole("list", { name: "Products of this ingredient" })).getByRole("link", { name: /All-Purpose Flour/ })).toBeInTheDocument();
  });

  it("shows the best recent price on the olive card, leaving out old and uncomparable prices", async () => {
    mount({ offers: [oldCheap, recent, noDensity], history: () => jsonResponse(200, { days: 90, points: [point(3, "0.002200")], low: "0.002200", high: "0.002200" }) });
    const best = await screen.findByRole("region", { name: "Best recent price" });
    expect(best).toHaveTextContent("$0.0022/g");
    expect(best).toHaveTextContent("Everyday flour");
    expect(best.className).toMatch(/bg-green-50/);
  });

  it("draws the 90-day sparkline and range, and hides the line with one point (D22)", async () => {
    mount({ offers: [recent], history: () => jsonResponse(200, { days: 90, points: [point(40, "0.002500"), point(10, "0.002000"), point(3, "0.002200")], low: "0.002000", high: "0.002600" }) });
    const card = await screen.findByRole("region", { name: "Last 90 days" });
    expect(await within(card).findByTestId("price-range")).toHaveTextContent("$0.002/g – $0.0026/g");
    expect(within(card).getByRole("img", { name: "Cheapest price each day over the last 90 days" })).toBeInTheDocument();
    expect(within(card).getAllByRole("row")).toHaveLength(4);
  });

  it("says the one price without a line when there is only one", async () => {
    mount({ offers: [recent], history: () => jsonResponse(200, { days: 90, points: [point(3, "0.002200")], low: "0.002200", high: "0.002200" }) });
    const card = await screen.findByRole("region", { name: "Last 90 days" });
    expect(await within(card).findByTestId("price-range")).toHaveTextContent("$0.0022/g");
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();
  });

  it("lists an uncomparable price last, with what was paid and how to fix it (G8)", async () => {
    mount({ offers: [recent, noDensity], history: () => jsonResponse(200, { days: 90, points: [], low: null, high: null }) });
    const table = await screen.findByRole("table", { name: "Prices by vendor" });
    const rows = within(table).getAllByTestId("offer");
    expect(rows.map((r) => r.dataset.comparable)).toEqual(["true", "false"]);
    expect(rows[0]).toHaveTextContent("Same price at every location");
    expect(rows[1]).toHaveTextContent("$3.25 / 2 cup");
    expect(within(rows[1]).getByRole("link", { name: /Can't compare yet · add a density/ })).toHaveAttribute(
      "href",
      `/catalog/ingredients/${flourId}#density-heading`,
    );
  });

  it("offers a first shelf price when there are no prices at all", async () => {
    mount({ offers: [], history: () => jsonResponse(200, { days: 90, points: [], low: null, high: null }) });
    const empty = await screen.findByRole("region", { name: "No prices yet" });
    expect(within(empty).getByRole("link", { name: "Log shelf price" })).toHaveAttribute("href", "/shop/shelf-prices");
    expect(screen.queryByRole("table", { name: "Prices by vendor" })).not.toBeInTheDocument();
  });
});
