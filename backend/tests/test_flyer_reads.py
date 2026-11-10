"""Weekly flyers 2U-4 (18): this week's linked offers, an offer's price per unit, a
product's offer history, and sizes taken from the product (U9, U16). Stores and
offers are invented."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.db import get_sessionmaker
from app.services import flyer_reads, flyers
from app.services.price_rounding import unit_price
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.test_flyer_offers import THURSDAY, links
from tests.test_flyers import (
    WEDNESDAY,
    candidate,
    queue,
    report,
    requests,
    store,  # noqa: F401  the invented store fixture
)
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client

# One pound in grams, the canonical unit of the invented lentils.
POUND = Decimal("453.59237")


@pytest.fixture(autouse=True)
def _in_the_ad_week(monkeypatch):
    monkeypatch.setattr(flyers, "today", lambda now=None: THURSDAY)


async def lentils(admin_client, location: dict, **pack) -> dict:
    """Invented lentils, bought at the store for $3 a month ago, so offers of them matter."""
    made = await make_product(admin_client, "Lentils", "Larkspur Green Lentils", **pack)
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": location["id"],
            "purchased_at": (WEDNESDAY - timedelta(days=30)).isoformat(),
            "lines": [{"product_id": made["id"], "qty": "1", "unit": "each", "unit_price": "3"}],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201, r.text
    return made


async def learned(owner_conn, location: dict, product: dict, name: str = "green lentils") -> None:
    """The vendor's printed name is already learned, so its offers link on their own."""
    await owner_conn.execute(
        "INSERT INTO flyer_alias (id, vendor_id, name_norm, product_id, ignore) "
        "VALUES ($1, $2, $3, $4, false)",
        uuid.uuid4(),
        uuid.UUID(location["vendor"]["id"]),
        name,
        uuid.UUID(product["id"]),
    )


async def read(helper_client, candidates: list[dict], again: str | None = None, **week) -> str:
    """Read the week, answering its request. Given that request again, the report
    stands in for the next week's, which the fixtures' calendar hasn't reached."""
    if again is None:
        await queue()
        [request] = await requests(helper_client)
        again = request["id"]
    r = await report(helper_client, again, candidates=candidates, **week)
    assert r.status_code == 200, r.text
    return again


async def kitchen(owner_conn, location: dict) -> str:
    return str(
        await owner_conn.fetchval(
            "SELECT home_base_id FROM vendor_location WHERE id = $1", uuid.UUID(location["id"])
        )
    )


