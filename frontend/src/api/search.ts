import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { CategoryKey } from "../components/CategoryChip";
import { qs } from "./catalog";
import { api } from "./client";

/** The search palette (docs/spec/09, Search). */

export type SearchKind = "ingredient" | "product" | "vendor" | "recipe";

/** A recipe row's badge (UI-7.4): a dirty file, a file that cannot be read, a vanished file. */
export type RecipeSearchBadge = "uncommitted" | "parse_error" | "missing";

export interface SearchResult {
  kind: SearchKind;
  id: string;
  label: string;
  detail: string | null;
  route: string;
  category_key: CategoryKey | null;
  /** Recipes only; absent from servers before Cook was built. */
  badge?: RecipeSearchBadge | null;
}

export interface SearchResults {
  ingredients: SearchResult[];
  products: SearchResult[];
  vendors: SearchResult[];
  /** After vendors (UI-7.17); absent from servers before Cook was built. */
  recipes?: SearchResult[];
}

/** The API accepts 1–200 characters. */
export const SEARCH_MAX = 200;

export function useSearch(q: string) {
  const trimmed = q.trim().slice(0, SEARCH_MAX);
  return useQuery({
    queryKey: ["search", trimmed],
    queryFn: () => api<SearchResults>(`/search${qs({ q: trimmed })}`),
    enabled: trimmed.length > 0,
    // Keep the last results on screen while the next query runs, so the list
    // does not blink empty on every keystroke.
    placeholderData: keepPreviousData,
    retry: false,
  });
}
