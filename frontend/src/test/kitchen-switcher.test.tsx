import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { HomeBase } from "../api/geo";
import type { CompareResult } from "../api/pricebook";
import type { Settings } from "../api/settings";
import { MAIN_SECTIONS, visibleSections } from "../components/Nav";
import { KitchenTag } from "../components/plan/KitchenTag";
import { secondTab } from "../components/TabBar";
import { resolveKitchen } from "../lib/kitchen";
import { availableActions, kitchenActions } from "../lib/paletteActions";
import { flour, flourId, flourProductId, ingredientMatch, units } from "./catalog-fixtures";
import { chainLocationId, chainVendor, chainVendorId, homeBase, homeBaseId, marketLocation, marketLocationId, marketVendor, marketVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { cookHealth, emptyHistory, mountedStatus, quietInbox, recipeList, stewCost, stewRecipe } from "./recipe-fixtures";

const planHealth = { status: "ok", features: ["catalog", "shop", "cook", "plan"] };
const cabinId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e02";
const cabin: HomeBase = { ...homeBase, id: cabinId, name: "Hillside cabin", lat: "33.700000", lon: "-120.300000" };
const kitchens = [homeBase, cabin];

/** The settings row on the mocked server, written by PUT and read back by GET. */
function settingsApi(initial: Settings = { current_home_base_id: homeBaseId, suggested_home_base_id: null }) {
  let row: Settings = { ...initial };
  return {
    read: () => row,
    routes: {
      "GET /me/settings": () => jsonResponse(200, row),
      "PUT /me/settings": (call: RecordedCall) => {
        row = { ...row, current_home_base_id: (call.body as Settings).current_home_base_id, updated_at: "2026-10-12T10:00:00Z" };
        return jsonResponse(200, row);
      },
    } satisfies Record<string, RouteHandler>,
  };
}

function mount(routes: Record<string, RouteHandler> = {}, path = "/settings/system", health: unknown = planHealth) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, health),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /home-bases": () => jsonResponse(200, { items: kitchens }),
    ...routes,
  });
  return { calls, ...renderApp(path) };
}

const puts = (calls: RecordedCall[]) => calls.filter((c) => c.method === "PUT" && c.path === "/me/settings");
const sidebar = () => screen.getAllByRole("navigation", { name: "Main" })[0];
const tabs = () => screen.getByRole("navigation", { name: "Tabs" });

afterEach(() => localStorage.clear());

describe("the Plan section (UI-8.1)", () => {
  it("sits between Cook and Shop only when plan is a feature, and takes Compare from Shop", () => {
    expect(MAIN_SECTIONS.map((s) => s.key)).toEqual(["home", "cook", "plan", "shop"]);
    const without = visibleSections(MAIN_SECTIONS, ["cook"]);
    expect(without.map((s) => s.key)).toEqual(["home", "cook", "shop"]);
    expect(without.find((s) => s.key === "shop")?.items.map((i) => i.label)).toContain("Compare prices");
    const withPlan = visibleSections(MAIN_SECTIONS, ["cook", "plan"]);
    expect(withPlan.map((s) => s.key)).toEqual(["home", "cook", "plan", "shop"]);
    expect(withPlan.find((s) => s.key === "plan")?.items.map((i) => i.to)).toEqual(["/plan/list", "/plan/trips", "/plan/compare"]);
    expect(withPlan.find((s) => s.key === "shop")?.items.map((i) => i.label)).not.toContain("Compare prices");
  });

  it("shows Plan in the sidebar with plan listed, and not without", async () => {
    const settings = settingsApi();
    const { unmount } = mount(settings.routes, "/plan/trips");
    await screen.findByRole("heading", { name: "Trips" });
    const nav = sidebar();
    const labels = () =>
      within(nav)
        .getAllByRole("link")
        .map((l) => l.textContent)
        .filter((t) => ["Home", "Cook", "Plan", "Shop"].includes(t ?? ""));
    await waitFor(() => expect(labels()).toEqual(["Home", "Cook", "Plan", "Shop"]));
    expect(within(nav).getByRole("link", { name: "Shopping list" })).toHaveAttribute("href", "/plan/list");
    expect(within(nav).getByRole("link", { name: "Compare prices" })).toHaveAttribute("href", "/plan/compare");
    expect(await screen.findByRole("button", { name: "Plan a trip" })).toBeInTheDocument();
    unmount();

    mount({}, "/settings/system", cookHealth);
    await screen.findByRole("heading", { name: "System" });
    expect(within(sidebar()).queryByRole("link", { name: /^Plan/ })).not.toBeInTheDocument();
  });

  it("redirects /shop/compare and /compare to /plan/compare with their parameters, and /plan to the list", async () => {
    const settings = settingsApi();
    const { unmount } = mount({ ...settings.routes, "GET /units": () => jsonResponse(200, { items: units }) }, "/shop/compare?home_base_id=" + cabinId);
    expect(await screen.findByRole("heading", { name: /Compare/ })).toBeInTheDocument();
    // The parameter carried over: the kitchen control reads it.
    await waitFor(() => expect(screen.getByRole("button", { name: "Hillside cabin", pressed: true })).toBeInTheDocument());
    unmount();

    const second = mount({ ...settings.routes, "GET /units": () => jsonResponse(200, { items: units }) }, "/compare");
    expect(await screen.findByRole("heading", { name: /Compare/ })).toBeInTheDocument();
    second.unmount();

    mount({ ...settings.routes, "GET /plan/list": () => jsonResponse(200, { id: "l1", home_base_id: homeBaseId, home_base_name: homeBase.name, status: "open", created_at: "2026-10-12T08:00:00Z", archived_at: null, unchecked: [], checked: [] }) }, "/plan");
    expect(await screen.findByRole("heading", { level: 1, name: /^Harbour flat list/ })).toBeInTheDocument();
  });
});

