import { hintClass, inputClass, labelClass } from "../catalog/fields";
import { Button } from "../ui";

/** The store filter and "Tick confident rows" above the naming list (issue 389). */
export function NamingControls({
  vendors,
  vendor,
  onVendor,
  confident,
  onTickConfident,
  disabled,
}: {
  vendors: { id: string; name: string }[];
  vendor: string;
  onVendor: (id: string) => void;
  confident: number;
  onTickConfident: () => void;
  disabled: boolean;
}) {
  if (vendors.length < 2 && confident === 0) return null;
  return (
    <div className="flex flex-wrap items-end gap-3">
      {vendors.length > 1 ? (
        <div className="flex flex-col gap-1">
          <label htmlFor="naming-store" className={labelClass}>
            Store
          </label>
          <select
            id="naming-store"
            value={vendor}
            onChange={(e) => onVendor(e.target.value)}
            className={inputClass}
          >
            <option value="">All stores</option>
            {vendors.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {confident > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={disabled} onClick={onTickConfident}>
            Tick {confident} confident {confident === 1 ? "row" : "rows"}
          </Button>
          <span className={hintClass}>
            Every word read and the ingredient named outright. Look them over, then Create.
          </span>
        </div>
      ) : null}
    </div>
  );
}
