"""Store brands and brand families (spec 16, 2R-1: criteria R1-R6, R11).

Every family, brand, domain and Wikidata id here is invented.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.brand_names import leading_keys, name_key
from app.core.db import get_sessionmaker
from app.core.errors import ApiError
from app.services import brand_catchup, brand_import
from tests.catalog_helpers import make_ingredient, make_product

SRC = [{"url": "https://example.com/brands", "checked": "2026-10-09"}]


def larkspur(**extra: Any) -> dict[str, Any]:
    return {
        "key": "larkspur",
        "name": "Larkspur Markets",
        "kind": "retailer",
        "sources": SRC,
        "banners": [
            {
                "key": "larkspur/larkspur-fresh",
                "name": "Larkspur Fresh",
                "wikidata": "Q9000001",
                "domains": ["larkspurfresh.example"],
                "sources": SRC,
            },
            {"key": "larkspur/larkspur-corner", "name": "Larkspur Corner", "sources": SRC},
        ],
        "carries": ["meadowline/hearthside"],
        "brands": [
            {
                "key": "larkspur/select",
                "name": "Larkspur Select",
                "aliases": ["Larkspur Sel"],
                "tier": "standard",
                "sources": SRC,
            },
            {
                "key": "larkspur/farms",
                "name": "Larkspur Farms",
                "tier": "standard",
                "until": "2024",
                "replaced_by": "larkspur/select",
                "sources": SRC,
            },
        ],
        **extra,
    }


def meadowline() -> dict[str, Any]:
    return {
        "key": "meadowline",
        "name": "Meadowline Cooperative",
        "kind": "cooperative",
        "sources": SRC,
        "brands": [{"key": "meadowline/hearthside", "name": "Hearthside", "tier": "value"}],
    }


def bundle(*families: dict[str, Any]) -> bytes:
    doc = {
        "format": "kitchen-erp-brands/1",
        "license": "ODbL-1.0",
        "source": {"name": "kitchen-erp-brands", "generated_at": "2026-10-09T00:00:00Z"},
        "families": list(families) or [larkspur(), meadowline()],
    }
    return json.dumps(doc).encode()


async def run(raw: bytes | None = None, **kw: Any):
    async with get_sessionmaker()() as db:
        return await brand_import.run(db, raw or bundle(), fmt="json", **kw)


async def vendor(client: httpx.AsyncClient, name: str, **extra: Any) -> dict:
    r = await client.post("/api/v1/vendors", json={"name": name, "kind": "chain", **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --- the name key (pure) -------------------------------------------------------


@pytest.mark.parametrize("text", ["LARKSPUR SELECT", "Larkspur Select®", " larkspur  select "])
def test_spellings_share_a_key(text):
    assert name_key(text) == "larkspur select"


def test_leading_keys_are_longest_first_and_exclude_the_whole():
    assert leading_keys("a b c", 8) == ["a b", "a"]
    assert leading_keys("a b c", 1) == ["a"]


@given(st.text(max_size=60))
def test_the_key_is_stable(text):
    key = name_key(text)
    assert name_key(key) == key
    assert key == key.strip() and "  " not in key


@given(st.text(max_size=40))
def test_case_and_trademark_signs_never_matter(text):
    assert name_key(text.upper() + "®") == name_key(text.lower())


# --- import ------------------------------------------------------------------


async def test_import_twice_changes_nothing_the_second_time():  # R1
    first = await run()
    assert first.counts.families_created == 2 and first.counts.brands_created == 3
    second = await run()
    assert second.counts.families_created == second.counts.brands_created == 0
    assert second.counts.families_updated == second.counts.brands_updated == 0


async def test_a_dry_run_writes_nothing():
    report = await run(dry_run=True)
    assert report.counts.brands_created == 3
    again = await run(dry_run=True)
    assert again.counts.brands_created == 3


async def test_an_edited_field_is_kept(owner_conn):  # R1
    await run()
    await owner_conn.execute("UPDATE brand SET tier = 'premium' WHERE key = 'larkspur/select'")
    changed = larkspur()
    changed["brands"][0]["tier"] = "value"
    report = await run(bundle(changed, meadowline()))
    assert report.counts.kept >= 1
    tier = await owner_conn.fetchval("SELECT tier FROM brand WHERE key = 'larkspur/select'")
    assert tier == "premium"


async def test_a_missing_family_is_reported_not_deleted(owner_conn):  # R2
    await run()
    report = await run(bundle(larkspur(carries=[])))
    assert "meadowline" in report.missing and "meadowline/hearthside" in report.missing
    assert await owner_conn.fetchval("SELECT count(*) FROM brand_family") == 2


async def test_a_colliding_alias_is_refused_and_the_rest_imports(owner_conn):  # R3
    m = meadowline()
    m["brands"][0]["aliases"] = ["LARKSPUR SELECT"]
    report = await run(bundle(larkspur(), m))
    assert any("already names another brand" in r.reason for r in report.refused)
    assert await owner_conn.fetchval("SELECT count(*) FROM brand") == 3


async def test_retired_brands_point_at_their_replacement(owner_conn):
    await run()
    name = await owner_conn.fetchval(
        "SELECT r.name FROM brand b JOIN brand r ON r.id = b.replaced_by_id"
        " WHERE b.key = 'larkspur/farms'"
    )
    assert name == "Larkspur Select"


async def test_a_float_is_refused():  # R11
    raw = bundle().replace(b'"2026-10-09"', b"2026.10", 1)
    with pytest.raises(ApiError):
        await run(raw)


# --- vendors -----------------------------------------------------------------


async def test_vendors_link_by_wikidata_domain_then_name(admin_client):  # R4
    await run()
    by_id = await vendor(admin_client, "Corner Shop A", wikidata="Q9000001")
    by_site = await vendor(
        admin_client, "Corner Shop B", website="https://shop.larkspurfresh.example/"
    )
    by_name = await vendor(admin_client, "Larkspur Corner")
    other = await vendor(admin_client, "Unrelated Grocer")
    for v in (by_id, by_site, by_name):
        assert v["brand_family"]["key"] == "larkspur", v
    assert other["brand_family"] is None
    assert by_site["sources"]["brand_family_id"]["ref"] == "domain"


async def test_an_ambiguous_name_links_nothing_and_is_reported(admin_client):  # R4
    second = larkspur()
    second.update(key="brightwater", name="Brightwater Grocers", carries=[])
    second["banners"] = [{"key": "brightwater/corner", "name": "Larkspur Corner", "sources": SRC}]
    second["brands"] = [{"key": "brightwater/own", "name": "Brightwater", "tier": "value"}]
    await vendor(admin_client, "Larkspur Corner")
    report = await run(bundle(larkspur(), meadowline(), second))
    assert [v.vendor for v in report.vendors_needing_you] == ["Larkspur Corner"]


async def test_a_persons_choice_of_family_is_kept(admin_client):
    await run()
    v = await vendor(admin_client, "Larkspur Corner")
    r = await admin_client.patch(f"/api/v1/vendors/{v['id']}", json={"brand_family_id": None})
    assert r.status_code == 200 and r.json()["brand_family"] is None
    await run()
    r = await admin_client.get(f"/api/v1/vendors/{v['id']}")
    assert r.json()["brand_family"] is None


# --- products and kind -------------------------------------------------------


async def test_spellings_link_one_brand_and_a_title_links_nothing(admin_client):  # R5
    await run()
    ing = await make_ingredient(admin_client, "rolled oats")
    a = await make_product(admin_client, ing["id"], "Oats", brand="LARKSPUR SELECT")
    b = await make_product(admin_client, ing["id"], "Oats 2", brand="Larkspur Select®")
    c = await make_product(admin_client, ing["id"], "Larkspur Select Oats")
    assert a["house_brand"]["key"] == b["house_brand"]["key"] == "larkspur/select"
    assert a["house_brand"]["family"]["name"] == "Larkspur Markets"
    assert c["house_brand"] is None


async def test_a_retired_brand_shows_its_replacement(admin_client):
    await run()
    ing = await make_ingredient(admin_client, "rolled oats")
    p = await make_product(admin_client, ing["id"], "Oats", brand="Larkspur Farms")
    assert p["house_brand"]["current"] is False
    assert p["house_brand"]["replaced_by_name"] == "Larkspur Select"


async def test_a_house_brand_is_a_store_brand(admin_client):  # R6
    await run()
    ing = await make_ingredient(admin_client, "rolled oats")
    own = await make_product(admin_client, ing["id"], "Oats", brand="Hearthside")
    national = await make_product(admin_client, ing["id"], "Oats 2", brand="Juniper Mills")
    chosen = await make_product(
        admin_client, ing["id"], "Oats 3", brand="Larkspur Select", kind="branded"
    )
    assert own["kind"] == "private_label"
    assert national["kind"] == "branded"
    assert chosen["kind"] == "branded"  # a kind a person chose is kept


async def test_a_house_brand_beats_a_market_stall(admin_client):  # R6
    await run()
    stall = await vendor(admin_client, "Elm Street Stall")
    ing = await make_ingredient(admin_client, "rolled oats")
    p = await make_product(
        admin_client,
        ing["id"],
        "Oats",
        brand="Larkspur Select",
        exclusive_vendor_id=stall["id"],
    )
    assert p["kind"] == "private_label"


async def test_renaming_the_brand_relinks(admin_client):
    await run()
    ing = await make_ingredient(admin_client, "rolled oats")
    p = await make_product(admin_client, ing["id"], "Oats", brand="Juniper Mills")
    assert p["house_brand"] is None
    r = await admin_client.patch(f"/api/v1/products/{p['id']}", json={"brand": "Larkspur Sel"})
    assert r.json()["house_brand"]["key"] == "larkspur/select"


async def test_the_catch_up_links_and_changes_only_approved_kinds(admin_client):
    ing = await make_ingredient(admin_client, "rolled oats")
    a = await make_product(admin_client, ing["id"], "Oats", brand="Larkspur Select")
    b = await make_product(admin_client, ing["id"], "Oats 2", brand="Hearthside")
    assert a["kind"] == b["kind"] == "branded"  # saved before the dataset
    await run()
    async with get_sessionmaker()() as db:
        rows = await brand_catchup.plan(db)
    assert {r["id"] for r in rows} == {a["id"], b["id"]}
    async with get_sessionmaker()() as db:
        counts = await brand_catchup.apply(db, [r for r in rows if r["id"] == a["id"]])
    assert counts["changed"] == 1
    got_a = (await admin_client.get(f"/api/v1/products/{a['id']}")).json()
    got_b = (await admin_client.get(f"/api/v1/products/{b['id']}")).json()
    assert got_a["kind"] == "private_label" and got_b["kind"] == "branded"
    assert got_b["house_brand"]["key"] == "meadowline/hearthside"  # linked either way


# --- 2R-1b: the vendor picker and review ---------------------------------------


async def test_brand_families_are_listed_by_name(admin_client):
    await run()
    r = await admin_client.get("/api/v1/brand-families")
    assert r.status_code == 200
    assert [f["key"] for f in r.json()["items"]] == ["larkspur", "meadowline"]


async def test_a_person_sets_a_vendors_family(admin_client):
    await run()
    v = await vendor(admin_client, "Corner Shop")
    families = (await admin_client.get("/api/v1/brand-families")).json()["items"]
    larkspur_id = next(f["id"] for f in families if f["key"] == "larkspur")
    r = await admin_client.patch(
        f"/api/v1/vendors/{v['id']}", json={"brand_family_id": larkspur_id}
    )
    assert r.json()["brand_family"]["key"] == "larkspur"
    assert "brand_family_id" not in r.json()["sources"]  # a person's choice, not a source's
    missing = "00000000-0000-4000-8000-000000000000"
    r = await admin_client.patch(f"/api/v1/vendors/{v['id']}", json={"brand_family_id": missing})
    assert r.status_code == 404


async def test_a_proposal_names_its_house_brand(admin_client):
    from app.models import ProductProposal

    await run()

    def field(value: str) -> dict:
        return {"value": value, "source": "page_data", "alternatives": []}

    async with get_sessionmaker()() as db:
        own = ProductProposal(fields={"title": field("Oats"), "brand": field("LARKSPUR SELECT")})
        national = ProductProposal(fields={"title": field("Oats"), "brand": field("Juniper Mills")})
        db.add_all([own, national])
        await db.commit()
    got = (await admin_client.get(f"/api/v1/product-proposals/{own.id}")).json()
    assert got["house_brand"]["key"] == "larkspur/select"
    got = (await admin_client.get(f"/api/v1/product-proposals/{national.id}")).json()
    assert got["house_brand"] is None
