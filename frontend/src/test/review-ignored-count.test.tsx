// Regression: ISSUE-006 — an ignored line was counted as "to identify"
// Found by /qa on 2026-09-28
// Report: .gstack/qa-reports/qa-report-localhost-2026-09-28.md
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function render(purchase: Purchase) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, purchase),
  });
  renderApp(base);
}

describe("receipt review counts", () => {
  it("does not count an ignored line as waiting to be identified", async () => {
    const ignored = { ...unmatchedLine, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8299", seq: 5, resolution: "ignored" as const, suggestions: [] };
    render({ ...receiptPurchase, lines: [...receiptPurchase.lines, ignored] });
    await screen.findByTestId("review");
    // Three item lines besides the discount; only the unmatched one needs a product.
    expect(screen.getByText("4 items · 1 to identify")).toBeInTheDocument();
    expect(screen.getByText("1 unidentified line will go to the to-identify queue.")).toBeInTheDocument();
  });
});
