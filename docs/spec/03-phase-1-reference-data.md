# 03 — Phase 1: Reference Data

Phase 1 builds everything that purchases will later point at: a running skeleton, the unit system and conversion library, the ingredient and product catalog, and vendors on a map. It ships no workflow that saves money or time by itself, so its success measure is different: at the end of Phase 1, adding a new product or a new farm stand should take well under a minute, and the conversion library should be trustworthy enough that nobody needs to think about it again.

The phase is divided into five sub-phases. 1A must come first. 1B is independent of 1C–1E and can be built in parallel. 1E depends on 1D.

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

## 1B — Units and the conversion library

Seed the `unit` table and implement `app/units/` as a pure library with no database or network access. Callers pass in the data the library needs; the service layer is responsible for loading it.

The seeded units and their exact factors to base are as follows. Mass, to grams: `mg` 0.001, `g` 1, `kg` 1000, `oz` 28.349523125, `lb` 453.59237. Volume, to millilitres, using US customary definitions: `ml` 1, `l` 1000, `tsp` 4.92892159375, `tbsp` 14.78676478125, `fl_oz` 29.5735295625, `cup` 236.5882365, `pt` 473.176473, `qt` 946.352946, `gal` 3785.411784. Count, to each: `each` 1, `dozen` 12. Each unit carries aliases covering common spellings, plurals, and abbreviations, and a parser resolves free text such as "lbs", "Tbsp", or "fluid ounces" to a unit code or reports that it cannot. The distinction between `T` and `t` as tablespoon and teaspoon is honoured; every other alias is case-insensitive. Metric cups are out of scope for now; US customary is assumed throughout.

The core function is `convert(qty, from_unit, context) -> CanonicalQty | ConversionFailure`. The context carries the ingredient's canonical unit, its density if any, its named measures, and optionally a product's pack and density override. Resolution proceeds in a fixed order. If `from_unit` is a named measure label for the ingredient, multiply by that measure's canonical quantity. If `from_unit` is `each` and a product with a pack is supplied, convert the pack quantity recursively and multiply. If `from_unit` shares a dimension with the canonical unit, apply the unit factors. If it crosses between mass and volume, use the product's density override if present, otherwise the ingredient's density, otherwise fail with `no_density`. If it is a count unit with no applicable measure or pack, fail with `unknown_measure`, or with `no_pack` when a product was supplied but has no pack. A null quantity fails with `no_qty`. There are no other outcomes, and in particular there is no default density. Two named anti-patterns from the reference projects illustrate what is forbidden: Mealie silently rewrites "ounce" to "fluid ounce" whenever the other operand is a volume, and cookcli's pantry treats any quantity under 100 g or 100 ml as "low" when no threshold was given. Neither guess may appear here in any form.

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

## Out of scope for Phase 1

Barcode lookup against external product databases, geocoding of typed addresses, drive times and routing, recipes, and anything involving purchases or prices. Address geocoding through a proxied public Nominatim is a small optional addition that may be made at the end of the phase if wanted, behind `ENABLE_NOMINATIM`, subject to the same no-outbound-by-default test as Overpass.
