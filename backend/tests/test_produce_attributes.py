"""Organic and size grade on produce products, as attributes and not only as words.

Stores, ingredients and products are invented.
"""

from __future__ import annotations

import pytest

from app.catalog.attributes import is_organic, validate_attributes
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location, shelf
from tests.test_produce_products import _accept, store, user

__all__ = ["store", "user"]


# --- pure ------------------------------------------------------------------------


def test_produce_takes_organic_and_a_grade():
    assert validate_attributes("produce", {"organic": True, "grade": "jumbo"}) == {
        "organic": True,
        "grade": "jumbo",
    }
    assert validate_attributes("produce", {"organic": None}) == {}
    assert is_organic({"organic": True}) and not is_organic({}) and not is_organic(None)


@pytest.mark.parametrize("data", [{"grade": "huge"}, {"species": "pork"}, {"organic": "maybe"}])
def test_produce_refuses_what_it_has_no_place_for(data):
    with pytest.raises(ValueError, match="validation error"):
        validate_attributes("produce", data)


def test_other_foods_take_organic_and_nothing_else():
    # Organic is a product's fact for every food, not only produce (03, 1H, 2026-10-27).
    assert validate_attributes("pantry", {"organic": True}) == {"organic": True}
    assert validate_attributes(None, {"organic": False}) == {"organic": False}
    with pytest.raises(ValueError, match="validation error"):
        validate_attributes("dairy", {"grade": "large"})


# --- the product form ---------------------------------------------------------------


@pytest.fixture
async def peppers(admin_client, db_session) -> dict:
    await seed_units_via_service(db_session)
    return await make_ingredient(admin_client, "lantern peppers", category="produce")


@pytest.mark.parametrize(
    ("name", "brand", "kept_brand"),
    [
        ("Organic lantern peppers", None, None),
        ("Lantern peppers", "Organic", None),  # the word is not a brand, but it is kept
        ("Lantern peppers", "Tall Grass Organics", "Tall Grass Organics"),
    ],
)
async def test_organic_in_the_name_or_brand_marks_the_product_organic(
    admin_client, peppers, name, brand, kept_brand
):
    extra = {"brand": brand} if brand else {}
    p = await make_product(admin_client, peppers["id"], name, **extra)
    assert (p["brand"], p["attributes"]) == (kept_brand, {"organic": True})


async def test_a_conventional_product_says_nothing(admin_client, peppers):
    p = await make_product(admin_client, peppers["id"], "Lantern peppers")
    assert p["attributes"] == {}


async def test_what_the_person_sets_wins(admin_client, peppers):
    p = await make_product(
        admin_client,
        peppers["id"],
        "Organic lantern peppers",
        attributes={"organic": False, "grade": "large"},
    )
    assert p["attributes"] == {"organic": False, "grade": "large"}


async def test_other_foods_are_marked_organic_too(admin_client, peppers):
    # Organic is a fact of any product (03, 1H amendment 2026-10-27).
    oats = await make_ingredient(admin_client, "lantern oats", category="pantry")
    p = await make_product(admin_client, oats["id"], "Organic rolled oats")
    assert p["attributes"] == {"organic": True}
    plain = await make_product(admin_client, oats["id"], "Rolled oats")
    assert plain["attributes"] == {}


async def test_a_grade_is_set_and_checked(admin_client, peppers):
    p = await make_product(admin_client, peppers["id"], "Lantern peppers")
    r = await admin_client.patch(
        f"/api/v1/products/{p['id']}", json={"attributes": {"grade": "large"}}
    )
    assert r.status_code == 200 and r.json()["attributes"] == {"grade": "large"}
    r = await admin_client.patch(
        f"/api/v1/products/{p['id']}", json={"attributes": {"grade": "huge"}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_attributes"


async def test_a_product_moved_out_of_produce_keeps_only_organic(admin_client, peppers):
    p = await make_product(admin_client, peppers["id"], "Organic lantern peppers")
    r = await admin_client.patch(
        f"/api/v1/products/{p['id']}", json={"attributes": {"organic": True, "grade": "large"}}
    )
    other = await make_ingredient(admin_client, "lantern chiles", category="produce")
    r = await admin_client.patch(f"/api/v1/products/{p['id']}", json={"ingredient_id": other["id"]})
    assert r.json()["attributes"] == {"organic": True, "grade": "large"}
    jar = await make_ingredient(admin_client, "lantern pepper relish", category="pantry")
    r = await admin_client.patch(f"/api/v1/products/{p['id']}", json={"ingredient_id": jar["id"]})
    # The pantry has no size grades; organic it keeps.
    assert r.status_code == 200 and r.json()["attributes"] == {"organic": True}


# --- a clip accepted as a new product --------------------------------------------------


async def test_a_clipped_organic_page_is_marked_organic(admin_client, user, store, peppers):
    product = await _accept(
        admin_client, user, store, peppers, {"title": "Organic Lantern peppers"}
    )
    assert product["attributes"] == {"organic": True}


# --- offers and compare -----------------------------------------------------------------


async def _two_lantern_offers(client, peppers) -> tuple[dict, dict]:
    loc = await make_location(client, "Quill Grocers", "Quill Grocers Uptown")
    pack = {"pack_qty": "1", "pack_unit": "lb"}
    organic = await make_product(client, peppers["id"], "Organic lantern peppers", **pack)
    plain = await make_product(client, peppers["id"], "Lantern peppers", **pack)
    await shelf(client, organic["id"], loc["id"], "3.10")
    await shelf(client, plain["id"], loc["id"], "2.20")
    return organic, plain


async def test_offers_say_which_are_organic_and_keep_either(admin_client, peppers):
    organic, plain = await _two_lantern_offers(admin_client, peppers)
    url = f"/api/v1/ingredients/{peppers['id']}/offers"
    items = (await admin_client.get(url)).json()["items"]
    assert {o["product_id"]: o["organic"] for o in items} == {
        organic["id"]: True,
        plain["id"]: False,
    }
    for scope, kept in (("organic", organic), ("conventional", plain)):
        items = (await admin_client.get(url, params={"organic_scope": scope})).json()["items"]
        assert [o["product_id"] for o in items] == [kept["id"]]
    assert (await admin_client.get(url, params={"organic_scope": "local"})).status_code == 422


async def test_compare_keeps_the_cheapest_of_the_scope(admin_client, peppers):
    organic, plain = await _two_lantern_offers(admin_client, peppers)

    async def cell(**scope) -> dict:
        body = {"ingredient_ids": [peppers["id"]], **scope}
        r = await admin_client.post("/api/v1/price-book/compare", json=body)
        return next(iter(r.json()["rows"][0]["cells"].values()))

    assert ((await cell())["product_id"], (await cell())["organic"]) == (plain["id"], False)
    only = await cell(organic_scope="organic")
    assert (only["product_id"], only["organic"]) == (organic["id"], True)
