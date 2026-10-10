import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngredientMatch } from "../api/catalog";
import type { Recipe, RecipePin } from "../api/recipes";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { barleyProduct, cookHealth, emptyHistory, mountedStatus, quietInbox, recipeList, stewBody, stewCost, stewRecipe } from "./recipe-fixtures";

const celeryRoot: IngredientMatch = { kind: "ingredient", id: "i-celery-root", key: null, name: "celery root", canonical_unit: "g", active: true, category: "produce", category_key: "produce", matched_spelling: null, exact: false };

function mount(routes: Record<string, RouteHandler> = {}, recipe: () => Recipe = () => stewRecipe) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, recipe()),
    "GET /recipes/r-barley/cost": () => jsonResponse(200, stewCost()),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    "GET /ingredients/search": () => jsonResponse(200, { items: [celeryRoot] }),
    "GET /products": () => jsonResponse(200, { items: [barleyProduct], next_cursor: null }),
    ...routes,
  });
  renderApp("/cook/recipes/r-barley");
  return calls;
}

const costRows = async () => within(await screen.findByRole("main")).findAllByTestId("cost-row");
const pinCalls = (calls: RecordedCall[]) => calls.filter((c) => c.path.startsWith("/recipes/r-barley/pins/") && c.method !== "GET");

