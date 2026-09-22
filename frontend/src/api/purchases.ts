// Phase 2A/2B wire types and hooks: price observations (shelf prices) and
// manual purchases. Decimals are strings end to end; timestamps are UTC ISO
// strings and are localized only for display.

import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { qs, validationMessages, type CanonicalUnit, type Page } from "./catalog";
import { api, isApiError, newIdempotencyKey } from "./client";
import type { PriceScope, VendorKind } from "./geo";

// --- types ------------------------------------------------------------------

export type NormStatus = "ok" | "no_density" | "unknown_measure" | "no_pack" | "no_qty";
export type ObservationSource = "receipt" | "manual" | "shelf" | "import";
export type PurchaseStatus = "draft" | "reviewed" | "committed";

export interface ObservationProduct {
  id: string;
  name: string;
  brand: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  ingredient: { id: string; name: string; canonical_unit: CanonicalUnit };
}

export interface ObservationLocation {
  id: string;
  name: string;
  vendor: { id: string; name: string; kind: VendorKind; price_scope: PriceScope };
}

export interface PriceNorm {
  status: NormStatus;
  canonical_qty: string | null;
  norm_unit: string | null;
  norm_unit_price: string | null;
  bridge_kind: string | null;
  bridge_source: string | null;
  bridge_confirmed: boolean | null;
}

export interface Observation {
  id: string;
  product: ObservationProduct;
  vendor_location: ObservationLocation;
  purchase_line_id: string | null;
  observed_at: string;
  price: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  source: ObservationSource;
  voided: boolean;
  norm: PriceNorm | null;
  created_at: string;
}

export interface PurchaseLineProduct {
  id: string;
  name: string;
  brand: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
}

export interface PurchaseLine {
  id: string;
  seq: number;
  raw_text: string | null;
  line_kind: string;
  product: PurchaseLineProduct | null;
  parent_line_id: string | null;
  qty: string | null;
  unit: string | null;
  unit_price: string | null;
  line_total: string | null;
  resolution: string | null;
  flags: string[];
  observation_id: string | null;
}

export interface PurchaseLocation {
  id: string;
  name: string;
  vendor: { id: string; name: string; kind: VendorKind };
}

export interface Purchase {
  id: string;
  vendor_location: PurchaseLocation;
  receipt_document_id: string | null;
  purchased_at: string;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  computed_total: string | null;
  status: PurchaseStatus;
  source: ObservationSource;
  flags: string[];
  lines: PurchaseLine[];
  created_at: string;
  updated_at: string;
}

// --- inputs -----------------------------------------------------------------

export interface ObservationCreateInput {
  product_id: string;
  vendor_location_id: string;
  price: string;
  qty: string;
  unit: string;
  is_promo?: boolean;
  observed_at?: string;
}

export interface ObservationFilters {
  product_id?: string;
  vendor_location_id?: string;
  include_voided?: boolean;
}

/** Exactly one of unit_price and line_total; the server computes the other. */
export interface PurchaseLineInput {
  product_id: string;
  qty: string;
  unit: string;
  unit_price?: string;
  line_total?: string;
}

export interface PurchaseInput {
  vendor_location_id: string;
  purchased_at: string;
  total?: string;
  lines: PurchaseLineInput[];
}

export interface PurchaseFilters {
  vendor_location_id?: string;
  status?: PurchaseStatus | "";
}

// --- errors -----------------------------------------------------------------

const knownMessages: Record<string, string> = {
  line_price_ambiguous: "Give each line either a unit price or a line total, not both.",
  line_price_missing: "Each line needs a unit price or a line total.",
  location_inactive: "That location is inactive; pick another.",
  purchase_not_manual: "Only manual purchases can be edited here.",
};

/** A message for a purchase or observation mutation error. */
export function purchaseErrorMessage(e: unknown): string {
  if (isApiError(e)) {
    const known = knownMessages[e.code];
    if (known) return known;
    const details = validationMessages(e);
    if (details.length > 0) return details.join(" ");
    return e.message;
  }
  if (e instanceof TypeError) return "Could not reach the server.";
  if (e instanceof Error && e.message) return e.message;
  return "Something went wrong.";
}

