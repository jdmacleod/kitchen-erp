import { useEffect, useRef, useState, type Dispatch, type FocusEvent, type KeyboardEvent, type ReactNode, type SetStateAction } from "react";
import { isPositiveDecimal } from "../../api/catalog";
import { fetchLastPurchaseUnit, itemLines, purchaseErrorMessage, type Purchase, type PurchaseInput, type PurchaseLineInput } from "../../api/purchases";
import { MONEY_PLACES, add, cmp, div, formatMoney, isNonNegativeDecimal, isZero, mul, roundTo, stripZeros } from "../../lib/decimal";
import { Badge, hintClass, inputClass, labelClass } from "../catalog/fields";
import { UnitSelect } from "../catalog/UnitSelect";
import { Alert, Button, Field } from "../ui";
import { LocationSelect } from "./LocationSelect";
import { ProductPicker, type ProductRef } from "./ProductPicker";

// --- values -----------------------------------------------------------------

export type PriceDriver = "unit_price" | "line_total";

export interface LineDraft {
  key: string;
  /** The server's line id when editing an existing purchase. */
  id?: string;
  product: ProductRef | null;
  qty: string;
  unit: string;
  /** True once the user picked a unit, so a late last-unit lookup does not override it. */
  unitTouched: boolean;
  /** Only the driver's text is stored; the other price field is computed. */
  unit_price: string;
  line_total: string;
  driver: PriceDriver | null;
}

export interface PurchaseFormValues {
  vendor_location_id: string;
  /** Local calendar date, YYYY-MM-DD. */
  date: string;
  total: string;
  lines: LineDraft[];
}

let lineCounter = 0;

export function newLine(): LineDraft {
  lineCounter += 1;
  return { key: `l${lineCounter}`, product: null, qty: "", unit: "", unitTouched: false, unit_price: "", line_total: "", driver: null };
}

/** YYYY-MM-DD in the viewer's zone. */
export function toLocalDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function emptyPurchaseValues(): PurchaseFormValues {
  return { vendor_location_id: "", date: toLocalDate(new Date()), total: "", lines: [newLine()] };
}

/** Editing: item lines come back with the paid total as the driver. */
export function purchaseValues(p: Purchase): PurchaseFormValues {
  const lines = itemLines(p).map<LineDraft>((l) => {
    const draft = newLine();
    const useTotal = l.line_total !== null;
    return {
      ...draft,
      id: l.id,
      product: l.product ? { ...l.product } : null,
      qty: l.qty ?? "",
      unit: l.unit ?? "",
      unitTouched: true,
      unit_price: useTotal ? "" : (l.unit_price ?? ""),
      line_total: useTotal ? l.line_total! : "",
      driver: useTotal ? "line_total" : l.unit_price !== null ? "unit_price" : null,
    };
  });
  return {
    vendor_location_id: p.vendor_location?.id ?? "",
    date: toLocalDate(new Date(p.purchased_at)),
    total: p.total ?? "",
    lines: lines.length > 0 ? [...lines, newLine()] : [newLine()],
  };
}

// --- arithmetic -------------------------------------------------------------

const qtyValid = (l: LineDraft) => isPositiveDecimal(l.qty);
const priceValid = (text: string) => text.trim() !== "" && isNonNegativeDecimal(text);

/** qty × unit price to four places, when the unit price is the driver. */
export function computedLineTotal(l: LineDraft): string | null {
  if (l.driver !== "unit_price" || !qtyValid(l) || !priceValid(l.unit_price)) return null;
  return roundTo(mul(l.qty.trim(), l.unit_price.trim()), MONEY_PLACES);
}

/** line total ÷ qty to four places, half-even, when the line total is the driver. */
export function computedUnitPrice(l: LineDraft): string | null {
  if (l.driver !== "line_total" || !qtyValid(l) || !priceValid(l.line_total)) return null;
  return div(l.line_total.trim(), l.qty.trim(), MONEY_PLACES);
}

/** What the line cost, whichever field drove it; null while incomplete. */
export function effectiveLineTotal(l: LineDraft): string | null {
  if (l.driver === "line_total") return priceValid(l.line_total) ? l.line_total.trim() : null;
  return computedLineTotal(l);
}

