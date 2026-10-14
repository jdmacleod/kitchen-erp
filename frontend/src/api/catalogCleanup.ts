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
  /** False for a category nothing could guess: choose one on the ingredient's page. */
  ready: boolean;
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
    mutationFn: (keys: string[]) =>
      api<{ applied: number; gone: string[] }>("/catalog/cleanup/apply", {
        method: "POST",
        body: { keys },
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
