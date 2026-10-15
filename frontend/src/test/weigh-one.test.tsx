import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NeedsBridgeItem } from "../api/pricebook";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented values for "Weigh one" on Needs a bridge (issue 357).
const limesId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d01";
const bagId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9e01";

function row(packUnit: string, canonical: "g" | "each" = "g"): NeedsBridgeItem {
  return {
    ingredient: { id: limesId, name: "limes", canonical_unit: canonical },
    product: {
      id: bagId,
      name: "Tarn limes, bag",
      brand: null,
      pack_qty: "8",
      pack_unit: packUnit,
    },
    status: "no_pack",
    observation_count: 2,
    latest_observed_at: "2026-10-01T15:00:00Z",
  };
}

const units = [
  { code: "g", dimension: "mass", to_base_factor: "1", system: "metric", aliases: [] },
  { code: "lb", dimension: "mass", to_base_factor: "453.59237", system: "us", aliases: [] },
  { code: "each", dimension: "count", to_base_factor: "1", system: "count", aliases: [] },
];

function routes(state: { items: NeedsBridgeItem[] }, posts: RecordedCall[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /price-book/needs-bridge": () => jsonResponse(200, { items: state.items }),
    "GET /usda/review": () =>
      jsonResponse(200, { loaded: true, release_date: "2026-04-30", groups: [] }),
    "GET /bridges/unconfirmed": () => jsonResponse(200, { items: [] }),
    [`POST /ingredients/${limesId}/measures`]: (call: RecordedCall) => {
      posts.push(call);
      state.items = [];
      return jsonResponse(201, { id: "m1", label: "each", canonical_qty: "67" });
    },
  };
}

describe("Weigh one (issue 357)", () => {
  it("saves what one piece weighs, once for the ingredient", async () => {
    const posts: RecordedCall[] = [];
    mockApi(routes({ items: [row("each")] }, posts));
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const bridgeRow = await screen.findByTestId("needs-bridge");
    expect(bridgeRow).toHaveTextContent("pack has no weight");
    await user.click(within(bridgeRow).getByRole("button", { name: "Weigh one" }));
    const form = screen.getByRole("form", { name: "What one limes weighs" });
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(within(form).getByText("Give a positive number, like 67.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("One limes weighs (g)"), "67");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      label: "each",
      canonical_qty: "67",
      source: "measured",
      confirmed: true,
    });
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Saved what one limes weighs. Its packs in pieces are priced through it.",
    );
  });

  it("is not offered for a pack with a weight, or for an ingredient counted in pieces", async () => {
    mockApi(routes({ items: [row("lb"), { ...row("each", "each"), status: "no_pack" }] }, []));
    renderApp("/catalog/bridges");
    await screen.findAllByTestId("needs-bridge");
    expect(screen.queryByRole("button", { name: "Weigh one" })).not.toBeInTheDocument();
  });
});
