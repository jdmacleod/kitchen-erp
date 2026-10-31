import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { catalogKeys } from "./catalog";
import { api } from "./client";

/** One code a product is known by (03, 1H); store-scoped schemes name their store. */
export interface ProductCode {
  id: string;
  scheme: "gtin" | "plu" | "vendor_sku" | "rw_item" | "other";
  value: string;
  vendor_id: string | null;
  vendor_name: string | null;
  source: string;
  /** The code the barcode field shows; it is changed in Edit product, not forgotten. */
  is_barcode: boolean;
  created_at: string;
}

// Under the product's key, so saving the product (a new barcode) refreshes it too.
const codesKey = (productId: string) => [...catalogKeys.product(productId), "codes"] as const;

export function useProductCodes(productId: string) {
  return useQuery({
    queryKey: codesKey(productId),
    queryFn: () =>
      api<{ items: ProductCode[] }>(`/products/${encodeURIComponent(productId)}/codes`),
    select: (data) => data.items,
  });
}

/** Forget a remembered code (issue 448); the lines it identified keep their product. */
export function useForgetProductCode(productId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (codeId: string) =>
      api<null>(`/products/${encodeURIComponent(productId)}/codes/${encodeURIComponent(codeId)}`, {
        method: "DELETE",
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: catalogKeys.product(productId) }),
  });
}
