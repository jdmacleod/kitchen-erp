import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { hintClass, inputClass, labelClass } from "./fields";

export interface ComboboxProps<T> {
  id: string;
  label: string;
  /** Visually hide the label (it still names the input for assistive tech). */
  hideLabel?: boolean;
  placeholder?: string;
  hint?: string;
  inputValue: string;
  onInputChange: (value: string) => void;
  items: T[];
  getKey: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  onSelect: (item: T) => void;
  /** A line shown under the list, or instead of it: "Searching…", "No matches". */
  status?: ReactNode;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Name of the list for assistive tech, e.g. "Products". */
  listLabel: string;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  /**
   * A heading for a run of consecutive items, e.g. "From the standard list".
   * Items with a heading are wrapped in a labelled group, which screen readers
   * announce; items returning null stay directly in the list.
   */
  groupOf?: (item: T) => string | null;
  /** Text read for an option instead of its visible content, when that needs context. */
  getLabel?: (item: T) => string | undefined;
}

/**
 * An ARIA 1.2 combobox with a listbox popup. The caller owns the query text and
 * the items; this handles opening, arrow keys, Enter, Escape, and pointer
 * selection. Nothing is selected without an explicit Enter or click.
 */
export function Combobox<T>({
  id,
  label,
  hideLabel = false,
  placeholder,
  hint,
  inputValue,
  onInputChange,
  items,
  getKey,
  renderItem,
  onSelect,
  status,
  disabled,
  autoFocus,
  listLabel,
  inputRef,
  groupOf,
  getLabel,
}: ComboboxProps<T>) {
  const [open, setOpen] = useState(false);
  const [rawActive, setActive] = useState(-1);
  // Clamp at render time so the highlight stays inside a list that shrank.
  const active = rawActive >= items.length ? items.length - 1 : rawActive;
  const localRef = useRef<HTMLInputElement>(null);
  const ref = inputRef ?? localRef;
  const listId = `${id}-listbox`;
  const hintId = hint ? `${id}-hint` : undefined;

  const hasPopup = open && !disabled && (items.length > 0 || Boolean(status));
  const expanded = hasPopup;

  const select = (item: T) => {
    onSelect(item);
    setOpen(false);
    setActive(-1);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        if (!open) setOpen(true);
        if (items.length === 0) return;
        setActive((active + 1) % items.length);
        return;
      }
      case "ArrowUp": {
        event.preventDefault();
        if (!open) setOpen(true);
        if (items.length === 0) return;
        setActive(active <= 0 ? items.length - 1 : active - 1);
        return;
      }
      case "Enter": {
        // Enter with nothing highlighted chooses nothing and lets a
        // surrounding form submit as usual.
        if (hasPopup && active >= 0 && active < items.length) {
          event.preventDefault();
          select(items[active]);
        }
        return;
      }
      case "Escape": {
        if (hasPopup) {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          setActive(-1);
        }
        return;
      }
      case "Tab": {
        setOpen(false);
        return;
      }
      default:
        return;
    }
  };

  return (
    <div className="relative flex flex-col gap-1">
      <label htmlFor={id} className={hideLabel ? "sr-only" : labelClass}>
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        type="text"
        role="combobox"
        autoComplete="off"
        spellCheck={false}
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={hasPopup && active >= 0 && active < items.length ? `${id}-option-${active}` : undefined}
        aria-describedby={hintId}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        value={inputValue}
        onChange={(e) => {
          onInputChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false);
          setActive(-1);
        }}
        onKeyDown={onKeyDown}
        className={inputClass}
      />
      {hint ? (
        <p id={hintId} className={hintClass}>
          {hint}
        </p>
      ) : null}
      {hasPopup ? (
        <div
          className="absolute top-full right-0 left-0 z-20 mt-1 max-h-72 overflow-y-auto rounded-md border border-neutral-300 bg-white shadow-md dark:border-neutral-700 dark:bg-neutral-900"
          // Keep focus in the input when a row is clicked, so blur does not
          // close the list before the click lands.
          onMouseDown={(e) => e.preventDefault()}
        >
          <ul id={listId} role="listbox" aria-label={listLabel}>
            {segments(items, groupOf).map((segment) => {
              const options = segment.entries.map(({ item, index }) => (
                <li
                  key={getKey(item)}
                  id={`${id}-option-${index}`}
                  role="option"
                  aria-selected={index === active}
                  aria-label={getLabel?.(item)}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => select(item)}
                  className={`flex min-h-11 cursor-pointer flex-col justify-center px-3 py-2 text-sm lg:block lg:min-h-0 ${
                    index === active ? "bg-neutral-200 dark:bg-neutral-800" : ""
                  }`}
                >
                  {renderItem(item)}
                </li>
              ));
              if (segment.heading === null) return options;
              const headingId = `${id}-group-${segment.entries[0].index}`;
              return (
                <li key={headingId} role="presentation">
                  <div id={headingId} role="presentation" className="border-t border-neutral-200 px-3 pt-2 pb-1 text-xs text-neutral-600 dark:border-neutral-800 dark:text-neutral-400">
                    {segment.heading}
                  </div>
                  <ul role="group" aria-labelledby={headingId}>
                    {options}
                  </ul>
                </li>
              );
            })}
          </ul>
          {status ? (
            <p role="status" className={`px-3 py-2 ${hintClass}`}>
              {status}
            </p>
          ) : null}
        </div>
      ) : (
        // The listbox must exist for aria-controls even while collapsed.
        <ul id={listId} role="listbox" aria-label={listLabel} hidden />
      )}
    </div>
  );
}

/** Split items into runs that share a group heading, keeping each item's list index. */
function segments<T>(items: T[], groupOf?: (item: T) => string | null) {
  const out: { heading: string | null; entries: { item: T; index: number }[] }[] = [];
  items.forEach((item, index) => {
    const heading = groupOf?.(item) ?? null;
    const last = out[out.length - 1];
    if (last && last.heading === heading) last.entries.push({ item, index });
    else out.push({ heading, entries: [{ item, index }] });
  });
  return out;
}
