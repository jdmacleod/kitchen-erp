"""kitchen-erp-vendors/2: the vendor facts products need (1H, criterion 93).

A /2 file carries a storefront platform, fetch policy, weighed-item label
layout, receipt code position and a location's storefront store id. /1 files
import unchanged, a /1 file may not carry those fields, and an export with none
of them is still /1. Vendors are invented; coordinates sit in the synthetic grid.
"""

from __future__ import annotations

import json

import asyncpg
import httpx

from tests import geo_helpers as gh
from tests.test_vendor_import import MART, document, imported, location, post, vendor

clean_geo = gh.clean_geo

LAYOUT = {
    "item_start": 1,
    "item_len": 5,
    "price_start": 6,
    "price_len": 5,
    "price_kind": "price_cents",
}
CODES = {"kind": "leading_token", "min_len": 4, "max_len": 7}


def v2(*vendors) -> dict:
    doc = document(*vendors)
    doc["format"] = "kitchen-erp-vendors/2"
    return doc


async def export_json(client: httpx.AsyncClient) -> dict:
    r = await client.get("/api/v1/vendors/export?format=json&mode=household")
    assert r.status_code == 200, r.text
    return json.loads(r.text)


async def test_v1_file_imports_and_exports_as_v1(admin_client: httpx.AsyncClient):
    await imported(admin_client, document(MART))
    assert (await export_json(admin_client))["format"] == "kitchen-erp-vendors/1"


async def test_v1_file_may_not_carry_v2_fields(admin_client: httpx.AsyncClient):
    doc = document(vendor("invented-mart", "Invented Mart", platform="chain_storefront"))
    r = await post(admin_client, doc, dry_run=True)
    assert r.status_code == 422, r.text
    assert "kitchen-erp-vendors/2" in r.text


async def test_v2_fields_round_trip(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    entry = vendor(
        "cardinal-foods",
        "Cardinal Foods",
        location(
            "cardinal-foods/elm-st",
            "Elm St",
            gh.NEAR_A,
            platform_store_ref="store/417",
        ),
        platform="chain_storefront",
        fetch_policy="server_fetch",
        rw_layout=LAYOUT,
        code_position=CODES,
    )
    report = await imported(admin_client, v2(entry))
    assert report["counts"]["created"] == 2
    row = await owner_conn.fetchrow(
        "SELECT platform, fetch_policy, rw_layout, code_position FROM vendor "
        "WHERE slug = 'cardinal-foods'"
    )
    assert row["platform"] == "chain_storefront" and row["fetch_policy"] == "server_fetch"
    assert json.loads(row["rw_layout"]) == LAYOUT and json.loads(row["code_position"]) == CODES
    ref = await owner_conn.fetchval(
        "SELECT platform_store_ref FROM vendor_location WHERE key = 'cardinal-foods/elm-st'"
    )
    assert ref == "store/417"

    out = await export_json(admin_client)
    assert out["format"] == "kitchen-erp-vendors/2"
    exported = next(v for v in out["vendors"] if v["key"] == "cardinal-foods")
    assert exported["platform"] == "chain_storefront"
    assert exported["fetch_policy"] == "server_fetch"
    assert exported["rw_layout"] == LAYOUT and exported["code_position"] == CODES
    assert exported["locations"][0]["platform_store_ref"] == "store/417"

    # Importing the same file again changes nothing.
    again = await imported(admin_client, v2(entry))
    assert again["counts"]["created"] == 0 and again["counts"]["updated"] == 0


async def test_default_fetch_policy_is_left_out_of_exports(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    await imported(admin_client, document(MART))
    await owner_conn.execute(
        "UPDATE vendor SET fetch_policy = 'capture_only' WHERE slug = 'invented-mart'"
    )
    assert (await export_json(admin_client))["format"] == "kitchen-erp-vendors/1"


async def test_invalid_layout_and_code_position_refused(admin_client: httpx.AsyncClient):
    bad_layout = vendor("invented-mart", "Invented Mart", rw_layout={**LAYOUT, "price_start": 9})
    r = await post(admin_client, v2(bad_layout), dry_run=True)
    assert r.status_code == 422
    bad_codes = vendor("invented-mart", "Invented Mart", code_position={**CODES, "min_len": 9})
    r = await post(admin_client, v2(bad_codes), dry_run=True)
    assert r.status_code == 422
