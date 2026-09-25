import { Link } from "react-router";
import { formatPack, productTitle } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { bridgeFixLink, normStatusText, useNeedsBridge } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { Alert, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

/** Observations that could not be priced per canonical unit, and what each is missing. */
export function NeedsBridgePage() {
  usePageTitle("Needs a bridge");
  const list = useNeedsBridge();
  const items = list.data ?? [];

  return (
    <>
      <PageHeader title="Needs a bridge" description="Products whose prices can't be compared until they have a density, a measure or a pack size." />
      {list.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : list.isError ? (
        <Alert tone="error">{errorMessage(list.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState title="Every price is normalized">Nothing is waiting on a density, a measure, or a pack size.</EmptyState>
      ) : (
        <Card>
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
                  return (
                    <tr key={`${item.product.id}-${item.status}`} data-testid="needs-bridge">
                      <td className="py-2 pr-3">
                        <Link to={`/catalog/ingredients/${item.ingredient.id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                          {item.ingredient.name}
                        </Link>
                        <span className="text-xs text-neutral-600 dark:text-neutral-400"> · {item.ingredient.canonical_unit}</span>
                      </td>
                      <td className="py-2 pr-3">
                        <Link to={`/catalog/products/${item.product.id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                          {productTitle(item.product)}
                        </Link>
                        {formatPack(item.product.pack_qty, item.product.pack_unit) ? <span className="text-xs text-neutral-600 dark:text-neutral-400"> · {formatPack(item.product.pack_qty, item.product.pack_unit)}</span> : null}
                      </td>
                      <td className="py-2 pr-3">
                        <Badge tone="warn">{normStatusText[item.status]}</Badge>
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">{item.observation_count}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">{formatDate(item.latest_observed_at)}</td>
                      <td className="py-2">
                        <Link to={fix.to} className={`rounded font-medium underline ${focusRing}`}>
                          {fix.text}
                        </Link>
                      </td>
                    </tr>
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
