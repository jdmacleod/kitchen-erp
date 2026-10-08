import { DEFAULT_FEATURES } from "../components/Nav";

/**
 * Actions and pages the search palette can open (docs/spec/09, Search; UI-2.9).
 * They are app routes, not data, so they are matched here rather than by the
 * search endpoint. "Add …" routes carry `?new=1`, which opens the page's create
 * drawer.
 */
export interface PaletteAction {
  kind: "action";
  id: string;
  label: string;
  /** Other words a person might type for it. */
  keywords: string[];
  route: string;
  /** The `/health` feature that builds it, as in the navigation; none means always built. */
  feature?: string;
  adminOnly?: boolean;
}

const action = (id: string, label: string, route: string, keywords: string[], extra: Partial<PaletteAction> = {}): PaletteAction => ({
  kind: "action",
  id,
  label,
  route,
  keywords,
  ...extra,
});

export const PALETTE_ACTIONS: PaletteAction[] = [
  // Things to do.
  action("new-purchase", "New purchase", "/shop/purchases/new", ["enter", "add", "create", "manual", "purchase", "shopping"], { feature: "shop" }),
  action("upload-receipts", "Upload receipts", "/shop/receipts", ["scan", "receipt", "photo", "add"], { feature: "shop" }),
  action("log-shelf-price", "Log a shelf price", "/shop/shelf-prices", ["record", "price", "spotted", "barcode"], { feature: "shop" }),
  action("photograph-product", "Photograph a product", "/catalog/products/photograph", ["photo", "camera", "picture", "label"], { feature: "catalog" }),
  action("add-product", "Add product", "/catalog/products?new=1", ["new", "create", "product"], { feature: "catalog" }),
  action("add-ingredient", "Add ingredient", "/catalog/ingredients?new=1", ["new", "create", "ingredient"], { feature: "catalog" }),
  action("add-vendor", "Add vendor", "/catalog/vendors?new=1", ["new", "create", "store", "shop", "market"], { feature: "catalog" }),
  // Pages to go to.
  action("home", "Home", "/", ["inbox", "needs you", "start"]),
  action("purchases", "Purchases", "/shop/purchases", ["shopping", "history", "drafts"], { feature: "shop" }),
  action("receipts", "Receipts", "/shop/receipts", ["uploads", "batches", "jobs"], { feature: "shop" }),
  action("to-identify", "Products to identify", "/shop/receipts/identify", ["unmatched", "name", "naming", "lines"], { feature: "shop" }),
  action("compare-prices", "Compare prices", "/shop/compare", ["cheapest", "price book", "compare"], { feature: "shop" }),
  action("ingredients", "Ingredients", "/catalog/ingredients", ["catalog", "food"], { feature: "catalog" }),
  action("products", "Products", "/catalog/products", ["catalog", "items"], { feature: "catalog" }),
  action("vendors", "Vendors", "/catalog/vendors", ["stores", "shops", "map"], { feature: "catalog" }),
  action("needs-bridge", "Needs a bridge", "/catalog/bridges", ["pack size", "density", "usda", "bridges"], { feature: "catalog" }),
  action("posted-prices", "Posted prices", "/catalog/products/posted-prices", ["online", "listings", "web prices"], { feature: "catalog" }),
  action("kitchens", "Kitchens", "/settings/kitchens", ["settings", "home bases", "home"]),
  action("users", "Users", "/settings/users", ["settings", "people", "accounts", "password"], { adminOnly: true }),
  action("api-tokens", "API tokens", "/settings/tokens", ["settings", "keys", "helper"]),
  action("capture-settings", "Capture settings", "/settings/capture", ["settings", "bookmarklet", "clip", "save to kitchen erp"]),
  action("system", "System", "/settings/system", ["settings", "health", "status", "backup"]),
];

/** Shown before typing, after any recents. */
export const COMMON_ACTION_IDS = ["new-purchase", "upload-receipts", "log-shelf-price", "add-product"];

const words = (text: string): string[] => text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/** The actions that are built for this person. Features only add to the defaults, as in the navigation. */
export function availableActions(
  features: readonly string[] | undefined,
  admin: boolean,
  actions: PaletteAction[] = PALETTE_ACTIONS,
): PaletteAction[] {
  const built = new Set<string>([...DEFAULT_FEATURES, ...(features ?? [])]);
  return actions.filter((a) => (!a.feature || built.has(a.feature)) && (admin || !a.adminOnly));
}

/**
 * Actions whose label or keywords match every typed word as a word prefix, best
 * first: label matches before keyword-only matches, then the list's own order.
 */
export function matchActions(query: string, actions: PaletteAction[], limit = 5): PaletteAction[] {
  const typed = words(query);
  if (typed.length === 0) return [];
  const hits = (pool: string[]) => typed.every((t) => pool.some((w) => w.startsWith(t)));
  const scored: [PaletteAction, number][] = [];
  for (const a of actions) {
    const label = words(a.label);
    if (hits(label)) scored.push([a, 0]);
    else if (hits([...label, ...a.keywords.flatMap(words)])) scored.push([a, 1]);
  }
  return scored
    .sort((x, y) => x[1] - y[1])
    .slice(0, limit)
    .map(([a]) => a);
}

/** The common actions that are built, in their fixed order. */
export function commonActions(actions: PaletteAction[]): PaletteAction[] {
  return COMMON_ACTION_IDS.map((id) => actions.find((a) => a.id === id)).filter((a): a is PaletteAction => a !== undefined);
}
