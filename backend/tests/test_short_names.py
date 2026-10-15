"""Product short names: no brand in front, no pack size, sentence case (#347).

Every brand and title here is invented.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.brand_names import name_key
from app.catalog.product_names import short_name


@pytest.mark.parametrize(
    ("title", "brand", "expected"),
    [
        ("HARBOR LIGHT Thick Cut Bacon, 12 oz", "Harbor Light", "Thick cut bacon"),
        ("Harbor Light Organic Baby Kale, 5 oz", "Harbor Light", "Organic baby kale"),
        ("Lantern Farms Plum Jam - 8 Oz", "Lantern Farms", "Plum jam"),
        ("Lantern Farms Rolled Oats 32oz Bag, 32 oz", "Lantern Farms", "Rolled oats"),
        ("Fernhill Lager, 6 cans / 12 fl oz", "Fernhill", "Lager"),
        ("Fernhill Hazy IPA Variety Pack, 12 pk / 12 fl oz", "Fernhill", "Hazy IPA variety"),
        ("Pepper Relish, NET WT 9 OZ (255g)", "Harbor Light", "Pepper relish"),
        ("Snap Peas BIG DEAL!, 32 oz", None, "Snap peas"),
        ("ONION POWDER - HARBOR LIGHT - 2 OZ, 2 OZ", "Harbor Light", "Onion powder"),
        (
            "Harbor Light Coastal Brewing Co Pale Ale",
            "Harbor Light Coastal Brewing Co.",
            "Pale ale",
        ),
        ("Gold Beet, Each", None, "Gold beet"),
        ("Chayote", "Harbor Light", "Chayote"),
        ("Harbor Light", "Harbor Light", "Harbor Light"),
    ],
)
def test_a_title_becomes_its_short_name(title, brand, expected):
    assert short_name(title, brand) == expected


def test_a_brand_spelled_another_way_is_dropped_too():
    keys = {name_key("Harbor Light Organic")}
    assert short_name("Harbor Light Organic Pears", "Harbor Light", brand_keys=keys) == "Pears"


def test_organic_is_part_of_the_name_not_a_brand():
    assert short_name("Organic Lantern Beans", "Organic") == "Organic lantern beans"
    keys = {name_key("Organic")}
    assert short_name("Organic Lantern Beans", None, brand_keys=keys) == "Organic lantern beans"


def test_a_brand_that_is_also_a_food_word_goes_only_from_its_own_products():
    keys = {name_key("Stone")}
    assert short_name("Stone Ground Lantern Mustard", None, brand_keys=keys) == (
        "Stone ground lantern mustard"
    )
    assert short_name("Stone Lantern Ale", "Stone", brand_keys=keys) == "Lantern ale"


def test_a_bunch_stays_in_the_name():
    # No unit says "bunch", so dropping it would lose how the produce is sold.
    assert short_name("Lantern Herb Bunch", None) == "Lantern herb bunch"


def test_the_pack_stays_in_the_name_when_the_product_has_no_pack():
    assert short_name("Snap Peas, 32 oz", None, has_pack=False) == "Snap peas, 32 oz"


def test_acronyms_proper_nouns_and_inner_capitals_keep_their_case():
    assert (
        short_name("DIJON STYLE BBQ SAUCE", None, proper_nouns={"dijon"}) == "Dijon style BBQ sauce"
    )
    assert short_name("Harbor Light McKenzie Blend", "Harbor Light") == "McKenzie blend"


def test_a_name_already_short_is_left_alone():
    assert short_name("Thick cut bacon", "Harbor Light") == "Thick cut bacon"


@given(st.text(max_size=60), st.sampled_from([None, "Harbor Light", "Fernhill"]))
def test_short_names_are_never_empty_and_settle(title, brand):
    once = short_name(title, brand)
    assert short_name(once, brand) == once
    if title.strip():
        assert once.strip()


async def test_the_endpoint_knows_the_households_brands(admin_client):
    r = await admin_client.get(
        "/api/v1/products/short-name",
        params={
            "title": "HARBOR LIGHT Thick Cut Bacon, 12 oz",
            "brand": "Harbor Light",
            "has_pack": "true",
        },
    )
    assert r.status_code == 200 and r.json() == {"name": "Thick cut bacon"}


async def test_a_proposal_carries_its_short_title(admin_client, admin):
    from app.catalog.proposals import Candidate
    from app.core.db import get_sessionmaker
    from app.services import proposals
    from app.services.proposals import Evidence

    page = "https://harbor-light.test/p/2"
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": page, "dom_text": "Thick cut bacon"},
            evidence=Evidence(
                candidates=[
                    Candidate("title", "HARBOR LIGHT Thick Cut Bacon, 12 oz", "page_data"),
                    Candidate("brand", "Harbor Light", "page_data"),
                    Candidate("pack", {"qty": "12", "unit": "oz"}, "page_data"),
                ]
            ),
            source_url=page,
        )
        proposal_id = result.proposal.id
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal_id}")).json()
    assert body["short_title"] == "Thick cut bacon"
    assert body["fields"]["title"]["value"] == "HARBOR LIGHT Thick Cut Bacon, 12 oz"
