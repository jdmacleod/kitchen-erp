import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { inboxKey } from "./inbox";

export type CleanupKind =
  | "category"
  | "ingredient_name"
  | "usda_link"
  | "ingredient_merge"
  | "product_name"
  | "product_brand";

/** One proposed fix (issue 349). */
export interface CleanupSuggestion {
  key: string;
  kind: CleanupKind;
  subject_id: string;
  subject: string;
  before: string;
  after: string;
  reason: string;
  /** False for a category nothing could guess: choose one before accepting. */
  ready: boolean;
  /** What accepting applies as offered: the category key, for a category; else "". */
  value: string;
  /** Whether a person may choose the value instead: a category, from the nine keys. */
  choosable: boolean;
}

/** Suggestions to apply, with any category chosen instead of the one offered. */
export interface CleanupApply {
  keys: string[];
  chosen?: Record<string, string>;
}

const cleanupKey = ["catalog", "cleanup"] as const;

export function useCleanup() {
  return useQuery({
    queryKey: cleanupKey,
    queryFn: () => api<{ items: CleanupSuggestion[] }>("/catalog/cleanup"),
    select: (data) => data.items,
  });
}

function useRefresh() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: cleanupKey });
    void client.invalidateQueries({ queryKey: inboxKey });
    void client.invalidateQueries({ queryKey: ["ingredients"] });
    void client.invalidateQueries({ queryKey: ["products"] });
  };
}

export function useApplyCleanup() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ keys, chosen = {} }: CleanupApply) =>
      api<{ applied: number; gone: string[] }>("/catalog/cleanup/apply", {
        method: "POST",
        body: { keys, chosen },
      }),
    onSuccess: refresh,
  });
}

export function useSkipCleanup() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (keys: string[]) =>
      api<unknown>("/catalog/cleanup/skip", { method: "POST", body: { keys } }),
    onSuccess: refresh,
  });
}
