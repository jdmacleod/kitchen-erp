# 18 — Weekly flyer survey (2U–2X)

**Status: approved 2026-11-05 as Phase 2 sub-phases 2U–2X (the owner's go-ahead to build the plan).** It was written from a design-session handoff ("Doc 15", which named the household's own stores). The rulings taken on 2026-11-05 (F1–F11 below) are folded in. The household's store roster and each chain's adapter notes live in the private `kitchen-erp-products` repository (`docs/FLYERS.md`), never here. Every store, brand and price in this document is invented.

## Why

Most chains publish a weekly circular: this week's sale prices, store by store. The price book only learns a sale price after someone pays it or spots it on a shelf. A surveyor that reads the circulars around each kitchen could tell the trip planner which store has this week's list on offer. It could also let recipe costing price a recipe at this week's offers, and over a few months build a series of offer weeks that shows what "a good price" is for each product at each store.

This is the mirror image of `13`. `13` ingests catalog data: what a product is, its photo, its maker. This document ingests offer data: what a product costs this week, at which store, under what conditions.

The feature is best-effort. Retailer websites are not stable APIs, and adapters will break. The one invariant is that a broken adapter degrades gracefully: that week's offers from that store are missing and flagged. It never corrupts stored data and never blocks the rest of the system.

## Rulings (2026-11-05)

- **F1.** The household's store roster, kitchen locations and per-chain adapter notes live in the private helper (`docs/FLYERS.md`), as retailer adapters already do. This document uses invented stores.
- **F2.** No chain, banner or flyer-platform name appears in this repository: not in code, tests, specs, commits or pull requests. Mechanisms are named generically, for example "hosted circular" or "storefront platform".
- **F3.** The helper fetches, archives and normalizes. Kitchen ERP keeps the registry and queues the weekly work. It also filters for relevance, resolves offers to products, fills the inbox and serves every read model. `products:read` stays "the queue only" (helper SPEC §8.1), and Kitchen ERP still makes no outbound calls (non-negotiable 9).
- **F4.** An offer is not a price observation. Offers are kept in their own append-only table, with a validity window and a price basis. The default price book, receipt resolution, recipe costing and the planner never read them unless asked. A learned alias links an offer to a product automatically, and anything else waits in the inbox. No person has to accept each offer, because an offer never becomes a price.
- **F5.** The work is Phase 2 sub-phases 2U–2X. The handoff's phases map onto them as A and B → 2U, C → 2V, D → 2W and E → 2X (F12).
- **F6.** This week's offers appear on a Home card and as a mark on shopping-list items. The inbox holds only what needs a person: offers to match and sources that failed.
- **F7.** The vision model for image and PDF circulars is chosen by a benchmark at the start of 2W, like 2O's. Text normalization reuses the helper's `LLM_MODEL`. Model names are configuration.
- **F8.** A headless browser is added only when an adapter cannot read its source's own data request. It runs as its own container beside the helper, outside Kitchen ERP's Compose project.
- **F9.** Flyer fetching follows the helper's existing rules (helper SPEC §5): robots.txt is honoured for every host, including undocumented backends, and only facts are kept. A source that robots.txt disallows is recorded as `not_allowed` and is never fetched.
- **F10.** Relevance is derived from the household's own data, plus a `watch` flag on ingredients. One threshold in configuration; no per-kitchen settings table.
- **F11.** There is no fixed quiet window. On publication day Kitchen ERP queues a source's request once no receipt is being read and, for image sources, once the model server answers. A sleeping model server delays the run; it never fails it.
- **F12 (2026-11-06).** The hosted circular proves the foundation in 2U. The 2U probe found that the chain first planned for it publishes its items' names, page boxes and codes as data but prints its prices only on the page, so it joins the image class in 2W as a region source.
- **F13 (2026-11-07).** Only offers for watched ingredients ask a person. The first live week found 44–56% of each store's offers relevant, against the 5–15% expected, because a household's purchases and recipes cover most of a circular. Every relevant offer is still kept and resolved, and an exact alias still links one automatically. A watched match without an alias waits in the inbox. Any other suggestion waits quietly under "Other offers" in the offer queue: a person may match it when they like, and the alias they teach links next week's offer automatically. An ingredient is watched from its own page.

## How circulars are published

Circulars come in five mechanism classes, and an adapter is written per mechanism, not per store:

