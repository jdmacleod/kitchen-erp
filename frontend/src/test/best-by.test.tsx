import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import type { Purchase, PurchaseLine } from "../api/purchases";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { manualPurchase, purchaseId } from "./purchase-fixtures";

// 2Q (docs/spec/15): a committed purchase shows each line's best-by date and its
// source; a freezer line reads as quality; a printed use-by date is sent as such.

const [first, second] = manualPurchase.lines;
const fridgeLine: PurchaseLine = { ...first, stored_in: "fridge", best_by: "2026-09-20", best_by_source: "inferred" };
const freezerLine: PurchaseLine = { ...second, stored_in: "freezer", best_by: "2027-06-16", best_by_source: "inferred" };
const withDates: Purchase = { ...manualPurchase, lines: [fridgeLine, freezerLine] };

function routes(purchase: () => Purchase) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, purchase()),
  };
}

describe("best-by dates", () => {
  it("shows each line's date and source, and a freezer line as quality", async () => {
    mockApi(routes(() => withDates));
    renderApp(`/shop/purchases/${purchaseId}`);
    const table = await screen.findByRole("table", { name: "Lines" });
    expect(within(table).getByRole("columnheader", { name: "Best by" })).toBeInTheDocument();
    const rows = within(table).getAllByRole("row");
    // The date is the household's calendar date, never shifted by zone.
    expect(rows[1]).toHaveTextContent("Sep 20, 2026");
    expect(rows[1]).toHaveTextContent("inferred");
    expect(rows[2]).toHaveTextContent("Frozen: best quality by Jun 16, 2027");
  });

  it("sends a printed use-by date for a line", async () => {
    let purchase = withDates;
    const calls = mockApi({
      ...routes(() => purchase),
      [`PUT /purchases/${purchaseId}/lines/${fridgeLine.id}/keeping`]: () => {
        purchase = { ...purchase, lines: [{ ...fridgeLine, best_by: "2026-09-22", best_by_source: "printed" }, freezerLine] };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);
    const table = await screen.findByRole("table", { name: "Lines" });
    const row = within(table).getAllByRole("row")[1];
    await user.click(within(row).getByText("Change"));
    const date = within(row).getByLabelText("Best by");
    await user.type(date, "2026-09-22");
    await user.click(within(row).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveTextContent("printed"));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ date: "use_by", best_by: "2026-09-22" });
  });
});

describe("keep times on the ingredient page", () => {
  const chicken: Ingredient = { ...flour, perishability: "fresh", keep_room_days: null, keep_fridge_days: 1, keep_freezer_days: 270, stored_in: "fridge" };

  it("shows them, and sends an edited time as whole days", async () => {
    let ingredient = chicken;
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient),
      [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: [], stale_after_days: 90 }),
      "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
      [`PATCH /ingredients/${flourId}`]: () => {
        ingredient = { ...ingredient, keep_fridge_days: 2 };
        return jsonResponse(200, ingredient);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);
    expect(await screen.findByText("Keeps 1 day in the fridge, 270 days in the freezer")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit details" })[0]);
    const fridge = screen.getByLabelText("Fridge");
    await user.clear(fridge);
    await user.type(fridge, "2");
    await user.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ keep_fridge_days: 2 });
  });
});
