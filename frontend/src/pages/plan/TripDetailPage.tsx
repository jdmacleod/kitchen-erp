import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import { useLocations, type VendorLocation } from "../../api/geo";
import {
  VARIANT_KINDS,
  chosenVariant,
  planOpen,
  planStatusLabel,
  planStatusTone,
  tradeSentence,
  useAbandonTrip,
  useChooseVariant,
  useTrip,
  variantLabel,
  variantOf,
  type Trip,
  type Variant,
  type VariantKind,
} from "../../api/trips";
import { Badge, Disclosure } from "../../components/catalog/fields";
import { MoreActionsDialog, type MoreAction } from "../../components/MoreActionsDialog";
import { useNavigateWithNotice, useNotice } from "../../components/Notice";
import { KitchenTag } from "../../components/plan/KitchenTag";
import { PlanTripDrawer, type TripPrefill } from "../../components/plan/PlanTripDrawer";
import { TripAnnotations } from "../../components/plan/TripAnnotations";
import { TripMap } from "../../components/plan/TripMap";
import { TripStops } from "../../components/plan/TripStops";
import { VariantCard } from "../../components/plan/VariantCard";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, PageHeader, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { useCurrentKitchen } from "../../lib/kitchen";
import { LG_QUERY, useMediaQuery } from "../../lib/useMediaQuery";
import { usePageTitle } from "../../lib/usePageTitle";
import { plannedNotice } from "./TripsPage";

const muted = "text-neutral-600 dark:text-neutral-400";

/** The inputs a Replan starts from: the same as this plan's (UI-8.15). */
export function replanPrefill(trip: Trip): TripPrefill {
  return {
    origin_place_id: trip.origin_place_id,
    destination_place_id: trip.destination_place_id,
    planned_at: trip.planned_at,
    value_of_time_per_hour: trip.value_of_time_per_hour,
    max_stops: trip.max_stops,
    mileage_rate: trip.mileage_rate,
    stop_overhead_min: trip.stop_overhead_min,
  };
}

/** The meta line under the title: the trip's shape and the inputs it was planned with. */
export function tripMeta(trip: Trip): string {
  const parts = [
    trip.origin_place_id === trip.destination_place_id ? "There and back" : "Ends somewhere else",
    `time at ${formatMoney(trip.value_of_time_per_hour)} an hour`,
    `up to ${trip.max_stops} ${trip.max_stops === 1 ? "stop" : "stops"}`,
  ];
  if (trip.mileage_rate !== null) parts.push(`mileage at ${formatMoney(trip.mileage_rate, 2, 4)} a mile`);
  return parts.join(" · ");
}

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
      <span>Plan</span> <span aria-hidden="true">/</span>{" "}
      <Link to="/plan/trips" className={`inline-flex min-h-11 items-center rounded underline lg:min-h-0 ${focusRing}`}>
        Trips
      </Link>
    </nav>
  );
}

/**
 * The trip page (`/plan/trips/:id`; 10, Trip page; UI-8.13–8.16, 8.19, 8.20):
 * the three plans side by side, "Choose" on each until one is chosen, the
 * planner's notes, the stops of the plan on show and the map of them.
 */
export function TripDetailPage() {
  const { id } = useParams();
  const trip = useTrip(id);
  usePageTitle(trip.data ? `Trip, ${formatDateTime(trip.data.planned_at)}` : "Trip");

  if (trip.isPending) {
    return (
      <>
        <Breadcrumb />
        <PageHeader title="Trip" />
        <div role="status" aria-label="Planning…" className="grid gap-4 lg:grid-cols-3" data-testid="trip-skeleton">
          {VARIANT_KINDS.map((k) => (
            <div key={k} className="h-56 rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900" />
          ))}
          <span className="sr-only">Planning…</span>
        </div>
      </>
    );
  }
  if (trip.isError) {
    const gone = isApiError(trip.error) && trip.error.status === 404;
    return (
      <>
        <Breadcrumb />
        <PageHeader title="Trip" />
        <Alert tone="error">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span>{gone ? "There is no such trip. It may have been deleted." : `Couldn't load the trip: ${errorMessage(trip.error)}`}</span>
            {gone ? null : (
              <Button variant="secondary" onClick={() => void trip.refetch()}>
                Try again
              </Button>
            )}
          </span>
        </Alert>
      </>
    );
  }
  return <TripView key={trip.data.id} trip={trip.data} refetch={() => void trip.refetch()} />;
}

