import { unitLabel } from "../../api/catalog";
import type { AmountIn, AmountOut } from "../../api/shoppingList";
import { isDecimal, isZero, stripZeros } from "../../lib/decimal";

/** One (amount, unit) pair as written: "2 lb". */
export function formatAmount(a: Pick<AmountOut, "amount" | "unit">): string {
  return `${stripZeros(a.amount)} ${unitLabel(a.unit)}`;
}

/**
 * Every pair in its own figure, joined by a middle dot ("2 lb · 1 bunch"); pairs
 * are never summed across units that do not convert (UI-8.5).
 */
export function formatAmounts(amounts: readonly Pick<AmountOut, "amount" | "unit">[]): string {
  return amounts.map(formatAmount).join(" · ");
}

/** A positive decimal as typed, or null when the text is not one. */
export function positiveAmount(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed || !isDecimal(trimmed) || isZero(trimmed) || trimmed.startsWith("-")) return null;
  return trimmed;
}

/**
 * The amount rows a form sends: none for an item with no amount, or one pair per
 * row that has both an amount and a unit. A row with one and not the other is
 * the caller's validation error.
 */
export function amountsInput(rows: readonly { amount: string; unit: string }[]): AmountIn[] {
  return rows.flatMap((row) => {
    const amount = positiveAmount(row.amount);
    return amount && row.unit ? [{ amount, unit: row.unit }] : [];
  });
}

/** The first row that is half filled: an amount with no unit, or a unit with no amount. */
export function halfFilledAmount(rows: readonly { amount: string; unit: string }[]): string | null {
  for (const row of rows) {
    const typed = row.amount.trim() !== "";
    if (typed && !positiveAmount(row.amount)) return "Enter the amount as a number above zero.";
    if (typed && !row.unit) return "Choose a unit for the amount.";
    if (!typed && row.unit) return "Enter an amount for the unit.";
  }
  return null;
}
