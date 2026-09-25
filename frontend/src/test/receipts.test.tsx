import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngestJob } from "../api/ingest";
import { units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { ingestJobId, receiptDocumentId, receiptPurchaseId } from "./purchase-fixtures";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// attempts mirrors the backend: zeroed whenever a stage succeeds or a job is
// retried, incremented only by a failed attempt.
const reviewJob: IngestJob = { id: ingestJobId, receipt_document_id: receiptDocumentId, stage: "review", status: "needs_review", attempts: 0, last_error: null, purchase_id: receiptPurchaseId, created_at: "2026-09-20T18:05:30Z", updated_at: "2026-09-20T18:06:00Z" };
const failedJob: IngestJob = { ...reviewJob, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9102", stage: "ocr", status: "failed", attempts: 5, last_error: "ocr_unavailable", purchase_id: null };

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
        jobs = [reviewJob, { ...failedJob, status: "pending", attempts: 0, last_error: null }];
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

  it("pins the job an inbox item names, even when it is not among the newest listed", async () => {
    mockApi({
      ...baseRoutes(() => [reviewJob]),
      [`GET /ingest-jobs/${failedJob.id}`]: () => jsonResponse(200, failedJob),
    });
    renderApp(`/receipts?job=${failedJob.id}`);

    const pinned = await screen.findByRole("heading", { name: "From your inbox" });
    const card = pinned.parentElement as HTMLElement;
    const row = await within(card).findByTestId("ingest-job");
    expect(row).toHaveAttribute("data-status", "failed");
    expect(within(row).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Enter by hand" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Ingest jobs" });
    expect(within(list).getAllByTestId("ingest-job")).toHaveLength(1);
  });

  it("lists the pinned job once, above the others", async () => {
    mockApi({
      ...baseRoutes(() => [reviewJob, failedJob]),
      [`GET /ingest-jobs/${failedJob.id}`]: () => jsonResponse(200, failedJob),
    });
    renderApp(`/receipts?job=${failedJob.id}`);
    await screen.findByRole("heading", { name: "From your inbox" });
    const list = await screen.findByRole("list", { name: "Ingest jobs" });
    const rows = within(list).getAllByTestId("ingest-job");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-status", "needs_review");
  });

  it("says what a failure means and what to change, keeping the code for a bug report", async () => {
    // A model that was simply given too little time used to read `model_unavailable`,
    // which sends the owner to check a model server that is working (issue #14).
    const timedOut: IngestJob = { ...failedJob, stage: "lines", last_error: "model_timeout", last_error_detail: "ReadTimeout after 120s" };
    mockApi(baseRoutes(() => [timedOut]));
    renderApp("/receipts");

    const row = within(await screen.findByRole("list", { name: "Ingest jobs" })).getByTestId("ingest-job");
    expect(row).toHaveTextContent("The model server answered too slowly");
    expect(row).toHaveTextContent("LLM_TIMEOUT_SECONDS");
    expect(row).toHaveTextContent("(model_timeout: ReadTimeout after 120s)");
  });

  it("calls a pending job that has already failed an attempt retrying, not queued", async () => {
    // A job looping on a retry used to be indistinguishable from one that had
    // not started (issue #13).
    const retrying: IngestJob = { ...failedJob, status: "pending", attempts: 3, last_error: "model_unavailable", last_error_detail: "ConnectError" };
    mockApi(baseRoutes(() => [retrying, { ...failedJob, status: "pending", attempts: 0, last_error: null }]));
    renderApp("/receipts");

    const rows = within(await screen.findByRole("list", { name: "Ingest jobs" })).getAllByTestId("ingest-job");
    expect(rows[0]).toHaveTextContent("retrying");
    expect(rows[0]).toHaveTextContent("The model server could not be reached");
    expect(rows[1]).toHaveTextContent("queued");
  });

  it("falls back to the bare code for a code it has no sentence for", async () => {
    // Visible rather than hidden behind "something went wrong": an unprosed code
    // on screen is the prompt to add one.
    mockApi(baseRoutes(() => [{ ...failedJob, last_error: "a_code_from_the_future" }]));
    renderApp("/receipts");

    const row = within(await screen.findByRole("list", { name: "Ingest jobs" })).getByTestId("ingest-job");
    expect(row).toHaveTextContent("a_code_from_the_future");
  });
});
