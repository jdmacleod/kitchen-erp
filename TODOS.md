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

### Strip GPS and other metadata from receipt photos

**What:** Remove EXIF, XMP and GPS from receipt originals on upload, and add a one-off command that does the same to files already stored.

**Why:** A receipt photographed at home keeps the home's coordinates. Originals are stored byte for byte and served untouched (`services/receipt_images.py`), while product photos will be stripped (spec 13 §6.2).

**Context:** Found in the 2026-10-01 CEO review of draft spec 13 (ruling TODO1); outside that plan's scope. Content addressing means a stripped file gets a new sha256, so the command must update `receipt_document.sha256` and `image_path` together and keep the receipt's link to its purchase. Reuse the stripping code spec 13 adds for product images.

**Effort:** S (human) / S (CC)
**Priority:** P2
**Depends on:** Spec 13's image normalization (sub-phase 1I), or written first and shared

### Flag a second photo of the same receipt

**What:** After a receipt is read, flag it in review when a committed or draft purchase at the same location has the same date and total ("possible duplicate"), with Remove one step away.

**Why:** Byte-identical re-uploads are already caught by the upload's sha256 lookup, but a second photo of the same paper receipt is read again, and committing both would double-count every price.

**Context:** Deferred from #74 by the eng review on 2026-09-28 (D2). The exact-file case is handled in `services/ingest.py` (the sha256 lookup before writing). Removing a purchase (#74) is the manual fix until this exists. The match rule must tolerate two real trips on one day at one store, so it should flag, not block.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** #74 (Remove purchase)

### Read long receipts in overlapping slices

**What:** Slice tall receipt images into overlapping bands, read each with the vision model, and drop duplicate rows only inside the overlap bands.

**Why:** A very long receipt may exceed what one vision call reads reliably.

**Context:** Deferred by the vision-reading CEO review on 2026-10-01 (board VH1). Phase 0 reads whole images only and reports reconciliation by receipt length; build this only if long receipts fail there. Spec-review findings R1-18 and R2-11 (overlap-only dedup, a trigger known before reading) apply. Design: `docs/designs/vision-receipt-reading.md`.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 0 results by receipt length

### Re-ask the vision model once when a receipt doesn't add up

**What:** After the vision reader (and consensus, if shipped) leaves a receipt unreconciled, ask the primary model once more, stating the gap ("the lines sum to X; the total reads Y"). Its lines become candidates only.

**Why:** A skipped or misread line is a common cause of a total that doesn't add up, and a second look may catch it.

**Context:** Accepted on the vision-reading scope board (VX3), then deferred by the outside voice (OV6a, 2026-10-01). The re-ask can tempt a model to invent the missing line, and with only about 12 receipts its value can't be measured before Phase 1. The design is settled; build it as decided:
- VD6: last resort only; never removes the first reading.
- D13: skipped when the OCR total disagrees.
- D14: works without consensus.
- A line only the re-ask produced stays unticked unless OCR or a second model independently shows its amount.
- After a re-ask a line can have 4 candidates, so the search caps at 9 disagreeing lines.
- `reconciled_after_reask` is reported apart from the headline.

Design: `docs/designs/vision-receipt-reading.md`.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Vision Phase 1-B live. Build it when `kerp reading-stats` shows more than 1 in 5 receipts unreconciled after 30 receipts.

### Show where each number on a receipt line came from

**What:** Record per number whether it was read from the paper, worked out by code (weight × rate), or repaired (decimal restored), show it in review, and never let a recompute overwrite a person's edit.

**Why:** Makes a later data fix traceable and tells the reviewer which numbers to trust.

**Context:** Deferred by the vision-reading CEO review on 2026-10-01 (board VX6). Phase 1 records only "read by vision" on the stage result; existing line flags (`qty_inferred`, `decimals_restore_total`) cover part of this. Idea from cartlog's PRINTED / EXTRACTED / REPAIRED / INFERRED / MANUAL. Needs a reversible migration.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Vision reading Phase 1

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

**What:** Prep-word stripping ("minced garlic" → garlic + note), USDA-pool proposals for unknown names, and the Ollama tier, as the handoff described (`12`, and 07's 1G amendments).

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

### Stock photos for produce and counter items, by ingredient

**What:** For a product with no photo whose ingredient has a freely licensed stock photo (for example from Wikimedia Commons, CC-licensed), show that photo marked as a stock photo, with its attribution, until the household adds its own.

**Why:** Produce, loose goods and meat or deli counter items have no barcode and rarely a store page with a usable photo, so neither the barcode lookup nor the name search can give them one (#184).

**Context:** Deferred by the issue rulings board on 2026-10-06 (card U2): the "Needs a photo" filter and the helper's search by name were built, this was not. It needs a design for where a licence, an author and a source address are stored and shown for a photo that belongs to an ingredient rather than a product, and it would be a new outbound source, so it lives in the private helper, off by default (non-negotiable 9).

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** A design for ingredient-level photos and their attribution

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

### Notice when a tab is running an old build

**What:** Compare the build the page was loaded with against the one `/api/v1/health` reports, and offer "Reload to get the new version" when they differ.

**Why:** After an update, an open tab keeps running the previous build. On 2026-09-30, a tab from before 1G followed Home's new "Link" inbox row to a route it didn't know. It rendered the new API's answer as the wrong page and went blank. #109 added a page-level error boundary, so a crash now shows "This page couldn't be shown" with Reload instead of a white screen. The tab still doesn't know it's out of date until something breaks.

**Context:** `/health` already carries `version` and `commit` (the sidebar's build line). The web bundle would need its own build id baked in at build time. Deploys happen by `make up` on the household stack.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** None

## Completed

- **Edit a location's receipt identifiers in the UI**: the vendor page's location form has "Store codes on receipts" (2026-09-28).