export function runningTotal(lines: LineDraft[]): string {
  let sum = "0.00";
  for (const l of lines) {
    const t = effectiveLineTotal(l);
    if (t !== null) sum = add(sum, t);
  }
  return sum;
}

export function isLineEmpty(l: LineDraft): boolean {
  return l.product === null && l.qty.trim() === "" && l.unit_price.trim() === "" && l.line_total.trim() === "";
}

type LineField = "product" | "qty" | "unit" | "unit_price" | "line_total";

export function lineProblem(l: LineDraft): { field: LineField; message: string } | null {
  if (!l.product) return { field: "product", message: "Choose a product for the line." };
  if (!qtyValid(l)) return { field: "qty", message: "Quantity must be a positive number." };
  if (!l.unit) return { field: "unit", message: "Choose a unit." };
  if (l.driver === "unit_price" && priceValid(l.unit_price)) return null;
  if (l.driver === "line_total" && priceValid(l.line_total)) return null;
  return { field: "unit_price", message: "Enter a unit price or a line total." };
}

/** The date at the current local time, or the existing instant when the date is unchanged. */
function purchasedAt(date: string, existing?: Purchase): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return null;
  if (existing && toLocalDate(new Date(existing.purchased_at)) === date) return existing.purchased_at;
  const now = new Date();
  const at = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), now.getHours(), now.getMinutes(), now.getSeconds());
  return Number.isNaN(at.getTime()) ? null : at.toISOString();
}

export type BuildResult = { ok: true; input: PurchaseInput } | { ok: false; message: string; lineKey?: string; field?: LineField };

/** Validate and turn the draft into a request body. Trailing empty lines are dropped. */
export function buildPurchaseInput(v: PurchaseFormValues, existing?: Purchase): BuildResult {
  if (!v.vendor_location_id) return { ok: false, message: "Choose a location." };
  const at = purchasedAt(v.date, existing);
  if (!at) return { ok: false, message: "Enter a valid date." };
  if (v.total.trim() !== "" && !isNonNegativeDecimal(v.total)) return { ok: false, message: "The entered total must be a number." };

  const lines: PurchaseLineInput[] = [];
  for (const l of v.lines) {
    if (isLineEmpty(l)) continue;
    const problem = lineProblem(l);
    if (problem) return { ok: false, message: problem.message, lineKey: l.key, field: problem.field };
    const line: PurchaseLineInput = { product_id: l.product!.id, qty: l.qty.trim(), unit: l.unit };
    if (l.driver === "unit_price") line.unit_price = l.unit_price.trim();
    else line.line_total = l.line_total.trim();
    lines.push(line);
  }
  if (lines.length === 0) return { ok: false, message: "Enter at least one line." };

  const input: PurchaseInput = { vendor_location_id: v.vendor_location_id, purchased_at: at, lines };
  if (v.total.trim() !== "") input.total = v.total.trim();
  return { ok: true, input };
}

// --- form -------------------------------------------------------------------

interface PurchaseFormProps {
  idPrefix: string;
  heading: string;
  values: PurchaseFormValues;
  onChange: Dispatch<SetStateAction<PurchaseFormValues>>;
  onSubmit: (input: PurchaseInput) => void;
  /** The purchase being edited, when any. */
  existing?: Purchase;
  busy: boolean;
  error: unknown;
  submitLabel: string;
  busyLabel: string;
  /** Extra controls next to the submit button. */
  children?: ReactNode;
}

const NARROW = 640;

/**
 * Header (location, date, entered total) and lines entered one after another.
 * Enter moves to the next field; on a price field it commits the line and
 * opens the next one; Ctrl+Enter or Cmd+Enter saves. Values live in the page
 * so an inline product creation, a save error, or a re-render never loses a
 * line that was already entered.
 */
