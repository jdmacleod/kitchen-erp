import { useQuery } from "@tanstack/react-query";
import type { CategoryKey } from "../components/CategoryChip";
import { qs } from "./catalog";
import { api } from "./client";

/** Where a suggested category came from, shown under the chip (#344). */
export type CategorySource = "standard" | "usda" | "clip" | "catalog";

export interface CategorySuggestion {
  category: CategoryKey | null;
  source: CategorySource | null;
}

/** The likeliest category for a new ingredient's name, to prefill its chip (#344). */
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
