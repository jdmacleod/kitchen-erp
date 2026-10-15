// Regression: ISSUE-001 — the review table squeezed the receipt text to a few characters and pushed Actions out of its card
// Found by /qa on 2026-10-12
// Report: QA run 2026-10-12, kept outside the repository (it was run on household data)
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { flagLabel } from "../components/purchases/lineParts";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { outlierLine, receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

// jsdom does not lay out, so this pins the structure that keeps the columns
// fixed: a fixed table with set widths, the flags under the receipt text rather
// than under the line number, and the editor in a row of its own.

const base = `/purchases/${receiptPurchaseId}`;

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    [`GET ${base}`]: () => jsonResponse(200, receiptPurchase),
  };
}

describe("receipt review: the lines table keeps its columns (QA 2026-10-12)", () => {
  it("lays the table out at fixed widths, five columns", async () => {
    mockApi(routes());
    renderApp(base);
    const table = await screen.findByRole("table", { name: "Receipt lines" });
    expect(table).toHaveClass("table-fixed");
    expect(table.querySelectorAll("colgroup col")).toHaveLength(5);
  });

  it("shows a line's flags under its receipt text, not under its number", async () => {
    mockApi(routes());
    renderApp(base);
    await screen.findByRole("table", { name: "Receipt lines" });
    const row = document.getElementById(`review-line-${outlierLine.id}`)!;
    const [number, receipt] = within(row).getAllByRole("cell");
    expect(outlierLine.flags).toEqual(["price_outlier"]);
    expect(number).toHaveTextContent(new RegExp(`^${outlierLine.seq}$`));
    expect(within(receipt).getByText(outlierLine.raw_text!)).toBeInTheDocument();
    expect(within(receipt).getByText(flagLabel("price_outlier"))).toBeInTheDocument();
  });

  it("opens the line editor in a row of its own under the line", async () => {
    mockApi(routes());
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByRole("table", { name: "Receipt lines" });
    const row = document.getElementById(`review-line-${outlierLine.id}`)!;
    row.focus();
    await user.keyboard("e");
    const editRow = await screen.findByTestId("review-edit-row");
    expect(
      within(editRow).getByRole("group", { name: `Edit line ${outlierLine.seq}` }),
    ).toBeInTheDocument();
    expect(within(row).getByText("Editing below")).toBeInTheDocument();
  });
});
