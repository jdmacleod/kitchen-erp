import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { flour, ingredientMatch } from "./catalog-fixtures";
import { chainLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { chickpeas, item, kitchen, kitchenId, list, planHealth, settings } from "./plan-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { quietInbox } from "./recipe-fixtures";

/** Home's "{Kitchen} list" panel (10, Home list panel; UI-8.9): desktop only, with `plan`. */

function mount(routes: Record<string, RouteHandler> = {}, health: unknown = planHealth): RecordedCall[] {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, health),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
    "GET /purchases": () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /plan/list": () => jsonResponse(200, list()),
    "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour, { exact: true })] }),
    ...routes,
  });
  renderApp("/");
  return calls;
}

const six = Array.from({ length: 6 }, (_, i) =>
  item({ id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f74${String(i).padStart(2, "0")}`, ingredient: { ...chickpeas.ingredient!, id: `i-${i}`, name: `thing ${i}` }, created_at: `2026-10-1${i}T09:00:00Z` }),
);

describe("Home's list panel (UI-8.9)", () => {
  it("shows the count, the five oldest unchecked items with amounts, Open list, and hides below lg", async () => {
    mount({ "GET /plan/list": () => jsonResponse(200, list({ unchecked: [chickpeas, ...six] })) });
    const panel = await screen.findByTestId("shopping-list-panel");
    expect(await within(panel).findByRole("heading", { name: /^Harbour flat list · 7 to get/ })).toBeInTheDocument();
    const rows = within(panel).getAllByTestId("panel-item");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toHaveTextContent("dried chickpeas");
    expect(rows[0]).toHaveTextContent("2 lb · 1 cup");
    expect(panel).toHaveTextContent("and 2 more");
    expect(within(panel).getByRole("link", { name: "Open list" })).toHaveAttribute("href", "/plan/list");
    expect(within(panel).getByTestId("kitchen-tag")).toHaveTextContent("Harbour flat");
    expect(panel.className).toContain("hidden");
    expect(panel.className).toContain("lg:block");
    // Above Recent purchases.
    const recent = screen.getByRole("heading", { name: "Recent purchases" });
    expect(panel.compareDocumentPosition(recent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("adds by name alone: choosing an ingredient posts it with no amount", async () => {
    const user = userEvent.setup();
    const calls = mount({ "POST /plan/list/items": () => jsonResponse(201, { ...chickpeas, ingredient: { ...flour, category_key: "pantry" } }) });
    const panel = await screen.findByTestId("shopping-list-panel");
    await user.type(within(panel).getByRole("combobox", { name: "What do you need?" }), "flour");
    await user.click(await within(panel).findByRole("option", { name: /all-purpose flour/ }));
    const post = await waitFor(() => calls.find((c) => c.method === "POST" && c.path === "/plan/list/items")!);
    expect(post.body).toEqual({ home_base_id: kitchenId, kind: "ingredient", ingredient_id: flour.id, amounts: [], deferrable: false, note: null, added_from: "manual" });
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Added all-purpose flour to the Harbour flat list.");
    expect(within(notice).getByRole("link", { name: "Open list" })).toHaveAttribute("href", "/plan/list");
  });

  it("says Nothing on the list for a kitchen with nothing to get", async () => {
    mount({ "GET /plan/list": () => jsonResponse(200, list({ unchecked: [], checked: [] })) });
    const panel = await screen.findByTestId("shopping-list-panel");
    expect(await within(panel).findByRole("heading", { name: /^Harbour flat list · 0 to get/ })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Nothing on the list.");
    expect(within(panel).getByRole("combobox", { name: "What do you need?" })).toBeInTheDocument();
  });

  it("is absent, and asks nothing of the list, without the plan feature", async () => {
    const calls = mount({}, { status: "ok", features: ["catalog", "shop", "cook"] });
    await screen.findByRole("heading", { name: "Recent purchases" });
    expect(screen.queryByTestId("shopping-list-panel")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/plan/") || c.path === "/me/settings")).toBe(false);
  });
});
