import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { UnconfirmedValue } from "../api/unconfirmedBridges";
import { flour } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented values for the unconfirmed list on Needs a bridge (issue 356).
const lemonsId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a01";
const honeyId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a02";
const measureId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8b01";
const productId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8c01";

const items: UnconfirmedValue[] = [
  {
    kind: "measure",
    id: measureId,
    ingredient_id: lemonsId,
    ingredient_name: "lemons",
    canonical_unit: "g",
    product_name: null,
    label: "tbsp juice",
    value: "0.33",
    source: "usda",
    g_per_ml: "0.0223",
    problem: "One tbsp juice at 0.33 g would be 0.022 g/ml, lighter than any food.",
  },
  {
    kind: "density",
    id: honeyId,
    ingredient_id: honeyId,
    ingredient_name: "honey",
    canonical_unit: "g",
    product_name: null,
    label: null,
    value: "1.42000",
    source: "usda",
    g_per_ml: "1.42",
    problem: null,
  },
  {
    kind: "product_density",
    id: productId,
    ingredient_id: honeyId,
    ingredient_name: "honey",
    canonical_unit: "g",
    product_name: "Tarn clover honey",
    label: null,
    value: "1.38",
    source: "label",
    g_per_ml: "1.38",
    problem: null,
  },
];

function routes(
  state: { items: UnconfirmedValue[] },
  extra: Record<string, (call: RecordedCall) => Response> = {},
) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /price-book/needs-bridge": () => jsonResponse(200, { items: [] }),
    "GET /usda/review": () =>
      jsonResponse(200, { loaded: true, release_date: "2026-04-30", groups: [] }),
    "GET /bridges/unconfirmed": () => jsonResponse(200, { items: state.items }),
    ...extra,
  };
}

describe("Unconfirmed values on Needs a bridge (issue 356)", () => {
  it("lists every unconfirmed value, the implausible one first with its reason", async () => {
    mockApi(routes({ items }));
    renderApp("/catalog/bridges");
    const rows = await screen.findAllByTestId("unconfirmed-value");
    expect(screen.getByRole("heading", { name: "Unconfirmed values" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /3 values were saved without a check\..* 1 looks wrong and is listed first\./,
      ),
    ).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent(
      "lemons · Measure: 1 tbsp juice = 0.33 g (0.0223 g/ml) · from USDA",
    );
    expect(rows[0]).toHaveTextContent("lighter than any food");
    expect(rows[1]).toHaveTextContent("honey · Density: 1.42 g/ml · from USDA");
    expect(rows[2]).toHaveTextContent(
      "honey · Density of Tarn clover honey: 1.38 g/ml · from a label",
    );
  });

  it("shows nothing when every value is confirmed", async () => {
    mockApi(routes({ items: [] }));
    renderApp("/catalog/bridges");
    expect(await screen.findByText("Every price is normalized")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Unconfirmed values" })).not.toBeInTheDocument();
  });

  it("confirms a value, and it leaves the list without a reload", async () => {
    const state = { items };
    const posts: RecordedCall[] = [];
    mockApi(
      routes(state, {
        [`POST /ingredients/${honeyId}/density/confirm`]: (call) => {
          posts.push(call);
          state.items = items.filter((v) => v.kind !== "density");
          return jsonResponse(200, { ...flour, id: honeyId, name: "honey" });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    await user.click(
      await screen.findByRole("button", { name: "Confirm density 1.42 g/ml for honey" }),
    );
    await waitFor(() => expect(screen.getAllByTestId("unconfirmed-value")).toHaveLength(2));
    expect(posts).toHaveLength(1);
    expect(await screen.findByTestId("notice")).toHaveTextContent(
      "Confirmed the density for honey.",
    );
  });

  it("saves an edit from a label and confirms it in one step", async () => {
    const calls: RecordedCall[] = [];
    const ok = (call: RecordedCall) => (calls.push(call), jsonResponse(200, {}));
    mockApi(
      routes(
        { items },
        { [`PATCH /measures/${measureId}`]: ok, [`POST /measures/${measureId}/confirm`]: ok },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    await user.click(
      await screen.findByRole("button", { name: "Edit measure 1 tbsp juice = 0.33 g for lemons" }),
    );
    const field = screen.getByLabelText("g in 1 tbsp juice");
    await user.clear(field);
    await user.type(field, "15");
    await user.click(screen.getByRole("button", { name: "Save and confirm" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      `PATCH /measures/${measureId}`,
      `POST /measures/${measureId}/confirm`,
    ]);
    expect(calls[0].body).toEqual({ canonical_qty: "15", source: "label" });
  });

  it("deletes each kind through its own route", async () => {
    const calls: RecordedCall[] = [];
    const ok = (call: RecordedCall) => (calls.push(call), jsonResponse(200, {}));
    mockApi(
      routes(
        { items },
        {
          [`DELETE /measures/${measureId}`]: (call) => (
            calls.push(call),
            new Response(null, { status: 204 })
          ),
          [`PATCH /ingredients/${honeyId}`]: ok,
          [`PATCH /products/${productId}`]: ok,
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/bridges");
    const rows = await screen.findAllByTestId("unconfirmed-value");
    for (const row of rows) {
      await user.click(within(row).getByRole("button", { name: /^Delete / }));
    }
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls.map((c) => [`${c.method} ${c.path}`, c.body])).toEqual([
      [`DELETE /measures/${measureId}`, undefined],
      [`PATCH /ingredients/${honeyId}`, { clear_density: true }],
      [`PATCH /products/${productId}`, { clear_density_override: true }],
    ]);
  });
});
