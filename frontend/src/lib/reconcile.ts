import type { Purchase, PurchaseLine } from "../api/purchases";
import { add, cmp, isDecimal, mul, roundTo, sub } from "./decimal";

// Why a receipt's lines miss its printed total (2S, U1). Pure: the review page
// renders what this finds, and each hint names the lines a fix would change.

/** Mirrors RECONCILE_TOLERANCE: two cents of rounding is not a gap. */
const TOLERANCE = "0.02";

/** Rows kept at zero for review, which are not purchases of their own. */
const ZERO_ROW_FLAGS = [
  "quantity_line",
  "no_amount_printed",
  "footer_text",
  "payment_row",
  "continuation_row",
  "points_not_money",
  "rate_note",
  "deposit_rate_row",
  "deposit_total",
];

export interface Breakdown {
  items: string;
  /** Savings counted against the items. */
  savings: string;
  /** Savings printed but already in their items' prices, counted as nothing. */
  inPrice: string;
  other: string;
  tax: string;
  linesTotal: string;
  total: string | null;
  /** linesTotal less the printed total; null when there is no total. */
  gap: string | null;
}

export interface Check {
  key: "payment" | "savings" | "items";
  label: string;
  printed: string;
  read: string;
  ok: boolean;
}

export type Hint =
  | { kind: "savings_in_price"; lines: PurchaseLine[]; amount: string }
  | { kind: "saving_misread"; line: PurchaseLine; from: string; to: string }
  | { kind: "weights_not_joined"; lines: PurchaseLine[] }
  | { kind: "line_is_gap"; line: PurchaseLine; amount: string };

function absolute(a: string): string {
  return cmp(a, "0") < 0 ? sub("0", a) : a;
}

function near(a: string, b: string, tolerance = TOLERANCE): boolean {
  return cmp(absolute(sub(a, b)), tolerance) <= 0;
}

function sum(values: string[]): string {
  return values.reduce((acc, v) => add(acc, v), "0");
}

function total(line: PurchaseLine): string {
  return line.line_total !== null && isDecimal(line.line_total) ? line.line_total : "0";
}

/** A saving that is subtracted from the receipt, as against one already in a price. */
export function countedSaving(line: PurchaseLine): boolean {
  return line.line_kind === "discount" && cmp(total(line), "0") > 0;
}

export function isZeroRow(line: PurchaseLine): boolean {
  return line.flags.some((f) => ZERO_ROW_FLAGS.includes(f));
}

export function breakdown(purchase: Purchase): Breakdown {
  const of = (kind: string) => sum(purchase.lines.filter((l) => l.line_kind === kind).map(total));
  const items = of("item");
  const savings = of("discount");
  const inPrice = sum(purchase.lines.map((l) => l.in_price_amount ?? "0"));
  const other = add(of("deposit"), of("fee"));
  const taxLines = purchase.lines.some((l) => l.line_kind === "tax");
  const tax = taxLines ? of("tax") : (purchase.tax ?? "0");
  const linesTotal =
    purchase.lines_total ?? purchase.computed_total ?? add(sub(add(items, other), savings), tax);
  const printed = purchase.total;
  const cents = (a: string) => roundTo(a, 2);
  return {
    items: cents(items),
    savings: cents(savings),
    inPrice: cents(inPrice),
    other: cents(other),
    tax: cents(tax),
    linesTotal: cents(linesTotal),
    total: printed,
    gap: printed === null ? null : roundTo(sub(linesTotal, printed), 2),
  };
}

/** Item lines that are purchases: not a weight row or note kept at zero. */
export function purchasedItems(purchase: Purchase): PurchaseLine[] {
  return purchase.lines.filter((l) => l.line_kind === "item" && !isZeroRow(l));
}

/** What the receipt prints about itself, against what was read. */
export function checks(purchase: Purchase): Check[] {
  const printed = purchase.printed_checks;
  if (!printed) return [];
  const out: Check[] = [];
  if (printed.payment && purchase.total !== null) {
    out.push({
      key: "payment",
      label: "Paid",
      printed: printed.payment,
      read: purchase.total,
      ok: near(printed.payment, purchase.total, "0"),
    });
  }
  if (printed.savings_total) {
    const read = add(breakdown(purchase).savings, breakdown(purchase).inPrice);
    out.push({
      key: "savings",
      label: "Savings",
      printed: printed.savings_total,
      read,
      ok: near(printed.savings_total, read, "0"),
    });
  }
  if (printed.item_count !== null && printed.item_count !== undefined) {
    const read = purchasedItems(purchase).length;
    out.push({
      key: "items",
      label: "Items",
      printed: String(printed.item_count),
      read: String(read),
      ok: printed.item_count === read,
    });
  }
  return out;
}

/** Whether the printed total is what was paid, so the lines are what is off. */
export function totalIsPaid(purchase: Purchase): boolean {
  return checks(purchase).some((c) => c.key === "payment" && c.ok);
}

/** Two amounts printed alike but for one digit: 0.60 read for 0.50. */
export function oneDigitOff(a: string, b: string): boolean {
  const [x, y] = [roundTo(a, 2), roundTo(b, 2)];
  if (x.length !== y.length) return false;
  let differ = 0;
  for (let i = 0; i < x.length; i += 1) if (x[i] !== y[i]) differ += 1;
  return differ === 1;
}

