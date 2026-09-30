import { useState } from "react";
import { errorMessage } from "../../api/client";
import { isPositiveDecimal } from "../../api/catalog";
import { purchaseErrorMessage, useNameProducts, useNamingRows, type NameProductRow, type NamingRow } from "../../api/purchases";
import { stripZeros } from "../../lib/decimal";
import { choiceFromMatch, choiceInput, IngredientPicker, type IngredientChoice } from "../catalog/IngredientPicker";
import { UnitSelect } from "../catalog/UnitSelect";
import { hintClass } from "../catalog/fields";
import { useWidePage } from "../chrome";
import { useNotice } from "../Notice";
import { Alert, Button, focusRing } from "../ui";

/** What a person has changed on a row; anything absent is still the suggestion. */
interface RowEdit {
  name?: string;
  ingredient?: IngredientChoice | null;
  packQty?: string;
  packUnit?: string;
  ticked?: boolean;
}

interface RowValue {
  name: string;
  ingredient: IngredientChoice | null;
  packQty: string;
  packUnit: string;
  ticked: boolean;
}

function rowKey(row: NamingRow): string {
  return `${row.vendor.id}:${row.raw_text_norm ?? ""}`;
}

function valueOf(row: NamingRow, edit: RowEdit | undefined): RowValue {
  return {
    name: edit?.name ?? row.name,
    ingredient: edit?.ingredient !== undefined ? edit.ingredient : row.ingredient ? choiceFromMatch(row.ingredient) : null,
    packQty: edit?.packQty ?? (row.pack_qty ? stripZeros(row.pack_qty, 0) : ""),
    packUnit: edit?.packUnit ?? row.pack_unit ?? "",
    ticked: edit?.ticked ?? false,
  };
}

/** Why a ticked row can't be sent yet, or null. */
function problemWith(v: RowValue): string | null {
  if (!v.name.trim()) return "Give the product a name.";
  if (!v.ingredient) return "Choose an ingredient, or create one by name.";
  const hasQty = v.packQty.trim() !== "";
  if (hasQty !== (v.packUnit !== "")) return "Give both a pack quantity and a pack unit, or neither.";
  if (hasQty && !isPositiveDecimal(v.packQty.trim())) return "Pack quantity must be a positive number.";
  return null;
}

/**
 * Name every waiting group's product in one pass (04, 2I; #88). Each row starts
 * from the line's wording and is created only once a person has ticked it (N5).
 */
