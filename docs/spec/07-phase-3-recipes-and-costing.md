# 07 — Phase 3: Recipes and Costing

**Status: draft for review, 2026-09-22. Not approved for implementation until the
household accepts it; `CLAUDE.md` still names Phases 1 and 2 as the approved scope.**

Phase 3 makes the price book answer the question it exists for: what does this
dish cost, and how much of that figure can be trusted. Recipes are Cooklang files
in the `cooklang-recipes` repository that sits beside this one. The application
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
- The mount exists: `RECIPES_PATH` (host path, default `../cooklang-recipes`) is
  bound read-only at `/data/recipes` in `api` and `worker`. An absent or empty
  directory is a valid state: the recipes area shows that no repository is
  mounted and nothing else is affected.
- **Parser.** Cooklang's grammar is small. The default is an in-house parser in
  `app/recipes/cooklang/`, pure like `app/units/`, covering front matter,
  sections, `@ingredient{qty%unit}` with multi-word names, `#cookware{}`,
  `~timer{}`, comments, and the quantity forms number, fraction, range, and
  text. It is validated against the canonical test suite published by the
  Cooklang project (MIT), vendored under `backend/tests/fixtures/cooklang/`
  with its licence file. A third-party parser may replace it only if its
  licence is permissive and it passes the same suite; `cooklang-py` on PyPI
  ships no licence metadata and is not adopted until that is resolved.
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

ingredient_alias(
  id, name_norm TEXT UNIQUE, ingredient_id FK,
  confirmed_count INT, last_seen_at
)

recipe_pin(                                    -- a single-source line pinned to a product
  id, recipe_id FK, name_norm TEXT, product_id FK,
  UNIQUE (recipe_id, name_norm)
)

