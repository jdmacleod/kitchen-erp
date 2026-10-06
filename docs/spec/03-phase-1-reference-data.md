# 03 — Phase 1: Reference Data

Phase 1 builds everything that purchases will later point at: a running skeleton, the unit system and conversion library, the ingredient and product catalog, and vendors on a map. It ships no workflow that saves money or time by itself, so its success measure is different: at the end of Phase 1, adding a new product or a new farm stand should take well under a minute, and the conversion library should be trustworthy enough that nobody needs to think about it again.

The phase was divided into five sub-phases, and two more were added after it shipped: 1F (vendor interchange) and 1G (ingredient vocabulary). 1A must come first. 1B is independent of 1C–1E and can be built in parallel. 1E depends on 1D. 1F depends on 1D, and 1G on 1C.

## 1A — Foundation

Stand up the repository layout described in `CLAUDE.md`, the Compose services described in the architecture document, and the two database roles with append-only enforcement scaffolding ready for Phase 2. Implement users, cookie sessions backed by the `session` table, API tokens, the `idempotency_key` table and the header handling described in the architecture document, and a first-run flow that creates the initial admin from the command line (`kerp create-admin`). Build the application shell in the frontend: login, navigation, and an empty state for each catalog area. Provide `.env.example` with every variable documented, declare the optional recipes bind mount, adopt the logging policy from the architecture document, and make a clean checkout followed by `docker compose up` produce a working, empty system.

### Acceptance criteria

