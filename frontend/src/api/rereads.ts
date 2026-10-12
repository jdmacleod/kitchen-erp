import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isApiError } from "./client";
import { inboxKey } from "./inbox";
import { invalidatePurchases, purchaseKeys } from "./purchases";

// Re-reading a receipt's lines (04, 2S, #272): a proposal a person applies.

export type RereadMode = "check" | "read";
export type RereadStatus = "pending" | "running" | "ready" | "no_changes" | "failed" | "applied" | "dismissed";

/** A line's values as the page shows them; amounts are decimal strings. */
export interface RereadLine {
  raw_text: string | null;
  line_kind: string;
  qty: string | null;
  unit: string | null;
  unit_price: string | null;
  line_total: string;
  /** The receipt text of the item a discount or deposit belongs to. */
  attached: string | null;
  flags: string[];
}

export type RereadField = "raw_text" | "line_kind" | "qty" | "unit" | "unit_price" | "line_total" | "attached" | "flags";

export interface RereadChange {
  action: "changed" | "added" | "removed" | "kept";
  line_id: string | null;
  before: RereadLine | null;
  after: RereadLine | null;
  fields: RereadField[] | null;
  why: "edited" | "added" | "removed" | null;
  /** Kept because a person removed it: what the reading would add back. */
  reading: RereadLine | null;
}

export interface Reconciliation {
  checked: boolean;
  computed_total: string;
  printed_total: string | null;
  difference: string | null;
  mismatch: boolean;
}

export interface RereadProposal {
  changes: RereadChange[];
  counts: { changed: number; added: number; removed: number; kept: number };
  before: { reconciliation: Reconciliation; flags: string[] };
  after: { reconciliation: Reconciliation; flags: string[] };
}

export interface Reread {
  id: string;
  purchase_id: string;
  mode: RereadMode;
  status: RereadStatus;
  reason: string | null;
  proposal: RereadProposal | null;
  created_at: string;
  decided_at: string | null;
}

const enc = encodeURIComponent;

export const rereadKeys = {
  latest: (purchaseId: string) => ["rereads", "latest", purchaseId] as const,
};

const WAITING: RereadStatus[] = ["pending", "running"];

/** The purchase's latest re-read, or null when it has none; polls while one is being read. */
export function useLatestReread(purchaseId: string) {
  return useQuery({
    queryKey: rereadKeys.latest(purchaseId),
    queryFn: async () => {
      try {
        return await api<Reread>(`/purchases/${enc(purchaseId)}/rereads/latest`);
      } catch (error) {
        if (isApiError(error) && error.status === 404) return null;
        throw error;
      }
    },
    refetchInterval: (query) => (query.state.data && WAITING.includes(query.state.data.status) ? 3000 : false),
  });
}

function useRereadMutation<I>(purchaseId: string, call: (input: I) => Promise<Reread>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: (reread) => {
      client.setQueryData(rereadKeys.latest(purchaseId), reread);
      void client.invalidateQueries({ queryKey: purchaseKeys.purchase(purchaseId) });
      void client.invalidateQueries({ queryKey: inboxKey });
    },
  });
}

export function useRequestReread(purchaseId: string) {
  return useRereadMutation(purchaseId, (mode: RereadMode) =>
    api<Reread>(`/purchases/${enc(purchaseId)}/rereads`, { method: "POST", body: { mode } }),
  );
}

export function useApplyReread(purchaseId: string) {
  const client = useQueryClient();
  const mutation = useRereadMutation(purchaseId, (rereadId: string) => api<Reread>(`/rereads/${enc(rereadId)}/apply`, { method: "POST" }));
  return {
    ...mutation,
    mutate: (rereadId: string, options?: Parameters<typeof mutation.mutate>[1]) =>
      mutation.mutate(rereadId, {
        ...options,
        onSuccess: (...args) => {
          invalidatePurchases(client, "all");
          options?.onSuccess?.(...args);
        },
      }),
  };
}

export function useDismissReread(purchaseId: string) {
  return useRereadMutation(purchaseId, (rereadId: string) => api<Reread>(`/rereads/${enc(rereadId)}/dismiss`, { method: "POST" }));
}

/** What a failed re-read's reason means, in words (the codes are the stage's). */
export function rereadReason(reason: string | null): string {
  switch (reason) {
    case "invalid_model_output":
      return "The model's answer didn't make sense as receipt lines.";
    case "no_ocr_text":
      return "There's no stored text for this receipt to read again.";
    case "voided":
      return "The purchase was removed while it was being read.";
    default:
      return "The receipt couldn't be read again.";
  }
}
