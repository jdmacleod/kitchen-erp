import { useQuery } from "@tanstack/react-query";
import type { CategoryKey } from "../components/CategoryChip";
import { qs, type IngredientCreateInput } from "./catalog";
import { api } from "./client";

/** Where a suggested category came from, shown under the chip (issue 344). */
export type CategorySource = "name" | "standard" | "usda" | "clip" | "catalog";

export interface CategorySuggestion {
  category: CategoryKey | null;
  source: CategorySource | null;
}

/** The likeliest category for a new ingredient's name, to prefill its chip (issue 344). */
export function useCategorySuggestion(
  name: string,
  context: { proposalId?: string; fdcId?: number } = {},
) {
  const trimmed = name.trim();
  const { proposalId, fdcId } = context;
  return useQuery({
    queryKey: ["catalog", "category-suggestion", trimmed, proposalId ?? "", fdcId ?? 0],
    queryFn: () =>
      api<CategorySuggestion>(
        `/ingredients/category-suggestion${qs({ name: trimmed, proposal_id: proposalId, fdc_id: fdcId })}`,
      ),
    enabled: trimmed.length > 0,
    staleTime: 60_000,
  });
}

/** A new ingredient to create, which may keep its name exactly as typed (issue 345). */
export type NewIngredientInput = IngredientCreateInput & { keep_name?: boolean };

/** A typed ingredient name as it would be saved, and what changed (issue 345). */
export interface ConformedName {
  name: string;
  changes: string[];
}

/** The 1G rules applied to a typed name, for a "Will be saved as" preview (issue 345). */
export function useConformedName(name: string) {
  const trimmed = name.trim();
  return useQuery({
    queryKey: ["catalog", "conformed-name", trimmed],
    queryFn: () => api<ConformedName>(`/ingredients/conformed-name${qs({ name: trimmed })}`),
    enabled: trimmed.length > 0,
    staleTime: 60_000,
  });
}
