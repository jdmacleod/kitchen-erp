import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import type { Reread, RereadLine } from "../api/rereads";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

// Spec 10, "Re-read the lines" (04, 2S, #272), and UI-3.23. Every wording is invented.

const api = `/purchases/${receiptPurchaseId}`;
const rereadId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fc001";

const line = (raw_text: string, line_total: string, extra: Partial<RereadLine> = {}): RereadLine => ({
  raw_text,
  line_kind: "item",
  qty: "1",
  unit: "each",
  unit_price: null,
  line_total,
  attached: null,
  flags: [],
  ...extra,
});

const sums = (mismatch: boolean, difference: string) => ({
  checked: true,
  computed_total: "12.00",
  printed_total: "12.00",
  difference,
  mismatch,
});

const ready: Reread = {
  id: rereadId,
  purchase_id: receiptPurchaseId,
  mode: "check",
  status: "ready",
  reason: null,
  created_at: "2026-10-12T18:00:00Z",
  decided_at: null,
  proposal: {
    changes: [
      { action: "changed", line_id: "l1", before: line("MOSS BAY KELP", "40.00"), after: line("MOSS BAY KELP", "4.00"), fields: ["line_total"], why: null, reading: null },
      { action: "added", line_id: null, before: null, after: line("BAG FEE", "0.10"), fields: null, why: null, reading: null },
      { action: "kept", line_id: "l3", before: line("FERN TEA", "6.25"), after: null, fields: null, why: "edited", reading: null },
    ],
    counts: { changed: 1, added: 1, removed: 0, kept: 1 },
    before: { reconciliation: sums(true, "36.00"), flags: ["total_mismatch"] },
    after: { reconciliation: sums(false, "0.00"), flags: [] },
  },
};

function purchase(extra: Partial<Purchase> = {}): Purchase {
  return { ...receiptPurchase, reread: { can_recheck: true, open: null }, ...extra };
}

function routes(p: Purchase, latest: () => Reread | null, more: Record<string, RouteHandler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    [`GET ${api}`]: () => jsonResponse(200, p),
    [`GET ${api}/rereads/latest`]: () => {
      const found = latest();
      return found ? jsonResponse(200, found) : jsonResponse(404, { error: { code: "not_found", message: "This purchase has no re-read." } });
    },
    ...more,
  };
}

describe("re-read the lines (04, 2S; UI-3.23)", () => {
  it("offers both ways, re-checks, shows the changes in words, and applies them", async () => {
    let latest: Reread | null = null;
    const calls = mockApi(
      routes(purchase(), () => latest, {
        [`POST ${api}/rereads`]: () => {
          latest = ready;
          return jsonResponse(201, ready);
        },
        [`POST /rereads/${rereadId}/apply`]: () => jsonResponse(200, { ...ready, status: "applied" }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/purchases/${receiptPurchaseId}`);
    await screen.findByTestId("review");

    await user.click(screen.getByRole("button", { name: "Re-read lines" }));
    const panel = screen.getByRole("group", { name: "Re-read this receipt's lines" });
    expect(within(panel).getByRole("button", { name: "Read again" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Re-check the lines" }));
    expect(calls.find((c) => c.method === "POST" && c.path === `${api}/rereads`)?.body).toEqual({ mode: "check" });

    expect(await screen.findByRole("heading", { level: 1, name: "Re-read lines" })).toBeInTheDocument();
    expect(screen.getByText("Adds up now. It was off by $36.00.")).toBeInTheDocument();
    expect(screen.getByText("1 changed, 1 added, 1 kept as you left them.")).toBeInTheDocument();
    const changes = screen.getByRole("list", { name: "Changes" });
    const rows = within(changes).getAllByRole("listitem");
    // In words, not only by colour: each row says what happens to it.
    expect(within(rows[0]).getByText("Changed")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$40.00")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$4.00")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Added")).toBeInTheDocument();
    expect(within(rows[2]).getByText(/You edited this line/)).toBeInTheDocument();
    expect(screen.queryByText(/Applying reopens this purchase/)).toBeNull();

    await user.click(screen.getAllByRole("button", { name: "Apply changes" })[0]);
    expect(await screen.findByText("Re-read applied: 2 lines changed.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === `/rereads/${rereadId}/apply`)).toBe(true);
  });

  it("offers only Read again when the reading didn't keep the reader's answer", async () => {
    mockApi(routes(purchase({ reread: { can_recheck: false, open: null } }), () => null));
    const user = userEvent.setup();
    renderApp(`/purchases/${receiptPurchaseId}`);
    await screen.findByTestId("review");
    await user.click(screen.getByRole("button", { name: "Re-read lines" }));
    const panel = screen.getByRole("group", { name: "Re-read this receipt's lines" });
    expect(within(panel).queryByRole("button", { name: "Re-check the lines" })).toBeNull();
    expect(within(panel).getByRole("button", { name: "Read again" })).toBeInTheDocument();
  });

  it("goes to an open re-read instead of starting another", async () => {
    mockApi(routes(purchase({ reread: { can_recheck: true, open: { id: rereadId, status: "ready" } } }), () => ready));
    renderApp(`/purchases/${receiptPurchaseId}`);
    await screen.findByTestId("review");
    expect(screen.getByRole("link", { name: "Re-read ready" })).toHaveAttribute("href", `/shop/purchases/${receiptPurchaseId}/reread`);
  });

  it("says a committed purchase is reopened, and that its lines changed since", async () => {
    mockApi(
      routes(purchase({ status: "committed" }), () => ready, {
        [`POST /rereads/${rereadId}/apply`]: () =>
          jsonResponse(409, { error: { code: "reread_stale", message: "The lines changed since this re-read." } }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${receiptPurchaseId}/reread`);
    expect(await screen.findByText("Applying reopens this purchase. Its prices change when you commit it again.")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Apply changes" })[0]);
    expect(await screen.findByText("The lines changed since this re-read. Re-check or read again to see what would change now.")).toBeInTheDocument();
  });

  it("waits while reading, and says when nothing would change", async () => {
    let latest: Reread = { ...ready, mode: "read", status: "pending", proposal: null };
    mockApi(routes(purchase(), () => latest));
    renderApp(`/shop/purchases/${receiptPurchaseId}/reread`);
    expect(await screen.findByText("Reading the receipt again… This page updates when it's done.")).toBeInTheDocument();
    latest = { ...ready, status: "no_changes", proposal: null };
    expect(await screen.findByText("Today's reading matches these lines.", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("says why a read failed and offers to try again", async () => {
    mockApi(routes(purchase(), () => ({ ...ready, mode: "read", status: "failed", reason: "invalid_model_output", proposal: null })));
    renderApp(`/shop/purchases/${receiptPurchaseId}/reread`);
    expect(await screen.findByText("The model's answer didn't make sense as receipt lines.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
