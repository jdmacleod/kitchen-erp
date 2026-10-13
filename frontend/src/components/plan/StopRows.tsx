import { useId, useRef, useState, type FormEvent } from "react";
import { formatPack } from "../../api/catalog";
import { errorMessage, isApiError } from "../../api/client";
import type { CheaperElsewhereItem, StopScreenItem } from "../../api/trips";
import { useClearStopPrice, useHoldStopPrice } from "../../api/trips";
import { cmp, formatMoney, isNonNegativeDecimal, stripZeros, sub } from "../../lib/decimal";
import { Badge } from "../catalog/fields";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Field, focusRing } from "../ui";
import { formatAmounts, positiveAmount } from "./amounts";

const muted = "text-neutral-600 dark:text-neutral-400";

/** The 28px circle inside a 44px hit area (10, At the store; UI-8.17). */
export function CheckCircle({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden="true"
      data-testid="check-circle"
      className={`inline-flex size-7 items-center justify-center rounded-full border-2 ${
        checked
          ? "border-green-700 bg-green-700 text-white dark:border-green-500 dark:bg-green-500 dark:text-neutral-950"
          : "border-neutral-400 bg-white dark:border-neutral-500 dark:bg-neutral-900"
      }`}
    >
      {checked ? (
        <svg
          viewBox="0 0 16 16"
          className="size-4"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 8.5l3 3 7-7" />
        </svg>
      ) : null}
    </span>
  );
}

/** "2 packs of 16 oz", "1 pack", or "by amount" when the product has no pack size. */
export function plannedPacks(item: Pick<StopScreenItem, "packs" | "product">): string {
  if (item.packs === null) return "by amount";
  const pack = formatPack(
    item.product.pack_qty,
    item.product.pack_unit,
    item.product.pack_count,
    item.product.piece_name,
  );
  const n = stripZeros(item.packs);
  return `${n} ${cmp(item.packs, "1") === 0 ? "pack" : "packs"}${pack ? ` of ${pack}` : ""}`;
}

/** "+$0.80 here" when it costs more here, "−$0.30 here" when less, "the same here" otherwise. */
export function differenceHere(difference: string): string {
  const c = cmp(difference, "0");
  if (c === 0) return "the same here";
  return c > 0 ? `+${formatMoney(difference)} here` : `−${formatMoney(sub("0", difference))} here`;
}

interface StopItemRowProps {
  item: StopScreenItem;
  planId: string;
  seq: number;
  checked: boolean;
  /** A done or ended stop: nothing on the row acts. */
  readOnly: boolean;
  onToggle: (item: StopScreenItem) => void;
  /** A 409 from the server: the stop changed under this screen. */
  onConflict: (e: unknown) => void;
}

/**
 * One item to get here: the check circle, the name and amounts, the planned
 * packs, the product, and the price as a 44px button that opens the inline
 * editor. An edited price tints the row neutral, reads "edited" and keeps the
 * plan's figure as "planned $x" (UI-8.17).
 */
