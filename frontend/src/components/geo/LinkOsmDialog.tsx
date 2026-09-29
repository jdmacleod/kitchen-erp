import { useId, useState } from "react";
import {
  formatDistance,
  geoErrorMessage,
  isIntegrationDisabled,
  useLinkCandidates,
  useLinkOsm,
  vendorKindLabel,
  type LinkCandidate,
  type LinkedField,
  type VendorLocation,
} from "../../api/geo";
import { fieldLabel } from "../../lib/sources";
import { Dialog } from "../Dialog";
import { Alert, Button, focusRing } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";

function fieldList(fields: LinkedField[]): string {
  return fields.map((f) => fieldLabel[f].toLowerCase()).join(", ");
}

/** What linking the chosen place would do, before anything is written (design D7). */
export function linkPreview(candidate: LinkCandidate): string {
  const parts: string[] = [];
  parts.push(candidate.fills.length ? `Will fill: ${fieldList(candidate.fills)}` : "Fills nothing new");
  if (candidate.keeps.length) parts.push(`Keeps your: ${fieldList(candidate.keeps)} (you edited ${candidate.keeps.length === 1 ? "it" : "them"})`);
  return parts.join(" · ");
}

/**
 * Link an existing location to an OpenStreetMap place near its pin (1F), modeled
 * on Find nearby. Choosing a place shows what it would fill and what a person's
 * edits keep; Link writes it.
 */
export function LinkOsmDialog({ location, onClose, onLinked }: { location: VendorLocation; onClose: () => void; onLinked: () => void }) {
  const titleId = useId();
  const candidates = useLinkCandidates(location.id, true);
  const link = useLinkOsm(location.id);
  const [chosen, setChosen] = useState<string | null>(null);
  const items = candidates.data ?? [];
  const key = (c: LinkCandidate) => `${c.osm_type}/${c.osm_id}`;
  const selected = items.find((c) => key(c) === chosen && !c.linked_to) ?? null;

  return (
    <Dialog open onClose={onClose} labelledBy={titleId} className="max-h-[80vh] overflow-y-auto p-5">
      <h2 id={titleId} className="mb-1 font-display text-xl">
        Link {location.name} to OpenStreetMap
      </h2>
      <p className={`mb-3 text-sm ${muted}`}>
        Places within 250 m of this pin. Linking fills what this location is missing; anything you typed stays. © OpenStreetMap contributors.
      </p>
      {candidates.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Searching near this pin…
        </p>
      ) : candidates.isError && isIntegrationDisabled(candidates.error) ? (
        <p role="status" className={`text-sm ${muted}`}>
          OpenStreetMap lookups are off. Set ENABLE_OVERPASS=true on the server to use them.
        </p>
      ) : candidates.isError ? (
        <Alert tone="error">{geoErrorMessage(candidates.error)}</Alert>
      ) : items.length === 0 ? (
        <p className={`text-sm ${muted}`}>No OpenStreetMap places within 250 m.</p>
      ) : (
        <fieldset>
          <legend className="sr-only">OpenStreetMap places near {location.name}</legend>
          <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {items.map((c) => {
              const id = `${titleId}-${c.osm_type}-${c.osm_id}`;
              const taken = c.linked_to !== null;
              return (
                <li key={key(c)} className="py-1">
                  <label htmlFor={id} className={`flex min-h-11 items-start gap-2 py-1 text-sm ${taken ? "text-neutral-500 dark:text-neutral-400" : ""}`}>
                    <input id={id} type="radio" name={`${titleId}-candidate`} className={`mt-1 size-4 ${focusRing}`} disabled={taken} checked={chosen === key(c)} onChange={() => setChosen(key(c))} />
                    <span className="min-w-0">
                      <span className="font-medium">{c.name ?? `${c.osm_type} ${c.osm_id}`}</span>
                      <span className={`block text-xs ${muted}`}>
                        {[formatDistance(c.distance_m), vendorKindLabel[c.kind_guess].toLowerCase(), c.address, taken ? `Linked to ${c.linked_to?.name}` : null].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </fieldset>
      )}
      {selected ? (
        <p aria-live="polite" className="mt-3 rounded-md bg-neutral-100 px-3 py-2 text-sm dark:bg-neutral-800">
          {linkPreview(selected)}
        </p>
      ) : null}
      {link.isError ? (
        <Alert tone="error" className="mt-3">
          {geoErrorMessage(link.error)}
        </Alert>
      ) : null}
      <div className="mt-4 flex flex-wrap justify-end gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!selected || link.isPending}
          onClick={() => selected && link.mutate({ osm_type: selected.osm_type, osm_id: selected.osm_id }, { onSuccess: onLinked })}
        >
          {link.isPending ? "Linking…" : "Link"}
        </Button>
      </div>
    </Dialog>
  );
}
