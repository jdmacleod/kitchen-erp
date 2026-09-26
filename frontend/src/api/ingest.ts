// Phase 2C/2D wire types and hooks for the receipt ingest pipeline: upload a
// receipt, watch its job, and hand the result to the review screen. Kept thin;
// the pipeline itself is the backend's concern.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { qs } from "./catalog";
import { API_BASE, ApiError, api, newIdempotencyKey } from "./client";
import { invalidatePurchases } from "./purchases";
import type { ErrorEnvelope } from "./types";

// The backend's statuses; anything else is shown verbatim.
export type JobStatus = "pending" | "running" | "needs_review" | "done" | "failed" | (string & {});

/** A job that will still change on its own; the list polls while any is in flight. */
export function jobInFlight(job: IngestJob): boolean {
  return job.status === "pending" || job.status === "running";
}

export interface StageResult {
  stage: string;
  adapter: string;
  adapter_version: string;
  duration_ms: number | null;
  created_at: string;
  output?: Record<string, unknown> | null;
}

export interface LocationCandidate {
  location_id: string;
  name: string;
  vendor_name: string;
  score: string;
}

export interface IngestJob {
  id: string;
  receipt_document_id?: string | null;
  stage: string | null;
  status: JobStatus;
  attempts?: number;
  purchase_id: string | null;
  last_error: string | null;
  last_error_detail?: string | null;
  /** Present on the detail endpoint; `results` is accepted as an older spelling. */
  stage_results?: StageResult[];
  results?: StageResult[];
  created_at?: string;
  updated_at?: string;
}

export interface ReceiptDocument {
  id: string;
  created_at?: string;
}

export interface ReceiptUploadResult {
  document: ReceiptDocument;
  job: IngestJob;
}

export const ingestKeys = {
  jobs: ["ingest-jobs"] as const,
  jobList: (status: string) => ["ingest-jobs", "list", status] as const,
  job: (id: string) => ["ingest-jobs", "detail", id] as const,
};

const enc = encodeURIComponent;

export function useIngestJobs(status = "", enabled = true) {
  return useQuery({
    queryKey: ingestKeys.jobList(status),
    queryFn: () => api<{ items: IngestJob[] }>(`/ingest-jobs${qs({ status })}`),
    select: (data) => data.items,
    enabled,
    // Poll while anything is still in flight.
    refetchInterval: (query) => (query.state.data?.items.some(jobInFlight) ? 3_000 : false),
  });
}

export function useIngestJob(id: string | undefined, includeOutput = true) {
  return useQuery({
    queryKey: [...ingestKeys.job(id ?? ""), includeOutput],
    queryFn: () => api<IngestJob>(`/ingest-jobs/${enc(id ?? "")}${qs({ include_output: includeOutput })}`),
    enabled: Boolean(id),
    // Poll while it is in flight, as the list does, so a retried job shows its outcome.
    refetchInterval: (query) => (query.state.data && jobInFlight(query.state.data) ? 3_000 : false),
  });
}

/**
 * Header-stage location candidates from a job's stage results, ranked as the
 * matcher left them. The header output nests them under `location.candidates`
 * with `vendor_location_id` / `location_name`; a flat `location_candidates`
 * list with `location_id` / `name` is accepted too. Nothing else in the output
 * is read: it is untrusted text and only these identifiers and names are shown.
 */
export function locationCandidates(job: IngestJob | undefined): LocationCandidate[] {
  const results = job?.stage_results ?? job?.results ?? [];
  const header = results.find((r) => r.stage === "header" && r.output) ?? results.find((r) => r.output);
  const output = header?.output as { location?: { candidates?: unknown }; location_candidates?: unknown } | null | undefined;
  const raw = output?.location?.candidates ?? output?.location_candidates;
  if (!Array.isArray(raw)) return [];
  const out: LocationCandidate[] = [];
  for (const c of raw as Record<string, unknown>[]) {
    const location_id = str(c.location_id ?? c.vendor_location_id);
    const name = str(c.name ?? c.location_name);
    if (!location_id || !name) continue;
    out.push({ location_id, name, vendor_name: str(c.vendor_name) ?? name, score: str(c.score) ?? "" });
  }
  return out;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function useRetryJob() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<IngestJob>(`/ingest-jobs/${enc(id)}/retry`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ingestKeys.jobs }),
  });
}

export function useJobToManual() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<{ job: IngestJob; purchase_id: string }>(`/ingest-jobs/${enc(id)}/to-manual`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ingestKeys.jobs });
      // Through the shared helper, not a literal key: it is the one place that
      // decides what a purchase mutation clears (issue #19).
      //
      // "all", not the default: convert_to_manual reuses the job's own draft
      // receipt purchase when it has one, dropping its lines and changing its
      // source to manual. That purchase may already be cached from the review
      // screen, so clearing only the lists would leave its detail showing lines
      // that no longer exist.
      invalidatePurchases(client, "all");
    },
  });
}

export interface ReceiptUploadInput {
  image: File;
  ocr_text?: string;
  captured_at?: string;
}

/**
 * Multipart upload; the JSON client cannot carry a file. Same error envelope
 * handling as `api`. Nothing about the household (no position) is sent.
 */
export async function uploadReceipt(input: ReceiptUploadInput): Promise<ReceiptUploadResult> {
  const form = new FormData();
  form.append("image", input.image, input.image.name);
  if (input.ocr_text) form.append("ocr_text", input.ocr_text);
  if (input.captured_at) form.append("captured_at", input.captured_at);
  const response = await fetch(`${API_BASE}/receipts`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Idempotency-Key": newIdempotencyKey() },
    body: form,
  });
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : undefined;
  } catch {
    payload = undefined;
  }
  if (!response.ok) {
    const err = (payload as ErrorEnvelope | undefined)?.error;
    if (err && typeof err.code === "string") throw new ApiError(response.status, err.code, err.message, err.details);
    throw new ApiError(response.status, "http_error", `Upload failed with status ${response.status}.`);
  }
  return payload as ReceiptUploadResult;
}

export function useUploadReceipt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: uploadReceipt,
    onSuccess: () => void client.invalidateQueries({ queryKey: ingestKeys.jobs }),
  });
}

/** The URL of a receipt's image on this origin; the browser sends the session cookie. */
/**
 * The receipt as a browser can show it: the original, or page 1 as PNG for a PDF
 * or HEIC (#30). `width` asks for a scaled PNG, for a thumbnail (#28).
 */
export function receiptImageUrl(documentId: string, width?: number): string {
  return `${API_BASE}/receipts/${enc(documentId)}/image${width ? `?width=${width}` : ""}`;
}
