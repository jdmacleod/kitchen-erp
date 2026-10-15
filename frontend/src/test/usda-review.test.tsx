import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { UsdaReview } from "../api/usdaReview";
import { flour } from "./catalog-fixtures";
import {
  adminUser,
  errorResponse,
  jsonResponse,
  mockApi,
  renderApp,
  type RecordedCall,
} from "./helpers";

// Invented values for the USDA section on Needs a bridge (1G, UI-5.6, UI-5.7).
const garlicId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01";
const review: UsdaReview = {
  loaded: true,
  release_date: "2026-04-30",
  groups: [
    {
      ingredient_id: garlicId,
      name: "garlic",
      canonical_unit: "g",
      fdc_id: 1002,
      usda_description: "Garlic, raw",
      has_density: false,
      densities: [
        {
          portion_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b01",
          portion_label: "1 teaspoon",
          gram_weight: "2.8",
          density_g_per_ml: "0.56806",
          suggested: false,
        },
      ],
      measures: [{ label: "clove", canonical_qty: "3", from_portion: "1 clove", suggested: true }],
      density_spread_pct: null,
    },
  ],
};

function routes(
  state: { review: UsdaReview },
  extra: Record<string, (call: RecordedCall) => Response> = {},
) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /price-book/needs-bridge": () => jsonResponse(200, { items: [] }),
    "GET /usda/review": () => jsonResponse(200, state.review),
    ...extra,
  };
}