export function NameProducts() {
  // Four fields a row, the ingredient picker among them, need more than reading width.
  useWidePage();
  const naming = useNamingRows(true);
  const create = useNameProducts();
  const notice = useNotice();
  const [edits, setEdits] = useState<Record<string, RowEdit>>({});
  const [problems, setProblems] = useState<Record<string, string>>({});
  const rows = naming.data ?? [];

  // Editing a row says it has been looked at, so it is ticked (N5).
  const change = (key: string, patch: RowEdit) => {
    setEdits((all) => ({ ...all, [key]: { ...all[key], ticked: true, ...patch } }));
    setProblems((all) => {
      const rest = { ...all };
      delete rest[key];
      return rest;
    });
  };

  const values = rows.map((row) => ({ row, key: rowKey(row), v: valueOf(row, edits[rowKey(row)]) }));
  const ticked = values.filter((x) => x.v.ticked);

  const submit = () => {
    const found: Record<string, string> = {};
    for (const { key, v } of ticked) {
      const p = problemWith(v);
      if (p) found[key] = p;
    }
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    const payload: NameProductRow[] = ticked.map(({ row, v }) => ({
      vendor_id: row.vendor.id,
      raw_text_norm: row.raw_text_norm ?? "",
      name: v.name.trim(),
      ...(v.packQty.trim() ? { pack_qty: v.packQty.trim(), pack_unit: v.packUnit } : {}),
      ...choiceInput(v.ingredient!),
    }));
    create.mutate(payload, {
      onSuccess: ({ results }) => {
        const failed: Record<string, string> = {};
        let products = 0;
        let lines = 0;
        for (const r of results) {
          const key = `${r.vendor_id}:${r.raw_text_norm}`;
          if (r.error) failed[key] = r.error.message;
          else {
            products += 1;
            lines += r.applied;
          }
        }
        setProblems(failed);
        // A created row leaves the list; a failed one keeps what was typed.
        setEdits((all) => Object.fromEntries(Object.entries(all).filter(([key]) => key in failed || !results.some((r) => `${r.vendor_id}:${r.raw_text_norm}` === key))));
        if (products > 0) {
          notice.show({ tone: "success", message: `Created ${products} ${products === 1 ? "product" : "products"} and identified ${lines} ${lines === 1 ? "line" : "lines"}.` });
        }
      },
    });
  };

  if (naming.isPending) {
    return (
      <p role="status" className={hintClass}>
        Loading…
      </p>
    );
  }
  if (naming.isError) return <Alert tone="error">{errorMessage(naming.error)}</Alert>;
  if (rows.length === 0) return <p className={`py-4 ${hintClass}`}>Every line has a product or is ignored.</p>;

  const failedCount = Object.keys(problems).length;
  return (
    <div className="flex flex-col gap-4">
      <p className={hintClass}>
        Each row starts from the receipt's wording. Tick a row, or change anything in it, to include it; nothing is created until you press Create.
      </p>
      {create.error ? <Alert tone="error">{purchaseErrorMessage(create.error)}</Alert> : null}
      {failedCount > 0 ? (
        <Alert tone="error">
          {failedCount === 1 ? "1 row wasn't created" : `${failedCount} rows weren't created`}. The reason is on each row.
        </Alert>
      ) : null}
      <ul aria-label="New products to name" className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
        {values.map(({ row, key, v }, i) => (
          <NamingRowItem key={key} id={`name-${i}`} row={row} value={v} problem={problems[key]} busy={create.isPending} onChange={(patch) => change(key, patch)} />
        ))}
      </ul>
      <div className="sticky bottom-[calc(6rem+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50/95 p-3 shadow-sm backdrop-blur lg:bottom-4 dark:border-neutral-800 dark:bg-neutral-950/95">
        <Button onClick={submit} disabled={create.isPending || ticked.length === 0} className="min-h-12 flex-1 text-base lg:min-h-10 lg:flex-none lg:text-sm">
          {create.isPending ? "Creating…" : ticked.length === 0 ? "Create products" : `Create ${ticked.length} ${ticked.length === 1 ? "product" : "products"}`}
        </Button>
        <span className={hintClass}>
          {ticked.length} of {rows.length} ticked
        </span>
      </div>
    </div>
  );
}

function NamingRowItem({ id, row, value, problem, busy, onChange }: { id: string; row: NamingRow; value: RowValue; problem?: string; busy: boolean; onChange: (patch: RowEdit) => void }) {
  const lineLabel = row.raw_text_norm ?? "(no text)";
  return (
    <li className="p-3" role="group" aria-label={`${row.vendor.name}: ${lineLabel}`}>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,16rem)] lg:items-start">
        <label className={`flex min-h-11 items-start gap-2 rounded-md lg:min-h-10 ${focusRing}`}>
          <input type="checkbox" checked={value.ticked} disabled={busy} onChange={(e) => onChange({ ticked: e.target.checked })} className={`mt-1 size-5 shrink-0 ${focusRing}`} aria-label={`Include ${lineLabel}`} />
          <span className="min-w-0">
            <span className="block font-mono text-sm break-words">{lineLabel}</span>
            <span className={hintClass}>
              {row.vendor.name} · {row.line_count} {row.line_count === 1 ? "line" : "lines"}
            </span>
          </span>
        </label>
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={`${id}-name`} className="text-sm font-medium">
            Name
          </label>
          <input
            id={`${id}-name`}
            value={value.name}
            disabled={busy}
            autoComplete="off"
            onChange={(e) => onChange({ name: e.target.value })}
            className={`min-h-11 min-w-0 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`}
          />
        </div>
        <div className="min-w-0">
          <IngredientPicker id={`${id}-ingredient`} value={value.ingredient} onChange={(choice) => onChange({ ingredient: choice })} disabled={busy} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-pack-qty`} className="text-sm font-medium">
              Pack
            </label>
            <input
              id={`${id}-pack-qty`}
              value={value.packQty}
              disabled={busy}
              inputMode="decimal"
              autoComplete="off"
              onChange={(e) => onChange({ packQty: e.target.value })}
              className={`min-h-11 min-w-0 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`}
            />
          </div>
          <UnitSelect id={`${id}-pack-unit`} label="Unit" value={value.packUnit} onChange={(unit) => onChange({ packUnit: unit })} emptyLabel="No pack" disabled={busy} />
        </div>
      </div>
      {problem ? (
        <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">
          {problem}
        </p>
      ) : null}
    </li>
  );
}