export function PurchaseForm({ idPrefix, heading, values, onChange, onSubmit, existing, busy, error, submitLabel, busyLabel, children }: PurchaseFormProps) {
  const [invalid, setInvalid] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  // A field to focus once the next render has put it in the document.
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingFocus.current;
    if (!id) return;
    pendingFocus.current = null;
    document.getElementById(id)?.focus();
  });

  const fieldId = (key: string, field: LineField) => `${idPrefix}-line-${key}-${field.replace("_", "-")}`;
  const focusField = (key: string, field: LineField) => document.getElementById(fieldId(key, field))?.focus();

  const setLine = (key: string, patch: Partial<LineDraft> | ((l: LineDraft) => Partial<LineDraft>)) =>
    onChange((prev) => ({
      ...prev,
      lines: prev.lines.map((l) => (l.key === key ? { ...l, ...(typeof patch === "function" ? patch(l) : patch) } : l)),
    }));

  const setProduct = (key: string, product: ProductRef | null) => {
    setLine(key, (l) => ({
      product,
      unit: product ? (l.unitTouched ? l.unit : "each") : l.unit,
      unitTouched: product ? l.unitTouched : false,
    }));
    if (!product) return;
    // The product's last purchase unit, when it has one and the user has not chosen since.
    void fetchLastPurchaseUnit(product.id)
      .then((unit) => {
        if (!unit) return;
        setLine(key, (l) => (l.product?.id === product.id && !l.unitTouched ? { unit } : {}));
      })
      .catch(() => undefined);
  };

  // Emptying either field, driver or computed, leaves the line without a
  // price, so whatever is typed next becomes the driver.
  const setPrice = (key: string, driver: PriceDriver, text: string) =>
    setLine(
      key,
      text.trim() === ""
        ? { unit_price: "", line_total: "", driver: null }
        : driver === "unit_price"
          ? { unit_price: text, line_total: "", driver }
          : { line_total: text, unit_price: "", driver },
    );

  const removeLine = (key: string) =>
    onChange((prev) => {
      const lines = prev.lines.filter((l) => l.key !== key);
      return { ...prev, lines: lines.length > 0 ? lines : [newLine()] };
    });

  const commitLine = (key: string) => {
    const index = values.lines.findIndex((l) => l.key === key);
    if (index === -1) return;
    const line = values.lines[index];
    const problem = lineProblem(line);
    if (problem) {
      setInvalid(problem.message);
      focusField(key, problem.field);
      return;
    }
    setInvalid(null);
    const next = values.lines[index + 1];
    if (next) {
      focusField(next.key, next.product ? "qty" : "product");
      return;
    }
    const fresh = newLine();
    pendingFocus.current = fieldId(fresh.key, "product");
    onChange((prev) => ({ ...prev, lines: [...prev.lines, fresh] }));
  };

  const submit = () => {
    const built = buildPurchaseInput(values, existing);
    if (!built.ok) {
      setInvalid(built.message);
      if (built.lineKey && built.field) focusField(built.lineKey, built.field);
      return;
    }
    setInvalid(null);
    onSubmit(built.input);
  };

  const focusNext = (from: HTMLElement) => {
    const form = formRef.current;
    if (!form) return;
    const fields = Array.from(form.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select")).filter(
      (el) => !el.disabled && el.type !== "hidden",
    );
    const index = fields.indexOf(from as HTMLInputElement);
    fields[index + 1]?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key !== "Enter") return;
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault();
      if (!busy) submit();
      return;
    }
    if (event.isDefaultPrevented()) return; // a combobox chose an option
    const target = event.target as HTMLElement;
    if (target.tagName === "BUTTON" || target.tagName === "TEXTAREA" || target.tagName === "A") return;
    event.preventDefault();
    // A product box picks with the arrow keys and Enter; a bare Enter does nothing.
    if (target.getAttribute("role") === "combobox") return;
    const field = target.dataset.field;
    const lineKey = target.closest<HTMLElement>("[data-line]")?.dataset.line;
    if (lineKey && (field === "unit_price" || field === "line_total")) {
      commitLine(lineKey);
      return;
    }
    focusNext(target);
  };

  // On a phone the on-screen keyboard covers the lower half of the screen;
  // bring the field being edited to the middle so it stays visible.
  const onFocus = (event: FocusEvent<HTMLElement>) => {
    if (typeof window === "undefined" || window.innerWidth >= NARROW) return;
    const target = event.target as HTMLElement;
    if (typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center" });
  };

  const total = runningTotal(values.lines);
  const entered = values.total.trim() !== "" && isNonNegativeDecimal(values.total) ? values.total.trim() : null;
  const difference = entered !== null ? roundTo(add(entered, `-${total}`), MONEY_PLACES) : null;
  const headingId = `${idPrefix}-heading`;

  return (
    <form
      ref={formRef}
      aria-labelledby={headingId}
      noValidate
      className="flex flex-col gap-5"
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) submit();
      }}
    >
      <h2 id={headingId} className="text-lg font-medium">
        {heading}
      </h2>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {error ? <Alert tone="error">{purchaseErrorMessage(error)}</Alert> : null}

      <div className="grid gap-4 sm:grid-cols-[2fr_1fr_1fr]">
        <LocationSelect
          id={`${idPrefix}-location`}
          value={values.vendor_location_id}
          onChange={(id) => onChange((prev) => ({ ...prev, vendor_location_id: id }))}
          autoDefault={!existing}
          disabled={busy}
        />
        <Field
          id={`${idPrefix}-date`}
          label="Date"
          type="date"
          required
          value={values.date}
          onChange={(e) => onChange((prev) => ({ ...prev, date: e.target.value }))}
          disabled={busy}
        />
        <Field
          id={`${idPrefix}-total`}
          label="Total on the slip"
          inputMode="decimal"
          autoComplete="off"
          placeholder="optional"
          value={values.total}
          onChange={(e) => onChange((prev) => ({ ...prev, total: e.target.value }))}
          disabled={busy}
        />
      </div>

      <div className="flex flex-col gap-3">
        <div className="hidden gap-2 px-1 text-xs font-semibold text-neutral-600 dark:text-neutral-400 sm:grid sm:grid-cols-[minmax(0,3fr)_5rem_6rem_7rem_7rem_4rem]">
          <span>Product</span>
          <span>Qty</span>
          <span>Unit</span>
          <span>Unit price</span>
          <span>Line total</span>
          <span className="sr-only">Actions</span>
        </div>
        <ol aria-label="Lines" className="flex flex-col gap-3">
          {values.lines.map((line, index) => (
            <li key={line.key} data-line={line.key}>
              <LineRow
                idPrefix={idPrefix}
                index={index}
                line={line}
                busy={busy}
                onProduct={(p) => setProduct(line.key, p)}
                onPicked={() => focusField(line.key, "qty")}
                onQty={(qty) => setLine(line.key, { qty })}
                onUnit={(unit) => setLine(line.key, { unit, unitTouched: true })}
                onPrice={(driver, text) => setPrice(line.key, driver, text)}
                onRemove={() => removeLine(line.key)}
              />
            </li>
          ))}
        </ol>
      </div>

      <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm" aria-label="Totals">
        <div className="flex gap-2">
          <dt className="text-neutral-600 dark:text-neutral-400">Running total</dt>
          <dd className="font-medium tabular-nums" data-testid={`${idPrefix}-running-total`}>
            {formatMoney(total)}
          </dd>
        </div>
        {difference !== null ? (
          <div className="flex gap-2">
            <dt className="text-neutral-600 dark:text-neutral-400">Against the slip</dt>
            <dd data-testid={`${idPrefix}-difference`}>
              {isZero(difference) ? (
                <Badge tone="good">matches</Badge>
              ) : (
                <Badge tone="warn">
                  {cmp(difference, "0") > 0 ? "slip is higher by " : "slip is lower by "}
                  {formatMoney(difference.replace("-", ""), 2, MONEY_PLACES)}
                </Badge>
              )}
            </dd>
          </div>
        ) : null}
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? busyLabel : submitLabel}
        </Button>
        {children}
        <span className={hintClass}>Enter moves on; Ctrl+Enter saves.</span>
      </div>
    </form>
  );
}

