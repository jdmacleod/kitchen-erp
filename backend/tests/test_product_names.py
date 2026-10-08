"""Product names and brands are tidied as they are saved (2P).

Names here are invented. Signs and odd spaces are named constants built with chr(),
so the file stays readable plain text.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.product_names import tidy
from app.models import Product

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
        "ingredient": {"name": "Plum jam"},
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
    body = {"ingredient": {"name": "Plum jam"}, "name": f"Fernhill{TM} Plum Jam"}
    assert (await admin_client.post("/api/v1/products", json=body)).status_code == 201
    r = await admin_client.get("/api/v1/products/search", params={"q": f"fernhill{REG} plum"})
    assert [h["name"] for h in r.json()["items"]] == ["Fernhill Plum Jam"]
