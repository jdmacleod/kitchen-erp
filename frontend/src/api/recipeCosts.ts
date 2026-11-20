// Phase 3 cost wire types and hooks: snapshots, lines, history and the text the
// cost views share (07, 3C–3D; 10, Cook: recipes). Decimals arrive and leave as
// strings; nothing here turns them into numbers.

import { useQuery } from "@tanstack/react-query";
import { cmp, formatMoney } from "../lib/decimal";
import { qs } from "./catalog";
import { api } from "./client";
import { recipeKeys, type RecipeIngredient } from "./recipes";
import type { MakesOut, MakesUnitCost, RecipeLink } from "./recipeSources";

/** "offers" (this week's, 18 X2) is a kitchen's view only, never a stored snapshot. */
export type CostBasis = "latest" | "average" | "cheapest" | "offers";
export type CostLineStatus =
  "priced" | "posted" | "unpriced" | "unconvertible" | "unmapped" | "negligible";
export type YieldMode = "auto" | "as_purchased" | "edible";

export const COST_BASES: readonly CostBasis[] = ["latest", "average", "cheapest"];

export const basisLabel: Record<CostBasis, string> = {
  latest: "Latest",
  average: "Average (90 days)",
  cheapest: "Cheapest",
  offers: "This week's offers",
};

/** The latest-basis snapshot of a recipe's current content, for the list. */
export interface RecipeCostSummary {
  consumed_cost: string | null;
  consumed_cost_high: string | null;
  basket_cost: string | null;
  basket_cost_high: string | null;
  per_serving: string | null;
  /** The posted twins: lines on a store's listed price, never in the counted figures (issue 523). */
  posted_consumed_cost?: string | null;
  posted_consumed_cost_high?: string | null;
  posted_basket_cost?: string | null;
  posted_basket_cost_high?: string | null;
  posted_share?: string;
  lines_priced: number;
  lines_total: number;
  /** Lines too small to cost (salt, water, "to taste"), left out of completeness (07, criterion 16). */
  lines_negligible: number;
  /** Lines on a posted price; not in lines_priced. */
  lines_posted?: number;
  /** Lines costed from a recipe that is itself incomplete (issue 526). */
  lines_partial?: number;
  /** Some posted price a line rests on is stale. */
  posted_stale?: boolean;
  provisional: boolean;
  computed_at: string;
}

/** The unit price a line was costed at, per lb, oz, fl oz or each as the server shows it (UI-3.10a). */
export interface CostPriceUsed {
  norm_unit_price: string;
  norm_unit: string;
  display_unit_price: string | null;
  display_unit: string | null;
  product_id: string | null;
  product_name: string | null;
  brand: string | null;
  vendor_id: string | null;
  vendor_name: string | null;
  location_id: string | null;
  location_name: string | null;
  observation_id: string | null;
  observed_at: string | null;
  stale: boolean;
  /** This week's offer priced it (the "offers" basis); an estimate when its size is the product's. */
  flyer_offer_id?: string | null;
  estimate?: boolean;
}

export interface CostQuantity {
  canonical_qty: string;
  canonical_qty_high: string | null;
  canonical_unit: string;
  display_qty: string | null;
  display_qty_high: string | null;
  display_unit: string | null;
}

export interface CostLine {
  id: string;
  line: RecipeIngredient;
  yield_mode: YieldMode;
  pinned: boolean;
  status: CostLineStatus;
  failure_code: string | null;
  quantity: CostQuantity | null;
  yield_applied: string | null;
  /** Grossed up at 100% because the ingredient has no yield yet. */
  yield_assumed: boolean;
  bridge_kind: string | null;
  bridge_confirmed: boolean | null;
  price: CostPriceUsed | null;
  consumed_cost: string | null;
  consumed_cost_high: string | null;
  basket_cost: string | null;
  basket_cost_high: string | null;
  packs: string | null;
  packs_high: string | null;
  /** USDA's value that would convert an unconvertible line, to take with "Use it". */
  usda_offer?: UsdaLineOffer | null;
  /** Why an unpriced line has no price though one is in sight. */
  hint?: CostLineHint | null;
  /** Costed from a recipe (issue 526): which, whether it is incomplete, and under which basis. */
  source_recipe?: RecipeLink | null;
  source_partial?: boolean;
  source_basis?: string | null;
  source_counts?: { priced: number; total: number } | null;
  source?: CostLineSource | null;
}

/** What a line from a recipe needs for its words. */
export interface CostLineSource {
  recipe: RecipeLink | null;
  makes: MakesOut | null;
  /** Where a recipe reference looked; null when it leaves the recipes directory. */
  target_path: string | null;
  /** A recipe with the same file name elsewhere. */
  suggestion: RecipeLink | null;
  counts: { priced: number; total: number } | null;
  cycle: CycleHop[];
}

/** One recipe on a cycle, and how it reaches the next: its file, or an ingredient. */
export interface CycleHop {
  recipe: RecipeLink;
  via_ingredient: { id: string; name: string } | null;
}