describe("the kitchen switcher (UI-2.4, UI-2.5)", () => {
  it("names the current kitchen in the sidebar footer, lists the kitchens in a menu and writes the choice", async () => {
    const settings = settingsApi();
    const { calls } = mount(settings.routes);
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();

    const chip = await within(sidebar()).findByRole("button", { name: "Kitchen: Harbour flat" });
    expect(chip).toHaveAttribute("aria-haspopup", "menu");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    // Above the signed-in user.
    expect(chip.compareDocumentPosition(within(sidebar()).getByText(adminUser.display_name)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    chip.focus();
    await user.keyboard("{Enter}");
    const menu = await screen.findByRole("menu", { name: "Switch kitchen" });
    const items = within(menu).getAllByRole("menuitemradio");
    expect(items.map((i) => i.textContent)).toEqual(["Harbour flat", "Switch to Hillside cabin"]);
    expect(items[0]).toHaveAttribute("aria-checked", "true");
    expect(items[0]).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(items[1]).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0].body).toEqual({ current_home_base_id: cabinId });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(await within(sidebar()).findByRole("button", { name: "Kitchen: Hillside cabin" })).toHaveFocus();
    expect(settings.read().current_home_base_id).toBe(cabinId);
  });

  it("reads the choice back on a second device: the setting, not the browser, decides", async () => {
    const settings = settingsApi({ current_home_base_id: cabinId, suggested_home_base_id: null });
    const { calls } = mount(settings.routes);
    await screen.findByRole("heading", { name: "System" });
    expect(await within(sidebar()).findByRole("button", { name: "Kitchen: Hillside cabin" })).toBeInTheDocument();
    expect(puts(calls)).toHaveLength(0);
  });

  it("closes on Escape and returns focus to the chip, choosing nothing", async () => {
    const settings = settingsApi();
    const { calls } = mount(settings.routes);
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    const chip = await within(sidebar()).findByRole("button", { name: "Kitchen: Harbour flat" });
    await user.click(chip);
    await screen.findByRole("menu", { name: "Switch kitchen" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(chip).toHaveFocus();
    expect(puts(calls)).toHaveLength(0);
  });

  it("is not there with one kitchen", async () => {
    const settings = settingsApi({ current_home_base_id: null, suggested_home_base_id: homeBaseId });
    mount({ ...settings.routes, "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }) });
    await screen.findByRole("heading", { name: "System" });
    await waitFor(() => expect(screen.getAllByTestId("health-status")[0]).toHaveTextContent("ok"));
    expect(screen.queryByTestId("kitchen-switcher")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Kitchen:/ })).not.toBeInTheDocument();
  });

  it("is not there without plan", async () => {
    mount({}, "/settings/system", cookHealth);
    await screen.findByRole("heading", { name: "System" });
    await waitFor(() => expect(screen.getAllByTestId("health-status")[0]).toHaveTextContent("ok"));
    expect(screen.queryByTestId("kitchen-switcher")).not.toBeInTheDocument();
  });

  it("resolves an empty setting from the remembered location's kitchen and writes it back", async () => {
    localStorage.setItem("kerp.lastVendorLocationId", marketLocationId);
    const settings = settingsApi({ current_home_base_id: null, suggested_home_base_id: null });
    const { calls } = mount({
      ...settings.routes,
      [`GET /vendor-locations/${marketLocationId}`]: () => jsonResponse(200, { ...marketLocation, home_base_id: cabinId, stalls: [] }),
    });
    await screen.findByRole("heading", { name: "System" });
    expect(await within(sidebar()).findByRole("button", { name: "Kitchen: Hillside cabin" })).toBeInTheDocument();
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0].body).toEqual({ current_home_base_id: cabinId });
  });

  it("follows the chain: setting, remembered location, suggestion, first by name", () => {
    const none = { current_home_base_id: null, suggested_home_base_id: null };
    expect(resolveKitchen({ ...none, current_home_base_id: cabinId }, kitchens, homeBaseId)).toBe(cabinId);
    expect(resolveKitchen(none, kitchens, cabinId)).toBe(cabinId);
    expect(resolveKitchen({ ...none, suggested_home_base_id: cabinId }, kitchens, null)).toBe(cabinId);
    expect(resolveKitchen(none, kitchens, null)).toBe(homeBaseId);
    // A kitchen that no longer exists counts for nothing.
    expect(resolveKitchen({ ...none, current_home_base_id: "gone" }, kitchens, "gone")).toBe(homeBaseId);
    expect(resolveKitchen(none, [], null)).toBeNull();
  });
});

