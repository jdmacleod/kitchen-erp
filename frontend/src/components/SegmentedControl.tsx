import { focusRing } from "./ui";

interface Option<T extends string> {
  value: T;
  label: string;
}

/**
 * A small enumeration as one control (spec 08: Segmented control): an oat track
 * with the selected segment raised in warm white. Buttons with aria-pressed, so
 * each is a plain Tab stop and says whether it is on.
 */
export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap gap-0.5 rounded-lg bg-neutral-200 p-0.5 dark:bg-neutral-800">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-3 text-sm lg:min-h-9 ${focusRing} ${
            o.value === value
              ? "bg-white font-medium text-neutral-900 shadow-sm dark:bg-neutral-950 dark:text-neutral-100"
              : "text-neutral-700 hover:text-neutral-900 dark:text-neutral-300 dark:hover:text-neutral-100"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
