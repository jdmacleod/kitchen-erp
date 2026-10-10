// Invented recipes for the Cook tests (07, Fixtures): no real dish, vendor or price.

import type { ProductListItem } from "../api/catalog";
import type { BodySection, CostHistory, CostLine, Recipe, RecipeCost, RecipeIngredient, RecipeListItem, RecipesStatus, ResolveName, ResolveProposal, ResolveQueue, ScanOut } from "../api/recipes";

export const cookHealth = { status: "ok", features: ["catalog", "shop", "cook"] };
export const quietInbox = { items: [], reading: { count: 0, oldest_at: null, stalled: false } };

export const mountedStatus: RecipesStatus = {
  mount: "mounted",
  mounted: true,
  head_commit: "0123456789abcdef0123456789abcdef01234567",
  counts: { ok: 2, parse_error: 1, missing: 1 },
  total: 4,
  last_scan_at: "2026-10-09T12:00:00Z",
};

export const noRepositoryStatus: RecipesStatus = {
  mount: "missing",
  mounted: false,
  head_commit: null,
  counts: { ok: 0, parse_error: 0, missing: 0 },
  total: 0,
  last_scan_at: null,
};

const base = {
  servings: "4",
  servings_text: "4",
  status: "ok" as const,
  dirty: false,
  content_hash: "a".repeat(40),
  last_indexed_at: "2026-10-09T11:00:00Z",
};

export const barleyStew: RecipeListItem = {
  ...base,
  id: "r-barley",
  path: "soups/barley_moon_stew.cook",
  title: "Barley moon stew",
  cost: {
    consumed_cost: "12.4000",
    consumed_cost_high: "13.1000",
    basket_cost: "31.9600",
    basket_cost_high: "31.9600",
    per_serving: "3.1000",
    lines_priced: 8,
    lines_total: 10,
    provisional: false,
    computed_at: "2026-10-09T11:05:00Z",
  },
};

export const cometCrumble: RecipeListItem = {
  ...base,
  id: "r-comet",
  path: "desserts/comet_crumble.cook",
  title: "Comet crumble",
  servings: "6",
  servings_text: "6",
  dirty: true,
  cost: {
    consumed_cost: "7.2000",
    consumed_cost_high: "7.2000",
    basket_cost: "15.5000",
    basket_cost_high: "15.5000",
    per_serving: "1.2000",
    lines_priced: 5,
    lines_total: 5,
    provisional: true,
    computed_at: "2026-10-09T11:06:00Z",
  },
};

export const harborFlatbread: RecipeListItem = {
  ...base,
  id: "r-harbor",
  path: "breads/harbor_flatbread.cook",
  title: "Harbor flatbread",
  servings: null,
  servings_text: null,
  status: "parse_error",
  cost: null,
};

export const lanternLentils: RecipeListItem = {
  ...base,
  id: "r-lantern",
  path: "mains/lantern_lentils.cook",
  title: "Lantern lentils",
  status: "missing",
  cost: null,
};

export const recipeList = [barleyStew, cometCrumble, harborFlatbread, lanternLentils];

export function ingredientLine(over: Partial<RecipeIngredient> & { id: string; seq: number; raw_name: string }): RecipeIngredient {
  return {
    section: null,
    name_norm: over.raw_name.toLowerCase(),
    qty_kind: "number",
    qty: "2",
    qty_high: null,
    qty_text: null,
    unit_text: "cups",
    unit: "cup",
    note: null,
    negligible: false,
    resolution: "alias",
    ingredient_id: `i-${over.raw_name.toLowerCase().replace(/\s+/g, "-")}`,
    ingredient_name: over.raw_name,
    ...over,
  };
}

export const stewLines: RecipeIngredient[] = [
  ingredientLine({ id: "l1", seq: 1, raw_name: "pearl barley", unit_text: "cup", qty: "1", note: "rinsed" }),
  ingredientLine({ id: "l2", seq: 2, raw_name: "onion", unit_text: null, unit: null, qty: "1" }),
  ingredientLine({ id: "l3", seq: 3, section: "Broth", raw_name: "mystery root", resolution: "unmatched", ingredient_id: null, ingredient_name: null, qty_kind: "text", qty: null, qty_text: "a handful", unit_text: null, unit: null }),
  ingredientLine({ id: "l4", seq: 4, section: "Broth", raw_name: "dried kelp", qty: "3", unit_text: "sheets", unit: null }),
  ingredientLine({ id: "l5", seq: 5, section: "Broth", raw_name: "ground fennel", qty_kind: "range", qty: "1", qty_high: "2", unit_text: "tsp", unit: "tsp" }),
  ingredientLine({ id: "l6", seq: 6, section: "Broth", raw_name: "salt", qty_kind: "none", qty: null, unit_text: null, unit: null, negligible: true, resolution: "negligible" }),
];

