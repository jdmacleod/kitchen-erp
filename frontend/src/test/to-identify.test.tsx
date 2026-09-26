import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ToIdentifyGroup } from "../api/purchases";
import { hits, units } from "./catalog-fixtures";
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

  it("says when there is nothing to identify", async () => {
    mockApi({ ...baseRoutes(), "GET /to-identify": () => jsonResponse(200, { items: [] }) });
    renderApp("/shop/receipts/identify");
    expect(await screen.findByText("All lines identified")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Home" })).toHaveAttribute("href", "/");
  });
});
