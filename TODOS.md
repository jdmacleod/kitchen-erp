# TODOS

Deferred work with its reason. Each item was deferred by an explicit decision; the
source is named so it can be traced back.

## UI

### Camera barcode scanning on the phone shelf-price flow

**What:** Open the camera, scan a barcode, and offer create-on-unknown with the barcode pre-filled (the addendum's UI-4.5 camera clause and UI-4.7).

**Why:** Standing at a shelf, scanning is faster than typing a 12-digit UPC.

**Context:** Deferred by the UI addendum review on 2026-09-25 (ruling S1). The spec's later-phase notes (`05-later-phase-design-notes.md`) give scanning to the Phase 6 native capture app. Until then, the phone shelf-price screen takes a typed barcode or a wedge scanner, which the product typeahead already matches exactly. Browser support is uneven: iOS Safari has no `BarcodeDetector`, so the web version needs a JS decoder.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 6 approval, or a decision to do it in the web app first

### Searchable actions in the ⌘K palette

**What:** Make actions ("new purchase", "log shelf price") findable in the search palette alongside ingredients, products and vendors (the addendum's UI-2.9).

**Why:** Keyboard users reach any action without the sidebar.

**Context:** Deferred by the UI addendum review on 2026-09-25 (ruling S2). Entity search ships first. "Switch to <kitchen>" also depends on the kitchen switcher below.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Search palette shipped

### Kitchen switcher and per-user current kitchen

**What:** A current-kitchen switcher persisted per user on the server, kitchen tags on kitchen-scoped panels, and an explicit `home_base_id` on scoped endpoints (the addendum's UI-2.4 to 2.7).

**Why:** Shopping lists, trips and stock are tied to one kitchen, and the household has two.

**Context:** Deferred by the UI addendum review on 2026-09-25 (ruling S3). In Phases 1 and 2 almost nothing is kitchen-scoped. Find-nearby keeps its home-base picker, and Capture's no-geolocation fallback uses the remembered last location. Needs a preference table, a migration and an endpoint.

**Effort:** M (human) / S (CC)
**Priority:** P2
**Depends on:** Phase 4 (shopping lists) approval

### At-the-store screen

**What:** The single-stop trip screen with in-aisle price edits and "Done here · record purchase" (the addendum's UI-4.8).

**Why:** It closes the loop between a trip's predicted cost and what was actually paid.

**Context:** Marked dormant by the UI addendum review on 2026-09-25 (ruling T4). The layout stays in the page-layouts spec as a Phase 4 design brief.

**Effort:** L (human) / M (CC)
**Priority:** P3
**Depends on:** Phase 4 approval, trip plans

### Decide whether recipe costing is per kitchen

**What:** Settle whether recipe costing is household-wide (the UI addendum) or restricted to a home base's locations (the Phase 3 draft).

**Why:** The two documents disagree, and whichever is built second has to change.

**Context:** Left to the Phase 3 approval by the UI addendum review on 2026-09-25 (ruling T8). The recipes line was removed from the addendum's scoping table so only the Phase 3 draft speaks to it.

**Effort:** S (human) / S (CC)
**Priority:** P2
**Depends on:** Phase 3 review

### One name for home bases

**What:** Pick one of "kitchen" and "home base" and use it everywhere. Today the Settings page title and nav item say Kitchens, while its form, list, empty state, the map's "Add home base here" button and the Vendors filter all say home base.

**Why:** A new user reads two names and wonders whether they are two things.

**Context:** Found in the fresh-install DX pass on 2026-09-27. The rename was left out of #57 because a partial rename would only move the inconsistency somewhere else. Spec 09 names the page Kitchens.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** A naming decision

## Receipts and import

### Upload several receipts at once

**What:** Let the Receipts upload take several files (`multiple`), one ingest job each.

**Why:** A backlog of receipts, the normal first import, means one file-picker round trip per receipt today.

**Context:** Found in the fresh-install DX pass on 2026-09-27 with eight receipts. The worker runs one job at a time, so a batch also makes #60 more visible.

**Effort:** S (human) / S (CC)
**Priority:** P2
**Depends on:** None

## Operations

### Backup refuses to overwrite an existing backup

**What:** `kerp backup --out DIR` should refuse a directory that already holds a `manifest.json`, unless given `--force`.

**Why:** A second backup to the same directory silently replaces the first one's dump and manifest. If the second backup fails partway, the last good backup is gone. The README's `$(date +%F)` example does this on any day with two runs.

**Context:** Found in the fresh-install DX pass on 2026-09-27. It was left as a policy call, because scripts that write to a fixed "latest" directory would break.

**Effort:** S (human) / S (CC)
**Priority:** P2
**Depends on:** None

### A fresh clone creates an empty ../cooklang-recipes

**What:** Stop Compose's default `RECIPES_PATH=../cooklang-recipes` bind mount from creating a directory beside a fresh clone.

**Why:** `make up` on a new checkout leaves an empty `cooklang-recipes/` next to the repository, outside anything the user chose. Nothing reads the mount until Phase 3.

**Context:** Found in the fresh-install DX pass on 2026-09-27. The options are to default to `./data/recipes`, or to add the mount only once Phase 3 is approved. Either one changes the default for existing deployments that rely on the sibling path.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 3 approval, or a decision on the default

## Completed

- **Edit a location's receipt identifiers in the UI**: the vendor page's location form has "Store codes on receipts" (2026-09-28).
