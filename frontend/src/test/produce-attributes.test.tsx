import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient, Product } from "../api/catalog";
import { keepFields } from "../lib/productAttributes";
import { choiceCategory, emptyProductValues } from "../pages/catalog/ProductForm";
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

  it("offers other foods organic alone", async () => {
    mockApi(routes(flourProduct, flour));
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });
    await within(form).findByLabelText("Pack quantity");
    expect(within(form).getByLabelText("Organic")).toBeInTheDocument();
    expect(within(form).queryByLabelText("Size grade")).not.toBeInTheDocument();
    expect(within(form).queryByLabelText("Variety")).not.toBeInTheDocument();
  });

  it("records a meat's species, bone and lean ratio, and asks for the species first", async () => {
    const thigh: Ingredient = {
      ...peppers,
      name: "hen thigh",
      category: "meat",
      category_key: "meat",
    };
    const meatProduct: Product = {
      ...pepperProduct,
      ingredient: { ...pepperProduct.ingredient, category: "meat", category_key: "meat" },
      name: "Hen thighs",
    };
    const calls = mockApi({
      ...routes(meatProduct, thigh),
      [`PATCH /products/${flourProductId}`]: () => jsonResponse(200, meatProduct),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });

    await user.selectOptions(await within(form).findByLabelText("Bone"), "boneless");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(
      await within(form).findByText("Choose the species before its other details."),
    ).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);

    await user.selectOptions(within(form).getByLabelText("Species"), "chicken");
    await user.type(within(form).getByLabelText("Lean %"), "85");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      attributes: { bone: "boneless", species: "chicken", lean_pct: 85 },
    });
  });

  it("keeps only what a product's new category shows", () => {
    const meat = { species: "chicken", bone: "boneless", organic: true, primal: "leg" };
    expect(keepFields("produce", meat)).toEqual({ organic: true });
    expect(keepFields("meat", { variety: "Lantern Gold", organic: true })).toEqual({
      organic: true,
    });
  });

  it("shows a standard-list ingredient's details, and a new one's organic alone", () => {
    const standard = emptyProductValues({
      kind: "standard",
      key: "hen-thigh",
      name: "hen thigh",
      canonical_unit: "g",
      category: "meat",
      category_key: "meat",
    });
    expect(choiceCategory(standard)).toBe("meat");
    expect(
      choiceCategory(emptyProductValues({ kind: "new", name: "tarn", category: "Meat" })),
    ).toBe(null);
  });
});
