# 17 — Phase 4: Shopping Lists and the Trip Planner

**Status: approved 2026-10-12 (Phase 4 review); rulings are folded inline.**

Phase 4 makes the price book plan a trip. It takes a kitchen's shopping list,
the drive times between the kitchen and its shops, and the household's value of
an hour, and answers: which stores, in what order, buying what at each, and what
the alternatives cost in money and minutes. Three sub-phases build and deploy in
order: 4A gives each kitchen one shared list and the kitchen switcher the UI
addendum deferred; 4B adds drive times and the planner; 4C closes the loop at
the store, where a stop's edited prices and check-offs become observations and a
purchase linked to the plan. The `features` list on `/health` gains `plan`,
keyed on the 4A migration, and the Plan section appears once it is applied.

The Phase 4 decisions in `05-later-phase-design-notes.md` are taken as settled:
a list belongs to a kitchen; an item is an ingredient, optionally with a minimum
quality, or an exact product, never both; items can be deferrable; members claim
and check off from their phones; drive times come once per pair of places from
a self-hosted engine behind a Compose profile, multiplier first; navigation is
the phone's; the planner is as written there. The Phase 4 review (2026-10-12)
took the draft's recommendation on every point it was asked; each is marked
"(Phase 4 review, 2026-10-12)" where it settles the text, and the rulings are
listed by id at the end.

## Prerequisites and decisions taken

- Phases 1–3 pass their acceptance criteria. The planner needs `convert`
  (`app/units/convert.py`), the 0029 views (`offer_applicable`, `offer_latest`,
  `offer_latest_regular`, `ingredient_offer`), `opening_hours.is_open_at`
  (`None` for unknown hours), `models/geo.py` (`HomeBase`, `Place`,
  `VendorLocation.home_base_id`, the unused `stop_overhead_min`) and Phase 3's
  recipe cost lines.
- **One open list per kitchen.** A kitchen has exactly one open shopping list,
  "the {kitchen} list", enforced by a partial unique index. A trip plans from
  it; items checked off at a stop leave its open section; "Start a fresh list"
  archives it and opens the next (L3, L8).
- **Routing is OSRM behind a `routing` Compose profile**, following the `ollama`
  service's `profiles: ["llm"]` pattern in `compose.yaml`, over a Southern
  California extract clipped to a bounding box. When `ROUTING_URL` is unset or
  the engine does not answer, legs are estimated and marked so; the planner
  never refuses to plan because the container is off (P2, P3).
- **Kitchen scoping follows `09`.** Scoped endpoints take an explicit
  `home_base_id`, never inferred from the session; the current kitchen is a
  per-user server setting the client sends. Switching never hides price data
  recorded at the other kitchen's vendors: purchases, observations, offers and
  costing stay household-wide, and the kitchen filter on Compare and the cost
  table is explicit and off by default (L3).
- **Claims are soft.** A claim shows who is getting the item; anyone may check
  it off, and checking clears the claim (L2).
- **Plans are derived until chosen.** `POST /plan/trips` stores all three
  variants so the choice is auditable; `choose` freezes one with its stop
  items as the prediction 4C compares against; replanning creates a new plan
  (P1).
- **Amounts are a child table** of `numeric` with a unit foreign key, never
  JSONB (non-negotiable 1); `06` settled that aggregation keeps (amount, unit)
  pairs when units cannot convert (L1).
- **Compare prices moves to `/plan/compare`** in 4A with redirects (L4); Home
  gains the list panel above Recent purchases, on desktop only (L5); the phone
  tab "Purchases" becomes "List" (L6), and the phone kitchen switcher tops the
  More sheet (L7).
- **Stale means `stale_after_days`** (default 90; `pricebook_views`). **Value
  of time** is the household setting `PLAN_VALUE_OF_TIME_PER_HOUR`, default
  `20.00`, overridable per plan (P4, P5). **Archiving** keeps the old list
  readable and carries unchecked items forward by default, as new rows on the
  new list that remember the row they came from (L8). **"Done here"** makes
  one observation per bought line from the purchase, flagged `planned_price`
  when the price was not edited, and a shelf observation only for a price
  edited but not bought (S1).

