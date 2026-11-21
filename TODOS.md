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

## Receipts

### Offer to remember a barcode printed in a receipt line

**What:** When an identified receipt line carries a valid GTIN and the chosen product has no `gtin` identifier, offer "Remember barcode" beside the code offer, and prefill the barcode in naming mode and "Create product" from the line.

**Why:** Some tills print the product barcode inside each item's text. `receipt_codes.read` finds it, but the offer covers only vendor-positioned codes, so the barcode is read and then dropped, and the next receipt or shelf scan with that barcode finds nothing.

**Context:** Issue #456. Deferred by the open-issues review (2026-11-17): seen on one first receipt from one vendor. Needs a ruling on whether a GTIN read from receipt text is ever recorded without a click; spec 04 2K and criterion 72 say nothing is, and the proposal keeps that. Start from `code_offers` (`LineCodes.offer`) in `services/resolution.py` and the 2I naming path. A GTIN another product holds stays `409 identifier_taken`.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** A second vendor whose till prints barcodes in line text, and the ruling

### Longer item codes for weighed items at counters that share one

**What:** Let a vendor's `rw_layout` take a longer item code, or mark the counters whose items share a code.

**Why:** At one vendor two counter items read the same five-digit item code, because the digit that tells them apart comes after the digits the layout takes.

**Context:** Issue #448, item 4. Items 1–3 shipped: #449 holds a shared code as a suggestion instead of identifying other wording on its own, and #467 lists a product's codes and forgets one from the product page. Deferred by the open-issues review (2026-11-17). Start from `catalog/receipt_codes.py` and `catalog/barcodes.py`.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** A second counter whose items share a code

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

**Context:** Deferred by the ingredient-vocabulary CEO review on 2026-09-30 (board card VC6). Doc 12 rule 6 proposed it, but satisfying through parents changes what price comparison (`ingredient_offer`) and costing mean. Sub-phase 1G ships flat ingredients, and a `parent_id` can be added later without reshaping anything. The Phase 3 review (2026-10-09) kept ingredients flat for costing: a product fulfils a recipe line only when its ingredient, after `merged_into`, is the line's. Issue #525 (2026-11-16) brought the first recipe case: a generic spirit line lost its only price once its bottles were filed under a style. The open-issues review (2026-11-17) kept `parent_id` deferred: the measured harm was one line, which a pin covers, and the cleanup check that offers to move a product to a more specific standard entry ships on its own. When this is built, parent fulfilment must cover `ingredient_offer` (Compare and the trip planner) as well as costing, and `recipe_cost_triggers` must carry a child's price change to recipes with a parent line.

When it lands, retire Tidy the catalog's `product_move` check (`services/cleanup_moves.py`, `catalog/specificity.py`, Wave 2), which stands in for it by moving products to the more specific ingredient.

