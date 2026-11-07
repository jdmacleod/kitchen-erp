# 18 — Weekly vendor flyer survey: the handoff draft

> Superseded by `18-weekly-flyer-survey.md`, the approved spec (2U–2X). This is the draft it was built from, kept as a record. Store, chain, platform and place names are redacted (`18`, F2), and the store roster is removed. Its phases A–E became 2U–2X, and several of its choices were overruled: offers are never prices, the helper fetches but does not decide, and there is no fixed quiet window.

Repo: `kitchen-erp-products`
Depends on: `13` (assisted product ingestion, folded into 1H, 1I and 2K–2N), the vendor and location model (`02`, `03`), the ingredient vocabulary (`12`, 1G), and the receipt-line product resolver.
Status: handoff for implementation. Phases are ordered; each phase is independently shippable and leaves the system in a usable state.

## 1. Purpose

The flyer surveyor turns the weekly internet circulars published by the grocery chains around each kitchen into structured, dated, store-scoped offers, filters those offers down to the ones this household plausibly cares about, and feeds them to three consumers in the core ERP: the trip planner (which store this week for this list), the COGS engine (what a recipe costs under this week's prices), and a per-product, per-vendor price history that becomes valuable only after a few months of weekly snapshots.

It is the mirror image of `13`, which ingests *catalog* data — what a product is, its image, its manufacturer. This document ingests *offer* data — what a product costs this week, at which store, under what conditions. The two share the vendor model and the product-resolution layer, which is why they live in the same repo.

The feature is explicitly best-effort. Retailer websites are not stable APIs, and the design assumes adapters will break. The non-negotiable invariant is that a broken adapter degrades gracefully (that week's offers for that store are missing and flagged) and never corrupts stored data or blocks the core ERP.

## 2. Findings that constrain the design

Three vendor sites were examined directly before this spec was written, and all three return only a navigation shell with no product content in the static HTML. Offer data is injected client-side after page load. The practical consequences are: a plain HTTP fetch plus HTML parsing will not work for the major chains; a headless browser is a first-milestone dependency, not a later optimization; and the three mechanisms observed are different enough that adapters must be written per *mechanism*, not per *store*.

The mechanisms encountered across the stores near both kitchens fall into five classes:

- **Hosted interactive circular.** Four banners of two grocery groups. The ad is an embedded widget scoped to a store id. The circular platform's backend endpoints are undocumented, keyless, and the same calls the retailer's own page makes; open-source projects use them successfully but they can change or vanish without notice. Flyer data from this source gives names and prices but usually lacks pack sizes.
- **Hosted storefront.** A regional independent. The weekly-deals page is store-scoped (a store must be selected; it defaulted to another branch rather than the household's) and rendered client-side, but the underlying data is almost certainly a storefront collection or product field that can be read as JSON once the request is identified.
- **Itemized first-party HTML.** Four chains publish a browsable item list on their own site. Structure differs per site but is parseable with per-site selectors; one is reported to also publish a PDF edition.
- **Image or PDF pages.** Several regional chains, a discount grocer, and most independents publish the ad as page images or a PDF. These need rasterization and a vision model. The second kitchen depends on this class heavily: three of its nearest markets are on one road and all three publish this way.
- **No circular.** One specialty grocer does not run weekly sales or publish a flyer. Roadside stands and farmers' markets have no web presence. These are out of scope for this document and remain manual-entry purchase sources.

## 3. Store roster at handoff

Redacted. The roster named the stores around each kitchen; it lives in the private helper's `docs/FLYERS.md` (`18`, F1).

## 4. Architecture

The pipeline has six stages. Each stage writes to its own table and is idempotent on its inputs, so any stage can be re-run against archived upstream data without re-fetching.

**Ad-source registry.** Every vendor location that publishes a circular gets one `ad_source` row: the mechanism class, the adapter name, the store identifier the adapter needs (a grocery group's store number, the circular platform's merchant and store ids, a storefront handle, a page URL for itemized sites, a PDF or image index URL for image sites), the expected publication weekday (Wednesday for most chains in the region), and an `enabled` flag. Zip code is used once, at seed time, to propose sources for a kitchen; after that the kitchen's curated `kitchen_ad_source` list governs what gets fetched.

**Fetch.** One adapter per mechanism class. Each adapter takes an `ad_source` row and returns a `raw_ad` record: the ad-week range as published, a fetch timestamp, and the raw payload (JSON for the circular platform and storefronts, captured HTML for itemized sites, one PNG per page for image sites). Raw payloads are stored verbatim and never mutated. The headless browser runs as a separate container in the Docker stack and is used only by adapters that need it. Fetching is scheduled, weekly, early on the publication day, and rate-limited to one request per source per run; the surveyor never polls.

**Normalize.** Each `raw_ad` is turned into zero or more `offer_candidate` rows with a fixed schema: `name_as_printed`, `brand` (nullable), `size_qty` and `size_unit` (nullable), `price`, `price_basis` (`each`, `per_lb`, `per_oz`, `multi` with quantity, `bogo`, `percent_off`, `dollar_off`), `requires_loyalty` (bool), `limit_qty` (nullable), `valid_from`, `valid_to`, `department` (nullable), `source_page` and `source_bbox` (for image sources, so a human can see where the parse came from). Structured sources are normalized by code; free-text names and ambiguous price callouts are normalized by a local text model through Ollama; image pages are normalized by a local vision model through Ollama. The model is a normalizer, not a decision-maker: it emits the schema and a confidence, nothing else.

**Relevance filter.** Before any expensive resolution, each candidate is scored against three cheap signals: fuzzy match to the names of products in this kitchen's purchase history, fuzzy match to the ingredient vocabulary entries that appear in this kitchen's indexed Cooklang recipes, and exact match to an explicit per-kitchen watchlist. Candidates below threshold are retained only as a count on the `raw_ad` (so "a chain had 312 offers, 23 relevant" is visible) and are otherwise discarded. The threshold is a kitchen setting with a conservative default; the expectation is that a household cares about five to fifteen percent of a chain circular.

**Resolve.** Relevant candidates go through the existing product resolver (shared with receipt lines). Flyer lines are less cryptic than receipt lines but less specific — there is no UPC — so a match is recorded as `confirmed` only above a confidence threshold and as `candidate` otherwise. Candidates land in the unified Home inbox as a fix-it queue item of type `flyer_offer_unresolved`, with the printed name, the price, the store, and (for image sources) the cropped region of the page. Confirming a match from the inbox teaches the resolver, as receipt confirmations already do. Where a resolved product has a known size and the offer has none, the offer inherits the product's size for unit-price computation and is flagged `size_inherited`.

**Serve.** Resolved offers are exposed through three read models: `current_offers(kitchen)` for the trip planner, `price_for(product, vendor_location, date)` for COGS projection, and `price_history(product, vendor_location)` for the series. The core ERP reads these; it does not read the candidate or raw tables.

## 5. Ollama allowance

The surveyor talks to Ollama over its HTTP API at a configurable base URL, defaulting to the same instance the receipt parser uses. Model names are configuration, not code. Two roles are defined:

- `FLYER_TEXT_MODEL` — used to normalize free-text offer names and price callouts from structured and HTML sources. Any instruct model already resident for receipt parsing is acceptable; the prompt asks for the offer schema as JSON and nothing else.
- `FLYER_VISION_MODEL` — used to read image pages. On the reference hardware (M4 Pro, 24 GB unified memory) Qwen2.5-VL 7B, Gemma 3 12B, and Llama 3.2 Vision 11B all fit with sequential loading; Gemma 3 12B is the default because it is already in the household's reviewer panel.

Image pages are never sent whole. Each page is tiled into overlapping regions (default 2×3 grid with 10% overlap, configurable per `ad_source` because flyer layouts differ), each tile is sent with the same schema prompt, and results are merged with de-duplication on `(name_as_printed, price)` across overlapping tiles. A second, cheaper verifier pass re-reads any tile whose result has a confidence below threshold or a price that fails a sanity check (negative, zero, or more than three standard deviations from that department's median for that store). Every vision result carries `source_page` and `source_bbox` so the inbox can show the human exactly what the model read.

Because vision models contend for memory with interactive receipt parsing, the weekly job is scheduled during a configurable quiet window (default 04:00–06:00 local on the publication day) and the scheduler checks that no interactive parse is in flight before loading the vision model.

## 6. Phases

### Phase A — Foundation and the first itemized adapter

Phase A builds the registry, the raw store, the normalizer interface, the relevance filter, the resolver hookup, the inbox integration, and the scheduler, and proves the whole loop end to end against the least fragile source available in both kitchens: one itemized chain's HTML ad. No headless browser is required if that chain's page can be read from its underlying data request; if it cannot, the browser container is introduced here rather than deferred. Phase A also seeds the two kitchens' curated store lists from the roster in section 3, with all non-Phase-A sources present but disabled.

Acceptance criteria:

1. An `ad_source` table exists with the fields in section 4, and both kitchens have a `kitchen_ad_source` list seeded from the section 3 roster, with only that chain's sources enabled.
2. A `raw_ad` table stores the verbatim payload, ad-week range, fetch timestamp, and adapter version for every fetch; re-running normalization against an archived `raw_ad` produces identical `offer_candidate` rows without a network call.
3. The first itemized adapter fetches the current week's ad for a given store id and produces at least 80% of the items visible on the site's own weekly-ad page, verified by a hand count on one ad week per kitchen.
4. `offer_candidate` rows carry every field in the normalize schema; `price_basis` is populated correctly for each, per-lb, multi-buy, and BOGO offers present in the sampled week.
5. The relevance filter reduces that chain's candidate set to only those matching purchase history, recipe ingredient vocabulary, or the watchlist, and the discarded count is recorded on the `raw_ad`.
6. Relevant candidates pass through the shared product resolver; confirmed matches appear in `current_offers(kitchen)`, unconfirmed matches appear in the Home inbox as `flyer_offer_unresolved` items, and confirming one from the inbox updates the resolver's learned mappings.
7. A scheduled weekly job runs every enabled `ad_source` for every kitchen on the configured publication day within the quiet window, logs per-source success or failure, and a single failed source does not prevent other sources from running.
8. A failed or empty fetch for a source produces a `raw_ad` with status `failed` and a human-readable reason, and the inbox shows one summary item per failed source per week rather than one per missing offer.

### Phase B — Hosted-circular adapter (two grocery groups' banners)

Phase B adds one adapter that covers the four hosted-circular banners in both kitchens, which is the largest single gain in coverage per adapter written. The adapter reads the circular platform's backend the retailers' own pages call. Because those endpoints are unofficial, the adapter is written defensively: every response is schema-validated before normalization, an unexpected shape fails the fetch loudly rather than producing garbage candidates, and the raw JSON is archived regardless so a parser fix can be replayed.

Acceptance criteria:

1. The hosted-circular adapter, given a merchant id and a store id, fetches the current ad week's items and archives the raw JSON.
2. The adapter is configured for all four banners in both kitchens, and each store's `ad_source` row records the specific ids used so a store change requires only a data edit.
3. On a sampled week, the adapter's candidate count for one sampled store is within 10% of the item count visible in the site's own ad viewer.
4. Loyalty-card qualifiers, quantity limits, and multi-buy pricing present in the raw data are carried into `requires_loyalty`, `limit_qty`, and `price_basis` respectively.
5. Offers lacking a size inherit size from the resolved product when confirmed, are flagged `size_inherited`, and unit price is computed only for sized offers.
6. Where the same offer is reachable both through the hosted circular and through the retailer's on-site sale listing, only the hosted circular is used; the on-site listing is not fetched.
7. A response that fails schema validation is recorded as a `failed` raw ad with the validation error, and the inbox surfaces it as a single "adapter needs attention" item.

### Phase C — Storefront and remaining itemized adapters

Phase C adds the storefront adapter for the regional independent and per-site itemized adapters for the other three itemized chains. These are lower-value per adapter than Phase B but the storefront in particular is a frequently visited specialty source for the first kitchen. The storefront adapter must select the household's branch before reading deals; the site defaults to another. The adapter for the chain with a PDF edition prefers the PDF edition if it is stable, since a PDF is less likely to change shape than a themed HTML page.

Acceptance criteria:

1. The storefront adapter reads the storefront's weekly deals for the household's branch specifically, verified by the presence of the branch name in the archived payload and by a spot check against the site with that branch selected.
2. Itemized adapters exist for the other three itemized chains, each with per-site selectors or PDF parsing isolated in a small, replaceable module.
3. Every Phase C adapter meets the same 80% hand-count threshold as Phase A on one sampled week.
4. Both kitchens' curated lists have all Phase A–C sources enabled and the weekly job completes within the quiet window with the browser container running.
5. Price history for every confirmed product at every enabled store has at least four consecutive weekly points before Phase D begins, and `price_history(product, vendor_location)` returns them in order with ad-week ranges.

### Phase D — Image and PDF adapter with Ollama vision

Phase D is the highest-effort phase and the most valuable for the second kitchen. It adds the image/PDF adapter and the vision normalizer described in section 5, and brings the image and PDF publishers online. Accuracy on dense, bilingual, stylized flyer pages is the central risk, so Phase D is gated on a measured recall target and ships with human confirmation on by default for every vision-sourced offer until that target is met for a given source.

Acceptance criteria:

1. The image/PDF adapter fetches each page of a source's current ad as a PNG at a resolution sufficient for the vision model (minimum 1600 px on the long edge), archives the pages as the raw payload, and records page count and ad-week range.
2. The vision normalizer tiles each page with the configured grid and overlap, sends each tile to `FLYER_VISION_MODEL` with the schema prompt, merges tile results with de-duplication, and records `source_page` and `source_bbox` for every candidate.
3. The verifier pass re-reads tiles with below-threshold confidence or prices failing the sanity check, and the final candidate carries the verifier's confidence.
4. On a hand-labeled sample of three pages per source (one per source per kitchen at minimum), recall of printed offers with a correct price is at least 85% and precision is at least 90%; sources below the target remain in `require_confirmation` mode, in which every vision-sourced offer goes to the inbox before entering `current_offers`.
5. The inbox item for a vision-sourced offer shows the cropped page region alongside the parsed fields, and the human can correct the price or size inline.
6. The weekly job loads the vision model only inside the quiet window, checks for an in-flight interactive parse before loading, and unloads the model after the last image source completes.
7. Bilingual flyer text (Spanish/English) is normalized to the English product name in `name_as_printed` with the original string preserved in a `name_original` field.

### Phase E — Consumers: trip planner, COGS projection, price history

Phase E wires the three read models into the core ERP. It is listed last because it depends on data accumulated during Phases B–D, but the read-model interfaces are defined in Phase A so core-ERP work can proceed in parallel against fixtures.

Acceptance criteria:

1. The trip planner consumes `current_offers(kitchen)` and, for a shopping list, shows per-store the list items on offer this week and the total expected savings against each product's trailing median price at that store.
2. The COGS engine can project a recipe's cost under current offers for a chosen store, and the projection distinguishes confirmed offer prices from inherited-size estimates.
3. A product detail view shows the price history series per vendor location with offer weeks marked, and a "typical price" derived from the trailing twelve weeks.
4. A weekly digest item in the Home inbox lists, per kitchen, the relevant offers this week grouped by store, with one line per offer and a link to the product.
5. Offers whose `valid_to` has passed are excluded from `current_offers` automatically, and overlapping ad weeks at a boundary resolve to the offer with the later `valid_from`.

## 7. Cross-cutting requirements

Every adapter is versioned, and `raw_ad.adapter_version` records which version produced the payload, so a parser regression can be traced and replayed. Adapters are exercised by a fixture test suite built from archived raw payloads, never from live fetches, and a live smoke test per adapter is run manually before each release. Fetch volume is bounded to one request per source per week plus retries with exponential backoff capped at three attempts, and the scheduler refuses to run a source more than once per publication day. Nothing fetched is redistributed; raw payloads are retained for twelve months then pruned, while normalized offers are retained indefinitely as the price-history series. All model calls go through a single Ollama client module with timeouts, so a hung inference cannot stall the weekly job past the quiet window.

## 8. Out of scope

The specialty grocer and any vendor without a published circular; farmers' markets and roadside stands; digital coupons and app-only offers that require authentication; retailer loyalty-account integration; automated purchasing; aggregator sites (including the circular platform's own consumer app) as a data source, which would add a second fragile dependency on top of the first.

## 9. Open questions for the implementer

Whether the first itemized chain's ad can be read from a data request without the browser container determines whether Phase A or Phase B introduces Playwright. Whether the PDF chain's edition is reliably available week to week determines whether its adapter is PDF-first or HTML-first. Whether a grocery group's product lookup is worth adding as a size-enrichment step for Phase B offers should be decided after measuring how often `size_inherited` is set and how often it is wrong.
