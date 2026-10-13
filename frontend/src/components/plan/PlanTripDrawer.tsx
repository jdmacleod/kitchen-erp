import { useMemo, useState } from "react";
import { Link } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { useLocations, type HomeBase } from "../../api/geo";
import { useCreateTrip, type PlanDefaults, type Trip, type TripCreateInput } from "../../api/trips";
import { Disclosure, SelectField } from "../catalog/fields";
import { Drawer } from "../Drawer";
import { Alert, Field, focusRing } from "../ui";
import { isNonNegativeDecimal } from "../../lib/decimal";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";

/** The documented default of `PLAN_VALUE_OF_TIME_PER_HOUR` (17, P4), used only if `/plan/defaults` cannot be read. */
export const DEFAULT_VALUE_OF_TIME = "20.00";
export const DEFAULT_MAX_STOPS = 3;

/** The inputs a drawer starts from: a Replan's (UI-8.15); a new plan starts from the household defaults. */
export type TripPrefill = Partial<Pick<Trip, "origin_place_id" | "destination_place_id" | "planned_at" | "value_of_time_per_hour" | "max_stops" | "mileage_rate" | "stop_overhead_min">>;

interface PlanTripDrawerProps {
  homeBaseId: string;
  kitchen: HomeBase;
  kitchens: HomeBase[];
  prefill?: TripPrefill | null;
  /** The household's planning defaults (`GET /plan/defaults`); a field the prefill leaves out starts here. */
  defaults?: PlanDefaults | null;
  onClose: () => void;
  onPlanned: (trip: Trip) => void;
}

/** The top of the next hour, in the viewer's zone, as a datetime-local value. */
export function nextHourLocal(now: Date = new Date()): string {
  const next = new Date(now);
  next.setMinutes(0, 0, 0);
  next.setHours(next.getHours() + 1);
  return toDateTimeLocal(next);
}

const KITCHEN = "kitchen";
const SAME = "same";
const PREVIOUS_START = "previous-start";
const PREVIOUS_END = "previous-end";

interface PlaceOption {
  value: string;
  label: string;
  placeId: string | null;
}

interface FormState {
  origin: string;
  destination: string;
  when: string;
  value: string;
  maxStops: string;
  mileage: string;
  overhead: string;
}

function initialState(prefill: TripPrefill | null | undefined, defaults: PlanDefaults | null | undefined, options: PlaceOption[]): FormState {
  const byPlace = (placeId: string | null | undefined) => options.find((o) => o.placeId !== null && o.placeId === placeId)?.value;
  const origin = byPlace(prefill?.origin_place_id) ?? KITCHEN;
  const destination = prefill?.destination_place_id && prefill.destination_place_id !== prefill.origin_place_id ? (byPlace(prefill.destination_place_id) ?? SAME) : SAME;
  const planned = prefill?.planned_at ? new Date(prefill.planned_at) : null;
  const when = planned && !Number.isNaN(planned.getTime()) && planned.getTime() > Date.now() ? toDateTimeLocal(planned) : nextHourLocal();
  return {
    origin,
    destination,
    when,
    value: prefill?.value_of_time_per_hour ?? defaults?.value_of_time_per_hour ?? DEFAULT_VALUE_OF_TIME,
    maxStops: String(prefill?.max_stops ?? defaults?.max_stops_default ?? DEFAULT_MAX_STOPS),
    mileage: prefill?.mileage_rate ?? "",
    overhead: prefill?.stop_overhead_min === null || prefill?.stop_overhead_min === undefined ? "" : String(prefill.stop_overhead_min),
  };
}

/**
 * "Plan a trip" (10, Trips: Drawer; UI-8.12): where to start and end, when, what
 * an hour is worth, how many stops, and under More the mileage rate and minutes
 * per stop. The server plans from the kitchen's open list and answers with the
 * three variants; an empty list is refused with a link to it.
 *
 * Places are chosen by their `place_id`: the current kitchen, the household's
 * other kitchens and every vendor location. The current kitchen is sent as
 * "nothing", which the server reads as the kitchen's place (17, Endpoints).
 */
