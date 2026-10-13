import { UnitSelect } from "../catalog/UnitSelect";
import { Button, Field } from "../ui";

export interface AmountRow {
  amount: string;
  unit: string;
}

interface AmountRowsProps {
  idPrefix: string;
  rows: AmountRow[];
  onChange: (rows: AmountRow[]) => void;
  disabled?: boolean;
}

/**
 * The (amount, unit) pairs of an item, one row each, as the list shows them.
 * Pairs that cannot combine stay separate here as they do on the row (UI-8.5).
 */
export function AmountRows({ idPrefix, rows, onChange, disabled }: AmountRowsProps) {
  const set = (index: number, patch: Partial<AmountRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-sm font-medium">Amounts</legend>
      {rows.map((row, i) => (
        <div key={i} className="flex flex-wrap items-end gap-2">
          <Field
            id={`${idPrefix}-amount-${i}`}
            label={rows.length > 1 ? `Amount ${i + 1}` : "Amount"}
            inputMode="decimal"
            autoComplete="off"
            className="w-28"
            value={row.amount}
            disabled={disabled}
            onChange={(e) => set(i, { amount: e.target.value })}
          />
          <div className="w-36">
            <UnitSelect
              id={`${idPrefix}-unit-${i}`}
              label={rows.length > 1 ? `Unit ${i + 1}` : "Unit"}
              value={row.unit}
              onChange={(unit) => set(i, { unit })}
              emptyLabel="unit"
              disabled={disabled}
            />
          </div>
          {rows.length > 1 ? (
            <Button
              variant="ghost"
              disabled={disabled}
              onClick={() => onChange(rows.filter((_, j) => j !== i))}
              aria-label={`Remove amount ${i + 1}`}
            >
              Remove
            </Button>
          ) : null}
        </div>
      ))}
      <div>
        <Button
          variant="ghost"
          className="px-2"
          disabled={disabled}
          onClick={() => onChange([...rows, { amount: "", unit: "" }])}
        >
          Add another amount
        </Button>
      </div>
    </fieldset>
  );
}