describe("the phone tab bar and More sheet (UI-8.10, UI-4.9)", () => {
  it("names the second tab List with plan, Purchases without", () => {
    expect(secondTab(undefined)).toMatchObject({ to: "/shop/purchases", label: "Purchases" });
    expect(secondTab(["catalog", "shop"])).toMatchObject({ to: "/shop/purchases", label: "Purchases" });
    expect(secondTab(["catalog", "shop", "plan"])).toMatchObject({ to: "/plan/list", label: "List" });
  });

  it("opens the list from the second tab, lists Purchases under More, and switches kitchens at the top of the sheet", async () => {
    const settings = settingsApi();
    const { calls } = mount({ ...settings.routes, "GET /plan/list": () => jsonResponse(200, { id: "l1", home_base_id: homeBaseId, home_base_name: homeBase.name, status: "open", created_at: "2026-10-12T08:00:00Z", archived_at: null, unchecked: [], checked: [] }) }, "/plan/list");
    await screen.findByRole("heading", { level: 1, name: /list/ });
    const user = userEvent.setup();

    const bar = within(tabs());
    await waitFor(() => expect(bar.getAllByRole("link").map((l) => l.textContent)).toEqual(["Home", "List"]));
    expect(bar.getByRole("link", { name: "List" })).toHaveAttribute("href", "/plan/list");
    expect(bar.getByRole("link", { name: "List" })).toHaveAttribute("aria-current", "page");

    await user.click(bar.getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "More" });
    const links = within(sheet).getAllByRole("link").map((l) => l.textContent);
    expect(links.slice(0, 5)).toEqual(["Recipes", "Trips", "Compare prices", "Purchases", "Receipts"]);

    // The switcher is the first row of the sheet, before any section.
    const row = within(sheet).getByTestId("kitchen-switcher-row");
    const chip = within(row).getByRole("button", { name: "Kitchen: Harbour flat" });
    expect(chip.compareDocumentPosition(within(sheet).getByRole("link", { name: "Recipes" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chip).toHaveClass("min-h-11");
    await user.click(chip);
    const choice = within(row).getByRole("radio", { name: "Hillside cabin" });
    expect(within(row).getByRole("radio", { name: "Harbour flat" })).toBeChecked();
    await user.click(choice);
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0].body).toEqual({ current_home_base_id: cabinId });
    expect(await within(row).findByRole("button", { name: "Kitchen: Hillside cabin" })).toBeInTheDocument();
    // Still on the sheet: switching is not a navigation.
    expect(screen.getByRole("dialog", { name: "More" })).toBeInTheDocument();
  });
});

