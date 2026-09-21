import { useMemo } from "react";
import { useUnits, type Unit } from "../../api/catalog";
import { SelectField } from "./fields";

interface UnitSelectProps {
  id: string;
  label: string;
  value: string;
  onChange: (code: string) => void;
  /** Restrict to these dimensions, e.g. ["mass", "volume"]. */
  dimensions?: readonly string[];
  /** The text of the empty option; omit to make a choice mandatory. */
  emptyLabel?: string | null;
  hint?: string;
  required?: boolean;
  disabled?: boolean;
}

const dimensionOrder = ["mass", "volume", "count"];

function dimensionRank(d: string): number {
  const i = dimensionOrder.indexOf(d);
  return i === -1 ? dimensionOrder.length : i;
}

/** A select of the seeded units, grouped by dimension. Loads once and caches. */
export function UnitSelect({
  id,
  label,
  value,
  onChange,
  dimensions,
  emptyLabel = "—",
  hint,
  required,
  disabled,
}: UnitSelectProps) {
  const units = useUnits();

  const groups = useMemo(() => {
    const items = (units.data ?? []).filter((u) => !dimensions || dimensions.includes(u.dimension));
    const byDimension = new Map<string, Unit[]>();
    for (const u of items) {
      const list = byDimension.get(u.dimension) ?? [];
      list.push(u);
      byDimension.set(u.dimension, list);
    }
    return [...byDimension.entries()].sort((a, b) => dimensionRank(a[0]) - dimensionRank(b[0]));
  }, [units.data, dimensions]);

  // A value the list does not (yet) contain is still shown, so a saved unit
  // never disappears from the form while the units load.
  const known = groups.some(([, list]) => list.some((u) => u.code === value));

  return (
    <SelectField
      id={id}
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      hint={units.isError ? "Units could not be loaded." : hint}
      required={required}
      disabled={disabled}
    >
      {emptyLabel !== null ? <option value="">{units.isPending ? "Loading…" : emptyLabel}</option> : null}
      {!known && value ? <option value={value}>{value}</option> : null}
      {groups.map(([dimension, list]) => (
        <optgroup key={dimension} label={dimension}>
          {list.map((u) => (
            <option key={u.code} value={u.code}>
              {u.code}
            </option>
          ))}
        </optgroup>
      ))}
    </SelectField>
  );
}
