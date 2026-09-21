import { focusRing } from "../ui";
import { hintClass, labelClass } from "./fields";

interface RatingInputProps {
  name: string;
  legend?: string;
  value: number | null;
  onChange: (value: number | null) => void;
  hint?: string;
  disabled?: boolean;
}

/** Quality rating 1–5 as native radios, plus "none". Stars are decoration only. */
export function RatingInput({ name, legend = "Quality rating", value, onChange, hint, disabled }: RatingInputProps) {
  const hintId = `${name}-hint`;
  return (
    <fieldset className="flex flex-col gap-1" aria-describedby={hint ? hintId : undefined}>
      <legend className={labelClass}>{legend}</legend>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <label className="inline-flex min-h-10 items-center gap-1.5 text-sm">
          <input
            type="radio"
            name={name}
            value=""
            checked={value === null}
            disabled={disabled}
            onChange={() => onChange(null)}
            className={`size-4 ${focusRing}`}
          />
          none
        </label>
        {[1, 2, 3, 4, 5].map((n) => (
          <label key={n} className="inline-flex min-h-10 items-center gap-1.5 text-sm">
            <input
              type="radio"
              name={name}
              value={String(n)}
              checked={value === n}
              disabled={disabled}
              onChange={() => onChange(n)}
              className={`size-4 ${focusRing}`}
            />
            <span aria-hidden="true" className="text-amber-600 dark:text-amber-400">
              {"★".repeat(n)}
            </span>
            <span className="sr-only">{n}</span>
          </label>
        ))}
      </div>
      {hint ? (
        <p id={hintId} className={hintClass}>
          {hint}
        </p>
      ) : null}
    </fieldset>
  );
}
