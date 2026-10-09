# 07 — Phase 3: Recipes and Costing

**Status: approved 2026-10-09 (Phase 3 review); amendments from that review are
marked inline.**

Phase 3 makes the price book answer the question it exists for: what does this
dish cost, and how much of that figure can be trusted. Recipes are Cooklang files
in a git repository mounted read-only: the household's `cooklang-recipes`
repository beside this one, or a copy under `data/recipes`. The application
never writes to that repository; it indexes the working tree, resolves each
ingredient line to a catalog ingredient once and remembers the answer, and costs
recipes from the price book. Everything the application knows about a recipe
beyond the file lives in the database.

The design decisions in `05-later-phase-design-notes.md` are taken as settled
here: working-tree indexing with a dirty marker, last-good index on parse error,
`missing` rather than deleted, identity carried without writing into the file,
snapshots keyed by content hash, costing from the price book rather than
inventory, and the review queue generalized rather than duplicated. This
document turns them into sub-phases with acceptance criteria. It supersedes
decision A4 in `06-open-questions.md` on recipe identity: no front-matter id is
required.

The sub-phases build in order. 3A and 3B can be built in parallel; 3C depends
on both; 3D depends on 3C; 3E depends on everything before it.

## Prerequisites and decisions taken

- Phases 1 and 2 pass their acceptance criteria. Costing needs `convert`, the
  price book views, and the resolution machinery.
- The mount exists: `RECIPES_PATH` (a host path) is bound read-only at
  `/data/recipes` in `api` and `worker`. The Compose default is
  `./data/recipes`, so a fresh clone creates nothing outside itself;
  `.env.example` shows `RECIPES_PATH=../cooklang-recipes` for a household whose
  repository sits beside this one (Phase 3 review, 2026-10-09). A `.git`
  directory counts as a repository. "No repository" means the directory is
  missing, or holds neither `.cook` files nor a `.git` entry: the recipes area
  reports that state and nothing else is affected. A directory with `.git` but
  no `.cook` files is a repository with no recipes: the scan runs and every
  indexed recipe becomes `missing` (package 2, 2026-10-09). A directory with
  `.cook` files and no `.git` is a repository with no commits: every file is
  dirty and `head_commit` is null.

  Public example recipes ship in a tracked top-level `examples/recipes/`
  directory (`examples/` leaves room for other domains later). `make
  seed-examples` copies them into `data/recipes/` on the host, so the running
  application still never writes to the mount; the demo and e2e Compose stacks
  mount `examples/recipes` directly. They are permissively licensed public
  Cooklang recipes (MIT, CC0 or Apache-2.0), each credited in
  `docs/licensing.md` as it is added, and never anything from the household's
  own repository (Phase 3 review, 2026-10-09).
- **Parser.** Cooklang's grammar is small. The default is an in-house parser in
  `app/recipes/cooklang/`, pure like `app/units/`, covering front matter,
  sections, `@ingredient{qty%unit}` with multi-word names, `#cookware{}`,
  `~timer{}`, comments, and the quantity forms number, fraction, range, and
  text. The Phase 3 review (2026-10-09) added what real files use: ingredient
  notes `@name{qty}(note)`, anonymous timers `~{3%minutes}`, unicode fractions
  (`¾`, `2½`), mixed numbers, and nested front matter, whose keys beyond the
  ones the application reads are kept verbatim in `recipe.front_matter`. It is
  validated against the canonical test suite published by the Cooklang project
  (MIT), vendored under `backend/tests/fixtures/cooklang/` with its licence
  file; the suite stays the gate for every addition. A third-party parser may
  replace it only if its licence is permissive and it passes the same suite;
  `cooklang-py` on PyPI ships no licence metadata and is not adopted until that
  is resolved.
- **Git access.** Blob hashes are computed in pure Python: SHA-1 of
  `b"blob %d\0" % len(content) + content`. Reading the `HEAD` tree, walking
  history for rename detection, and reading blobs at a commit use `dulwich`
  (Apache-2.0 or GPL-2.0 dual licence, used under Apache-2.0). `pygit2` is not
  used. Nothing ever takes a git lock, refreshes the index, or shells out to
  `git`.
