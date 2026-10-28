import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient, Product } from "../api/catalog";
import { flour, flourId, flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Organic and a size grade on produce products. The produce here is invented.

const peppers: Ingredient = {
  ...flour,
  name: "lantern peppers",
  category: "produce",
  category_key: "produce",
  measures: [],
};
const pepperProduct: Product = {
  ...flourProduct,
  ingredient: { ...flourProduct.ingredient, category: "produce", category_key: "produce" },
  brand: null,
  name: "Lantern peppers",
  pack_qty: null,
  pack_unit: null,
  barcode: null,
  attributes: {},
};

function routes(product: Product, ingredient: Ingredient) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
  };
}

describe("produce attributes", () => {
  it("marks produce organic and graded, and saves only what changed", async () => {
    const calls = mockApi({
      ...routes(pepperProduct, peppers),
      [`PATCH /products/${flourProductId}`]: () =>
        jsonResponse(200, { ...pepperProduct, attributes: { organic: true, grade: "large" } }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });

    await user.click(await within(form).findByLabelText("Organic"));
    await user.selectOptions(within(form).getByLabelText("Size grade"), "large");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      attributes: { organic: true, grade: "large" },
    });
  });

  it("keeps a variety when organic changes", async () => {
    const honeyed = { ...pepperProduct, attributes: { variety: "Lantern Gold" } };
    const calls = mockApi({
      ...routes(honeyed, peppers),
      [`PATCH /products/${flourProductId}`]: () =>
        jsonResponse(200, { ...honeyed, attributes: { variety: "Lantern Gold", organic: true } }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });

    await user.click(await within(form).findByLabelText("Organic"));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      attributes: { variety: "Lantern Gold", organic: true },
    });
  });

  it("leaves other foods without the fields", async () => {
    mockApi(routes(flourProduct, flour));
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });
    await within(form).findByLabelText("Pack quantity");
    expect(within(form).queryByLabelText("Organic")).not.toBeInTheDocument();
    expect(within(form).queryByLabelText("Size grade")).not.toBeInTheDocument();
  });
});