export const stewRecipe: Recipe = {
  ...barleyStew,
  head_commit: mountedStatus.head_commit,
  parse_error_message: null,
  front_matter: { title: "Barley moon stew", servings: "4", tags: ["soup", "winter"], source: "a family card" },
  last_seen_at: "2026-10-09T11:00:00Z",
  notes: null,
  relink: null,
  ingredients: stewLines,
  pins: [],
  body: null,
};

function costLine(line: RecipeIngredient, over: Partial<CostLine>): CostLine {
  return {
    id: `c-${line.id}`,
    line,
    yield_mode: "auto",
    pinned: false,
    status: "priced",
    failure_code: null,
    quantity: { canonical_qty: "200", canonical_qty_high: null, canonical_unit: "g", display_qty: "0.44", display_qty_high: null, display_unit: "lb" },
    yield_applied: "1",
    yield_assumed: false,
    bridge_kind: "density",
    bridge_confirmed: true,
    price: {
      norm_unit_price: "0.0066",
      norm_unit: "g",
      display_unit_price: "2.99",
      display_unit: "lb",
      product_id: "p1",
      product_name: "Moonfield pearl barley 1 lb",
      brand: "Moonfield",
      vendor_id: "v1",
      vendor_name: "Harbor Grocer",
      location_id: "loc1",
      location_name: "Harbor Grocer, Pier Street",
      observation_id: "o1",
      observed_at: "2026-09-20T15:00:00Z",
      stale: false,
    },
    consumed_cost: "1.3200",
    consumed_cost_high: null,
    basket_cost: "2.9900",
    basket_cost_high: null,
    packs: "1",
    packs_high: null,
    ...over,
  };
}

export function stewCost(over: Partial<RecipeCost> = {}): RecipeCost {
  return {
    id: "snap-latest-1",
    recipe_id: stewRecipe.id,
    basis: "latest",
    window_days: null,
    min_quality: null,
    content_hash: stewRecipe.content_hash,
    head_commit: stewRecipe.head_commit,
    provisional: false,
    computed_at: "2026-10-09T11:05:00Z",
    stale_after_days: 90,
    totals: { consumed_cost: "12.4000", consumed_cost_high: "13.1000", basket_cost: "31.9600", basket_cost_high: "31.9600", per_serving: "3.1000" },
    completeness: { lines_total: 6, lines_priced: 3, lines_unpriced: 1, lines_unconvertible: 1, lines_unmapped: 1, lines_negligible: 1 },
    unconfirmed_share: "0.1200",
    lines: [
      costLine(stewLines[0], { yield_assumed: true }),
      costLine(stewLines[1], {
        price: { ...costLine(stewLines[0], {}).price!, display_unit_price: "1.29", display_unit: "each", product_name: "Yellow onion", vendor_name: "Pier Market", observed_at: "2026-05-01T15:00:00Z", stale: true },
        quantity: { canonical_qty: "1", canonical_qty_high: null, canonical_unit: "each", display_qty: "1", display_qty_high: null, display_unit: "each" },
        consumed_cost: "1.2900",
        basket_cost: "1.2900",
      }),
      costLine(stewLines[2], { status: "unmapped", failure_code: "unmapped", quantity: null, price: null, consumed_cost: null, basket_cost: null, packs: null, yield_applied: null, bridge_kind: null, bridge_confirmed: null }),
      costLine(stewLines[3], { status: "unpriced", failure_code: "no_price", price: null, consumed_cost: null, basket_cost: null, packs: null, quantity: { canonical_qty: "30", canonical_qty_high: null, canonical_unit: "g", display_qty: "1.06", display_qty_high: null, display_unit: "oz" } }),
      costLine(stewLines[4], { status: "unconvertible", failure_code: "needs_density", quantity: null, price: null, consumed_cost: null, basket_cost: null, packs: null, bridge_kind: null, bridge_confirmed: null }),
      costLine(stewLines[5], { status: "negligible", quantity: null, price: null, consumed_cost: null, basket_cost: null, packs: null, yield_applied: null, bridge_kind: null, bridge_confirmed: null }),
    ],
    ...over,
  };
}

