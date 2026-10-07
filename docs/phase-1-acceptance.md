# Phase 1 acceptance audit

Each criterion from `docs/spec/03-phase-1-reference-data.md` and the test or
procedure that demonstrates it. Backend tests live under `backend/tests/`,
browser tests under `e2e/tests/`. "Manual" means verified by running the
Compose stack and observing, with the command shown.

| # | Criterion (short) | Evidence |
|---|---|---|
| 1 | Clean checkout, `docker compose up`, migrate, create-admin, log in through the browser | Manual on 2026-09-21; automated in `e2e/tests/login.spec.ts` on the desktop, 390 px, and 375 px profiles; the CI `e2e` job repeats it from a clean checkout |
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
| 23 (1D-21) | Kitchen, vendor, location from pins and names only | `test_geo_locations.py::test_home_base_vendor_and_location_from_pins_and_names`; browser: `e2e/tests/map.spec.ts` (pin drop, inline vendor, name only) |
| 24 (1D-22) | Stall inherits market hours unless it has its own | `test_geo_locations.py::test_stall_inherits_market_hours_unless_it_has_its_own` |
| 25 (1D-23) | Invalid hours rejected with a locating message; the two spec strings evaluate correctly across DST in America/Los_Angeles | `test_geo_opening_hours.py` (all tests, including `::test_is_open_endpoint_across_dst`) |
| 26 (1D-24) | Overpass candidates, adoption in one transaction, double adoption refused | `test_geo_osm.py::test_adopt_creates_vendor_place_location_and_refuses_twice`, `::test_candidates_query_user_agent_and_cache` |
| 27 (1D-25) | Overpass off by default; no outbound request on any path, proven by a test that fails on network access | `test_geo_osm.py::test_disabled_by_default_and_no_code_path_touches_the_network`, `::test_guard_blocks_dns_and_direct_connections` |
| 28 (1D-26) | Refresh updates hours from OSM but keeps a user-changed name | `test_geo_osm.py::test_refresh_updates_hours_but_keeps_user_edits` |
| 29 (1D-27) | New location defaults to the nearest kitchen; can be cleared | `test_geo_locations.py::test_home_base_defaults_to_nearest_and_can_be_cleared` |
| 30 (1D-28) | `price_scope = chain` reflected in the API and covered by a test | `test_geo_vendors.py::test_price_scope_chain_is_reflected_everywhere` |
| 31 (1E-29) | Tiles present: map renders with no external request | `e2e/tests/map.spec.ts` asserts zero external requests through `watchExternalRequests`; the basemap style drops every symbol layer so no glyph or sprite CDN is ever contacted. Run locally with an extract in `data/tiles/` to exercise the tiles-present path; CI runs the no-tiles path |
| 32 (1E-30) | No tiles: pins at correct positions and a "tiles missing" notice | `e2e/tests/map.spec.ts`; `frontend/src/test/map.test.tsx` (markers carry the location's coordinates) |
| 33 (1E-31) | "Open at" hides a closed seasonal market and shows it when open | `e2e/tests/map.spec.ts` (seasonal stall hidden in January, shown on a July Saturday); API level in `test_geo_locations.py::test_open_at_filter_and_is_open_flag`. Locations with unknown hours stay listed |
| 34 (1E-32) | Kinds distinguishable without colour alone | `frontend/src/components/geo/MapView.tsx`: square, circle, diamond, triangle, and a house glyph per kind, plus colour; asserted in `frontend/src/test/map.test.tsx` |
| 35 (1E-33) | Market lists stalls; stall shows inherited or own hours | `frontend/src/test/map.test.tsx` (market → stalls → inherited hours); `e2e/tests/map.spec.ts` |
| 36 (1E-34) | 390 px: pin selected and detail read without horizontal scroll | `e2e/tests/map.spec.ts` in the `phone` project asserts `scrollWidth <= innerWidth` with the detail sheet open; `e2e/tests/narrow-phone.spec.ts` repeats the no-sideways-scroll check for `/map` in the `phone-375` project, measured against the project's declared device width because `innerWidth` inflates when an overflowing page makes the browser zoom out |
| 37 (1E-35) | OSM and Protomaps attribution shown | `e2e/tests/map.spec.ts` (attribution present with tiles absent) |
| 60 (1F) | An existing location links to an OSM place near its pin; refresh fills what changed and keeps a person's edits; refused with no request while Overpass is off | `test_geo_osm_link.py::test_link_fills_empty_fields_keeps_edits_and_records_sources`, `::test_linking_is_refused_with_no_request_while_overpass_is_off`, `::test_refresh_overwrites_keeps_or_fills_by_state` |
| 61 (1F) | Review offers a store code the location lacks; remembering it makes the next receipt printing it match without a click | `test_store_code_offer.py::test_remembered_code_matches_the_next_receipt`, `::test_codes_that_do_not_read_like_a_store_number_are_not_offered`; `frontend/src/test/review-store-code.test.tsx` |
| 62 (1F) | A public export holds no household field, store code, stand, or location neither publishable nor linked; a household export holds the household block | `test_vendor_export.py::test_public_export_carries_no_household_data` (scans every key), `::test_household_export_carries_the_household_block`, `::test_linked_locations_are_public_and_inactive_ones_are_not` |
| 63 (1F) | YAML and JSON exports parse to equal documents with every coordinate a string | `test_vendor_export.py::test_yaml_and_json_are_the_same_document` |
| 64 (1F) | Importing a file twice reports 0 created, 0 updated the second time | `test_vendor_import.py::test_apply_then_the_same_file_again_changes_nothing`, `::test_an_exported_household_file_imports_unchanged` |
| 65 (1F) | A field edited after an import is a conflict next time, left unchanged | `test_vendor_import.py::test_a_field_edited_after_import_is_a_conflict` |
| 66 (1F) | Key before OSM id before name within 150 m; ambiguity reported, not guessed | `test_vendor_import.py::test_matching_order_key_then_osm_then_name_nearby`, `::test_two_nearby_candidates_are_reported_not_guessed` |
| 67 (1F) | Float, alias, unknown format or oversized file is 422 `bad_export`; a dry run changes nothing | `test_vendor_import.py::test_a_malformed_file_is_refused_before_any_write`, `::test_yaml_aliases_and_oversized_files_are_refused`, `::test_dry_run_reports_and_writes_nothing`; `e2e/tests/vendor-import.spec.ts` |
| 73 (1F) | Unknown kitchen names are reported and none is created | `test_vendor_import.py::test_household_fields_only_from_a_household_file` |
| 68 (1F) | A `vendors:read` token gets the public export and 403 elsewhere; a `vendors:suggest` token posts suggestions and cannot read or change a vendor | `test_scopes.py::test_scoped_tokens_reach_only_their_routes` (walks every route), `::test_read_token_gets_the_public_export_only`, `::test_sessions_and_full_tokens_are_never_refused_for_scope`, `::test_a_token_made_before_scopes_keeps_full_access`; `test_vendor_suggestions.py::test_a_suggest_token_cannot_read_or_change_vendors` |
| 69 (1F) | A malformed batch is 422; a valid batch changes no vendor; a proposal cannot be edited | `test_vendor_suggestions.py::test_a_malformed_batch_stores_nothing`, `::test_posting_changes_no_vendor_and_collapses_repeats`, `::test_proposals_cannot_be_edited_or_deleted` |
| 70 (1F) | Accepting writes the value and its provenance; a field changed since proposed goes stale | `test_vendor_suggestions.py::test_accept_writes_the_value_and_its_source`, `::test_a_field_changed_since_proposed_goes_stale`; `frontend/src/test/suggestion-review.test.tsx` |
| 71 (1F) | Pending suggestions are one inbox row, gone once decided | `test_vendor_suggestions.py::test_accept_writes_the_value_and_its_source` |
| 72 (1F) | On synthetic receipts from a chain with three branches, the branch whose phone is printed is chosen without a click | `test_location_matching.py::test_the_branch_whose_phone_is_printed_is_chosen`, `::test_a_number_several_branches_share_decides_nothing`, `::test_a_matching_address_picks_the_branch`, `::test_without_phones_or_addresses_ranking_is_unchanged` (frozen-copy property test) |

| 74 (1G) | The migration reverses; existing ingredients get a unique generated slug and start unreviewed; no generated slug equals a standard key | `test_migrations.py::test_downgrade_and_upgrade_round_trip`, `test_ingredient_vocabulary.py::test_migration_marks_existing_ingredients_unreviewed`, `::test_new_ingredient_gets_a_local_slug_and_no_review`, `test_catalog_names.py::test_generated_slugs_can_never_be_standard_keys` |
| 75 (1G) | The normalizer is pure, versioned, idempotent, case- and accent-blind, keeps in-word hyphens; the plural rules are table-tested | `test_catalog_names.py` (Hypothesis properties and example tables) |
| 76 (1G) | A taken generated plural is skipped and listed; a typed spelling another ingredient has is 409 `alias_taken`; one preferred reference per system | `test_ingredient_vocabulary.py::test_a_plural_another_ingredient_has_is_skipped_not_an_error`, `::test_a_typed_spelling_another_ingredient_has_is_refused`, `::test_one_preferred_reference_per_system` |
| 77 (1G) | `kerp import usda` loads foods, portions, usage and the release in one transaction; a missing column names file and column and changes nothing; the old command and the 1C tests still work | `test_usda_import.py`, `test_catalog_usda.py` (unchanged) |
| 78 (1G) | USDA suggestions rank a more-used food above an equally similar one | `test_usda_import.py::test_suggestions_rank_the_more_used_food_first` |
| 79 (1G) | The standard list validates structurally in CI | `test_standard_ingredients.py` |
| 80 (1G) | Search by name or spelling, exact > prefix > trigram, one row per ingredient, never inactive, p95 < 100 ms at 5,000 plus spellings | `test_ingredient_search.py` |
| 81 (1G) | Standard names appear only while not in the catalog; creating from one is transactional; 409/422 leave nothing; a discarded drawer creates nothing | `test_ingredient_search.py::test_standard_names_follow_and_hide_once_in_the_catalog`, `::test_product_create_from_the_standard_list`, `::test_taken_name_and_unknown_key_leave_nothing_behind`; `frontend/src/test/ingredient-picker.test.tsx` |
| 82 (1G) | Link, Rename, Skip and Reopen; replaced names stay as legacy spellings; summary counts follow | `test_ingredient_link.py`; `frontend/src/test/ingredient-link.test.tsx` |
| 83 (1G) | Merge in either survivor direction moves products, spellings and references, deactivates the loser, copies ticked measures and renormalizes in one commit; cross-unit prices land in Needs a bridge | `test_ingredient_link.py::test_merge_moves_everything_in_one_commit` (both directions), `::test_merge_copies_only_ticked_measures_when_units_agree` |
| 84 (1G) | USDA review offers only what an ingredient lacks, never a density to an `each` one; accepting stores unconfirmed USDA values and recomputes; a race is 409 with the current value | `test_usda_review.py`; `frontend/src/test/usda-review.test.tsx` |
| 85 (1G) | The Link and USDA inbox rows appear and leave as specified; no USDA row without USDA data | `test_ingredient_link.py::test_the_inbox_row_appears_and_leaves`, `test_usda_review.py::test_the_inbox_row_waits_for_linking_unless_a_linked_ingredient_has_suggestions`, `::test_without_usda_data_nothing_is_offered` |
| 86 (1G) | `kerp ingredients check` reports and changes nothing | `test_ingredient_vocabulary.py::test_check_command_prints_the_report_and_changes_nothing`, `test_usda_import.py::test_check_lists_references_absent_from_the_release`, `::test_check_lists_standard_references_absent_from_the_release` |

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
