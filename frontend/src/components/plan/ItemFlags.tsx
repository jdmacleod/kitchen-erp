import type { ItemKind } from "../../api/shoppingList";
import { SelectField } from "../catalog/fields";
import { focusRing } from "../ui";

interface ItemFlagsProps {
  idPrefix: string;
  kind: ItemKind;
  deferrable: boolean;
  /** "" for none, else "1" to "5". */
  minQuality: string;
  onDeferrable: (value: boolean) => void;
  onMinQuality: (value: string) => void;
  disabled?: boolean;
}

/**
 * "Deferrable" and "Minimum quality" (10, Plan: Inline add), both off by
 * default; the quality applies to ingredient items only, so a product item
 * does not offer it.
 */
export function ItemFlags({
  idPrefix,
  kind,
  deferrable,
  minQuality,
  onDeferrable,
  onMinQuality,
  disabled,
}: ItemFlagsProps) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-10">
        <input
          type="checkbox"
          className={`size-4 ${focusRing}`}
          checked={deferrable}
          disabled={disabled}
          onChange={(e) => onDeferrable(e.target.checked)}
        />
        Deferrable
      </label>
      {kind === "ingredient" ? (
        <SelectField
          id={`${idPrefix}-min-quality`}
          label="Minimum quality"
          className="w-36"
          value={minQuality}
          disabled={disabled}
          onChange={(e) => onMinQuality(e.target.value)}
        >
          <option value="">Any</option>
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={String(n)}>
              {"★".repeat(n)} {n} of 5
            </option>
          ))}
        </SelectField>
      ) : null}
    </div>
  );
}
