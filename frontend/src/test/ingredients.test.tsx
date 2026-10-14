import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import { flour, flourId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function baseRoutes(items: () => Ingredient[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: items(), next_cursor: null }),
    "GET /usda/suggestions": () => jsonResponse(200, { items: [], loaded: false }),
    // A new ingredient's likeliest category (issue 344): the standard list's, here.
    "GET /ingredients/category-suggestion": (call: RecordedCall) =>
      jsonResponse(200, suggestionFor(call.query.get("name") ?? "")),
  };
}

const SUGGESTED: Record<string, string> = {
  cumin: "spices",
  peas: "frozen",
  "all-purpose flour": "pantry",
};

function suggestionFor(name: string) {
  const category = SUGGESTED[name] ?? null;
  return { category, source: category ? "standard" : null };
}

/** Open the add drawer from the page header. */
async function openAddDrawer(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Ingredients" });
  await user.click(screen.getAllByRole("button", { name: "Add ingredient" })[0]);
  return screen.findByRole("dialog", { name: "Add ingredient" });
}

describe("ingredients", () => {
  it("creates an ingredient from a name and its suggested category", async () => {
    let items: Ingredient[] = [];
    const created: Ingredient = {
      ...flour,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09",
      name: "cumin",
      category: null,
      measures: [],
    };
    const calls = mockApi({
      ...baseRoutes(() => items),
      "POST /ingredients": () => {
        items = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");

    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "cumin");
    // The category is prefilled from the standard list, and says so (issue 344).
    await waitFor(() => expect(screen.getByLabelText("Category")).toHaveValue("spices"));
    expect(screen.getByText("Suggested from the standard list.")).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/ingredients");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ name: "cumin", canonical_unit: "g", category: "spices" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);

    // The drawer closes and the new row, now in the list, takes focus (G10).
    const list = await screen.findByRole("list", { name: "Ingredients" });
    const row = within(list).getByRole("link", { name: "cumin" });
    expect(row).toHaveAttribute("href", `/catalog/ingredients/${created.id}`);
    await waitFor(() => expect(row).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("leaves perishability to the standard list or category unless one is picked", async () => {
    const created: Ingredient = {
      ...flour,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b0a",
      name: "peas",
      category: null,
      measures: [],
    };
    const calls = mockApi({
      ...baseRoutes(() => []),
      "POST /ingredients": () => jsonResponse(201, created),
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");

    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "peas");
    await waitFor(() => expect(screen.getByLabelText("Category")).toHaveValue("frozen"));
    await user.click(screen.getByText("More: density, yield, perishability, notes"));
    const select = screen.getByLabelText("Perishability");
    expect(select).toHaveValue("");
    expect(screen.getByText("From the standard list, or else the category.")).toBeInTheDocument();
    await user.selectOptions(select, "frozen");
    expect(screen.getByText("Bought and kept frozen.")).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/ingredients");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({
      name: "peas",
      canonical_unit: "g",
      category: "frozen",
      perishability: "frozen",
    });
  });

  it("fills the density from a USDA suggestion with source usda and queues a measure", async () => {
    const created: Ingredient = { ...flour, measures: [] };
    const calls = mockApi({
      ...baseRoutes(() => []),
      "GET /usda/suggestions": () =>
        jsonResponse(200, {
          loaded: true,
          items: [
            {
              fdc_id: 1001,
              description: "Wheat flour, white, all-purpose",
              similarity: "0.8",
              densities: [{ density_g_per_ml: "0.528", from_portion: "1 cup" }],
              measures: [{ label: "cup", canonical_qty_g: "125", from_portion: "1 cup" }],
            },
          ],
        }),
      "POST /ingredients": () => jsonResponse(201, created),
      [`POST /ingredients/${flourId}/measures`]: (call) =>
        jsonResponse(201, { ...flour.measures[0], ...(call.body as object) }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");

    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "all-purpose flour");
    await waitFor(() => expect(screen.getByLabelText("Category")).toHaveValue("pantry"));

    await user.click(
      await screen.findByRole("button", { name: "Use density 0.528 g/ml (from 1 cup)" }),
    );
    expect(screen.getByLabelText("Density (g/ml)")).toHaveValue("0.528");
    expect(screen.getByLabelText("Density source")).toHaveValue("usda");

    await user.click(screen.getByRole("button", { name: "Add measure cup = 125 g" }));
    expect(screen.getByRole("group", { name: "Measures to add" })).toHaveTextContent("cup = 125 g");

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );
    // The new row is not in this list, so the Notice links to it (G10).
    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent(/with 1 measure/);
    await waitFor(() =>
      expect(within(notice).getByRole("link", { name: "Open it" })).toHaveFocus(),
    );

    const post = calls.find((c) => c.method === "POST" && c.path === "/ingredients");
    expect(post?.body).toEqual({
      name: "all-purpose flour",
      canonical_unit: "g",
      category: "pantry",
      density_g_per_ml: "0.528",
      density_source: "usda",
    });
    const measure = calls.find(
      (c) => c.method === "POST" && c.path === `/ingredients/${flourId}/measures`,
    );
    expect(measure?.body).toEqual({ label: "cup", canonical_qty: "125", source: "usda" });
  });

  it("shows no suggestions panel when the USDA table is not loaded", async () => {
    mockApi(baseRoutes(() => []));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");
    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "all-purpose flour");
    await waitFor(() =>
      expect(screen.queryByText("Reference suggestions")).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("searches with a debounced q and can show inactive ingredients", async () => {
    const inactive: Ingredient = {
      ...flour,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b03",
      name: "old flour",
      active: false,
    };
    const calls = mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients": (call) =>
        jsonResponse(200, {
          items: call.query.get("include_inactive") === "true" ? [flour, inactive] : [flour],
          next_cursor: null,
        }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");

    const list = await screen.findByRole("list", { name: "Ingredients" });
    expect(within(list).getByText("all-purpose flour")).toBeInTheDocument();
    expect(within(list).queryByText("old flour")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Search ingredients"), "flour");
    await waitFor(() => expect(calls.some((c) => c.query.get("q") === "flour")).toBe(true));

    await user.click(screen.getByLabelText("Show inactive"));
    expect(await screen.findByText("old flour")).toBeInTheDocument();
    expect(screen.getByText("inactive")).toBeInTheDocument();
  });
});

describe("ingredients, empty states and suggestions (G11, UI-3.6)", () => {
  it("offers the first ingredient on an empty catalog, and clears a search that matched nothing", async () => {
    mockApi(baseRoutes(() => []));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients?q=zzqx");
    const empty = await screen.findByRole("region", { name: "No ingredients match ‘zzqx’" });
    await user.click(within(empty).getByRole("button", { name: "Clear search" }));
    expect(
      await screen.findByRole("region", { name: "Add your first ingredient" }),
    ).toBeInTheDocument();
  });

  it("offers the nine known categories as a choice, and Other for free text", async () => {
    mockApi(baseRoutes(() => []));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");
    await openAddDrawer(user);
    const field = screen.getByLabelText("Category");
    expect([...field.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Choose a category",
      "Produce",
      "Dairy",
      "Meat",
      "Seafood",
      "Bakery",
      "Pantry",
      "Spices",
      "Beverages",
      "Frozen",
      "Other…",
    ]);
    expect(screen.queryByLabelText("Other category")).not.toBeInTheDocument();
    await user.selectOptions(field, "Other…");
    expect(screen.getByLabelText("Other category")).toBeInTheDocument();
  });
});

describe("a typed name, conformed (issue 345)", () => {
  function routes(calls: { body?: unknown }[]) {
    return {
      ...baseRoutes(() => []),
      "GET /ingredients/conformed-name": (call: RecordedCall) => {
        const typed = call.query.get("name") ?? "";
        return jsonResponse(
          200,
          typed === "Okra, frozen"
            ? { name: "frozen okra", changes: ["“frozen” goes in front, as a cook writes it"] }
            : { name: typed, changes: [] },
        );
      },
      "GET /ingredients/search": (call: RecordedCall) =>
        jsonResponse(200, {
          items:
            call.query.get("q") === "lantern okra"
              ? [
                  {
                    kind: "standard",
                    id: null,
                    key: "lantern-okra",
                    name: "lantern okra",
                    canonical_unit: "g",
                    active: true,
                    category: "produce",
                    category_key: "produce",
                    matched_spelling: null,
                    exact: false,
                  },
                ]
              : [],
        }),
      "POST /ingredients": (call: RecordedCall) => {
        calls.push(call);
        return jsonResponse(201, { ...flour, name: "x", measures: [] });
      },
    };
  }

  it("saves the conformed name, or the typed one when kept", async () => {
    const posts: { body?: unknown }[] = [];
    mockApi(routes(posts));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");
    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "Okra, frozen");
    const preview = await screen.findByTestId("new-ingredient-name-preview");
    expect(preview).toHaveTextContent("Will be saved as “frozen okra”");
    expect(preview).toHaveTextContent("goes in front");
    await user.selectOptions(screen.getByLabelText("Category"), "frozen");
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ name: "frozen okra", category: "frozen" });
    expect(posts[0].body).not.toHaveProperty("keep_name");
  });

  it("keeps the name as typed on request", async () => {
    const posts: { body?: unknown }[] = [];
    mockApi(routes(posts));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");
    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "Okra, frozen");
    await user.click(await screen.findByRole("button", { name: "Keep “Okra, frozen”" }));
    expect(screen.getByTestId("new-ingredient-name-kept")).toHaveTextContent("Saved as typed.");
    await user.selectOptions(screen.getByLabelText("Category"), "frozen");
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ name: "Okra, frozen", keep_name: true });
  });

  it("offers a standard entry for the typed name and creates from it", async () => {
    const posts: { body?: unknown }[] = [];
    mockApi(routes(posts));
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");
    await openAddDrawer(user);
    await user.type(screen.getByLabelText("Name"), "lantern okra");
    const offers = await screen.findByRole("group", { name: "From the standard list" });
    await user.click(within(offers).getByRole("button", { name: /lantern okra/ }));
    expect(screen.queryByLabelText("Category")).not.toBeInTheDocument();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ name: "lantern okra", standard_key: "lantern-okra" });
  });
});
