import { annotationLabel, type Annotation } from "../../api/trips";
import { Badge } from "../catalog/fields";

interface TripAnnotationsProps {
  annotations: Annotation[];
  /** A location's name by id, for a note that names only the id. */
  locationName: (id: string) => string | null;
}

/**
 * The planner's notes under the cards (10, Trip page; UI-8.14): one neutral
 * sentence each, with the kind as a word. Stale, estimated and unsourceable
 * carry a squash mark beside their words; nothing is colour alone.
 */
export function TripAnnotations({ annotations, locationName }: TripAnnotationsProps) {
  return (
    <section aria-labelledby="trip-notes-heading" className="flex flex-col gap-2">
      <h2 id="trip-notes-heading" className="font-display text-lg">
        Notes
      </h2>
      {annotations.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">Nothing to note: every item has a price at a store that is open.</p>
      ) : (
        <ul className="flex flex-col gap-1.5 text-sm">
          {annotations.map((a, i) => {
            const { word, tone } = annotationLabel(a.kind);
            const where = a.location_id ? locationName(a.location_id) : null;
            const named = where && !a.text.includes(where) ? where : null;
            return (
              <li key={`${a.kind}-${a.item_id ?? ""}-${a.location_id ?? ""}-${i}`} data-testid="annotation" data-kind={a.kind} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Badge tone={tone}>{word}</Badge>
                <span>
                  {a.text}
                  {named ? <span className="text-neutral-600 dark:text-neutral-400"> — {named}</span> : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