describe("palette actions for Plan (UI-2.9, UI-2.4)", () => {
  it("offers the list, trips and Switch to {kitchen} only with plan", () => {
    const labels = (features: string[] | undefined) => availableActions(features, true).map((a) => a.label);
    expect(labels(["cook"])).not.toContain("Shopping list");
    expect(labels(["cook", "plan"])).toEqual(expect.arrayContaining(["Shopping list", "Trips"]));
    expect(availableActions(["plan"], true).find((a) => a.id === "compare-prices")?.route).toBe("/plan/compare");
    const switches = kitchenActions(kitchens, homeBaseId);
    expect(switches.map((a) => a.label)).toEqual(["Switch to Hillside cabin"]);
    expect(switches[0]).toMatchObject({ section: "Plan", switchToKitchenId: cabinId });
  });

  it("switches the kitchen from the palette without leaving the page", async () => {
    const settings = settingsApi();
    const { calls } = mount({ ...settings.routes, "GET /search": () => jsonResponse(200, { ingredients: [], products: [], vendors: [] }) });
    await screen.findByRole("heading", { name: "System" });
    await within(sidebar()).findByRole("button", { name: "Kitchen: Harbour flat" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "switch");
    const option = await within(dialog).findByRole("option", { name: /Switch to Hillside cabin/ });
    expect(option).toHaveTextContent("Plan");
    expect(within(dialog).queryByRole("option", { name: /Switch to Harbour flat/ })).not.toBeInTheDocument();
    await user.click(option);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(screen.getByRole("heading", { name: "System" })).toBeInTheDocument();
    expect(await within(sidebar()).findByRole("button", { name: "Kitchen: Hillside cabin" })).toBeInTheDocument();
  });
});

describe("the kitchen tag (UI-2.6)", () => {
  it("is a neutral pill that reads 'Kitchen: {name}' to a screen reader", () => {
    render(<KitchenTag name="Harbour flat" />);
    const tag = screen.getByTestId("kitchen-tag");
    expect(tag).toHaveTextContent("Kitchen: Harbour flat");
    expect(tag.querySelector(".bg-neutral-200")).not.toBeNull();
    expect(tag.querySelector("[class*='blue']")).toBeNull();
  });
});

const cell = (over: Partial<CompareResult["rows"][0]["cells"][string]>) => ({
  product_id: flourProductId,
  product_name: "All-Purpose Flour",
  brand: "Millstone",
  quality_rating: 4,
  location_id: chainLocationId,
  location_name: "Millstone Harbour",
  observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7001",
  observed_at: "2026-09-18T15:00:00Z",
  is_promo: false,
  norm_unit_price: "0.002200",
  display_unit_price: "0.998",
  display_unit: "lb",
  norm_unit: "g",
  stale: false,
  cheapest: false,
  age_days: "3",
  ...over,
});

const twoVendors: CompareResult = {
  vendors: [
    { id: chainVendorId, name: chainVendor.name },
    { id: marketVendorId, name: marketVendor.name },
  ],
  rows: [
    {
      ingredient_id: flourId,
      ingredient_name: flour.name,
      canonical_unit: "g",
      cells: {
        [chainVendorId]: cell({ location_id: chainLocationId, norm_unit_price: "0.002200", cheapest: true }),
        [marketVendorId]: cell({ location_id: marketLocationId, location_name: "Pier Farmers Market", norm_unit_price: "0.003100", display_unit_price: "1.41" }),
      },
    },
  ],
  stale_after_days: 90,
};

/** The server's answer with the cabin's filter on: only the market, its price the cheapest. */
const cabinOnly: CompareResult = {
  ...twoVendors,
  vendors: [{ id: marketVendorId, name: marketVendor.name }],
  rows: [{ ...twoVendors.rows[0], cells: { [marketVendorId]: { ...twoVendors.rows[0].cells[marketVendorId], cheapest: true } } }],
};

describe("Compare's kitchen control (UI-8.8, UI-2.7)", () => {
  it("is All by default, keeps the choice in the URL, shows the tag and asks the server for that kitchen's prices", async () => {
    const settings = settingsApi();
    const { calls } = mount(
      {
        ...settings.routes,
        "GET /units": () => jsonResponse(200, { items: units }),
        "GET /ingredients/search": (call) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? [ingredientMatch(flour)] : [] }),
        "POST /price-book/compare": (call) => jsonResponse(200, (call.body as { home_base_id?: string }).home_base_id === cabinId ? cabinOnly : twoVendors),
      },
      "/plan/compare",
    );
    const user = userEvent.setup();
    const control = await screen.findByRole("group", { name: "Kitchen" });
    expect(within(control).getAllByRole("button").map((b) => b.textContent)).toEqual(["All", "Harbour flat", "Hillside cabin"]);
    expect(within(control).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("kitchen-tag")).not.toBeInTheDocument();

    await user.type(screen.getByRole("combobox", { name: "Add an ingredient" }), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));
    const table = await screen.findByRole("table", { name: "Price comparison" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Ingredient", chainVendor.name, marketVendor.name]);
    const compares = () => calls.filter((c) => c.method === "POST" && c.path === "/price-book/compare");
    // All: no kitchen in the body, so the server answers household-wide (UI-2.7).
    expect("home_base_id" in (compares()[0].body as object)).toBe(false);

    await user.click(within(control).getByRole("button", { name: "Hillside cabin" }));
    expect(within(control).getByRole("button", { name: "Hillside cabin" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Price comparison" })).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Ingredient", marketVendor.name]));
    expect(screen.getByTestId("kitchen-tag")).toHaveTextContent("Hillside cabin");
    expect((compares().at(-1)?.body as { home_base_id?: string }).home_base_id).toBe(cabinId);
    // The cheapest mark is the server's: the kitchen's own price.
    expect(within(screen.getByTestId(`cell-${flourId}-${marketVendorId}`)).getByText("cheapest")).toBeInTheDocument();
    // Nothing is read client-side: no location lookup stands in for the server.
    expect(calls.some((c) => c.method === "GET" && c.path.startsWith("/vendor-locations"))).toBe(false);

    await user.click(within(control).getByRole("button", { name: "All" }));
    await waitFor(() => expect(screen.queryByTestId("kitchen-tag")).not.toBeInTheDocument());
    expect(within(screen.getByRole("table", { name: "Price comparison" })).getAllByRole("columnheader")).toHaveLength(3);
    expect("home_base_id" in (compares().at(-1)?.body as object)).toBe(false);
  });

  it("has no kitchen control without plan", async () => {
    mount({ "GET /units": () => jsonResponse(200, { items: units }) }, "/plan/compare", cookHealth);
    await screen.findByRole("heading", { name: /Compare/ });
    await waitFor(() => expect(screen.getAllByTestId("health-status")[0]).toHaveTextContent("ok"));
    expect(screen.queryByRole("group", { name: "Kitchen" })).not.toBeInTheDocument();
  });
});

