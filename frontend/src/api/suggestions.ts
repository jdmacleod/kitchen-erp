// Vendor suggestions from an outside tool, and their review (spec 03 §1F).
// Nothing a tool posts changes a vendor until a person accepts it here.

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { qs } from "./catalog";
import { geoKeys } from "./geo";
import { inboxKey } from "./inbox";
import type { ListResponse } from "./types";

export type SuggestionField = "website" | "brand" | "wikidata" | "phone" | "address" | "opening_hours" | "osm" | "name" | "price_scope";
export type SuggestionValue = string | { type: string; id: number } | null;

export interface Suggestion {
  id: string;
  batch_id: string;
  target: "vendor" | "location";
  vendor_id: string;
  vendor_name: string;
  location_id: string | null;
  location_name: string | null;
  field: SuggestionField;
  /** What the tool read there when it proposed this; null for empty. */
  expected: SuggestionValue;
  current: SuggestionValue;
  proposed: SuggestionValue;
  /** The field changed since it was proposed: accepting needs an override (design D8). */
  stale: boolean;
  status: "pending" | "accepted" | "rejected" | "stale";
  source_url: string;
  source_domain: string;
  /** Plain text from the tool; never rendered as markup (UI-3.15). */
  evidence: string | null;
  tool: string;
  tool_version: string;
  created_at: string;
}

export interface SuggestionSummary {
  count: number;
  tools: string[];
  vendors: { vendor_id: string; name: string; count: number }[];
}

export interface Decision {
  outcome: "accepted" | "rejected" | "stale";
  suggestion: Suggestion;
}

export const suggestionKeys = {
  all: ["vendor-suggestions"] as const,
  summary: ["vendor-suggestions", "summary"] as const,
  list: (vendorId: string | null) => ["vendor-suggestions", "list", vendorId] as const,
};

export function useSuggestionSummary() {
  return useQuery({
    queryKey: suggestionKeys.summary,
    queryFn: () => api<SuggestionSummary>("/vendor-suggestions/summary"),
  });
}

export function useSuggestions(vendorId: string | null, enabled = true) {
  return useQuery({
    queryKey: suggestionKeys.list(vendorId),
    queryFn: () => api<ListResponse<Suggestion>>(`/vendor-suggestions${qs({ vendor_id: vendorId })}`),
    select: (data) => data.items,
    enabled: enabled && vendorId !== null,
  });
}

function afterDecision(client: QueryClient) {
  void client.invalidateQueries({ queryKey: suggestionKeys.all });
  void client.invalidateQueries({ queryKey: inboxKey });
  void client.invalidateQueries({ queryKey: geoKeys.vendors });
  void client.invalidateQueries({ queryKey: geoKeys.locations });
}

export function useDecideSuggestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, override = false }: { id: string; action: "accept" | "reject"; override?: boolean }) =>
      api<Decision>(`/vendor-suggestions/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        body: action === "accept" ? { override } : undefined,
      }),
    onSuccess: () => afterDecision(client),
  });
}

export function useAcceptAll() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (vendorId: string) =>
      api<{ accepted: number; skipped_stale: number }>(`/vendor-suggestions/vendors/${encodeURIComponent(vendorId)}/accept-all`, { method: "POST" }),
    onSuccess: () => afterDecision(client),
  });
}
