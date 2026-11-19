import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CleanupSuggestion } from "../api/catalogCleanup";
import {
  adminUser,
  errorResponse,
  jsonResponse,
  mockApi,
  renderApp,
  type RecordedCall,
  type RouteHandler,
} from "./helpers";

// An invented bottle filed under the generic word (issue 525).
const move: CleanupSuggestion = {
  key: "product_move:0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c01:abc",
  kind: "product_move",
  subject_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7c01",
  subject: "Harbor Distillers Bourbon Whiskey",
  before: "whiskey",
  after: "bourbon",
  reason:
    "its name says bourbon, a kind of whiskey; 1 recipe line naming whiskey loses its price; pin it or log a whiskey price",
  ready: true,
  value: "",
  choosable: false,
  confidence: "guess",
  consequences: [
    { text: "Harbor dram: whiskey loses its price", recipe_id: "r-dram" },
    { text: "Pier sour: whiskey stays pinned to it, now under bourbon", recipe_id: "r-sour" },
  ],
  fingerprint: "f1",
};

function mount(apply: RouteHandler, items: () => CleanupSuggestion[] = () => [move]) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /catalog/cleanup": () => jsonResponse(200, { items: items() }),
    "POST /catalog/cleanup/apply": apply,
  });
  renderApp("/catalog/cleanup");
  return calls;
}

const applied = (call: RecordedCall) =>
  jsonResponse(200, {
    applied: 1,
    gone: [],
    held: [],
    results: [
      {
        key: (call.body as { keys: string[] }).keys[0],
        prices_renormalized: 0,
        recipes_recomputed: 3,
        measures_dropped: 0,
        measures_rewritten: 0,
      },
    ],
  });

describe("a product move in Tidy the catalog (issue 525)", () => {
  it("lists what the move changes, linked to the recipes, and is named for its target", async () => {
    mount(applied);
    const group = await screen.findByRole("region", {
      name: /Products under a broader ingredient/,
    });
    const row = within(group).getByTestId("cleanup-row");
    expect(within(row).getByText("Check this")).toBeInTheDocument();
    const list = within(row).getByRole("list", { name: "What else this changes" });
    expect(
      within(list).getByRole("link", { name: "Harbor dram: whiskey loses its price" }),
    ).toHaveAttribute("href", "/cook/recipes/r-dram");
    expect(within(row).getByRole("button", { name: /^Move to bourbon/ })).toBeInTheDocument();
  });

  it("sends the fingerprint it showed and ends with the counts", async () => {
    const user = userEvent.setup();
    const calls = mount(applied);
    await user.click(await screen.findByRole("button", { name: /^Move to bourbon/ }));
    expect(await screen.findByText("Moved to bourbon: 3 recipes recomputed.")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({
      keys: [move.key],
      chosen: {},
      fingerprints: { [move.key]: "f1" },
    });
  });

  it("keeps the row and shows the server's words when the move changed meanwhile", async () => {
    const user = userEvent.setup();
    mount(() =>
      errorResponse(409, "cleanup_changed", "What this changes is different now; check it again."),
    );
    await user.click(await screen.findByRole("button", { name: /^Move to bourbon/ }));
    const row = screen.getByTestId("cleanup-row");
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "What this changes is different now; check it again. (cleanup_changed)",
    );
    expect(screen.queryByText("Nothing to tidy")).not.toBeInTheDocument();
  });

  it("shows five consequences, then all of them on request", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 7 }, (_, i) => ({
      text: `Harbor dram ${i + 1}: whiskey loses its price`,
      recipe_id: `r-${i}`,
    }));
    mount(applied, () => [{ ...move, consequences: many }]);
    const list = await screen.findByRole("list", { name: "What else this changes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    await user.click(screen.getByRole("button", { name: "Show all 7" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
  });
});