- **Quantity representation.** A recipe line quantity is one of a number, a
  range with a low and a high, or free text. Ranges are stored as ranges and
  costed at both ends; text quantities are negligible for costing. Numbers are
  `Decimal`; fractions such as `1/2` and `1 1/2` parse exactly.
- **Metric cups, prepared states, derived ingredients, and importers** stay
  deferred as `05` lists them. Importers, when they arrive, are command-line
  tools that write `.cook` files for a person to review and commit.

## Data model additions

```
recipe(
  id, path TEXT UNIQUE,                       -- relative to the repository root
  title, servings NUMERIC?, servings_text?,
  content_hash TEXT,                          -- git blob hash of the file on disk
  head_commit TEXT?,                          -- HEAD when last indexed; null if no commits
  dirty BOOLEAN,                              -- untracked, or differs from its blob at HEAD
  status CHECK IN (ok, parse_error, missing),
  parse_error_message?,
  front_matter JSONB?,                        -- every front-matter key as written (Phase 3 review, 2026-10-09)
  last_indexed_at, last_seen_at,
  notes?
)

recipe_ingredient(                             -- derived; rebuilt when content_hash changes
  id, recipe_id FK, seq INT, section?,
  raw_name TEXT,                               -- exactly as written
  name_norm TEXT,                              -- normalized for alias lookup
  qty_kind CHECK IN (number, range, text, none),
  qty NUMERIC?, qty_high NUMERIC?, qty_text?,
  unit_text?, unit FK unit?,                   -- as written, and as parsed by the 1B parser
  ingredient_id FK?,                           -- resolved through ingredient_alias or a person
  resolution CHECK IN (alias, manual, unmatched, negligible),
  negligible BOOLEAN,                          -- no quantity, "to taste", or text quantity
  yield_mode CHECK IN (auto, as_purchased, edible) DEFAULT auto,
  UNIQUE (recipe_id, seq)
)

ingredient_alias(                              -- created by 1G (02); Phase 3 adds kind/source values
  id, name_norm TEXT UNIQUE, ingredient_id FK,
  kind, source,                                -- a recipe decision writes kind synonym, source recipe
  confirmed_count INT, last_seen_at
)

recipe_name_ignore(                            -- "not an ingredient" (Phase 3 review, 2026-10-09)
  name_norm TEXT UNIQUE, created_at, created_by FK
)

recipe_pin(                                    -- a single-source line pinned to a product
  id, recipe_id FK, name_norm TEXT, product_id FK,
  UNIQUE (recipe_id, name_norm)
)

recipe_cost_snapshot(                          -- household-wide (Phase 3 review, 2026-10-09)
  id, recipe_id FK, content_hash, head_commit?, provisional BOOLEAN,
  basis CHECK IN (latest, average, cheapest), window_days INT?, min_quality SMALLINT?,
  consumed_cost NUMERIC(12,4)?, consumed_cost_high NUMERIC(12,4)?,
  basket_cost NUMERIC(12,4)?, basket_cost_high NUMERIC(12,4)?,
  per_serving NUMERIC(12,4)?,
  lines_total INT, lines_priced INT, lines_unpriced INT, lines_unconvertible INT,
  lines_unmapped INT, lines_negligible INT,
  unconfirmed_share NUMERIC(5,4),              -- fraction of consumed cost resting on unconfirmed bridges
  computed_at,
  UNIQUE (recipe_id, content_hash, basis, window_days, min_quality)
)

recipe_cost_line(                              -- one per recipe_ingredient at snapshot time
  id, snapshot_id FK, recipe_ingredient_id FK,
  status CHECK IN (priced, unpriced, unconvertible, unmapped, negligible),
  canonical_qty NUMERIC?, canonical_unit?, yield_applied NUMERIC(5,4)?,
  product_id FK?, observation_id FK?, norm_unit_price NUMERIC(14,6)?,
  consumed_cost NUMERIC(12,4)?, basket_cost NUMERIC(12,4)?, packs NUMERIC?,
  bridge_kind?, bridge_confirmed BOOLEAN?, failure_code?
)
```

