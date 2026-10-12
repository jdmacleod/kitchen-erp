import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CostBasis } from "../api/recipes";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { chickpeas, kitchen, kitchenId, list, planHealth, settings } from "./plan-fixtures";
import { emptyHistory, mountedStatus, quietInbox, recipeList, stewCost, stewRecipe } from "./recipe-fixtures";

/** "Add to list" on the ingredient hub and the recipe page (10, Plan; UI-8.4). */

const planRoutes: Record<string, RouteHandler> = {
  "GET /auth/me": () => jsonResponse(200, adminUser),
  "GET /health": () => jsonResponse(200, planHealth),
  "GET /inbox": () => jsonResponse(200, quietInbox),
  "GET /me/settings": () => jsonResponse(200, settings),
  "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
  "GET /units": () => jsonResponse(200, { items: units }),
};

function mountIngredient(routes: Record<string, RouteHandler> = {}): RecordedCall[] {
  const calls = mockApi({
    ...planRoutes,
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
    [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: [], stale_after_days: 90 }),
    [`GET /ingredients/${flourId}/price-history`]: () => jsonResponse(200, { points: [], days: 90 }),
    [`GET /ingredients/${flourId}/recipes`]: () => jsonResponse(200, { items: [], total: 0 }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
    ...routes,
  });
  renderApp(`/catalog/ingredients/${flourId}`);
  return calls;
}

function mountRecipe(routes: Record<string, RouteHandler> = {}): RecordedCall[] {
  const calls = mockApi({
    ...planRoutes,
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": (call) => jsonResponse(200, stewCost({ basis: (call.query.get("basis") as CostBasis | null) ?? "latest" })),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    ...routes,
  });
  renderApp("/cook/recipes/r-barley");
  return calls;
}

describe("Add to list on the ingredient hub (UI-8.4)", () => {
  it("opens a panel naming the kitchen, posts the ingredient and confirms through the Notice with Open list", async () => {
    const user = userEvent.setup();
    const calls = mountIngredient({ "POST /plan/list/items": () => jsonResponse(201, { ...chickpeas, ingredient: { ...flour, category_key: "pantry" } }) });
    const main = await screen.findByRole("main");
    await within(main).findByRole("heading", { level: 1, name: "all-purpose flour" });
    await user.click(within(main).getByRole("button", { name: "Add to list" }));
    const panel = within(main).getByRole("form", { name: "Add to list" });
    expect(panel).toHaveTextContent("Adding to the Harbour flat list.");
    await user.type(within(panel).getByLabelText("Amount"), "2");
    await user.selectOptions(within(panel).getByLabelText("Unit"), "lb");
    await user.click(within(panel).getByRole("checkbox", { name: "Deferrable" }));
    await user.click(within(panel).getByRole("button", { name: "Add" }));

    const post = await waitFor(() => calls.find((c) => c.method === "POST" && c.path === "/plan/list/items")!);
    expect(post.body).toEqual({ home_base_id: kitchenId, kind: "ingredient", ingredient_id: flourId, amounts: [{ amount: "2", unit: "lb" }], deferrable: true, added_from: "ingredient" });
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Added all-purpose flour to the Harbour flat list.");
    const open = within(notice).getByRole("link", { name: "Open list" });
    expect(open).toHaveAttribute("href", "/plan/list");
    expect(open).toHaveFocus();
    expect(within(main).queryByRole("form", { name: "Add to list" })).not.toBeInTheDocument();
  });

  it("offers no Add to list without the plan feature", async () => {
    mountIngredient({ "GET /health": () => jsonResponse(200, { status: "ok", features: ["catalog", "shop", "cook"] }) });
    const main = await screen.findByRole("main");
    await within(main).findByRole("heading", { level: 1, name: "all-purpose flour" });
    expect(within(main).queryByRole("button", { name: "Add to list" })).not.toBeInTheDocument();
  });
});

describe("Add to list on the recipe page (UI-8.4, UI-8.5)", () => {
  it("adds the recipe at the typed scale and reports added, merged and skipped counts", async () => {
    const user = userEvent.setup();
    const calls = mountRecipe({
      "POST /plan/list/items/from-recipe": () => jsonResponse(200, { list: list(), added: 3, merged: 1, skipped: [], already_on_list: false, existing_items: 0 }),
    });
    const main = await screen.findByRole("main");
    await within(main).findByRole("heading", { level: 1, name: "Barley moon stew" });
    await user.click(within(main).getByRole("button", { name: "Add to list" }));
    const panel = within(main).getByRole("form", { name: "Add to list" });
    expect(panel).toHaveTextContent("Adding to the Harbour flat list.");
    const scale = within(panel).getByLabelText("Scale");
    expect(scale).toHaveValue("1");
    await user.clear(scale);
    await user.type(scale, "2");
    expect(panel).toHaveTextContent("4 servings · ×2");
    await user.click(within(panel).getByRole("button", { name: "Add to list" }));

    const post = await waitFor(() => calls.find((c) => c.method === "POST")!);
    expect(post.path).toBe("/plan/list/items/from-recipe");
    expect(post.body).toEqual({ home_base_id: kitchenId, recipe_id: "r-barley", scale: "2", replace: false });
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Added 3 items from Barley moon stew. 1 merged into an item already on the list to the Harbour flat list.");
    expect(within(notice).getByRole("link", { name: "Open list" })).toHaveAttribute("href", "/plan/list");
  });

  it("says when the recipe is already on the list and offers Replace", async () => {
    const user = userEvent.setup();
    const calls = mountRecipe({
      "POST /plan/list/items/from-recipe": (call) =>
        (call.body as { replace: boolean }).replace
          ? jsonResponse(200, { list: list(), added: 4, merged: 0, skipped: [], already_on_list: false, existing_items: 0 })
          : jsonResponse(200, { list: list(), added: 0, merged: 0, skipped: [], already_on_list: true, existing_items: 4 }),
    });
    const main = await screen.findByRole("main");
    await within(main).findByRole("heading", { level: 1, name: "Barley moon stew" });
    await user.click(within(main).getByRole("button", { name: "Add to list" }));
    const panel = within(main).getByRole("form", { name: "Add to list" });
    await user.click(within(panel).getByRole("button", { name: "Add to list" }));
    expect(await within(panel).findByTestId("already-on-list")).toHaveTextContent("Barley moon stew is already on the list: 4 items to get. Nothing was changed.");
    await user.click(within(panel).getByRole("button", { name: "Replace at ×1" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toMatchObject({ replace: true, scale: "1" });
    expect(await screen.findByTestId("notice")).toHaveTextContent("Added 4 items from Barley moon stew to the Harbour flat list.");
  });
});
