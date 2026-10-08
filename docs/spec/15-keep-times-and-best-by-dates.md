# Keep times and best-by dates (2Q) — draft awaiting approval

This sub-phase records how long each ingredient keeps where it is stored, and gives each purchased item a best-by date worked out from it. It is a draft: nothing here is approved for implementation until the household accepts it, as with `07-phase-3-recipes-and-costing.md`.

## Why

Perishability (spec 02) says how fast an ingredient spoils, in four or five broad values. It cannot say when the chicken bought on Tuesday should be cooked or frozen, which is the question a household asks. That date belongs to the purchase, not to the price book: prices never expire, and since issue 2E's revision a price goes stale after one window whatever the ingredient (spec 04).

## Sources

The keep times come from the same USDA material behind perishability, plus one product-specific guide:

- **USDA FSIS, *Food Product Dating*** (fact sheet). Dates on packages are mostly about quality, not safety. If a product has a "use-by" date, follow it. If it has a "sell-by" date or none, cook or freeze it within the chart's times: poultry, ground meat and uncooked sausage 1–2 days; beef, pork and lamb 3–5 days; cured ham 5–7 days; eggs 3–5 weeks; bacon, hot dogs and sealed lunch meat 2 weeks unopened. Frozen food kept frozen is safe indefinitely; freezer times are for quality.
- **FoodSafety.gov, *Cold Food Storage Chart*** (last reviewed 2023-09-19): refrigerator times at 40 °F and freezer times at 0 °F for meat, poultry, fish, shellfish, eggs and leftovers.
- **Food Banks BC, *Food Storage Guidelines*** (reprinting USDA's charts): shelf, refrigerator and freezer times for shelf-stable foods, bakery products, fresh produce, frozen foods and refrigerated foods, unopened and opened.
- **UC ANR Publication 8406, *Nuts: Safe Methods for Consumers to Handle, Store, and Enjoy*** (2010): nuts keep "a few months" at room temperature before their oils go rancid, a year or more refrigerated, and up to 2 years frozen; in-shell chestnuts are the exception at 2–3 months refrigerated.

Where a source gives a range, the keep time is its low end, so a best-by date errs early.

## Data

**Ingredient keep times.** Three optional whole-day fields on `ingredient`: `keep_room_days`, `keep_fridge_days`, `keep_freezer_days`. Absent means the sources give no time for that place, for example raw chicken at room temperature. A `stored_in` default of `room`, `fridge` or `freezer` follows from perishability: `shelf_stable` and `shelf_months` are stored at room temperature, `refrigerated` and `fresh` in the fridge, and `frozen` in the freezer. Each field carries a `field_source` record (1F), so a person's edit wins over a later catch-up.

**Standard list.** Every entry gains the three keep times from the sources, reviewed in the pull request that adds them, as perishability was. A new ingredient takes its entry's times, or for a name typed in, its category's defaults. A catch-up command proposes times for existing ingredients for review, like `kerp ingredients perishability`.

**Purchase line.** Three nullable fields on `purchase_line`:

- `stored_in`: room, fridge or freezer. Defaults to the ingredient's `stored_in` once the line resolves.
- `best_by`: a local date.
- `best_by_source`: `inferred`, `printed` or `person`.

An inferred date is the purchase's local date plus the keep time for the place the line is stored, and is absent when that time is. A printed date is one read from a label or typed in at review. Following FSIS, a printed "use-by" date replaces the inferred one; a "sell-by" date does not, and the inferred date stands. A person may set or clear the date. Only inferred dates are recomputed: when the purchase date is corrected, the line is re-pointed to another product, the line is moved to another place, or the ingredient's keep time changes. Printed and person dates are never overwritten.

Keep times are for unopened food. The sources also give opened times, which are left to the later inventory phase, where opening a package is an event.

## Where it shows

- **Purchase detail.** A "Best by" column with the date and its source ("inferred", "printed"). A line stored in the freezer reads "Frozen: best quality by …".
- **Purchase review.** A date field on each item line for a printed use-by date.
- **Ingredient page.** The three keep times beside perishability, with a hint naming the source chart.
- **Not in this sub-phase:** reminders, a "use soon" list and consumption tracking. Phase 5 (tiered inventory, spec 05) builds on these dates and owns those.

## Acceptance criteria

Q1. Every standard-list entry carries its keep times, and an entry with a negative or fractional time is refused.
Q2. A new ingredient takes its standard entry's keep times, or its category's defaults, and a person's edit is never overwritten by a catch-up.
Q3. A committed purchase line for raw chicken bought on a given local date, stored in the fridge, shows a best-by date one day later; the same line moved to the freezer shows a date from the freezer time, labelled as quality.
Q4. Entering a printed use-by date replaces the inferred date and survives correcting the purchase date; a sell-by date does not replace it.
Q5. Correcting the purchase date moves inferred best-by dates with it.
Q6. A line whose ingredient has no keep time for its place shows no best-by date, never a guessed one.
Q7. Price observations and their staleness are unaffected by any of this.

## Open questions

- Should a household-wide "err early by N days" setting shift every inferred date?
- Should produce that ripens on the counter (bananas, avocados, tomatoes) carry a shelf time and a fridge time, with the line moving from one to the other?