`recipe.path` is the working identity; the rename procedure in 3A moves rows
rather than creating new ones. `recipe_cost_snapshot` and `recipe_cost_line`
are derived and rebuildable in the same sense as `price_norm`: `kerp
recompute-costs` may truncate them, so both join the `TRUNCATABLE` list in
`backend/app/core/grants.py` in the migration that creates them (Phase 3
review, 2026-10-09). Pins, aliases and ignored names are facts a person stated
and are not derived.

The `ingredient_alias` source check constraint (`ck_ingredient_alias_source`,
today `standard, generated, rename, merge, manual, import`) gains `recipe` in
the Phase 3 migration. A name a person marks as not an ingredient cannot be an
alias, since an alias always names an ingredient; it goes in
`recipe_name_ignore`, and such a name is neither queued nor counted as unmapped
again (Phase 3 review, 2026-10-09).

The unified inbox (`GET /api/v1/inbox`, `09-information-architecture.md`) gains a
`recipe` kind for unmapped recipe names, so the generalized queue is the inbox
rather than a separate `review_queue` view (UI review T5, 2026-09-25). Receipt,
identify and bridge behaviour is unchanged.

Every recipe endpoint takes a session. None is offered to an API token scope:
the products helper's `products:read` and `products:suggest` scopes do not
reach recipes, and no new scope is added (Phase 3 review, 2026-10-09).

## 3A — Repository indexer

Scan `RECIPES_PATH` for `*.cook` files on a schedule (`RECIPES_SCAN_SECONDS`,
default 30) in the worker and on demand through `POST /api/v1/recipes/rescan`.
There is no cron: the worker's idle timer, which already runs the listing
refresh, runs the scan every `RECIPES_SCAN_SECONDS`, and a rescan request runs
the same scan inline in `api` and answers when it is done (Phase 3 review,
2026-10-09). A scan lists files with their modification times, skips any file
modified within the last `RECIPES_SETTLE_SECONDS` (default 2), computes the
blob hash of each remaining file, and re-indexes only those whose hash differs
from the stored one. The settle window stays a setting because a bind mount
under Docker on macOS can report modification times late; a deployment that
sees half-written files raises it. Dirtiness is the comparison of that hash
with the blob at the same path in the `HEAD` tree; a file absent from `HEAD` is
dirty and untracked. When the repository has no commits, or the directory has
no `.git` at all, every file is dirty and `head_commit` is null.

A file that fails to parse keeps its last good `recipe_ingredient` rows, pins,
and snapshot; the recipe is marked `parse_error` with the message and the new
hash, so the scan does not retry it until it changes again. A file that has
vanished is marked `missing`; its rows are kept.

Identity across renames is carried in three steps, in this order. A path that
disappeared while a new path appeared with the same content hash is a pure move:
the `recipe` row is repointed. If `HEAD` advanced since the last index,
`dulwich`'s rename detection between the two commits repoints rows for moves
combined with edits. For an uncommitted move with an edit, the indexer records a
proposal when a new file's title and normalized ingredient set resemble a
`missing` recipe's (title similarity above a threshold, or at least two thirds of
the ingredient names in common); `POST /api/v1/recipes/{id}/relink` with the
proposed target confirms it, and a recipe can also be explicitly removed with
`DELETE /api/v1/recipes/{id}` once missing.

Nothing in this sub-phase writes to the mount, and the indexer runs with the
mount read-only; a test bind-mounts a temporary repository read-only and asserts
that no file under it changes across a full scan.

### Acceptance criteria

1. With a temporary repository of five synthetic recipes, a scan indexes all five
   with their titles, paths, and hashes; a second scan re-indexes none.
