import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Ingredient } from "../api/catalog";
import { cupMeasureId, flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

function baseRoutes(ingredient: () => Ingredient) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient()),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
  };
}

describe("ingredient detail", () => {
  it("shows the typed failure code when a conversion needs a density that is missing", async () => {
    const calls = mockApi({
      ...baseRoutes(() => flour),
      [`POST /ingredients/${flourId}/convert`]: () =>
        jsonResponse(200, {
          ok: false,
          failure_code: "no_density",
          message: "Crossing mass and volume needs a density; none is known.",
          version: "1",
        }),
    });
    const user = userEvent.setup();
    renderApp(`/ingredients/${flourId}`);

    expect(await screen.findByRole("heading", { name: "all-purpose flour" })).toBeInTheDocument();
    const bench = screen.getByRole("form", { name: "Conversion test bench" });
    await user.clear(within(bench).getByLabelText("Quantity"));
    await user.type(within(bench).getByLabelText("Quantity"), "2");
    await user.type(within(bench).getByLabelText("Unit or measure"), "ml");
    await user.click(within(bench).getByRole("button", { name: "Convert" }));

    expect(await screen.findByTestId("bench-failure-code")).toHaveTextContent("no_density");
    expect(screen.getByText("Crossing mass and volume needs a density; none is known.")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === `/ingredients/${flourId}/convert`);
    expect(post?.body).toEqual({ qty: "2", unit: "ml" });
  });

  it("shows the result and provenance of a conversion through a product", async () => {
    mockApi({
      ...baseRoutes(() => flour),
      [`POST /ingredients/${flourId}/convert`]: (call) =>
        jsonResponse(200, {
          ok: true,
          qty: "2267.96185",
          unit: "g",
          provenance: {
            bridge_kind: "pack",
            source: null,
            confirmed: null,
            detail: `1 each = 5 lb (${(call.body as { product_id: string }).product_id})`,
            via: { bridge_kind: "none", source: null, confirmed: null, detail: "lb → g", rests_on_unconfirmed: false },
            rests_on_unconfirmed: false,
          },
          version: "1",
        }),
    });
    const user = userEvent.setup();
    renderApp(`/ingredients/${flourId}`);

    await screen.findByRole("heading", { name: "all-purpose flour" });
    const bench = screen.getByRole("form", { name: "Conversion test bench" });
    await user.type(within(bench).getByLabelText("Unit or measure"), "each");
    await user.selectOptions(await within(bench).findByLabelText("Through product"), flourProduct.id);
    await user.click(within(bench).getByRole("button", { name: "Convert" }));

    expect(await screen.findByTestId("bench-result")).toHaveTextContent("= 2267.96185 g");
    expect(screen.getByText("product pack")).toBeInTheDocument();
    expect(screen.getByText("no unconfirmed bridges")).toBeInTheDocument();
  });

  it("confirms a measure through the confirm endpoint", async () => {
    let ingredient = flour;
    const calls = mockApi({
      ...baseRoutes(() => ingredient),
      [`POST /measures/${cupMeasureId}/confirm`]: () => {
        const confirmed = { ...flour.measures[0], confirmed: true };
        ingredient = { ...flour, measures: [confirmed] };
        return jsonResponse(200, confirmed);
      },
    });
    const user = userEvent.setup();
    renderApp(`/ingredients/${flourId}`);

    const table = await screen.findByRole("table", { name: "Named measures" });
    expect(within(table).getByText("unconfirmed")).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Confirm cup" }));

    await waitFor(() => expect(within(table).getByText("confirmed")).toBeInTheDocument());
    expect(within(table).queryByRole("button", { name: "Confirm cup" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === `/measures/${cupMeasureId}/confirm`)).toBe(true);
  });

  it("adds a density and deactivates the ingredient", async () => {
    let ingredient = flour;
    const calls = mockApi({
      ...baseRoutes(() => ingredient),
      [`PATCH /ingredients/${flourId}`]: (call) => {
        const body = call.body as { density_g_per_ml: string; density_source: string };
        ingredient = { ...ingredient, density_g_per_ml: body.density_g_per_ml, density_source: "measured" };
        return jsonResponse(200, ingredient);
      },
      [`POST /ingredients/${flourId}/deactivate`]: () => {
        ingredient = { ...ingredient, active: false };
        return jsonResponse(200, ingredient);
      },
    });
    const user = userEvent.setup();
    renderApp(`/ingredients/${flourId}`);

    await user.click(await screen.findByRole("button", { name: "Add density" }));
    await user.type(screen.getByLabelText("Density (g/ml)"), "0.55");
    await user.click(screen.getByRole("button", { name: "Save density" }));
    expect(await screen.findByTestId("density-summary")).toHaveTextContent("0.55 g/ml · measured");
    expect(screen.getByRole("button", { name: "Confirm density" })).toBeInTheDocument();
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.body).toEqual({ density_g_per_ml: "0.55", density_source: "measured" });

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(await screen.findByRole("button", { name: "Activate" })).toBeInTheDocument();
  });
});
