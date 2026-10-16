import type { NameChange } from "../../api/naming";
import { hintClass } from "../catalog/fields";
import { focusRing } from "../ui";

const SOURCE: Record<NameChange["source"], string> = {
  learned: "learned at this store",
  spelling: "spelling",
  vocabulary: "from your ingredients",
};

/** Sentence case, as the server builds the name: "rvrbnd bread flour" → "Rvrbnd bread flour". */
function sentence(words: string[]): string {
  const text = words.join(" ").toLowerCase();
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

/** The suggested name with every change applied except the ones undone (issue 384). */
export function nameFromWords(
  words: string[],
  changes: NameChange[],
  undone: ReadonlySet<number>,
): string {
  const shown = words.map((w) => w.toLowerCase());
  for (const c of changes) if (!undone.has(c.index)) shown[c.index] = c.shown;
  return sentence(shown);
}

/**
 * The words the suggested name shows differently from the receipt, one toggle
 * each: pressing one puts the printed word back (issue 384).
 */
export function NameChanges({
  changes,
  undone,
  disabled,
  onToggle,
}: {
  changes: NameChange[];
  undone: ReadonlySet<number>;
  disabled: boolean;
  onToggle: (index: number) => void;
}) {
  if (changes.length === 0) return null;
  return (
    <ul aria-label="Words changed from the receipt" className="flex flex-wrap gap-1">
      {changes.map((c) => {
        const off = undone.has(c.index);
        return (
          <li key={c.index}>
            <button
              type="button"
              aria-pressed={!off}
              disabled={disabled}
              onClick={() => onToggle(c.index)}
              title={off ? `Show "${c.shown}" again` : `Put "${c.printed}" back`}
              className={`inline-flex min-h-11 items-center gap-1 rounded-full border px-2 text-xs lg:min-h-8 ${focusRing} ${
                off
                  ? "border-neutral-300 text-neutral-600 line-through dark:border-neutral-700 dark:text-neutral-400"
                  : "border-neutral-300 bg-neutral-100 text-neutral-800 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
              }`}
            >
              <span className="font-mono">{c.printed}</span>
              <span aria-hidden="true">→</span>
              <span className="sr-only">shown as</span>
              <span>{c.shown}</span>
              <span className={hintClass}>· {SOURCE[c.source]}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
