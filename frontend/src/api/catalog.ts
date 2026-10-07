// Phase 1C wire types and hooks: units, ingredients, measures, products, the
// typeahead, USDA suggestions, and the bridge test bench. Decimals arrive and
// leave as strings; nothing here turns them into numbers.

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import type { PhotoSummary } from "./productPhotos";
import type { CategoryKey } from "../components/CategoryChip";
import { api, isApiError, newIdempotencyKey } from "./client";
import type { ListResponse } from "./types";

// --- types ------------------------------------------------------------------

export type CanonicalUnit = "g" | "ml" | "each";
export type BridgeSource = "usda" | "label" | "measured" | "llm" | "manual";
export type Perishability = "shelf_stable" | "refrigerated" | "fresh";

export const CANONICAL_UNITS: readonly CanonicalUnit[] = ["g", "ml", "each"];
export const BRIDGE_SOURCES: readonly BridgeSource[] = ["manual", "measured", "label", "usda", "llm"];
export const PERISHABILITIES: readonly Perishability[] = ["shelf_stable", "refrigerated", "fresh"];

export const perishabilityLabel: Record<Perishability, string> = {
  shelf_stable: "Shelf stable",
  refrigerated: "Refrigerated",
  fresh: "Fresh",
};

export interface Unit {
  code: string;
  dimension: string;
  to_base_factor: string;
  system: string;
  aliases: string[];
}

export interface Measure {
  id: string;
  ingredient_id: string;
  label: string;
  canonical_qty: string;
  source: BridgeSource;
  confirmed: boolean;
  created_at: string;
  updated_at: string;
}

export interface IngredientSummary {
  id: string;
  name: string;
  canonical_unit: CanonicalUnit;
  active: boolean;
  category: string | null;
  /** The backend's display key for `category` (D12); null when it has none. */
  category_key: CategoryKey | null;
}

export interface Ingredient {
  id: string;
  name: string;
  /** The standard key once linked to the standard list, otherwise "local.<name>" (1G). */
  slug: string;
  category: string | null;
  category_key: CategoryKey | null;
  canonical_unit: CanonicalUnit;
  density_g_per_ml: string | null;
  density_source: BridgeSource | null;
  density_confirmed: boolean;
  yield_pct: string;
  perishability: Perishability;
  active: boolean;
  notes: string | null;
  measures: Measure[];
  created_at: string;
  updated_at: string;
}

/** The summary shape of a full ingredient, as product responses carry it. */
export function ingredientSummary(i: Ingredient): IngredientSummary {
  return { id: i.id, name: i.name, canonical_unit: i.canonical_unit, active: i.active, category: i.category, category_key: i.category_key };
}

export interface Product {
  id: string;
  ingredient: IngredientSummary;
  brand: string | null;
  name: string;
  pack_qty: string | null;
  pack_unit: string | null;
  /** The pieces a weighed or measured pack holds (19 oz, 5 links), and what one is called. */
  pack_count?: number | null;
  piece_name?: string | null;
  barcode: string | null;
  quality_rating: number | null;
  exclusive_vendor_id: string | null;
  density_override: string | null;
  density_override_source: BridgeSource | null;
  density_override_confirmed: boolean;
  active: boolean;
  notes: string | null;
  /** The product this one was merged into (issue 179); its page links there. */
  merged_into?: string | null;
  /** The main photo (1I); null shows the category placeholder. */
  photo?: PhotoSummary | null;
  created_at: string;
  updated_at: string;
}

/** The latest committed purchase of a product (T16): what was paid, where and when. */
export interface LastPaid {
  price: string;
  qty: string;
  unit: string;
  is_promo: boolean;
  vendor_id: string;
  vendor_name: string;
  purchase_id: string;
  paid_at: string;
}

/** A row of the product list, which also says what the product last cost. */
export interface ProductListItem extends Product {
  last_paid: LastPaid | null;
}

export type MatchKind = "barcode" | "name" | "brand" | "ingredient";

export interface SearchHit {
  id: string;
  name: string;
  brand: string | null;
  barcode: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  pack_count?: number | null;
  piece_name?: string | null;
  quality_rating: number | null;
  ingredient: IngredientSummary;
  match: MatchKind;
  score: string;
}

export type BridgeKind = "none" | "density" | "density_override" | "measure" | "pack" | "pack_count";

export interface Provenance {
  bridge_kind: BridgeKind;
  source: string | null;
  confirmed: boolean | null;
  detail: string | null;
  via?: Provenance | null;
  rests_on_unconfirmed: boolean;
}