## Data model additions

```
shopping_list(                                 -- 4A; one open list per kitchen
  id, home_base_id FK home_base, status CHECK IN (open, archived) DEFAULT open, created_at, archived_at?,
  UNIQUE (home_base_id) WHERE status = 'open'  -- partial index: "the {kitchen} list"
)
shopping_list_item(                            -- 4A; a fact a person stated
  id, list_id FK, kind CHECK IN (ingredient, product), ingredient_id FK?, product_id FK?,   -- CHECK exactly one, matching kind
  min_quality SMALLINT?, note?, deferrable BOOLEAN DEFAULT false,   -- min_quality: ingredient items only, 1–5 as product.quality_rating
  added_by FK app_user, added_from CHECK IN (manual, ingredient, recipe, stock), recipe_id FK recipe?,   -- stock: the Phase 5 hook, no FK yet
  claimed_by FK app_user?, claimed_at?, checked_at?, checked_by FK app_user?,   -- a soft claim; cleared by a check
  carried_from_item_id FK shopping_list_item?, -- set when archiving copied this row forward from the old list (L8)
  created_at, updated_at                       -- updated_at is the optimistic token
)
shopping_list_item_qty(                        -- 4A; (amount, unit) pairs, never JSONB
  id, item_id FK, seq INT, amount NUMERIC CHECK (amount > 0), unit FK unit.code, UNIQUE (item_id, seq)
)
user_setting(                                  -- 4A; one typed row per user, one column per setting
  user_id PK FK app_user, current_home_base_id FK home_base? ON DELETE SET NULL, updated_at
)
travel_leg(                                    -- 4B; free-flow, per ordered pair of places; no multiplier stored
  from_place_id FK place, to_place_id FK place, PRIMARY KEY (from_place_id, to_place_id),
  seconds INT, meters INT, source CHECK IN (osrm, estimate), profile TEXT DEFAULT 'car', engine_version?, computed_at
)
trip_plan(                                     -- 4B; the inputs, and the choice
  id, list_id FK, home_base_id FK, origin_place_id FK place, destination_place_id FK place,
  planned_at timestamptz, value_of_time_per_hour NUMERIC(8,2), max_stops SMALLINT CHECK (1..4),
  mileage_rate NUMERIC(8,4)?, stop_overhead_min SMALLINT, status CHECK IN (planned, underway, done, abandoned),
  chosen_variant_id FK trip_plan_variant?,     -- set once by choose
  created_by FK app_user, created_at, chosen_at?, finished_at?
)
trip_plan_variant(                             -- 4B; derived until chosen
  id, plan_id FK, kind CHECK IN (fastest, cheapest, balanced), UNIQUE (plan_id, kind),
  item_cost NUMERIC(12,4), driving_seconds INT, stop_seconds INT, mileage_cost NUMERIC(12,4), total_cost NUMERIC(12,4),
  estimated BOOLEAN, annotations JSONB         -- any leg from source = estimate; [{kind, item_id?, location_id?, amount?, minutes?, text}]
)
trip_plan_stop(                                -- 4B; one row per stop of a variant, in order
  id, variant_id FK, seq INT, vendor_location_id FK, UNIQUE (variant_id, seq),
  leg_seconds INT, leg_meters INT, leg_source CHECK IN (osrm, estimate), multiplier NUMERIC(4,2),
  arrive_at timestamptz, overhead_min SMALLINT, subtotal NUMERIC(12,4), hours_known BOOLEAN,
  done_at?, purchase_id FK purchase?           -- 4C
)
trip_plan_stop_item(                           -- 4B; what to buy at a stop
  id, stop_id FK, list_item_id FK, product_id FK, UNIQUE (stop_id, list_item_id),
  packs NUMERIC?, pack_price NUMERIC(12,4)?, line_cost NUMERIC(12,4), observation_id FK price_observation,   -- the price the plan rests on
  price_stale BOOLEAN, forced BOOLEAN,
  actual_price NUMERIC(12,4)?, actual_qty NUMERIC?, actual_unit FK unit.code?, got_here BOOLEAN DEFAULT false   -- 4C: edited in the aisle; "Get here"
)
purchase.trip_plan_id FK trip_plan?            -- 4C; nullable, as 02 anticipated
```

