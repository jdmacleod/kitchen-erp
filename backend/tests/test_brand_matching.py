"""Store brands in matching (spec 16, 2R-2: criteria R7-R9). Every family, brand,
store and product here is invented."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from app.catalog import proposals as merging
from app.catalog import sameness
from app.core.db import get_sessionmaker
from app.models import Product, ProductProposal
from app.services import brand_import, brands, lookups, proposals
from tests.catalog_helpers import make_ingredient, make_product

SRC = [{"url": "https://example.com/brands", "checked": "2026-10-09"}]


def family(key: str, name: str, brand: str, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "kind": "retailer",
        "sources": SRC,
        "banners": [{"key": f"{key}/main", "name": name, "sources": SRC}],
        "brands": [{"key": f"{key}/own", "name": brand, "tier": "standard"}],
        **extra,
    }


BUNDLE = {
    "format": "kitchen-erp-brands/1",
    "license": "ODbL-1.0",
    "families": [
        family(
            "larkspur", "Larkspur Markets", "Larkspur Select", carries=["meadowline/hearthside"]
        ),
        family("brightwater", "Brightwater Grocers", "Brightwater Basics"),
        {
            "key": "meadowline",
            "name": "Meadowline Cooperative",
            "kind": "cooperative",
            "brands": [{"key": "meadowline/hearthside", "name": "Hearthside", "tier": "value"}],
        },
    ],
}


async def setup(client) -> dict[str, Any]:
    async with get_sessionmaker()() as db:
        await brand_import.run(db, json.dumps(BUNDLE).encode(), fmt="json")
    r = await client.post("/api/v1/vendors", json={"name": "Larkspur Markets", "kind": "chain"})
    vendor = r.json()
    assert vendor["brand_family"]["key"] == "larkspur"
    ing = await make_ingredient(client, "rolled oats")
    made = {
        "own": await make_product(client, ing["id"], "Rolled oats", brand="Larkspur Select"),
        "other": await make_product(client, ing["id"], "Rolled oats", brand="Brightwater Basics"),
        "label": await make_product(client, ing["id"], "Rolled oats", brand="Hearthside"),
        "national": await make_product(client, ing["id"], "Rolled oats", brand="Juniper Mills"),
    }
    return {"vendor": vendor, **made}


async def test_another_familys_store_brand_is_flagged_and_ranked_lower(admin_client):  # R7
    s = await setup(admin_client)
    ids = [uuid.UUID(s[k]["id"]) for k in ("own", "other", "label", "national")]
    async with get_sessionmaker()() as db:
        flagged = await brands.out_of_family(db, ids, uuid.UUID(s["vendor"]["id"]))
    assert set(flagged) == {uuid.UUID(s["other"]["id"])}
    assert flagged[uuid.UUID(s["other"]["id"])].note() == (
        "Brightwater Basics is Brightwater Grocers' own brand"
    )
    entries = [
        {"id": s["other"]["id"], "score": "0.90"},
        {"id": s["national"]["id"], "score": "0.80"},
        {"id": s["label"]["id"], "score": "0.70"},
    ]
    ranked = brands.rank_with_family(entries, flagged, id_key="id")
    assert [e["id"] for e in ranked] == [s["national"]["id"], s["other"]["id"], s["label"]["id"]]
    assert ranked[1]["brand_note"] and "brand_note" not in ranked[0]


async def test_a_clearly_better_out_of_family_match_still_leads():
    pid = uuid.uuid4()
    flagged = {pid: brands.OutOfFamily("Brightwater Basics", "Brightwater Grocers")}
    entries = [{"id": str(pid), "score": "0.95"}, {"id": str(uuid.uuid4()), "score": "0.60"}]
    assert brands.rank_with_family(entries, flagged, id_key="id")[0]["id"] == str(pid)


async def test_a_vendor_with_no_family_flags_nothing(admin_client):
    s = await setup(admin_client)
    r = await admin_client.post("/api/v1/vendors", json={"name": "Corner Shop", "kind": "chain"})
    async with get_sessionmaker()() as db:
        flagged = await brands.out_of_family(
            db, [uuid.UUID(s["other"]["id"])], uuid.UUID(r.json()["id"])
        )
    assert flagged == {}


async def test_review_candidates_carry_the_note(admin_client):  # R7
    s = await setup(admin_client)
    stored = [
        {"product_id": s[k]["id"], "name": "Rolled oats", "brand": None, "score": "0.80"}
        for k in ("other", "national")
    ]

    def field(value: str) -> dict:
        return {"value": value, "source": "page_data", "alternatives": []}

    async with get_sessionmaker()() as db:
        proposal = ProductProposal(
            fields={"title": field("Rolled oats")},
            match={"strong": None, "candidates": stored, "preselect": None},
            listing={
                "vendor_id": s["vendor"]["id"],
                "canonical_url": "https://shop.example.test/p/1",
            },
        )
        db.add(proposal)
        await db.commit()
        got = await proposals.candidates_of(db, proposal)
    assert [str(c["product_id"]) for c in got] == [s["national"]["id"], s["other"]["id"]]
    assert got[1]["brand_note"] == "Brightwater Basics is Brightwater Grocers' own brand"


def test_one_brand_row_is_one_brand_whatever_the_spelling():  # R8
    one, other = uuid.uuid4(), uuid.uuid4()
    a = sameness.Facts(name="Rolled oats", brand="Larkspur Select", brand_id=one)
    b = sameness.Facts(name="Rolled oats", brand="LARKSPUR SEL", brand_id=one)
    assert sameness.compare(a, b).kind == "same"
    c = sameness.Facts(name="Rolled oats", brand="Larkspur Farms", brand_id=other)
    assert sameness.compare(a, c).kind == "similar"
    # Without links, text decides as before.
    assert (
        sameness.compare(
            sameness.Facts("Rolled oats", "Larkspur Select"),
            sameness.Facts("Rolled oats", "Larkspur Sel"),
        ).kind
        == "similar"
    )


async def test_a_respelled_brand_opens_no_update(admin_client):  # R9
    s = await setup(admin_client)
    async with get_sessionmaker()() as db:
        product = await db.get(Product, uuid.UUID(s["own"]["id"]))
        assert product.brand_id is not None
        same = merging.Candidate("brand", "LARKSPUR SELECT", "page_data")
        other = merging.Candidate("brand", "Brightwater Basics", "page_data")
        ids = {
            "LARKSPUR SELECT": product.brand_id,
            "Brightwater Basics": uuid.uuid4(),
        }
        assert not lookups._changes_product(product, [same], ids)
        assert lookups._changes_product(product, [other], ids)
        assert lookups._changes_product(product, [same])  # no links: text decides
    assert Decimal(brands.OUT_OF_FAMILY_PENALTY) > 0
