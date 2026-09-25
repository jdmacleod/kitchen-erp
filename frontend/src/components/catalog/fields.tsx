import { useId, useState, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { focusRing } from "../ui";

/** The input chrome shared by Field, selects, and textareas. */
export const inputClass = `min-w-0 min-h-11 lg:min-h-10 rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 placeholder:text-neutral-400 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 ${focusRing}`;

export const labelClass = "text-sm font-medium";
export const hintClass = "text-xs text-neutral-600 dark:text-neutral-400";

type SelectFieldProps = SelectHTMLAttributes<HTMLSelectElement> & {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
};

export function SelectField({ id, label, hint, className = "", children, ...rest }: SelectFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <select id={id} aria-describedby={hintId} className={inputClass} {...rest}>
        {children}
      </select>
      {hint ? (
        <p id={hintId} className={hintClass}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

type TextAreaFieldProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  id: string;
  label: string;
  hint?: string;
};

export function TextAreaField({ id, label, hint, className = "", ...rest }: TextAreaFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <textarea id={id} aria-describedby={hintId} rows={3} className={inputClass} {...rest} />
      {hint ? (
        <p id={hintId} className={hintClass}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

interface RadioOption<V extends string> {
  value: V;
  label: string;
}

interface RadioGroupProps<V extends string> {
  name: string;
  legend: string;
  options: readonly RadioOption<V>[];
  value: V;
  onChange: (value: V) => void;
  hint?: string;
  disabled?: boolean;
}

/** A row of native radios inside a fieldset; the legend is the accessible name. */
export function RadioGroup<V extends string>({ name, legend, options, value, onChange, hint, disabled }: RadioGroupProps<V>) {
  const hintId = useId();
  return (
    <fieldset className="flex flex-col gap-1" aria-describedby={hint ? hintId : undefined}>
      <legend className={labelClass}>{legend}</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {options.map((option) => (
          <label key={option.value} className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={disabled}
              onChange={() => onChange(option.value)}
              className={`size-4 ${focusRing}`}
            />
            {option.label}
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

/**
 * One tone per meaning (spec 08): olive for success, committed, cheapest and on sale;
 * squash for drafts, warnings and guesses; tomato for failures; neutral for information.
 * Never herb: herb means "you can act on this".
 */
const badgeTones = {
  neutral: "bg-neutral-200 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200",
  good: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  warn: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  danger: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
} as const;

export type BadgeTone = keyof typeof badgeTones;

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${badgeTones[tone]}`}>
      {children}
    </span>
  );
}

interface DisclosureProps {
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
  /** Controlled mode: the caller owns the open state. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

/** A native details/summary block: keyboard and screen-reader behaviour for free. */
export function Disclosure({ summary, children, defaultOpen = false, open, onOpenChange, className = "" }: DisclosureProps) {
  const [internal, setInternal] = useState(defaultOpen);
  const isOpen = open ?? internal;
  return (
    <details
      open={isOpen}
      onToggle={(e) => {
        const next = e.currentTarget.open;
        if (next === isOpen) return;
        setInternal(next);
        onOpenChange?.(next);
      }}
      className={className}
    >
      <summary className={`cursor-pointer rounded-md py-2 text-sm font-medium select-none ${focusRing}`}>
        {summary}
      </summary>
      {isOpen ? <div className="mt-2 flex flex-col gap-4">{children}</div> : null}
    </details>
  );
}

/** Label text for a confirmation state, used in tables and rows. */
export function ConfirmedBadge({ confirmed }: { confirmed: boolean }) {
  return confirmed ? <Badge tone="good">confirmed</Badge> : <Badge tone="warn">unconfirmed</Badge>;
}

/**
 * A quality rating as walnut stars (T13b). The label carries the number, so the
 * stars are never the only signal; unrated reads as a dash.
 */
export function QualityStars({ rating }: { rating: number | null }) {
  if (rating === null) return <span aria-label="Unrated">—</span>;
  return (
    <span role="img" aria-label={`${rating} of 5 stars`} className="tracking-tight whitespace-nowrap text-neutral-800 dark:text-neutral-200">
      {"★".repeat(rating)}
      <span className="text-neutral-300 dark:text-neutral-700">{"★".repeat(5 - rating)}</span>
    </span>
  );
}