- **Hosted circular.** An embedded widget scoped to a store id, served from a third-party circular platform's JSON backend. These give names and prices, but usually no pack sizes.
- **Storefront platform.** A store-scoped weekly-deals collection on a hosted e-commerce platform. The store must be selected explicitly, because the site's default store may be another branch.
- **Itemized first-party page.** A browsable item list on the chain's own site, parsed with per-site selectors. Some chains also publish a PDF edition.
- **Image or PDF pages.** The circular is page images or a PDF. These need rasterization and a vision model.
- **Regions.** A circular platform serves each item's name, its box on the page and the store's item codes as data, but the price is printed only on the page image. The structure comes from data and only each item's price is read from its crop (2W).
- **No circular.** Chains that don't run weekly sales, and markets and stands with no web presence. These stay manual-entry sources and are out of scope.

All the examined first-party pages render their offers client-side, so a plain page fetch yields nothing. Most adapters read the JSON request the page itself makes. A headless browser is needed only where that fails (F8).

## Stages

| Stage | Where | What |
|---|---|---|
| Registry | Kitchen ERP | `ad_source` per store location that publishes a circular |
| Schedule | Kitchen ERP worker | One `flyer` lookup request per enabled source per ad week |
| Fetch | Helper | The adapter for the source's mechanism; the raw payload is archived verbatim |
| Normalize | Helper | Code for structured sources, the text model for free text, the vision model for pages |
| Report | Helper → Kitchen ERP | `POST /flyer-reports` with the run's status and its candidates |
| Relevance | Kitchen ERP | Keep the candidates this household plausibly cares about, and count the rest |
| Resolve | Kitchen ERP | Link each relevant offer to a product: automatically through a learned alias, otherwise through the inbox |
| Serve | Kitchen ERP | `current_offers`, `offer_price`, `offer_history` |

Each stage is idempotent on its inputs. The helper can re-normalize an archived payload with a newer adapter version and report it again without fetching, and Kitchen ERP then reads that week's offers from the newest run.

## Data (Kitchen ERP)

```
ad_source
  id uuid PK
  vendor_location_id FK UNIQUE        -- one circular per store location
  mechanism CHECK IN (hosted_circular, storefront, itemized, image, region)
  adapter text                        -- the helper's adapter name
  store_ref jsonb                     -- ids the adapter needs; an object of strings, opaque here
  publish_weekday smallint 1..7       -- ISO weekday the ad week starts
  enabled bool default false
  require_confirmation bool           -- default true for image and region, false otherwise (2W)
  tiles jsonb?                        -- image only: {rows, cols, overlap}
  failures int default 0, paused_until timestamptz?
  created_at, updated_at

flyer_run                              -- one per report; append-only
  id uuid PK, ad_source_id FK, lookup_request_id FK?
  status CHECK IN (ok, empty, failed, not_allowed)
  reason text?                         -- human-readable, for failed and not_allowed
  adapter_version text
  ad_week_from date, ad_week_to date
  fetched_at timestamptz
  offered_count int, relevant_count int
  created_at

flyer_offer                            -- relevant candidates only; append-only
  id uuid PK, flyer_run_id FK
  name_as_printed text, name_norm text, name_original text?, brand text?
  size_qty numeric?, size_unit FK unit?
  price_basis CHECK IN (each, per_lb, per_oz, multi, bogo, percent_off, dollar_off)
  price numeric(12,4)?                 -- money for each/per_lb/per_oz/multi/bogo
  basis_qty numeric?                   -- multi: "3 for $5" is basis_qty 3, price 5
  discount numeric(12,4)?              -- percent_off: percent; dollar_off: amount
  requires_loyalty bool, limit_qty numeric?
  valid_from date, valid_to date
  department text?, item_ref text?     -- the source's own item id, when it has one
  source_page int?, source_bbox numeric[4]?   -- image sources: page and box, page fractions
  confidence numeric(3,2)
  relevance CHECK IN (alias, purchased, recipe, list, watched)
  created_at

flyer_offer_link                       -- decision-only: every column but flyer_offer_id
  flyer_offer_id PK FK
  status CHECK IN (auto, suggested, confirmed, rejected)
  product_id FK?, resolution text?, suggestions jsonb?
  size_inherited bool default false
  corrected_price numeric(12,4)?       -- a person's correction of the printed price (2W)
  corrected_size_qty numeric?, corrected_size_unit FK unit?   -- and of its size
  decided_by FK user?, decided_at timestamptz?

flyer_offer_crop                       -- image and region sources only (2W)
  flyer_offer_id PK FK, media_path text, created_at

flyer_alias                            -- learned on confirm; like receipt_alias, kept apart
  vendor_id FK, name_norm text, product_id FK?, ignore bool
  confirmed_count int, last_seen_at
  UNIQUE (vendor_id, name_norm)
```

