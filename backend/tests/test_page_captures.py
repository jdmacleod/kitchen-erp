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

from app.catalog.extract import extract, from_address, placeholder_title, site_names
from app.catalog.proposals import merge, value
from app.core.config import get_settings
from app.services import plugins
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests import test_products_helper as tph
from tests.pricebook_helpers import make_location
from tests.test_captures import work

no_network = gh.no_network
recorded = ih.recorded
helper = tph.helper  # the stub helper: products:read and products:suggest tokens

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


# Regression: ISSUE-001 — a route segment ("product-details") was taken for the name
# Found by /qa on 2026-10-05
@pytest.mark.parametrize(
    "path",
    ["/product-details/40112/12/77881", "/item-detail/40112", "/shop/view-item/40112"],
)
def test_a_route_segment_is_not_a_name(path):
    fields = merge(from_address(f"{SHOP}{path}").candidates)
    assert "title" not in fields
    assert value(fields, "item_number")


# Regression: ISSUE-002 — a page titled only "Product Detail" offered it as a name
# Found by /qa on 2026-10-05
@pytest.mark.parametrize("title", ["Product Detail", "Product details", "Item"])
def test_a_generic_page_title_is_not_a_name(title):
    found = extract(f"{SHOP}/product-details/40112", meta={"og:title": title})
    assert "title" not in merge(found.candidates)


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


async def test_a_half_emoji_in_the_page_is_kept_as_a_replacement_mark(
    admin_client, store, owner_conn: asyncpg.Connection
):
    """A storefront that cuts a review short can split an emoji, leaving half of it.
    That half cannot be stored as UTF-8; it must not cost the whole clip."""
    half = "\ud83d"
    body = page(
        dom_text=f"Five stars {half}..",
        title=f"Rolled Oats 500g {half}",
        meta={**META, "description": f"Tasty {half}"},
    )
    # As a browser sends it: JSON.stringify writes the half as the escape \ud83d.
    r = await admin_client.post(
        "/api/v1/product-captures",
        content=json.dumps(body).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 201, r.text
    payload = await owner_conn.fetchval(
        "SELECT payload::text FROM product_capture WHERE id = $1",
        uuid.UUID(r.json()["capture"]["id"]),
    )
    assert "\ufffd" in payload and "\\ud83d" not in payload


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
        "known": None,
    }
    pasted = await admin_client.post(
        "/api/v1/product-captures", json={"channel": "paste_url", "page_url": PAGE}
    )
    assert pasted.status_code == 201 and pasted.json()["capture"]["channel"] == "paste_url"


async def test_the_address_check_names_a_product_already_in_the_catalog(
    admin_client, store, no_network
):
    """Criterion 105: a known page, or the same item number on another address, names
    the product; an unknown page names none."""
    pasted = await admin_client.post(
        "/api/v1/product-captures", json={"channel": "paste_url", "page_url": PAGE}
    )
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    accepted = await admin_client.post(
        f"/api/v1/product-proposals/{pasted.json()['id']}/accept",
        json={"action": "new", "ingredient_id": ing["id"]},
    )
    assert accepted.status_code == 200, accepted.text
    product_id = accepted.json()["product_id"]

    async def known(url: str):
        r = await admin_client.post("/api/v1/product-captures/address", json={"page_url": url})
        assert r.status_code == 200, r.text
        return r.json()["known"]

    assert await known(PAGE) == {
        "product_id": product_id,
        "name": "Rolled oats 500g",
        "reason": "listing",
    }
    same_item = await known(f"{SHOP}/p/oats-family-size-77123")
    assert same_item is not None and same_item["reason"] == "identifier"
    assert await known(f"{SHOP}/p/barley-flakes-88001") is None
    assert await known("https://other.example.test/p/rolled-oats-77123") is None


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


IMAGE_ADAPTER = f"""
def read(page):
    return [
        {{"field": "price", "value": "2.99"}},
        {{"field": "image", "value": "{SHOP}/media/oats-front.jpg"}},
        {{"field": "image", "value": "{SHOP}/media/oats-front.jpg"}},
        {{"field": "image", "value": "http://{SHOP[8:]}/media/oats-plain.jpg"}},
        {{"field": "image", "value": "/media/oats-relative.jpg"}},
        {{"field": "image", "value": "javascript:alert(1)"}},
        {{"field": "image", "value": 7}},
    ] + [
        {{"field": "image", "value": "{SHOP}/media/oats-%d.jpg" % n}} for n in range(5)
    ]
"""