export interface CostLineHint {
  kind: "posted_other_product" | "posted_outside_kitchen" | "pin_moved";
  product_name: string | null;
  store_name: string | null;
  ingredient_name: string | null;
}

export interface UsdaLineOffer {
  ingredient_id: string;
  kind: "density" | "measure";
  density_portion_id: string | null;
  measure_label: string | null;
  /** "1 tsp = 2.3 g". */
  text: string;
}

/** The counted totals; lines on a posted price are only in the posted twins. */
export interface CostTotals {
  consumed_cost: string | null;
  consumed_cost_high: string | null;
  basket_cost: string | null;
  basket_cost_high: string | null;
  per_serving: string | null;
  posted_consumed_cost?: string | null;
  posted_consumed_cost_high?: string | null;
  posted_basket_cost?: string | null;
  posted_basket_cost_high?: string | null;
}

export interface CostCompleteness {
  lines_total: number;
  lines_priced: number;
  lines_unpriced: number;
  lines_unconvertible: number;
  lines_unmapped: number;
  lines_negligible: number;
  lines_posted?: number;
  lines_partial?: number;
}

export interface RecipeCost {
  id: string;
  recipe_id: string;
  basis: CostBasis;
  window_days: number | null;
  min_quality: number | null;
  content_hash: string;
  head_commit: string | null;
  provisional: boolean;
  computed_at: string;
  /** The kitchen whose prices alone were counted: the on-demand view (17, 4A); null for a snapshot. */
  home_base_id: string | null;
  stale_after_days: number;
  totals: CostTotals;
  completeness: CostCompleteness;
  /** The share of the consumed cost resting on unconfirmed bridges, 0–1. */
  unconfirmed_share: string;
  posted_share?: string;
  /** Counted cost per display unit of what the recipe makes (issue 526). */
  makes_unit_cost?: MakesUnitCost | null;
  lines: CostLine[];
}

export interface CostHistoryItem {
  id: string;
  content_hash: string;
  head_commit: string | null;
  computed_at: string;
  window_days: number | null;
  min_quality: number | null;
  totals: CostTotals;
  lines_priced: number;
  lines_total: number;
  lines_posted?: number;
}

export interface CostHistory {
  basis: CostBasis;
  items: CostHistoryItem[];
}

/**
 * The recipe's cost under a basis. A basis without a snapshot is computed on the
 * server inside this call, which is why the page says "Costing…" while it is pending.
 */
/**
 * The recipe's cost under a basis. With `homeBaseId`, the kitchen filter (17, 4A;
 * UI-8.8): prices restricted to that kitchen's locations, computed on demand and
 * never snapshotted.
 */
export function useRecipeCost(
  id: string | undefined,
  basis: CostBasis,
  enabled = true,
  homeBaseId = "",
) {
  return useQuery({
    queryKey: recipeKeys.cost(id ?? "", basis, homeBaseId),
    queryFn: () => api<RecipeCost>(`/recipes/${id}/cost${qs({ basis, home_base_id: homeBaseId })}`),
    enabled: id !== undefined && enabled,
  });
}

/** Committed snapshots under a basis, oldest first. */
export function useRecipeCostHistory(id: string | undefined, basis: CostBasis, enabled = true) {
  return useQuery({
    queryKey: recipeKeys.history(id ?? "", basis),
    queryFn: () => api<CostHistory>(`/recipes/${id}/cost/history${qs({ basis })}`),
    enabled: id !== undefined && enabled,
  });
}

/** "8 of 10 lines priced". */
export function pricedSentence(priced: number, total: number): string {
  return `${priced} of ${total} ${total === 1 ? "line" : "lines"} priced`;
}

/**
 * The lines that count toward completeness and how many are priced: negligible
 * lines (salt, water, "to taste") are never costed, so they count for neither
 * (07, criterion 16). `partial` is true while any counted line has no cost, and
 * the totals are then only a floor.
 */
export function known(c: {
  lines_priced: number;
  lines_total: number;
  lines_negligible: number;
  lines_partial?: number;
}): {
  priced: number;
  counted: number;
  partial: boolean;
  /** Priced lines costed from a recipe that is itself incomplete (issue 526). */
  fromPartial: number;
} {
  const counted = Math.max(c.lines_total - c.lines_negligible, 0);
  const fromPartial = c.lines_partial ?? 0;
  return {
    priced: c.lines_priced,
    counted,
    partial: c.lines_priced < counted || fromPartial > 0,
    fromPartial,
  };
}

/** A figure that is only a floor while lines are missing: "at least $20.90". */
export function floorText(figure: string, partial: boolean): string {
  return partial && figure !== "—" ? `at least ${figure}` : figure;
}

/** "$12.40", or "$12.40–13.10" for a range whose high differs; "—" with no figure. */
export function formatCostRange(
  low: string | null | undefined,
  high: string | null | undefined,
): string {
  if (!low) return "—";
  const lowText = formatMoney(low);
  if (!high || cmp(low, high) === 0) return lowText;
  return `${lowText}–${formatMoney(high).replace(/^\$/, "")}`;
}
