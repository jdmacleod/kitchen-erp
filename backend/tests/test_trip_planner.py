"""The trip planner (17, 4B, package 6): criteria 13 to 19, rulings P1, P2, P4, P5.

Geography is the synthetic grid of ``geo_helpers``; vendors, products and prices
are invented. The pure search is tested on hand-computed figures with legs
given directly; the end-to-end tests seed the price book through the API and
let the estimate path run with every socket beyond the database refused, or
answer the routing engine with the stub from ``test_travel_legs``.

The hand-computed case (criterion 13). A kitchen K and shops A, B, C; free-flow
legs, symmetric: K-A 600 s / 5,000 m, K-B 900 / 8,000, K-C 1,200 / 12,000,
A-B 300 / 2,500, B-C 600 / 5,000, A-C 900 / 7,000. The trip starts Wednesday
12:00 in Los Angeles (19:00 UTC), inside the 10-15 band, so every leg is ×1.15:
K-A 690, K-B 1,035, K-C 1,380, A-B 345, B-C 690, A-C 1,035. Ten minutes per
stop; an hour is worth 20.00. Items: barley (A 4.00, B 3.00, C 2.00), milk
(A 3.00, C 2.40) and dates, deferrable (B 5.00, C 6.00).

Sets that buy everything: {C} 10.40 in 1,380 + 600 + 1,380 = 3,360 s;
{A, B} 11.00 (barley at B, milk at A, dates at B) in 690 + 600 + 345 + 600 +
1,035 = 3,270 s either way round; {B, C} 9.40 in 1,035 + 600 + 690 + 600 +
1,380 = 4,305 s. {A} misses the dates and {A, C} and {A, B, C} would buy nothing
at A. Time costs: 3,270 s is 18.1667, 3,360 s is 18.6667, 4,305 s is 23.9167.
So fastest is {A, B} at 29.1667, cheapest {B, C} at 33.3167 and balanced {C}
at 29.0667; against balanced, fastest trades +0.60 for −1.50 minutes and
cheapest −1.00 for +15.75 minutes. In cheapest the dates sit alone at B:
without them cheapest is {C} at 4.40 + 18.6667 = 23.0667, saving 10.25 and
15.75 minutes.

Across a band: a trip that starts Wednesday 09:50 (16:50 UTC) reaches A after
600 × 1.35 = 810 s, at 10:03:30, and leaves at 10:13:30 in the next band, so the
leg home is 600 × 1.15 = 690 s: 1,500 s driving and 600 s stopped.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import httpx
import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import ApiError
from app.core.grants import APP_ROLE, TRUNCATABLE
from app.services import travel_tables
from app.services import trip_planner as tp
from app.services.trip_offers import Offer, OfferBook
from tests import geo_helpers as gh
from tests.catalog_helpers import make_product as make_sibling_product
from tests.geo_helpers import HOME_A, NEAR_A, make_home_base, make_location
from tests.pricebook_helpers import make_product, shelf
from tests.test_posted_prices import post_listing_price
from tests.test_shopping_lists import member_client  # noqa: F401
from tests.test_travel_legs import ROUTING_URL, FakeOsrm, osrm  # noqa: F401

clean_geo = gh.clean_geo
no_network = gh.no_network

D = Decimal
# Synthetic grid points near HOME_A (SECURITY.md). pii-scan: allow synthetic ocean coordinates
SHOP_B = ("33.520000", "-120.480000")  # about 2.9 km from HOME_A
SHOP_C = ("33.550000", "-120.450000")  # about 7.2 km
TRIPS = "/api/v1/plan/trips"
ITEMS = "/api/v1/plan/list/items"
NOON_LA = datetime(2026, 10, 14, 19, 0, tzinfo=UTC)  # Wednesday 12:00 in Los Angeles
MULTIPLIERS = travel_tables.multipliers(
    "weekday=06-10:1.35,10-15:1.15,15-20:1.45;weekend=09-19:1.15"
)


# --- the pure search: fixtures ---------------------------------------------------------------


def inputs(**overrides) -> tp.PlanInputs:
    base = {
        "origin_place_id": K,
        "destination_place_id": K,
        "planned_at": NOON_LA,
        "value_of_time_per_hour": D("20.00"),
        "max_stops": 3,
        "mileage_rate": None,
        "stop_overhead_min": 10,
        "timezone": "America/Los_Angeles",
        "multipliers": MULTIPLIERS,
    }
    return tp.PlanInputs(**{**base, **overrides})


K = uuid.uuid4()
PLACES = {name: uuid.uuid4() for name in "ABCDEF"}
LOCATIONS = {name: uuid.uuid4() for name in "ABCDEF"}


def candidate(name: str, *, hours_known: bool = True, overhead: int | None = None) -> tp.Candidate:
    return tp.Candidate(LOCATIONS[name], PLACES[name], overhead, hours_known, 1000)


FREE_FLOW = {
    ("K", "A"): (600, 5000),
    ("K", "B"): (900, 8000),
    ("K", "C"): (1200, 12000),
    ("A", "B"): (300, 2500),
    ("B", "C"): (600, 5000),
    ("A", "C"): (900, 7000),
    # D, E and F sit where A does, for the hours and estimate cases.
    ("K", "D"): (600, 5000),
    ("K", "E"): (600, 5000),
    ("K", "F"): (600, 5000),
    ("A", "D"): (300, 2500),
    ("A", "E"): (300, 2500),
    ("A", "F"): (300, 2500),
    ("B", "D"): (300, 2500),
    ("C", "D"): (900, 7000),
}


def place(name: str) -> uuid.UUID:
    return K if name == "K" else PLACES[name]


def legs(*, estimate: set[tuple[str, str]] = frozenset()) -> dict[tp.PairKey, tp.Leg]:
    out: dict[tp.PairKey, tp.Leg] = {}
    for (a, b), (seconds, meters) in FREE_FLOW.items():
        est = (a, b) in estimate or (b, a) in estimate
        out[(place(a), place(b))] = tp.Leg(seconds, meters, est)
        out[(place(b), place(a))] = tp.Leg(seconds, meters, est)
    return out


ITEM_IDS = {name: uuid.uuid4() for name in ("barley", "milk", "dates", "saffron", "tea")}


def item(name: str, *, deferrable: bool = False, pinned: bool = False) -> tp.PlanItem:
    return tp.PlanItem(ITEM_IDS[name], name, deferrable, pinned)


def offer(item_name: str, location: str, cost: str, *, stale: bool = False) -> Offer:
    return Offer(
        item_id=ITEM_IDS[item_name],
        location_id=LOCATIONS[location],
        product_id=uuid.uuid4(),
        observation_id=uuid.uuid4(),
        observed_at=NOON_LA - timedelta(days=1),
        unit_price=D(cost),
        packs=None,
        pack_price=None,
        line_cost=D(cost),
        stale=stale,
    )


def book(*offers: Offer, reasons: dict | None = None) -> OfferBook:
    return OfferBook({(o.item_id, o.location_id): o for o in offers}, reasons or {})


def always_open(_: uuid.UUID, __: datetime) -> bool | None:
    return True


HAND_CASE = (
    offer("barley", "A", "4.00"),
    offer("barley", "B", "3.00"),
    offer("barley", "C", "2.00"),
    offer("milk", "A", "3.00"),
    offer("milk", "C", "2.40"),
    offer("dates", "B", "5.00"),
    offer("dates", "C", "6.00"),
)
HAND_ITEMS = [item("barley"), item("milk"), item("dates", deferrable=True)]


def stops_of(variant: tp.Variant) -> list[str]:
    names = {v: k for k, v in LOCATIONS.items()}
    return [names[s.location_id] for s in variant.stops]


def notes(variant: tp.Variant, kind: str) -> list[dict]:
    return [n for n in variant.annotations if n["kind"] == kind]


# --- criterion 13: the three variants to the cent and the second ---------------------------


def test_the_three_variants_equal_the_hand_computed_figures():
    result = tp.plan(
        inputs(), HAND_ITEMS, [candidate(n) for n in "ABC"], book(*HAND_CASE), legs(), always_open
    )
    fastest, cheapest, balanced = (result.variants[k] for k in ("fastest", "cheapest", "balanced"))

    assert stops_of(fastest) == ["A", "B"]
    assert (fastest.item_cost, fastest.driving_seconds, fastest.stop_seconds) == (
        D("11.00"),
        2070,
        1200,
    )
    assert fastest.total_cost == D("29.1667")
    assert (fastest.trade_money, fastest.trade_minutes) == (D("0.60"), D("-1.50"))

    assert stops_of(cheapest) == ["B", "C"]
    assert (cheapest.item_cost, cheapest.driving_seconds, cheapest.stop_seconds) == (
        D("9.40"),
        3105,
        1200,
    )
    assert cheapest.total_cost == D("33.3167")
    assert (cheapest.trade_money, cheapest.trade_minutes) == (D("-1.00"), D("15.75"))

    assert stops_of(balanced) == ["C"]
    assert (balanced.item_cost, balanced.driving_seconds, balanced.stop_seconds) == (
        D("10.40"),
        2760,
        600,
    )
    assert balanced.total_cost == D("29.0667")
    assert (balanced.trade_money, balanced.trade_minutes) == (D("0.00"), D("0.00"))
    assert balanced.mileage_cost == D("0.0000") and not balanced.estimated

    # Each stop records the free-flow leg that reaches it, the multiplier applied
    # at its departure and the arrival it yields (P2); items go to their stops.
    a, b = fastest.stops
    assert (a.leg_seconds, a.leg_meters, a.multiplier) == (600, 5000, D("1.15"))
    assert a.arrive_at == NOON_LA + timedelta(seconds=690)
    assert (b.leg_seconds, b.leg_meters, b.multiplier) == (300, 2500, D("1.15"))
    assert b.arrive_at == NOON_LA + timedelta(seconds=690 + 600 + 345)
    assert [o.item_id for o in a.items] == [ITEM_IDS["milk"]]
    assert {o.item_id for o in b.items} == {ITEM_IDS["barley"], ITEM_IDS["dates"]}
    assert (a.subtotal, b.subtotal) == (D("3.00"), D("8.00"))
    assert all(s.hours_known and s.overhead_min == 10 for s in fastest.stops)
    assert not notes(fastest, "forced_stop") and not notes(fastest, "unsourceable")


def test_mileage_joins_the_objective_when_a_rate_is_given():
    # Balanced at 0.50 a mile: {C} drives 24,000 m = 14.9129 mi (7.4565), {A, B}
    # 15,500 m = 9.6313 mi (4.8156): {A, B} wins at 11 + 18.1667 + 4.8156 = 33.9823.
    result = tp.plan(
        inputs(mileage_rate=D("0.50")),
        HAND_ITEMS,
        [candidate(n) for n in "ABC"],
        book(*HAND_CASE),
        legs(),
        always_open,
    )
    balanced = result.variants["balanced"]
    assert stops_of(balanced) == ["A", "B"]
    assert balanced.mileage_cost == D("4.8156") and balanced.total_cost == D("33.9823")
    # {B, C} drives 25,000 m = 15.5343 mi (7.7671): its trade is 17.1671 - 15.8156.
    assert result.variants["cheapest"].trade_money == D("1.3515")


def test_the_multiplier_follows_the_band_at_each_legs_departure():
    at = datetime(2026, 10, 14, 16, 50, tzinfo=UTC)  # Wednesday 09:50 in Los Angeles
    result = tp.plan(
        inputs(planned_at=at, max_stops=1),
        [item("barley")],
        [candidate("A")],
        book(offer("barley", "A", "4.00")),
        legs(),
        always_open,
    )
    variant = result.variants["balanced"]
    (stop,) = variant.stops
    assert stop.multiplier == D("1.35") and stop.arrive_at == at + timedelta(seconds=810)
    assert (variant.driving_seconds, variant.stop_seconds) == (1500, 600)
    assert variant.total_cost == D("4.00") + D("11.6667")


def test_a_location_overhead_replaces_the_plan_default():
    result = tp.plan(
        inputs(max_stops=1),
        [item("barley")],
        [candidate("A", overhead=25)],
        book(offer("barley", "A", "4.00")),
        legs(),
        always_open,
    )
    variant = result.variants["fastest"]
    assert variant.stops[0].overhead_min == 25 and variant.stop_seconds == 1500


# --- criterion 14: hours at the projected arrival ----------------------------------------------


def open_between(start: int, end: int, *names: str) -> Callable[[uuid.UUID, datetime], bool | None]:
    """D (or the names given) is open only between these offsets after noon; the rest always."""
    watched = {LOCATIONS[n] for n in (names or "D")}

    def is_open(location_id: uuid.UUID, at: datetime) -> bool | None:
        if location_id not in watched:
            return True
        offset = (at - NOON_LA).total_seconds()
        return start <= offset < end

    return is_open


def test_a_location_closed_at_one_orderings_arrival_is_kept_in_the_other():
    # D is open until 19:15: reached first (690 s) it is open; after A (1,635 s) it is closed.
    items = [item("barley"), item("milk")]
    offers = book(
        offer("barley", "D", "1.00"), offer("milk", "A", "3.00"), offer("barley", "A", "4.00")
    )
    result = tp.plan(
        inputs(), items, [candidate("A"), candidate("D")], offers, legs(), open_between(0, 900)
    )
    for variant in result.variants.values():
        if "D" in stops_of(variant):
            assert stops_of(variant) == ["D", "A"]
    assert stops_of(result.variants["cheapest"]) == ["D", "A"]
    assert result.orderings_visited == 4  # {A}, {D}, (A, D) dropped and (D, A) kept
    assert not notes(result.variants["cheapest"], "closed_excluded")


def test_a_location_closed_at_every_arrival_is_left_out_and_its_item_is_unsourceable():
    items = [item("barley"), item("saffron")]
    offers = book(offer("barley", "A", "4.00"), offer("saffron", "D", "9.00"))
    result = tp.plan(
        inputs(),
        items,
        [candidate("A"), candidate("D")],
        offers,
        legs(),
        open_between(-7200, -3600),
    )
    for variant in result.variants.values():
        assert stops_of(variant) == ["A"]
        (closed,) = notes(variant, "closed_excluded")
        assert closed["location_id"] == str(LOCATIONS["D"])
        (gone,) = notes(variant, "unsourceable")
        assert gone["item_id"] == str(ITEM_IDS["saffron"]) and gone["reason"] == "closed"


def test_unknown_hours_keep_the_stop_and_say_so():
    result = tp.plan(
        inputs(),
        [item("barley")],
        [candidate("E", hours_known=False)],
        book(offer("barley", "E", "4.00")),
        legs(),
        lambda *_: None,
    )
    variant = result.variants["balanced"]
    assert variant.stops[0].hours_known is False
    (note,) = notes(variant, "hours_unknown")
    assert note["location_id"] == str(LOCATIONS["E"])


# --- criterion 17: the bound, and the planner's speed at it -----------------------------------


def test_the_enumeration_bound_at_fifteen_candidates_and_four_stops():
    assert tp.enumeration_bound(15, 4) == (1940, 35715)
    assert tp.enumeration_bound(6, 3) == (41, 156)
    assert tp.enumeration_bound(3, 4) == (7, 15)  # never more stops than candidates


def test_the_planner_visits_at_most_the_bound_and_finishes_in_two_seconds():
    origin = uuid.uuid4()
    candidates = [tp.Candidate(uuid.uuid4(), uuid.uuid4(), None, True, 1000 * i) for i in range(15)]
    items = [
        tp.PlanItem(uuid.uuid4(), f"item {i}", deferrable=i % 5 == 0, pinned=i % 7 == 0)
        for i in range(20)
    ]
    offers = {}
    for i, it in enumerate(items):
        for j, c in enumerate(candidates):
            if (i + j) % 3 == 0:
                continue
            price = D(1 + (i * 7 + j * 3) % 11) / 2
            offers[(it.item_id, c.location_id)] = Offer(
                it.item_id,
                c.location_id,
                uuid.uuid4(),
                uuid.uuid4(),
                NOON_LA,
                price,
                None,
                None,
                price,
                stale=(i == 3 and j == 1),
            )
    places = [origin] + [c.place_id for c in candidates]
    matrix = {}
    for a_i, a in enumerate(places):
        for b_i, b in enumerate(places):
            if a != b:
                matrix[(a, b)] = tp.Leg(
                    300 + 50 * ((a_i * 7 + b_i * 3) % 13), 2000 + 500 * ((a_i + b_i) % 9), False
                )
    start = time.perf_counter()
    result = tp.plan(
        inputs(origin_place_id=origin, destination_place_id=origin, max_stops=4),
        items,
        candidates,
        OfferBook(offers, {}),
        matrix,
        always_open,
    )
    elapsed = time.perf_counter() - start
    assert result.sets_visited == 1940
    assert result.orderings_visited <= 35715
    assert elapsed < 2.0, f"planned in {elapsed:.2f} s"
    assert {len(v.stops) for v in result.variants.values()} <= {1, 2, 3, 4}


def test_more_than_fifteen_candidates_are_refused_by_the_pure_planner():
    candidates = [tp.Candidate(uuid.uuid4(), uuid.uuid4(), None, True, i) for i in range(16)]
    with pytest.raises(ValueError):
        tp.plan(inputs(), [item("barley")], candidates, book(), legs(), always_open)


# --- criterion 18: forced stops, defer suggestions, stale savings -----------------------------


def test_a_pinned_item_sold_at_one_candidate_is_a_forced_stop():
    items = [item("barley"), item("saffron", pinned=True)]
    offers = book(
        offer("barley", "A", "4.00"), offer("barley", "B", "3.00"), offer("saffron", "B", "9.00")
    )
    result = tp.plan(inputs(), items, [candidate("A"), candidate("B")], offers, legs(), always_open)
    for variant in result.variants.values():
        (forced,) = notes(variant, "forced_stop")
        assert forced["item_id"] == str(ITEM_IDS["saffron"])
        assert forced["location_id"] == str(LOCATIONS["B"])
        assert ITEM_IDS["saffron"] in variant.forced_item_ids
    # An unpinned ingredient item at one candidate is merely sourced there.
    plain = tp.plan(
        inputs(),
        [item("barley"), item("saffron")],
        [candidate("A"), candidate("B")],
        offers,
        legs(),
        always_open,
    )
    assert not notes(plain.variants["balanced"], "forced_stop")


def test_a_deferrable_item_alone_at_its_stop_suggests_deferring_with_the_saving():
    result = tp.plan(
        inputs(), HAND_ITEMS, [candidate(n) for n in "ABC"], book(*HAND_CASE), legs(), always_open
    )
    cheapest = result.variants["cheapest"]
    (defer,) = notes(cheapest, "defer_suggestion")
    assert defer["item_id"] == str(ITEM_IDS["dates"])
    assert defer["location_id"] == str(LOCATIONS["B"])
    assert (defer["amount"], defer["minutes"]) == ("10.2500", "15.75")
    assert "dates" in defer["text"]
    assert not notes(result.variants["fastest"], "defer_suggestion")
    assert not notes(result.variants["balanced"], "defer_suggestion")


def test_a_saving_that_rests_on_a_stale_price_is_said_so():
    stale = tuple(
        offer("milk", "C", "2.40", stale=True)
        if (o.item_id, o.location_id) == (ITEM_IDS["milk"], LOCATIONS["C"])
        else o
        for o in HAND_CASE
    )
    result = tp.plan(
        inputs(), HAND_ITEMS, [candidate(n) for n in "ABC"], book(*stale), legs(), always_open
    )
    cheapest = result.variants["cheapest"]
    (note,) = notes(cheapest, "stale_dependent")
    assert note["amount"] == "2.4000"
    # Without the stale milk at C, cheapest would be {A, B, C} at 10.00 rather than {B, C}.
    assert "another set of stops" in note["text"]
    assert [o.stale for s in cheapest.stops for o in s.items if o.item_id == ITEM_IDS["milk"]] == [
        True
    ]
    assert not notes(result.variants["fastest"], "stale_dependent")

    # Without a stale barley at C, {B, C} still wins (barley at B for 3.00): it stays cheapest.
    still = tuple(
        offer("barley", "C", "2.00", stale=True)
        if (o.item_id, o.location_id) == (ITEM_IDS["barley"], LOCATIONS["C"])
        else o
        for o in HAND_CASE
    )
    again = tp.plan(
        inputs(), HAND_ITEMS, [candidate(n) for n in "ABC"], book(*still), legs(), always_open
    )
    (note,) = notes(again.variants["cheapest"], "stale_dependent")
    assert note["amount"] == "2.0000" and "stays cheapest" in note["text"]


# --- unsourceable items, estimates, nothing to buy ---------------------------------------------


def test_an_item_nobody_sells_is_annotated_with_the_reason():
    items = [item("barley"), item("tea"), item("saffron")]
    reasons = {
        (ITEM_IDS["tea"], LOCATIONS["A"]): "cant_convert",
        (ITEM_IDS["saffron"], LOCATIONS["A"]): "no_offer",
    }
    result = tp.plan(
        inputs(),
        items,
        [candidate("A")],
        book(offer("barley", "A", "4.00"), reasons=reasons),
        legs(),
        always_open,
    )
    variant = result.variants["balanced"]
    by_item = {n["item_id"]: n["reason"] for n in notes(variant, "unsourceable")}
    assert by_item == {str(ITEM_IDS["tea"]): "cant_convert", str(ITEM_IDS["saffron"]): "no_offer"}
    assert result.unsourceable == notes(variant, "unsourceable")


def test_a_list_that_needs_more_stops_than_allowed_buys_what_it_can():
    items = [item("barley"), item("milk")]
    offers = book(offer("barley", "A", "4.00"), offer("milk", "B", "3.00"))
    result = tp.plan(
        inputs(max_stops=1), items, [candidate("A"), candidate("B")], offers, legs(), always_open
    )
    for variant in result.variants.values():
        assert len(variant.stops) == 1
        (note,) = notes(variant, "unsourceable")
        assert note["reason"] == "max_stops"


def test_nothing_to_buy_anywhere_is_a_422():
    with pytest.raises(ApiError) as exc:
        tp.plan(inputs(), [item("barley")], [candidate("A")], book(), legs(), always_open)
    assert exc.value.status_code == 422 and exc.value.code == "unsourceable_list"


def test_estimated_is_set_when_any_leg_used_is_an_estimate_and_clear_otherwise():
    items = [item("barley")]
    offers = book(offer("barley", "A", "4.00"))
    exact = tp.plan(inputs(max_stops=1), items, [candidate("A")], offers, legs(), always_open)
    assert not exact.variants["balanced"].estimated
    assert not notes(exact.variants["balanced"], "estimated_legs")
    rough = tp.plan(
        inputs(max_stops=1),
        items,
        [candidate("A")],
        offers,
        legs(estimate={("K", "A")}),
        always_open,
    )
    assert rough.variants["balanced"].estimated
    assert rough.variants["balanced"].stops[0].leg_estimate
    assert notes(rough.variants["balanced"], "estimated_legs")


def test_a_candidate_at_the_origin_drives_nothing_to_reach_it():
    at_home = tp.Candidate(LOCATIONS["F"], K, None, True, 0)
    result = tp.plan(
        inputs(max_stops=1),
        [item("barley")],
        [at_home],
        book(offer("barley", "F", "4.00")),
        {},
        always_open,
    )
    variant = result.variants["fastest"]
    assert variant.driving_seconds == 0 and variant.stops[0].leg_seconds == 0


# --- end to end: the price book, the list, the endpoints ------------------------------------


@pytest.fixture
async def kitchen(admin_client: httpx.AsyncClient) -> dict:
    return await make_home_base(admin_client, "Harbor kitchen", HOME_A)


async def add_item(client: httpx.AsyncClient, kitchen: dict, **fields) -> dict:
    body = {"home_base_id": kitchen["id"], "kind": "ingredient", **fields}
    r = await client.post(ITEMS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def plan_trip(
    client: httpx.AsyncClient, kitchen: dict, *, expect: int = 201, **fields
) -> dict:
    body = {"home_base_id": kitchen["id"], "planned_at": NOON_LA.isoformat(), **fields}
    r = await client.post(TRIPS, json=body)
    assert r.status_code == expect, r.text
    return r.json()


def variant(plan: dict, kind: str) -> dict:
    return next(v for v in plan["variants"] if v["kind"] == kind)


def item_lines(plan: dict, kind: str) -> dict[str, dict]:
    return {i["list_item_id"]: i for s in variant(plan, kind)["stops"] for i in s["items"]}


@pytest.fixture
async def shops(admin_client: httpx.AsyncClient) -> dict:
    """Two stores of one chain (NEAR_A and SHOP_B), an independent stall at SHOP_C."""
    first = await make_location(
        admin_client,
        "Harbor Chain north",
        NEAR_A,
        vendor={"name": "Harbor Chain", "kind": "chain", "price_scope": "chain"},
        opening_hours="Mo-Su 08:00-21:00",
    )
    second = await make_location(
        admin_client, "Harbor Chain south", SHOP_B, vendor_id=first["vendor"]["id"]
    )
    stall = await make_location(
        admin_client,
        "Pier stall",
        SHOP_C,
        vendor={"name": "Pier stall", "kind": "stand", "price_scope": "location"},
    )
    return {"north": first, "south": second, "stall": stall}


async def test_a_plan_from_the_open_list_with_estimated_legs(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], shops["north"]["id"], "4.00")
    item = await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1500", "unit": "g"}],
    )

    plan = await plan_trip(admin_client, kitchen, max_stops=2, value_of_time_per_hour="30.00")
    assert plan["status"] == "planned" and plan["chosen_variant_id"] is None
    assert plan["origin_place_id"] == plan["destination_place_id"]
    assert plan["value_of_time_per_hour"] == "30.00" and plan["stop_overhead_min"] == 10
    assert [v["kind"] for v in plan["variants"]] == ["fastest", "cheapest", "balanced"]
    for v in plan["variants"]:
        assert v["estimated"] is True
        assert any(a["kind"] == "estimated_legs" for a in v["annotations"])
        assert len(v["stops"]) == 1 and v["stop_count"] == 1
        (stop,) = v["stops"]
        assert stop["leg_source"] == "estimate" and stop["hours_known"] is True
        assert stop["vendor_location_name"] == "Harbor Chain north"
        # Two 1 kg bags at 4.00: the chain price applies at both stores, the nearer wins.
        (line,) = stop["items"]
        assert line["list_item_id"] == item["id"]
        assert (line["packs"], line["pack_price"], line["line_cost"]) == ("2", "4.0000", "8.0000")
        assert line["price_stale"] is False and line["forced"] is False
        assert v["item_cost"] == "8.0000" and stop["subtotal"] == "8.0000"
        assert v["trade"] == {"money": "0.0000", "minutes": "0.00"}
    # 1,448 m straight line → 1,882 m at 30 km/h = 226 s each way (test_travel_legs), ×1.15.
    balanced = variant(plan, "balanced")
    assert balanced["driving_seconds"] == 2 * 260 and balanced["stop_seconds"] == 600
    assert balanced["stops"][0]["multiplier"] == "1.15"

    listed = await admin_client.get(TRIPS, params={"home_base_id": kitchen["id"]})
    assert listed.status_code == 200 and [p["id"] for p in listed.json()["items"]] == [plan["id"]]
    assert listed.json()["items"][0]["variants"][0]["stop_count"] == 1
    got = await admin_client.get(f"{TRIPS}/{plan['id']}")
    assert got.status_code == 200 and got.json() == plan
    assert (await admin_client.get(TRIPS)).status_code == 422  # the kitchen is explicit


async def test_legs_from_the_engine_make_the_plan_exact(
    admin_client: httpx.AsyncClient,
    kitchen: dict,
    shops: dict,
    osrm: FakeOsrm,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "routing_url", ROUTING_URL)
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], shops["north"]["id"], "4.00")
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    for v in plan["variants"]:
        assert v["estimated"] is False
        assert not any(a["kind"] == "estimated_legs" for a in v["annotations"])
        assert v["stops"][0]["leg_source"] == "osrm" and v["stops"][0]["leg_seconds"] == 600
    assert len(osrm.requests) == 1


async def test_prices_apply_by_chain_or_location_and_only_committed_shelf_prices_count(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    """Criterion 15 with a stale price marked (P5)."""
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    sack = await make_sibling_product(
        admin_client, barley["ingredient"]["id"], "Barley sack", pack_qty="1", pack_unit="kg"
    )
    # A chain price at the south store applies at the north store too; the stall's at its own.
    await shelf(admin_client, barley["id"], shops["south"]["id"], "4.00")
    await shelf(admin_client, sack["id"], shops["stall"]["id"], "3.00")
    # A posted price, a voided one and a price at an inactive location never fill an item.
    await post_listing_price(shops["north"], sack, "1.00")
    voided = await shelf(admin_client, sack["id"], shops["north"]["id"], "0.50")
    r = await admin_client.post(
        f"/api/v1/price-observations/{voided['id']}/void", json={"reason": "typo"}
    )
    assert r.status_code == 200, r.text
    closed = await make_location(admin_client, "Shut stand", NEAR_A)
    await shelf(admin_client, sack["id"], closed["id"], "0.10")
    assert (
        await admin_client.post(f"/api/v1/vendor-locations/{closed['id']}/deactivate")
    ).status_code == 200
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )

    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    stops = {v["kind"]: v["stops"][0] for v in plan["variants"]}
    assert stops["cheapest"]["vendor_location_id"] == shops["stall"]["id"]
    assert stops["cheapest"]["items"][0]["line_cost"] == "3.0000"
    assert stops["fastest"]["vendor_location_id"] == shops["north"]["id"]
    assert stops["fastest"]["items"][0]["line_cost"] == "4.0000"
    assert stops["fastest"]["items"][0]["product_id"] == barley["id"]

    # An old price is used and marked stale; the cheapest variant says what rests on it.
    old = datetime.now(UTC) - timedelta(days=get_settings().stale_after_days + 1)
    await shelf(admin_client, sack["id"], shops["stall"]["id"], "2.00", observed_at=old.isoformat())
    # The newer 3.00 is the stall's latest; put the stale price on a stall of its own.
    far_stall = await make_location(admin_client, "Old stall", SHOP_C)
    await shelf(admin_client, sack["id"], far_stall["id"], "1.00", observed_at=old.isoformat())
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    cheapest = variant(plan, "cheapest")
    assert cheapest["stops"][0]["vendor_location_id"] == far_stall["id"]
    assert cheapest["stops"][0]["items"][0]["price_stale"] is True
    (note,) = [a for a in cheapest["annotations"] if a["kind"] == "stale_dependent"]
    assert note["amount"] == "1.0000" and "another set" in note["text"]


async def test_packs_round_up_counts_round_by_pack_count_and_cant_convert_is_unsourceable(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    """Criterion 16."""
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="500", pack_unit="g")
    dates = await make_product(
        admin_client, "Dates", "Dates box", canonical_unit="each", pack_qty="20", pack_unit="each"
    )
    rolls = await make_product(
        admin_client,
        "Rolls",
        "Rolls pack",
        pack_qty="600",
        pack_unit="g",
        pack_count=6,
        piece_name="roll",
    )
    loose = await make_product(admin_client, "Oats", "Oats loose")  # no pack size
    tea = await make_product(admin_client, "Tea", "Tea tin", pack_qty="100", pack_unit="g")
    north = shops["north"]["id"]
    await shelf(admin_client, barley["id"], north, "2.00")  # 0.004/g
    await shelf(admin_client, dates["id"], north, "5.00")  # 0.25 each
    await shelf(admin_client, rolls["id"], north, "3.00")  # 0.005/g
    await shelf(admin_client, loose["id"], north, "1.00", qty="1", unit="kg")  # 0.001/g
    await shelf(admin_client, tea["id"], north, "4.00")
    items = {
        "barley": await add_item(
            admin_client,
            kitchen,
            ingredient_id=barley["ingredient"]["id"],
            amounts=[{"amount": "1200", "unit": "g"}],
        ),
        "dates": await add_item(
            admin_client,
            kitchen,
            ingredient_id=dates["ingredient"]["id"],
            amounts=[{"amount": "25", "unit": "each"}],
        ),
        "rolls": await add_item(
            admin_client,
            kitchen,
            ingredient_id=rolls["ingredient"]["id"],
            amounts=[{"amount": "8", "unit": "each"}],
        ),
        "loose": await add_item(
            admin_client,
            kitchen,
            ingredient_id=loose["ingredient"]["id"],
            amounts=[{"amount": "250", "unit": "g"}],
        ),
        "tea": await add_item(
            admin_client,
            kitchen,
            ingredient_id=tea["ingredient"]["id"],
            amounts=[{"amount": "2", "unit": "cup"}],
        ),
        "pinned": await add_item(
            admin_client,
            kitchen,
            kind="product",
            product_id=rolls["id"],
            amounts=[{"amount": "2", "unit": "each"}],
        ),
    }
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    lines = item_lines(plan, "balanced")
    # 1,200 g in 500 g bags: three bags at 2.00.
    assert (lines[items["barley"]["id"]]["packs"], lines[items["barley"]["id"]]["line_cost"]) == (
        "3",
        "6.0000",
    )
    # 25 dates in boxes of 20: two boxes at 5.00.
    assert (lines[items["dates"]["id"]]["packs"], lines[items["dates"]["id"]]["line_cost"]) == (
        "2",
        "10.0000",
    )
    # 8 rolls of an ingredient counted in grams: the pack states 6 pieces, so two packs.
    assert (lines[items["rolls"]["id"]]["packs"], lines[items["rolls"]["id"]]["line_cost"]) == (
        "2",
        "6.0000",
    )
    # The product item "2 each" of the rolls pack is two packs.
    assert (lines[items["pinned"]["id"]]["packs"], lines[items["pinned"]["id"]]["line_cost"]) == (
        "2",
        "6.0000",
    )
    # The chain price applies at both chain stores, so the pinned pack is not forced.
    assert lines[items["pinned"]["id"]]["forced"] is False
    # No pack size: quantity times unit price, packs null.
    assert (lines[items["loose"]["id"]]["packs"], lines[items["loose"]["id"]]["line_cost"]) == (
        None,
        "0.2500",
    )
    # Two cups of tea with no density: nothing converts, the only product is unusable.
    assert items["tea"]["id"] not in lines
    notes_ = {
        a["item_id"]: a
        for a in variant(plan, "balanced")["annotations"]
        if a["kind"] == "unsourceable"
    }
    assert notes_[items["tea"]["id"]]["reason"] == "cant_convert"
    assert not [a for a in variant(plan, "balanced")["annotations"] if a["kind"] == "forced_stop"]


async def test_min_quality_narrows_an_ingredient_item(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    plain = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    fine = await make_sibling_product(
        admin_client,
        plain["ingredient"]["id"],
        "Barley fine",
        pack_qty="1",
        pack_unit="kg",
        quality_rating=4,
    )
    await shelf(admin_client, plain["id"], shops["north"]["id"], "2.00")
    await shelf(admin_client, fine["id"], shops["stall"]["id"], "5.00")
    good = await add_item(
        admin_client,
        kitchen,
        ingredient_id=plain["ingredient"]["id"],
        min_quality=4,
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    for kind in ("fastest", "cheapest", "balanced"):
        line = item_lines(plan, kind)[good["id"]]
        assert line["product_id"] == fine["id"] and line["forced"] is True
    await admin_client.patch(
        f"{ITEMS}/{good['id']}", json={"updated_at": good["updated_at"], "min_quality": 5}
    )
    plan = await plan_trip(admin_client, kitchen, max_stops=1, expect=422)
    assert plan["error"]["code"] == "unsourceable_list"
    assert plan["error"]["details"]["unsourceable"][0]["reason"] == "quality"


async def test_a_closed_store_is_excluded_at_arrival_and_allowed_earlier(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    """Criterion 14 through real opening hours in the household zone."""
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], shops["north"]["id"], "4.00")
    await shelf(admin_client, barley["id"], shops["south"]["id"], "4.00")
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    # North is open 08:00-21:00 Los Angeles; south has no hours.
    late = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # 22:00 Wednesday in Los Angeles
    plan = await plan_trip(admin_client, kitchen, max_stops=1, planned_at=late.isoformat())
    for v in plan["variants"]:
        assert v["stops"][0]["vendor_location_id"] == shops["south"]["id"]
        assert v["stops"][0]["hours_known"] is False
        kinds = {a["kind"] for a in v["annotations"]}
        assert {"hours_unknown", "closed_excluded"} <= kinds
    plan = await plan_trip(admin_client, kitchen, max_stops=1)  # noon: north is open and nearer
    for v in plan["variants"]:
        assert v["stops"][0]["vendor_location_id"] == shops["north"]["id"]
        assert v["stops"][0]["hours_known"] is True


async def test_candidates_are_the_nearest_active_locations_within_the_radius(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, monkeypatch: pytest.MonkeyPatch
):
    async with get_sessionmaker()() as db:
        from app.models.geo import HomeBase

        home = await db.get(HomeBase, uuid.UUID(kitchen["id"]))
        assert home is not None
        found, rows = await tp.candidates(db, home, get_settings())
        assert [c.location_id for c in found] == [
            uuid.UUID(shops[k]["id"]) for k in ("north", "south", "stall")
        ]
        assert found[0].distance_m == 1448 and found[0].hours_known and not found[1].hours_known
        assert set(rows) == {c.location_id for c in found}
        monkeypatch.setattr(get_settings(), "plan_max_candidates", 2)
        found, _ = await tp.candidates(db, home, get_settings())
        assert len(found) == 2
        monkeypatch.setattr(get_settings(), "plan_candidate_radius_km", D("2"))
        found, _ = await tp.candidates(db, home, get_settings())
        assert [c.location_id for c in found] == [uuid.UUID(shops["north"]["id"])]


async def test_choose_freezes_the_variant_and_a_list_change_makes_a_new_plan_only(
    admin_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,  # noqa: F811
    kitchen: dict,
    shops: dict,
    no_network: None,
):
    """Criterion 19 and P1."""
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], shops["north"]["id"], "4.00")
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    plan = await plan_trip(admin_client, kitchen, max_stops=1)
    balanced = variant(plan, "balanced")

    r = await member_client.post(
        f"{TRIPS}/{plan['id']}/choose", json={"variant_id": balanced["id"]}
    )
    assert r.status_code == 200, r.text
    chosen = r.json()
    assert chosen["chosen_variant_id"] == balanced["id"] and chosen["chosen_at"] is not None
    assert chosen["status"] == "planned" and chosen["variants"] == plan["variants"]
    again = await admin_client.post(
        f"{TRIPS}/{plan['id']}/choose", json={"variant_id": balanced["id"]}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_chosen"
    gone = await admin_client.delete(f"{TRIPS}/{plan['id']}")
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "plan_chosen"

    # The list changes: the chosen plan is what it was; a new plan sees the change.
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "2", "unit": "kg"}],
    )
    same = await admin_client.get(f"{TRIPS}/{plan['id']}")
    assert same.json()["variants"] == plan["variants"]
    fresh = await plan_trip(admin_client, kitchen, max_stops=1)
    assert variant(fresh, "balanced")["item_cost"] == "12.0000"
    assert len(variant(fresh, "balanced")["stops"][0]["items"]) == 2

    # An unchosen plan may be deleted; an unknown variant is a 404.
    bad = await admin_client.post(
        f"{TRIPS}/{fresh['id']}/choose", json={"variant_id": str(uuid.uuid4())}
    )
    assert bad.status_code == 404
    deleted = await admin_client.delete(f"{TRIPS}/{fresh['id']}")
    assert deleted.status_code == 204
    assert (await admin_client.get(f"{TRIPS}/{fresh['id']}")).status_code == 404
    listed = await admin_client.get(TRIPS, params={"home_base_id": kitchen["id"]})
    assert [p["id"] for p in listed.json()["items"]] == [plan["id"]]

    ended = await admin_client.post(f"{TRIPS}/{plan['id']}/abandon")
    assert ended.status_code == 200 and ended.json()["status"] == "abandoned"
    assert ended.json()["finished_at"] is not None
    twice = await admin_client.post(f"{TRIPS}/{plan['id']}/abandon")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "plan_ended"


async def test_the_drawer_reads_places_defaults_and_the_rows_name_the_stores(
    admin_client: httpx.AsyncClient, kitchen: dict, shops: dict, no_network: None
):
    """Kitchens and locations publish their place, the defaults are readable, a
    plan may start at a store, and a trips-list row names its stores in order."""
    assert kitchen["place_id"]
    kitchens = (await admin_client.get("/api/v1/home-bases")).json()["items"]
    assert {k["id"]: k["place_id"] for k in kitchens}[kitchen["id"]] == kitchen["place_id"]
    loc = (await admin_client.get(f"/api/v1/vendor-locations/{shops['stall']['id']}")).json()
    assert loc["place_id"] == shops["stall"]["place_id"] != kitchen["place_id"]

    defaults = await admin_client.get("/api/v1/plan/defaults")
    assert defaults.status_code == 200, defaults.text
    settings = get_settings()
    assert defaults.json() == {
        "value_of_time_per_hour": str(settings.plan_value_of_time_per_hour),
        "max_stops_default": 3,
        "candidate_radius_km": str(settings.plan_candidate_radius_km),
    }

    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], shops["north"]["id"], "4.00")
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    plan = await plan_trip(
        admin_client,
        kitchen,
        max_stops=1,
        origin_place_id=shops["stall"]["place_id"],
        destination_place_id=kitchen["place_id"],
    )
    assert plan["origin_place_id"] == shops["stall"]["place_id"]
    assert plan["destination_place_id"] == kitchen["place_id"]

    def row() -> dict:
        return next(p for p in listed.json()["items"] if p["id"] == plan["id"])

    listed = await admin_client.get(TRIPS, params={"home_base_id": kitchen["id"]})
    balanced = variant(plan, "balanced")
    expected = [
        {
            "seq": s["seq"],
            "vendor_location_id": s["vendor_location_id"],
            "name": s["vendor_location_name"],
        }
        for s in balanced["stops"]
    ]
    assert (
        row()["stops"]
        == expected
        == [{"seq": 1, "vendor_location_id": shops["north"]["id"], "name": "Harbor Chain north"}]
    )
    cheapest = variant(plan, "cheapest")
    r = await admin_client.post(f"{TRIPS}/{plan['id']}/choose", json={"variant_id": cheapest["id"]})
    assert r.status_code == 200, r.text
    listed = await admin_client.get(TRIPS, params={"home_base_id": kitchen["id"]})
    assert [s["vendor_location_id"] for s in row()["stops"]] == [
        s["vendor_location_id"] for s in cheapest["stops"]
    ]


async def test_an_empty_list_and_a_kitchen_without_shops_are_422(
    admin_client: httpx.AsyncClient, kitchen: dict, no_network: None
):
    r = await plan_trip(admin_client, kitchen, expect=422)
    assert r["error"]["code"] == "empty_list"
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    r = await plan_trip(admin_client, kitchen, expect=422)
    assert r["error"]["code"] == "no_candidates"
    for bad in ({"max_stops": 0}, {"max_stops": 5}, {"value_of_time_per_hour": "-1"}):
        r = await admin_client.post(
            TRIPS, json={"home_base_id": kitchen["id"], "planned_at": NOON_LA.isoformat(), **bad}
        )
        assert r.status_code == 422, bad
    r = await admin_client.post(TRIPS, json={"planned_at": NOON_LA.isoformat()})
    assert r.status_code == 422
    unknown = await admin_client.post(
        TRIPS, json={"home_base_id": str(uuid.uuid4()), "planned_at": NOON_LA.isoformat()}
    )
    assert unknown.status_code == 404


async def test_the_endpoints_take_a_session_only(client: httpx.AsyncClient):
    assert (await client.get(TRIPS, params={"home_base_id": str(uuid.uuid4())})).status_code == 401
    assert (await client.post(TRIPS, json={})).status_code == 401
    assert (await client.get("/api/v1/plan/defaults")).status_code == 401


# --- the migration and the grants -----------------------------------------------------------


async def test_trip_tables_are_migrated_and_the_derived_ones_truncatable(
    owner_conn: asyncpg.Connection,
):
    assert {"trip_plan_variant", "trip_plan_stop", "trip_plan_stop_item"} <= set(TRUNCATABLE)
    assert "trip_plan" not in TRUNCATABLE
    for table in ("trip_plan", "trip_plan_variant", "trip_plan_stop", "trip_plan_stop_item"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await owner_conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)", APP_ROLE, table, privilege
            ), (table, privilege)
    for table in ("trip_plan_variant", "trip_plan_stop", "trip_plan_stop_item"):
        assert await owner_conn.fetchval(
            "SELECT has_table_privilege($1, $2, 'TRUNCATE')", APP_ROLE, table
        ), table
    assert not await owner_conn.fetchval(
        "SELECT has_table_privilege($1, 'trip_plan', 'TRUNCATE')", APP_ROLE
    )
    columns = {
        r["column_name"]
        for r in await owner_conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'trip_plan'"
        )
    }
    assert {"chosen_variant_id", "value_of_time_per_hour", "mileage_rate", "max_stops"} <= columns
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'purchase' AND column_name = 'trip_plan_id'"
        )
        == 1
    )


async def test_the_database_refuses_a_bad_plan(app_conn: asyncpg.Connection, admin_client, kitchen):
    async with get_sessionmaker()() as db:
        from app.models.plan import ShoppingList

        lst = ShoppingList(home_base_id=uuid.UUID(kitchen["id"]), status="open")
        db.add(lst)
        await db.commit()
        list_id = lst.id
    user_id = await app_conn.fetchval("SELECT id FROM app_user LIMIT 1")
    place_id = await app_conn.fetchval(
        "SELECT place_id FROM home_base WHERE id = $1", uuid.UUID(kitchen["id"])
    )
    insert = (
        "INSERT INTO trip_plan (id, list_id, home_base_id, origin_place_id, destination_place_id, "
        "planned_at, value_of_time_per_hour, max_stops, stop_overhead_min, status, created_by) "
        "VALUES ($1, $2, $3, $4, $4, now(), 20, $5, 10, $6, $7)"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(
            insert, uuid.uuid4(), list_id, uuid.UUID(kitchen["id"]), place_id, 5, "planned", user_id
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(
            insert, uuid.uuid4(), list_id, uuid.UUID(kitchen["id"]), place_id, 2, "lost", user_id
        )
    await app_conn.execute(
        insert, uuid.uuid4(), list_id, uuid.UUID(kitchen["id"]), place_id, 2, "planned", user_id
    )
