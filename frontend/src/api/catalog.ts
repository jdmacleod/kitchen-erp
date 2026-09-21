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
}

export interface Ingredient {
  id: string;
  name: string;
  category: string | null;
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

export interface Product {
  id: string;
  ingredient: IngredientSummary;
  brand: string | null;
  name: string;
  pack_qty: string | null;
  pack_unit: string | null;
  barcode: string | null;
  quality_rating: number | null;
  exclusive_vendor_id: string | null;
  density_override: string | null;
  density_override_source: BridgeSource | null;
  density_override_confirmed: boolean;
  active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export type MatchKind = "barcode" | "name" | "brand" | "ingredient";

export interface SearchHit {
  id: string;
  name: string;
  brand: string | null;
  barcode: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  quality_rating: number | null;
  ingredient: IngredientSummary;
  match: MatchKind;
  score: string;
}

export type BridgeKind = "none" | "density" | "density_override" | "measure" | "pack";

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
  ingredientSearch: (q: string) => ["ingredients", "search", q] as const,
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
export function useIngredientSearch(q: string, limit = 10) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: catalogKeys.ingredientSearch(trimmed),
    queryFn: () => api<Page<Ingredient>>(`/ingredients${qs({ q: trimmed, limit })}`),
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

export function useProducts(
  options: { ingredientId?: string; includeInactive?: boolean; limit?: number; enabled?: boolean } = {},
) {
  const { ingredientId, includeInactive = false, limit = 50, enabled = true } = options;
  return useInfiniteQuery({
    queryKey: catalogKeys.productList(ingredientId, includeInactive),
    queryFn: ({ pageParam }) =>
      api<Page<Product>>(
        `/products${qs({ ingredient_id: ingredientId, include_inactive: includeInactive, limit, cursor: pageParam })}`,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
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

/** "12 oz" or "" when the product has no pack. */
export function formatPack(pack_qty: string | null, pack_unit: string | null): string {
  if (pack_qty === null || pack_unit === null) return "";
  return `${trimDecimal(pack_qty)} ${pack_unit}`;
}

/** Drop trailing zeros from a decimal string for display; never for storage. */
export function trimDecimal(value: string): string {
  if (!/^-?\d+\.\d+$/.test(value)) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
}

/** "Brand Name" or just "Name". */
export function productTitle(p: { brand: string | null; name: string }): string {
  return p.brand ? `${p.brand} ${p.name}` : p.name;
}

/** True for a positive decimal such as "0.5", "12", or "1.". */
export function isPositiveDecimal(text: string): boolean {
  const t = text.trim();
  if (!/^\d*\.?\d*$/.test(t) || t === "" || t === ".") return false;
  return /[1-9]/.test(t);
}
