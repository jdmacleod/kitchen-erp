import { useId } from "react";
import { Alert } from "../../components/ui";
import type { FieldChange } from "./reviewValues";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * What an update found that the product doesn't have: values Accept adds, values it
 * keeps unless the person chooses the new one, new photos and a posted price; or that
 * it found nothing new.
 */
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
        {/* Accept fills what the product lacks; a value it has is kept unless chosen. */}
        {changes.map((c) =>
          c.chosen ? (
            <li key={c.name}>
              {labels[c.name]}: <span className={muted}>{c.from}</span> → {c.to}{" "}
              <span className={muted}>(your choice)</span>
            </li>
          ) : c.from === "—" ? (
            <li key={c.name}>
              {labels[c.name]}: {c.to} <span className={muted}>(added on Accept)</span>
            </li>
          ) : (
            <li key={c.name}>
              {labels[c.name]}: found {c.to}{" "}
              <span className={muted}>
                (it keeps {c.from} unless you choose {c.to} under Edit details)
              </span>
            </li>
          ),
        )}
        {photos > 0 ? <li>{photos === 1 ? "A new photo" : `${photos} new photos`}</li> : null}
        {price ? <li>Posted price: {price}</li> : null}
      </ul>
    </section>
  );
}
