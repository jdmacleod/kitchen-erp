// Phase 2E wire types and hooks: price history, offers, the comparison matrix,
// the map's cheapest layer, the location price panel, and the needs-a-bridge
// list. Prices are strings; they are turned into numbers only for chart geometry.

import { useQuery } from "@tanstack/react-query";
import { qs, type CanonicalUnit } from "./catalog";
import { api } from "./client";
import type { PriceScope, VendorKind } from "./geo";
import type { NormStatus, ObservationSource } from "./purchases";

// --- types ------------------------------------------------------------------

export interface PricePoint {
  observation_id: string;
  observed_at: string;
  price: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  source: ObservationSource;
  norm_unit_price: string | null;
  norm_unit: string | null;
  norm_status: NormStatus | null;
  location_id: string;
  location_name: string;
  vendor_id: string;
  vendor_name: string;
  price_scope: PriceScope;
  /** Vendor id for chain-scoped vendors, location id otherwise. */
  series: string;
}

export interface LatestPrice {
  location_id: string;
  location_name: string;
  vendor_id: string;
  vendor_name: string;
  price_scope: PriceScope;
  observation_id: string;
  observed_at: string;
  price: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  norm_unit_price: string | null;
  norm_unit: string | null;
  norm_status: NormStatus | null;
  /** Whole days as a decimal string (the API serializes every number this way). */
  age_days: string | number;
  stale: boolean;
}

export interface ProductPrices {
  points: PricePoint[];
  latest: LatestPrice[];
}

/** Days before a price counts as stale, keyed by perishability. */
export type StaleThresholds = Record<string, number>;

export interface Offer {
  product_id: string;
  product_name: string;
  brand: string | null;
  quality_rating: number | null;
  pack_qty: string | null;
  pack_unit: string | null;
  location_id: string;
  location_name: string;
  vendor_id: string;
  vendor_name: string;
  price_scope: PriceScope;
  observation_id: string;
  observed_at: string;
  price: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  norm_unit_price: string | null;
  norm_unit: string | null;
  norm_status: NormStatus | null;
  /** Whole days as a decimal string (the API serializes every number this way). */
  age_days: string | number;
  stale: boolean;
}

export interface OfferList {
  items: Offer[];
  stale_thresholds: StaleThresholds;
}

export interface CompareCell {
  product_id: string;
  product_name: string;
  brand: string | null;
  quality_rating: number | null;
  location_id: string;
  location_name: string;
  observation_id: string;
  observed_at: string;
  is_promo: boolean;
  norm_unit_price: string;
  norm_unit: string;
  stale: boolean;
  cheapest: boolean;
  age_days?: string | number;
}

export interface CompareRow {
  ingredient_id: string;
  ingredient_name: string;
  canonical_unit: CanonicalUnit;
  /** A missing vendor key means nothing is known: render blank, never zero. */
  cells: Record<string, CompareCell>;
}

export interface CompareResult {
  vendors: { id: string; name: string }[];
  rows: CompareRow[];
  stale_thresholds: StaleThresholds;
}

export interface PricePanel {
  last_visit: string | null;
  spend: string | null;
  visits: number;
  period_days: number;
  recent: {
    observation_id: string;
    observed_at: string;
    price: string;
    qty: string;
    unit: string;
    is_promo: boolean;
    norm_unit_price: string | null;
    norm_unit: string | null;
    norm_status: NormStatus | null;
    product_id: string;
    product_name: string;
    brand: string | null;
  }[];
}

export interface CheapestItem {
  location_id: string;
  location_name: string;
  lat: string;
  lon: string;
  vendor_id: string;
  vendor_name: string;
  kind: VendorKind;
  product_id: string;
  product_name: string;
  quality_rating: number | null;
  observation_id: string;
  observed_at: string;
  is_promo: boolean;
  norm_unit_price: string;
  norm_unit: string;
  stale: boolean;
}

export interface CheapestResult {
  items: CheapestItem[];
  unit: string | null;
}

export interface NeedsBridgeItem {
  ingredient: { id: string; name: string; canonical_unit: CanonicalUnit };
  product: { id: string; name: string; brand: string | null; pack_qty: string | null; pack_unit: string | null };
  status: NormStatus;
  observation_count: number;
  latest_observed_at: string;
}

export interface PriceFilters {
  min_quality?: number | "";
  exclude_stale?: boolean;
  exclude_promo?: boolean;
}

export interface CompareInput extends PriceFilters {
  ingredient_ids: string[];
}

// --- keys and hooks ---------------------------------------------------------

const enc = encodeURIComponent;

