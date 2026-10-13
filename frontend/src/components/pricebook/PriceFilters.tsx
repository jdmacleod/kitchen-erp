import type { BrandScope, PriceFilters as Filters } from "../../api/pricebook";
import { SelectField } from "../catalog/fields";
import { focusRing } from "../ui";

interface PriceFiltersProps {
  id: string;
  value: Filters;
  onChange: (next: Filters) => void;
  /** Hide the promo toggle where it does not apply. */
  promo?: boolean;
  stale?: boolean;
  /** Offer the store-brand filter (2R-3): only where the server supports it. */
  brands?: boolean;
}

/** Minimum quality plus the exclude-stale and exclude-promo toggles, shared by the price views. */
export function PriceFilters({
  id,
  value,
  onChange,
  promo = true,
  stale = true,
  brands = false,
}: PriceFiltersProps) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <SelectField
        id={`${id}-min-quality`}
        label="Minimum quality"
        value={value.min_quality ?? ""}
        onChange={(e) =>
          onChange({ ...value, min_quality: e.target.value ? Number(e.target.value) : "" })
        }
        className="w-40"
      >
        <option value="">Any</option>
        {[1, 2, 3, 4, 5].map((q) => (
          <option key={q} value={q}>
            {q}/5 or better
          </option>
        ))}
      </SelectField>
      {brands ? (
        <SelectField
          id={`${id}-brand-scope`}
          label="Brands"
          value={value.brand_scope ?? "all"}
          onChange={(e) => onChange({ ...value, brand_scope: e.target.value as BrandScope })}
          className="w-44"
        >
          <option value="all">All brands</option>
          <option value="store">Store brands</option>
          <option value="national">National brands</option>
        </SelectField>
      ) : null}
      {stale ? (
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={Boolean(value.exclude_stale)}
            onChange={(e) => onChange({ ...value, exclude_stale: e.target.checked })}
            className={`size-4 ${focusRing}`}
          />
          Exclude stale
        </label>
      ) : null}
      {promo ? (
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={Boolean(value.exclude_promo)}
            onChange={(e) => onChange({ ...value, exclude_promo: e.target.checked })}
            className={`size-4 ${focusRing}`}
          />
          Exclude sale prices
        </label>
      ) : null}
    </div>
  );
}