Lists, items, amounts, settings and plans are facts a person stated and use
ordinary DML under the runtime role's grants in `backend/app/core/grants.py`.
`travel_leg` is derived and rebuildable from places plus the engine, so it joins
the `TRUNCATABLE` list in the 4B migration. `trip_plan_variant`, `_stop` and
`_stop_item` are derived and may be recomputed only while their plan has no
`chosen_variant_id`; once chosen they are the frozen prediction, and nothing
rewrites them except the 4C actual columns and `done_at`. Each sub-phase ships
one reversible migration; the `plan` feature is keyed on 4A's (0050: `main`
was at 0049, `normalize_v3`, when this was approved; check the head before
merging) in `services/health.FEATURE_MIGRATIONS`. No inbox kind and no
API-token scope is added; every endpoint takes a session
(`api/deps.CurrentUser`), and the mutations a phone may retry take
`Idempotency`.

## 4A — Lists and the kitchen switcher

**The current kitchen.** `GET /api/v1/me/settings` returns
`{current_home_base_id}`; `PUT` sets it, refusing a kitchen that does not
exist. The client resolves the kitchen it sends on scoped calls in this order:
the server setting; the kitchen of the device's remembered location
(`components/purchases/LocationSelect.readLastLocation`, still browser-only);
the only kitchen when there is one; otherwise the first by name; a fallback is
written back. No scoped endpoint reads the setting itself: `/plan/list`
without `home_base_id` is a 422, as `09` requires.

**The list.** `GET /api/v1/plan/list?home_base_id=` returns the kitchen's open
list with its unchecked and checked items and their amounts, creating the open
list on first read in the same transaction; the partial unique index settles a
race. `POST /plan/list/items` adds an item (`home_base_id`, kind, ingredient or
product, amounts, note, deferrable, min_quality); `PATCH /plan/list/items/{id}`
edits those fields and requires the `updated_at` the client last saw, answering
`409 stale_item` with the current row when it differs; `DELETE` removes one.
`POST …/items/{id}/claim`, `unclaim`, `check` and `uncheck` are the
transitions: a claim records who and when and is only a display; a check sets
`checked_at` and `checked_by` and clears the claim; checking a checked item is
a 200, so a retried tap is harmless. The list page refetches every 15 seconds
while visible, so two phones in one store see each other's check-offs without
a push channel. Amounts display per lb, oz and fl oz through the unit-display
layer; storage stays metric.

**From a recipe.** `POST /plan/list/items/from-recipe {home_base_id, recipe_id,
scale}` adds the recipe's non-negligible lines, scaled. A pinned line adds a
product item for the pinned product; any other resolved line adds an ingredient
item with `added_from = recipe`. Quantities aggregate with `convert` in the
ingredient's context: a quantity that converts merges into the one canonical
amount row of an existing item for the same ingredient; a `ConversionFailure`
keeps the quantity as its own (amount, unit) row, as `06` settled; a range
takes its high; a text or absent quantity adds the item with the text as its
note and no amount row. Unmapped lines are skipped and counted in the response,
which links to the resolve page. The same recipe added twice merges.

**The switcher and the tag.** The sidebar footer in `components/Nav.tsx` gains
the current kitchen above the user line, a neutral chip with a menu of the
kitchens, shown only with more than one; on phones it tops the More sheet. The
palette's Actions group gains "Switch to {kitchen}" for each other kitchen
(`lib/paletteActions.ts`, feature `plan`). Every kitchen-scoped panel carries
the kitchen's name as a neutral tag; nothing household-wide carries one.

