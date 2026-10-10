import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { cookHealth, mountedStatus, noRepositoryStatus, quietInbox, recipeList, scanChanged } from "./recipe-fixtures";

afterEach(() => localStorage.clear());

function mount(routes: Record<string, RouteHandler> = {}, path = "/cook/recipes", health: unknown = cookHealth) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, health),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    ...routes,
  });
  renderApp(path);
  return calls;
}

const sidebar = () => screen.getAllByRole("navigation", { name: "Main" })[0];
const rows = async () => within(await screen.findByRole("main")).findAllByTestId("recipe-row");
const listCalls = (calls: RecordedCall[]) => calls.filter((c) => c.method === "GET" && /^\/recipes(\?|$)/.test(c.path));

describe("the Cook section (UI-7.1)", () => {
  it("shows Cook before Shop only when /health lists the cook feature", async () => {
    mount();
    await screen.findByRole("heading", { name: "Recipes" });
    const nav = sidebar();
    await within(nav).findByRole("link", { name: "Cook" });
    const labels = within(nav)
      .getAllByRole("link")
      .map((l) => l.textContent?.trim())
      .filter((t) => ["Home", "Cook", "Shop", "Catalog"].includes(t ?? ""));
    expect(labels).toEqual(["Home", "Cook", "Shop", "Catalog"]);
    expect(within(nav).getByRole("link", { name: "Cook" })).toHaveAttribute("aria-current", "true");
    expect(within(nav).getByRole("link", { name: "Recipes" })).toHaveAttribute("aria-current", "page");
  });

  it("hides Cook without the feature, while the route itself still answers", async () => {
    mount({}, "/cook/recipes", { status: "ok", features: ["catalog", "shop"] });
    await screen.findByRole("heading", { name: "Recipes" });
    expect(within(sidebar()).queryByRole("link", { name: "Cook" })).not.toBeInTheDocument();
  });

  it("sends /cook to the list and /cook/costing to the incomplete filter", async () => {
    const calls = mount({}, "/cook/costing");
    await screen.findByRole("heading", { name: "Recipes" });
    await waitFor(() => expect(listCalls(calls).some((c) => c.path.includes("completeness=incomplete"))).toBe(true));
    const control = within((await screen.findByRole("main"))).getByRole("group", { name: "Completeness" });
    expect(within(control).getByRole("button", { name: "Incomplete" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the resolve route for the inbox row's link", async () => {
    mount({ "GET /recipes/resolve": () => jsonResponse(200, { items: [], names: 0, recipes: 0, model_configured: false }) }, "/cook/recipes/resolve");
    expect(await screen.findByRole("heading", { name: "Resolve recipe names" })).toBeInTheDocument();
    expect(await within((await screen.findByRole("main"))).findByText("Every recipe name is resolved")).toBeInTheDocument();
  });
});

describe("the recipes list (UI-7.3, UI-7.4)", () => {
  it("shows each recipe's title, path, servings, cost range, basket, known lines and indexed time, ordered as the server sends them", async () => {
    mount();
    await screen.findByRole("heading", { name: "Recipes" });
    expect(within((await screen.findByRole("main"))).getByText("What each dish costs from the price book, and how much of that figure is known.")).toBeInTheDocument();
    const all = await rows();
    expect(all).toHaveLength(4);
    const stew = all[0];
    expect(within(stew).getByRole("link", { name: "Barley moon stew" })).toHaveAttribute("href", "/cook/recipes/r-barley");
    expect(stew).toHaveTextContent("soups/barley_moon_stew.cook");
    expect(stew).toHaveTextContent("4");
    // A range reads "low–high" with the currency once; per serving beneath.
    expect(stew).toHaveTextContent("$12.40–13.10");
    expect(stew).toHaveTextContent("$3.10 per serving");
    expect(stew).toHaveTextContent("$31.96");
    expect(stew).toHaveTextContent("8 of 10 lines priced");
    expect(within(stew).getByText("8 of 10 lines priced")).toHaveClass("text-amber-900");
    expect(within(stew).getByText(/ago|just now/)).toBeInTheDocument();
    expect(within(stew).queryByText(/provisional/)).not.toBeInTheDocument();
  });

  it("carries every badge in words and marks a provisional snapshot", async () => {
    mount();
    await screen.findByRole("heading", { name: "Recipes" });
    const [, crumble, flatbread, lentils] = await rows();
    expect(within(crumble).getByText("Uncommitted")).toHaveClass("bg-neutral-200");
    expect(within(crumble).getByText("provisional")).toBeInTheDocument();
    expect(within(crumble).getByText("5 of 5 lines priced")).not.toHaveClass("text-amber-900");
    expect(within(flatbread).getByText("Can't read")).toHaveClass("bg-red-100");
    expect(within(flatbread).getByText("Not costed yet")).toBeInTheDocument();
    expect(within(lentils).getByText("Missing")).toHaveClass("bg-amber-100");
    // Nothing on the list is blue except the links (UI-7.19).
    for (const badge of ["Uncommitted", "Can't read", "Missing"]) {
      expect(screen.getByText(badge).className).not.toMatch(/blue/);
    }
  });

  it("keeps search, status and completeness in the URL and runs them on the server", async () => {
    const calls = mount({}, "/cook/recipes?status=missing");
    await screen.findByRole("heading", { name: "Recipes" });
    const main = await screen.findByRole("main");
    const statusControl = within(main).getByRole("group", { name: "Status" });
    expect(within(statusControl).getByRole("button", { name: "Missing (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(listCalls(calls)[0].query.get("status")).toBe("missing");

    const user = userEvent.setup();
    await user.click(within(statusControl).getByRole("button", { name: "Can't read (1)" }));
    await waitFor(() => expect(listCalls(calls).at(-1)?.query.get("status")).toBe("parse_error"));

    await user.click(within(within(main).getByRole("group", { name: "Completeness" })).getByRole("button", { name: "Complete" }));
    await waitFor(() => expect(listCalls(calls).at(-1)?.query.get("completeness")).toBe("complete"));
    expect(listCalls(calls).at(-1)?.query.get("status")).toBe("parse_error");

    await user.type(within(main).getByRole("textbox", { name: "Search recipes" }), "soup");
    await waitFor(() => expect(listCalls(calls).at(-1)?.query.get("q")).toBe("soup"));
    expect(listCalls(calls).at(-1)?.query.get("status")).toBe("parse_error");
  });
});

describe("the list's states (UI-7.5, UI-7.6)", () => {
  it("shows the no-recipes empty state naming RECIPES_PATH and make seed-examples when there is no repository", async () => {
    mount({
      "GET /recipes/status": () => jsonResponse(200, noRepositoryStatus),
      "GET /recipes": () => jsonResponse(200, { items: [] }),
    });
    const empty = await within((await screen.findByRole("main"))).findByRole("region", { name: "No recipes found" });
    expect(empty).toHaveTextContent("Point RECIPES_PATH at a Cooklang repository, or run make seed-examples to start with the example recipes.");
    expect(within(empty).getByRole("button", { name: "Rescan recipes" })).toBeInTheDocument();
    expect(within((await screen.findByRole("main"))).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says what a filtered-empty list filtered, and Clear filters clears the URL", async () => {
    const calls = mount({ "GET /recipes": () => jsonResponse(200, { items: [] }) }, "/cook/recipes?q=soup&status=parse_error");
    const empty = await within((await screen.findByRole("main"))).findByRole("region", { name: "No recipes match ‘soup’ that can't be read" });
    await userEvent.setup().click(within(empty).getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      const last = listCalls(calls).at(-1)!;
      expect(last.query.get("q")).toBeNull();
      expect(last.query.get("status")).toBeNull();
    });
    expect(within((await screen.findByRole("main"))).getByRole("textbox", { name: "Search recipes" })).toHaveValue("");
  });

  it("shows a failed load as an alert with Try again, never the empty state", async () => {
    let failures = 1;
    mount({
      "GET /recipes": () => (failures-- > 0 ? errorResponse(500, "db_down", "The database is away.") : jsonResponse(200, { items: recipeList })),
    });
    const alert = await within((await screen.findByRole("main"))).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load recipes: The database is away.");
    expect(within((await screen.findByRole("main"))).queryByRole("region", { name: /No recipes/ })).not.toBeInTheDocument();
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await within((await screen.findByRole("main"))).findByRole("link", { name: "Barley moon stew" })).toBeInTheDocument();
  });

  it("rescans from the primary action, reads Rescanning… meanwhile, and ends with the Notice", async () => {
    let release: (() => void) | null = null;
    const calls = mount({
      "POST /recipes/rescan": () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(jsonResponse(200, scanChanged));
        }),
    });
    await screen.findByRole("heading", { name: "Recipes" });
    const user = userEvent.setup();
    const main = await screen.findByRole("main");
    await user.click(within(main).getByRole("button", { name: "Rescan recipes" }));
    const busy = await within(main).findByRole("button", { name: "Rescanning…" });
    expect(busy).toBeDisabled();
    release!();
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Rescanned: 2 recipes changed.");
    expect(notice).toHaveAttribute("data-tone", "success");
    expect(within(main).getByRole("button", { name: "Rescan recipes" })).toBeEnabled();
    // The list is read again once the scan has answered.
    await waitFor(() => expect(listCalls(calls).length).toBeGreaterThan(1));
  });

  it("says Nothing changed when the scan changed nothing, and runs the palette's rescan once", async () => {
    const calls = mount({ "POST /recipes/rescan": () => jsonResponse(200, { ...scanChanged, created: 0, updated: 0 }) }, "/cook/recipes?rescan=1");
    expect(await screen.findByTestId("notice")).toHaveTextContent("Rescanned. Nothing changed.");
    expect(calls.filter((c) => c.method === "POST" && c.path === "/recipes/rescan")).toHaveLength(1);
  });

  it("shows the names-waiting line from the inbox's recipe row", async () => {
    mount({
      "GET /inbox": () =>
        jsonResponse(200, {
          ...quietInbox,
          items: [{ kind: "recipe", title: "14 recipe names to resolve", detail: "In 9 recipes.", action_label: "Resolve", action_route: "/cook/recipes/resolve", created_at: "2026-10-09T10:00:00Z" }],
        }),
    });
    const line = await within((await screen.findByRole("main"))).findByTestId("names-waiting-line");
    expect(line).toHaveTextContent("14 recipe names to resolve · Resolve");
    expect(within(line).getByRole("link", { name: "Resolve" })).toHaveAttribute("href", "/cook/recipes/resolve");
  });
});

describe("the search palette's Recipes group (UI-7.17)", () => {
  it("lists recipes after vendors with their badge, and offers the Cook actions only with the feature", async () => {
    mount({
      "GET /search": () =>
        jsonResponse(200, {
          ingredients: [],
          products: [],
          vendors: [{ kind: "vendor", id: "v1", label: "Harbor Grocer", detail: "Independent", route: "/catalog/vendors/v1", category_key: null, badge: null }],
          recipes: [
            { kind: "recipe", id: "r-harbor", label: "Harbor flatbread", detail: "breads/harbor_flatbread.cook", route: "/cook/recipes/r-harbor", category_key: null, badge: "parse_error" },
            { kind: "recipe", id: "r-barley", label: "Barley moon stew", detail: "soups/barley_moon_stew.cook", route: "/cook/recipes/r-barley", category_key: null, badge: null },
          ],
        }),
    });
    await screen.findByRole("heading", { name: "Recipes" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "harbor");
    const listbox = await within(dialog).findByRole("listbox");
    await within(listbox).findByRole("group", { name: "Recipes" });
    // Vendors, then Recipes; no action matches "harbor", so there is no Actions group yet.
    const groups = within(listbox).getAllByRole("group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAccessibleName("Vendors");
    expect(groups[1]).toHaveAccessibleName("Recipes");
    const recipes = within(listbox).getByRole("group", { name: "Recipes" });
    const flatbread = within(recipes).getByRole("option", { name: /Harbor flatbread/ });
    expect(within(flatbread).getByText("Can't read")).toBeInTheDocument();
    expect(within(recipes).getByRole("option", { name: /Barley moon stew/ }).textContent).toBe("Barley moon stew");

    await user.clear(within(dialog).getByRole("combobox"));
    await user.type(within(dialog).getByRole("combobox"), "rescan");
    const actions = await within(dialog).findByRole("group", { name: "Actions" });
    expect(within(actions).getByRole("option", { name: /Rescan recipes/ })).toHaveTextContent("Cook");
  });

  it("offers no Cook actions without the feature", async () => {
    mount({ "GET /search": () => jsonResponse(200, { ingredients: [], products: [], vendors: [] }) }, "/cook/recipes", { status: "ok", features: ["catalog", "shop"] });
    await screen.findByRole("heading", { name: "Recipes" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "recipes");
    expect(await within(dialog).findByText(/No matches for ‘recipes’/)).toBeInTheDocument();
  });
});
