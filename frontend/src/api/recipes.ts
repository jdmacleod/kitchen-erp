// Phase 3 wire types and hooks: the indexed recipe repository, rescans, relinks,
// and cost snapshots (07, 3A–3D; 10, Cook: recipes). Decimals arrive and leave as
// strings; nothing here turns them into numbers.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cmp, formatMoney, stripZeros } from "../lib/decimal";
import { qs } from "./catalog";
import { api } from "./client";

// --- types ------------------------------------------------------------------

export type RecipeStatus = "ok" | "parse_error" | "missing";
export type MountState = "mounted" | "missing" | "empty" | "no_cook_files";
export type QtyKind = "number" | "range" | "text" | "none";
export type Resolution = "alias" | "manual" | "unmatched" | "negligible" | "ignored";
export type Completeness = "complete" | "incomplete";
export type CostBasis = "latest" | "average" | "cheapest";
export type CostLineStatus = "priced" | "unpriced" | "unconvertible" | "unmapped" | "negligible";
export type YieldMode = "auto" | "as_purchased" | "edible";

export const COST_BASES: readonly CostBasis[] = ["latest", "average", "cheapest"];

export const basisLabel: Record<CostBasis, string> = {
  latest: "Latest",
  average: "Average (90 days)",
  cheapest: "Cheapest",
};

export interface RecipeSummary {
  id: string;
  path: string;
  title: string;
  servings: string | null;
  servings_text: string | null;
  status: RecipeStatus;
  dirty: boolean;
  content_hash: string;
  last_indexed_at: string;
}

/** The latest-basis snapshot of a recipe's current content, for the list. */
export interface RecipeCostSummary {
  consumed_cost: string | null;
  consumed_cost_high: string | null;
  basket_cost: string | null;
  basket_cost_high: string | null;
  per_serving: string | null;
  lines_priced: number;
  lines_total: number;
  provisional: boolean;
  computed_at: string;
}

export interface RecipeListItem extends RecipeSummary {
  cost: RecipeCostSummary | null;
}

export interface RecipeList {
  items: RecipeListItem[];
}

export interface RecipesStatus {
  mount: MountState;
  mounted: boolean;
  head_commit: string | null;
  counts: { ok: number; parse_error: number; missing: number };
  total: number;
  last_scan_at: string | null;
}

/** What one scan changed. */
export interface ScanOut {
  mounted: boolean;
  scanned_at: string;
  files: number;
  settling: number;
  created: number;
  updated: number;
  moved: number;
  missing: number;
  parse_errors: number;
  proposals: number;
}

export interface RelinkProposal {
  target_id: string;
  path: string;
  title: string;
  reason: string;
}

/** One ingredient reference as written, in document order. */
export interface RecipeIngredient {
  id: string;
  seq: number;
  section: string | null;
  raw_name: string;
  name_norm: string;
  qty_kind: QtyKind;
  qty: string | null;
  qty_high: string | null;
  qty_text: string | null;
  unit_text: string | null;
  unit: string | null;
  note: string | null;
  negligible: boolean;
  resolution: Resolution;
  ingredient_id: string | null;
  ingredient_name: string | null;
}

export interface RecipePin {
  name_norm: string;
  product_id: string;
  product_name: string;
  brand: string | null;
}

export interface Recipe extends RecipeSummary {
  head_commit: string | null;
  parse_error_message: string | null;
  front_matter: Record<string, unknown> | null;
  last_seen_at: string;
  notes: string | null;
  relink: RelinkProposal | null;
  /** The rows of the last good parse; still there when the file stopped parsing. */
  ingredients: RecipeIngredient[];
  pins: RecipePin[];
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
}

export interface CostTotals {
  consumed_cost: string | null;
  consumed_cost_high: string | null;
  basket_cost: string | null;
  basket_cost_high: string | null;
  per_serving: string | null;
}

export interface CostCompleteness {
  lines_total: number;
  lines_priced: number;
  lines_unpriced: number;
  lines_unconvertible: number;
  lines_unmapped: number;
  lines_negligible: number;
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
  stale_after_days: number;
  totals: CostTotals;
  completeness: CostCompleteness;
  /** The share of the consumed cost resting on unconfirmed bridges, 0–1. */
  unconfirmed_share: string;
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
}

export interface CostHistory {
  basis: CostBasis;
  items: CostHistoryItem[];
}

// --- keys and queries ---------------------------------------------------------

