import { useId } from "react";
import { Alert } from "../../components/ui";
import type { FieldChange } from "./reviewValues";

const muted = "text-neutral-600 dark:text-neutral-400";

/** What accepting an update would bring to the product, or that it would bring nothing. */
export function WhatsNew({
  changes,
  labels,
  photos,
  price,
  headingClass,
}: {
  changes: FieldChange[];
  labels: Record<string, string>;
  photos: number;
  price: string | null;
  headingClass: string;
}) {
  const id = useId();
  if (changes.length === 0 && photos === 0 && !price)
    return (
      <Alert tone="info">
        Nothing new: what was found matches this product. Reject it to clear it from Home.
      </Alert>
    );
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className={headingClass}>
        What&apos;s new
      </h2>
      <ul data-testid="whats-new" className="flex flex-col gap-1 text-sm">
        {changes.map((c) => (
          <li key={c.name}>
            {labels[c.name]}: <span className={muted}>{c.from}</span> → {c.to}
          </li>
        ))}
        {photos > 0 ? <li>{photos === 1 ? "A new photo" : `${photos} new photos`}</li> : null}
        {price ? <li>Posted price: {price}</li> : null}
      </ul>
    </section>
  );
}
