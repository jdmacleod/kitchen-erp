"""Pieces in a pack, from pages and the lookup helper (2026-10-05).

Every store, product and figure here is invented.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.catalog.extract import extract, size_from_text
from app.catalog.proposals import Candidate, UnknownCandidate, merge, pieces_value
from app.schemas.products_interchange import FORMAT
from tests import test_pasted_page_lookup as pasted
from tests.pricebook_helpers import make_location, make_product, shelf

D = Decimal
helper = pasted.helper
store = pasted.store
media_root = pasted.media_root


@pytest.mark.parametrize(
    ("text", "pack", "pieces"),
    [
        ("Maple breakfast links, 14 oz, 4 ct", {"qty": "14", "unit": "oz"}, {"count": "4"}),
        ("Sparkling water 6 x 330 ml", {"qty": "1980", "unit": "ml"}, {"count": "6"}),
        ("Seltzer 12 × 12 fl oz", {"qty": "144", "unit": "fl_oz"}, {"count": "12"}),
        ("Burger buns 6-pack 18 oz", {"qty": "18", "unit": "oz"}, {"count": "6"}),
        ("Large eggs 12 ct", {"qty": "12", "unit": "each"}, None),
        ("Rolled oats 15.5 oz / 439 g", {"qty": "439", "unit": "g"}, None),
        ("Breakfast links", None, None),
    ],
)
def test_a_size_and_its_pieces_from_text(text, pack, pieces):
    assert size_from_text(text) == (pack, pieces)


def test_a_page_title_brings_its_pieces():
    found = extract(
        "https://shop.example.test/p/1", meta={"og:title": "Maple Breakfast Links 14 oz (4 count)"}
    )
    fields = merge(found.candidates)
    assert fields["pack"]["value"] == {"qty": "14", "unit": "oz"}
    assert fields["pieces"]["value"] == {"count": "4"}


def test_pieces_are_a_whole_count_and_an_optional_name():
    assert pieces_value({"count": "4", "name": " Link "}) == {"count": "4", "name": "link"}
    assert pieces_value({"count": 4}) == {"count": "4"}
    for bad in ({"count": "4.5"}, {"count": "0"}, {"count": "about 5"}, {"count": True}, {}, "4"):
        assert pieces_value(bad) is None
    with pytest.raises(UnknownCandidate):
        merge([Candidate("pieces", {"count": "4.5"}, "adapter")])


def test_two_counts_conflict_and_two_names_do_not():
    fields = merge(
        [
            Candidate("pieces", {"count": "4", "name": "link"}, "adapter"),
            Candidate("pieces", {"count": "5"}, "page_meta"),
        ]
    )
    assert fields["pieces"]["conflict"] is True
    fields = merge(
        [
            Candidate("pieces", {"count": "4", "name": "link"}, "adapter"),
            Candidate("pieces", {"count": "4", "name": "sausage"}, "page_meta"),
        ]
    )
    assert fields["pieces"]["conflict"] is False


def _answer(request_id: str) -> str:
    candidates = [
        {"field": "title", "value": "Maple Breakfast Links", "source": "adapter"},
        {"field": "pack", "value": {"qty": "14", "unit": "oz"}, "source": "adapter"},
        {"field": "pieces", "value": {"count": "4", "name": "link"}, "source": "adapter"},
    ]
    return json.dumps({"format": FORMAT, "request_id": request_id, "candidates": candidates})


async def test_the_helpers_pieces_reach_the_product_and_reprice_it(admin_client, helper, store):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    links = await make_product(
        admin_client, "Breakfast links", "Maple links", canonical_unit="each"
    )
    # Without a pack, one each is one piece: the whole pack's price counts as one link.
    o = await shelf(admin_client, links["id"], loc["id"], "5.20")
    assert o["norm"]["status"] == "ok" and o["norm"]["canonical_qty"] == "1"

    request = (await pasted.ask(admin_client, links["id"])).json()
    r = await pasted.post_answer(helper, _answer(request["id"]))
    assert r.json()["outcome"] == "update_opened", r.text
    update = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['proposal_id']}")).json()
    assert update["fields"]["pieces"]["value"] == {"count": "4", "name": "link"}

    accepted = await admin_client.post(
        f"/api/v1/product-proposals/{update['id']}/accept",
        json={"action": "update", "product_id": links["id"]},
    )
    assert accepted.status_code == 200, accepted.text
    product = (await admin_client.get(f"/api/v1/products/{links['id']}")).json()
    assert (product["pack_qty"], product["pack_unit"]) == ("14", "oz")
    assert (product["pack_count"], product["piece_name"]) == (4, "link")
    # What was recorded before is priced per piece now.
    after = (await admin_client.get(f"/api/v1/price-observations/{o['id']}")).json()
    assert after["norm"]["bridge_kind"] == "pack_count"
    assert D(after["norm"]["norm_unit_price"]) == D("1.300000")


async def test_a_count_pack_takes_no_pieces(admin_client, helper, store):
    eggs = await make_product(
        admin_client, "Eggs", "Large eggs", canonical_unit="each", pack_qty="12", pack_unit="each"
    )
    request = (await pasted.ask(admin_client, eggs["id"])).json()
    r = await pasted.post_answer(helper, _answer(request["id"]))
    update = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['proposal_id']}")).json()
    accepted = await admin_client.post(
        f"/api/v1/product-proposals/{update['id']}/accept",
        json={"action": "update", "product_id": eggs["id"]},
    )
    assert accepted.status_code == 200, accepted.text
    product = (await admin_client.get(f"/api/v1/products/{eggs['id']}")).json()
    assert (product["pack_qty"], product["pack_unit"], product["pack_count"]) == (
        "12",
        "each",
        None,
    )
