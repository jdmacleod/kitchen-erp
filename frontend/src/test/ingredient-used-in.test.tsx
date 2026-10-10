import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngredientRecipeUse } from "../api/recipes";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import { quietInbox } from "./recipe-fixtures";

function use(n: number, over: Partial<IngredientRecipeUse> = {}): IngredientRecipeUse {
  return { id: `r-${n}`, title: `Dish ${String(n).padStart(2, "0")}`, path: `dishes/dish_${n}.cook`, status: "ok", quantities: [`${n} cups`], ...over };
}

function mount(routes: Record<string, RouteHandler> = {}, features: string[] = ["catalog", "shop", "cook"]) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok", features }),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
    [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: [], stale_after_days: 90 }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
    ...routes,
  });
  renderApp(`/catalog/ingredients/${flourId}`);
  return calls;
}

describe("the ingredient hub's Used in card (UI-7.14, criterion 33)", () => {
  it("lists the recipes using the ingredient with the quantity as written, ten then All {n} recipes", async () => {
    const items = Array.from({ length: 12 }, (_, i) => use(i + 1));
    items[1] = use(2, { quantities: ["2 cups", null, "1 tbsp"], status: "missing" });
    mount({ [`GET /ingredients/${flourId}/recipes`]: () => jsonResponse(200, { items, total: 12 }) });
    const card = await screen.findByTestId("used-in-card");
    expect(within(card).getByRole("heading", { name: "Used in" })).toBeInTheDocument();
    // Under Products in the right column.
    expect(card.previousElementSibling).toHaveAttribute("aria-labelledby", "ingredient-products");
    const list = within(card).getByRole("list", { name: "Recipes using this ingredient" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(within(list).getByRole("link", { name: "Dish 01" })).toHaveAttribute("href", "/cook/recipes/r-1");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("1 cups");
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("2 cups · 1 tbsp");
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("Missing");
    expect(within(list).queryByRole("link", { name: "Dish 11" })).not.toBeInTheDocument();

    await userEvent.setup().click(within(card).getByRole("button", { name: "All 12 recipes" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(12);
    expect(within(card).queryByRole("button", { name: /All .* recipes/ })).not.toBeInTheDocument();
  });

  it("is omitted when no recipe uses the ingredient", async () => {
    mount({ [`GET /ingredients/${flourId}/recipes`]: () => jsonResponse(200, { items: [], total: 0 }) });
    expect(await screen.findByRole("heading", { name: "Products" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("list", { name: "Products of this ingredient" })).toBeInTheDocument());
    expect(screen.queryByTestId("used-in-card")).not.toBeInTheDocument();
  });

  it("is omitted, and never asked for, when the Cook section is not built", async () => {
    const calls = mount({}, ["catalog", "shop"]);
    await waitFor(() => expect(screen.getByRole("list", { name: "Products of this ingredient" })).toBeInTheDocument());
    expect(screen.queryByTestId("used-in-card")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/recipes"))).toBe(false);
  });
});