**Where the list appears.** The phone tab bar's second tab becomes "List"
(`/plan/list`) once `plan` is a feature, and Purchases moves under More with
Shop's other pages (T18 in `09`). Home's right column gains the "{kitchen}
list" panel above Recent purchases: the unchecked count, the five oldest
unchecked items with amounts, an inline ingredient typeahead, and "Open list";
the phone Home keeps its three recent purchases, with no list summary row,
since the List tab is one tap away (Phase 4 review, 2026-10-12: the panel is
desktop only, L5). The ingredient hub's dormant "Add to list" becomes its
secondary action, and
the recipe page gains "Add to list" with a scale field; both are small inline
panels confirmed through the Notice. Compare prices moves to Plan: nav item,
palette action and route become `/plan/compare`, `/shop/compare` redirects
with its parameters, and the page gains a "Kitchen: all · {name}" control,
off by default, restricting to locations of that kitchen. The cost table gains
the same control: `GET /api/v1/recipes/{id}/cost?home_base_id=` computes the
lines on demand with prices restricted to that kitchen's locations and stores
no snapshot, so `07`'s snapshot shape and household-wide history stand.
Find-nearby keeps its own kitchen picker, pre-set to the current kitchen;
Capture's no-geolocation fallback keeps the remembered location.

### Acceptance criteria

1. `GET /plan/list` without `home_base_id` is a 422; with one, it returns the
   kitchen's open list, created on first read; two concurrent first reads
   yield one list, and a second open list is refused by the database.
2. `PUT /me/settings` persists the current kitchen; a second browser reads it
   back; the fallback chain resolves an empty setting and writes it.
3. An item is an ingredient or a product, never both or neither (database
   check); `min_quality` is refused on a product item.
4. A synthetic recipe at scale 2 yields one canonical amount for convertible
   lines, an (amount, unit) row per failed conversion, the high of a range, a
   note for a text quantity, a product item for a pin; again, nothing changes.
5. A claim shows the claimant; a check by another user clears it and sets
   `checked_at`; a second check is a 200 with no change.
6. A `PATCH` with a stale `updated_at` answers `409 stale_item` with the
   current row; the list page shows "Changed by {name} · Reload".
7. The switcher appears only with two or more kitchens, in the sidebar footer
   and the More sheet; "Switch to {kitchen}" is in the palette; the choice
   survives a reload and a second device; the kitchen tag appears on the list
   page and the Home panel and on no household-wide page.
8. `/shop/compare` and `/compare` redirect to `/plan/compare` with their
   parameters; with the kitchen control off, Compare and the cost table show
   every qualifying price, and switching kitchens changes no figure.
9. With `plan` in `features`, the phone's second tab reads "List" and opens
   `/plan/list`, and Purchases is under More; without `plan`, nothing changes.
10. Every list control is at least 44px below 1024px with a visible focus ring
    and 4.5:1 contrast; an item can be added, claimed and checked by keyboard.

## 4B — Drive times and the planner

**Legs.** A leg is the free-flow time and distance from one place to another,
stored once per ordered pair in `travel_leg`. When a plan is requested, the
planner collects the pairs it needs among origin, destination and candidates,
finds the ones missing or estimated, and makes one OSRM call,
`GET {ROUTING_URL}/table/v1/car/{lon,lat;…}?sources=…&destinations=…&annotations=duration,distance`,
with timeout `ROUTING_TIMEOUT_S` (default 5). An answered cell is stored with
`source = osrm` and the engine version; a null cell, a timeout, a refused
connection or an unset `ROUTING_URL` stores `source = estimate`: haversine
times a road factor, at a speed chosen by the distance band. The factor and the
bands are config settings beside the multiplier table (Phase 4 review,
2026-10-12, P2): `PLAN_ESTIMATE_ROAD_FACTOR`, default `1.3`, and
`PLAN_ESTIMATE_SPEED_BANDS`, default `3:30,15:40,:60` (an upper bound in km and
a speed in km/h per band, the last band unbounded), so 30 km/h under 3 km,
40 km/h to 15 km and 60 km/h beyond. An estimate is replaced when the engine
next answers for that pair, never the reverse. Admins recompute with
`POST /api/v1/geo/travel-legs/recompute` or `kerp travel-legs recompute`, which
drop estimates; OSRM legs only with `--all`.

