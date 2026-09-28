import { useState } from "react";
import { formatLatLon } from "../../api/geo";
import { parseLatLon } from "../../lib/latlon";
import { CoordinatesField } from "./CoordinatesField";

type Point = { lat: string; lon: string };

/**
 * Where the draft pin is, and the two ways to say so that are not a click.
 *
 * On a fresh deployment the map has no tiles and no pins, so the only way to
 * place the first location was to click a featureless grey field and hope
 * (issue #18). Those coordinates then drive nearest-store defaulting, the
 * distance labels and the cheapest-nearby layer for the life of the deployment,
 * which is a lot to rest on a guess.
 *
 * The empty map still opens on open ocean: that default is deliberate, so that
 * nothing about the household is implied before the first pin exists. Reading
 * this device's position is a button someone presses, not something the page
 * does on arrival, so the default is untouched.
 */
export function DraftPointControl({ draft, onPoint, disabled }: { draft: Point | null; onPoint: (point: Point | null) => void; disabled?: boolean }) {
  const [text, setText] = useState(() => (draft ? formatLatLon(draft.lat, draft.lon) : ""));

  // A click on the map is the other direction of the same state, so it has to
  // reach the field. Adjusted during render rather than in an effect: React
  // restarts the render with the new value before anything is shown, where an
  // effect would paint the stale text first and then cascade a second render.
  // Compared by value, so typing "33.5,-120" is not rewritten under the cursor
  // by the point it just produced.
  const draftText = draft ? formatLatLon(draft.lat, draft.lon) : "";
  const [mirrored, setMirrored] = useState(draftText);
  if (draftText !== mirrored) {
    setMirrored(draftText);
    const shown = parseLatLon(text);
    // Only when there IS a draft to show. A draft cleared because the field
    // stopped reading must not then blank the field and erase the half-typed
    // pair that cleared it.
    if (draft && (!shown || shown.lat !== draft.lat || shown.lon !== draft.lon)) {
      setText(draftText);
    }
  }

  const onText = (value: string) => {
    setText(value);
    const point = parseLatLon(value);
    // A pin that no longer matches what the field says is a pin nobody chose:
    // editing a good pair into a bad one, or clearing the field, used to leave
    // the old point in place and submittable. The pin follows the field.
    onPoint(point);
  };

  return (
    <div className="flex flex-col gap-2">
      <p className="font-mono text-xs text-neutral-600 dark:text-neutral-400" data-testid="draft-point">
        {draft ? formatLatLon(draft.lat, draft.lon) : "No pin yet — click the map, or give the coordinates below."}
      </p>
      <CoordinatesField id="draft-coordinates" value={text} onChange={onText} disabled={disabled} />
    </div>
  );
}
