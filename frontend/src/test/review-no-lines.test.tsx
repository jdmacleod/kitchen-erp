import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

// A receipt the reader got no lines from: review said "Nothing here needs you.
// Every line is matched or ignored." and "Every item line has a product." beside
// Commit, with Add a line closed at the bottom.

const base = `/purchases/${receiptPurchaseId}`;
const unread = { ...receiptPurchase, lines: [], flags: ["lines_unparsed"] };

describe("a receipt with no lines read", () => {
  it("says so, opens Add a line and waits to be committed", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`GET ${base}`]: () => jsonResponse(200, unread),
    });
    renderApp(base);
    await screen.findByTestId("review");
    expect(screen.getByText(/No lines were read from this receipt/)).toBeInTheDocument();
    expect(screen.queryByText(/Every line is matched or ignored/)).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "New line" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Commit purchase" })).toBeDisabled();
    expect(screen.getByText("Add the receipt's lines before committing.")).toBeInTheDocument();
  });
});
