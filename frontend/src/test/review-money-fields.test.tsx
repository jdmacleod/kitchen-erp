// Regression: ISSUE-009 — review fields showed stored money to four places ("65.4700")
// Found by /qa on 2026-09-28
// Report: .gstack/qa-reports/qa-report-localhost-2026-09-28.md
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { aliasLine, receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;
const purchase: Purchase = { ...receiptPurchase, subtotal: "11.5000", tax: "0.0000", total: "12.0000" };

describe("review money fields", () => {
  it("shows amounts to cents and does not resend one that was not changed", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`GET ${base}`]: () => jsonResponse(200, purchase),
      [`PATCH ${base}`]: () => jsonResponse(200, purchase),
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    expect(screen.getByLabelText("Subtotal")).toHaveValue("11.50");
    expect(screen.getByLabelText("Tax")).toHaveValue("0.00");
    expect(screen.getByLabelText("Total")).toHaveValue("12.00");

    // Only the tax changes: "12.00" is the stored "12.0000", not an edit,
    // and sending it would answer a missing-total flag nobody answered.
    await user.clear(screen.getByLabelText("Tax"));
    await user.type(screen.getByLabelText("Tax"), "0.50");
    await user.click(screen.getByRole("button", { name: "Save header" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ tax: "0.50" });
  });

  it("sends a flagged price saved as it stands, so the warning can clear", async () => {
    // Review of #76: comparing as numbers dropped an unchanged line total from
    // the patch, and the server clears a price flag only when it is sent.
    const flagged = { ...aliasLine, line_total: "3.4900", flags: ["exceeds_total"] };
    const withFlag: Purchase = { ...purchase, lines: [flagged, ...receiptPurchase.lines.filter((l) => l.id !== aliasLine.id)] };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`GET ${base}`]: () => jsonResponse(200, withFlag),
      [`PATCH ${base}/lines/${aliasLine.id}`]: () => jsonResponse(200, withFlag),
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    screen.getAllByTestId("review-line")[0].focus();
    await user.keyboard("e");
    expect(await screen.findByLabelText("Line total")).toHaveValue("3.49");
    await user.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ line_total: "3.49" });
  });
});
