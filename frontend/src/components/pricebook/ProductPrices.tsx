import { useMemo } from "react";
import { Link } from "react-router";
import type { Product } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useOfferHistory, type HistoryOffer, type OfferHistory } from "../../api/flyerOffers";
import { useProductPrices } from "../../api/pricebook";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { offerPriceText } from "../../lib/offers";
import { Alert, Card, focusRing, sectionHeading } from "../ui";
import { PriceAge, PromoBadge, RegularPrice, formatUnitPrice } from "./PriceAge";
import { PriceHistoryChart, chartable, type OfferWeek } from "./PriceHistoryChart";

/** The product page's price history chart and latest-price-per-location table. */
export function ProductPrices({ product }: { product: Product }) {
  const prices = useProductPrices(product.id);
  // Offers are extra: while they load, or if they can't, the chart shows without them.
  const history = useOfferHistory(product.id).data;
  const weeks = useMemo(() => offerWeeks(history?.items ?? []), [history]);
  // The chart's unit is its points' (issue 371: normalized, else per bunch).
  const unit =
    chartable(prices.data?.points ?? []).find((p) => p.display_unit)?.display_unit ??
    prices.data?.latest.find((l) => l.display_unit)?.display_unit ??
    null;

  return (
    <Card>
      <h2 className={`${sectionHeading} mb-3`}>Prices</h2>
      {prices.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : prices.isError ? (
        <Alert tone="error">{errorMessage(prices.error)}</Alert>
      ) : (
        <div className="flex flex-col gap-4">
          <PriceHistoryChart points={prices.data.points} unit={unit} offerWeeks={weeks} />
          {history ? <TypicalPrice history={history} /> : null}
          {prices.data.latest.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Latest price per location">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
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
                        <Link
                          to={`/catalog/vendors?view=map&location=${encodeURIComponent(l.location_id)}`}
                          className={`rounded-sm underline-offset-2 hover:underline ${focusRing}`}
                        >
                          {l.location_name === l.vendor_name
                            ? l.location_name
                            : `${l.vendor_name} — ${l.location_name}`}
                        </Link>
                        {l.price_scope === "chain" ? (
                          <span className="text-xs text-neutral-600 dark:text-neutral-400">
                            {" "}
                            · chain price
                          </span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">
                        {formatMoney(l.price)} / {stripZeros(l.qty)} {l.unit}{" "}
                        <PromoBadge promo={l.is_promo} />
                        <RegularPrice price={l.regular_price} className="block" />
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {formatUnitPrice(l.display_unit_price, l.display_unit)}
                      </td>
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

/** Only for a product with offers: without them nothing new shows (X5). */
function TypicalPrice({ history }: { history: OfferHistory }) {
  const typical = history.typical;
  if (history.items.length === 0 || typical === null) return null;
  return (
    <p className="text-sm" data-testid="typical-price">
      Typical price{" "}
      <span className="tabular-nums">
        {formatUnitPrice(typical.display_unit_price, typical.display_unit)}
      </span>{" "}
      <span className="text-neutral-600 dark:text-neutral-400">
        over the last {typical.weeks} weeks, from {count(typical.regular_count, "regular price")}{" "}
        and {count(typical.offer_count, "offer")}
      </span>
    </p>
  );
}

function count(n: number, noun: string): string {
  return `${n} ${n === 1 ? noun : `${noun}s`}`;
}

/** The product's offers as chart bands, one per ad week, naming each store's offer (X3). */
function offerWeeks(items: HistoryOffer[]): OfferWeek[] {
  const weeks = new Map<string, OfferWeek>();
  for (const o of items) {
    const from = o.ad_week_from ?? o.valid_from;
    const to = o.ad_week_to ?? o.valid_to;
    const week = weeks.get(`${from}:${to}`) ?? { from, to, offers: [] };
    const line = `${o.vendor.name}: ${offerPriceText(o)}`;
    if (!week.offers.includes(line)) week.offers.push(line);
    weeks.set(`${from}:${to}`, week);
  }
  return [...weeks.values()];
}
