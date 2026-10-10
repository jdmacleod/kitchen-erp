// Phase 3 wire types and hooks: the indexed recipe repository, rescans, relinks,
// and cost snapshots (07, 3A–3D; 10, Cook: recipes). Decimals arrive and leave as
// strings; nothing here turns them into numbers.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cmp, formatMoney, stripZeros } from "../lib/decimal";
import { catalogKeys, qs, type IngredientCreateInput, type IngredientSummary } from "./catalog";
import { api } from "./client";
import { inboxKey } from "./inbox";

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

/**
 * The parsed file as the rendered recipe shows it (package 8): sections of steps
 * of items in the order written. Quantities are text ("2", "1–2", "a handful").
 */
export type BodyItem =
  | { t: "text"; v: string }
  | { t: "ingredient"; name: string; qty: string | null; unit: string | null; note: string | null; seq: number }
  | { t: "cookware"; name: string; qty: string | null }
  | { t: "timer"; name: string | null; qty: string | null; unit: string | null };

export interface BodySection {
  name: string | null;
  steps: BodyItem[][];
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
  /** The last good parse's steps; null for a recipe indexed before migration 0047. */
  body: BodySection[] | null;
}

// --- 3C: the resolve queue, decisions and pins ------------------------------------

/** The cascade's tiers (07, 3C; VS2). A model's guess carries the squash outline (UI-7.16). */
export type ProposalTier = "standard" | "similar" | "prep" | "usda" | "model";

export interface ResolveProposal {
  tier: ProposalTier;
  name: string;
  ingredient_id: string | null;
  standard_key: string | null;
  category: string | null;
  matched_spelling: string | null;
  /** The prep tier's stripped words, sent back with the decision. */
  note: string | null;
  /** The USDA tier's food, sent back with a new ingredient. */
  fdc_id: number | null;
  fdc_description: string | null;
}

export interface ResolveRecipeRef {
  id: string;
  title: string;
  path: string;
}

/** One unmatched name across every recipe that uses it (criterion 15). */
export interface ResolveName {
  name_norm: string;
  raw_names: string[];
  recipes: ResolveRecipeRef[];
  line_count: number;
  proposals: ResolveProposal[];
}

export interface ResolveQueue {
  items: ResolveName[];
  names: number;
  recipes: number;
  model_configured: boolean;
}

/** Exactly one of an ingredient, a new ingredient, or ignore (schemas.ResolveDecisionIn). */
export interface ResolveDecisionInput {
  name_norm: string;
  ingredient_id?: string;
  ingredient?: IngredientCreateInput;
  ignore?: boolean;
  note?: string;
  fdc_id?: number;
}

export interface ResolveDecision {
  name_norm: string;
  action: "matched" | "created" | "ignored";
  ingredient: IngredientSummary | null;
  /** Lines resolved by this decision, across every recipe. */
  lines: number;
  recipes: number;
  /** Names still in the queue. */
  remaining: number;
}

export interface ResolveAsk {
  name_norm: string;
  proposals: ResolveProposal[];
  model_asked: boolean;
}

/** One recipe using an ingredient, with each line's quantity as written (UI-7.14). */
export interface IngredientRecipeUse {
  id: string;
  title: string;
  path: string;
  status: RecipeStatus;
  quantities: (string | null)[];
}

export interface IngredientRecipes {
  items: IngredientRecipeUse[];
  total: number;
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
  resolve: ["recipes", "resolve"] as const,
  usedIn: (ingredientId: string) => ["recipes", "used-in", ingredientId] as const,
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

/** The unmatched names, most-used first, with their recipes and proposals (UI-7.16). */
export function useResolveQueue() {
  return useQuery({
    queryKey: recipeKeys.resolve,
    queryFn: () => api<ResolveQueue>("/recipes/resolve"),
  });
}

/** The recipes whose lines resolve to an ingredient, for the hub's "Used in" card. */
export function useIngredientRecipes(ingredientId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: recipeKeys.usedIn(ingredientId ?? ""),
    queryFn: () => api<IngredientRecipes>(`/ingredients/${ingredientId}/recipes`),
    enabled: ingredientId !== undefined && enabled,
  });
}

// --- mutations ----------------------------------------------------------------

/**
 * One decision for a name: an ingredient, a new one, or not an ingredient. It
 * applies to every recipe using the name and writes one spelling (criterion 15),
 * so every recipe query, the inbox's count and the ingredient caches refresh.
 */
export function useDecideName() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ResolveDecisionInput) => api<ResolveDecision>("/recipes/resolve", { method: "POST", body: input }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: recipeKeys.all });
      void client.invalidateQueries({ queryKey: inboxKey });
      void client.invalidateQueries({ queryKey: catalogKeys.ingredients });
    },
  });
}

/** Ask the cascade, the local model included, about one queued name. Suggestions only. */
export function useAskModel() {
  return useMutation({
    mutationFn: (name_norm: string) => api<ResolveAsk>("/recipes/resolve/ask", { method: "POST", body: { name_norm } }),
  });
}

/** Pin a line's name to a product of its ingredient (UI-7.11). */
export function useSetPin(recipeId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ nameNorm, productId }: { nameNorm: string; productId: string }) =>
      api<Recipe>(`/recipes/${recipeId}/pins/${encodeURIComponent(nameNorm)}`, { method: "PUT", body: { product_id: productId } }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

export function useDeletePin(recipeId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (nameNorm: string) => api<void>(`/recipes/${recipeId}/pins/${encodeURIComponent(nameNorm)}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

/** "Not the same": the proposal goes and that file is never proposed for this recipe again. */
export function useDismissRelink(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<Recipe>(`/recipes/${id}/relink/dismiss`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: recipeKeys.all }),
  });
}

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

/** The badge words for a proposal's tier: "standard list", "without 'minced'", "USDA", "model". */
export function tierLabel(p: Pick<ResolveProposal, "tier" | "note">): string {
  switch (p.tier) {
    case "standard":
      return "standard list";
    case "similar":
      return "similar";
    case "prep":
      return p.note ? `without '${p.note}'` : "without prep words";
    case "usda":
      return "USDA";
    case "model":
      return "model";
  }
}

/** The request a proposal makes when chosen: an existing ingredient, or one to create from it. */
export function decisionFromProposal(nameNorm: string, p: ResolveProposal): ResolveDecisionInput {
  const input: ResolveDecisionInput = { name_norm: nameNorm };
  if (p.note) input.note = p.note;
  if (p.ingredient_id) {
    input.ingredient_id = p.ingredient_id;
    return input;
  }
  const ingredient: IngredientCreateInput = { name: p.name };
  if (p.standard_key) ingredient.standard_key = p.standard_key;
  else if (p.category) ingredient.category = p.category;
  input.ingredient = ingredient;
  if (p.fdc_id) input.fdc_id = p.fdc_id;
  return input;
}

/**
 * "Resolved 'minced garlic' as garlic in 3 recipes." (10, Changing an ingredient);
 * when the lines outnumber the recipes, "…: 4 lines in 3 recipes."
 */
export function resolvedSentence(rawName: string, decision: ResolveDecision): string {
  const recipes = `${decision.recipes} ${decision.recipes === 1 ? "recipe" : "recipes"}`;
  if (decision.action === "ignored") return `Ignored '${rawName}'. It won't be asked about again.`;
  const name = decision.ingredient?.name ?? rawName;
  if (decision.lines > decision.recipes) return `Resolved '${rawName}' as ${name}: ${decision.lines} lines in ${recipes}.`;
  return `Resolved '${rawName}' as ${name} in ${recipes}.`;
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
