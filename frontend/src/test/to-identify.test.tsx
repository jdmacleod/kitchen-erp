import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ToIdentifyGroup } from "../api/purchases";
import { flour, flourProduct, hits, units } from "./catalog-fixtures";
import { chainLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { receiptPurchaseId } from "./purchase-fixtures";

const group: ToIdentifyGroup = {
  vendor: { id: chainLocation.vendor.id, name: chainLocation.vendor.name },
  raw_text_norm: "RVRBND BREAD FLR",
  line_count: 2,
  lines: [
    { line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8202", purchase_id: receiptPurchaseId, raw_text: "RVRBND BREAD FLR 2KG", purchased_at: "2026-09-20T18:05:00Z", line_total: "6.50", qty: "1", unit: "each" },
    { line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8302", purchase_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8004", raw_text: "RVRBND BREAD FLR 2KG", purchased_at: "2026-09-06T17:00:00Z", line_total: "6.25", qty: "1", unit: "each" },
  ],
};

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? hits : [] }),
    "GET /to-identify": () => jsonResponse(200, { items: [group] }),
    "POST /to-identify/apply": (call: RecordedCall) => jsonResponse(200, { applied: (call.body as { line_ids?: string[] }).line_ids?.length ?? 2 }),
  };
}

describe("to-identify queue", () => {
  it("applies a chosen product to every line in the group by default", async () => {
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");

    const card = await screen.findByRole("group", { name: "Millstone Market: RVRBND BREAD FLR" });
    expect(card).toHaveTextContent("2 lines");
    expect(within(card).getByRole("checkbox", { name: "Apply to all 2" })).toBeChecked();
    expect(within(card).getAllByRole("link")[0]).toHaveAttribute("href", `/shop/purchases/${receiptPurchaseId}`);

    await user.type(within(card).getByRole("combobox", { name: "Product" }), "flour");
    await user.click(await screen.findByRole("option", { name: /Bread Flour/ }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/to-identify/apply");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ vendor_id: chainLocation.vendor.id, raw_text_norm: "RVRBND BREAD FLR", product_id: hits[1].id });
    expect(await screen.findByText("Applied to 2 lines.")).toBeInTheDocument();
  });

  it("ignores only the chosen line when apply-to-all is off", async () => {
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");

    const card = await screen.findByRole("group", { name: "Millstone Market: RVRBND BREAD FLR" });
    await user.click(within(card).getByRole("checkbox", { name: "Apply to all 2" }));
    const only = within(card).getByLabelText("Only this line");
    await user.selectOptions(only, group.lines[1].line_id);
    await user.click(within(card).getByRole("button", { name: "Ignore" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/to-identify/apply");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ vendor_id: chainLocation.vendor.id, raw_text_norm: "RVRBND BREAD FLR", ignore: true, line_ids: [group.lines[1].line_id] });
    expect(await screen.findByText("Applied to 1 line.")).toBeInTheDocument();
  });

  it("starts a new product from the line's wording and offers the ingredient it names", async () => {
    // #88: New product started with an empty name, and every ingredient was typed.
    const second: ToIdentifyGroup = { ...group, raw_text_norm: "OAT MILK", line_count: 1, lines: [{ ...group.lines[0], line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8402", raw_text: "OAT MILK 3.49" }] };
    const queue = [group, second];
    const calls = mockApi({
      ...baseRoutes(),
      "GET /to-identify": () => jsonResponse(200, { items: queue }),
      "GET /ingredients/in-text": (call: RecordedCall) =>
        jsonResponse(200, {
          items: call.query.get("text") === "RVRBND BREAD FLR" ? [{ kind: "ingredient", id: flour.id, name: flour.name, canonical_unit: flour.canonical_unit, active: true, category: null, category_key: null, matched_spelling: null, exact: true }] : [],
        }),
      "POST /products": () => jsonResponse(201, flourProduct),
      "POST /to-identify/apply": () => {
        queue.shift();
        return jsonResponse(200, { applied: 2 });
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");

    const card = await screen.findByRole("group", { name: "Millstone Market: RVRBND BREAD FLR" });
    await user.click(within(card).getByRole("button", { name: "New product" }));
    const form = within(card).getByRole("group", { name: "New product" });
    expect(within(form).getByRole("textbox", { name: /^Name/ })).toHaveValue("Rvrbnd bread flr");
    // Offered, not chosen: nothing is picked until the button is pressed.
    const offered = await within(form).findByRole("group", { name: "Named on the receipt line" });
    await user.click(within(offered).getByRole("button", { name: flour.name }));
    expect(within(form).getByTestId("identify-0-new-ingredient-choice")).toHaveTextContent(flour.name);
    await user.click(within(form).getByRole("button", { name: "Create product" }));

    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/products")?.body).toMatchObject({ name: "Rvrbnd bread flr", ingredient_id: flour.id }));
    // The group is answered and the next one's product box has focus.
    await waitFor(() => expect(screen.queryByRole("group", { name: "Millstone Market: RVRBND BREAD FLR" })).not.toBeInTheDocument());
    const next = screen.getByRole("group", { name: "Millstone Market: OAT MILK" });
    await waitFor(() => expect(within(next).getByRole("combobox", { name: "Product" })).toHaveFocus());
  });

  it("says when there is nothing to identify", async () => {
    mockApi({ ...baseRoutes(), "GET /to-identify": () => jsonResponse(200, { items: [] }) });
    renderApp("/shop/receipts/identify");
    expect(await screen.findByText("All lines identified")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Home" })).toHaveAttribute("href", "/");
  });
});