1. From a clean checkout, `cp .env.example .env && docker compose up -d` followed by `kerp migrate` and `kerp create-admin` yields a system where the admin can log in through the browser.
2. The first migration enables `postgis` and `pg_trgm`, creates `kerp_app` privileges as specified, and is fully reversible.
3. `GET /api/v1/health` reports database connectivity, migration head, model-server reachability, and ingest queue depth, and returns a healthy overall status when the model server is unreachable, noting it as degraded rather than failed.
4. An admin can create a member user and can create and revoke API tokens; a revoked token is rejected on its next use; token plaintext is shown exactly once.
5. Decimals in any API response are JSON strings, verified by a test that inspects a serialized response.
6. The test suite runs with one command inside the `api` container against a real PostgreSQL instance and passes.
7. A retried `POST` carrying the same `Idempotency-Key` and the same body replays the stored response; the same key with a different body is rejected with a 422.
8. Revoking a session makes its cookie invalid on the next request, and "log out everywhere" revokes all of a user's sessions.
9. (#75) An admin can edit a member's name, email and role, deactivate and reactivate them, and set a new password for them. Deactivating revokes the member's sessions and API tokens at once, and setting a password revokes their sessions. An admin cannot demote or deactivate themselves, so the last active admin can never be removed through the API. Any user can change their own password with the current one; that keeps the current session and revokes the others.

## 1B — Units and the conversion library

Seed the `unit` table and implement `app/units/` as a pure library with no database or network access. Callers pass in the data the library needs; the service layer is responsible for loading it.

The seeded units and their exact factors to base are as follows. Mass, to grams: `mg` 0.001, `g` 1, `kg` 1000, `oz` 28.349523125, `lb` 453.59237. Volume, to millilitres, using US customary definitions: `ml` 1, `l` 1000, `tsp` 4.92892159375, `tbsp` 14.78676478125, `fl_oz` 29.5735295625, `cup` 236.5882365, `pt` 473.176473, `qt` 946.352946, `gal` 3785.411784. Count, to each: `each` 1, `dozen` 12. Each unit carries aliases covering common spellings, plurals, and abbreviations, and a parser resolves free text such as "lbs", "Tbsp", or "fluid ounces" to a unit code or reports that it cannot. The distinction between `T` and `t` as tablespoon and teaspoon is honoured; every other alias is case-insensitive. Metric cups are out of scope for now; US customary is assumed throughout.

The core function is `convert(qty, from_unit, context) -> CanonicalQty | ConversionFailure`. The context carries the ingredient's canonical unit, its density if any, its named measures, and optionally a product's pack and density override. Resolution proceeds in a fixed order. If `from_unit` is a named measure label for the ingredient, multiply by that measure's canonical quantity. If `from_unit` is `each` and a product with a pack is supplied, convert the pack quantity recursively and multiply; when the canonical unit is a count and the pack states its pieces beside a weight or volume, one pack is that many pieces (bridge `pack_count`). A pack's pieces never turn a weight into a count: there is no piece weight. If `from_unit` shares a dimension with the canonical unit, apply the unit factors. If it crosses between mass and volume, use the product's density override if present, otherwise the ingredient's density, otherwise fail with `no_density`. If it is a count unit with no applicable measure or pack, fail with `unknown_measure`, or with `no_pack` when a product was supplied but has no pack. A null quantity fails with `no_qty`. There are no other outcomes, and in particular there is no default density. Two named anti-patterns from the reference projects illustrate what is forbidden: Mealie silently rewrites "ounce" to "fluid ounce" whenever the other operand is a volume, and cookcli's pantry treats any quantity under 100 g or 100 ml as "low" when no threshold was given. Neither guess may appear here in any form.

Every result carries provenance: which bridge was used, its source, and whether it was confirmed. This lets later phases say not only what a recipe costs but how much of that figure rests on unconfirmed estimates.

### Acceptance criteria

9. `kerp seed units` is idempotent and produces exactly the units and factors listed above, stored in an unconstrained `numeric` column so that eleven-decimal factors survive intact.
10. Property-based tests show that converting any quantity from unit A to unit B and back within one dimension returns the original to within one part in 10⁹, and that converting A→B→C equals A→C.
11. The unit parser resolves at least the aliases enumerated in its test table, distinguishes `T` from `t`, and returns a typed failure for unknown text rather than raising.
12. Table-driven tests cover every branch of the resolution order, including the precedence of a product density override over an ingredient density, and each of the four failure types.
13. `convert` performs no I/O, verified by the library having no imports from the database, HTTP, or filesystem layers.
14. Every successful result includes bridge provenance, and a result that used no bridge says so explicitly.

## 1C — Ingredients, measures, and products

Provide create, read, update, and deactivate for ingredients, their named measures, and products, through both API and UI. Deactivation rather than deletion is the rule for anything a purchase could reference.

Creating an ingredient asks for a name, a category, and a canonical unit, with the canonical unit defaulting to grams. Density, yield, and perishability are optional and may be filled in later. When the optional USDA reference table has been loaded, the ingredient form offers suggestions: searching the reference descriptions by trigram similarity and proposing a density derived from a cup or tablespoon portion weight, or named measures derived from portions like "1 clove" or "1 medium". Accepting a suggestion records `source = usda` and leaves it unconfirmed until a human confirms or replaces it. A `kerp import usda-portions --path <dir>` command loads the reference table from a local FoodData Central CSV download; the system works fully without it.

Creating a product asks for the ingredient it fulfils, with inline creation of a new ingredient from the same form, and then brand, name, pack, barcode, quality rating, and exclusive vendor, all optional except name. The product list is searchable by name, brand, ingredient, and barcode using trigram matching, and this search is exposed as a typeahead endpoint that Phase 2's review and manual-entry screens will reuse. It must return useful results within a keystroke or two for a catalog of a few thousand products.

A bridge editor on the ingredient page shows the density and every named measure with its source and confirmation state, lets the user add a measured value (for instance after weighing a cup of flour), and offers a small test bench: enter a quantity and unit, see the canonical result and the provenance, or see the typed failure. This bench is how a person builds trust in the conversion library.

### Acceptance criteria

13. An ingredient can be created with only a name and is immediately usable by a product; names are unique case-insensitively and the API returns a specific error code on collision.
14. A product can be created, along with a new ingredient, from a single form submission, and the whole operation is transactional.
15. The typeahead endpoint returns ranked matches across name, brand, ingredient name, and exact barcode, responds in under 100 ms at the 95th percentile against a seeded catalog of 5,000 products, and ranks an exact barcode match first.
16. Pack quantity and pack unit are both present or both absent, enforced in the database and reported clearly by the API.
17. With the USDA table loaded, creating an ingredient named "all-purpose flour" offers at least one density suggestion; with the table absent, the form works and offers none.
18. Accepted suggestions are stored with their source and as unconfirmed, and confirming one is a distinct, recorded action.
19. The bridge test bench returns the same result as a direct call to `convert` for the same inputs, including failures.
20. Deactivated ingredients and products disappear from typeahead but remain resolvable by identifier.

## 1D — Vendors, locations, and home bases

Provide management of home bases, vendors, and vendor locations. A home base is created by placing a pin. A vendor has a kind and a price scope. A location belongs to a vendor, sits at a place, and may have a parent location, which is how a stall belongs to a market; a stall with no hours of its own inherits the market's.

Locations can be created in three ways. The first is by dropping a pin on the map and typing a name, which is the path for roadside stands and anything OpenStreetMap does not know. The second is by coordinates supplied by a client, which is how the future capture app will create a stand from where the phone is standing. The third, available when `ENABLE_OVERPASS` is true, is adoption from OpenStreetMap: for a chosen home base and radius, the server queries Overpass for food-related shops and marketplaces, presents them as candidates, and lets the user adopt the ones they actually use. Adoption creates the vendor if needed, creates the place and location, and stores the OSM identifier, name, address, and opening hours. A later refresh action updates hours from OSM for adopted locations without overwriting fields the user has edited. The candidate query covers supermarkets, greengrocers, butchers, seafood shops, bakeries, delicatessens, cheese shops, farm shops, and marketplaces, and its results are cached so that browsing candidates does not repeat the query.

Opening hours are stored in OpenStreetMap `opening_hours` syntax, which expresses weekly schedules, exceptions, and seasonal ranges in a single string. The server validates the string on save using an established parser library and exposes an `is_open_at(location, instant)` service function and an API filter built on it. The location form offers a simple builder for the common cases (daily hours, a weekly market, a seasonal stand) that writes the syntax for the user, with the raw string available for anything unusual.

Each location defaults its home base to the nearest one by geodesic distance, which the user may override or clear.

### Acceptance criteria

21. A home base, a vendor, and a location can be created entirely by map interaction and a name, without typing an address or coordinates.
22. A stall created under a market reports the market's opening hours when it has none of its own, and its own when it does.
23. Invalid `opening_hours` strings are rejected on save with a message that identifies the problem; the strings `Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00` and `Apr-Oct Sa 08:00-13:00` are accepted and evaluate correctly for instants inside and outside their ranges, including across a daylight-saving boundary in the `America/Los_Angeles` zone.
24. With Overpass enabled, candidates within a radius of a home base are listed, adopting one creates vendor, place, and location in one transaction, and adopting the same OSM object twice is refused.
25. With Overpass disabled, which is the default, no outbound request is made by any code path, verified by a test that fails on any network access.
26. Refreshing an adopted location updates its hours from OSM but preserves a name the user has changed.
27. A new location's home base defaults to the nearest base and can be cleared.
28. Setting a vendor's price scope to `chain` is reflected in the vendor's API representation and is covered by a test that Phase 2's `offer_latest` view will rely on.

## 1E — The map

Render vendor locations and home bases on a MapLibre map backed by a PMTiles extract of Southern California served from `data/tiles/` by the `web` container. Document in the repository how to obtain or cut the extract; the file itself is not committed. If no tiles file is present the map falls back to a plain background with pins still placed correctly, and the UI says that tiles are missing, so that a missing download never blocks the rest of the system.

Pins are distinguished by vendor kind using both colour and shape. A filter narrows by kind, by home base, and by "open at", which defaults to now and accepts any date and time; it removes locations known to be closed at that instant and keeps locations whose hours are unknown, marked as such. Selecting a pin shows the location's name, vendor, hours in readable form, and whether it is open at the chosen time; in Phase 2 this panel gains price information. Stalls are reachable by selecting their market. The map is usable on a phone: pins are large enough to tap and the detail panel does not cover the whole map.

### Acceptance criteria

29. With a tiles file present, the map renders Southern California with no request to any external origin, verified in a browser test by asserting on network activity.
30. With no tiles file present, pins still render at correct relative positions and the UI states that tiles are missing.
31. Filtering by "open at" a time when a seasonal market is closed hides it, and at a time when it is open shows it.
32. Vendor kinds are distinguishable without relying on colour alone.
33. Selecting a market lists its stalls, and selecting a stall shows its inherited or own hours.
34. On a 390-pixel-wide viewport, a pin can be selected and its detail read without horizontal scrolling.
35. The map shows OpenStreetMap and Protomaps attribution whenever tiles or adopted OSM data are displayed.

## 1F — Vendor interchange and enrichment

Added 2026-09-29 after a /devex-review found the household's vendor list held little beyond names and pins: no addresses, phones, websites or OpenStreetMap links, and no store codes. Receipt matching needs those facts (#86). This section adds a file format for vendors, export and import, and a way for an outside tool to propose missing facts that a person then accepts. A separate public repository, `kitchen-erp-vendors`, holds contributor-facing store facts under the ODbL; this application imports from it and exports to it, and never pushes to it.

**Privacy comes first.** A household's list of the stores it visits is personal data even with every household field removed (SECURITY.md). So:

- **Two export modes.** `household` exports everything the household holds about its vendors, for moving between deployments or as input to a tool the household controls. `public` exports only what is safe to contribute:
  - It leaves out notes, home bases, stop overheads, the `active` flag, internal ids, and anything derived from purchases, receipts or visits: last visits, and `receipt_identifiers`, which are learned from the household's own receipts.
  - It includes only locations marked `publishable` or linked to an OpenStreetMap object, and never stands, whose pin may be someone's home.
- **The file is a contribution candidate.** A person reads the public export and opens the pull request to the public repository themselves.

**Linking to OpenStreetMap.** Adoption (1D) creates a new location, so an existing location could never gain its OSM facts. A location can now be linked to an OSM object:
- `POST /api/v1/vendor-locations/{id}/link-osm` takes `{osm_type, osm_id}` from the candidates near the location's pin, and records the id and the `osm_*` snapshots.
- A linked location refreshes like an adopted one.
- The candidate query also captures `phone`, `website`, `brand` and `brand:wikidata` from OSM tags.
- This is subject to `ENABLE_OVERPASS` and makes no request when it is off (criterion 25 still holds).

**Remembering a store code.** When a reviewer confirms a receipt's location and the header read a store identifier that the location does not have, review offers "Remember store code {code}". Accepting it appends the code to the location's `receipt_identifiers`, so the next receipt from that store matches on it. Nothing is added without that click.

**Provenance.** Vendors and locations carry `field_source`, which records for each field where its value came from:
- `source` is `osm`, `observed`, `import`, or `enriched:<tool>`;
- `ref` is an OSM object or a source URL;
- `checked_at` records when the value was checked;
- `imported` is the value an import, a refresh or an accepted suggestion last wrote.

This generalises the rule refresh already follows: a field is overwritten only while it still equals the value last written from that source, so a person's edit always wins. The `osm_*` snapshot columns stay as they are.

### The format: `kitchen-erp-vendors/1`

One schema, written as YAML or JSON; the file extension or the `Accept` / `Content-Type` header chooses. YAML is read with a safe loader only, aliases are refused, and files over 5 MB are refused. Coordinates and every other number that is not an integer are strings, and a float anywhere is rejected.

```yaml
format: kitchen-erp-vendors/1
license: ODbL-1.0
source: {name: "…", exported_at: "2026-09-29T12:00:00Z", mode: public}
vendors:
  - key: invented-mart                # stable across exports and repositories
    name: Invented Mart
    kind: chain                       # chain | independent | market | stand
    price_scope: location             # chain | location
    website: https://example.test
    brand: Invented Mart
    wikidata: Q000000
    sources: {website: {source: "enriched:tool", ref: "https://…", checked_at: "…"}}
    locations:
      - key: invented-mart/elm-st
        name: Elm St
        lat: "33.61234"
        lon: "-120.52345"
        address: "…"                         # one line, as stored and as the edit form takes it
        phone: "+1 555 0100"
        opening_hours: "Mo-Su 08:00-21:00"
        osm: {type: node, id: 123}
        parent: null                  # another location's key, for a stall
        sources: {address: {source: osm, ref: "node/123", checked_at: "…"}}
    household:                        # household mode only
      id: "…"
      notes: "…"
      active: true
      locations:
        invented-mart/elm-st: {id: "…", home_base: "Home", stop_overhead_min: 5, active: true, receipt_identifiers: ["0123"]}
```

### Export and import

- **Export.** `GET /api/v1/vendors/export?format=yaml|json&mode=public|household` and `kerp export vendors --format --mode --out`.
- **Import.** `POST /api/v1/vendors/import?dry_run=` and `kerp import vendors --from PATH [--dry-run]`.
- **Matching.** Import matches each vendor and location in this order:
  1. by key;
  2. by OSM type and id;
  3. by vendor name (case-insensitive), then a location of that vendor within 150 m whose name is similar.

  Two or more matches at one step is ambiguous: reported, never guessed.
- **Writing fields.**
  - A field is written only when it is empty or still equals `field_source.imported`. Otherwise it is a conflict: left alone and listed.
  - Household fields are written only from a household-mode file.
  - A home base is matched by its name. A name with no matching home base is reported as unresolved and the location keeps its nearest-base default; import never creates a home base, because a home base is where someone lives.
  - Nothing is deleted.
- **Report.** Import returns `{created, updated, unchanged, conflicts, unmatched}` with each field's old and new value. A dry run is the same code rolled back. A malformed file is `422 bad_export`.

### Suggestions from an outside tool

- **Scoped tokens.** An API token may be limited to scopes. A session, or a token with the scope `*` (every existing token), keeps full rights.
  - `vendors:read` may read the **public** export and nothing else. The household export and every other vendor and location route are `403` to it, because those return notes, home bases, visits and store codes. A tool addresses suggestions by the keys in the public export.
  - `vendors:suggest` may only post suggestions.
- **Posting suggestions.** `POST /api/v1/vendor-suggestions` accepts a batch of proposed field values, each with a target (vendor or location, by id or key), a field, the value it expects to replace, the proposed value, a required `source_url`, optional evidence (plain text, at most 1,000 characters), the tool's name and version, and a confidence (a decimal string).
  - Fields are a closed set: `website`, `brand`, `wikidata`, `phone`, `address`, `opening_hours`, `osm`, `name`, `price_scope`.
  - Each value is validated as the edit form validates it: opening hours by the parser, URLs as http or https, OSM type and id by their rules.
  - A duplicate of a pending suggestion is collapsed.
  - Posting never changes a vendor or location.
- **Suggestions are append-only.** A suggestion's proposal cannot be changed: the runtime role may update only its decision columns, and a trigger refuses anything else. Its status is pending, accepted, rejected or stale.
- **Review.** Pending suggestions become one inbox row, and a person accepts or rejects them per field or all at once. Accepting checks that the field still holds the value the suggestion expected; if it changed, the suggestion is stale and is applied only on an explicit override. An accepted value is written with its provenance (`enriched:<tool>`) in one transaction.
- **What a tool may send outside.** The reference enrichment tool lives in the public repository, not here. It works from the public export and sends a model only public facts: vendor and branch names, city or region, OSM ids, public coordinates and current public values, one vendor at a time. It never sends home bases, notes, purchases or receipt text, and it drops values from sources whose terms are incompatible with the ODbL.

### Receipt matching uses the new facts

The header stage already reads the printed phone and address. `match_location` scores a phone match (digits only, a country code on one side allowed) like a store identifier, but only when the number belongs to exactly one active branch of its vendor: a number several branches share adds nothing and is recorded as `phone_shared`. Address similarity (trigram, 0.4 or better) counts alongside name similarity (weight 0.6 × similarity). With no phone or address on either side, ranking is exactly what it was. Branches of a chain then stop tying, which is what #86 asks for. In review, each candidate chip shows its branch's saved address.

### Acceptance criteria

60. An existing location can be linked to an OSM object near its pin; a refresh then fills its address and hours, and a name the user changed survives. With Overpass disabled, linking is refused and no request is made.
61. Confirming a receipt's location offers to remember a store code the location lacks. Accepting it appends the code, and the next receipt printing that code is matched to that location without a click.
62. A public export contains no household field, no receipt identifier, no stand, and no location that is neither publishable nor linked to OSM, verified by a test that scans every key in the document. A household export contains the household block.
63. The same export written as YAML and as JSON parses to equal documents, with every coordinate a string.
64. Importing a file twice reports `0 created, 0 updated` the second time.
65. A field the user edited after an import is reported as a conflict by the next import and left unchanged.
66. Import matches by key before OSM id before name and proximity, and reports an ambiguous match instead of choosing.
67. A file with a float coordinate, a YAML alias, an unknown format, or over the size limit is refused with `422 bad_export`, and a dry run changes nothing.
68. A `vendors:read` token gets the public export and `403` from the household export and from every other vendor and location route; a `vendors:suggest` token can post suggestions and cannot read or change a vendor.
73. Importing a household file on a deployment without its home bases reports each unmatched home-base name, and creates none.
69. A malformed suggestion batch is `422`; posting a valid batch changes no vendor; an attempt to update a suggestion's proposal is refused by the database.
70. Accepting a suggestion writes the value and its provenance; accepting one whose field changed since it was proposed marks it stale.
71. Pending suggestions appear as one inbox row and leave the inbox once decided.
72. On synthetic receipts from a chain with three branches, the branch whose phone is printed is chosen without a click.

## 1G — Ingredient vocabulary

Added 2026-09-30 from a design-session handoff (`12-ingredient-vocabulary.md` keeps the record) and its CEO, engineering, design and outside-voice reviews. Ingredient names drift: "scallions", "green onions" and "scallion" end up as three ingredients with three price histories, and none has a USDA reference. This sub-phase gives each identity one canonical ingredient that any of its spellings reaches, a standard list to start from, and USDA references and suggestions reviewed by a person. Recipe work (extracting names from `cooklang-recipes`, conforming, linting) belongs to Phase 3 (`07`). The application never writes to the recipes repository.

**Naming rules.** These rules apply to the standard list, to review, and to anything that later proposes names:
1. Culinary word order, lowercase: "yellow onion", "ground cumin", "parmesan". Proper nouns keep their capitals (Parmigiano-Reggiano, Italian sausage).
2. The natural recipe form of the noun: singular for count nouns (egg, onion), plural only where a cook writes it (green beans, almonds). Other inflections are spellings of the same ingredient.
3. A form that changes what you buy is its own ingredient: garlic and garlic powder; whole and ground cumin; dried and canned beans; salted and unsalted butter; sesame oil and toasted sesame oil.
4. Prep belongs in a recipe's note, not the name: garlic, not minced garlic.
5. Cooking state is not identity. The USDA reference points at the raw or dry record, meaning what you buy and weigh.
6. Brands never appear in ingredient names; they belong on products.

Ingredients stay flat in 1G. A hierarchy (cheese → parmesan) is deferred (TODOS.md, Ingredients).

**Spellings.** `ingredient_alias` (`02`) holds every other spelling of an ingredient: synonyms, generated plurals, and legacy names left behind by a rename or merge. The canonical name is never a spelling row. One versioned normalizer in `app/catalog/names.py` makes the key. A plural generator writes the plural of the last word of each name (s, x, ch and sh take es; a consonant before y becomes ies; an uncountables list is left alone). A generated form that another ingredient already has is skipped and logged, never an error; a spelling a person types that another ingredient has is refused with `409 alias_taken`. `kerp ingredients check` lists skipped plurals, ingredients without a USDA reference, and USDA references absent from the loaded release.

**USDA import.** `kerp import usda --path <dir>` loads a local, unzipped FoodData Central CSV download in one transaction with one skip report, replacing the previous load. `kerp import usda-portions` stays as an alias. It checks each file's required columns first and exits naming the file and column that are missing. It records the release date. Alongside the portions table of 1C, it loads `fdc_food` for the Foundation, SR Legacy and FNDDS survey foods, with a usage count: how many FNDDS survey foods use each one as an input, found through `input_food`, `sr_legacy_food` and the FNDDS ingredient code table. USDA suggestions rank by that count, so "Onions, raw" comes before "Onions, dehydrated flakes". FoodOn identifiers and USDA attributes are not imported (Phase 3).

**The standard list.** `backend/app/catalog/standard_ingredients.yaml` is a tracked, reviewed list of common household ingredients. Each entry has a key, a lowercase name, a category from the nine display keys, a canonical unit, other spellings, an optional USDA reference, and optional named measures. The list is flat. Dried and canned beans are separate entries; lemon zest and juice are measures on lemon, and bottled lemon juice is its own entry; coffee, wine, beer and common spirits are included. Its USDA references point at raw or dry records, chosen by a person with a helper that reads `fdc_food`. USDA food group names map onto the nine category keys in `app/catalog/categories.py`. CI checks the file's structure; `kerp ingredients check` checks its references against the loaded release. The list only offers names: it seeds nothing into the catalog. It grows in reviewed tiers: Tier A holds the foods USDA's survey recipes use most (about 9 or more uses once variants are collapsed); Tier B adds the band below that, kept to things a household buys and cooks with, plus common staples the survey data underweights; Tier C adds the staples of the cuisines US households cook that the survey data barely sees (mushroom and potato varieties, Asian greens, cheeses, cooking fats, specialty flours and noodles, cuts, more spices). Produce from USDA's Seasonal Produce Guide is added too (#111). A bare word that could mean several entries ("bell pepper", "winter squash", "lettuce") is no entry's spelling, so typing it lists them all; "yam" stays a spelling of sweet potato, as US stores label it; lima beans are the fresh or frozen entry; pomegranate and corn on the cob are counted each, with pomegranate arils a note rather than a measure. Corn on the cob takes USDA's raw sweet corn record, whose portions are ears, and the spelling "sweet corn"; corn kernels reference the frozen kernels record instead, the one exception to raw or dry, because that is how loose kernels are bought. Each tier is reviewed by the household in its own pull request.

**Finding an ingredient.** One `search_ingredients` service matches ingredient names and their spellings, active ingredients only. It ranks an exact name or spelling first, then prefixes, then trigram similarity, and returns one row per ingredient. `GET /api/v1/ingredients/search?q=&include_standard=` exposes it, and the search palette uses it without standard names. With `include_standard=true`, standard-list entries the catalog doesn't have yet (no ingredient has their key as its slug) follow the catalog matches. The ingredient picker asks for them only where it may create an ingredient. `GET /api/v1/ingredients`, the paged list, is unchanged.

**Creating from the standard list.** Choosing a standard name in the picker creates nothing until the form saves. On save, `IngredientCreate.standard_key` creates the ingredient, its spellings, its USDA reference and its measures in the form's own transaction, with the standard key as its slug and `reconcile_state = linked`. A taken name returns `409 ingredient_name_taken`; an unknown key returns `422 unknown_standard_entry`. Measures from the list are stored as `source = usda` or `manual` and unconfirmed.

**Linking existing ingredients.** Ingredients created before 1G start `unreviewed`. A name written the USDA way, noun first ("butter, unsalted"), is matched with its words turned round. `kerp ingredients recheck` puts typed-in `not_applicable` ingredients that a standard entry now matches back to `unreviewed`, so the link page offers them; nothing is linked by it. A link page (`/catalog/ingredients/link`, reached from an inbox row, with no nav item) pairs each with its likely standard name and USDA food. Per row, a person can:
- **Link**: take the standard name, slug, spellings and USDA reference. A name that changes is kept as a `legacy` spelling.
- **Rename**: give the ingredient a new name. The old name is kept as a `legacy` spelling.
- **Skip**: leave it as it is. Skipped rows can be reopened.

`GET /api/v1/ingredients/link/summary` returns the counts of rows to review and skipped rows for the page and the Ingredients list.

**Merging.** When a Link or Rename would give an ingredient a name another active ingredient already has, the page offers a merge instead. The person picks which ingredient survives, defaulting to the one with more products. The merge runs in one transaction with a single commit:
1. The loser is renamed "<name> (merged into <survivor>)", then flushed, because the unique name index covers inactive rows.
2. The survivor takes the standard name and slug, then is flushed.
3. Products move to the survivor. The loser's spellings and references move, and its old name becomes a `legacy` spelling of the survivor.
4. Named measures the person ticked are copied; they are pre-ticked when the units share a dimension.
5. The loser is deactivated with `merged_into` set.
6. Every price of the survivor is renormalized in full, as a change of canonical unit requires (#102), through a flush-only normalize core.

A merge across canonical units is allowed. The confirmation names the unit change and how many prices will need a bridge, and those prices land in Needs a bridge.

**USDA suggestions.** Once an ingredient has a USDA reference, its portions become suggestions reviewed on the Needs a bridge page (`10`). `GET /api/v1/usda/review` lists ingredients with unreviewed suggestions, grouped per ingredient. Suggestions are left out when they are densities for an ingredient that already has one, measures whose label (compared lowercase) it already has, or densities for an ingredient counted in `each`. An `each` ingredient with no measures to offer never appears. `POST /api/v1/usda/review/{ingredient_id}` takes `{density_portion?, measures[], skip}`. Accepted values go through the existing density and measure write paths as `source = usda`, unconfirmed, and trigger a recompute. The request records `usda_reviewed_fdc_id`, so a reviewed ingredient leaves the list until its reference changes. There is no accept-all. If a density was set since the list was read, the request returns `409` with the current value.

**Inbox.** Two kinds join the unified inbox (`09`):
- **Link**: "n ingredients to link to the standard list", while any are `unreviewed`.
- **USDA**: "n ingredients have USDA densities to review", once no ingredient is `unreviewed` or a linked ingredient has suggestions, and only when USDA data is loaded.

**Undo.** Merges are not undone in the app. The runbook in the README says to run `kerp backup` before `kerp import usda`, before linking, and before any downgrade.

### Acceptance criteria

74. The 1G migration applies and reverses cleanly. Existing ingredients get a unique generated slug and `reconcile_state = unreviewed`; new ingredients default to `not_applicable`, and no generated slug equals a standard key.
75. The normalizer is pure and versioned, and property-based tests show it is idempotent, ignores case and accents in the key, and keeps in-word hyphens. The plural generator's table-driven tests cover each rule and the uncountables list.
76. A generated plural that another ingredient already has is skipped, logged and listed by `kerp ingredients check`; a typed spelling another ingredient has is refused with `409 alias_taken`. At most one reference per ingredient and system is preferred, enforced by the database.
77. `kerp import usda` on a synthetic FoodData Central fixture loads foods, portions and usage counts in one transaction, records the release date, reports skipped rows, and replaces an earlier load when run twice. A missing column exits non-zero naming the file and the column, and changes nothing. `kerp import usda-portions` still works, and the 1C USDA tests pass unchanged.
78. USDA food suggestions rank a food used by more FNDDS survey foods above a less-used one of similar text similarity.
79. The standard list validates in CI: unique keys and names, lowercase names except listed proper nouns, categories among the nine keys, canonical units among g, ml and each, and no spelling claimed by two entries.
80. `search_ingredients` finds an ingredient by its name and by any spelling, ranks exact above prefix above trigram, returns one row per ingredient however many spellings match, never returns an inactive ingredient, and answers in under 100 ms at the 95th percentile with 5,000 ingredients and their spellings.
81. With `include_standard=true`, a standard name appears only while no ingredient has its key as slug. Saving a product whose new ingredient came from the standard list creates the ingredient, its spellings and its USDA reference in the product's transaction; `409 ingredient_name_taken` and `422 unknown_standard_entry` leave nothing behind, and a discarded drawer creates nothing.
82. Linking an ingredient gives it the standard name, slug and reference and keeps a changed name as a `legacy` spelling; Rename does the same with a typed name; Skip and Reopen change only `reconcile_state`; the summary counts follow.
83. A merge in either survivor direction, including one where the survivor's id sorts first, moves every product, spelling and reference, keeps the loser's name as a spelling of the survivor, deactivates the loser with `merged_into`, copies only the ticked measures, and renormalizes every price of the survivor in the same single commit. A merge across canonical units sends the prices it cannot convert to Needs a bridge.
84. The USDA review list never offers a density to an ingredient that has one or is counted in `each`, nor a measure whose label it has. Accepting stores `source = usda`, unconfirmed, recomputes affected prices, and removes the ingredient from the list; a density set since the list was read returns `409` with the current value.
85. The Link inbox row appears while any ingredient is unreviewed and leaves when none is. The USDA row follows the rule above and is absent when no USDA data is loaded.
86. `kerp ingredients check` reports skipped plurals, ingredients with no USDA reference, and references absent from the loaded release, and exits zero; it makes no change.

## 1H — Product identity, kinds and listings

Added 2026-10-01 from a design-session handoff (`13-product-ingestion-and-photos.md` keeps the record) and its CEO, engineering and design reviews. A product has had one free-text `barcode`. This sub-phase gives it every code it is known by (barcodes, produce codes, a store's item numbers, the item code inside a weighed-item label), a kind that says how it is identified, and the vendor pages it is sold on. It is the foundation for product photos (1I), receipt matching by code (04, 2K) and assisted product creation (04, 2L–2N). Nothing here makes an outbound request.

**Build order.** The product sub-phases are built in this order, each landing on its own: 1H, then 1I, then 2L, then 2M, then 2K, then 2N just before the products helper. 2K moved after 2M on 2026-10-01: a count of the household's receipts found item codes on 3 of 166 lines (04, 2K).

**Identifiers.** `product_identifier` (`02`) holds one row per code:
- `gtin`: barcodes, stored as GTIN-14 with a valid check digit. UPC-A, EAN-13, EAN-8 and GTIN-14 normalize to the same value; UPC-E expands to UPC-A first. An 8-digit code that is valid both as EAN-8 and as UPC-E is read by the symbology the client reports; typed without one, the form asks "Is this EAN-8 or UPC-E?".
- `plu`, `vendor_sku`, `rw_item`: produce codes, a store's own item numbers, and the item code inside a weighed-item label. All three belong to one vendor; the same code may sit on each vendor's own product.
- `other`: a code that is none of the above, kept exactly as entered and matched exactly.

One pure module, `app/catalog/identifiers.py`, normalizes and validates codes and replaces the check-digit code in receipt resolution; `app/catalog/barcodes.py` parses weighed-item labels. Both are free of I/O and covered by table-driven and property-based tests.

**The barcode field stays.** The API keeps `barcode` and `clear_barcode` on product create, update and read, `barcode` on search hits, and `?barcode=` on the product list. They read and write the product's barcode identifier: its earliest `gtin` or `other` identifier, shown in display form (a GTIN-14 shortened to 8, 12 or 13 digits). PATCH replaces that identifier and leaves any others. The list parameter, search and the receipt rung normalize the query before matching, and a code another product holds still answers `409 barcode_taken`. An 8, 12, 13 or 14 digit code with a wrong check digit is refused with `422 invalid_gtin`; any other code is stored as `other`. The capture contract (04, 2F) does not change.

**Moving existing barcodes.** One reversible migration moves `product.barcode` into `product_identifier` and drops the column. Each moved row keeps the original string as `legacy_value` and `source = migrated_barcode`:
- valid GTIN forms become `gtin`;
- an 8-digit value valid both ways becomes `other` and is listed;
- a 4–5 digit value becomes a `plu` scoped to the product's exclusive vendor, or `other` when the product has none;
- anything else becomes `other`.

`kerp migrate --check-barcodes` prints the counts per outcome before the migration runs. The down migration restores the column byte for byte from `legacy_value`. The migration carries its own copy of the normalizer, so later changes to app code cannot change it.

**Kinds.** `product.kind` says how a product is identified:

| Kind | Shown as | For | Identified by |
|---|---|---|---|
| `branded` | Branded | A manufacturer's packaged product | GTIN |
| `private_label` | Store brand | A store's own brand | The store's item number, plus a GTIN when scanned |
| `random_weight` | Weighed | Meat, deli and cheese sold with a printed weight-and-price label | The label's item code at that store |
| `loose` | Loose | Produce sold by PLU or weight at the register, with no label | PLU at that store, or nothing |
| `unbranded_vendor` | Market stall | One stand's or market's own item | Its exclusive vendor |

The migration sets kinds from evidence: an exclusive vendor gives Market stall; a brand or a barcode gives Branded; anything else is Loose. The kind stays editable.

**Attributes.** `product.attributes` holds kind-specific details, validated by a Pydantic model chosen from the kind and the ingredient's category. Only meat and seafood have one in 1H:
- species;
- primal and cut, as free text;
- bone (bone-in or boneless);
- skin (skin-on or skinless);
- grade;
- form (whole, sliced, cubed or ground).

Other categories accept an empty object.

**Whose value wins.** `product.field_source` records where each field came from, as `vendor.field_source` does (1F). When a person accepts a proposal (04, 2L), what they chose is written. Only an answer the lookup helper sends after a proposal was decided (04, 2N) uses 1F's rule, and then a person's edit always wins.

**Vendor facts for products.** These facts sit on `vendor`:
- `platform`: which storefront software its pages run on.
- `fetch_policy`: whether anything may fetch its pages.
  - `capture_only` is the default.
  - `server_fetch` lets the lookup helper fetch them.
  - `none` turns capture off for that vendor.
- `rw_layout`: where the item code and price sit in its weighed-item labels.
- `code_position`: where its receipts print item codes.

`vendor_location.platform_store_ref` maps a storefront's own store id to one of the household's locations. The interchange format becomes `kitchen-erp-vendors/2`, which carries these fields:
- Import reads `/1` and `/2`.
- Export writes `/1` when the new fields are empty and `/2` otherwise.
- The `kitchen-erp-vendors` repository's checks learn `/2` in the same change.

**Listings.** `vendor_listing` is a vendor's page for a product. It is keyed by vendor and canonical address: the page's canonical link, with the query string, fragment and any store-scope path removed. The removed scope is kept as `store_ref`.

### Acceptance criteria

87. GTIN normalization maps every UPC-A, UPC-E, EAN-8, EAN-13 and GTIN-14 form in its test table to the same GTIN-14 and refuses a wrong check digit. An 8-digit code valid both as EAN-8 and as UPC-E needs a symbology, and without one is never read silently. Invalid-check-digit vectors in tests carry an inline `pii-scan: allow`.
88. `parse_random_weight` reads the item code and price or weight from a table of labels under each supported layout and returns exact Decimals, or `None` for a zeroed price field.
89. A `vendor_sku`, `rw_item` or `plu` identifier without a vendor, or a `gtin` or `other` identifier with one, fails a check constraint. A duplicate scheme, value and vendor fails the unique index, which treats a missing vendor as equal (`NULLS NOT DISTINCT`).
90. The barcode migration has a seeded test. It upgrades from the previous head with synthetic barcodes of every form: GTIN forms, an ambiguous 8-digit value, 4–5 digit values with and without an exclusive vendor, and other text. It asserts each outcome, then downgrades and asserts the column is byte-equal to before. `kerp migrate --check-barcodes` prints the counts and changes nothing.
91. The existing barcode tests keep their assertions; only how fixtures set barcodes may change. These are the search, product, resolution, import, ingest and capture-contract tests, plus the 100 ms typeahead test (criterion 15). The OpenAPI document does not change for `barcode` fields or parameters.
92. A product's kind is set by the migration from the rules above, and each kind's attribute model refuses an unknown species and accepts `{}` for categories without a model.
93. `kitchen-erp-vendors/1` files import unchanged. A `/2` file round-trips its new fields, and an export with no new fields writes `/1`.
94. Canonical-address normalization strips query strings, fragments and known store-scope segments, and records the scope as `store_ref`. It is tested on invented storefront addresses.

## 1I — Product photos

Added 2026-10-01 with 1H. Every product gets a main photo: the household's own, a manufacturer's, or one from a vendor page, with a placeholder when there is none. The household's own photos look consistent because the product can be lifted off its background (a cutout) and shown on the card surface in either theme. Photos can be added to existing products here; photos that start a new product arrive with proposals (04, 2L).

**Storage.**
- **Originals** are kept under `MEDIA_PATH` (default `/data/media`) at `originals/<aa>/<bb>/<sha256>.<ext>`:
  - every original is stored with all metadata, including GPS, removed;
  - it is oriented upright and converted to sRGB;
  - it is capped at 3,000 px on the long edge, at quality 90;
  - nothing else in its pixels is changed (no colour adjustments).
- **Masks** are stored the same way under `masks/`. A mask is a single-channel PNG in the photo's displayed (post-EXIF) orientation, at the uploaded size. The server applies the same rotation and resize to the mask as to the photo.
- **Derivatives** are WebP at 160, 480 and 1,200 px, plus cutout renditions with alpha (`cutout-480`, `cutout-1200`, square, 8% padding). They live under `derived/`, keyed by the photo's sha, the pipeline version and, for cutouts, the mask's sha. They are disposable: `kerp images rebuild` recreates them byte for byte for a given pipeline version, and the pipeline version changes whenever Pillow or libwebp does.
- **Masks that cover too little or too much.** A mask covering under 5% or over 95% of the photo is discarded, and the photo has no cutout.

**Processing.** An upload is stored and queued without decoding. The api reads only the header, to refuse an unreadable type (`415 unsupported_image`), a photo over 50 megapixels (`422 image_too_large`) or a mask of the wrong size (`422 mask_mismatch`). The worker checks the size again before decoding. Receipts reuse the same guard in `ingest/raster.py`, with their own limit. A row starts with status `processing`, deduplicated on the uploaded bytes (`upload_sha256`), so the same photo twice returns the same image. The worker decodes, normalizes, hashes, makes derivatives and sets the photo `active`. A photo that fails to process says so on the product page, with Retry.

**Jobs.** Product work runs in `product_job`, with each stage's output recorded in `product_stage_result` (append-only, protected like `ingest_stage_result`). The worker claims receipts first, then product jobs, then name suggestions. It commits before any model call.

**Main photo.** `select_primary` is a pure function that runs on every insert, hide, use-as-main and unset. The person's choice wins. Otherwise the order is:
1. The household's photo with a cutout.
2. The household's photo without one.
3. A manufacturer's or Open Food Facts photo.
4. A vendor-page photo.

Within that order, higher resolution comes first, then the newest. A vendor-page photo matching photos on three or more other products of the same vendor is marked as a likely stock photo and ranks last (checked when a proposal is accepted, 2L). Choosing a main photo never deletes one. The worker and the person's choice both lock the product row before choosing.

**Serving.** `GET /api/v1/media/<sha>/<variant>-<pipeline_version>[-<mask_sha>]` requires a session or token and answers `Cache-Control: private, max-age=31536000, immutable`. Because the address names its version, a change produces a new address and nothing goes stale. A missing derivative whose original exists is rebuilt; a missing original is a `404`, and the product shows its placeholder.

**Backups.** `kerp backup` copies originals and masks (not derivatives) and records their hashes; `kerp restore` verifies each one and reports mismatches. Restore re-applies the runtime role's privileges from `app/core/grants.py` (`01`); the new append-only table is registered there.

**Cutouts.** A cutout comes from a mask. The Phase 6 app will send one; until then the optional products helper (04, 2N) sends one made by a background-removal model. The helper finds photos without a cutout through `cutout` lookup requests, queued automatically because the photo never leaves the machine (04, 2N). Its licence must allow this use (u2net is Apache-2.0, BiRefNet is MIT; isnet-general-use and BRIA RMBG-2.0 are refused), and the choice is recorded in `docs/licensing.md`. Without a mask, a photo simply has no cutout.

**Adding photos.** `POST /api/v1/product-photos` with `product_id` adds up to four photos to an existing product, each with a role:
- Product;
- Front label;
- Nutrition;
- Ingredients;
- Shelf tag.

The first is an `active` product photo; the label roles never become the main photo. The product page has "Use as main photo", "Hide" and "Show hidden (n)" (10).

### Acceptance criteria

95. Uploading the same photo twice to a product returns the same image. Stored originals, masks and derivatives contain no EXIF, XMP or GPS data, checked on a generated JPEG carrying GPS tags. A generated HEIC produces every derivative.
96. Deleting `derived/` and running `kerp images rebuild` reproduces byte-identical derivatives for the current pipeline version. A media address with a different pipeline version or mask sha is a different address.
97. A generated portrait photo with EXIF orientation 6 and a mask in displayed orientation produces a square cutout with alpha and 8% padding. A mask covering 2% of the photo produces no cutout, and the photo stays usable.
98. A photo whose header declares more than 50 megapixels is refused with `422 image_too_large` before decoding. An unreadable type is refused with `415`.
99. `select_primary` matches a table of photo sets covering the person's choice, stock-photo marks and the source order; choosing and then unsetting a main photo restores the rule's choice, and concurrent choices end with one main photo.
100. Media requires a session or token, answers `private, immutable` caching, and is served under `/api/v1` with no proxy change.
101. `kerp backup` and `kerp restore` include originals and masks, and restore reports any hash mismatch. Privileges after restore equal those after migrating, `product_stage_result` included.
102. With no mask, a photo is processed without a cutout. With a mask posted by a stub helper, it gets `cutout_source = tool`.

## Out of scope for Phase 1

Barcode lookup against external product databases (the optional products helper does that outside this application; see 04, 2N), geocoding of typed addresses, drive times and routing, recipes, and anything involving purchases or prices. Address geocoding through a proxied public Nominatim is a small optional addition that may be made at the end of the phase if wanted, behind `ENABLE_NOMINATIM`, subject to the same no-outbound-by-default test as Overpass.
