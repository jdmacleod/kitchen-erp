import type { ReactNode } from "react";
import { trimDecimal } from "../../api/catalog";
import type { LinePatchInput, Purchase, PurchaseLine } from "../../api/purchases";
import { cmp, formatMoney, isZero } from "../../lib/decimal";
import { breakdown, type RunningTotal } from "../../lib/reconcile";
import { Badge } from "../catalog/fields";
import { Button } from "../ui";

// How a review line reads: its flags in words, what the receipt says and
// prints, and what it was read as. Shared by the table and the cards.

const printedClass = "font-mono text-[11px] text-neutral-600 dark:text-neutral-400";

/** Letters and digits only, so "PEPPERS  THAI" and "Peppers Thai" are the same text. */
function key(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * The OCR row the line was matched to (2S, U3), when it says more than the
 * model's text: usually the code and the prices the model left out.
 */
export function printedRow(line: PurchaseLine): string | null {
  const printed = line.printed_text?.trim();
  if (!printed) return null;
  return key(printed) === key(line.raw_text ?? "") ? null : printed;
}

/** The item a saving or deposit sits under: the nearest purchase printed above it. */
function itemAbove(line: PurchaseLine, itemLines: PurchaseLine[]): PurchaseLine | undefined {
  return itemLines.filter((l) => l.seq < line.seq).sort((a, b) => b.seq - a.seq)[0];
}

/**
 * The flags worth showing (U8): "parent inferred" only when the line is not
 * attached to the item right above it, where nobody would guess otherwise.
 */
export function shownFlags(line: PurchaseLine, itemLines: PurchaseLine[]): string[] {
  const above = itemAbove(line, itemLines);
  const plain = line.parent_line_id !== null && above?.id === line.parent_line_id;
  return line.flags.filter((f) => !(f === "parent_inferred" && plain));
}

/**
 * A saving the reader meant to attach but could not: review asks which item it
 * belongs to. One never attached (a coupon off the whole order) is left alone.
 */
export function orphanSaving(line: PurchaseLine): boolean {
  return (
    line.line_kind === "discount" &&
    line.parent_line_id === null &&
    line.flags.some((f) => f === "parent_inferred" || f === "parent_rejected")
  );
}

function InPriceAmount({ line }: { line: PurchaseLine }) {
  return (
    <span className="block text-xs tabular-nums">
      <s>−{formatMoney(line.in_price_amount)}</s>
      <span className="block text-neutral-600 dark:text-neutral-400">
        already in the price, not counted
      </span>
    </span>
  );
}

const small = "mt-1 min-h-11 lg:min-h-8 px-2 text-xs";

/** On a saving: count it as already in its item's price, or count it again (U6). */
export function SavingInPrice({
  line,
  busy,
  onPatch,
}: {
  line: PurchaseLine;
  busy: boolean;
  onPatch: (input: LinePatchInput) => void;
}) {
  if (line.line_kind !== "discount") return null;
  if (line.in_price_amount)
    return (
      <Button
        variant="secondary"
        className={small}
        disabled={busy}
        onClick={() => onPatch({ in_price: false })}
      >
        Count it again
      </Button>
    );
  if (!line.line_total || Number(line.line_total) <= 0) return null;
  return (
    <Button
      variant="secondary"
      className={small}
      disabled={busy}
      onClick={() => onPatch({ in_price: true })}
    >
      Already in the price
    </Button>
  );
}

/** A word OCR likely misread, with the catalog's spelling one tap away (U9). */
export function SpellingFix({
  line,
  busy,
  onPatch,
}: {
  line: PurchaseLine;
  busy: boolean;
  onPatch: (input: LinePatchInput) => void;
}) {
  const fixes = line.spelling ?? [];
  if (fixes.length === 0 || !line.raw_text) return null;
  const text = line.raw_text;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1 text-xs">
      {fixes.map(({ word, maybe }) => (
        <Button
          key={word}
          variant="secondary"
          className={small}
          disabled={busy}
          aria-label={`Change ${word} to ${maybe}`}
          onClick={() => onPatch({ raw_text: text.replace(word, maybe) })}
        >
          {word}: maybe {maybe}?
        </Button>
      ))}
    </span>
  );
}

/** A saving with no item: the items printed just above it, one tap each (U8). */
export function AttachSaving({
  line,
  itemLines,
  busy,
  onPatch,
  title,
}: {
  line: PurchaseLine;
  itemLines: PurchaseLine[];
  busy: boolean;
  onPatch: (input: LinePatchInput) => void;
  title: (l: PurchaseLine) => ReactNode;
}) {
  if (!orphanSaving(line)) return null;
  const near = itemLines
    .filter((l) => l.seq < line.seq)
    .sort((a, b) => b.seq - a.seq)
    .slice(0, 2);
  if (near.length === 0) return null;
  return (
    <span className="mt-1 block text-xs">
      Which item is this saving for?
      <span className="flex flex-wrap gap-1">
        {near.map((l) => (
          <Button
            key={l.id}
            variant="secondary"
            className={small}
            disabled={busy}
            onClick={() => onPatch({ parent_line_id: l.id })}
          >
            Line {l.seq}, {title(l)}
          </Button>
        ))}
      </span>
    </span>
  );
}

