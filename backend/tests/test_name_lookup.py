"""Search by name: a branded product with no barcode asks the lookup helper (#184).

What goes out is the product's public facts only; what comes back waits as a Product
update until a person accepts it. Every product, brand and code is invented.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.schemas.products_interchange import FORMAT
from tests import test_products_helper as tph
from tests.pricebook_helpers import make_product
from tests.test_captures import UNKNOWN, with_check

helper = tph.helper  # the stub helper: products:read and products:suggest tokens
post_answer = tph.post_answer

OTHER = with_check("5 0123 4500 028")  # a code another product holds


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


async def branded(admin_client, ingredient: str = "Rolled oats", **extra) -> dict:
    return await make_product(
        admin_client,
        ingredient,
        "Original Rolled Oats",
        brand="Larkfield",
        pack_qty="18",
        pack_unit="oz",
        **extra,
    )


async def ask(admin_client, product_id: str):
    return await admin_client.post(f"/api/v1/products/{product_id}/look-up", json={"by_name": True})


def answer(request_id: str, code: str, *, photo: bool = False) -> str:
    body: dict = {
        "format": FORMAT,
        "request_id": request_id,
        "candidates": [
            {"field": "title", "value": "Original Rolled Oats", "source": "manufacturer"},
            {"field": "brand", "value": "Larkfield", "source": "manufacturer"},
            {"field": "gtin", "value": code, "source": "manufacturer"},
            {"field": "pack", "value": {"qty": 18, "unit": "oz"}, "source": "manufacturer"},
        ],
    }
    if photo:
        out = io.BytesIO()
        Image.new("RGB", (200, 200), (180, 140, 60)).save(out, format="JPEG")
        body["photos"] = [
            {
                "data_base64": base64.b64encode(out.getvalue()).decode(),
                "source_kind": "open_food_facts",
                "attribution": "Open Food Facts contributors, CC BY-SA",
                "source_url": "https://facts.example.test/p/2",
            }
        ]
    return json.dumps(body)


async def test_search_by_name_needs_a_helper(admin_client):
    product = await branded(admin_client)
    r = await ask(admin_client, product["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_helper"


async def test_only_public_facts_go_out_and_one_request_stays_open(admin_client, helper):
    product = await branded(admin_client)
    r = await ask(admin_client, product["id"])
    assert r.status_code == 200, r.text
    asked = r.json()
    assert asked["kind"] == "name"
    assert json.loads(asked["value"]) == {
        "brand": "Larkfield",
        "name": "Original Rolled Oats",
        "pack_qty": "18",
        "pack_unit": "oz",
    }
    again = (await ask(admin_client, product["id"])).json()
    assert again["id"] == asked["id"]
    queue = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()
    assert [(i["id"], i["kind"]) for i in queue["items"]] == [(asked["id"], "name")]


async def test_without_a_brand_or_with_a_barcode_there_is_nothing_to_search(admin_client, helper):
    plain = await make_product(admin_client, "Oat groats", "Loose groats")
    r = await ask(admin_client, plain["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_look_up"
    coded = await branded(admin_client, "Porridge oats", barcode=UNKNOWN)
    r = await ask(admin_client, coded["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_barcode"


async def test_a_match_waits_as_an_update_and_accepting_sets_the_barcode_and_photo(
    admin_client, helper
):
    product = await branded(admin_client)
    asked = (await ask(admin_client, product["id"])).json()
    r = await post_answer(helper, answer(asked["id"], UNKNOWN, photo=True))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "update_opened"
    # Nothing is applied until a person accepts.
    still = (await admin_client.get(f"/api/v1/products/{product['id']}")).json()
    assert still["barcode"] is None
    update = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['proposal_id']}")).json()
    assert update["kind"] == "product_update" and update["product_id"] == product["id"]
    assert [p["source_kind"] for p in update["photos"]] == ["open_food_facts"]
    r = await admin_client.post(
        f"/api/v1/product-proposals/{update['id']}/accept", json={"action": "update"}
    )
    assert r.status_code == 200, r.text
    got = (await admin_client.get(f"/api/v1/products/{product['id']}")).json()
    assert got["barcode"] is not None and got["barcode"].replace(" ", "").endswith(UNKNOWN)
    assert got["name"] == "Original Rolled Oats"


async def test_a_match_whose_barcode_another_product_holds_is_a_conflict(admin_client, helper):
    await make_product(admin_client, "Oat bran", "Oat bran tin", barcode=OTHER)
    product = await branded(admin_client)
    asked = (await ask(admin_client, product["id"])).json()
    r = await post_answer(helper, answer(asked["id"], OTHER))
    assert r.json()["outcome"] == "update_opened"
    r = await admin_client.post(
        f"/api/v1/product-proposals/{r.json()['proposal_id']}/accept", json={"action": "update"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "identifier_taken"
    got = (await admin_client.get(f"/api/v1/products/{product['id']}")).json()
    assert got["barcode"] is None
