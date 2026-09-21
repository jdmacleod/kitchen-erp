# 05 — Later-Phase Design Notes

These notes record decisions already reached for Phases 3–6 so that Phases 1–2 are built with them in view. They are context, not a work order. Each phase will get its own specification with acceptance criteria before it is built, and details here may change.

## Phase 3 — Recipes and costing

Recipes are Cooklang files in a git repository, and those files are the source of truth. The database indexes them: a `recipe` row holds the file path, content hash, commit, title, and servings, and `recipe_ingredient` rows are derived by parsing and are rebuilt whenever the hash changes. The text of each ingredient as written in the file is preserved as `raw_name`.

Cooklang ingredient names are free text, so recipes have the same resolution problem as receipts, and the same solution: an `ingredient_alias` table maps normalized names to ingredients, confirmed once and remembered. The review queue built in Phase 2 should be generalized, not duplicated, to serve receipt lines, recipe ingredient names, and missing conversion bridges.

A recipe line may pin a product, for single-source items where only one product will do. Lines with no quantity or with "to taste" are marked negligible and excluded from costing without counting as incomplete.

Costing uses the price book, not inventory. Each line's quantity is converted to the ingredient's canonical unit and multiplied by a normalized unit price chosen by a stated basis: latest, average over a window, or cheapest qualifying. Yield is applied by heuristic: quantities given in count or named measures are treated as as-purchased, quantities given in mass or volume as edible portion and grossed up by `yield_pct`, with a per-line override. Every cost snapshot reports two figures. Consumed cost is quantity times unit price and is the cost-of-goods figure. Basket cost rounds each line up to purchasable packs and is what making the dish requires spending. A snapshot also reports its completeness, broken down into lines that are unpriced, unconvertible, or unmapped, and how much of the total rests on unconfirmed bridges.

Deferred within Phase 3: prepared states such as cooked rice, derived ingredients such as lemon juice from lemons, metric cups, and import from Paprika and from recipe URLs, which should produce Cooklang files rather than database rows.

## Phase 4 — Shopping lists and the trip planner

A shopping list belongs to a home base. Each item references either an ingredient, optionally with a minimum quality, or an exact product, never both; the planner may fill an ingredient item with any qualifying product and a product item only with that product. Items can be marked deferrable. Lists are shared among household members, who can claim and check off items from their own phones.

Drive times are computed once per pair of places by a self-hosted routing engine, Valhalla or OSRM over a Southern California extract, and stored in `travel_leg`. Because locations are static, the routing container runs behind a Compose profile and is needed only when places are added. Free-flow times understate Los Angeles traffic; a time-of-day multiplier is the first mitigation and calibration from observed trips is a later one. Navigation itself is handed off to the phone's maps application.

The planner takes a list, an origin and destination that may differ, a planned time, a value of time, a per-stop overhead that locations may override, and a maximum number of stops. For each item at each location the offer is the cheapest qualifying product from `ingredient_offer`, rounded to purchasable packs. Given a fixed set of stops, every item independently goes to the cheapest stop in the set, so the only search is over sets of stops and their order; with around fifteen candidate locations and at most four stops this is a few thousand cases and is solved exactly by enumeration. The objective is item cost plus value of time multiplied by driving and stop time, plus optional mileage. Locations closed at the planned time are excluded using `opening_hours`.

The planner returns three plans, fastest, cheapest, and balanced, with the marginal trade between them stated in money and minutes. Plans are annotated with forced stops caused by single-source items, suggestions to defer an item that alone forces a stop, savings that depend on stale prices, and items no location can source. Purchases link back to the plan that prompted them so predicted and actual cost can be compared.

## Phase 5 — Tiered inventory

Stock locations such as pantry, refrigerator, and freezer belong to a home base. Each stock item declares how it is tracked. Perpetual tracking keeps a quantity and is meant for expensive items and staples. Par tracking keeps only a state of have, low, or out and suits most things. Untracked items are treated as consumed on purchase, which suits perishables in a kitchen with fast turnover. Committed purchases can increment perpetual items and reset par items; cooking a recipe can decrement perpetual items. Lots with expiry dates and first-in-first-out costing are a possible later addition for perpetual items only. Low and out states feed shopping lists.

## Phase 6 — iOS capture app

A small native SwiftUI app with four actions: photograph a receipt with on-device text recognition so that text and image are uploaded together, enter a quick manual purchase, note a shelf price, and scan a barcode to find or create a product. It keeps an offline queue and syncs when the server is reachable, relying on the idempotency guarantees of the Phase 2 capture contract. It can create a vendor location from the phone's position, which is how roadside stands get onto the map. Review, mapping, recipes, analysis, and everything else remain in the responsive web application.

## Cross-cutting notes

The opaque `ledger_txn_ref` on a purchase is the only link to any finance system and is set by hand or by a future importer; the two systems are deliberately uncoupled. Barcode lookup against an external product database is an optional, off-by-default adapter that may arrive with Phase 6. Backup and restore covers the database volume and the `data/` directory, and a documented restore drill belongs in whichever phase first holds data someone would be sorry to lose, which is Phase 2.
