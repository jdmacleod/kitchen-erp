# 09 — Information architecture

This document defines the app's navigation, routes, global context and cross-cutting surfaces. It replaces the table-oriented sidebar (Catalog, Purchases, Price book, Settings). Decision IDs in parentheses refer to the 2026-09-25 review record (see 08).

## Principle

Navigation follows the household's weekly loop, not the database schema:

```
Cook  →  Plan  →  Shop  →  Stock
  ↑                          │
  └──── stock gaps and recipes feed the next plan
```

Reference data (ingredients, products, vendors) is the foundation under the loop. It is reached from the workflows, from search, and from a quiet Catalog entry, rather than occupying the main nav.

## Sections

| Section | Phase | Sub-pages | Notes |
|---|---|---|---|
| Home | now | — | Unified inbox plus summaries; the first-run checklist until the first committed purchase (T11). |
| Cook | 3 | Recipes, Costing | Dormant until Phase 3 is approved. |
| Plan | 4 | Shopping list, Trip planner, Compare prices | Dormant until Phase 4 is approved. |
| Shop | 2 (exists) | Purchases, Receipts, Shelf prices, Compare prices | Compare lives here until Phase 4 moves it to Plan (D7). |
| Stock | 5 | Pantry, Par levels | Dormant until Phase 5 is approved. |
| Catalog | 1 (exists) | Ingredients, Products, Vendors | Footer entry. The ingredient detail page is the hub. Vendors has a list/map toggle (T9). |
| Settings | exists | Kitchens (home bases), Users, API tokens, System | System holds health detail and "log out everywhere" (T10). |

**Phase gating.** The sidebar shows only sections whose phase is built. Today those are Home, Shop and Catalog, plus Settings.
- **Source:** the authenticated `GET /api/v1/health` response carries a `features` list derived from the migration head (S4). It carries that list even when the response status is 503.
- **While loading (D11):** the navigation renders the Phase 1–2 sections at once, and also while `/health` is pending or failing. `features` can only add sections, never remove them. A section added later takes its fixed place in the order above.
- **No placeholders:** empty sections are never shown.

