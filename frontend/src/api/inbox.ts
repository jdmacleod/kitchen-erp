import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { Trust } from "./purchases";

/** The unified inbox (docs/spec/09, Unified inbox). */

export type InboxKind = "receipt" | "receipt_held" | "receipt_failed" | "identify" | "bridge" | "vendor_suggestions" | "link" | "usda" | "new_product" | "product_update" | "posted_prices";

export interface InboxItem {
  kind: InboxKind;
  title: string;
  detail: string;
  action_label: string;
  action_route: string;
  created_at: string;
  /** A failed read's ingest error code, for the sentence in lib/ingestErrors.ts. */
  error_code?: string | null;
  /** A receipt row's reading, for its badge, and how far off with check_lines (issue 122). */
  trust?: Trust | null;
  gap?: string | null;
}

export interface InboxReading {
  /** Receipts being read. */
  count: number;
  /** Product photos and product pages being identified (2L). */
  photos?: number;
  pages?: number;
  /** Barcodes and pages waiting on the products helper, only once overdue (2N, PD7). */
  lookups_overdue?: number;
  lookups_since?: string | null;
  oldest_at: string | null;
  /** The oldest read has waited longer than INGEST_STALL_MINUTES (D21). */
  stalled: boolean;
  /** Receipts in the batches being read, and how many are done (issue 122). */
  batch_done?: number | null;
  batch_of?: number | null;
  /** Whole minutes at the recent pace, at least 1; null until any read has finished. */
  minutes_left?: number | null;
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
    refetchInterval: (query) => (readingTotal(query.state.data?.reading) > 0 ? 5_000 : false),
  });
}

/** Everything still being read: receipts, product photos and product pages. */
export function readingTotal(reading: InboxReading | undefined): number {
  if (!reading) return 0;
  return reading.count + (reading.photos ?? 0) + (reading.pages ?? 0);
}

function counted(n: number, one: string, many: string): string | null {
  return n > 0 ? `${n} ${n === 1 ? one : many}` : null;
}

/** "Reading 4 of 12 receipts", while a batch of several is being read (issue 122). */
function receiptsPart(reading: InboxReading): string | null {
  const of = reading.batch_of ?? 0;
  if (reading.count > 0 && of > 1) return `${Math.min((reading.batch_done ?? 0) + 1, of)} of ${of} receipts`;
  return counted(reading.count, "receipt", "receipts");
}

/**
 * "Reading 2 receipts and 1 product page…" (09, PD7): one sentence for everything,
 * with an estimate once recent reads give one: "Reading 4 of 12 receipts · about
 * 8 minutes left" (issue 122).
 */
export function readingSentence(reading: InboxReading): string {
  const parts = [
    receiptsPart(reading),
    counted(reading.photos ?? 0, "product photo", "product photos"),
    counted(reading.pages ?? 0, "product page", "product pages"),
  ].filter((p): p is string => p !== null);
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  const minutes = reading.count > 0 ? reading.minutes_left : null;
  if (minutes) return `Reading ${list} · about ${minutes} ${minutes === 1 ? "minute" : "minutes"} left`;
  return `Reading ${list}…`;
}
