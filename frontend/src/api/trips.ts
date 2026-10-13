import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey } from "./client";
import { qs } from "./catalog";
import type { ListResponse } from "./types";
import { cmp, formatMoney, sub } from "../lib/decimal";

/**
 * Trip plans (docs/spec/17, 4B). A plan is the inputs and three variants, derived
 * until one is chosen (P1). Money and minutes cross the wire as strings; seconds
 * and counts as integers. Every kitchen-scoped call carries `home_base_id`.
 */

export type PlanStatus = "planned" | "underway" | "done" | "abandoned";
export type VariantKind = "fastest" | "cheapest" | "balanced";
export type LegSource = "osrm" | "estimate";

export const VARIANT_KINDS: readonly VariantKind[] = ["fastest", "cheapest", "balanced"];

export const variantLabel: Record<VariantKind, string> = {
  fastest: "Fastest",
  cheapest: "Cheapest",
  balanced: "Balanced",
};

export const planStatusLabel: Record<PlanStatus, string> = {
  planned: "Planned",
  underway: "Underway",
  done: "Done",
  abandoned: "Abandoned",
};

/** Olive marks only a Done trip (UI-8.20); every other status is neutral. */
export const planStatusTone: Record<PlanStatus, "neutral" | "good"> = {
  planned: "neutral",
  underway: "neutral",
  done: "good",
  abandoned: "neutral",
};

export interface TripCreateInput {
  home_base_id: string;
  origin_place_id?: string;
  destination_place_id?: string;
  /** UTC ISO-8601. */
  planned_at: string;
  /** Omitted, the household setting applies. */
  value_of_time_per_hour?: string;
  max_stops: number;
  mileage_rate?: string;
  stop_overhead_min?: number;
}

/**
 * A planner note. `amount` is money and `minutes` a duration, both strings;
 * `reason` accompanies `unsourceable`. The server may add fields.
 */
export interface Annotation {
  kind: string;
  item_id?: string | null;
  location_id?: string | null;
  amount?: string | null;
  minutes?: string | null;
  reason?: string | null;
  text: string;
}

export interface StopItem {
  id: string;
  list_item_id: string;
  product_id: string;
  product_name: string;
  packs: string | null;
  pack_price: string | null;
  line_cost: string;
  observation_id: string;
  price_stale: boolean;
  forced: boolean;
  actual_price: string | null;
  actual_qty: string | null;
  actual_unit: string | null;
  got_here: boolean;
}

export interface Stop {
  id: string;
  seq: number;
  vendor_location_id: string;
  vendor_location_name: string;
  /** The free-flow leg that reaches the stop. */
  leg_seconds: number;
  leg_meters: number;
  leg_source: LegSource;
  /** The traffic multiplier applied at the leg's projected departure. */
  multiplier: string;
  arrive_at: string;
  overhead_min: number;
  subtotal: string;
  hours_known: boolean;
  done_at: string | null;
  purchase_id: string | null;
  items: StopItem[];
}

/** The marginal trade against Balanced: positive is more money or more minutes. */
export interface Trade {
  money: string;
  minutes: string;
}

export interface VariantSummary {
  id: string;
  kind: VariantKind;
  item_cost: string;
  driving_seconds: number;
  stop_seconds: number;
  mileage_cost: string;
  total_cost: string;
  /** Some leg is an estimate: the routing engine did not answer for it. */
  estimated: boolean;
  trade: Trade;
  stop_count: number;
}

export interface Variant extends VariantSummary {
  annotations: Annotation[];
  stops: Stop[];
}

interface TripBase {
  id: string;
  list_id: string;
  home_base_id: string;
  origin_place_id: string;
  destination_place_id: string;
  planned_at: string;
  value_of_time_per_hour: string;
  max_stops: number;
  mileage_rate: string | null;
  stop_overhead_min: number;
  status: PlanStatus;
  chosen_variant_id: string | null;
  created_by: string;
  created_at: string;
  chosen_at: string | null;
  finished_at: string | null;
}

export interface TripSummary extends TripBase {
  variants: VariantSummary[];
}

export interface Trip extends TripBase {
  variants: Variant[];
}

// --- reading a plan ------------------------------------------------------------

export function variantOf<V extends VariantSummary>(trip: { variants: V[] }, kind: VariantKind): V | undefined {
  return trip.variants.find((v) => v.kind === kind);
}

export function chosenVariant<V extends VariantSummary>(trip: { variants: V[]; chosen_variant_id: string | null }): V | undefined {
  return trip.chosen_variant_id ? trip.variants.find((v) => v.id === trip.chosen_variant_id) : undefined;
}

