import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { inboxKey } from "./inbox";

export type CleanupKind =
  | "category"
  | "ingredient_name"
  | "usda_link"
  | "ingredient_merge"
  | "product_name"
  | "product_brand"
  | "product_pack"
  | "product_move"
  | "unit_mismatch";

/** One thing an accept changes beyond its row, linked to its recipe when it is a line. */
export interface CleanupConsequence {
  text: string;
  recipe_id: string | null;
}

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
  /**
   * "sure": a rule settled it, and "Accept these N" may apply it. "guess": a search or
   * a resemblance found it; accepted one at a time, or with a value chosen (issue 372).
   */
  confidence: "sure" | "guess";
  /** A move's or unit switch's consequences, from the latest cost snapshots. */
  consequences?: CleanupConsequence[];
  /** Sent back with the accept: the consequences the person was shown. */
  fingerprint?: string;
  /** A unit switch's measures that would be dropped. */
  measures_dropped?: number;
}

/** What one accepted move or unit switch changed. */
export interface CleanupResult {
  key: string;
  prices_renormalized: number;
  recipes_recomputed: number;
  measures_dropped: number;
  measures_rewritten: number;
}

/** What an accept did: applied, changed since the page loaded, or held back as a guess. */
export interface CleanupApplied {
  applied: number;
  gone: string[];
  held: string[];
  results?: CleanupResult[];
}

/** Suggestions to apply, with any category chosen instead of the one offered. */
export interface CleanupApply {
  keys: string[];
  chosen?: Record<string, string>;
  /** Each key's fingerprint as listed; a different one now is 409 cleanup_changed. */
  fingerprints?: Record<string, string>;
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
    // A unit switch renormalizes prices and recomputes recipes (issue 527).
    void client.invalidateQueries({ queryKey: ["price-book"] });
    void client.invalidateQueries({ queryKey: ["bridges"] });
    void client.invalidateQueries({ queryKey: ["recipes"] });
    void client.invalidateQueries({ queryKey: ["unit-switch"] });
  };
}

/** An ingredient's switch to its standard entry's unit, or null when there is none (issue 527). */
export function useUnitSwitch(ingredientId: string, enabled = true) {
  return useQuery({
    queryKey: ["unit-switch", ingredientId],
    queryFn: () =>
      api<{ suggestion: CleanupSuggestion | null }>(
        `/ingredients/${encodeURIComponent(ingredientId)}/unit-switch`,
      ),
    select: (data) => data.suggestion,
    enabled,
  });
}

/** The switch itself, against the consequences shown (409 cleanup_changed otherwise). */
export function useSwitchUnit(ingredientId: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (fingerprint: string | undefined) =>
      api<CleanupResult>(`/ingredients/${encodeURIComponent(ingredientId)}/unit-switch`, {
        method: "POST",
        body: { fingerprint },
      }),
    onSuccess: refresh,
    onError: refresh,
  });
}

export function useApplyCleanup() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ keys, chosen = {}, fingerprints = {} }: CleanupApply) =>
      api<CleanupApplied>("/catalog/cleanup/apply", {
        method: "POST",
        body: Object.keys(fingerprints).length ? { keys, chosen, fingerprints } : { keys, chosen },
      }),
    onSuccess: refresh,
    // A suggestion that changed or went is listed again as it is now.
    onError: refresh,
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
