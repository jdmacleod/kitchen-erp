"""Vendor export in the kitchen-erp-vendors/1 format (1F, criteria 62 and 63).

Every vendor, street and number is invented; coordinates are in the synthetic
grid from SECURITY.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

import asyncpg
import httpx
import pytest
import yaml

from app.schemas.vendor_interchange import VendorFile
from tests import geo_helpers as gh
from tests.conftest import run_alembic
from tests.geo_helpers import HOME_A, MID, NEAR_A, NEAR_B, make_home_base, make_location

clean_geo = gh.clean_geo

# Keys that belong to the household and must never appear in a public file.
HOUSEHOLD_KEYS = {
    "household",
    "id",
    "notes",
    "active",
    "home_base",
    "home_base_id",
    "stop_overhead_min",
    "receipt_identifiers",
    "publishable",
    "last_visit",
}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def all_keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in all_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in all_keys(v)}
    return set()


async def household(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """A chain with a shared, a linked-by-hand and a private branch; a stand; a market."""
    home = await make_home_base(client, "Harbour flat", HOME_A)
    shared = await make_location(
        client,
        "Elm St",
        NEAR_A,
        vendor={"name": "Invented Mart", "kind": "chain"},
        phone="+1 555 0100",
        publishable=True,
        receipt_identifiers=["0217"],
        home_base_id=home["id"],
    )
    vendor_id = shared["vendor"]["id"]
    await client.patch(f"/api/v1/vendors/{vendor_id}", json={"notes": "Parking behind"})
    private = await make_location(client, "Harbor Rd", NEAR_B, vendor_id=vendor_id)
    stand = await make_location(
        client, "Blue Barn", MID, vendor={"name": "Blue Barn", "kind": "stand"}, publishable=True
    )
    market = await make_location(
        client, "Quay market", MID, vendor={"name": "Quay Market", "kind": "market"}
    )
    return {"shared": shared, "private": private, "stand": stand, "market": market}


async def export(client: httpx.AsyncClient, fmt: str, mode: str) -> httpx.Response:
    r = await client.get(f"/api/v1/vendors/export?format={fmt}&mode={mode}")
    assert r.status_code == 200, r.text
    return r


async def test_public_export_carries_no_household_data(admin_client: httpx.AsyncClient):
    """Criterion 62: scan every key of the document."""
    await household(admin_client)
    r = await export(admin_client, "yaml", "public")
    assert r.headers["content-type"].startswith("application/yaml")
    assert re.match(
        r'attachment; filename="vendors-public-\d{4}-\d{2}-\d{2}\.yaml"',
        r.headers["content-disposition"],
    )
    document = yaml.safe_load(r.text)
    VendorFile.model_validate(document)
    assert all_keys(document) & HOUSEHOLD_KEYS == set()
    assert not UUID.search(r.text)
    assert "0217" not in r.text and "Parking" not in r.text
    assert document["format"] == "kitchen-erp-vendors/1" and document["license"] == "ODbL-1.0"
    # Only the shared branch: not the private one, not the stand, not the unmarked market.
    assert [v["key"] for v in document["vendors"]] == ["invented-mart"]
    [location] = document["vendors"][0]["locations"]
    assert location["key"] == "invented-mart/elm-st" and location["phone"] == "+1 555 0100"


async def test_household_export_carries_the_household_block(admin_client: httpx.AsyncClient):
    made = await household(admin_client)
    document = json.loads((await export(admin_client, "json", "household")).text)
    VendorFile.model_validate(document)
    by_key = {v["key"]: v for v in document["vendors"]}
    assert set(by_key) == {"blue-barn", "invented-mart", "quay-market"}
    mart = by_key["invented-mart"]
    assert mart["household"]["notes"] == "Parking behind"
    assert mart["household"]["id"] == made["shared"]["vendor"]["id"]
    elm = mart["household"]["locations"]["invented-mart/elm-st"]
    assert elm == {
        "id": made["shared"]["id"],
        "home_base": "Harbour flat",
        "active": True,
        "publishable": True,
        "receipt_identifiers": ["0217"],
    }


async def test_yaml_and_json_are_the_same_document(admin_client: httpx.AsyncClient):
    """Criterion 63: equal documents, and every coordinate a string in both."""
    await household(admin_client)
    for mode in ("public", "household"):
        as_yaml = yaml.safe_load((await export(admin_client, "yaml", mode)).text)
        as_json = json.loads((await export(admin_client, "json", mode)).text)
        as_yaml["source"].pop("exported_at")
        as_json["source"].pop("exported_at")
        assert as_yaml == as_json
        for vendor in as_yaml["vendors"]:
            for location in vendor["locations"]:
                assert isinstance(location["lat"], str) and isinstance(location["lon"], str)


async def test_linked_locations_are_public_and_inactive_ones_are_not(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    made = await household(admin_client)
    # Linking needs Overpass; the column is what export reads.
    await owner_conn.execute(
        "UPDATE vendor_location SET osm_type = 'node', osm_id = 301 WHERE id = $1",
        made["private"]["id"],
    )
    await admin_client.post(f"/api/v1/vendor-locations/{made['shared']['id']}/deactivate")
    document = yaml.safe_load((await export(admin_client, "yaml", "public")).text)
    [location] = document["vendors"][0]["locations"]
    assert location["key"] == "invented-mart/harbor-rd"
    assert location["osm"] == {"type": "node", "id": 301}
    summary = (await admin_client.get("/api/v1/vendors/export-summary")).json()
    assert summary == {"locations": 3, "public": 1}


async def test_keys_are_assigned_once_and_survive_renames(admin_client: httpx.AsyncClient):
    first = await make_location(
        admin_client, "Elm St", NEAR_A, vendor={"name": "Café Nord", "kind": "chain"}
    )
    second = await make_location(
        admin_client, "Elm St", NEAR_B, vendor={"name": "Cafe Nord", "kind": "chain"}
    )
    again = await make_location(admin_client, "Elm St", MID, vendor_id=first["vendor"]["id"])
    assert first["key"] == "cafe-nord/elm-st"
    assert second["key"] == "cafe-nord-2/elm-st"
    assert again["key"] == "cafe-nord/elm-st-2"
    renamed = await admin_client.patch(
        f"/api/v1/vendor-locations/{first['id']}", json={"name": "Pier"}
    )
    assert renamed.json()["key"] == "cafe-nord/elm-st"
    vendor = await admin_client.patch(
        f"/api/v1/vendors/{first['vendor']['id']}", json={"name": "Nord"}
    )
    assert vendor.json()["slug"] == "cafe-nord"


async def test_brand_and_wikidata_are_validated(admin_client: httpx.AsyncClient):
    location = await make_location(
        admin_client, "Elm St", NEAR_A, vendor={"name": "Invented Mart", "kind": "chain"}
    )
    url = f"/api/v1/vendors/{location['vendor']['id']}"
    ok = await admin_client.patch(url, json={"brand": "Invented Mart", "wikidata": "Q123"})
    assert ok.status_code == 200 and ok.json()["wikidata"] == "Q123"
    bad = await admin_client.patch(url, json={"wikidata": "123"})
    assert bad.status_code == 422


@pytest.mark.usefixtures("clean_geo")
async def test_migration_backfills_unique_keys(owner_conn: asyncpg.Connection):
    run_alembic("downgrade", "0010")
    try:
        place = await owner_conn.fetchval(
            "INSERT INTO place (id, lat, lon, geom) VALUES (gen_random_uuid(), 33.5, -120.5, "
            "ST_SetSRID(ST_MakePoint(-120.5, 33.5), 4326)::geography) RETURNING id"
        )
        vendors = []
        for name in ("Café Nord", "Cafe  Nord", "!!!"):
            vendors.append(
                await owner_conn.fetchval(
                    "INSERT INTO vendor (id, name, kind, price_scope, active) "
                    "VALUES (gen_random_uuid(), $1, 'chain', 'location', true) RETURNING id",
                    name,
                )
            )
        for name in ("Elm St", "Elm-St"):
            await owner_conn.execute(
                "INSERT INTO vendor_location "
                "(id, vendor_id, place_id, name, receipt_identifiers, active) "
                "VALUES (gen_random_uuid(), $1, $2, $3, '{}', true)",
                vendors[0],
                place,
                name,
            )
    finally:
        run_alembic("upgrade", "head")
    slugs = [
        r["slug"] for r in await owner_conn.fetch("SELECT slug FROM vendor ORDER BY created_at, id")
    ]
    assert sorted(slugs) == ["cafe-nord", "cafe-nord-2", "vendor"]
    keys = sorted(r["key"] for r in await owner_conn.fetch("SELECT key FROM vendor_location"))
    assert keys[0].endswith("/elm-st") and keys[1].endswith("/elm-st-2")
    from app.core.db import dispose_engine

    await dispose_engine()
