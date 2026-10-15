import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NeedsBridgeItem } from "../api/pricebook";
import { ingredientIdFor } from "../components/catalog/IngredientPicker";
import { flour } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented values for "Measure in ml instead" on Needs a bridge (issue 358).
const juiceId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01";
const bottleId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b01";
const juice = {
  ...flour,
  id: juiceId,
  name: "Tarn apple juice",
  measures: [
    {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01",
      ingredient_id: juiceId,
      label: "glass",
      canonical_qty: "250.00000",
      source: "manual",
      confirmed: false,
      updated_at: "2026-10-01T12:00:00Z",
    },
  ],
};

function row(switchToMl: boolean): NeedsBridgeItem {
  return {
    ingredient: { id: juiceId, name: juice.name, canonical_unit: "g" },
    product: {
      id: bottleId,
      name: "Tarn juice, bottle",
      brand: null,
      pack_qty: "1.5",
      pack_unit: "l",
    },
    status: "no_density",
    switch_to_ml: switchToMl,
    observation_count: 3,
    latest_observed_at: "2026-10-01T15:00:00Z",
  };
}

function routes(state: { items: NeedsBridgeItem[] }, patches: RecordedCall[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok", features: ["catalog", "shop", "cook"] }),
    "GET /price-book/needs-bridge": () => jsonResponse(200, { items: state.items }),
    "GET /usda/review": () =>
      jsonResponse(200, { loaded: true, release_date: "2026-04-30", groups: [] }),
    "GET /bridges/unconfirmed": () => jsonResponse(200, { items: [] }),
    [`GET /ingredients/${juiceId}`]: () => jsonResponse(200, juice),
    [`GET /ingredients/${juiceId}/recipes`]: () =>
      jsonResponse(200, {
        total: 1,
        items: [
          {
            id: "r1",
            title: "Tarn cider punch",
            path: "drinks/punch.cook",
            status: "ok",
            quantities: ["500 g"],
          },
        ],
      }),
    [`PATCH /ingredients/${juiceId}`]: (call: RecordedCall) => {
      patches.push(call);
      state.items = [];
      return jsonResponse(200, { ...juice, canonical_unit: "ml", measures: [] });
    },
  };
}

describe("Measure in ml instead (issue 358)", () => {
  it("is offered only on a no-density row whose products are all sold by volume", async () => {
    mockApi(routes({ items: [row(false)] }, []));
    renderApp("/catalog/bridges");
    await screen.findByTestId("needs-bridge");
    expect(screen.queryByRole("button", { name: "Measure in ml instead" })).not.toBeInTheDocument();
  });

  it("says what changes, then switches the unit and drops the measures in g", async () => {
    const patches: RecordedCall[] = [];
    mockApi(routes({ items: [row(true)] }, patches));
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const bridgeRow = await screen.findByTestId("needs-bridge");
    expect(within(bridgeRow).getByRole("link", { name: "Add a density" })).toBeInTheDocument();
    await user.click(within(bridgeRow).getByRole("button", { name: "Measure in ml instead" }));
    const panel = screen.getByRole("group", { name: "Measure Tarn apple juice in ml" });
    expect(
      await within(panel).findByText(/will be removed: 1 glass = 250 g\./),
    ).toBeInTheDocument();
    expect(
      await within(panel).findByText(/Used in 1 recipe: Tarn cider punch\./),
    ).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Switch to ml" }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0].body).toEqual({ canonical_unit: "ml", drop_measures: true });
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Tarn apple juice is measured in ml now.",
    );
    expect(await screen.findByText("Every price is normalized")).toBeInTheDocument();
  });
});

describe("a new ingredient for a pack sold by volume (issue 358)", () => {
  it("is created in the unit the caller names", async () => {
    const calls = mockApi({
      "POST /ingredients": () => jsonResponse(201, { ...juice, canonical_unit: "ml" }),
    });
    const choice = { kind: "new", name: "Tarn pear juice", category: "drinks" } as const;
    await ingredientIdFor(choice, "ml");
    await ingredientIdFor(choice);
    expect(calls.map((c) => (c.body as { canonical_unit?: string }).canonical_unit)).toEqual([
      "ml",
      undefined,
    ]);
  });
});
