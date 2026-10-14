import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** One side of a possible duplicate ingredient pair (issue 346). */
export interface IngredientDuplicateSide {
  id: string;
  name: string;
  products: number;
  /** Linked to the standard list. */
  standard: boolean;
}

export interface IngredientDuplicatePair {
  a: IngredientDuplicateSide;
  b: IngredientDuplicateSide;
  /** "same USDA food", "same standard entry", "same name", "similar name". */
  reasons: string[];
  /** The suggested one to keep. */
  keep_id: string;
}

const duplicatesKey = ["ingredients", "duplicates"] as const;

export function useIngredientDuplicates() {
  return useQuery({
    queryKey: duplicatesKey,
    queryFn: () => api<{ items: IngredientDuplicatePair[] }>("/ingredients/duplicates"),
    select: (data) => data.items,
  });
}

/** "Not the same": the pair is never offered again. */
export function useMarkIngredientsDistinct() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (pair: { a: string; b: string }) =>
      api<unknown>("/ingredients/duplicates/distinct", { method: "POST", body: pair }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: duplicatesKey });
      void client.invalidateQueries({ queryKey: ["inbox"] });
    },
  });
}