describe("the cost table's ingredient picker (UI-7.10, criterion 28)", () => {
  it("resolves an unmapped line from its combobox, refreshes the cost and shows the Notice", async () => {
    let costFetches = 0;
    const calls = mount({
      "GET /recipes/r-barley/cost": () => {
        costFetches += 1;
        return jsonResponse(200, stewCost());
      },
      "POST /recipes/resolve": () =>
        jsonResponse(200, { name_norm: "mystery root", action: "matched", ingredient: { id: "i-celery-root", name: "celery root", canonical_unit: "g", active: true, category: "produce", category_key: "produce" }, lines: 1, recipes: 1, remaining: 6 }),
    });
    const user = userEvent.setup();
    const [, , root] = await costRows();
    const before = costFetches;
    const box = within(root).getByRole("combobox", { name: "Choose an ingredient" });
    await user.type(box, "celery");
    await waitFor(() => expect(within(root).getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.path === "/recipes/resolve")).toHaveLength(1));
    expect(calls.find((c) => c.method === "POST" && c.path === "/recipes/resolve")?.body).toEqual({ name_norm: "mystery root", ingredient_id: "i-celery-root" });
    expect(await screen.findByTestId("notice")).toHaveTextContent("Resolved 'mystery root' as celery root in 1 recipe.");
    // The row and totals come from the refetched cost: no reload.
    await waitFor(() => expect(costFetches).toBeGreaterThan(before));
  });

  it("shows a collision at the row with Use {holder}", async () => {
    const calls = mount({
      "POST /recipes/resolve": (call) =>
        (call.body as { ingredient_id?: string }).ingredient_id === "i-celeriac"
          ? jsonResponse(200, { name_norm: "mystery root", action: "matched", ingredient: { id: "i-celeriac", name: "celeriac", canonical_unit: "g", active: true, category: null, category_key: null }, lines: 1, recipes: 1, remaining: 6 })
          : errorResponse(409, "alias_taken", "taken", { holder: "celeriac", holder_id: "i-celeriac" }),
    });
    const user = userEvent.setup();
    const [, , root] = await costRows();
    await user.type(within(root).getByRole("combobox", { name: "Choose an ingredient" }), "celery");
    await waitFor(() => expect(within(root).getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{ArrowDown}{Enter}");
    const collision = await within(root).findByTestId("alias-collision");
    expect(collision).toHaveTextContent("‘mystery root’ is already a spelling of celeriac.");
    await user.click(within(collision).getByRole("button", { name: "Use celeriac" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.path === "/recipes/resolve")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST" && c.path === "/recipes/resolve")[1].body).toEqual({ name_norm: "mystery root", ingredient_id: "i-celeriac" });
  });
});

describe("pins (UI-7.11, criterion 31)", () => {
  it("Pin product… offers only the line's ingredient's products, pins on Enter, then shows Pinned with Unpin", async () => {
    let pins: RecipePin[] = [];
    const calls = mount(
      {
        "PUT /recipes/r-barley/pins/pearl%20barley": (call) => {
          pins = [{ name_norm: "pearl barley", product_id: (call.body as { product_id: string }).product_id, product_name: "pearl barley 1 lb", brand: "Moonfield" }];
          return jsonResponse(200, { ...stewRecipe, pins });
        },
        "DELETE /recipes/r-barley/pins/pearl%20barley": () => {
          pins = [];
          return jsonResponse(204);
        },
      },
      () => ({ ...stewRecipe, pins }),
    );
    const user = userEvent.setup();
    const [barley, onion, root] = await costRows();
    // Unmapped and negligible lines offer no pin; resolved ones do.
    expect(within(root).queryByRole("button", { name: "Pin product…" })).not.toBeInTheDocument();
    await user.click(within(barley).getByRole("button", { name: "Pin product…" }));
    const box = within(barley).getByRole("combobox", { name: "Pin product" });
    expect(box).toHaveFocus();
    const listbox = await within(barley).findByRole("listbox", { name: "Products" });
    await waitFor(() => expect(within(listbox).getAllByRole("option")).toHaveLength(1));
    expect(listbox).toHaveTextContent("Moonfield pearl barley 1 lb");
    const productCall = calls.find((c) => c.method === "GET" && c.path.startsWith("/products?"));
    expect(productCall?.query.get("ingredient_id")).toBe("i-pearl-barley");

    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(pinCalls(calls)).toHaveLength(1));
    expect(pinCalls(calls)[0]).toMatchObject({ method: "PUT", body: { product_id: "p-barley-1" } });
    const pinned = await within(barley).findByTestId("pinned");
    expect(pinned).toHaveTextContent("Pinned · Moonfield pearl barley 1 lb");
    // Focus moves to the next line that can take a pin.
    await waitFor(() => expect(within(onion).getByRole("button", { name: "Pin product…" })).toHaveFocus());

    await user.click(within(pinned).getByRole("button", { name: "Unpin" }));
    await waitFor(() => expect(pinCalls(calls).map((c) => c.method)).toEqual(["PUT", "DELETE"]));
    expect(await within(barley).findByRole("button", { name: "Pin product…" })).toBeInTheDocument();
  });

  it("a refused pin shows its reason at the row", async () => {
    mount({
      "PUT /recipes/r-barley/pins/pearl%20barley": () => errorResponse(409, "product_does_not_fulfil", "That product is not of the line's ingredient.", { product_ingredient: "flour", line_ingredient: "pearl barley" }),
    });
    const user = userEvent.setup();
    const [barley] = await costRows();
    await user.click(within(barley).getByRole("button", { name: "Pin product…" }));
    await waitFor(() => expect(within(barley).getAllByRole("option")).toHaveLength(1));
    await user.keyboard("{ArrowDown}{Enter}");
    expect(await within(barley).findByTestId("pin-refused")).toHaveTextContent("That product is not of the line's ingredient. It is flour; the line is pearl barley.");
    expect(within(barley).getByRole("button", { name: "Pin product…" })).toBeInTheDocument();
  });

  it("Escape puts the pin picker away and the shortcuts are listed under the table", async () => {
    mount();
    const user = userEvent.setup();
    const [barley] = await costRows();
    await user.click(within(barley).getByRole("button", { name: "Pin product…" }));
    await user.keyboard("{Escape}");
    expect(within(barley).queryByRole("combobox", { name: "Pin product" })).not.toBeInTheDocument();
    expect(screen.getByTestId("cost-shortcuts")).toHaveTextContent("Enter chooses");
  });
});

describe("the rendered recipe (10, Rendered recipe)", () => {
  it("renders numbered steps from the body: ingredients in weight 600 with quantity, cookware and timers in neutral, notes in parentheses", async () => {
    mount({}, () => ({ ...stewRecipe, body: stewBody }));
    const main = await screen.findByRole("main");
    const recipe = await within(main).findByRole("region", { name: "Recipe" });
    const steps = await within(recipe).findByRole("list", { name: "Steps" });
    expect(steps.tagName).toBe("OL");
    expect(within(steps).getAllByRole("listitem")).toHaveLength(1);
    expect(steps).toHaveTextContent("Rinse the pearl barley 1 cup (rinsed) and soften the onion 1 in a stock pot.");
    const barley = within(steps).getAllByTestId("step-ingredient")[0];
    expect(barley.querySelector(".font-semibold")).toHaveTextContent("pearl barley");
    expect(barley).toHaveAttribute("data-seq", "1");
    expect(within(steps).getByTestId("step-cookware")).toHaveClass("text-neutral-600");

    expect(within(recipe).getByRole("heading", { name: "Broth" })).toBeInTheDocument();
    const broth = within(recipe).getByRole("list", { name: "Broth steps" });
    expect(within(broth).getAllByRole("listitem")).toHaveLength(2);
    expect(broth).toHaveTextContent("ground fennel 1–2 tsp; simmer for 40 minutes.");
    expect(within(broth).getByTestId("step-timer")).toHaveClass("text-neutral-600");
    expect(within(recipe).queryByRole("list", { name: "Ingredients" })).not.toBeInTheDocument();
    for (const el of recipe.querySelectorAll("span")) expect(el.className).not.toMatch(/blue/);
  });

  it("falls back to the ingredient lines when the index has no body", async () => {
    mount();
    const recipe = await within(await screen.findByRole("main")).findByRole("region", { name: "Recipe" });
    expect(await within(recipe).findByRole("list", { name: "Ingredients" })).toBeInTheDocument();
    expect(within(recipe).queryByRole("list", { name: "Steps" })).not.toBeInTheDocument();
  });
});
