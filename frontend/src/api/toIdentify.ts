// The to-identify queue (spec 04, 2D): unmatched item lines on committed
// purchases, grouped by vendor and wording, each answered once for the group.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import {
  invalidateObservations,
  invalidatePurchases,
  purchaseKeys,
  type CodeOffer,
} from "./purchases";
import type { SupplyCategory } from "./supplies";

export interface ToIdentifyLine {
  line_id: string;
  purchase_id: string;
  raw_text: string | null;
  purchased_at: string;
  line_total: string | null;
  qty: string | null;
  unit: string | null;
}

export interface ToIdentifyGroup {
  vendor: { id: string; name: string };
  raw_text_norm: string | null;
  line_count: number;
  lines: ToIdentifyLine[];
  /** The item code these lines carry where the vendor prints codes (2K). */
  code?: CodeOffer | null;
}

export type ToIdentifyApplyInput = {
  vendor_id: string;
  raw_text_norm: string;
  line_ids?: string[];
} & ({ product_id: string } | { ignore: true } | { supply_category: SupplyCategory });

export function useToIdentify() {
  return useQuery({
    queryKey: purchaseKeys.toIdentify,
    queryFn: () => api<{ items: ToIdentifyGroup[] }>("/to-identify"),
    select: (data) => data.items,
  });
}

export function useApplyToIdentify() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ToIdentifyApplyInput) =>
      api<{ applied: number }>("/to-identify/apply", { method: "POST", body: input }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: purchaseKeys.toIdentify });
      // "all": this applies a product to lines in purchases this hook never
      // loaded, so their cached details are stale too, not just the lists.
      invalidatePurchases(client, "all");
      invalidateObservations(client);
    },
  });
}
