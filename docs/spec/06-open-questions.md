# 06 — Open Questions Before Phases 1 and 2

A review of this package before implementation, written 2026-09-21. **Every recommendation below was accepted on 2026-09-21 and folded into documents 00 through 04; this file is kept as the record of why.** Each item is a
tension between two things the package says, or between the package and the
repository as it stands. Each ends with a recommendation. Items marked **decide**
need an answer before the sub-phase named; the rest were resolved during review
and are recorded so nobody re-derives them.

The reference clones (cookcli, grocy, kitchenowl, mealie, paprika-recipes) were
surveyed for design lessons only. Two are AGPL-3.0; `docs/licensing.md` records
what may be borrowed from each. Lessons that changed a recommendation below cite
the project by name.

## A. Repository posture (before 1A)

**A1. Household location in the spec — decide.** The glossary names the two home
bases as two cities about an hour apart, and the tile extract and the daylight-saving test are
pinned to Southern California and `America/Los_Angeles`. In a public repository
that discloses where the household lives to city level. The region is hard to hide
(the tiles and timezone are real configuration), but the city names are gratuitous.
*Recommendation:* replace the glossary example with "two home bases about an hour
apart", keep the region and timezone as documented defaults, and never commit real
home coordinates (the scanner's denylist holds their prefixes). Accept that the
repository reveals "a household in Southern California".

**A2. Real purchase data from `unbagged` — decide.** The sibling repository holds
right-to-know responses from retailers: transactions with store codes, line items
with raw descriptions, UPCs, quantities, and retail and loyalty amounts. The package
has no route for that data: purchases enter only by receipt, manual entry, or shelf
price, and the fixture corpus must be synthetic. Three ways to use it:

1. *Bulk import as a fourth purchase source.* Add a sub-phase 2G: `kerp import
   purchases --from <json>` reading a documented normalized JSON (which `unbagged`
   can be taught to export), creating purchases with `source = import`, resolving
   lines by UPC first and alias second, and leaving the rest in the to-identify
   queue. This bootstraps years of price history and exercises the resolver at
   scale on day one. Cost: `purchase.source` and `price_observation.source` gain
   `import`; the importer must take amounts from the export's decimal strings, not
   from `unbagged`'s in-memory floats.
2. *Private validation only.* Keep the import out of the product, but allow an
   opt-in test marker (`-m realdata`) that runs the normalizer and resolver against
   files under `data/imports/` and reports accuracy. Skipped when the directory is
   absent, never in CI.
3. *Design input only.* Read the exports by hand to shape the normalizer's test
   table and the fixture layouts; build nothing.

*Recommendation:* 1 and 2 together, with 2G scheduled after 2D. Whatever is chosen,
the rule in `SECURITY.md` stands: the data stays under `data/` in either repository
and nothing derived from it is committed.

**A3. Barcode as the first rung of resolution.** Follows from A2 and from Phase 6's
barcode scanning: a receipt line or import line that carries a UPC matching
`product.barcode` should resolve automatically, ahead of the alias rung, with
`resolution = barcode`. The ladder in 2D has no such rung and the `resolution`
CHECK list has no such value. *Recommendation:* add it; it costs one column value.

**A4. The recipes repository — decide before 1A's Compose file.** Recipes will
live in a separate repository (`cooklang-recipes`, currently an empty directory).
Phase 3 indexes files by path, hash, and commit, so the API container needs a
checkout. Options: a bind mount of a local checkout via `RECIPES_PATH`; a git URL
the worker pulls; a submodule. A submodule ties this public repository's history to
a repository that may hold private family recipes, and a URL pull needs credentials
in the container. *Recommendation:* bind mount at `data/recipes/`, configured by
`RECIPES_PATH`, present in Compose from 1A as an optional volume. Two related
decisions for that repository: its licence (recipe text suits CC BY 4.0 better than
MIT) and whether each `.cook` file must carry a front-matter `id` so renames survive
reindexing. Cookcli treats the path as identity; the Paprika translator uses a
stable UUID plus a content hash, which is the shape Phase 3 wants. *Recommendation:*
require the front-matter id from the first recipe, and validate it in Phase 3.

**A5. Resolved: layout.** The spec files were under `docs/` and the clones at the
repository root; both now match `CLAUDE.md` (`docs/spec/`, `reference/`). The one
absolute home-directory path in `00-README.md` was removed.

**A6. Resolved: opening-hours parser.** An "established parser library" for OSM
`opening_hours` in Python was a risk. `opening-hours-py` 2.1.4 (MIT or Apache-2.0,
Rust-backed, wheels for Linux) exists and evaluates instants. Avoid
`pyopening-hours`, which is GPL-3.0.

## B. Data model (before 1A migrations, or the sub-phase named)

**B1. `price_observation.purchase_line_id UNIQUE` contradicts void-and-re-emit —
decide (2A).** Reopening a purchase voids a line's observation and emits a new one
for the same line. The voided row stays, because the table is append-only, so the
second insert violates the unique constraint. Options: drop the constraint and add
a `BEFORE INSERT` trigger that rejects an observation for a line that already has a
non-voided one; or make each edit create a new `purchase_line` row and retire the
old one, which is heavier. *Recommendation:* the trigger. `price_current` already
excludes voided rows, so views need no change.

**B2. Unit factors do not fit `NUMERIC(20,10)` — decide (1B).** The seed list gives
`tsp` as 4.92892159375 and `tbsp` as 14.78676478125, eleven decimal places. The
column would round them and criterion 7 ("exactly the factors listed") could not
pass. *Recommendation:* declare `to_base_factor` as unconstrained `numeric`, which
PostgreSQL stores exactly, and keep the Python side as `Decimal` literals from
strings. `mg` appears in the 1B seed list but not in the 02 column comment; seed it.

**B3. "Exactly" for a non-terminating division — decide (2A).** Criterion 2 wants
the normalized unit price to equal price divided by canonical quantity "exactly".
3.99 ÷ 453.59237 does not terminate, and `norm_unit_price` is `NUMERIC(14,6)`.
*Recommendation:* state the rule once: the service computes with a `Decimal`
context of 28 significant digits and quantizes to six places with
`ROUND_HALF_EVEN`; the test applies the same rule. Criterion 5 (truncate and rebuild
reproduces the table exactly) must compare every column except `computed_at`.

**B4. `price_norm` has no provenance — decide (2A).** 1B says every conversion
result carries which bridge was used, its source, and whether it was confirmed.
Criterion 4 asks to recompute exactly the observations that depended on a changed
density, and Phase 3 wants to say how much of a cost rests on unconfirmed bridges.
None of that is possible from the columns listed. *Recommendation:* add
`bridge_kind` (`none`, `density`, `density_override`, `measure`, `pack`),
`bridge_source`, `bridge_confirmed`, and `convert_version` to `price_norm`. The
dependent set for a changed density is then a query, not a guess.

**B5. `purchase.vendor_location_id` must be nullable while a receipt is in
draft.** The header stage leaves an unconfident location for review with ranked
candidates, but the column is a plain foreign key. *Recommendation:* nullable, with
`CHECK (status = 'draft' OR vendor_location_id IS NOT NULL)`.

**B6. What `purchase_line.resolution` records — decide (2D).** Fuzzy and model
rungs only suggest; a human accepts. Is an accepted model suggestion `llm` or
`manual`? Both readings are defensible, and the audit trail needs one.
*Recommendation:* `resolution` names the rung whose answer was used (`barcode`,
`alias`, `fuzzy`, `llm`, `manual`, `unmatched`, `ignored`), and a separate nullable
`resolved_by` names the person who confirmed it; only `barcode` and `alias` may have
a null `resolved_by`. This mirrors Mealie's habit of keeping the original text and
per-component confidence beside the parse.

**B7. Idempotency has no table.** 2F criterion 46 requires a retried `POST` to
return the original response and create nothing. That needs storage:
`idempotency_key(user_id, key, request_hash, status, response_body, created_at)`
with a unique key per user. Two semantics to fix: the key is scoped to the
authenticated user, and the same key with a different payload is a 422.
*Recommendation:* add the table in 1A, since auth and the first `POST`s arrive
there, with a 24-hour retention sweep.

**B8. Cookie sessions have no table.** Server-side sessions allow revocation and
"log out everywhere"; a signed stateless cookie does not. *Recommendation:* a
`session(id, user_id, expires_at, last_seen_at, revoked_at)` table in 1A.

**B9. `reconcile_mismatch` has nowhere to live.** 2C sets it "on the purchase", but
`flags` exists only on `purchase_line`, and reconciliation is not a stage in
`ingest_job.stage`. *Recommendation:* add `purchase.flags TEXT[]`; run
reconciliation as the last step of the `lines` stage and record it in that stage's
output.

**B10. Receipt times are local and naive — decide (2C).** Receipts print a local
time; storage is `timestamptz`; nothing carries a zone. *Recommendation:* a
`HOUSEHOLD_TIMEZONE` setting (default `America/Los_Angeles`) used to interpret
receipt headers and to evaluate opening hours, with a later per-location override
if the household ever shops across zones. Grocy stores naive local time throughout
and it is its most-reported class of bug; KitchenOwl stores UTC.

**B11. Currency is implicit.** No column, no setting. *Recommendation:* a
`CURRENCY` setting defaulting to `USD`, no column, documented as single-currency.

**B12. Health endpoint authentication.** It reports migration head and queue depth.
*Recommendation:* unauthenticated `status` only; details behind a session or token.

## C. Stack and test harness

**C1. Browser tests need a runner.** Criteria 29, 30, and 34 assert on network
activity and on a 390-pixel viewport. *Recommendation:* add Playwright (MIT) to the
frontend stack; run it in CI against the Compose stack with no tiles file present.

**C2. Two statements about the test database.** 01 says the harness starts a real
PostgreSQL; 1A says the suite runs inside the `api` container. *Recommendation:* the
suite uses the Compose `db` service and creates a throwaway database per run with
the owner role, so both `DATABASE_URL` and `MIGRATION_DATABASE_URL` must reach the
`api` container.

**C3. Default model size — check.** `gpt-oss:20b` needs roughly 14 GB of memory
for weights alone and runs on the Mac host natively. Confirm the host can hold it
alongside Docker; if not, pick a smaller schema-constrained model as the default
and let the opt-in `llm` suite's accuracy report justify a later upgrade. The
default suite never needs the model.

**C4. Logging policy for receipt content.** `ingest_stage_result.output` holds full
OCR text, which includes masked tender lines, loyalty numbers, and cashier names.
That belongs in the database and in backups, never in logs. *Recommendation:* write
it into 1A's logging conventions: log identifiers and durations, never stage
output, and redact raw text from error messages.

**C5. Map attribution.** The basemap and adopted locations derive from OpenStreetMap
(ODbL) and Protomaps. The map must show both attributions; `docs/licensing.md`
records the obligation. No decision, but 1E should carry an acceptance criterion
for it.

## D. Lessons from the reference projects

These did not change a decision above but are worth writing into the spec so
reviewers recognise the anti-patterns when they appear.

- **Name the forbidden guesses.** Mealie silently rewrites "ounce" to "fluid ounce"
  whenever the other operand is a volume; cookcli's pantry treats any quantity
  under 100 g or ml as "low" when no threshold is set. Both are exactly what
  non-negotiable 6 forbids. Cite them in `03` beside the resolution order.
- **When units cannot convert, carry both.** cookcli, KitchenOwl, and Mealie each
  independently keep a list of (amount, unit) pairs when aggregation would need a
  density they do not have. Phase 4's shopping-list aggregation should be a list
  from the start; Phases 1 and 2 already behave this way by returning a typed
  failure.
- **Append-only is validated by its absence.** Grocy edits its live stock row in
  place and pairs it with before-and-after journal rows; every price view then
  needs a helper view that reconstructs the original quantity. Void-and-re-observe
  is the cheaper design, and B1 is the only place it needs a constraint adjusted.
- **Pack size belongs with the barcode.** Grocy's product-barcode rows each carry
  their own pack size and store, so one product has several purchasable forms.
  Our `product` row already is that form; do not let pack size drift onto the
  ingredient.
- **Ranges and free text are worth keeping.** Cooklang gives Phase 3 `2-3` as a
  range and "to taste" as text; Mealie collapses ranges to the lower bound and
  cannot get them back. Phase 3 should store ranges as ranges.
- **Grocycode.** Grocy accepts a self-issued code (`grcy:p:<id>`) in any field that
  accepts a barcode, so unbranded goods and specific lots can be labelled and
  scanned. A cheap idea for Phase 6's scanner and for market produce.
