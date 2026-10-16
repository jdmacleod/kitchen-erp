"""Product names and brands are tidied as they are saved (2P).

Names here are invented. Signs and odd spaces are named constants built with chr(),
so the file stays readable plain text.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.product_names import tidy
from app.catalog.proposals import Candidate
from app.core.db import get_sessionmaker
from app.models import Product
from app.services import proposals
from app.services.proposals import Evidence
from tests.pricebook_helpers import make_location
from tests.test_naming import _queue as naming_queue

TM, REG, COPY = chr(0x2122), chr(0x00AE), chr(0x00A9)
NBSP, ZWSP = chr(0x00A0), chr(0x200B)
RSQUO, LDQUO, RDQUO = chr(0x2019), chr(0x201C), chr(0x201D)
EN_DASH, EM_DASH, DOUBLE_PRIME = chr(0x2013), chr(0x2014), chr(0x2033)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (f"Fernhill{TM} Plum Jam", "Fernhill Plum Jam"),
        (f"Copperleaf{REG} 80/20 Ground Lamb", "Copperleaf 80/20 Ground Lamb"),
        (f"Lark{COPY} Loaf", "Lark Loaf"),
        (f"Lark{REG}, Petite Loaf", "Lark, Petite Loaf"),
        (f"Moss Bay{RSQUO}s Kelp Chips", "Moss Bay's Kelp Chips"),
        (f"{LDQUO}Old{RDQUO} Tarn Cheese", '"Old" Tarn Cheese'),
        ('Lark Wrap 10" 6 ct', 'Lark Wrap 10" 6 ct'),
        (f"Lark Wrap 10{DOUBLE_PRIME}", 'Lark Wrap 10"'),
        (f"Plum Jam {EN_DASH} Spiced", "Plum Jam - Spiced"),
        (f"Plum{NBSP}Jam{ZWSP} ", "Plum Jam"),
        ("  Plum   Jam  ", "Plum Jam"),
        ("Plum Jam", "Plum Jam"),
    ],
)
def test_tidy(raw, clean):
    assert tidy(raw) == clean


@given(st.text(max_size=60))
def test_tidy_is_idempotent_and_leaves_no_signs(text):
    once = tidy(text)
    assert tidy(once) == once
    assert not any(c in once for c in (TM, REG, COPY, NBSP, ZWSP, RSQUO, EM_DASH))


def test_a_product_is_tidied_as_it_is_set():
    product = Product(name=f"Fernhill{TM}  Plum Jam", brand=REG)
    assert (product.name, product.brand) == ("Fernhill Plum Jam", None)
    product.brand = f"Fernhill{TM}"
    assert product.brand == "Fernhill"


async def test_saved_names_are_tidied_through_the_api(admin_client):
    body = {
        "ingredient": {"name": "Plum jam", "category": "pantry"},
        "name": f"Fernhill{TM} Plum Jam",
        "brand": f"Fernhill{REG}",
    }
    r = await admin_client.post("/api/v1/products", json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["name"], r.json()["brand"]) == ("Fernhill Plum Jam", "Fernhill")
    renamed = {"name": f"Plum{NBSP}Jam{EN_DASH}Spiced"}
    r = await admin_client.patch(f"/api/v1/products/{r.json()['id']}", json=renamed)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Plum Jam-Spiced"


async def test_a_pasted_sign_still_finds_the_tidied_name(admin_client):
    body = {
        "ingredient": {"name": "Plum jam", "category": "pantry"},
        "name": f"Fernhill{TM} Plum Jam",
    }
    assert (await admin_client.post("/api/v1/products", json=body)).status_code == 201
    r = await admin_client.get("/api/v1/products/search", params={"q": f"fernhill{REG} plum"})
    assert [h["name"] for h in r.json()["items"]] == ["Fernhill Plum Jam"]


# --- the other save paths (criterion 106) ---------------------------------------------------


async def _clip(admin, store: dict, title: str, brand: str, page: str):
    evidence = Evidence(
        candidates=[Candidate("title", title, "page_data"), Candidate("brand", brand, "page_data")],
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": page, "title": title},
    )
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": page},
            evidence=evidence,
            source_url=page,
        )
    return result.proposal


async def test_an_accepted_proposal_is_tidied_as_new_and_as_an_update(admin_client, admin):
    store = await make_location(admin_client, "Juniper Market", "Juniper Market")
    ing = (
        await admin_client.post("/api/v1/ingredients", json={"name": "Kelp", "category": "pantry"})
    ).json()
    first = await _clip(
        admin, store, f"Moss Bay{RSQUO}s Kelp{NBSP}Chips{TM}", f"Moss Bay{REG}", "https://a.test/1"
    )
    r = await admin_client.post(
        f"/api/v1/product-proposals/{first.id}/accept",
        json={"action": "new", "ingredient_id": ing["id"]},
    )
    assert r.status_code == 200, r.text
    product_id = r.json()["product_id"]
    made = (await admin_client.get(f"/api/v1/products/{product_id}")).json()
    assert (made["name"], made["brand"]) == ("Moss Bay's Kelp Chips", "Moss Bay")

    second = await _clip(admin, store, "Kelp chips", "Moss Bay", "https://a.test/2")
    r = await admin_client.post(
        f"/api/v1/product-proposals/{second.id}/accept",
        json={
            "action": "update",
            "product_id": product_id,
            "edits": {"title": f"Kelp Chips {EM_DASH} Sea Salt 4{DOUBLE_PRIME}"},
        },
    )
    assert r.status_code == 200, r.text
    updated = (await admin_client.get(f"/api/v1/products/{product_id}")).json()
    assert updated["name"] == 'Kelp Chips - Sea Salt 4"'


async def test_bulk_naming_tidies_the_name(admin_client, admin, db_session):
    loc = await naming_queue(admin_client, admin, db_session)
    row = {
        "vendor_id": loc["vendor"]["id"],
        "raw_text_norm": "BNLS CHKN BRST",
        "name": f"Larkfield{TM} Chicken{NBSP}Breast",
        "ingredient": {"name": "chicken breast", "category": "meat"},
    }
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": [row]})
    assert r.status_code == 200, r.text
    [result] = r.json()["results"]
    assert result["error"] is None
    product = (await admin_client.get(f"/api/v1/products/{result['product_id']}")).json()
    assert product["name"] == "Larkfield Chicken Breast"