**Build line.** The build line and status dot stay in the sidebar footer (T10; issue #12).

## Routes

Keep the old paths as client-side redirects for at least one release, preserving route parameters.

| New route | Replaces |
|---|---|
| `/` | `/` |
| `/shop/purchases`, `/shop/purchases/new`, `/shop/purchases/:id` | `/purchases`, `/purchases/new`, `/purchases/:id` |
| `/shop/receipts` | `/receipts` |
| `/shop/receipts/identify` | `/to-identify` (reached from the inbox; no nav item, T6) |
| `/shop/shelf-prices` | `/prices/new` |
| `/shop/compare` | `/compare` (D7; moves to `/plan/compare` in Phase 4) |
| `/catalog/ingredients`, `/catalog/ingredients/:id` | `/ingredients`, `/ingredients/:id` |
| `/catalog/products`, `/catalog/products/:id` | `/products`, `/products/:id` |
| `/catalog/vendors`, `/catalog/vendors/:id` | `/vendors`, `/vendors/:id` |
| `/catalog/vendors?view=map` | `/map` (T9) |
| `/catalog/bridges` | `/price-book/needs-bridge` (reached from the inbox; no nav item) |
| `/settings/kitchens`, `/settings/users`, `/settings/tokens`, `/settings/system` | `/settings/home-bases`, `/settings/users`, `/settings/tokens` |

Dormant routes, built with their phase: `/cook/recipes`, `/cook/recipes/:id`, `/cook/costing` (Phase 3); `/plan/list`, `/plan/trips`, `/plan/trips/:id`, `/plan/compare` (Phase 4); `/stock/pantry`, `/stock/par` (Phase 5).

The vendor map is the Vendors page's map view (T9). It keeps every current map function, including dropping a pin to create a vendor and its location in one act. That is the only way to create a location, and it is the first step of the first-run path (issue #15). The Phase 4 trip planner reuses the same map component.

## Global chrome (desktop, 1024px and wider)

The sidebar, top to bottom:

1. The wordmark "Kitchen ERP" in Fraunces.
2. **Search** (⌘K / Ctrl+K) and **Capture** side by side.
3. Sections. Only the active section expands to show its sub-pages. Home shows the inbox count badge.
4. Footer: Catalog, Settings, the signed-in user, and the build line with its status dot.

"New purchase" is not a nav item; it lives under Capture and as the primary action on Shop pages.

**Inbox badge (G6, G16):**
- It counts inbox rows, and an aggregate row counts once.
- It stays hidden until the first inbox response arrives.
- On an inbox error it shows a neutral "!" labelled "Couldn't check what needs you" (D6).

## Kitchen scoping (deferred)

The kitchen switcher and the per-user current-kitchen preference are deferred to Phase 4 (S3; see TODOS.md). In Phases 1 and 2, the only kitchen-scoped features take an explicit home base chosen in context:
- Find nearby, which is OpenStreetMap adoption. It is pre-set to the only home base when there is one.
- Choosing the nearest home base when creating a location.

Kitchen scoping is designed when the switcher returns. Two rules stand now:
- Scoped endpoints take an explicit `home_base_id`; the server never infers scope from a hidden session value.
- Switching kitchens never hides price data recorded at the other kitchen's vendors.

Whether recipe costing is per kitchen is left to the Phase 3 review (T8).

## Search

A command palette opened by the Search button or ⌘K, and the Search tab on phones.

- It searches ingredients, products (including exact barcode) and vendors through one endpoint, `GET /api/v1/search?q=`. `q` is 1–200 characters, and the endpoint returns typed results with a display label and route. It reuses the existing trigram ranking.
- Results are grouped by type, ingredients first, keyboard-navigable, and open the item's page. A group with no hits is hidden.
- Before typing, it shows the last five opened results remembered on this device in localStorage (G15), or the hint "Type a product, ingredient, vendor or barcode." Storage failures degrade quietly to the hint.
- Searchable actions ("new purchase", "log shelf price") are deferred (S2; see TODOS.md). Recipes join search in Phase 3.

## Capture

One entry point for getting data in, with three modes:

| Mode | For | Leads to |
|---|---|---|
| Log a shelf price | Spotting a price without buying | Price entry by barcode or product name (G3) |
| Scan a receipt | Store purchases | Photo upload, then the confirmation notice "Receipt uploaded. It'll appear in Needs you once it's read." (G1) |
| Enter a purchase | Markets and stands without receipts | Manual purchase entry |

- **Where it opens:** a bottom sheet on phone and tablet; a small centred dialog with the same three options on desktop (G14).
- **Store detection:**
  - With geolocation, Capture pre-fills the nearest adopted vendor location ("Near {vendor}") with one tap to change, and shows "Finding where you are…" with Skip while it waits.
  - Without geolocation, it pre-fills the last-used location and says so, "Last used: {vendor} · change", in a squash outline. It never says "Near" (G2).
- **Camera:** barcode scanning with the camera is deferred to Phase 6 (S1). Typed barcodes and wedge scanners work.

## Unified inbox

Every queue of work the system could not finish on its own feeds one list on Home, with one count shown on the Home nav item and the phone Home tab.

| Kind | Source | Title (example) | Fix action |
|---|---|---|---|
| Receipt | Draft or reviewed purchase awaiting a location or commit | "Finish the Sep 24 receipt" | Choose location / Review |
| Couldn't read | Failed ingest job (D4); drafts belonging to a failed job are not listed twice | "A receipt couldn't be read" | Open receipt (Retry and Enter by hand sit together on that page, G5) |
| Identify | Unmatched lines on committed purchases, as one aggregate row (T6) | "23 receipt lines to identify" | Review lines → `/shop/receipts/identify` |
| Bridge | Products whose current observations failed to normalize (no density, unknown measure, no pack), one row per product (T7) | "Oat milk, 1 L carton" | Add density or measure |
| Recipe | Recipe lines that do not resolve to ingredients (Phase 3) | "[Recipe] has 2 unresolved lines" | Resolve |

API (T5): `GET /api/v1/inbox`.
- **Rows:** items with `kind`, `title`, `detail`, `action_label`, `action_route` and `created_at`, oldest first. Each kind is computed from existing tables; no inbox table is required.
- **Reading line:** the response also carries `reading: {count, oldest_at}` for receipts being read (pending or running ingest jobs). Home shows it as "Reading n receipts…" above the list, and it is not counted in the badge (G1).
- **Stalled reading:** when `oldest_at` is older than `INGEST_STALL_MINUTES` (default 10), the line turns squash: "Reading is taking longer than usual · Check System" (D21).
- **Errors:** any kind's query failing fails the whole request, and Home shows an error, never "Nothing needs you" (D6).
- **Freshness:** mutations that resolve an item invalidate the inbox, so it leaves without a manual refresh.

The separate "Needs a bridge" and "To identify" nav items are removed; their pages remain, reached from inbox actions.

## Phone and tablet

Below the `lg` breakpoint (1024px), the sidebar is replaced by a bottom tab bar (G19):

| Tab | Contains |
|---|---|
| Home | Wordmark, inbox, recent purchases (G13) |
| Purchases | Shop → Purchases. Renamed "List" when Phase 4 adds shopping lists (T18). |
| Capture (centre, raised) | The capture sheet |
| Search | The search palette as a full screen |
| More | Shop's other pages, Catalog, Settings; Cook and Stock once built |

Sit-down tasks (recipes, pantry management, catalog editing) live under More. In-store tasks are one thumb away. Only the shell (app shell, navigation, tab bar) changes at `lg`; page layouts keep their own breakpoints.
