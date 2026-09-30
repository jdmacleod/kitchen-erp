import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CategoryKey } from "../components/CategoryChip";
import type { CanonicalUnit, Ingredient } from "./catalog";
import { api } from "./client";

/** Linking ingredients to the standard list, and merging them (03, 1G). */

export interface StandardEntry {
  key: string;
  name: string;
  category: string | null;
  category_key: CategoryKey | null;
  canonical_unit: CanonicalUnit;
  fdc_id: number | null;
}

export interface LinkSuggestion extends StandardEntry {
  usda_description: string | null;
}

export interface LinkOther {
  id: string;
  name: string;
  canonical_unit: CanonicalUnit;
  products: number;
}

export interface LinkRow {
  id: string;
  name: string;
  canonical_unit: CanonicalUnit;
  category: string | null;
  category_key: CategoryKey | null;
  products: number;
  suggestion: LinkSuggestion | null;
  /** Another ingredient already has the suggested name: linking merges. */
  conflict: LinkOther | null;
}

export interface LinkPage {
  to_review: LinkRow[];
  skipped: LinkRow[];
}

export interface LinkSummary {
  to_review: number;
  skipped: number;
}

/** A merge's target: the standard entry a Link named, or the name a Rename typed. */
export type MergeTarget = { standard_key: string } | { name: string };

export interface MergeMeasure {
  label: string;
  canonical_qty: string;
  copyable: boolean;
  suggested: boolean;
}

export interface MergeResult {
  survivor_id: string;
  loser_id: string;
  target_name: string;
  products_moving: number;
  unit_from: CanonicalUnit;
  unit_to: CanonicalUnit;
  prices_needing_bridge: number;
  measures: MergeMeasure[];
}

/** What a 409 merge_needed carries in its details. */
export interface MergeNeeded {
  target_name: string;
  other: LinkOther;
}

export const linkKeys = {
  page: ["ingredients", "link"] as const,
  summary: ["ingredients", "link", "summary"] as const,
  standard: (q: string) => ["standard-ingredients", q] as const,
  preview: (survivor: string, loser: string, target: MergeTarget) => ["ingredients", "merge-preview", survivor, loser, target] as const,
};

const enc = encodeURIComponent;

export function useLinkPage() {
  return useQuery({ queryKey: linkKeys.page, queryFn: () => api<LinkPage>("/ingredients/link") });
}

export function useLinkSummary() {
  return useQuery({ queryKey: linkKeys.summary, queryFn: () => api<LinkSummary>("/ingredients/link/summary") });
}

/** Standard entries by name or spelling, taken or not ("Choose another standard name"). */
export function useStandardEntries(q: string) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: linkKeys.standard(trimmed),
    queryFn: () => api<{ items: StandardEntry[] }>(`/standard-ingredients?q=${enc(trimmed)}&limit=10`),
    select: (data) => data.items,
    enabled: trimmed.length > 0,
    placeholderData: (previous) => previous,
  });
}

export function useMergePreview(survivorId: string, loserId: string, target: MergeTarget) {
  return useQuery({
    queryKey: linkKeys.preview(survivorId, loserId, target),
    queryFn: () =>
      api<MergeResult>("/ingredients/merge/preview", { method: "POST", body: { survivor_id: survivorId, loser_id: loserId, ...target } }),
    staleTime: 0,
  });
}

function useInvalidateCatalog() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ["ingredients"] });
    void client.invalidateQueries({ queryKey: ["products"] });
  };
}

export function useLinkAction() {
  const invalidate = useInvalidateCatalog();
  return useMutation({
    mutationFn: (input: { id: string; action: "link"; standard_key: string } | { id: string; action: "rename"; name: string } | { id: string; action: "skip" | "reopen" }) => {
      const { id, action } = input;
      const body = input.action === "link" ? { standard_key: input.standard_key } : input.action === "rename" ? { name: input.name } : undefined;
      return api<Ingredient>(`/ingredients/${enc(id)}/${action}`, { method: "POST", body });
    },
    onSuccess: invalidate,
  });
}

export function useMerge() {
  const invalidate = useInvalidateCatalog();
  return useMutation({
    mutationFn: (input: { survivor_id: string; loser_id: string; target: MergeTarget; copy_measures: string[] }) =>
      api<MergeResult>("/ingredients/merge", {
        method: "POST",
        body: { survivor_id: input.survivor_id, loser_id: input.loser_id, copy_measures: input.copy_measures, ...input.target },
      }),
    onSuccess: invalidate,
  });
}
