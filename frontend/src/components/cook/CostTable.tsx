import { Link } from "react-router";
import { formatCostRange, formatQuantity, lineText, pricedSentence, type CostLine, type RecipeCost } from "../../api/recipes";
import { formatMoney, isZero, mul, roundTo, stripZeros } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { Badge } from "../catalog/fields";
import { formatUnitPrice } from "../pricebook/PriceAge";
import { focusRing, tapTarget } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";
const squash = "text-amber-900 dark:text-amber-200";

/** "12%" of the consumed cost resting on unconfirmed bridges, or null at zero. */
export function unconfirmedPercent(share: string): string | null {
  if (isZero(share)) return null;
  const pct = stripZeros(roundTo(mul(share, "100"), 0));
  return `${pct === "0" ? "<1" : pct}%`;
}

/**
 * The totals strip (10, Cost table): Consumed, Basket, Per serving, Known, and
 * the unconfirmed share in squash when above zero. A provisional snapshot carries
 * a neutral "Provisional" badge with its reason (UI-7.4).
 */
export function CostTotals({ cost, costing }: { cost: RecipeCost | undefined; costing: boolean }) {
  const unconfirmed = cost ? unconfirmedPercent(cost.unconfirmed_share) : null;
  const partial = cost !== undefined && cost.completeness.lines_priced < cost.completeness.lines_total;
  const figure = (label: string, value: string | null) => (
    <div className="flex min-w-0 flex-col">
      <dt className={`text-xs ${muted}`}>{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{costing ? "Costing…" : (value ?? "—")}</dd>
    </div>
  );
  return (
    <section aria-label="Totals" className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900" data-testid="cost-totals">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {figure("Consumed", cost ? formatCostRange(cost.totals.consumed_cost, cost.totals.consumed_cost_high) : null)}
        {figure("Basket", cost ? formatCostRange(cost.totals.basket_cost, cost.totals.basket_cost_high) : null)}
        {figure("Per serving", cost?.totals.per_serving ? formatMoney(cost.totals.per_serving) : null)}
        <div className="flex min-w-0 flex-col">
          <dt className={`text-xs ${muted}`}>Known</dt>
          <dd className={`text-sm ${partial ? squash : ""}`}>
            {costing ? "Costing…" : cost ? pricedSentence(cost.completeness.lines_priced, cost.completeness.lines_total) : "—"}
          </dd>
        </div>
      </dl>
      {unconfirmed ? (
        <p className={`text-sm ${squash}`} data-testid="unconfirmed-share">
          {unconfirmed} rests on unconfirmed bridges
        </p>
      ) : null}
      {cost?.provisional ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge>Provisional</Badge>
          <span className={muted}>The file has uncommitted changes.</span>
        </p>
      ) : null}
    </section>
  );
}

const headCell = `px-3 py-2 text-left text-xs font-semibold ${muted}`;
// Line, Ingredient, Quantity, Price used, Cost.
const columns = "lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,1.6fr)_minmax(0,0.9fr)]";

/**
 * The cost table (10, Cost table; UI-7.9): each line as written, its ingredient,
 * the converted quantity in the display unit as the server sends it, the price
 * used with product, vendor and date, and its cost. Every row state is words.
 * At 390px each line is a block and the table never scrolls sideways (UI-7.18).
 */
export function CostTable({ cost }: { cost: RecipeCost }) {
  if (cost.lines.length === 0) {
    return <p className={`text-sm ${muted}`}>This recipe names no ingredients, so there is nothing to cost.</p>;
  }
  return (
    <div role="table" aria-label="Cost by line" className="rounded-lg border border-neutral-200 bg-white text-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div role="rowgroup" className="hidden lg:block">
        <div role="row" className={`grid border-b border-neutral-200 dark:border-neutral-800 ${columns}`}>
          <div role="columnheader" className={headCell}>
            Line
          </div>
          <div role="columnheader" className={headCell}>
            Ingredient
          </div>
          <div role="columnheader" className={headCell}>
            Quantity
          </div>
          <div role="columnheader" className={headCell}>
            Price used
          </div>
          <div role="columnheader" className={`${headCell} lg:text-right`}>
            Cost
          </div>
        </div>
      </div>
      <div role="rowgroup" className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {cost.lines.map((line) => (
          <CostRow key={line.id} line={line} staleAfterDays={cost.stale_after_days} />
        ))}
      </div>
    </div>
  );
}

