import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import { flour, flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function baseRoutes(products: () => Product[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products": () => jsonResponse(200, { items: products(), next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: hits }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
  };
}

describe("products", () => {
  it("creates a product with an inline new ingredient in one request", async () => {
    let products: Product[] = [];
    const created: Product = {
      ...flourProduct,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d09",
      ingredient: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09", name: "rolled oats", canonical_unit: "g", active: true, category: null, category_key: null },
      brand: null,
      name: "Rolled Oats",
      pack_qty: "1",
      pack_unit: "kg",
      barcode: null,
      quality_rating: 3,
    };
    const calls = mockApi({
      ...baseRoutes(() => products),
      "POST /products": () => {
        products = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    expect(await screen.findByText("No products yet")).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Add a product" });

    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "rolled oats");
    const option = await within(form).findByRole("option", { name: /Create new ingredient/ });
    await user.click(option);
    expect(screen.getByTestId("new-product-ingredient-choice")).toHaveTextContent("rolled oats");

    await user.type(within(form).getByLabelText("Name"), "Rolled Oats");
    await user.type(within(form).getByLabelText("Pack quantity"), "1");
    await user.selectOptions(within(form).getByLabelText("Pack unit"), "kg");
    await user.click(within(form).getByRole("radio", { name: "3" }));
    await user.click(within(form).getByRole("button", { name: "Create product" }));

    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Added Rolled Oats.");
    expect(within(notice).getByRole("link", { name: "Open it" })).toHaveAttribute("href", `/catalog/products/${created.id}`);
    const post = calls.find((c) => c.method === "POST" && c.path === "/products");
    expect(post?.body).toEqual({
      name: "Rolled Oats",
      ingredient: { name: "rolled oats" },
      pack_qty: "1",
      pack_unit: "kg",
      quality_rating: 3,
    });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    // The ingredient stays selected for the next product.
    expect(screen.getByTestId("new-product-ingredient-choice")).toHaveTextContent("rolled oats");
  });

  it("refuses a pack quantity without a unit before calling the API", async () => {
    const calls = mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients": () => jsonResponse(200, { items: [flour], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    await screen.findByText("No products yet");
    const form = screen.getByRole("form", { name: "Add a product" });
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Name"), "Flour");
    await user.type(within(form).getByLabelText("Pack quantity"), "5");
    await user.click(within(form).getByRole("button", { name: "Create product" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("both a pack quantity and a pack unit");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the server's conflict message for a taken barcode", async () => {
    mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients": () => jsonResponse(200, { items: [flour], next_cursor: null }),
      "POST /products": () => errorResponse(409, "barcode_taken", "barcode taken"),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    await screen.findByText("No products yet");
    const form = screen.getByRole("form", { name: "Add a product" });
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Name"), "Flour");
    await user.click(within(form).getByRole("button", { name: "Create product" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Another product already has that barcode.");
  });

  it("edits a product, clearing the pack and confirming a density override", async () => {
    let product: Product = { ...flourProduct, density_override: "0.6", density_override_source: "label" };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`PATCH /products/${flourProductId}`]: () => {
        product = { ...product, pack_qty: null, pack_unit: null, name: "AP Flour", updated_at: "2026-03-03T00:00:00Z" };
        return jsonResponse(200, product);
      },
      [`POST /products/${flourProductId}/density-override/confirm`]: () => {
        product = { ...product, density_override_confirmed: true };
        return jsonResponse(200, product);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    expect(await screen.findByRole("heading", { name: "Millstone All-Purpose Flour" })).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Edit product" });
    expect(within(form).getByLabelText("Pack unit")).toHaveValue("lb");

    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "AP Flour");
    await user.clear(within(form).getByLabelText("Pack quantity"));
    await user.selectOptions(within(form).getByLabelText("Pack unit"), "");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Millstone AP Flour" })).toBeInTheDocument());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.body).toEqual({ name: "AP Flour", clear_pack: true });

    await user.click(screen.getByRole("button", { name: "Confirm density override" }));
    await waitFor(() => expect(screen.getByTestId("density-override-summary")).toHaveTextContent("confirmed"));
    expect(screen.queryByRole("button", { name: "Confirm density override" })).not.toBeInTheDocument();
  });
});
