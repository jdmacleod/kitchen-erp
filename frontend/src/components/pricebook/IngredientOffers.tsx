import { Link } from "react-router";
import type { Ingredient } from "../../api/catalog";
import { formatPack } from "../../api/catalog";
import { priceScopeLabel } from "../../api/geo";
import { bridgeFixLink, type Offer, type PriceFilters as Filters } from "../../api/pricebook";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { focusRing } from "../ui";
import { PriceAge, PromoBadge, QualityText, formatUnitPrice } from "./PriceAge";
import { PriceFilters } from "./PriceFilters";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Prices by vendor (docs/spec/10, Ingredient hub): the latest price at each
 * location, cheapest normalized price first. A price that can't be compared yet
 * sorts last and shows what was paid and how to make it comparable (G8).
 */
export function IngredientOffers({
  ingredient,
  offers,
  filters,
  onFilters,
  staleThreshold,
}: {
  ingredient: Ingredient;
  offers: Offer[];
  filters: Filters;
  onFilters: (filters: Filters) => void;
  staleThreshold: number | undefined;
}) {
  return (
    <section aria-labelledby="prices-by-vendor" className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <h2 id="prices-by-vendor" className="font-display text-lg">
          Prices by vendor
        </h2>
        <PriceFilters id="offers" value={filters} onChange={onFilters} />
      </div>
      {offers.length === 0 ? (
        <p className={`text-sm ${muted}`}>No prices match these filters.</p>
      ) : (
        <div className="overflow-x-auto">
          {/* Four columns from sm up; on a phone, two: the product rides under the
              vendor and the date under the price, so the price is never off-screen. */}
          <table className="w-full text-sm" aria-label="Prices by vendor">
            <thead>
              <tr className={`border-b border-neutral-200 text-left text-xs font-semibold dark:border-neutral-800 ${muted}`}>
                <th className="py-2 pr-3">Vendor</th>
                <th className="hidden py-2 pr-3 sm:table-cell">Product</th>
                <th className="py-2 pr-3 text-right">Per {ingredient.canonical_unit}</th>
                <th className="hidden py-2 sm:table-cell">Seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {offers.map((o) => (
                <OfferRow key={o.observation_id} offer={o} ingredient={ingredient} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className={`mt-3 flex flex-wrap justify-between gap-2 text-xs ${muted}`}>
        <span>
          {staleThreshold !== undefined
            ? `Prices older than ${staleThreshold} days count as stale for a ${ingredient.perishability.replace("_", "-")} ingredient.`
            : null}
        </span>
        <Link to="/shop/compare" className={`rounded text-sm font-medium underline ${focusRing}`}>
          Compare prices
        </Link>
      </p>
    </section>
  );
}

function OfferRow({ offer: o, ingredient }: { offer: Offer; ingredient: Ingredient }) {
  const comparable = o.norm_unit_price !== null;
  const fix = !comparable && o.norm_status ? bridgeFixLink(o.norm_status, ingredient.id, o.product_id) : null;
  const product = (
    <>
      <Link to={`/catalog/products/${o.product_id}`} className={`rounded underline-offset-2 hover:underline ${focusRing}`}>
        {o.brand ? `${o.brand} ${o.product_name}` : o.product_name}
      </Link>
      <span className={`block text-xs ${muted}`}>
        {formatPack(o.pack_qty, o.pack_unit) ? `${formatPack(o.pack_qty, o.pack_unit)} · ` : ""}
        <QualityText rating={o.quality_rating} />
      </span>
    </>
  );
  const seen = <PriceAge observedAt={o.observed_at} ageDays={o.age_days} stale={o.stale} />;
  return (
    <tr data-testid="offer" data-comparable={comparable}>
      <td className="py-2 pr-3 align-top">
        <span className="block font-medium">{o.location_name === o.vendor_name ? o.vendor_name : `${o.vendor_name} — ${o.location_name}`}</span>
        <span className={`block text-xs ${muted}`}>{priceScopeLabel[o.price_scope]}</span>
        <span className="mt-1 block sm:hidden">{product}</span>
      </td>
      <td className="hidden py-2 pr-3 align-top sm:table-cell">
        {product}
      </td>
      <td className="py-2 pr-3 text-right align-top tabular-nums">
        {comparable ? (
          <span className="font-medium">{formatUnitPrice(o.norm_unit_price, o.norm_unit)}</span>
        ) : (
          <>
            <span className="block">
              {formatMoney(o.price)} / {stripZeros(o.qty)} {o.unit}
            </span>
            {fix ? (
              <Link to={fix.to} className={`text-xs underline ${focusRing}`}>
                Can&apos;t compare yet · {fix.text.toLowerCase()}
              </Link>
            ) : (
              <span className={`text-xs ${muted}`}>Can&apos;t compare yet</span>
            )}
          </>
        )}{" "}
        <PromoBadge promo={o.is_promo} />
        <span className="mt-1 block sm:hidden">{seen}</span>
      </td>
      <td className="hidden py-2 align-top sm:table-cell">{seen}</td>
    </tr>
  );
}
