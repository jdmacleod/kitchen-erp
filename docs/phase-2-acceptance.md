# Phase 2 acceptance audit

Each criterion from `docs/spec/04-phase-2-purchases-and-price-book.md` and the
test or procedure that demonstrates it. Backend tests live under `backend/tests/`,
frontend unit tests under `frontend/src/test/`, browser tests under `e2e/tests/`.

| # | Criterion (short) | Evidence |
|---|---|---|
| 1 | Append-only tables refuse UPDATE/DELETE for `kerp_app` on privilege and for `kerp_owner` on the trigger | `test_append_only.py` (all three tables, both roles) |
| 2 | One `each` of a packaged product normalizes to price ÷ pack canonical quantity under the stated rounding rule | `test_pricebook.py::test_packaged_each_normalizes_exactly_under_the_rounding_rule` |
| 3 | Unnormalizable observation stored intact with a failure status | `test_pricebook.py::test_unnormalizable_observation_is_stored_with_failure_status` |
| 4 | Density change recomputes exactly the dependents | `test_pricebook.py::test_density_change_recomputes_exactly_the_dependents` (same-dimension row's `computed_at` untouched) |
| 5 | Truncate and `kerp recompute-norms` reproduces the table (all columns but `computed_at`) | `test_pricebook.py::test_truncate_and_recompute_reproduces_all_but_computed_at` |
| 6 | Voided observation absent from all views, present for audit | `test_pricebook.py::test_void_hides_from_views_but_keeps_audit` |
| 7 | Chain scope fans out to every active location; location scope does not | `test_pricebook.py::test_chain_scope_fans_out_and_location_scope_does_not` |
| 8 | Excluding promotions falls back to the latest regular price | `test_pricebook.py::test_promo_exclusion_falls_back_to_latest_regular` (`offer_latest_regular`) |
| 9 | Shelf price in one screen with inline product creation | `frontend/src/test/shelf-price.test.tsx`; API in `test_capture_contract.py` |
| 9a | Second live observation for a line rejected; allowed after a void | `test_resolution.py::test_reopen_and_repoint_one_line_voids_and_reemits_only_that_line` and the trigger in migration 0005 |
| 10 | Three-line manual purchase, keyboard only | `frontend/src/test/purchase-entry.test.tsx` (userEvent keyboard); API in `test_purchases_manual.py` |
| 11 | Phone width: each line enterable without the keyboard obscuring the field | Vertical per-line layout below 640 px with `scrollIntoView`; `e2e/tests` phone project covers the shell, not this screen specifically |
| 12 | Unit price ↔ line total computed in decimal arithmetic, computed field marked | `frontend/src/test/decimal.test.ts`, `purchase-entry.test.tsx`; server side `test_purchases_manual.py::test_computed_fields_are_decimal_and_rounded_half_even` |
| 13 | Exactly one observation per item line, linked by `purchase_line_id` | `test_purchases_manual.py::test_three_line_purchase_commits_and_emits_one_observation_per_line` |
| 14 | Reopen and change a price: old observation voided with a system reason, new one emitted, untouched lines keep theirs | `test_purchases_manual.py::test_reopen_voids_and_reemits_only_changed_lines` |
| 15 | Inline product and ingredient creation keeps entered lines | `frontend/src/test/purchase-entry.test.tsx` |
| 16–25 | Receipt ingest pipeline | Pending: `tests/test_ingest_*.py` (being finished) |
| 26 | Normalization table, idempotence, version recorded | `test_normalize.py`; version in the resolve stage output (`resolve_purchase` returns `normalize_version`) |
| 27 | Confirmed alias resolves; `confirmed_count = 0` does not | `test_resolution.py::test_confirmed_alias_resolves_and_unconfirmed_only_suggests`, `::test_unconfirmed_alias_does_not_auto_resolve` |
| 28 | Fuzzy and model rungs only suggest | `test_resolution.py::test_fuzzy_alias_and_llm_only_suggest_and_llm_outside_shortlist_is_rejected` |
| 29 | Model answer outside the shortlist rejected | same test (rogue ranker) |
| 30 | Accepting, choosing, ignoring upsert the alias; next receipt resolves automatically | `test_resolution.py` (alias tests, queue test, ignored test) |
| 31 | Alias-resolved outlier flagged `price_outlier` | `test_resolution.py::test_price_outlier_flag_on_alias_resolution`; review UI draws attention to flagged lines (`review.test.tsx`) |
| 32 | Commit with unresolved lines; observations only for resolved lines; queue lists the rest | `test_resolution.py::test_commit_with_unresolved_lines_queues_them_and_identify_applies_to_all` |
| 33 | Identifying a queued line emits an observation dated to the purchase and applies to siblings | same test |
| 34 | Ignored lines emit nothing, leave the queue, and stay ignored | `test_resolution.py::test_ignored_lines_emit_nothing_and_stay_ignored` |
| 35 | Attached discount reduces price and sets promo; deposit does not | `test_resolution.py::test_attached_discount_and_deposit_affect_the_observation_correctly` |
| 36 | Ten-line review without a pointing device | `frontend/src/test/review.test.tsx` (j/k, Enter, /, i, c); `e2e/tests/review.spec.ts` keyboard-only against the stack |
| 37 | Reopen a receipt purchase, re-point one line: only that observation voided and re-emitted, alias updated | `test_resolution.py::test_reopen_and_repoint_one_line_voids_and_reemits_only_that_line` |
| 38 | Product history across three locations of two vendors, promo marked | `test_pricebook_views.py::test_product_history_series_and_promo_marks`; chart in `frontend/src/test/price-book.test.tsx` |
| 39 | Min-quality filter excludes lower-rated products | `test_pricebook_views.py::test_ingredient_offers_min_quality_filter` |
| 40 | Matrix highlights cheapest, leaves unknown cells empty | `test_pricebook_views.py::test_compare_matrix_highlights_cheapest_and_leaves_unknown_empty`; `compare.test.tsx` |
| 41 | Staleness by perishability; matrix can exclude | `test_pricebook_views.py::test_stale_marking_and_exclusion` |
| 42 | Cheapest layer in a consistent unit, respecting chain scope | `test_pricebook_views.py::test_cheapest_layer_respects_chain_scope`; map tests |
| 43 | Needs-a-bridge list empties after the bridge is added and the price appears | `test_pricebook_views.py::test_needs_bridge_clears_and_price_appears_in_compare` |
| 44 | Comparison views p95 < 500 ms at 20,000 observations | `test_pricebook_views.py::test_comparison_views_p95_under_500ms_with_20000_observations` |
| 45 | Every contract endpoint authenticates with a bearer token and rejects a revoked one | `test_capture_contract.py` |
| 46 | Retried POST with the same key returns the original and creates nothing | `test_capture_contract.py::test_bearer_only_flow_with_retries` (locations, products, observations, purchases); receipts pending 2C |
| 47 | `near=` ordering with distance | `test_geo_locations.py::test_list_near_orders_by_distance_with_decimal_strings` |
| 48 | Checked-in OpenAPI document matches the running app | `test_capture_contract.py::test_openapi_document_matches_the_application`; regenerate with `kerp export-openapi` |
| 48a–48d | Export import: purchases per transaction, barcode resolution, queue, idempotence, exact amounts, `realdata` skip | `test_importer.py` |
| 49 | Backup and restore reproduce rows and images by count and hash | `test_backup.py` (runs where `pg_dump` exists: the api container) |
| 50 | Restore refuses a non-empty database unless forced | same test |

## Notes

- Rounding rule: 28-digit `Decimal` context, unit price quantized to six places, `ROUND_HALF_EVEN`, in `app/services/pricebook.py`.
- Manual-entry unit default is `each` for packaged products (one pack), not the pack unit, so a 5 lb bag's price is never recorded as a per-pound price.
- The "open at" and staleness thresholds are settings (`STALE_DAYS_*`), defaulting to 14/45/120 days.
