"""Vendor import: dry run, then apply (1F, criteria 64-67 and 73; eng review R2).

Every vendor, street and number is invented; coordinates are in the synthetic
grid from SECURITY.md.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import httpx
import pytest
import yaml

from tests import geo_helpers as gh
from tests.geo_helpers import HOME_A, MID, NEAR_A, NEAR_B, make_home_base, make_location

clean_geo = gh.clean_geo

# About 50 m from NEAR_A. pii-scan: allow synthetic ocean coordinates
BESIDE_A = ("33.510400", "-120.490000")


def location(key: str, name: str, at: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {"key": key, "name": name, "lat": at[0], "lon": at[1], **extra}


def vendor(key: str, name: str, *locations: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "kind": "chain",
        "price_scope": "location",
        "locations": list(locations),
        **extra,
    }


def document(*vendors: dict[str, Any], mode: str = "public") -> dict[str, Any]:
    return {
        "format": "kitchen-erp-vendors/1",
        "license": "ODbL-1.0",
        "source": {"name": "kitchen-erp", "exported_at": "2026-09-29T12:00:00Z", "mode": mode},
        "vendors": json.loads(json.dumps(list(vendors))),  # a copy: cases edit it
    }


MART = vendor(
    "invented-mart",
    "Invented Mart",
    location(
        "invented-mart/elm-st",
        "Elm St",
        NEAR_A,
        phone="+1 555 0100",
        opening_hours="Mo-Su 08:00-21:00",
    ),
    location("invented-mart/harbor-rd", "Harbor Rd", NEAR_B),
    website="https://inventedmart.example",
)


async def post(
    client: httpx.AsyncClient,
    doc: Any,
    *,
    dry_run: bool,
    fmt: str = "yaml",
    raw: bytes | None = None,
) -> httpx.Response:
    body = (
        raw
        if raw is not None
        else (yaml.safe_dump(doc).encode() if fmt == "yaml" else json.dumps(doc).encode())
    )
    return await client.post(
        f"/api/v1/vendors/import?dry_run={str(dry_run).lower()}&filename=vendors.{fmt}",
        content=body,
        headers={"Content-Type": f"application/{fmt}"},
    )


async def imported(client: httpx.AsyncClient, doc: Any, **kw: Any) -> dict[str, Any]:
    r = await post(client, doc, dry_run=False, **kw)
    assert r.status_code == 200, r.text
    return r.json()


async def counts(owner: asyncpg.Connection) -> tuple[int, int, int]:
    return (
        await owner.fetchval("SELECT count(*) FROM vendor"),
        await owner.fetchval("SELECT count(*) FROM vendor_location"),
        await owner.fetchval("SELECT count(*) FROM place"),
    )


async def test_dry_run_reports_and_writes_nothing(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    """Criterion 67 (a dry run changes nothing) and R2 (row counts unchanged)."""
    before = await counts(owner_conn)
    r = await post(admin_client, document(MART), dry_run=True)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["dry_run"] is True
    assert report["counts"] == {
        "created": 3,
        "updated": 0,
        "unchanged": 0,
        "conflicts": 0,
        "unmatched": 0,
    }
    assert await counts(owner_conn) == before
    elm = next(i for i in report["items"] if i["key"] == "invented-mart/elm-st")
    assert {c["field"]: c["new"] for c in elm["changes"]}["phone"] == "+1 555 0100"


async def test_apply_then_the_same_file_again_changes_nothing(admin_client: httpx.AsyncClient):
    """Criterion 64."""
    first = await imported(admin_client, document(MART))
    assert first["counts"]["created"] == 3
    loc = (await admin_client.get("/api/v1/vendor-locations?near=33.51,-120.49")).json()["items"]
    assert {entry["key"] for entry in loc} == {"invented-mart/elm-st", "invented-mart/harbor-rd"}
    elm = next(entry for entry in loc if entry["key"] == "invented-mart/elm-st")
    assert elm["sources"]["phone"]["source"] == "import"
    assert elm["sources"]["phone"]["ref"] == "vendors.yaml"
    again = await imported(admin_client, document(MART), fmt="json")
    assert again["counts"] == {
        "created": 0,
        "updated": 0,
        "unchanged": 3,
        "conflicts": 0,
        "unmatched": 0,
    }


async def test_a_field_edited_after_import_is_a_conflict(admin_client: httpx.AsyncClient):
    """Criterion 65."""
    await imported(admin_client, document(MART))
    loc = (await admin_client.get("/api/v1/vendor-locations?near=33.51,-120.49")).json()["items"]
    elm = next(entry for entry in loc if entry["key"] == "invented-mart/elm-st")
    await admin_client.patch(f"/api/v1/vendor-locations/{elm['id']}", json={"phone": "555-0142"})

    changed = json.loads(json.dumps(MART))
    changed["locations"][0]["phone"] = "+1 555 0107"
    changed["locations"][0]["address"] = "4 Pier Lane"  # pii-scan: allow invented street
    report = await imported(admin_client, document(changed))
    item = next(i for i in report["items"] if i["key"] == "invented-mart/elm-st")
    assert item["outcome"] == "conflict"
    assert item["conflicts"] == [{"field": "phone", "current": "555-0142", "file": "+1 555 0107"}]
    assert [c["field"] for c in item["changes"]] == ["address"]  # the untouched field still fills
    after = (await admin_client.get(f"/api/v1/vendor-locations/{elm['id']}")).json()
    assert after["phone"] == "555-0142"


async def test_matching_order_key_then_osm_then_name_nearby(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    """Criterion 66."""
    by_key = await make_location(
        admin_client, "Old name", MID, vendor={"name": "Invented Mart", "kind": "chain"}
    )
    vendor_id = by_key["vendor"]["id"]
    by_osm = await make_location(admin_client, "Pier", NEAR_B, vendor_id=vendor_id)
    await owner_conn.execute(
        "UPDATE vendor_location SET osm_type = 'node', osm_id = 301 WHERE id = $1", by_osm["id"]
    )
    await make_location(admin_client, "Elm Street", NEAR_A, vendor_id=vendor_id)
    doc = document(
        vendor(
            "different-key",  # the vendor matches by name
            "Invented Mart",
            location(by_key["key"], "Old name", MID),
            location("invented-mart/pier-x", "Pier", NEAR_B, osm={"type": "node", "id": 301}),
            location("invented-mart/elm", "Elm St", BESIDE_A),  # 50 m away, similar name
        )
    )
    report = await imported(admin_client, doc)
    assert report["counts"]["created"] == 0 and report["counts"]["unmatched"] == 0
    assert (await counts(owner_conn))[1] == 3


async def test_two_nearby_candidates_are_reported_not_guessed(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    first = await make_location(
        admin_client, "Elm St", NEAR_A, vendor={"name": "Invented Mart", "kind": "chain"}
    )
    await make_location(admin_client, "Elm St.", BESIDE_A, vendor_id=first["vendor"]["id"])
    doc = document(vendor("invented-mart-x", "Invented Mart", location("x/elm", "Elm St", NEAR_A)))
    report = await imported(admin_client, doc)
    item = next(i for i in report["items"] if i["target"] == "location")
    assert item["outcome"] == "unmatched"
    assert item["reason"].startswith("Matches 2 locations within 150 m")
    assert (await counts(owner_conn))[1] == 2


async def test_household_fields_only_from_a_household_file(admin_client: httpx.AsyncClient):
    """Criterion 73: an unknown kitchen is reported and none is created."""
    await make_home_base(admin_client, "Harbour flat", HOME_A)
    household = json.loads(json.dumps(MART))
    household["household"] = {
        "id": "not-used",
        "notes": "Parking behind",
        "active": True,
        "locations": {
            "invented-mart/elm-st": {
                "id": "x",
                "home_base": "Cliff cottage",
                "active": True,
                "publishable": True,
                "receipt_identifiers": ["0217"],
            },
            "invented-mart/harbor-rd": {
                "id": "y",
                "home_base": "Harbour flat",
                "active": True,
                "publishable": False,
            },
        },
    }
    public = await post(admin_client, document(household), dry_run=True)
    assert public.status_code == 422  # a public file with household blocks
    report = await imported(admin_client, document(household, mode="household"))
    assert report["unresolved_home_bases"] == ["Cliff cottage"]
    homes = (await admin_client.get("/api/v1/home-bases")).json()["items"]
    assert [h["name"] for h in homes] == ["Harbour flat"]
    locs = (await admin_client.get("/api/v1/vendor-locations?near=33.51,-120.49")).json()["items"]
    elm = next(entry for entry in locs if entry["key"] == "invented-mart/elm-st")
    assert elm["receipt_identifiers"] == ["0217"] and elm["publishable"] is True
    assert elm["home_base_id"] == homes[0]["id"]  # the nearest-base default
    vendor_out = (await admin_client.get(f"/api/v1/vendors/{elm['vendor']['id']}")).json()
    assert vendor_out["notes"] == "Parking behind"


# --- malformed files (criterion 67) --------------------------------------------------


def _float_coordinate() -> dict[str, Any]:
    doc = document(MART)
    doc["vendors"][0]["locations"][0]["lat"] = 33.51
    return doc


def _dup_keys() -> dict[str, Any]:
    doc = document(MART)
    doc["vendors"][0]["locations"][1]["key"] = "invented-mart/elm-st"
    return doc


def _unknown_parent() -> dict[str, Any]:
    doc = document(MART)
    doc["vendors"][0]["locations"][1]["parent"] = "invented-mart/nowhere"
    return doc


def _cycle() -> dict[str, Any]:
    doc = document(MART)
    doc["vendors"][0]["locations"][0]["parent"] = "invented-mart/harbor-rd"
    doc["vendors"][0]["locations"][1]["parent"] = "invented-mart/elm-st"
    return doc


def _cross_vendor_stall() -> dict[str, Any]:
    other = vendor("quay-market", "Quay Market", location("quay-market/stall", "Stall", MID))
    other["locations"][0]["parent"] = "invented-mart/elm-st"
    return document(MART, other)


def _unknown_format() -> dict[str, Any]:
    doc = document(MART)
    doc["format"] = "kitchen-erp-vendors/9"
    return doc


@pytest.mark.parametrize(
    ("case", "fmt"),
    [
        (_float_coordinate, "yaml"),
        (_float_coordinate, "json"),
        (_dup_keys, "yaml"),
        (_unknown_parent, "yaml"),
        (_cycle, "yaml"),
        (_cross_vendor_stall, "json"),
        (_unknown_format, "yaml"),
    ],
)
async def test_a_malformed_file_is_refused_before_any_write(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection, case, fmt: str
):
    before = await counts(owner_conn)
    r = await post(admin_client, case(), dry_run=False, fmt=fmt)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "bad_export"
    assert await counts(owner_conn) == before


async def test_yaml_aliases_and_oversized_files_are_refused(admin_client: httpx.AsyncClient):
    alias = (
        "format: kitchen-erp-vendors/1\nlicense: ODbL-1.0\n"
        "source: &s {name: x, exported_at: '2026-09-29T12:00:00Z', mode: public}\n"
        "other: *s\nvendors: []\n"
    )
    r = await post(admin_client, None, dry_run=True, raw=alias.encode())
    assert r.status_code == 422 and "anchors" in r.json()["error"]["message"]
    big = b"# " + b"x" * (5 * 1024 * 1024) + b"\n"
    r = await post(admin_client, None, dry_run=True, raw=big)
    assert r.status_code == 422 and "5 MB" in r.json()["error"]["message"]


async def test_values_are_checked_like_the_edit_form(admin_client: httpx.AsyncClient):
    doc = document(MART)
    doc["vendors"][0]["locations"][0]["opening_hours"] = "whenever"
    r = await post(admin_client, doc, dry_run=True)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_opening_hours"
    doc = document(MART)
    doc["vendors"][0]["locations"][0]["phone"] = "call us"
    r = await post(admin_client, doc, dry_run=True)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_phone"


async def test_an_exported_household_file_imports_unchanged(admin_client: httpx.AsyncClient):
    """Export, then import the same file: nothing to change."""
    await make_location(
        admin_client, "Elm St", NEAR_A, vendor={"name": "Invented Mart", "kind": "chain"}
    )
    exported = await admin_client.get("/api/v1/vendors/export?format=yaml&mode=household")
    r = await post(admin_client, None, dry_run=True, raw=exported.content)
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == {
        "created": 0,
        "updated": 0,
        "unchanged": 2,
        "conflicts": 0,
        "unmatched": 0,
    }
