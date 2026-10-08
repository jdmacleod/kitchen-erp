// Product proposals (04, 2L): what a capture says a product is, waiting for a
// person. Values arrive as the API stores them: decimals as strings, a pack as
// {qty, unit}, a GTIN as its fourteen digits.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, ApiError, api } from "./client";
import { catalogKeys } from "./catalog";
import { inboxKey } from "./inbox";
import type { PhotoRole, PhotoSummary, ProductPhoto } from "./productPhotos";
import type { CategoryKey } from "../components/CategoryChip";
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
  /** "helper" when the products helper brought it: the badge "Lookup helper" (2N). */
  via?: string | null;
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

export type Sameness = "same" | "other_size" | "variant" | "similar";

/** A fuzzy candidate as review shows it (2P): the product now, and its verdict. */
export interface ReviewCandidate {
  product_id: string;
  name: string;
  brand: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  pack_count?: number | null;
  piece_name?: string | null;
  photo: PhotoSummary | null;
  ingredient: { id: string; name: string; category_key?: CategoryKey | null };
  score: string;
  verdict: Sameness;
  reasons: string[];
  /** The identifying words only this proposal has, and only the product has. */
  only_here: string[];
  only_there: string[];
}

/** The catalog product an address already names (2P): "Already in your catalog". */
export interface KnownProduct {
  product_id: string;
  name: string;
  reason: "identifier" | "listing";
}

export interface ProposalMatch {
  strong?: { product_id: string; reason: "identifier" | "listing" | "lookup" } | null;
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
  /** The match's fuzzy candidates, ordered by verdict then similarity (2P). */
  candidates?: ReviewCandidate[];
  /** Other pending proposals that are likely the same product (2P). */
  look_alikes?: { id: string; title: string | null }[];
  listing: { vendor_id: string; canonical_url: string; title?: string } | null;
  vendor: ProposalVendor | null;
  price: { amount: string; qty?: string; unit?: string; is_promo?: boolean } | null;
  photos: ProductPhoto[];
  jobs: ProductJob[];
  /** How the capture was identified (criterion 81). */
  reading?: { path: "barcode" | "vision" | "ocr_text" | "unread" | "page"; error: string | null } | null;
  /** The latest "Look this up online" request (2N). */
  lookup?: { status: "open" | "answered" | "closed"; created_at: string; answered_at: string | null } | null;
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
  /** Other pending proposals that are likely the same product (2P). */
  look_alikes?: string[];
  created_at: string;
}

/** A price for a quantity: 0.69 for 1 lb. A bare amount is for 1 each. */
export interface PriceBasis {
  amount: string;
  qty: string;
  unit: string;
}

export interface AcceptInput {
  action: "new" | "update";
  product_id?: string;
  ingredient_id?: string;
  kind?: ProductKind;
  edits?: Record<string, unknown>;
  record_price?: boolean;
  /** The reviewer's posted price and what it is for, over the page's. */
  price?: PriceBasis;
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

/** The evidence column's line about how a capture was read. */
export function readingWords(reading: Proposal["reading"]): string | null {
  if (!reading) return null;
  if (reading.error === "model_unavailable") return "The model couldn't be reached, so nothing was read.";
  if (reading.error) return "The model's answer couldn't be used, so nothing was read.";
  if (reading.path === "barcode") return "Read from the barcode in the photo.";
  if (reading.path === "vision") return "Read from the photo by the vision model.";
  if (reading.path === "ocr_text") return "Read from the label text by the model.";
  if (reading.path === "page") return "Read from the page.";
  return null;
}

// --- the products helper (2N) -----------------------------------------------------------

/** Whether "Look this up online" is offered: a products helper token exists (PD8). */
export function useProductsHelper() {
  return useQuery({
    queryKey: ["products-helper"],
    queryFn: () => api<{ configured: boolean }>("/products-helper"),
    staleTime: 60_000,
  });
}

export function useLookUp(proposalId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api(`/product-proposals/${encodeURIComponent(proposalId)}/look-up`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: proposalKeys.detail(proposalId) }),
  });
}

/** How long a lookup may wait before it reads as overdue: Home's stall threshold. */
export const LOOKUP_OVERDUE_MINUTES = 10;

export interface PriceChange {
  id: string;
  amount: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  seen_at: string;
  listing_id: string;
  title: string;
  canonical_url: string;
  product_id: string;
  product_name: string;
  vendor_id: string;
  vendor_name: string;
  created_at: string;
}

const priceChangesKey = ["listing-price-changes"] as const;

export function usePriceChanges() {
  return useQuery({ queryKey: priceChangesKey, queryFn: () => api<{ items: PriceChange[] }>("/listing-price-changes") });
}

export function useDecidePriceChange() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, accept, vendor_location_id }: { id: string; accept: boolean; vendor_location_id?: string }) =>
      api(`/listing-price-changes/${encodeURIComponent(id)}/${accept ? "accept" : "reject"}`, {
        method: "POST",
        body: accept ? { vendor_location_id } : undefined,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: priceChangesKey });
      void client.invalidateQueries({ queryKey: inboxKey });
    },
  });
}
