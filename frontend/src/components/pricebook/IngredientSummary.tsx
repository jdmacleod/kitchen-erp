import type { Ingredient } from "../../api/catalog";
import type { UseQueryResult } from "@tanstack/react-query";
import type { IngredientPriceHistory, IngredientPricePoint } from "../../api/pricebook";
import { formatDate } from "../../lib/format";
import { formatUnitPrice } from "./PriceAge";
import { Sparkline } from "./Sparkline";

const WINDOW_DAYS = 90;
const card = "rounded-lg border p-4";
const muted = "text-sm text-neutral-600 dark:text-neutral-400";

/**
 * The cheapest comparable price in the window, or null, from the price history:
 * every observation, not just the latest per location, so a cheaper price from
 * weeks ago still counts. Prices that can't be compared are never in the history
 * (G8). On a tie, the most recent wins.
 */
export function bestRecent(points: IngredientPricePoint[]): IngredientPricePoint | null {
  let best: IngredientPricePoint | null = null;
  for (const p of points) {
    // Decimal strings compare as numbers for ordering only; the text shown is the API's.
    if (!best || Number(p.norm_unit_price) <= Number(best.norm_unit_price)) best = p;
  }
  return best;
}

/** The hub's summary strip (docs/spec/10, Ingredient hub). */
/**
 * The hub's summary strip. Built only from the 90-day history, so the price
 * table's filters (quality, stale, sale) never change what it says.
 */
export function IngredientSummary({ ingredient, history }: { ingredient: Ingredient; history: UseQueryResult<IngredientPriceHistory> }) {
  const points = history.data?.points ?? [];
  const best = bestRecent(points);
  const unit = points[0]?.norm_unit ?? ingredient.canonical_unit;

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {/* Olive marks the cheapest price (spec 08). */}
      <section aria-labelledby="best-price" className={`${card} border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950`}>
        <h2 id="best-price" className="text-sm font-medium text-green-900 dark:text-green-200">
          Best recent price
        </h2>
        {history.isPending ? (
          <p role="status" className="mt-1 text-sm text-green-900 dark:text-green-200">
            Loading…
          </p>
        ) : best ? (
          <>
            <p className="font-display mt-1 text-2xl tabular-nums">{formatUnitPrice(best.norm_unit_price, best.norm_unit)}</p>
            <p className="text-sm text-green-900 dark:text-green-200">
              {best.product_name} · {best.vendor_name} · {formatDate(best.observed_at)}
            </p>
          </>
        ) : (
          <p className="mt-1 text-sm text-green-900 dark:text-green-200">No comparable price in the last {WINDOW_DAYS} days.</p>
        )}
      </section>

      <section aria-labelledby="last-days" className={`${card} border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900`}>
        <h2 id="last-days" className="text-sm font-medium">
          Last {WINDOW_DAYS} days
        </h2>
        {history.isPending ? (
          <p role="status" className={`mt-1 ${muted}`}>
            Loading…
          </p>
        ) : history.isError ? (
          <p className={`mt-1 ${muted}`}>Couldn&apos;t load the price history.</p>
        ) : history.data.low === null ? (
          <p className={`mt-1 ${muted}`}>No comparable prices yet.</p>
        ) : (
          <>
            <p className="mt-1 text-sm tabular-nums" data-testid="price-range">
              {history.data.low === history.data.high
                ? formatUnitPrice(history.data.low, unit)
                : `${formatUnitPrice(history.data.low, unit)} – ${formatUnitPrice(history.data.high, unit)}`}
            </p>
            {/* A line needs two points; one price is said by the range alone (D22). */}
            {points.length >= 2 ? (
              <div className="mt-2">
                <Sparkline points={points} label={`Cheapest price each day over the last ${WINDOW_DAYS} days`} />
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
