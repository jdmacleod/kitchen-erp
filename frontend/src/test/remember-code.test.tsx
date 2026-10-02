import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { PurchaseLine } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { aliasLine, receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

// 2K, criterion 72 in review: a line identified by hand that carries an item
// code offers "Remember {code} for {product}", and only a click records it.

const base = `/purchases/${receiptPurchaseId}`;

describe("remembering an item code in review", () => {
  it("offers the code once the line has a product, and records it on a click", async () => {
    const chosen: PurchaseLine = { ...aliasLine, resolution: "manual", raw_text: "88001 WHOLE MILK GAL 4.29", code_offer: { scheme: "vendor_sku", value: "88001" } };
    let remembered = 0;
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`GET ${base}`]: () => jsonResponse(200, { ...receiptPurchase, lines: [chosen] }),
      [`POST ${base}/lines/${chosen.id}/remember-code`]: () => {
        remembered += 1;
        return jsonResponse(200, { scheme: "vendor_sku", value: "88001", product_id: aliasLine.product!.id });
      },
    });
    const user = userEvent.setup();
    renderApp(`/shop${base}`);
    const buttons = await screen.findAllByRole("button", { name: /Remember 88001 for .*All-Purpose Flour/ });
    expect(remembered).toBe(0);
    await user.click(buttons[0]);
    await waitFor(() => expect(remembered).toBe(1));
  });
});
