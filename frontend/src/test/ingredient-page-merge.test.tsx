import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import type { MergeResult } from "../api/ingredientLink";
import { flour, flourId, flourProduct, ingredientMatch, units } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Merging and linking from an ingredient's own page (issue 211; spec 10, the ingredient hub).

const wholeWheatId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09";
const wholeWheat: Ingredient = { ...flour, id: wholeWheatId, name: "whole wheat flour", slug: "whole-wheat-flour", reconcile_state: "linked", measures: [] };

function routes(ingredient: () => Ingredient, extra: Record<string, (call: RecordedCall) => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient()),
    [`GET /ingredients/${wholeWheatId}`]: () => jsonResponse(200, wholeWheat),
    [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: [], stale_thresholds: { fresh: 14, refrigerated: 45, shelf_stable: 120 } }),
    [`GET /ingredients/${flourId}/price-history`]: () => jsonResponse(200, { points: [] }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
    "GET /ingredients/search": (call: RecordedCall) =>
      jsonResponse(200, { items: call.query.get("q")?.includes("wheat") ? [ingredientMatch(wholeWheat)] : [] }),
    ...extra,
  };
}

const preview = (survivor: string, loser: string, name: string): MergeResult => ({
  survivor_id: survivor,
  loser_id: loser,
  target_name: name,
  products_moving: 1,
  unit_from: "g",
  unit_to: "g",
  prices_needing_bridge: 0,
  measures: [{ label: "cup", canonical_qty: "125", copyable: true, suggested: true }],
});

describe("an ingredient's own page", () => {
  it("merges into another ingredient after the link page's preview, keeping the other's name", async () => {
    let current: Ingredient = flour;
    const merges: RecordedCall[] = [];
    const calls = mockApi(
      routes(() => current, {
        "POST /ingredients/merge/preview": (call) => {
          const b = call.body as { survivor_id: string; loser_id: string; name: string };
          return jsonResponse(200, preview(b.survivor_id, b.loser_id, b.name));
        },
        "POST /ingredients/merge": (call) => {
          merges.push(call);
          current = { ...flour, name: "all-purpose flour (merged into whole wheat flour)", active: false, merged_into: wholeWheatId };
          return jsonResponse(200, preview(wholeWheatId, flourId, "whole wheat flour"));
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await user.click(await screen.findByRole("button", { name: "Merge into…" }));
    await user.type(screen.getByLabelText("Merge into"), "wheat");
    await user.click(await screen.findByRole("option", { name: /whole wheat flour/ }));

    const panel = await screen.findByRole("group", { name: "Merge all-purpose flour into whole wheat flour?" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Cancel" })).toHaveFocus());
    await waitFor(() => expect(panel).toHaveTextContent("1 product moves. all-purpose flour becomes another spelling."));
    expect(within(panel).getByRole("radio", { name: "whole wheat flour" })).toBeChecked();
    expect(calls.find((c) => c.path === "/ingredients/merge/preview")?.body).toEqual({ survivor_id: wholeWheatId, loser_id: flourId, name: "whole wheat flour" });

    await user.click(within(panel).getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(merges).toHaveLength(1));
    expect(merges[0].body).toEqual({ survivor_id: wholeWheatId, loser_id: flourId, copy_measures: ["cup"], name: "whole wheat flour" });
    expect(await screen.findByTestId("notice")).toHaveTextContent("Merged into whole wheat flour.");
    const merged = await screen.findByText(/Its products and spellings are there now\./);
    expect(within(merged).getByRole("link", { name: "whole wheat flour" })).toHaveAttribute("href", `/catalog/ingredients/${wholeWheatId}`);
    for (const name of ["Merge into…", "Link to standard name", "Activate", "Deactivate"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("keeps this ingredient when chosen, under its own name", async () => {
    mockApi(
      routes(() => flour, {
        "POST /ingredients/merge/preview": (call) => {
          const b = call.body as { survivor_id: string; loser_id: string; name: string };
          return jsonResponse(200, preview(b.survivor_id, b.loser_id, b.name));
        },
        "POST /ingredients/merge": () => jsonResponse(200, preview(flourId, wholeWheatId, "all-purpose flour")),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await user.click(await screen.findByRole("button", { name: "Merge into…" }));
    await user.type(screen.getByLabelText("Merge into"), "wheat");
    await user.click(await screen.findByRole("option", { name: /whole wheat flour/ }));
    const panel = await screen.findByRole("group", { name: /^Merge all-purpose flour into/ });
    await user.click(within(panel).getByRole("radio", { name: "all-purpose flour" }));
    expect(await screen.findByRole("group", { name: "Merge whole wheat flour into all-purpose flour?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Merge" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent("Merged whole wheat flour into all-purpose flour.");
  });

  it("refuses to merge an ingredient into itself", async () => {
    mockApi(
      routes(() => flour, {
        "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await user.click(await screen.findByRole("button", { name: "Merge into…" }));
    await user.type(screen.getByLabelText("Merge into"), "flour");
    await user.click(await screen.findByRole("option", { name: /all-purpose flour/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose another ingredient. This is the one you're on.");
    expect(screen.queryByRole("group", { name: /^Merge / })).not.toBeInTheDocument();
  });

  it("links to a standard name, and merges when another ingredient already has it", async () => {
    const links: RecordedCall[] = [];
    mockApi(
      routes(() => flour, {
        "GET /standard-ingredients": () =>
          jsonResponse(200, { items: [{ key: "whole-wheat-flour", name: "whole wheat flour", category: "pantry", category_key: "pantry", canonical_unit: "g", fdc_id: null }] }),
        [`POST /ingredients/${flourId}/link`]: (call) => {
          links.push(call);
          return errorResponse(409, "merge_needed", "Another ingredient has that name.", {
            target_name: "whole wheat flour",
            other: { id: wholeWheatId, name: "whole wheat flour", canonical_unit: "g", products: 4 },
          });
        },
        "POST /ingredients/merge/preview": (call) => {
          const b = call.body as { survivor_id: string; loser_id: string };
          return jsonResponse(200, preview(b.survivor_id, b.loser_id, "whole wheat flour"));
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await user.click(await screen.findByRole("button", { name: "Link to standard name" }));
    await user.type(screen.getByLabelText("Standard name"), "wheat");
    await user.click(await screen.findByRole("option", { name: /whole wheat flour/ }));
    await waitFor(() => expect(links).toHaveLength(1));
    expect(links[0].body).toEqual({ standard_key: "whole-wheat-flour" });
    const panel = await screen.findByRole("group", { name: "Merge all-purpose flour into whole wheat flour?" });
    expect(within(panel).getByRole("radio", { name: "whole wheat flour, 4 products" })).toBeChecked();
  });

  it("offers no link for an ingredient already linked", async () => {
    mockApi(routes(() => ({ ...flour, reconcile_state: "linked" })));
    renderApp(`/catalog/ingredients/${flourId}`);
    expect(await screen.findByRole("button", { name: "Merge into…" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Link to standard name" })).not.toBeInTheDocument();
  });
});
