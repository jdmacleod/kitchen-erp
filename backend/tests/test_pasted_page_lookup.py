"""A page pasted in Add product goes to the lookup helper (04, 2M with a helper).

The storefront, its pages and its products are invented.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from app.core.config import get_settings
from app.schemas.products_interchange import FORMAT
from tests import test_products_helper as tph
from tests.pricebook_helpers import make_location, make_product

SHOP = "https://www.hollow-creek-market.example.test"
PAGE = f"{SHOP}/p/rolled-oats-1kg-4417"
helper = tph.helper  # the stub helper: products:read and products:suggest tokens
post_answer = tph.post_answer


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def store(admin_client) -> dict:
    loc = await make_location(
        admin_client, "Hollow Creek Market", "Hollow Creek Market", price_scope="chain"
    )
    r = await admin_client.patch(f"/api/v1/vendors/{loc['vendor']['id']}", json={"website": SHOP})
    assert r.status_code == 200, r.text
    return loc


async def ask(admin_client, product_id: str, page_url: str = PAGE):
    return await admin_client.post(
        f"/api/v1/products/{product_id}/look-up", json={"page_url": page_url}
    )


def answer(request_id: str, price: object = 3.79, title: str = "Rolled Oats 1kg") -> str:
    candidates = [
        {"field": "title", "value": title, "source": "page_data", "source_url": PAGE},
        {"field": "brand", "value": "Larkfield", "source": "page_data"},
        {"field": "item_number", "value": "4417", "source": "page_data"},
    ]
    if price is not None:
        candidates.append({"field": "price", "value": price, "source": "page_data"})
    return json.dumps({"format": FORMAT, "request_id": request_id, "candidates": candidates})


async def test_without_a_helper_the_page_is_not_queued(admin_client):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    r = await ask(admin_client, oats["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_helper"


async def test_a_pasted_page_is_queued_once_for_the_helper(admin_client, helper):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    first = await ask(admin_client, oats["id"], PAGE + "?utm_source=x#reviews")
    again = await ask(admin_client, oats["id"])
    assert first.status_code == 200, first.text
    assert again.json()["id"] == first.json()["id"]
    queue = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()
    [request] = queue["items"]
    assert (request["kind"], request["value"], request["listing_id"]) == ("page", PAGE, None)


@pytest.mark.parametrize("bad", ["not a web address", "ftp://example.test/oats"])
async def test_only_web_addresses_are_queued(admin_client, helper, bad):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    r = await ask(admin_client, oats["id"], bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_url"


async def test_an_unknown_product_is_refused(admin_client, helper):
    r = await ask(admin_client, str(uuid.uuid4()))
    assert r.status_code == 404


async def test_the_answer_for_a_stores_page_brings_its_listing_and_price(
    admin_client,
    helper,
    store,
    owner_conn: asyncpg.Connection,
):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    request = (await ask(admin_client, oats["id"])).json()
    r = await post_answer(helper, answer(request["id"]))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "update_opened"
    update = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['proposal_id']}")).json()
    assert update["kind"] == "product_update"
    # Regression: ISSUE-003 — a lookup update said "Same barcode" for a product without one
    # Found by /qa on 2026-10-05
    assert update["match"]["strong"] == {"product_id": oats["id"], "reason": "lookup"}
    assert update["listing"]["canonical_url"] == PAGE
    assert update["listing"]["vendor_sku"] == "4417"
    assert update["price"] == {"amount": "3.79", "qty": "1", "unit": "each", "is_promo": False}

    accepted = await admin_client.post(
        f"/api/v1/product-proposals/{update['id']}/accept",
        json={
            "action": "update",
            "product_id": oats["id"],
            "record_price": True,
            "vendor_location_id": store["id"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    listing = await owner_conn.fetchrow(
        "SELECT product_id, canonical_url FROM vendor_listing WHERE canonical_url = $1", PAGE
    )
    assert listing["product_id"] == uuid.UUID(oats["id"])
    price = await owner_conn.fetchval(
        "SELECT price FROM price_observation WHERE source = 'listing' AND product_id = $1",
        uuid.UUID(oats["id"]),
    )
    assert price == Decimal("3.79")


async def test_a_page_with_nothing_new_closes_without_a_proposal(
    admin_client,
    helper,
    store,
):
    oats = await make_product(admin_client, "Oats", "Rolled oats", brand="Larkfield")
    first = (await ask(admin_client, oats["id"])).json()
    opened = (await post_answer(helper, answer(first["id"]))).json()
    await admin_client.post(
        f"/api/v1/product-proposals/{opened['proposal_id']}/accept",
        json={"action": "update", "product_id": oats["id"]},
    )
    # The listing now exists and the details match: a second read changes nothing.
    second = (await ask(admin_client, oats["id"])).json()
    r = await post_answer(helper, answer(second["id"], title="Rolled oats"))
    assert r.json()["outcome"] == "no_change"


async def test_a_page_from_no_known_store_brings_details_but_no_listing(
    admin_client,
    helper,
):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    request = (await ask(admin_client, oats["id"], "https://brand.example.test/oats")).json()
    r = (await post_answer(helper, answer(request["id"]))).json()
    assert r["outcome"] == "update_opened"
    update = (await admin_client.get(f"/api/v1/product-proposals/{r['proposal_id']}")).json()
    assert update["listing"] is None and update["price"] is None


# "Look this up online" from a product's own page (found by /devex-review on 2026-10-05):
# with no page, the product's barcode goes to the helper.
async def test_a_product_with_a_barcode_is_looked_up_by_it(admin_client, helper):
    oats = await make_product(
        admin_client, "Oats", "Rolled oats", barcode="0 12345 67890 5".replace(" ", "")
    )
    r = await admin_client.post(f"/api/v1/products/{oats['id']}/look-up", json={})
    assert r.status_code == 200, r.text
    request = r.json()
    assert request["kind"] == "gtin" and request["value"].endswith("012345678905")
    again = await admin_client.post(f"/api/v1/products/{oats['id']}/look-up", json={})
    assert again.json()["id"] == request["id"]
    r = await post_answer(helper, answer(request["id"], price=None, title="Rolled Oats 1kg"))
    assert r.json()["outcome"] == "update_opened", r.text


async def test_without_a_barcode_a_page_is_needed(admin_client, helper):
    oats = await make_product(admin_client, "Oats", "Rolled oats")
    r = await admin_client.post(f"/api/v1/products/{oats['id']}/look-up", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_look_up"
