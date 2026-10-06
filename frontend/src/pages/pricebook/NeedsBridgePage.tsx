import { Fragment, useState } from "react";
import { Link } from "react-router";
import { formatPack, productTitle } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { bridgeFixLink, normStatusText, useNeedsBridge } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { useNotice } from "../../components/Notice";
import { QuickPackEditor } from "../../components/pricebook/QuickPackEditor";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";
import { UsdaReviewSection } from "./UsdaReviewSection";

/** Observations that could not be priced per canonical unit, and what each is missing. */
export function NeedsBridgePage() {
  usePageTitle("Needs a bridge");
  const list = useNeedsBridge();
  const items = list.data ?? [];
  const notice = useNotice();
  // The product whose pack is being set in place (issue 186); one at a time.
  const [editing, setEditing] = useState<string | null>(null);
  const products = new Set(items.map((i) => i.product.id)).size;
  const packIds = items.filter((i) => i.status === "no_pack").map((i) => i.product.id);

  // After a save, the next product waiting on a pack opens, so a batch can be
  // worked through from the keyboard without leaving the page.
  function saved(productId: string, title: string) {
    const rest = packIds.filter((id) => id !== productId);
    const next = rest[packIds.indexOf(productId)] ?? rest[0] ?? null;
    setEditing(next);
    notice.show({ tone: "success", message: `Saved the pack for ${title}.`, timeoutMs: 4000 });
  }

  return (
    <>
      <PageHeader title="Needs a bridge" description="Products whose prices can't be compared until they have a density, a measure or a pack size." />
      <UsdaReviewSection />
      {list.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : list.isError ? (
        <Alert tone="error">{errorMessage(list.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState
          title="Every price is normalized"
          action={
            <Link to="/" className={secondaryLinkClass}>
              Back to Home
            </Link>
          }
        >
          Nothing is waiting on a density, a measure, or a pack size.
        </EmptyState>
      ) : (
        <Card>
          <p role="status" className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
            {products === 1 ? "1 product" : `${products} products`}
            {packIds.length ? ` · ${packIds.length} ${packIds.length === 1 ? "needs" : "need"} a pack size` : ""}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Needs a bridge">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                  <th className="py-2 pr-3">Ingredient</th>
                  <th className="py-2 pr-3">Product</th>
                  <th className="py-2 pr-3">Missing</th>
                  <th className="py-2 pr-3 text-right">Prices</th>
                  <th className="py-2 pr-3">Latest</th>
                  <th className="py-2">Fix</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                {items.map((item) => {
                  const fix = bridgeFixLink(item.status, item.ingredient.id, item.product.id);
                  const title = productTitle(item.product);
                  const quickPack = item.status === "no_pack";
                  const open = quickPack && editing === item.product.id;
                  return (
                    <Fragment key={`${item.product.id}-${item.status}`}>
                      <tr data-testid="needs-bridge">
                        <td className="py-2 pr-3">
                          <Link to={`/catalog/ingredients/${item.ingredient.id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                            {item.ingredient.name}
                          </Link>
                          <span className="text-xs text-neutral-600 dark:text-neutral-400"> · {item.ingredient.canonical_unit}</span>
                        </td>
                        <td className="py-2 pr-3">
                          <Link to={`/catalog/products/${item.product.id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                            {title}
                          </Link>
                          {formatPack(item.product.pack_qty, item.product.pack_unit) ? <span className="text-xs text-neutral-600 dark:text-neutral-400"> · {formatPack(item.product.pack_qty, item.product.pack_unit)}</span> : null}
                        </td>
                        <td className="py-2 pr-3">
                          <Badge tone="warn">{normStatusText[item.status]}</Badge>
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">{item.observation_count}</td>
                        <td className="py-2 pr-3 whitespace-nowrap">{formatDate(item.latest_observed_at)}</td>
                        <td className="py-2">
                          {quickPack ? (
                            <Button
                              variant="secondary"
                              aria-expanded={open}
                              aria-controls={open ? `pack-row-${item.product.id}` : undefined}
                              onClick={() => setEditing(open ? null : item.product.id)}
                            >
                              Set the pack
                            </Button>
                          ) : (
                            <Link to={fix.to} className={`rounded font-medium underline ${focusRing}`}>
                              {fix.text}
                            </Link>
                          )}
                        </td>
                      </tr>
                      {open ? (
                        <tr id={`pack-row-${item.product.id}`}>
                          <td colSpan={6} className="pb-3">
                            {/* Sticky so the editor stays in view when the table scrolls sideways on a phone. */}
                            <div className="sticky left-0 max-w-[calc(100vw-4rem)] lg:max-w-none">
                              <QuickPackEditor
                                key={item.product.id}
                                productId={item.product.id}
                                title={title}
                                onSaved={() => saved(item.product.id, title)}
                                onCancel={() => setEditing(null)}
                              />
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
