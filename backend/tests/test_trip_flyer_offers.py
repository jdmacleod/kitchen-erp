"""The trip planner's "With this week's offers" (18, X1). Stores and offers are invented.

An offer valid on the planned day prices its product at its store in place of the
price book's latest price there, and the stop says what it saves against the
trailing median regular price. Off, the plan is exactly the price book's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from tests import geo_helpers as gh
from tests.geo_helpers import HOME_A, NEAR_A, make_home_base, make_location
from tests.pricebook_helpers import make_product, shelf
from tests.test_flyer_reads import learned
from tests.test_flyers import candidate, queue, report, requests, seed, source_row
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client
from tests.test_trip_planner import SHOP_C, TRIPS, add_item, variant

no_network = gh.no_network

# Thursday noon in Los Angeles, inside the invented ad week of Nov 4–10.
IN_THE_AD_WEEK = datetime(2026, 11, 5, 20, 0, tzinfo=UTC)
# Shelf prices from before the ad week: the regular price the offer saves against.
BEFORE = "2026-10-20T17:00:00Z"


@pytest.fixture
async def world(admin_client: httpx.AsyncClient, helper: httpx.AsyncClient) -> dict:  # noqa: F811
    """A chain store whose circular has barley at 2.00, and a stall selling it at 3.00."""
    kitchen = await make_home_base(admin_client, "Harbor kitchen", HOME_A)
    north = await make_location(
        admin_client,
        "Harbor Chain north",
        NEAR_A,
        vendor={"name": "Harbor Chain", "kind": "chain", "price_scope": "location"},
    )
    stall = await make_location(
        admin_client,
        "Pier stall",
        SHOP_C,
        vendor={"name": "Pier stall", "kind": "stand", "price_scope": "location"},
    )
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], north["id"], "4.00", observed_at=BEFORE)
    await shelf(admin_client, barley["id"], stall["id"], "3.00", observed_at=BEFORE)
    item = await add_item(
        admin_client,
        kitchen,
        ingredient_id=barley["ingredient"]["id"],
        amounts=[{"amount": "1", "unit": "kg"}],
    )
    return {"kitchen": kitchen, "north": north, "stall": stall, "barley": barley, "item": item}


async def circular(helper: httpx.AsyncClient, owner_conn, world: dict, price: str) -> None:  # noqa: F811
    """The chain store's week, with the vendor's printed name already learned."""
    assert (await seed([source_row(world["north"])])).created == 1
    await learned(owner_conn, world["north"], world["barley"], "barley bag")
    await queue()
    [request] = await requests(helper)
    r = await report(
        helper,
        request["id"],
        candidates=[candidate("Barley Bag", price, size_qty="1", size_unit="kg")],
    )
    assert r.status_code == 200, r.text


async def plan(client: httpx.AsyncClient, world: dict, **fields) -> dict:
    body = {
        "home_base_id": world["kitchen"]["id"],
        "planned_at": IN_THE_AD_WEEK.isoformat(),
        "max_stops": 1,
        **fields,
    }
    r = await client.post(TRIPS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def cheapest_stop(trip: dict) -> dict:
    [stop] = variant(trip, "cheapest")["stops"]
    return stop


async def test_an_offer_stands_in_at_its_store_and_the_stop_says_what_it_saves(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    world,
    no_network: None,
):
    await circular(helper, owner_conn, world, "2.00")

    off = await plan(admin_client, world)
    stop = cheapest_stop(off)
    assert off["with_offers"] is False
    assert (stop["vendor_location_id"], stop["expected_saving"]) == (world["stall"]["id"], None)
    assert stop["items"][0]["line_cost"] == "3.0000"
    assert stop["items"][0]["flyer_offer_id"] is None

    on = await plan(admin_client, world, with_offers=True)
    stop = cheapest_stop(on)
    [line] = stop["items"]
    assert on["with_offers"] is True
    assert stop["vendor_location_id"] == world["north"]["id"]
    assert (line["line_cost"], line["price_stale"]) == ("2.0000", False)
    assert uuid.UUID(line["flyer_offer_id"])
    # Against the 4.00 regular price there.
    assert stop["expected_saving"] == "2.0000"
    # The price book observation it stood in for is kept.
    assert uuid.UUID(line["observation_id"])
    assert (await admin_client.get(f"{TRIPS}/{on['id']}")).json() == on


async def test_off_the_plan_is_the_price_book_s_alone(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    world,
    no_network: None,
):
    before = await plan(admin_client, world)
    await circular(helper, owner_conn, world, "2.00")
    after = await plan(admin_client, world)

    def costs(trip: dict) -> list:
        return [
            (
                v["kind"],
                v["item_cost"],
                [(s["vendor_location_id"], s["subtotal"]) for s in v["stops"]],
            )
            for v in trip["variants"]
        ]

    assert costs(before) == costs(after)


async def test_an_offer_outside_the_planned_day_is_not_used(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    world,
    no_network: None,
):
    await circular(helper, owner_conn, world, "2.00")
    later = await plan(admin_client, world, with_offers=True, planned_at="2026-11-12T20:00:00Z")
    stop = cheapest_stop(later)
    assert (stop["vendor_location_id"], stop["expected_saving"]) == (world["stall"]["id"], None)


async def test_the_stop_screen_carries_the_offer_it_was_planned_with(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    world,
    no_network: None,
):
    await circular(helper, owner_conn, world, "2.00")
    trip = await plan(admin_client, world, with_offers=True)
    chosen = variant(trip, "cheapest")
    r = await admin_client.post(f"{TRIPS}/{trip['id']}/choose", json={"variant_id": chosen["id"]})
    assert r.status_code == 200, r.text
    screen = (await admin_client.get(f"{TRIPS}/{trip['id']}/stops/1")).json()
    [line] = screen["items"]
    assert line["flyer_offer_id"] == chosen["stops"][0]["items"][0]["flyer_offer_id"]
    assert line["line_cost"] == "2.0000"


async def test_bought_at_the_offer_is_recorded_as_a_sale_price(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    world,
    no_network: None,
):
    """A sale price never joins the regular prices offers and savings are measured
    against."""
    await circular(helper, owner_conn, world, "2.00")
    trip = await plan(admin_client, world, with_offers=True)
    chosen = variant(trip, "cheapest")
    r = await admin_client.post(f"{TRIPS}/{trip['id']}/choose", json={"variant_id": chosen["id"]})
    assert r.status_code == 200, r.text
    [line] = chosen["stops"][0]["items"]
    r = await admin_client.post(
        f"{TRIPS}/{trip['id']}/stops/1/done",
        json={"checked_item_ids": [line["id"]]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    [promo] = await owner_conn.fetch(
        "SELECT o.is_promo FROM price_observation o JOIN purchase_line pl"
        " ON pl.id = o.purchase_line_id WHERE 'offer_price' = ANY(pl.flags)"
    )
    assert promo["is_promo"] is True


def test_a_dearer_offer_takes_from_the_stops_saving():
    from decimal import Decimal

    from app.services import trip_offers

    def line(saving: str | None) -> trip_offers.Offer:
        return trip_offers.Offer(
            item_id=uuid.uuid4(),
            location_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            observation_id=uuid.uuid4(),
            observed_at=IN_THE_AD_WEEK,
            unit_price=Decimal(1),
            packs=None,
            pack_price=None,
            line_cost=Decimal(1),
            stale=False,
            saving=Decimal(saving) if saving else None,
        )

    assert trip_offers.expected_saving([line("2"), line("-1")]) == Decimal("1.0000")
    assert trip_offers.expected_saving([line("-1")]) == Decimal("0.0000")
    assert trip_offers.expected_saving([line(None)]) is None
