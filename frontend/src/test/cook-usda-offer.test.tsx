import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import {
  cookHealth,
  emptyHistory,
  mountedStatus,
  quietInbox,
  recipeList,
  stewCost,
  stewRecipe,
} from "./recipe-fixtures";

const offer = {
  ingredient_id: "i-ground-fennel",
  kind: "density" as const,
  density_portion_id: "p-tsp",
  measure_label: null,
  text: "1 tsp = 2 g",
};

function withOffer() {
  const cost = stewCost();
  return {
    ...cost,
    lines: cost.lines.map((l) => (l.status === "unconvertible" ? { ...l, usda_offer: offer } : l)),
  };
}

function mount() {
  return mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, cookHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": () => jsonResponse(200, withOffer()),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    "POST /usda/review/i-ground-fennel": () =>
      jsonResponse(200, { saved: 1, ingredient: { id: "i-ground-fennel" } }),
  });
}

describe("USDA's value on an unconvertible line", () => {
  it("offers it beside the fix link and takes it without closing the review", async () => {
    const calls = mount();
    renderApp("/cook/recipes/r-barley");
    const row = (await screen.findAllByTestId("cost-row")).find(
      (r) => r.dataset.status === "unconvertible",
    );
    if (!row) throw new Error("the fixture has an unconvertible line");
    const offered = within(row).getByTestId("usda-offer");
    expect(offered).toHaveTextContent("USDA: 1 tsp = 2 g");
    expect(within(row).getByRole("link", { name: "add density" })).toBeInTheDocument();

    await userEvent.click(within(offered).getByRole("button", { name: "Use it" }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "POST" && c.path === "/usda/review/i-ground-fennel"),
      ).toBe(true),
    );
    const post = calls.find(
      (c) => c.method === "POST" && c.path === "/usda/review/i-ground-fennel",
    );
    expect(post?.body).toEqual({ density_portion_id: "p-tsp", mark_reviewed: false });
    expect(await screen.findByText(/Added USDA's 1 tsp = 2 g to/)).toBeInTheDocument();
  });

  it("shows no offer on a line without one", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, cookHealth),
      "GET /inbox": () => jsonResponse(200, quietInbox),
      "GET /recipes/status": () => jsonResponse(200, mountedStatus),
      "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
      "GET /recipes/r-barley/cost": () => jsonResponse(200, stewCost()),
      "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    });
    renderApp("/cook/recipes/r-barley");
    await screen.findAllByTestId("cost-row");
    expect(screen.queryByTestId("usda-offer")).not.toBeInTheDocument();
  });
});
