# 07 — Phase 3: Recipes and Costing

**Status: approved 2026-10-09 (Phase 3 review); built 2026-10-10. Amendments
from the review and notes from the build are marked inline; the acceptance audit
is `docs/phase-3-acceptance.md`.**

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
  ones the application reads are kept verbatim in `recipe.front_matter`. A line
  starting with `>` is a note (amended 2026-11-04): consecutive `>` lines join
  into one note, kept as plain text, never read for components and never
  numbered; `kerp recipes rescan --reparse` reads unchanged files again after a
  change like this one. It is
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
  posted_consumed_cost NUMERIC(12,4)?, posted_consumed_cost_high NUMERIC(12,4)?,  -- #523 (0070)
  posted_basket_cost NUMERIC(12,4)?, posted_basket_cost_high NUMERIC(12,4)?,
  posted_share NUMERIC(5,4) DEFAULT 0,         -- posted consumed cost / (counted + posted)
  lines_posted INT DEFAULT 0,                  -- not in lines_priced
  costing_version INT DEFAULT 0,               -- recipe_costing.COSTING_VERSION that computed it
  computed_at,
  UNIQUE (recipe_id, content_hash, basis, window_days, min_quality)
)

recipe_cost_line(                              -- one per recipe_ingredient at snapshot time
  id, snapshot_id FK, recipe_ingredient_id FK,
  status CHECK IN (priced, posted, unpriced, unconvertible, unmapped, negligible),
  canonical_qty NUMERIC?, canonical_qty_high NUMERIC?,   -- _high columns: Phase 3 build, 2026-10-10
  canonical_unit?, yield_applied NUMERIC(5,4)?,
  product_id FK?, observation_id FK?, norm_unit_price NUMERIC(14,6)?,
  consumed_cost NUMERIC(12,4)?, consumed_cost_high NUMERIC(12,4)?,
  basket_cost NUMERIC(12,4)?, basket_cost_high NUMERIC(12,4)?,
  packs NUMERIC?, packs_high NUMERIC?,
  bridge_kind?, bridge_confirmed BOOLEAN?, failure_code?
)
```

A range quantity gives a low and a high per line as well as per snapshot, so
`recipe_cost_line` carries `canonical_qty_high`, `consumed_cost_high`,
`basket_cost_high` and `packs_high`, null for a single number (Phase 3 build,
2026-10-10).

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
2026-10-09). The scan serializes on a `pg_advisory_xact_lock`, so a rescan
requested while the worker's scan is running waits for it rather than
colliding with it (Phase 3 build, 2026-10-10). A scan lists files with their modification times, skips any file
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

A Cooklang recipe reference, `@./basics/simple-syrup{1%fl oz}` (the spec's
`@./path{qty%unit}`), is an ingredient line whose `ref_path` is the path as
written and whose name is the path's base name (#526, rulings S1–S4,
2026-11-17). The braceless form stays text. Its row has resolution `recipe` and
no ingredient, so it never waits in the resolve queue, the vocabulary report
leaves it out, and a shopping list skips it as made at home. After each scan
every reference is linked by path: the target is the written path relative to
the referring file's directory, normalized, with `.cook` added, matched against
a recipe that isn't missing. A path that leaves the recipes root names nothing.
Links follow paths only: a moved target unlinks its referrers until the
referring file is edited, and a recipe created at the old path links them
again. `kerp recipes rescan` prints each missing target; `kerp recipes lint`
reports missing targets, paths outside the root, cycles of references, and a
`yield:` that doesn't parse on a referenced recipe (`--strict` fails on them).

Front matter `yield:` says what one batch makes (ruling S1): an amount above
zero and a mass or volume unit, `yield: 500 ml`, read with the unit parser into
`recipe.makes_qty` and `makes_unit`. The text as written is kept in
`makes_text`, so a count (`yield: 12 pieces`), a range or a word indexes as text
only and is reported only where a reference needs it. Code and the UI call it
"makes", so it never reads as an ingredient's trim yield. After the upgrade
that added these (migration 0071), `kerp recipes rescan --reparse` re-reads
unchanged files.

References within one file follow the cooklang-rs `&` extensions (issue 367):

- `@&name{}` (or `@&name`) is an ingredient named earlier in the file, matched
  without regard to case or spacing. It has no `recipe_ingredient` row.
- `@&name{qty%unit}` is more of that ingredient. It keeps a row of its own, so
  the amount adds to the first.
- `@&(~N)name{}` is the output of the step N back, and `@&(N)name{}` the output
  of step N. Both count steps within the current section only.
- `@&(=N)name{}` is the output of section N, and `@&(=~N)name{}` the output of
  the section N back. Sections are counted from 1 as the recipe page shows them.

A step or section reference has no row, so the resolver, the to-resolve queue
and costing never see it. The recipe body carries it as a `reference` item
pointing at its section and step, and the recipe page links its name there.
A reference whose target doesn't come before it is a `ParseError` at the
reference. So is a malformed `(…)`. `kerp recipes lint` suggests the `@&` form
in two cases:

- an ingredient line names an earlier section;
- an ingredient line repeats an earlier ingredient in a measure that doesn't
  convert and differs from the first one's (`@noodles{1%portion}` after
  `@noodles{1%lb}`).

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
1G amendments below give the tiers' rules. The model tier runs only on an
explicit per-name request, `POST /api/v1/recipes/resolve/ask` ("Ask the model"
on a resolve row), never while listing the queue or in the typeahead, because
one local-model call per queued name would hold the page for minutes (Phase 3
build, 2026-10-10).

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
normalization, from a committed purchase or a shelf price, from an active
location, of a product that fulfils the line's ingredient with at least
`min_quality` when given, and of the pinned product when the line has a pin. A
price older than `stale_after_days` is used and reported stale on its line,
never filtered out and never silently preferred.

A posted (listing) price is the last resort (ruling on #523, 2026-11-17, P1–P3).
A line that no real price prices (and, under the per-kitchen `offers` basis, no
offer either: an offer first, then real prices, then posted) takes the latest
posted price of its ingredient, whatever the basis; `window_days` does not
apply to it. It qualifies as a real price does but for its source: scoped to
the kitchen in a kitchen's view, of at least `min_quality`, and under a pin of
the pinned product only (a pin with no posted price of its own leaves the line
unpriced). An old one is used and marked stale. The line's status is `posted`;
it keeps its observation, product, quantities and costs as a priced line does.
Posted lines are never counted: `consumed_cost`, `basket_cost`, `per_serving`,
`unconfirmed_share` and the history leave them out, and `lines_priced` does not
count them, so a recipe resting on one is not complete. The snapshot carries
their twins, `posted_consumed_cost` (and `posted_consumed_cost_high`) and
`posted_basket_cost` (and its high), with `lines_posted` and `posted_share`,
the posted consumed cost over the counted plus posted, from the low ends. An API
client that wants counted figures only reads `consumed_cost`, `basket_cost`
and `per_serving`; the posted fields are separate. Costing runs in two passes:
real prices first, then one posted query for the ingredients of the lines left
unpriced.

An unpriced line says why when a price is in sight: another product has a
posted price (a pinned line, "Only {product} has a posted price; change the pin
to use it"), the only posted price is at a store outside this kitchen (in a
kitchen's view: active stores and products, any kitchen, any quality), or the
pinned product now sits under another ingredient (#525).

### Lines costed from a recipe

A line costs from another recipe in two cases (#526, rulings S2–S4): a recipe
reference, and (route b) a line on an ingredient whose `made_by_recipe_id`
names the recipe that makes it, when no real or posted product price qualifies
and the line ended unpriced, or unconvertible only at the canonical conversion.
A product price always wins.

Cycles are found first, as strongly connected components of the link graph
(referrer to referenced recipe, and referrer to the made-by recipe of a line's
ingredient), before any snapshot is read: a line whose source is in the
referrer's own component is `cycle`. Recursion only leaves a component, so it
ends; there is no depth limit.

The source is costed under the referrer's basis, window and quality floor: its
snapshot of the current `costing_version`, or one computed on demand in the
referrer's transaction. A kitchen's view, and the `offers` basis, cost it
unsaved under `latest` with the same kitchen, and the line says so
(`source_basis`). The line's quantity is converted to the source's makes unit
(a reference within one dimension; route b also through the ingredient's
density and measures) and `factor = qty / makes_qty`, never quantized; the
line's cost is factor times the source's consumed cost, quantized once, and its
basket cost is the same, since a batch made for many uses is not charged whole.
A source with any posted part makes the line `posted`, at factor times counted
plus posted. A source that is incomplete marks the line `source_partial`;
`lines_partial` counts them and a recipe with any is not complete.

Every line routed to a recipe records `source_recipe_id`, failures included.
The failure codes:

- <a id="failure-no-yield"></a>`no_yield` (unconvertible): the source has no `yield:`.
- <a id="failure-bad-yield"></a>`bad_yield` (unconvertible): its `yield:` isn't an amount above zero and a mass or volume unit.
- <a id="failure-yield-unit"></a>`yield_unit` (unconvertible): a reference line's unit can't reach the yield's (route b keeps the conversion's own code, such as `no_density`).
- <a id="failure-no-recipe"></a>`no_recipe` (unmapped): no recipe at the path, a path outside the root, or a made-by recipe whose file is gone.
- <a id="failure-cycle"></a>`cycle` (unpriced): the source costs from the referrer, directly or through others.
- <a id="failure-recipe-unpriced"></a>`recipe_unpriced` (unpriced): the source has no counted or posted cost.
- <a id="failure-recipe-unreadable"></a>`recipe_unreadable` (unpriced): the source's file doesn't parse.

Every trigger recomputes, with the recipes it reaches, every recipe that costs
from one of them, transitively and children first. `COSTING_VERSION` 2 marks
this.

Costing is household-wide: every qualifying price counts, whichever kitchen's
vendors recorded it (Phase 3 review, 2026-10-09, settling UI review T8 of
2026-09-25). Phase 4 adds a per-kitchen filter with the kitchen switcher; until
then no snapshot carries a kitchen.

Each line's quantity converts to the ingredient's canonical unit through
`convert`, using the pinned or chosen product's context where one exists. An
unpinned line never counts in packs, so its failure for want of a pack is
reported as `unknown_measure`: the fix is the weight of one piece, not the
product's pack size (amended 2026-11-04). An unconvertible line whose ingredient
has a preferred USDA reference carries the one USDA offer that would convert it
(`usda_offer`), taken through the USDA review's write path with
`mark_reviewed: false`. Yield
applies by the heuristic in `05`: count and named-measure quantities are
as-purchased; mass and volume quantities are edible portion and are grossed up
by dividing by `yield_pct`; `yield_mode` overrides per line. `yield_pct` has
been on `ingredient` since 1G; 3D gives it a field on the ingredient form that
offers the USDA refuse value when the ingredient has a reference, and an
ingredient without one costs at 100% with the cost line saying so (Phase 3
review, 2026-10-09). The refuse value is `fdc_food.refuse_pct`, which
`kerp import usda` reads from `food_component.csv` (`12` §4); `GET
/ingredients/{id}/usda` returns the preferred USDA food with it and the yield
it implies, `1 − refuse / 100`. The form shows "USDA: X% refuse → yield Y%"
under the yield field with a "Use Y%" button that puts Y in the field; it never
fills the field by itself, and nothing changes until the person saves. It shows
nothing when the ingredient has no USDA food in the loaded release or USDA
gives that food no refuse figure, and says the yield matches when it already
does (decision 2026-10-16, issue 282). Consumed cost is canonical quantity times unit price.
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

Each snapshot records the `recipe_costing.COSTING_VERSION` that computed it
(version 1: posted prices, #523). `kerp migrate` recomputes, in one
transaction, the snapshots of each recipe's current content whose version
differs, as it does for a new `CONVERT_VERSION`; history snapshots of earlier
content are facts of their time and stay as they were, so a second migrate
recomputes nothing. Deploying a costing change is `make up` (which migrates);
nothing truncates the cost tables. Migration 0070's downgrade turns posted
lines back into unpriced ones and adds `lines_posted` to `lines_unpriced`, so
every kept snapshot still adds up; after a downgrade, run `kerp
recompute-costs` if the older figures are wanted at once.

Deploy runbook for a costing change: run the read-only measuring script (ids
and counts only) for a before dump, `make up`, then the script again, and post
counts only on the issues.

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
27a. A price from an uncommitted purchase, a voided price and a price at an
    inactive location never cost a line; a price older than
    `stale_after_days` does, and its line reports it stale. A posted price
    costs a line only as the last resort, marked `posted`, never in the
    counted totals or history (amended by the ruling on #523, 2026-11-17).
27b. A recipe reference and a made-by ingredient cost a line from the source
    recipe's counted cost scaled by what it makes; an incomplete source makes
    the referrer incomplete; a cycle is `cycle` even with snapshots stored; a
    price change on the source's ingredient recomputes the source, then the
    referrer (#526).

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
the ingredient, is built here (Phase 3 review, 2026-10-09); the recipes list
has no ingredient filter, so the card's "All {n} recipes" expands the card in
place rather than opening the list (Phase 3 build, 2026-10-10; `10`).

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
  spelling resolves a line without a person. A similar or USDA suggestion is
  offered only when it shares a word naming a food with the recipe name: a
  shared colour, size, freshness or style word (`describing_words.txt`), prep
  word or part word ("leaves") is not enough, so "golden syrup glaze" is not
  offered golden beets. A name left with no suggestion shows only "Ask the
  model" and the search (amended 2026-11-14, #516).
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
  scientific names) were to arrive with this work; they did not (Phase 3
  build, 2026-10-10): the USDA import loads no attribute table, and both move
  to TODOS.md as one item ("USDA attribute names and FoodOn identifiers for
  the usda tier"), with the FoodOn licensing entry still waiting until
  something reads FoodOn. The usda tier searches Foundation and SR Legacy
  foods only, as `kerp ingredients usda-candidates` does (Phase 3 build,
  2026-10-10).
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
