import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Found by /devex-review on 2026-10-06: labelling products meant leaving the product
// and typing a category name; nothing listed the products still without one.

describe("categories from the products pages", () => {
  it("filters the products whose ingredient has no category", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const group = await screen.findByRole("group", { name: "Category" });
    await user.click(within(group).getByRole("button", { name: "No category" }));
    await waitFor(() => expect(calls.some((c) => c.path.split("?")[0] === "/products" && c.query.get("category") === "none")).toBe(true));
    expect(within(group).getByRole("button", { name: "No category" })).toHaveAttribute("aria-pressed", "true");
  });

  it("sets the ingredient's category from the product's page", async () => {
    const product = { ...flourProduct, ingredient: { ...flourProduct.ingredient, category: null, category_key: null } };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products-helper": () => jsonResponse(200, { configured: false }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [], primary_image_id: null }),
      "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`PATCH /ingredients/${product.ingredient.id}`]: () => jsonResponse(200, { ...product.ingredient, category: "pantry", category_key: "pantry" }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const card = (await screen.findByRole("heading", { name: "Category" })).parentElement!;
    await user.selectOptions(within(card).getByLabelText("Category"), "Pantry");
    expect(await within(card).findByText("Saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ category: "pantry" });
  });

  it("gives a new ingredient its category in Add product", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingredients/search": () => jsonResponse(200, { items: [] }),
      "GET /products-helper": () => jsonResponse(200, { configured: false }),
      "POST /products": () => jsonResponse(201, { ...flourProduct, name: "Snap peas" }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    await screen.findByRole("heading", { name: "Products" });
    await user.click(screen.getAllByRole("button", { name: "Add product" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Add product" });
    await user.type(within(dialog).getByRole("combobox", { name: "Ingredient" }), "snap peas");
    await user.click(await within(dialog).findByRole("option", { name: /Create new ingredient/ }));
    await user.selectOptions(within(dialog).getByLabelText("Category"), "Produce");
    await user.type(within(dialog).getByLabelText("Name"), "Snap peas");
    await user.click(within(dialog).getByRole("button", { name: "Add product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")?.body as { ingredient: unknown }).ingredient).toEqual({ name: "snap peas", category: "produce" });
  });
});
