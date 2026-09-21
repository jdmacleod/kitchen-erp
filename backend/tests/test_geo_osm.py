"""OpenStreetMap adoption (criteria 24-26) and the no-network guarantee when disabled (25)."""

import socket
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.core.config import get_settings
from app.services import osm
from tests import geo_helpers as gh
from tests.geo_helpers import (
    FAR,
    HOME_A,
    MID,
    NEAR_A,
    FakeOverpass,
    make_home_base,
    make_location,
    make_vendor,
    osm_node,
)

clean_geo = gh.clean_geo
no_network = gh.no_network
overpass = gh.overpass

WEEK = "Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00"
# pii-scan: allow invented street (Kelp Road, synthetic fixture)
ADDRESS = {"addr:housenumber": "12", "addr:street": "Kelp Road", "addr:city": "Seaside"}


def candidates_url(home_id: str, radius: int = 5000) -> str:
    return f"/api/v1/osm/candidates?home_base_id={home_id}&radius_m={radius}"


# --- disabled by default --------------------------------------------------------


async def test_disabled_by_default_and_no_code_path_touches_the_network(
    admin_client: httpx.AsyncClient, no_network
):
    """Criterion 25: with ENABLE_OVERPASS unset, exercise every geo path under a socket guard."""
    assert get_settings().enable_overpass is False
    with pytest.raises(AssertionError, match="outbound network"):
        socket.create_connection(("192.0.2.1", 80), timeout=0.2)  # the guard is live

    home = await make_home_base(admin_client, "Home", HOME_A)
    vendor = await make_vendor(admin_client, "Harbour Greens")
    market = await make_location(
        admin_client,
        "Market",
        MID,
        vendor={"name": "Market", "kind": "market"},
        opening_hours="Sa 08:00-13:00",
    )
    stall = await make_location(
        admin_client, "Stall", MID, vendor_id=vendor["id"], parent_location_id=market["id"]
    )
    for method, path, body in [
        ("GET", "/api/v1/home-bases", None),
        ("GET", f"/api/v1/home-bases/{home['id']}", None),
        ("PATCH", f"/api/v1/home-bases/{home['id']}", {"label": "home"}),
        ("GET", "/api/v1/vendors?q=harbour", None),
        ("GET", f"/api/v1/vendors/{vendor['id']}", None),
        ("PATCH", f"/api/v1/vendors/{vendor['id']}", {"price_scope": "chain"}),
        ("POST", f"/api/v1/vendors/{vendor['id']}/deactivate", None),
        ("POST", f"/api/v1/vendors/{vendor['id']}/activate", None),
        ("GET", f"/api/v1/vendor-locations?near={HOME_A[0]},{HOME_A[1]}", None),
        ("GET", "/api/v1/vendor-locations?open_at=2026-06-06T17:00:00Z&kind=market", None),
        ("GET", f"/api/v1/vendor-locations/{market['id']}", None),
        ("PATCH", f"/api/v1/vendor-locations/{stall['id']}", {"address": None}),
        ("GET", f"/api/v1/vendor-locations/{stall['id']}/is-open", None),
        ("POST", f"/api/v1/vendor-locations/{stall['id']}/deactivate", None),
        ("POST", f"/api/v1/vendor-locations/{stall['id']}/activate", None),
        ("POST", "/api/v1/opening-hours/validate", {"text": WEEK}),
    ]:
        r = await admin_client.request(method, path, json=body)
        assert r.status_code == 200, (method, path, r.text)

    for method, path, body in [
        ("GET", candidates_url(home["id"]), None),
        (
            "POST",
            "/api/v1/osm/adopt",
            {"osm_type": "node", "osm_id": 1, "home_base_id": home["id"], "radius_m": 5000},
        ),
        ("POST", f"/api/v1/vendor-locations/{market['id']}/refresh-osm", None),
    ]:
        r = await admin_client.request(method, path, json=body)
        assert r.status_code == 409, (method, path, r.text)
        assert r.json()["error"]["code"] == "integration_disabled"

    assert (await admin_client.delete(f"/api/v1/home-bases/{home['id']}")).status_code == 409


async def test_guard_blocks_dns_and_direct_connections(no_network):
    with pytest.raises(AssertionError, match="outbound network"):
        socket.getaddrinfo("overpass-api.de", 443)
    # anyio wraps the socket-level failure in an ExceptionGroup; the guard still fired.
    with pytest.raises(BaseExceptionGroup) as info:
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.get("http://192.0.2.1/")
    assert info.group_contains(AssertionError, match="outbound network")


# --- enabled, against a mocked Overpass ------------------------------------------------


