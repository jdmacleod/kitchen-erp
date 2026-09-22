import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngestJob } from "../api/ingest";
import { units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { ingestJobId, receiptDocumentId, receiptPurchaseId } from "./purchase-fixtures";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const reviewJob: IngestJob = { id: ingestJobId, receipt_document_id: receiptDocumentId, stage: "review", status: "needs_review", attempts: 1, last_error: null, purchase_id: receiptPurchaseId, created_at: "2026-09-20T18:05:30Z", updated_at: "2026-09-20T18:06:00Z" };
const failedJob: IngestJob = { ...reviewJob, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9102", stage: "ocr", status: "failed", last_error: "ocr_unavailable", purchase_id: null };

function baseRoutes(jobs: () => IngestJob[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: jobs() }),
  };
}

describe("receipts", () => {
  it("uploads the photo as multipart with an idempotency key and lists the new job", async () => {
    let jobs: IngestJob[] = [];
    const calls = mockApi({
      ...baseRoutes(() => jobs),
      "POST /receipts": () => {
        jobs = [{ ...reviewJob, stage: "ocr", status: "pending", purchase_id: null }];
        return jsonResponse(201, { document: { id: receiptDocumentId }, job: jobs[0] });
      },
    });
    const user = userEvent.setup();
    renderApp("/receipts");
    expect(await screen.findByText("No receipts yet")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a photo of the receipt.");

    // A synthetic image; no real receipt is ever part of the tree.
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "slip.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Photo"), file);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/receipts");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    // The JSON client is bypassed: no content type is forced, so the browser sets the multipart boundary.
    expect(post?.headers.get("Content-Type")).toBeNull();
    expect(post?.body).toBeUndefined();
    expect(await screen.findByText(/Uploaded\. The receipt is being read/)).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Ingest jobs" });
    expect(within(list).getByTestId("ingest-job")).toHaveAttribute("data-status", "pending");
    expect(within(list).getByText("queued")).toBeInTheDocument();
  });

  it("links a job that is ready to review to its purchase and offers retry on a failed one", async () => {
    let jobs = [reviewJob, failedJob];
    const calls = mockApi({
      ...baseRoutes(() => jobs),
      [`POST /ingest-jobs/${failedJob.id}/retry`]: () => {
        jobs = [reviewJob, { ...failedJob, status: "pending", last_error: null }];
        return jsonResponse(200, jobs[1]);
      },
    });
    const user = userEvent.setup();
    renderApp("/receipts");

    const list = await screen.findByRole("list", { name: "Ingest jobs" });
    const rows = within(list).getAllByTestId("ingest-job");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("ready to review");
    expect(within(rows[0]).getByRole("link", { name: "Review purchase" })).toHaveAttribute("href", `/purchases/${receiptPurchaseId}`);
    expect(within(rows[0]).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("failed");
    expect(rows[1]).toHaveTextContent("ocr_unavailable");
    expect(within(rows[1]).getByRole("button", { name: "Enter by hand" })).toBeInTheDocument();

    await user.click(within(rows[1]).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/ingest-jobs/${failedJob.id}/retry`)).toBe(true));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Ingest jobs" })).getAllByTestId("ingest-job")[1]).toHaveAttribute("data-status", "pending"));
  });
});