/** Mirrors backend PRICE_FLAGS: a suspected misreading, cleared when a person gives the price. */
export const PRICE_FLAGS = [
  "decimal_missing",
  "exceeds_total",
  "tax_code_as_digit",
  "no_amount_printed",
  "regular_price_from_text",
  "tax_from_rate",
  "not_in_scan",
  "points_not_money",
  "payment_row",
  "continuation_row",
  "rate_note",
  "saving_already_netted",
  "saving_amount_from_row",
  "saving_in_price_by_total",
  "deposit_rate_row",
  "deposit_total",
];

/** A line whose amount, or whether it is a line at all, is in doubt: shown first on a receipt held for a careful look. */
export function suspectLine(line: PurchaseLine): boolean {
  return line.flags.some((f) => PRICE_FLAGS.includes(f) || f === "footer_text");
}

/** What a line flag means, in words; unknown flags fall back to their code. */
const FLAG_LABELS: Record<string, string> = {
  // #31: the quantity is the default, or the line looked weighed but could not be read.
  qty_assumed: "quantity assumed",
  qty_corrected: "quantity from the print",
  qty_inferred: "quantity from the print",
  // #87: a weight or count printed on a row of its own, joined to its item or not.
  qty_from_line_above: "quantity from above",
  qty_from_line_below: "quantity from below",
  quantity_line: "only a quantity",
  // A weight row whose "lb" OCR garbled, joined by its arithmetic.
  unit_misread: "pounds, unit misread",
  // #59: a price printed with no decimal point, or a line over the whole receipt.
  decimal_missing: "decimal point missing?",
  exceeds_total: "more than the receipt total",
  // A tax letter OCR read as a third decimal ("6.378"), cut back to the cents.
  tax_code_as_digit: "tax letter read as a digit",
  // Issue 181: line structure fixed from the printed arithmetic.
  qty_from_prefix: "count printed before the name",
  no_amount_printed: "prints no amount",
  kind_from_wording: "a product, not a saving",
  regular_price_from_text: "regular price restored",
  footer_text: "footer text?",
  tax_from_rate: "tax from its rate",
  // A saving printed "8.00-", points read as money, a payment read as an item.
  negative_from_text: "printed as a saving",
  points_not_money: "points, not money",
  payment_row: "a payment, not a purchase",
  // A row of the item above (its rate, its name in another script, its code).
  continuation_row: "part of the item above",
  // A rate note ("2 @ 0.45") read as a discount; a saving the price already had.
  rate_note: "a rate note, not a saving",
  saving_already_netted: "saving already in the price",
  // 2S: a saving the item's two printed prices prove, or the totals.
  saving_in_price: "saving already in the price",
  saving_amount_from_row: "saving read from its item's prices",
  saving_in_price_by_total: "in the price, by the totals: check",
  // A deposit's rate row, or the line totalling the deposits, read as more deposit.
  deposit_rate_row: "the deposit's rate, already counted",
  deposit_total: "the deposits' total, already counted",
  // A count or weight printed beneath the item that the reader left out.
  qty_from_text: "quantity from below",
  // Issue 121: the amount is printed nowhere in the scan's text.
  not_in_scan: "not on the scan",
  // An imported line whose quantity the export never gave: no price is recorded.
  no_price: "quantity unknown, no price",
};

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replaceAll("_", " ");
}

/** The line's number, kind and flags. */
export function LineTags({ line, itemLines }: { line: PurchaseLine; itemLines: PurchaseLine[] }) {
  return (
    <>
      <span className="tabular-nums">{line.seq}</span>
      {line.line_kind !== "item" ? (
        <>
          {" "}
          <Badge>{line.line_kind}</Badge>
        </>
      ) : null}
      {shownFlags(line, itemLines).map((f) => (
        <span key={f} className="mt-1 block">
          {/* Wrapping: an unbroken "quantity from the print" made the # column
              wider than the text, pushing Product and Actions out of view. */}
          <Badge tone="warn" wrap>
            {flagLabel(f)}
          </Badge>
        </span>
      ))}
    </>
  );
}

/** In the table, a line's kind and flags sit under its receipt text, not under its number. */
export function LineFlags({ line, itemLines }: { line: PurchaseLine; itemLines: PurchaseLine[] }) {
  const flags = shownFlags(line, itemLines);
  if (line.line_kind === "item" && flags.length === 0) return null;
  return (
    <span className="mt-1 flex flex-wrap gap-1">
      {line.line_kind !== "item" ? <Badge>{line.line_kind}</Badge> : null}
      {flags.map((f) => (
        <Badge key={f} tone="warn" wrap>
          {flagLabel(f)}
        </Badge>
      ))}
    </span>
  );
}

