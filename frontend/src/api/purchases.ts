// Phase 2A/2B wire types and hooks: price observations (shelf prices) and
// manual purchases. Decimals are strings end to end; timestamps are UTC ISO
// strings and are localized only for display.

import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { qs, validationMessages, type CanonicalUnit, type Page, type ProductCreateInput } from "./catalog";
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

export type Resolution = "barcode" | "alias" | "fuzzy" | "llm" | "manual" | "unmatched" | "ignored";
export type SuggestionKind = "alias_unconfirmed" | "fuzzy" | "llm";
export type AcceptedKind = "alias" | "fuzzy" | "llm";

export interface Suggestion {
  kind: SuggestionKind;
  product_id: string | null;
  ignore: boolean;
  label: string;
  score: string;
}

export const LINE_KINDS = ["item", "discount", "deposit", "tax", "fee"] as const;

export interface PurchaseLine {
  id: string;
  seq: number;
  raw_text: string | null;
  raw_text_norm?: string | null;
  line_kind: string;
  product: PurchaseLineProduct | null;
  parent_line_id: string | null;
  qty: string | null;
  unit: string | null;
  unit_price: string | null;
  line_total: string | null;
  resolution: Resolution | string | null;
  resolved_by?: string | null;
  resolution_confidence?: string | null;
  flags: string[];
  suggestions?: Suggestion[];
  observation_id: string | null;
}

export interface PurchaseLocation {
  id: string;
  name: string;
  vendor: { id: string; name: string; kind: VendorKind };
}

export interface Purchase {
  id: string;
  /** Null while a receipt's location is still unknown; commit needs one. */
  vendor_location: PurchaseLocation | null;
  receipt_document_id: string | null;
  purchased_at: string;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  computed_total: string | null;
  status: PurchaseStatus;
  source: ObservationSource;
  flags: string[];
  ledger_txn_ref?: string | null;
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

/** Exactly one of product_id, ignore, or product. */
export type ResolveInput = ({ product_id: string } | { ignore: true } | { product: ProductCreateInput }) & {
  accepted_kind?: AcceptedKind;
};

export interface LinePatchInput {
  raw_text?: string;
  line_kind?: string;
  qty?: string;
  unit?: string;
  unit_price?: string;
  line_total?: string;
  parent_line_id?: string;
  clear_parent?: true;
  clear_qty?: true;
}

export interface LineAddInput {
  raw_text?: string;
  line_kind: string;
  qty?: string;
  unit?: string;
  unit_price?: string;
  line_total: string;
  parent_line_id?: string;
  product_id?: string;
  after_seq?: number;
}

export interface PurchaseHeaderInput {
  vendor_location_id?: string;
  purchased_at?: string;
  subtotal?: string;
  tax?: string;
  total?: string;
  ledger_txn_ref?: string;
}

export interface ToIdentifyLine {
  line_id: string;
  purchase_id: string;
  raw_text: string | null;
  purchased_at: string;
  line_total: string | null;
  qty: string | null;
  unit: string | null;
}

export interface ToIdentifyGroup {
  vendor: { id: string; name: string };
  raw_text_norm: string | null;
  line_count: number;
  lines: ToIdentifyLine[];
}

export type ToIdentifyApplyInput = { vendor_id: string; raw_text_norm: string; line_ids?: string[] } & (
  | { product_id: string }
  | { ignore: true }
);

// --- errors -----------------------------------------------------------------

const knownMessages: Record<string, string> = {
  line_price_ambiguous: "Give each line either a unit price or a line total, not both.",
  line_price_missing: "Each line needs a unit price or a line total.",
  location_inactive: "That location is inactive; pick another.",
  purchase_not_manual: "Only manual purchases can be edited here.",
  committed: "This purchase is committed; reopen it to change it.",
  location_required: "Choose a location before committing.",
  not_committed: "Only a committed purchase can be reopened.",
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
  toIdentify: ["to-identify"] as const,
};

function invalidateObservations(client: QueryClient) {
  void client.invalidateQueries({ queryKey: purchaseKeys.observations });
}

/**
 * Clear the purchase caches after a mutation. The single place that decides
 * what a purchase mutation invalidates, so a key added here reaches every
 * caller (issue #19); two call sites used to invalidate by hand and would have
 * silently missed it.
 *
 * "lists" is enough for a mutation that already wrote the detail it changed
 * back into the cache, which `usePurchaseMutation` does. "all" is for one that
 * changes purchases it does not hold: creating one, or applying an
 * identification across every purchase with a line that matches.
 */
export function invalidatePurchases(client: QueryClient, scope: "lists" | "all" = "lists") {
  void client.invalidateQueries({
    queryKey: scope === "all" ? purchaseKeys.purchases : [...purchaseKeys.purchases, "list"],
  });
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

/**
 * The household's most recent committed purchases — and, from the same answer,
 * whether it has recorded any at all.
 *
 * `status=committed` is load-bearing, not tidiness. Receipt parsing inserts a
 * `draft` purchase the moment a photo is read, before anyone has looked at it,
 * so an unfiltered probe would report a household as set up on the strength of
 * an upload nobody reviewed. Committed is also exactly what the price book
 * contains, so this is the same bar the rest of the app uses.
 *
 * One query, two consumers: the home page's checklist ticks on `length > 0` and
 * its "Lately" section renders the same rows. Keyed under
 * `["purchases", "list", …]` so `invalidatePurchases` already reaches it by
 * prefix — there is no separate invalidation for a future call site to forget.
 */
export function useRecentCommittedPurchases(limit = 5) {
  return useQuery({
    queryKey: [...purchaseKeys.purchases, "list", "committed", limit] as const,
    queryFn: () => api<Page<Purchase>>(`/purchases${qs({ status: "committed", limit })}`),
    select: (page) => page.items,
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

/** Every mutation on a purchase returns the whole purchase; cache it and refresh the lists. */
function usePurchaseMutation<I>(id: string, call: (input: I) => Promise<Purchase>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: (updated) => {
      client.setQueryData(purchaseKeys.purchase(id), updated);
      invalidatePurchases(client);
      invalidateObservations(client);
      void client.invalidateQueries({ queryKey: purchaseKeys.toIdentify });
    },
  });
}

// --- review (2D) ------------------------------------------------------------

export function useResolveLine(purchaseId: string) {
  return usePurchaseMutation(purchaseId, ({ lineId, ...input }: ResolveInput & { lineId: string }) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}/lines/${enc(lineId)}/resolve`, { method: "POST", body: input }),
  );
}

export function useReResolveLine(purchaseId: string) {
  return usePurchaseMutation(purchaseId, (lineId: string) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}/lines/${enc(lineId)}/re-resolve`, { method: "POST" }),
  );
}

export function usePatchLine(purchaseId: string) {
  return usePurchaseMutation(purchaseId, ({ lineId, ...input }: LinePatchInput & { lineId: string }) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}/lines/${enc(lineId)}`, { method: "PATCH", body: input }),
  );
}

export function useAddLine(purchaseId: string) {
  return usePurchaseMutation(purchaseId, (input: LineAddInput) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}/lines`, { method: "POST", body: input }),
  );
}