export function PlanTripDrawer({ homeBaseId, kitchen, kitchens, prefill = null, defaults = null, onClose, onPlanned }: PlanTripDrawerProps) {
  const locations = useLocations({});
  const options = useMemo<PlaceOption[]>(() => {
    const out: PlaceOption[] = [{ value: KITCHEN, label: `${kitchen.name} (kitchen)`, placeId: kitchen.place_id ?? null }];
    for (const k of kitchens) {
      if (k.id !== kitchen.id && k.place_id) out.push({ value: `place:${k.place_id}`, label: `${k.name} (kitchen)`, placeId: k.place_id });
    }
    // A Replan keeps its start and end even when the API names no place for
    // them, so the new plan is made from the same inputs (UI-8.15).
    // Checked against the kitchens only, which answer before the drawer opens,
    // so the option does not vanish under a chosen value when locations load.
    const known = (id: string | undefined) => !id || out.some((o) => o.placeId === id);
    const origin = prefill?.origin_place_id;
    const destination = prefill?.destination_place_id;
    if (origin && !known(origin)) out.push({ value: PREVIOUS_START, label: "Where the last plan started", placeId: origin });
    if (destination && destination !== origin && !known(destination)) out.push({ value: PREVIOUS_END, label: "Where the last plan ended", placeId: destination });
    for (const l of locations.data ?? []) {
      if (l.place_id) out.push({ value: `place:${l.place_id}`, label: l.name, placeId: l.place_id });
    }
    return out;
  }, [kitchen, kitchens, locations.data, prefill]);

  // The initial state is fixed on first render: the options only grow, and a
  // form that re-seeded itself would discard typed input.
  const [initial] = useState(() => initialState(prefill, defaults, options));
  const [form, setForm] = useState<FormState>(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const create = useCreateTrip();
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const dirty = (Object.keys(initial) as (keyof FormState)[]).some((k) => form[k] !== initial[k]);
  const moreOpen = initial.mileage !== "" || initial.overhead !== "";

  const placeIdOf = (value: string): string | null => options.find((o) => o.value === value)?.placeId ?? null;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const plannedAt = fromDateTimeLocal(form.when);
    if (!plannedAt) return setProblem("Choose a date and time for the trip.");
    if (!isNonNegativeDecimal(form.value.trim())) return setProblem("Value of your time must be dollars per hour, 0 or more.");
    const maxStops = Number(form.maxStops);
    if (!Number.isInteger(maxStops) || maxStops < 1 || maxStops > 4) return setProblem("Stops at most must be between 1 and 4.");
    const mileage = form.mileage.trim();
    if (mileage !== "" && !isNonNegativeDecimal(mileage)) return setProblem("Mileage rate must be dollars per mile, 0 or more, or blank.");
    const overhead = form.overhead.trim();
    if (overhead !== "" && !/^\d+$/.test(overhead)) return setProblem("Minutes per stop must be a whole number, or blank.");
    setProblem(null);

    const input: TripCreateInput = { home_base_id: homeBaseId, planned_at: plannedAt, value_of_time_per_hour: form.value.trim(), max_stops: maxStops };
    const origin = form.origin === KITCHEN ? null : placeIdOf(form.origin);
    if (origin) input.origin_place_id = origin;
    if (form.destination !== SAME) {
      const destination = placeIdOf(form.destination);
      if (destination) input.destination_place_id = destination;
    }
    if (mileage !== "") input.mileage_rate = mileage;
    if (overhead !== "") input.stop_overhead_min = Number(overhead);
    create.mutate(input, { onSuccess: onPlanned });
  };

  const emptyList = create.isError && isApiError(create.error) && create.error.code === "empty_list";
  const destinationOptions = options.filter((o) => o.value !== KITCHEN || o.placeId !== null);

  return (
    <Drawer title="Plan a trip" thing="trip" dirty={dirty} onClose={onClose} formId="plan-trip" primaryLabel="Plan" busy={create.isPending} busyLabel="Planning…">
      <form id="plan-trip" aria-label="Plan a trip" className="flex flex-col gap-4" onSubmit={submit} noValidate>
        {problem ? <Alert tone="error">{problem}</Alert> : null}
        {create.isError ? (
          <Alert tone="error">
            {emptyList ? (
              <>
                Nothing on the list to plan for.{" "}
                <Link to="/plan/list" className={`rounded underline ${focusRing}`}>
                  Open the list
                </Link>
              </>
            ) : (
              `Couldn't plan the trip: ${errorMessage(create.error)}`
            )}
          </Alert>
        ) : null}

        <SelectField id="plan-trip-origin" label="Starting from" value={form.origin} onChange={(e) => set("origin", e.target.value)}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </SelectField>
        <SelectField id="plan-trip-destination" label="Ending at" value={form.destination} onChange={(e) => set("destination", e.target.value)}>
          <option value={SAME}>Same as the start</option>
          {destinationOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </SelectField>
        <Field id="plan-trip-when" label="When" type="datetime-local" required value={form.when} onChange={(e) => set("when", e.target.value)} hint="Drive times follow the traffic at that hour." />
        <Field
          id="plan-trip-value"
          label="Value of your time"
          inputMode="decimal"
          value={form.value}
          onChange={(e) => set("value", e.target.value)}
          hint="Dollars per hour. What an hour of driving and shopping is worth to you."
        />
        <SelectField id="plan-trip-max-stops" label="Stops at most" value={form.maxStops} onChange={(e) => set("maxStops", e.target.value)}>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={String(n)}>
              {n}
            </option>
          ))}
        </SelectField>
        <Disclosure summary="More" defaultOpen={moreOpen}>
          <Field id="plan-trip-mileage" label="Mileage rate" inputMode="decimal" value={form.mileage} onChange={(e) => set("mileage", e.target.value)} hint="Dollars per mile. Blank leaves mileage out of the cost." />
          <Field id="plan-trip-overhead" label="Minutes per stop" inputMode="numeric" value={form.overhead} onChange={(e) => set("overhead", e.target.value)} hint="Parking, the aisles and the till. Blank uses each store's own, else 10." />
        </Disclosure>
      </form>
    </Drawer>
  );
}
