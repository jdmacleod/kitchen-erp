import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import {
  catalogErrorMessage,
  productTitle,
  unitLabel,
  useMergeProduct,
  useProductMergePreview,
  type Product,
  type ProductMerge,
  type SearchHit,
} from "../../api/catalog";
import { Alert, Button, Card, alertTones } from "../ui";
import { ProductTypeahead } from "./ProductTypeahead";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "2 prices, 1 code and 1 photo" from the counts a merge moves; "" when nothing moves. */
export function mergeSummary(m: ProductMerge): string {
  const parts = [
    m.prices ? plural(m.prices, "price") : "",
    m.codes ? plural(m.codes, "code") : "",
    m.listings ? plural(m.listings, "store page") : "",
    m.photos ? plural(m.photos, "photo") : "",
    m.aliases ? plural(m.aliases, "receipt wording") : "",
  ].filter(Boolean);
  if (parts.length < 2) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The unit warnings a merge shows before it is confirmed (ruling C3). */
export function mergeWarnings(m: ProductMerge): string[] {
  const out: string[] = [];
  if (m.loser_pack_unit && m.survivor_pack_unit && m.loser_pack_unit !== m.survivor_pack_unit) {
    out.push(`Its pack is in ${unitLabel(m.loser_pack_unit)}; ${m.survivor_name}'s is in ${unitLabel(m.survivor_pack_unit)}, and that pack is kept.`);
  }
  if (m.other_dimension_prices > 0) {
    const units = m.other_dimension_units.map(unitLabel).join(" and ");
    const are = m.other_dimension_prices === 1 ? "is" : "are";
    out.push(
      `${plural(m.other_dimension_prices, "price")} ${are} in ${units}. They need a density before they compare in ${unitLabel(m.compare_unit)}.`,
    );
  }
  if (m.prices_needing_bridge > 0) {
    out.push(`${plural(m.prices_needing_bridge, "price")} that compare now won't until a bridge is added.`);
  }
  return out;
}

/**
 * "Merge into…" on a product page (issue 179): choose the product to keep, read
 * what moves, then confirm in place. The merge can't be undone here.
 */
export function ProductMergePanel({
  product,
  keep = null,
  onMerged,
  onCancel,
}: {
  product: Product;
  /** The product to keep, already chosen (a possible duplicate's pair, 2P). */
  keep?: Pick<SearchHit, "id" | "name" | "brand"> | null;
  onMerged: (done: ProductMerge) => void;
  onCancel: () => void;
}) {
  const [survivor, setSurvivor] = useState<Pick<SearchHit, "id" | "name" | "brand"> | null>(keep);
  const [self, setSelf] = useState(false);
  const preview = useProductMergePreview(product.id, survivor?.id ?? null);
  const merge = useMergeProduct(product.id);
  const search = useRef<HTMLInputElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => search.current?.focus(), []);
  useEffect(() => {
    if (preview.data) cancel.current?.focus();
  }, [preview.data]);
  const p = preview.data;
  const warnings = p ? mergeWarnings(p) : [];
  const moves = p ? mergeSummary(p) : "";

  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Merge into another product</h2>
      <p className="mb-3 text-sm text-neutral-700 dark:text-neutral-300">
        For a duplicate of a product you already have. The product you choose is kept, with its own name, pack and details; this one
        becomes inactive and its prices, codes, photos and receipt wordings go to the one you keep.
      </p>
      {survivor === null ? (
        <div className="flex flex-col gap-2">
          <ProductTypeahead
            id={`merge-${product.id}`}
            label="Product to keep"
            placeholder="Type to search products"
            inputRef={search}
            onSelect={(hit) => {
              if (hit.id === product.id) return setSelf(true);
              setSelf(false);
              setSurvivor(hit);
            }}
          />
          {self ? <Alert tone="error">Choose another product to keep.</Alert> : null}
          <div>
            <Button variant="secondary" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div
          role="group"
          aria-labelledby={`merge-${product.id}-heading`}
          className="flex flex-col gap-3 rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950"
        >
          <h3 id={`merge-${product.id}-heading`} className="text-sm font-medium">
            Merge {productTitle(product)} into {productTitle(survivor)}?
          </h3>
          {preview.isPending ? (
            <p role="status" className="text-sm">
              Checking what moves…
            </p>
          ) : null}
          {p ? (
            <p className="text-sm" data-testid="merge-moves">
              {moves ? `${moves} ${moves.includes(" and ") || !moves.startsWith("1 ") ? "go" : "goes"} to ${p.survivor_name}.` : `Nothing else goes to ${p.survivor_name}.`}{" "}
              {productTitle(product)} becomes inactive, and its page links to {p.survivor_name}.
            </p>
          ) : null}
          {warnings.length > 0 ? (
            <ul className="flex flex-col gap-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100" data-testid="merge-warnings">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
          {preview.isError ? <Alert tone="error">{catalogErrorMessage(preview.error)}</Alert> : null}
          {merge.isError ? (
            <div role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error}`}>
              {catalogErrorMessage(merge.error) || errorMessage(merge.error)}
            </div>
          ) : null}
          <p className="text-sm">This can't be undone here.</p>
          <div className="grid grid-cols-2 gap-2 lg:flex">
            <Button
              variant="danger"
              disabled={merge.isPending || !p}
              onClick={() => merge.mutate(survivor.id, { onSuccess: onMerged })}
            >
              {merge.isPending ? "Merging…" : "Merge"}
            </Button>
            <Button ref={cancel} variant="secondary" onClick={onCancel} disabled={merge.isPending}>
              Cancel
            </Button>
          </div>
          <div>
            <Button variant="ghost" className="px-2" disabled={merge.isPending} onClick={() => setSurvivor(null)}>
              Choose another product
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
