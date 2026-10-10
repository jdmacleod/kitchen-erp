import { useState, type RefObject } from "react";
import { formatPack, productTitle, useProducts, type ProductListItem } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Combobox } from "../catalog/Combobox";

interface PinPickerProps {
  id: string;
  /** Only products of the line's ingredient are offered (UI-7.11). */
  ingredientId: string;
  ingredientName: string;
  onPick: (product: ProductListItem) => void;
  onCancel: () => void;
  disabled?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
}

/**
 * The product typeahead behind "Pin product…": a combobox over `/products`
 * filtered to the line's ingredient, so nothing it offers can be refused for
 * being of another ingredient. Opens with the ingredient's products listed, so
 * ArrowDown and Enter pin without typing (UI-7.12); Escape puts it away.
 */
export function PinPicker({ id, ingredientId, ingredientName, onPick, onCancel, disabled, inputRef }: PinPickerProps) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 200);
  const products = useProducts({ ingredientId, q: debounced.trim() || undefined, limit: 10 });
  const items = products.data?.pages[0]?.items ?? [];
  const settled = debounced === text && !products.isFetching;
  let status: string | undefined;
  if (products.isError) status = errorMessage(products.error);
  else if (!settled) status = "Searching…";
  else if (items.length === 0) status = `No products of ${ingredientName}${text.trim() ? " match" : " yet"}.`;

  return (
    <div
      // Capture, so one Escape puts the picker away even while the list is open.
      onKeyDownCapture={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <Combobox<ProductListItem>
        id={id}
        label="Pin product"
        hideLabel
        placeholder={`Products of ${ingredientName}`}
        listLabel="Products"
        inputValue={text}
        onInputChange={setText}
        items={items}
        getKey={(p) => p.id}
        status={status}
        disabled={disabled}
        autoFocus
        inputRef={inputRef}
        onSelect={onPick}
        renderItem={(p) => (
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{productTitle(p)}</span>
            {formatPack(p.pack_qty, p.pack_unit, p.pack_count, p.piece_name) ? (
              <span className="text-xs text-neutral-600 dark:text-neutral-400">{formatPack(p.pack_qty, p.pack_unit, p.pack_count, p.piece_name)}</span>
            ) : null}
          </span>
        )}
      />
    </div>
  );
}
