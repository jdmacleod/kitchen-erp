import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { qs } from "./catalog";
import { api } from "./client";
import { inboxKey } from "./inbox";

/** The offer queue (18, 2U-3): suggested flyer offers, one group per vendor and printed name. */

export type PriceBasis =
  "each" | "per_lb" | "per_oz" | "multi" | "bogo" | "percent_off" | "dollar_off";

export interface OfferSuggestion {
  kind: "alias" | "fuzzy" | "llm" | "similar";
  product_id: string | null;
  ignore: boolean;
  label: string;
  score: string | null;
  brand_note: string | null;
}

export interface QueuedOffer {
  id: string;
  location: { id: string; name: string };
  name_as_printed: string;
  price_basis: PriceBasis;
  price: string | null;
  basis_qty: string | null;
  discount: string | null;
  size_qty: string | null;
  size_unit: string | null;
  requires_loyalty: boolean;
  limit_qty: string | null;
  valid_from: string;
  valid_to: string;
}

export interface OfferGroup {
  vendor: { id: string; name: string };
  name_norm: string;
  name_as_printed: string;
  watched: boolean;
  suggestions: OfferSuggestion[];
  offers: QueuedOffer[];
  first_seen_at: string;
}

/** Watched offers ask a person; the other offers wait quietly (F13). */
export interface OfferQueue {
  watched: OfferGroup[];
  other: OfferGroup[];
}

export type OfferDecision =
  | { vendor_id: string; name_norm: string; product_id: string }
  | { vendor_id: string; name_norm: string; ignore: true };

/** A linked offer valid today at one of the kitchen's stores (18, 2U-4; UI-9.1). */
export interface CurrentOffer extends QueuedOffer {
  product: { id: string; name: string; ingredient_id: string | null };
  vendor: { id: string; name: string };
  size_inherited: boolean;
  ad_week_from: string | null;
  ad_week_to: string | null;
  /** Per canonical unit; null when the offer can only be shown as printed. */
  unit_price: string | null;
  unit: string | null;
  regular_unit_price: string | null;
}

/** A product's linked offer in its own week, priced as the product's prices are shown (X3). */
export interface HistoryOffer extends Omit<CurrentOffer, "regular_unit_price"> {
  display_unit_price: string | null;
  display_unit: string | null;
}

export interface OfferHistory {
  items: HistoryOffer[];
  /** The median of regular and offer prices over the last `weeks`; null with none. */
  typical: {
    display_unit_price: string;
    display_unit: string;
    weeks: number;
    regular_count: number;
    offer_count: number;
  } | null;
}

const queueKey = ["flyer-offers", "queue"] as const;
const currentKey = (homeBaseId: string) => ["flyer-offers", "current", homeBaseId] as const;

/** The kitchen's current offers, ordered by vendor, location and product. */
export function useCurrentOffers(homeBaseId: string | null | undefined) {
  return useQuery({
    queryKey: currentKey(homeBaseId ?? ""),
    queryFn: () =>
      api<{ items: CurrentOffer[] }>(
        `/flyer-offers/current${qs({ home_base_id: homeBaseId ?? "" })}`,
      ).then((r) => r.items),
    enabled: Boolean(homeBaseId),
  });
}

export function useOfferQueue() {
  return useQuery({
    queryKey: queueKey,
    queryFn: () => api<OfferQueue>("/flyer-offers/queue"),
  });
}

export function useDecideOffer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: OfferDecision) =>
      api<{ decided: number }>("/flyer-offers/decide", { method: "POST", body: input }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queueKey });
      // A match links this week's offers, so the kitchen's current ones change.
      void client.invalidateQueries({ queryKey: ["flyer-offers", "current"] });
      void client.invalidateQueries({ queryKey: inboxKey });
    },
  });
}

/** A product's offers, newest week first, and its typical price (X3). */
export function useOfferHistory(productId: string) {
  return useQuery({
    queryKey: ["flyer-offers", "product", productId] as const,
    queryFn: () => api<OfferHistory>(`/products/${encodeURIComponent(productId)}/flyer-offers`),
  });
}
