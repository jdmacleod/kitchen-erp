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

### Flag a second photo of the same receipt

**What:** After a receipt is read, flag it in review when a committed or draft purchase at the same location has the same date and total ("possible duplicate"), with Remove one step away.

**Why:** Byte-identical re-uploads are already caught by the upload's sha256 lookup, but a second photo of the same paper receipt is read again, and committing both would double-count every price.

**Context:** Deferred from #74 by the eng review on 2026-09-28 (D2). The exact-file case is handled in `services/ingest.py` (the sha256 lookup before writing). Removing a purchase (#74) is the manual fix until this exists. The match rule must tolerate two real trips on one day at one store, so it should flag, not block.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** #74 (Remove purchase)

## Purchases

### Restore a voided purchase

**What:** A "Restore" action on a voided purchase: voided → reviewed, then a normal commit re-emits its prices.

**Why:** Removing the wrong purchase otherwise means entering it again by hand.

**Context:** Recorded by the eng review of #72/#74 on 2026-09-28 (D13). With D4, a purchase that ever reached the price book is voided rather than deleted, and keeps its lines. `reopen_purchase` (`services/resolution.py`) accepts only `committed` today and would need to accept `voided`. The recommit path already re-emits observations for resolved item lines.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** #74

## Vendors

### Public vendor dataset and reference enrichment tool

**What:** Create the public `kitchen-erp-vendors` repository (ODbL) and, in it, the reference tool that reads a household's public vendor export, cross-checks it against OpenStreetMap and stores' own sites, asks a model for a structured diff, and posts vendor suggestions.

**Why:** Spec 03 §1F builds the app side (public export, scoped tokens, the suggestion API and review), but nothing uses it until this tool exists, and the public dataset needs a home that is not this repository.

**Context:** Plan `playful-launching-parrot.md`, Phase 4; eng review 2026-09-29. The tool authenticates with a `vendors:read` + `vendors:suggest` token, which can read only the public export. It sends a model public facts only (vendor and branch names, city or region, OSM ids, public coordinates, current public values), one vendor per prompt, never home bases, notes, purchases or receipt text. Every suggestion carries a `source_url`; values from sources whose terms are incompatible with the ODbL are dropped. The app caps a batch at 200 items and pending suggestions at 2,000. Start from the suggestion API's schema in `docs/api/openapi.json`.

**Effort:** L
**Priority:** P3
**Depends on:** Phase 3 (scoped tokens and vendor suggestions) merged.

## Ingredients

### Ingredient hierarchy and substitution

**What:** Add parent ingredients (cheese → parmesan → Parmigiano-Reggiano) and decide whether a product satisfies an ingredient through its parents.

**Why:** Recipes name ingredients at different levels ("cheese", "parmesan"), and substitution and cost rollups want the chain.

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VC6). Doc 12 rule 6 proposed it, but satisfying through parents changes what price comparison (`ingredient_offer`) and costing mean. Sub-phase 1G ships flat ingredients, and a `parent_id` can be added later without reshaping anything. Decide with the Phase 3 costing rules.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 3 approval

### The full name-matching cascade for recipe text

**What:** Prep-word stripping ("minced garlic" → garlic + note), USDA-pool proposals for unknown names, and the Ollama tier, as doc 14 §3 describes.

**Why:** Recipe lines carry prep words and names no alias knows yet. Typed names in 1G don't, because a person is already choosing.

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VS2) to Phase 3 sub-phase 3C. Close matches stay suggestions and never apply themselves (VC3). The Phase 3 draft is amended to carry it. FoodOn references and the USDA attribute table (common and scientific names) moved here too (outside-voice card O8): nothing in 1G reads them, and the FoodOn licensing entry waits with them.

**Effort:** L (human) / M (CC)
**Priority:** P3
**Depends on:** Phase 3 approval, sub-phase 1G

### Choosing one USDA density per ingredient

**What:** Pick the plain "cup" portion or the median of the volume portions automatically, and keep descriptors ("chopped", "packed") so a recipe note can select the matching conversion.

**Why:** Today each portion is its own suggestion and a person picks one.

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VS5). It only pays off once recipe notes exist to choose between descriptors.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 3 approval

### Merge from an ingredient's own page

**What:** A "Merge into…" action (and "Link to standard name") on the ingredient detail page, reusing the link page's merge: survivor choice, retired name, moved products, copied measures, full recompute.

**Why:** Duplicates keep appearing after the one-time link list is cleared, and merging is the lasting capability.

**Context:** Proposed by the outside-voice review of sub-phase 1G on 2026-09-30 (card O12); the user kept the dedicated link page (DV1) and deferred this. Build on `app/services/ingredient_reconcile.py` once 1G ships.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Sub-phase 1G

### Ingredient vocabulary export and import

**What:** `kerp export ingredients` and `kerp import ingredients [--dry-run]` reading and writing a versioned `kitchen-erp-ingredients/1` file, with the vendor file loader's rules and the 1F edit-wins merge, lifted into one module shared with vendors.

**Why:** Moving the household's vocabulary between deployments, or sharing a standard list, needs a plain file.

**Context:** CEO ruling VC1 (2026-09-30) made the database the source with this file as its interchange. The eng review the same day deferred the file itself (D1): nothing reads it yet, backups are full pg_dumps (`kerp backup`), and building it means refactoring `vendor_import.py`'s loader and `geo.py`'s `write_unless_edited` into a shared module. Start from `app/services/vendor_exchange.py` and `vendor_import.py`.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Sub-phase 1G; a reader for the file

### Barcode lookup from USDA branded foods

**What:** Load a slim table of branded foods (barcode, brand, description, category, package weight) and use it to suggest a product and ingredient for an unknown barcode.

**Why:** Scanning an unknown barcode could pre-fill a new product instead of starting from nothing.

**Context:** Deferred by the ingredient-vocabulary handoff itself (doc 15, task T11) and kept deferred by the 2026-09-30 review. The branded CSV is 954 MB, mostly label text, which should never be imported. Pairs with camera scanning (UI section).

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Sub-phase 1G; the household's go-ahead

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
