import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fractionToPercent, isYieldPercent, percentToFraction, type Ingredient } from "../api/catalog";
import { flour, flourId, flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// The yield field on the ingredient form (07, 3D): shown and typed as a percentage,
// stored as the fraction recipe costing divides by. Text arithmetic throughout.

function routes(ingredient: () => Ingredient, onPatch: (body: unknown) => Ingredient) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /ingredients/${flourId}`]: () => jsonResponse(200, ingredient()),
    [`GET /ingredients/${flourId}/offers`]: () => jsonResponse(200, { items: [], stale_after_days: 90 }),
    "GET /products": () => jsonResponse(200, { items: [flourProduct], next_cursor: null }),
    [`PATCH /ingredients/${flourId}`]: (call: { body: unknown }) => jsonResponse(200, onPatch(call.body)),
  };
}

describe("yield percentage helpers", () => {
  it("restate a stored fraction as a percentage and back without floats", () => {
    expect(fractionToPercent("0.85")).toBe("85");
    expect(fractionToPercent("0.7200")).toBe("72");
    expect(fractionToPercent("1")).toBe("100");
    expect(fractionToPercent("1.0000")).toBe("100");
    expect(fractionToPercent("0.6667")).toBe("66.67");
    expect(fractionToPercent("0.05")).toBe("5");
    expect(percentToFraction("85")).toBe("0.85");
    expect(percentToFraction("87.5")).toBe("0.875");
    expect(percentToFraction("100")).toBe("1");
    expect(percentToFraction("5")).toBe("0.05");
    expect(percentToFraction("0.5")).toBe("0.005");
    expect(percentToFraction(" 66.67 ")).toBe("0.6667");
  });

  it("accept 0 < percent <= 100 only", () => {
    expect(isYieldPercent("85")).toBe(true);
    expect(isYieldPercent("100")).toBe(true);
    expect(isYieldPercent("100.0")).toBe(true);
    expect(isYieldPercent("0.5")).toBe(true);
    expect(isYieldPercent("0")).toBe(false);
    expect(isYieldPercent("100.5")).toBe(false);
    expect(isYieldPercent("120")).toBe(false);
    expect(isYieldPercent("")).toBe(false);
    expect(isYieldPercent("abc")).toBe(false);
  });
});

describe("yield on the ingredient page", () => {
  it("shows the stored fraction as a percentage and sends an edit back as a fraction", async () => {
    let ingredient: Ingredient = { ...flour, yield_pct: "0.7200" };
    const calls = mockApi(
      routes(
        () => ingredient,
        (body) => {
          ingredient = { ...ingredient, ...(body as Partial<Ingredient>) };
          return ingredient;
        },
      ),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    expect(await screen.findByText("yield 72%")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit details" })[0]);
    const field = screen.getByLabelText("Yield (%)");
    expect(field).toHaveValue("72");
    await user.clear(field);
    await user.type(field, "87.5");
    await user.click(screen.getByRole("button", { name: "Save details" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ yield_pct: "0.875" });
    expect(await screen.findByText("yield 87.5%")).toBeInTheDocument();
  });

  it("refuses a percentage over 100 and sends nothing", async () => {
    const calls = mockApi(routes(() => flour, () => flour));
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await screen.findByRole("heading", { name: flour.name });
    await user.click(screen.getAllByRole("button", { name: "Edit details" })[0]);
    const field = screen.getByLabelText("Yield (%)");
    expect(field).toHaveValue("100");
    await user.clear(field);
    await user.type(field, "120");
    await user.click(screen.getByRole("button", { name: "Save details" }));

    expect(await screen.findByText("Yield is a percentage above 0 and up to 100, such as 85.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("does not resend an unchanged yield", async () => {
    const calls = mockApi(routes(() => flour, () => flour));
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${flourId}`);

    await screen.findByRole("heading", { name: flour.name });
    await user.click(screen.getAllByRole("button", { name: "Edit details" })[0]);
    await user.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save details" })).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });
});

describe("yield in the add drawer", () => {
  it("sends a typed percentage as a fraction", async () => {
    const created: Ingredient = { ...flour, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b10", name: "leeks", category: null, measures: [], yield_pct: "0.6" };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /usda/suggestions": () => jsonResponse(200, { items: [], loaded: false }),
      "POST /ingredients": () => jsonResponse(201, created),
    });
    const user = userEvent.setup();
    renderApp("/catalog/ingredients");

    await screen.findByRole("heading", { name: "Ingredients" });
    await user.click(screen.getAllByRole("button", { name: "Add ingredient" })[0]);
    await screen.findByRole("dialog", { name: "Add ingredient" });
    await user.type(screen.getByLabelText("Name"), "leeks");
    await user.click(screen.getByText("More: density, yield, perishability, notes"));
    await user.type(screen.getByLabelText("Yield (%)"), "60");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add ingredient" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/ingredients")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "leeks", canonical_unit: "g", yield_pct: "0.6" });
  });
});
