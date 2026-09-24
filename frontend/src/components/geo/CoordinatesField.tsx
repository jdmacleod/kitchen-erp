import { useState } from "react";
import { parseLatLon } from "../../lib/latlon";
import { Button, Field } from "../ui";

/**
 * Where a location is, for a form that has no map in it.
 *
 * One field rather than separate latitude and longitude boxes, because the
 * thing being pasted is one string: every map application copies "lat, lon"
 * together. "Use my location" covers the other way this is known — standing in
 * the shop with the phone that is capturing the receipt.
 *
 * The browser's position goes into this field and nowhere else. It is not
 * stored, not logged, and not sent anywhere until the form is submitted, and
 * then only to this deployment's own API.
 */
/** The shape the field is asking for, inside the synthetic box from SECURITY.md. */
// pii-scan: allow an invented placeholder, shown to teach the format
const PLACEHOLDER = "33.512345, -120.487654";

export function CoordinatesField({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const [locating, setLocating] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);
  // Said here rather than by each caller, so the control behaves the same
  // wherever it is used. The map's draft form told you as you typed and the
  // vendor page stayed silent until submit, which is the same field giving two
  // different answers.
  const unreadable = value.trim() !== "" && parseLatLon(value) === null;

  const locate = () => {
    setGeoError(null);
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGeoError("This browser cannot report a position.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false);
        onChange(`${position.coords.latitude}, ${position.coords.longitude}`);
      },
      () => {
        setLocating(false);
        // Refused, unavailable and timed out are the same thing to the person
        // reading this: type or paste the coordinates instead.
        setGeoError("Could not read this device's position. Type or paste the coordinates instead.");
      },
      { maximumAge: 5 * 60_000, timeout: 8_000 },
    );
  };

  return (
    <div className="flex flex-col gap-2">
      <Field
        id={id}
        label="Coordinates"
        autoComplete="off"
        required
        inputMode="decimal"
        placeholder={PLACEHOLDER}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        hint="Latitude and longitude, separated by a comma. Copy them from a map application, or use this device's position."
      />
      {unreadable ? <p className="text-xs text-red-700 dark:text-red-300">Not a latitude and longitude yet.</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" className="min-h-8 px-2 text-xs" onClick={locate} disabled={disabled || locating}>
          {locating ? "Locating…" : "Use my location"}
        </Button>
        {geoError ? <span className="text-xs text-red-700 dark:text-red-300">{geoError}</span> : null}
      </div>
    </div>
  );
}