async def current(admin_client, kitchen_id: str, on: date | None = None) -> list[dict]:
    params = {"home_base_id": kitchen_id, **({"on": on.isoformat()} if on else {})}
    r = await admin_client.get("/api/v1/flyer-offers/current", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# --- U9: current offers ------------------------------------------------------------------


async def test_a_linked_offer_is_current_in_its_week_and_gone_after(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await learned(owner_conn, store, product)
    await read(helper, [candidate("Green Lentils", "2.49", size_qty="1", size_unit="lb")])
    home = await kitchen(owner_conn, store)

    [offer] = await current(admin_client, home)
    assert offer["product"] == {
        "id": product["id"],
        "name": "Larkspur Green Lentils",
        "ingredient_id": product["ingredient"]["id"],
    }
    assert (offer["location"]["id"], offer["price"], offer["size_inherited"]) == (
        store["id"],
        "2.4900",
        False,
    )
    assert (offer["unit"], Decimal(offer["unit_price"])) == (
        "g",
        unit_price(Decimal("2.49"), POUND),
    )
    assert (offer["ad_week_from"], offer["valid_to"]) == ("2026-11-04", "2026-11-10")
    assert await current(admin_client, home, date(2026, 11, 11)) == []
    assert await current(admin_client, home, date(2026, 11, 3)) == []


async def test_an_offer_waiting_for_a_person_is_not_current(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    await lentils(admin_client, store)
    await read(helper, [candidate("Green Lentils")])
    [link] = await links(owner_conn)
    assert link["status"] == "suggested"
    assert await current(admin_client, await kitchen(owner_conn, store)) == []


async def test_at_a_week_boundary_the_later_offer_wins(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store)
    await learned(owner_conn, store, product)
    first = await read(helper, [candidate("Green Lentils", "2.49")])
    nxt = {"valid_from": "2026-11-10", "valid_to": "2026-11-16"}
    await read(
        helper,
        [candidate("Green Lentils", "1.99", **nxt)],
        first,
        ad_week_from="2026-11-10",
        ad_week_to="2026-11-16",
    )
    home = await kitchen(owner_conn, store)
    assert [o["price"] for o in await current(admin_client, home, date(2026, 11, 9))] == ["2.4900"]
    assert [o["price"] for o in await current(admin_client, home, date(2026, 11, 10))] == ["1.9900"]


# --- U16: sizes from the product ------------------------------------------------------------


async def test_an_offer_without_a_size_reads_by_its_products(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await learned(owner_conn, store, product)
    await read(helper, [candidate("Green Lentils", "2.49")])
    [offer] = await current(admin_client, await kitchen(owner_conn, store))
    assert (offer["size_qty"], offer["size_unit"], offer["size_inherited"]) == ("1", "lb", True)
    assert Decimal(offer["unit_price"]) == unit_price(Decimal("2.49"), POUND)


async def test_a_size_learned_by_a_person_is_inherited_too(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
):
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await read(helper, [candidate("Green Lentils", "2.49")])
    r = await admin_client.post(
        "/api/v1/flyer-offers/decide",
        json={
            "vendor_id": store["vendor"]["id"],
            "name_norm": "green lentils",
            "product_id": product["id"],
        },
    )
    assert r.status_code == 200, r.text
    [offer] = (await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")).json()[
        "items"
    ]
    assert (offer["size_qty"], offer["size_inherited"]) == ("1", True)


async def test_an_offer_with_no_size_anywhere_has_no_unit_price(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store)
    await learned(owner_conn, store, product)
    await read(helper, [candidate("Green Lentils", "2.49")])
    [offer] = await current(admin_client, await kitchen(owner_conn, store))
    assert (offer["size_qty"], offer["size_inherited"], offer["unit_price"]) == (None, False, None)
    assert offer["price"] == "2.4900"  # still shown as printed


# --- offer_price: each basis -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ({"price_basis": "multi", "price": "5", "basis_qty": "3"}, Decimal(5) / 3),
        ({"price_basis": "bogo", "price": "4"}, Decimal(2)),
        ({"price_basis": "per_lb", "price": "3", "size_qty": None, "size_unit": None}, Decimal(3)),
        # The $3 bought a month ago is the regular price: 25% off, and $1 off.
        ({"price_basis": "percent_off", "price": None, "discount": "25"}, Decimal("2.25")),
        ({"price_basis": "dollar_off", "price": None, "discount": "1"}, Decimal(2)),
    ],
)
async def test_each_basis_has_its_price_per_unit(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
    printed,
    expected,
):
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await learned(owner_conn, store, product)
    sized = {"size_qty": "1", "size_unit": "lb"}
    await read(helper, [candidate("Green Lentils", **(sized | printed))])
    async with get_sessionmaker()() as db:
        priced = await flyer_reads.offer_price(
            db, uuid.UUID(product["id"]), uuid.UUID(store["id"]), THURSDAY
        )
    assert priced is not None
    assert priced.unit == "g"
    assert priced.price is not None
    # A discount is taken off the rounded regular price, so it may differ in the last place.
    assert abs(priced.price - unit_price(expected, POUND)) <= Decimal("0.000001")
    if printed["price_basis"].endswith("_off"):
        assert priced.regular == unit_price(Decimal(3), POUND)


async def test_a_discount_with_no_regular_price_is_shown_only_as_printed(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await make_product(admin_client, "Lentils", "Larkspur Green Lentils")
    await learned(owner_conn, store, product)
    await owner_conn.execute(
        "UPDATE ingredient SET watch = true WHERE id = $1", uuid.UUID(product["ingredient"]["id"])
    )
    await read(helper, [candidate("Green Lentils", None, price_basis="percent_off", discount="20")])
    [offer] = await current(admin_client, await kitchen(owner_conn, store))
    assert (offer["discount"], offer["unit_price"], offer["regular_unit_price"]) == (
        "20.0000",
        None,
        None,
    )


# --- history -----------------------------------------------------------------------------------


async def test_a_products_offer_history_lists_every_week(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store)
    await learned(owner_conn, store, product)
    first = await read(helper, [candidate("Green Lentils", "2.49")])
    nxt = {"valid_from": "2026-11-11", "valid_to": "2026-11-17"}
    await read(
        helper,
        [candidate("Green Lentils", "1.99", **nxt)],
        first,
        ad_week_from="2026-11-11",
        ad_week_to="2026-11-17",
    )
    r = await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")
    assert r.status_code == 200, r.text
    assert [(o["ad_week_from"], o["price"]) for o in r.json()["items"]] == [
        ("2026-11-11", "1.9900"),
        ("2026-11-04", "2.4900"),
    ]
    elsewhere = await admin_client.get(
        f"/api/v1/products/{product['id']}/flyer-offers",
        params={"vendor_location_id": str(uuid.uuid4())},
    )
    assert elsewhere.json()["items"] == []
    missing = await admin_client.get(f"/api/v1/products/{uuid.uuid4()}/flyer-offers")
    assert missing.status_code == 404


async def test_the_history_prices_each_week_and_gives_a_typical_price(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    """X3: the product page's typical price is the median of its regular prices and
    its offers' over the last twelve weeks; a week not begun yet doesn't count."""
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await learned(owner_conn, store, product)
    pound = {"size_qty": "1", "size_unit": "lb"}
    first = await read(helper, [candidate("Green Lentils", "2.49", **pound)])
    nxt = {"valid_from": "2026-11-11", "valid_to": "2026-11-17"}
    await read(
        helper,
        [candidate("Green Lentils", "1.99", **pound, **nxt)],
        first,
        ad_week_from="2026-11-11",
        ad_week_to="2026-11-17",
    )
    body = (await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")).json()
    assert [(o["display_unit_price"], o["display_unit"]) for o in body["items"]] == [
        ("1.99", "lb"),
        ("2.49", "lb"),
    ]
    typical = body["typical"]
    # The $3 bought a month ago and this week's $2.49; next week's $1.99 hasn't begun.
    assert (typical["display_unit"], typical["regular_count"], typical["offer_count"]) == (
        "lb",
        1,
        1,
    )
    assert Decimal(typical["display_unit_price"]).quantize(Decimal("0.01")) == Decimal("2.75")


async def test_a_discount_in_the_history_is_taken_off_the_regular_price_then(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    """The history reads every regular price its discounts need at once; the price is
    the one ``current_offers`` gives for the same week."""
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    await learned(owner_conn, store, product)
    pound = {"size_qty": "1", "size_unit": "lb"}
    await read(
        helper,
        [candidate("Green Lentils", None, price_basis="percent_off", discount="20", **pound)],
    )
    [current_offer] = await current(admin_client, await kitchen(owner_conn, store))
    body = (await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")).json()
    [offer] = body["items"]
    assert current_offer["unit_price"] is not None
    assert offer["unit_price"] == current_offer["unit_price"]
    assert offer["display_unit"] == "lb"
    assert Decimal(offer["display_unit_price"]).quantize(Decimal("0.01")) == Decimal("2.40")


async def test_a_chain_price_counts_once_in_the_typical_price(admin_client):
    """A chain price applies at every branch, and is still one price."""
    first = await make_location(
        admin_client, "Larkspur chain", "Larkspur north", kind="chain", price_scope="chain"
    )
    await make_location(
        admin_client, "Larkspur chain", "Larkspur south", vendor_id=first["vendor"]["id"]
    )
    product = await make_product(
        admin_client, "Lentils", "Larkspur Green Lentils", pack_qty="1", pack_unit="lb"
    )
    await shelf(
        admin_client, product["id"], first["id"], "3.00", observed_at="2026-10-20T17:00:00Z"
    )
    body = (await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")).json()
    assert body["typical"]["regular_count"] == 1


async def test_a_product_with_no_prices_has_no_typical_price(admin_client):
    product = await make_product(admin_client, "Lentils", "Larkspur Green Lentils")
    body = (await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")).json()
    assert body == {"items": [], "typical": None}


async def test_a_counted_pack_is_priced_per_piece_not_per_pack_of_packs(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await make_product(
        admin_client,
        "Eggs",
        "Larkspur Eggs",
        canonical_unit="each",
        pack_qty="6",
        pack_unit="each",
    )
    await learned(owner_conn, store, product, "eggs")
    await owner_conn.execute(
        "UPDATE ingredient SET watch = true WHERE id = $1", uuid.UUID(product["ingredient"]["id"])
    )
    await read(
        helper,
        [
            candidate("Eggs", "6"),
            candidate("Eggs", "6", size_qty="6", size_unit="each", valid_from="2026-11-05"),
        ],
    )
    # The later valid_from is the printed six; both are $1 an egg.
    [offer] = await current(admin_client, await kitchen(owner_conn, store))
    assert (offer["size_inherited"], offer["unit"], offer["unit_price"]) == (
        False,
        "each",
        "1.000000",
    )
    [printed, inherited] = (
        await admin_client.get(f"/api/v1/products/{product['id']}/flyer-offers")
    ).json()["items"]
    assert (printed["size_inherited"], inherited["size_inherited"]) == (False, True)
    async with get_sessionmaker()() as db:
        priced = await flyer_reads.offer_price(
            db, uuid.UUID(product["id"]), uuid.UUID(store["id"]), date(2026, 11, 4)
        )
    assert priced is not None and priced.price == Decimal(1)


async def test_a_size_given_to_the_product_later_is_read_by(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store)
    await learned(owner_conn, store, product)
    await read(helper, [candidate("Green Lentils", "2.49")])
    r = await admin_client.patch(
        f"/api/v1/products/{product['id']}", json={"pack_qty": "1", "pack_unit": "lb"}
    )
    assert r.status_code == 200, r.text
    [offer] = await current(admin_client, await kitchen(owner_conn, store))
    assert (offer["size_qty"], offer["size_inherited"]) == ("1", True)
    assert Decimal(offer["unit_price"]) == unit_price(Decimal("2.49"), POUND)


async def test_a_discount_is_off_the_prices_seen_by_that_day_only(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    product = await lentils(admin_client, store, pack_qty="1", pack_unit="lb")
    later = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": store["id"],
            "purchased_at": "2026-11-07T18:00:00Z",
            "lines": [{"product_id": product["id"], "qty": "1", "unit": "each", "unit_price": "9"}],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert later.status_code == 201, later.text
    await learned(owner_conn, store, product)
    await read(helper, [candidate("Green Lentils", None, price_basis="percent_off", discount="50")])
    async with get_sessionmaker()() as db:
        priced = await flyer_reads.offer_price(
            db, uuid.UUID(product["id"]), uuid.UUID(store["id"]), THURSDAY
        )
    # Only the $3 bought before Thursday counts, not the $9 bought after it.
    assert priced is not None and priced.regular == unit_price(Decimal(3), POUND)
