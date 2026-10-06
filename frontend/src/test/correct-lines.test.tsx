import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { aliasLine, discountLine, outlierLine, receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    [`GET ${base}`]: () => jsonResponse(200, receiptPurchase),
    [`PUT ${base}/lines`]: () => jsonResponse(200, receiptPurchase),
  };
}

async function openTable() {
  const user = userEvent.setup();
  renderApp(base);
  await screen.findByTestId("review");
  await user.click(screen.getByRole("button", { name: "Correct the lines" }));
  return { user, table: screen.getByRole("group", { name: "Correct the lines" }) };
}

describe("correct the lines (issue 182)", () => {
  it("retypes in the table, adds a row with Enter, and saves every line in one request", async () => {
    const calls = mockApi(routes());
    const { user, table } = await openTable();
    expect(within(table).getByText(/Lines add up to \$11\.50; the receipt says \$12\.00\./)).toBeInTheDocument();
    // Commit waits until the corrections are saved or dropped.
    expect(screen.getByRole("button", { name: "Commit purchase" })).toBeDisabled();

    const total = within(table).getByLabelText("Line total, row 2");
    await user.clear(total);
    await user.type(total, "7.00");
    expect(within(table).getByText("Adds up.")).toBeInTheDocument();

    // Enter adds a row below and puts the cursor in its receipt text.
    await user.type(total, "{Enter}");
    expect(within(table).getByLabelText("Receipt text, row 3")).toHaveFocus();
    await user.keyboard("JAR DEPOSIT");
    await user.type(within(table).getByLabelText("Line total, row 3"), "0.25");
    expect(within(table).getByText("Off by $0.25.")).toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: "Save lines" }));
    const put = await waitFor(() => {
      const found = calls.find((c) => c.method === "PUT" && c.path === `${base}/lines`);
      expect(found).toBeDefined();
      return found;
    });
    expect(put?.body).toEqual({
      lines: [
        { id: aliasLine.id, raw_text: "MLSTN AP FLOUR 5LB", line_kind: "item", qty: "1", unit: "each", line_total: "4.99" },
        { id: unmatchedLine.id, raw_text: "RVRBND BREAD FLR 2KG", line_kind: "item", qty: "1", unit: "each", line_total: "7.00" },
        { raw_text: "JAR DEPOSIT", line_kind: "item", line_total: "0.25" },
        // A discount with no item of its own attaches to the item above it.
        { id: discountLine.id, raw_text: "COUPON", line_kind: "discount", line_total: "-1.00", attach_to: 2 },
        { id: outlierLine.id, raw_text: "CSTLN WHOLE MILK GAL", line_kind: "item", qty: "1", unit: "each", line_total: "1.01" },
      ],
    });
    expect(await screen.findByText("Lines saved: 5 lines.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Correct the lines" })).not.toBeInTheDocument();
  });

  it("asks before discarding typed corrections, and drops a row with Delete", async () => {
    const calls = mockApi(routes());
    const { user, table } = await openTable();
    await user.click(within(table).getByRole("button", { name: "Delete row 3" }));
    expect(within(table).queryByDisplayValue("COUPON")).not.toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: "Cancel" }));
    expect(within(table).getByRole("alert")).toHaveTextContent("Discard your corrections?");
    expect(within(table).getByRole("button", { name: "Keep editing" })).toHaveFocus();
    await user.click(within(table).getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("group", { name: "Correct the lines" })).toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: "Cancel" }));
    await user.click(within(table).getByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("group", { name: "Correct the lines" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("names the row that cannot be saved and sends nothing", async () => {
    const calls = mockApi(routes());
    const { user, table } = await openTable();
    await user.clear(within(table).getByLabelText("Line total, row 1"));
    await user.click(within(table).getByRole("button", { name: "Save lines" }));
    expect(within(table).getByText("Row 1: a line total is required.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});
