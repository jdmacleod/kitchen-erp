import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NamingRow } from "../api/naming";
import type { ToIdentifyGroup } from "../api/purchases";
import { flour, units } from "./catalog-fixtures";
import { chainLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { receiptPurchaseId } from "./purchase-fixtures";

const vendor = { id: chainLocation.vendor.id, name: chainLocation.vendor.name };

function group(norm: string, n: number): ToIdentifyGroup {
  return {
    vendor,
    raw_text_norm: norm,
    line_count: 1,
    lines: [
      {
        line_id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f85${n}0`,
        purchase_id: receiptPurchaseId,
        raw_text: norm,
        purchased_at: "2026-09-20T18:05:00Z",
        line_total: "4.00",
        qty: "1",
        unit: "each",
      },
    ],
  };
}

const rows: NamingRow[] = [
  {
    vendor,
    raw_text_norm: "RVRBND BREAD FLR 2KG",
    line_count: 2,
    raw_text: "RVRBND BREAD FLR 2KG 6.50",
    name: "Rvrbnd bread flr",
    name_words: ["rvrbnd", "bread", "flr"],
    name_changes: [],
    ingredient: {
      kind: "ingredient",
      id: flour.id,
      key: null,
      name: flour.name,
      canonical_unit: flour.canonical_unit,
      active: true,
      category: null,
      category_key: null,
      matched_spelling: null,
      exact: true,
    },
    pack_qty: "2",
    pack_unit: "kg",
    model: null,
    candidates: [],
  },
  {
    vendor,
    raw_text_norm: "BNLS CHKN BRST",
    line_count: 1,
    raw_text: "BNLS CHKN BRST 8.40",
    name: "Bnls chkn brst",
    name_words: ["bnls", "chkn", "brst"],
    name_changes: [],
    ingredient: null,
    pack_qty: null,
    pack_unit: null,
    model: null,
    candidates: [],
  },
  {
    vendor,
    raw_text_norm: "OAT MILK",
    line_count: 1,
    raw_text: "OAT MILK 3.49",
    name: "Oat milk",
    name_words: ["oat", "milk"],
    name_changes: [],
    ingredient: null,
    pack_qty: null,
    pack_unit: null,
    model: null,
    candidates: [],
  },
];

function routes(queue: ToIdentifyGroup[], post?: (call: RecordedCall) => Response) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /ingredients/in-text": () => jsonResponse(200, { items: [] }),
    "GET /to-identify": () => jsonResponse(200, { items: queue }),
    "GET /to-identify/naming": () => jsonResponse(200, { items: rows }),
    "POST /to-identify/name-products": post ?? (() => jsonResponse(200, { results: [] })),
  };
}

const three = [group("RVRBND BREAD FLR 2KG", 1), group("BNLS CHKN BRST", 2), group("OAT MILK", 3)];

describe("naming new products in bulk", () => {
  it("is offered only once three groups wait", async () => {
    mockApi(routes(three.slice(0, 2)));
    renderApp("/shop/receipts/identify");
    await screen.findByRole("group", { name: `${vendor.name}: BNLS CHKN BRST` });
    expect(screen.queryByRole("button", { name: "Name the new products" })).not.toBeInTheDocument();
  });

  it("starts each row from the wording, unticked, and creates only the rows a person ticked", async () => {
    const calls = mockApi(
      routes(three, (call) => {
        const sent = (call.body as { rows: { raw_text_norm: string }[] }).rows;
        return jsonResponse(200, {
          results: sent.map((r) =>
            r.raw_text_norm === "OAT MILK"
              ? {
                  vendor_id: vendor.id,
                  raw_text_norm: r.raw_text_norm,
                  product_id: null,
                  applied: 0,
                  error: { code: "not_found", message: "No such ingredient." },
                }
              : {
                  vendor_id: vendor.id,
                  raw_text_norm: r.raw_text_norm,
                  product_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e01",
                  applied: 2,
                  error: null,
                },
          ),
        });
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    await user.click(await screen.findByRole("button", { name: "Name the new products" }));

    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", {
      name: `${vendor.name}: RVRBND BREAD FLR 2KG`,
    });
    expect(within(flourRow).getByRole("textbox", { name: "Name" })).toHaveValue("Rvrbnd bread flr");
    expect(within(flourRow).getByRole("textbox", { name: "Pack" })).toHaveValue("2");
    expect(within(flourRow).getByTestId("name-0-ingredient-choice")).toHaveTextContent(flour.name);
    for (const box of within(list).getAllByRole("checkbox")) expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Create products" })).toBeDisabled();

    // Ticking one row, and editing another, includes exactly those two.
    await user.click(
      within(flourRow).getByRole("checkbox", { name: "Include RVRBND BREAD FLR 2KG" }),
    );
    const chickenRow = within(list).getByRole("group", { name: `${vendor.name}: BNLS CHKN BRST` });
    const chickenName = within(chickenRow).getByRole("textbox", { name: "Name" });
    await user.clear(chickenName);
    await user.type(chickenName, "Chicken breast");
    expect(within(chickenRow).getByRole("checkbox")).toBeChecked();

    // A ticked row with no ingredient says so and sends nothing.
    await user.click(screen.getByRole("button", { name: "Create 2 products" }));
    expect(within(chickenRow).getByRole("alert")).toHaveTextContent("Choose an ingredient");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    // Untick it; only the flour row goes.
    await user.click(within(chickenRow).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    const post = await waitFor(() => {
      const found = calls.find(
        (c) => c.method === "POST" && c.path === "/to-identify/name-products",
      );
      expect(found).toBeDefined();
      return found!;
    });
    expect(post.body).toEqual({
      rows: [
        {
          vendor_id: vendor.id,
          raw_text_norm: "RVRBND BREAD FLR 2KG",
          name: "Rvrbnd bread flr",
          pack_qty: "2",
          pack_unit: "kg",
          ingredient_id: flour.id,
        },
      ],
    });
    expect(
      await screen.findByText("Created 1 product and identified 2 lines."),
    ).toBeInTheDocument();
  });

  it("fills the rows the wording couldn't name from the model, never over what was typed", async () => {
    let answered = false;
    const chicken = {
      kind: "standard" as const,
      id: null,
      key: "chicken-breast",
      name: "chicken breast",
      canonical_unit: "g" as const,
      active: true,
      category: "meat",
      category_key: null,
      matched_spelling: null,
      exact: true,
    };
    const withModel = (): NamingRow[] =>
      answered
        ? [
            rows[0],
            {
              ...rows[1],
              model: { status: "done", name: "Boneless chicken breast", ingredient: chicken },
            },
            { ...rows[2], model: { status: "done", name: "Oat beverage", ingredient: null } },
          ]
        : rows;
    const calls = mockApi({
      ...routes(three),
      "GET /to-identify/naming": () => jsonResponse(200, { items: withModel() }),
      "POST /to-identify/naming/suggest": () => {
        answered = true;
        return jsonResponse(200, { queued: 2 });
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const list = await screen.findByRole("list", { name: "New products to name" });
    expect(
      screen.getByText("The wording didn't name an ingredient for 2 lines."),
    ).toBeInTheDocument();

    // A name typed before the model answers stays.
    const milkRow = within(list).getByRole("group", { name: `${vendor.name}: OAT MILK` });
    const milkName = within(milkRow).getByRole("textbox", { name: "Name" });
    await user.clear(milkName);
    await user.type(milkName, "Oat milk, barista");

    await user.click(screen.getByRole("button", { name: "Suggest names with the model" }));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "POST" && c.path === "/to-identify/naming/suggest"),
      ).toBe(true),
    );
    const chickenRow = within(list).getByRole("group", { name: `${vendor.name}: BNLS CHKN BRST` });
    await waitFor(() =>
      expect(within(chickenRow).getByRole("textbox", { name: "Name" })).toHaveValue(
        "Boneless chicken breast",
      ),
    );
    expect(within(chickenRow).getByTestId("name-1-ingredient-choice")).toHaveTextContent(
      "chicken breast",
    );
    expect(within(chickenRow).getAllByText("Suggested by the model")).toHaveLength(2);
    // A suggestion is not a decision: the row stays unticked.
    expect(within(chickenRow).getByRole("checkbox")).not.toBeChecked();
    expect(milkName).toHaveValue("Oat milk, barista");
    expect(within(milkRow).queryByText("Suggested by the model")).not.toBeInTheDocument();
  });

  it("keeps a failed row's input and shows why", async () => {
    mockApi(
      routes(three, () =>
        jsonResponse(200, {
          results: [
            {
              vendor_id: vendor.id,
              raw_text_norm: "RVRBND BREAD FLR 2KG",
              product_id: null,
              applied: 0,
              error: { code: "unknown_unit", message: "pack_unit is not a known unit code." },
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", {
      name: `${vendor.name}: RVRBND BREAD FLR 2KG`,
    });
    const name = within(flourRow).getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Riverbend bread flour");
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    expect(await within(flourRow).findByRole("alert")).toHaveTextContent(
      "pack_unit is not a known unit code.",
    );
    expect(
      screen.getByText("1 row wasn't created. The reason is on each row."),
    ).toBeInTheDocument();
    expect(name).toHaveValue("Riverbend bread flour");
    // Back to one at a time.
    await user.click(screen.getByRole("button", { name: "One at a time" }));
    expect(await screen.findByRole("list", { name: "Lines to identify" })).toBeInTheDocument();
  });

  it("offers the existing product with the same name and ingredient instead of a second one (issue 179)", async () => {
    const existing = {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e07",
      name: "Riverbend bread flour",
      brand: null,
    };
    const calls = mockApi(
      routes(three, (call) => {
        const [sent] = (
          call.body as {
            rows: { raw_text_norm: string; product_id?: string; allow_duplicate?: boolean }[];
          }
        ).rows;
        if (sent.product_id || sent.allow_duplicate) {
          return jsonResponse(200, {
            results: [
              {
                vendor_id: vendor.id,
                raw_text_norm: sent.raw_text_norm,
                product_id: sent.product_id ?? "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e08",
                applied: 2,
                error: null,
              },
            ],
          });
        }
        return jsonResponse(200, {
          results: [
            {
              vendor_id: vendor.id,
              raw_text_norm: sent.raw_text_norm,
              product_id: null,
              applied: 0,
              error: {
                code: "product_exists",
                message: "Riverbend bread flour already exists with this ingredient.",
                product: existing,
              },
            },
          ],
        });
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", {
      name: `${vendor.name}: RVRBND BREAD FLR 2KG`,
    });
    await user.click(
      within(flourRow).getByRole("checkbox", { name: "Include RVRBND BREAD FLR 2KG" }),
    );
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));

    expect(await within(flourRow).findByRole("alert")).toHaveTextContent(
      "Riverbend bread flour already exists with this ingredient.",
    );
    await user.click(within(flourRow).getByRole("button", { name: "Use Riverbend bread flour" }));
    expect(within(flourRow).getByTestId("name-0-use")).toHaveTextContent(
      "Uses the existing product Riverbend bread flour.",
    );
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    const second = calls.filter((c) => c.method === "POST")[1].body as {
      rows: Record<string, unknown>[];
    };
    expect(second.rows[0]).toEqual({
      vendor_id: vendor.id,
      raw_text_norm: "RVRBND BREAD FLR 2KG",
      name: "Riverbend bread flour",
      product_id: existing.id,
    });
    expect(
      await screen.findByText("Used 1 existing product and identified 2 lines."),
    ).toBeInTheDocument();
  });

  it("can still create another product of the same name", async () => {
    const existing = {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e07",
      name: "Riverbend bread flour",
      brand: null,
    };
    const calls = mockApi(
      routes(three, (call) => {
        const [sent] = (
          call.body as { rows: { raw_text_norm: string; allow_duplicate?: boolean }[] }
        ).rows;
        return jsonResponse(200, {
          results: [
            sent.allow_duplicate
              ? {
                  vendor_id: vendor.id,
                  raw_text_norm: sent.raw_text_norm,
                  product_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e08",
                  applied: 2,
                  error: null,
                }
              : {
                  vendor_id: vendor.id,
                  raw_text_norm: sent.raw_text_norm,
                  product_id: null,
                  applied: 0,
                  error: {
                    code: "product_exists",
                    message: "Riverbend bread flour already exists with this ingredient.",
                    product: existing,
                  },
                },
          ],
        });
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", {
      name: `${vendor.name}: RVRBND BREAD FLR 2KG`,
    });
    await user.click(
      within(flourRow).getByRole("checkbox", { name: "Include RVRBND BREAD FLR 2KG" }),
    );
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    await user.click(await within(flourRow).findByRole("button", { name: "Create another" }));
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    const second = calls.filter((c) => c.method === "POST")[1].body as {
      rows: Record<string, unknown>[];
    };
    expect(second.rows[0].allow_duplicate).toBe(true);
    expect(
      await screen.findByText("Created 1 product and identified 2 lines."),
    ).toBeInTheDocument();
  });
});

describe("words the suggested name changed (issue 384)", () => {
  const yogurt: NamingRow = {
    ...rows[0],
    raw_text_norm: "HRBR YGRT PLN",
    line_count: 1,
    raw_text: "HRBR YGRT PLN 3.10",
    name: "Hrbr yogurt plain",
    name_words: ["hrbr", "ygrt", "pln"],
    name_changes: [
      { index: 1, printed: "ygrt", shown: "yogurt", source: "learned" },
      { index: 2, printed: "pln", shown: "plain", source: "vocabulary" },
    ],
    pack_qty: null,
    pack_unit: null,
  };
  const queue = [group("HRBR YGRT PLN", 4), group("BNLS CHKN BRST", 2), group("OAT MILK", 3)];

  it("shows each change as a toggle, and undoing one puts the printed word back", async () => {
    const calls = mockApi({
      ...routes(queue),
      "GET /to-identify/naming": () => jsonResponse(200, { items: [yogurt, rows[1], rows[2]] }),
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const row = await screen.findByRole("group", { name: `${vendor.name}: HRBR YGRT PLN` });
    const name = within(row).getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Hrbr yogurt plain");
    const changed = within(row).getByRole("list", { name: "Words changed from the receipt" });
    const learned = within(changed).getByRole("button", { name: /ygrt.*yogurt.*learned/ });
    expect(learned).toHaveAttribute("aria-pressed", "true");

    await user.click(learned);
    expect(name).toHaveValue("Hrbr ygrt plain");
    expect(learned).toHaveAttribute("aria-pressed", "false");
    expect(within(row).getByRole("checkbox")).toBeChecked();
    await user.click(within(changed).getByRole("button", { name: /pln.*plain/ }));
    expect(name).toHaveValue("Hrbr ygrt pln");

    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    // Only the learned word is forgotten: a vocabulary guess has no count to lower.
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      rows: [
        {
          vendor_id: vendor.id,
          raw_text_norm: "HRBR YGRT PLN",
          name: "Hrbr ygrt pln",
          ingredient_id: flour.id,
          forget_words: [{ word: "ygrt", expansion: "yogurt" }],
        },
      ],
    });
  });

  it("hides the toggles once the name is typed over", async () => {
    mockApi({
      ...routes(queue),
      "GET /to-identify/naming": () => jsonResponse(200, { items: [yogurt, rows[1], rows[2]] }),
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const row = await screen.findByRole("group", { name: `${vendor.name}: HRBR YGRT PLN` });
    const name = within(row).getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Greek yogurt");
    expect(
      within(row).queryByRole("list", { name: "Words changed from the receipt" }),
    ).not.toBeInTheDocument();
  });
});

describe("products a row may already be (issue 386)", () => {
  const known = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e09", name: "Riverbend bread flour" };
  const withCandidates = (): NamingRow[] => [
    {
      ...rows[0],
      candidates: [{ ...known, brand: "Riverbend", pack_qty: "2.000", pack_unit: "kg" }],
    },
    rows[1],
    rows[2],
  ];

  it("offers them before Create, and using one sends its id", async () => {
    const calls = mockApi({
      ...routes(three),
      "GET /to-identify/naming": () => jsonResponse(200, { items: withCandidates() }),
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const row = await screen.findByRole("group", { name: `${vendor.name}: RVRBND BREAD FLR 2KG` });
    await user.click(within(row).getByRole("button", { name: "Use Riverbend bread flour · 2 kg" }));
    expect(within(row).getByTestId("name-0-use")).toHaveTextContent(
      "Uses the existing product Riverbend bread flour",
    );
    expect(within(row).queryByText("Maybe one you have:")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      rows: [
        {
          vendor_id: vendor.id,
          raw_text_norm: "RVRBND BREAD FLR 2KG",
          name: known.name,
          product_id: known.id,
        },
      ],
    });
  });
});