function TripView({ trip, refetch }: { trip: Trip; refetch: () => void }) {
  const kitchen = useCurrentKitchen();
  const wide = useMediaQuery(LG_QUERY);
  const notice = useNotice();
  const navigate = useNavigateWithNotice();
  const choose = useChooseVariant();
  const locations = useLocations({});
  const [shownKind, setShownKind] = useState<VariantKind>("balanced");
  const [selectedStop, setSelectedStop] = useState<string | null>(null);
  const [replanning, setReplanning] = useState(false);
  const [abandonAsk, setAbandonAsk] = useState(false);
  const [more, setMore] = useState(false);

  const tripKitchen = kitchen.kitchens.find((k) => k.id === trip.home_base_id) ?? null;
  const chosen = chosenVariant(trip);
  const open = planOpen(trip);
  const shown = chosen ?? variantOf(trip, shownKind) ?? trip.variants[0];
  const ordered = VARIANT_KINDS.map((k) => variantOf(trip, k)).filter((v): v is Variant => v !== undefined);
  const showMileage = trip.mileage_rate !== null;

  const byId = useMemo(() => new globalThis.Map((locations.data ?? []).map((l) => [l.id, l])), [locations.data]);
  const locationById = useCallback((lid: string): VendorLocation | undefined => byId.get(lid), [byId]);
  const locationName = useCallback((lid: string) => byId.get(lid)?.name ?? shown?.stops.find((s) => s.vendor_location_id === lid)?.vendor_location_name ?? null, [byId, shown]);

  const onChoose = (variant: Variant) =>
    choose.mutate(
      { id: trip.id, variantId: variant.id },
      {
        onSuccess: () => notice.show({ tone: "success", message: `Chose ${variantLabel[variant.kind]}. The plan is kept as it is now.` }),
        onError: (e) => {
          // A second Choose is refused; the page refreshes into the chosen view (UI-8.15).
          if (isApiError(e) && e.status === 409) {
            refetch();
            notice.show({ tone: "info", message: "A plan was already chosen for this trip; showing it." });
          } else notice.show({ tone: "error", message: `Couldn't choose ${variantLabel[variant.kind]}: ${errorMessage(e)}` });
        },
      },
    );

  const actions: MoreAction[] = open ? [{ label: "Abandon", variant: "danger", open: () => setAbandonAsk(true), disabled: abandonAsk }] : [];

  return (
    <>
      <Breadcrumb />
      <PageHeader
        title={`Trip, ${formatDateTime(trip.planned_at)}`}
        tag={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone={planStatusTone[trip.status]}>{planStatusLabel[trip.status]}</Badge>
            {tripKitchen ? <KitchenTag name={tripKitchen.name} /> : null}
          </span>
        }
        description={tripMeta(trip)}
      >
        <div className="flex flex-wrap gap-2">
          {actions.length > 0 ? (
            wide ? (
              actions.map((a) => (
                <Button key={a.label} variant={a.variant} disabled={a.disabled} onClick={a.open}>
                  {a.label}
                </Button>
              ))
            ) : (
              <Button variant="secondary" aria-haspopup="dialog" onClick={() => setMore(true)}>
                More actions
              </Button>
            )
          ) : null}
          {/* Choose lives on the cards until one is chosen; afterwards Replan is the primary (10, Trip page). */}
          {tripKitchen ? (
            <Button variant={chosen ? "primary" : "secondary"} onClick={() => setReplanning(true)}>
              Replan
            </Button>
          ) : null}
        </div>
      </PageHeader>

      {more ? (
        <MoreActionsDialog
          actions={actions.map((a) => ({
            ...a,
            open: () => {
              setMore(false);
              a.open();
            },
          }))}
          onClose={() => setMore(false)}
        />
      ) : null}
      {abandonAsk ? <AbandonConfirm trip={trip} onClose={() => setAbandonAsk(false)} /> : null}

      {chosen ? (
        <div className="flex flex-col gap-3">
          <div className="lg:max-w-md">
            <VariantCard variant={chosen} showMileage={showMileage} chosenAt={trip.chosen_at} canChoose={false} />
          </div>
          <Disclosure summary="Show the other plans">
            <ul className="flex flex-col gap-2 text-sm" data-testid="other-plans">
              {ordered
                .filter((v) => v.id !== chosen.id)
                .map((v) => (
                  <li key={v.id} data-testid="other-plan" data-kind={v.kind} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="font-medium">{variantLabel[v.kind]}</span>
                    {v.kind === "cheapest" ? <Badge tone="good">cheapest</Badge> : null}
                    {v.estimated ? <Badge tone="warn">estimated</Badge> : null}
                    <span className="tabular-nums">{formatMoney(v.total_cost)}</span>
                    {v.kind === "balanced" ? null : <span className={muted}>{tradeSentence(v.trade)}</span>}
                  </li>
                ))}
            </ul>
          </Disclosure>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3" data-testid="variant-cards">
          {ordered.map((v) => (
            <VariantCard key={v.id} variant={v} showMileage={showMileage} canChoose={open} choosing={choose.isPending && choose.variables?.variantId === v.id} onChoose={onChoose} />
          ))}
        </div>
      )}

      {shown ? (
        <div className="mt-8 grid gap-8 lg:grid-cols-2">
          <div className="flex flex-col gap-8">
            {chosen ? null : (
              <SegmentedControl
                label="Show the stops of"
                value={shown.kind}
                onChange={(k) => {
                  setShownKind(k);
                  setSelectedStop(null);
                }}
                options={ordered.map((v) => ({ value: v.kind, label: variantLabel[v.kind] }))}
              />
            )}
            <TripAnnotations annotations={shown.annotations} locationName={locationName} />
            <TripStops variant={shown} locationById={locationById} selectedId={selectedStop} onSelect={setSelectedStop} wide={wide} />
          </div>
          <div className="lg:sticky lg:top-4 lg:self-start">
            {/* The map waits for the kitchen and the locations, so its first fit takes in every pin. */}
            {kitchen.ready && !locations.isPending ? (
              <TripMap kitchen={tripKitchen} stops={shown.stops} locationById={locationById} selectedId={selectedStop} onSelect={setSelectedStop} />
            ) : (
              <div role="status" aria-label="Loading the map" className="h-80 rounded-lg border border-neutral-200 bg-white lg:h-[28rem] dark:border-neutral-800 dark:bg-neutral-900" />
            )}
          </div>
        </div>
      ) : (
        <p className={`mt-6 text-sm ${muted}`}>This plan has no variants: the list emptied before it was planned.</p>
      )}

      {replanning && tripKitchen ? (
        <PlanTripDrawer
          homeBaseId={trip.home_base_id}
          kitchen={tripKitchen}
          kitchens={kitchen.kitchens}
          prefill={replanPrefill(trip)}
          onClose={() => setReplanning(false)}
          onPlanned={(next) => {
            setReplanning(false);
            navigate(`/plan/trips/${next.id}`, plannedNotice(next));
          }}
        />
      ) : null}
    </>
  );
}