export function StopItemRow({
  item,
  planId,
  seq,
  checked,
  readOnly,
  onToggle,
  onConflict,
}: StopItemRowProps) {
  const [editing, setEditing] = useState(false);
  const priceButton = useRef<HTMLButtonElement>(null);
  const amounts = formatAmounts(item.amounts);
  const close = () => {
    setEditing(false);
    // Back to the price, so the keyboard picks up where it left off.
    requestAnimationFrame(() => priceButton.current?.focus());
  };

  return (
    <li
      data-testid="stop-item"
      data-id={item.id}
      data-checked={checked ? "true" : "false"}
      data-edited={item.edited ? "true" : "false"}
      className={`rounded-md px-1 py-2 ${item.edited ? "bg-neutral-100 dark:bg-neutral-800" : ""}`}
    >
      <div className="flex items-start gap-2">
        {readOnly ? (
          <span className="inline-flex size-11 shrink-0 items-center justify-center lg:size-8">
            <CheckCircle checked={checked} />
            <span className="sr-only">{checked ? "In the basket" : "Not bought"}</span>
          </span>
        ) : (
          <button
            type="button"
            role="checkbox"
            aria-checked={checked}
            aria-label={checked ? `Uncheck ${item.label}` : `Check ${item.label}`}
            onClick={() => onToggle(item)}
            className={`inline-flex size-11 shrink-0 items-center justify-center rounded-md ${focusRing}`}
          >
            <CheckCircle checked={checked} />
          </button>
        )}

        <div className="min-w-0 flex-1 pt-2">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span
              className={`font-medium ${checked ? `line-through ${muted}` : ""}`}
              data-testid="item-name"
            >
              {item.label}
            </span>
            {amounts ? <span className="text-sm tabular-nums">{amounts}</span> : null}
          </div>
          <p className={`text-sm tabular-nums ${muted}`} data-testid="item-packs">
            {plannedPacks(item)}
          </p>
          <p className={`text-xs ${muted}`}>{item.product.name}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 empty:hidden">
            {item.price_stale ? <Badge tone="warn">stale</Badge> : null}
            {item.edited ? <Badge tone="neutral">edited</Badge> : null}
            {item.got_here ? <Badge tone="neutral">moved here</Badge> : null}
            {item.claimed_by ? (
              <Badge tone="neutral">{item.claimed_by.display_name} is getting it</Badge>
            ) : null}
          </div>
          {item.edited ? (
            <p className={`text-xs tabular-nums ${muted}`} data-testid="planned-caption">
              planned {formatMoney(item.line_cost)}
            </p>
          ) : null}
          {item.note ? <p className={`text-sm ${muted}`}>{item.note}</p> : null}
        </div>

        {readOnly ? (
          <span className="min-h-11 shrink-0 pt-2.5 text-sm tabular-nums" data-testid="item-price">
            {formatMoney(item.effective_cost)}
          </span>
        ) : (
          <Button
            ref={priceButton}
            variant="secondary"
            className="min-w-16 shrink-0 tabular-nums"
            aria-label={`Price of ${item.label}, ${formatMoney(item.effective_cost)}${item.edited ? ", edited" : ""}. Edit`}
            aria-expanded={editing}
            data-testid="item-price"
            onClick={() => setEditing((v) => !v)}
          >
            {formatMoney(item.effective_cost)}
          </Button>
        )}
      </div>

      {editing && !readOnly ? (
        <PriceEditor
          item={item}
          planId={planId}
          seq={seq}
          onClose={close}
          onConflict={onConflict}
        />
      ) : null}
    </li>
  );
}

/**
 * The row beneath an item's price: price, quantity and unit, with Save and
 * Cancel. Saving holds the price on the stop item; it records nothing until
 * "Done here" (10, Editing a price). "Use the planned price" drops a held one.
 */
