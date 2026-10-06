import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, errorMessage } from "../../api/client";
import type { Product } from "../../api/catalog";
import { useProductsHelper } from "../../api/proposals";
import { Alert, Button, Card, Field } from "../ui";

/**
 * "Look this up online" for a product the household already has (2N): its barcode,
 * a store page pasted here, or, for a branded product with no barcode, a search by
 * its brand, name and size (issue 184). The lookup helper reads it, and what it finds
 * (a photo, the name, the size, a barcode) waits on Home as a product update. Shown
 * only when a helper is set up.
 */
type LookUp = { pageUrl: string } | { byName: true } | null;
export function ProductLookUpCard({ product }: { product: Product }) {
  const helper = useProductsHelper();
  const [page, setPage] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const lookUp = useMutation({
    mutationFn: (what: LookUp) =>
      api(`/products/${encodeURIComponent(product.id)}/look-up`, {
        method: "POST",
        body: what === null ? {} : "pageUrl" in what ? { page_url: what.pageUrl } : { by_name: true },
      }),
    onSuccess: (_, what) => {
      setSent(
        what === null
          ? "The lookup helper will look up the barcode."
          : "pageUrl" in what
            ? "The lookup helper will read the page."
            : "The lookup helper will search by the brand, name and size.",
      );
      setPage("");
    },
  });
  if (!helper.data?.configured) return null;
  const address = page.trim();
  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Look this up online</h2>
      <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
        A photo, the name and the size from the barcode or a store's page{product.barcode || !product.brand ? "" : ", or a search by name"}. What the helper finds waits
        on Home for review.
      </p>
      <div className="flex flex-col gap-3">
        {product.barcode ? (
          <div>
            <Button variant="secondary" disabled={lookUp.isPending} onClick={() => lookUp.mutate(null)}>
              Look up its barcode
            </Button>
          </div>
        ) : product.brand ? (
          <div>
            <Button variant="secondary" disabled={lookUp.isPending} onClick={() => lookUp.mutate({ byName: true })}>
              Search by name
            </Button>
          </div>
        ) : null}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (/^https?:\/\//i.test(address)) lookUp.mutate({ pageUrl: address });
          }}
        >
          <div className="min-w-0 flex-1">
            <Field
              id={`look-up-page-${product.id}`}
              label="A store page for it"
              type="url"
              inputMode="url"
              autoComplete="off"
              placeholder="https://"
              value={page}
              onChange={(e) => setPage(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary" disabled={lookUp.isPending || !/^https?:\/\//i.test(address)}>
            Read this page
          </Button>
        </form>
        {sent ? (
          <p role="status" className="text-sm">
            {sent} What it finds will wait on Home for review.
          </p>
        ) : null}
        {lookUp.error ? <Alert tone="error">{errorMessage(lookUp.error)}</Alert> : null}
      </div>
    </Card>
  );
}
