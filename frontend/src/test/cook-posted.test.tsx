import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RecipeCost, RecipeCostSummary } from "../api/recipeCosts";
import type { RecipeListItem } from "../api/recipes";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import {
  barleyProduct,
  barleyStew,
  cookHealth,
  costLine,
  emptyHistory,
  history,
  mountedStatus,
  quietInbox,
  stewCost,
  stewLines,
  stewRecipe,
} from "./recipe-fixtures";

// Lines costed from a store's listed price (issue 523): the last resort, shown
// apart from the counted figures everywhere a cost is.

function must<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error("the fixture has no value here");
  return value;
}

const counted: RecipeCostSummary = {
  ...must(barleyStew.cost),
  consumed_cost: "4.1000",
  consumed_cost_high: "4.1000",
  per_serving: "1.0250",
  lines_priced: 8,
  lines_total: 10,
};

function withCost(over: Partial<RecipeCostSummary>): RecipeListItem {
  return { ...barleyStew, cost: { ...counted, ...over } };
}

function mountList(item: RecipeListItem) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: [item] }),
  });
  renderApp("/cook/recipes");
}

const row = async () =>
  (await within(await screen.findByRole("main")).findAllByTestId("recipe-row"))[0];

describe("the list's cost cell with posted lines", () => {
  it("shows the counted total, then the posted part beneath it", async () => {
    mountList(
      withCost({
        posted_consumed_cost: "2.3000",
        posted_consumed_cost_high: "2.3000",
        lines_posted: 2,
        lines_priced: 8,
        lines_total: 10,
      }),
    );
    const stew = await row();
    expect(stew).toHaveTextContent("at least $4.10");
    expect(within(stew).getByTestId("posted-part")).toHaveTextContent("+ $2.30 posted");
    expect(stew).toHaveTextContent("counted, per serving");
    expect(within(stew).queryByText("some stale")).not.toBeInTheDocument();
  });

  it("gives each part its own range, and says when a posted price is stale", async () => {
    mountList(
      withCost({
        consumed_cost_high: "5.0000",
        posted_consumed_cost: "2.3000",
        posted_consumed_cost_high: "2.6000",
        lines_posted: 1,
        lines_priced: 9,
        posted_stale: true,
      }),
    );
    const stew = await row();
    expect(stew).toHaveTextContent("$4.10–5.00");
    expect(within(stew).getByTestId("posted-part")).toHaveTextContent("+ $2.30–2.60 posted");
    expect(within(stew).getByText("some stale")).toBeInTheDocument();
  });

  it("reads a recipe on posted prices only as the posted figure, with no per serving", async () => {
    mountList(
      withCost({
        consumed_cost: null,
        consumed_cost_high: null,
        basket_cost: null,
        basket_cost_high: null,
        per_serving: null,
        posted_consumed_cost: "6.4000",
        posted_consumed_cost_high: "6.4000",
        lines_posted: 10,
        lines_priced: 0,
      }),
    );
    const stew = await row();
    expect(stew).toHaveTextContent("$6.40 posted");
    expect(stew).not.toHaveTextContent("at least");
    expect(stew).not.toHaveTextContent("per serving");
    expect(within(stew).queryByTestId("posted-part")).not.toBeInTheDocument();
  });

  it("says nothing of a posted part before the snapshot carries one", async () => {
    mountList(withCost({ posted_consumed_cost: null, lines_posted: undefined }));
    const stew = await row();
    expect(stew).not.toHaveTextContent("posted");
    expect(stew).not.toHaveTextContent("$0.00");
    expect(stew).toHaveTextContent("$1.02 per serving");
    expect(stew).not.toHaveTextContent("counted");
  });
});

function postedCost(): RecipeCost {
  const cost = stewCost();
  const kelp = costLine(stewLines[3], {
    status: "posted",
    price: {
      ...must(cost.lines[0].price),
      product_name: "Kelp flakes",
      vendor_name: "Cove Grocer",
      observed_at: "2025-03-14T15:00:00Z",
      stale: true,
    },
    consumed_cost: "0.9000",
  });
  const onion = { ...cost.lines[1], status: "posted" as const };
  return {
    ...cost,
    totals: {
      ...cost.totals,
      posted_consumed_cost: "2.3000",
      posted_consumed_cost_high: "2.3000",
      posted_basket_cost: "3.0000",
      posted_basket_cost_high: "3.0000",
    },
    completeness: { ...cost.completeness, lines_posted: 2 },
    lines: cost.lines.map((l, i) => (i === 1 ? onion : i === 3 ? kelp : l)),
  };
}

function mountRecipe(make: () => RecipeCost, more: Record<string, RouteHandler> = {}) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": () => jsonResponse(200, make()),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    "GET /products": () => jsonResponse(200, { items: [barleyProduct], next_cursor: null }),
    ...more,
  });
  renderApp("/cook/recipes/r-barley");
}

describe("the cost table with posted lines", () => {
  it("counts the posted lines in the header and names the oldest month and year", async () => {
    mountRecipe(postedCost);
    expect(await screen.findByTestId("posted-header")).toHaveTextContent(
      "2 lines use a posted price, the oldest from March 2025. A store's listed price counts only when no receipt or shelf price exists.",
    );
  });

  it("has no header line when no line is on a posted price", async () => {
    mountRecipe(() => stewCost());
    await screen.findAllByTestId("cost-row");
    expect(screen.queryByTestId("posted-header")).not.toBeInTheDocument();
  });

  it("marks a posted line, with its store and date, and stale beside it", async () => {
    mountRecipe(postedCost);
    const kelp = (await screen.findAllByTestId("cost-row"))[3];
    expect(kelp).toHaveAttribute("data-status", "posted");
    expect(within(kelp).getByText("posted")).toBeInTheDocument();
    expect(kelp).toHaveTextContent("a store's listed price, not counted:");
    expect(within(kelp).getByText("stale")).toBeInTheDocument();
    expect(kelp).toHaveTextContent("Kelp flakes · Cove Grocer · Mar 14, 2025");
  });

  it("adds the posted part to the totals and labels per serving as counted", async () => {
    mountRecipe(postedCost);
    await screen.findAllByTestId("cost-row");
    const totals = screen.getByTestId("cost-totals");
    expect(totals).toHaveTextContent("+ $2.30 posted");
    expect(totals).toHaveTextContent("+ $3.00 posted");
    expect(totals).toHaveTextContent("Counted, per serving");
  });

  it("captions the history chart while posted lines are left out of it", async () => {
    mountRecipe(postedCost, {
      "GET /recipes/r-barley/cost/history": () =>
        jsonResponse(200, history("latest", ["4.0000", "4.1000"])),
    });
    expect(await screen.findByTestId("chart-posted")).toHaveTextContent(
      "Counted lines only. Lines on a posted price join the chart when a receipt or shelf price arrives.",
    );
  });
});
