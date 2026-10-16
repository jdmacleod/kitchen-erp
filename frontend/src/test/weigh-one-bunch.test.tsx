import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { NeedsBridgeItem } from "../api/pricebook";
import { BunchHint } from "../components/catalog/BunchHint";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented values for "Weigh one bunch" on Needs a bridge (issue 371).
const cressId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d71";
const bunchId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9e71";

const row: NeedsBridgeItem = {
  ingredient: { id: cressId, name: "vellum cress", canonical_unit: "g" },
  product: {
    id: bunchId,
    name: "Vellum cress",
    brand: null,
    pack_qty: "1",
    pack_unit: "bunch",
  },
  status: "no_bunch_weight",
  observation_count: 3,
  latest_observed_at: "2026-10-01T15:00:00Z",
};

const units = [
  { code: "g", dimension: "mass", to_base_factor: "1", system: "metric", aliases: [] },
  { code: "each", dimension: "count", to_base_factor: "1", system: "any", aliases: [] },
  { code: "bunch", dimension: "bunch", to_base_factor: "1", system: "any", aliases: [] },
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
    [`POST /ingredients/${cressId}/measures`]: (call: RecordedCall) => {
      posts.push(call);
      state.items = [];
      return jsonResponse(201, { id: "m1", label: "bunch", canonical_qty: "90" });
    },
  };
}

describe("Weigh one bunch (issue 371)", () => {
  it("saves what one bunch weighs, once for the ingredient", async () => {
    const posts: RecordedCall[] = [];
    mockApi(routes({ items: [row] }, posts));
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const bridgeRow = await screen.findByTestId("needs-bridge");
    expect(bridgeRow).toHaveTextContent("bunch not weighed");
    expect(within(bridgeRow).queryByRole("button", { name: "Set the pack" })).toBeNull();
    await user.click(within(bridgeRow).getByRole("button", { name: "Weigh one bunch" }));
    const form = screen.getByRole("form", { name: "What one bunch of vellum cress weighs" });
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(within(form).getByText("Give a positive number, like 90.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("One bunch of vellum cress weighs (g)"), "90");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      label: "bunch",
      canonical_qty: "90",
      source: "measured",
      confirmed: true,
    });
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Saved what a bunch of vellum cress weighs. Its bunches are priced through it.",
    );
  });
});

describe("BunchHint (issue 371)", () => {
  it("offers 1 bunch for produce whose name says so and whose pack is empty", async () => {
    const onUse = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <BunchHint name="Vellum cress bunch" produce hasPack={false} onUse={onUse} />,
    );
    await user.click(screen.getByRole("button", { name: "Use 1 bunch" }));
    expect(onUse).toHaveBeenCalledOnce();
    rerender(<BunchHint name="Vellum cress bunch" produce hasPack onUse={onUse} />);
    expect(screen.queryByTestId("bunch-hint")).toBeNull();
    rerender(<BunchHint name="Vellum bunchberry" produce hasPack={false} onUse={onUse} />);
    expect(screen.queryByTestId("bunch-hint")).toBeNull();
    rerender(<BunchHint name="Vellum cress bunch" produce={false} hasPack={false} onUse={onUse} />);
    expect(screen.queryByTestId("bunch-hint")).toBeNull();
  });
});
