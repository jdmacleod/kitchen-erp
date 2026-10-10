// Another store family's own brand, offered as a match, says so (spec 16, 2R-2).
// Every brand, family and store here is invented.
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;
const NOTE = "Brightwater Basics is Brightwater Grocers' own brand";

describe("store-brand notes on receipt suggestions", () => {
  it("shows the note beside a suggestion that carries one", async () => {
    const line = {
      ...unmatchedLine,
      suggestions: [unmatchedLine.suggestions![0], { ...unmatchedLine.suggestions![1], brand_note: NOTE }],
    };
    const purchase: Purchase = { ...receiptPurchase, lines: [line] };
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
    const list = await screen.findByRole("list", { name: `Suggestions for line ${line.seq}` });
    const items = within(list).getAllByRole("listitem");
    expect(items[1]).toHaveTextContent(NOTE);
    expect(items[0]).not.toHaveTextContent("own brand");
  });
});