export type FailureCode = "no_density" | "unknown_measure" | "no_pack" | "no_qty";

export interface ConvertResult {
  ok: boolean;
  qty?: string | null;
  unit?: string | null;
  provenance?: Provenance | null;
  failure_code?: FailureCode | string | null;
  message?: string | null;
  version: string;
}

export interface UsdaSuggestion {
  fdc_id: number;
  description: string;
  similarity: string;
  densities: { density_g_per_ml: string; from_portion: string }[];
  measures: { label: string; canonical_qty_g: string; from_portion: string }[];
}

export interface UsdaSuggestionList {
  items: UsdaSuggestion[];
  loaded: boolean;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

// --- inputs -----------------------------------------------------------------

export interface IngredientCreateInput {
  name: string;
  /** Create from the standard list; the entry supplies name, unit, spellings and USDA link. */
  standard_key?: string;
  category?: string;
  canonical_unit?: CanonicalUnit;
  density_g_per_ml?: string;
  density_source?: BridgeSource;
  yield_pct?: string;
  perishability?: Perishability;
  notes?: string;
}

export interface IngredientUpdateInput {
  name?: string;
  category?: string | null;
  canonical_unit?: CanonicalUnit;
  density_g_per_ml?: string;
  density_source?: BridgeSource;
  clear_density?: boolean;
  yield_pct?: string;
  perishability?: Perishability;
  notes?: string | null;
}

export interface MeasureCreateInput {
  label: string;
  canonical_qty: string;
  source?: BridgeSource;
  confirmed?: boolean;
}

export interface MeasureUpdateInput {
  label?: string;
  canonical_qty?: string;
  source?: BridgeSource;
}

export interface ConvertInput {
  qty: string | null;
  unit: string;
  product_id?: string;
}

export interface ProductCreateInput {
  ingredient_id?: string;
  ingredient?: IngredientCreateInput;
  brand?: string;
  name: string;
  pack_qty?: string;
  pack_unit?: string;
  pack_count?: number;
  piece_name?: string;
  barcode?: string;
  quality_rating?: number;
  exclusive_vendor_id?: string;
  density_override?: string;
  density_override_source?: BridgeSource;
  notes?: string;
}

export interface ProductUpdateInput {
  ingredient_id?: string;
  brand?: string | null;
  name?: string;
  pack_qty?: string;
  pack_unit?: string;
  clear_pack?: boolean;
  pack_count?: number;
  piece_name?: string;
  clear_pieces?: boolean;
  barcode?: string;
  clear_barcode?: boolean;
  quality_rating?: number | null;
  exclusive_vendor_id?: string;
  clear_exclusive_vendor?: boolean;
  density_override?: string;
  density_override_source?: BridgeSource;
  clear_density_override?: boolean;
  notes?: string | null;
}

// --- helpers ----------------------------------------------------------------

type QueryValue = string | number | boolean | null | undefined;

/** Build a query string, dropping empty and false values. */
export function qs(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/**
 * The per-field messages of a 422 validation error, or an empty list.
 * The server puts them in details.errors[].msg.
 */
export function validationMessages(e: unknown): string[] {
  if (!isApiError(e) || !e.details) return [];
  const errors = (e.details as { errors?: unknown }).errors;
  if (!Array.isArray(errors)) return [];
  return errors
    .map((entry) => (typeof entry === "object" && entry !== null ? (entry as { msg?: unknown }).msg : undefined))
    .filter((msg): msg is string => typeof msg === "string");
}

const conflictMessages: Record<string, string> = {
  ingredient_name_taken: "An ingredient with that name already exists.",
  barcode_taken: "Another product already has that barcode.",
  measure_label_taken: "This ingredient already has a measure with that label.",
  merge_self: "Choose another product to keep.",
  merge_target_merged: "That product was merged into another; choose the one it became.",
  merge_target_inactive: "Choose an active product to keep.",
  already_merged: "This product was already merged.",
  product_merged: "This product was merged into another and can't be reactivated.",
};

/** A message for a catalog mutation error, with conflict codes spelled out. */
export function catalogErrorMessage(e: unknown): string {
  if (isApiError(e)) {
    const known = conflictMessages[e.code];
    if (known) return known;
    const details = validationMessages(e);
    if (details.length > 0) return details.join(" ");
    return e.message;
  }
  if (e instanceof TypeError) return "Could not reach the server.";
  if (e instanceof Error && e.message) return e.message;
  return "Something went wrong.";
}

const idem = () => ({ "Idempotency-Key": newIdempotencyKey() });
const enc = encodeURIComponent;

// --- keys -------------------------------------------------------------------

export const catalogKeys = {
  units: ["units"] as const,
  ingredients: ["ingredients"] as const,
  ingredientList: (q: string, includeInactive: boolean) =>
    ["ingredients", "list", { q, includeInactive }] as const,
  ingredientSearch: (q: string, includeStandard: boolean) => ["ingredients", "search", q, includeStandard] as const,
  ingredient: (id: string) => ["ingredients", "detail", id] as const,
  products: ["products"] as const,
  productList: (ingredientId: string | undefined, includeInactive: boolean) =>
    ["products", "list", { ingredientId, includeInactive }] as const,
  productSearch: (q: string) => ["products", "search", q] as const,
  product: (id: string) => ["products", "detail", id] as const,
  usda: (name: string) => ["usda", "suggestions", name] as const,
};

function invalidateIngredient(client: QueryClient, id: string) {
  void client.invalidateQueries({ queryKey: catalogKeys.ingredient(id) });
  void client.invalidateQueries({ queryKey: [...catalogKeys.ingredients, "list"] });
  void client.invalidateQueries({ queryKey: [...catalogKeys.ingredients, "search"] });
}

function invalidateProduct(client: QueryClient, id: string) {
  void client.invalidateQueries({ queryKey: catalogKeys.product(id) });
  void client.invalidateQueries({ queryKey: [...catalogKeys.products, "list"] });
  void client.invalidateQueries({ queryKey: [...catalogKeys.products, "search"] });
}

// --- units ------------------------------------------------------------------

export function useUnits() {
  return useQuery({
    queryKey: catalogKeys.units,
    queryFn: () => api<ListResponse<Unit>>("/units"),
    select: (data) => data.items,
    staleTime: Infinity,
  });
}

// --- ingredients ------------------------------------------------------------

export function useIngredients(q: string, includeInactive: boolean, limit = 50) {
  return useInfiniteQuery({
    queryKey: catalogKeys.ingredientList(q, includeInactive),
    queryFn: ({ pageParam }) =>
      api<Page<Ingredient>>(
        `/ingredients${qs({ q, include_inactive: includeInactive, limit, cursor: pageParam })}`,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** A short ranked list for pickers. Disabled until there is something to search for. */
/** One ingredient search row (1G): a catalog ingredient, or a standard name not yet in it. */
export interface IngredientMatch {
  kind: "ingredient" | "standard";
  /** Set for catalog ingredients. */
  id: string | null;
  /** Set for standard names. */
  key: string | null;
  name: string;
  canonical_unit: CanonicalUnit;
  active: boolean;
  category: string | null;
  category_key: CategoryKey | null;
  /** The other spelling the text matched, e.g. "green onion" for scallion. */
  matched_spelling: string | null;
  /** The text equals the name or a spelling. */
  exact: boolean;
}

/** Ingredients by name or other spelling, best first; standard names on request (1G). */
/** Ingredients a receipt line names outright, for a new product's picker (#88). */
export function useIngredientsInText(text: string) {
  const trimmed = text.trim().slice(0, 200);
  return useQuery({
    queryKey: [...catalogKeys.ingredientSearch(trimmed, true), "in-text"],
    queryFn: () => api<{ items: IngredientMatch[] }>(`/ingredients/in-text${qs({ text: trimmed })}`),
    select: (data) => data.items,
    enabled: trimmed.length > 0,
    staleTime: 10_000,
  });
}

export function useIngredientSearch(q: string, includeStandard = false, limit = 10) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: catalogKeys.ingredientSearch(trimmed, includeStandard),
    queryFn: () =>
      api<{ items: IngredientMatch[] }>(
        `/ingredients/search${qs({ q: trimmed, include_standard: includeStandard ? "true" : "false", limit })}`,
      ),
    select: (data) => data.items,
    enabled: trimmed.length > 0,
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
}

export function useIngredient(id: string | undefined) {
  return useQuery({
    queryKey: catalogKeys.ingredient(id ?? ""),
    queryFn: () => api<Ingredient>(`/ingredients/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export function useCreateIngredient() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: IngredientCreateInput) =>
      api<Ingredient>("/ingredients", { method: "POST", body: input, headers: idem() }),
    onSuccess: (created) => invalidateIngredient(client, created.id),
  });
}

export function useUpdateIngredient(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: IngredientUpdateInput) =>
      api<Ingredient>(`/ingredients/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.ingredient(id), updated);
      invalidateIngredient(client, id);
      // Products show their ingredient's name and category chip.
      void client.invalidateQueries({ queryKey: catalogKeys.products });
    },
  });
}

export function useSetIngredientActive(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (active: boolean) =>
      api<Ingredient>(`/ingredients/${enc(id)}/${active ? "activate" : "deactivate"}`, { method: "POST" }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.ingredient(id), updated);
      invalidateIngredient(client, id);
    },
  });
}

export function useConfirmDensity(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<Ingredient>(`/ingredients/${enc(id)}/density/confirm`, { method: "POST" }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.ingredient(id), updated);
      invalidateIngredient(client, id);
    },
  });
}