/** What the receipt says, verbatim and normalized; one truncated line when compact. */
export function LineRaw({ line, compact }: { line: PurchaseLine; compact?: boolean }) {
  const printed = printedRow(line);
  if (compact) {
    return (
      <>
        <span className="block truncate font-mono text-xs" title={line.raw_text ?? undefined}>
          {line.raw_text ?? "—"}
        </span>
        {printed ? (
          <span className={`block truncate ${printedClass}`} title={printed}>
            <span className="sr-only">Printed: </span>
            {printed}
          </span>
        ) : null}
      </>
    );
  }
  return (
    <>
      <span className="font-mono text-xs break-words">{line.raw_text ?? "—"}</span>
      {printed ? (
        <span className={`block break-words ${printedClass}`}>
          <span className="font-sans">Printed: </span>
          {printed}
        </span>
      ) : null}
      {line.raw_text_norm && line.raw_text_norm !== line.raw_text ? (
        <span className="block text-[11px] text-neutral-600 dark:text-neutral-400">
          {line.raw_text_norm}
        </span>
      ) : null}
    </>
  );
}

/** A line's amount as it counts: a saving shows with its minus (2S). */
function counted(line: PurchaseLine): string {
  if (line.line_total === null) return "";
  return line.line_kind === "discount"
    ? `−${formatMoney(line.line_total.replace(/^-/, ""))}`
    : formatMoney(line.line_total);
}

/** The lines' sum through this line, and where it already equals the receipt's total. */
function SoFar({ running }: { running: RunningTotal | undefined }) {
  if (!running) return null;
  return (
    <span className="block text-[11px] tabular-nums text-neutral-600 dark:text-neutral-400">
      so far {formatMoney(running.soFar)}
      {running.reachesTotal ? <Badge wrap>the receipt's total</Badge> : null}
    </span>
  );
}

/** What it was read as: quantity × unit, and the prices; on one line when compact. */
export function LineParsed({
  line,
  compact,
  running,
}: {
  line: PurchaseLine;
  compact?: boolean;
  running?: RunningTotal;
}) {
  if (line.in_price_amount)
    return (
      <>
        <InPriceAmount line={line} />
        <SoFar running={running} />
      </>
    );
  const qty = line.qty !== null ? `${trimDecimal(line.qty)} × ${line.unit ?? ""}` : "—";
  if (compact) {
    return (
      <>
        <span className="tabular-nums">
          {line.line_total === null
            ? qty
            : line.qty === null
              ? counted(line)
              : `${qty} · ${counted(line)}`}
        </span>
        <SoFar running={running} />
      </>
    );
  }
  return (
    <>
      <span className="tabular-nums">{qty}</span>
      <span className="block text-xs tabular-nums">
        {line.unit_price !== null ? `${formatMoney(line.unit_price, 2, 4)} ea` : ""}
        {line.unit_price !== null && line.line_total !== null ? " · " : ""}
        {counted(line)}
      </span>
      <SoFar running={running} />
    </>
  );
}

/** The one-click fixes on a line: a spelling, a saving in the price, the item a saving is for. */
export function LineFixes({
  line,
  itemLines,
  busy,
  onPatch,
  title,
}: {
  line: PurchaseLine;
  itemLines: PurchaseLine[];
  busy: boolean;
  onPatch: (input: LinePatchInput) => void;
  title: (l: PurchaseLine) => ReactNode;
}) {
  return (
    <>
      <SpellingFix line={line} busy={busy} onPatch={onPatch} />
      <SavingInPrice line={line} busy={busy} onPatch={onPatch} />
      <AttachSaving line={line} itemLines={itemLines} busy={busy} onPatch={onPatch} title={title} />
    </>
  );
}

/** Where the running sums end, beside what the receipt says (2S). */
export function LinesTotal({ purchase }: { purchase: Purchase }) {
  const sums = breakdown(purchase);
  const headerTax = !purchase.lines.some((l) => l.line_kind === "tax") && !isZero(sums.tax);
  return (
    <p className="mt-2 border-t border-neutral-200 pt-2 text-sm tabular-nums dark:border-neutral-800">
      {headerTax ? `With the header's tax of ${formatMoney(sums.tax)}, the` : "The"} lines add up to{" "}
      <span className="font-medium">{formatMoney(sums.linesTotal)}</span>; the receipt says{" "}
      <span className="font-medium">{formatMoney(sums.total)}</span>
      {sums.gap !== null && !isZero(sums.gap)
        ? `, ${formatMoney(sums.gap.replace(/^-/, ""))} ${cmp(sums.gap, "0") < 0 ? "short" : "over"}.`
        : "."}
    </p>
  );
}
