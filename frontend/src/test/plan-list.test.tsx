import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import type { ShoppingItem } from "../api/shoppingList";
import { NoticeProvider } from "../components/Notice";
import { createQueryClient } from "../lib/queryClient";
import { ListPage } from "../pages/plan/ListPage";
import { flour, ingredientMatch, units } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "./helpers";
import { archivedList, archivedListId, archivedSummary, bayLeaves, chickpeas, kitchen, kitchenId, list, me, noKitchenSettings, oatMilk, settings } from "./plan-fixtures";
import { recipeList } from "./recipe-fixtures";

/**
 * The list page (17, 4A; UI-8.2, 8.3, 8.5, 8.6, 8.11). Mounted on its own route,
 * not through App: package 2 owns the route table, and this page only needs a
 * router, a query client and the Notice region.
 */

function baseRoutes(): Record<string, RouteHandler> {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /me/settings": () => jsonResponse(200, settings),
    "GET /home-bases": () => jsonResponse(200, { items: [kitchen] }),
    "GET /plan/list": () => jsonResponse(200, list()),
    "GET /plan/lists": () => jsonResponse(200, { items: [archivedSummary] }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour, { exact: true })] }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
  };
}

function mount(routes: Record<string, RouteHandler> = {}, path = "/plan/list"): RecordedCall[] {
  const calls = mockApi({ ...baseRoutes(), ...routes });
  const client = createQueryClient({ retry: false });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <main>
          <NoticeProvider>
            <Routes>
              <Route path="/plan/list" element={<ListPage />} />
              <Route path="/settings/kitchens" element={<h1>Kitchens</h1>} />
            </Routes>
          </NoticeProvider>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

/** A response the test completes later, to watch the optimistic state in between. */
function deferred() {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => (resolve = r));
  return { handler: () => promise, resolve };
}

/** A window narrower than lg (1024px), as a phone is. */
function phoneWidth() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }),
  });
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const toGet = async () => within(await screen.findByRole("region", { name: /To get/ })).getAllByTestId("list-item");
const notice = () => screen.findByTestId("notice");

