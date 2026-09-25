import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { IngestJob } from "../api/ingest";
import type { Purchase } from "../api/purchases";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase } from "./purchase-fixtures";

const draft: Purchase = { ...manualPurchase, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a01", status: "draft", source: "receipt", vendor_location: null };
const readingJob: IngestJob = {
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01",
  stage: "ocr",
  status: "running",
  purchase_id: null,
  last_error: null,
  created_at: "2026-09-25T09:00:00Z",
};
const doneJob: IngestJob = { ...readingJob, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a02", status: "done", purchase_id: draft.id };

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 1, oldest_at: readingJob.created_at, stalled: false } }),
    "GET /purchases": (call: RecordedCall) => {
      const status = call.query.get("status");
      if (status === "draft") return jsonResponse(200, { items: [draft], next_cursor: null });
      if (status) return jsonResponse(200, { items: [], next_cursor: null });
      return jsonResponse(200, { items: [draft, manualPurchase], next_cursor: null });
    },
    "GET /ingest-jobs": () => jsonResponse(200, { items: [readingJob, doneJob] }),
  };
}

const rows = () => within(screen.getByRole("table", { name: "Purchases" })).getAllByRole("row").slice(1);

describe("purchases: receipts being read and drafts (G1, spec 10)", () => {
  it("lists a receipt being read as a row with a Reading pill, linked to its job", async () => {
    mockApi(routes());
    renderApp("/shop/purchases");

    const reading = await screen.findByTestId("reading-row");
    expect(reading).toHaveTextContent("Reading");
    expect(within(reading).getByRole("link")).toHaveAttribute("href", `/shop/receipts?job=${readingJob.id}`);
    // Only jobs in flight: the finished one is its draft now.
    expect(screen.getAllByTestId("reading-row")).toHaveLength(1);
    expect(rows()).toHaveLength(3);
  });

  it("says a draft needs a location, and counts drafts on the filter and the phone banner", async () => {
    mockApi(routes());
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByTestId("reading-row");

    expect(rows()[1]).toHaveTextContent("Location needed");
    expect(rows()[1]).toHaveTextContent("Receipt");
    const filter = screen.getByRole("group", { name: "Status" });
    expect(within(filter).getByRole("button", { name: "Drafts 1" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /1 draft to finish/ }));
    expect(within(filter).getByRole("button", { name: "Drafts 1" })).toHaveAttribute("aria-pressed", "true");
    // Drafts still shows the receipt that is about to become one.
    expect(await screen.findByTestId("reading-row")).toBeInTheDocument();
  });

  it("leaves receipts being read out of Reviewed and Committed", async () => {
    mockApi(routes());
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByTestId("reading-row");

    await user.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: "Committed" }));
    expect(await screen.findByRole("region", { name: "No committed purchases" })).toBeInTheDocument();
    expect(screen.queryByTestId("reading-row")).toBeNull();
  });

  it("lists purchases as rows below lg: where, date and lines, total and status (Phone: purchases)", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }),
    });
    mockApi(routes());
    renderApp("/shop/purchases");

    const list = await screen.findByRole("list", { name: "Purchases" });
    expect(screen.queryByRole("table")).toBeNull();
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Receipt being read");
    expect(items[0]).toHaveTextContent("Reading");
    expect(items[1]).toHaveTextContent("Location needed");
    expect(items[2]).toHaveTextContent("Pier Farmers Market");
    expect(items[2]).toHaveTextContent("2 lines");
    expect(items[2]).toHaveTextContent("$14.21");
    expect(items[2]).toHaveTextContent("Committed");
  });
});

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});
