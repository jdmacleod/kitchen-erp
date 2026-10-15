// Naming new products in bulk from the to-identify queue (04, 2I; #88).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { IngredientCreateInput, IngredientMatch } from "./catalog";
import { api } from "./client";
import { invalidateObservations, invalidatePurchases, purchaseKeys } from "./purchases";

/** A waiting group with the product its wording suggests. */
export interface NamingRow {
  vendor: { id: string; name: string };
  raw_text_norm: string | null;
  line_count: number;
  raw_text: string | null;
  name: string;
  ingredient: IngredientMatch | null;
  pack_qty: string | null;
  pack_unit: string | null;
  /** The model's suggestion, once asked for (N2, N3). */
  model: {
    status: "asking" | "done" | "failed";
    name: string | null;
    ingredient: IngredientMatch | null;
  } | null;
}

export type NameProductRow = {
  vendor_id: string;
  raw_text_norm: string;
  name: string;
  pack_qty?: string;
  pack_unit?: string;
  /** Create it even though a product of the same name and ingredient exists (issue 179). */
  allow_duplicate?: boolean;
} & ({ ingredient_id: string } | { ingredient: IngredientCreateInput } | { product_id: string });

export interface NamedProduct {
  vendor_id: string;
  raw_text_norm: string;
  product_id: string | null;
  applied: number;
  /** `product_exists` carries the product with the same name and ingredient (issue 179). */
  error: {
    code: string;
    message: string;
    product?: { id: string; name: string; brand: string | null } | null;
  } | null;
}

export function useNamingRows(enabled: boolean) {
  return useQuery({
    queryKey: [...purchaseKeys.toIdentify, "naming"],
    queryFn: () => api<{ items: NamingRow[] }>("/to-identify/naming"),
    select: (data) => data.items,
    enabled,
    // The worker answers one line at a time; look again while any is waiting.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => r.model?.status === "asking") ? 3000 : false,
  });
}

/** Ask the model about the rows the wording couldn't name (N3). */
export function useSuggestNames() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: number }>("/to-identify/naming/suggest", { method: "POST" }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: [...purchaseKeys.toIdentify, "naming"] }),
  });
}

export function useNameProducts() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (rows: NameProductRow[]) =>
      api<{ results: NamedProduct[] }>("/to-identify/name-products", {
        method: "POST",
        body: { rows },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: purchaseKeys.toIdentify });
      void client.invalidateQueries({ queryKey: ["products"] });
      void client.invalidateQueries({ queryKey: ["ingredients"] });
      invalidatePurchases(client, "all");
      invalidateObservations(client);
    },
  });
}