/** The stew's steps as the index keeps them (package 8). */
export const stewBody: BodySection[] = [
  {
    name: null,
    steps: [
      [
        { t: "text", v: "Rinse the " },
        { t: "ingredient", name: "pearl barley", qty: "1", unit: "cup", note: "rinsed", seq: 1 },
        { t: "text", v: " and soften the " },
        { t: "ingredient", name: "onion", qty: "1", unit: null, note: null, seq: 2 },
        { t: "text", v: " in a " },
        { t: "cookware", name: "stock pot", qty: null },
        { t: "text", v: "." },
      ],
    ],
  },
  {
    name: "Broth",
    steps: [
      [
        { t: "text", v: "Add " },
        { t: "ingredient", name: "mystery root", qty: "a handful", unit: null, note: null, seq: 3 },
        { t: "text", v: ", " },
        { t: "ingredient", name: "dried kelp", qty: "3", unit: "sheets", note: null, seq: 4 },
        { t: "text", v: " and " },
        { t: "ingredient", name: "ground fennel", qty: "1–2", unit: "tsp", note: null, seq: 5 },
        { t: "text", v: "; simmer for " },
        { t: "timer", name: null, qty: "40", unit: "minutes" },
        { t: "text", v: "." },
      ],
      [
        { t: "text", v: "Season with " },
        { t: "ingredient", name: "salt", qty: null, unit: null, note: null, seq: 6 },
        { t: "text", v: "." },
      ],
    ],
  },
];

/** A product of pearl barley, for the pin picker. */
export const barleyProduct: ProductListItem = {
  id: "p-barley-1",
  ingredient: { id: "i-pearl-barley", name: "pearl barley", canonical_unit: "g", active: true, category: "pantry", category_key: "pantry" },
  brand: "Moonfield",
  name: "pearl barley 1 lb",
  pack_qty: "1",
  pack_unit: "lb",
  barcode: null,
  quality_rating: null,
  exclusive_vendor_id: null,
  density_override: null,
  density_override_source: null,
  density_override_confirmed: false,
  active: true,
  notes: null,
  created_at: "2026-03-02T00:00:00Z",
  updated_at: "2026-03-02T00:00:00Z",
  last_paid: null,
};

export function proposal(over: Partial<ResolveProposal> & { tier: ResolveProposal["tier"]; name: string }): ResolveProposal {
  return { ingredient_id: null, standard_key: null, category: null, matched_spelling: null, note: null, fdc_id: null, fdc_description: null, ...over };
}

const ref = (r: RecipeListItem) => ({ id: r.id, title: r.title, path: r.path });

/** Three unmatched names, most-used first (UI-7.16). */
export const mincedGarlic: ResolveName = {
  name_norm: "minced garlic",
  raw_names: ["minced garlic", "Minced Garlic"],
  recipes: [ref(barleyStew), ref(cometCrumble), ref(harborFlatbread)],
  line_count: 4,
  proposals: [
    proposal({ tier: "prep", name: "garlic", ingredient_id: "i-garlic", note: "minced" }),
    proposal({ tier: "standard", name: "garlic", standard_key: "garlic", category: "produce" }),
    proposal({ tier: "model", name: "garlic powder", ingredient_id: "i-garlic-powder" }),
    proposal({ tier: "usda", name: "Garlic, raw", fdc_id: 11215, fdc_description: "Garlic, raw" }),
  ],
};

export const mysteryRoot: ResolveName = {
  name_norm: "mystery root",
  raw_names: ["mystery root"],
  recipes: [ref(barleyStew)],
  line_count: 2,
  proposals: [proposal({ tier: "similar", name: "celery root", ingredient_id: "i-celery-root" })],
};

export const parchment: ResolveName = {
  name_norm: "parchment paper",
  raw_names: ["parchment paper"],
  recipes: [ref(cometCrumble)],
  line_count: 1,
  proposals: [],
};

export const resolveQueue: ResolveQueue = { items: [mincedGarlic, mysteryRoot, parchment], names: 3, recipes: 3, model_configured: true };

export const emptyQueue: ResolveQueue = { items: [], names: 0, recipes: 0, model_configured: false };

export const emptyHistory: CostHistory = { basis: "latest", items: [] };

export function history(basis: CostHistory["basis"], costs: string[]): CostHistory {
  return {
    basis,
    items: costs.map((c, i) => ({
      id: `h-${basis}-${i}`,
      content_hash: "b".repeat(40),
      head_commit: "c".repeat(40),
      computed_at: `2026-0${(i % 8) + 1}-15T10:00:00Z`,
      window_days: basis === "average" ? 90 : null,
      min_quality: null,
      totals: { consumed_cost: c, consumed_cost_high: c, basket_cost: c, basket_cost_high: c, per_serving: null },
      lines_priced: 6,
      lines_total: 6,
    })),
  };
}

export const scanChanged: ScanOut = {
  mounted: true,
  scanned_at: "2026-10-09T12:30:00Z",
  files: 4,
  settling: 0,
  created: 1,
  updated: 1,
  moved: 0,
  missing: 0,
  parse_errors: 0,
  proposals: 0,
};
