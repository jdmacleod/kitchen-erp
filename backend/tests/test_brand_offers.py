"""Store brands on offers and compare, and barcode prefixes (spec 16, 2R-3: criterion
R10). Every family, brand, store, prefix and price here is invented."""

from __future__ import annotations

import json
from typing import Any

from app.catalog.identifiers import check_digit
from app.core.db import get_sessionmaker
from app.models import ProductProposal
from app.services import brand_import, brands
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf

SRC = [{"url": "https://example.com/brands", "checked": "2026-10-09"}]


def bundle(**extra: Any) -> bytes:
    return json.dumps(
        {
            "format": "kitchen-erp-brands/1",
            "license": "ODbL-1.0",
            "families": [
                {
                    "key": "larkspur",
                    "name": "Larkspur Markets",
                    "kind": "retailer",
                    "sources": SRC,
                    "banners": [{"key": "larkspur/main", "name": "Larkspur Markets"}],
                    "brands": [
                        {"key": "larkspur/select", "name": "Larkspur Select", "tier": "standard"}
                    ],
                    "gs1_prefixes": [{"prefix": "0999001", "also": ["larkspur-club"]}],
                },
                {
                    "key": "larkspur-club",
                    "name": "Larkspur Club",
                    "kind": "retailer",
                    "banners": [{"key": "larkspur-club/club", "name": "Larkspur Club"}],
                    "brands": [
                        {
                            "key": "larkspur-club/members",
                            "name": "Larkspur Members",
                            "tier": "value",
                        }
                    ],
                },
            ],
            **extra,
        }
    ).encode()


def gtin14(body13: str) -> str:
    """An invented GTIN-14 from 13 digits, with its computed check digit."""
    return body13 + str(check_digit(body13))


async def run(raw: bytes | None = None):
    async with get_sessionmaker()() as db:
        return await brand_import.run(db, raw or bundle(), fmt="json")


async def offers_fixture(client) -> dict[str, Any]:
    await run()
    loc = await make_location(client, "Corner Grocer", "Elm", coords=SYNTH[0])
    own = await make_product(
        client, "Rolled oats", "Oats", brand="Larkspur Select", pack_qty="500", pack_unit="g"
    )
    national = (
        await client.post(
            "/api/v1/products",
            json={
                "ingredient_id": own["ingredient"]["id"],
                "name": "Oats",
                "brand": "Juniper Mills",
                "pack_qty": "500",
                "pack_unit": "g",
            },
        )
    ).json()
    await shelf(client, own["id"], loc["id"], "2.00")
    await shelf(client, national["id"], loc["id"], "3.00")
    return {"own": own, "national": national, "iid": own["ingredient"]["id"]}


async def test_offers_show_tier_and_family_and_filter_by_brand(admin_client):  # R10
    f = await offers_fixture(admin_client)
    url = f"/api/v1/ingredients/{f['iid']}/offers"
    items = (await admin_client.get(url)).json()["items"]
    own = next(o for o in items if o["product_id"] == f["own"]["id"])
    national = next(o for o in items if o["product_id"] == f["national"]["id"])
    assert (own["is_store_brand"], own["brand_tier"], own["brand_family_name"]) == (
        True,
        "standard",
        "Larkspur Markets",
    )
    assert (national["is_store_brand"], national["brand_tier"]) == (False, None)
    store = (await admin_client.get(url, params={"brand_scope": "store"})).json()["items"]
    assert [o["product_id"] for o in store] == [f["own"]["id"]]
    nat = (await admin_client.get(url, params={"brand_scope": "national"})).json()["items"]
    assert [o["product_id"] for o in nat] == [f["national"]["id"]]
    bad = await admin_client.get(url, params={"brand_scope": "premium"})
    assert bad.status_code == 422


async def test_compare_cells_carry_the_brand_and_filter(admin_client):  # R10
    f = await offers_fixture(admin_client)
    r = await admin_client.post(
        "/api/v1/price-book/compare", json={"ingredient_ids": [f["iid"]], "brand_scope": "national"}
    )
    cells = list(r.json()["rows"][0]["cells"].values())
    assert [c["product_id"] for c in cells] == [f["national"]["id"]]
    r = await admin_client.post("/api/v1/price-book/compare", json={"ingredient_ids": [f["iid"]]})
    cell = next(iter(r.json()["rows"][0]["cells"].values()))
    assert cell["is_store_brand"] is True and cell["brand_tier"] == "standard"


async def test_prefixes_are_replaced_on_each_import_and_name_shared_families():
    await run()
    code = gtin14("0" + "0999001" + "12345")
    async with get_sessionmaker()() as db:
        found = await brands.families_by_barcode(db, code)
        assert [f.key for f in found] == ["larkspur-club", "larkspur"]  # by name
        assert await brands.families_by_barcode(db, gtin14("0" + "0888001" + "12345")) == []
    later = json.loads(bundle())
    later["families"][0]["gs1_prefixes"] = []
    await run(json.dumps(later).encode())
    async with get_sessionmaker()() as db:
        assert await brands.families_by_barcode(db, code) == []


async def test_a_proposal_with_a_store_prefix_offers_its_families(admin_client):
    await run()

    def field(value: str) -> dict:
        return {"value": value, "source": "page_data", "alternatives": []}

    code = gtin14("0" + "0999001" + "12345")
    async with get_sessionmaker()() as db:
        bare = ProductProposal(fields={"title": field("Oats"), "gtin": field(code)})
        named = ProductProposal(
            fields={
                "title": field("Oats"),
                "gtin": field(gtin14("0" + "0999001" + "54321")),
                "brand": field("Larkspur Select"),
            }
        )
        db.add_all([bare, named])
        await db.commit()
    got = (await admin_client.get(f"/api/v1/product-proposals/{bare.id}")).json()
    assert [f["key"] for f in got["barcode_families"]] == ["larkspur-club", "larkspur"]
    got = (await admin_client.get(f"/api/v1/product-proposals/{named.id}")).json()
    assert got["house_brand"]["key"] == "larkspur/select" and got["barcode_families"] == []
