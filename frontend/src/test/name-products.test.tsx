import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NamingRow, ToIdentifyGroup } from "../api/purchases";
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
    lines: [{ line_id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f85${n}0`, purchase_id: receiptPurchaseId, raw_text: norm, purchased_at: "2026-09-20T18:05:00Z", line_total: "4.00", qty: "1", unit: "each" }],
  };
}

const rows: NamingRow[] = [
  { vendor, raw_text_norm: "RVRBND BREAD FLR 2KG", line_count: 2, raw_text: "RVRBND BREAD FLR 2KG 6.50", name: "Rvrbnd bread flr", ingredient: { kind: "ingredient", id: flour.id, key: null, name: flour.name, canonical_unit: flour.canonical_unit, active: true, category: null, category_key: null, matched_spelling: null, exact: true }, pack_qty: "2", pack_unit: "kg" },
  { vendor, raw_text_norm: "BNLS CHKN BRST", line_count: 1, raw_text: "BNLS CHKN BRST 8.40", name: "Bnls chkn brst", ingredient: null, pack_qty: null, pack_unit: null },
  { vendor, raw_text_norm: "OAT MILK", line_count: 1, raw_text: "OAT MILK 3.49", name: "Oat milk", ingredient: null, pack_qty: null, pack_unit: null },
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
              ? { vendor_id: vendor.id, raw_text_norm: r.raw_text_norm, product_id: null, applied: 0, error: { code: "not_found", message: "No such ingredient." } }
              : { vendor_id: vendor.id, raw_text_norm: r.raw_text_norm, product_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e01", applied: 2, error: null },
          ),
        });
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    await user.click(await screen.findByRole("button", { name: "Name the new products" }));

    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", { name: `${vendor.name}: RVRBND BREAD FLR 2KG` });
    expect(within(flourRow).getByRole("textbox", { name: "Name" })).toHaveValue("Rvrbnd bread flr");
    expect(within(flourRow).getByRole("textbox", { name: "Pack" })).toHaveValue("2");
    expect(within(flourRow).getByTestId("name-0-ingredient-choice")).toHaveTextContent(flour.name);
    for (const box of within(list).getAllByRole("checkbox")) expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Create products" })).toBeDisabled();

    // Ticking one row, and editing another, includes exactly those two.
    await user.click(within(flourRow).getByRole("checkbox", { name: "Include RVRBND BREAD FLR 2KG" }));
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
      const found = calls.find((c) => c.method === "POST" && c.path === "/to-identify/name-products");
      expect(found).toBeDefined();
      return found!;
    });
    expect(post.body).toEqual({
      rows: [{ vendor_id: vendor.id, raw_text_norm: "RVRBND BREAD FLR 2KG", name: "Rvrbnd bread flr", pack_qty: "2", pack_unit: "kg", ingredient_id: flour.id }],
    });
    expect(await screen.findByText("Created 1 product and identified 2 lines.")).toBeInTheDocument();
  });

  it("keeps a failed row's input and shows why", async () => {
    mockApi(
      routes(three, () =>
        jsonResponse(200, {
          results: [{ vendor_id: vendor.id, raw_text_norm: "RVRBND BREAD FLR 2KG", product_id: null, applied: 0, error: { code: "unknown_unit", message: "pack_unit is not a known unit code." } }],
        }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify?mode=name");
    const list = await screen.findByRole("list", { name: "New products to name" });
    const flourRow = within(list).getByRole("group", { name: `${vendor.name}: RVRBND BREAD FLR 2KG` });
    const name = within(flourRow).getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Riverbend bread flour");
    await user.click(screen.getByRole("button", { name: "Create 1 product" }));
    expect(await within(flourRow).findByRole("alert")).toHaveTextContent("pack_unit is not a known unit code.");
    expect(screen.getByText("1 row wasn't created. The reason is on each row.")).toBeInTheDocument();
    expect(name).toHaveValue("Riverbend bread flour");
    // Back to one at a time.
    await user.click(screen.getByRole("button", { name: "One at a time" }));
    expect(await screen.findByRole("list", { name: "Lines to identify" })).toBeInTheDocument();
  });
});
