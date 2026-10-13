import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey } from "./client";
import { qs } from "./catalog";
import type { ListResponse } from "./types";
import type { AmountOut, ProductBrief, UserBrief } from "./shoppingList";
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
  /** Once "Done here" ran: what its purchase came to ("0" when nothing was bought), beside `subtotal`, the prediction (4C). */
  actual_cost?: string | null;
  lines_recorded?: number | null;
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
  /** Predicted against actual (4C): the chosen variant's item cost and the sum of the purchases "Done here" recorded; null until a stop is done. */
  predicted_item_cost?: string | null;
  actual_item_cost?: string | null;
}

/** One store of a trip summary, in visiting order. */
export interface SummaryStop {
  seq: number;
  vendor_location_id: string;
  name: string;
}

export interface TripSummary extends TripBase {
  variants: VariantSummary[];
  /** The chosen variant's stores in order, or the balanced variant's while nothing is chosen. */
  stops: SummaryStop[];
}

/** The household's planning defaults (`GET /plan/defaults`; 17, P4). */
export interface PlanDefaults {
  value_of_time_per_hour: string;
  max_stops_default: number;
  candidate_radius_km: string;
}

export interface Trip extends TripBase {
  variants: Variant[];
}

// --- reading a plan ------------------------------------------------------------

export function variantOf<V extends VariantSummary>(
  trip: { variants: V[] },
  kind: VariantKind,
): V | undefined {
  return trip.variants.find((v) => v.kind === kind);
}

