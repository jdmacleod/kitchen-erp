import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CleanupSuggestion } from "../api/catalogCleanup";
import type { LinkPage, LinkRow } from "../api/ingredientLink";
import type { NeedsBridgeItem } from "../api/pricebook";
import { flour } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// An invented spirit linked to a standard name in another unit (issue 527).
const whiskeyId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d01";
const sure: CleanupSuggestion = {
  key: `unit_mismatch:${whiskeyId}:abc`,
  kind: "unit_mismatch",
  subject_id: whiskeyId,
  subject: "whiskey",
  before: "g",
  after: "ml",
  reason: "the standard list measures whiskey in ml; 12 prices fail to normalize in g",
  ready: true,
  value: "",
  choosable: false,
  confidence: "sure",
  consequences: [],
  fingerprint: "f1",
  measures_dropped: 0,
};
const guess: CleanupSuggestion = {
  ...sure,
  confidence: "guess",
  reason: "the standard list measures whiskey in ml; 2 measures drop",
  consequences: [
    { text: "1 shot = 42 g is dropped (entered by hand)", recipe_id: null },
    { text: "1 bottle = 700 g is dropped", recipe_id: null },
    { text: "Harbor dram: whiskey can no longer convert", recipe_id: "r-dram" },
  ],
  measures_dropped: 2,
};
const result = {
  key: sure.key,
  prices_renormalized: 12,
  recipes_recomputed: 4,
  measures_dropped: 0,
  measures_rewritten: 0,
};

function tidy(items: CleanupSuggestion[], posts: RecordedCall[]) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /catalog/cleanup": () => jsonResponse(200, { items }),
    "POST /catalog/cleanup/apply": (call) => {
      posts.push(call);
      return jsonResponse(200, { applied: 1, gone: [], held: [], results: [result] });
    },
  });
  renderApp("/catalog/cleanup");
}

describe("a unit switch in Tidy the catalog (issue 527)", () => {
  it("applies a sure switch in one click and ends with the counts", async () => {
    const posts: RecordedCall[] = [];
    tidy([sure], posts);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Switch to ml/ }));
    expect(
      await screen.findByText("Switched to ml: 12 prices renormalized, 4 recipes recomputed."),
    ).toBeInTheDocument();
    expect(posts[0].body).toEqual({
      keys: [sure.key],
      chosen: {},
      fingerprints: { [sure.key]: "f1" },
    });
  });

  it("opens a confirm for a guess, with a red button counting the drops; Cancel returns focus", async () => {
    const posts: RecordedCall[] = [];
    tidy([guess], posts);
    const user = userEvent.setup();
    const trigger = await screen.findByRole("button", { name: /^Switch to ml/ });
    await user.click(trigger);
    expect(posts).toHaveLength(0);
    const panel = screen.getByTestId("unit-switch-confirm");
    const drop = within(panel).getByRole("button", { name: "Switch and drop 2 measures" });
    expect(drop).toHaveFocus();
    expect(drop).toHaveClass("bg-red-700");
    expect(
      within(panel).getByText("1 shot = 42 g is dropped (entered by hand)"),
    ).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("unit-switch-confirm")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Switch and drop 2 measures" }));
    await waitFor(() => expect(posts).toHaveLength(1));
  });
});

const entry = {
  key: "whiskey",
  name: "whiskey",
  category: "beverages",
  category_key: null,
  canonical_unit: "ml" as const,
  fdc_id: null,
  usda_description: null,
};
const linkRow: LinkRow = {
  id: whiskeyId,
  name: "whisky",
  canonical_unit: "g",
  category: null,
  category_key: null,
  products: 2,
  suggestion: entry,
  conflict: null,
};

describe("the link page's follow-up (issue 527)", () => {
  it("offers the switch once a row is linked to a name measured in another unit", async () => {
    const state = { page: { to_review: [linkRow], skipped: [] } as LinkPage };
    const switched: RecordedCall[] = [];
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /ingredients/link": () => jsonResponse(200, state.page),
      "GET /ingredients/duplicates": () => jsonResponse(200, { items: [] }),
      "GET /usda/review": () => jsonResponse(200, { loaded: true, release_date: null, groups: [] }),
      [`POST /ingredients/${whiskeyId}/link`]: () => {
        state.page = { to_review: [], skipped: [] };
        return jsonResponse(200, { ...flour, id: whiskeyId, name: "whiskey" });
      },
      [`GET /ingredients/${whiskeyId}/unit-switch`]: () => jsonResponse(200, { suggestion: sure }),
      [`POST /ingredients/${whiskeyId}/unit-switch`]: (call) => {
        switched.push(call);
        return jsonResponse(200, result);
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients/link");
    await user.click(
      await within(await screen.findByTestId("link-row")).findByRole("button", { name: "Link" }),
    );
    const offer = await screen.findByTestId("unit-switch-offer");
    expect(offer).toHaveTextContent(
      "The standard list measures this in ml; this ingredient is in g.",
    );
    await user.click(within(offer).getByRole("button", { name: "Switch to ml" }));
    await waitFor(() => expect(switched).toHaveLength(1));
    expect(switched[0].body).toEqual({ fingerprint: "f1" });
  });
});

function bridgeRow(skipped: boolean): NeedsBridgeItem {
  return {
    ingredient: { id: whiskeyId, name: "whiskey", canonical_unit: "g" },
    product: {
      id: "p-bottle",
      name: "Harbor whiskey",
      brand: null,
      pack_qty: "750",
      pack_unit: "ml",
    },
    status: "no_density",
    switch_to_ml: false,
    standard_switch: { unit: "ml", key: sure.key, skipped },
    observation_count: 3,
    latest_observed_at: "2026-10-01T15:00:00Z",
  };
}

function bridges(item: NeedsBridgeItem) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok", features: ["catalog", "shop"] }),
    "GET /price-book/needs-bridge": () => jsonResponse(200, { items: [item] }),
    "GET /usda/review": () => jsonResponse(200, { loaded: true, release_date: null, groups: [] }),
    "GET /bridges/unconfirmed": () => jsonResponse(200, { items: [] }),
    [`GET /ingredients/${whiskeyId}/unit-switch`]: () => jsonResponse(200, { suggestion: guess }),
  });
  renderApp("/catalog/bridges");
}

describe("Needs a bridge defers to the standard switch (issue 527)", () => {
  it("links to the suggestion in Tidy the catalog, and offers no ml switch of its own", async () => {
    bridges(bridgeRow(false));
    const link = await screen.findByRole("link", { name: "Switch to the standard unit (ml)" });
    expect(link).toHaveAttribute(
      "href",
      `/catalog/cleanup#${encodeURIComponent(`cleanup-row-${sure.key}`)}`,
    );
    expect(screen.queryByRole("button", { name: "Measure in ml instead" })).not.toBeInTheDocument();
  });

  it("makes the switch inline, with the same confirm, once it was skipped there", async () => {
    bridges(bridgeRow(true));
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Switch to the standard unit (ml)" }),
    );
    expect(
      within(screen.getByTestId("unit-switch-confirm")).getByRole("button", {
        name: "Switch and drop 2 measures",
      }),
    ).toBeInTheDocument();
  });
});
