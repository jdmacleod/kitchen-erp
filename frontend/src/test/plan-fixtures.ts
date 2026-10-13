// Synthetic shopping-list records (17, 4A). Kitchens, people and items are
// invented; ids are fixed v7-shaped UUIDs.
import type { IngredientSummary } from "../api/catalog";
import type { Settings } from "../api/settings";
import type { ListSummary, ShoppingItem, ShoppingList, UserBrief } from "../api/shoppingList";
import { homeBase, homeBaseId } from "./geo-fixtures";
import { adminUser, memberUser } from "./helpers";

export const kitchen = homeBase;
export const kitchenId = homeBaseId;
export const planHealth = { status: "ok", features: ["catalog", "shop", "cook", "plan"] };
export const settings: Settings = {
  current_home_base_id: kitchenId,
  suggested_home_base_id: kitchenId,
};
export const noKitchenSettings: Settings = {
  current_home_base_id: null,
  suggested_home_base_id: null,
};

export const me: UserBrief = { id: adminUser.id, display_name: adminUser.display_name };
export const other: UserBrief = { id: memberUser.id, display_name: memberUser.display_name };

export const openListId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7001";
export const archivedListId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7002";

function ingredient(
  id: string,
  name: string,
  over: Partial<IngredientSummary> = {},
): IngredientSummary {
  return {
    id,
    name,
    canonical_unit: "g",
    active: true,
    category: "pantry",
    category_key: "pantry",
    ...over,
  };
}

export function item(over: Partial<ShoppingItem> & { id: string }): ShoppingItem {
  return {
    list_id: openListId,
    kind: "ingredient",
    ingredient: ingredient("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7101", "dried chickpeas"),
    product: null,
    amounts: [],
    min_quality: null,
    note: null,
    deferrable: false,
    added_by: me,
    added_from: "manual",
    recipe_id: null,
    carried_from_item_id: null,
    claimed_by: null,
    claimed_at: null,
    checked_by: null,
    checked_at: null,
    created_at: "2026-10-10T09:00:00Z",
    updated_at: "2026-10-10T09:00:00Z",
    ...over,
  };
}

/** Two pairs that do not convert, shown as written (UI-8.5). */
export const chickpeas = item({
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7201",
  amounts: [
    { seq: 1, amount: "2.0", unit: "lb" },
    { seq: 2, amount: "1", unit: "cup" },
  ],
});

/** A product item, claimed by someone else. */
export const oatMilk = item({
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7202",
  kind: "product",
  ingredient: null,
  product: {
    id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7301",
    name: "Moonfield oat milk",
    ingredient_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7102",
    pack_qty: "946",
    pack_unit: "ml",
    pack_count: null,
    piece_name: null,
    quality_rating: null,
    active: true,
  },
  amounts: [{ seq: 1, amount: "2", unit: "each" }],
  claimed_by: other,
  claimed_at: "2026-10-10T10:00:00Z",
  created_at: "2026-10-10T09:05:00Z",
  updated_at: "2026-10-10T10:00:00Z",
});

/** From a recipe, deferrable, with a minimum quality and a note. */
export const bayLeaves = item({
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7203",
  ingredient: ingredient("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7103", "bay leaves", {
    category: "spices",
    category_key: "spices",
  }),
  note: "the fresh kind",
  deferrable: true,
  min_quality: 4,
  added_from: "recipe",
  recipe_id: "r-barley",
  created_at: "2026-10-10T09:10:00Z",
  updated_at: "2026-10-10T09:10:00Z",
});

export const onions = item({
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7204",
  ingredient: ingredient("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7104", "yellow onions", {
    category: "produce",
    category_key: "produce",
  }),
  amounts: [{ seq: 1, amount: "3", unit: "each" }],
  checked_by: other,
  checked_at: "2026-10-11T08:00:00Z",
  created_at: "2026-10-10T09:15:00Z",
  updated_at: "2026-10-11T08:00:00Z",
});

export function list(over: Partial<ShoppingList> = {}): ShoppingList {
  return {
    id: openListId,
    home_base_id: kitchenId,
    home_base_name: kitchen.name,
    status: "open",
    created_at: "2026-10-10T08:00:00Z",
    archived_at: null,
    unchecked: [chickpeas, oatMilk, bayLeaves],
    checked: [onions],
    ...over,
  };
}

export const archivedList: ShoppingList = list({
  id: archivedListId,
  status: "archived",
  created_at: "2026-10-01T08:00:00Z",
  archived_at: "2026-10-08T18:00:00Z",
  unchecked: [
    item({ ...chickpeas, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7211", list_id: archivedListId }),
  ],
  checked: [
    item({ ...onions, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7212", list_id: archivedListId }),
  ],
});

export const archivedSummary: ListSummary = {
  id: archivedListId,
  home_base_id: kitchenId,
  status: "archived",
  created_at: "2026-10-01T08:00:00Z",
  archived_at: "2026-10-08T18:00:00Z",
  item_count: 2,
  unchecked_count: 1,
};
