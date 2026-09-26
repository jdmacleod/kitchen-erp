import { screen, waitFor, within } from "@testing-library/react";
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
// Past the lines stage: its draft exists, though the job is still running.
const runningWithDraft: IngestJob = { ...readingJob, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a02", stage: "resolve", status: "running", purchase_id: draft.id };

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
    "GET /ingest-jobs": (call: RecordedCall) => {
      const status = call.query.get("status");
      if (status === "pending") return jsonResponse(200, { items: [readingJob] });
      if (status === "running") return jsonResponse(200, { items: [runningWithDraft] });
      // Unfiltered, the newest 50 would not reach an old upload still being read.
      return jsonResponse(200, { items: [] });
    },
  };
}

const rows = () => within(screen.getByRole("table", { name: "Purchases" })).getAllByRole("row").slice(1);

describe("purchases: receipts being read and drafts (G1, spec 10)", () => {
  it("lists a receipt being read as a row with a Reading pill, linked to its job", async () => {
    mockApi(routes());
    renderApp("/shop/purchases");

    const reading = await screen.findByTestId("reading-row");
    expect(reading).toHaveTextContent("Reading");
    expect(reading).toHaveTextContent("Uploaded Sep 25, 2026");
    expect(within(reading).getByRole("link")).toHaveAttribute("href", `/shop/receipts?job=${readingJob.id}`);
    // The running job that already made its draft is listed as that draft, once.
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

describe("purchases: review findings on Reading rows", () => {
  it("says it could not check for receipts being read, rather than that there are none", async () => {
    mockApi({
      ...routes(),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingest-jobs": () => jsonResponse(500, { error: { code: "internal", message: "boom" } }),
    });
    renderApp("/shop/purchases");

    expect(await screen.findByText("Couldn't check for receipts being read.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "No purchases yet" })).toBeNull();
  });

  it("fetches the purchases again when a Reading row becomes a draft", async () => {
    let read = false;
    const calls = mockApi({
      ...routes(),
      "GET /purchases": (call: RecordedCall) =>
        call.query.get("status") ? jsonResponse(200, { items: read ? [draft] : [], next_cursor: null }) : jsonResponse(200, { items: read ? [draft] : [], next_cursor: null }),
      "GET /ingest-jobs": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("status") === "pending" && !read ? [readingJob] : [] }),
    });
    renderApp("/shop/purchases");
    await screen.findByTestId("reading-row");
    const before = calls.filter((c) => c.path.startsWith("/purchases")).length;

    // The job finishes; the next poll (every 3 s while one is in flight) sees it gone.
    read = true;
    await waitFor(() => expect(screen.queryByTestId("reading-row")).toBeNull(), { timeout: 5000 });
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith("/purchases")).length).toBeGreaterThan(before));
    expect(await screen.findByText("Location needed")).toBeInTheDocument();
  }, 10_000);
});

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});
