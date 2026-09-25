import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { CategoryKey } from "../components/CategoryChip";
import { qs } from "./catalog";
import { api } from "./client";

/** The search palette (docs/spec/09, Search). */

export type SearchKind = "ingredient" | "product" | "vendor";

export interface SearchResult {
  kind: SearchKind;
  id: string;
  label: string;
  detail: string | null;
  route: string;
  category_key: CategoryKey | null;
}

export interface SearchResults {
  ingredients: SearchResult[];
  products: SearchResult[];
  vendors: SearchResult[];
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
