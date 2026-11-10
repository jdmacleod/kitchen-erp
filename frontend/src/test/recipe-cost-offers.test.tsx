// Weekly flyers 2X (spec 18, X2, X5): recipe costing's "This week's offers" basis, a
// kitchen's view only, with offer-priced lines marked and estimates from the product's
// size marked as such. Every store, recipe and price here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { RecipeCost } from "../api/recipes";
import { homeBase, homeBaseId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import {
  emptyHistory,
  mountedStatus,
  quietInbox,
  recipeList,
  stewCost,
  stewRecipe,
} from "./recipe-fixtures";

const planHealth = { status: "ok", features: ["catalog", "shop", "cook", "plan"] };
const anOffer = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c61" };

function offerCost(): RecipeCost {
  const base = stewCost({ basis: "offers", home_base_id: homeBaseId });
  const [first, ...rest] = base.lines;
  if (!first.price) throw new Error("the fixture's first line is priced");
  return {
    ...base,
    lines: [
      { ...first, price: { ...first.price, flyer_offer_id: anOffer.id, estimate: true } },
      ...rest,
    ],
  };
}

function mount(path: string, offers: unknown[], routes: Record<string, RouteHandler> = {}) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, planHealth),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    "GET /me/settings": () =>
      jsonResponse(200, { current_home_base_id: homeBaseId, suggested_home_base_id: null }),
    "GET /recipes/status": () => jsonResponse(200, mountedStatus),
    "GET /recipes": () => jsonResponse(200, { items: recipeList }),
    "GET /recipes/r-barley": () => jsonResponse(200, stewRecipe),
    "GET /recipes/r-barley/cost": (call) =>
      jsonResponse(
        200,
        call.query.get("basis") === "offers"
          ? offerCost()
          : stewCost({ home_base_id: call.query.get("home_base_id") ?? null }),
      ),
    "GET /recipes/r-barley/cost/history": () => jsonResponse(200, emptyHistory),
    "GET /flyer-offers/current": () => jsonResponse(200, { items: offers }),
    ...routes,
  });
  renderApp(path);
  return calls;
}

describe("this week's offers as a cost basis (X2)", () => {
  it("is a basis of a kitchen with offers, and marks the lines an offer priced", async () => {
    const calls = mount(`/cook/recipes/r-barley?home_base_id=${homeBaseId}`, [anOffer]);
    const user = userEvent.setup();
    const basis = await screen.findByRole("group", { name: "Basis" });
    await user.click(await within(basis).findByRole("button", { name: "This week's offers" }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.path.startsWith("/recipes/r-barley/cost?") &&
            c.query.get("basis") === "offers" &&
            c.query.get("home_base_id") === homeBaseId,
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("this week's offer")).toBeInTheDocument();
    expect(screen.getByText("estimate")).toBeInTheDocument();
  });

  it("isn't offered without a kitchen, or for a kitchen without offers (X5)", async () => {
    mount("/cook/recipes/r-barley", [anOffer]);
    const all = await screen.findByRole("group", { name: "Basis" });
    expect(within(all).queryByRole("button", { name: "This week's offers" })).toBeNull();
  });

  it("isn't offered for a kitchen with no offers this week (X5)", async () => {
    mount(`/cook/recipes/r-barley?home_base_id=${homeBaseId}`, []);
    await screen.findByTestId("kitchen-cost-note");
    const basis = screen.getByRole("group", { name: "Basis" });
    expect(within(basis).queryByRole("button", { name: "This week's offers" })).toBeNull();
    expect(screen.queryByText("this week's offer")).not.toBeInTheDocument();
  });
});
