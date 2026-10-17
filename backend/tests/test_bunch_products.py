"""Produce sold by the bunch (issue 371): the pack says "1 bunch", and the name needn't.

Stores, ingredients, products and pages are invented.
"""

from __future__ import annotations

import time
import uuid

import asyncpg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.product_names import drop_bunch, says_bunch, short_name
from app.catalog.proposals import Candidate
from app.services.proposals import Evidence
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.test_catalog_cleanup import accept, by_kind, suggestions
from tests.test_proposals import capture, user

PAGE = "https://www.quill-grocers.example.test/p/vellum-cress-2207"
__all__ = ["user"]

# --- pure ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "dropped"),
    [
        ("Vellum cress bunch", "Vellum cress"),
        ("Vellum cress, 1 bunch", "Vellum cress"),
        ("Vellum cress (bunch)", "Vellum cress"),
        ("Bunch of vellum cress", "Vellum cress"),
        ("1 bunch vellum cress", "vellum cress"),
        ("Vellum cress bunches", "Vellum cress"),
        ("Bunch", "Bunch"),
        ("Vellum bunch cress", "Vellum bunch cress"),
        ("Vellum cress", "Vellum cress"),
    ],
)
def test_drop_bunch(name, dropped):
    assert drop_bunch(name) == dropped
    assert drop_bunch(dropped) == dropped or dropped == "Bunch"


def test_says_bunch_reads_the_word_only():
    assert says_bunch("Vellum cress BUNCH") and says_bunch("2 bunches of cress")
    assert not says_bunch("Bunchberry jam") and not says_bunch(None)


def test_the_short_name_keeps_bunch_unless_the_pack_says_it():
    title = "Vellum cress bunch"
    assert short_name(title, produce=True, has_pack=False) == title
    assert short_name(title, produce=True, has_pack=True, bunch_pack=True) == "Vellum cress"


@pytest.mark.parametrize("name", [" " * 20000 + "x", "cress" + " " * 20000 + "1", "(" * 20000])
def test_a_long_run_of_spaces_is_cheap(name):
    started = time.perf_counter()
    drop_bunch(name)
    says_bunch(name)
    assert time.perf_counter() - started < 0.5


@given(st.text(st.sampled_from(list("abunchBUNCH ,-()1of")), max_size=40))
def test_drop_bunch_never_empties_a_name(name):
    if name.strip():
        assert drop_bunch(name).strip()


# --- creating products --------------------------------------------------------------


@pytest.fixture
async def cress(admin_client, db_session) -> dict:
    await seed_units_via_service(db_session)
    return await make_ingredient(admin_client, "vellum cress", category="produce")


async def test_a_produce_name_with_bunch_makes_the_pack(admin_client, cress):
    p = await make_product(admin_client, cress["id"], "Fresh Vellum cress bunch")
    assert (p["name"], p["pack_qty"], p["pack_unit"]) == ("Vellum cress", "1", "bunch")


async def test_a_pack_given_wins_and_the_name_keeps_bunch(admin_client, cress):
    p = await make_product(
        admin_client, cress["id"], "Vellum cress bunch", pack_qty="200", pack_unit="g"
    )
    assert (p["name"], p["pack_unit"]) == ("Vellum cress bunch", "g")


async def test_a_bunch_pack_given_drops_the_word(admin_client, cress):
    p = await make_product(
        admin_client, cress["id"], "Vellum cress, 1 bunch", pack_qty="1", pack_unit="bunch"
    )
    assert p["name"] == "Vellum cress"


async def test_other_foods_are_not_bunched(admin_client, cress):
    cereal = await make_ingredient(admin_client, "vellum flakes", category="pantry")
    p = await make_product(admin_client, cereal["id"], "Vellum bunch flakes")
    assert (p["name"], p["pack_unit"]) == ("Vellum bunch flakes", None)


async def test_the_short_name_hint_reads_the_pack_unit(admin_client, cress):
    url = "/api/v1/products/short-name"
    r = await admin_client.get(url, params={"title": "Vellum cress bunch"})
    assert r.json()["name"] == "Vellum cress bunch"
    params = {"title": "Vellum cress bunch", "has_pack": True, "pack_unit": "bunch"}
    assert (await admin_client.get(url, params=params)).json()["name"] == "Vellum cress"


# --- a clip accepted as a new product --------------------------------------------------


async def test_a_clipped_bunch_gets_the_pack(admin_client, user, cress):
    store = await make_location(admin_client, "Quill Grocers", "Quill Grocers Uptown")
    evidence = Evidence(
        candidates=[
            Candidate("title", "Vellum Cress Bunch", "page_data"),
            Candidate("price", {"amount": "1.29", "qty": "1", "unit": "each"}, "page_data"),
        ],
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": PAGE, "title": "Cress"},
        price={"amount": "1.29", "qty": "1", "unit": "each", "is_promo": False},
    )
    result = await capture(user, evidence, url=PAGE)
    body = {"action": "new", "ingredient_id": cress["id"], "edits": {}}
    r = await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/accept", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["result"]["product_id"]
    product = (await admin_client.get(f"/api/v1/products/{pid}")).json()
    assert (product["name"], product["pack_qty"], product["pack_unit"]) == (
        "Vellum Cress",
        "1",
        "bunch",
    )


# --- cleanup --------------------------------------------------------------------------


async def test_cleanup_offers_a_bunch_pack_then_the_name(
    admin_client, cress, owner_conn: asyncpg.Connection
):
    p = await make_product(admin_client, cress["id"], "Vellum cress")
    # Made before bunches: one each, and only the name said bunch.
    await owner_conn.execute(
        "UPDATE product SET name = 'Vellum cress bunch', pack_qty = 1, pack_unit = 'each' "
        "WHERE id = $1",
        uuid.UUID(p["id"]),
    )
    items = await suggestions(admin_client)
    assert by_kind(items, "product_name") == []
    [s] = by_kind(items, "product_pack")
    assert (s["subject_id"], s["before"], s["after"]) == (p["id"], "1 each", "1 bunch")
    await accept(admin_client, s["key"])
    product = (await admin_client.get(f"/api/v1/products/{p['id']}")).json()
    assert (product["pack_qty"], product["pack_unit"]) == ("1", "bunch")
    items = await suggestions(admin_client)
    assert by_kind(items, "product_pack") == []
    [n] = by_kind(items, "product_name")
    assert n["after"] == "Vellum cress"


async def test_cleanup_leaves_a_weighed_pack_alone(admin_client, cress):
    await make_product(
        admin_client, cress["id"], "Vellum cress bunch", pack_qty="200", pack_unit="g"
    )
    assert by_kind(await suggestions(admin_client), "product_pack") == []