@pytest.fixture
def image_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "plugins"
    folder.mkdir()
    name = f"kerp_test_images_{uuid.uuid4().hex[:8]}"
    (folder / f"{name}.py").write_text(IMAGE_ADAPTER)
    monkeypatch.setattr(get_settings(), "plugins_path", str(folder))
    monkeypatch.setattr(get_settings(), "product_adapters", [f"{name}:read"])
    plugins._cache.clear()
    yield folder
    plugins._cache.clear()


def test_an_adapter_image_is_an_https_address_and_never_a_candidate(image_plugin):
    adapted = plugins.run(page())
    assert [c.field for c in adapted.candidates] == ["price"]
    assert adapted.images == [
        f"{SHOP}/media/oats-front.jpg",
        f"{SHOP}/media/oats-0.jpg",
        f"{SHOP}/media/oats-1.jpg",
        f"{SHOP}/media/oats-2.jpg",
    ]
    [record] = adapted.records
    assert record["fields"] == ["price"] and record["images"] == 7


async def test_adapter_images_go_to_the_helper_to_fetch(
    admin_client, store, image_plugin, recorded, no_network, helper
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    await admin_client.post("/api/v1/product-captures", json=page())
    await work()
    queue = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()
    images = [r["value"] for r in queue["items"] if r["kind"] == "image"]
    assert images == [
        f"{SHOP}/media/oats-front.jpg",
        f"{SHOP}/media/oats-0.jpg",
        f"{SHOP}/media/oats-1.jpg",
        f"{SHOP}/media/oats-2.jpg",
    ]
    assert {r["kind"] for r in queue["items"]} == {"image"}


async def test_adapter_images_are_kept_back_without_a_helper(
    admin_client, store, image_plugin, recorded, no_network, owner_conn: asyncpg.Connection
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    proposal = (await admin_client.post("/api/v1/product-captures", json=page())).json()
    await work()
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()
    assert body["fields"]["price"]["value"] == "2.99"
    queued = await owner_conn.fetchval(
        "SELECT count(*) FROM lookup_request WHERE proposal_id = $1", uuid.UUID(proposal["id"])
    )
    assert queued == 0


def test_reading_a_pack_size_stays_fast_on_hostile_text():
    """CodeQL py/polynomial-redos: a long run of digits must not make it backtrack."""
    import time

    from app.catalog.extract import pack_from_text

    started = time.monotonic()
    assert pack_from_text("0" * 100_000) is None
    assert pack_from_text("1" + "0" * 50_000 + " g") is None
    assert time.monotonic() - started < 1
    assert pack_from_text("Oats 500 g") == {"qty": "500", "unit": "g"}
    assert pack_from_text("Rolled oats 1.5kg") == {"qty": "1.5", "unit": "kg"}


# --- placeholder titles, a barcode in the address, the listing after the job (#219) -----


def _gtin13(body: str) -> str:
    """A GTIN-13 with its check digit, from an invented 12-digit body (spaces allowed)."""
    digits = body.replace(" ", "")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits))
    return digits + str((10 - total % 10) % 10)


CODE = _gtin13("2 415803 30718")  # invented
ROUTED = f"{SHOP}/product-details/40112/12/{CODE}"


def placeholder_page(**extra) -> dict:
    """A storefront built from page data: no JSON-LD, and a tab title naming the page's kind."""
    return {
        "page_url": ROUTED,
        "title": "Product Detail",
        "meta": {"og:title": "Product Detail", "og:site_name": "JuniperMarket"},
        "structured_data": [],
        "dom_text": "Lantern Bay Rolled Oats 500 g $4.29 Barcode " + CODE,
        **extra,
    }


@pytest.mark.parametrize("title", ["Product Detail", "Item details", "Juniper Market"])
def test_a_tab_title_naming_the_page_or_the_site_is_no_title(title):
    names = site_names(ROUTED, {}, "Juniper Market")
    assert placeholder_title(title, names)
    assert not placeholder_title("Rolled Oats | Juniper Market", names)


def test_a_barcode_in_the_address_is_a_gtin_not_the_stores_number():
    fields = merge(from_address(ROUTED).candidates)
    assert value(fields, "gtin") == "0" + CODE and fields["gtin"]["source"] == "address"
    assert "item_number" not in fields


def test_a_number_that_fails_its_check_digit_stays_the_stores_number():
    bad = CODE[:-1] + str((int(CODE[-1]) + 1) % 10)
    fields = merge(from_address(f"{SHOP}/product-details/{bad}").candidates)
    assert value(fields, "item_number") == bad and "gtin" not in fields


