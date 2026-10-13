import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import type { HomeBase } from "../../api/geo";
import { chosenVariant, planStatusLabel, planStatusTone, usePlanDefaults, useTrips, variantLabel, variantOf, type Trip, type TripSummary, type VariantSummary } from "../../api/trips";
import { Badge } from "../../components/catalog/fields";
import { useNavigateWithNotice } from "../../components/Notice";
import { KitchenTag } from "../../components/plan/KitchenTag";
import { PlanTripDrawer } from "../../components/plan/PlanTripDrawer";
import { Alert, Button, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDateTime, formatMinutes } from "../../lib/format";
import { useCurrentKitchen } from "../../lib/kitchen";
import { usePageTitle } from "../../lib/usePageTitle";

const DESCRIPTION = "Where to shop for the list, in what order, and what the alternatives cost.";
const muted = "text-neutral-600 dark:text-neutral-400";

/** A variant's figures in a line: its cost, its minutes driving and shopping together, and its stops. */
export function variantFigures(v: Pick<VariantSummary, "total_cost" | "driving_seconds" | "stop_seconds" | "stop_count">): string {
  return `${formatMoney(v.total_cost)} · ${formatMinutes(v.driving_seconds + v.stop_seconds)} · ${v.stop_count} ${v.stop_count === 1 ? "stop" : "stops"}`;
}

/** The notice after planning, carried to the trip page (UI-8.12). */
export function plannedNotice(trip: Pick<Trip, "planned_at">) {
  return { tone: "success" as const, message: `Planned a trip for ${formatDateTime(trip.planned_at)}. Choose one of the three plans.` };
}

/**
 * The kitchen's trips (`/plan/trips`; 10, Trips; UI-8.12, 8.19, 8.20): newest
 * first, each opening its trip page, and "Plan a trip" in a right-side drawer.
 * The palette arrives with `?new=1`, which opens the drawer and is consumed so
 * Back does not reopen it.
 */
export function TripsPage() {
  usePageTitle("Trips");
  const kitchen = useCurrentKitchen();
  const [params, setParams] = useSearchParams();
  const [planning, setPlanning] = useState(false);

  const askedToPlan = params.get("new") === "1";
  if (askedToPlan && !planning) setPlanning(true);
  useEffect(() => {
    if (!askedToPlan) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("new");
        return next;
      },
      { replace: true },
    );
  }, [askedToPlan, setParams]);

  if (!kitchen.ready) {
    return (
      <>
        <PageHeader title="Trips" description={DESCRIPTION} />
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      </>
    );
  }
  if (!kitchen.currentId || !kitchen.current) {
    return (
      <>
        <PageHeader title="Trips" description={DESCRIPTION} />
        <EmptyState
          title="No kitchen yet"
          action={
            <Link to="/settings/kitchens" className={secondaryLinkClass}>
              Settings → Kitchens
            </Link>
          }
        >
          A trip starts from a kitchen and plans for its list. Add one under Settings → Kitchens.
        </EmptyState>
      </>
    );
  }
  return <TripsView key={kitchen.currentId} kitchen={kitchen.current} kitchens={kitchen.kitchens} planning={planning} onPlan={() => setPlanning(true)} onClosePlan={() => setPlanning(false)} />;
}

interface TripsViewProps {
  kitchen: HomeBase;
  kitchens: HomeBase[];
  planning: boolean;
  onPlan: () => void;
  onClosePlan: () => void;
}

function TripsView({ kitchen, kitchens, planning, onPlan, onClosePlan }: TripsViewProps) {
  const trips = useTrips(kitchen.id);
  const navigate = useNavigateWithNotice();
  const defaults = usePlanDefaults();
  const newest = trips.data ? newestFirst(trips.data) : [];

  return (
    <>
      <PageHeader title="Trips" tag={<KitchenTag name={kitchen.name} />} description={DESCRIPTION}>
        <Button onClick={onPlan}>Plan a trip</Button>
      </PageHeader>

      {trips.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : trips.isError ? (
        <Alert tone="error">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span>Couldn&apos;t load the trips: {errorMessage(trips.error)}</span>
            <Button variant="secondary" onClick={() => void trips.refetch()}>
              Try again
            </Button>
          </span>
        </Alert>
      ) : newest.length === 0 ? (
        <EmptyState
          title="No trips yet"
          action={
            <Button variant="secondary" onClick={onPlan}>
              Plan a trip
            </Button>
          }
        >
          Plan one from the {kitchen.name} list.
        </EmptyState>
      ) : (
        <ul aria-label="Trips" className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
          {newest.map((trip) => (
            <TripRow key={trip.id} trip={trip} />
          ))}
        </ul>
      )}

      {/* The drawer waits for the household defaults, which seed the value of time (UI-8.12); if they cannot be read it opens on the documented ones. */}
      {planning && !defaults.isPending ? (
        <PlanTripDrawer
          homeBaseId={kitchen.id}
          kitchen={kitchen}
          kitchens={kitchens}
          defaults={defaults.data ?? null}
          onClose={onClosePlan}
          onPlanned={(trip) => {
            onClosePlan();
            navigate(`/plan/trips/${trip.id}`, plannedNotice(trip));
          }}
        />
      ) : null}
    </>
  );
}

/** The stores in visiting order: the chosen plan's, or Balanced's until one is chosen (10, Trips: List). */
export function storesInOrder(trip: Pick<TripSummary, "stops">): string {
  return [...trip.stops].sort((a, b) => a.seq - b.seq).map((s) => s.name).join(" → ");
}

function newestFirst(trips: TripSummary[]): TripSummary[] {
  return [...trips].sort((a, b) => b.planned_at.localeCompare(a.planned_at) || b.created_at.localeCompare(a.created_at));
}

/** One trip: when, its status, and the chosen plan's figures or "Not chosen yet" (10, Trips: List). */
function TripRow({ trip }: { trip: TripSummary }) {
  const chosen = chosenVariant(trip);
  const balanced = variantOf(trip, "balanced");
  return (
    <li data-testid="trip-row" data-id={trip.id}>
      <Link
        to={`/plan/trips/${trip.id}`}
        className={`flex min-h-11 flex-col gap-1 rounded-lg px-4 py-3 hover:bg-neutral-100 lg:flex-row lg:items-baseline lg:justify-between lg:gap-4 dark:hover:bg-neutral-800 ${focusRing}`}
      >
        <span className="flex flex-wrap items-baseline gap-2">
          <span className="font-medium">{formatDateTime(trip.planned_at)}</span>
          <Badge tone={planStatusTone[trip.status]}>{planStatusLabel[trip.status]}</Badge>
          {trip.stops.length > 0 ? (
            <span className={`w-full text-sm ${muted}`} data-testid="trip-row-stores">
              {storesInOrder(trip)}
            </span>
          ) : null}
        </span>
        <span className="text-sm tabular-nums" data-testid="trip-row-figures">
          {chosen ? (
            `${variantLabel[chosen.kind]} · ${variantFigures(chosen)}${trip.actual_item_cost != null ? ` · predicted ${formatMoney(trip.predicted_item_cost ?? chosen.item_cost)} · actual ${formatMoney(trip.actual_item_cost)}` : ""}`
          ) : (
            <span className={muted}>
              Not chosen yet{balanced ? ` · Balanced ${variantFigures(balanced)}` : ""}
            </span>
          )}
        </span>
      </Link>
    </li>
  );
}