/** The bridge test bench. A mutation because it is run on demand, not cached. */
export function useConvert(ingredientId: string) {
  return useMutation({
    mutationFn: (input: ConvertInput) =>
      api<ConvertResult>(`/ingredients/${enc(ingredientId)}/convert`, { method: "POST", body: input }),
  });
}

// --- measures ---------------------------------------------------------------

export function addMeasure(ingredientId: string, input: MeasureCreateInput): Promise<Measure> {
  return api<Measure>(`/ingredients/${enc(ingredientId)}/measures`, { method: "POST", body: input });
}

export function useAddMeasure(ingredientId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: MeasureCreateInput) => addMeasure(ingredientId, input),
    onSuccess: () => invalidateIngredient(client, ingredientId),
  });
}

export function useUpdateMeasure(ingredientId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...input }: MeasureUpdateInput & { id: string }) =>
      api<Measure>(`/measures/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: () => invalidateIngredient(client, ingredientId),
  });
}

export function useConfirmMeasure(ingredientId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<Measure>(`/measures/${enc(id)}/confirm`, { method: "POST" }),
    onSuccess: () => invalidateIngredient(client, ingredientId),
  });
}

export function useDeleteMeasure(ingredientId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/measures/${enc(id)}`, { method: "DELETE" }),
    onSuccess: () => invalidateIngredient(client, ingredientId),
  });
}

