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

### Kitchen switcher and per-user current kitchen

**What:** A current-kitchen switcher persisted per user on the server, kitchen tags on kitchen-scoped panels, and an explicit `home_base_id` on scoped endpoints (the addendum's UI-2.4 to 2.7).

**Why:** Shopping lists, trips and stock are tied to one kitchen, and the household has two.

**Context:** Deferred by the UI addendum review on 2026-09-25 (ruling S3). In Phases 1 and 2 almost nothing is kitchen-scoped. Find-nearby keeps its kitchen picker, and Capture's no-geolocation fallback uses the remembered last location. Needs a preference table, a migration and an endpoint.

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

**Context:** Plan `playful-launching-parrot.md`, Phase 4; eng review 2026-09-29. The tool authenticates with a `vendors:read` + `vendors:suggest` token, which can read only the public export. It sends a model public facts only (vendor and branch names, city or region, OSM ids, public coordinates, current public values), one vendor per prompt, never kitchens, notes, purchases or receipt text. Every suggestion carries a `source_url`; values from sources whose terms are incompatible with the ODbL are dropped. The app caps a batch at 200 items and pending suggestions at 2,000. Start from the suggestion API's schema in `docs/api/openapi.json`.

**Effort:** L
**Priority:** P3
**Depends on:** Phase 3 (scoped tokens and vendor suggestions) merged.

## Ingredients

### Ingredient hierarchy and substitution

**What:** Add parent ingredients (cheese → parmesan → Parmigiano-Reggiano) and decide whether a product satisfies an ingredient through its parents.

**Why:** Recipes name ingredients at different levels ("cheese", "parmesan"), and substitution and cost rollups want the chain.

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VC6). Doc 12 rule 6 proposed it, but satisfying through parents changes what price comparison (`ingredient_offer`) and costing mean. Sub-phase 1G ships flat ingredients, and a `parent_id` can be added later without reshaping anything. The Phase 3 review (2026-10-09) kept ingredients flat for costing: a product fulfils a recipe line only when its ingredient, after `merged_into`, is the line's.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Deferred past Phase 3 (review 2026-10-09); a recipe or comparison that needs the chain

### USDA attribute names and FoodOn identifiers for the usda tier

**What:** Let the cascade's usda tier (`07`, 3C) find a food by its common or scientific name, and carry a FoodOn identifier on an ingredient's reference.

**Why:** The USDA pool is searched by description only, so a name the description does not contain finds nothing, and a reference has no cross-vocabulary identifier.

**Context:** The 1G amendments in `07` (VS2) expected both to arrive with 3C; the Phase 3 build (2026-10-10) shipped neither. The USDA import (`services/usda.py`) loads no attribute table. What it takes: an `fdc_food_attribute` table (fdc_id, attribute type, name), the importer reading `food_attribute*.csv` when present in the FoodData Central download, a trigram index on the name, and `usda.candidates` searching attribute names beside descriptions. FoodOn needs a data file, an entry in `docs/licensing.md` and an identifier column on the ingredient reference; the licensing entry waits until something reads FoodOn. The usda tier, like `kerp ingredients usda-candidates`, searches Foundation and SR Legacy foods only.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** A recipe name the usda tier misses that an attribute name would find

## Recipes

### Cooklang recipe references

**What:** Parse `@./Shared/Vinaigrette{50%ml}` as a reference to another recipe in the repository, cost it from that recipe's snapshot scaled to the quantity, and show it as a link on the recipe page.

**Why:** Cooklang's references let a sauce or a stock be written once; today the parser reads one as an ingredient named `./Shared/Vinaigrette`, which goes to the resolve queue as an unmatched name.

**Context:** Noted in `07`, 3B at the Phase 3 build (2026-10-10). The example recipes in `examples/recipes/` avoid references (their README says which were replaced). Needs a reference kind on `recipe_ingredient` pointing at the other recipe, the resolver skipping references, costing that follows the referenced snapshot and reports the line incomplete when that recipe is, and a cycle guard.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** A household recipe that uses a reference

### Choosing one USDA density per ingredient

**What:** Pick the plain "cup" portion or the median of the volume portions automatically, and keep descriptors ("chopped", "packed") so a recipe note can select the matching conversion.

**Why:** Today each portion is its own suggestion and a person picks one.

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VS5). It only pays off once recipe notes exist to choose between descriptors; the Phase 3 review (2026-10-09) left it out of Phase 3, which parses notes but does not read them for conversions.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Deferred past Phase 3 (review 2026-10-09); recipes whose notes need it

### Stock photos for produce and counter items, by ingredient

**What:** For a product with no photo whose ingredient has a freely licensed stock photo (for example from Wikimedia Commons, CC-licensed), show that photo marked as a stock photo, with its attribution, until the household adds its own.

**Why:** Produce, loose goods and meat or deli counter items have no barcode and rarely a store page with a usable photo, so neither the barcode lookup nor the name search can give them one (#184).

**Context:** Deferred by the issue rulings board on 2026-10-06 (card U2): the "Needs a photo" filter and the helper's search by name were built, this was not. It needs a design for where a licence, an author and a source address are stored and shown for a photo that belongs to an ingredient rather than a product, and it would be a new outbound source, so it lives in the private helper, off by default (non-negotiable 9).

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** A design for ingredient-level photos and their attribution

## Completed

- **Edit a location's receipt identifiers in the UI**: the vendor page's location form has "Store codes on receipts" (2026-09-28).
- **Decide whether recipe costing is per kitchen**: household-wide; `recipe_cost_snapshot` carries no kitchen, and Phase 4 adds a per-kitchen filter with the kitchen switcher (Phase 3 review, 2026-10-09; `07`, 3D).
- **A fresh clone creates an empty ../cooklang-recipes**: the Compose default becomes `./data/recipes`, and `.env.example` shows `RECIPES_PATH=../cooklang-recipes` for a sibling repository; a missing, empty or `.cook`-less directory is "no repository", and a directory without `.git` is a repository with no commits (Phase 3 review, 2026-10-09; `07`, prerequisites).
- **The full name-matching cascade for recipe text**: built in sub-phase 3C (2026-10-10): exact name or spelling, inflection, exact standard-list entry, prep-word stripping, USDA pool, then the local model on an explicit "Ask the model"; only the first two resolve without a person. The USDA attribute table and FoodOn identifiers that were to come with it did not; they are their own item under Ingredients.
- **Public example recipes**: a tracked `examples/recipes/` directory of permissively licensed public Cooklang recipes, copied into `data/recipes/` by `make seed-examples` and mounted directly by the demo and e2e stacks; each is credited in `docs/licensing.md` as it is added (2026-10-09; `07`, prerequisites).
