"""Captured vendor pages (04, 2M): criteria 84, 86, 87 and 88.

The storefronts, their pages and the adapter are invented for these tests.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from app.catalog.extract import extract, from_address
from app.catalog.proposals import merge, value
from app.core.config import get_settings
from app.services import plugins
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.pricebook_helpers import make_location
from tests.test_captures import work

no_network = gh.no_network
recorded = ih.recorded

SHOP = "https://www.juniper-market.example.test"
PAGE = f"{SHOP}/shop/store/12/p/rolled-oats-500g-77123"
JSON_LD = json.dumps(
    {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "BreadcrumbList", "itemListElement": []},
            {
                "@type": "Product",
                "name": "Rolled Oats 500g",
                "brand": {"@type": "Brand", "name": "Hollow Creek"},
                "sku": "77123",
                "offers": {"@type": "Offer", "price": "PRICE", "priceCurrency": "USD"},
                "image": [f"{SHOP}/img/oats.jpg"],
            },
        ],
    }
)
META = {
    "og:title": "Rolled Oats | Juniper Market",
    "og:image": f"{SHOP}/img/oats-og.jpg",
    "product:price:amount": "3.49",
}


def page(price: str = "3.49", **extra) -> dict:
    # The price goes in as a JSON number, as storefronts write it.
    return {
        "page_url": PAGE,
        "canonical_url": f"{SHOP}/p/rolled-oats-500g-77123",
        "title": "Rolled Oats 500g – Juniper Market",
        "meta": META,
        "structured_data": [JSON_LD.replace('"PRICE"', price)],
        "dom_text": "Rolled Oats 500g. Hollow Creek. Whole grain rolled oats.",
        **extra,
    }


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def store(admin_client) -> dict:
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market", price_scope="chain")
    r = await admin_client.patch(f"/api/v1/vendors/{loc['vendor']['id']}", json={"website": SHOP})
    assert r.status_code == 200, r.text
    return loc


# --- the ladder (pure) -------------------------------------------------------------------


def test_structured_data_and_meta_fill_title_price_size_and_item_number():
    """Criterion 87, first part."""
    fields = merge(
        extract(PAGE, meta=META, structured_data=[JSON_LD.replace('"PRICE"', "3.49")]).candidates
    )
    assert value(fields, "title") == "Rolled Oats 500g"
    assert fields["title"]["source"] == "page_data"
    assert value(fields, "brand") == "Hollow Creek"
    assert value(fields, "price") == "3.49"
    assert value(fields, "pack") == {"qty": "500", "unit": "g"}
    assert value(fields, "item_number") == "77123"


def test_a_meta_only_page_gives_title_and_photo_from_meta_and_item_number_from_the_address():
    """Criterion 87, second part."""
    found = extract(
        f"{SHOP}/p/oat-drink-1l?sku=55210",
        meta={"og:title": "Oat Drink 1 L", "og:image": f"{SHOP}/i.jpg"},
    )
    fields = merge(found.candidates)
    assert value(fields, "title") == "Oat Drink 1 L" and fields["title"]["source"] == "page_meta"
    assert found.images == [f"{SHOP}/i.jpg"]
    assert value(fields, "item_number") == "55210" and fields["item_number"]["source"] == "address"


def test_a_block_that_is_not_json_is_skipped():
    found = extract(PAGE, structured_data=["<script>alert(1)</script>", "{not json"])
    assert {c.source for c in found.candidates} == {"address"}


@pytest.mark.parametrize("price", ["0.10", "19.99"])
def test_prices_stay_exact_decimals(price):
    """Criterion 86, first part: no float between the page and the proposal."""
    fields = merge(extract(PAGE, structured_data=[JSON_LD.replace('"PRICE"', price)]).candidates)
    assert value(fields, "price") == price


def test_the_address_alone_gives_a_title_and_item_number():
    fields = merge(from_address(PAGE).candidates)
    assert value(fields, "item_number") == "77123"
    assert value(fields, "title") == "Rolled oats 500g"


# --- the endpoint ------------------------------------------------------------------------


async def test_a_clip_becomes_a_proposal_for_the_matched_vendor(admin_client, store, no_network):
    r = await admin_client.post("/api/v1/product-captures", json=page())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["capture"]["channel"] == "clip"
    assert body["vendor"]["id"] == store["vendor"]["id"]
    assert body["listing"]["canonical_url"] == f"{SHOP}/p/rolled-oats-500g-77123"
    assert body["listing"]["store_ref"] == "store/12"
    assert body["price"]["amount"] == "3.49"
    again = await admin_client.post("/api/v1/product-captures", json=page())
    assert again.status_code == 200 and again.json()["id"] == body["id"]


@pytest.mark.parametrize("price", ["0.10", "19.99"])
async def test_a_structured_price_reaches_the_observation_exactly(
    admin_client, store, owner_conn: asyncpg.Connection, price
):
    """Criterion 86, second part."""
    proposal = (await admin_client.post("/api/v1/product-captures", json=page(price))).json()
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "record_price": True,
            "vendor_location_id": store["id"],
        },
    )
    assert r.status_code == 200, r.text
    stored = await owner_conn.fetchval(
        "SELECT price FROM price_observation WHERE id = $1",
        uuid.UUID(r.json()["result"]["observation_id"]),
    )
    assert stored == Decimal(price)


async def test_page_text_over_200_kb_is_refused_naming_the_field(admin_client, store):
    """Criterion 84, first part."""
    r = await admin_client.post(
        "/api/v1/product-captures", json=page(dom_text="x" * (200 * 1024 + 1))
    )
    assert r.status_code == 413
    assert r.json()["error"]["details"] == {"field": "dom_text"}


async def test_a_retry_with_the_same_idempotency_key_returns_the_original(admin_client, store):
    """Criterion 84, second part."""
    headers = {"Idempotency-Key": "clip-0001"}
    first = await admin_client.post("/api/v1/product-captures", json=page(), headers=headers)
    second = await admin_client.post("/api/v1/product-captures", json=page(), headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_a_page_from_no_known_vendor_keeps_details_but_no_listing_or_price(admin_client):
    r = await admin_client.post(
        "/api/v1/product-captures", json={**page(), "page_url": "https://other.example.test/p/x-9"}
    )
    body = r.json()
    assert body["vendor"] is None and body["listing"] is None and body["price"] is None
    assert body["fields"]["title"]["value"] == "Rolled Oats 500g"


async def test_pasting_an_address_prefills_without_any_request(admin_client, store, no_network):
    """Criterion 88."""
    r = await admin_client.post("/api/v1/product-captures/address", json={"page_url": PAGE})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "vendor": {"id": store["vendor"]["id"], "name": "Juniper Market"},
        "canonical_url": f"{SHOP}/p/rolled-oats-500g-77123",
        "title": "Rolled oats 500g",
        "item_number": "77123",
    }
    pasted = await admin_client.post(
        "/api/v1/product-captures", json={"channel": "paste_url", "page_url": PAGE}
    )
    assert pasted.status_code == 201 and pasted.json()["capture"]["channel"] == "paste_url"


# --- adapters and the model -----------------------------------------------------------------


ADAPTER = """
def read(page):
    assert "dom_text" in page
    return [
        {"field": "price", "value": "2.99"},
        {"field": "item_code", "value": "A-7"},
        {"field": "colour", "value": "ignored: not a field"},
    ]


