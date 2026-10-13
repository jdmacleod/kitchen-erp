import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { qs, type IngredientSummary } from "./catalog";
import { api, isApiError, newIdempotencyKey } from "./client";
import { useMe } from "./queries";
import type { ListResponse } from "./types";

/**
 * The shopping list (docs/spec/17, 4A). Amounts cross the wire as strings and
 * come back as (amount, unit) pairs; an item names an ingredient or a product,
 * never both. Every kitchen-scoped call carries `home_base_id` explicitly (09);
 * the kitchen itself comes from `lib/kitchen.ts`.
 */

export type ListStatus = "open" | "archived";
export type ItemKind = "ingredient" | "product";
export type AddedFrom = "manual" | "ingredient" | "recipe" | "stock";

export interface UserBrief {
  id: string;
  display_name: string;
}

export interface ProductBrief {
  id: string;
  name: string;
  ingredient_id: string;
  pack_qty: string | null;
  pack_unit: string | null;
  pack_count: number | null;
  piece_name: string | null;
  quality_rating: number | null;
  active: boolean;
}

export interface AmountOut {
  seq: number;
  amount: string;
  unit: string;
}

export interface AmountIn {
  amount: string;
  unit: string;
}

export interface ShoppingItem {
  id: string;
  list_id: string;
  kind: ItemKind;
  ingredient: IngredientSummary | null;
  product: ProductBrief | null;
  amounts: AmountOut[];
  min_quality: number | null;
  note: string | null;
  deferrable: boolean;
  added_by: UserBrief;
  added_from: AddedFrom;
  recipe_id: string | null;
  carried_from_item_id: string | null;
  claimed_by: UserBrief | null;
  claimed_at: string | null;
  checked_by: UserBrief | null;
  checked_at: string | null;
  created_at: string;
  /** The optimistic token: a PATCH carries back the value it last saw. */
  updated_at: string;
}

export interface ShoppingList {
  id: string;
  home_base_id: string;
  home_base_name: string;
  status: ListStatus;
  created_at: string;
  archived_at: string | null;
  unchecked: ShoppingItem[];
  checked: ShoppingItem[];
}

export interface ListSummary {
  id: string;
  home_base_id: string;
  status: ListStatus;
  created_at: string;
  archived_at: string | null;
  item_count: number;
  unchecked_count: number;
}

export interface ItemCreateInput {
  home_base_id: string;
  kind: ItemKind;
  ingredient_id?: string;
  product_id?: string;
  amounts?: AmountIn[];
  min_quality?: number | null;
  note?: string | null;
  deferrable?: boolean;
  added_from?: AddedFrom;
  recipe_id?: string | null;
}

export interface ItemPatchInput {
  updated_at: string;
  amounts?: AmountIn[];
  min_quality?: number | null;
  note?: string | null;
  deferrable?: boolean;
}

export interface FromRecipeInput {
  home_base_id: string;
  recipe_id: string;
  scale: string;
  /** Remove the recipe's unchecked items first and add its lines again at this scale. */
  replace?: boolean;
}

export interface SkippedLine {
  raw_name: string;
  reason: "unmatched";
}

export interface FromRecipeOut {
  list: ShoppingList;
  added: number;
  merged: number;
  skipped: SkippedLine[];
  /** The recipe already had unchecked items on the list and nothing was changed. */
  already_on_list: boolean;
  existing_items: number;
}

export interface ArchiveOut {
  archived: ShoppingList;
  /** The new open list. */
  list: ShoppingList;
}

/** The name an item shows: the ingredient's, or the product's. */
export function itemName(item: ShoppingItem): string {
  return item.kind === "product"
    ? (item.product?.name ?? "Product")
    : (item.ingredient?.name ?? "Ingredient");
}

// --- keys ---------------------------------------------------------------------------

export const listKeys = {
  all: ["plan"] as const,
  list: (homeBaseId: string, listId?: string | null) =>
    ["plan", "list", homeBaseId, listId ?? "open"] as const,
  lists: (homeBaseId: string) => ["plan", "lists", homeBaseId] as const,
};

// --- the list ---------------------------------------------------------------------

/**
 * The kitchen's open list, or one of its lists by id. Refetched every 15 seconds
 * while the page is visible, so two phones in one store see each other's checks.
 */
export function useShoppingList(homeBaseId: string | null, listId?: string | null) {
  return useQuery({
    queryKey: listKeys.list(homeBaseId ?? "", listId),
    queryFn: () =>
      api<ShoppingList>(`/plan/list${qs({ home_base_id: homeBaseId, list_id: listId })}`),
    enabled: Boolean(homeBaseId),
    refetchInterval: 15_000,
  });
}

export function useArchivedLists(homeBaseId: string | null, enabled = true) {
  return useQuery({
    queryKey: listKeys.lists(homeBaseId ?? ""),
    queryFn: () =>
      api<ListResponse<ListSummary>>(
        `/plan/lists${qs({ home_base_id: homeBaseId, status: "archived" })}`,
      ),
    select: (data) => data.items,
    enabled: enabled && Boolean(homeBaseId),
  });
}