function PriceEditor({
  item,
  planId,
  seq,
  onClose,
  onConflict,
}: {
  item: StopScreenItem;
  planId: string;
  seq: number;
  onClose: () => void;
  onConflict: (e: unknown) => void;
}) {
  const base = useId();
  const hold = useHoldStopPrice();
  const clear = useClearStopPrice();
  const [price, setPrice] = useState(
    item.actual_price !== null ? stripZeros(item.actual_price, 2) : "",
  );
  const [qty, setQty] = useState(item.actual_qty !== null ? stripZeros(item.actual_qty) : "1");
  const [unit, setUnit] = useState(item.actual_unit ?? "each");
  const [invalid, setInvalid] = useState<string | null>(null);
  const busy = hold.isPending || clear.isPending;
  const failure = hold.error ?? clear.error;

  const onError = (e: unknown) => {
    if (isApiError(e) && e.status === 409) onConflict(e);
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    const p = price.trim();
    const q = positiveAmount(qty);
    if (!p || !isNonNegativeDecimal(p)) return setInvalid("Enter the shelf price, such as 4.29.");
    if (q === null) return setInvalid("Enter how much the price is for, such as 1.");
    setInvalid(null);
    hold.mutate(
      { id: planId, seq, itemId: item.id, input: { price: p, qty: q, unit } },
      { onSuccess: onClose, onError },
    );
  };

  return (
    <form
      onSubmit={save}
      className="mt-2 ml-12 flex flex-col gap-2 rounded-md border border-neutral-300 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
      aria-label={`Price of ${item.label}`}
      data-testid="price-editor"
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Field
          // Focus lands in the price, which opens the decimal keypad.
          autoFocus
          id={`${base}-price`}
          label="Price"
          inputMode="decimal"
          autoComplete="off"
          className="col-span-2 sm:col-span-1"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          disabled={busy}
        />
        <Field
          id={`${base}-qty`}
          label="For"
          inputMode="decimal"
          autoComplete="off"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          disabled={busy}
        />
        <UnitSelect
          id={`${base}-unit`}
          label="Unit"
          value={unit}
          onChange={setUnit}
          emptyLabel={null}
          disabled={busy}
        />
      </div>
      <p className={`text-xs ${muted}`}>
        One pack is 1 each. Saving holds the price here; Done here decides what it records.
      </p>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {failure ? (
        <Alert tone="error">Couldn&apos;t save the price: {errorMessage(failure)}</Alert>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {hold.isPending ? "Saving…" : "Save"}
        </Button>
        <Button variant="secondary" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        {item.edited ? (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() =>
              clear.mutate({ id: planId, seq, itemId: item.id }, { onSuccess: onClose, onError })
            }
          >
            {clear.isPending ? "Clearing…" : "Use the planned price"}
          </Button>
        ) : null}
      </div>
    </form>
  );
}

interface CheaperRowProps {
  item: CheaperElsewhereItem;
  busy: boolean;
  /** "Not sold here" after a 422: this location cannot fill it after all. */
  notHere: boolean;
  error: string | null;
  readOnly: boolean;
  onGetHere: (item: CheaperElsewhereItem) => void;
}

/** A later stop's item this store could fill: the difference and "Get here" (UI-8.17). */
export function CheaperRow({ item, busy, notHere, error, readOnly, onGetHere }: CheaperRowProps) {
  const amounts = formatAmounts(item.amounts);
  return (
    <li
      data-testid="cheaper-item"
      data-id={item.id}
      className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 border-t border-neutral-200 py-2 first:border-t-0 dark:border-neutral-800"
    >
      <div className="min-w-0 flex-1 pt-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="font-medium">{item.label}</span>
          {amounts ? <span className="text-sm tabular-nums">{amounts}</span> : null}
        </div>
        <p className={`text-sm tabular-nums ${muted}`}>
          <span
            data-testid="cheaper-difference"
            className="font-medium text-neutral-900 dark:text-neutral-100"
          >
            {differenceHere(item.difference)}
          </span>{" "}
          · {formatMoney(item.line_cost_here)} here, {formatMoney(item.line_cost_there)} at{" "}
          {item.at_location_name}
        </p>
        <p className={`text-xs ${muted}`}>{item.product_here.name}</p>
        {item.price_stale_here ? (
          <span className="mt-1 inline-block">
            <Badge tone="warn">stale</Badge>
          </span>
        ) : null}
        {notHere ? (
          <p className="mt-1 text-sm text-red-800 dark:text-red-200">Not sold here</p>
        ) : null}
        {error ? (
          <p className="mt-1 text-sm text-red-800 dark:text-red-200">
            Couldn&apos;t move it: {error}
          </p>
        ) : null}
      </div>
      {readOnly || notHere ? null : (
        <Button
          variant="secondary"
          className="shrink-0"
          disabled={busy}
          aria-label={`Get ${item.label} here`}
          onClick={() => onGetHere(item)}
        >
          {busy ? "Moving…" : "Get here"}
        </Button>
      )}
    </li>
  );
}
