import { tradeSentence, variantLabel, type Variant } from "../../api/trips";
import { formatMoney } from "../../lib/decimal";
import { formatDateTime, formatMinutes } from "../../lib/format";
import { Badge } from "../catalog/fields";
import { Button } from "../ui";

interface VariantCardProps {
  variant: Variant;
  /** The plan's mileage rate was given, so mileage is a line of the cost. */
  showMileage: boolean;
  chosenAt?: string | null;
  /** "Choose" is offered while no variant is chosen and the plan is open (UI-8.15). */
  canChoose: boolean;
  choosing?: boolean;
  onChoose?: (variant: Variant) => void;
}

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * One of the three plans (10, Trip page; UI-8.13): the total, its parts, the
 * trade against Balanced in money and minutes, the stops in order and "Choose".
 * The Cheapest card's heading carries the olive "cheapest" word; a card with an
 * estimated leg reads "estimated" in squash; no figure is coloured (UI-8.20).
 */
export function VariantCard({
  variant,
  showMileage,
  chosenAt = null,
  canChoose,
  choosing = false,
  onChoose,
}: VariantCardProps) {
  const chosen = Boolean(chosenAt);
  const headingId = `variant-${variant.kind}-heading`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid="variant-card"
      data-kind={variant.kind}
      data-chosen={chosen ? "true" : "false"}
      className={`flex flex-col gap-3 rounded-lg border bg-white p-4 dark:bg-neutral-900 ${chosen ? "border-neutral-400 dark:border-neutral-600" : "border-neutral-200 dark:border-neutral-800"} ${variant.kind === "balanced" ? "max-lg:order-first" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="font-display text-lg">
          {variantLabel[variant.kind]}
        </h2>
        {variant.kind === "cheapest" ? <Badge tone="good">cheapest</Badge> : null}
        {variant.estimated ? <Badge tone="warn">estimated</Badge> : null}
        {chosen ? <Badge tone="neutral">Chosen {formatDateTime(chosenAt)}</Badge> : null}
      </header>

      <p className="text-2xl tabular-nums" data-testid="variant-total">
        {formatMoney(variant.total_cost)}
        <span className={`ml-2 text-sm ${muted}`}>all in</span>
      </p>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className={muted}>Items</dt>
        <dd className="tabular-nums" data-testid="variant-items">
          {formatMoney(variant.item_cost)}
        </dd>
        <dt className={muted}>Driving</dt>
        <dd className="tabular-nums" data-testid="variant-driving">
          {formatMinutes(variant.driving_seconds)}
        </dd>
        <dt className={muted}>Shopping</dt>
        <dd className="tabular-nums" data-testid="variant-shopping">
          {formatMinutes(variant.stop_seconds)}
        </dd>
        {showMileage ? (
          <>
            <dt className={muted}>Mileage</dt>
            <dd className="tabular-nums" data-testid="variant-mileage">
              {formatMoney(variant.mileage_cost)}
            </dd>
          </>
        ) : null}
      </dl>

      <p className="text-sm" data-testid="variant-trade">
        {variant.kind === "balanced" ? (
          <span className={muted}>The reference the others are measured against.</span>
        ) : (
          tradeSentence(variant.trade)
        )}
      </p>
      {variant.estimated ? (
        <p className={`text-xs ${muted}`}>
          Drive times are estimates: the routing engine wasn&apos;t available.
        </p>
      ) : null}

      <ol
        className="flex flex-col gap-1 text-sm"
        aria-label={`${variantLabel[variant.kind]} stops`}
      >
        {variant.stops.map((stop) => (
          <li key={stop.id} className="flex items-baseline justify-between gap-3">
            <span>
              <span className={`tabular-nums ${muted}`}>{stop.seq}.</span>{" "}
              {stop.vendor_location_name}
            </span>
            <span className="tabular-nums">{formatMoney(stop.subtotal)}</span>
          </li>
        ))}
      </ol>

      {canChoose && !chosen ? (
        <div className="mt-auto pt-1">
          <Button className="w-full" disabled={choosing} onClick={() => onChoose?.(variant)}>
            {choosing ? "Choosing…" : "Choose"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
