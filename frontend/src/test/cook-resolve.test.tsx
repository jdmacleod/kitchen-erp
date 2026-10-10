import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngredientMatch } from "../api/catalog";
import type { ResolveDecision, ResolveQueue } from "../api/recipes";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { cookHealth, emptyQueue, mountedStatus, mysteryRoot, parchment, proposal, quietInbox, recipeList, resolveQueue } from "./recipe-fixtures";

// Invented ingredients the picker can find (1G).
const scallion: IngredientMatch = { kind: "ingredient", id: "i-scallion", key: null, name: "scallion", canonical_unit: "g", active: true, category: "produce", category_key: "produce", matched_spelling: null, exact: true };

function decided(over: Partial<ResolveDecision> = {}): ResolveDecision {
  return { name_norm: "minced garlic", action: "matched", ingredient: { id: "i-garlic", name: "garlic", canonical_unit: "g", active: true, category: "produce", category_key: "produce" }, lines: 4, recipes: 3, remaining: 2, ...over };
}

/**
 * The queue as a server would keep it: a decision that answers 200 takes its
 * name out of the next listing, which the mutation refetches.
 */
function mount(routes: Record<string, RouteHandler> = {}, queue: ResolveQueue = resolveQueue) {
  const resolved = new Set<string>();
  const post = routes["POST /recipes/resolve"] ?? (() => jsonResponse(200, decided()));
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /ingredients/search": () => jsonResponse(200, { items: [scallion] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /recipes/resolve": () => {
      const items = queue.items.filter((i) => !resolved.has(i.name_norm));
      return jsonResponse(200, { ...queue, items, names: items.length, recipes: new Set(items.flatMap((i) => i.recipes.map((r) => r.id))).size });
    },
    ...routes,
    "POST /recipes/resolve": async (call) => {
      const response = await post(call);
      if (response.status === 200) resolved.add((call.body as { name_norm: string }).name_norm);
      return response;
    },
  });
  renderApp("/cook/recipes/resolve");
  return calls;
}

const rows = async () => within(await screen.findByRole("main")).findAllByTestId("resolve-row");
const decisions = (calls: RecordedCall[]) => calls.filter((c) => c.method === "POST" && c.path === "/recipes/resolve").map((c) => c.body);
const notice = () => screen.findByTestId("notice");

