import { useQuery } from "@tanstack/react-query";
import { catalogKeys } from "./catalog";
import { api } from "./client";

/** An ingredient's preferred USDA food and its refuse (07, 3D; issue 282). */
export interface UsdaReference {
  fdc_id: number;
  description: string;
  /** Inedible share as bought, in percent, such as "12.500"; null when USDA gives none. */
  refuse_pct: string | null;
  /** 1 − refuse / 100, as the fraction `yield_pct` stores, such as "0.8750". */
  yield_pct: string | null;
}

export interface IngredientUsda {
  reference: UsdaReference | null;
}

/** Under the ingredient's detail key, so whatever refreshes the ingredient refreshes this. */
export function useIngredientUsda(id: string) {
  return useQuery({
    queryKey: [...catalogKeys.ingredient(id), "usda"],
    queryFn: () => api<IngredientUsda>(`/ingredients/${encodeURIComponent(id)}/usda`),
  });
}
