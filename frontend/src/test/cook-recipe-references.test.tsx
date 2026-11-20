import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CostLine, CostLineSource, RecipeCost } from "../api/recipeCosts";
import type { Recipe } from "../api/recipes";
import type { IngredientMadeBy as Ingredient, MakesOut, RecipeLink } from "../api/recipeSources";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import {
  barleyProduct,
  cookHealth,
  costLine,
  emptyHistory,
  ingredientLine,
  mountedStatus,
  quietInbox,
  stewCost,
  stewRecipe,
} from "./recipe-fixtures";

// Lines costed from a recipe (issue 526): a recipe reference, `@./basics/
// lantern-syrup{1%fl oz}`, or a line on an ingredient a house recipe makes. All
// names are invented.

const syrup: RecipeLink = {
  id: "r-syrup",
  title: "Lantern syrup",
  path: "basics/lantern-syrup.cook",
  status: "ok",
};
const makes: MakesOut = {
  text: "500 ml",
  qty: "500",
  unit: "ml",
  display_qty: "16.9",
  display_unit: "fl oz",
};
const refLine = ingredientLine({
  id: "lr",
  seq: 9,
  raw_name: "lantern-syrup",
  qty: "1",
  unit_text: "fl oz",
  unit: "fl_oz",
  resolution: "recipe",
  ingredient_id: null,
  ingredient_name: null,
  ref_path: "./basics/lantern-syrup",
  ref_recipe_id: syrup.id,
});

function source(over: Partial<CostLineSource> = {}): CostLineSource {
  return {
    recipe: syrup,
    makes,
    target_path: syrup.path,
    suggestion: null,
    counts: null,
    cycle: [],
    ...over,
  };
}

function sourced(over: Partial<CostLine> = {}): CostLine {
  return costLine(refLine, {
    price: null,
    packs: null,
    bridge_kind: null,
    bridge_confirmed: null,
    yield_applied: null,
    quantity: {
      canonical_qty: "29.5735295625",
      canonical_qty_high: null,
      canonical_unit: "ml",
      display_qty: "1",
      display_qty_high: null,
      display_unit: "fl oz",
    },
    consumed_cost: "0.0591",
    basket_cost: "0.0591",
    source_recipe: syrup,
    source_partial: false,
    source_basis: null,
    source: source(),
    ...over,
  });
}

function failed(code: string, over: Partial<CostLine> = {}): CostLine {
  return sourced({
    status:
      code === "no_recipe"
        ? "unmapped"
        : code.startsWith("recipe_") || code === "cycle"
          ? "unpriced"
          : "unconvertible",
    failure_code: code,
    consumed_cost: null,
    basket_cost: null,
    quantity: null,
    ...over,
  });
}

function costWith(lines: CostLine[], over: Partial<RecipeCost> = {}): RecipeCost {
  const base = stewCost();
  const priced = lines.filter((l) => l.status === "priced").length;
  return {
    ...base,
    totals: { ...base.totals, consumed_cost: "0.0591", basket_cost: "0.0591", per_serving: null },
    completeness: {
      lines_total: lines.length,
      lines_priced: priced,
      lines_unpriced: lines.filter((l) => l.status === "unpriced").length,
      lines_unconvertible: lines.filter((l) => l.status === "unconvertible").length,
      lines_unmapped: lines.filter((l) => l.status === "unmapped").length,
      lines_negligible: 0,
      lines_posted: lines.filter((l) => l.status === "posted").length,
      lines_partial: lines.filter((l) => l.source_partial).length,
    },
    lines,
    ...over,
  };
}

function mountRecipe(
  cost: RecipeCost,
  recipe: Recipe = stewRecipe,
  more: Record<string, RouteHandler> = {},
) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes/r-barley": () => jsonResponse(200, recipe),
    "GET /recipes/r-barley/cost": () => jsonResponse(200, cost),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    "GET /products": () => jsonResponse(200, { items: [barleyProduct], next_cursor: null }),
    ...more,
  });
  renderApp("/cook/recipes/r-barley");
}

const firstRow = async () => (await screen.findAllByTestId("cost-row"))[0];