A second measured case came on 2026-11-19 (#525 comment): one accepted product move emptied a generic ingredient and left its lines unpriced, which no pin can cover. The Depends-on below is met; the wave that builds `parent_id` is still to be planned.

**Effort:** L (human) / M (CC)
**Priority:** P2
**Depends on:** Met 2026-11-19 (#525): a second measured recipe line that a pin can't cover

### USDA attribute names and FoodOn identifiers for the usda tier

**What:** Let the cascade's usda tier (`07`, 3C) find a food by its common or scientific name, and carry a FoodOn identifier on an ingredient's reference.

**Why:** The USDA pool is searched by description only, so a name the description does not contain finds nothing, and a reference has no cross-vocabulary identifier.

**Context:** The 1G amendments in `07` (VS2) expected both to arrive with 3C; the Phase 3 build (2026-10-10) shipped neither. The USDA import (`services/usda.py`) loads no attribute table. What it takes: an `fdc_food_attribute` table (fdc_id, attribute type, name), the importer reading `food_attribute*.csv` when present in the FoodData Central download, a trigram index on the name, and `usda.candidates` searching attribute names beside descriptions. FoodOn needs a data file, an entry in `docs/licensing.md` and an identifier column on the ingredient reference; the licensing entry waits until something reads FoodOn. The usda tier, like `kerp ingredients usda-candidates`, searches Foundation and SR Legacy foods only.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** A recipe name the usda tier misses that an attribute name would find

## Recipes

### Count yields as pieces

**What:** Read `yield: 12 pieces` as a recipe that makes 12 `each`, so a reference such as `@./basics/dumplings{3}` costs 3 of 12.

**Why:** Recipe references (#526) scale only by mass and volume yields. 65 of the household's 70 `yield:` keys are counts, which index as text today and are reported only when a reference needs them.

**Context:** `app/recipes/cooklang/makes.py` refuses a count (`not_mass_or_volume`) on purpose: no reference to a counted recipe exists yet. Allowing `each` makes needs the reference line's count unit too, and `kerp recipes lint` already names an unparsed yield on a referenced recipe.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** A recipe reference to a recipe counted in pieces

### Shopping lists expand recipe references

**What:** Adding a cocktail to a list also adds its syrup's ingredients, scaled by the share of a batch it uses.

**Why:** A recipe reference is skipped as "make <title>" today (#526), so the sugar for the syrup isn't on the list.

**Context:** `shopping_lists.add_recipe` skips `recipe` lines with reason `recipe`. Expansion needs the same scaling as costing (`recipe_cost_sources`), a choice between a whole batch and the share, and a guard against cycles (the link graph has one).

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Phase 4 shopping-list work picking it up

### Route (b) opt-out and whole-batch charging

**What:** A per-ingredient or per-line way to turn off costing from the recipe that makes an ingredient, or to charge a whole batch rather than the share used.

**Why:** Syrups are made for many drinks, so a line takes its share (#526). Stocks and doughs may be made for one dish, where the whole batch is the honest cost.

**Context:** Route (b) costs a line from `ingredient.made_by_recipe_id` when no product price qualifies (`recipe_cost_sources`); the basket adds the same share as consumed.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** House stock, sauce or dough recipes linked to their ingredients

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

### A cost report by folder or tag

**What:** `kerp recipes cost-report`: the priced, posted and unpriced line counts of every recipe, by folder or tag, in a stable text format.

**Why:** Re-measuring costing after a change (Wave 2's cocktail set: `lines_priced + lines_posted` up, nothing priced lost) took a one-off script.

**Context:** Proposed as E3 in the Wave 2 (Costing A) review on 2026-11-18 and deferred as outside its blast radius: the recipes list's completeness filter and one SQL count cover the re-measure, and a read-only scratch script did it for Wave 2.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Nothing

### "Bottles to price" in Needs you

**What:** An inbox row naming the products whose posted prices cost the most recipe lines, so a person knows which bottles to price next.

**Why:** Once posted prices are a recipe's last resort (#523), each recipe's cost table names its posted lines, but nothing says which one receipt or shelf price would firm up the most recipes.

**Context:** Proposed as V10 in the Wave 2 (Costing A) review on 2026-11-18 and deferred as outside its blast radius. The data is in place: `recipe_cost_line.status = 'posted'` with its product.

**Effort:** S (human) / S (CC)
**Priority:** P3
**Depends on:** Wave 2 (#523) deployed

## Plan

### Calibrate the traffic multipliers from observed trips

**What:** Compare each done stop's planned arrival with when "Done here" was pressed at the stop before, fit per-band corrections to `PLAN_TRAFFIC_MULTIPLIERS`, and propose the new table to an admin rather than applying it.

**Why:** The multipliers are a fixed time-of-day table; the household's real drive times are the better evidence once enough trips are done.

**Context:** `05` (drive times) names calibration from observed trips as the later mitigation after the multiplier; Phase 4 (`17`, 4B, P2) shipped the table as a setting with the draft's defaults. Noted at the Phase 4 build (2026-10-13). Arrival is only approximated by stop timestamps, so it needs enough done trips per band before a correction means anything.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Enough done trips to fill each band

### Let an offer stand in at a store with no price-book price

**What:** In a plan made "With this week's offers", let a linked offer price a product at a store where the price book has no price for it yet, and give the stop screen a planned price that names the offer instead of an observation.

**Why:** As built, an offer stands in only where the price book already has a price there, so a store that is cheap only this week, for a product never bought there, is never chosen for it.

**Context:** `18` (2X-3, 2026-11-09). Every planned item keeps the observation that "planned price" and "Cheaper at stop" compare against; an offer-only line has none, so both comparisons need a second source of planned price first.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Linked offers in the household, and a trip where the narrower rule visibly missed a cheaper store

## Completed

- **Edit a location's receipt identifiers in the UI**: the vendor page's location form has "Store codes on receipts" (2026-09-28).
- **Decide whether recipe costing is per kitchen**: household-wide; `recipe_cost_snapshot` carries no kitchen, and Phase 4 adds a per-kitchen filter with the kitchen switcher (Phase 3 review, 2026-10-09; `07`, 3D).
- **A fresh clone creates an empty ../cooklang-recipes**: the Compose default becomes `./data/recipes`, and `.env.example` shows `RECIPES_PATH=../cooklang-recipes` for a sibling repository; a missing, empty or `.cook`-less directory is "no repository", and a directory without `.git` is a repository with no commits (Phase 3 review, 2026-10-09; `07`, prerequisites).
- **The full name-matching cascade for recipe text**: built in sub-phase 3C (2026-10-10): exact name or spelling, inflection, exact standard-list entry, prep-word stripping, USDA pool, then the local model on an explicit "Ask the model"; only the first two resolve without a person. The USDA attribute table and FoodOn identifiers that were to come with it did not; they are their own item under Ingredients.
- **Public example recipes**: a tracked `examples/recipes/` directory of permissively licensed public Cooklang recipes, copied into `data/recipes/` by `make seed-examples` and mounted directly by the demo and e2e stacks; each is credited in `docs/licensing.md` as it is added (2026-10-09; `07`, prerequisites).
- **Kitchen switcher and per-user current kitchen**: a per-user server setting, the switcher in the sidebar footer and the More sheet, kitchen tags on scoped panels and an explicit `home_base_id` on scoped endpoints; deferred on 2026-09-25 (S3), specified in `17` on 2026-10-12 (Phase 4 review, L3 and L7; UI-2.4 to UI-2.7), built in 4A.
- **At-the-store screen**: one stop of a chosen trip plan with in-aisle price edits, "Get here" and "Done here · record purchase", which writes one purchase linked to the plan; dormant since 2026-09-25 (T4), specified in `17` on 2026-10-12 (Phase 4 review, S1; UI-4.8, UI-8.17 and UI-8.18), built in 4C.
