import { useState } from "react";
import { useNavigate } from "react-router";
import { trimDecimal } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import {
  purchaseErrorMessage,
  useMergeLine,
  usePatchLine,
  type Purchase,
  type PurchaseLine,
} from "../../api/purchases";
import { useRequestReread } from "../../api/rereads";
import { cmp, formatMoney, isZero, sub } from "../../lib/decimal";
import {
  breakdown,
  checks,
  hints,
  totalIsPaid,
  weightFits,
  type Breakdown,
  type Check,
  type Hint,
} from "../../lib/reconcile";
import { Alert, Button, sectionHeading } from "../ui";

const small = "min-h-11 lg:min-h-8 px-2 text-xs";

function signed(amount: string): string {
  return cmp(amount, "0") < 0 ? `−${formatMoney(sub("0", amount))}` : formatMoney(amount);
}

function Sums({ sums }: { sums: Breakdown }) {
  const rows: [string, string, boolean?][] = [
    ["Items", formatMoney(sums.items)],
    ["Savings", isZero(sums.savings) ? formatMoney("0") : `−${formatMoney(sums.savings)}`],
  ];
  if (!isZero(sums.inPrice))
    rows.push(["Savings already in the prices, not counted", formatMoney(sums.inPrice)]);
  if (!isZero(sums.other)) rows.push(["Deposits and fees", formatMoney(sums.other)]);
  rows.push(["Tax", formatMoney(sums.tax)]);
  rows.push(["Lines add up to", formatMoney(sums.linesTotal), true]);
  rows.push(["The receipt's total", formatMoney(sums.total), true]);
  return (
    <dl className="grid max-w-md grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm tabular-nums">
      {rows.map(([label, value, strong]) => (
        <div key={label} className="contents">
          <dt className={strong ? "font-medium" : "text-neutral-700 dark:text-neutral-300"}>
            {label}
          </dt>
          <dd className={`text-right ${strong ? "font-medium" : ""}`}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CheckRow({ check }: { check: Check }) {
  const money = check.key !== "items";
  const printed = money ? formatMoney(check.printed) : check.printed;
  const read = money ? formatMoney(check.read) : check.read;
  const what =
    check.key === "payment"
      ? check.ok
        ? `Paid ${printed}, the same as the total.`
        : `Paid ${printed}; the total reads ${read}.`
      : check.key === "savings"
        ? `The receipt prints ${printed} in savings; the lines have ${read}.`
        : `The receipt counts ${printed} items; ${read} lines are items.`;
  return (
    <li className="flex items-start gap-2">
      <span aria-hidden="true" className={check.ok ? "text-green-800 dark:text-green-300" : ""}>
        {check.ok ? "✓" : "✗"}
      </span>
      <span>
        <span className="sr-only">{check.ok ? "Agrees: " : "Disagrees: "}</span>
        {what}
      </span>
    </li>
  );
}

interface HintProps {
  hint: Hint;
  lines: PurchaseLine[];
  busy: boolean;
  run: (steps: (() => Promise<unknown>)[]) => void;
  patch: ReturnType<typeof usePatchLine>;
  merge: ReturnType<typeof useMergeLine>;
  onShowLine: (lineId: string) => void;
}

function inPrice(patch: HintProps["patch"], line: PurchaseLine) {
  return () => patch.mutateAsync({ lineId: line.id, in_price: true });
}

function HintRow({ hint, lines, busy, run, patch, merge, onShowLine }: HintProps) {
  switch (hint.kind) {
    case "savings_in_price":
      return (
        <>
          The gap is exactly the{" "}
          {hint.lines.length === 1 ? "saving" : `${hint.lines.length} savings`} (
          {formatMoney(hint.amount)}). The prices may already include them, as on a receipt that
          prints a Price and a You Pay column.
          <Button
            variant="secondary"
            className={`mt-2 block ${small}`}
            disabled={busy}
            onClick={() => run(hint.lines.map((l) => inPrice(patch, l)))}
          >
            Count {hint.lines.length === 1 ? "it" : "them"} as already in the price
          </Button>
        </>
      );
    case "saving_misread": {
      const { line, to } = hint;
      const steps = [() => patch.mutateAsync({ lineId: line.id, line_total: to })];
      if (line.in_price_amount) steps.push(inPrice(patch, line));
      return (
        <>
          The saving on line {line.seq} reads {formatMoney(hint.from)}; {formatMoney(to)} would make
          the savings agree with the receipt. Check it on the photo.
          <Button
            variant="secondary"
            className={`mt-2 block ${small}`}
            disabled={busy}
            onClick={() => run(steps)}
          >
            Use {formatMoney(to)} on line {line.seq}
          </Button>
        </>
      );
    }
    case "weights_not_joined":
      return (
        <>
          {hint.lines.length === 1
            ? "A weight row isn't"
            : `${hint.lines.length} weight rows aren't`}{" "}
          joined to {hint.lines.length === 1 ? "its item" : "their items"}, so they count as items
          of their own.
          <span className="mt-2 flex flex-wrap gap-1">
            {hint.lines.map((l) => {
              const at = lines.indexOf(l);
              const fit = weightFits(l, { above: lines[at - 1], below: lines[at + 1] });
              if (!fit)
                return (
                  <Button
                    key={l.id}
                    variant="secondary"
                    className={small}
                    onClick={() => onShowLine(l.id)}
                  >
                    Show line {l.seq}
                  </Button>
                );
              return (
                <Button
                  key={l.id}
                  variant="secondary"
                  className={small}
                  disabled={busy}
                  onClick={() =>
                    run([() => merge.mutateAsync({ lineId: l.id, intoLineId: fit.into.id })])
                  }
                >
                  Join line {l.seq} to line {fit.into.seq} ({trimDecimal(l.qty ?? "")} {l.unit} ={" "}
                  {formatMoney(fit.amount)})
                </Button>
              );
            })}
          </span>
        </>
      );
    case "line_is_gap":
      return hint.line.line_kind === "discount" ? (
        <>
          The saving on line {hint.line.seq} is exactly the gap ({formatMoney(hint.amount)}). It may
          already be in its item's price.
          <Button
            variant="secondary"
            className={`mt-2 block ${small}`}
            disabled={busy}
            onClick={() => run([inPrice(patch, hint.line)])}
          >
            Count it as already in the price
          </Button>
        </>
      ) : (
        <>
          Line {hint.line.seq} comes to exactly the gap ({formatMoney(hint.amount)}). It may be read
          twice, or not be a purchase.
          <Button
            variant="secondary"
            className={`mt-2 block ${small}`}
            onClick={() => onShowLine(hint.line.id)}
          >
            Show line {hint.line.seq}
          </Button>
        </>
      );
  }
}

/**
 * A receipt read before lines kept their printed rows (2S) has none, and none of
 * the receipt's own checks: today's line rules, run again on the same answer,
 * may explain the gap themselves. Instant, no model.
 */
function RecheckOffer({ purchase }: { purchase: Purchase }) {
  const request = useRequestReread(purchase.id);
  const navigate = useNavigate();
  const state = purchase.reread;
  const readBefore = !purchase.printed_checks && !purchase.lines.some((l) => l.printed_text);
  if (!readBefore || !state?.can_recheck || state.open) return null;
  return (
    <div className="rounded-md border border-amber-200 bg-neutral-50 px-3 py-2 dark:border-amber-900 dark:bg-neutral-900">
      This receipt was read before today's line rules, which match each line to its printed row and
      read the receipt's own totals. Re-checking runs them on the same reading, instantly.
      <span className="mt-2 block">
        <Button
          variant="secondary"
          className={small}
          disabled={request.isPending}
          onClick={() =>
            request.mutate("check", {
              onSuccess: () => void navigate(`/shop/purchases/${purchase.id}/reread`),
            })
          }
        >
          Re-check the lines
        </Button>
      </span>
      {request.isError ? <Alert tone="error">{errorMessage(request.error)}</Alert> : null}
    </div>
  );
}

/**
 * Why a receipt's lines miss its printed total (2S, U1): the sums side by side,
 * what the receipt prints about itself, and the shapes the gap matches, each
 * with the correction it suggests. Every fix is one a person can undo.
 */
export function WhyOff({
  purchase,
  onShowLine,
}: {
  purchase: Purchase;
  onShowLine: (lineId: string) => void;
}) {
  const patch = usePatchLine(purchase.id);
  const merge = useMergeLine(purchase.id);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const sums = breakdown(purchase);
  if (sums.gap === null || cmp(sums.gap, "0") === 0) return null;
  const lines = [...purchase.lines].sort((a, b) => a.seq - b.seq);
  const found = hints(purchase);
  const busy = working || patch.isPending || merge.isPending;
  const run = (steps: (() => Promise<unknown>)[]) => {
    setWorking(true);
    setError(null);
    void (async () => {
      try {
        for (const step of steps) await step();
      } catch (e) {
        setError(e);
      } finally {
        setWorking(false);
      }
    })();
  };
  return (
    <section
      aria-labelledby="why-off-h"
      className="flex flex-col gap-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-50"
    >
      <h2 id="why-off-h" className={sectionHeading}>
        The lines are off by {signed(sums.gap).replace("−", "")}
      </h2>
      <p>
        {totalIsPaid(purchase)
          ? "The total matches what was paid, so a line is off."
          : "Compare the lines with the receipt photo. If the total itself was misread, correct it in the header."}
      </p>
      <Sums sums={sums} />
      <RecheckOffer purchase={purchase} />
      {checks(purchase).length > 0 ? (
        <ul aria-label="What the receipt prints about itself" className="flex flex-col gap-1">
          {checks(purchase).map((c) => (
            <CheckRow key={c.key} check={c} />
          ))}
        </ul>
      ) : null}
      {found.length > 0 ? (
        <ul aria-label="What may explain it" className="flex flex-col gap-2">
          {found.map((hint) => (
            <li
              key={`${hint.kind}-${"line" in hint ? hint.line.id : ""}`}
              className="rounded-md border border-amber-200 bg-neutral-50 px-3 py-2 dark:border-amber-900 dark:bg-neutral-900"
            >
              <HintRow
                hint={hint}
                lines={lines}
                busy={busy}
                run={run}
                patch={patch}
                merge={merge}
                onShowLine={onShowLine}
              />
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <Alert tone="error">{purchaseErrorMessage(error)}</Alert> : null}
    </section>
  );
}
