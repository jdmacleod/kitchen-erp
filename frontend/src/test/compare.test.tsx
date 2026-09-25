import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CompareResult } from "../api/pricebook";
import { flour, flourId, flourProductId, units } from "./catalog-fixtures";
import { chainVendor, chainVendorId, marketVendor, marketVendorId, chainLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

const result: CompareResult = {
  vendors: [
    { id: chainVendorId, name: chainVendor.name },
    { id: marketVendorId, name: marketVendor.name },
  ],
  rows: [
    {
      ingredient_id: flourId,
      ingredient_name: flour.name,
      canonical_unit: "g",
      // Only the chain has a price; the market's key is absent, which means unknown.
      cells: {
        [chainVendorId]: {
          product_id: flourProductId,
          product_name: "All-Purpose Flour",
          brand: "Millstone",
          quality_rating: 4,
          location_id: chainLocationId,
          location_name: "Millstone Harbour",
          observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7001",
          observed_at: "2026-09-18T15:00:00Z",
          is_promo: false,
          norm_unit_price: "0.002200",
          norm_unit: "g",
          stale: false,
          cheapest: true,
          age_days: "3",
        },
      },
    },
  ],
  stale_thresholds: { fresh: 14, refrigerated: 45, shelf_stable: 120 },
};

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? [flour] : [], next_cursor: null }),
    "POST /price-book/compare": () => jsonResponse(200, result),
  };
}

describe("comparison matrix", () => {
  it("marks the cheapest cell and leaves an unknown cell blank rather than zero", async () => {
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/compare");

    expect(await screen.findByText("Pick ingredients to compare")).toBeInTheDocument();
    await user.type(screen.getByRole("combobox", { name: "Add an ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));

    const table = await screen.findByRole("table", { name: "Price comparison" });
    expect(within(table).getByRole("columnheader", { name: "Millstone Market" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Pier Farmers Market" })).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/price-book/compare");
    expect(post?.body).toEqual({ ingredient_ids: [flourId] });

    const cheapest = screen.getByTestId(`cell-${flourId}-${chainVendorId}`);
    expect(cheapest).toHaveTextContent("$0.0022/g");
    expect(within(cheapest).getByText("cheapest")).toBeInTheDocument();
    expect(within(cheapest).getByText(/\$0\.0022\/g/)).toHaveClass("font-bold");
    expect(cheapest).toHaveTextContent("3 days");

    const unknown = screen.getByTestId(`cell-${flourId}-${marketVendorId}`);
    expect(unknown).toBeEmptyDOMElement();
    expect(unknown).toHaveAttribute("title", "No price known");
    expect(screen.queryByText(/\$0\.00\b/)).not.toBeInTheDocument();
  });

  it("re-queries with the filters and lists the chosen ingredients with a remove button", async () => {
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/compare");

    await user.type(await screen.findByRole("combobox", { name: "Add an ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));
    await screen.findByRole("table", { name: "Price comparison" });
    expect(within(screen.getByRole("list", { name: "Chosen ingredients" })).getByText("all-purpose flour")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Exclude stale" }));
    await user.selectOptions(screen.getByLabelText("Minimum quality"), "4");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(3));
    expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ ingredient_ids: [flourId], exclude_stale: true, min_quality: 4 });

    await user.click(screen.getByRole("button", { name: "Remove all-purpose flour" }));
    expect(await screen.findByText("Pick ingredients to compare")).toBeInTheDocument();
  });

  it("says when the chosen ingredients have no prices, and offers a shelf price (G11)", async () => {
    mockApi({ ...baseRoutes(), "POST /price-book/compare": () => jsonResponse(200, { ...result, vendors: [], rows: result.rows.map((r) => ({ ...r, cells: [] })) }) });
    const user = userEvent.setup();
    renderApp("/shop/compare");

    await user.type(await screen.findByRole("combobox", { name: "Add an ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));

    const empty = await screen.findByRole("region", { name: "No prices to compare yet" });
    expect(empty).toHaveTextContent("This ingredient has no prices that pass the filters.");
    expect(within(empty).getByRole("link", { name: "Log a shelf price" })).toHaveAttribute("href", "/shop/shelf-prices");
    expect(screen.queryByRole("table")).toBeNull();
  });
});
