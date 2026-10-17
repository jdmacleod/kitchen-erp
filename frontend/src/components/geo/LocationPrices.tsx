import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import { useLocationPricePanel } from "../../api/pricebook";
import { cmp, formatMoney, stripZeros } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { PriceAge, PromoBadge, formatUnitPrice } from "../pricebook/PriceAge";
import { Alert, focusRing } from "../ui";

const PERIODS = [7, 30, 90, 365] as const;

/** Last visit, spend over a chosen period, and the most recent prices seen at a location. */
export function LocationPrices({ id }: { id: string }) {
  const [days, setDays] = useState<number>(30);
  const panel = useLocationPricePanel(id, days);
  return (
    <section
      aria-label="Prices here"
      className="mt-3 border-t border-neutral-200 pt-3 dark:border-neutral-800"
      data-testid="location-prices"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Prices here</h3>
        <label className="inline-flex items-center gap-1 text-xs">
          Over
          <select
            aria-label="Spend period"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className={`min-h-11 lg:min-h-8 rounded-md border border-neutral-300 bg-white px-1 text-xs dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
          >
            {PERIODS.map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </label>
      </div>
      {panel.isPending ? (
        <p role="status" className="text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : panel.isError ? (
        <Alert tone="error">{errorMessage(panel.error)}</Alert>
      ) : (
        <>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-neutral-600 dark:text-neutral-400">Last visit</dt>
            <dd>{panel.data.last_visit ? formatDateTime(panel.data.last_visit) : "never"}</dd>
            <dt className="text-neutral-600 dark:text-neutral-400">Spend</dt>
            <dd className="tabular-nums">
              {formatMoney(panel.data.spend)} over {panel.data.visits}{" "}
              {panel.data.visits === 1 ? "visit" : "visits"} in {panel.data.period_days} days
            </dd>
            {/* The part of it that went on kitchen supplies (spec 04, 2T), when any did. */}
            {panel.data.supply_spend && cmp(panel.data.supply_spend, "0") > 0 ? (
              <>
                <dt className="text-neutral-600 dark:text-neutral-400">Kitchen supplies</dt>
                <dd className="tabular-nums">{formatMoney(panel.data.supply_spend)} of it</dd>
              </>
            ) : null}
          </dl>
          {panel.data.recent.length === 0 ? (
            <p className="mt-1 text-neutral-600 dark:text-neutral-400">No prices seen here yet.</p>
          ) : (
            <ul
              aria-label="Recent prices"
              className="mt-1 divide-y divide-neutral-200 dark:divide-neutral-800"
            >
              {panel.data.recent.map((r) => (
                <li
                  key={r.observation_id}
                  className="flex flex-wrap items-baseline justify-between gap-x-2 py-1"
                >
                  <Link
                    to={`/catalog/products/${r.product_id}`}
                    className={`min-w-0 truncate rounded-sm underline-offset-2 hover:underline ${focusRing}`}
                  >
                    {r.brand ? `${r.brand} ${r.product_name}` : r.product_name}
                  </Link>
                  <span className="text-xs whitespace-nowrap tabular-nums">
                    {formatMoney(r.price)} / {stripZeros(r.qty)} {r.unit}{" "}
                    <PromoBadge promo={r.is_promo} />
                    {r.norm_unit_price ? (
                      <span className="text-neutral-600 dark:text-neutral-400">
                        {" "}
                        · {formatUnitPrice(r.display_unit_price, r.display_unit)}
                      </span>
                    ) : null}
                    {" · "}
                    <PriceAge observedAt={r.observed_at} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
