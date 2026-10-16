import { productTitle } from "../../api/catalog";
import type { NamingRow } from "../../api/naming";
import { hintClass } from "../catalog/fields";
import { choiceName, IngredientPicker } from "../catalog/IngredientPicker";
import { UnitSelect } from "../catalog/UnitSelect";
import { Button, focusRing } from "../ui";
import { NameChanges } from "./NameChanges";
import { NameField } from "./NameField";
import { NamingCandidates } from "./NamingCandidates";
import type { ExistingProduct, RowEdit, RowValue } from "./namingRows";

export function NamingRowItem({
  id,
  row,
  value,
  problem,
  existing,
  busy,
  onChange,
  onNext,
}: {
  id: string;
  row: NamingRow;
  value: RowValue;
  problem?: string;
  existing?: ExistingProduct;
  busy: boolean;
  onChange: (patch: RowEdit) => void;
  /** Move to the next row's Name (Enter in Pack; issue 389). */
  onNext?: () => void;
}) {
  const lineLabel = row.raw_text_norm ?? "(no text)";
  return (
    <li className="p-3" role="group" aria-label={`${row.vendor.name}: ${lineLabel}`}>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,16rem)] lg:items-start">
        <RowLabel row={row} value={value} busy={busy} onChange={onChange} />
        <div className="flex min-w-0 flex-col gap-1">
          <NameField
            id={`${id}-name`}
            vendorId={row.vendor.id}
            value={value.name}
            ingredientName={value.ingredient ? choiceName(value.ingredient) : null}
            hint={value.fromModel.name ? "Suggested by the model" : undefined}
            disabled={busy}
            onChange={(name) => onChange({ name })}
            onUse={(hit) =>
              onChange({ use: { id: hit.id, name: hit.name, brand: hit.brand }, duplicate: false })
            }
          />
          <NameChanges
            changes={value.changes}
            undone={value.undone}
            disabled={busy}
            onToggle={(index) =>
              onChange({
                undone: value.undone.has(index)
                  ? [...value.undone].filter((i) => i !== index)
                  : [...value.undone, index],
              })
            }
          />
        </div>
        <div className="min-w-0">
          <IngredientPicker
            id={`${id}-ingredient`}
            value={value.ingredient}
            onChange={(choice) => onChange({ ingredient: choice })}
            disabled={busy}
          />
          {value.fromModel.ingredient ? (
            <p className={`mt-1 ${hintClass}`}>Suggested by the model</p>
          ) : null}
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
              onKeyDown={(e) => {
                if (e.key === "Enter" && onNext) {
                  e.preventDefault();
                  onNext();
                }
              }}
              className={`min-h-11 min-w-0 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`}
            />
          </div>
          <UnitSelect
            id={`${id}-pack-unit`}
            label="Unit"
            value={value.packUnit}
            onChange={(unit) => onChange({ packUnit: unit })}
            emptyLabel="No pack"
            disabled={busy}
          />
        </div>
      </div>
      <RowOutcome
        id={id}
        row={row}
        value={value}
        problem={problem}
        existing={existing}
        busy={busy}
        onChange={onChange}
      />
    </li>
  );
}

/** What happens to the row: an existing product it uses, one it may be, or why it can't go. */
function RowOutcome({
  id,
  row,
  value,
  problem,
  existing,
  busy,
  onChange,
}: {
  id: string;
  row: NamingRow;
  value: RowValue;
  problem?: string;
  existing?: ExistingProduct;
  busy: boolean;
  onChange: (patch: RowEdit) => void;
}) {
  return (
    <>
      {value.use ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm" data-testid={`${id}-use`}>
          <span>
            Uses the existing product <span className="font-medium">{productTitle(value.use)}</span>
            .
          </span>
          <Button
            variant="ghost"
            className="px-2"
            disabled={busy}
            onClick={() => onChange({ use: null })}
          >
            Create a new product instead
          </Button>
        </div>
      ) : existing ? (
        <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <span>{productTitle(existing)} already exists with this ingredient.</span>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => onChange({ use: existing, duplicate: false })}
          >
            Use {productTitle(existing)}
          </Button>
          <Button
            variant="ghost"
            className="px-2"
            disabled={busy}
            onClick={() => onChange({ duplicate: true })}
          >
            Create another
          </Button>
        </div>
      ) : problem ? (
        <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">
          {problem}
        </p>
      ) : null}
      {value.use || existing ? null : (
        <NamingCandidates
          candidates={row.candidates}
          disabled={busy}
          onUse={(product) => onChange({ use: product, duplicate: false })}
        />
      )}
    </>
  );
}

/** The row's tick box, its printed wording, and what Create will do with it. */
function RowLabel({
  row,
  value,
  busy,
  onChange,
}: {
  row: NamingRow;
  value: RowValue;
  busy: boolean;
  onChange: (patch: RowEdit) => void;
}) {
  const lineLabel = row.raw_text_norm ?? "(no text)";
  return (
    <label className={`flex min-h-11 items-start gap-2 rounded-md lg:min-h-10 ${focusRing}`}>
      <input
        type="checkbox"
        checked={value.ticked}
        disabled={busy}
        onChange={(e) => onChange({ ticked: e.target.checked })}
        className={`mt-1 size-5 shrink-0 ${focusRing}`}
        aria-label={`Include ${lineLabel}`}
      />
      <span className="min-w-0">
        <span className="block font-mono text-sm break-words">{lineLabel}</span>
        <span className={hintClass}>
          {row.vendor.name} · {row.line_count} {row.line_count === 1 ? "line" : "lines"}
        </span>
        {value.ticked ? (
          <span className="block text-xs font-medium text-neutral-800 dark:text-neutral-200">
            {value.use ? "Will use an existing product" : "Will be created"}
          </span>
        ) : null}
      </span>
    </label>
  );
}