const idem = () => ({ "Idempotency-Key": newIdempotencyKey() });
const enc = encodeURIComponent;

function useInvalidateLists() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: listKeys.all });
}

export function useAddItem() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: (input: ItemCreateInput) =>
      api<ShoppingItem>("/plan/list/items", { method: "POST", body: input, headers: idem() }),
    onSuccess: () => void invalidate(),
  });
}

export function useAddFromRecipe() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: (input: FromRecipeInput) =>
      api<FromRecipeOut>("/plan/list/items/from-recipe", {
        method: "POST",
        body: input,
        headers: idem(),
      }),
    onSuccess: () => void invalidate(),
  });
}

/** The row the server holds when a PATCH was stale (409 `stale_item`), if it sent one. */
export function staleItem(e: unknown): ShoppingItem | null {
  if (!isApiError(e) || e.code !== "stale_item") return null;
  const item = e.details?.item;
  return typeof item === "object" && item !== null ? (item as ShoppingItem) : null;
}

export function usePatchItem() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ItemPatchInput }) =>
      api<ShoppingItem>(`/plan/list/items/${enc(id)}`, { method: "PATCH", body: input }),
    // Invalidated on a stale answer too, so the row shown is the latest one.
    onSettled: () => void invalidate(),
  });
}

export function useDeleteItem() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/plan/list/items/${enc(id)}`, { method: "DELETE" }),
    onSuccess: () => void invalidate(),
  });
}

export function useClaimItem() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: (id: string) =>
      api<ShoppingItem>(`/plan/list/items/${enc(id)}/claim`, { method: "POST" }),
    onSuccess: () => void invalidate(),
  });
}

export function useUnclaimItem() {
  const invalidate = useInvalidateLists();
  return useMutation({
    mutationFn: (id: string) =>
      api<ShoppingItem>(`/plan/list/items/${enc(id)}/claim`, { method: "DELETE" }),
    onSuccess: () => void invalidate(),
  });
}

interface CheckVars {
  id: string;
  /** The query the row is shown from, for the optimistic move. */
  listKey: ReturnType<typeof listKeys.list>;
}

export interface ToggleCallbacks {
  onSuccess?: (item: ShoppingItem) => void;
  onError?: (e: unknown) => void;
}

/**
 * Check or uncheck, moving the row between the groups at once and putting it
 * back if the server refuses; the list is refetched either way. The callbacks
 * are hook options, not `mutate` options: the row that asked unmounts as it
 * moves, and TanStack Query drops a `mutate` callback with its observer.
 */
function useToggleCheck(checked: boolean, callbacks: ToggleCallbacks = {}) {
  const client = useQueryClient();
  const me = useMe();
  return useMutation({
    mutationFn: ({ id }: CheckVars) =>
      api<ShoppingItem>(`/plan/list/items/${enc(id)}/check`, {
        method: checked ? "POST" : "DELETE",
      }),
    onMutate: async ({ id, listKey }) => {
      await client.cancelQueries({ queryKey: listKey });
      const previous = client.getQueryData<ShoppingList>(listKey);
      if (previous) {
        const from = checked ? previous.unchecked : previous.checked;
        const item = from.find((i) => i.id === id);
        if (item) {
          const by = me.data
            ? { id: me.data.id, display_name: me.data.display_name }
            : item.added_by;
          const moved: ShoppingItem = checked
            ? {
                ...item,
                checked_by: by,
                checked_at: new Date().toISOString(),
                claimed_by: null,
                claimed_at: null,
              }
            : { ...item, checked_by: null, checked_at: null };
          client.setQueryData<ShoppingList>(listKey, {
            ...previous,
            unchecked: checked
              ? previous.unchecked.filter((i) => i.id !== id)
              : [...previous.unchecked, moved],
            checked: checked
              ? [moved, ...previous.checked]
              : previous.checked.filter((i) => i.id !== id),
          });
        }
      }
      return { previous };
    },
    onSuccess: (item) => callbacks.onSuccess?.(item),
    onError: (e, { listKey }, context) => {
      if (context?.previous) client.setQueryData(listKey, context.previous);
      callbacks.onError?.(e);
    },
    onSettled: () => void client.invalidateQueries({ queryKey: listKeys.all }),
  });
}

export function useCheckItem(callbacks?: ToggleCallbacks) {
  return useToggleCheck(true, callbacks);
}

export function useUncheckItem(callbacks?: ToggleCallbacks) {
  return useToggleCheck(false, callbacks);
}

export function useArchiveList() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { home_base_id: string; carry_over: boolean }) =>
      api<ArchiveOut>("/plan/list/archive", { method: "POST", body: input, headers: idem() }),
    onSuccess: (out) => {
      client.setQueryData<ShoppingList>(listKeys.list(out.list.home_base_id), out.list);
      void client.invalidateQueries({ queryKey: listKeys.all });
    },
  });
}