`ingredient.watch bool default false`. `lookup_request.kind` gains `flyer`, owned by `ad_source_id`.

`flyer_run` and `flyer_offer` join the append-only list, and `flyer_offer_link` joins the decision-only list, both in `backend/app/core/grants.py`. No float anywhere: prices, quantities, discounts and box fractions are `numeric` and are serialized as strings.

An offer stays as printed; a person's correction of its price or size is kept on its link, and every read model uses the corrected value where there is one.

Flyer aliases are kept apart from receipt aliases. A circular says "Organic Fuji Apples" where a receipt says "ORG FUJI APL", and mixing the two would make each text space a worse fuzzy match for the other.

## Scheduling

The worker checks once an hour, alongside the listing refresh. An enabled source that is not paused is due when all of the following hold:

- its ad week has started;
- that week has no `ok` or `empty` run yet;
- no request for the source is open;
- no receipt or re-read job is pending or running;
- for an image source, the model server answers.

A source gets at most three attempts per ad week, at least a day apart, and a source failing in two consecutive weeks is paused, with the vendor refresh back-off. Kitchen ERP queues work only while a helper token exists, as listing refreshes do.

The ad week starts on the most recent `publish_weekday`. The report's printed `ad_week_from`/`ad_week_to` are kept as published, and `valid_from`/`valid_to` on each offer may differ from them.

## The helper's report

`GET /lookup-requests` carries, for `flyer` requests, the source's `mechanism`, `adapter`, `store_ref`, `tiles` and the ad week expected. The helper answers with `POST /flyer-reports` (`products:suggest`), validated against a new contract schema, `FlyerReport`:

- `request_id`, `status`, `reason`, `adapter_version`, `ad_week_from`, `ad_week_to`, `fetched_at`;
- `candidates`: at most 2000, each with the `flyer_offer` fields above except `relevance`.

A report that fails validation is refused with 422 and stores no run and no offer. When its `request_id` names an open flyer request, that request is answered as a failed attempt: the refusal is recorded in `lookup_answer` against it, it counts toward the week's attempts and the source's pause like a `failed` run, and the inbox reads it as "the adapter needs attention". A report on a closed request is refused with 409. A report may also arrive on a request already answered: a replay of the archived payload with a newer adapter version (U5).

The response lists, for each candidate in order, the id of the offer it became, or null when it wasn't relevant. For an image or region source the helper then posts each stored offer's page crop to `POST /flyer-offers/{id}/crop` (`products:suggest`; a PNG up to 1 MB, metadata stripped), kept as `flyer_offer_crop` and served under `/api/v1/media` with a session, like product photos (2W).

Kitchen ERP never sees the raw payload. The helper keeps it for twelve months (helper SPEC §13).

## Relevance

A candidate is relevant to a kitchen when one of these holds, checked in order:

1. Its normalized name matches a `flyer_alias` for the vendor that isn't `ignore`.
2. It and a watched ingredient appear within one another, as in 3.
3. It and a product bought at one of the kitchen's locations in the last `FLYER_RELEVANCE_DAYS` (default 365), or that product's ingredient, appear within one another: pg_trgm strict word similarity (whole words only) in either direction of at least `FLYER_RELEVANCE_THRESHOLD` (default 0.6). A name never matches inside a longer word: "salt" is not in "salad". "Fuji Apples 3 lb Bag" holds "apple", and "Rolled Oats" sits inside a product name that adds a brand and a size.
4. Likewise for an ingredient a recipe calls for.
5. Likewise for an item on the kitchen's open shopping list.

A learned name marked `ignore` makes the offer irrelevant whatever else matches. The first that holds is recorded in `relevance`, so a watched ingredient that was also bought is recorded as `watched` (F13). Everything else is counted in `offered_count` and dropped, so "312 offers, 23 relevant" stays visible. A kitchen is the source location's `home_base_id`.

