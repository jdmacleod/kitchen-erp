"""Vendor locations: pin-and-name creation (21), stalls (22), nearest home base (27), filters."""

import uuid

import httpx

from tests import geo_helpers as gh
from tests.geo_helpers import (
    FAR,
    HOME_A,
    HOME_B,
    MID,
    NEAR_A,
    NEAR_B,
    make_home_base,
    make_location,
    make_vendor,
)

clean_geo = gh.clean_geo

WEEK = "Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00"
SEASONAL = "Apr-Oct Sa 08:00-13:00"
LOCATION_KEYS = {
    "id",
    "vendor",
    "name",
    "lat",
    "lon",
    "address",
    "home_base_id",
    "parent_location_id",
    "opening_hours",
    "effective_opening_hours",
    "opening_hours_inherited",
    "stop_overhead_min",
    "receipt_identifiers",
    "osm_type",
    "osm_id",
    "active",
    "is_open",
    "distance_m",
    "created_at",
}


async def test_home_base_vendor_and_location_from_pins_and_names(admin_client: httpx.AsyncClient):
    """Criterion 21: no address, no typed coordinates beyond the pin, no vendor pre-created."""
    home = await make_home_base(admin_client, "Home", HOME_A)
    r = await admin_client.post(
        "/api/v1/vendor-locations",
        json={
            # pii-scan: allow invented street (Kelp Road, synthetic fixture)
            "name": "Kelp Road Stand",
            "lat": NEAR_A[0],
            "lon": NEAR_A[1],
            # pii-scan: allow invented street (Kelp Road, synthetic fixture)
            "vendor": {"name": "Kelp Road Stand", "kind": "stand"},
        },
    )
    assert r.status_code == 201, r.text
    location = r.json()
    assert set(location) == LOCATION_KEYS | {"stalls"}
    # pii-scan: allow invented street (Kelp Road, synthetic fixture)
    assert location["vendor"]["name"] == "Kelp Road Stand"
    assert location["vendor"]["kind"] == "stand"
    assert location["vendor"]["price_scope"] == "location"
    assert location["lat"] == NEAR_A[0] and location["lon"] == NEAR_A[1]
    assert location["home_base_id"] == home["id"]
    assert location["address"] is None
    assert location["opening_hours"] is None
    assert location["effective_opening_hours"] is None
    assert location["opening_hours_inherited"] is False
    assert location["receipt_identifiers"] == []
    assert location["osm_type"] is None and location["osm_id"] is None
    assert location["stalls"] == []
    assert location["active"] is True

    vendors = await admin_client.get("/api/v1/vendors")
    # pii-scan: allow invented street (Kelp Road, synthetic fixture)
    assert [v["name"] for v in vendors.json()["items"]] == ["Kelp Road Stand"]
    # The inline vendor is reused, case-insensitively, rather than duplicated.
    again = await make_location(
        admin_client, "Second Table", MID, vendor={"name": "kelp road stand", "kind": "stand"}
    )
    assert again["vendor"]["id"] == location["vendor"]["id"]


