# 13 — Assisted Product Ingestion and Product Photos

**Status: draft for review, 2026-10-01. Not approved for implementation; `CLAUDE.md` names Phases 1 and 2 as the approved scope.** This document arrived as a handoff from a design session and is kept close to how it arrived. It was renumbered from 14, its cross-references now follow this repository's documents, and its examples use invented vendors, codes and prices. Its tensions with documents 00–12 are being settled in planning.

This document adds assisted product ingestion to Kitchen ERP. Products and their vendor listings are built from vendor web pages, manufacturer data keyed by barcode, and photos taken in the store. A product photo pipeline gives every product a consistent, good-looking image. The document follows the conventions of documents 00–05 (architecture, data model, capture API), the UI documents 08–11, and the ingredient vocabulary (sub-phase 1G in `03`, recorded in `12`).

It is written for Claude Code working in this repository. The `CLAUDE.md` addendum at the end is not merged until planning settles it.

The server-side sub-phases (P1–P6) are specified to implementation depth. The iOS pieces (share extension, on-device subject lift, label OCR) belong to Phase 6. They are specified here only as API contracts the server must accept, so the server can be built and tested now with a mobile-style contract test client.

Where table or column names here differ from what documents 02 and 03 actually produced in the repo, the existing names win. Adapt this document to them, and note the mapping in the PR description. If a decision here conflicts with an existing invariant, stop and raise it rather than quietly diverging.

---

## 1. Goals and non-goals

### Goals

- Cut the cost of creating a product to a single capture plus a one-tap review in the Home inbox.
- Use the best available source for each field. Manufacturer data (by GTIN) beats vendor page data for identity and nutrition. Vendor page data beats everything for vendor SKU, listing URL, and posted price.
- Give random-weight items (butcher meat, deli, bulk produce) a stable identity through the item code embedded in their in-store barcode. Use that code to improve receipt resolution.
- Give every product a primary image, prefer the household's own photos, and make photos look consistent across the catalog in both light and dark themes.
- Record provenance for every field and every image.

### Non-goals

- No crawling of vendor catalogs. Capture is driven by the user, one product at a time. The optional listing refresh in P6 only revisits listings the user already captured, and only on vendors that allow automated fetching.
- No headless browser in this phase. JS-rendered pages are handled by client-side capture (§4.1). A Playwright-based renderer can be added later behind a Compose profile if a real need appears.
- No automatic writes to the catalog. Every ingestion run produces a **proposal**. Only a human action turns a proposal into products, identifiers, listings, images, or price observations. This mirrors the receipt rule that a model suggestion never sets `product_id` without a human action.
- No full nutrition model. Label text is captured and stored. Parsing it into structured nutrition is deferred.

---

## 2. Product archetypes

Ingestion behaves differently for four kinds of product. The kind is stored on `product.kind`.

| Kind | Example | Identity | Primary enrichment source |
|---|---|---|---|
| `branded` | DeCecco spaghetti | GTIN | Open Food Facts / FDC Branded by GTIN; vendor page for SKU and price |
| `private_label` | A grocer's own-brand hash browns | Vendor SKU, plus package GTIN when scanned | Vendor page; Open Food Facts by GTIN if present |
| `random_weight` | Pork shoulder, bone-in, from a chain's meat case; lamb shoulder from an independent market | Vendor + random-weight item code | Vendor page; the ingredient's USDA reference from 1G |
| `unbranded_vendor` | A specific stand's strawberries | Vendor-exclusive product (existing `vendor_exclusive_id`) | User photo and manual entry |

`random_weight` products carry structured cut attributes (§3.3), because "pork shoulder, bone-in" and "pork shoulder, boneless" are different purchases with different yields.

---

## 3. Data model additions

All tables follow existing conventions: UUID primary keys, UTC `timestamptz`, `Decimal`/`numeric` for money and quantities, and migrations through Alembic. Tables described as append-only get the same privilege-plus-trigger enforcement as `price_observation`.

### 3.1 `product` (altered)

```
product
  + kind            text not null default 'branded'
                    check (kind in ('branded','private_label','random_weight','unbranded_vendor'))
  + attributes      jsonb not null default '{}'      -- validated per kind, see 3.3
  + primary_image_id uuid null references product_image(id) deferrable initially deferred
```

Backfill: products with a `vendor_exclusive_id` become `unbranded_vendor`. Everything else becomes `branded` and can be corrected in the UI.

### 3.2 `product_identifier` (new)

One product can have many identifiers, and some identifiers are only meaningful within a vendor.

```
product_identifier
  id            uuid pk
  product_id    uuid not null references product(id)
  scheme        text not null check (scheme in ('gtin','plu','vendor_sku','rw_item'))
  value         text not null          -- normalized, see below
  vendor_id     uuid null references vendor(id)   -- required for vendor_sku and rw_item, null for gtin/plu
  source        text not null          -- 'barcode_scan','listing','manufacturer','manual','receipt'
  created_at    timestamptz not null
  -- pii-scan: allow nil UUID in an index expression, not a card number
  unique (scheme, value, coalesce(vendor_id, '00000000-0000-0000-0000-000000000000'))
  check ((scheme in ('vendor_sku','rw_item')) = (vendor_id is not null))
```

