import type { LineAddInput, Purchase, PurchaseLine } from "../../api/purchases";
import { formatMoney } from "../../lib/decimal";
import { Alert, Button } from "../ui";

/**
 * The last amount a row prints ("FERNLEAF BASIL $0.89" gives 0.89, "1,234.56"
 * gives 1234.56), unless it is negative ("-2.50" or "2.50-").
 */
export function rowAmount(row: string): string | null {
  const amounts = [
    ...row.matchAll(/(-?)\$?(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)[.,](\d{2})(?![\d,])(-?)/g),
  ];
  const last = amounts.at(-1);
  if (!last || last[1] === "-" || last[4] === "-") return null;
  return `${last[2].replaceAll(",", "")}.${last[3]}`;
}

interface UnreadRowsProps {
  purchase: Purchase;
  lines: PurchaseLine[];
  busy: boolean;
  onAdd: (input: LineAddInput) => void;
}

/**
 * Rows the scan prints like purchases that no line accounts for (issue 181),
 * each with a one-click "Add it" as an item at the amount it prints. A row
 * already added as a line says so instead.
 */
export function UnreadRows({ purchase, lines, busy, onAdd }: UnreadRowsProps) {
  const rows = purchase.printed_checks?.unread_rows ?? [];
  if (rows.length === 0) {
    return (
      <Alert tone="info">
        Some rows on the receipt print an amount but were not read as lines. Compare the lines with
        the receipt image and add any that are missing.
      </Alert>
    );
  }
  // Each saved line answers one unread row, so a row printed twice and added once
  // still offers the second.
  const saved = new Map<string | null, number>();
  for (const line of lines) saved.set(line.raw_text, (saved.get(line.raw_text) ?? 0) + 1);
  const added = rows.map((row) => {
    const left = saved.get(row) ?? 0;
    if (left > 0) saved.set(row, left - 1);
    return left > 0;
  });
  return (
    <Alert tone="info">
      {rows.length === 1
        ? "This row on the receipt prints an amount but was not read as a line:"
        : "These rows on the receipt print an amount but were not read as lines:"}
      <ul className="my-1 flex flex-col gap-1 pl-1">
        {rows.map((row, i) => {
          const amount = rowAmount(row);
          return (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{row}</span>
              {added[i] ? (
                <span className="text-xs">Added</span>
              ) : amount ? (
                <Button
                  variant="secondary"
                  className="min-h-11 px-2 text-xs lg:min-h-8"
                  disabled={busy}
                  onClick={() => onAdd({ raw_text: row, line_kind: "item", line_total: amount })}
                >
                  Add it at {formatMoney(amount)}
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
      Check each against the receipt image: add the ones that are purchases, here or with Add a
      line.
    </Alert>
  );
}