recipe_cost_snapshot(
  id, recipe_id FK, content_hash, head_commit?, provisional BOOLEAN,
  basis CHECK IN (latest, average, cheapest), window_days INT?, min_quality SMALLINT?,
  home_base_id FK?,                            -- prices considered are those at its locations
  consumed_cost NUMERIC(12,4)?, consumed_cost_high NUMERIC(12,4)?,
  basket_cost NUMERIC(12,4)?, basket_cost_high NUMERIC(12,4)?,
  per_serving NUMERIC(12,4)?,
  lines_total INT, lines_priced INT, lines_unpriced INT, lines_unconvertible INT,
  lines_unmapped INT, lines_negligible INT,
  unconfirmed_share NUMERIC(5,4),              -- fraction of consumed cost resting on unconfirmed bridges
  computed_at,
  UNIQUE (recipe_id, content_hash, basis, window_days, min_quality, home_base_id)
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
rather than creating new ones. `recipe_cost_snapshot` is derived and rebuildable
in the same sense as `price_norm`: `kerp recompute-costs` may truncate it.
Pins and aliases are facts a person stated and are not derived.

The unified inbox (`GET /api/v1/inbox`, `09-information-architecture.md`) gains a
`recipe` kind for unmapped recipe names, so the generalized queue is the inbox
rather than a separate `review_queue` view (UI review T5, 2026-09-25). Receipt,
identify and bridge behaviour is unchanged.

## 3A — Repository indexer

Scan `RECIPES_PATH` for `*.cook` files on a schedule (`RECIPES_SCAN_SECONDS`,
default 30) in the worker and on demand through `POST /api/v1/recipes/rescan`.
A scan lists files with their modification times, skips any file modified within
the last `RECIPES_SETTLE_SECONDS` (default 2), computes the blob hash of each
remaining file, and re-indexes only those whose hash differs from the stored
one. Dirtiness is the comparison of that hash with the blob at the same path in
the `HEAD` tree; a file absent from `HEAD` is dirty and untracked. When the
repository has no commits, every file is dirty and `head_commit` is null.

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
9. With no repository mounted, the recipes area reports that state and every
   other endpoint works.

## 3B — Cooklang parsing

Implement `app/recipes/cooklang/` as a pure library: `parse(text) -> Recipe |
ParseError`, where `Recipe` carries front matter (title, servings, tags, any
other keys preserved verbatim), sections, steps with their ingredient, cookware,
and timer references in order, and per-ingredient `raw_name`, quantity, and unit
text. Quantities parse to number, range, or text without loss; `1/2`, `1 1/2`,
`2-3`, `2 to 3`, and `some` are all covered. The unit text is passed to the 1B
unit parser; a measure label such as `clove` is kept as text for 3C.

The library is validated against the Cooklang canonical test suite, vendored
with its licence, and with property tests: parsing a rendered recipe reproduces
its structure, and every failure returns a typed `ParseError` with line and
column rather than raising.

### Acceptance criteria

10. The canonical suite passes in full; any deliberate exclusion is listed with
    a reason in the test file.
11. Quantity forms number, fraction, mixed number, range, and text each parse to
    the documented representation, exactly and in `Decimal`.
12. A syntax error reports its line and column and never raises out of `parse`.
13. `app/recipes/cooklang/` imports nothing that performs I/O, verified as for
    `app/units/`.

## 3C — Ingredient resolution and the generalized queue

Each `recipe_ingredient.raw_name` is normalized (lowercase, whitespace
collapsed, punctuation and quantity fragments removed; a versioned pure function
distinct from the receipt normalizer) and looked up in `ingredient_alias`. A hit
resolves the line with `resolution = alias`. A miss leaves it `unmatched` with
suggestions from trigram similarity against ingredient names, ranked, never
auto-applied. A person choosing an ingredient, creating one inline, or marking
the name as not an ingredient upserts the alias, so the same name resolves on
every recipe thereafter. Lines with no quantity, a text quantity, or names in a
small configurable negligible list (`salt`, `pepper`, `water` by default) are
`negligible` and never counted as incomplete.

A recipe line may pin a product: `PUT /api/v1/recipes/{id}/pins/{name_norm}`
with a product that fulfils the resolved ingredient. Pins survive re-indexing
while the name in the file is unchanged.

The queue: the inbox's `recipe` kind lists unmapped recipe names grouped by
normalized name with the recipes using them, one row per name. Its action opens
a resolution page where one decision applies to every listed instance and writes
one alias. Receipt lines keep the Phase 2 to-identify endpoints and page, and
bridges keep theirs; there is no separate review-queue endpoint or page.

### Acceptance criteria

14. A name confirmed once resolves automatically in every later recipe that uses
    it, across re-indexing.
15. Unmatched names appear in the queue grouped with their recipes; one decision
    applies to all of them and writes one alias.
16. Negligible lines are excluded from the queue and from completeness counts.
17. A pin is refused for a product that does not fulfil the line's ingredient,
    and survives a re-index that leaves the name unchanged.
18. Receipt-line identification (the inbox's `identify` kind, and the Phase 2
    to-identify endpoints and page) behaves exactly as the Phase 2 tests require;
    those tests run unchanged.

## 3D — Costing

A snapshot is computed for a recipe under a stated basis: `latest` (each line's
most recent qualifying normalized price), `average` over `window_days`, or
`cheapest` qualifying. Qualifying means a non-voided observation with a
successful normalization, from an active location, of a product that fulfils the
line's ingredient with at least `min_quality` when given, restricted to the
locations of a home base when one is given, and to the pinned product when the
line has a pin. Stale prices are used and reported, never silently preferred.

Open for this document's approval: the UI documents (08–11) were adopted
without deciding whether costing is household-wide or restricted to a home base
by default (UI review T8, 2026-09-25). This section's optional home-base filter
stands until the Phase 3 review settles that.

Each line's quantity converts to the ingredient's canonical unit through
`convert`, using the pinned or chosen product's context where one exists. Yield
applies by the heuristic in `05`: count and named-measure quantities are
as-purchased; mass and volume quantities are edible portion and are grossed up
by dividing by `yield_pct`; `yield_mode` overrides per line. Consumed cost is
canonical quantity times unit price. Basket cost rounds each line up to whole
packs of the product used and multiplies by that product's pack price; a product
without a pack contributes its consumed cost. Ranges produce a low and a high
for both figures. Per-serving divides consumed cost by `servings` when numeric.

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

## 3E — Recipe screens

A recipes list with title, servings, dirty and status badges, the latest
consumed and basket cost with completeness, and filters for status and
completeness. A recipe page shows the file's rendered structure beside a cost
table: each line with its raw text, resolved ingredient (with the typeahead to
change it, which writes an alias), pin, converted quantity and provenance,
price used with its age and location, and line cost; totals with low and high;
completeness and unconfirmed share; a basis selector and home-base selector; a
history of committed snapshots as a chart with provisional points marked. Parse
errors show the message and line. Relink proposals appear on `missing` recipes.
Unmapped recipe names reach the person through the Home inbox; the
to-identify page stays for receipt lines.

### Acceptance criteria

28. Resolving an unmapped line from the recipe page writes the alias and
    updates the cost without a reload.
29. Switching basis or home base changes the figures and shows which prices
    were used.
30. A dirty recipe shows the marker on the list and page, and its snapshot is
    labelled provisional.
31. A ten-line recipe can be fully resolved and pinned from the keyboard.
32. On a 390-pixel viewport the cost table reads without horizontal scrolling.

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
