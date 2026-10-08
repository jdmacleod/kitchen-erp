import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import { flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// 2P, part 3: possible duplicates on the Products page (03, criteria 106–107; UI-6.18).

const twin: Product = { ...flourProduct, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d01", name: "strong white flour", brand: null };

function routes(pairs: () => { a: Product; b: Product; reasons: string[] }[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct, twin], next_cursor: null }),
    "GET /products/duplicates": () => jsonResponse(200, { items: pairs() }),
  };
}

describe("possible duplicates", () => {
  it("shows each pair side by side, and Not the same is remembered", async () => {
    let pairs = [{ a: flourProduct, b: twin, reasons: ["words"] }];
    const calls = mockApi({
      ...routes(() => pairs),
      "POST /products/duplicates/distinct": () => {
        pairs = [];
        return new Response(null, { status: 204 });
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/products?duplicates=1");
    const section = await screen.findByRole("region", { name: "Possible duplicates" });
    const card = await within(section).findByRole("group", { name: /and strong white flour/ });
    expect(within(card).getAllByRole("button", { name: "Keep this one" })).toHaveLength(2);
    await user.click(within(card).getByRole("button", { name: "Not the same" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/products/duplicates/distinct")?.body).toEqual({ a: flourProduct.id, b: twin.id }));
    expect(await within(section).findByText("No possible duplicates")).toBeInTheDocument();
  });

  it("opens the merge with the product to keep already chosen", async () => {
    mockApi({
      ...routes(() => [{ a: flourProduct, b: twin, reasons: ["words"] }]),
      [`POST /products/${twin.id}/merge/preview`]: () =>
        jsonResponse(200, {
          survivor_id: flourProduct.id,
          loser_id: twin.id,
          survivor_name: flourProduct.name,
          loser_name: twin.name,
          prices: 2,
          listings: 0,
          codes: 0,
          photos: 0,
          aliases: 0,
          survivor_pack_unit: null,
          loser_pack_unit: null,
          other_dimension_prices: 0,
          other_dimension_units: [],
          prices_needing_bridge: 0,
          compare_unit: "g",
        }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products?duplicates=1");
    const card = await screen.findByRole("group", { name: /and strong white flour/ });
    await user.click(within(card).getAllByRole("button", { name: "Keep this one" })[0]);
    expect(await screen.findByRole("heading", { name: /Merge strong white flour into/ })).toBeInTheDocument();
    expect(await screen.findByTestId("merge-moves")).toHaveTextContent("2 prices go to");
  });
});