## Resolution

The receipt resolver's ladder (`04`, 2B) is split so that a name and a vendor can be resolved without a receipt line, and offers use it with their own aliases:

- **Exact flyer alias.** The offer links automatically (`status = auto`), or is rejected when the alias says `ignore`.
- **Fuzzy alias, model ranking over a shortlist, or similar names.** These become `suggested`, with the suggestions stored. A `watched` offer waits in the inbox; any other waits under "Other offers" (F13).
- **A person** confirms a suggestion, chooses or creates a product, or rejects the offer. Each choice upserts `flyer_alias`, as receipt confirmations upsert `receipt_alias`.

A source with `require_confirmation` sends every offer to the inbox, even one with an exact alias and whether or not it is watched, until it is turned off (2W): its reading isn't trusted yet, so it overrides F13. An offer with no size, linked to a product with one, takes the product's size for unit prices, and `size_inherited` is set.

## Read models

- `current_offers(home_base, on = today)`: linked offers (`auto` or `confirmed`) from the newest run per source and ad week, where `valid_from <= on <= valid_to`. At an ad-week boundary, the offer with the later `valid_from` wins.
- `offer_price(product, location, on)`: the effective unit price of the current offer, if any:
  - `multi` is price ÷ basis_qty;
  - `bogo` is half the price;
  - `percent_off` and `dollar_off` apply to the trailing regular price: the median of the product's non-promo prices that apply at that location (`offer_applicable`, posted prices left out) observed in the last `STALE_AFTER_DAYS`. With none, the offer has no effective price and is shown only as printed.
- `offer_history(product, location)`: every linked offer, in order, with its ad week.

The effective unit price is per canonical unit, as `price_norm`'s, and rounded by the price book's rule. An offer with no size, own or inherited, has none, nor has a percent or dollar off with no regular price, nor a dollar off with no size; each is shown as printed.

No price view, receipt resolution or default cost reads `flyer_offer` (F4). The read models read the price book only for the discount base above.

## Inbox

- **`flyer_offers`:** one row, "12 flyer offers to match", while any `watched` offers, or any offers from a source with `require_confirmation`, are `suggested` and still valid. Opens the offer queue at its watched offers. The queue's "Other offers" holds the rest of the suggested offers that are still valid, and never counts toward the row (F13). In the queue, each offer shows its printed name, price and basis, store, week and suggestions. Image and region sources also show the page crop (2W).
- **`flyer_sources`:** one row per source whose current ad week ended without a read: a `failed` or `not_allowed` run, or a refused report, on its last attempt. The row carries the reason; for a refused report it says "the adapter needs attention".

## Where it shows (2X)

- **Home card "This week's offers":** for the current kitchen, grouped by store, one line per linked offer, each linking to its product. Hidden when there are none.
- **Shopping list:** an item with a current offer shows "On offer at {store}" in olive.
- **Trip planner:** a "With this week's offers" toggle. When it's on, an offer's effective price stands in for the price book's price at that store inside the offer's dates. Each stop shows the expected saving against the trailing median regular price there.
- **Recipe costing:** a "with this week's offers" basis for a chosen kitchen. Offer prices from inherited sizes are marked as estimates.
- **Product page:** offer weeks marked on the price history chart, and a typical price from the trailing twelve weeks of offers and regular prices.
- **Vendor location page:** its `ad_source`, with:
  - mechanism, adapter and store ids, set as a data edit;
  - publication weekday;
  - enabled and require-confirmation;
  - the last runs with their counts and status;
  - "Check now".

## Privacy

Real chains, banners, store ids and kitchen locations exist only in the private helper and in a household's own database. This repository's code, tests, fixtures, comments, commits and pull requests use invented stores ("Larkspur Market") and invented circular payloads (SECURITY.md). `kerp flyers import` reads its seed from a file under `data/`, which is never committed.

## Slices and acceptance criteria

### 2U — Foundation and the hosted-circular adapter

2U-1 adds the tables, the scheduler, the report endpoint and relevance. 2U-2 adds the helper's flyer handler, the archive and the hosted-circular adapter. 2U-3 adds resolution, flyer aliases and the inbox, and 2U-4 adds the read models and the vendor-location panel.