export const priceKeys = {
  productPrices: (id: string) => ["price-book", "product", id] as const,
  offers: (id: string, f: PriceFilters) => ["price-book", "offers", id, f] as const,
  compare: (input: CompareInput) => ["price-book", "compare", input] as const,
  panel: (id: string, days: number) => ["price-book", "panel", id, days] as const,
  cheapest: (ingredientId: string, f: PriceFilters) => ["price-book", "cheapest", ingredientId, f] as const,
  needsBridge: ["price-book", "needs-bridge"] as const,
};

export function useProductPrices(productId: string | undefined) {
  return useQuery({
    queryKey: priceKeys.productPrices(productId ?? ""),
    queryFn: () => api<ProductPrices>(`/products/${enc(productId ?? "")}/prices`),
    enabled: Boolean(productId),
  });
}

export function useIngredientOffers(ingredientId: string | undefined, filters: PriceFilters) {
  return useQuery({
    queryKey: priceKeys.offers(ingredientId ?? "", filters),
    queryFn: () =>
      api<OfferList>(
        `/ingredients/${enc(ingredientId ?? "")}/offers${qs({
          min_quality: filters.min_quality,
          exclude_stale: filters.exclude_stale,
          exclude_promo: filters.exclude_promo,
        })}`,
      ),
    enabled: Boolean(ingredientId),
    placeholderData: (previous) => previous,
  });
}

/** A read that happens to be a POST: the ingredient set is the query key. */
export function useCompare(input: CompareInput) {
  return useQuery({
    queryKey: priceKeys.compare(input),
    queryFn: () => {
      const body: Record<string, unknown> = { ingredient_ids: input.ingredient_ids };
      if (input.min_quality) body.min_quality = input.min_quality;
      if (input.exclude_stale) body.exclude_stale = true;
      if (input.exclude_promo) body.exclude_promo = true;
      return api<CompareResult>("/price-book/compare", { method: "POST", body });
    },
    enabled: input.ingredient_ids.length > 0,
    placeholderData: (previous) => previous,
  });
}

export function useLocationPricePanel(locationId: string | undefined, days: number) {
  return useQuery({
    queryKey: priceKeys.panel(locationId ?? "", days),
    queryFn: () => api<PricePanel>(`/vendor-locations/${enc(locationId ?? "")}/price-panel${qs({ days })}`),
    enabled: Boolean(locationId),
    placeholderData: (previous) => previous,
  });
}

export function useCheapest(ingredientId: string | undefined, filters: PriceFilters) {
  return useQuery({
    queryKey: priceKeys.cheapest(ingredientId ?? "", filters),
    queryFn: () =>
      api<CheapestResult>(
        `/price-book/cheapest${qs({ ingredient_id: ingredientId, min_quality: filters.min_quality, exclude_stale: filters.exclude_stale })}`,
      ),
    enabled: Boolean(ingredientId),
    placeholderData: (previous) => previous,
  });
}

export function useNeedsBridge() {
  return useQuery({
    queryKey: priceKeys.needsBridge,
    queryFn: () => api<{ items: NeedsBridgeItem[] }>("/price-book/needs-bridge"),
    select: (data) => data.items,
  });
}

// --- formatting -------------------------------------------------------------

/** "today", "1 day", "12 days"; from age_days when given, else from the timestamp. */
export function formatAge(ageDays: string | number | null | undefined, observedAt?: string): string {
  // Age is a whole number of days for display only, never money; Number() is safe here.
  let days: number | null = ageDays === null || ageDays === undefined || ageDays === "" ? null : Math.floor(Number(ageDays));
  if (days !== null && !Number.isFinite(days)) days = null;
  if (days === null && observedAt) {
    const then = new Date(observedAt).getTime();
    if (!Number.isNaN(then)) days = Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
  }
  if (days === null) return "";
  if (days === 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

/** Where a failed normalization is fixed: the ingredient's bridge editor or the product's pack. */
export function bridgeFixLink(status: NormStatus, ingredientId: string, productId: string): { to: string; text: string } {
  switch (status) {
    case "no_density":
      return { to: `/ingredients/${enc(ingredientId)}#density-heading`, text: "Add a density" };
    case "unknown_measure":
      return { to: `/ingredients/${enc(ingredientId)}#measures-heading`, text: "Add the measure" };
    case "no_pack":
      return { to: `/products/${enc(productId)}`, text: "Set the pack" };
    default:
      return { to: `/products/${enc(productId)}`, text: "Check the product" };
  }
}

export const normStatusText: Record<NormStatus, string> = {
  ok: "normalized",
  no_density: "no density",
  unknown_measure: "unknown measure",
  no_pack: "no pack size",
  no_qty: "no quantity",
};
