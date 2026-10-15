import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { isPositiveDecimal, unitLabel, useUnits } from "../../api/catalog";
import {
  LINE_KINDS,
  purchaseErrorMessage,
  useReplaceLines,
  type LineRowInput,
  type Purchase,
} from "../../api/purchases";
import { add, cmp, formatMoney, isDecimal, mul, stripZeros, sub } from "../../lib/decimal";
import { hintClass, inputClass } from "../catalog/fields";
import { useNotice } from "../Notice";
import { Alert, Button } from "../ui";

/** A line as typed in the table; `id` is the saved line it edits, if any. */
interface Row {
  key: string;
  id?: string;
  rawText: string;
  kind: string;
  qty: string;
  unit: string;
  total: string;
  /** The row this discount or deposit belongs to, while it is still an item. */
  parentKey: string | null;
  /** The OCR row the saved line was matched to, shown under its text (2S). */
  printed?: string | null;
  /** A saving already in its item's price: kept, counted as nothing, until given an amount. */
  inPrice?: string | null;
}

const ATTACHABLE = new Set(["discount", "deposit"]);

// Mirrors the backend's HOLD_GAP_SHARE (issue 121): a draft this far off its total
// needs a careful look.
const HOLD_GAP_SHARE = "0.25";

let nextKey = 0;
const newKey = () => `new-${++nextKey}`;

function blankRow(): Row {
  return {
    key: newKey(),
    rawText: "",
    kind: "item",
    qty: "",
    unit: "",
    total: "",
    parentKey: null,
  };
}

function rowsOf(purchase: Purchase): Row[] {
  return [...purchase.lines]
    .sort((a, b) => a.seq - b.seq)
    .map((l) => ({
      key: l.id,
      id: l.id,
      rawText: l.raw_text ?? "",
      kind: l.line_kind,
      qty: l.qty !== null ? stripZeros(l.qty) : "",
      unit: l.unit ?? "",
      total: l.line_total !== null ? stripZeros(l.line_total, 2) : "",
      parentKey: l.parent_line_id,
      printed: l.printed_text ?? null,
      inPrice: l.in_price_amount ?? null,
    }));
}

/** A new row nobody typed into: dropped on save rather than refused. */
function isBlank(row: Row): boolean {
  return !row.id && row.rawText.trim() === "" && row.total.trim() === "" && row.qty.trim() === "";
}

function abs(a: string): string {
  return cmp(a, "0") < 0 ? sub("0", a) : a;
}

/** What the rows add up to, counted as the server counts a purchase's lines. */
function rowsTotal(rows: Row[], headerTax: string | null): string {
  let sum = "0";
  for (const row of rows) {
    const total = row.total.trim();
    if (!isDecimal(total)) continue;
    sum = row.kind === "discount" ? sub(sum, abs(total)) : add(sum, total);
  }
  // The header's tax counts only when no line carries tax, as at ingest.
  if (headerTax !== null && !rows.some((r) => r.kind === "tax")) sum = add(sum, headerTax);
  return sum;
}

/** The item a discount or deposit attaches to: its own while it is still an item, else the nearest above. */
function attachIndex(rows: Row[], at: number): number | undefined {
  const own = rows.findIndex((r) => r.key === rows[at].parentKey);
  if (own !== -1 && own !== at && rows[own].kind === "item") return own;
  for (let i = at - 1; i >= 0; i -= 1) if (rows[i].kind === "item") return i;
  return undefined;
}

/**
 * "Correct the lines" (issue 182): every line of a draft in one editable table,
 * saved in one request. Tab moves between cells, Enter adds a row below, and the
 * lines' sum against the printed total updates as you type.
 */
