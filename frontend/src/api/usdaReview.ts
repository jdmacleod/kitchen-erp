import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CanonicalUnit, Ingredient } from "./catalog";
import { api } from "./client";

/** USDA suggestions for linked ingredients, reviewed on Needs a bridge (03 and 10, 1G). */

export interface DensityOffer {
  portion_id: string;
  portion_label: string;
  gram_weight: string;
  density_g_per_ml: string;
  /** Preselected: the cup portion, else the largest volume portion. */
  suggested: boolean;
}

export interface MeasureOffer {
  label: string;
  canonical_qty: string;
  from_portion: string;
  /** Ticked by default: the label starts with a volume unit or a plain size word. */
  suggested: boolean;
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
  /** How far apart the densities are, in percent, when more than 5%. */
  density_spread_pct: number | null;
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
      api<{ saved: number; ingredient: Ingredient }>(
        `/usda/review/${encodeURIComponent(ingredientId)}`,
        { method: "POST", body },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: usdaReviewKey });
      void client.invalidateQueries({ queryKey: ["ingredients"] });
      void client.invalidateQueries({ queryKey: ["price-book"] });
    },
  });
}

/** Link the ingredient to another USDA food, or to none (`fdcId: null`). */
export function useUsdaRelink() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ingredientId, fdcId }: { ingredientId: string; fdcId: number | null }) =>
      api<Ingredient>(`/usda/review/${encodeURIComponent(ingredientId)}/link`, {
        method: "POST",
        body: { fdc_id: fdcId },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: usdaReviewKey });
      void client.invalidateQueries({ queryKey: ["ingredients"] });
    },
  });
}