describe("a line costed from a recipe", () => {
  it("names the recipe with the share of a batch, and offers no shelf price", async () => {
    mountRecipe(costWith([sourced()]));
    const row = await firstRow();
    const cell = within(row).getByTestId("source-cell");
    expect(cell).toHaveTextContent("from Lantern syrup recipe");
    expect(cell).toHaveTextContent("1 fl oz of 16.9 fl oz");
    const links = within(row).getAllByRole("link", { name: "Lantern syrup recipe" });
    expect(links[0]).toHaveAttribute("href", "/cook/recipes/r-syrup");
    expect(row).toHaveTextContent("$0.0591".replace("0.0591", "0.06"));
    expect(within(row).queryByText("Log a shelf price")).not.toBeInTheDocument();
  });

  it("floors the cost of an incomplete recipe and says why in Known", async () => {
    mountRecipe(costWith([sourced({ source_partial: true })]));
    const row = await firstRow();
    expect(row).toHaveTextContent("at least $0.06");
    expect(row).toHaveTextContent("Lantern syrup recipe incomplete");
    expect(screen.getByTestId("known")).toHaveTextContent(
      "1 of 1 line priced · 1 from an incomplete recipe",
    );
  });

  it("says (latest prices) when a kitchen's view costed the recipe under Latest", async () => {
    mountRecipe(costWith([sourced({ source_basis: "latest" })]));
    expect(await screen.findByTestId("source-cell")).toHaveTextContent("(latest prices)");
  });

  it("counts a line from a recipe on a posted price apart in the header", async () => {
    mountRecipe(costWith([sourced({ status: "posted" })]));
    expect(await screen.findByTestId("posted-header")).toHaveTextContent(
      "1 line costs from Lantern syrup recipe, which uses a posted price.",
    );
    expect(screen.getByTestId("posted-header")).not.toHaveTextContent("lines use a posted price");
    expect(within(await firstRow()).getByText("posted")).toBeInTheDocument();
  });
});

describe("why a line from a recipe has no cost", () => {
  const cases: [string, Partial<CostLine>, string, string | null][] = [
    [
      "no_yield",
      { source: source({ makes: null }) },
      "Add how much Lantern syrup makes, yield: 500 ml, to basics/lantern-syrup.cook, then rescan",
      "/cook/recipes/r-syrup",
    ],
    [
      "bad_yield",
      { source: source({ makes: { ...makes, text: "2 jars", qty: null, unit: null } }) },
      "Lantern syrup makes 2 jars, not an amount and a unit. Write it as yield: 500 ml in basics/lantern-syrup.cook, then rescan",
      "/cook/recipes/r-syrup",
    ],
    [
      "yield_unit",
      { line: { ...refLine, unit_text: "oz", unit: "oz" } },
      "Lantern syrup makes 16.9 fl oz; this line is in oz. Write fl oz for a liquid.",
      "/cook/recipes/r-syrup",
    ],
    [
      "recipe_unpriced",
      { source_counts: { priced: 0, total: 3 } },
      "Lantern syrup has no priced lines yet (0 of 3)",
      "/cook/recipes/r-syrup",
    ],
    [
      "recipe_unreadable",
      {},
      "Lantern syrup can't be read; fix basics/lantern-syrup.cook, then rescan",
      "/cook/recipes/r-syrup",
    ],
  ];
  it.each(cases)("%s", async (code, over, words, href) => {
    mountRecipe(costWith([failed(code, over)]));
    const row = await firstRow();
    expect(within(row).getByTestId("source-words")).toHaveTextContent(words);
    if (href) {
      const link = within(within(row).getByTestId("source-words")).getAllByRole("link")[0];
      expect(link).toHaveAttribute("href", href);
    }
    expect(within(row).queryByText("Log a shelf price")).not.toBeInTheDocument();
    expect(within(row).queryByText("add density")).not.toBeInTheDocument();
  });

  it("a yield_unit line in g against a volume gives no fl oz advice", async () => {
    mountRecipe(
      costWith([failed("yield_unit", { line: { ...refLine, unit_text: "g", unit: "g" } })]),
    );
    const words = within(await firstRow()).getByTestId("source-words");
    expect(words).toHaveTextContent("this line is in g.");
    expect(words).not.toHaveTextContent("Write fl oz");
  });
});

