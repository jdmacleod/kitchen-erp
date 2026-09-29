// "Remember store code" in receipt review (spec 03 §1F criterion 61, design D10).
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase, StoreCodeOffer } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;
const offer: StoreCodeOffer = {
  code: "0217",
  location_id: chainLocationId,
  location_name: chainLocation.name,
  printed_line: "STORE 0217  TERM ••••",
};

function render(remember: () => Response, purchase: Purchase = receiptPurchase) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, purchase),
    [`GET ${base}/store-code-offer`]: () => jsonResponse(200, { offer }),
    [`POST ${base}/remember-store-code`]: remember,
  });
  renderApp(base);
  return calls;
}

describe("remember store code", () => {
  it("shows the printed line and remembers the code in place", async () => {
    const calls = render(() => jsonResponse(200, offer));
    const user = userEvent.setup();
    expect(await screen.findByText("STORE 0217 TERM ••••", { normalizer: (t) => t.replace(/\s+/g, " ") })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Remember 0217 for ${chainLocation.name}` }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ code: "0217" }));
    expect(await screen.findByText(`Remembered 0217 for ${chainLocation.name}.`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remember 0217/ })).not.toBeInTheDocument();
  });

  it("says why when the code is no longer on offer, and keeps the button", async () => {
    render(() => errorResponse(409, "store_code_not_offered", "That store code is not on offer for this receipt any more."));
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: `Remember 0217 for ${chainLocation.name}` });
    await user.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("not on offer");
    expect(screen.getByRole("button", { name: `Remember 0217 for ${chainLocation.name}` })).toBeEnabled();
  });

  it("offers nothing on a committed purchase", async () => {
    const calls = render(() => jsonResponse(200, offer), { ...receiptPurchase, status: "committed" });
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/purchases/"))).toBe(true));
    expect(screen.queryByRole("button", { name: /Remember 0217/ })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/store-code-offer"))).toBe(false);
  });
});
