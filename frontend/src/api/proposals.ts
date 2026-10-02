// Product proposals (04, 2L): what a capture says a product is, waiting for a
// person. Values arrive as the API stores them: decimals as strings, a pack as
// {qty, unit}, a GTIN as its fourteen digits.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, ApiError, api } from "./client";
import { catalogKeys } from "./catalog";
import { inboxKey } from "./inbox";
import type { PhotoRole, ProductPhoto } from "./productPhotos";
import type { ErrorEnvelope } from "./types";

export type ProductKind = "branded" | "private_label" | "random_weight" | "loose" | "unbranded_vendor";

export const KIND_LABELS: Record<ProductKind, string> = {
  branded: "Branded",
  private_label: "Store brand",
  random_weight: "Weighed",
  loose: "Loose",
  unbranded_vendor: "Market stall",
};

export type ProposalStatus = "pending" | "accepted" | "rejected" | "superseded";
export type CaptureChannel = "clip" | "paste_url" | "barcode" | "photo" | "helper";
export type FieldSource =
  | "person"
  | "scan"
  | "manufacturer"
  | "usda_branded"
  | "page_data"
  | "adapter"
  | "page_meta"
  | "model"
  | "address";

export interface Pack {
  qty: string;
  unit: string;
}

export interface FieldCandidate {
  value: unknown;
  source: FieldSource;
  confidence: string | null;
}

export interface ProposalField extends FieldCandidate {
  alternatives: FieldCandidate[];
  conflict: boolean;
}

export interface MatchCandidate {
  product_id: string;
  name: string;
  brand: string | null;
  score: string;
}

export interface ProposalMatch {
  strong?: { product_id: string; reason: "identifier" | "listing" } | null;
  candidates?: MatchCandidate[];
  preselect?: string | null;
}

export interface ProposalVendor {
  id: string;
  name: string;
  price_scope: "chain" | "location";
  locations: { id: string; name: string }[];
  suggested_location_id: string | null;
}

export interface ProductJob {
  id: string;
  kind: string;
  status: "pending" | "running" | "done" | "failed";
  last_error: string | null;
}

export interface Proposal {
  id: string;
  kind: "new_product" | "product_update";
  status: ProposalStatus;
  product_id: string | null;
  capture: { id: string; channel: CaptureChannel; source_url: string | null; captured_at: string } | null;
  fields: Partial<Record<string, ProposalField>>;
  match: ProposalMatch;
  listing: { vendor_id: string; canonical_url: string; title?: string } | null;
  vendor: ProposalVendor | null;
  price: { amount: string; qty?: string; unit?: string; is_promo?: boolean } | null;
  photos: ProductPhoto[];
  jobs: ProductJob[];
  decided_at: string | null;
  result: { product_id?: string; superseded_by?: string } | null;
  created_at: string;
}

export interface ProposalSummary {
  id: string;
  kind: Proposal["kind"];
  status: ProposalStatus;
  title: string | null;
  brand: string | null;
  channel: CaptureChannel | null;
  has_conflict: boolean;
  created_at: string;
}

export interface AcceptInput {
  action: "new" | "update";
  product_id?: string;
  ingredient_id?: string;
  kind?: ProductKind;
  edits?: Record<string, unknown>;
  record_price?: boolean;
  vendor_location_id?: string;
  main_photo_id?: string;
  photo_roles?: Record<string, PhotoRole>;
  hidden_photo_ids?: string[];
}

export const SOURCE_BADGES: Record<FieldSource, string> = {
  person: "You",
  scan: "Scanned",
  manufacturer: "Manufacturer",
  usda_branded: "USDA",
  page_data: "Store page",
  adapter: "Store page",
  page_meta: "Store page",
  model: "Model's guess",
  address: "From the address",
};

export const CHANNEL_WORDS: Record<CaptureChannel, string> = {
  clip: "clipped from the page",
  paste_url: "from a pasted address",
  barcode: "scanned barcode",
  photo: "photographed",
  helper: "from the lookup helper",
};

export const proposalKeys = {
  all: ["proposals"] as const,
  pending: ["proposals", "pending"] as const,
  detail: (id: string) => ["proposals", "detail", id] as const,
};

/** Still reading: the identify job has not finished and nothing has been read yet. */
export function isReading(p: Proposal | undefined): boolean {
  return Boolean(p?.jobs.some((j) => j.kind === "identify" && (j.status === "pending" || j.status === "running")));
}

export function useProposal(id: string | undefined) {
  return useQuery({
    queryKey: proposalKeys.detail(id ?? ""),
    queryFn: () => api<Proposal>(`/product-proposals/${encodeURIComponent(id ?? "")}`),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const p = query.state.data;
      return isReading(p) || p?.photos.some((ph) => ph.status === "processing") ? 2000 : false;
    },
  });
}

export function usePendingProposals() {
  return useQuery({
    queryKey: proposalKeys.pending,
    queryFn: () => api<{ items: ProposalSummary[]; counts: Record<string, number> }>("/product-proposals"),
  });
}

function useAfterDecision(id: string) {
  const client = useQueryClient();
  return (updated: Proposal) => {
    client.setQueryData(proposalKeys.detail(id), updated);
    void client.invalidateQueries({ queryKey: proposalKeys.pending });
    void client.invalidateQueries({ queryKey: inboxKey });
    void client.invalidateQueries({ queryKey: catalogKeys.products });
  };
}

export function useAcceptProposal(id: string) {
  const done = useAfterDecision(id);
  return useMutation({
    mutationFn: (input: AcceptInput) =>
      api<Proposal>(`/product-proposals/${encodeURIComponent(id)}/accept`, { method: "POST", body: input }),
    onSuccess: done,
  });
}

export function useRejectProposal(id: string) {
  const done = useAfterDecision(id);
  return useMutation({
    mutationFn: () => api<Proposal>(`/product-proposals/${encodeURIComponent(id)}/reject`, { method: "POST" }),
    onSuccess: done,
  });
}

/** The product another product's code belongs to, from a 409 identifier_taken (PR6). */
export function takenBy(error: unknown): { product_id: string; name: string | null } | null {
  if (error instanceof ApiError && error.code === "identifier_taken" && error.details) {
    const { product_id, name } = error.details as { product_id?: string; name?: string | null };
    return product_id ? { product_id, name: name ?? null } : null;
  }
  return null;
}

/**
 * Send up to four photos of one product; `onProgress` gets 0–1 as they upload,
 * for the determinate bar (10). XHR, because fetch cannot report upload progress.
 */
export function photographProduct(
  photos: { file: File; role: PhotoRole }[],
  onProgress?: (fraction: number) => void,
): Promise<Proposal> {
  const form = new FormData();
  for (const { file, role } of photos) {
    form.append("photos", file, file.name);
    form.append("roles", role);
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/product-captures/photos`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new TypeError("Could not reach the server."));
    xhr.onload = () => {
      let payload: unknown;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
      } catch {
        payload = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as Proposal);
        return;
      }
      const err = (payload as ErrorEnvelope | undefined)?.error;
      reject(
        err && typeof err.code === "string"
          ? new ApiError(xhr.status, err.code, err.message, err.details)
          : new ApiError(xhr.status, "http_error", `Upload failed with status ${xhr.status}.`),
      );
    };
    xhr.send(form);
  });
}
