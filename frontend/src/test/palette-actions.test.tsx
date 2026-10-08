import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { PALETTE_ACTIONS, availableActions, matchActions, type PaletteAction } from "../lib/paletteActions";
import { units } from "./catalog-fixtures";
import { adminUser, jsonResponse, memberUser, mockApi, renderApp, type RouteHandler } from "./helpers";

const quietInbox = { items: [], reading: { count: 0, oldest_at: null, stalled: false } };
const noResults = { ingredients: [], products: [], vendors: [] };

afterEach(() => localStorage.clear());

function mount(routes: Record<string, RouteHandler> = {}, user = adminUser) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, user),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /search": () => jsonResponse(200, noResults),
    ...routes,
  });
  renderApp("/settings/system");
}

async function openPalette() {
  await screen.findByRole("heading", { name: "System" });
  const user = userEvent.setup();
  await user.keyboard("{Control>}k{/Control}");
  const dialog = await screen.findByRole("dialog", { name: "Search" });
  return { user, dialog };
}

const labels = (actions: PaletteAction[]) => actions.map((a) => a.label);

describe("matching actions (UI-2.9)", () => {
  const all = availableActions(undefined, true);

  it("matches every typed word as a word prefix, label matches first", () => {
    expect(labels(matchActions("shelf", all))[0]).toBe("Log a shelf price");
    expect(labels(matchActions("add prod", all))).toEqual(["Add product"]);
    // A keyword finds it too, after any label matches.
    expect(labels(matchActions("bookmarklet", all))).toEqual(["Capture settings"]);
    expect(labels(matchActions("new", all))[0]).toBe("New purchase");
  });

  it("matches nothing for blank or unrelated text", () => {
    expect(matchActions("   ", all)).toEqual([]);
    expect(matchActions("zzqx", all)).toEqual([]);
  });

  it("offers only built sections and admin pages to admins", () => {
    const later: PaletteAction = { kind: "action", id: "recipes", label: "Recipes", route: "/cook/recipes", keywords: [], feature: "cook" };
    const list = [...PALETTE_ACTIONS, later];
    expect(labels(availableActions(undefined, true, list))).not.toContain("Recipes");
    expect(labels(availableActions(["cook"], true, list))).toContain("Recipes");
    expect(labels(availableActions(undefined, false))).not.toContain("Users");
    expect(labels(availableActions(undefined, true))).toContain("Users");
  });
});

describe("actions in the search palette (UI-2.9)", () => {
  it("lists common actions under the hint before typing", async () => {
    mount();
    const { dialog } = await openPalette();
    expect(within(dialog).getByText("Type a product, ingredient, vendor or barcode.")).toBeInTheDocument();
    const group = within(dialog).getByRole("group", { name: "Actions" });
    expect(within(group).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "New purchaseShop",
      "Upload receiptsShop",
      "Log a shelf priceShop",
      "Add productCatalog",
    ]);
  });

  it("puts actions after the search groups and opens one by keyboard", async () => {
    mount({
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /usda/suggestions": () => jsonResponse(200, { items: [], loaded: false }),
    });
    const { user, dialog } = await openPalette();
    await user.type(within(dialog).getByRole("combobox"), "add ingred");
    const group = await within(dialog).findByRole("group", { name: "Actions" });
    const option = within(group).getByRole("option", { name: /Add ingredient/ });
    expect(option).toHaveAttribute("aria-selected", "true");
    // Actions are not "no matches" when the search itself finds nothing.
    await waitFor(() => expect(within(dialog).queryByRole("status")).not.toBeInTheDocument());
    expect(within(dialog).queryByText(/No matches/)).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    // The page opens with its create drawer, and closing it does not reopen it.
    expect(await screen.findByRole("dialog", { name: "Add ingredient" })).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Ingredients" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add ingredient" })).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not remember an action as a recent", async () => {
    mount();
    const { user, dialog } = await openPalette();
    await user.type(within(dialog).getByRole("combobox"), "system");
    await within(dialog).findByRole("group", { name: "Actions" });
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(localStorage.getItem("kerp.searchRecents")).toBeNull();
  });

  it("shows admin pages only to admins", async () => {
    mount({}, memberUser);
    const { user, dialog } = await openPalette();
    await user.type(within(dialog).getByRole("combobox"), "users");
    expect(await within(dialog).findByText(/No matches for ‘users’/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /Users/ })).not.toBeInTheDocument();
  });
});
