import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { LinkPage, LinkRow, MergeResult } from "../api/ingredientLink";
import { flour } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented ingredients (1G, UI-5.3 to UI-5.5).
const ids = {
  onions: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6a01",
  sorrel: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6a02",
  scallion: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6a03",
};
const scallionEntry = {
  key: "scallion",
  name: "scallion",
  category: "produce",
  category_key: "produce" as const,
  canonical_unit: "g" as const,
  fdc_id: 170005,
  usda_description: "Onions, spring or scallions (includes tops and bulb), raw",
};
const onions: LinkRow = {
  id: ids.onions,
  name: "green onions",
  canonical_unit: "each",
  category: null,
  category_key: null,
  products: 3,
  suggestion: scallionEntry,
  conflict: { id: ids.scallion, name: "Scallion", canonical_unit: "g", products: 5 },
};
const sorrel: LinkRow = { ...onions, id: ids.sorrel, name: "sorrel", products: 1, suggestion: null, conflict: null };

function routes(state: { page: LinkPage }, extra: Record<string, (call: RecordedCall) => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /ingredients/link": () => jsonResponse(200, state.page),
    ...extra,
  };
}

describe("link page (1G)", () => {
  it("links a row, which leaves, and moves focus to the next row with an announcement", async () => {
    const state = { page: { to_review: [{ ...onions, conflict: null }, sorrel], skipped: [] } as LinkPage };
    const calls = mockApi(
      routes(state, {
        [`POST /ingredients/${ids.onions}/link`]: () => {
          state.page = { to_review: [sorrel], skipped: [] };
          return jsonResponse(200, { ...flour, id: ids.onions, name: "scallion" });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/catalog/ingredients/link");
    expect(await screen.findByRole("heading", { level: 1, name: "Link ingredients to the standard list" })).toBeInTheDocument();
    expect(await screen.findByTestId("link-counts")).toHaveTextContent("2 to review · 0 skipped");
    const [first] = screen.getAllByTestId("link-row");
    expect(first).toHaveTextContent("→ scallion");
    expect(first).toHaveTextContent("“green onions” kept as another spelling");
    expect(first).toHaveTextContent("USDA: Onions, spring or scallions");
    expect(screen.getAllByTestId("link-row")[1]).toHaveTextContent("No standard name fits");

    await user.click(within(first).getByRole("button", { name: "Link" }));
    await waitFor(() => expect(screen.getAllByTestId("link-row")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ standard_key: "scallion" });
    expect(screen.getByTestId("link-announcement")).toHaveTextContent("Linked green onions to scallion. 1 left.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Choose…" })).toHaveFocus());
  });

  it("opens the merge panel on a taken name, focuses Cancel, and merges with the ticked measures", async () => {
    const state = { page: { to_review: [onions], skipped: [] } as LinkPage };
    const preview: MergeResult = {
      survivor_id: ids.scallion,
      loser_id: ids.onions,
      target_name: "scallion",
      products_moving: 3,
      unit_from: "each",
      unit_to: "g",
      prices_needing_bridge: 4,
      measures: [{ label: "bunch", canonical_qty: "1", copyable: false, suggested: false }],
    };
    const merges: RecordedCall[] = [];
    mockApi(
      routes(state, {
        [`POST /ingredients/${ids.onions}/link`]: () =>
          errorResponse(409, "merge_needed", "Scallion already exists. Merge into it?", {
            target_name: "scallion",
            other: { id: ids.scallion, name: "Scallion", canonical_unit: "g", products: 5 },
          }),
        "POST /ingredients/merge/preview": () => jsonResponse(200, preview),
        "POST /ingredients/merge": (call) => {
          merges.push(call);
          state.page = { to_review: [], skipped: [] };
          return jsonResponse(200, preview);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/catalog/ingredients/link");
    await user.click(await screen.findByRole("button", { name: "Link" }));
    const panel = await screen.findByRole("group", { name: "Merge green onions into scallion?" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Cancel" })).toHaveFocus());
    await waitFor(() => expect(panel).toHaveTextContent("Units differ (each → g): 4 prices will need a bridge."));
    expect(panel).toHaveTextContent("3 products move. green onions becomes another spelling.");
    expect(within(panel).getByRole("radio", { name: "Scallion, 5 products" })).toBeChecked();
    expect(panel).toHaveTextContent("bunch can't carry over: the units differ.");
    expect(panel).toHaveTextContent("This can't be undone here.");

    await user.click(within(panel).getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(merges).toHaveLength(1));
    expect(merges[0].body).toEqual({ survivor_id: ids.scallion, loser_id: ids.onions, standard_key: "scallion", copy_measures: [] });
    expect(await screen.findByText("Every ingredient is linked or skipped")).toBeInTheDocument();
    expect(screen.getByTestId("notice")).toHaveTextContent("1 reviewed: 0 linked, 1 merged, 0 skipped.");
  });

  it("renames inline and shows a failed action inside its row with the input kept", async () => {
    const state = { page: { to_review: [sorrel], skipped: [] } as LinkPage };
    mockApi(
      routes(state, {
        [`POST /ingredients/${ids.sorrel}/rename`]: () => errorResponse(409, "ingredient_name_taken", "An inactive ingredient already has that name."),
      }),
    );
    const user = userEvent.setup();
    renderApp("/catalog/ingredients/link");
    await user.click(await screen.findByRole("button", { name: "Rename…" }));
    const field = screen.getByLabelText("New name for sorrel");
    await user.clear(field);
    await user.type(field, "wood sorrel");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const row = screen.getByTestId("link-row");
    expect(await within(row).findByText("An inactive ingredient already has that name.")).toBeInTheDocument();
    expect(screen.getByLabelText("New name for sorrel")).toHaveValue("wood sorrel");
  });

  it("folds skipped rows with Reopen", async () => {
    const state = { page: { to_review: [], skipped: [sorrel] } as LinkPage };
    const calls = mockApi(routes(state, { [`POST /ingredients/${ids.sorrel}/reopen`]: () => jsonResponse(200, flour) }));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients/link");
    expect(await screen.findByText("Every ingredient is linked or skipped")).toBeInTheDocument();
    await user.click(screen.getByText("Skipped (1)"));
    await user.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(calls.some((c) => c.path === `/ingredients/${ids.sorrel}/reopen`)).toBe(true));
  });
});