export const recipeKeys = {
  all: ["recipes"] as const,
  list: (filters: RecipeListFilters) => ["recipes", "list", filters] as const,
  status: ["recipes", "status"] as const,
  recipe: (id: string) => ["recipes", "recipe", id] as const,
  cost: (id: string, basis: CostBasis) => ["recipes", "cost", id, basis] as const,
  history: (id: string, basis: CostBasis) => ["recipes", "history", id, basis] as const,
};

export interface RecipeListFilters {
  q?: string;
  status?: RecipeStatus;
  completeness?: Completeness;
}

/** The list, ordered by title, with every filter run on the server (UI-7.3). */
export function useRecipes(filters: RecipeListFilters) {
  return useQuery({
    queryKey: recipeKeys.list(filters),
    queryFn: () => api<RecipeList>(`/recipes${qs({ q: filters.q, status: filters.status, completeness: filters.completeness })}`),
  });
}

export function useRecipesStatus() {
  return useQuery({
    queryKey: recipeKeys.status,
    queryFn: () => api<RecipesStatus>("/recipes/status"),
  });
}

export function useRecipe(id: string | undefined) {
  return useQuery({
    queryKey: recipeKeys.recipe(id ?? ""),
    queryFn: () => api<Recipe>(`/recipes/${id}`),
    enabled: id !== undefined,
  });
}

/**
 * The recipe's cost under a basis. A basis without a snapshot is computed on the
 * server inside this call, which is why the page says "Costing…" while it is pending.
 */
export function useRecipeCost(id: string | undefined, basis: CostBasis, enabled = true) {
  return useQuery({
    queryKey: recipeKeys.cost(id ?? "", basis),
    queryFn: () => api<RecipeCost>(`/recipes/${id}/cost${qs({ basis })}`),
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

// --- mutations ----------------------------------------------------------------

/** Run the scan inline; every recipe query is refreshed when it answers (UI-7.6). */
export function useRescanRecipes() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<ScanOut>("/recipes/rescan", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

/** Confirm that a missing recipe became the proposed file (3A, step 3). */
export function useRelinkRecipe(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (targetId: string) => api<Recipe>(`/recipes/${id}/relink`, { method: "POST", body: { target_id: targetId } }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

/** Remove a missing recipe with its costs and pins. */
export function useDeleteRecipe(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>(`/recipes/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

// --- words --------------------------------------------------------------------

/** "Rescanned: 2 recipes changed." or "Rescanned. Nothing changed." (10, Recipes list). */
export function rescanSentence(scan: ScanOut): string {
  const changed = scan.created + scan.updated + scan.moved + scan.missing;
  if (changed === 0) return "Rescanned. Nothing changed.";
  return `Rescanned: ${changed} ${changed === 1 ? "recipe" : "recipes"} changed.`;
}

/** "8 of 10 lines priced". */
export function pricedSentence(priced: number, total: number): string {
  return `${priced} of ${total} ${total === 1 ? "line" : "lines"} priced`;
}

/** Servings as written, or the number, or nothing. */
export function servingsText(recipe: Pick<RecipeSummary, "servings" | "servings_text">): string | null {
  return recipe.servings_text ?? recipe.servings;
}

/** "$12.40", or "$12.40–13.10" for a range whose high differs; "—" with no figure. */
export function formatCostRange(low: string | null | undefined, high: string | null | undefined): string {
  if (!low) return "—";
  const lowText = formatMoney(low);
  if (!high || cmp(low, high) === 0) return lowText;
  return `${lowText}–${formatMoney(high).replace(/^\$/, "")}`;
}

/** "1.5 lb", or "1–2 lb" for a range; "—" when the server sent no display quantity. */
export function formatQuantity(qty: string | null | undefined, high: string | null | undefined, unit: string | null | undefined): string {
  if (!qty || !unit) return "—";
  const low = stripZeros(qty);
  const top = high && cmp(qty, high) !== 0 ? `–${stripZeros(high)}` : "";
  return `${low}${top} ${unit}`;
}

/** The ingredient line as written: "2 cups pearl barley (rinsed)". */
export function lineText(line: RecipeIngredient): string {
  let qty = "";
  if (line.qty_kind === "text") qty = line.qty_text ?? "";
  else if (line.qty_kind === "range" && line.qty) qty = `${stripZeros(line.qty)}–${line.qty_high ? stripZeros(line.qty_high) : ""}`;
  else if (line.qty_kind === "number" && line.qty) qty = stripZeros(line.qty);
  const parts = [qty, line.unit_text ?? "", line.raw_name].filter((p) => p !== "");
  const text = parts.join(" ");
  return line.note ? `${text} (${line.note})` : text;
}