async def test_vendor_id_or_inline_vendor_exactly_one(admin_client: httpx.AsyncClient):
    vendor = await make_vendor(admin_client, "Harbour Greens")
    neither = await admin_client.post(
        "/api/v1/vendor-locations", json={"name": "x", "lat": NEAR_A[0], "lon": NEAR_A[1]}
    )
    assert neither.status_code == 422
    both = await admin_client.post(
        "/api/v1/vendor-locations",
        json={
            "name": "x",
            "lat": NEAR_A[0],
            "lon": NEAR_A[1],
            "vendor_id": vendor["id"],
            "vendor": {"name": "y", "kind": "stand"},
        },
    )
    assert both.status_code == 422
    unknown = await admin_client.post(
        "/api/v1/vendor-locations",
        json={"name": "x", "lat": NEAR_A[0], "lon": NEAR_A[1], "vendor_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 404


async def test_home_base_defaults_to_nearest_and_can_be_cleared(admin_client: httpx.AsyncClient):
    """Criterion 27."""
    a = await make_home_base(admin_client, "A", HOME_A)
    b = await make_home_base(admin_client, "B", HOME_B)
    near_a = await make_location(admin_client, "Near A", NEAR_A)
    near_b = await make_location(admin_client, "Near B", NEAR_B)
    assert near_a["home_base_id"] == a["id"]
    assert near_b["home_base_id"] == b["id"]

    explicit = await make_location(admin_client, "Explicit", NEAR_A, home_base_id=b["id"])
    assert explicit["home_base_id"] == b["id"]
    none = await make_location(admin_client, "In Between", MID, home_base_id=None)
    assert none["home_base_id"] is None

    cleared = await admin_client.patch(
        f"/api/v1/vendor-locations/{near_a['id']}", json={"home_base_id": None}
    )
    assert cleared.status_code == 200 and cleared.json()["home_base_id"] is None
    reassigned = await admin_client.patch(
        f"/api/v1/vendor-locations/{near_a['id']}", json={"home_base_id": a["id"]}
    )
    assert reassigned.json()["home_base_id"] == a["id"]
    missing = await admin_client.patch(
        f"/api/v1/vendor-locations/{near_a['id']}", json={"home_base_id": str(uuid.uuid4())}
    )
    assert missing.status_code == 404


async def test_no_home_bases_means_none(admin_client: httpx.AsyncClient):
    location = await make_location(admin_client, "Lonely", NEAR_A)
    assert location["home_base_id"] is None


async def test_stall_inherits_market_hours_unless_it_has_its_own(admin_client: httpx.AsyncClient):
    """Criterion 22."""
    market = await make_location(
        admin_client,
        "Tidepool Market",
        MID,
        vendor={"name": "Tidepool Market", "kind": "market"},
        opening_hours=SEASONAL,
    )
    stall = await make_location(
        admin_client,
        "Blue Barn Eggs",
        MID,
        vendor={"name": "Blue Barn Farm", "kind": "stand"},
        parent_location_id=market["id"],
    )
    assert stall["parent_location_id"] == market["id"]
    assert stall["opening_hours"] is None
    assert stall["effective_opening_hours"] == SEASONAL
    assert stall["opening_hours_inherited"] is True

    own = await admin_client.patch(
        f"/api/v1/vendor-locations/{stall['id']}", json={"opening_hours": "Sa 09:00-12:00"}
    )
    assert own.status_code == 200, own.text
    assert own.json()["effective_opening_hours"] == "Sa 09:00-12:00"
    assert own.json()["opening_hours_inherited"] is False

    back = await admin_client.patch(
        f"/api/v1/vendor-locations/{stall['id']}", json={"opening_hours": None}
    )
    assert back.json()["effective_opening_hours"] == SEASONAL
    assert back.json()["opening_hours_inherited"] is True

    detail = await admin_client.get(f"/api/v1/vendor-locations/{market['id']}")
    assert [s["id"] for s in detail.json()["stalls"]] == [stall["id"]]
    assert detail.json()["stalls"][0]["effective_opening_hours"] == SEASONAL

    listed = await admin_client.get("/api/v1/vendor-locations")
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[stall["id"]]["opening_hours_inherited"] is True


async def test_stall_rules(admin_client: httpx.AsyncClient):
    market = await make_location(
        admin_client, "Market", MID, vendor={"name": "Market", "kind": "market"}
    )
    stall = await make_location(admin_client, "Stall", MID, parent_location_id=market["id"])
    nested = await admin_client.post(
        "/api/v1/vendor-locations",
        json={
            "name": "Nested",
            "lat": MID[0],
            "lon": MID[1],
            "vendor": {"name": "Nested", "kind": "stand"},
            "parent_location_id": stall["id"],
        },
    )
    assert nested.status_code == 422 and nested.json()["error"]["code"] == "parent_is_stall"
    self_parent = await admin_client.patch(
        f"/api/v1/vendor-locations/{market['id']}", json={"parent_location_id": market["id"]}
    )
    assert self_parent.status_code == 422
    demote = await admin_client.patch(
        f"/api/v1/vendor-locations/{market['id']}", json={"parent_location_id": stall["id"]}
    )
    assert demote.status_code == 422 and demote.json()["error"]["code"] == "has_stalls"
    detached = await admin_client.patch(
        f"/api/v1/vendor-locations/{stall['id']}", json={"parent_location_id": None}
    )
    assert detached.status_code == 200 and detached.json()["parent_location_id"] is None


async def test_list_near_orders_by_distance_with_decimal_strings(admin_client: httpx.AsyncClient):
    home = await make_home_base(admin_client, "Home", HOME_A)
    await make_location(admin_client, "Far", FAR)
    await make_location(admin_client, "Near", NEAR_A)
    await make_location(admin_client, "Middle", MID)
    r = await admin_client.get(
        "/api/v1/vendor-locations", params={"near": f"{home['lat']},{home['lon']}"}
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Near", "Middle", "Far"]
    distances = [i["distance_m"] for i in items]
    assert all(isinstance(d, str) for d in distances)
    assert [float(d) for d in distances] == sorted(float(d) for d in distances)
    assert 1000 < float(distances[0]) < 2000  # about 1.4 km on the synthetic grid
    plain = await admin_client.get("/api/v1/vendor-locations")
    assert all(i["distance_m"] is None for i in plain.json()["items"])
    bad = await admin_client.get("/api/v1/vendor-locations", params={"near": "north"})
    assert bad.status_code == 422


async def test_list_filters(admin_client: httpx.AsyncClient):
    a = await make_home_base(admin_client, "A", HOME_A)
    await make_home_base(admin_client, "B", HOME_B)
    chain = await make_vendor(admin_client, "Kelp & Co", kind="chain")
    kelp = await make_location(admin_client, "Kelp Pier", NEAR_A, vendor_id=chain["id"])
    stand = await make_location(admin_client, "Stand", NEAR_B)

    by_kind = await admin_client.get("/api/v1/vendor-locations", params={"kind": "chain"})
    assert [i["id"] for i in by_kind.json()["items"]] == [kelp["id"]]
    by_home = await admin_client.get("/api/v1/vendor-locations", params={"home_base_id": a["id"]})
    assert [i["id"] for i in by_home.json()["items"]] == [kelp["id"]]
    by_vendor = await admin_client.get(
        "/api/v1/vendor-locations", params={"vendor_id": stand["vendor"]["id"]}
    )
    assert [i["id"] for i in by_vendor.json()["items"]] == [stand["id"]]

    off = await admin_client.post(f"/api/v1/vendor-locations/{stand['id']}/deactivate")
    assert off.status_code == 200 and off.json()["active"] is False
    default = await admin_client.get("/api/v1/vendor-locations")
    assert [i["id"] for i in default.json()["items"]] == [kelp["id"]]
    everything = await admin_client.get(
        "/api/v1/vendor-locations", params={"include_inactive": "true"}
    )
    assert len(everything.json()["items"]) == 2
    assert (await admin_client.get(f"/api/v1/vendor-locations/{stand['id']}")).status_code == 200
    on = await admin_client.post(f"/api/v1/vendor-locations/{stand['id']}/activate")
    assert on.json()["active"] is True

    # Deactivating the vendor hides its locations from the default listing too.
    await admin_client.post(f"/api/v1/vendors/{chain['id']}/deactivate")
    default = await admin_client.get("/api/v1/vendor-locations")
    assert [i["id"] for i in default.json()["items"]] == [stand["id"]]


async def test_open_at_filter_and_is_open_flag(admin_client: httpx.AsyncClient):
    weekly = await make_location(admin_client, "Weekly", NEAR_A, opening_hours=WEEK)
    seasonal = await make_location(admin_client, "Seasonal", MID, opening_hours=SEASONAL)
    unknown = await make_location(admin_client, "Unknown Hours", FAR)

    # Saturday 2026-06-06 10:00 PDT: both open.
    r = await admin_client.get(
        "/api/v1/vendor-locations", params={"open_at": "2026-06-06T17:00:00Z"}
    )
    names = {i["name"]: i["is_open"] for i in r.json()["items"]}
    assert names == {"Weekly": True, "Seasonal": True}
    # Saturday 2026-01-03 10:00 PST: the seasonal market is closed for winter.
    r = await admin_client.get(
        "/api/v1/vendor-locations", params={"open_at": "2026-01-03T18:00:00Z"}
    )
    assert [i["name"] for i in r.json()["items"]] == ["Weekly"]
    # Without open_at, is_open is not evaluated and unknown-hours locations are listed.
    r = await admin_client.get("/api/v1/vendor-locations")
    assert {i["name"]: i["is_open"] for i in r.json()["items"]} == {
        "Weekly": None,
        "Seasonal": None,
        "Unknown Hours": None,
    }
    assert {weekly["id"], seasonal["id"], unknown["id"]} == {i["id"] for i in r.json()["items"]}


async def test_patch_fields_and_move(admin_client: httpx.AsyncClient):
    location = await make_location(admin_client, "Stand", NEAR_A)
    r = await admin_client.patch(
        f"/api/v1/vendor-locations/{location['id']}",
        json={
            "name": "Renamed Stand",
            "address": "12 Kelp Road",  # pii-scan: allow invented address
            "lat": MID[0],
            "lon": MID[1],
            "stop_overhead_min": 5,
            "receipt_identifiers": ["STORE #0042", " ST 7 "],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed Stand"
    # pii-scan: allow invented street (Kelp Road, synthetic fixture)
    assert body["address"] == "12 Kelp Road"
    assert body["lat"] == MID[0] and body["lon"] == MID[1]
    assert body["stop_overhead_min"] == 5
    assert body["receipt_identifiers"] == ["STORE #0042", "ST 7"]
    home = await make_home_base(admin_client, "Home", HOME_B)
    near = await admin_client.get(
        "/api/v1/vendor-locations", params={"near": f"{home['lat']},{home['lon']}"}
    )
    assert near.json()["items"][0]["lat"] == MID[0]
    assert (await admin_client.get(f"/api/v1/vendor-locations/{uuid.uuid4()}")).status_code == 404


async def test_create_honours_idempotency_key(admin_client: httpx.AsyncClient):
    headers = {"Idempotency-Key": "loc-1"}
    body = {
        "name": "Stand",
        "lat": NEAR_A[0],
        "lon": NEAR_A[1],
        "vendor": {"name": "Stand", "kind": "stand"},
    }
    first = await admin_client.post("/api/v1/vendor-locations", json=body, headers=headers)
    second = await admin_client.post("/api/v1/vendor-locations", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert len((await admin_client.get("/api/v1/vendor-locations")).json()["items"]) == 1
    changed = await admin_client.post(
        "/api/v1/vendor-locations", json={**body, "name": "Other"}, headers=headers
    )
    assert changed.status_code == 422
    assert changed.json()["error"]["code"] == "idempotency_key_reused"
