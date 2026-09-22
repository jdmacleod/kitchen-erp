import { useState } from "react";
import { Link } from "react-router";
import type { Ingredient } from "../../api/catalog";
import { formatPack } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useIngredientOffers, type PriceFilters as Filters } from "../../api/pricebook";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { Alert, Card, focusRing } from "../ui";
import { PriceAge, PromoBadge, QualityText, formatUnitPrice } from "./PriceAge";
import { PriceFilters } from "./PriceFilters";

/** Every product that fulfils the ingredient, cheapest normalized price first. */
export function IngredientOffers({ ingredient }: { ingredient: Ingredient }) {
  const [filters, setFilters] = useState<Filters>({ min_quality: "", exclude_stale: false, exclude_promo: false });
  const offers = useIngredientOffers(ingredient.id, filters);
  const items = offers.data?.items ?? [];
  const threshold = offers.data?.stale_thresholds[ingredient.perishability];

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-lg font-medium">Offers</h2>
        <PriceFilters id="offers" value={filters} onChange={setFilters} />
      </div>
      {offers.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : offers.isError ? (
        <Alert tone="error">{errorMessage(offers.error)}</Alert>
      ) : items.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">No prices match. Note a shelf price or enter a purchase.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Offers">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:border-neutral-800">
                <th className="py-2 pr-3">Product</th>
                <th className="py-2 pr-3 text-right">Quality</th>
                <th className="py-2 pr-3">Where</th>
                <th className="py-2 pr-3 text-right">Per {ingredient.canonical_unit}</th>
                <th className="py-2 pr-3 text-right">Paid</th>
                <th className="py-2">Observed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {items.map((o) => (
                <tr key={o.observation_id} data-testid="offer">
                  <td className="py-2 pr-3">
                    <Link to={`/products/${o.product_id}`} className={`rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
                      {o.brand ? `${o.brand} ${o.product_name}` : o.product_name}
                    </Link>
                    {formatPack(o.pack_qty, o.pack_unit) ? <span className="text-xs text-neutral-500"> · {formatPack(o.pack_qty, o.pack_unit)}</span> : null}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <QualityText rating={o.quality_rating} />
                  </td>
                  <td className="py-2 pr-3">{o.location_name === o.vendor_name ? o.location_name : `${o.vendor_name} — ${o.location_name}`}</td>
                  <td className="py-2 pr-3 text-right font-medium tabular-nums">{formatUnitPrice(o.norm_unit_price, o.norm_unit)}</td>
                  <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">
                    {formatMoney(o.price)} / {stripZeros(o.qty)} {o.unit} <PromoBadge promo={o.is_promo} />
                  </td>
                  <td className="py-2">
                    <PriceAge observedAt={o.observed_at} ageDays={o.age_days} stale={o.stale} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {threshold !== undefined ? (
            <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
              Prices older than {threshold} days count as stale for a {ingredient.perishability.replace("_", "-")} ingredient.
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}