describe("USDA suggestions (1G)", () => {
  it("says how to load USDA data when it isn't loaded", async () => {
    mockApi(routes({ review: { loaded: false, release_date: null, groups: [] } }));
    renderApp("/catalog/bridges");
    expect(await screen.findByText(/USDA data isn't loaded yet/)).toHaveTextContent(
      "kerp import usda",
    );
  });

  it("shows nothing when there is nothing to review", async () => {
    mockApi(routes({ review: { loaded: true, release_date: "2026-04-30", groups: [] } }));
    renderApp("/catalog/bridges");
    expect(await screen.findByText("Every price is normalized")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "USDA suggestions" })).not.toBeInTheDocument();
  });

  it("accepts the chosen density and ticked measures, then says they are unconfirmed", async () => {
    const state = { review };
    const posts: RecordedCall[] = [];
    mockApi(
      routes(state, {
        [`POST /usda/review/${garlicId}`]: (call) => {
          posts.push(call);
          state.review = { ...review, groups: [] };
          return jsonResponse(200, {
            saved: 2,
            ingredient: { ...flour, id: garlicId, name: "garlic" },
          });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const group = await screen.findByTestId("usda-group");
    expect(group).toHaveTextContent("garlic · g · linked to “Garlic, raw”");
    expect(screen.getByText("Data: USDA FoodData Central, release 2026-04-30")).toBeInTheDocument();
    expect(within(group).getByRole("checkbox", { name: "1 clove = 3 g" })).toBeChecked();
    expect(within(group).queryByRole("button", { name: /Accept all/ })).not.toBeInTheDocument();
    await user.click(
      within(group).getByRole("radio", { name: "1 teaspoon = 2.8 g → 0.56806 g/ml" }),
    );
    await user.click(within(group).getByRole("button", { name: "Accept selected" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      density_portion_id: review.groups[0].densities[0].portion_id,
      measures: ["clove"],
      replace_density: false,
    });
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Saved 2 values for garlic as unconfirmed. Confirm them on its page after checking a label.",
    );
    expect(screen.getByRole("link", { name: "Open garlic" })).toHaveAttribute(
      "href",
      `/catalog/ingredients/${garlicId}`,
    );
  });

  it("offers Keep it · Replace when a density was set meanwhile", async () => {
    const posts: RecordedCall[] = [];
    mockApi(
      routes(
        { review },
        {
          [`POST /usda/review/${garlicId}`]: (call) => {
            posts.push(call);
            if (posts.length === 1)
              return errorResponse(409, "density_exists", "garlic now has a density.", {
                density_g_per_ml: "0.52000",
                density_source: "measured",
                density_confirmed: false,
              });
            return jsonResponse(200, {
              saved: 1,
              ingredient: { ...flour, id: garlicId, name: "garlic" },
            });
          },
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const group = await screen.findByTestId("usda-group");
    await user.click(within(group).getByRole("radio", { name: /1 teaspoon/ }));
    await user.click(within(group).getByRole("button", { name: "Accept selected" }));
    const alert = await within(group).findByRole("alert");
    expect(alert).toHaveTextContent("garlic now has a density of 0.52 g/ml (unconfirmed).");
    const buttons = within(alert).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Keep it", "Replace"]);
    await user.click(within(alert).getByRole("button", { name: "Replace" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].body).toMatchObject({
      replace_density: true,
      density_portion_id: review.groups[0].densities[0].portion_id,
    });
  });

  it("skips an ingredient", async () => {
    const posts: RecordedCall[] = [];
    mockApi(
      routes(
        { review },
        {
          [`POST /usda/review/${garlicId}`]: (call) => (
            posts.push(call),
            jsonResponse(200, { saved: 0, ingredient: flour })
          ),
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    await user.click(
      within(await screen.findByTestId("usda-group")).getByRole("button", { name: "Skip" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ skip: true });
  });
});

describe("USDA suggestions: what starts chosen (#359)", () => {
  it("starts with the suggested density and measures, and notes densities that disagree", async () => {
    const posts: RecordedCall[] = [];
    const syrup = {
      ...review.groups[0],
      name: "maple syrup",
      usda_description: "Syrups, maple",
      densities: [
        {
          portion_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b11",
          portion_label: "1 tbsp",
          gram_weight: "17",
          density_g_per_ml: "1.15",
          suggested: false,
        },
        {
          portion_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b12",
          portion_label: "1 cup",
          gram_weight: "279",
          density_g_per_ml: "1.18",
          suggested: true,
        },
      ],
      measures: [
        { label: "nlea serving", canonical_qty: "30", from_portion: "1 serving", suggested: false },
        { label: "slice", canonical_qty: "25", from_portion: "1 slice", suggested: true },
      ],
      density_spread_pct: 5,
    };
    mockApi(
      routes(
        { review: { ...review, groups: [syrup] } },
        {
          [`POST /usda/review/${garlicId}`]: (call) => (
            posts.push(call),
            jsonResponse(200, { saved: 2, ingredient: flour })
          ),
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const group = await screen.findByTestId("usda-group");
    expect(within(group).getByRole("radio", { name: "1 cup = 279 g → 1.18 g/ml" })).toBeChecked();
    expect(within(group).getByRole("radio", { name: "None" })).not.toBeChecked();
    expect(within(group).getByText(/These portions differ by 5%/)).toBeInTheDocument();
    expect(
      within(group).getByRole("checkbox", { name: "1 nlea serving = 30 g" }),
    ).not.toBeChecked();
    expect(within(group).getByRole("checkbox", { name: "1 slice = 25 g" })).toBeChecked();
    await user.click(within(group).getByRole("button", { name: "Accept selected" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      density_portion_id: syrup.densities[1].portion_id,
      measures: ["slice"],
      replace_density: false,
    });
  });
});

describe("USDA suggestions: changing a wrong food (#359)", () => {
  it("changes a wrong USDA food from the row", async () => {
    const posts: RecordedCall[] = [];
    mockApi(
      routes(
        { review },
        {
          "GET /usda/suggestions": (call) =>
            jsonResponse(200, {
              loaded: true,
              items: [
                {
                  fdc_id: 1002,
                  description: "Garlic, raw",
                  similarity: "1",
                  densities: [],
                  measures: [],
                },
                {
                  fdc_id: 2002,
                  description: call.query.get("name") === "garlic" ? "Garlic, invented" : "Other",
                  similarity: "0.8",
                  densities: [],
                  measures: [],
                },
              ],
            }),
          [`POST /usda/review/${garlicId}/link`]: (call) => (
            posts.push(call),
            jsonResponse(200, { ...flour, id: garlicId, name: "garlic" })
          ),
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const group = await screen.findByTestId("usda-group");
    await user.click(within(group).getByRole("button", { name: "Wrong food? Change the link" }));
    const panel = within(group).getByRole("group", { name: "Change the USDA food for garlic" });
    expect(within(panel).getByLabelText("Search USDA foods")).toHaveValue("garlic");
    // The food it is linked to now is not offered again.
    await user.click(await within(panel).findByRole("button", { name: "Use “Garlic, invented”" }));
    expect(
      within(panel).queryByRole("button", { name: "Use “Garlic, raw”" }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ fdc_id: 2002 });
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Linked garlic to “Garlic, invented”.",
    );
  });

  it("can leave an ingredient with no USDA food", async () => {
    const posts: RecordedCall[] = [];
    mockApi(
      routes(
        { review },
        {
          "GET /usda/suggestions": () => jsonResponse(200, { loaded: true, items: [] }),
          [`POST /usda/review/${garlicId}/link`]: (call) => (
            posts.push(call),
            jsonResponse(200, flour)
          ),
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const group = await screen.findByTestId("usda-group");
    await user.click(within(group).getByRole("button", { name: "Wrong food? Change the link" }));
    await user.click(within(group).getByRole("button", { name: "No USDA food" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ fdc_id: null });
    expect(await screen.findByTestId("notice")).toHaveTextContent("garlic has no USDA food now.");
  });
});

describe("USDA suggestions: paging (#359)", () => {
  it("shows 20 rows at a time", async () => {
    const groups = Array.from({ length: 25 }, (_, i) => ({
      ...review.groups[0],
      ingredient_id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f${String(1000 + i).padStart(4, "0")}`,
      name: `herb ${String(i + 1).padStart(2, "0")}`,
    }));
    mockApi(routes({ review: { ...review, groups } }));
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    await screen.findAllByTestId("usda-group");
    expect(screen.getAllByTestId("usda-group")).toHaveLength(20);
    expect(
      screen.getByText("25 ingredients with values to review · showing 20"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show 5 more of 5" }));
    expect(screen.getAllByTestId("usda-group")).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /Show .* more/ })).not.toBeInTheDocument();
  });
});