interface LineRowProps {
  idPrefix: string;
  index: number;
  line: LineDraft;
  busy: boolean;
  onProduct: (product: ProductRef | null) => void;
  onPicked: () => void;
  onQty: (qty: string) => void;
  onUnit: (unit: string) => void;
  onPrice: (driver: PriceDriver, text: string) => void;
  onRemove: () => void;
}

function LineRow({ idPrefix, index, line, busy, onProduct, onPicked, onQty, onUnit, onPrice, onRemove }: LineRowProps) {
  const id = (field: string) => `${idPrefix}-line-${line.key}-${field}`;
  const n = index + 1;
  const unitPriceComputed = line.driver !== "unit_price" ? computedUnitPrice(line) : null;
  const lineTotalComputed = line.driver !== "line_total" ? computedLineTotal(line) : null;
  const unitPriceValue = line.driver === "unit_price" ? line.unit_price : unitPriceComputed ? stripZeros(unitPriceComputed, 2) : "";
  const lineTotalValue = line.driver === "line_total" ? line.line_total : lineTotalComputed ? stripZeros(lineTotalComputed, 2) : "";

  return (
    <fieldset
      aria-label={`Line ${n}`}
      className="grid gap-2 rounded-md border border-neutral-200 p-2 sm:grid-cols-[minmax(0,3fr)_5rem_6rem_7rem_7rem_4rem] sm:items-start sm:border-0 sm:p-0 dark:border-neutral-800"
    >
      <div className="min-w-0">
        <ProductPicker id={id("product")} label={`Product ${n}`} hideLabel value={line.product} onChange={onProduct} onPicked={onPicked} disabled={busy} />
      </div>
      <PriceInput id={id("qty")} label={`Quantity ${n}`} field="qty" value={line.qty} onChange={onQty} disabled={busy} />
      <UnitSelectCompact id={id("unit")} label={`Unit ${n}`} value={line.unit} onChange={onUnit} disabled={busy} />
      <PriceInput
        id={id("unit-price")}
        label={`Unit price ${n}`}
        field="unit_price"
        value={unitPriceValue}
        computed={line.driver === "line_total" && unitPriceComputed !== null}
        onChange={(t) => onPrice("unit_price", t)}
        disabled={busy}
      />
      <PriceInput
        id={id("line-total")}
        label={`Line total ${n}`}
        field="line_total"
        value={lineTotalValue}
        computed={line.driver === "unit_price" && lineTotalComputed !== null}
        onChange={(t) => onPrice("line_total", t)}
        disabled={busy}
      />
      <div className="flex sm:justify-end">
        <Button variant="ghost" className="min-h-11 lg:min-h-10 px-2 text-xs" disabled={busy} onClick={onRemove} aria-label={`Remove line ${n}`}>
          Remove
        </Button>
      </div>
    </fieldset>
  );
}

