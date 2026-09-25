import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { itemLines, purchaseStatusLabel, purchaseStatusTone, sourceLabel, usePurchases, type PurchaseStatus } from "../../api/purchases";
import { Badge, SelectField } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing, primaryLinkClass, secondaryLinkClass } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const STATUSES: readonly PurchaseStatus[] = ["committed", "reviewed", "draft"];

export function PurchasesPage() {
  usePageTitle("Purchases");
  const [status, setStatus] = useState<PurchaseStatus | "">("");
  const purchases = usePurchases({ status });
  const items = purchases.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader title="Purchases">
        <div className="flex flex-wrap gap-2">
          <Link to="/shop/shelf-prices" className={secondaryLinkClass}>
            Shelf price
          </Link>
          <Link to="/shop/purchases/new" className={primaryLinkClass}>
            New purchase
          </Link>
        </div>
      </PageHeader>

      <Card>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-lg font-medium">All purchases</h2>
          <SelectField id="purchases-status" label="Status" value={status} onChange={(e) => setStatus(e.target.value as PurchaseStatus | "")} className="w-40">
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {purchaseStatusLabel[s]}
              </option>
            ))}
          </SelectField>
        </div>

        {purchases.isPending ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Loading…
          </p>
        ) : purchases.isError ? (
          <Alert tone="error">{errorMessage(purchases.error)}</Alert>
        ) : items.length === 0 ? (
          <EmptyState title="No purchases yet">
            Enter a market or stand purchase by hand, or note a shelf price. Receipts arrive with the ingest pipeline.
          </EmptyState>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Purchases">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <th className="py-2 pr-3">Date</th>
                    <th className="py-2 pr-3">Location</th>
                    <th className="py-2 pr-3 text-right">Total</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3">Source</th>
                    <th className="py-2 text-right">Lines</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {items.map((p) => (
                    <tr key={p.id}>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <Link to={`/shop/purchases/${p.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
                          {formatDate(p.purchased_at)}
                        </Link>
                      </td>
                      <td className="py-2 pr-3">
                        {p.vendor_location ? (
                          <>
                            {p.vendor_location.vendor.name}
                            {p.vendor_location.name !== p.vendor_location.vendor.name ? (
                              <span className="text-neutral-600 dark:text-neutral-400"> — {p.vendor_location.name}</span>
                            ) : null}
                          </>
                        ) : (
                          <span className="text-neutral-600 italic dark:text-neutral-400">No location yet</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMoney(p.total ?? p.computed_total)}</td>
                      <td className="py-2 pr-3">
                        <Badge tone={purchaseStatusTone[p.status]}>{purchaseStatusLabel[p.status]}</Badge>
                      </td>
                      <td className="py-2 pr-3">{sourceLabel[p.source]}</td>
                      <td className="py-2 text-right tabular-nums">{itemLines(p).length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {purchases.hasNextPage ? (
              <div className="mt-3">
                <Button variant="secondary" disabled={purchases.isFetchingNextPage} onClick={() => purchases.fetchNextPage()}>
                  {purchases.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </>
  );
}
