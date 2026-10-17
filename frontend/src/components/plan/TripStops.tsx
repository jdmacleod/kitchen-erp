import { Link } from "react-router";
import type { VendorLocation } from "../../api/geo";
import { variantLabel, type Stop, type Variant } from "../../api/trips";
import { formatMoney, mul } from "../../lib/decimal";
import { formatDateTime, formatKm, formatMinutes, formatTime } from "../../lib/format";
import { navigateHref } from "../../lib/navigateLink";
import { Badge, Disclosure } from "../catalog/fields";
import { Button, focusRing, secondaryLinkClass, sectionHeading, tapTarget } from "../ui";

interface TripStopsProps {
  variant: Variant;
  locationById: (id: string) => VendorLocation | undefined;
  selectedId: string | null;
  onSelect: (stopId: string | null) => void;
  /** Below lg each stop folds behind its heading (UI-8.19). */
  wide: boolean;
  /**
   * The trip, once this variant is the chosen one: each stop then links to its
   * at-the-store screen, "Open this stop" (UI-8.15).
   */
  openStopsOf?: string | null;
}

const muted = "text-neutral-600 dark:text-neutral-400";
/** A text link in a stop's rows: a 44px target below lg, plain inline text at lg. */
const stopLink = `${tapTarget} rounded-sm underline ${focusRing}`;

/** The leg's minutes with the traffic multiplier the planner applied, for display. */
export function legMinutes(stop: Pick<Stop, "leg_seconds" | "multiplier">): string {
  return formatMinutes(Number(mul(String(stop.leg_seconds), stop.multiplier)));
}

/**
 * The stops of the variant on show, in order (10, Trip page: Stops; UI-8.14):
 * the leg before each, its overhead, the items with packs and line cost, the
 * subtotal, and "Navigate" to the phone's maps application in a new tab.
 */
export function TripStops({
  variant,
  locationById,
  selectedId,
  onSelect,
  wide,
  openStopsOf = null,
}: TripStopsProps) {
  return (
    <section aria-labelledby="trip-stops-heading" className="flex flex-col gap-3">
      <h2 id="trip-stops-heading" className={sectionHeading}>
        Stops · {variantLabel[variant.kind]}
      </h2>
      {variant.stops.length === 0 ? (
        <p className={`text-sm ${muted}`}>No stops: nothing on the list could be sourced.</p>
      ) : (
        variant.stops.map((stop) => {
          const location = locationById(stop.vendor_location_id);
          const title = `Stop ${stop.seq} · ${stop.vendor_location_name}`;
          const body = (
            <StopBody
              stop={stop}
              location={location}
              selected={selectedId === stop.id}
              onSelect={onSelect}
              tripId={openStopsOf}
            />
          );
          return wide ? (
            <section
              key={stop.id}
              aria-label={title}
              data-testid="stop"
              data-seq={stop.seq}
              data-selected={selectedId === stop.id ? "true" : "false"}
              className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
            >
              <h3 className="text-base font-medium">{title}</h3>
              {body}
            </section>
          ) : (
            <div
              key={stop.id}
              data-testid="stop"
              data-seq={stop.seq}
              data-selected={selectedId === stop.id ? "true" : "false"}
              className="rounded-lg border border-neutral-200 bg-white px-4 py-1 dark:border-neutral-800 dark:bg-neutral-900"
            >
              <Disclosure
                summary={`${title} · ${formatMoney(stop.subtotal)}`}
                defaultOpen={stop.seq === 1}
              >
                {body}
              </Disclosure>
            </div>
          );
        })
      )}
    </section>
  );
}

function StopBody({
  stop,
  location,
  selected,
  onSelect,
  tripId,
}: {
  stop: Stop;
  location: VendorLocation | undefined;
  selected: boolean;
  onSelect: (id: string | null) => void;
  tripId: string | null;
}) {
  return (
    <div className="mt-2 flex flex-col gap-3 text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="stop-leg">
        <span className="tabular-nums">
          {legMinutes(stop)} · {formatKm(stop.leg_meters)} · arrive {formatTime(stop.arrive_at)}
        </span>
        {stop.leg_source === "estimate" ? <Badge tone="warn">estimated</Badge> : null}
        {stop.hours_known ? null : <Badge tone="neutral">hours unknown</Badge>}
      </p>
      <p className={muted}>{stop.overhead_min} min in the store</p>

      <ul
        className="flex flex-col divide-y divide-neutral-200 dark:divide-neutral-800"
        aria-label={`Items at stop ${stop.seq}`}
      >
        {stop.items.map((item) => (
          <li
            key={item.id}
            data-testid="stop-item"
            className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 py-1.5"
          >
            <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
              <Link to={`/catalog/products/${item.product_id}`} className={stopLink}>
                {item.product_name}
              </Link>
              <span className={`tabular-nums ${muted}`}>
                {item.packs !== null && item.pack_price !== null
                  ? `${item.packs} × ${formatMoney(item.pack_price)}`
                  : "by amount"}
              </span>
              {item.price_stale ? <Badge tone="warn">stale</Badge> : null}
              {item.forced ? <Badge tone="neutral">only here</Badge> : null}
            </span>
            <span className="tabular-nums">{formatMoney(item.line_cost)}</span>
          </li>
        ))}
      </ul>
      <p className="flex items-baseline justify-between gap-3 font-medium">
        <span>Subtotal</span>
        <span className="tabular-nums" data-testid="stop-subtotal">
          {formatMoney(stop.subtotal)}
        </span>
      </p>

      {stop.done_at ? (
        <p className={muted} data-testid="stop-actual">
          Done {formatDateTime(stop.done_at)} · predicted{" "}
          <span className="tabular-nums">{formatMoney(stop.subtotal)}</span>
          {stop.actual_cost !== null && stop.actual_cost !== undefined ? (
            <>
              {" · "}actual <span className="tabular-nums">{formatMoney(stop.actual_cost)}</span>
            </>
          ) : null}
          {stop.purchase_id ? (
            <>
              {" · "}
              <Link to={`/shop/purchases/${stop.purchase_id}`} className={stopLink}>
                Open the purchase
              </Link>
            </>
          ) : null}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {tripId ? (
          <Link to={`/plan/trips/${tripId}/stop/${stop.seq}`} className={secondaryLinkClass}>
            Open this stop
          </Link>
        ) : null}
        {location ? (
          <a
            href={navigateHref(location.lat, location.lon, location.name)}
            target="_blank"
            rel="noopener noreferrer"
            className={secondaryLinkClass}
          >
            Navigate
          </a>
        ) : null}
        <Button
          variant={selected ? "secondary" : "ghost"}
          aria-pressed={selected}
          onClick={() => onSelect(selected ? null : stop.id)}
        >
          {selected ? "Shown on the map" : "Show on the map"}
        </Button>
      </div>
    </div>
  );
}