// --- USDA suggestions -------------------------------------------------------

/** Suggestions for an ingredient name. Empty and `loaded: false` are both "show nothing". */
export function useUsdaSuggestions(name: string, limit = 5) {
  const trimmed = name.trim();
  return useQuery({
    queryKey: catalogKeys.usda(trimmed),
    queryFn: () => api<UsdaSuggestionList>(`/usda/suggestions${qs({ name: trimmed, limit })}`),
    enabled: trimmed.length >= 2,
    staleTime: 60_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
}

// --- products ---------------------------------------------------------------

/**
 * The product list, by name. `q` and `category` filter on the server, so a match
 * beyond the first page is found (D12); a search returns one ranked page.
 */
export function useProducts(
  options: {
    ingredientId?: string;
    includeInactive?: boolean;
    q?: string;
    /** A category key, or "none" for products whose ingredient has no category. */
    category?: CategoryKey | "none" | null;
    /** Only products with no main photo: the ones still to photograph (issue 184). */
    noPhoto?: boolean;
    limit?: number;
    enabled?: boolean;
  } = {},
) {
  const { ingredientId, includeInactive = false, noPhoto = false, limit = 50, enabled = true } = options;
  const q = options.q?.trim().slice(0, 200) || undefined;
  const category = options.category ?? undefined;
  // qs() drops false, so the flag goes as the string "true" or not at all.
  const noPhotoParam = noPhoto ? "true" : undefined;
  return useInfiniteQuery({
    queryKey: [...catalogKeys.productList(ingredientId, includeInactive), q ?? "", category ?? "", noPhoto],
    queryFn: ({ pageParam }) =>
      api<Page<ProductListItem>>(
        `/products${qs({ ingredient_id: ingredientId, include_inactive: includeInactive, q, category, no_photo: noPhotoParam, limit, cursor: pageParam })}`,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Keep the rows on screen while a new search or filter loads.
    placeholderData: (previous) => previous,
    enabled,
  });
}

/** The typeahead endpoint. Disabled until there is a query. */
export function useProductSearch(q: string, limit = 10) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: catalogKeys.productSearch(trimmed),
    queryFn: () => api<ListResponse<SearchHit>>(`/products/search${qs({ q: trimmed, limit })}`),
    select: (data) => data.items,
    enabled: trimmed.length > 0,
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
}

export function useProduct(id: string | undefined) {
  return useQuery({
    queryKey: catalogKeys.product(id ?? ""),
    queryFn: () => api<Product>(`/products/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export function useCreateProduct() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ProductCreateInput) =>
      api<Product>("/products", { method: "POST", body: input, headers: idem() }),
    onSuccess: (created) => {
      invalidateProduct(client, created.id);
      // An inline ingredient may have been created too.
      invalidateIngredient(client, created.ingredient.id);
    },
  });
}

export function useUpdateProduct(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ProductUpdateInput) =>
      api<Product>(`/products/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.product(id), updated);
      invalidateProduct(client, id);
    },
  });
}

export function useSetProductActive(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (active: boolean) =>
      api<Product>(`/products/${enc(id)}/${active ? "activate" : "deactivate"}`, { method: "POST" }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.product(id), updated);
      invalidateProduct(client, id);
    },
  });
}

