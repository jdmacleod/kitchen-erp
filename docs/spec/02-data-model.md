# 02 — Data Model, Phases 1–2

This document defines the tables, constraints, and views that Phases 1 and 2 create. Column lists are normative for names, nullability, and meaning; choose sensible numeric precisions where none is given. Every table has a UUID `id` primary key unless stated otherwise, and mutable tables carry `created_at` and `updated_at`, which are omitted below for brevity.

The central idea is a three-way separation. An ingredient is what a recipe asks for. A product is a specific thing you can buy that fulfils an ingredient, and it is where brand, pack size, and perceived quality live. A price observation is a product at a vendor location at a moment in time. Recipes will point at ingredients, optionally pinning a product; purchases point at products; comparison happens at the ingredient level, filtered by quality. Collapsing any two of these three is the mistake this model exists to avoid.

## Phase 1 tables

### Identity

```
app_user(id, email UNIQUE, display_name, password_hash, role CHECK IN (admin, member), active)
api_token(id, user_id FK, name, token_hash UNIQUE, last_used_at?, revoked_at?)
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
  density_g_per_ml NUMERIC(10,5)?, density_source?,
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

`density_source` takes the same values as `ingredient_measure.source`. `yield_pct` and `perishability` are not used until Phase 3 and Phase 4 respectively but are cheap to capture while an ingredient is being created, and both default sensibly.

### Products

```
product(
  id, ingredient_id FK,
  brand?, name,
  pack_qty NUMERIC?, pack_unit FK unit?,     -- both null means sold by variable weight or loose
  barcode? (unique when present),
  quality_rating SMALLINT? CHECK (1..5),
  exclusive_vendor_id FK vendor?,            -- single-source items and vendor-specific produce
  density_override NUMERIC(10,5)?, density_override_source?,
  active BOOLEAN DEFAULT true, notes?,
  CHECK ((pack_qty IS NULL) = (pack_unit IS NULL))
)
```

A brandless product tied to a vendor is how unbranded produce keeps its identity: the strawberries from one stand are a different product from a supermarket's, with their own quality rating and price history, while both fulfil the ingredient strawberries. The density override exists because density sometimes varies by brand enough to matter, kosher salt being the standard example.

### Geography and vendors

```
place(id, geom GEOGRAPHY(Point,4326), label)

home_base(id, name UNIQUE, place_id FK)

vendor(
  id, name (unique on lower(name)),
  kind CHECK IN (chain, independent, market, stand),
  price_scope CHECK IN (chain, location) DEFAULT location,
  website?, notes?
)

vendor_location(
  id, vendor_id FK, place_id FK,
  home_base_id FK?,                          -- defaults to nearest; nullable for in-between sites
  parent_location_id FK vendor_location?,    -- a stall inside a market
  name, address?,
  osm_type CHECK IN (node, way, relation)?, osm_id BIGINT?,
  opening_hours TEXT?,                       -- OSM opening_hours syntax; seasonality included
  stop_overhead_min SMALLINT?,               -- used by the Phase 4 planner
  receipt_identifiers TEXT[],                -- store numbers or address fragments as printed on receipts
  active BOOLEAN DEFAULT true,
  CHECK (parent_location_id IS DISTINCT FROM id)
)
```

`place` is a shared node type so that Phase 4 can store drive times between any two places without caring whether an endpoint is a home or a shop. `price_scope` records whether a vendor prices uniformly across its locations; when it is `chain`, an observation at any location stands in for all of them. A stall's `opening_hours` may be null, in which case it inherits its parent's. `receipt_identifiers` lets header parsing pin a receipt to the right location of a chain from the store number printed on it.

### Optional reference data

```
ref_usda_portion(id, fdc_id, food_description, portion_label, gram_weight, data_type)
```

Loaded by a CLI importer from a local USDA FoodData Central download, and used only to suggest densities and measures when an ingredient is created. Nothing reads it at costing time.

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
  status CHECK IN (pending, running, needs_review, done, failed),
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
  status CHECK IN (draft, reviewed, committed),
  source CHECK IN (receipt, manual, import),
  entered_by FK app_user,
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
  resolution CHECK IN (barcode, alias, fuzzy, llm, manual, unmatched, ignored),   -- the rung whose answer was used
  resolved_by FK app_user?,                   -- the person who confirmed it; null only for barcode and alias
  resolution_confidence NUMERIC?, flags TEXT[],
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
  source CHECK IN (receipt, manual, shelf, import),
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

### Views

`price_current` joins non-voided observations to `price_norm`. `offer_latest` yields, for each product and vendor location, the most recent current observation that applies there; for vendors with `price_scope = chain`, an observation at any of the vendor's locations applies to every active location of that vendor. `ingredient_offer` builds on `offer_latest` to give, per ingredient and location, the candidate products with their normalized unit prices and quality ratings, which is what comparison screens and the Phase 4 planner consume.

## Forward compatibility

Later phases add `ingredient_alias`, `recipe`, `recipe_ingredient`, and `recipe_cost_snapshot` in Phase 3; `shopping_list`, `shopping_list_item`, `travel_leg`, and the `trip_plan` tables in Phase 4, along with a nullable `purchase.trip_plan_id`; and `stock_location` and `stock_item` in Phase 5. Nothing in Phases 1–2 should need to change shape to accommodate them. If an implementation choice here would make one of those additions awkward, raise it.