const idem = () => ({ "Idempotency-Key": newIdempotencyKey() });
const enc = encodeURIComponent;

// --- keys -------------------------------------------------------------------

export const purchaseKeys = {
  observations: ["price-observations"] as const,
  observationList: (filters: ObservationFilters) => ["price-observations", "list", filters] as const,
  purchases: ["purchases"] as const,
  purchaseList: (filters: PurchaseFilters) => ["purchases", "list", filters] as const,
  purchase: (id: string) => ["purchases", "detail", id] as const,
  lastUnit: (productId: string) => ["products", "last-purchase-unit", productId] as const,
};

function invalidateObservations(client: QueryClient) {
  void client.invalidateQueries({ queryKey: purchaseKeys.observations });
}

function invalidatePurchases(client: QueryClient) {
  void client.invalidateQueries({ queryKey: [...purchaseKeys.purchases, "list"] });
}

// --- price observations -----------------------------------------------------

export function useObservations(filters: ObservationFilters, limit = 50, enabled = true) {
  return useInfiniteQuery({
    queryKey: purchaseKeys.observationList(filters),
    queryFn: ({ pageParam }) =>
      api<Page<Observation>>(
        `/price-observations${qs({
          product_id: filters.product_id,
          vendor_location_id: filters.vendor_location_id,
          include_voided: filters.include_voided,
          limit,
          cursor: pageParam,
        })}`,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
  });
}

export function useCreateObservation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ObservationCreateInput) =>
      api<Observation>("/price-observations", { method: "POST", body: input, headers: idem() }),
    onSuccess: () => invalidateObservations(client),
  });
}

export function useVoidObservation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api<Observation>(`/price-observations/${enc(id)}/void`, { method: "POST", body: { reason } }),
    onSuccess: () => invalidateObservations(client),
  });
}

// --- purchases --------------------------------------------------------------

export function usePurchases(filters: PurchaseFilters, limit = 50) {
  return useInfiniteQuery({
    queryKey: purchaseKeys.purchaseList(filters),
    queryFn: ({ pageParam }) =>
      api<Page<Purchase>>(
        `/purchases${qs({
          vendor_location_id: filters.vendor_location_id,
          status: filters.status,
          limit,
          cursor: pageParam,
        })}`,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

export function usePurchase(id: string | undefined) {
  return useQuery({
    queryKey: purchaseKeys.purchase(id ?? ""),
    queryFn: () => api<Purchase>(`/purchases/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export function useCreatePurchase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: PurchaseInput) => api<Purchase>("/purchases", { method: "POST", body: input, headers: idem() }),
    onSuccess: (created) => {
      client.setQueryData(purchaseKeys.purchase(created.id), created);
      invalidatePurchases(client);
      invalidateObservations(client);
    },
  });
}

export function useUpdatePurchase(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: PurchaseInput) => api<Purchase>(`/purchases/${enc(id)}`, { method: "PUT", body: input }),
    onSuccess: (updated) => {
      client.setQueryData(purchaseKeys.purchase(id), updated);
      invalidatePurchases(client);
      invalidateObservations(client);
    },
  });
}

/**
 * The unit a product was last bought in, or null when it has never been
 * bought or the endpoint is missing. Used only as a form default.
 */
export async function fetchLastPurchaseUnit(productId: string): Promise<string | null> {
  try {
    const result = await api<{ unit: string | null }>(`/products/${enc(productId)}/last-purchase-unit`);
    return result?.unit ?? null;
  } catch (e) {
    if (isApiError(e) && e.status === 404) return null;
    throw e;
  }
}

// --- formatting -------------------------------------------------------------

export const purchaseStatusLabel: Record<PurchaseStatus, string> = {
  draft: "Draft",
  reviewed: "Reviewed",
  committed: "Committed",
};

export const sourceLabel: Record<ObservationSource, string> = {
  receipt: "Receipt",
  manual: "Manual",
  shelf: "Shelf",
  import: "Import",
};

/** Item lines only: discounts, tax, and other non-item kinds are excluded. */
export function itemLines(purchase: Purchase): PurchaseLine[] {
  return purchase.lines.filter((l) => l.line_kind === "item");
}