function CostRow({ line, staleAfterDays }: { line: CostLine; staleAfterDays: number }) {
  const ingredient = line.line;
  const price = line.price;
  const packs = line.packs && !isZero(line.packs) ? `${stripZeros(line.packs)}${line.packs_high && line.packs_high !== line.packs ? `–${stripZeros(line.packs_high)}` : ""} ${line.packs === "1" ? "pack" : "packs"}` : null;
  return (
    <div role="row" className={`grid gap-x-3 gap-y-1 px-3 py-3 lg:items-start lg:py-2 ${columns}`} data-testid="cost-row" data-status={line.status}>
      {/* Line: the text as written, in monospace, like a receipt's. */}
      <div role="cell" className="min-w-0 font-mono text-xs leading-5 break-words lg:text-sm">
        {lineText(ingredient)}
        {line.pinned ? <span className={`ml-2 font-sans text-xs ${muted}`}>pinned</span> : null}
      </div>
      {/* Ingredient: the resolved ingredient, or the words for a line that needs one (the picker is the next package). */}
      <div role="cell" className="min-w-0">
        {ingredient.ingredient_id && ingredient.ingredient_name ? (
          <Link to={`/catalog/ingredients/${ingredient.ingredient_id}`} className={`${tapTarget} rounded font-medium underline-offset-2 hover:underline ${focusRing}`}>
            {ingredient.ingredient_name}
          </Link>
        ) : line.status === "negligible" ? (
          <span className={muted}>Not costed</span>
        ) : (
          <span className={muted}>Needs an ingredient</span>
        )}
      </div>
      {/* Quantity: converted, in the display unit the server chose (UI-3.10a). */}
      <div role="cell" className="min-w-0">
        <span className="flex flex-wrap items-baseline gap-x-2 lg:block">
          <span className={`lg:hidden ${muted}`}>Quantity</span>
          <span className="tabular-nums">{line.quantity ? formatQuantity(line.quantity.display_qty, line.quantity.display_qty_high, line.quantity.display_unit) : "—"}</span>
        </span>
        {line.yield_assumed ? <span className={`block text-xs ${squash}`}>yield 100% assumed</span> : null}
        {line.yield_mode !== "auto" ? <span className={`block text-xs ${muted}`}>{line.yield_mode === "as_purchased" ? "as purchased" : "edible"}</span> : null}
      </div>
      {/* Price used, or the words for why there is none. */}
      <div role="cell" className="min-w-0">
        {price ? (
          <>
            <span className="flex flex-wrap items-center gap-x-2">
              <span className={`lg:hidden ${muted}`}>Price</span>
              <span className="tabular-nums">{formatUnitPrice(price.display_unit_price, price.display_unit)}</span>
              {price.stale ? (
                <Badge tone="warn">
                  <span className="sr-only">older than {staleAfterDays} days: </span>stale
                </Badge>
              ) : null}
            </span>
            <span className={`block text-xs ${muted}`}>
              {[price.product_name, price.vendor_name ?? price.location_name, price.observed_at ? formatDate(price.observed_at) : null].filter(Boolean).join(" · ")}
            </span>
          </>
        ) : (
          <RowWords line={line} />
        )}
      </div>
      {/* Cost: consumed, with the basket beneath. */}
      <div role="cell" className="tabular-nums lg:text-right">
        <span className="flex items-baseline justify-between gap-2 lg:block">
          <span className={`lg:hidden ${muted}`}>Cost</span>
          <span className={line.consumed_cost ? "font-medium" : muted}>{formatCostRange(line.consumed_cost, line.consumed_cost_high)}</span>
        </span>
        {line.basket_cost && packs ? (
          <span className={`block text-xs ${muted}`}>
            {packs} · {formatCostRange(line.basket_cost, line.basket_cost_high)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** The row states, each in words (10, Row states; UI-7.9). */
function RowWords({ line }: { line: CostLine }) {
  const link = `rounded font-medium underline ${focusRing}`;
  switch (line.status) {
    case "unmapped":
      return <span className={muted}>Needs an ingredient</span>;
    case "unpriced":
      return (
        <span className={muted}>
          No price yet ·{" "}
          <Link to="/shop/shelf-prices" className={link}>
            Log a shelf price
          </Link>
        </span>
      );
    case "unconvertible":
      return (
        <span className={muted}>
          Can&apos;t convert yet ·{" "}
          {line.line.ingredient_id ? (
            <Link to={`/catalog/ingredients/${line.line.ingredient_id}#density-heading`} className={link}>
              add density
            </Link>
          ) : (
            "add density"
          )}
        </span>
      );
    case "negligible":
      return <span className={muted}>Not costed</span>;
    default:
      return <span className={muted}>—</span>;
  }
}
