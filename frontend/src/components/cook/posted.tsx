// Lines costed from a posted price (issue 523): a store's listed price, the last
// resort, shown apart from the counted figures. "Posted" is defined once, in the
// cost table's header sentence, and used in that sense everywhere.

import {
  formatCostRange,
  type CostLine,
  type CostLineHint,
  type RecipeCost,
} from "../../api/recipeCosts";
import { Badge } from "../catalog/fields";

const monthYear = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });

/**
 * "3 lines use a posted price, the oldest from March 2025. A store's listed price
 * counts only when no receipt or shelf price exists." Null when no line does.
 */
export function postedHeader(lines: CostLine[]): string | null {
  const posted = lines.filter((l) => l.status === "posted");
  if (posted.length === 0) return null;
  const dates = posted
    .map((l) => l.price?.observed_at)
    .filter((d): d is string => Boolean(d))
    .sort();
  const one = posted.length === 1;
  const when = dates.length ? monthYear.format(new Date(dates[0])) : null;
  const count = `${posted.length} ${one ? "line uses" : "lines use"} a posted price`;
  const oldest = when ? (one ? `, from ${when}` : `, the oldest from ${when}`) : "";
  return `${count}${oldest}. A store's listed price counts only when no receipt or shelf price exists.`;
}

/** "$2.30" or "$2.30–2.60" of the posted part, or null when there is none to show. */
export function postedFigure(
  low: string | null | undefined,
  high: string | null | undefined,
  lines: number | undefined,
): string | null {
  if (!lines || !low) return null;
  return formatCostRange(low, high);
}

/** The neutral mark on a line costed from a posted price. */
export function PostedBadge() {
  return (
    <Badge tone="neutral">
      <span className="sr-only">a store&apos;s listed price, not counted: </span>posted
    </Badge>
  );
}

/** What an unpriced line says when a price is in sight, or null. */
export function hintText(hint: CostLineHint | null | undefined): string | null {
  switch (hint?.kind) {
    case "posted_other_product":
      return `Only ${hint.product_name} has a posted price; change the pin to use it`;
    case "posted_outside_kitchen":
      return `A posted price exists at ${hint.store_name}, outside this kitchen`;
    case "pin_moved":
      return `Pinned to ${hint.product_name}, now filed under ${hint.ingredient_name}`;
    default:
      return null;
  }
}

/** Whether the current cost rests partly on posted prices, for the chart caption. */
export function hasPosted(cost: RecipeCost | undefined): boolean {
  return (cost?.completeness.lines_posted ?? 0) > 0;
}