async def test_candidates_query_user_agent_and_cache(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    overpass.elements = [
        osm_node(101, "Kelp & Co", *NEAR_A, opening_hours=WEEK, address=ADDRESS),
        osm_node(102, "Tidepool Market", *MID, shop=None, amenity="marketplace", osm_type="way"),
        osm_node(103, "Driftwood Bakery", *FAR, shop="bakery", opening_hours="not hours"),
        osm_node(104, None, *FAR, shop="farm"),
        {"type": "node", "id": 105, "tags": {"shop": "deli", "name": "No Coordinates"}},
    ]
    home = await make_home_base(admin_client, "Home", HOME_A)
    r = await admin_client.get(candidates_url(home["id"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["osm_id"] for i in items] == [103, 101, 102, 104]  # named first, by name
    kelp, market, bakery, farm = items[1], items[2], items[0], items[3]
    assert kelp == {
        "osm_type": "node",
        "osm_id": 101,
        "name": "Kelp & Co",
        "kind_guess": "chain",
        "lat": NEAR_A[0].rstrip("0"),
        "lon": NEAR_A[1].rstrip("0"),
        # pii-scan: allow invented street (Kelp Road, synthetic fixture)
        "address": "12 Kelp Road, Seaside",
        "opening_hours": WEEK,
        "already_adopted": False,
    }
    assert market["kind_guess"] == "market" and market["osm_type"] == "way"
    assert bakery["opening_hours"] is None  # unparseable OSM hours are dropped, not stored
    assert farm["name"] is None and farm["kind_guess"] == "stand"

    assert len(overpass.requests) == 1
    request = overpass.requests[0]
    assert request["url"] == osm.OVERPASS_URL
    assert request["user_agent"].startswith("kitchen-erp/")
    assert f"around:5000,{HOME_A[0]},{HOME_A[1]}" in request["query"]
    for value in osm.SHOP_VALUES:
        assert value in request["query"]
    assert '"amenity"="marketplace"' in request["query"]

    again = await admin_client.get(candidates_url(home["id"]))
    assert again.json() == r.json()
    assert len(overpass.requests) == 1  # served from cache
    wider = await admin_client.get(candidates_url(home["id"], 8000))
    assert wider.status_code == 200 and len(overpass.requests) == 2  # a new radius is a new key

    osm.cache.clear()
    from_disk = await admin_client.get(candidates_url(home["id"]))
    assert from_disk.json() == r.json() and len(overpass.requests) == 2

    missing = await admin_client.get(candidates_url(str(uuid.uuid4())))
    assert missing.status_code == 404
    too_small = await admin_client.get(candidates_url(home["id"], 10))
    assert too_small.status_code == 422


async def test_adopt_creates_vendor_place_location_and_refuses_twice(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    """Criterion 24."""
    overpass.elements = [
        osm_node(101, "Kelp & Co", *NEAR_A, opening_hours=WEEK, address=ADDRESS),
        osm_node(104, None, *FAR, shop="farm"),
    ]
    home = await make_home_base(admin_client, "Home", HOME_A)
    body = {"osm_type": "node", "osm_id": 101, "home_base_id": home["id"], "radius_m": 5000}
    r = await admin_client.post("/api/v1/osm/adopt", json=body)
    assert r.status_code == 201, r.text
    location = r.json()
    assert location["name"] == "Kelp & Co"
    assert location["vendor"]["name"] == "Kelp & Co"
    assert location["vendor"]["kind"] == "chain"
    # pii-scan: allow invented street (Kelp Road, synthetic fixture)
    assert location["address"] == "12 Kelp Road, Seaside"
    assert location["opening_hours"] == WEEK
    assert location["osm_type"] == "node" and location["osm_id"] == 101
    assert location["home_base_id"] == home["id"]
    assert location["lat"] == NEAR_A[0].rstrip("0")

    vendors = await admin_client.get("/api/v1/vendors")
    assert [v["name"] for v in vendors.json()["items"]] == ["Kelp & Co"]
    listed = await admin_client.get("/api/v1/vendor-locations")
    assert [i["id"] for i in listed.json()["items"]] == [location["id"]]

    twice = await admin_client.post("/api/v1/osm/adopt", json=body)
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "already_adopted"
    assert twice.json()["error"]["details"]["location_id"] == location["id"]
    candidates = await admin_client.get(candidates_url(home["id"]))
    assert {c["osm_id"]: c["already_adopted"] for c in candidates.json()["items"]} == {
        101: True,
        104: False,
    }

    unnamed = await admin_client.post("/api/v1/osm/adopt", json={**body, "osm_id": 104})
    assert unnamed.status_code == 422
    assert unnamed.json()["error"]["code"] == "osm_candidate_unnamed"
    unknown = await admin_client.post("/api/v1/osm/adopt", json={**body, "osm_id": 999})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "osm_candidate_not_found"
    assert len((await admin_client.get("/api/v1/vendor-locations")).json()["items"]) == 1


async def test_adopt_reuses_existing_vendor_and_honours_kind_override(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    existing = await make_vendor(admin_client, "kelp & co", kind="chain")
    overpass.elements = [
        osm_node(101, "Kelp & Co", *NEAR_A),
        osm_node(102, "Blue Barn", *MID, shop="farm"),
    ]
    home = await make_home_base(admin_client, "Home", HOME_A)
    base = {"home_base_id": home["id"], "radius_m": 5000, "osm_type": "node"}
    reused = await admin_client.post("/api/v1/osm/adopt", json={**base, "osm_id": 101})
    assert reused.status_code == 201 and reused.json()["vendor"]["id"] == existing["id"]
    overridden = await admin_client.post(
        "/api/v1/osm/adopt", json={**base, "osm_id": 102, "vendor_kind": "independent"}
    )
    assert overridden.status_code == 201
    assert overridden.json()["vendor"]["kind"] == "independent"
    assert len((await admin_client.get("/api/v1/vendors")).json()["items"]) == 2


async def test_refresh_updates_hours_but_keeps_user_edits(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    """Criterion 26."""
    node = osm_node(101, "Kelp & Co", *NEAR_A, opening_hours=WEEK, address=ADDRESS)
    overpass.elements = [node]
    home = await make_home_base(admin_client, "Home", HOME_A)
    adopted = (
        await admin_client.post(
            "/api/v1/osm/adopt",
            json={"osm_type": "node", "osm_id": 101, "home_base_id": home["id"], "radius_m": 5000},
        )
    ).json()
    url = f"/api/v1/vendor-locations/{adopted['id']}"

    # The user renames the location; OSM later changes hours, name, and address.
    await admin_client.patch(url, json={"name": "Kelp & Co (pier)"})
    node["tags"].update(
        {"name": "Kelp and Company", "opening_hours": "Mo-Su 07:00-22:00", "addr:city": "Tidewater"}
    )
    r = await admin_client.post(f"{url}/refresh-osm")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Kelp & Co (pier)"  # user's edit preserved
    assert r.json()["opening_hours"] == "Mo-Su 07:00-22:00"  # hours followed OSM
    # pii-scan: allow invented street (Kelp Road, synthetic fixture)
    assert r.json()["address"] == "12 Kelp Road, Tidewater"  # untouched field followed OSM
    assert overpass.requests[-1]["query"].startswith("[out:json][timeout:25];node(101);")

    # A user-edited opening_hours string is theirs too; only untouched fields follow OSM.
    await admin_client.patch(url, json={"opening_hours": "Sa 08:00-13:00"})
    node["tags"]["opening_hours"] = "24/7"
    r = await admin_client.post(f"{url}/refresh-osm")
    assert r.json()["opening_hours"] == "Sa 08:00-13:00"
    # Restoring the OSM value hands the field back: the next refresh follows OSM again.
    await admin_client.patch(url, json={"opening_hours": "24/7"})
    node["tags"]["opening_hours"] = "Mo-Fr 06:00-23:00"
    r = await admin_client.post(f"{url}/refresh-osm")
    assert r.json()["opening_hours"] == "Mo-Fr 06:00-23:00"

    # A name that vanished from OSM never blanks the location; an invalid string clears hours.
    node["tags"].pop("name")
    node["tags"]["opening_hours"] = "garbage"
    r = await admin_client.post(f"{url}/refresh-osm")
    assert r.json()["name"] == "Kelp & Co (pier)" and r.json()["opening_hours"] is None

    not_adopted = await make_location(admin_client, "Pin", MID)
    r = await admin_client.post(f"/api/v1/vendor-locations/{not_adopted['id']}/refresh-osm")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_adopted"
    overpass.elements = []
    gone = await admin_client.post(f"{url}/refresh-osm")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "osm_object_not_found"


async def test_upstream_failure_is_a_502(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    overpass.status_code = 429
    home = await make_home_base(admin_client, "Home", HOME_A)
    r = await admin_client.get(candidates_url(home["id"]))
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "overpass_unavailable"
    assert r.json()["error"]["details"] == {"status": 429}


async def test_cache_expires_after_ttl(overpass: FakeOverpass, tmp_path):
    key = "k"
    item = osm.OsmCandidate(
        "node", 1, "x", "chain", Decimal(NEAR_A[0]), Decimal(NEAR_A[1]), None, None
    )
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    osm.cache.put(key, [item], now=t0)
    assert osm.cache.get(key, now=t0 + timedelta(hours=23)) == [item]
    assert osm.cache.get(key, now=t0 + timedelta(hours=25)) is None
    files = list((tmp_path / "osm-cache").glob("*.json"))
    assert len(files) == 1 and "33.51" in files[0].read_text()


async def test_rate_limiter_spaces_requests_two_seconds_apart():
    clock = {"now": 100.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    limiter = osm.RateLimiter(2.0, clock=lambda: clock["now"], sleep=fake_sleep)
    await limiter.wait()
    await limiter.wait()
    clock["now"] += 0.5
    await limiter.wait()
    clock["now"] += 10
    await limiter.wait()
    assert sleeps == [2.0, 1.5]
    assert osm.MIN_INTERVAL_SECONDS == 2.0
