import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CanonicalUnit, Ingredient } from "./catalog";
import { api } from "./client";

/** USDA suggestions for linked ingredients, reviewed on Needs a bridge (03 and 10, 1G). */

export interface DensityOffer {
  portion_id: string;
  portion_label: string;
  gram_weight: string;
  density_g_per_ml: string;
}

export interface MeasureOffer {
  label: string;
  canonical_qty: string;
  from_portion: string;
}

export interface UsdaReviewGroup {
  ingredient_id: string;
  name: string;
  canonical_unit: CanonicalUnit;
  fdc_id: number;
  usda_description: string | null;
  has_density: boolean;
  densities: DensityOffer[];
  measures: MeasureOffer[];
}

export interface UsdaReview {
  loaded: boolean;
  release_date: string | null;
  groups: UsdaReviewGroup[];
}

export interface UsdaDecision {
  density_portion_id?: string;
  measures?: string[];
  skip?: boolean;
  replace_density?: boolean;
}

/** What a 409 density_exists carries: the density set since the list was read. */
export interface DensityExists {
  density_g_per_ml: string;
  density_source: string | null;
  density_confirmed: boolean;
}

export const usdaReviewKey = ["usda", "review"] as const;

export function useUsdaReview() {
  return useQuery({ queryKey: usdaReviewKey, queryFn: () => api<UsdaReview>("/usda/review") });
}

export function useUsdaDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ingredientId, ...body }: UsdaDecision & { ingredientId: string }) =>
      api<{ saved: number; ingredient: Ingredient }>(`/usda/review/${encodeURIComponent(ingredientId)}`, { method: "POST", body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: usdaReviewKey });
      void client.invalidateQueries({ queryKey: ["ingredients"] });
      void client.invalidateQueries({ queryKey: ["price-book"] });
    },
  });
}
