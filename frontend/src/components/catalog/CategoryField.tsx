import { useState } from "react";
import { CATEGORY_KEYS } from "../CategoryChip";
import { Field } from "../ui";
import { SelectField } from "./fields";

const OTHER = "__other__";
const label = (key: string) => key[0].toUpperCase() + key.slice(1);

/**
 * An ingredient's category: one of the nine the chips colour (UI-3.6), chosen
 * from a list instead of typed, or "Other…" for free text, which still works
 * (it gets the neutral chip). An existing free-text value opens as Other.
 */
export function CategoryField({
  id,
  value,
  onChange,
  disabled,
  hint,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  hint?: string;
}) {
  const known = (CATEGORY_KEYS as readonly string[]).includes(value.trim().toLowerCase());
  const [other, setOther] = useState(value.trim() !== "" && !known);
  const selected = other ? OTHER : known ? value.trim().toLowerCase() : "";
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <SelectField
        id={id}
        label="Category"
        value={selected}
        disabled={disabled}
        hint={hint}
        onChange={(e) => {
          if (e.target.value === OTHER) {
            setOther(true);
            return;
          }
          setOther(false);
          onChange(e.target.value);
        }}
      >
        <option value="">No category</option>
        {CATEGORY_KEYS.map((key) => (
          <option key={key} value={key}>
            {label(key)}
          </option>
        ))}
        <option value={OTHER}>Other…</option>
      </SelectField>
      {other ? (
        <Field
          id={`${id}-other`}
          label="Other category"
          autoComplete="off"
          disabled={disabled}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          hint="Any name works; only the categories above get a colour."
        />
      ) : null}
    </div>
  );
}
