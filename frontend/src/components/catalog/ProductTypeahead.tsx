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
  /** The text as typed, e.g. so "New product" can start from it. */
  onTextChange?: (text: string) => void;
  /**
   * Offer `Create product "…"` for what was typed, as the vendor and ingredient
   * pickers do, instead of ending at "No products match" (#33).
   */
  onCreate?: (name: string) => void;
}

type Option = { kind: "hit"; hit: SearchHit } | { kind: "create"; name: string };

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
  onTextChange,
  onCreate,
}: ProductTypeaheadProps) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, debounceMs);
  const search = useProductSearch(debounced, limit);
  const trimmed = text.trim();
  const hits = trimmed ? (search.data ?? []) : [];
  const settled = debounced === text && !search.isPending && !search.isFetching;

  const options: Option[] = hits.map((hit) => ({ kind: "hit", hit }));
  // Offered once the search has answered, and not for a name that already exists.
  const exact = hits.some((hit) => hit.name.toLowerCase() === trimmed.toLowerCase());
  if (onCreate && trimmed && settled && !search.isError && !exact) options.push({ kind: "create", name: trimmed });

  let status: string | undefined;
  if (trimmed) {
    if (search.isError) status = errorMessage(search.error);
    else if (!settled) status = "Searching…";
    else if (hits.length === 0 && !onCreate) status = "No products match.";
  }

  return (
    <Combobox<Option>
      id={id}
      label={label}
      hideLabel={hideLabel}
      placeholder={placeholder}
      hint={hint}
      listLabel="Products"
      inputValue={text}
      onInputChange={(value) => {
        setText(value);
        onTextChange?.(value);
      }}
      items={options}
      getKey={(option) => (option.kind === "hit" ? option.hit.id : `create:${option.name}`)}
      status={status}
      disabled={disabled}
      autoFocus={autoFocus}
      inputRef={inputRef}
      onSelect={(option) => {
        if (option.kind === "create") {
          onCreate?.(option.name);
          return;
        }
        const { hit } = option;
        setText(clearOnSelect ? "" : hit.brand ? `${hit.brand} ${hit.name}` : hit.name);
        onSelect(hit);
      }}
      renderItem={(option) =>
        option.kind === "hit" ? (
          <HitRow hit={option.hit} />
        ) : (
          <span>
            Create product <span className="font-medium">“{option.name}”</span>
          </span>
        )
      }
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
