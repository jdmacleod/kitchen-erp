// Where each line's printed row sits on the receipt photo (04, 2S; issue 380).
// Found by a local Tesseract pass after reading; a line without a box simply
// has no outline.

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export interface RowBox {
  line_id: string;
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Boxes in the upright photo's pixels; `width` and `height` are null when none were found. */
export interface RowBoxes {
  width: number | null;
  height: number | null;
  lines: RowBox[];
}

/**
 * The purchase's row boxes. `revision` (the purchase's `updated_at`) is in the
 * key, so a change to the lines, which can give them new ids, asks again.
 */
export function useRowBoxes(purchaseId: string, revision: string) {
  return useQuery({
    queryKey: ["purchases", "row-boxes", purchaseId, revision] as const,
    queryFn: () => api<RowBoxes>(`/purchases/${encodeURIComponent(purchaseId)}/row-boxes`),
    staleTime: 60_000,
  });
}
