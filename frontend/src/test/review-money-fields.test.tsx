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
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

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
});
