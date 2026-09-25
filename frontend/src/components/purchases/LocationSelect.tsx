import { useEffect, useMemo, useRef, useState } from "react";
import { useLocations, type VendorLocation } from "../../api/geo";
import { SelectField } from "../catalog/fields";

const LAST_LOCATION_KEY = "kerp.lastVendorLocationId";

/** The location most recently used on an entry screen, if the browser kept it. */
export function readLastLocation(): string | null {
  try {
    return localStorage.getItem(LAST_LOCATION_KEY);
  } catch {
    return null;
  }
}

export function rememberLocation(id: string): void {
  try {
    localStorage.setItem(LAST_LOCATION_KEY, id);
  } catch {
    // Private mode or storage disabled: the default simply is not remembered.
  }
}

export type GeoState = { status: "pending" } | { status: "ok"; near: string } | { status: "unavailable" };

/**
 * One position fix, asked for once. The coordinates go only to this
 * deployment's own API as the `near` filter; they are never stored or logged.
 * `skip` gives up waiting, as if the browser had said no.
 */
export function useGeoPosition(enabled: boolean): [GeoState, () => void] {
  const [state, setState] = useState<GeoState>(() =>
    enabled && typeof navigator !== "undefined" && navigator.geolocation ? { status: "pending" } : { status: "unavailable" },
  );
  useEffect(() => {
    if (state.status !== "pending") return;
    let cancelled = false;
    const fail = () => {
      if (!cancelled) setState({ status: "unavailable" });
    };
    try {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          if (cancelled) return;
          setState({ status: "ok", near: `${position.coords.latitude},${position.coords.longitude}` });
        },
        fail,
        { maximumAge: 5 * 60_000, timeout: 8_000 },
      );
    } catch {
      fail();
    }
    return () => {
      cancelled = true;
    };
  }, [state.status]);
  return [state, () => setState({ status: "unavailable" })];
}

interface LocationSelectProps {
  id: string;
  label?: string;
  value: string;
  onChange: (id: string) => void;
  /**
   * Fill an empty value with the nearest active location once the browser
   * yields a position, and until then with the most recently used one.
   * Off for editing an existing record.
   */
  autoDefault?: boolean;
  disabled?: boolean;
  hint?: string;
}

/** A location as people name it: the vendor, and the branch when it has its own name. */
export function locationLabel(l: VendorLocation): string {
  return l.name === l.vendor.name ? l.name : `${l.vendor.name} — ${l.name}`;
}

function optionLabel(l: VendorLocation): string {
  const name = locationLabel(l);
  if (l.distance_m === null) return name;
  const metres = Number(l.distance_m);
  if (!Number.isFinite(metres)) return name;
  return metres < 1000 ? `${name} · ${Math.round(metres)} m` : `${name} · ${(metres / 1000).toFixed(1)} km`;
}

/**
 * A select of active vendor locations. With a position, the list is ordered
 * by distance and the nearest is chosen for an empty value; without one the
 * last location used is chosen instead. A choice the user makes is kept.
 */
export function LocationSelect({
  id,
  label = "Location",
  value,
  onChange,
  autoDefault = true,
  disabled,
  hint,
}: LocationSelectProps) {
  const [geo] = useGeoPosition(autoDefault);
  const locations = useLocations(geo.status === "ok" ? { near: geo.near } : {});
  const items = useMemo(() => locations.data ?? [], [locations.data]);
  // A value handed in at the start (the store chosen in Capture) counts as chosen.
  const [touched, setTouched] = useState(() => autoDefault && value !== "");
  // Which default has been applied so far, so the nearest location replaces
  // the remembered one exactly once and never a later user choice.
  const applied = useRef<"none" | "remembered" | "nearest">("none");

  useEffect(() => {
    if (!autoDefault || touched || items.length === 0) return;
    if (geo.status === "ok" && applied.current !== "nearest") {
      // Until the distance-ordered list arrives, the old list is a placeholder.
      if (locations.isPlaceholderData) return;
      applied.current = "nearest";
      const nearest = items.find((l) => l.active);
      if (nearest) onChange(nearest.id);
      return;
    }
    if (applied.current === "none") {
      applied.current = "remembered";
      const last = readLastLocation();
      if (!value && last && items.some((l) => l.id === last)) onChange(last);
    }
  }, [autoDefault, touched, items, geo.status, value, onChange, locations.isPlaceholderData]);

  let status = hint;
  if (locations.isError) status = "Locations could not be loaded.";
  else if (autoDefault && geo.status === "pending" && !touched) status = "Locating the nearest store…";

  return (
    <SelectField
      id={id}
      label={label}
      value={value}
      required
      disabled={disabled}
      hint={status}
      onChange={(e) => {
        setTouched(true);
        onChange(e.target.value);
      }}
    >
      <option value="">{locations.isPending ? "Loading…" : "Choose a location"}</option>
      {items.map((l) => (
        <option key={l.id} value={l.id}>
          {optionLabel(l)}
        </option>
      ))}
    </SelectField>
  );
}