async def test_a_clip_titled_only_product_detail_has_no_title_and_a_gtin(admin_client, store):
    r = await admin_client.post("/api/v1/product-captures", json=placeholder_page())
    assert r.status_code == 201, r.text
    body = r.json()
    assert "title" not in body["fields"]
    assert body["fields"]["gtin"]["value"] == "0" + CODE
    assert "item_number" not in body["fields"]
    assert body["listing"]["title"] == body["listing"]["canonical_url"]
    assert body["listing"]["vendor_sku"] is None


NEXT_DATA_ADAPTER = """
def read(page):
    return [
        {"field": "title", "value": "Lantern Bay Rolled Oats"},
        {"field": "item_number", "value": "40112"},
        {"field": "price", "value": "4.29"},
    ]
"""


@pytest.fixture
def page_data_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "plugins"
    folder.mkdir()
    name = f"kerp_test_pagedata_{uuid.uuid4().hex[:8]}"
    (folder / f"{name}.py").write_text(NEXT_DATA_ADAPTER)
    monkeypatch.setattr(get_settings(), "plugins_path", str(folder))
    monkeypatch.setattr(get_settings(), "product_adapters", [f"{name}:read"])
    plugins._cache.clear()
    yield folder
    plugins._cache.clear()


async def test_the_listing_follows_the_adapters_name_and_number_after_the_job(
    admin_client, store, page_data_adapter, recorded, no_network, owner_conn: asyncpg.Connection
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    proposal = (await admin_client.post("/api/v1/product-captures", json=placeholder_page())).json()
    await work()
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()
    assert body["fields"]["title"]["value"] == "Lantern Bay Rolled Oats"
    assert body["listing"]["title"] == "Lantern Bay Rolled Oats"
    assert body["listing"]["vendor_sku"] == "40112"
    assert body["price"]["amount"] == "4.29"

    # A reviewer's name wins on the stored listing too, not the one read at capture.
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "edits": {"title": "Rolled oats, large"},
        },
    )
    assert r.status_code == 200, r.text
    row = await owner_conn.fetchrow(
        "SELECT title, vendor_sku FROM vendor_listing WHERE id = $1",
        uuid.UUID(r.json()["result"]["listing_id"]),
    )
    assert (row["title"], row["vendor_sku"]) == ("Rolled oats, large", "40112")


# --- a page whose price the clip never saw: the helper reads the public page -------------


async def page_requests(helper) -> list[dict]:
    r = await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)
    return [i for i in r.json()["items"] if i["kind"] == "page"]


async def test_a_clip_without_a_price_asks_the_helper_for_its_public_page_once(
    admin_client, store, helper, recorded, no_network
):
    """The store keeps its price in page data the clip never sends: after the extract job
    the helper is asked for the canonical page, once, and its price joins the proposal."""
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    proposal = (await admin_client.post("/api/v1/product-captures", json=placeholder_page())).json()
    await work()
    asked = await page_requests(helper)
    assert [a["value"] for a in asked] == [proposal["listing"]["canonical_url"]]
    assert (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()[
        "price"
    ] is None

    body = json.dumps(
        {
            "format": tph.FORMAT,
            "request_id": asked[0]["id"],
            "candidates": [
                {"field": "title", "value": "Lantern Bay Rolled Oats", "source": "adapter"},
                {"field": "item_number", "value": "40112", "source": "adapter"},
                {"field": "price", "value": "4.29", "source": "adapter"},
            ],
        }
    )
    r = await tph.post_answer(helper, body)
    assert r.status_code == 200 and r.json()["outcome"] == "merged", r.text
    shown = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()
    assert shown["price"]["amount"] == "4.29"
    assert shown["fields"]["price"]["via"] == "helper"
    assert shown["listing"]["vendor_sku"] == "40112"
    assert shown["status"] == "pending"  # a person still decides (non-negotiable 8)
    # Answered: never asked again for this proposal.
    await work()
    assert await page_requests(helper) == []


async def test_a_clip_without_a_price_asks_nothing_without_a_helper(
    admin_client, store, recorded, no_network, owner_conn: asyncpg.Connection
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    await admin_client.post("/api/v1/product-captures", json=placeholder_page())
    await work()
    assert await owner_conn.fetchval("SELECT count(*) FROM lookup_request") == 0


async def test_a_clip_whose_page_gave_a_price_asks_for_no_page(
    admin_client, store, helper, page_data_adapter, recorded, no_network
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    await admin_client.post("/api/v1/product-captures", json=placeholder_page())
    await work()
    assert await page_requests(helper) == []


async def test_a_page_saved_without_a_store_is_never_sent_to_the_helper(
    admin_client, store, helper, recorded, no_network
):
    recorded({"ProductReading": {"name": "Rolled oats", "confidence": 0.3}})
    await admin_client.post("/api/v1/product-captures", json=placeholder_page(without_store=True))
    await work()
    assert await page_requests(helper) == []