describe("the list page: groups and rows (UI-8.2, UI-8.3, UI-8.5)", () => {
  it("heads the page with the kitchen, groups To get and Checked, and shows pairs as written", async () => {
    const calls = mount();
    expect(await screen.findByRole("heading", { level: 1, name: "Harbour flat list" })).toBeInTheDocument();
    expect(screen.getByText("What this kitchen needs next. Anyone can add, claim and check.")).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/plan/list?"))?.query.get("home_base_id")).toBe(kitchenId);

    const rows = await toGet();
    expect(screen.getByRole("heading", { name: "To get (3)" })).toBeInTheDocument();
    expect(rows.map((r) => within(r).getByTestId("item-name").textContent)).toEqual(["dried chickpeas", "Moonfield oat milk", "bay leaves"]);
    // Pairs that do not convert stay separate figures, never summed.
    expect(within(rows[0]).getByTestId("item-amounts")).toHaveTextContent("2 lb · 1 cup");
    // A product row shows its pack in a caption.
    expect(rows[1]).toHaveTextContent("946 ml");
    expect(rows[1]).toHaveTextContent("2 each");
    // Deferrable, the minimum quality and the recipe link are words, not colour.
    expect(within(rows[2]).getByText("deferrable")).toBeInTheDocument();
    expect(within(rows[2]).getByRole("img", { name: "4 of 5 stars" })).toBeInTheDocument();
    expect(within(rows[2]).getByRole("link", { name: "from recipe" })).toHaveAttribute("href", "/cook/recipes/r-barley");
    expect(rows[2]).toHaveTextContent("the fresh kind");

    // Checked is folded with its count; opened, it says who checked and when.
    const checked = screen.getByText("Checked (1)");
    expect(checked.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(checked);
    const checkedRow = within(screen.getByRole("region", { name: "Checked" })).getByTestId("list-item");
    expect(checkedRow).toHaveAttribute("data-checked", "true");
    expect(checkedRow).toHaveTextContent("Checked by Member Example");
    expect(within(checkedRow).getByRole("checkbox", { name: "Uncheck yellow onions" })).toBeChecked();
  });

  it("adds an item through the picker with the amount and unit as written, and keeps focus in the picker", async () => {
    const user = userEvent.setup();
    const added: ShoppingItem = { ...chickpeas, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7299", ingredient: { ...flour, category_key: "pantry" } };
    const calls = mount({ "POST /plan/list/items": () => jsonResponse(201, added) });
    await toGet();
    const form = screen.getByRole("form", { name: "Add an item" });
    const picker = within(form).getByRole("combobox", { name: "What do you need?" });
    await user.type(picker, "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    expect(within(form).getByTestId("add-list-item-ingredient-choice")).toHaveTextContent("all-purpose flour");
    // Focus moved on to the amount, so Enter from there adds.
    const amount = within(form).getByLabelText("Amount");
    await waitFor(() => expect(amount).toHaveFocus());
    await user.selectOptions(within(form).getByLabelText("Unit"), "lb");
    await user.type(amount, "2{Enter}");

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/plan/list/items")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.path === "/plan/list/items")!;
    expect(post.body).toEqual({
      home_base_id: kitchenId,
      kind: "ingredient",
      ingredient_id: flour.id,
      amounts: [{ amount: "2", unit: "lb" }],
      deferrable: false,
      note: null,
      added_from: "manual",
    });
    expect(post.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await notice()).toHaveTextContent("Added all-purpose flour.");
    await waitFor(() => expect(within(form).getByRole("combobox", { name: "What do you need?" })).toHaveFocus());
  });

  it("refuses an amount without a unit before anything is sent", async () => {
    const user = userEvent.setup();
    const calls = mount();
    await toGet();
    const form = screen.getByRole("form", { name: "Add an item" });
    await user.type(within(form).getByRole("combobox", { name: "What do you need?" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Amount"), "2");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose a unit for the amount.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("the list page: check, claim and stale edits (UI-8.6)", () => {
  it("moves a row to Checked at once and announces the count left", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    mount({ [`POST /plan/list/items/${chickpeas.id}/check`]: pending.handler });
    await toGet();
    await user.click(screen.getByRole("checkbox", { name: "Check dried chickpeas" }));
    // Optimistic: the row has moved before the server answers.
    expect(screen.getByRole("heading", { name: "To get (2)" })).toBeInTheDocument();
    expect(screen.getByText("Checked (2)")).toBeInTheDocument();
    await act(async () => {
      pending.resolve(jsonResponse(200, { ...chickpeas, checked_by: me, checked_at: "2026-10-12T10:00:00Z" }));
    });
    await waitFor(() => expect(screen.getByTestId("list-announcement")).toHaveTextContent("Checked dried chickpeas. 2 to get."));
  });

  it("puts the row back when the server refuses the check", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    mount({ [`POST /plan/list/items/${chickpeas.id}/check`]: pending.handler });
    await toGet();
    await user.click(screen.getByRole("checkbox", { name: "Check dried chickpeas" }));
    expect(screen.getByRole("heading", { name: "To get (2)" })).toBeInTheDocument();
    await act(async () => {
      pending.resolve(errorResponse(500, "server_error", "The list could not be saved."));
    });
    expect(await screen.findByRole("heading", { name: "To get (3)" })).toBeInTheDocument();
    expect(await notice()).toHaveTextContent("The list could not be saved.");
  });

  it("shows who is getting an item, offers I'll get it on an unclaimed row and Unclaim to the claimant", async () => {
    const user = userEvent.setup();
    const mine = { ...bayLeaves, claimed_by: me, claimed_at: "2026-10-12T09:00:00Z" };
    const calls = mount({
      "GET /plan/list": () => jsonResponse(200, list({ unchecked: [chickpeas, oatMilk, mine] })),
      [`POST /plan/list/items/${chickpeas.id}/claim`]: () => jsonResponse(200, { ...chickpeas, claimed_by: me }),
    });
    const [unclaimed, theirs, ours] = await toGet();
    expect(theirs).toHaveTextContent("Member Example is getting it");
    expect(within(theirs).queryByRole("button", { name: "Unclaim" })).not.toBeInTheDocument();
    expect(within(theirs).queryByRole("button", { name: "I'll get it" })).not.toBeInTheDocument();
    await waitFor(() => expect(ours).toHaveTextContent("You're getting it"));
    expect(within(ours).getByRole("button", { name: "Unclaim" })).toBeInTheDocument();

    await user.click(within(unclaimed).getByRole("button", { name: "I'll get it" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/plan/list/items/${chickpeas.id}/claim`)).toBe(true));
  });

  it("sends the updated_at it saw and, on a stale answer, shows the server's row and says so", async () => {
    const user = userEvent.setup();
    const current: ShoppingItem = { ...bayLeaves, note: "changed elsewhere", updated_at: "2026-10-12T11:00:00Z" };
    let patched = false;
    const calls = mount({
      "GET /plan/list": () => jsonResponse(200, patched ? list({ unchecked: [chickpeas, oatMilk, current] }) : list()),
      [`PATCH /plan/list/items/${bayLeaves.id}`]: () => {
        patched = true;
        return errorResponse(409, "stale_item", "This item changed since you loaded it; reload.", { item: current });
      },
    });
    const rows = await toGet();
    await user.click(within(rows[2]).getByRole("button", { name: "More actions for bay leaves" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit bay leaves" });
    const note = within(drawer).getByLabelText("Note");
    await user.clear(note);
    await user.type(note, "dried is fine");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    const patch = await waitFor(() => calls.find((c) => c.method === "PATCH")!);
    expect(patch.body).toMatchObject({ updated_at: bayLeaves.updated_at, note: "dried is fine", deferrable: true, min_quality: 4, amounts: [] });
    expect(await notice()).toHaveTextContent("Updated by someone else; showing the latest.");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect((await toGet())[2]).toHaveTextContent("changed elsewhere");
  });

  it("asks before closing an edit drawer with typed input", async () => {
    const user = userEvent.setup();
    mount();
    const rows = await toGet();
    await user.click(within(rows[0]).getByRole("button", { name: "More actions for dried chickpeas" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit dried chickpeas" });
    await user.type(within(drawer).getByLabelText("Note"), "x");
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent("Discard this change?");
    expect(within(drawer).getByRole("button", { name: "Keep editing" })).toHaveFocus();
  });
});

describe("the list page: from a recipe and a fresh list (UI-8.2, UI-8.5)", () => {
  it("adds a recipe at a scale, and offers Replace when it is already on the list", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const calls = mount({
      "POST /plan/list/items/from-recipe": (call) => {
        attempt += 1;
        const body = call.body as { replace: boolean };
        if (!body.replace) return jsonResponse(200, { list: list(), added: 0, merged: 0, skipped: [], already_on_list: true, existing_items: 2 });
        return jsonResponse(200, { list: list(), added: 3, merged: 1, skipped: [{ raw_name: "mystery root", reason: "unmatched" }], already_on_list: false, existing_items: 0 });
      },
    });
    await toGet();
    await user.click(screen.getByRole("button", { name: "Add from recipe" }));
    const drawer = screen.getByRole("dialog", { name: "Add from recipe" });
    await user.type(within(drawer).getByRole("combobox", { name: "Recipe" }), "barley");
    await user.click(await within(drawer).findByRole("option", { name: /Barley moon stew/ }));
    expect(within(drawer).getByTestId("add-from-recipe-recipe-choice")).toHaveTextContent("Barley moon stew · 4 servings");
    const scale = within(drawer).getByLabelText("Scale");
    await user.clear(scale);
    await user.type(scale, "2");
    expect(drawer).toHaveTextContent("4 servings · ×2");
    await user.click(within(drawer).getByRole("button", { name: "Add to list" }));

    expect(await within(drawer).findByTestId("already-on-list")).toHaveTextContent("Barley moon stew is already on the list: 2 items to get. Nothing was changed.");
    expect(calls.filter((c) => c.method === "POST")[0].body).toEqual({ home_base_id: kitchenId, recipe_id: "r-barley", scale: "2", replace: false });
    await user.click(within(drawer).getByRole("button", { name: "Replace at ×2" }));
    await waitFor(() => expect(attempt).toBe(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toMatchObject({ recipe_id: "r-barley", scale: "2", replace: true });
    const shown = await notice();
    expect(shown).toHaveTextContent("Added 3 items from Barley moon stew. 1 merged into an item already on the list. 1 unmapped line skipped.");
    expect(within(shown).getByRole("link", { name: "Resolve them" })).toHaveAttribute("href", "/cook/recipes/resolve");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("starts a fresh list after an inline confirmation, carrying unchecked items over unless unticked", async () => {
    const user = userEvent.setup();
    const fresh = list({ id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7003", unchecked: [], checked: [] });
    let archived = false;
    const calls = mount({
      "GET /plan/list": () => jsonResponse(200, archived ? fresh : list()),
      "POST /plan/list/archive": () => {
        archived = true;
        return jsonResponse(200, { archived: { ...list(), id: archivedListId, status: "archived", archived_at: "2026-10-12T12:00:00Z" }, list: fresh });
      },
    });
    await toGet();
    await user.click(screen.getByRole("button", { name: "Start a fresh list" }));
    const confirm = screen.getByTestId("fresh-list-confirm");
    expect(confirm).toHaveTextContent("Archive this list and start a new one? 3 unchecked items carry over.");
    const carry = within(confirm).getByRole("checkbox", { name: "Carry unchecked items over" });
    expect(carry).toBeChecked();
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(carry);
    expect(confirm).toHaveTextContent("3 unchecked items stay on the old list.");
    await user.click(within(confirm).getByRole("button", { name: "Start a fresh list" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/plan/list/archive")).toBe(true));
    expect(calls.find((c) => c.path === "/plan/list/archive")!.body).toEqual({ home_base_id: kitchenId, carry_over: false });
    const shown = await notice();
    expect(shown).toHaveTextContent("Started a fresh list.");
    expect(within(shown).getByRole("link", { name: "Open the old one" })).toHaveAttribute("href", `/plan/list?list_id=${archivedListId}`);
    expect(await screen.findByText("Nothing on the list.")).toBeInTheDocument();
  });

  it("reads an archived list without offering to change it", async () => {
    const calls = mount({ "GET /plan/list": () => jsonResponse(200, archivedList) }, `/plan/list?list_id=${archivedListId}`);
    expect(await screen.findByRole("heading", { level: 1, name: /Harbour flat list · archived/ })).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/plan/list?"))?.query.get("list_id")).toBe(archivedListId);
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the current list" })).toHaveAttribute("href", "/plan/list");
    expect(screen.getByRole("status")).toHaveTextContent("nothing on it can change");
    expect(screen.queryByRole("form", { name: "Add an item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /More actions/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "I'll get it" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Check dried chickpeas" })).toBeDisabled();
  });

  it("lists previous lists on request, each readable by its id", async () => {
    const user = userEvent.setup();
    const calls = mount();
    await toGet();
    expect(calls.some((c) => c.path.startsWith("/plan/lists"))).toBe(false);
    await user.click(screen.getByText("Previous lists"));
    const link = await screen.findByRole("link", { name: /^Archived/ });
    expect(link).toHaveAttribute("href", `/plan/list?list_id=${archivedListId}`);
    expect(link.closest("li")).toHaveTextContent("2 items · 1 never checked");
  });
});

describe("the list page: states and the phone (G11, UI-8.11)", () => {
  it("names the three ways to add on an empty list", async () => {
    mount({ "GET /plan/list": () => jsonResponse(200, list({ unchecked: [], checked: [] })) });
    const empty = await screen.findByRole("region", { name: "Nothing on the list." });
    expect(empty).toHaveTextContent("Add what you need above, add a recipe's ingredients with “Add from recipe”, or add an ingredient from its own page.");
    expect(screen.getByRole("combobox", { name: "What do you need?" })).toHaveFocus();
  });

  it("sends to Settings → Kitchens when there is no kitchen, and asks for no list", async () => {
    const calls = mount({ "GET /me/settings": () => jsonResponse(200, noKitchenSettings) });
    const empty = await screen.findByRole("region", { name: "No kitchen yet" });
    expect(within(empty).getByRole("link", { name: "Settings → Kitchens" })).toHaveAttribute("href", "/settings/kitchens");
    expect(calls.some((c) => c.path.startsWith("/plan/list"))).toBe(false);
  });

  it("shows a load error with Try again, never the empty state", async () => {
    const user = userEvent.setup();
    let failures = 1;
    mount({
      "GET /plan/list": () => {
        if (failures > 0) {
          failures -= 1;
          return errorResponse(500, "server_error", "The database is away.");
        }
        return jsonResponse(200, list());
      },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load the list: The database is away.");
    expect(screen.queryByText("Nothing on the list.")).not.toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "To get (3)" })).toBeInTheDocument();
  });

  it("on a phone, folds the secondary actions into More actions and keeps every control at 44px", async () => {
    phoneWidth();
    const user = userEvent.setup();
    mount();
    const rows = await toGet();
    expect(screen.queryByRole("button", { name: "Add from recipe" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    const sheet = screen.getByRole("dialog", { name: "More actions" });
    expect(within(sheet).getByRole("button", { name: "Add from recipe" })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Start a fresh list" })).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Close" }));

    const circle = within(rows[0]).getByRole("checkbox", { name: "Check dried chickpeas" });
    expect(circle.className).toContain("size-11");
    expect(circle.querySelector("span")?.className).toContain("size-7");
    expect(within(rows[0]).getByRole("button", { name: "I'll get it" }).className).toContain("min-h-11");
    expect(screen.getByRole("button", { name: "Add" }).className).toContain("min-h-11");
  });
});
