import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Product, ProductMerge } from "../api/catalog";
import { mergeSummary, mergeWarnings } from "../components/catalog/ProductMergePanel";
import { flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const keepId = hits[1].id;

const preview: ProductMerge = {
  survivor_id: keepId,
  loser_id: flourProductId,
  survivor_name: "Bread Flour",
  loser_name: "All-Purpose Flour",
  prices: 3,
  listings: 0,
  codes: 1,
  photos: 0,
  aliases: 1,
  lines: 2,
  survivor_pack_unit: "kg",
  loser_pack_unit: "lb",
  compare_unit: "g",
  other_dimension_prices: 2,
  other_dimension_units: ["fl_oz"],
  prices_needing_bridge: 0,
};

function routes(product: () => Product, extra: Record<string, (body?: unknown) => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, product()),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [] }),
    "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: hits }),
    ...extra,
  };
}

describe("merging a product (issue 179)", () => {
  it("says what moves and warns about units before merging", () => {
    expect(mergeSummary(preview)).toBe("3 prices, 1 code and 1 receipt wording");
    expect(mergeWarnings(preview)).toEqual([
      "Its pack is in lb; Bread Flour's is in kg, and that pack is kept.",
      "2 prices are in fl oz. They need a density before they compare in g.",
    ]);
  });

  it("chooses the product to keep, previews, and merges in place", async () => {
    let product: Product = flourProduct;
    const calls = mockApi(
      routes(() => product, {
        [`POST /products/${flourProductId}/merge/preview`]: () => jsonResponse(200, preview),
        [`POST /products/${flourProductId}/merge`]: () => {
          product = { ...product, active: false, merged_into: keepId, updated_at: "2026-10-06T00:00:00Z" };
          return jsonResponse(200, preview);
        },
        [`GET /products/${keepId}`]: () =>
          jsonResponse(200, { ...flourProduct, id: keepId, brand: "Riverbend", name: "Bread Flour" }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    await user.click(await screen.findByRole("button", { name: "Merge into…" }));
    const box = await screen.findByRole("combobox", { name: "Product to keep" });
    await user.type(box, "flour");
    await user.click(await screen.findByRole("option", { name: /Bread Flour/ }));

    const confirm = await screen.findByRole("group", { name: /Merge Millstone All-Purpose Flour into Riverbend Bread Flour/ });
    expect(await within(confirm).findByTestId("merge-moves")).toHaveTextContent("3 prices, 1 code and 1 receipt wording go to Bread Flour.");
    expect(within(confirm).getByTestId("merge-warnings")).toHaveTextContent("They need a density");
    await waitFor(() => expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus());

    await user.click(within(confirm).getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/merge"))?.body).toEqual({ survivor_id: keepId }));
    expect(await screen.findByRole("link", { name: "Open Bread Flour" })).toBeInTheDocument();
    expect(await screen.findByText(/Its prices, codes, photos and receipt wordings are there now/)).toBeInTheDocument();
    // A merged product offers neither merge nor reactivation.
    expect(screen.queryByRole("button", { name: "Merge into…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("refuses the product itself as the one to keep", async () => {
    mockApi(routes(() => flourProduct));
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    await user.click(await screen.findByRole("button", { name: "Merge into…" }));
    await user.type(await screen.findByRole("combobox", { name: "Product to keep" }), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    expect(await screen.findByText("Choose another product to keep.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /Merge .* into/ })).not.toBeInTheDocument();
  });
});
