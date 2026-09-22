"""Home bases: a pin and a name (criterion 21), coordinates as decimal strings, delete rules."""

import uuid

import httpx

from tests import geo_helpers as gh
from tests.geo_helpers import HOME_A, HOME_B, NEAR_A, make_home_base, make_location

clean_geo = gh.clean_geo


async def test_create_with_name_and_pin_only(admin_client: httpx.AsyncClient):
    created = await make_home_base(admin_client, "Harbour Flat", HOME_A)
    assert set(created) == {"id", "name", "lat", "lon", "label", "created_at"}
    assert created["lat"] == HOME_A[0] and created["lon"] == HOME_A[1]
    assert isinstance(created["lat"], str)  # decimals cross the wire as strings
    assert created["label"] == "Harbour Flat"

    fetched = await admin_client.get(f"/api/v1/home-bases/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_coordinates_round_trip_exactly(admin_client: httpx.AsyncClient):
    created = await make_home_base(admin_client, "Precise", ("33.1234567", "-120.7654321"))
    assert created["lat"] == "33.1234567"
    assert created["lon"] == "-120.7654321"


async def test_list_patch_delete(admin_client: httpx.AsyncClient):
    a = await make_home_base(admin_client, "Alpha", HOME_A)
    b = await make_home_base(admin_client, "Beta", HOME_B, label="weekend place")
    listed = await admin_client.get("/api/v1/home-bases")
    assert [h["name"] for h in listed.json()["items"]] == ["Alpha", "Beta"]
    assert listed.json()["items"][1]["label"] == "weekend place"

    patched = await admin_client.patch(
        f"/api/v1/home-bases/{a['id']}", json={"name": "Alpha Prime", "lat": "33.6", "label": None}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Alpha Prime"
    assert patched.json()["lat"] == "33.6" and patched.json()["lon"] == HOME_A[1]
    assert patched.json()["label"] is None

    gone = await admin_client.delete(f"/api/v1/home-bases/{b['id']}")
    assert gone.status_code == 204
    assert (await admin_client.get(f"/api/v1/home-bases/{b['id']}")).status_code == 404


async def test_name_must_be_unique(admin_client: httpx.AsyncClient):
    await make_home_base(admin_client, "Twin", HOME_A)
    dup = await admin_client.post(
        "/api/v1/home-bases", json={"name": "Twin", "lat": HOME_B[0], "lon": HOME_B[1]}
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "home_base_name_taken"


async def test_delete_refused_while_referenced(admin_client: httpx.AsyncClient):
    home = await make_home_base(admin_client, "Home", HOME_A)
    location = await make_location(admin_client, "Kelp Stand", NEAR_A)
    assert location["home_base_id"] == home["id"]
    refused = await admin_client.delete(f"/api/v1/home-bases/{home['id']}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "home_base_in_use"

    cleared = await admin_client.patch(
        f"/api/v1/vendor-locations/{location['id']}", json={"home_base_id": None}
    )
    assert cleared.status_code == 200 and cleared.json()["home_base_id"] is None
    # The request is made before the assertion, not inside it: `python -O`
    # strips assert statements, and a DELETE that only happens under a
    # non-optimised interpreter is a test that silently stops testing.
    deleted = await admin_client.delete(f"/api/v1/home-bases/{home['id']}")
    assert deleted.status_code == 204


async def test_validation_and_auth(admin_client: httpx.AsyncClient, client: httpx.AsyncClient):
    bad = await admin_client.post("/api/v1/home-bases", json={"name": "x", "lat": "91", "lon": "0"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
    assert (await admin_client.get(f"/api/v1/home-bases/{uuid.uuid4()}")).status_code == 404
    admin_client.cookies.clear()
    assert (await client.get("/api/v1/home-bases")).status_code == 401
