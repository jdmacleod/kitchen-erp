import { Link } from "react-router";
import type { Product } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useProductPrices } from "../../api/pricebook";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { Alert, Card, focusRing } from "../ui";
import { PriceAge, PromoBadge, formatUnitPrice } from "./PriceAge";
import { PriceHistoryChart } from "./PriceHistoryChart";

/** The product page's price history chart and latest-price-per-location table. */
export function ProductPrices({ product }: { product: Product }) {
  const prices = useProductPrices(product.id);
  const unit = prices.data?.points.find((p) => p.norm_unit)?.norm_unit ?? prices.data?.latest.find((l) => l.norm_unit)?.norm_unit ?? product.ingredient.canonical_unit;

  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Prices</h2>
      {prices.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : prices.isError ? (
        <Alert tone="error">{errorMessage(prices.error)}</Alert>
      ) : (
        <div className="flex flex-col gap-4">
          <PriceHistoryChart points={prices.data.points} unit={unit} />
          {prices.data.latest.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Latest price per location">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:border-neutral-800">
                    <th className="py-2 pr-3">Location</th>
                    <th className="py-2 pr-3 text-right">Price</th>
                    <th className="py-2 pr-3 text-right">Per {unit}</th>
                    <th className="py-2">Observed</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {prices.data.latest.map((l) => (
                    <tr key={l.location_id} data-testid="latest-price">
                      <td className="py-2 pr-3">
                        <Link to={`/map?location=${encodeURIComponent(l.location_id)}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
                          {l.location_name === l.vendor_name ? l.location_name : `${l.vendor_name} — ${l.location_name}`}
                        </Link>
                        {l.price_scope === "chain" ? <span className="text-xs text-neutral-500"> · chain price</span> : null}
                      </td>
                      <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">
                        {formatMoney(l.price)} / {stripZeros(l.qty)} {l.unit} <PromoBadge promo={l.is_promo} />
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatUnitPrice(l.norm_unit_price, l.norm_unit)}</td>
                      <td className="py-2">
                        <PriceAge observedAt={l.observed_at} ageDays={l.age_days} stale={l.stale} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}
