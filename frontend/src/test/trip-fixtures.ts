// Synthetic trip plans (17, 4B). Stores, products and figures are invented; the
// locations are the synthetic geography in geo-fixtures; ids are fixed
// v7-shaped UUIDs.
import {
  chosenVariant,
  type Annotation,
  type Stop,
  type StopItem,
  type Trip,
  type TripSummary,
  type Variant,
} from "../api/trips";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { kitchenId, openListId } from "./plan-fixtures";

export const tripId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8001";
export const olderTripId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8002";
export const replannedTripId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8003";
export const placeId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8101";

export const fastestId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8201";
export const cheapestId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8202";
export const balancedId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8203";

let n = 0;
const nextId = () => `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9${String(++n).padStart(3, "0")}`;

function item(name: string, over: Partial<StopItem> = {}): StopItem {
  return {
    id: nextId(),
    list_item_id: nextId(),
    product_id: nextId(),
    product_name: name,
    packs: "1",
    pack_price: "4.50",
    line_cost: "4.50",
    observation_id: nextId(),
    price_stale: false,
    forced: false,
    actual_price: null,
    actual_qty: null,
    actual_unit: null,
    got_here: false,
    ...over,
  };
}

function stop(
  seq: number,
  location: typeof chainLocation,
  subtotal: string,
  items: StopItem[],
  over: Partial<Stop> = {},
): Stop {
  return {
    id: nextId(),
    seq,
    vendor_location_id: location.id,
    vendor_location_name: location.name,
    leg_seconds: 600,
    leg_meters: 6400,
    leg_source: "osrm",
    multiplier: "1.20",
    arrive_at: "2026-10-17T16:12:00Z",
    overhead_min: 10,
    subtotal,
    hours_known: true,
    done_at: null,
    purchase_id: null,
    items,
    ...over,
  };
}

const staleNote: Annotation = {
  kind: "stale_dependent",
  amount: "6.40",
  text: "$6.40 of Balanced rests on prices older than 90 days; it's still cheapest without them.",
};
const forcedNote: Annotation = {
  kind: "forced_stop",
  location_id: marketLocation.id,
  text: "Only Pier Farmers Market sells Larkspur rolled oats.",
};
const estimatedNote: Annotation = {
  kind: "estimated_legs",
  text: "Two legs are estimates: the routing engine didn't answer for them.",
};

export const fastest: Variant = {
  id: fastestId,
  kind: "fastest",
  item_cost: "33.10",
  driving_seconds: 1260,
  stop_seconds: 600,
  mileage_cost: "0.00",
  total_cost: "41.80",
  estimated: false,
  trade: { money: "3.20", minutes: "-14" },
  stop_count: 1,
  annotations: [],
  stops: [
    stop(1, chainLocation, "33.10", [
      item("Moonfield oat milk", { packs: "2", pack_price: "4.25", line_cost: "8.50" }),
      item("Cedar row lentils", { line_cost: "24.60", pack_price: "24.60" }),
    ]),
  ],
};

export const cheapest: Variant = {
  id: cheapestId,
  kind: "cheapest",
  item_cost: "24.90",
  driving_seconds: 2700,
  stop_seconds: 1800,
  mileage_cost: "0.00",
  total_cost: "36.25",
  estimated: true,
  trade: { money: "-2.35", minutes: "19" },
  stop_count: 2,
  annotations: [estimatedNote],
  stops: [
    stop(
      1,
      chainLocation,
      "14.40",
      [item("Moonfield oat milk", { packs: "2", pack_price: "4.25", line_cost: "8.50" })],
      { leg_source: "estimate" },
    ),
    stop(
      2,
      marketLocation,
      "10.50",
      [item("Larkspur rolled oats", { line_cost: "10.50", pack_price: "10.50", forced: true })],
      { arrive_at: "2026-10-17T16:40:00Z" },
    ),
  ],
};

export const balanced: Variant = {
  id: balancedId,
  kind: "balanced",
  item_cost: "27.70",
  driving_seconds: 1800,
  stop_seconds: 1200,
  mileage_cost: "0.00",
  total_cost: "38.60",
  estimated: false,
  trade: { money: "0", minutes: "0" },
  stop_count: 2,
  annotations: [staleNote, forcedNote],
  stops: [
    stop(1, chainLocation, "17.20", [
      item("Moonfield oat milk", { packs: "2", pack_price: "4.25", line_cost: "8.50" }),
      item("Cedar row lentils", { line_cost: "8.70", pack_price: "8.70", price_stale: true }),
    ]),
    stop(
      2,
      marketLocation,
      "10.50",
      [item("Larkspur rolled oats", { line_cost: "10.50", pack_price: "10.50", forced: true })],
      { arrive_at: "2026-10-17T16:35:00Z", leg_seconds: 420, leg_meters: 3100 },
    ),
  ],
};

export function trip(over: Partial<Trip> = {}): Trip {
  return {
    id: tripId,
    list_id: openListId,
    home_base_id: kitchenId,
    origin_place_id: placeId,
    destination_place_id: placeId,
    planned_at: "2026-10-17T16:00:00Z",
    value_of_time_per_hour: "20.00",
    max_stops: 3,
    mileage_rate: null,
    stop_overhead_min: 10,
    status: "planned",
    chosen_variant_id: null,
    created_by: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8301",
    created_at: "2026-10-16T09:00:00Z",
    chosen_at: null,
    finished_at: null,
    // Server order is not the page's order: the page shows Fastest, Cheapest, Balanced.
    variants: [balanced, cheapest, fastest],
    ...over,
  };
}

export function chosenTrip(variant: Variant = cheapest): Trip {
  return trip({ chosen_variant_id: variant.id, chosen_at: "2026-10-16T09:30:00Z" });
}

export function summary(t: Trip): TripSummary {
  return {
    ...t,
    variants: t.variants.map((v) => ({
      id: v.id,
      kind: v.kind,
      item_cost: v.item_cost,
      driving_seconds: v.driving_seconds,
      stop_seconds: v.stop_seconds,
      mileage_cost: v.mileage_cost,
      total_cost: v.total_cost,
      estimated: v.estimated,
      trade: v.trade,
      stop_count: v.stop_count,
    })),
    stops: (
      chosenVariant(t) ??
      t.variants.find((v) => v.kind === "balanced") ??
      t.variants[0]
    ).stops.map((s) => ({
      seq: s.seq,
      vendor_location_id: s.vendor_location_id,
      name: s.vendor_location_name,
    })),
  };
}
