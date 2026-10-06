# 02 — Data Model, Phases 1–2

This document defines the tables, constraints, and views that Phases 1 and 2 create. Column lists are normative for names, nullability, and meaning; choose sensible numeric precisions where none is given. Every table has a UUID `id` primary key unless stated otherwise, and mutable tables carry `created_at` and `updated_at`, which are omitted below for brevity.

The central idea is a three-way separation. An ingredient is what a recipe asks for. A product is a specific thing you can buy that fulfils an ingredient, and it is where brand, pack size, and perceived quality live. A price observation is a product at a vendor location at a moment in time. Recipes will point at ingredients, optionally pinning a product; purchases point at products; comparison happens at the ingredient level, filtered by quality. Collapsing any two of these three is the mistake this model exists to avoid.

## Phase 1 tables

### Identity

```
app_user(id, email UNIQUE, display_name, password_hash, role CHECK IN (admin, member), active)
api_token(id, user_id FK, name, token_hash UNIQUE, last_used_at?, revoked_at?,
          scopes TEXT[] DEFAULT '{*}')      -- 1F: '*' is full rights; vendors:read is the public export only; vendors:suggest posts suggestions only;
                                    -- 2N: products:read reads the lookup queue only; products:suggest posts answers only
session(id, user_id FK, expires_at, last_seen_at, revoked_at?)
idempotency_key(id, user_id FK, key, request_hash, status_code, response_body JSONB, created_at, UNIQUE (user_id, key))
```

### Units and bridges

```
unit(
  code TEXT PK,                      -- mg, g, kg, oz, lb, ml, l, tsp, tbsp, fl_oz, cup, pt, qt, gal, each, dozen
  dimension CHECK IN (mass, volume, count),
  to_base_factor NUMERIC,            -- unconstrained numeric: several factors have eleven decimals; multiply to reach g, ml, or each
  system CHECK IN (us, metric, any),
  aliases TEXT[]                     -- "pound", "lbs", "tablespoon", "T", ...
)
```

Conversions within a dimension are physical constants and live entirely in `to_base_factor`. There is no pairwise conversion table. Crossing dimensions requires a bridge, and bridges are per ingredient.

```
ingredient(
  id, name (unique on lower(name)), category,
  canonical_unit FK unit CHECK IN (g, ml, each),
  density_g_per_ml NUMERIC(10,5)?, density_source?, density_confirmed BOOLEAN DEFAULT false,
  yield_pct NUMERIC(5,4) DEFAULT 1 CHECK (0 < yield_pct <= 1),
  perishability CHECK IN (shelf_stable, refrigerated, fresh),
  notes?
)

ingredient_measure(
  id, ingredient_id FK, label TEXT,  -- clove, head, bunch, sprig, stick, can, each
  canonical_qty NUMERIC,             -- in the ingredient's canonical unit
  source CHECK IN (usda, label, measured, llm, manual),
  confirmed BOOLEAN,
  UNIQUE (ingredient_id, lower(label))
)
```

`density_source` takes the same values as `ingredient_measure.source`, and `density_confirmed` plays the role that `confirmed` plays on a measure: an accepted suggestion or a newly entered value is unconfirmed until a person confirms it as a distinct action. The same pair exists on `product` for the density override. `yield_pct` and `perishability` are not used until Phase 3 and Phase 4 respectively but are cheap to capture while an ingredient is being created, and both default sensibly.

### Ingredient vocabulary (1G)

Sub-phase 1G (`03`) gives each ingredient one canonical identity that any spelling reaches. It extends `ingredient` rather than adding a second vocabulary table.

