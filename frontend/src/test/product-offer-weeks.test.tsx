// Weekly flyers 2X (spec 18, X3, X5): the product page's chart marks offer weeks and
// gives a typical price. Every store, product and amount here is invented.
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { HistoryOffer, OfferHistory } from "../api/flyerOffers";
import type { ProductPrices } from "../api/pricebook";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, chainVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const point = {
  observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7d01",
  observed_at: "2026-10-07T15:00:00Z",
  price: "5.20",
  qty: "1",
  unit: "each",
  is_promo: false,
  regular_price: null,
  norm_unit_price: "0.002293",
  norm_unit: "g",
  display_unit_price: "1.04",
  display_unit: "lb",
  regular_display_unit_price: null,
  norm_status: "ok" as const,
  location_id: chainLocationId,
  location_name: chainLocation.name,
  vendor_id: chainVendorId,
  vendor_name: chainLocation.vendor.name,
  price_scope: "chain" as const,
};

const prices: ProductPrices = {
  points: [{ ...point, source: "receipt", series: chainVendorId }],
  latest: [{ ...point, age_days: "30", stale: false }],
};

function offer(n: number, over: Partial<HistoryOffer> = {}): HistoryOffer {
  return {
    id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7e0${n}`,
    product: { id: flourProductId, name: flourProduct.name, ingredient_id: null },
    vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01", name: "Larkspur Market" },
    location: { id: `loc-${n}`, name: `Larkspur ${n}` },
    name_as_printed: "Flour",
    price_basis: "multi",
    price: "5.00",
    basis_qty: "2",
    discount: null,
    size_qty: "5",
    size_unit: "lb",
    size_inherited: false,
    requires_loyalty: false,
    limit_qty: null,
    valid_from: "2026-11-04",
    valid_to: "2026-11-10",
    ad_week_from: "2026-11-04",
    ad_week_to: "2026-11-10",
    unit_price: "0.0011",
    unit: "g",
    display_unit_price: "0.50",
    display_unit: "lb",
    ...over,
  };
}

const typical = {
  display_unit_price: "0.77",
  display_unit: "lb",
  weeks: 12,
  regular_count: 1,
  offer_count: 2,
};

function mount(history: OfferHistory) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, prices),
    [`GET /products/${flourProductId}/flyer-offers`]: () => jsonResponse(200, history),
  });
  renderApp(`/catalog/products/${flourProductId}`);
}

describe("offer weeks on the product chart (X3)", () => {
  it("bands each ad week once, naming every store's offer, and gives the typical price", async () => {
    mount({
      items: [
        offer(1),
        // The same circular at another location adds nothing new to its week.
        offer(2),
        offer(3, {
          vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02", name: "Juniper Grocers" },
          price_basis: "each",
          price: "2.49",
          basis_qty: null,
          requires_loyalty: true,
        }),
        offer(4, {
          valid_from: "2026-10-21",
          valid_to: "2026-10-27",
          ad_week_from: "2026-10-21",
          ad_week_to: "2026-10-27",
        }),
      ],
      typical,
    });
    expect(await screen.findByTestId("typical-price")).toHaveTextContent(
      "Typical price $0.77/lb over the last 12 weeks, from 1 regular price and 2 offers",
    );
    const chart = screen.getByTestId("price-history-chart");
    const weeks = within(chart).getAllByTestId("offer-week");
    expect(weeks.map((w) => w.textContent)).toEqual([
      "On offer Nov 4, 2026 to Nov 10, 2026: Larkspur Market: 2 for $5; Juniper Grocers: $2.49, card price",
      "On offer Oct 21, 2026 to Oct 27, 2026: Larkspur Market: 2 for $5",
    ]);
    expect(screen.getByText("offer week")).toBeInTheDocument();
  });

  it("adds nothing for a product never on offer, even with a typical price (X5)", async () => {
    mount({ items: [], typical });
    await screen.findByTestId("price-history-chart");
    expect(screen.queryByTestId("offer-week")).not.toBeInTheDocument();
    expect(screen.queryByTestId("typical-price")).not.toBeInTheDocument();
    expect(screen.queryByText("offer week")).not.toBeInTheDocument();
  });
});
