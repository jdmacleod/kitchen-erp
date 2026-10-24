// Rows the scan prints like purchases that no line accounts for are named in
// review, so a person can add the missing line without hunting the image.
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

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
});