```
ingredient(
  ...,                                -- the columns above
  slug TEXT UNIQUE,                   -- the standard key when created from, or linked to, the standard list;
                                      -- otherwise generated, and never equal to a standard key
  reconcile_state CHECK IN (unreviewed, linked, skipped, not_applicable) DEFAULT not_applicable,  -- indexed
  usda_reviewed_fdc_id INT?,          -- the FDC food whose suggestions a person last reviewed
  merged_into FK ingredient?          -- set on the loser of a merge, which is also deactivated
)

ingredient_alias(
  id, name_norm TEXT UNIQUE,          -- other spellings only; the canonical name is never a row here
  ingredient_id FK,
  kind CHECK IN (synonym, inflection, legacy),
  source TEXT,                        -- standard, generated, rename, merge, manual; Phase 3 adds recipe
  confirmed_count INT DEFAULT 0, last_seen_at?
)                                     -- trigram GIN index on name_norm

ingredient_ref(
  id, ingredient_id FK,
  system CHECK IN (fdc),              -- Phase 3 may add foodon
  external_id TEXT, is_preferred BOOLEAN,
  UNIQUE (system, external_id, ingredient_id)
)                                     -- one preferred per (ingredient_id, system), a partial unique index
```

Existing rows migrate to `unreviewed` with a generated slug; rows created later start `linked` when created from the standard list, `unreviewed` when typed in with a name a standard entry matches, and `not_applicable` otherwise (#189). `name_norm` comes from one versioned pure normalizer in `app/catalog/names.py`: NFKC, lowercase, accents stripped in the key only, punctuation removed except hyphens inside words, quantity fragments removed, and no stemming. Plurals are explicit `inflection` rows made by a small generator, so "grass" never becomes "gras". The unique index on `lower(name)` still covers inactive rows, so a merge first renames its loser to "<name> (merged into <survivor>)".

`ingredient_alias` is the same table Phase 3 uses for recipe names (`07`); 1G creates it.

The standard list is a tracked file, `backend/app/catalog/standard_ingredients.yaml`, not a table. An ingredient created from it records the entry's key as its slug, which is how the list knows which entries the catalog already has.

### Products

```
product(
  id, ingredient_id FK,
  brand?, name,
  pack_qty NUMERIC?, pack_unit FK unit?,     -- both null means sold by variable weight or loose
  pack_count INTEGER?, piece_name?,           -- the pieces a mass or volume pack holds (19 oz, 5 links);
                                              -- pack_qty stays the total ("6 x 330 ml" is 1980 ml, 6 pieces)
  kind CHECK IN (branded, private_label, random_weight, loose, unbranded_vendor),   -- 1H
  attributes JSONB DEFAULT '{}',             -- 1H: validated per kind and category (meat and seafood only so far)
  primary_image_id FK product_image? DEFERRABLE INITIALLY DEFERRED,   -- 1I: chosen by select_primary
  field_source JSONB DEFAULT '{}',           -- 1H: as on vendor (1F)
  quality_rating SMALLINT? CHECK (1..5),
  exclusive_vendor_id FK vendor?,            -- single-source items and vendor-specific produce
  density_override NUMERIC(10,5)?, density_override_source?, density_override_confirmed BOOLEAN DEFAULT false,
  active BOOLEAN DEFAULT true, notes?,
  merged_into FK product?,                   -- set on the duplicate of a product merge (#179, 0028)
  CHECK ((pack_qty IS NULL) = (pack_unit IS NULL)),
  CHECK ((density_override IS NULL) = (density_override_source IS NULL)),
  CHECK (merged_into IS NULL OR (NOT active AND merged_into <> id))
)
```

Merging a duplicate product into the one to keep (#179) never touches its price observations, which are facts. The duplicate becomes inactive with `merged_into` naming the survivor, and the price views report its observations under the survivor, normalized against the survivor's pack and density. Its identifiers, vendor listings, photos, receipt aliases, receipt lines, pending update proposals and open lookups are re-pointed to the survivor in the same transaction; the survivor's own fields are not changed. Merges stay one level deep: merging A into B re-points anything already merged into A, and a merged product can't be a survivor or be reactivated.

A brandless product tied to a vendor is how unbranded produce keeps its identity: the strawberries from one stand are a different product from a supermarket's, with their own quality rating and price history, while both fulfil the ingredient strawberries. The density override exists because density sometimes varies by brand enough to matter, kosher salt being the standard example.

`product.barcode` was moved into `product_identifier` by 1H and dropped; the API keeps a `barcode` field that reads and writes the product's earliest `gtin` or `other` identifier (03, 1H).

### Product identity and listings (1H)

```
product_identifier(
  id, product_id FK,
  scheme CHECK IN (gtin, plu, vendor_sku, rw_item, other),
  value TEXT,                                -- gtin: GTIN-14; others as printed, trimmed
  vendor_id FK vendor?,                      -- required for plu, vendor_sku, rw_item; null for gtin, other
  source CHECK IN (barcode_scan, listing, manufacturer, manual, receipt, migrated_barcode),
  legacy_value TEXT?,                        -- migrated_barcode only: the original product.barcode string
  created_at,
  UNIQUE NULLS NOT DISTINCT (scheme, value, vendor_id),
  CHECK ((scheme IN ('plu', 'vendor_sku', 'rw_item')) = (vendor_id IS NOT NULL))
)

vendor_listing(
  id, vendor_id FK, product_id FK?,          -- null until a proposal is accepted
  canonical_url, vendor_sku?, store_ref?, title,
  last_captured_at,
  status CHECK IN (active, gone, ignored) DEFAULT active,
  UNIQUE (vendor_id, canonical_url)
)
```

A code belongs to one product per vendor, or to one product overall for `gtin` and `other`. `vendor_listing` is a vendor's page for a product; `store_ref` keeps the store scope stripped from its address.

### Product photos (1I)

```
product_image(
  id, product_id FK?, proposal_id FK product_proposal?,   -- a candidate has a proposal, not yet a product
  vendor_id FK vendor?,                      -- from the capture's listing; scopes the stock-photo check
  upload_sha256,                             -- of the uploaded bytes; a repeat upload returns this row
  sha256?,                                   -- of the stored original; null while processing
  source_kind CHECK IN (user_photo, manufacturer, open_food_facts, vendor_listing),
  source_url?, attribution?,
  role CHECK IN (product, label_front, label_nutrition, label_ingredients, shelf_tag) DEFAULT product,
  status CHECK IN (processing, candidate, active, hidden, failed),
  width INT?, height INT?, phash BIGINT?,    -- null while processing
  mask_sha256?, cutout_source CHECK IN (device, tool)?,
  is_stock_suspect BOOLEAN DEFAULT false, pinned BOOLEAN DEFAULT false, pinned_at?,
  ocr_text?, captured_at?, created_at,
  UNIQUE (product_id, upload_sha256), UNIQUE (proposal_id, upload_sha256)
)

product_job(
  id, kind CHECK IN (extract, resolve, image_process, identify),
  product_capture_id FK?, product_image_id FK?,
  status CHECK IN (pending, running, done, failed), attempts INT,
  last_error?, locked_at?, locked_by?
)

product_stage_result(                        -- append-only
  id, job_id FK product_job, stage, adapter, adapter_version,
  output JSONB, duration_ms, created_at
)
```

Files live under `MEDIA_PATH` (`01`): originals and masks are kept and backed up; derivatives are rebuildable. `product_stage_result` is protected like `ingest_stage_result` and is listed in `app/core/grants.py`.

### Geography and vendors

```
place(id, geom GEOGRAPHY(Point,4326), label)

home_base(id, name UNIQUE, place_id FK)

vendor(
  id, name (unique on lower(name)),
  kind CHECK IN (chain, independent, market, stand),
  price_scope CHECK IN (chain, location) DEFAULT location,
  website?, notes?,
  slug UNIQUE?,                              -- 1F: the stable key in kitchen-erp-vendors files
  brand?, wikidata?,                         -- 1F
  field_source JSONB DEFAULT '{}',           -- 1F: {field: {source, ref, checked_at, imported}}
  platform?,                                 -- 1H: storefront software, chooses an adapter
  fetch_policy CHECK IN (server_fetch, capture_only, none) DEFAULT capture_only,   -- 1H
  rw_layout JSONB?,                          -- 1H: item and price positions in weighed-item labels
  code_position JSONB?                       -- 1H: where receipts print item codes (04, 2K)
)

vendor_location(
  id, vendor_id FK, place_id FK,
  home_base_id FK?,                          -- defaults to nearest; nullable for in-between sites
  parent_location_id FK vendor_location?,    -- a stall inside a market
  name, address?,
  osm_type CHECK IN (node, way, relation)?, osm_id BIGINT?,   -- UNIQUE together when set
  osm_name?, osm_address?, osm_opening_hours?,  -- the values OSM last gave; refresh overwrites only while unchanged
  opening_hours TEXT?,                       -- OSM opening_hours syntax; seasonality included
  stop_overhead_min SMALLINT?,               -- used by the Phase 4 planner
  receipt_identifiers TEXT[],                -- store numbers or address fragments as printed on receipts
  active BOOLEAN DEFAULT true,
  key UNIQUE?,                               -- 1F: "<vendor slug>/<location slug>"
  phone?,                                    -- 1F: as printed or published; matched on its digits
  publishable BOOLEAN DEFAULT false,         -- 1F: may appear in a public export
  field_source JSONB DEFAULT '{}',           -- 1F
  platform_store_ref?,                       -- 1H: the storefront's own store id; UNIQUE with vendor_id when set
  CHECK (parent_location_id IS DISTINCT FROM id)
)

vendor_suggestion(                           -- 1F; append-only apart from its decision
  id, batch_id,
  target CHECK IN (vendor, location),
  vendor_id FK?, vendor_location_id FK?,     -- exactly one, matching target
  field CHECK IN (website, brand, wikidata, phone, address, opening_hours, osm, name, price_scope),
  old_value JSONB?, proposed_value JSONB,
  source_url, evidence?,                     -- evidence is plain text, at most 1,000 characters
  tool, tool_version, confidence NUMERIC(4,3)?,
  created_by_token_id FK api_token?, created_at,
  status CHECK IN (pending, accepted, rejected, stale) DEFAULT pending,
  decided_by FK app_user?, decided_at?
)
```

`place` is a shared node type so that Phase 4 can store drive times between any two places without caring whether an endpoint is a home or a shop. `price_scope` records whether a vendor prices uniformly across its locations; when it is `chain`, an observation at any location stands in for all of them. A stall's `opening_hours` may be null, in which case it inherits its parent's. `receipt_identifiers` lets header parsing pin a receipt to the right location of a chain from the store number printed on it.

`field_source` (1F) says where each field's value came from and what that source last wrote. An import, an OSM refresh or an accepted suggestion overwrites a field only while it still equals `imported`, so a person's edit always wins. `vendor_suggestion` rows hold what an outside tool proposed. The runtime role may update only `status`, `decided_by` and `decided_at`, and a trigger refuses any other update and every delete, as for the append-only price tables.

### Optional reference data

```
fdc_release(id, release_date DATE, imported_at, source_dir TEXT)   -- one row per import; the latest is current

fdc_food(
  fdc_id INT PK, data_type TEXT,      -- foundation_food, sr_legacy_food, survey_fndds_food
  description TEXT, category TEXT?,
  fndds_uses INT DEFAULT 0            -- times the food is an input to an FNDDS survey food
)                                     -- trigram GIN index on description

ref_usda_portion(id, fdc_id, food_description, portion_label, portion_amount, portion_unit TEXT,
                 gram_weight, data_type)
```

fdc_branded(                          -- 2L, opt-in: kerp import usda --branded
  gtin TEXT PK,                       -- GTIN-14; the latest release's row per code
  fdc_id INT, brand?, description, category?, package_size?, release_date DATE
)

Loaded by `kerp import usda` from a local, unzipped USDA FoodData Central download, in one transaction that replaces the previous load. They are used only to suggest standard-list links, densities and measures; nothing reads them at costing time, and the system works fully without them. `ref_usda_portion` keeps its 1C shape; `fdc_food` and `fndds_uses` let suggestions rank by how often USDA's survey recipes use a food.

## Phase 2 tables

### Receipts and ingest

```
receipt_document(
  id, sha256 UNIQUE, image_path, mime, bytes,
  captured_at?, capture_geo GEOGRAPHY(Point,4326)?,
  client_ocr_text?,                          -- supplied by a capture client, never edited
  uploaded_by FK app_user
)

ingest_job(
  id, receipt_document_id FK UNIQUE,
  stage CHECK IN (captured, ocr, header, lines, resolve, review, committed),
  status CHECK IN (pending, running, needs_review, done, failed, discarded),   -- discarded: the receipt was removed (#74)
  attempts INT, last_error?, locked_at?, locked_by?,
  purchase_id FK?
)

ingest_stage_result(                          -- append-only
  id, job_id FK, stage, adapter, adapter_version,
  output JSONB, duration_ms, created_at
)
```

`receipt_document` is immutable after insert. Re-running a stage appends a new `ingest_stage_result`; the latest result per stage is the effective one.

### Purchases

```
purchase(
  id, vendor_location_id FK?,                 -- null only while a receipt's location is unconfirmed
  receipt_document_id FK?,
  purchased_at, subtotal?, tax?, total,
  status CHECK IN (draft, reviewed, committed, voided),   -- voided: removed after reaching the price book (#74)
  source CHECK IN (receipt, manual, import),
  entered_by FK app_user,
  voided_at?, voided_by FK app_user?,         -- set together, only when status = voided
  flags TEXT[],                               -- purchase-level review hints such as reconcile_mismatch
  ledger_txn_ref TEXT?,                       -- opaque external reference; no integration
  CHECK (status = 'draft' OR vendor_location_id IS NOT NULL)
)

purchase_line(
  id, purchase_id FK, seq INT,
  raw_text?,                                  -- exactly as printed; null for manual entry
  line_kind CHECK IN (item, discount, tax, deposit, fee),
  product_id FK?, parent_line_id FK purchase_line?,   -- discounts and deposits attach to an item
  qty NUMERIC?, unit FK unit?, unit_price NUMERIC?, line_total NUMERIC,
  resolution CHECK IN (barcode, identifier, alias, fuzzy, llm, similar, manual, unmatched, ignored),   -- the rung whose answer was used; 2K writes identifier
  resolved_by FK app_user?,                   -- the person who confirmed it; null only for barcode, identifier and alias
  resolution_confidence NUMERIC?, flags TEXT[],
  removed_at?, removed_by FK app_user?,       -- a recorded line taken off its purchase (#72); set together
  CHECK (line_kind = 'item' OR product_id IS NULL)
)

receipt_alias(
  id, vendor_id FK, raw_text_norm TEXT,
  disposition CHECK IN (product, ignore),
  product_id FK?, confirmed_count INT, last_seen_at,
  UNIQUE (vendor_id, raw_text_norm),
  CHECK ((disposition = 'product') = (product_id IS NOT NULL))
)
```

A line with `removed_at` set is kept only because an observation still points at it as provenance (observations are append-only). Every reader of a purchase's lines skips it: totals, reconcile, review, the edit form, the inbox and the to-identify queue. Backup keeps it. A line that never produced an observation is deleted outright rather than marked removed. Line numbers (`seq`) stay unique across removed lines too, so a number always names one line (#72, D8).

A purchase is removed by deleting it when none of its lines ever produced an observation, and by setting `status = voided` (and voiding its live observations) otherwise; see 04, 2H.

`resolution = ignored` and `disposition = ignore` handle the paper towels and batteries that share a receipt with the groceries: once told, the system stops asking. `flags` carries review hints such as `price_outlier` or `reconcile_mismatch`. Aliases are keyed by vendor, not location, because a chain abbreviates consistently across its stores. A trigram index on `raw_text_norm` supports fuzzy matching.

### The price book

```
price_observation(                            -- append-only
  id, product_id FK, vendor_location_id FK,
  purchase_line_id FK?,                       -- at most one non-voided observation per line, enforced by a BEFORE INSERT trigger
  observed_at,
  price NUMERIC(12,4),                        -- amount paid or posted for qty × unit, after attached discounts
  qty NUMERIC, unit FK unit,                  -- "1 each" means one pack of the product
  is_promo BOOLEAN,
  source CHECK IN (receipt, manual, shelf, import, listing),   -- listing: a posted web price (04, 2L)
  listing_id FK vendor_listing?,              -- set exactly when source = listing
  entered_by FK app_user, created_at
)

price_observation_void(                       -- append-only
  id, observation_id FK UNIQUE, reason, voided_by FK app_user, voided_at
)

price_norm(                                   -- derived; rebuildable
  observation_id PK FK,
  canonical_qty NUMERIC?, norm_unit FK unit?, norm_unit_price NUMERIC(14,6)?,
  status CHECK IN (ok, no_density, unknown_measure, no_pack),
  bridge_kind CHECK IN (none, density, density_override, measure, pack),
  bridge_source?, bridge_confirmed BOOLEAN?,   -- provenance copied from the conversion result
  convert_version TEXT,
  computed_at
)
```

An observation records only what was seen: this much money for this quantity in this unit. It never stores a normalized price, because normalization depends on densities and measures that improve over time. `price_norm` holds the normalized figure and is recomputed whenever a bridge that affects it changes; the provenance columns make "every observation that depended on this density" a query. A unique constraint on `purchase_line_id` is deliberately absent, because a voided observation stays in the table and the re-emitted one shares its line; the trigger enforces uniqueness among non-voided rows instead. Deposits and tax are never part of `price`. This supersedes the earlier sketch in which the normalized price sat on the observation itself.

### Product ingestion (2L–2N)

```
product_capture(                              -- append-only except purging payload.dom_text after a decision
  id, sha256,                                 -- of the canonical payload without its capture time
  channel CHECK IN (clip, paste_url, barcode, photo, helper),
  source_url?, payload JSONB,                 -- structured data kept as raw text; page text up to 200 KB
  captured_at, capture_geo GEOGRAPHY(Point,4326)?, created_by FK app_user
)

product_proposal(
  id, capture_id FK product_capture?,         -- null for a Product update opened by a late helper answer
  kind CHECK IN (new_product, product_update),
  product_id FK?,                             -- the product a Product update is for
  status CHECK IN (pending, accepted, rejected, superseded),
  fields JSONB, match JSONB, listing JSONB?, price JSONB?,
  listing_key TEXT GENERATED, gtin_key TEXT GENERATED,   -- partial UNIQUE indexes WHERE status = 'pending'
  decided_at?, decided_by FK app_user?, result JSONB?
)

lookup_request(
  id, kind CHECK IN (gtin, page, cutout),
  proposal_id FK product_proposal?, product_id FK?, product_image_id FK?,   -- exactly one, matching kind (cutout: product_image_id)
  listing_id FK vendor_listing?,              -- a scheduled refresh of this listing (page only)
  value?,                                     -- the GTIN or page address; null for cutout
  requested_by FK app_user?,                  -- null when queued automatically (auto-queue setting, cutouts)
  status CHECK IN (open, answered, closed), created_at, answered_at?
)
```

The runtime role may update only `payload` on `product_capture`, and a trigger allows only removing `dom_text`; both are listed in `app/core/grants.py`.

### Views

`price_current` joins non-voided observations to `price_norm`, reporting each under `COALESCE(product.merged_into, product_id)` with the recorded product kept as `observed_product_id` (#179), and leaving out posted prices (`source = listing`, 2L); `price_current_all` and the views built on it include them, for the "Include posted prices" filter. `offer_latest` yields, for each product and vendor location, the most recent current observation that applies there; for vendors with `price_scope = chain`, an observation at any of the vendor's locations applies to every active location of that vendor. `ingredient_offer` builds on `offer_latest` to give, per ingredient and location, the candidate products with their normalized unit prices and quality ratings, which is what comparison screens and the Phase 4 planner consume.

## Forward compatibility

Later phases add `recipe`, `recipe_ingredient`, and `recipe_cost_snapshot` in Phase 3 (`ingredient_alias` arrives earlier, in 1G); `shopping_list`, `shopping_list_item`, `travel_leg`, and the `trip_plan` tables in Phase 4, along with a nullable `purchase.trip_plan_id`; and `stock_location` and `stock_item` in Phase 5. Nothing in Phases 1–2 should need to change shape to accommodate them. If an implementation choice here would make one of those additions awkward, raise it.