**Time of day.** Free-flow understates the household's traffic. A multiplier
table in config, `PLAN_TRAFFIC_MULTIPLIERS`, defaults to weekdays 06–10 ×1.35,
10–15 ×1.15, 15–20 ×1.45, otherwise ×1.0, and weekends 09–19 ×1.15, otherwise
×1.0, in `household_timezone` (Phase 4 review, 2026-10-12: these defaults
stand, and the road factor and speed bands are their own settings, above, P2).
It applies per leg at the leg's projected departure, and the multiplier used
and the arrival it yields are recorded on the stop the leg reaches; the stored
leg stays free-flow, and a variant with any estimated leg is marked
`estimated`.

**Candidates.** Active vendor locations within `PLAN_CANDIDATE_RADIUS_KM`
(default 25) of the list's kitchen, at most 15 nearest by straight line. A
location closed at its projected arrival in the ordering being scored is
excluded from that ordering (`is_open_at`); unknown hours are allowed, with
`hours_known = false` on the stop and an `hours_unknown` annotation; a stall
inherits its parent's hours as `effective_hours` does. `05` says "closed at the
planned time"; a third stop can come an hour later, so arrival is stricter.

**Offers.** Per item per candidate, the cheapest qualifying product. Qualifying
is the price book's rule as `07` states it, keyed by location: a query in
`services/trip_offers.py` over `ingredient_offer` (so a chain price applies at
every active location of the chain), restricted as
`recipe_costing.qualifying_prices` is to committed purchases and shelf prices,
never posted prices, of active products, with `min_quality` and a pinned
product honoured. `qualifying_prices` itself is not reused: its `DISTINCT ON
(observation_id)` collapses a chain price to one row on purpose, and the
planner needs one per location. Each amount row converts to the product's
canonical unit through `convert` with the product's context; a count row in
`each` for a product with `pack_count` rounds by count; a row that fails to
convert makes that product unusable (`cant_convert`), and an item no candidate
can fill is `unsourceable`. Packs round up through `_pack_canonical_qty` and
`_basket`, moved from `recipe_costing` to `services/packs.py`; without a pack
size, cost is quantity times unit price with `packs` null; a price older than
`stale_after_days` is used and marked `price_stale`.

