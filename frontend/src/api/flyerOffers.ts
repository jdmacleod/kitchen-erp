import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

const queueKey = ["flyer-offers", "queue"] as const;

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
      void client.invalidateQueries({ queryKey: inboxKey });
    },
  });
}