/** "Abandon" confirms inline under the header, never in a dialog, with focus on Cancel (UI-8.15). */
function AbandonConfirm({ trip, onClose }: { trip: Trip; onClose: () => void }) {
  const abandon = useAbandonTrip();
  const notice = useNotice();
  const cancel = useRef<HTMLButtonElement>(null);
  // As on the list's "Start a fresh list": after the More actions sheet hands
  // focus back to its opener, which runs before this mount effect.
  useEffect(() => cancel.current?.focus(), []);
  return (
    <Alert tone="warn" className="mb-4">
      <div className="flex flex-col gap-2" data-testid="abandon-confirm">
        <p>Abandon this trip? It stays in the trips list as Abandoned, and nothing is bought or recorded.</p>
        <div className="flex flex-wrap gap-2">
          <Button ref={cancel} variant="secondary" onClick={onClose} disabled={abandon.isPending}>
            Cancel
          </Button>
          <Button
            variant="dangerFill"
            disabled={abandon.isPending}
            onClick={() =>
              abandon.mutate(trip.id, {
                onSuccess: () => {
                  onClose();
                  notice.show({ tone: "success", message: "Abandoned the trip." });
                },
              })
            }
          >
            {abandon.isPending ? "Abandoning…" : "Abandon trip"}
          </Button>
        </div>
        {abandon.isError ? <span className="text-red-800 dark:text-red-200">Couldn&apos;t abandon the trip: {errorMessage(abandon.error)}</span> : null}
      </div>
    </Alert>
  );
}
