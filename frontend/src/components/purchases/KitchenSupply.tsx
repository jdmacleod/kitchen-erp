import { useEffect, useRef, type KeyboardEvent } from "react";
import { errorMessage } from "../../api/client";
import { supplyLabel, useSupplyCategories, type SupplyCategory } from "../../api/supplies";
import { hintClass } from "../catalog/fields";
import { Alert, Button, focusRing } from "../ui";

/**
 * "Kitchen supply" on a receipt line (spec 04, 2T; issue 262): which kind it
 * is, one button per category. The line keeps its amount and has no product or
 * price; the choice is remembered for the store's same wording, like Ignore.
 * Escape or Cancel closes it without choosing.
 */
export function SupplyChoices({
  id,
  subject,
  busy,
  onChoose,
  onCancel,
}: {
  id: string;
  /** What the choice is for, in the title: "line 4", "BRIGHTWRAP FOIL". */
  subject: string;
  busy: boolean;
  onChoose: (category: SupplyCategory) => void;
  onCancel: () => void;
}) {
  const categories = useSupplyCategories();
  const first = useRef<HTMLButtonElement>(null);
  const loaded = categories.isSuccess;
  useEffect(() => {
    if (loaded) first.current?.focus();
  }, [loaded]);
  // On each button: the group itself is not interactive.
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    onCancel();
  };
  return (
    <div id={id} role="group" aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <p id={`${id}-title`} className="text-sm font-medium">
        Kitchen supply: which kind is {subject}?
      </p>
      <p className={hintClass}>
        For cooking and storing food. Cleaning, serving and appliance supplies: use Ignore.
      </p>
      {categories.isPending ? (
        <p role="status" className={hintClass}>
          Loading…
        </p>
      ) : categories.isError ? (
        <Alert tone="error">{errorMessage(categories.error)}</Alert>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {categories.data.map((c, i) => (
            <li key={c.key}>
              <button
                ref={i === 0 ? first : undefined}
                type="button"
                disabled={busy}
                onClick={() => onChoose(c.key)}
                onKeyDown={onKeyDown}
                className={`flex min-h-11 w-full flex-col items-start rounded-lg border border-neutral-300 px-3 py-2 text-left text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800 ${focusRing}`}
              >
                <span className="font-medium">{c.label}</span>
                <span className="text-xs text-neutral-600 dark:text-neutral-400">{c.examples}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Button
        variant="ghost"
        className="min-h-11 lg:min-h-8 self-start px-2 text-xs"
        onClick={onCancel}
        onKeyDown={onKeyDown}
      >
        Cancel
      </Button>
    </div>
  );
}

/** A supply line's category as people read it ("Wraps and papers"). */
export function SupplyName({ category }: { category: string | null | undefined }) {
  const categories = useSupplyCategories();
  return <span>{supplyLabel(categories.data, category)}</span>;
}

/** A line set aside from the price book, where its product would be: ignored, or its supply's kind. */
export function SetAside({
  line,
}: {
  line: { resolution: string | null; supply_category?: string | null };
}) {
  return line.resolution === "supply" ? (
    <SupplyName category={line.supply_category} />
  ) : (
    <span className="italic">ignored</span>
  );
}