describe("the cost table's kitchen control (UI-8.8)", () => {
  function mountRecipe(routes: Record<string, RouteHandler> = {}, path = "/cook/recipes/r-barley") {
    const settings = settingsApi();
    return mount(
      {
        ...settings.routes,
        "GET /recipes/status": () => jsonResponse(200, mountedStatus),
        "GET /recipes": () => jsonResponse(200, { items: recipeList }),
        "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
        // The server's per-kitchen view echoes the kitchen; the snapshot carries null.
        "GET /recipes/r-barley/cost": (call) => jsonResponse(200, stewCost({ home_base_id: call.query.get("home_base_id") ?? null })),
        "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
        ...routes,
      },
      path,
    );
  }

  it("sits beside the basis control, asks the server with home_base_id and keeps it in the URL", async () => {
    const { calls } = mountRecipe();
    const user = userEvent.setup();
    const control = await screen.findByRole("group", { name: "Kitchen" });
    expect(screen.getByRole("group", { name: "Basis" })).toBeInTheDocument();
    expect(within(control).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("kitchen-tag")).not.toBeInTheDocument();
    const costCalls = () => calls.filter((c) => c.path.startsWith("/recipes/r-barley/cost?"));
    const before = costCalls().length;

    await user.click(within(control).getByRole("button", { name: "Hillside cabin" }));
    await waitFor(() => expect(costCalls().length).toBeGreaterThan(before));
    const scoped = costCalls().at(-1);
    expect(scoped?.query.get("home_base_id")).toBe(cabinId);
    expect(scoped?.query.get("basis")).toBe("latest");
    expect(screen.getByTestId("kitchen-tag")).toHaveTextContent("Hillside cabin");
    expect(screen.getByTestId("kitchen-cost-note")).toHaveTextContent("Prices at Hillside cabin's stores only");
    // The server filters, so no location lookup and no row is second-guessed.
    expect(calls.some((c) => c.method === "GET" && c.path.startsWith("/vendor-locations"))).toBe(false);
    expect(screen.queryByTestId("priced-elsewhere")).not.toBeInTheDocument();
  });

  it("reads the kitchen from the URL and shows the server's per-kitchen figures as they are", async () => {
    const { calls } = mountRecipe({}, `/cook/recipes/r-barley?home_base_id=${cabinId}`);
    expect(await screen.findByRole("button", { name: "Hillside cabin", pressed: true })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/recipes/r-barley/cost?") && c.query.get("home_base_id") === cabinId)).toBe(true));
    expect(await screen.findByTestId("kitchen-cost-note")).toHaveTextContent("Prices at Hillside cabin's stores only");
    expect(screen.getByTestId("kitchen-tag")).toHaveTextContent("Hillside cabin");
    // The history is household-wide and stays out of a per-kitchen view (UI-8.8).
    expect(calls.some((c) => c.path.startsWith("/recipes/r-barley/cost/history"))).toBe(false);
    expect(screen.queryByTestId("kitchen-cost-unfiltered")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priced-elsewhere")).not.toBeInTheDocument();
  });
});