describe("why a reference or a made-by line has no recipe to cost from", () => {
  it("no_recipe names where it looked, as written, and a same-name file elsewhere", async () => {
    const elsewhere: RecipeLink = { ...syrup, id: "r-other", path: "syrups/lantern-syrup.cook" };
    mountRecipe(
      costWith([
        failed("no_recipe", {
          source_recipe: null,
          source: source({ recipe: null, makes: null, suggestion: elsewhere }),
        }),
      ]),
    );
    const words = within(await firstRow()).getByTestId("source-words");
    expect(words).toHaveTextContent(
      "No recipe at basics/lantern-syrup.cook (written ./basics/lantern-syrup in soups/barley_moon_stew.cook). Create it or fix the path, then rescan.",
    );
    expect(within(words).getByRole("link", { name: "syrups/lantern-syrup.cook" })).toHaveAttribute(
      "href",
      "/cook/recipes/r-other",
    );
  });

  it("no_recipe for a path outside the recipes directory", async () => {
    mountRecipe(
      costWith([
        failed("no_recipe", {
          source_recipe: null,
          line: { ...refLine, ref_path: "../../elsewhere" },
          source: source({ recipe: null, makes: null, target_path: null }),
        }),
      ]),
    );
    expect(within(await firstRow()).getByTestId("source-words")).toHaveTextContent(
      "../../elsewhere in soups/barley_moon_stew.cook points outside the recipes directory.",
    );
  });

  it("cycle lists the walk as title links with hidden arrows and says how each hop is made", async () => {
    const dip: RecipeLink = {
      id: "r-dip",
      title: "Comet dip",
      path: "comet-dip.cook",
      status: "ok",
    };
    const glaze: RecipeLink = {
      id: "r-glaze",
      title: "Comet glaze",
      path: "comet-glaze.cook",
      status: "ok",
    };
    mountRecipe(
      costWith([
        failed("cycle", {
          source_recipe: glaze,
          source: source({
            recipe: glaze,
            cycle: [
              { recipe: dip, via_ingredient: null },
              { recipe: glaze, via_ingredient: { id: "i-dip", name: "Comet dip" } },
              { recipe: dip, via_ingredient: null },
            ],
          }),
        }),
      ]),
    );
    const chain = within(await firstRow()).getByTestId("cycle-chain");
    expect(within(chain).getAllByRole("link", { name: "Comet dip" })[0]).toHaveAttribute(
      "href",
      "/cook/recipes/r-dip",
    );
    expect(chain).toHaveTextContent("Comet dip: edit comet-dip.cook");
    expect(chain).toHaveTextContent("Comet glaze uses Comet dip, which Comet dip makes");
    const via = within(chain)
      .getAllByRole("link", { name: "Comet dip" })
      .find((a) => a.getAttribute("href")?.startsWith("/catalog/"));
    expect(via).toHaveAttribute("href", "/catalog/ingredients/i-dip#made-by-heading");
    expect(chain.querySelector("[aria-hidden='true']")).toHaveTextContent("→");
  });

  it("route (b) no_density adds the recipe's way out", async () => {
    const onIngredient = ingredientLine({
      id: "lb",
      seq: 10,
      raw_name: "Lantern syrup",
      qty: "1",
      unit_text: "fl oz",
      unit: "fl_oz",
    });
    mountRecipe(
      costWith([
        costLine(onIngredient, {
          status: "unconvertible",
          failure_code: "no_density",
          price: null,
          consumed_cost: null,
          basket_cost: null,
          quantity: null,
          source_recipe: syrup,
          source: source(),
        }),
      ]),
    );
    const words = within(await firstRow()).getByTestId("source-words");
    expect(words).toHaveTextContent("Can't convert yet · add density");
    expect(words).toHaveTextContent("or give how much Lantern syrup makes as a volume");
  });
});