def broken(page):
    raise RuntimeError("the retailer changed its page")
"""


@pytest.fixture
def plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "plugins"
    folder.mkdir()
    name = f"kerp_test_shop_{uuid.uuid4().hex[:8]}"
    (folder / f"{name}.py").write_text(ADAPTER)
    monkeypatch.setattr(get_settings(), "plugins_path", str(folder))
    monkeypatch.setattr(
        get_settings(),
        "product_adapters",
        [f"{name}:read", f"{name}:broken", "kerp_not_installed:read"],
    )
    plugins._cache.clear()
    yield folder
    plugins._cache.clear()


async def test_an_installed_adapter_adds_its_fields_and_a_bad_one_is_skipped(
    admin_client, store, plugin_dir, recorded, no_network
):
    """Criterion 87, last part."""
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    proposal = (await admin_client.post("/api/v1/product-captures", json=page())).json()
    await work()
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()
    fields = body["fields"]
    assert fields["price"]["value"] == "2.99" and fields["price"]["source"] == "adapter"
    assert fields["item_code"]["value"] == "A-7"
    assert "colour" not in fields
    # Generic extraction carried on beside the broken and missing adapters.
    assert fields["brand"]["value"] == "Hollow Creek"
    assert body["price"]["amount"] == "2.99"
    assert body["reading"] == {"path": "page", "error": None}
    health = (await admin_client.get("/api/v1/health")).json()
    statuses = health["checks"]["product_adapters"]["detail"]["adapters"]
    assert sorted(statuses.values()) == ["loaded", "loaded", "missing"]
