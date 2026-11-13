// Regression: ISSUE-006 — an ignored line was counted as "to identify"
// Found by /qa on 2026-09-28
// Report: .gstack/qa-reports/qa-report-localhost-2026-09-28.md
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { add } from "../lib/decimal";
import { breakdown } from "../lib/reconcile";
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
    const ignored = {
      ...unmatchedLine,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8299",
      seq: 5,
      resolution: "ignored" as const,
      suggestions: [],
    };
    render({ ...receiptPurchase, lines: [...receiptPurchase.lines, ignored] });
    await screen.findByTestId("review");
    // Three item lines besides the discount; only the unmatched one needs a product.
    expect(screen.getByText("4 items · 1 to identify")).toBeInTheDocument();
    expect(
      screen.getByText("1 unidentified line will go to the to-identify queue."),
    ).toBeInTheDocument();
  });

  it("does not count an ignored line among those that emit a price", async () => {
    // Review of #76: the commit dialog counted items less unresolved, so an
    // ignored line now counted as emitting a price it never emits.
    const ignored = {
      ...unmatchedLine,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8299",
      seq: 5,
      resolution: "ignored" as const,
      suggestions: [],
    };
    render({ ...receiptPurchase, lines: [...receiptPurchase.lines, ignored] });
    const user = userEvent.setup();
    await screen.findByTestId("review");
    screen.getAllByTestId("review-line")[1].focus();
    await user.keyboard("c");
    const dialog = await screen.findByRole("dialog", { name: "Commit this purchase?" });
    expect(dialog).toHaveTextContent(
      "2 lines emit a price observation now. 1 unidentified line waits in the to-identify queue.",
    );
  });

  it("asks before committing lines that are short of the receipt's total", async () => {
    const short = {
      ...receiptPurchase,
      total: add(breakdown(receiptPurchase).linesTotal, "5"),
      flags: ["total_mismatch", "lines_partial"],
    };
    render(short);
    const user = userEvent.setup();
    await screen.findByTestId("review");
    // The header and the checks name one figure for what the lines add up to.
    expect(screen.getByText(`Lines add up to $${breakdown(short).linesTotal}`)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Commit purchase" }));
    const dialog = await screen.findByRole("dialog", { name: "Commit this purchase?" });
    expect(dialog).toHaveTextContent(
      "The lines are $5.00 short of the receipt's total. Part of the receipt was not read",
    );
    expect(screen.getByRole("button", { name: "Commit anyway" })).toBeInTheDocument();
  });
});