interface PriceInputProps {
  id: string;
  label: string;
  field: LineField;
  value: string;
  onChange: (text: string) => void;
  computed?: boolean;
  disabled?: boolean;
}

/** A decimal text input with a visible "computed" mark when its value was derived. */
function PriceInput({ id, label, field, value, onChange, computed = false, disabled }: PriceInputProps) {
  const short = label.replace(/ \d+$/, "");
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className={`${labelClass} sm:sr-only`}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          data-field={field}
          aria-description={computed ? `${short}, computed` : undefined}
          value={value}
          disabled={disabled}
          placeholder={short}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full tabular-nums ${inputClass} ${computed ? "bg-neutral-50 text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300" : ""}`}
        />
        {computed ? (
          <span className="pointer-events-none absolute top-1/2 right-1.5 -translate-y-1/2" data-testid={`${id}-computed`}>
            <Badge>computed</Badge>
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** The unit select with its label hidden on wide screens, where the column header names it. */
function UnitSelectCompact({ id, label, value, onChange, disabled }: { id: string; label: string; value: string; onChange: (u: string) => void; disabled?: boolean }) {
  return (
    <div className="sm:[&_label]:sr-only">
      <UnitSelect id={id} label={label} value={value} onChange={onChange} disabled={disabled} />
    </div>
  );
}
