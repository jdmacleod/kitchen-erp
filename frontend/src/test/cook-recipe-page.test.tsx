import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CostBasis } from "../api/recipes";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";
import { cookHealth, emptyHistory, history, lanternLentils, mountedStatus, quietInbox, recipeList, stewCost, stewRecipe } from "./recipe-fixtures";

function mount(routes: Record<string, RouteHandler> = {}, path = "/cook/recipes/r-barley") {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": (call) => jsonResponse(200, stewCost({ basis: (call.query.get("basis") as CostBasis | null) ?? "latest" })),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    ...routes,
  });
  renderApp(path);
  return calls;
}

const costRows = () => within(screen.getByRole("main")).getAllByTestId("cost-row");
const costCalls = (calls: RecordedCall[]) => calls.filter((c) => c.method === "GET" && c.path.startsWith("/recipes/r-barley/cost?"));

describe("the recipe page (UI-7.7, UI-7.9)", () => {
  it("renders the file beside the cost table, read-only, with the breadcrumb and header", async () => {
    mount();
    const main = await screen.findByRole("main");
    expect(await within(main).findByRole("heading", { level: 1, name: "Barley moon stew" })).toBeInTheDocument();
    expect(within(main).getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Cook / Recipes");
    expect(main).toHaveTextContent("soups/barley_moon_stew.cook");
    expect(main).toHaveTextContent("Serves 4");
    expect(within(main).getByRole("button", { name: "Rescan recipes" })).toBeInTheDocument();
    expect(within(main).getByRole("link", { name: "Jump to recipe" })).toHaveAttribute("href", "#recipe");

    const recipe = within(main).getByRole("region", { name: "Recipe" });
    // Front matter as a meta block, the section as a heading, and the lines as written.
    const meta = recipe.querySelector("dl")!;
    expect(meta).toHaveTextContent("servings4");
    expect(meta).toHaveTextContent("tagssoup, winter");
    expect(meta).toHaveTextContent("sourcea family card");
    expect(within(recipe).getByRole("heading", { name: "Broth" })).toBeInTheDocument();
    expect(within(recipe).getByRole("list", { name: "Ingredients" })).toHaveTextContent("pearl barley1 cup(rinsed)");
    expect(within(recipe).getByRole("list", { name: "Broth ingredients" })).toHaveTextContent("ground fennel1–2 tsp");
    expect(recipe).toHaveTextContent("Edit this recipe in its file; it updates here on the next scan.");
    expect(within(recipe).queryByRole("textbox")).not.toBeInTheDocument();

    // The cost table sits in the wider column (1 : 1.4) and the recipe is second in reading order at lg.
    const grid = recipe.parentElement!.parentElement!;
    expect(grid.className).toContain("lg:grid-cols-[1fr_1.4fr]");
    expect(recipe.parentElement!.className).toContain("lg:order-1");
  });

  it("shows each line's words, marks and figures, never by colour alone", async () => {
    mount();
    await within((await screen.findByRole("main"))).findByRole("table", { name: "Cost by line" });
    const [barley, onion, root, kelp, fennel, salt] = costRows();

    expect(barley).toHaveTextContent("1 cup pearl barley (rinsed)");
    expect(within(barley).getByRole("link", { name: "pearl barley" })).toHaveAttribute("href", "/catalog/ingredients/i-pearl-barley");
    expect(barley).toHaveTextContent("0.44 lb");
    expect(barley).toHaveTextContent("yield 100% assumed");
    expect(barley).toHaveTextContent("$2.99/lb");
    expect(barley).toHaveTextContent("Moonfield pearl barley 1 lb · Harbor Grocer ·");
    expect(barley).toHaveTextContent("$1.32");
    expect(barley).toHaveTextContent("1 pack · $2.99");
    expect(within(barley).queryByText("stale")).not.toBeInTheDocument();

    expect(onion).toHaveTextContent("$1.29 each");
    expect(within(onion).getByText("stale")).toHaveClass("bg-amber-100");
    expect(onion).not.toHaveTextContent("yield 100% assumed");

    expect(root).toHaveAttribute("data-status", "unmapped");
    expect(root).toHaveTextContent("a handful mystery root");
    // The ingredient cell is the picker (UI-7.10); the price cell keeps the words.
    expect(within(root).getByRole("combobox", { name: "Choose an ingredient" })).toBeInTheDocument();
    expect(within(root).getAllByText("Needs an ingredient")).toHaveLength(1);
    expect(within(root).getAllByText("—").length).toBeGreaterThan(0);

    expect(kelp).toHaveTextContent("No price yet ·");
    expect(within(kelp).getByRole("link", { name: "Log a shelf price" })).toHaveAttribute("href", "/shop/shelf-prices");
    expect(kelp).toHaveTextContent("1.06 oz");

    expect(fennel).toHaveTextContent("Can't convert yet ·");
    expect(within(fennel).getByRole("link", { name: "add density" })).toHaveAttribute("href", "/catalog/ingredients/i-ground-fennel#density-heading");

    expect(salt).toHaveAttribute("data-status", "negligible");
    expect(within(salt).getAllByText("Not costed").length).toBeGreaterThan(0);

    // Nothing in the table is blue but the links (UI-7.19).
    for (const row of [barley, onion, root, kelp, fennel, salt]) {
      for (const el of row.querySelectorAll("span")) expect(el.className).not.toMatch(/blue/);
    }
  });

  it("totals: low–high, per serving, completeness in squash words and the unconfirmed share", async () => {
    mount();
    const totals = await within((await screen.findByRole("main"))).findByTestId("cost-totals");
    await within(totals).findByText("$12.40–13.10");
    expect(totals).toHaveTextContent("Consumed$12.40–13.10");
    expect(totals).toHaveTextContent("Basket$31.96");
    expect(totals).toHaveTextContent("Per serving$3.10");
    expect(within(totals).getByText("3 of 6 lines priced")).toHaveClass("text-amber-900");
    expect(within(totals).getByTestId("unconfirmed-share")).toHaveTextContent("12% rests on unconfirmed bridges");
    expect(within(totals).queryByText("Provisional")).not.toBeInTheDocument();
  });

  it("labels a provisional snapshot with its reason", async () => {
    mount({ "GET /recipes/r-barley": () => jsonResponse(200, { ...stewRecipe, dirty: true }), "GET /recipes/r-barley/cost": () => jsonResponse(200, stewCost({ provisional: true, unconfirmed_share: "0" })) });
    const totals = await within((await screen.findByRole("main"))).findByTestId("cost-totals");
    expect(await within(totals).findByText("Provisional")).toHaveClass("bg-neutral-200");
    expect(totals).toHaveTextContent("The file has uncommitted changes.");
    expect(within(totals).queryByTestId("unconfirmed-share")).not.toBeInTheDocument();
    expect(within((await screen.findByRole("main"))).getByText("Uncommitted")).toBeInTheDocument();
  });
});

describe("the basis control (UI-7.8)", () => {
  it("offers Latest, Average (90 days) and Cheapest, keeps the choice in the URL and asks the server for it", async () => {
    let release: (() => void) | null = null;
    const calls = mount({
      "GET /recipes/r-barley/cost": (call) => {
        const basis = call.query.get("basis");
        if (basis === "cheapest") {
          return new Promise<Response>((resolve) => {
            release = () => resolve(jsonResponse(200, stewCost({ basis: "cheapest", totals: { ...stewCost().totals, consumed_cost: "9.0000", consumed_cost_high: "9.0000" } })));
          });
        }
        return jsonResponse(200, stewCost({ basis: (basis as CostBasis | null) ?? "latest" }));
      },
    });
    const main = await screen.findByRole("main");
    const control = await within(main).findByRole("group", { name: "Basis" });
    expect(within(control).getAllByRole("button").map((b) => b.textContent)).toEqual(["Latest", "Average (90 days)", "Cheapest"]);
    expect(within(control).getByRole("button", { name: "Latest" })).toHaveAttribute("aria-pressed", "true");
    expect(costCalls(calls)[0].query.get("basis")).toBe("latest");
    await within(main).findByText("$12.40–13.10");

    const user = userEvent.setup();
    await user.click(within(control).getByRole("button", { name: "Cheapest" }));
    expect(within(control).getByRole("button", { name: "Cheapest" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(costCalls(calls).at(-1)?.query.get("basis")).toBe("cheapest"));
    // A basis without a snapshot computes one: "Costing…" until it answers.
    const totals = within(main).getByTestId("cost-totals");
    expect(within(totals).getAllByText("Costing…").length).toBeGreaterThan(0);
    release!();
    await within(totals).findByText("$9.00");
    expect(within(totals).queryByText("Costing…")).not.toBeInTheDocument();
    // The cheapest basis's figures are not coloured (UI-7.19).
    expect(within(totals).getByText("$9.00").className).not.toMatch(/green|blue/);
  });

  it("reads the basis from the URL on arrival", async () => {
    const calls = mount({}, "/cook/recipes/r-barley?basis=average");
    const control = await within((await screen.findByRole("main"))).findByRole("group", { name: "Basis" });
    expect(within(control).getByRole("button", { name: "Average (90 days)" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(costCalls(calls)[0].query.get("basis")).toBe("average"));
    expect(calls.find((c) => c.path.startsWith("/recipes/r-barley/cost/history"))?.query.get("basis")).toBe("average");
  });
});

describe("the cost history chart (UI-7.13)", () => {
  it("hides with fewer than two points", async () => {
    mount({ "GET /recipes/r-barley/cost/history": () => jsonResponse(200, history("latest", ["11.0000"])) });
    await within((await screen.findByRole("main"))).findByTestId("cost-totals");
    await within((await screen.findByRole("main"))).findByRole("table", { name: "Cost by line" });
    expect(within((await screen.findByRole("main"))).queryByTestId("snapshot-chart")).not.toBeInTheDocument();
  });

  it("draws committed snapshots with the provisional one hollow and labelled", async () => {
    mount({
      "GET /recipes/r-barley/cost/history": () => jsonResponse(200, history("latest", ["11.0000", "11.8000"])),
      "GET /recipes/r-barley/cost": () => jsonResponse(200, stewCost({ provisional: true, computed_at: "2026-10-09T11:05:00Z" })),
    });
    const chart = await within((await screen.findByRole("main"))).findByTestId("snapshot-chart");
    expect(within(chart).getAllByTestId("snapshot-point")).toHaveLength(2);
    const provisional = within(chart).getByTestId("provisional-point");
    expect(provisional).toHaveTextContent("provisional");
    expect(provisional.querySelector("circle")).toHaveAttribute("fill", "var(--color-white)");
    // Series colours come from the chart palette only.
    expect(chart.querySelector("polyline")).toHaveAttribute("stroke", "#4C82C6");
  });
});

describe("parse errors and missing files (UI-7.15)", () => {
  it("shows a parse error in a squash alert with the last good cost table beneath", async () => {
    mount({
      "GET /recipes/r-barley": () => jsonResponse(200, { ...stewRecipe, status: "parse_error", parse_error_message: "unclosed ingredient at line 7" }),
    });
    const main = await screen.findByRole("main");
    const alert = await within(main).findByText(/This file can't be read: unclosed ingredient at line 7\./);
    expect(alert.closest("[role=status]")).toHaveClass("border-amber-300");
    expect(within(main).getByText("Can't read")).toHaveClass("bg-red-100");
    expect(await within(main).findByRole("table", { name: "Cost by line" })).toBeInTheDocument();
    expect(within(main).getByText("Showing the last version that could be read.")).toBeInTheDocument();
  });

  it("offers Relink and Not the same on a missing recipe's proposal", async () => {
    let dismissed = false;
    const missing = () => ({
      ...stewRecipe,
      status: "missing",
      relink: dismissed ? null : { target_id: "r-new", path: "soups/barley_stew_v2.cook", title: "Barley moon stew (v2)", reason: "same title" },
    });
    const calls = mount({
      "GET /recipes/r-barley": () => jsonResponse(200, missing()),
      "POST /recipes/r-barley/relink": () => jsonResponse(200, { ...stewRecipe, id: "r-barley", path: "soups/barley_stew_v2.cook" }),
      // "Not the same" dismisses on the server, so no later scan proposes that file again.
      "POST /recipes/r-barley/relink/dismiss": () => {
        dismissed = true;
        return jsonResponse(200, missing());
      },
    });
    const main = await screen.findByRole("main");
    const alert = await within(main).findByText("This file is no longer in the repository. Its costs and pins are kept until you remove it.");
    expect(alert.closest("[role=status]")).toHaveClass("border-neutral-300");
    const proposal = within(main).getByTestId("relink-proposal");
    expect(proposal).toHaveTextContent("Is it now ‘Barley moon stew (v2)’ (soups/barley_stew_v2.cook)?");
    const user = userEvent.setup();
    await user.click(within(proposal).getByRole("button", { name: "Not the same" }));
    await waitFor(() => expect(within(main).queryByTestId("relink-proposal")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST" && c.path === "/recipes/r-barley/relink/dismiss")).toBe(true);
    expect(calls.some((c) => c.method === "POST" && c.path === "/recipes/r-barley/relink")).toBe(false);
  });

  it("relinks on confirm", async () => {
    const calls = mount({
      "GET /recipes/r-barley": () =>
        jsonResponse(200, { ...stewRecipe, status: "missing", relink: { target_id: "r-new", path: "soups/barley_stew_v2.cook", title: "Barley moon stew (v2)", reason: "same title" } }),
      "POST /recipes/r-barley/relink": () => jsonResponse(200, stewRecipe),
    });
    const proposal = await within((await screen.findByRole("main"))).findByTestId("relink-proposal");
    await userEvent.setup().click(within(proposal).getByRole("button", { name: "Relink" }));
    const post = await waitFor(() => calls.find((c) => c.method === "POST" && c.path === "/recipes/r-barley/relink")!);
    expect(post.body).toEqual({ target_id: "r-new" });
    expect(await screen.findByTestId("notice")).toHaveTextContent("Relinked to Barley moon stew (v2).");
  });

  it("removes a missing recipe after an inline confirmation with focus on Cancel, then opens the list with the Notice", async () => {
    const calls = mount({
      "GET /recipes/r-barley": () => jsonResponse(200, { ...stewRecipe, status: "missing", relink: null }),
      "DELETE /recipes/r-barley": () => jsonResponse(204),
    });
    const main = await screen.findByRole("main");
    const user = userEvent.setup();
    const remove = await within(main).findByRole("button", { name: "Remove recipe" });
    expect(remove.className).toContain("text-red-700");
    await user.click(remove);
    const panel = within(main).getByRole("group", { name: "Remove ‘Barley moon stew’?" });
    expect(within(panel).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(within(main).queryByRole("group", { name: /Remove/ })).not.toBeInTheDocument();
    await waitFor(() => expect(within(main).getByRole("button", { name: "Remove recipe" })).toHaveFocus());

    await user.click(within(main).getByRole("button", { name: "Remove recipe" }));
    await user.click(within(within(main).getByRole("group", { name: "Remove ‘Barley moon stew’?" })).getByRole("button", { name: "Remove recipe" }));
    expect(await screen.findByRole("heading", { name: "Recipes" })).toBeInTheDocument();
    expect(screen.getByTestId("notice")).toHaveTextContent("Removed Barley moon stew.");
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/recipes/r-barley")).toBe(true);
  });

  it("has no Remove recipe on a recipe that is still there", async () => {
    mount();
    await within((await screen.findByRole("main"))).findByRole("heading", { level: 1, name: "Barley moon stew" });
    expect(within((await screen.findByRole("main"))).queryByRole("button", { name: "Remove recipe" })).not.toBeInTheDocument();
    expect(lanternLentils.status).toBe("missing");
  });
});
