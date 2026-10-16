import { useState } from "react";
import { useBrandCompletions } from "../../api/naming";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Combobox } from "../catalog/Combobox";

/**
 * A naming row's Brand (issue 390): prefilled when the wording starts with a known
 * brand, optional, and suggesting known brands once a person types in it.
 */
export function BrandField({
  id,
  value,
  fromWording,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  /** True while the value is still the one the wording gave. */
  fromWording: boolean;
  disabled: boolean;
  onChange: (brand: string) => void;
}) {
  const [typed, setTyped] = useState(false);
  const query = useDebouncedValue(typed ? value : "", 250);
  const found = useBrandCompletions(query);
  const items = typed ? (found.data ?? []).filter((b) => b !== value) : [];
  return (
    <Combobox<string>
      id={id}
      label="Brand"
      placeholder="Optional"
      hint={fromWording && value ? "From the receipt's wording" : undefined}
      inputValue={value}
      onInputChange={(next) => {
        setTyped(true);
        onChange(next);
      }}
      items={items}
      getKey={(b) => b}
      renderItem={(b) => b}
      onSelect={onChange}
      listLabel="Known brands"
      disabled={disabled}
    />
  );
}