**The search.** Items are independent given a set of stops: each goes to the
stop in the set with its cheapest offer, so the search is over sets and their
order, as `05` says. The planner enumerates every set of 1 to `max_stops`
candidates (1,940 sets at 15 candidates and 4 stops), assigns items and sums
item cost once per set, then scores every ordering of the set (at most 35,715
orderings in total): driving with multipliers, overhead per stop
(`vendor_location.stop_overhead_min`, else the plan's), arrival-hours checks,
and mileage when a rate is given. The objective is item cost plus value of time
times (driving plus stop time) in hours, plus mileage rate times miles
(`meters / 1609.344`). A set that cannot source an item some other set can is
infeasible; an item no set can source is excluded and annotated. `fastest` is
the feasible ordering with the least driving plus stop time, ties by objective;
`cheapest` the least item cost, ties by time; `balanced` the least objective;
the response states each one's marginal trade against `balanced` in money and
minutes.

**Annotations.** `forced_stop` (a product item, or an ingredient item with a
minimum quality, that one candidate alone can fill); `defer_suggestion` (a
deferrable item alone at its stop: the planner re-solves without it, at most
`max_stops` times, and reports the saving in money and minutes);
`stale_dependent` (how much item cost rests on stale prices, and whether
`cheapest` stays cheapest without them); `unsourceable` (the item and why: no
offer, `cant_convert`, quality, closed); `estimated_legs`; `hours_unknown`;
`closed_excluded` (a candidate left out for its hours).

**Endpoints.** `POST /api/v1/plan/trips {home_base_id, origin_place_id?,
destination_place_id?, planned_at, value_of_time_per_hour?, max_stops,
mileage_rate?, stop_overhead_min?}` creates a plan from the kitchen's open list
with its three variants (201); origin defaults to the kitchen's place,
destination to the origin, value of time to the household setting, `max_stops`
to 3, overhead to 10 minutes; an empty list is 422 `empty_list`. `GET
/plan/trips?home_base_id=` lists plans newest first; `GET /plan/trips/{id}`
returns variants, stops, stop items and annotations; `POST …/{id}/choose
{variant_id}` sets `chosen_variant_id` once (409 after); `POST …/{id}/abandon`
ends a planned or underway plan.

**Compose and the extract.** `compose.yaml` gains a `routing` service with
`profiles: ["routing"]`, `ghcr.io/project-osrm/osrm-backend` pinned,
`./data/routing:/data:ro`, `osrm-routed --algorithm mld /data/region.osrm`,
bound to `127.0.0.1:5000`. Env: `ROUTING_URL` (empty by default;
`http://routing:5000` with the profile on), `ROUTING_TIMEOUT_S`, and
`ROUTING_BBOX`, which only the host uses and joins `NOT_FORWARDED` in
`tools/check_compose_env.py` with that reason. `make routing-prepare` fetches
the Geofabrik Southern California extract into `data/routing/`, clips it to
`ROUTING_BBOX` with `osmium extract`, then runs `osrm-extract` (car profile),
`osrm-partition` and `osrm-customize` in the pinned image; clipping first keeps
preprocessing within a laptop's memory. `.env.example` documents the bbox
format and leaves it unset: a real bounding box is household geography.
`docs/licensing.md` gains OSRM (BSD-2-Clause, run as a container), osmium-tool
(GPL-3.0, a host tool only, never linked) and the ODbL extract (Phase 4 review,
2026-10-12, P3).

**Screens.** `/plan/trips` lists plans; "Plan a trip" is the primary action and
opens the inputs in a drawer. `/plan/trips/:id` shows the three variants as
cards with cost, minutes and the marginal trade, each with "Choose" until one
is chosen; each stop with its items and subtotal; the annotations as a neutral
list, stale and estimated in squash; and `components/geo/MapView` with the
kitchen pin, numbered stop pins and straight segments. Each stop's "Navigate"
opens the phone's maps application. "Replan" makes a new plan from the same
inputs and the list as it is now (P1). The layouts are in `10` under Plan.

### Acceptance criteria

11. With a stubbed OSRM table response, missing pairs are fetched in one call,
    stored `osrm` and not refetched; with `ROUTING_URL` unset the same pairs
    are estimates and the variants read `estimated`.
12. An OSRM answer replaces an estimated leg, never the reverse; `recompute`
    drops only estimates unless `--all`; a synthetic pair's estimate equals
    haversine × the road factor at the band's speed, with the default settings
    and again with changed ones.
13. On a synthetic grid of six locations and a kitchen, the three variants
    equal hand-computed figures to the cent and the second under each
    multiplier band, with the marginal trades in money and minutes.
14. A location closed at its projected arrival in one ordering is excluded
    there and kept in an ordering where it is open; unknown hours keep it,
    with `hours_known = false` and the annotation.
15. A chain price applies at every active location of the chain and a
    location-scoped price at its own only; a posted, uncommitted, voided or
    inactive-location price never fills an item; a stale one does, marked.
16. Packs round up per product; a count amount rounds by `pack_count`; no pack
    size means quantity times unit price; an amount that cannot convert makes
    the product unusable and, with no other, the item `unsourceable`.
17. The enumeration visits at most 1,940 sets and 35,715 orderings at 15
    candidates and 4 stops, and never scores more than 15 candidates.
18. A product item sold at one candidate yields `forced_stop`; a deferrable
    item alone at its stop yields `defer_suggestion` with the saving; a
    variant whose cheapest standing rests on a stale price says so.
19. `choose` freezes the variant and refuses a second choice; a plan made from
    a list that then changes is unchanged, and a new plan sees the change.
20. The trips page renders the three cards, the map with numbered pins and
    "Navigate" per stop; at 390px it reads without horizontal scrolling.
21. `make check-compose-env` passes with `ROUTING_BBOX` documented and not
    forwarded; with the `routing` profile off the stack starts and plans.

## 4C — At the store

**The stop.** `GET /api/v1/plan/trips/{id}/stops/{seq}` returns one stop of
the chosen variant: the store, "Stop {seq} of {n}", the items to get here with
planned packs and prices, the items assigned to later stops that this location
could also fill with the price difference ("Cheaper at stop {m}"), basket
progress and the estimated cost here; the screen is `/plan/trips/:id/stop/:seq`
and the phone layout is `10`'s (UI-4.8).
`PUT …/stops/{seq}/items/{item_id}/price {price, qty, unit}` holds an edited
price on the stop item (`actual_*`) and the row shows the neutral edited tint.
`POST …/stops/{seq}/get-here {item_id}` moves an item from a later stop here
(`got_here`), re-priced at this location's offer or the edited price.

**Done here.** `POST …/stops/{seq}/done {checked_item_ids}` is idempotent
through `Idempotency` and, in one transaction: creates one purchase with
`source = manual`, `status = committed`, this location, `purchased_at = now`,
`trip_plan_id`, and one line per checked item at its edited price or its
planned pack price times packs, through the manual-purchase path, which emits
one observation per line as `create_manual` does; writes a shelf observation
(`source = shelf`) for each item whose price was edited here but not checked,
a price read off the shelf and not bought; sets `checked_at` and `checked_by`
on the checked items and clears their claims; records `done_at` and
`purchase_id` on the stop; moves the plan to `underway` on the first stop and
to `done` after the last, with `finished_at`. A line taken at the planned
price carries the line flag `planned_price`, so a reader can tell a confirmed
price from an assumed one (Phase 4 review, 2026-10-12, S1: a bought line's
observation comes from the purchase line whether or not its price was edited,
flagged `planned_price` when it was not; a shelf observation is written only
for a price edited but not bought). "Done here" on a plan with no chosen
variant, or on a stop already done, is a 409 with the reason; a stop with
nothing checked records `done_at` and no purchase.

**After the trip.** `GET /plan/trips/{id}` reports predicted against actual
once a stop is done: the chosen variant's item cost beside the sum of its
linked purchases, per stop and in total. The list stays open with its unchecked
leftovers. "Start a fresh list" (`POST /plan/list/archive {home_base_id,
carry_over}`) archives the open list and opens a new one, in one transaction.
With `carry_over` true (the default) each unchecked item becomes a new row on
the new list with its kind, ingredient or product, amount rows, note,
`deferrable`, `min_quality`, `added_from` and `recipe_id` preserved,
`carried_from_item_id` pointing at the old row, and no claim; the old rows are
untouched. An archived list stays readable at `/plan/list?list_id=`, with its
checked and unchecked items as they were, and carried items say "Carried to the
current list" (Phase 4 review, 2026-10-12, L8).

### Acceptance criteria

22. The stop screen lists this stop's items and the "Cheaper at stop {m}" items
    with the difference, and "Get here" moves one with its price re-done.
23. "Done here" with three checked items, one edited, writes one committed
    manual purchase linked to the plan with three lines and three observations,
    no shelf observation, and `planned_price` on the unedited two.
24. An item edited and left unchecked yields exactly one shelf observation and
    no purchase line; a retried "Done here" with the same key changes nothing.
25. The plan is `underway` after the first stop and `done` after the last;
    "Done here" on a stop already done, or on an unchosen plan, is a 409.
26. The list keeps its unchecked items after the trip; "Start a fresh list"
    archives it and carries them forward by default as new rows with
    `carried_from_item_id` set, `added_from` kept and claims cleared; the
    archived list stays readable with its rows unchanged; the trip page shows
    predicted beside actual after a stop.
27. Every stop-screen control is at least 44px, check circles 28px, price
    buttons 44px with the edited tint; "Done here · record purchase" is
    anchored and reachable from the keyboard.
28. Nothing on the Plan pages is blue except actions; stale, estimated and
    unsourceable marks are squash; the kitchen tag and selection are neutral;
    the cheapest variant's figures are not coloured.

## Fixtures and personal data

Test geography is the synthetic grid `SECURITY.md` prescribes, latitude 33.0 to
34.0 and longitude −121.0 to −120.0, with invented vendors and stalls at
invented addresses suppressed as such. OSRM is never run in tests: the table
call is stubbed with a hand-written response the planner tests compute from,
and the estimate path is tested without any network. No real extract, bounding
box, address, household place or store name appears in a fixture, test name,
docstring, commit message or `.env.example`; `data/routing/` is gitignored.
Screenshots come only from the demo stack, whose seed gains a list and a plan.

## Out of scope for Phase 4

Stock-driven items and the "use soon" list (Phase 5; `added_from = stock` is
only a value), camera scanning and offline sync (Phase 6), traffic calibration
from trips, Valhalla, route geometry, named extra lists, and editing recipes.

## Rulings from the Phase 4 review (2026-10-12)

The household ruled on the review board on 2026-10-12; every card took the
draft's recommendation, with no notes. The rulings are folded into this
document, into `09`–`11` (the Plan layouts and UI-8) and into CLAUDE.md's
approved scope.

- **L1** Amounts are the child table `shopping_list_item_qty` (`numeric`
  amount, unit foreign key, `seq`), never JSONB.
- **L2** Claims are soft: a claim shows a name, anyone may check the item, and
  a check clears the claim.
- **L3** Kitchen scope: lists and trips take an explicit `home_base_id`;
  Compare and the cost table get an optional kitchen filter, off by default;
  everything else stays household-wide, and the per-kitchen cost view is
  computed on demand, never snapshotted.
- **L4** Compare prices moves to `/plan/compare` in 4A, with `/shop/compare`
  and `/compare` redirecting.
- **L5** The Home list panel is desktop only: the count, the five oldest
  unchecked items, inline add and "Open list"; the phone Home keeps recent
  purchases.
- **L6** The phone tab Purchases becomes List at 4A; Purchases goes under More
  with Shop's other pages.
- **L7** The phone kitchen switcher sits at the top of the More sheet, and the
  List tab's header shows the kitchen's name.
- **L8** Archiving keeps the old list readable and carries unchecked items
  forward to the new list as new rows that remember where they came from.
- **P1** Plans are derived until chosen: all three variants are stored,
  `choose` freezes one and its stop items, and replanning creates a new plan.
- **P2** The multiplier table, the road factor 1.3 and the 30/40/60 km/h bands
  are config settings with the draft's defaults; a variant with an estimated
  leg is annotated `estimated`; multipliers are applied per leg and recorded
  on the stop each leg reaches, with its projected arrival.
- **P3** The OSRM extract is Geofabrik Southern California clipped by
  `ROUTING_BBOX`, which is never tracked; MLD; prepared by
  `make routing-prepare`.
- **P4** Value of time is `PLAN_VALUE_OF_TIME_PER_HOUR`, default `20.00`, with
  a per-plan override.
- **P5** Stale reuses `stale_after_days`; there is no second threshold.
- **S1** "Done here": a bought line's observation comes from the purchase
  line, flagged `planned_price` when unedited; a shelf observation only for a
  price edited but not bought.