describe("the resolve page (UI-7.16)", () => {
  it("lists names most-used first with their recipes, three proposals badged by tier, the picker and Not an ingredient", async () => {
    mount();
    const main = await screen.findByRole("main");
    expect(await within(main).findByRole("heading", { level: 1, name: "Resolve recipe names" })).toBeInTheDocument();
    expect(main).toHaveTextContent("Say once which ingredient each name means. It's remembered for every recipe.");
    expect(within(main).queryByRole("button", { name: "Rescan recipes" })).not.toBeInTheDocument();
    expect(within(main).getByTestId("resolve-counts")).toHaveTextContent("3 names · in 3 recipes");
    expect(within(main).getByTestId("resolve-counts").className).toContain("sticky");

    const [garlic, root, paper] = await rows();
    expect(garlic).toHaveTextContent("minced garlic");
    expect(garlic).toHaveTextContent("also written ‘Minced Garlic’");
    expect(root).toHaveTextContent("mystery root");
    expect(paper).toHaveTextContent("parchment paper");

    // Two titles as links, then "+1 more".
    expect(garlic).toHaveTextContent("in 3 recipes:");
    expect(within(garlic).getByRole("link", { name: "Barley moon stew" })).toHaveAttribute("href", "/cook/recipes/r-barley");
    expect(within(garlic).getByRole("link", { name: "Comet crumble" })).toBeInTheDocument();
    expect(within(garlic).queryByRole("link", { name: "Harbor flatbread" })).not.toBeInTheDocument();
    expect(garlic).toHaveTextContent("+1 more");

    // Up to three proposals, each badged by tier; the model's guess in a squash outline.
    const proposals = within(within(garlic).getByRole("group", { name: "Proposals for minced garlic" })).getAllByRole("button").filter((b) => b.hasAttribute("data-proposal"));
    expect(proposals.map((b) => b.textContent)).toEqual(["garlic without 'minced'", "garlic standard list", "garlic powder model"]);
    expect(proposals[2].className).toContain("border-amber-400");
    expect(proposals[0].className).not.toContain("amber");
    expect(garlic).not.toHaveTextContent("USDA");
    // Nothing but the focus ring is blue: proposals are neutral, the model's squash (UI-7.19).
    for (const b of proposals) expect(b.className).not.toMatch(/bg-blue|text-blue/);

    expect(within(garlic).getByRole("combobox", { name: "Choose an ingredient" })).toBeInTheDocument();
    expect(within(garlic).getByRole("button", { name: "Not an ingredient" })).toBeInTheDocument();
    expect(within(garlic).getByRole("button", { name: "Ask the model" })).toBeInTheDocument();
    expect(within(paper).queryAllByRole("button").filter((b) => b.hasAttribute("data-proposal"))).toHaveLength(0);
  });

  it("a proposal's decision removes the group, updates the count, shows the Notice and moves focus to the next row", async () => {
    const calls = mount();
    const user = userEvent.setup();
    const [garlic] = await rows();
    await user.click(within(garlic).getByRole("button", { name: "garlic without 'minced'" }));

    expect(decisions(calls)).toEqual([{ name_norm: "minced garlic", ingredient_id: "i-garlic", note: "minced" }]);
    await waitFor(() => expect(screen.queryByText("also written ‘Minced Garlic’")).not.toBeInTheDocument());
    const main = screen.getByRole("main");
    expect(within(main).getAllByTestId("resolve-row")).toHaveLength(2);
    expect(within(main).getByTestId("resolve-counts")).toHaveTextContent("2 names · in 2 recipes");
    expect(await notice()).toHaveTextContent("Resolved 'minced garlic' as garlic: 4 lines in 3 recipes. 2 left.");
    // Focus moves to the next row's first proposal (UI-7.12).
    await waitFor(() => expect(within(main).getByRole("button", { name: "celery root similar" })).toHaveFocus());
  });

  it("a standard-list proposal creates the ingredient from its entry", async () => {
    const calls = mount({ "POST /recipes/resolve": () => jsonResponse(200, decided({ action: "created" })) });
    const user = userEvent.setup();
    const [garlic] = await rows();
    await user.click(within(garlic).getByRole("button", { name: "garlic standard list" }));
    expect(decisions(calls)).toEqual([{ name_norm: "minced garlic", ingredient: { name: "garlic", standard_key: "garlic" } }]);
  });

  it("a collision shows the holder with Use {holder}, which re-posts with that id, and Choose another gives the picker back", async () => {
    let posts = 0;
    const calls = mount({
      "POST /recipes/resolve": (call) => {
        posts += 1;
        if ((call.body as { ingredient_id?: string }).ingredient_id === "i-green-onion") return jsonResponse(200, decided({ ingredient: { id: "i-green-onion", name: "green onion", canonical_unit: "g", active: true, category: null, category_key: null } }));
        return errorResponse(409, "alias_taken", "That name is already a spelling of another ingredient.", { holder: "green onion", holder_id: "i-green-onion" });
      },
    });
    const user = userEvent.setup();
    const [garlic] = await rows();
    const box = within(garlic).getByRole("combobox", { name: "Choose an ingredient" });
    await user.type(box, "scal");
    const listbox = await within(garlic).findByRole("listbox", { name: "Ingredients" });
    await waitFor(() => expect(within(listbox).getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{ArrowDown}{Enter}");

    const collision = await within(garlic).findByTestId("alias-collision");
    expect(collision).toHaveTextContent("‘minced garlic’ is already a spelling of green onion.");
    expect(posts).toBe(1);
    // Nothing is written until one is pressed; the picker is back.
    expect(within(garlic).getByRole("combobox", { name: "Choose an ingredient" })).toBeInTheDocument();
    await user.click(within(collision).getByRole("button", { name: "Choose another" }));
    expect(within(garlic).queryByTestId("alias-collision")).not.toBeInTheDocument();

    await user.type(within(garlic).getByRole("combobox", { name: "Choose an ingredient" }), "scal");
    await waitFor(() => expect(within(garlic).getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{ArrowDown}{Enter}");
    await user.click(await within(garlic).findByRole("button", { name: "Use green onion" }));
    expect(decisions(calls)).toEqual([
      { name_norm: "minced garlic", ingredient_id: "i-scallion" },
      { name_norm: "minced garlic", ingredient_id: "i-scallion" },
      { name_norm: "minced garlic", ingredient_id: "i-green-onion" },
    ]);
    await waitFor(() => expect(screen.getAllByTestId("resolve-row")).toHaveLength(2));
    expect(await notice()).toHaveTextContent("Resolved 'minced garlic' as green onion");
  });

  it("an inactive ingredient's refusal shows its message at the row", async () => {
    mount({ "POST /recipes/resolve": () => errorResponse(409, "ingredient_inactive", "That ingredient is inactive; activate it first.") });
    const user = userEvent.setup();
    const [garlic] = await rows();
    await user.click(within(garlic).getByRole("button", { name: "garlic without 'minced'" }));
    expect(await within(garlic).findByRole("alert")).toHaveTextContent("That ingredient is inactive; activate it first.");
    expect(screen.getAllByTestId("resolve-row")).toHaveLength(3);
  });

  it("Not an ingredient records the name as ignored", async () => {
    const calls = mount({ "POST /recipes/resolve": () => jsonResponse(200, decided({ name_norm: "parchment paper", action: "ignored", ingredient: null, lines: 1, recipes: 1, remaining: 2 })) });
    const user = userEvent.setup();
    const [, , paper] = await rows();
    await user.click(within(paper).getByRole("button", { name: "Not an ingredient" }));
    expect(decisions(calls)).toEqual([{ name_norm: "parchment paper", ignore: true }]);
    expect(await notice()).toHaveTextContent("Ignored 'parchment paper'. It won't be asked about again. 2 left.");
    await waitFor(() => expect(screen.getAllByTestId("resolve-row")).toHaveLength(2));
  });

  it("Ask the model replaces the name's proposals, or says No suggestion", async () => {
    const calls = mount({
      "POST /recipes/resolve/ask": (call) => {
        const name = (call.body as { name_norm: string }).name_norm;
        if (name === "parchment paper") return jsonResponse(200, { name_norm: name, proposals: [], model_asked: true });
        return jsonResponse(200, { name_norm: name, proposals: [proposal({ tier: "model", name: "celeriac", ingredient_id: "i-celeriac" })], model_asked: true });
      },
    });
    const user = userEvent.setup();
    const [, root, paper] = await rows();
    await user.click(within(root).getByRole("button", { name: "Ask the model" }));
    expect(await within(root).findByRole("button", { name: "celeriac model" })).toHaveClass("border-amber-400");
    expect(within(root).queryByRole("button", { name: "celery root similar" })).not.toBeInTheDocument();
    await user.click(within(paper).getByRole("button", { name: "Ask the model" }));
    expect(await within(paper).findByText("No suggestion")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/recipes/resolve/ask").map((c) => c.body)).toEqual([{ name_norm: "mystery root" }, { name_norm: "parchment paper" }]);
  });

  it("hides Ask the model when no model is configured", async () => {
    mount({}, { ...resolveQueue, model_configured: false });
    const [garlic] = await rows();
    expect(within(garlic).queryByRole("button", { name: "Ask the model" })).not.toBeInTheDocument();
  });

  it("finishes with the counts and Back to Recipes; an empty queue says so from the start", async () => {
    mount({ "POST /recipes/resolve": () => jsonResponse(200, decided({ name_norm: "mystery root", remaining: 0, lines: 2, recipes: 1 })) }, { items: [mysteryRoot], names: 1, recipes: 1, model_configured: false });
    const user = userEvent.setup();
    const [root] = await rows();
    await user.click(within(root).getByRole("button", { name: "celery root similar" }));
    const main = screen.getByRole("main");
    expect(await within(main).findByText("Every recipe name is resolved")).toBeInTheDocument();
    expect(within(main).getByRole("link", { name: "Back to Recipes" })).toHaveAttribute("href", "/cook/recipes");
    expect(await notice()).toHaveTextContent("1 resolved: 1 matched.");
  });

  it("shows the empty state for an empty queue and an alert with Try again on failure", async () => {
    mount({}, emptyQueue);
    expect(await screen.findByText("Every recipe name is resolved")).toBeInTheDocument();
  });

  it("shows an alert with Try again when the queue cannot load", async () => {
    let failures = 0;
    mount({
      "GET /recipes/resolve": () => {
        failures += 1;
        return failures === 1 ? errorResponse(500, "internal", "boom") : jsonResponse(200, { items: [parchment], names: 1, recipes: 1, model_configured: false });
      },
    });
    const main = await screen.findByRole("main");
    const alert = await within(main).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load the names: boom");
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await within(main).findByText("parchment paper")).toBeInTheDocument();
  });

  it("can be worked without a pointer: Tab to a proposal, Enter chooses, focus moves on, the picker opens on typing", async () => {
    let n = 0;
    const calls = mount({
      "POST /recipes/resolve": (call) => {
        n += 1;
        const name = (call.body as { name_norm: string }).name_norm;
        return jsonResponse(200, decided({ name_norm: name, remaining: 3 - n, lines: 1, recipes: 1 }));
      },
    });
    const user = userEvent.setup();
    const [garlic] = await rows();
    const first = within(garlic).getByRole("button", { name: "garlic without 'minced'" });
    first.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("resolve-row")).toHaveLength(2));
    // The next row's first proposal has focus; Enter chooses it too.
    const root = screen.getAllByTestId("resolve-row")[0];
    await waitFor(() => expect(within(root).getByRole("button", { name: "celery root similar" })).toHaveFocus());
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("resolve-row")).toHaveLength(1));
    // The last row has no proposal, so its picker takes focus; typing opens the list and Enter chooses.
    const paper = screen.getAllByTestId("resolve-row")[0];
    await waitFor(() => expect(within(paper).getByRole("combobox", { name: "Choose an ingredient" })).toHaveFocus());
    await user.keyboard("scal");
    await waitFor(() => expect(within(paper).getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(decisions(calls)).toHaveLength(3));
    expect(decisions(calls)[2]).toEqual({ name_norm: "parchment paper", ingredient_id: "i-scallion" });
    expect(await screen.findByText("Every recipe name is resolved")).toBeInTheDocument();
  });
});