- **U1.** The migration creates the flyer tables, `ingredient.watch` and the `flyer` kind. It upgrades and downgrades cleanly, and the runtime role cannot update or delete `flyer_run` or `flyer_offer`.
- **U2.** `kerp flyers import <file>` seeds `ad_source` rows from a YAML seed file, matched to vendor locations by their `key`. A second import changes nothing, and unmatched keys are reported.
- **U3.** The worker queues one `flyer` request per due source and none while a receipt is being read. A second check in the same ad week queues nothing. Three failed attempts in a week stop the retries.
- **U4.** A valid report creates one `flyer_run` and one `flyer_offer` per relevant candidate, records `offered_count` and `relevant_count`, and answers with each candidate's offer id or null. An invalid report is refused with 422, stores no run or offer, and answers its request as a failed attempt recorded for the inbox.
- **U5.** A report of an archived payload re-normalized by a newer adapter version creates a new run, and the read models then use it.
- **U6.** Relevance keeps a candidate matching a flyer alias, watched ingredient, purchase, recipe or list item, records the first that holds in that order, and drops the rest.
- **U7.** An exact flyer alias links an offer automatically. Anything else becomes `suggested`: a `watched` offer, or any offer from a source with `require_confirmation`, is counted in the inbox's `flyer_offers` row, any other only under the queue's "Other offers" (F13). Confirming either upserts the alias, and the next week's same name links automatically.
- **U17.** An ingredient's page has a "Watch for offers" switch that sets `ingredient.watch`; the next week's offers for it count in the inbox.
- **U8.** A `failed` or `not_allowed` run, or a refused report, on a week's last attempt gives one `flyer_sources` inbox row per source, with its reason, never one per missing offer.
- **U9.** `current_offers` returns linked offers valid today for the kitchen's locations. An offer past `valid_to` is gone, and at a boundary the later `valid_from` wins.
- **U10.** No price view, receipt resolution, recipe cost or plan changes when offers are reported.
- **U11.** The hosted-circular backend's robots.txt allows the requests the adapter makes (checked 2026-11-05). Should that change, its sources are reported `not_allowed` and never fetched (F9).
- **U12.** Given a merchant and store id from `store_ref`, the adapter fetches the current ad week's items, archives the raw JSON, and validates it against its expected shape before normalizing.
- **U13.** A response of an unexpected shape is a `failed` run with the validation error, and the inbox row says the adapter needs attention.
- **U14.** On one sampled week per kitchen, the candidate count for one store is within 10% of the items its own viewer shows. The count is recorded privately.
- **U15.** Loyalty qualifiers, limits and multi-buy prices are carried into `requires_loyalty`, `limit_qty` and `price_basis`.
- **U16.** An offer with no size takes its linked product's size, with `size_inherited` set. Unit prices are computed only for sized offers.

### 2V — Storefront and itemized adapters

- **V1.** The storefront adapter reads the deals of the store in `store_ref` explicitly, and the archived payload names that store.
- **V2.** Each itemized adapter is one small, replaceable module. Where a chain publishes both PDF and HTML, the choice is recorded after two weeks of watching.
- **V3.** Each yields at least 80% of the items its site shows on one hand-counted week, with `price_basis` right for each, per-pound, multi-buy and BOGO offers.
- **V4.** With every 2U–2V source enabled, a week's runs finish without one failing source stopping another.
- **V5.** Before 2W begins, every linked product at every enabled source has four consecutive weekly offer points, and `offer_history` returns them in order.

### 2W — Image, PDF and region circulars with a vision model

- **W1.** A benchmark on hand-labelled synthetic circular pages and price crops, bilingual ones included, compares the candidate vision models and sets the default `FLYER_VISION_MODEL`. Its results are recorded in the helper.
- **W2.** The image adapter rasterizes each page at 1600 px or more on the long edge, archives the pages, and records the page count and ad week.
- **W3.** Each page is tiled by `tiles`. Results are merged with duplicates removed on name and price, and every candidate carries `source_page` and `source_bbox`.
- **W4.** A region source takes each item's name, box and codes from data and reads only its price from the item's crop; the price text is parsed in code into `price_basis`, and the first code is the offer's `item_ref`.
- **W5.** A verifier pass re-reads tiles and crops with low confidence or failing price checks: a price that is zero, negative, or more than three standard deviations from that store's department median.
- **W6.** On three hand-labelled pages per source, recall of offers with a correct price is at least 85% and precision at least 90%. Until then the source keeps `require_confirmation`, and every one of its offers goes to the inbox.
- **W7.** The helper posts each stored image or region offer's crop. The offer queue shows it beside the parsed fields, and a corrected price or size is kept on the link, leaving the offer as printed.
- **W8.** Bilingual text is normalized to an English `name_as_printed`, with the printed text kept in `name_original`.
- **W9.** The helper unloads the vision model after the last page.

