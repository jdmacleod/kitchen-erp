import { useState, type RefObject } from "react";
import { errorMessage } from "../../api/client";
import { formatPack, useProductSearch, type SearchHit } from "../../api/catalog";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Badge } from "./fields";
import { Combobox } from "./Combobox";

interface ProductTypeaheadProps {
  id: string;
  label?: string;
  hideLabel?: boolean;
  placeholder?: string;
  hint?: string;
  onSelect: (hit: SearchHit) => void;
  /** Clear the box after a selection (default true; a picker keeps the name instead). */
  clearOnSelect?: boolean;
  limit?: number;
  autoFocus?: boolean;
  disabled?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
  debounceMs?: number;
}

const matchLabel: Record<SearchHit["match"], string> = {
  barcode: "barcode",
  name: "name",
  brand: "brand",
  ingredient: "ingredient",
};

/**
 * Search products by name, brand, ingredient, or barcode as you type. Ranked
 * hits come from GET /products/search; Phase 2's review and manual-entry
 * screens reuse this box to pick a product for a receipt line.
 */
export function ProductTypeahead({
  id,
  label = "Search products",
  hideLabel = false,
  placeholder = "Name, brand, ingredient, or barcode",
  hint,
  onSelect,
  clearOnSelect = true,
  limit = 10,
  autoFocus,
  disabled,
  inputRef,
  debounceMs = 150,
}: ProductTypeaheadProps) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, debounceMs);
  const search = useProductSearch(debounced, limit);
  const hits = text.trim() ? (search.data ?? []) : [];

  let status: string | undefined;
  if (text.trim()) {
    if (search.isError) status = errorMessage(search.error);
    else if (search.isPending || search.isFetching || debounced !== text) status = "Searching…";
    else if (hits.length === 0) status = "No products match.";
  }

  return (
    <Combobox<SearchHit>
      id={id}
      label={label}
      hideLabel={hideLabel}
      placeholder={placeholder}
      hint={hint}
      listLabel="Products"
      inputValue={text}
      onInputChange={setText}
      items={hits}
      getKey={(hit) => hit.id}
      status={status}
      disabled={disabled}
      autoFocus={autoFocus}
      inputRef={inputRef}
      onSelect={(hit) => {
        setText(clearOnSelect ? "" : hit.brand ? `${hit.brand} ${hit.name}` : hit.name);
        onSelect(hit);
      }}
      renderItem={(hit) => <HitRow hit={hit} />}
    />
  );
}

export function HitRow({ hit }: { hit: SearchHit }) {
  const pack = formatPack(hit.pack_qty, hit.pack_unit);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
      <span className="min-w-0">
        <span className="font-medium">{hit.name}</span>
        {hit.brand ? <span className="text-neutral-600 dark:text-neutral-400"> · {hit.brand}</span> : null}
        {pack ? <span className="text-neutral-600 dark:text-neutral-400"> · {pack}</span> : null}
      </span>
      <span className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
        <span>{hit.ingredient.name}</span>
        <Badge>{matchLabel[hit.match]}</Badge>
      </span>
    </div>
  );
}
