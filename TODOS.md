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

## Completed
