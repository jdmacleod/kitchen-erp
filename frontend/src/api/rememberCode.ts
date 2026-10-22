import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { purchaseKeys } from "./purchases";

/** A remembered code, and the waiting lines it was not applied to because they read differently. */
export interface RememberedCode {
  scheme: string;
  value: string;
  product_id: string;
  held: { purchase_id: string; line_id: string; text: string | null }[];
}

/**
 * Remember a line's code for its product at its vendor; only ever on a person's click (2K).
 * A weighed code other wording also prints is refused as `code_shared` until `sharedOk` (#448).
 */
export function useRememberCode(purchaseId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ lineId, sharedOk = false }: { lineId: string; sharedOk?: boolean }) =>
      api<RememberedCode>(
        `/purchases/${encodeURIComponent(purchaseId)}/lines/${encodeURIComponent(lineId)}/remember-code`,
        {
          method: "POST",
          body: { shared_ok: sharedOk },
        },
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: purchaseKeys.purchase(purchaseId) }),
  });
}