export function CorrectLines({ purchase, onDone }: { purchase: Purchase; onDone: () => void }) {
  const initial = useMemo(() => rowsOf(purchase), [purchase]);
  const [rows, setRows] = useState<Row[]>(initial);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const replace = useReplaceLines(purchase.id);
  const notice = useNotice();
  const units = useUnits();
  const keepEditing = useRef<HTMLButtonElement>(null);

  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    document.getElementById(target)?.focus();
  });
  const cellId = (key: string, field: string) => `correct-${key}-${field}`;

  const dirty = JSON.stringify(rows.filter((r) => !isBlank(r))) !== JSON.stringify(initial);

  const change = (key: string, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const insertAt = (at: number) => {
    const row = blankRow();
    setRows((rs) => [...rs.slice(0, at), row, ...rs.slice(at)]);
    pendingFocus.current = cellId(row.key, "text");
  };
  const remove = (at: number) => {
    setRows((rs) => rs.filter((_, i) => i !== at));
    const neighbour = rows[at + 1] ?? rows[at - 1];
    pendingFocus.current = neighbour ? cellId(neighbour.key, "text") : "correct-add-top";
  };

  const totalKnown = purchase.total !== null && !purchase.flags.includes("total_missing");
  const sum = rowsTotal(rows, purchase.tax);
  const gap = totalKnown ? abs(sub(sum, purchase.total!)) : null;
  const addsUp = gap !== null && cmp(gap, "0.02") <= 0;
  const held =
    gap !== null &&
    !addsUp &&
    purchase.status === "draft" &&
    cmp(gap, mul(purchase.total!, HOLD_GAP_SHARE)) > 0;

  const save = () => {
    const kept = rows.filter((r) => !isBlank(r));
    for (const [n, row] of kept.entries()) {
      const label = `Row ${n + 1}`;
      if (!isDecimal(row.total.trim())) return setInvalid(`${label}: a line total is required.`);
      if (row.qty.trim() !== "" && !isPositiveDecimal(row.qty))
        return setInvalid(`${label}: the quantity must be a positive number.`);
    }
    setInvalid(null);
    const body: LineRowInput[] = kept.map((row, at) => {
      const input: LineRowInput = { line_kind: row.kind, line_total: row.total.trim() };
      if (row.id) input.id = row.id;
      if (row.rawText.trim()) input.raw_text = row.rawText.trim();
      if (row.qty.trim()) {
        input.qty = row.qty.trim();
        if (row.unit) input.unit = row.unit;
      }
      if (ATTACHABLE.has(row.kind)) {
        const parent = attachIndex(kept, at);
        if (parent !== undefined) input.attach_to = parent;
      }
      return input;
    });
    replace.mutate(body, {
      onSuccess: () => {
        notice.show({
          tone: "success",
          message: `Lines saved: ${body.length} ${body.length === 1 ? "line" : "lines"}.`,
        });
        onDone();
      },
    });
  };

  const requestCancel = () => {
    if (!dirty) return onDone();
    setAsking(true);
    queueMicrotask(() => keepEditing.current?.focus());
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      requestCancel();
    }
  };
  // Enter in a cell adds a row below it, ready to type.
  const onCellKey = (at: number) => (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    event.stopPropagation();
    insertAt(at + 1);
  };

  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-label="Correct the lines"
      onKeyDown={onKeyDown}
    >
      <p className={hintClass}>
        Tab moves between cells, Enter adds a row below. Nothing changes until you save.
      </p>
      {replace.error ? <Alert tone="error">{purchaseErrorMessage(replace.error)}</Alert> : null}
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}

      <div
        aria-live="polite"
        className={`rounded-md border px-3 py-2 text-sm ${held ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100" : "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900"}`}
      >
        Lines add up to {formatMoney(sum)}
        {totalKnown ? (
          <>
            ; the receipt says {formatMoney(purchase.total)}.{" "}
            {addsUp ? (
              <span className="font-medium text-green-800 dark:text-green-300">Adds up.</span>
            ) : (
              <span className="font-medium">
                {held
                  ? `Off by ${formatMoney(gap)}: still a careful look.`
                  : `Off by ${formatMoney(gap)}.`}
              </span>
            )}
          </>
        ) : (
          "; the receipt's total has not been read."
        )}
      </div>

      <div>
        <Button id="correct-add-top" variant="secondary" onClick={() => insertAt(0)}>
          Add a row at the top
        </Button>
      </div>

      <ol aria-label="Lines to save" className="flex flex-col">
        {rows.map((row, at) => (
          <CorrectRow
            key={row.key}
            row={row}
            at={at}
            attachedTo={ATTACHABLE.has(row.kind) ? attachIndex(rows, at) : undefined}
            units={units.data ?? []}
            cellId={cellId}
            change={change}
            onCellKey={onCellKey}
            insertAt={insertAt}
            remove={remove}
          />
        ))}
      </ol>
      {rows.length === 0 ? (
        <p className={hintClass}>No lines. Add a row to start typing the receipt.</p>
      ) : null}

      {asking ? (
        <div
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
        >
          <p>Discard your corrections? What you typed will be lost.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button ref={keepEditing} variant="secondary" onClick={() => setAsking(false)}>
              Keep editing
            </Button>
            <Button variant="danger" onClick={onDone}>
              Discard
            </Button>
          </div>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={replace.isPending} onClick={save}>
          {replace.isPending ? "Saving…" : "Save lines"}
        </Button>
        <Button variant="secondary" onClick={requestCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

const cell = `${inputClass} w-full`;
const shortLabel = "text-[11px] text-neutral-600 dark:text-neutral-400";

interface CorrectRowProps {
  row: Row;
  at: number;
  /** The row a discount or deposit counts against, by index. */
  attachedTo: number | undefined;
  units: { code: string }[];
  cellId: (key: string, field: string) => string;
  change: (key: string, patch: Partial<Row>) => void;
  onCellKey: (at: number) => (event: KeyboardEvent<HTMLInputElement>) => void;
  insertAt: (at: number) => void;
  remove: (at: number) => void;
}

/** What a row is, in words, under its text: the printed row, its item, a saving in the price. */
function RowNotes({ row, attachedTo }: Pick<CorrectRowProps, "row" | "attachedTo">) {
  const printed = row.printed && row.printed.trim() !== row.rawText.trim() ? row.printed : null;
  const kept = row.inPrice && (row.total.trim() === "" || cmp(row.total, "0") === 0);
  if (!printed && attachedTo === undefined && !kept) return null;
  return (
    <span className={`flex flex-wrap gap-x-3 ${shortLabel}`}>
      {printed ? <span className="font-mono">Printed: {printed}</span> : null}
      {attachedTo !== undefined ? <span>On line {attachedTo + 1}</span> : null}
      {kept ? (
        <span>
          Already in the price: −{formatMoney(row.inPrice)}, not counted. Type an amount to count
          it.
        </span>
      ) : null}
    </span>
  );
}

/**
 * One line in Correct the lines (U5): the receipt text across the full width,
 * so six "Member Savings" rows can be told apart, and the numbers beneath.
 */
function CorrectRow(props: CorrectRowProps) {
  const { row, at, units, cellId, change, onCellKey, insertAt, remove } = props;
  const n = at + 1;
  const known = units.some((u) => u.code === row.unit);
  return (
    <li className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2 gap-y-1 border-b border-neutral-200 py-2 dark:border-neutral-800">
      <span className="pt-2 text-sm tabular-nums text-neutral-600 dark:text-neutral-400">{n}</span>
      <span className="flex min-w-0 flex-col gap-1">
        <input
          id={cellId(row.key, "text")}
          aria-label={`Receipt text, row ${n}`}
          placeholder="Receipt text"
          className={`${cell} font-mono`}
          autoComplete="off"
          value={row.rawText}
          onChange={(e) => change(row.key, { rawText: e.target.value })}
          onKeyDown={onCellKey(at)}
        />
        <RowNotes row={row} attachedTo={props.attachedTo} />
      </span>
      <span className="col-start-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-[5rem_7rem_6.5rem_7rem_auto]">
        <label className="flex min-w-0 flex-col">
          <span className={shortLabel} aria-hidden="true">
            Qty
          </span>
          <input
            id={cellId(row.key, "qty")}
            aria-label={`Quantity, row ${n}`}
            className={cell}
            inputMode="decimal"
            autoComplete="off"
            value={row.qty}
            onChange={(e) => change(row.key, { qty: e.target.value })}
            onKeyDown={onCellKey(at)}
          />
        </label>
        <label className="flex min-w-0 flex-col">
          <span className={shortLabel} aria-hidden="true">
            Unit
          </span>
          <select
            id={cellId(row.key, "unit")}
            aria-label={`Unit, row ${n}`}
            className={cell}
            value={row.unit}
            onChange={(e) => change(row.key, { unit: e.target.value })}
          >
            <option value="">—</option>
            {row.unit && !known ? <option value={row.unit}>{row.unit}</option> : null}
            {units.map((u) => (
              <option key={u.code} value={u.code}>
                {unitLabel(u.code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 flex-col">
          <span className={shortLabel} aria-hidden="true">
            Line total
          </span>
          <input
            id={cellId(row.key, "total")}
            aria-label={`Line total, row ${n}`}
            className={cell}
            inputMode="decimal"
            autoComplete="off"
            value={row.total}
            onChange={(e) => change(row.key, { total: e.target.value })}
            onKeyDown={onCellKey(at)}
          />
        </label>
        <label className="flex min-w-0 flex-col">
          <span className={shortLabel} aria-hidden="true">
            Kind
          </span>
          <select
            id={cellId(row.key, "kind")}
            aria-label={`Kind, row ${n}`}
            className={cell}
            value={row.kind}
            onChange={(e) => change(row.key, { kind: e.target.value })}
          >
            {LINE_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <span className="col-span-2 flex gap-1 sm:col-span-1">
          <Button
            variant="secondary"
            className="px-2 text-xs"
            aria-label={`Add a row below row ${n}`}
            onClick={() => insertAt(at + 1)}
          >
            Add below
          </Button>
          <Button
            variant="danger"
            className="px-2 text-xs"
            aria-label={`Delete row ${n}`}
            onClick={() => remove(at)}
          >
            Delete
          </Button>
        </span>
      </span>
    </li>
  );
}
