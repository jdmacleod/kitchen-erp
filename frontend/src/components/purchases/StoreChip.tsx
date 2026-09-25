import { useId, useMemo, useState } from "react";
import { useLocations, type VendorLocation } from "../../api/geo";
import { SelectField } from "../catalog/fields";
import { Button, focusRing } from "../ui";
import { locationLabel, readLastLocation, useGeoPosition } from "./LocationSelect";

/**
 * "Near" is claimed only this close. Farther than this the nearest store is a
 * guess like any other, so the last-used store is offered instead (G2).
 */
export const NEAR_METRES = 1000;

export type StoreGuess =
  | { status: "finding" }
  | { status: "near"; location: VendorLocation }
  | { status: "last"; location: VendorLocation }
  | { status: "none" };

/**
 * Where the person probably is (docs/spec/09, Capture: store detection). With a
 * position, the nearest active location within NEAR_METRES; otherwise the last
 * location used on this device, labelled as such. Never "near" without a fix.
 */
export function useStoreGuess(): { guess: StoreGuess; skip: () => void; locations: VendorLocation[]; isError: boolean } {
  const [geo, skip] = useGeoPosition(true);
  const located = useLocations(geo.status === "ok" ? { near: geo.near } : {});
  const locations = useMemo(() => located.data ?? [], [located.data]);

  let guess: StoreGuess;
  if (geo.status === "pending" || (geo.status === "ok" && (located.isPending || located.isPlaceholderData))) {
    guess = { status: "finding" };
  } else {
    const nearest = geo.status === "ok" ? locations.find((l) => l.active) : undefined;
    const close = nearest && nearest.distance_m !== null && Number(nearest.distance_m) <= NEAR_METRES;
    const lastId = readLastLocation();
    const last = lastId ? locations.find((l) => l.id === lastId && l.active) : undefined;
    if (nearest && close) guess = { status: "near", location: nearest };
    else if (last) guess = { status: "last", location: last };
    else guess = { status: "none" };
  }
  return { guess, skip, locations, isError: located.isError };
}

interface StoreChipProps {
  /** The store in use: the one chosen, or the guess until someone chooses. */
  value: VendorLocation | null;
  /** How `value` was arrived at. "chosen" once the person picks one. */
  source: "near" | "last" | "chosen" | "finding" | "none";
  locations: VendorLocation[];
  onChoose: (location: VendorLocation) => void;
  onSkip: () => void;
  disabled?: boolean;
}

/**
 * The store as a tappable chip (10, Shelf price; Capture sheet). A guess says it
 * is one: "Last used: {vendor} · change" in a squash outline (G2).
 */
export function StoreChip({ value, source, locations, onChoose, onSkip, disabled }: StoreChipProps) {
  const selectId = useId();
  const [changing, setChanging] = useState(false);
  const picking = changing || (source !== "finding" && !value);

  if (source === "finding") {
    return (
      <p role="status" className="flex min-h-11 flex-wrap items-center gap-2 text-sm text-neutral-700 dark:text-neutral-300">
        Finding where you are…
        <Button variant="ghost" onClick={onSkip}>
          Skip
        </Button>
      </p>
    );
  }

  if (picking) {
    return (
      <SelectField
        id={selectId}
        label="Store"
        value={value?.id ?? ""}
        required
        disabled={disabled}
        onChange={(e) => {
          const next = locations.find((l) => l.id === e.target.value);
          if (!next) return;
          // Once a store is chosen the picker folds back into the chip.
          setChanging(false);
          onChoose(next);
        }}
      >
        <option value="">Choose a store</option>
        {locations
          .filter((l) => l.active)
          .map((l) => (
            <option key={l.id} value={l.id}>
              {locationLabel(l)}
            </option>
          ))}
      </SelectField>
    );
  }

  const name = value ? locationLabel(value) : "";
  const text = source === "near" ? `Near ${name}` : source === "last" ? `Last used: ${name}` : name;
  const tone =
    source === "last"
      ? "border-amber-400 text-amber-900 dark:border-amber-600 dark:text-amber-200"
      : "border-neutral-300 text-neutral-900 dark:border-neutral-700 dark:text-neutral-100";
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setChanging(true)}
      aria-label={`${text}. Change store`}
      className={`inline-flex min-h-11 max-w-full items-center gap-1 self-start rounded-full border px-4 text-sm ${tone} ${focusRing}`}
    >
      <span className="min-w-0 truncate font-medium">{text}</span>
      <span aria-hidden="true">·</span>
      <span className="shrink-0 underline">change</span>
    </button>
  );
}
