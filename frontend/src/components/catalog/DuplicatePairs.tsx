import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { formatPack, productTitle, useDuplicates, useMarkDistinct, type DuplicatePair, type Product } from "../../api/catalog";
import { useNotice } from "../Notice";
import { Alert, Button, Card, EmptyState, focusRing } from "../ui";
import { ProductMergePanel } from "./ProductMergePanel";
import { ProductThumb } from "./ProductThumb";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Possible duplicates on the Products page (2P, 03; UI-6.18): each pair side by side,
 * with "Keep this one" on either side, which opens the merge with the pair filled in,
 * and "Not the same", which is remembered. Nothing is merged without a person.
 */
export function DuplicatePairs({ onClose }: { onClose: () => void }) {
  const duplicates = useDuplicates();
  const items = duplicates.data?.items ?? [];
  return (
    <section aria-labelledby="duplicates-heading" className="mb-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="duplicates-heading" className="text-lg font-medium">
          Possible duplicates
        </h2>
        <button type="button" onClick={onClose} className={`min-h-11 rounded-md text-sm underline lg:min-h-0 ${focusRing}`}>
          Back to all products
        </button>
      </div>
      {duplicates.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Looking for duplicates…
        </p>
      ) : duplicates.isError ? (
        <Alert tone="error">{errorMessage(duplicates.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState title="No possible duplicates">
          <p className={`mt-1 text-sm ${muted}`}>Products that look like the same thing entered twice appear here.</p>
        </EmptyState>
      ) : (
        <>
          <p className={`text-sm ${muted}`}>
            Each pair looks like one product entered twice. Keep one and merge the other into it, or say they are not the same.
          </p>
          {items.map((pair) => (
            <PairCard key={`${pair.a.id}:${pair.b.id}`} pair={pair} />
          ))}
        </>
      )}
    </section>
  );
}

function PairCard({ pair }: { pair: DuplicatePair }) {
  const notice = useNotice();
  const distinct = useMarkDistinct();
  // The product to keep, once chosen; the other is merged into it.
  const [keep, setKeep] = useState<Product | null>(null);
  const other = keep && (keep.id === pair.a.id ? pair.b : pair.a);
  const title = `${productTitle(pair.a)} and ${productTitle(pair.b)}`;

  if (keep && other) {
    return (
      <ProductMergePanel
        product={other}
        keep={keep}
        onCancel={() => setKeep(null)}
        onMerged={(done) => notice.show({ tone: "success", message: `Merged ${done.loser_name} into ${done.survivor_name}.` })}
      />
    );
  }
  return (
    <Card>
      <div role="group" aria-label={title} className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {[pair.a, pair.b].map((p) => (
            <div key={p.id} className="flex items-start gap-3">
              <ProductThumb name={p.name} photo={p.photo} categoryKey={p.ingredient.category_key} />
              <div className="flex min-w-0 flex-col gap-1 text-sm">
                <Link to={`/catalog/products/${p.id}`} className={`rounded font-medium underline ${focusRing}`}>
                  {productTitle(p)}
                </Link>
                <span className={muted}>
                  {[formatPack(p.pack_qty, p.pack_unit, p.pack_count, p.piece_name) || "No pack", p.ingredient.name].join(" · ")}
                </span>
                <div>
                  <Button variant="secondary" onClick={() => setKeep(p)}>
                    Keep this one
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
        {distinct.isError ? <Alert tone="error">{errorMessage(distinct.error)}</Alert> : null}
        <div>
          <Button
            variant="secondary"
            disabled={distinct.isPending}
            onClick={() =>
              distinct.mutate(
                { a: pair.a.id, b: pair.b.id },
                { onSuccess: () => notice.show({ tone: "success", message: `${title} won't be offered again.` }) },
              )
            }
          >
            Not the same
          </Button>
        </div>
      </div>
    </Card>
  );
}