2. A file modified less than two seconds ago is skipped and indexed on the next
   scan once stable.
3. Editing a tracked file marks it dirty; committing it (in the test harness,
   through `dulwich`) and rescanning clears the marker without re-parsing when
   the content is unchanged.
4. A file that stops parsing keeps its previous ingredient rows and snapshot,
   shows `parse_error` with a message locating the problem, and returns to `ok`
   when fixed.
5. A pure move keeps the recipe id, its pins, and its snapshots.
6. A committed move with an edit is repointed through git rename detection.
7. An uncommitted move with an edit yields a relink proposal; confirming it
   repoints the row, and refusing leaves a `missing` recipe whose history is
   retained until it is explicitly removed.
8. A scan against a read-only mount changes no file, takes no git lock, and never
   spawns a `git` process (verified by a test that fails on `subprocess` use in
   `app/recipes/`).
9. With no repository mounted (the directory missing, or holding neither `.cook`
   files nor a `.git` entry; package 2, 2026-10-09), the recipes area reports
   that state and every other endpoint works.

## 3B — Cooklang parsing

Implement `app/recipes/cooklang/` as a pure library: `parse(text) -> Recipe |
ParseError`, where `Recipe` carries front matter (title, servings, tags, any
other keys preserved verbatim, nested keys included; the indexer stores the
whole mapping in `recipe.front_matter`), sections, steps with their ingredient,
cookware, and timer references in order, and per-ingredient `raw_name`,
quantity, unit text, and note (`@name{qty}(note)`). Timers may be anonymous
(`~{3%minutes}`). Quantities parse to number, range, or text without loss;
`1/2`, `1 1/2`, `¾`, `2½`, `2-3`, `2 to 3`, and `some` are all covered (Phase 3
review, 2026-10-09). The unit text is passed to the 1B unit parser; a measure
label such as `clove` is kept as text for 3C.

The library is validated against the Cooklang canonical test suite, vendored
with its licence, and with property tests: parsing a rendered recipe reproduces
its structure, and every failure returns a typed `ParseError` with line and
column rather than raising.

### Acceptance criteria

10. The canonical suite passes in full; any deliberate exclusion is listed with
    a reason in the test file.
11. Quantity forms number, fraction, unicode fraction, mixed number, range, and
    text each parse to the documented representation, exactly and in `Decimal`;
    an ingredient note and an anonymous timer each parse to theirs.
12. A syntax error reports its line and column and never raises out of `parse`.
13. `app/recipes/cooklang/` imports nothing that performs I/O, verified as for
    `app/units/`.

## 3C — Ingredient resolution and the generalized queue

