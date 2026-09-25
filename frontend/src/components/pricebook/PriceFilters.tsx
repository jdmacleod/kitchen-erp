import type { PriceFilters as Filters } from "../../api/pricebook";
import { SelectField } from "../catalog/fields";
import { focusRing } from "../ui";

interface PriceFiltersProps {
  id: string;
  value: Filters;
  onChange: (next: Filters) => void;
  /** Hide the promo toggle where it does not apply. */
  promo?: boolean;
  stale?: boolean;
}

/** Minimum quality plus the exclude-stale and exclude-promo toggles, shared by the price views. */
export function PriceFilters({ id, value, onChange, promo = true, stale = true }: PriceFiltersProps) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <SelectField
        id={`${id}-min-quality`}
        label="Minimum quality"
        value={value.min_quality ?? ""}
        onChange={(e) => onChange({ ...value, min_quality: e.target.value ? Number(e.target.value) : "" })}
        className="w-40"
      >
        <option value="">Any</option>
        {[1, 2, 3, 4, 5].map((q) => (
          <option key={q} value={q}>
            {q}/5 or better
          </option>
        ))}
      </SelectField>
      {stale ? (
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input type="checkbox" checked={Boolean(value.exclude_stale)} onChange={(e) => onChange({ ...value, exclude_stale: e.target.checked })} className={`size-4 ${focusRing}`} />
          Exclude stale
        </label>
      ) : null}
      {promo ? (
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input type="checkbox" checked={Boolean(value.exclude_promo)} onChange={(e) => onChange({ ...value, exclude_promo: e.target.checked })} className={`size-4 ${focusRing}`} />
          Exclude sale prices
        </label>
      ) : null}
    </div>
  );
}