describe("the recipe page of a recipe others use", () => {
  const syrupRecipe: Recipe = {
    ...stewRecipe,
    makes,
    used_in: [
      { id: "r-sour", title: "Harbor sour", path: "harbor-sour.cook", status: "ok" },
      { id: "r-fizz", title: "Comet fizz", path: "comet-fizz.cook", status: "ok" },
    ],
    used_lines: 3,
  };

  it("says what it makes, costs per fl oz made, and lists where it is used", async () => {
    mountRecipe(
      costWith([sourced()], {
        makes_unit_cost: { amount: "0.059", unit: "fl oz", partial: true, source_basis: null },
      }),
      syrupRecipe,
    );
    expect(await screen.findByTestId("makes-meta")).toHaveTextContent("Makes 16.9 fl oz (500 ml)");
    const totals = await screen.findByTestId("cost-totals");
    expect(totals).toHaveTextContent("Per fl oz made");
    expect(totals).toHaveTextContent("at least$0.059");
    const used = screen.getByTestId("used-in");
    expect(within(used).getByRole("link", { name: "Harbor sour" })).toHaveAttribute(
      "href",
      "/cook/recipes/r-sour",
    );
    expect(screen.queryByTestId("yield-hint")).not.toBeInTheDocument();
  });

  it("shows a yield that isn't an amount and a unit as written, with the hint", async () => {
    mountRecipe(costWith([sourced()]), {
      ...syrupRecipe,
      makes: { text: "12 pieces", qty: null, unit: null, display_qty: null, display_unit: null },
    });
    expect(await screen.findByTestId("makes-meta")).toHaveTextContent("Makes 12 pieces");
    expect(screen.getByTestId("yield-hint")).toHaveTextContent(
      "12 pieces isn't an amount and a unit, so 3 lines in 2 recipes can't be costed. Write it as yield: 500 ml in soups/barley_moon_stew.cook, then rescan.",
    );
  });

  it("renders a recipe reference in the steps as a link to that recipe", async () => {
    mountRecipe(costWith([sourced()]), {
      ...stewRecipe,
      body: [
        {
          name: null,
          steps: [
            [
              { t: "text", v: "Shake " },
              {
                t: "ingredient",
                name: "lantern-syrup",
                qty: "1",
                unit: "fl oz",
                note: null,
                seq: 9,
                ref_path: "./basics/lantern-syrup",
                recipe_id: "r-syrup",
              },
              { t: "text", v: " with ice." },
            ],
          ],
        },
      ],
    });
    const item = await screen.findByTestId("step-ingredient");
    expect(within(item).getByRole("link", { name: "lantern-syrup" })).toHaveAttribute(
      "href",
      "/cook/recipes/r-syrup",
    );
  });

  it("stays quiet for a recipe nobody uses, and hides Used in", async () => {
    mountRecipe(costWith([sourced()]), {
      ...stewRecipe,
      makes: { text: "12 pieces", qty: null, unit: null, display_qty: null, display_unit: null },
      used_in: [],
      used_lines: 0,
    });
    await screen.findAllByTestId("cost-row");
    expect(screen.queryByTestId("yield-hint")).not.toBeInTheDocument();
    expect(screen.queryByTestId("used-in")).not.toBeInTheDocument();
  });
});

// --- the ingredient page -------------------------------------------------------------------

function ingredientRoutes(ingredient: () => Ingredient, more: Record<string, RouteHandler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient()),
    [`GET /ingredients/${flourId}/offers`]: () =>
      jsonResponse(200, { items: [], stale_after_days: 90 }),
    [`GET /ingredients/${flourId}/recipes`]: () => jsonResponse(200, { items: [], total: 0 }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
    "GET /recipes": () => jsonResponse(200, { items: [] }),
    ...more,
  };
}

