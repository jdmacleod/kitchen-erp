import { formatAge } from "../../api/pricebook";
import { Badge } from "../catalog/fields";

/**
 * A unit price as the server worked it out (issue 245): "$2.99/lb", "$0.269/fl oz",
 * "$1.29 each", or "—" when the price isn't normalized. The browser does no unit
 * arithmetic: the server already chose the unit and rounded the price.
 */
export function formatUnitPrice(price: string | null | undefined, unit: string | null | undefined): string {
  if (!price || !unit) return "—";
  // The server's digits as sent: it already chose 2 or 3 places ("0.680" stays "0.680").
  const money = price.startsWith("-") ? `-$${price.slice(1)}` : `$${price}`;
  return unit === "each" ? `${money} each` : `${money}/${unit}`;
}

interface PriceAgeProps {
  observedAt: string;
  ageDays?: string | number | null;
  stale?: boolean;
}

/** Every price shown carries its age; a stale one says so in words, not colour. */
export function PriceAge({ observedAt, ageDays, stale = false }: PriceAgeProps) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1 whitespace-nowrap">
      <time dateTime={observedAt} className="text-neutral-600 dark:text-neutral-400">
        {formatAge(ageDays, observedAt)}
      </time>
      {stale ? <Badge tone="warn">stale</Badge> : null}
    </span>
  );
}

/** The numeric quality, "4/5", or "—". Numbers rather than stars: readable everywhere. */
export function QualityText({ rating }: { rating: number | null }) {
  return <span className="tabular-nums">{rating === null ? "—" : `${rating}/5`}</span>;
}

/** "sale" for a promotional observation, in words. */
export function PromoBadge({ promo }: { promo: boolean }) {
  return promo ? <Badge tone="good">sale</Badge> : null;
}
