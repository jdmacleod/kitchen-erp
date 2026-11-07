// Weekly flyers 2U-3 (spec 18; UI-9.3, UI-9.6): the offer queue with its Watched
// and Other offers, the printed price, and an ingredient's "Watch for offers".
// Every store, name and price here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import type { OfferGroup, OfferQueue, QueuedOffer } from "../api/flyerOffers";
import { offerPriceText } from "../lib/offers";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const vendor = { id: "01000000-0000-7000-8000-00000000f001", name: "Larkspur Market" };

function offer(extra: Partial<QueuedOffer> = {}): QueuedOffer {
  return {
    id: "01000000-0000-7000-8000-00000000f101",
    location: { id: "01000000-0000-7000-8000-00000000f201", name: "Larkspur East" },
    name_as_printed: "Green Lentils",
    price_basis: "each",
    price: "2.4900",
    basis_qty: null,
    discount: null,
    size_qty: "16",
    size_unit: "oz",
    requires_loyalty: false,
    limit_qty: null,
    valid_from: "2026-11-04",
    valid_to: "2026-11-10",
    ...extra,
  };
}

function group(extra: Partial<OfferGroup> = {}): OfferGroup {
  return {
    vendor,
    name_norm: "green lentils",
    name_as_printed: "Green Lentils",
    watched: true,
    suggestions: [
      {
        kind: "similar",
        product_id: flourProduct.id,
        ignore: false,
        label: "Larkspur Green Lentils",
        score: "0.8",
        brand_note: null,
      },
    ],
    offers: [offer()],
    first_seen_at: "2026-11-04T12:00:00Z",
    ...extra,
  };
}

function routes(queue: () => OfferQueue) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /flyer-offers/queue": () => jsonResponse(200, queue()),
  };
}

describe("the printed price", () => {
  it("reads as the circular prints it", () => {
    const base = { basis_qty: null, discount: null, requires_loyalty: false };
    expect(offerPriceText({ ...base, price_basis: "each", price: "2.5000" })).toBe("$2.50");
    expect(offerPriceText({ ...base, price_basis: "per_lb", price: "3.0000" })).toBe("$3/lb");
    expect(offerPriceText({ ...base, price_basis: "multi", price: "5.0000", basis_qty: "3" })).toBe(
      "3 for $5",
    );
    expect(offerPriceText({ ...base, price_basis: "bogo", price: null })).toBe("Buy one get one");
    expect(
      offerPriceText({ ...base, price_basis: "percent_off", price: null, discount: "20.00" }),
    ).toBe("20% off");
    expect(
      offerPriceText({ ...base, price_basis: "each", price: "1.99", requires_loyalty: true }),
    ).toBe("$1.99, card price");
  });
});

describe("the offer queue", () => {
  it("opens at Watched, counts both, and a suggestion answers every store's offer", async () => {
    let queue: OfferQueue = {
      watched: [group()],
      other: [group({ watched: false, name_norm: "rolled oats", name_as_printed: "Rolled Oats" })],
    };
    const calls = mockApi({
      ...routes(() => queue),
      "POST /flyer-offers/decide": () => {
        queue = { ...queue, watched: [] };
        return jsonResponse(200, { decided: 1 });
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/offers/match");

    expect(await screen.findByRole("heading", { name: "Match offers" })).toBeInTheDocument();
    const tabs = await screen.findByRole("group", { name: "Offers" });
    expect(within(tabs).getByRole("button", { name: "Watched 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(tabs).getByRole("button", { name: "Other offers 1" })).toBeInTheDocument();
    const card = screen.getByRole("group", { name: "Larkspur Market: Green Lentils" });
    expect(card).toHaveTextContent("$2.49 · 16 oz · Larkspur East · through Nov 10, 2026");

    await user.click(within(card).getByRole("button", { name: "Use Larkspur Green Lentils" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        vendor_id: vendor.id,
        name_norm: "green lentils",
        product_id: flourProduct.id,
      }),
    );
    expect(await screen.findByText("No watched offers to match")).toBeInTheDocument();
  });

  it("shows the other offers on their own tab, and Not wanted answers them", async () => {
    const queue: OfferQueue = {
      watched: [],
      other: [group({ watched: false, suggestions: [] })],
    };
    const calls = mockApi({
      ...routes(() => queue),
      "POST /flyer-offers/decide": () => jsonResponse(200, { decided: 1 }),
    });
    const user = userEvent.setup();
    renderApp("/shop/offers/match?tab=other");

    const card = await screen.findByRole("group", { name: "Larkspur Market: Green Lentils" });
    await user.click(within(card).getByRole("button", { name: "Not wanted" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        vendor_id: vendor.id,
        name_norm: "green lentils",
        ignore: true,
      }),
    );
  });
});

describe("watching an ingredient", () => {
  it("is a switch on its page that saves at once", async () => {
    let ingredient: Ingredient = { ...flour, watch: false };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient),
      [`GET /ingredients/${flourId}/offers`]: () =>
        jsonResponse(200, { items: [], stale_after_days: 90 }),
      "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
      [`PATCH /ingredients/${flourId}`]: (call) => {
        ingredient = { ...ingredient, ...(call.body as Partial<Ingredient>) };
        return jsonResponse(200, ingredient);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    const watch = await screen.findByRole("switch", { name: "Watch for offers" });
    expect(watch).toHaveAttribute("aria-checked", "false");
    await user.click(watch);
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Watch for offers" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ watch: true });
  });
});
