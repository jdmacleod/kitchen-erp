"""A clipped page that also shows other products (2M, issue 281).

The model reads the page's text, which includes a "you may also like" strip. It is
told which product the page is about, a reading about a neighbour is dropped, and a
guess that disagrees with the page's own data is an alternative, never a conflict.
The storefront, its products and the strip are invented.
"""

from __future__ import annotations

import json

from app.catalog.identifiers import check_digit
from app.catalog.proposals import Candidate, has_conflict, merge
from app.services import page_reading
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.test_captures import work
from tests.test_page_captures import SHOP, store

no_network = gh.no_network
recorded = ih.recorded
__all__ = ["store"]

PAGE = f"{SHOP}/p/barley-flakes-400g-55120"
JSON_LD = json.dumps(
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Quiet Field Barley Flakes 400 g",
        "sku": "55120",
        "offers": {"@type": "Offer", "price": "2.79", "priceCurrency": "USD"},
    }
)
STRIP = (
    "You may also like\n"
    "Fizzwell Sparkling Lime Soda 12 x 355 ml $6.49 Add to cart\n"
    "Brightwater Peach Sparkling Water 8 x 355 ml $4.99 Add to cart\n"
)


def strip_page() -> dict:
    return {
        "page_url": PAGE,
        "title": "Quiet Field Barley Flakes 400 g | Juniper Market",
        "meta": {},
        "structured_data": [JSON_LD],
        "dom_text": (
            "Quiet Field Barley Flakes 400 g\n$2.79\nToasted barley flakes for porridge.\n" + STRIP
        ),
    }


NEIGHBOUR = {
    "name": "Fizzwell Sparkling Lime Soda",
    "brand": "Fizzwell",
    "pack_qty": "4260",
    "pack_unit": "ml",
    "category": "soda",
    "confidence": 0.9,
}


async def _proposal(admin_client) -> dict:
    created = (await admin_client.post("/api/v1/product-captures", json=strip_page())).json()
    await work()
    return (await admin_client.get(f"/api/v1/product-proposals/{created['id']}")).json()


async def test_the_model_is_told_the_pages_product_inside_the_data(
    admin_client, store, recorded, no_network
):
    transport = recorded({"ProductReading": {"name": "Barley flakes", "confidence": 0.5}})
    await _proposal(admin_client)
    [request] = [r for r in transport.requests if r["stage"] == "ProductReading"]
    user = request["body"]["messages"][-1]["content"]
    task, data = user.split("-----BEGIN", 1)
    assert "Describe only that product" in task and "you may also like" in task
    assert "Barley Flakes" not in task  # the page's words stay in the data block
    assert data.split("\n", 2)[1] == "Product this page is about: Quiet Field Barley Flakes 400 g"


async def test_a_reading_of_a_neighbouring_product_is_dropped_whole(
    admin_client, store, recorded, no_network
):
    recorded({"ProductReading": NEIGHBOUR})
    fields = (await _proposal(admin_client))["fields"]
    assert fields["title"]["value"] == "Quiet Field Barley Flakes 400 g"
    assert "brand" not in fields and "category" not in fields
    assert all(a["source"] != "model" for a in fields["title"]["alternatives"])
    assert fields["pack"]["value"] == {"qty": "400", "unit": "g"}
    assert not fields["pack"]["conflict"]


async def test_a_guess_that_disagrees_with_page_data_is_an_alternative_not_a_conflict(
    admin_client, store, recorded, no_network
):
    # The right product, with the strip's pack read onto it.
    reading = {"name": "Barley flakes", "brand": "Quiet Field", "pack_qty": "355"}
    recorded({"ProductReading": {**reading, "pack_unit": "ml", "confidence": 0.5}})
    fields = (await _proposal(admin_client))["fields"]
    assert fields["pack"]["value"] == {"qty": "400", "unit": "g"}
    guesses = [a for a in fields["pack"]["alternatives"] if a["source"] == "model"]
    assert [g["value"] for g in guesses] == [{"qty": "355", "unit": "ml"}]
    assert fields["pack"]["conflict"] is False
    assert has_conflict(fields) == []
    brand = fields["brand"]
    assert (brand["value"], brand["source"], brand["conflict"]) == ("Quiet Field", "model", False)


def test_two_page_sources_that_disagree_still_conflict():
    fields = merge(
        [
            Candidate("pack", {"qty": "400", "unit": "g"}, "page_data"),
            Candidate("pack", {"qty": "355", "unit": "ml"}, "model"),
            Candidate("pack", {"qty": "500", "unit": "g"}, "adapter"),
        ]
    )
    assert fields["pack"]["conflict"] is True


def _gtin(body: str) -> str:
    """An invented GTIN-14 with its check digit."""
    digits = body.replace(" ", "")
    return digits + str(check_digit(digits))


def test_a_model_guess_against_a_scan_is_no_conflict():
    scanned, guessed = _gtin("000 1234 5678 90"), _gtin("000 9876 5432 10")
    fields = merge([Candidate("gtin", scanned, "scan"), Candidate("gtin", guessed, "model")])
    assert fields["gtin"]["conflict"] is False
    assert fields["gtin"]["alternatives"][0]["source"] == "model"


def test_the_pages_title_comes_only_from_the_page_or_the_person():
    assert page_reading.page_title([Candidate("title", "Oat drink", "page_data")]) == "Oat drink"
    assert page_reading.page_title([Candidate("title", "oat-drink", "address")]) is None
    assert page_reading.page_title([Candidate("title", "Oat drink", "model")]) is None
    assert page_reading.page_title([]) is None


def test_a_name_is_the_same_product_when_it_shares_a_telling_word():
    title = "Quiet Field Barley Flakes 400 g"
    assert page_reading.same_product("Barley flakes", title)
    assert not page_reading.same_product("Fizzwell Sparkling Lime Soda", title)
    # Sizes and filler words say nothing about which product it is.
    assert not page_reading.same_product("Organic fresh soda 400 g", title)


def test_without_a_known_title_the_page_text_goes_alone():
    assert page_reading.page_text("text", None) == "text"
    assert page_reading.page_text("text", "Oat drink").startswith("Product this page is about:")