Normalization rules:

- `gtin` is stored as GTIN-14 digits with a valid check digit. UPC-A (12), EAN-13, and GTIN-14 inputs all normalize to the same value. Invalid check digits are rejected.
- `plu` is a 4- or 5-digit produce PLU, stored as digits.
- `vendor_sku` is stored exactly as the vendor presents it (e.g. a grocer's `061528`), trimmed.
- `rw_item` is the item code extracted from a random-weight barcode (§3.5), stored as digits without leading-zero stripping.

If `product.gtin` or another barcode column already exists from Phase 1, migrate its values into `product_identifier` and keep the old column as a read-through view until callers move over. The existing `GET /api/v1/products?barcode=` parameter must resolve through `product_identifier`.

### 3.3 Product attributes

`product.attributes` is validated by a Pydantic model chosen by `kind` and the linked ingredient's category. In this phase only the meat/seafood schema is defined. The other categories accept `{}`.

```
MeatAttributes
  species: lamb | pork | beef | chicken | turkey | goat | duck | other
  primal:  str | None       # "shoulder", "loin", "leg", ...
  cut:     str | None       # "butt", "picnic", "blade chop", ...
  bone:    bone_in | boneless | None
  skin:    skin_on | skinless | None
  grade:   str | None       # "choice", "prime", "premium", free text
  form:    whole | sliced | cubed | ground | None
```

The species and primal values feed ingredient matching (the 1G ingredient vocabulary). The other values are display and filter attributes. Vocabulary beyond this list is an open question (§11).

### 3.4 `vendor` and `vendor_location` (altered)

```
vendor
  + platform        text null      -- adapter key, e.g. 'instacart_storefront', 'chain_storefront', 'grocer_spa'
  + fetch_policy    text not null default 'capture_only'
                    check (fetch_policy in ('server_fetch','capture_only','none'))
  + rw_layout       jsonb null     -- random-weight barcode layout, see 3.5

vendor_location
  + platform_store_ref text null   -- the platform's store id, e.g. 'store/417'
  unique (vendor_id, platform_store_ref) where platform_store_ref is not null
```

`fetch_policy` controls whether the server may fetch a vendor's pages:

- `server_fetch` means the server may fetch listing URLs (paste-URL path, P6 refresh). The vendor's `robots.txt` is still checked on every fetch (§5.4).
- `capture_only` means only client-captured payloads are accepted. This is the default, and it is the right value for any vendor whose `robots.txt` disallows automated access, such as an independent market's Instacart storefront.
- `none` means listing capture is disabled for that vendor.

`platform_store_ref` lets a listing's store-scoped price map to one of the household's real locations. A captured page scoped to a store the household does not use (for example, a branch in a city the household never shops in) still yields product data. It does not yield a price observation unless the user maps it to a location in review.

### 3.5 Random-weight barcode layout

In-store labels for weighed items use UPC-A numbers beginning with `2` (GS1 US variable-measure range). Layouts vary by retailer, so the parser is driven by the vendor's `rw_layout`:

```json
{ "item_start": 1, "item_len": 5, "price_start": 6, "price_len": 5, "price_kind": "price_cents" }
```

Positions are 0-based into the 12-digit UPC-A, excluding nothing (the leading `2` is position 0, the check digit is position 11). `price_kind` is one of `price_cents`, `weight_hundredths_lb`, or `none`.

Worked example (an invented chain's listing): product number `00251234000001` normalizes to UPC-A `251234000001`. With the default layout above, the item code is `51234` and the price field is `00000` (zeroed on the web listing, populated on the in-store label). The chain's page separately reports item number `51234`, which confirms the layout.

`kerp.barcodes.parse_random_weight(upc, layout) -> RandomWeight(item, price: Decimal | None, weight: Decimal | None)` is a pure function with a table-driven test suite. When a vendor has no `rw_layout`, the default layout above is used and the review screen shows the parsed item code so the user can confirm it.

### 3.6 `vendor_listing` (new)

A vendor's page for a product. There is at most one listing per vendor product page, and it may outlive changes to the product.

```
vendor_listing
  id               uuid pk
  vendor_id        uuid not null references vendor(id)
  product_id       uuid null references product(id)   -- null until accepted/linked
  canonical_url    text not null
  vendor_sku       text null
  store_ref        text null          -- platform store scope seen on capture, may be null
  title            text not null      -- as the vendor shows it
  last_captured_at timestamptz not null
  status           text not null default 'active' check (status in ('active','gone','ignored'))
  unique (vendor_id, canonical_url)
```

Canonical URL rules: prefer the page's `<link rel="canonical">` or `og:url`. Strip query strings and fragments. Strip store-scope path segments for platforms whose adapter knows them (for example, a chain storefront's `/pickup/store/<n>` prefix), and record the stripped value in `store_ref`.

### 3.7 `product_capture` (new, append-only)

The raw evidence for one capture, content-addressed like `receipt_document`.

```
product_capture
  id            uuid pk
  sha256        text not null unique      -- of the canonicalized payload JSON
  channel       text not null check (channel in ('ios_share','bookmarklet','paste_url','server_fetch','barcode','photo'))
  source_url    text null
  payload       jsonb not null            -- see 4.2; HTML/DOM text capped, images by reference
  captured_at   timestamptz not null
  capture_geo   geography(point) null
  created_by    uuid not null
```

### 3.8 `product_proposal` (new)

The reviewable result of running a capture through the pipeline.

```
product_proposal
  id                 uuid pk
  capture_id         uuid not null references product_capture(id)
  status             text not null check (status in ('pending','accepted','rejected','superseded'))
  fields             jsonb not null       -- merged field candidates with provenance, see 5.5
  match              jsonb not null       -- resolver result: existing product candidates, ingredient candidates
  image_candidates   jsonb not null       -- ordered list of product_image ids (status 'candidate')
  listing            jsonb null           -- vendor, canonical_url, sku, store_ref
  price              jsonb null           -- amount, unit, avg_weight, is_promo, store_ref, mapped location id
  decided_at         timestamptz null
  decided_by         uuid null
  result             jsonb null           -- ids of everything created/updated on accept
```

A new capture for the same `(vendor, canonical_url)` or GTIN supersedes any older pending proposal for it.

### 3.9 `external_lookup` (new)

A cache of manufacturer-database lookups, so repeat scans and re-proposals don't hit external APIs.

```
external_lookup
  provider     text not null check (provider in ('open_food_facts','fdc_branded'))
  key          text not null          -- normalized GTIN-14
  status       text not null check (status in ('hit','miss','error'))
  payload      jsonb null
  fetched_at   timestamptz not null
  primary key (provider, key)
```

Default TTL: hits 90 days, misses 14 days, errors 1 hour. `kerp lookups refresh --gtin <g>` forces a refetch.

### 3.10 `product_image` (new) — see §6 for the pipeline

```
product_image
  id               uuid pk
  product_id       uuid null references product(id)   -- null while only a proposal candidate
  sha256           text not null                      -- of the normalized original
  source_kind      text not null check (source_kind in
                     ('user_photo','manufacturer','open_food_facts','vendor_listing'))
  source_url       text null
  attribution      text null          -- e.g. OFF contributor/licence line
  role             text not null default 'product' check (role in
                     ('product','label_front','label_nutrition','label_ingredients','shelf_tag'))
  status           text not null check (status in ('candidate','active','hidden'))
  width            int not null
  height           int not null
  phash            bigint not null     -- 64-bit perceptual hash of the normalized original
  has_cutout       boolean not null default false
  cutout_source    text null check (cutout_source in ('device','server'))
  is_stock_suspect boolean not null default false
  pinned           boolean not null default false
  ocr_text         text null           -- for label roles
  captured_at      timestamptz null
  created_at       timestamptz not null
  unique (product_id, sha256)
```

`product.primary_image_id` is maintained by the selection rule in §6.5, unless an image is `pinned`.

### 3.11 `price_observation` (altered)

Add a `source` value for web listings if the column exists, or add the column:

```
price_observation
  + source  text not null default '<existing default>'
            -- adds 'listing' alongside existing receipt/manual/shelf values
  + listing_id uuid null references vendor_listing(id)
```

Listing observations go through `price_norm` like any other observation. Comparison views gain a filter for including or excluding `source = 'listing'` (default policy is an open question, §11). The append-only invariant is unchanged.

---

## 4. Capture surfaces

All capture surfaces post to the same endpoint and produce a `product_capture`. Captures are processed by the existing Postgres-claimed ingest worker under new job kinds: `product_extract`, `product_enrich`, `product_resolve`, `image_process`. Stage results go to `ingest_stage_result` as usual.

### 4.1 Client-side page capture

This is the primary path. The user's own browser has already rendered the page, which handles JS-heavy sites (some grocers ship only OpenGraph tags and a JavaScript stub to a plain fetch) and sites that disallow server fetches.

- **iOS Share extension (Phase 6).** The extension declares an `NSExtensionJavaScriptPreprocessingFile`. Its `run()` collects the payload below from the live Safari DOM and hands it to the extension. The extension posts it to `POST /api/v1/product-captures` and shows the proposal's status. It works offline through the app's existing queue.
- **Desktop bookmarklet (this phase).** Vendor sites' Content-Security-Policy usually blocks `fetch` to another origin, so the bookmarklet does not post directly. It collects the payload, opens a small window on the app origin (`/capture/clip`), and hands the payload over with `postMessage`, using an origin check on both sides. The clip window, which is authenticated by the app session, posts the capture and shows a link to the proposal. The settings page serves the bookmarklet source with the app origin baked in.

Collected payload (identical for both):

```json
{
  "page_url": "...",
  "canonical_url": "...",            // <link rel=canonical> or og:url, if present
  "title": "...",
  "meta": { "og:title": "...", "og:image": "...", "og:type": "...", "...": "..." },
  "jsonld": [ { "...": "..." } ],    // every application/ld+json block, parsed
  "dom_text": "...",                 // visible text of <main> or <body>, whitespace-collapsed, max 200 KB
  "image_urls": ["..."],             // og:image + the largest <img> elements in the product region, max 12
  "captured_at": "2026-10-01T18:00:00Z"
}
```

The payload never includes cookies, local storage, form values, or full HTML.

### 4.2 Paste URL (web app)

The product creation dialog accepts a URL. If the URL's host maps to a vendor with `fetch_policy = server_fetch`, the server fetches it under the rules in §5.4 and builds the same payload server-side (channel `paste_url`). Otherwise the dialog explains that this vendor needs the share extension or bookmarklet, and offers a manual product form prefilled with whatever the URL slug implies (for example, the title from `hash-browns-061528` and the SKU `061528`).

### 4.3 Barcode capture

`POST /api/v1/barcode-lookups` with a scanned code (channel `barcode`). The flow is:

1. If the code parses as a random-weight UPC, look up `rw_item` for the vendor of the supplied or nearest location. On a hit, return the product plus a parsed price/weight. On a miss, create a proposal with the item code prefilled.
2. Otherwise normalize to GTIN-14 and look up `product_identifier`. On a hit, return the product.
3. On a miss, run enrichment (§5.3) and create a proposal.

Phase 6 uses VisionKit's live data scanner. The web app accepts the same input from a USB/Bluetooth scanner in any product typeahead.

### 4.4 Photo capture

`POST /api/v1/product-photos` (multipart, channel `photo`). The client can attach on-device results: OCR text, detected barcodes, and a subject mask. The server treats a photo with a detected barcode as a barcode capture plus an image candidate. A photo with no barcode goes to the vision fallback in §5.2. Full contract in §7.

---

## 5. Extraction, enrichment, and resolution

### 5.1 Extractor ladder

`product_extract` runs extractors in order. Each one returns field candidates:

```python
@dataclass(frozen=True)
class FieldCandidate:
    field: str          # see field list below
    value: Any
    source: str         # 'jsonld' | 'meta' | 'adapter:<platform>' | 'llm' | 'url_slug' | 'manufacturer:<provider>' | 'user'
    confidence: Decimal # 0..1
```

1. **JSON-LD.** Parse schema.org `Product` / `Offer` / `AggregateOffer`. Map `name`, `brand.name`, `gtin*`, `sku`, `image`, `offers.price`, `offers.priceCurrency`, `offers.priceSpecification.unitCode`, and so on. Confidence 0.9.
2. **Meta tags.** OpenGraph and Twitter tags for title, image, and canonical URL. Confidence 0.7 for title, 0.8 for image and URL.
3. **Platform adapter**, selected by `vendor.platform`. An adapter is a pure function from payload to candidates over `dom_text` and URL. It holds no network code. Adapters in this phase:
   - `chain_storefront`: title with weight suffix ("Pork Shoulder Bone In - 7.25 Pound"), per-lb price, "avg/ea" price, product number (→ GTIN or random-weight UPC), item number, case size, breadcrumb category, and the store scope `store/<n>` from the URL.
   - `instacart_storefront`: product id and slug from `/store/<retailer>/products/<id>-<slug>`, title, price and unit text, size text from the rendered DOM. Capture-only.
   - `grocer_spa`: SKU from the URL suffix (`-061528`), title, price, size, and ingredients from the rendered DOM.
   Confidence 0.85. Adapters are versioned. A version bump re-runs extraction only on demand (`kerp captures reextract --platform <p>`).
4. **LLM fallback.** Runs only for fields still missing after steps 1–3. It calls the local Ollama model (`OLLAMA_BASE_URL`, configured model) over a cleaned `dom_text` excerpt, with a strict JSON schema and recorded responses in fixtures. Its output may only fill missing fields, never override. Confidence is capped at 0.6.
5. **URL slug.** Last resort for title and SKU. Confidence 0.3.

Fields: `title`, `brand`, `gtin`, `vendor_sku`, `rw_item`, `size_text`, `pack` (quantity + unit, parsed from `size_text` through the Phase 1 conversion library), `price_amount`, `price_unit` (`each` or a weight/volume unit), `avg_weight`, `is_promo`, `category_path`, `ingredients_text`, `image_urls`, `canonical_url`, `store_ref`.

### 5.2 Photo-only identification

When a photo arrives with no barcode, the server sends the image and any on-device OCR text to a configurable local vision model (`OLLAMA_VISION_MODEL`, unset by default). The model is asked for brand, product name, size text, and whether it sees a barcode. Its output becomes `llm` candidates (cap 0.5). With no vision model configured, OCR text alone goes to the text model. A proposal is always created, even if every field is empty, so the photo is never lost.

### 5.3 Manufacturer enrichment

`product_enrich` runs when a GTIN is known from extraction, scan, or photo.

- **Open Food Facts:** `GET /api/v2/product/{gtin}` on the public API. Send a descriptive `User-Agent` (`KitchenERP/<version> (<contact from settings>)`) as their usage policy asks. Map name, brands, quantity, ingredients text, categories, and image URLs. Images enter as `open_food_facts` candidates with the attribution line their licence requires.
- **USDA FDC Branded:** search with `dataType=Branded` and the GTIN, then keep only results whose `gtinUpc` normalizes to the same GTIN-14. Map brand owner, description, and package size. Record the FDC id on the proposal, so ingredient matching (1G) can use it as a hint.

Both results are cached in `external_lookup`. Manufacturer candidates carry confidence 0.95 for identity fields (brand, GTIN, size) and outrank vendor candidates for those fields. Vendor candidates still win for `vendor_sku`, price, and listing fields. Network access is optional: with `ENRICHMENT_ENABLED=false`, enrichment records `status = skipped` and the pipeline continues. Verify both endpoints against current provider docs while implementing.

### 5.4 Polite server fetcher

Used only for `server_fetch` vendors (paste URL, P6 refresh).

- Fetch and cache `robots.txt` per host for 24 hours. If the path is disallowed for the app's user agent, the fetch fails with `robots_disallowed`, and the vendor's `fetch_policy` is flagged in the inbox for the user to change to `capture_only`.
- At most one request per host per 10 seconds, with a global concurrency of 2.
- Use the same identifying `User-Agent` as enrichment. Never send credentials or cookies.
- Fail on non-HTML responses and on pages larger than 5 MB.

### 5.5 Merge and provenance

For each field, the merged value is the highest-ranked candidate under this per-field precedence table:

| Field group | Precedence |
|---|---|
| `brand`, `gtin`, `pack` | user > manufacturer > jsonld > adapter > meta > llm > url_slug |
| `title` | user > adapter > jsonld > manufacturer > meta > llm > url_slug |
| `vendor_sku`, `rw_item`, `price_*`, `avg_weight`, `is_promo`, `store_ref`, `canonical_url` | user > adapter > jsonld > meta > llm > url_slug |
| `ingredients_text` | user > manufacturer > adapter > llm |

Ties go to higher confidence. `product_proposal.fields` stores, for each field, the chosen value, its source, and every losing candidate, so the review screen can offer alternates with one click.

Conflicts that change identity are flagged on the proposal and are never resolved silently. Examples: a page GTIN different from a scanned GTIN, or a manufacturer size that disagrees with the vendor size by more than 5%.

### 5.6 Resolution

`product_resolve` matches the proposal to the existing catalog:

1. **Exact identifier.** A GTIN, `vendor_sku` for the vendor, or `rw_item` for the vendor that matches a `product_identifier` gives a strong match. The review default becomes "update existing product".
2. **Same listing.** A matching `(vendor, canonical_url)` that already has a `product_id` gives a strong match.
3. **Fuzzy.** `pg_trgm` similarity over title and brand, filtered by compatible pack dimension, gives up to 8 candidates.
4. **Model choice.** The local model picks from the shortlist or says "new". As in receipt resolution, a response that references an id outside the shortlist is rejected.

Ingredient matching reuses the 1G ingredient vocabulary, with title, category path, meat attributes, and any FDC hint as inputs. A proposal can create a new ingredient only through that resolver's existing inline flow.

---

## 6. Product photos

### 6.1 Sources and priority

Images come from four sources, in this default priority for the primary image:

1. `user_photo` with a cutout (§6.3)
2. `user_photo` without a cutout
3. `manufacturer` / `open_food_facts`
4. `vendor_listing`, unless flagged `is_stock_suspect`
5. A generated placeholder, which is not stored. It renders the ingredient's category accent from the UI theme (`08`) with a category glyph and the product's initials.

Label images (`label_front`, `label_nutrition`, `label_ingredients`, `shelf_tag`) are never primary. They appear in a "Labels" strip on the product page.

### 6.2 Ingest and normalization

`image_process` does this for every image, whether uploaded or downloaded from a candidate URL:

1. **Download (remote sources).** Same polite-fetch rules as §5.4, except that image hosts named by a captured page are fetched once, at capture time, regardless of the vendor's `fetch_policy`. This is equivalent to the user's browser loading the image. Limit: 15 MB. Content types: JPEG, PNG, WebP, HEIC/HEIF, AVIF.
2. **Decode and normalize.** Apply the EXIF orientation, convert to sRGB, and decode HEIC with `pillow-heif`.
3. **Strip all metadata** from stored files, including GPS. If the client sent a location, it goes in `product_capture.capture_geo`, never in image files.
4. **Content-address.** Compute `sha256` over the normalized original's encoded bytes. Store it at `media/originals/<sha[0:2]>/<sha>.<ext>`. If the same sha256 already exists, reuse the file.
5. **Perceptual hash.** Compute a 64-bit dHash. If an image's pHash is within Hamming distance 6 of an image already attached to three or more *different* products from the same vendor, mark it `is_stock_suspect`. This catches generic stock photos used for many meat cuts.
6. **Quality checks.** Flag images under 400 px on the short edge. Flag blur with Laplacian variance below a configurable threshold. Flags show on the candidate in review. They never block.
7. **Derivatives.** Produce WebP renditions at 160, 480, and 1200 px on the long edge, stored at `media/derived/<sha>/<variant>-<pipeline_version>.webp`. Derivatives are disposable. `kerp images rebuild` recreates all of them, byte-identical for a given pipeline version.

The media volume is included in the Phase 2 backup and restore commands. Restore must verify every original against its sha256.

### 6.3 Photo enhancement: subject cutout

The enhancement makes the household's own product photos look like a consistent catalog. The product is lifted off a cluttered counter or shelf and rendered on the app's own surface color, so the same image looks right in both light and dark themes.

**On device (Phase 6, preferred).** The capture app runs Vision's foreground instance mask request (iOS 17+) on the photo. The user taps the subject if several instances are found. The app uploads the original plus the mask as a single-channel PNG at the original's resolution, with `cutout_source = device`. Nothing about the original is altered on device.

**Server fallback (optional, this phase).** If the upload has no mask and `CUTOUT_SERVER_ENABLED=true`, an `imaging` Compose profile runs a background-removal model container. The default is `rembg` with a general-purpose model; verify the licences of the package and the model weights before enabling it. The worker sends the normalized original and receives a mask, with `cutout_source = server`. The profile is off by default. Without it, user photos simply have no cutout.

**Producing the cutout rendition from original + mask:**

1. Threshold and feather the mask: a 2 px edge blur, with alpha preserved.
2. Crop to the mask's bounding box plus 8% padding on each side, then pad to square.
3. Write `cutout-480` and `cutout-1200` as WebP **with alpha**. Do not bake in a background color. The UI composites the cutout onto the `--surface-raised` token with a soft shadow token, so light and dark themes both work and theme changes need no reprocessing.
4. If the mask covers less than 5% or more than 95% of the frame, discard it, record `has_cutout = false`, and log the reason. The original remains usable.

**Color fidelity.** No auto-levels, saturation, or white-balance changes are applied to food photos. The only pixel changes are orientation, the sRGB conversion, and resizing. The product page has a "Show original" toggle that displays the uncut original.

### 6.4 Images in review

The proposal review screen shows every image candidate as a card. Each card has a source badge (Your photo, Open Food Facts, Manufacturer, Vendor), any quality or stock flags, and a cutout preview if one exists. The user can:

- pick the primary image, which pins it,
- assign a label role,
- hide a candidate,
- or upload or replace an image inline, taking a photo from the phone's browser.

Hidden candidates stay stored but are excluded from selection.

### 6.5 Primary selection rule

`select_primary(product)` is a deterministic, pure function:

1. If any image is `pinned` and `active`, pick the most recently pinned one.
2. Otherwise take `active` images with `role = product`, excluding `is_stock_suspect` when any alternative exists. Order them by the §6.1 priority, then by short-edge resolution, then by `created_at` descending.

It runs on image insert, hide, pin, and unpin, and on `kerp images reselect`. Changing `primary_image_id` never deletes an image.

### 6.6 Serving

`GET /media/<sha>/<variant>` serves derivatives with `Cache-Control: public, max-age=31536000, immutable`. Because the URLs are content-addressed, they never go stale. The API returns image URLs on product payloads as `{ thumb, card, detail, cutout_card?, cutout_detail?, source_kind, attribution? }`. Where an attribution exists, the UI shows it in the image's info popover.

Images from vendors and manufacturers are stored for private use inside one household deployment. The app never exposes media without authentication. Shared family links (Phase 4) must continue to require a session.

---

## 7. API additions (capture contract v1)

All endpoints follow the Phase 2 contract conventions: bearer token, `Idempotency-Key` on every `POST`, and inclusion in the checked-in OpenAPI document with the drift test.

| Method & path | Purpose |
|---|---|
| `POST /api/v1/product-captures` | Body: the §4.1 payload plus `channel`, optional `lat`/`lon`, optional `vendor_location_id`. Returns `{capture_id, job_id, proposal_id?}`. |
| `POST /api/v1/barcode-lookups` | Body: `{code, symbology?, vendor_location_id?, lat?, lon?}`. Returns `{product?, random_weight?: {item, price?, weight?}, proposal_id?}`. |
| `POST /api/v1/product-photos` | Multipart: `image` (required), `mask` (optional PNG), `role` (default `product`), `product_id` (optional; attach directly to an existing product), `ocr_text`, `barcodes[]`, `captured_at`, `lat`/`lon`. Without `product_id`, creates a capture and proposal. With it, creates an `active` image on that product. Returns `{image_id, capture_id?, proposal_id?}`. |
| `GET /api/v1/product-proposals/{id}` | Proposal with fields, provenance, candidates, and match results. |
| `POST /api/v1/product-proposals/{id}/accept` | Body: the user's final choices (see §8). Returns created and updated ids. |
| `POST /api/v1/product-proposals/{id}/reject` | Marks the proposal rejected. Captures and images are retained. |
| `POST /api/v1/products/{id}/images/{image_id}/pin` · `/unpin` · `/hide` | Image management. |

The existing `GET /api/v1/ingest-jobs/{id}` reports status for the new job kinds.

---

## 8. Review and accept

Proposals appear in the unified Home inbox (`09`) as a "New product" or "Product update" card showing the primary image candidate, the title, and the vendor. Opening a card shows the review screen:

- **Match.** "Update {existing product}" (preselected on a strong match), one of the fuzzy candidates, or "Create new product".
- **Fields.** Each field shows its chosen value with a source badge. A dropdown lists the losing candidates. Identity conflicts are highlighted.
- **Kind and attributes.** Kind is preselected from evidence: GTIN plus a brand that isn't the vendor's gives `branded`; vendor brand gives `private_label`; a random-weight UPC or per-lb price gives `random_weight`. Meat attributes are prefilled from title parsing.
- **Ingredient.** Prefilled from the resolver, with inline creation through the 1G flow.
- **Images.** Per §6.4.
- **Price.** Shown when the capture had a price. If the listing's `store_ref` maps to a `vendor_location`, the observation is preselected for that location. Otherwise the price section shows "Not one of your stores" with a location picker and defaults to *not* recording a price.

**Accept** runs in one transaction. It creates or updates the product, upserts the `product_identifier` rows, upserts the `vendor_listing` and links it, sets the image statuses and runs primary selection, emits at most one `price_observation` (`source = 'listing'`), and writes `product_proposal.result`. A failure rolls back everything and leaves the proposal pending.

The whole review must be completable from the keyboard, matching the receipt review's keyboard requirement.

---

## 9. Receipt linkage

Receipt resolution (Phase 2, 2D) gains a step that runs before alias lookup:

- When a parsed line contains a number that parses as a random-weight UPC or a vendor item code, as warehouse-style receipts often print before the name, and that code matches an `rw_item` or `vendor_sku` identifier for the receipt's vendor, the line resolves with `resolution = 'identifier'`. It is treated like a confirmed alias, including the price-outlier check.
- Identifying a line in the to-identify queue offers to record the line's code as an identifier when the line has one.

New fixtures: add one synthetic warehouse-style receipt with item codes. Make at least one of those codes match a seeded `rw_item`.

---

## 10. Sub-phases and acceptance criteria

Each sub-phase can be built and merged independently, in order. A sub-phase is done when every criterion in its block passes, the suite is green, and `docker compose up` works from a clean checkout. All external providers and pages are tested against recorded fixtures. No test touches the network.

### P1 — Identifiers, kinds, vendors, listings

1. GTIN normalization maps every UPC-A, EAN-13, and GTIN-14 pair in its test table to the same GTIN-14 and rejects invalid check digits.
2. `parse_random_weight("251234000001", default_layout)` returns item `51234` with price `None` for a zeroed field. A table of populated-price labels parses to exact `Decimal` prices.
3. Inserting a `vendor_sku` or `rw_item` identifier without `vendor_id`, or a `gtin` with one, fails on the check constraint. A duplicate `(scheme, value, vendor)` fails on the unique index.
4. Existing product barcodes are migrated to `product_identifier`, and `GET /api/v1/products?barcode=` returns the same products before and after the migration.
5. Canonical URL normalization strips query strings, fragments, and known store-scope segments, recording the scope in `store_ref`. The chain-storefront fixture URL yields the store-free canonical form and `store_ref = 'store/417'`.
6. `MeatAttributes` validation rejects an unknown `species` and accepts `{}` for non-meat categories.

### P2 — Product image pipeline

7. Uploading the same photo twice yields one original file and one `product_image` row per product.
8. Stored originals and derivatives contain no EXIF, XMP, or GPS metadata. A fixture JPEG with GPS tags is verified clean.
9. A HEIC fixture is ingested and produces all three derivatives.
10. Deleting the derived media directory and running `kerp images rebuild` reproduces byte-identical derivatives.
11. Given a fixture original and a device mask, the cutout derivatives have an alpha channel, are square, and include the 8% padding. A mask covering 2% of the frame produces no cutout and sets `has_cutout = false`.
12. With `CUTOUT_SERVER_ENABLED=false`, user photos without masks are processed successfully with no cutout. With it enabled, using a stubbed `imaging` service in tests, they get `cutout_source = 'server'`.
13. An image whose pHash matches images on three other products from the same vendor is flagged `is_stock_suspect`, and a user photo on the product is selected as primary over it.
14. `select_primary` returns results matching a table of image sets, covering pinned, stock-suspect, and priority ordering. Pinning and then unpinning restores the rule-based choice.
15. Media URLs require authentication and carry immutable cache headers.
16. Restoring from backup verifies every original's sha256 and reports any mismatch.

### P3 — Capture and extraction

17. `POST /api/v1/product-captures` with the same payload twice creates one `product_capture` (same sha256). With the same `Idempotency-Key`, it returns the original response.
18. A payload whose `dom_text` exceeds 200 KB is rejected with a 413-class error naming the field.
19. The bookmarklet's clip window ignores messages from unexpected origins and posts captures only for an authenticated session.
20. The extractor ladder, run on the chain-storefront fixture payload, produces title "Pork Shoulder Bone In", price 3.49 per lb, avg weight 7.25 lb, product number parsed as a random-weight UPC with item `51234`, and `store_ref = 'store/417'`, each with the expected source.
21. Run on a grocer fixture with only meta tags, it produces the title and image from meta and the SKU `061528` from the URL. Run on the corresponding rendered-DOM fixture, it adds price and size from the adapter.
22. Run on an Instacart-storefront fixture payload, it produces the platform product id and title from the adapter.
23. The LLM extractor fills only fields that are missing after steps 1–3, never overrides them, and caps confidence at 0.6. This is verified with recorded responses.
24. Paste URL for a `capture_only` vendor makes no network request and returns the guidance response with slug-derived prefill.
25. The server fetcher refuses a path disallowed by a fixture `robots.txt` with `robots_disallowed`, and enforces the per-host interval against a fake clock.

### P4 — Manufacturer enrichment and barcode lookup

26. A GTIN with recorded Open Food Facts and FDC hits produces manufacturer candidates that outrank vendor candidates for brand and pack, but not for price or SKU.
27. A second lookup within TTL is served from `external_lookup` with no provider call. A miss is cached for its shorter TTL.
28. With `ENRICHMENT_ENABLED=false`, the pipeline completes with enrichment marked `skipped`.
29. An FDC search result whose `gtinUpc` normalizes to a different GTIN-14 is discarded.
30. `POST /api/v1/barcode-lookups` returns an existing product on a GTIN hit. For a random-weight label at a vendor with a matching `rw_item`, it returns the product with the parsed price. On a miss, it returns a proposal id.
31. OFF image candidates carry an attribution string, and the product page shows it.

### P5 — Proposals and review

32. A capture's proposal lists every field with chosen value, source, and alternates. A page GTIN that conflicts with a scanned GTIN is flagged and never auto-resolved.
33. A proposal whose GTIN matches an existing product preselects "Update existing". A fuzzy-only match preselects nothing.
34. A model resolution referencing a product id outside the shortlist is rejected.
35. Accepting a new-product proposal creates the product, identifiers, listing, active images, and primary image in one transaction. A forced failure mid-accept leaves no partial rows and the proposal pending.
36. A listing price whose `store_ref` maps to a household location defaults to emitting one `price_observation` with `source = 'listing'`. One whose `store_ref` is unmapped defaults to emitting none.
37. A newer capture for the same `(vendor, canonical_url)` supersedes the older pending proposal.
38. A full review of a fixture proposal, including picking a primary image and an ingredient, can be completed without a pointing device.
39. Proposal cards appear in the Home inbox and leave it on accept or reject.

### P6 — Receipt linkage and optional listing refresh

40. A fixture receipt line carrying a known `rw_item` resolves with `resolution = 'identifier'`, and an outlier price on it is flagged as for aliases.
41. Identifying a queued line that carries a code offers to record the code as an identifier and does so on confirmation.
42. `kerp listings refresh` revisits only `active` listings of `server_fetch` vendors, honors robots and rate limits, and emits a `listing` observation only when the price changed and the store is mapped. It is off unless `LISTING_REFRESH_ENABLED=true`.
43. A listing that returns 404 twice in a row is marked `gone` and stops being refreshed.
44. Comparison views can include or exclude `listing`-source observations, and the default follows the setting chosen in §11.

---

## 11. Open questions

These are deliberately left undecided:

- **Listing prices in comparisons.** Should web listing prices count in "where is this cheapest" by default, or only receipt and shelf observations? Web prices can differ from shelf prices.
- **Server-side cutout fallback.** Is it worth running the `imaging` container at all, or should cutouts be device-only?
- **Meat attribute vocabulary.** Free text for `primal` and `cut`, or a controlled list per species seeded from USDA IMPS naming?
- **Nutrition.** Parse nutrition label photos and OFF nutriments into structured fields now, or keep raw text until recipe nutrition is in scope?
- **Offline manufacturer data.** Live API lookups (default), or an imported Open Food Facts bulk dump for fully offline operation?

---

## CLAUDE.md addendum

```
## Product ingestion and images (doc 13)
- Ingestion never writes catalog rows directly. Every capture -> product_proposal -> human accept.
- Server-side fetching of vendor pages only when vendor.fetch_policy = 'server_fetch' AND robots.txt allows.
  Never work around a robots disallow; such vendors are capture_only.
- Model outputs (extraction, resolution, vision) only fill missing fields or choose from a shortlist;
  ids outside the shortlist are rejected. Record responses in fixtures; tests never hit the network.
- GTINs are stored as GTIN-14. Vendor-scoped identifiers (vendor_sku, rw_item) always carry vendor_id.
- Image originals are content-addressed by sha256 and metadata-stripped (including GPS). Derivatives are
  disposable and rebuildable byte-identically. Do not alter food photo colors.
- Cutouts are stored with alpha; never bake a background color. The UI composites on theme tokens.
- Media is never served without authentication.
```
