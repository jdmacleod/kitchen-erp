import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

/** The unified inbox (docs/spec/09, Unified inbox). */

export type InboxKind = "receipt" | "receipt_failed" | "identify" | "bridge";

export interface InboxItem {
  kind: InboxKind;
  title: string;
  detail: string;
  action_label: string;
  action_route: string;
  created_at: string;
  /** A failed read's ingest error code, for the sentence in lib/ingestErrors.ts. */
  error_code?: string | null;
}

export interface InboxReading {
  count: number;
  oldest_at: string | null;
  /** The oldest read has waited longer than INGEST_STALL_MINUTES (D21). */
  stalled: boolean;
}

export interface Inbox {
  items: InboxItem[];
  reading: InboxReading;
}

export const inboxKey = ["inbox"] as const;

/**
 * Shared by Home and the nav badge. No interval polling: it refreshes on focus and
 * after every successful mutation (lib/queryClient.ts), and while a receipt is
 * being read, so the reading line and the new draft appear on their own.
 */
export function useInbox() {
  return useQuery({
    queryKey: inboxKey,
    queryFn: () => api<Inbox>("/inbox"),
    refetchInterval: (query) => ((query.state.data?.reading.count ?? 0) > 0 ? 5_000 : false),
  });
}