/** What merging a product into another does, or would do (issue 179). */
export interface ProductMerge {
  survivor_id: string;
  loser_id: string;
  survivor_name: string;
  loser_name: string;
  prices: number;
  listings: number;
  codes: number;
  photos: number;
  aliases: number;
  lines: number;
  survivor_pack_unit: string | null;
  loser_pack_unit: string | null;
  compare_unit: string;
  other_dimension_prices: number;
  other_dimension_units: string[];
  prices_needing_bridge: number;
}

/** A trial merge the server rolls back; nothing is written. */
export function useProductMergePreview(loserId: string, survivorId: string | null) {
  return useQuery({
    queryKey: [...catalogKeys.products, "merge-preview", loserId, survivorId ?? ""],
    queryFn: () =>
      api<ProductMerge>(`/products/${enc(loserId)}/merge/preview`, { method: "POST", body: { survivor_id: survivorId } }),
    enabled: Boolean(survivorId),
    staleTime: 0,
    gcTime: 0,
  });
}

export function useMergeProduct(loserId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (survivorId: string) =>
      api<ProductMerge>(`/products/${enc(loserId)}/merge`, { method: "POST", body: { survivor_id: survivorId } }),
    // Prices, codes, photos, listings and receipt lines all move: refetch everything.
    onSuccess: () => void client.invalidateQueries(),
  });
}

export function useConfirmDensityOverride(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<Product>(`/products/${enc(id)}/density-override/confirm`, { method: "POST" }),
    onSuccess: (updated) => {
      client.setQueryData(catalogKeys.product(id), updated);
      invalidateProduct(client, id);
    },
  });
}

// --- formatting -------------------------------------------------------------

/** "12 oz", "19 oz · 5 links", or "" when the product has no pack. */
export function formatPack(pack_qty: string | null, pack_unit: string | null, pack_count?: number | null, piece_name?: string | null): string {
  if (pack_qty === null || pack_unit === null) return "";
  const size = `${trimDecimal(pack_qty)} ${unitLabel(pack_unit)}`;
  return pack_count ? `${size} · ${formatPieces(pack_count, piece_name)}` : size;
}

/** A unit code as people write it: "fl_oz" is "fl oz". */
export function unitLabel(code: string): string {
  return code.replace(/_/g, " ");
}

/** "5 links", "1 link", "6 pieces" when the piece has no name. */
export function formatPieces(count: number, name?: string | null): string {
  const piece = name?.trim() || "piece";
  return `${count} ${count === 1 ? piece : plural(piece)}`;
}

/** A short English plural for a piece's name: link → links, box → boxes, patty → patties. */
function plural(word: string): string {
  if (/[^aeiou]y$/i.test(word)) return `${word.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/i.test(word)) return `${word}es`;
  return `${word}s`;
}

/** Drop trailing zeros from a decimal string for display; never for storage. */
export function trimDecimal(value: string): string {
  if (!/^-?\d+\.\d+$/.test(value)) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
}

/** "Brand Name" or just "Name". */
/** Brand and name, once: a name that already starts with its brand isn't given it twice. */
export function productTitle(p: { brand: string | null; name: string }): string {
  const brand = p.brand?.trim();
  if (!brand) return p.name;
  const fold = (s: string) => s.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const name = fold(p.name);
  return name === fold(brand) || name.startsWith(`${fold(brand)} `) ? p.name : `${brand} ${p.name}`;
}

/** True for a positive decimal such as "0.5", "12", or "1.". */
export function isPositiveDecimal(text: string): boolean {
  const t = text.trim();
  if (!/^\d*\.?\d*$/.test(t) || t === "" || t === ".") return false;
  return /[1-9]/.test(t);
}