export function chosenVariant<V extends VariantSummary>(trip: {
  variants: V[];
  chosen_variant_id: string | null;
}): V | undefined {
  return trip.chosen_variant_id
    ? trip.variants.find((v) => v.id === trip.chosen_variant_id)
    : undefined;
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
  const moneyPart =
    money === 0
      ? "the same cost"
      : `${formatMoney(money < 0 ? sub("0", trade.money) : trade.money)} ${money < 0 ? "less" : "more"}`;
  const n = Math.abs(minutes);
  const minutesPart =
    minutes === 0
      ? "the same time"
      : `${n} ${n === 1 ? "minute" : "minutes"} ${minutes < 0 ? "less" : "more"}`;
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
  defaults: ["plan", "defaults"] as const,
  stop: (id: string, seq: number) => ["plan", "trips", "stop", id, seq] as const,
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

/** What a new plan starts from: the household's value of time and the default stop count. */
export function usePlanDefaults() {
  return useQuery({
    queryKey: tripKeys.defaults,
    queryFn: () => api<PlanDefaults>("/plan/defaults"),
    staleTime: 5 * 60_000,
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
    mutationFn: (input: TripCreateInput) =>
      api<Trip>("/plan/trips", { method: "POST", body: input, headers: idem() }),
    onSuccess: store,
  });
}

export function useChooseVariant() {
  const store = useStoreTrip();
  return useMutation({
    mutationFn: ({ id, variantId }: { id: string; variantId: string }) =>
      api<Trip>(`/plan/trips/${enc(id)}/choose`, {
        method: "POST",
        body: { variant_id: variantId },
        headers: idem(),
      }),
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
    mutationFn: ({ id }: { id: string; homeBaseId: string }) =>
      api<void>(`/plan/trips/${enc(id)}`, { method: "DELETE" }),
    onSuccess: (_, { id, homeBaseId }) => {
      client.removeQueries({ queryKey: tripKeys.detail(id) });
      void client.invalidateQueries({ queryKey: tripKeys.list(homeBaseId) });
    },
  });
}

// --- at the store (17, 4C) ------------------------------------------------------

export interface StopLocation {
  id: string;
  name: string;
  vendor_name: string;
  address: string | null;
}

/**
 * One item to get at this stop. `line_cost` is the plan's figure; an aisle price
 * (`actual_price` for `actual_qty actual_unit`) sets `edited`, and
 * `effective_cost` is what the item comes to with it.
 */
export interface StopScreenItem {
  id: string;
  list_item_id: string;
  label: string;
  amounts: AmountOut[];
  note: string | null;
  product: ProductBrief;
  packs: string | null;
  pack_price: string | null;
  line_cost: string;
  observation_id: string;
  planned_observed_at: string;
  price_stale: boolean;
  forced: boolean;
  got_here: boolean;
  actual_price: string | null;
  actual_qty: string | null;
  actual_unit: string | null;
  edited: boolean;
  effective_cost: string;
  claimed_by: UserBrief | null;
  checked_at: string | null;
  checked_by: UserBrief | null;
}

/** A later stop's item this location could also fill; `difference` is here minus there. */
export interface CheaperElsewhereItem {
  id: string;
  list_item_id: string;
  label: string;
  amounts: AmountOut[];
  at_seq: number;
  at_location_name: string;
  product_there: ProductBrief;
  line_cost_there: string;
  product_here: ProductBrief;
  packs_here: string | null;
  pack_price_here: string | null;
  line_cost_here: string;
  price_stale_here: boolean;
  difference: string;
}

/** One stop of a chosen plan (UI-4.8, UI-8.17). */
export interface StopScreen {
  plan_id: string;
  plan_status: PlanStatus;
  list_id: string;
  home_base_id: string;
  home_base_name: string;
  stop_id: string;
  seq: number;
  stop_count: number;
  location: StopLocation;
  arrive_at: string;
  /** The stop's frozen subtotal. */
  predicted_cost: string;
  /** The items re-summed with their edited prices. */
  estimated_here: string;
  done_at: string | null;
  purchase_id: string | null;
  lines_recorded: number | null;
  actual_cost: string | null;
  next_seq: number | null;
  items: StopScreenItem[];
  cheaper_elsewhere: CheaperElsewhereItem[];
}

export interface StopPriceInput {
  price: string;
  qty: string;
  unit: string;
}

export interface StopDone {
  plan_id: string;
  stop_id: string;
  seq: number;
  status: PlanStatus;
  done_at: string;
  purchase_id: string | null;
  lines_recorded: number;
  observation_ids: string[];
  shelf_observation_ids: string[];
  checked: number;
  next_seq: number | null;
}

const stopPath = (id: string, seq: number) => `/plan/trips/${enc(id)}/stops/${seq}`;

/** The first stop of the chosen variant not yet done, or null when every stop is. */
export function firstOpenStop(trip: Trip): Stop | null {
  const chosen = chosenVariant(trip);
  return chosen?.stops.find((s) => s.done_at === null) ?? null;
}

export function useStop(id: string | undefined, seq: number) {
  return useQuery({
    queryKey: tripKeys.stop(id ?? "", seq),
    queryFn: () => api<StopScreen>(stopPath(id ?? "", seq)),
    enabled: Boolean(id) && Number.isInteger(seq) && seq > 0,
  });
}

function useStoreStop() {
  const client = useQueryClient();
  return (stop: StopScreen) =>
    client.setQueryData<StopScreen>(tripKeys.stop(stop.plan_id, stop.seq), stop);
}

/** Hold a price read in the aisle on the stop item; it records nothing until "Done here". */
export function useHoldStopPrice() {
  const store = useStoreStop();
  return useMutation({
    mutationFn: ({
      id,
      seq,
      itemId,
      input,
    }: {
      id: string;
      seq: number;
      itemId: string;
      input: StopPriceInput;
    }) =>
      api<StopScreen>(`${stopPath(id, seq)}/items/${enc(itemId)}/price`, {
        method: "PUT",
        body: input,
      }),
    onSuccess: store,
  });
}

/** Drop an edited price: the item goes back to the plan's. */
export function useClearStopPrice() {
  const store = useStoreStop();
  return useMutation({
    mutationFn: ({ id, seq, itemId }: { id: string; seq: number; itemId: string }) =>
      api<StopScreen>(`${stopPath(id, seq)}/items/${enc(itemId)}/price`, { method: "DELETE" }),
    onSuccess: store,
  });
}

/**
 * Move a later stop's item here. The caller holds `key` and sends the same one
 * again when it retries the same move, so a retry that crossed a success replays.
 */
export function useGetHere() {
  const store = useStoreStop();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      seq,
      itemId,
      key,
    }: {
      id: string;
      seq: number;
      itemId: string;
      key: string;
    }) =>
      api<StopScreen>(`${stopPath(id, seq)}/get-here`, {
        method: "POST",
        body: { item_id: itemId },
        headers: { "Idempotency-Key": key },
      }),
    onSuccess: (stop) => {
      store(stop);
      // The later stop lost the item; its screen and the trip page refetch.
      void client.invalidateQueries({ queryKey: tripKeys.detail(stop.plan_id) });
    },
  });
}

/**
 * "Done here": one committed purchase for the checked items. The caller holds
 * `key` for this set of checks and reuses it on a retry, which then replays.
 */
export function useStopDone() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      seq,
      checkedItemIds,
      key,
    }: {
      id: string;
      seq: number;
      checkedItemIds: string[];
      key: string;
    }) =>
      api<StopDone>(`${stopPath(id, seq)}/done`, {
        method: "POST",
        body: { checked_item_ids: checkedItemIds },
        headers: { "Idempotency-Key": key },
      }),
    onSuccess: (done) => {
      void client.invalidateQueries({ queryKey: tripKeys.detail(done.plan_id) });
      void client.invalidateQueries({ queryKey: tripKeys.all });
      void client.invalidateQueries({ queryKey: ["plan", "list"] });
    },
  });
}
