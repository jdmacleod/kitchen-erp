import { inputClass, labelClass, SelectField } from "../catalog/fields";
import { IDENTIFY_SORTS, type IdentifySort } from "./identifyList";

/** Store, wording and order above the to-identify list; the page keeps them in the address. */
export function IdentifyFilters({
  stores,
  store,
  onStore,
  text,
  onText,
  sort,
  onSort,
}: {
  stores: { id: string; name: string; groups: number }[];
  store: string;
  onStore: (id: string) => void;
  text: string;
  onText: (text: string) => void;
  sort: IdentifySort;
  onSort: (sort: IdentifySort) => void;
}) {
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
      <div className="flex min-w-0 flex-col gap-1">
        <label htmlFor="identify-search" className={labelClass}>
          Wording
        </label>
        <input
          id="identify-search"
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          maxLength={200}
          value={text}
          onChange={(e) => onText(e.target.value)}
          placeholder="Words from the receipt line"
          className={inputClass}
        />
      </div>
      <SelectField
        id="identify-store"
        label="Store"
        value={store}
        onChange={(e) => onStore(e.target.value)}
        className="sm:w-52"
      >
        <option value="">All stores</option>
        {stores.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} ({s.groups})
          </option>
        ))}
      </SelectField>
      <SelectField
        id="identify-sort"
        label="Order"
        value={sort}
        onChange={(e) => onSort(e.target.value as IdentifySort)}
        className="sm:w-44"
      >
        {IDENTIFY_SORTS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </SelectField>
    </div>
  );
}
