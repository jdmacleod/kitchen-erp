// Rows the scan prints like purchases that no line accounts for are named in
// review, so a person can add the missing line without hunting the image.
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { rowAmount } from "../components/purchases/UnreadRows";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function render(purchase: Purchase) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, purchase),
    [`POST ${base}/lines`]: () => jsonResponse(201, purchase),
  });
  renderApp(base);
  return calls;
}

const flagged: Purchase = {
  ...receiptPurchase,
  status: "draft",
  flags: [...receiptPurchase.flags, "rows_not_read"],
};

describe("rows not read", () => {
  it("names each row the reading kept", async () => {
    render({
      ...flagged,
      printed_checks: { unread_rows: ["FERNLEAF BASIL 0.89 F", "MOSSGLEN OATS 3.10 F"] },
    });
    await screen.findByTestId("review");
    expect(
      screen.getByText(/These rows on the receipt print an amount but were not read as lines/),
    ).toBeInTheDocument();
    expect(screen.getByText("FERNLEAF BASIL 0.89 F")).toBeInTheDocument();
    expect(screen.getByText("MOSSGLEN OATS 3.10 F")).toBeInTheDocument();
  });

  it("says one row in the singular", async () => {
    render({ ...flagged, printed_checks: { unread_rows: ["FERNLEAF BASIL 0.89 F"] } });
    await screen.findByTestId("review");
    expect(
      screen.getByText(/This row on the receipt prints an amount but was not read as a line/),
    ).toBeInTheDocument();
  });

  it("keeps the general sentence for a reading that kept no rows", async () => {
    render({ ...flagged, printed_checks: null });
    await screen.findByTestId("review");
    expect(
      screen.getByText(/Some rows on the receipt print an amount but were not read as lines/),
    ).toBeInTheDocument();
  });

  it("adds a row as an item at the amount it prints, in one click", async () => {
    const calls = render({
      ...flagged,
      printed_checks: { unread_rows: ["FERNLEAF BASIL $0.89 F", "MOSSGLEN OATS 3.10 F"] },
    });
    const user = userEvent.setup();
    await screen.findByTestId("review");
    await user.click(screen.getByRole("button", { name: "Add it at $0.89" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      raw_text: "FERNLEAF BASIL $0.89 F",
      line_kind: "item",
      line_total: "0.89",
    });
  });

  it("says a row already added is added", async () => {
    const line = { ...receiptPurchase.lines[0], raw_text: "FERNLEAF BASIL 0.89 F" };
    render({
      ...flagged,
      lines: [...receiptPurchase.lines, { ...line, id: "added-line", seq: 99 }],
      printed_checks: { unread_rows: ["FERNLEAF BASIL 0.89 F"] },
    });
    await screen.findByTestId("review");
    expect(screen.getByText("Added")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add it at/ })).not.toBeInTheDocument();
  });

  it("reads the last amount a row prints, and no amount from a saving", () => {
    expect(rowAmount("MOSSGLEN OATS 2 @ 1.55 3.10 F")).toBe("3.10");
    expect(rowAmount("KESTREL JAM 4,25")).toBe("4.25");
    expect(rowAmount("MEMBER SAVINGS 1.00-")).toBeNull();
    expect(rowAmount("FERNLEAF BASIL")).toBeNull();
  });
});