export function useDeleteLine(purchaseId: string) {
  return usePurchaseMutation(purchaseId, (lineId: string) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}/lines/${enc(lineId)}`, { method: "DELETE" }),
  );
}

export function usePatchPurchase(purchaseId: string) {
  return usePurchaseMutation(purchaseId, (input: PurchaseHeaderInput) =>
    api<Purchase>(`/purchases/${enc(purchaseId)}`, { method: "PATCH", body: input }),
  );
}

export function useCommitPurchase(purchaseId: string) {
  return usePurchaseMutation(purchaseId, () => api<Purchase>(`/purchases/${enc(purchaseId)}/commit`, { method: "POST" }));
}

export function useReopenPurchase(purchaseId: string) {
  return usePurchaseMutation(purchaseId, () => api<Purchase>(`/purchases/${enc(purchaseId)}/reopen`, { method: "POST" }));
}

// --- to-identify queue ------------------------------------------------------

export function useToIdentify() {
  return useQuery({
    queryKey: purchaseKeys.toIdentify,
    queryFn: () => api<{ items: ToIdentifyGroup[] }>("/to-identify"),
    select: (data) => data.items,
  });
}

export function useApplyToIdentify() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ToIdentifyApplyInput) => api<{ applied: number }>("/to-identify/apply", { method: "POST", body: input }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: purchaseKeys.toIdentify });
      // "all": this applies a product to lines in purchases this hook never
      // loaded, so their cached details are stale too, not just the lists.
      invalidatePurchases(client, "all");
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

/** Badge tone per status (spec 08): a draft waits on a person, reviewed is neutral, committed is done. */
export const purchaseStatusTone: Record<PurchaseStatus, "warn" | "neutral" | "good"> = {
  draft: "warn",
  reviewed: "neutral",
  committed: "good",
};

export const resolutionLabel: Record<Resolution, string> = {
  barcode: "barcode",
  alias: "alias",
  fuzzy: "fuzzy alias",
  llm: "model",
  manual: "chosen",
  unmatched: "unmatched",
  ignored: "ignored",
};

/** Lines a reviewer need not look at: resolved automatically and unflagged. */
export function isQuietLine(line: PurchaseLine): boolean {
  return (line.resolution === "alias" || line.resolution === "barcode") && line.flags.length === 0;
}

export const sourceLabel: Record<ObservationSource, string> = {
  receipt: "Receipt",
  manual: "Manual",
  shelf: "Shelf",
  import: "Import",
};

/** "Vendor — Location", or just the name when they coincide; "No location yet" when unset. */
export function purchaseLocationLabel(purchase: Pick<Purchase, "vendor_location">): string {
  const l = purchase.vendor_location;
  if (!l) return "No location yet";
  return l.name === l.vendor.name ? l.name : `${l.vendor.name} — ${l.name}`;
}

/** Item lines only: discounts, tax, and other non-item kinds are excluded. */
export function itemLines(purchase: Purchase): PurchaseLine[] {
  return purchase.lines.filter((l) => l.line_kind === "item");
}
