import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { manualPurchase, outlierLine, receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

/**
 * Issue 121, ruling R2: a draft whose lines miss its printed total by a wide
 * margin is held for a careful look. It says so before it is opened, and the
 * review opens on the gap and the lines most likely to explain it. Nothing here
 * blocks a commit.
 */

const unscanned = { ...outlierLine, flags: ["not_in_scan"] };
const held: Purchase = {
  ...receiptPurchase,
  lines: receiptPurchase.lines.map((l) => (l.id === outlierLine.id ? unscanned : l)),
  lines_total: "38.2000",
  trust: "check_lines",
  held: true,
};

function mount(path: string, purchase: Purchase) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    [`GET /purchases/${receiptPurchaseId}`]: () => jsonResponse(200, purchase),
    "GET /purchases": () => jsonResponse(200, { items: [purchase, manualPurchase], next_cursor: null }),
    "GET /inbox": () =>
      jsonResponse(200, {
        items: [
          {
            kind: "receipt_held",
            title: "The Sep 20 receipt needs a careful look",
            detail: "Its lines add up to 38.20, but the receipt says 12.00. The flagged lines are shown first.",
            action_label: "Review",
            action_route: `/shop/purchases/${receiptPurchaseId}`,
            created_at: "2026-09-20T18:06:00Z",
          },
        ],
        reading: { count: 0, oldest_at: null, stalled: false },
      }),
  });
  renderApp(path);
}

describe("a receipt held for a careful look", () => {
  it("opens on the gap, with the unprinted line first and current", async () => {
    mount(`/purchases/${receiptPurchaseId}`, held);
    await screen.findByTestId("review");
    expect(screen.getByText("This receipt needs a careful look")).toBeInTheDocument();
    expect(screen.getByText(/Lines add up to \$38\.20; the receipt says \$12\.00\. The line most likely to explain it is shown first\./)).toBeInTheDocument();
    // The ordinary mismatch line would say the same thing twice.
    expect(screen.queryByText(/The receipt says \$12\.00 but the lines add up to/)).not.toBeInTheDocument();
    const rows = screen.getAllByTestId("review-line");
    expect(within(rows[0]).getByText("CSTLN WHOLE MILK GAL")).toBeInTheDocument();
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(within(rows[0]).getByText("not on the scan")).toBeInTheDocument();
    // Never a block: Commit is there as on any draft.
    expect(screen.getByRole("button", { name: "Commit purchase" })).toBeEnabled();
  });

  it("keeps the usual order and mismatch line when the draft is not held", async () => {
    mount(`/purchases/${receiptPurchaseId}`, { ...held, held: false });
    await screen.findByTestId("review");
    expect(screen.queryByText("This receipt needs a careful look")).not.toBeInTheDocument();
    expect(screen.getByText(/The receipt says \$12\.00 but the lines add up to/)).toBeInTheDocument();
  });

  it("is a squash row in Needs you", async () => {
    mount("/", held);
    const list = await screen.findByRole("list", { name: "Needs you" });
    const row = within(list).getByTestId("inbox-item");
    expect(within(row).getByText("Careful look")).toHaveClass("bg-amber-100");
    expect(within(row).getByText("The Sep 20 receipt needs a careful look")).toBeInTheDocument();
  });

  it("says Careful look in place of Draft on Purchases", async () => {
    mount("/shop/purchases", held);
    const table = await screen.findByRole("table", { name: "Purchases" });
    const [heldRow, manualRow] = within(table).getAllByRole("row").slice(1);
    expect(within(heldRow).getByText("Careful look")).toBeInTheDocument();
    expect(within(heldRow).queryByText("Draft")).not.toBeInTheDocument();
    expect(within(manualRow).queryByText("Careful look")).not.toBeInTheDocument();
  });
});
