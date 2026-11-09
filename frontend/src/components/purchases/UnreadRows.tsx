import type { LineAddInput, Purchase, PurchaseLine } from "../../api/purchases";
import { formatMoney } from "../../lib/decimal";
import { Alert, Button } from "../ui";

/** The last amount a row prints ("FERNLEAF BASIL $0.89" gives 0.89), unless it is negative. */
export function rowAmount(row: string): string | null {
  const amounts = [...row.matchAll(/(\d+)[.,](\d{2})(?!\d)(-?)/g)];
  const last = amounts.at(-1);
  if (!last || last[3] === "-") return null;
  return `${last[1]}.${last[2]}`;
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
  const added = new Set(lines.map((l) => l.raw_text));
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
              {added.has(row) ? (
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
