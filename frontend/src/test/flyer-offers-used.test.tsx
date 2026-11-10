// Weekly flyers 2X (spec 18; UI-9.1, UI-9.2, X5): this week's offers on Home and as a
// mark on shopping-list items. Every store, product and offer here is invented.
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CurrentOffer } from "../api/flyerOffers";
import { chainLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import { chickpeas, item, kitchen, list, oatMilk, planHealth, settings } from "./plan-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { quietInbox } from "./recipe-fixtures";

const larkspur = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01", name: "Larkspur Market" };
const juniper = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02", name: "Juniper Grocers" };
const chickpeaProduct = {
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b01",
  name: "Larkspur dried chickpeas",
  ingredient_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7101", // the chickpeas item's ingredient
};

function offer(n: number, over: Partial<CurrentOffer> = {}): CurrentOffer {
  return {
    id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c${String(n).padStart(2, "0")}`,
    product: chickpeaProduct,
    vendor: larkspur,
    location: { id: `loc-${n}`, name: `Larkspur ${n}` },
    name_as_printed: "Dried Chickpeas",
    price_basis: "multi",
    price: "5.00",
    basis_qty: "3",
    discount: null,
    size_qty: "16",
    size_unit: "oz",
    size_inherited: false,
    requires_loyalty: false,
    limit_qty: null,
    valid_from: "2026-11-04",
    valid_to: "2026-11-10",
    ad_week_from: "2026-11-04",
    ad_week_to: "2026-11-10",
    unit_price: "0.0037",
    unit: "g",
    regular_unit_price: null,
    ...over,
  };
}

const offers = [
  offer(1),
  // The same circular at the chain's second location: one line, not two.
  offer(2),
  offer(3, {
    vendor: juniper,
    product: {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7301",
      name: "Moonfield oat milk",
      ingredient_id: null,
    },
    name_as_printed: "Moonfield Oat Milk",
    price_basis: "per_lb",
    price: "2.49",
    basis_qty: null,
    requires_loyalty: true,
    valid_to: "2026-11-11",
  }),
];

function mount(path: string, current: CurrentOffer[], routes: Record<string, RouteHandler> = {}) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
    "GET /purchases": () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /plan/list": () => jsonResponse(200, list({ unchecked: [chickpeas, oatMilk] })),
    "GET /plan/lists": () => jsonResponse(200, { items: [] }),
    "GET /flyer-offers/current": () => jsonResponse(200, { items: current }),
    ...routes,
  });
  renderApp(path);
  return calls;
}

describe("this week's offers on Home (UI-9.1)", () => {
  it("lists the kitchen's offers by store, priced as printed, each linking to its product", async () => {
    const calls = mount("/", offers);
    const card = await screen.findByRole("region", { name: "This week's offers" });
    const lark = within(card).getByRole("region", { name: "Larkspur Market" });
    expect(within(lark).getAllByRole("listitem")).toHaveLength(1);
    expect(within(lark).getByRole("link", { name: "Larkspur dried chickpeas" })).toHaveAttribute(
      "href",
      `/catalog/products/${chickpeaProduct.id}`,
    );
    expect(lark).toHaveTextContent("3 for $5");
    expect(lark).toHaveTextContent("through Nov 10, 2026");
    const jun = within(card).getByRole("region", { name: "Juniper Grocers" });
    expect(jun).toHaveTextContent("$2.49/lb, card price");
    expect(calls.find((c) => c.path.startsWith("/flyer-offers/current"))?.path).toContain(
      `home_base_id=${kitchen.id}`,
    );
  });

  it("shows nothing at all when the kitchen has no linked offers (X5)", async () => {
    mount("/", []);
    await screen.findByRole("heading", { name: "Recent purchases" });
    expect(screen.queryByRole("region", { name: "This week's offers" })).not.toBeInTheDocument();
    expect(screen.queryByText(/offer/i)).not.toBeInTheDocument();
  });
});

describe("the list's offer mark (UI-9.2)", () => {
  it("marks an ingredient item by any of its products' offers, a product item only by its own", async () => {
    mount("/plan/list", offers);
    const rows = await screen.findAllByTestId("list-item");
    const [peas, milk] = rows;
    expect(await within(peas).findByText("On offer at Larkspur Market")).toHaveClass(
      "text-green-800",
    );
    expect(peas).toHaveTextContent("Dried Chickpeas, 3 for $5, through Nov 10, 2026");
    expect(within(peas).queryByText(/Juniper/)).not.toBeInTheDocument();
    expect(within(milk).getByText("On offer at Juniper Grocers")).toBeInTheDocument();
  });

  it("leaves checked items, and items without an offer, unmarked", async () => {
    const checked = item({ ...chickpeas, checked_at: "2026-11-05T10:00:00Z", checked_by: null });
    mount("/plan/list", [offers[0]], {
      "GET /plan/list": () => jsonResponse(200, list({ unchecked: [oatMilk], checked: [checked] })),
    });
    await screen.findAllByTestId("list-item");
    expect(screen.queryByText(/On offer at/)).not.toBeInTheDocument();
  });
});
