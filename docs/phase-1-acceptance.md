# Phase 1 acceptance audit

Each criterion from `docs/spec/03-phase-1-reference-data.md` and the test or
procedure that demonstrates it. Backend tests live under `backend/tests/`,
browser tests under `e2e/tests/`. "Manual" means verified by running the
Compose stack and observing, with the command shown.

| # | Criterion (short) | Evidence |
|---|---|---|
| 1 | Clean checkout, `docker compose up`, migrate, create-admin, log in through the browser | Manual on 2026-09-21; automated in `e2e/tests/login.spec.ts` on desktop and 390 px profiles; the CI `e2e` job repeats it from a clean checkout |
| 2 | First migration enables postgis and pg_trgm, sets `kerp_app` privileges, reversible | `test_db_roles.py::test_extensions_enabled`, `::test_app_role_cannot_create_tables`, `::test_app_role_has_dml_on_ordinary_tables`; `test_migrations.py::test_downgrade_and_upgrade_round_trip` |
| 3 | Health reports database, migration head, model server, queue depth; healthy when the model server is unreachable | `test_health.py` (both tests; model server unreachable in the harness) |
| 4 | Admin creates a member, creates and revokes tokens; revoked token rejected; plaintext shown once | `test_users_tokens.py::test_admin_creates_member_and_member_cannot_manage_users`, `::test_token_lifecycle` |
| 5 | Decimals in responses are JSON strings | `test_decimals.py` (schema and serialized-response tests) |
| 6 | One-command suite inside the `api` container against real PostgreSQL | `docker compose exec api pytest`, run on 2026-09-21: 184 passed |
| 7 | Same `Idempotency-Key` and body replays; different body is 422 | `test_idempotency.py`; also `test_catalog_ingredients.py::test_idempotent_create`, `test_geo_locations.py::test_create_honours_idempotency_key` |
| 8 | Session revocation and "log out everywhere" | `test_auth.py::test_logout_revokes_session`, `::test_logout_everywhere` |
| 9 | `kerp seed units` idempotent, exact factors, unconstrained numeric | `test_units_seed.py::test_seed_is_idempotent_and_exact` (checks `tsp` = 4.92892159375 survives) |
| 10 | Round trips within 1e-9; A→B→C equals A→C | `test_units_properties.py` (Hypothesis, 400 examples each) |
| 11 | Parser alias table, `T` vs `t`, typed failure | `test_units_parse.py` |
| 12 | Every branch of the resolution order and all four failures | `test_units_convert.py` |
| 13 | `convert` performs no I/O | `test_units_purity.py` (AST scan of `app/units/`) |
| 14 | Provenance on every result; "no bridge" stated | `test_units_convert.py::test_same_dimension_uses_factor_and_no_bridge` and the measure/density/pack tests |
| 15 | Ingredient created with only a name; case-insensitive uniqueness with a specific code | `test_catalog_ingredients.py::test_create_with_only_a_name_and_use_it`, `::test_name_unique_case_insensitively_with_specific_code` |
| 16 | Product plus new ingredient in one transactional submission | `test_catalog_products.py::test_inline_ingredient_creation_is_transactional` |
| 17 | Typeahead ranks across fields, exact barcode first, p95 < 100 ms at 5,000 products | `test_catalog_search.py` (both tests; p95 measured through the HTTP layer) |
| 18 | Pack qty and unit both or neither, enforced in the database and reported by the API | `test_catalog_products.py::test_pack_pair_enforced_by_api_and_database` |
| 19 | USDA loaded: "all-purpose flour" offers a density; absent: form works, no suggestions | `test_catalog_usda.py::test_import_and_suggest`, `::test_without_table_no_suggestions` |
| 20 | Accepted suggestions stored with source, unconfirmed; confirming is a distinct action | `test_catalog_usda.py::test_accepted_suggestion_is_unconfirmed_until_confirmed`, `test_catalog_ingredients.py::test_density_set_then_confirmed_as_distinct_action`, `::test_measures_crud_and_confirm` |
| 21 | Bench equals a direct `convert` call, including failures | `test_catalog_bench.py::test_bench_matches_direct_convert` |
| 22 | Deactivated items leave the typeahead but resolve by id | `test_catalog_products.py::test_update_clear_and_deactivate`, `test_catalog_ingredients.py::test_deactivated_hidden_from_list_but_resolvable` |
| 23 (1D-21) | Home base, vendor, location from pins and names only | `test_geo_locations.py::test_home_base_vendor_and_location_from_pins_and_names`; browser: `e2e/tests/map.spec.ts` (pin drop, inline vendor, name only) |
| 24 (1D-22) | Stall inherits market hours unless it has its own | `test_geo_locations.py::test_stall_inherits_market_hours_unless_it_has_its_own` |
| 25 (1D-23) | Invalid hours rejected with a locating message; the two spec strings evaluate correctly across DST in America/Los_Angeles | `test_geo_opening_hours.py` (all tests, including `::test_is_open_endpoint_across_dst`) |
| 26 (1D-24) | Overpass candidates, adoption in one transaction, double adoption refused | `test_geo_osm.py::test_adopt_creates_vendor_place_location_and_refuses_twice`, `::test_candidates_query_user_agent_and_cache` |
| 27 (1D-25) | Overpass off by default; no outbound request on any path, proven by a test that fails on network access | `test_geo_osm.py::test_disabled_by_default_and_no_code_path_touches_the_network`, `::test_guard_blocks_dns_and_direct_connections` |
| 28 (1D-26) | Refresh updates hours from OSM but keeps a user-changed name | `test_geo_osm.py::test_refresh_updates_hours_but_keeps_user_edits` |
| 29 (1D-27) | New location defaults to the nearest home base; can be cleared | `test_geo_locations.py::test_home_base_defaults_to_nearest_and_can_be_cleared` |
| 30 (1D-28) | `price_scope = chain` reflected in the API and covered by a test | `test_geo_vendors.py::test_price_scope_chain_is_reflected_everywhere` |
| 31 (1E-29) | Tiles present: map renders with no external request | `e2e/tests/map.spec.ts` asserts zero external requests through `watchExternalRequests`; the basemap style drops every symbol layer so no glyph or sprite CDN is ever contacted. Run locally with an extract in `data/tiles/` to exercise the tiles-present path; CI runs the no-tiles path |
| 32 (1E-30) | No tiles: pins at correct positions and a "tiles missing" notice | `e2e/tests/map.spec.ts`; `frontend/src/test/map.test.tsx` (markers carry the location's coordinates) |
| 33 (1E-31) | "Open at" hides a closed seasonal market and shows it when open | `e2e/tests/map.spec.ts` (seasonal stall hidden in January, shown on a July Saturday); API level in `test_geo_locations.py::test_open_at_filter_and_is_open_flag`. Locations with unknown hours stay listed |
| 34 (1E-32) | Kinds distinguishable without colour alone | `frontend/src/components/geo/MapView.tsx`: square, circle, diamond, triangle, and a house glyph per kind, plus colour; asserted in `frontend/src/test/map.test.tsx` |
| 35 (1E-33) | Market lists stalls; stall shows inherited or own hours | `frontend/src/test/map.test.tsx` (market → stalls → inherited hours); `e2e/tests/map.spec.ts` |
| 36 (1E-34) | 390 px: pin selected and detail read without horizontal scroll | `e2e/tests/map.spec.ts` in the `phone` project asserts `scrollWidth <= innerWidth` with the detail sheet open |
| 37 (1E-35) | OSM and Protomaps attribution shown | `e2e/tests/map.spec.ts` (attribution present with tiles absent) |

Numbering note: the spec numbers 1A–1B criteria 1–14 after the review added two
criteria to 1A, then restarts at 13 for 1C and continues to 35; the second column
gives the spec's own number where it differs.

All Phase 1 criteria pass as of 2026-09-21: backend 184 tests, frontend 38, browser 16.

## Known gaps carried into Phase 2

- Vendor and location list endpoints return `{items}` without cursor pagination.
  A household has tens of locations, not thousands; add pagination if the
  planner in Phase 4 ever needs it.
- Nominatim geocoding was optional at the end of Phase 1 and was not built.
- The basemap is unlabelled: label layers need glyphs, and serving them from a CDN would break the no-external-requests rule. Self-hosted glyphs under `frontend/public/` would restore labels later.
- Stalls share their market's pin and are reached through the market's panel.