### 2X — Where offers are used

- **X1.** The planner's "With this week's offers" uses `offer_price` inside each offer's dates. Each stop shows its expected saving against the trailing median regular price there, and the plan is unchanged with the toggle off.
- **X2.** Recipe costing has a "this week's offers" basis for a kitchen. Estimates from inherited sizes are marked.
- **X3.** The product page chart marks offer weeks, and shows a typical price from the trailing twelve weeks.
- **X4.** The Home card and the list mark follow UI-9.
- **X5.** With no linked offers, nothing new shows anywhere.

UI criteria UI-9.1–UI-9.5 in `11` are dormant until their slice.

## Build checklist

Updated with every pull request, so the work can stop and resume at any line.

- [x] Step 0: this spec (kitchen-erp #481), the roster and adapter notes (helper #28: `docs/FLYERS.md`, SPEC §13). Approved 2026-11-05
- [x] 2U-1: migration, `ad_source` import, scheduler, `POST /flyer-reports`, relevance, contract schema (U1–U6, U10). Migration 0066, `kerp flyers import`
- [x] 2U-2: helper `flyer` handler, raw archive, replay, the hosted-circular adapter (helper #29; U11–U13, U15). The owner allowed the banner's public token (2026-11-06); a sample week read every priced item. One grocery group's banners are seeded; the other group's sites don't answer a plain request, so their ids are still open
- [x] 2U-3: shared name resolver, flyer aliases, offer queue with "Other offers", inbox rows, "Watch for offers" (U7, U8, U17; F13). The report links exact aliases and suggests fuzzy aliases or similar names; the idle worker asks the model about one watched offer at a time. One answer covers every store's offer of a vendor's printed name. The other grocery group's banners were read in a browser and seeded 2026-11-07; every seeded store read its week
- [x] 2U-4a: read models and API, size inheritance (U9, U16). `GET /flyer-offers/current?home_base_id=` lists the kitchen's linked offers valid that day with their price per canonical unit; `GET /products/{id}/flyer-offers` is a product's offer history. A size is marked inherited when an offer links, by an alias or a person
- [x] 2U-4b: the vendor location's Circular card and its API (UI-9.5); live count (U14). `GET`, `PUT` and `PATCH /vendor-locations/{id}/circular` and `POST …/circular/check`; "Check now" lifts a pause and reads a week again even when it was read. The live count was taken on 2026-11-08 for one store per kitchen and passed; it is recorded in the private helper
- [ ] 2V: storefront and itemized adapters, four-week gate (V1–V5)
  - [x] V1–V3 probes and adapters (helper #32, 2026-11-09). The storefront adapter reads one store's deals and archives the store it read for; its hand count read every item. One chain listed as itemized is a hosted circular, read by the 2U adapter with 97% recall. One chain's own ad is disallowed by robots.txt, and its only other source is a consumer aggregator, so it waits on a ruling
  - [x] V4: a request left unanswered past its week is closed, so it never stops its source being queued again (#494)
  - [ ] Seed the new sources in the household, then V5: four consecutive weekly offer points per linked product at every enabled source
- [ ] 2X: planner, costing, product chart, Home card, list mark (X1–X5, UI-9)
- [ ] 2W: vision benchmark, image, PDF and region adapters, verifier, crops (W1–W9)

## Open questions

- Can the first itemized chain's ad be read from its own data request, without a browser? Yes, but its prices are printed only on the page (2U probe, 2026-11-05), so it moved to 2W as a region source (F12).
- PDF-first or HTML-first, for chains that publish both? Neither: the one chain that publishes both serves its PDF as page images, and its item data comes from the hosted circular (2V, 2026-11-09).
- Is a size lookup worth adding for hosted-circular offers? Decide after measuring how often `size_inherited` is set, and how often it's wrong.