function savingMisread(purchase: Purchase): Hint | null {
  const savings = checks(purchase).find((c) => c.key === "savings" && !c.ok);
  if (!savings) return null;
  const diff = sub(savings.printed, savings.read);
  const fits = purchase.lines.filter((l) => {
    if (l.line_kind !== "discount") return false;
    const amount = l.in_price_amount ?? total(l);
    const to = add(amount, diff);
    return cmp(to, "0") > 0 && oneDigitOff(amount, to);
  });
  if (fits.length !== 1) return null;
  const from = fits[0].in_price_amount ?? total(fits[0]);
  return { kind: "saving_misread", line: fits[0], from, to: roundTo(add(from, diff), 2) };
}

/** Weight or count rows left on their own: each should be part of an item beside it. */
function weightsNotJoined(purchase: Purchase): Hint | null {
  const loose = purchase.lines.filter((l) => l.flags.includes("quantity_line"));
  return loose.length === 0 ? null : { kind: "weights_not_joined", lines: loose };
}

/**
 * The shapes a gap usually has, most specific first: every saving counted twice,
 * a saving misread by one digit, weight rows not joined, or one line's amount.
 */
export function hints(purchase: Purchase): Hint[] {
  const out: Hint[] = [];
  const { gap } = breakdown(purchase);
  const counted = purchase.lines.filter(countedSaving);
  const savings = sum(counted.map(total));
  if (gap !== null && counted.length > 0 && cmp(gap, "0") < 0 && near(absolute(gap), savings)) {
    out.push({ kind: "savings_in_price", lines: counted, amount: roundTo(savings, 2) });
  }
  const misread = savingMisread(purchase);
  if (misread) out.push(misread);
  const loose = weightsNotJoined(purchase);
  if (loose) out.push(loose);
  if (gap !== null && out.length === 0 && !near(gap, "0")) {
    const want = absolute(gap);
    const kind = cmp(gap, "0") > 0 ? "item" : "discount";
    const same = purchase.lines.filter((l) => l.line_kind === kind && near(total(l), want));
    if (same.length === 1) out.push({ kind: "line_is_gap", line: same[0], amount: want });
  }
  return out;
}

/** The weight at its rate, to the cent: what a joined item should come to. */
export function weightComesTo(line: PurchaseLine): string | null {
  if (line.qty === null || line.unit_price === null) return null;
  return roundTo(mul(line.qty, line.unit_price), 2);
}

/** The one neighbour a loose weight row fits, as the reader joins them: its amount is the weight at the rate. */
export function weightFits(
  line: PurchaseLine,
  neighbours: { above?: PurchaseLine; below?: PurchaseLine },
): { into: PurchaseLine; where: "above" | "below"; amount: string } | null {
  const amount = weightComesTo(line);
  if (amount === null) return null;
  const fits = (["above", "below"] as const).filter((where) => {
    const other = neighbours[where];
    // A cent, as the reader joins them: the weight at the rate rounds to the amount.
    return other?.line_kind === "item" && near(total(other), amount, "0.01") && !isZeroRow(other);
  });
  const into = fits.length === 1 ? neighbours[fits[0]] : undefined;
  return into ? { into, where: fits[0], amount } : null;
}

export interface RunningTotal {
  /** What the lines come to through this one, in printed order. */
  soFar: string;
  /** The receipt's total is reached here, before the last line that counts. */
  reachesTotal: boolean;
}

/** How a line counts toward the total: a saving takes away, the rest adds. */
export function signedAmount(line: PurchaseLine): string {
  return line.line_kind === "discount" ? sub("0", total(line)) : total(line);
}

/**
 * The lines' sum after each line, in printed order (2S): read down the column
 * to see where the sum leaves the receipt's arithmetic. A line where the sum
 * already equals the printed total, with lines still counting after it, marks
 * where the extra lines begin.
 */
export function runningTotals(purchase: Purchase): Map<string, RunningTotal> {
  const lines = [...purchase.lines].sort((a, b) => a.seq - b.seq);
  const lastCounting = lines.filter((l) => cmp(total(l), "0") !== 0).at(-1);
  const out = new Map<string, RunningTotal>();
  let soFar = "0";
  for (const line of lines) {
    soFar = add(soFar, signedAmount(line));
    const reaches =
      purchase.total !== null &&
      line.id !== lastCounting?.id &&
      cmp(total(line), "0") !== 0 &&
      near(soFar, purchase.total, "0");
    out.set(line.id, { soFar: roundTo(soFar, 2), reachesTotal: reaches });
  }
  return out;
}

/** A new header total that is just what the lines add up to, on a receipt whose lines are off. */
export function totalHidingGap(purchase: Purchase, newTotal: string | undefined): string | null {
  if (newTotal === undefined || purchase.trust !== "check_lines") return null;
  const lines = purchase.lines_total ?? null;
  return lines !== null && isDecimal(newTotal) && cmp(newTotal, lines) === 0 ? newTotal : null;
}
