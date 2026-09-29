"""Linking an existing location to OpenStreetMap, phones, and field provenance (1F, criterion 60).

Every vendor, street and number is invented; phones use the 555-01xx range and
coordinates the synthetic grid from SECURITY.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.models.geo import Vendor, VendorLocation
from app.services import geo, osm
from tests import geo_helpers as gh
from tests.geo_helpers import NEAR_A, FakeOverpass, make_location, osm_node

clean_geo = gh.clean_geo
no_network = gh.no_network
overpass = gh.overpass

# pii-scan: allow invented street (Pier Lane, synthetic fixture)
ADDRESS = {"addr:housenumber": "4", "addr:street": "Pier Lane", "addr:city": "Seaside"}
ADDRESS_LINE = "4 Pier Lane, Seaside"  # pii-scan: allow invented street (synthetic fixture)
HOURS = "Mo-Su 08:00-21:00"
PHONE = "+1 555 0100"
SITE = "https://inventedmart.example"
# About 90 m north of NEAR_A. pii-scan: allow synthetic ocean coordinates
NEXT_DOOR = ("33.510800", "-120.490000")


def mart(**extra_tags: str) -> dict[str, Any]:
    node = osm_node(301, "Invented Mart", *NEAR_A, opening_hours=HOURS, address=ADDRESS)
    node["tags"].update({"phone": PHONE, "website": SITE, **extra_tags})
    return node


async def pinned(client: httpx.AsyncClient, name: str = "Elm St") -> dict[str, Any]:
    return await make_location(
        client, name, NEAR_A, vendor={"name": "Invented Mart", "kind": "chain"}
    )


def loc(location: dict[str, Any]) -> str:
    return f"/api/v1/vendor-locations/{location['id']}"


def url(location: dict[str, Any], tail: str) -> str:
    return f"{loc(location)}/{tail}"


# --- disabled ------------------------------------------------------------------


async def test_linking_is_refused_with_no_request_while_overpass_is_off(
    admin_client: httpx.AsyncClient, no_network
):
    assert get_settings().enable_overpass is False
    location = await pinned(admin_client)
    for method, path, body in [
        ("GET", url(location, "osm-candidates"), None),
        ("POST", url(location, "link-osm"), {"osm_type": "node", "osm_id": 301}),
    ]:
        r = await admin_client.request(method, path, json=body)
        assert r.status_code == 409, (path, r.text)
        assert r.json()["error"]["code"] == "integration_disabled"
    # Unlinking needs no network and is refused only because nothing is linked.
    r = await admin_client.post(url(location, "unlink-osm"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_adopted"


# --- candidates and link ----------------------------------------------------------


async def test_candidates_are_nearest_first_with_what_linking_would_change(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    overpass.elements = [osm_node(302, "Elm Corner Deli", *NEXT_DOOR, shop="deli"), mart()]
    location = await pinned(admin_client)
    r = await admin_client.get(url(location, "osm-candidates"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["osm_id"] for i in items] == [301, 302]
    first = items[0]
    assert first["distance_m"] == 0 and 80 <= items[1]["distance_m"] <= 100
    assert (first["phone"], first["website"]) == (PHONE, SITE)
    # "Elm St" was typed by a person, so linking keeps it; the rest is empty and fills.
    assert first["keeps"] == ["name"]
    assert first["fills"] == ["address", "opening_hours", "phone", "website"]
    assert first["linked_to"] is None
    assert "around:250," in overpass.requests[0]["query"]


async def test_link_fills_empty_fields_keeps_edits_and_records_sources(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    """Criterion 60: link, then a refresh fills what OSM changed and a renamed field survives."""
    node = mart()
    overpass.elements = [node]
    location = await pinned(admin_client)
    r = await admin_client.post(url(location, "link-osm"), json={"osm_type": "node", "osm_id": 301})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["osm_type"], body["osm_id"]) == ("node", 301)
    assert body["name"] == "Elm St"
    assert (body["address"], body["opening_hours"], body["phone"]) == (ADDRESS_LINE, HOURS, PHONE)
    assert set(body["sources"]) == {"address", "opening_hours", "phone"}
    assert body["sources"]["phone"]["source"] == "osm"
    assert body["sources"]["phone"]["ref"] == "node/301"
    assert body["sources"]["phone"]["checked_at"] is not None
    vendor = (await admin_client.get(f"/api/v1/vendors/{body['vendor']['id']}")).json()
    assert vendor["website"] == SITE and vendor["sources"]["website"]["ref"] == "node/301"

    # OSM changes its hours and name; the person changes the phone.
    await admin_client.patch(loc(location), json={"phone": "555-0142"})
    node["tags"].update({"opening_hours": "Mo-Sa 07:00-22:00", "name": "Invented Mart Elm"})
    r = await admin_client.post(url(location, "refresh-osm"))
    body = r.json()
    assert body["opening_hours"] == "Mo-Sa 07:00-22:00"
    assert body["name"] == "Elm St" and body["phone"] == "555-0142"
    assert "phone" not in body["sources"]  # entered by hand now


async def test_link_refusals(admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network):
    overpass.elements = [mart()]
    first = await pinned(admin_client)
    second = await make_location(admin_client, "Harbor Rd", NEAR_A, vendor_id=first["vendor"]["id"])
    link = {"osm_type": "node", "osm_id": 301}

    missing = await admin_client.post(url(first, "link-osm"), json={**link, "osm_id": 999})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "osm_candidate_not_found"

    assert (await admin_client.post(url(first, "link-osm"), json=link)).status_code == 200
    again = await admin_client.post(url(first, "link-osm"), json=link)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_linked"

    taken = await admin_client.post(url(second, "link-osm"), json=link)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "already_adopted"
    assert taken.json()["error"]["details"]["location_id"] == first["id"]
    listed = (await admin_client.get(url(second, "osm-candidates"))).json()["items"]
    assert listed[0]["linked_to"] == {"id": first["id"], "name": "Elm St"}


async def test_unlink_keeps_values_and_stops_refresh(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    overpass.elements = [mart()]
    location = await pinned(admin_client)
    await admin_client.post(url(location, "link-osm"), json={"osm_type": "node", "osm_id": 301})
    r = await admin_client.post(url(location, "unlink-osm"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["osm_type"] is None and body["osm_id"] is None
    assert (body["address"], body["phone"]) == (ADDRESS_LINE, PHONE)
    refused = await admin_client.post(url(location, "refresh-osm"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_adopted"


async def test_an_edited_phone_survives_two_refreshes(
    admin_client: httpx.AsyncClient, overpass: FakeOverpass, no_network
):
    """R7: the source record advances each time without handing the field back."""
    node = mart()
    overpass.elements = [node]
    location = await pinned(admin_client)
    await admin_client.post(url(location, "link-osm"), json={"osm_type": "node", "osm_id": 301})
    await admin_client.patch(loc(location), json={"phone": "555-0142"})
    for new in ("+1 555 0107", "+1 555 0108"):
        node["tags"]["phone"] = new
        r = await admin_client.post(url(location, "refresh-osm"))
        assert r.json()["phone"] == "555-0142"
    # Typing OSM's current number hands the field back to OSM.
    await admin_client.patch(loc(location), json={"phone": "+1 555 0108"})
    node["tags"]["phone"] = "+1 555 0109"
    r = await admin_client.post(url(location, "refresh-osm"))
    assert r.json()["phone"] == "+1 555 0109"


# --- phones ----------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["(555) 555-0100", "555 555 0100", "+1 555 555 0100", "5550100"])
async def test_a_phone_is_kept_as_typed(admin_client: httpx.AsyncClient, phone: str):
    location = await pinned(admin_client)
    r = await admin_client.patch(loc(location), json={"phone": f"  {phone} "})
    assert r.status_code == 200 and r.json()["phone"] == phone


@pytest.mark.parametrize("phone", ["555-01", "call 555 0100", "5555.0100.0100.0100"])
async def test_a_malformed_phone_is_refused(admin_client: httpx.AsyncClient, phone: str):
    location = await pinned(admin_client)
    r = await admin_client.patch(loc(location), json={"phone": phone})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_phone"


def test_osm_tags_keep_only_well_formed_phones_and_web_sites():
    node = mart(phone="call us; +1 555 0101; +1 555 0102", website="ftp://example.test")
    node["tags"]["contact:website"] = SITE
    candidate = osm._candidate(node)
    assert candidate is not None
    assert candidate.phone == "+1 555 0101" and candidate.website == SITE
    node["tags"].update({"phone": "none", "contact:website": "https://"})
    candidate = osm._candidate(node)
    assert candidate is not None and candidate.website is None  # no host
    node["tags"].update({"contact:website": "javascript:alert(1)"})
    node["tags"].pop("website")
    candidate = osm._candidate(node)
    assert candidate is not None and candidate.phone is None and candidate.website is None


def test_old_cache_entries_without_phone_still_load():
    old = {
        "osm_type": "node",
        "osm_id": 1,
        "name": "Invented Mart",
        "kind_guess": "chain",
        "lat": "33.5",
        "lon": "-120.5",
        "address": None,
        "opening_hours": None,
    }
    assert osm.OsmCandidate.from_json(old).phone is None


# --- R7: refresh by field and state, for both storage forms ------------------------

NOW = datetime(2026, 9, 29, tzinfo=UTC)
LOCATION_FIELDS = {"name": "Invented Mart", "address": ADDRESS_LINE, "opening_hours": HOURS}


def _candidate(**values: Any) -> osm.OsmCandidate:
    base: dict[str, Any] = {
        "osm_type": "node",
        "osm_id": 301,
        "name": "Invented Mart",
        "kind_guess": "chain",
        "lat": Decimal("33.51"),
        "lon": Decimal("-120.49"),
        "address": ADDRESS_LINE,
        "opening_hours": HOURS,
        "phone": PHONE,
        "website": SITE,
    }
    return osm.OsmCandidate(**{**base, **values})


def _linked(storage: str) -> tuple[VendorLocation, Vendor]:
    """A location and vendor as a link left them, in either storage form.

    ``snapshot``: adopted before ``field_source`` existed, so only the three
    ``osm_*`` columns remember what OSM said. ``field_source``: linked since.
    """
    vendor = Vendor(name="Invented Mart", kind="chain", field_source={})
    location = VendorLocation(name="Invented Mart", osm_type="node", osm_id=301, field_source={})
    location.vendor = vendor
    geo._apply_osm(location, vendor, _candidate(), NOW)
    if storage == "snapshot":
        location.field_source = {}
        vendor.field_source = {}
    return location, vendor


NEW = {
    "name": "Invented Mart Elm",
    "address": "6 Pier Lane, Seaside",  # pii-scan: allow invented street (synthetic fixture)
    "opening_hours": "Mo-Sa 07:00-22:00",
    "phone": "+1 555 0107",
    "website": "https://inventedmart.example/elm",
}


@pytest.mark.parametrize("storage", ["snapshot", "field_source"])
@pytest.mark.parametrize("name", list(NEW))
@pytest.mark.parametrize("state", ["unedited", "edited", "empty"])
def test_refresh_overwrites_keeps_or_fills_by_state(storage: str, name: str, state: str):
    location, vendor = _linked(storage)
    obj: Any = vendor if name == "website" else location
    if state == "edited":
        setattr(obj, name, "Hand typed" if name != "phone" else "555-0142")
    elif state == "empty":
        if name == "name":
            pytest.skip("a location always has a name")
        # Empty because the source never had it, not because a person cleared it.
        setattr(obj, name, None)
        obj.field_source = {k: v for k, v in obj.field_source.items() if k != name}
        if name in geo.OSM_SNAPSHOTS:
            setattr(location, geo.OSM_SNAPSHOTS[name], None)
    before = getattr(obj, name)

    geo._apply_osm(location, vendor, _candidate(**{name: NEW[name]}), NOW)
    after = getattr(obj, name)

    if name == "website":
        # The vendor's website is filled only while it has none.
        expected = NEW[name] if before is None else before
    elif state == "edited":
        expected = before
    elif storage == "snapshot" and name == "phone" and state == "unedited":
        # No snapshot column ever held a phone: a phone present before field_source
        # existed was typed by a person.
        expected = before
    else:
        expected = NEW[name]
    assert after == expected
    if name in geo.OSM_SNAPSHOTS:
        assert getattr(location, geo.OSM_SNAPSHOTS[name]) == NEW[name]


def test_a_person_clearing_a_field_is_an_edit():
    location, vendor = _linked("field_source")
    location.address = None
    geo._apply_osm(location, vendor, _candidate(address=NEW["address"]), NOW)
    assert location.address is None


# --- kerp osm refresh ----------------------------------------------------------------


def test_the_refresh_command_needs_a_target_and_overpass():
    from typer.testing import CliRunner

    from app.cli import cli

    runner = CliRunner()
    bare = runner.invoke(cli, ["osm", "refresh"])
    assert bare.exit_code == 2 and "--all-linked" in bare.output
    off = runner.invoke(cli, ["osm", "refresh", "--all-linked"])
    assert off.exit_code == 2 and "ENABLE_OVERPASS" in off.output
