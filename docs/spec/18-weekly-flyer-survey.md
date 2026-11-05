# 18 — Weekly flyer survey (2U–2Y)

**Status: proposed 2026-11-05, for approval as Phase 2 sub-phases 2U–2Y.** It was written from a design-session handoff ("Doc 15", which named the household's own stores). The rulings taken on 2026-11-05 (F1–F11 below) are folded in. The household's store roster and each chain's adapter notes live in the private `kitchen-erp-products` repository (`docs/FLYERS.md`), never here. Every store, brand and price in this document is invented.

## Why

Most chains publish a weekly circular: this week's sale prices, store by store. The price book only learns a sale price after someone pays it or spots it on a shelf. A surveyor that reads the circulars around each kitchen could tell the trip planner which store has this week's list on offer. It could also let recipe costing price a recipe at this week's offers, and over a few months build a series of offer weeks that shows what "a good price" is for each product at each store.

This is the mirror image of `13`. `13` ingests catalog data: what a product is, its photo, its maker. This document ingests offer data: what a product costs this week, at which store, under what conditions.

The feature is best-effort. Retailer websites are not stable APIs, and adapters will break. The one invariant is that a broken adapter degrades gracefully: that week's offers from that store are missing and flagged. It never corrupts stored data and never blocks the rest of the system.

## Rulings (2026-11-05)

- **F1.** The household's store roster, kitchen locations and per-chain adapter notes live in the private helper (`docs/FLYERS.md`), as retailer adapters already do. This document uses invented stores.
- **F2.** No chain, banner or flyer-platform name appears in this repository: not in code, tests, specs, commits or pull requests. Mechanisms are named generically, for example "hosted circular" or "storefront platform".
- **F3.** The helper fetches, archives and normalizes. Kitchen ERP keeps the registry and queues the weekly work. It also filters for relevance, resolves offers to products, fills the inbox and serves every read model. `products:read` stays "the queue only" (helper SPEC §8.1), and Kitchen ERP still makes no outbound calls (non-negotiable 9).
- **F4.** An offer is not a price observation. Offers are kept in their own append-only table, with a validity window and a price basis. The default price book, receipt resolution, recipe costing and the planner never read them unless asked. A learned alias links an offer to a product automatically, and anything else waits in the inbox. No person has to accept each offer, because an offer never becomes a price.
- **F5.** The work is Phase 2 sub-phases 2U–2Y, one per handoff phase A–E.
- **F6.** This week's offers appear on a Home card and as a mark on shopping-list items. The inbox holds only what needs a person: offers to match and sources that failed.
- **F7.** The vision model for image and PDF circulars is chosen by a benchmark at the start of 2X, like 2O's. Text normalization reuses the helper's `LLM_MODEL`. Model names are configuration.
- **F8.** A headless browser is added only when an adapter cannot read its source's own data request. It runs as its own container beside the helper, outside Kitchen ERP's Compose project.
- **F9.** Flyer fetching follows the helper's existing rules (helper SPEC §5): robots.txt is honoured for every host, including undocumented backends, and only facts are kept. A source that robots.txt disallows is recorded as `not_allowed` and is never fetched.
- **F10.** Relevance is derived from the household's own data, plus a `watch` flag on ingredients. One threshold in configuration; no per-kitchen settings table.
- **F11.** There is no fixed quiet window. On publication day Kitchen ERP queues a source's request once no receipt is being read and, for image sources, once the model server answers. A sleeping model server delays the run; it never fails it.

## How circulars are published

Circulars come in five mechanism classes, and an adapter is written per mechanism, not per store:

- **Hosted circular.** An embedded widget scoped to a store id, served from a third-party circular platform's JSON backend. These give names and prices, but usually no pack sizes.
- **Storefront platform.** A store-scoped weekly-deals collection on a hosted e-commerce platform. The store must be selected explicitly, because the site's default store may be another branch.
- **Itemized first-party page.** A browsable item list on the chain's own site, parsed with per-site selectors. Some chains also publish a PDF edition.
- **Image or PDF pages.** The circular is page images or a PDF. These need rasterization and a vision model.
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
  mechanism CHECK IN (hosted_circular, storefront, itemized, image)
  adapter text                        -- the helper's adapter name
  store_ref jsonb                     -- ids the adapter needs; an object of strings, opaque here
  publish_weekday smallint 1..7       -- ISO weekday the ad week starts
  enabled bool default false
  require_confirmation bool           -- default true for image, false otherwise (2X)
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
  name_as_printed text, name_original text?, brand text?
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

flyer_offer_link                       -- decision-only (status, product_id, decided_*)
  flyer_offer_id PK FK
  status CHECK IN (auto, suggested, confirmed, rejected)
  product_id FK?, resolution text?, suggestions jsonb?
  size_inherited bool default false
  decided_by FK user?, decided_at timestamptz?

flyer_alias                            -- learned on confirm; like receipt_alias, kept apart
  vendor_id FK, name_norm text, product_id FK?, ignore bool
  confirmed_count int, last_seen_at
  UNIQUE (vendor_id, name_norm)
```

`ingredient.watch bool default false`. `lookup_request.kind` gains `flyer`, owned by `ad_source_id`.

`flyer_run` and `flyer_offer` join the append-only list, and `flyer_offer_link` joins the decision-only list, both in `backend/app/core/grants.py`. No float anywhere: prices, quantities, discounts and box fractions are `numeric` and are serialized as strings.

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

A report that fails validation is refused with 422 and the request stays open. A report on a closed request is refused with 409. Kitchen ERP never sees the raw payload. The helper keeps it for twelve months (helper SPEC §13).

## Relevance

A candidate is relevant to a kitchen when one of these holds, checked in order:

1. Its normalized name matches a `flyer_alias` for the vendor that isn't `ignore`.
2. Its name is similar (trigram, at least `FLYER_RELEVANCE_THRESHOLD`, default 0.45) to a product bought at one of the kitchen's locations in the last `FLYER_RELEVANCE_DAYS` (default 365).
3. It's similar to an ingredient a recipe calls for.
4. It's similar to an item on the kitchen's shopping list.
5. It's similar to a watched ingredient.

The first that holds is recorded in `relevance`. Everything else is counted in `offered_count` and dropped, so "312 offers, 23 relevant" stays visible. A kitchen is the source location's `home_base_id`.

## Resolution

The receipt resolver's ladder (`04`, 2B) is split so that a name and a vendor can be resolved without a receipt line, and offers use it with their own aliases:

- **Exact flyer alias.** The offer links automatically (`status = auto`), or is rejected when the alias says `ignore`.
- **Fuzzy alias, model ranking over a shortlist, or similar names.** These become `suggested`, with the suggestions stored, and the offer waits in the inbox.
- **A person** confirms a suggestion, chooses or creates a product, or rejects the offer. Each choice upserts `flyer_alias`, as receipt confirmations upsert `receipt_alias`.

A source with `require_confirmation` sends every offer to the inbox, even one with an exact alias, until it is turned off (2X). An offer with no size, linked to a product with one, takes the product's size for unit prices, and `size_inherited` is set.

## Read models

- `current_offers(home_base, on = today)`: linked offers (`auto` or `confirmed`) from the newest run per source and ad week, where `valid_from <= on <= valid_to`. At an ad-week boundary, the offer with the later `valid_from` wins.
- `offer_price(product, location, on)`: the effective unit price of the current offer, if any:
  - `multi` is price ÷ basis_qty;
  - `bogo` is half the price;
  - `percent_off` and `dollar_off` apply to the trailing regular price (`regular_prices`), and give none when there isn't one.
- `offer_history(product, location)`: every linked offer, in order, with its ad week.

None of these read `price_observation`, and no price view reads `flyer_offer` (F4).

## Inbox

- **`flyer_offers`:** one row, "12 flyer offers to match", while any are `suggested`. Opens the offer queue: each offer shows its printed name, price and basis, store, week and suggestions. Image sources also show the page crop (2X).
- **`flyer_sources`:** one row per source whose current ad week has a `failed` or `not_allowed` run after its last attempt, with the reason. A report refused for validation says "the adapter needs attention".

## Where it shows (2Y)

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

### 2U — Foundation and the first itemized adapter

2U-1 adds the tables, the scheduler and the report endpoint. 2U-2 adds the helper's flyer handler, the archive and the first itemized adapter. 2U-3 adds relevance, resolution and the inbox, and 2U-4 adds the read models and the vendor-location panel.

- **U1.** The migration creates the five tables, `ingredient.watch` and the `flyer` kind. It upgrades and downgrades cleanly, and the runtime role cannot update or delete `flyer_run` or `flyer_offer`.
- **U2.** `kerp flyers import <file>` seeds `ad_source` rows from a seed file, matched to vendor locations by `platform_store_ref` or vendor and address. A second import changes nothing, and unmatched lines are reported.
- **U3.** The worker queues one `flyer` request per due source and none while a receipt is being read. A second check in the same ad week queues nothing. Three failed attempts in a week stop the retries.
- **U4.** A valid report creates one `flyer_run` and one `flyer_offer` per relevant candidate, and records `offered_count` and `relevant_count`. An invalid report is refused with 422 and changes nothing.
- **U5.** A report of an archived payload re-normalized by a newer adapter version creates a new run, and the read models then use it.
- **U6.** Relevance keeps a candidate matching a purchase, recipe, list item, watched ingredient or flyer alias, records which one, and drops the rest.
- **U7.** An exact flyer alias links an offer automatically. Anything else becomes `suggested` and appears in the inbox's `flyer_offers` row. Confirming one upserts the alias, and the next week's same name links automatically.
- **U8.** A `failed` or `not_allowed` run after its last attempt gives one `flyer_sources` inbox row per source, with its reason, never one per missing offer.
- **U9.** `current_offers` returns linked offers valid today for the kitchen's locations. An offer past `valid_to` is gone, and at a boundary the later `valid_from` wins.
- **U10.** No price view, receipt resolution, recipe cost or plan changes when offers are reported.
- **U11.** The first itemized adapter, on one hand-counted ad week per kitchen, yields at least 80% of the items its site shows, with `price_basis` right for each, per-pound, multi-buy and BOGO offers. The count is recorded privately.

### 2V — The hosted-circular adapter

- **V1.** Before any code is written, the backend host's robots.txt is checked. If it disallows the requests, 2V stops and the sources are recorded `not_allowed` (F9).
- **V2.** Given a merchant and store id from `store_ref`, the adapter fetches the current ad week's items, archives the raw JSON, and validates it against its expected shape before normalizing.
- **V3.** A response of an unexpected shape is a `failed` run with the validation error, and the inbox row says the adapter needs attention.
- **V4.** On one sampled week, the candidate count for one store is within 10% of the items its own viewer shows.
- **V5.** Loyalty qualifiers, limits and multi-buy prices are carried into `requires_loyalty`, `limit_qty` and `price_basis`.
- **V6.** An offer with no size takes its linked product's size, with `size_inherited` set. Unit prices are computed only for sized offers.

### 2W — Storefront and remaining itemized adapters

- **W1.** The storefront adapter reads the deals of the store in `store_ref` explicitly, and the archived payload names that store.
- **W2.** Each remaining itemized adapter is one small, replaceable module. Where a chain publishes both PDF and HTML, the choice is recorded after two weeks of watching.
- **W3.** Each meets U11's 80% threshold on one sampled week.
- **W4.** With every 2U–2W source enabled, a week's runs finish without one failing source stopping another.
- **W5.** Before 2X begins, every linked product at every enabled source has four consecutive weekly offer points, and `offer_history` returns them in order.

### 2X — Image and PDF circulars with a vision model

- **X1.** A benchmark on hand-labelled synthetic circular pages, bilingual ones included, compares the candidate vision models and sets the default `FLYER_VISION_MODEL`. Its results are recorded in the helper.
- **X2.** The adapter rasterizes each page at 1600 px or more on the long edge, archives the pages, and records the page count and ad week.
- **X3.** Each page is tiled by `tiles`. Results are merged with duplicates removed on name and price, and every candidate carries `source_page` and `source_bbox`.
- **X4.** A verifier pass re-reads tiles with low confidence or failing price checks: a price that is zero, negative, or more than three standard deviations from that store's department median.
- **X5.** On three hand-labelled pages per source, recall of offers with a correct price is at least 85% and precision at least 90%. Until then the source keeps `require_confirmation`, and every one of its offers goes to the inbox.
- **X6.** The offer queue shows the page crop beside the parsed fields, and the price and size can be corrected before confirming.
- **X7.** Bilingual text is normalized to an English `name_as_printed`, with the printed text kept in `name_original`.
- **X8.** The helper unloads the vision model after the last page.

### 2Y — Where offers are used

- **Y1.** The planner's "With this week's offers" uses `offer_price` inside each offer's dates. Each stop shows its expected saving against the trailing median regular price, and the plan is unchanged with the toggle off.
- **Y2.** Recipe costing has a "this week's offers" basis for a kitchen. Estimates from inherited sizes are marked.
- **Y3.** The product page chart marks offer weeks, and shows a typical price from the trailing twelve weeks.
- **Y4.** The Home card and the list mark follow UI-9.
- **Y5.** With no linked offers, nothing new shows anywhere.

UI criteria UI-9.1–UI-9.5 in `11` are dormant until their slice.

## Build checklist

Updated with every pull request, so the work can stop and resume at any line.

- [ ] Step 0: this spec (kitchen-erp), the roster and adapter notes (helper `docs/FLYERS.md`, SPEC §13). Approved: —
- [ ] 2U-1: migration, `ad_source` import, scheduler, `POST /flyer-reports`, contract schema (U1–U5)
- [ ] 2U-2: helper `flyer` handler, raw archive, replay, robots check, first itemized adapter, text normalization (helper)
- [ ] 2U-3: relevance, shared name resolver, flyer aliases, offer queue and inbox rows (U6–U8)
- [ ] 2U-4: read models and API, vendor-location panel (U9–U10); live hand count (U11)
- [ ] 2V: hosted-circular adapter (V1–V6)
- [ ] 2W: storefront and itemized adapters, four-week gate (W1–W5)
- [ ] 2Y: planner, costing, product chart, Home card, list mark (Y1–Y5, UI-9)
- [ ] 2X: vision benchmark, image and PDF adapter, verifier, crops (X1–X8)

## Open questions

- Can the first itemized chain's ad be read from its own data request, without a browser? (Answered in 2U-2.)
- PDF-first or HTML-first, for chains that publish both? (Answered in 2W.)
- Is a size lookup worth adding for hosted-circular offers? Decide after measuring how often `size_inherited` is set, and how often it's wrong.
