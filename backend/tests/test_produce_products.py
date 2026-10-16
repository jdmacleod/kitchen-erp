"""New produce products: loose, no store's brand, no retail filler in the name (issue 371).

Stores, ingredients, products and receipt lines are invented.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.catalog import categories
from app.catalog.product_names import produce_name, short_name
from app.catalog.proposals import Candidate
from app.services import new_products
from app.services.proposals import Evidence
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase
from tests.test_catalog_cleanup import by_kind, suggestions
from tests.test_proposals import capture, user

STORE = "Fernhill Market"
PAGE = "https://www.fernhill-market.example.test/p/lantern-peppers-4410"
__all__ = ["user"]

# --- pure ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Fresh Lantern peppers, 1 ct", "Lantern peppers"),
        ("Jumbo lantern onions, 1 ct", "Jumbo lantern onions"),
        ("fresh dill", "dill"),
        ("Lantern pears (1 each)", "Lantern pears"),
        ("Fresh", "Fresh"),
        ("Lantern peppers, 3 ct", "Lantern peppers, 3 ct"),
    ],
)
def test_produce_names_lose_fresh_and_a_count_of_one(name, short):
    assert produce_name(name) == short
    assert produce_name(short) == short


def test_the_short_name_drops_the_filler_only_for_produce():
    title = "Fresh Lantern peppers, 1 ct"
    assert short_name(title, has_pack=False, produce=True) == "Lantern peppers"
    assert short_name(title, has_pack=False) == title


def test_produce_is_filed_produce_and_not_settled_otherwise_by_its_name():
    assert categories.is_produce("Produce", "lantern peppers")
    assert not categories.is_produce("produce", "frozen lantern peppers")
    assert not categories.is_produce("pantry", "lantern peppers")


@pytest.mark.parametrize(
    ("brand", "dropped"),
    [
        (STORE, True),
        ("fernhill  market", True),
        ("WET VEGETABLES - HERBS", True),
        ("WET VEGETABLES: LEAFY", True),
        ("Organic", True),
        ("Tall Grass Farms", False),
        ("TALL GRASS", False),  # a brand in capitals is still a brand
    ],
)
def test_what_is_not_a_produce_brand(brand, dropped):
    assert new_products.not_a_brand(brand, {"fernhill market"}) is dropped


def test_a_price_per_pound_is_per_weight():
    assert new_products.priced_per_weight({"amount": "1.99", "qty": "1", "unit": "lb"})
    assert not new_products.priced_per_weight({"amount": "0.89", "qty": "1", "unit": "each"})
    assert not new_products.priced_per_weight(None)


# --- the product form ---------------------------------------------------------------


@pytest.fixture
async def peppers(admin_client, db_session) -> dict:
    await seed_units_via_service(db_session)
    await make_location(admin_client, STORE, f"{STORE} Uptown", price_scope="chain")
    return await make_ingredient(admin_client, "lantern peppers", category="produce")


@pytest.mark.parametrize("brand", [STORE, "WET VEGETABLES - PEPPERS", "Organic", None])
async def test_a_produce_product_gets_no_stores_brand_and_is_loose(admin_client, peppers, brand):
    extra = {"brand": brand} if brand else {}
    p = await make_product(admin_client, peppers["id"], "Fresh Lantern peppers, 1 ct", **extra)
    assert (p["name"], p["brand"], p["kind"]) == ("Lantern peppers", None, "loose")


async def test_a_brand_the_person_typed_is_kept_on_produce(admin_client, peppers):
    p = await make_product(admin_client, peppers["id"], "Lantern peppers", brand="Tall Grass Farms")
    assert (p["brand"], p["kind"]) == ("Tall Grass Farms", "branded")


async def test_other_foods_keep_their_name_and_brand(admin_client, peppers):
    oats = await make_ingredient(admin_client, "lantern oats", category="pantry")
    p = await make_product(admin_client, oats["id"], "Fresh rolled oats, 1 ct", brand=STORE)
    assert (p["name"], p["brand"]) == ("Fresh rolled oats, 1 ct", STORE)


# --- a receipt line made into a product ---------------------------------------------


async def test_a_weighed_receipt_line_makes_a_random_weight_product(admin_client, admin, peppers):
    loc = await make_location(admin_client, "Quill Grocers", "Quill Grocers Uptown")
    line = {"raw_text": "LANTERN PEPPERS", "line_total": "1.63", "qty": "0.82", "unit": "lb"}
    purchase = await make_receipt_purchase(admin.id, loc["id"], [line])
    body = (await admin_client.get(f"/api/v1/purchases/{purchase}")).json()
    r = await admin_client.post(
        f"/api/v1/purchases/{purchase}/lines/{body['lines'][0]['id']}/resolve",
        json={
            "product": {
                "ingredient_id": peppers["id"],
                "name": "Lantern peppers",
                "brand": "Quill Grocers",
                "allow_duplicate": True,
            }
        },
    )
    assert r.status_code == 200, r.text
    made = r.json()["lines"][0]["product"]
    product = (await admin_client.get(f"/api/v1/products/{made['id']}")).json()
    assert (product["brand"], product["kind"]) == (None, "random_weight")


# --- a clip accepted as a new product --------------------------------------------------


def _clip(store: dict, *extra: Candidate) -> Evidence:
    return Evidence(
        candidates=[
            Candidate("title", "Fresh Lantern peppers, 1 ct", "page_data"),
            Candidate("brand", STORE, "page_data"),
            Candidate("price", {"amount": "1.99", "qty": "1", "unit": "lb"}, "page_data"),
            *extra,
        ],
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": PAGE, "title": "Peppers"},
        price={"amount": "1.99", "qty": "1", "unit": "lb", "is_promo": False},
    )


async def _accept(admin_client, user, store, peppers, edits=None) -> dict:
    result = await capture(user, _clip(store), url=PAGE)
    body = {"action": "new", "ingredient_id": peppers["id"], "edits": edits or {}}
    r = await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/accept", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["result"]["product_id"]
    return (await admin_client.get(f"/api/v1/products/{pid}")).json()


@pytest.fixture
async def store(admin_client) -> dict:
    return await make_location(admin_client, "Quill Grocers", "Quill Grocers Uptown")


async def test_a_clipped_produce_page_gives_no_brand_and_is_sold_by_weight(
    admin_client, user, store, peppers
):
    product = await _accept(admin_client, user, store, peppers)
    assert (product["name"], product["brand"]) == ("Lantern peppers", None)
    assert product["kind"] == "random_weight"


async def test_a_brand_typed_on_the_review_page_is_kept(admin_client, user, store, peppers):
    product = await _accept(admin_client, user, store, peppers, {"brand": "Tall Grass Farms"})
    assert (product["brand"], product["kind"]) == ("Tall Grass Farms", "branded")


# --- cleanup --------------------------------------------------------------------------


async def test_cleanup_offers_the_produce_name_without_its_filler(
    admin_client, peppers, owner_conn: asyncpg.Connection
):
    p = await make_product(admin_client, peppers["id"], "Lantern peppers")
    # Made before these defaults: the name kept its filler.
    await owner_conn.execute(
        "UPDATE product SET name = 'Fresh Lantern peppers, 1 ct' WHERE id = $1", uuid.UUID(p["id"])
    )
    [s] = by_kind(await suggestions(admin_client), "product_name")
    assert (s["subject_id"], s["after"]) == (p["id"], "Lantern peppers")
    assert "Fresh" in s["reason"]
