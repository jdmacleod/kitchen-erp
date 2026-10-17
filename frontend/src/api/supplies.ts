// Kitchen supplies (spec 04, 2T; issue 262): a receipt line marked a kitchen
// supply keeps its amount and one category, and has no product and no price.
// The categories and their names come from the server; this file keeps no copy.

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type SupplyCategory = "wraps" | "storage" | "cooking" | "filters" | "fuel";

export interface SupplyCategoryInfo {
  key: SupplyCategory;
  label: string;
  /** What it covers, in a few words ("foil, parchment, …"). */
  examples: string;
}

/** The fixed list, in order. It changes only with a release, so it is fetched once. */
export function useSupplyCategories() {
  return useQuery({
    queryKey: ["supply-categories"],
    queryFn: () => api<{ items: SupplyCategoryInfo[] }>("/supply-categories"),
    select: (data) => data.items,
    staleTime: Infinity,
  });
}

/** A category's name, or its key until the list has loaded. */
export function supplyLabel(
  categories: SupplyCategoryInfo[] | undefined,
  key: string | null | undefined,
): string {
  return categories?.find((c) => c.key === key)?.label ?? key ?? "";
}

/**
 * A line a person (or a confirmed alias) set aside from the price book:
 * ignored, or a kitchen supply. Neither waits in To identify or emits a price.
 */
export function isSetAside(line: { resolution: string | null }): boolean {
  return line.resolution === "ignored" || line.resolution === "supply";
}

/** What accepting a suggestion does, in words: a product, ignoring, or a kind of supply. */
export function suggestionText(
  s: { ignore: boolean; supply_category?: string | null; label: string },
  categories: SupplyCategoryInfo[] | undefined,
  ignoreText = "Ignore this line",
): string {
  if (s.supply_category) return `Kitchen supply: ${supplyLabel(categories, s.supply_category)}`;
  return s.ignore ? ignoreText : s.label;
}
