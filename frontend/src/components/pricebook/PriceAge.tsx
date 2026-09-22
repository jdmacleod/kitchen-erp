import { formatAge } from "../../api/pricebook";
import { formatMoney } from "../../lib/decimal";
import { Badge } from "../catalog/fields";

/** A price per canonical unit, "$0.0022/g", or "—" when not normalized. */
export function formatUnitPrice(price: string | null | undefined, unit: string | null | undefined): string {
  if (!price || !unit) return "—";
  return `${formatMoney(price, 2, 6)}/${unit}`;
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