/** Whether a plan can still be chosen or abandoned. */
export function planOpen(trip: Pick<TripBase, "status" | "chosen_variant_id">): boolean {
  return trip.status === "planned" || trip.status === "underway";
}

const wholeMinutes = (text: string): number => Math.round(Number.parseFloat(text));

/**
 * The trade against Balanced in both currencies (UI-8.13): "$3.20 more, 14
 * minutes less", "the same as Balanced", or one side "the same" when only the
 * other differs. Balanced itself is the reference and gets no sentence.
 */
export function tradeSentence(trade: Trade): string {
  const money = cmp(trade.money, "0");
  const minutes = wholeMinutes(trade.minutes);
  if (money === 0 && minutes === 0) return "the same as Balanced";
  const moneyPart = money === 0 ? "the same cost" : `${formatMoney(money < 0 ? sub("0", trade.money) : trade.money)} ${money < 0 ? "less" : "more"}`;
  const n = Math.abs(minutes);
  const minutesPart = minutes === 0 ? "the same time" : `${n} ${n === 1 ? "minute" : "minutes"} ${minutes < 0 ? "less" : "more"}`;
  return `${moneyPart}, ${minutesPart}`;
}

/** The sentence's words for an annotation kind; squash for what a person should weigh first. */
export const annotationWord: Record<string, { word: string; tone: "neutral" | "warn" }> = {
  forced_stop: { word: "forced stop", tone: "neutral" },
  defer_suggestion: { word: "could defer", tone: "neutral" },
  stale_dependent: { word: "stale", tone: "warn" },
  unsourceable: { word: "unsourceable", tone: "warn" },
  estimated_legs: { word: "estimated", tone: "warn" },
  hours_unknown: { word: "hours unknown", tone: "neutral" },
  closed_excluded: { word: "closed", tone: "neutral" },
};

export function annotationLabel(kind: string): { word: string; tone: "neutral" | "warn" } {
  return annotationWord[kind] ?? { word: kind.replace(/_/g, " "), tone: "neutral" };
}

// --- keys ----------------------------------------------------------------------

export const tripKeys = {
  all: ["plan", "trips"] as const,
  list: (homeBaseId: string) => ["plan", "trips", "list", homeBaseId] as const,
  detail: (id: string) => ["plan", "trips", "detail", id] as const,
};

const idem = () => ({ "Idempotency-Key": newIdempotencyKey() });
const enc = encodeURIComponent;

// --- queries ---------------------------------------------------------------------

/** The kitchen's plans, newest first, each with its variants' totals. */
export function useTrips(homeBaseId: string | null) {
  return useQuery({
    queryKey: tripKeys.list(homeBaseId ?? ""),
    queryFn: () => api<ListResponse<TripSummary>>(`/plan/trips${qs({ home_base_id: homeBaseId })}`),
    select: (data) => data.items,
    enabled: Boolean(homeBaseId),
  });
}

export function useTrip(id: string | undefined) {
  return useQuery({
    queryKey: tripKeys.detail(id ?? ""),
    queryFn: () => api<Trip>(`/plan/trips/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

// --- mutations -------------------------------------------------------------------

function useStoreTrip() {
  const client = useQueryClient();
  return (trip: Trip) => {
    client.setQueryData<Trip>(tripKeys.detail(trip.id), trip);
    void client.invalidateQueries({ queryKey: tripKeys.list(trip.home_base_id) });
  };
}

export function useCreateTrip() {
  const store = useStoreTrip();
  return useMutation({
    mutationFn: (input: TripCreateInput) => api<Trip>("/plan/trips", { method: "POST", body: input, headers: idem() }),
    onSuccess: store,
  });
}

export function useChooseVariant() {
  const store = useStoreTrip();
  return useMutation({
    mutationFn: ({ id, variantId }: { id: string; variantId: string }) =>
      api<Trip>(`/plan/trips/${enc(id)}/choose`, { method: "POST", body: { variant_id: variantId }, headers: idem() }),
    onSuccess: store,
  });
}

export function useAbandonTrip() {
  const store = useStoreTrip();
  return useMutation({
    mutationFn: (id: string) => api<Trip>(`/plan/trips/${enc(id)}/abandon`, { method: "POST" }),
    onSuccess: store,
  });
}

export function useDeleteTrip() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string; homeBaseId: string }) => api<void>(`/plan/trips/${enc(id)}`, { method: "DELETE" }),
    onSuccess: (_, { id, homeBaseId }) => {
      client.removeQueries({ queryKey: tripKeys.detail(id) });
      void client.invalidateQueries({ queryKey: tripKeys.list(homeBaseId) });
    },
  });
}
