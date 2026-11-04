import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatAmount, known, lineText, type BodySection } from "../api/recipes";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import {
  barleyStew,
  cookHealth,
  emptyHistory,
  ingredientLine,
  mountedStatus,
  quietInbox,
  recipeList,
  stewCost,
  stewRecipe,
} from "./recipe-fixtures";

function mount(routes: Record<string, RouteHandler> = {}, path = "/cook/recipes/r-barley") {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": () => jsonResponse(200, stewCost()),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    ...routes,
  });
  renderApp(path);
}

describe("recipe amounts", () => {
  it("shows a stored third as the fraction it was written as", () => {
    expect(formatAmount("0.3333333333333333333333333333")).toBe("1/3");
    expect(formatAmount("1.666666666666666666666666667")).toBe("1 2/3");
    expect(formatAmount("0.1666666666666666666666666667")).toBe("1/6");
  });

  it("leaves short decimals and whole numbers as written", () => {
    expect(formatAmount("0.25")).toBe("0.25");
    expect(formatAmount("1.5000")).toBe("1.5");
    expect(formatAmount("3")).toBe("3");
    expect(formatAmount("0.142857142857")).toBe("0.143");
    // A small amount is never rounded away to 0.
    expect(formatAmount("0.00025")).toBe("0.00025");
  });

  it("writes a line's quantity with its fraction", () => {
    const line = ingredientLine({
      id: "l-cilantro",
      seq: 1,
      raw_name: "cilantro",
      qty: "0.3333333333333333333333333333",
      unit_text: "cup",
    });
    expect(lineText(line)).toBe("1/3 cup cilantro");
  });
});

describe("what counts toward known (07, criterion 16)", () => {
  it("leaves negligible lines out of both sides", () => {
    expect(known({ lines_priced: 8, lines_total: 19, lines_negligible: 3 })).toEqual({
      priced: 8,
      counted: 16,
      partial: true,
    });
    expect(known({ lines_priced: 4, lines_total: 6, lines_negligible: 2 }).partial).toBe(false);
  });
});

const bodyWithNotes: BodySection[] = [
  {
    name: null,
    steps: [
      [{ t: "text", v: "Heat the oven." }],
      [{ t: "note", v: "Without an oven-safe pot, simmer it covered for the same time." }],
      [{ t: "text", v: "Braise two hours." }],
    ],
  },
];

describe("the rendered recipe", () => {
  it("shows a note beside the steps, unnumbered, and keeps counting the steps", async () => {
    mount({
      "GET /recipes/r-barley": () => jsonResponse(200, { ...stewRecipe, body: bodyWithNotes }),
    });
    const recipe = await screen.findByRole("region", { name: "Recipe" });
    const note = await within(recipe).findByTestId("recipe-note");
    expect(note).toHaveTextContent(
      "Without an oven-safe pot, simmer it covered for the same time.",
    );
    expect(note.textContent).not.toContain(">");
    expect(note.closest("ol")).toBeNull();
    const lists = within(recipe).getAllByRole("list", { name: "Steps" });
    expect(lists.map((l) => l.getAttribute("start"))).toEqual(["1", "2"]);
    expect(lists[1]).toHaveTextContent("Braise two hours.");
  });

  it("links the source's web address by its site", async () => {
    mount({
      "GET /recipes/r-barley": () =>
        jsonResponse(200, {
          ...stewRecipe,
          front_matter: {
            ...stewRecipe.front_matter,
            source: "Barley stew by Harbor Kitchen, https://www.harbor-kitchen.example/barley",
          },
        }),
    });
    const recipe = await screen.findByRole("region", { name: "Recipe" });
    const link = await within(recipe).findByRole("link", { name: /harbor-kitchen\.example/ });
    expect(link).toHaveAttribute("href", "https://www.harbor-kitchen.example/barley");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(recipe.querySelector("dl")).toHaveTextContent("Barley stew by Harbor Kitchen");
  });

  it("keeps a comma inside an address and leaves trailing punctuation out", async () => {
    mount({
      "GET /recipes/r-barley": () =>
        jsonResponse(200, {
          ...stewRecipe,
          front_matter: {
            ...stewRecipe.front_matter,
            source: "From https://harbor-kitchen.example/recipes/rice,beans.",
          },
        }),
    });
    const recipe = await screen.findByRole("region", { name: "Recipe" });
    const link = await within(recipe).findByRole("link", { name: "harbor-kitchen.example" });
    expect(link).toHaveAttribute("href", "https://harbor-kitchen.example/recipes/rice,beans");
    expect(recipe.querySelector("dl")).toHaveTextContent("From · harbor-kitchen.example.");
  });

  it("reads a Cooklang amount in the front matter as words", async () => {
    mount({
      "GET /recipes/r-barley": () =>
        jsonResponse(200, {
          ...stewRecipe,
          front_matter: { ...stewRecipe.front_matter, yield: "6%balls" },
        }),
    });
    const recipe = await screen.findByRole("region", { name: "Recipe" });
    await within(recipe).findByText("6 balls");
  });

  it("puts no floor on a total that has no figure", async () => {
    mount({
      "GET /recipes/r-barley/cost": () =>
        jsonResponse(
          200,
          stewCost({
            totals: {
              consumed_cost: null,
              consumed_cost_high: null,
              basket_cost: null,
              basket_cost_high: null,
              per_serving: null,
            },
          }),
        ),
    });
    const totals = await screen.findByTestId("cost-totals");
    await within(totals).findAllByText("—");
    expect(totals).not.toHaveTextContent("at least");
  });
});

describe("the recipes list", () => {
  it("shows a partly priced recipe's figures as floors and a complete one's as they are", async () => {
    if (!barleyStew.cost) throw new Error("the fixture has a cost");
    const complete = {
      ...barleyStew,
      id: "r-complete",
      title: "Complete chowder",
      cost: { ...barleyStew.cost, lines_priced: 9, lines_total: 10, lines_negligible: 1 },
    };
    mount(
      { "GET /recipes": () => jsonResponse(200, { items: [barleyStew, complete] }) },
      "/cook/recipes",
    );
    const rows = await screen.findAllByTestId("recipe-row");
    const [partial, whole] = rows;
    expect(partial).toHaveTextContent("at least $12.40–13.10");
    expect(partial).toHaveTextContent("at least $3.10 per serving");
    expect(partial).toHaveTextContent("8 of 10 lines priced");
    expect(whole).not.toHaveTextContent("at least");
    expect(whole).toHaveTextContent("9 of 9 lines priced");
  });
});
