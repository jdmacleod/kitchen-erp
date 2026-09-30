import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngredientMatch, Product } from "../api/catalog";
import { flour, flourProduct, hits, ingredientMatch, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented ingredients for the picker (1G, UI-5.1, UI-5.2).
const scallion = ingredientMatch(
  { ...flour, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5c01", name: "scallion", category: "produce", category_key: "produce" },
  { matched_spelling: "green onion", exact: true },
);
const onionPowder = ingredientMatch({ ...flour, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5c02", name: "green onion powder" });
const greenBean: IngredientMatch = {
  kind: "standard",
  id: null,
  key: "green-beans",
  name: "green beans",
  canonical_unit: "g",
  active: true,
  category: "produce",
  category_key: "produce",
  matched_spelling: null,
  exact: false,
};

function routes(search: (call: RecordedCall) => IngredientMatch[], onPost?: (call: RecordedCall) => void) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: hits }),
    "GET /ingredients/search": (call: RecordedCall) => jsonResponse(200, { items: search(call) }),
    "POST /products": (call: RecordedCall) => {
      onPost?.(call);
      const created: Product = { ...flourProduct, name: "Bundle" };
      return jsonResponse(201, created);
    },
  };
}

async function openAddDrawer(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Products" });
  await user.click(screen.getAllByRole("button", { name: "Add product" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "Add product" });
  return within(dialog).getByRole("form", { name: "Add product" });
}

describe("ingredient picker (1G)", () => {
  it("lists a spelling match first, the standard list as a named group, and no Create new for a known name", async () => {
    const calls = mockApi(routes(() => [scallion, onionPowder, greenBean]));
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);

    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "green on");
    const listbox = await within(form).findByRole("listbox", { name: "Ingredients" });
    await waitFor(() => expect(within(listbox).getAllByRole("option")).toHaveLength(3));
    const options = within(listbox).getAllByRole("option");
    expect(options[0]).toHaveAccessibleName("green onion, another name for scallion");
    expect(options[0]).toHaveTextContent("matches green onion");
    expect(options[1]).toHaveTextContent("green onion powder");
    const group = within(listbox).getByRole("group", { name: "From the standard list" });
    expect(within(group).getByRole("option")).toHaveTextContent("green beans");
    expect(within(listbox).queryByRole("option", { name: /Create new ingredient/ })).not.toBeInTheDocument();

    const search = calls.find((c) => c.path.startsWith("/ingredients/search"));
    expect(search?.query.get("include_standard")).toBe("true");
  });

  it("offers Create new when nothing is named exactly", async () => {
    mockApi(routes(() => [onionPowder]));
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "ramp");
    expect(await within(form).findByRole("option", { name: /Create new ingredient “ramp”/ })).toBeInTheDocument();
  });

  it("says which spelling matched once an ingredient is chosen", async () => {
    mockApi(routes(() => [scallion]));
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "green onion");
    await user.click(await within(form).findByRole("option", { name: "green onion, another name for scallion" }));
    expect(screen.getByTestId("new-product-ingredient-choice")).toHaveTextContent("scallion · g");
    expect(screen.getByTestId("new-product-ingredient-note")).toHaveTextContent("matched green onion");
  });

  it("creates nothing until save, then sends the standard key with the product", async () => {
    const posts: RecordedCall[] = [];
    mockApi(routes(() => [greenBean], (call) => posts.push(call)));
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "green bea");
    await user.click(await within(form).findByRole("option", { name: /green beans/ }));
    expect(screen.getByTestId("new-product-ingredient-note")).toHaveTextContent("New, from the standard list");
    expect(posts).toHaveLength(0);

    await user.type(within(form).getByLabelText("Name"), "Bundle");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      name: "Bundle",
      ingredient: { name: "green beans", standard_key: "green-beans" },
    });
  });

  it("asks for no standard names where it cannot create (Compare)", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /ingredients/search": () => jsonResponse(200, { items: [onionPowder] }),
      "GET /compare": () => jsonResponse(200, { ingredients: [], locations: [], cells: [] }),
    });
    const user = userEvent.setup();
    renderApp("/shop/compare");
    await user.type(await screen.findByRole("combobox", { name: "Add an ingredient" }), "onion");
    await screen.findByRole("option", { name: /green onion powder/ });
    const search = calls.find((c) => c.path.startsWith("/ingredients/search"));
    expect(search?.query.get("include_standard")).toBe("false");
  });
});