describe("Made by recipe on the ingredient page", () => {
  it("offers the namesake recipe and links it with one click", async () => {
    let current: Ingredient = { ...flour, recipe_match: syrup, made_by_recipe: null };
    const calls = mockApi(
      ingredientRoutes(() => current, {
        [`PATCH /ingredients/${flourId}`]: () => {
          current = {
            ...flour,
            made_by_recipe: {
              ...syrup,
              makes_text: "500 ml",
              makes_ok: true,
              unit_cost: { amount: "0.059", unit: "fl oz", partial: false, source_basis: null },
            },
            recipe_match: null,
            recomputed: 4,
            recompute_failed: 0,
            warnings: [],
          };
          return jsonResponse(200, current);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);
    const section = await screen.findByTestId("made-by");
    await user.click(
      within(section).getByRole("button", { name: "Cost this from the Lantern syrup recipe" }),
    );
    expect(await screen.findByTestId("notice")).toHaveTextContent("Linked. 4 recipes recomputed.");
    expect(
      within(screen.getByTestId("notice")).getByRole("link", { name: "Used in" }),
    ).toHaveAttribute("href", "/cook/recipes/r-syrup#used-in");
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.body).toEqual({ made_by_recipe_id: "r-syrup" });
    expect(await screen.findByTestId("made-by-linked")).toHaveTextContent(
      "Made by Lantern syrup recipe · $0.059 per fl oz when no product price qualifies",
    );
  });

  it("clears the link with no confirmation", async () => {
    let current: Ingredient = {
      ...flour,
      made_by_recipe: {
        ...syrup,
        makes_text: "500 ml",
        makes_ok: true,
        unit_cost: null,
      },
    };
    const calls = mockApi(
      ingredientRoutes(() => current, {
        [`PATCH /ingredients/${flourId}`]: () => {
          current = { ...flour, made_by_recipe: null, recomputed: 2, recompute_failed: 0 };
          return jsonResponse(200, current);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);
    await user.click(await screen.findByRole("button", { name: "Clear" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Unlinked. 2 recipes recomputed.",
    );
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ made_by_recipe_id: null });
  });
});

describe("Made by recipe's states", () => {
  it("warns when the linked recipe doesn't say how much it makes", async () => {
    mockApi(
      ingredientRoutes(() => ({
        ...flour,
        made_by_recipe: { ...syrup, makes_text: null, makes_ok: false, unit_cost: null },
      })),
    );
    renderApp(`/catalog/ingredients/${flourId}`);
    expect(await screen.findByTestId("made-by-warning")).toHaveTextContent(
      "Lantern syrup has no amount it makes, so lines won't cost until you add yield: 500 ml to basics/lantern-syrup.cook, then rescan.",
    );
  });

  it("warns when the linked recipe's file is gone", async () => {
    mockApi(
      ingredientRoutes(() => ({
        ...flour,
        made_by_recipe: {
          ...syrup,
          status: "missing",
          makes_text: "500 ml",
          makes_ok: true,
          unit_cost: null,
        },
      })),
    );
    renderApp(`/catalog/ingredients/${flourId}`);
    expect(await screen.findByTestId("made-by-warning")).toHaveTextContent(
      "basics/lantern-syrup.cook is gone",
    );
  });

  it("says Linking… while pending and shows a refusal as an error", async () => {
    const gate: { release?: () => void } = {};
    mockApi(
      ingredientRoutes(() => ({ ...flour, recipe_match: syrup }), {
        [`PATCH /ingredients/${flourId}`]: () =>
          new Promise((resolve) => {
            gate.release = () =>
              resolve(
                jsonResponse(422, {
                  error: {
                    code: "recipe_missing",
                    message: "The file basics/lantern-syrup.cook is gone.",
                    details: { path: "basics/lantern-syrup.cook" },
                  },
                }),
              );
          }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);
    const section = await screen.findByTestId("made-by");
    await user.click(
      within(section).getByRole("button", { name: "Cost this from the Lantern syrup recipe" }),
    );
    expect(await within(section).findByRole("button", { name: "Linking…" })).toBeDisabled();
    await waitFor(() => expect(gate.release).toBeDefined());
    gate.release?.();
    expect(
      await within(section).findByText("The file basics/lantern-syrup.cook is gone."),
    ).toBeInTheDocument();
  });

  it("is not there before the Cook section is built", async () => {
    const calls = mockApi({
      ...ingredientRoutes(() => flour),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
    });
    renderApp(`/catalog/ingredients/${flourId}`);
    await screen.findByRole("heading", { name: "all-purpose flour" });
    expect(screen.queryByTestId("made-by")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === "/recipes")).toBe(false);
  });
});
