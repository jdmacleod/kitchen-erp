import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import { flour, flourId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp } from "./helpers";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function baseRoutes(items: () => Ingredient[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: items(), next_cursor: null }),
    "GET /usda/suggestions": () => jsonResponse(200, { items: [], loaded: false }),
  };
}

describe("ingredients", () => {
  it("creates an ingredient with only a name", async () => {
    let items: Ingredient[] = [];
    const created: Ingredient = { ...flour, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09", name: "cumin", category: null, measures: [] };
    const calls = mockApi({
      ...baseRoutes(() => items),
      "POST /ingredients": () => {
        items = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp("/ingredients");

    expect(await screen.findByText("No ingredients yet")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "cumin");
    await user.click(screen.getByRole("button", { name: "Create ingredient" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/ingredients");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ name: "cumin", canonical_unit: "g" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);

    expect(await within(mainRegion()).findByRole("status")).toHaveTextContent(/Created/);
    const list = await screen.findByRole("list", { name: "Ingredients" });
    expect(within(list).getByRole("link", { name: "cumin" })).toHaveAttribute("href", `/ingredients/${created.id}`);
    // The form reset for the next entry.
    expect(screen.getByLabelText("Name")).toHaveValue("");
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
    renderApp("/ingredients");

    await screen.findByText("No ingredients yet");
    await user.type(screen.getByLabelText("Name"), "all-purpose flour");

    await user.click(await screen.findByRole("button", { name: "Use density 0.528 g/ml (from 1 cup)" }));
    expect(screen.getByLabelText("Density (g/ml)")).toHaveValue("0.528");
    expect(screen.getByLabelText("Density source")).toHaveValue("usda");

    await user.click(screen.getByRole("button", { name: "Add measure cup = 125 g" }));
    expect(screen.getByRole("group", { name: "Measures to add" })).toHaveTextContent("cup = 125 g");

    await user.click(screen.getByRole("button", { name: "Create ingredient" }));
    expect(await within(mainRegion()).findByRole("status")).toHaveTextContent(
      /with 1 measure/,
    );

    const post = calls.find((c) => c.method === "POST" && c.path === "/ingredients");
    expect(post?.body).toEqual({
      name: "all-purpose flour",
      canonical_unit: "g",
      density_g_per_ml: "0.528",
      density_source: "usda",
    });
    const measure = calls.find((c) => c.method === "POST" && c.path === `/ingredients/${flourId}/measures`);
    expect(measure?.body).toEqual({ label: "cup", canonical_qty: "125", source: "usda" });
  });

  it("shows no suggestions panel when the USDA table is not loaded", async () => {
    mockApi(baseRoutes(() => []));
    const user = userEvent.setup();
    renderApp("/ingredients");
    await screen.findByText("No ingredients yet");
    await user.type(screen.getByLabelText("Name"), "all-purpose flour");
    await waitFor(() => expect(screen.queryByText("Reference suggestions")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("searches with a debounced q and can show inactive ingredients", async () => {
    const inactive: Ingredient = { ...flour, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b03", name: "old flour", active: false };
    const calls = mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients": (call) =>
        jsonResponse(200, {
          items: call.query.get("include_inactive") === "true" ? [flour, inactive] : [flour],
          next_cursor: null,
        }),
    });
    const user = userEvent.setup();
    renderApp("/ingredients");

    const list = await screen.findByRole("list", { name: "Ingredients" });
    expect(within(list).getByText("all-purpose flour")).toBeInTheDocument();
    expect(within(list).queryByText("old flour")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Search"), "flour");
    await waitFor(() => expect(calls.some((c) => c.query.get("q") === "flour")).toBe(true));

    await user.click(screen.getByLabelText("Show inactive"));
    expect(await screen.findByText("old flour")).toBeInTheDocument();
    expect(screen.getByText("inactive")).toBeInTheDocument();
  });
});