Each `recipe_ingredient.raw_name` is normalized by the 1G normalizer in
`app/catalog/names.py` (a versioned pure function distinct from the receipt
normalizer) and looked up against ingredient names and `ingredient_alias`, as
1G's `search_ingredients` does. A hit resolves the line with
`resolution = alias`; an inflection such as "eggs" for egg (1G's `singulars`)
is a hit, not drift. Those two steps, exact name or spelling and then
inflection, are the only ones that resolve a line without a person (VC3). A
miss leaves it `unmatched` with proposals from the cascade below, ranked, never
auto-applied. A person choosing an ingredient or creating one inline writes an
alias, so the same name resolves on every recipe thereafter; marking the name
as not an ingredient writes `recipe_name_ignore` instead. Lines with no
quantity, a text quantity, or names in a small configurable negligible list
(`salt`, `pepper`, `water` by default) are `negligible` and never counted as
incomplete.

**The cascade (Phase 3 review, 2026-10-09).** For a name the first two steps do
not resolve, 3C proposes in this order: an exact match against the standard
list (`standard_match.match_entry`, which offers the entry's existing
ingredient or creating one from it); prep-word stripping; the USDA pool; then
the local model, whose JSON is accepted only if it validates against the
expected model, and whose tests carry the opt-in `llm` marker. Each tier's
proposals are suggestions on the resolve page and in the typeahead; the
1G amendments below give the tiers' rules.

**Writing the decision (Phase 3 review, 2026-10-09).** A recipe decision writes
its alias through the 1G spelling service (`services/spellings.py`,
`add_spelling`, with kind `synonym` and source `recipe`), never through the
receipt alias writer. A name another ingredient already has as a name or
spelling is refused with `409 alias_taken`; the resolve page shows which
ingredient has it and offers "Use {ingredient}" or choosing again, so the
collision is a choice rather than a silent overwrite. Confirming a name that an
alias already resolves bumps `confirmed_count` and `last_seen_at` on that alias.

**Hierarchy stays flat (Phase 3 review, 2026-10-09).** Ingredients have no
parent in Phase 3. A product fulfils a line when `product.ingredient_id`, after
following `merged_into`, is the line's ingredient; nothing is satisfied
through a parent. TODOS.md keeps the hierarchy item.

**Merges repoint recipe rows (Phase 3 review, 2026-10-09).** A product merge
repoints `recipe_pin.product_id` and `recipe_cost_line.product_id` to the
survivor, and an ingredient merge repoints `recipe_ingredient.ingredient_id`,
each in the same transaction as the other tables that merge repoints: the
table lists in `services/product_merge._repoint` and the ingredient merge in
`services/ingredient_reconcile.py` gain the three recipe tables. Snapshots of
recipes a merge touched are recomputed.

A recipe line may pin a product: `PUT /api/v1/recipes/{id}/pins/{name_norm}`
with a product that fulfils the resolved ingredient. Pins survive re-indexing
while the name in the file is unchanged.

The queue: the inbox's `recipe` kind is one aggregate row, "N recipe names to
resolve", mirroring the identify row, and its action opens
`/cook/recipes/resolve` (Phase 3 review, 2026-10-09). That page lists unmapped
recipe names grouped by normalized name with the recipes using them, one row
per name, and one decision applies to every listed instance and writes one
alias. Receipt lines keep the Phase 2 to-identify endpoints and page, and
bridges keep theirs; there is no separate review-queue endpoint or page.

### Acceptance criteria

14. A name confirmed once resolves automatically in every later recipe that uses
    it, across re-indexing.
15. Unmatched names appear as one inbox row and, on the resolve page, grouped
    with their recipes; one decision applies to all of them and writes one
    alias through `add_spelling`, and a name another ingredient has is refused
    with `alias_taken` rather than moved.
15a. A name marked as not an ingredient is recorded in `recipe_name_ignore`,
    leaves the queue, and is not counted as unmapped on any recipe.
16. Negligible lines are excluded from the queue and from completeness counts.
17. A pin is refused for a product that does not fulfil the line's ingredient,
    and survives a re-index that leaves the name unchanged.
18. Receipt-line identification (the inbox's `identify` kind, and the Phase 2
    to-identify endpoints and page) behaves exactly as the Phase 2 tests require;
    those tests run unchanged.

## 3D — Costing

A snapshot is computed for a recipe under a stated basis: `latest` (each line's
most recent qualifying normalized price), `average` over `window_days`, or
`cheapest` qualifying. The default basis is `latest`; the selector offers
`average`, whose window is `stale_after_days` (default 90), and `cheapest`
(Phase 3 review, 2026-10-09). Qualifying follows the price book views exactly
(Phase 3 review, 2026-10-09): a non-voided observation with a successful
normalization, from a committed purchase or a shelf price, never a posted
(listing) price, from an active location, of a product that fulfils the line's
ingredient with at least `min_quality` when given, and of the pinned product
when the line has a pin. A price older than `stale_after_days` is used and
reported stale on its line, never filtered out and never silently preferred.

Costing is household-wide: every qualifying price counts, whichever kitchen's
vendors recorded it (Phase 3 review, 2026-10-09, settling UI review T8 of
2026-09-25). Phase 4 adds a per-kitchen filter with the kitchen switcher; until
then no snapshot carries a kitchen.

Each line's quantity converts to the ingredient's canonical unit through
`convert`, using the pinned or chosen product's context where one exists. Yield
applies by the heuristic in `05`: count and named-measure quantities are
as-purchased; mass and volume quantities are edible portion and are grossed up
by dividing by `yield_pct`; `yield_mode` overrides per line. `yield_pct` has
been on `ingredient` since 1G; 3D gives it a field on the ingredient form that
offers the USDA refuse value when the ingredient has a reference, and an
ingredient without one costs at 100% with the cost line saying so (Phase 3
review, 2026-10-09). Consumed cost is canonical quantity times unit price.
Basket cost rounds each line up to whole packs of the product used and
multiplies by that product's pack price; a product without a pack contributes
its consumed cost. Ranges produce a low and a high for both figures.
Per-serving divides consumed cost by `servings` when numeric. Line prices and
converted quantities are shown through the unit-display layer
(`services/unit_display`), per lb, oz or fl oz as the price book shows them;
storage stays metric (Phase 3 review, 2026-10-09).

Completeness counts lines by status and reports `unconfirmed_share`, the
fraction of consumed cost that rests on an unconfirmed bridge, using the
provenance `convert` returns. A snapshot records `content_hash`, `head_commit`,
and `provisional = dirty`. Snapshots are recomputed for a recipe when its hash
changes, when an alias or pin affecting it changes, and when a bridge or price
that a priced line used changes; `kerp recompute-costs` rebuilds all.

### Acceptance criteria

19. For a synthetic recipe whose lines all have confirmed bridges and prices,
    consumed and basket costs equal hand-computed figures to the cent under the
    price book's rounding rule, for each of the three bases.
20. A line in cups of an ingredient with a density is grossed up by yield; the
    same quantity given in `each` is not; a per-line override flips either.
21. A range quantity yields a low and a high; a recipe with one text quantity
    reports it negligible and complete.
22. A line without a price, one without a bridge, and one without a mapping are
    counted in the right completeness buckets and the totals exclude them.
23. `unconfirmed_share` equals the consumed cost of lines whose provenance is
    unconfirmed divided by total consumed cost.
24. A snapshot from a dirty file is provisional; committing the file unchanged
    clears the flag on the next scan without recomputation.
25. Changing a density used by a costed line recomputes exactly the snapshots of
    recipes that used it, and no others (asserted on `computed_at`).
26. Truncating `recipe_cost_snapshot` and running `kerp recompute-costs`
    reproduces every figure.
27. A pinned line is costed only from the pinned product, and reports unpriced
    when that product has no qualifying price even if others do.
27a. A posted price, a price from an uncommitted purchase, a voided price and a
    price at an inactive location never cost a line; a price older than
    `stale_after_days` does, and its line reports it stale.

## 3E — Recipe screens

A recipes list with title, servings, dirty and status badges, the latest
consumed and basket cost with completeness, and filters for status and
completeness. A recipe page shows the file's rendered structure beside a cost
table: each line with its raw text, resolved ingredient (with the typeahead to
change it, which writes an alias), pin, converted quantity and provenance,
price used with its age and location, and line cost; totals with low and high;
completeness and unconfirmed share; a basis selector; a history of committed
snapshots as a chart with provisional points marked. Parse errors show the
message and line. Relink proposals appear on `missing` recipes. Unmapped
recipe names reach the person through the Home inbox's one recipe row, which
opens the resolve page; the to-identify page stays for receipt lines. The
ingredient hub's "Used in" card, listing the recipes whose lines resolve to
the ingredient, is built here (Phase 3 review, 2026-10-09).

The Cook section's routes, layouts and UI criteria are in `09`, `10` and `11`
(UI-7), written at the Phase 3 review (2026-10-09). The `features` list on
`/health` gains `cook`, keyed on migration 0042, which creates the recipe
tables; the Cook section appears in the navigation once it is applied.

### Acceptance criteria

28. Resolving an unmapped line from the recipe page writes the alias and
    updates the cost without a reload.
29. Switching basis changes the figures and shows which prices were used.
30. A dirty recipe shows the marker on the list and page, and its snapshot is
    labelled provisional.
31. A ten-line recipe can be fully resolved and pinned from the keyboard.
32. On a 390-pixel viewport the cost table reads without horizontal scrolling.
33. An ingredient's page lists the recipes that use it, and an ingredient used
    by none shows no card.

## Amendments from sub-phase 1G (2026-09-30)

The ingredient-vocabulary review (`12`, and 1G in `03`) moved its recipe work
here. These amendments were approved with this document on 2026-10-09; the
VS2 and VC6 entries carry that review's rulings.

- **Extraction and report.** `kerp recipes vocabulary` lists the distinct
  ingredient names across the mount with counts and files, bucketed as
  conformant (a name or spelling), inflection, drift (a confident different
  target) and unknown. Reports are written under `data/`, never into the mount
  and never into the repository.
- **Conform on the host (VC2).** `kerp recipes conform --apply` is a host
  command, not an application feature. It rewrites only the ingredient token
  spans of drift names, adds `{}` when a rewrite changes a single-word name to a
  multi-word one, moves stripped prep words into the note (creating it, or
  prepending to an existing one), and never commits: a person reads `git diff`
  in `cooklang-recipes` and commits. A golden-file test shows the bytes outside
  the rewritten spans are unchanged, and a second run reports no drift. The
  running application still never writes to the mount.
- **Close matches never apply themselves (VC3).** Trigram matches, however
  close, are suggestions in the `recipe` inbox kind; only an exact name or
  spelling resolves a line without a person.
- **The matching cascade (VS2; Phase 3 review, 2026-10-09).** 3C looks a name
  up in this order: an exact name or spelling; an inflection (1G's
  `singulars`); an exact standard-list entry (`standard_match.match_entry`);
  prep-word stripping ("minced garlic" → garlic with the note "minced"; form
  words such as ground, dried, powder, canned, toasted and unsalted are never
  stripped, and both lists are data files); USDA pool matches from `fdc_food`;
  then the local model, whose JSON is accepted only if it validates against the
  expected model, tested under the opt-in `llm` marker. Only the first two
  resolve a line without a person (VC3); every later tier is a proposal that
  goes to review. FoodOn identifiers and the USDA attribute table (common and
  scientific names) arrive with this work; the FoodOn licensing entry waits
  until something reads FoodOn.
- **Hierarchy (VC6; Phase 3 review, 2026-10-09).** Deferred past Phase 3.
  Ingredients stay flat; a product fulfils a line only when its ingredient,
  after `merged_into`, is the line's ingredient. Parent ingredients (cheese →
  parmesan → Parmigiano-Reggiano), and satisfying through parents, keep their
  TODOS.md item and are decided when something needs them.
- **Density choice (VS5).** Choosing one USDA density per ingredient, and
  keeping portion descriptors such as "chopped" or "packed" so a recipe note
  selects the matching conversion, waits for recipes that need it.
- **Lint.** `kerp recipes lint [--strict]` reports ingredient names that are
  neither a name nor a spelling and suggests one; it is advisory unless
  `--strict`. An optional pre-commit hook for `cooklang-recipes` is documented,
  not installed.

## Fixtures and personal data

Test recipes are synthetic `.cook` files under `backend/tests/fixtures/recipes/`,
invented dishes with invented names, never copied from the household's
repository. Tests build a temporary git repository with `dulwich`, commit
fixtures into it, and mount it read-only where the scan needs one. The household
repository is never referenced by any test or fixture, and nothing from it is
committed here; recipe titles that name people are personal data under
`SECURITY.md`.

## Out of scope for Phase 3

Prepared states, derived ingredients, metric cups, Paprika and URL importers,
nutrition, scaling recipes in the UI, editing recipe files from the application,
and anything involving shopping lists or inventory.
