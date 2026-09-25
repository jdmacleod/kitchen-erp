import { Link } from "react-router";
import type { Purchase } from "../../api/purchases";
import { focusRing } from "../ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";

/**
 * The last few committed purchases.
 *
 * These rows are the items of the same query whose emptiness decides whether the
 * checklist is showing, so this section cannot be empty when it renders: the home
 * mode only exists because that query returned something.
 */

const sectionLabel = "text-xs font-semibold text-neutral-600 dark:text-neutral-400";

export function Lately({ purchases }: { purchases: Purchase[] }) {
  return (
    <section className="mt-8" aria-label="Lately">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className={sectionLabel}>
          Lately
        </h2>
        <Link to="/purchases" className={`rounded text-sm underline underline-offset-2 hover:no-underline ${focusRing}`}>
          All purchases
        </Link>
      </div>

      <ul className="mt-2">
        {purchases.map((p) => (
          <li
            key={p.id}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-neutral-200 py-2 text-sm first:border-t-0 dark:border-neutral-800"
          >
            <Link
              to={`/purchases/${p.id}`}
              className={`min-w-0 flex-1 rounded font-medium underline-offset-2 hover:underline ${focusRing}`}
            >
              {p.vendor_location?.vendor.name ?? "Receipt"}
            </Link>
            <span className="text-neutral-600 dark:text-neutral-400">{formatDate(p.purchased_at)}</span>
            <span className="tabular-nums">{formatMoney(p.total ?? p.computed_total)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
