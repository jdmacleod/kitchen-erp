import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { IngestJob } from "../api/ingest";
import { readingSentence, type InboxReading } from "../api/inbox";
import { TrustBadge } from "../components/purchases/TrustBadge";
import { units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { batchOf, ingestJobId, receiptDocumentId, receiptPurchaseId } from "./purchase-fixtures";

/**
 * Issue 122, ruling F2: a batch of receipts says what it came to, each receipt
 * says whether its reading can be trusted before it is opened, and Home says how
 * far reading has got. Invented stores and amounts only.
 */

const job: IngestJob = { id: ingestJobId, receipt_document_id: receiptDocumentId, stage: "review", status: "needs_review", attempts: 0, last_error: null, purchase_id: receiptPurchaseId, created_at: "2026-09-12T18:05:30Z", updated_at: "2026-09-20T18:06:00Z", uploaded_at: "2026-09-20T18:05:30Z" };
const readingJob: IngestJob = { ...job, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c02", status: "pending", stage: "ocr", purchase_id: null };
const seenJob: IngestJob = { ...job, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c03", status: "done", stage: "committed" };

const reading = (over: Partial<InboxReading> = {}): InboxReading => ({ count: 0, oldest_at: null, stalled: false, ...over });

describe("the reading sentence", () => {
  it("counts a batch of several as how far it has got, with an estimate", () => {
    expect(readingSentence(reading({ count: 9, batch_done: 3, batch_of: 12, minutes_left: 8 }))).toBe("Reading 4 of 12 receipts · about 8 minutes left");
  });

  it("gives no estimate until a read has finished", () => {
    expect(readingSentence(reading({ count: 3, batch_done: 0, batch_of: 3, minutes_left: null }))).toBe("Reading 1 of 3 receipts…");
  });

  it("keeps one receipt plain, and a minute singular", () => {
    expect(readingSentence(reading({ count: 1, batch_done: 0, batch_of: 1, minutes_left: 1 }))).toBe("Reading 1 receipt · about 1 minute left");
  });

  it("names product pages beside the batch", () => {
    expect(readingSentence(reading({ count: 2, pages: 1, batch_done: 1, batch_of: 3 }))).toBe("Reading 2 of 3 receipts and 1 product page…");
  });
});

describe("the trust badge", () => {
  it("says each state in words beside a mark, never by colour alone", () => {
    const { rerender } = render(<TrustBadge trust="adds_up" />);
    expect(screen.getByTestId("trust-badge")).toHaveTextContent("Adds up");
    expect(screen.getByTestId("trust-badge")).toHaveClass("bg-green-100");
    expect(screen.getByTestId("trust-badge").querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<TrustBadge trust="check_lines" gap="0.3000" />);
    expect(screen.getByTestId("trust-badge")).toHaveTextContent("Check the lines · off by $0.30");
    expect(screen.getByTestId("trust-badge")).toHaveClass("bg-amber-100");
    rerender(<TrustBadge trust="check_lines" gap="5.6000" held />);
    expect(screen.getByTestId("trust-badge")).toHaveTextContent("Careful look · off by $5.60");
    rerender(<TrustBadge trust="couldnt_read" />);
    expect(screen.getByTestId("trust-badge")).toHaveTextContent("Couldn't read");
    expect(screen.getByTestId("trust-badge")).toHaveClass("bg-red-100");
  });

  it("shows nothing for a purchase entered by hand", () => {
    const { container } = render(<TrustBadge trust={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

function mountReceipts(batches: unknown[]) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /upload-batches": () => jsonResponse(200, { items: batches }),
  });
  renderApp("/shop/receipts");
}

describe("upload history on Receipts", () => {
  it("groups receipts by upload, with what the batch came to", async () => {
    const batch = batchOf([job, readingJob, seenJob], {
      file_count: 4,
      already_seen: 1,
      new: 2,
      reading: 1,
      check_lines: 1,
      couldnt_read: 0,
      receipts: [
        { job, outcome: "new", store: "Gullwing Grocer", total: "9.8000", item_lines: 3, lines_total: "4.2000", trust: "check_lines", gap: "5.6000", held: true },
        { job: readingJob, outcome: "new", store: null, total: null, item_lines: null, lines_total: null, trust: null, gap: null, held: false },
        { job: seenJob, outcome: "already_seen", store: "Gullwing Grocer", total: "2.1000", item_lines: 1, lines_total: "2.1000", trust: "adds_up", gap: null, held: false },
      ],
    });
    mountReceipts([batch]);
    const section = await screen.findByTestId("upload-batch");
    expect(section).toHaveTextContent("3 receipts · 1 uploaded before · 1 not uploaded · 1 being read");
    const counts = within(section).getByLabelText("What the readings came to");
    // A zero is a dash, not something to look at.
    expect(within(counts).getByText("Add up").nextSibling).toHaveTextContent("—");
    expect(within(counts).getByText("To check").nextSibling).toHaveTextContent("1");
    expect(within(counts).getByText("Couldn't read").nextSibling).toHaveTextContent("—");

    const rows = within(section).getAllByTestId("ingest-job");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByTestId("trust-badge")).toHaveTextContent("Careful look · off by $5.60");
    expect(rows[0]).toHaveTextContent("Gullwing Grocer · $9.80 · 3 lines");
    expect(within(rows[1]).queryByTestId("trust-badge")).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("waiting to be read");
    expect(rows[2]).toHaveTextContent("uploaded before");
    expect(within(rows[2]).getByTestId("trust-badge")).toHaveTextContent("Adds up");
  });

  it("dates a receipt by its last upload, not its first", async () => {
    mountReceipts([batchOf([job])]);
    const row = await screen.findByTestId("ingest-job");
    expect(within(row).getByRole("time")).toHaveAttribute("dateTime", "2026-09-20T18:05:30Z");
  });
});

describe("a receipt row in Needs you", () => {
  it("carries its trust badge", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /inbox": () =>
        jsonResponse(200, {
          items: [
            {
              kind: "receipt",
              title: "Finish the Sep 20 receipt",
              detail: "3 lines ready to review and commit.",
              action_label: "Review",
              action_route: `/shop/purchases/${receiptPurchaseId}`,
              created_at: "2026-09-20T18:06:00Z",
              trust: "check_lines",
              gap: "0.3000",
            },
          ],
          reading: { count: 2, oldest_at: "2026-09-20T18:05:00Z", stalled: false, batch_done: 1, batch_of: 3, minutes_left: 4 },
        }),
    });
    renderApp("/");
    const row = await screen.findByTestId("inbox-item");
    expect(within(row).getByTestId("trust-badge")).toHaveTextContent("Check the lines · off by $0.30");
    expect(await screen.findByText("Reading 2 of 3 receipts · about 4 minutes left")).toBeInTheDocument();
  });
});
