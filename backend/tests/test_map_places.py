"""Finding a place by name in the deployment's own map extract (#62)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.services import mvt, place_search
from tests.tile_helpers import write_extract

TOWN_ZOOMS = (8, 12, 15)
POI_ZOOMS = (15,)

# Invented names, all in the synthetic box from SECURITY.md.
FEATURES = [
    (
        "places",
        33.50,
        -120.50,
        {"kind": "locality", "kind_detail": "town", "name": "Saltmarsh Harbour"},
        TOWN_ZOOMS,
    ),
    (
        "places",
        33.20,
        -120.80,
        {"kind": "locality", "kind_detail": "village", "name": "Kelp Point"},
        TOWN_ZOOMS,
    ),
    ("places", 33.505, -120.49, {"kind": "neighbourhood", "name": "Old Quay"}, (12, 15)),
    ("pois", 33.5010, -120.5010, {"kind": "supermarket", "name": "Tideline Grocers"}, POI_ZOOMS),
    ("pois", 33.5100, -120.5100, {"kind": "supermarket", "name": "Harbourside Grocery"}, POI_ZOOMS),
    ("pois", 33.5020, -120.4990, {"kind": "cafe", "name": "Café Saltmarsh"}, POI_ZOOMS),
    # Far from the harbour: found only when searching near Kelp Point.
    ("pois", 33.2000, -120.8000, {"kind": "supermarket", "name": "Kelp Point Grocers"}, POI_ZOOMS),
]


@pytest.fixture
def extract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tiles = tmp_path / "tiles"
    write_extract(tiles / "basemap.pmtiles", FEATURES)
    monkeypatch.setattr(get_settings(), "tiles_path", str(tiles))
    place_search._open.clear()
    return tiles


async def test_towns_are_found_anywhere_in_the_extract(admin_client: httpx.AsyncClient, extract):
    r = await admin_client.get("/api/v1/map/places", params={"q": "kelp"})
    assert r.status_code == 200, r.text
    [town] = r.json()["items"]
    assert town["name"] == "Kelp Point" and town["kind"] == "town" and town["detail"] == "village"
    assert Decimal(town["lat"]) == pytest.approx(Decimal("33.2"), abs=Decimal("0.001"))
    assert town["distance_m"] is None


async def test_shops_near_the_map_centre_come_nearest_first(admin_client, extract):
    r = await admin_client.get(
        "/api/v1/map/places", params={"q": "grocer", "lat": "33.5", "lon": "-120.5"}
    )
    names = [item["name"] for item in r.json()["items"]]
    # Kelp Point Grocers is 40 km away: outside the shop radius.
    assert names == ["Tideline Grocers", "Harbourside Grocery"]
    first = r.json()["items"][0]
    assert first["kind"] == "poi" and first["detail"] == "supermarket"
    assert 100 < first["distance_m"] < 200


async def test_matching_ignores_case_and_accents_and_needs_every_word(admin_client, extract):
    near = {"lat": "33.5", "lon": "-120.5"}
    r = await admin_client.get("/api/v1/map/places", params={"q": "CAFE salt", **near})
    assert [i["name"] for i in r.json()["items"]] == ["Café Saltmarsh"]
    r = await admin_client.get("/api/v1/map/places", params={"q": "old quay", **near})
    assert [(i["name"], i["kind"]) for i in r.json()["items"]] == [("Old Quay", "neighbourhood")]
    r = await admin_client.get("/api/v1/map/places", params={"q": "tideline bakery", **near})
    assert r.json()["items"] == []


async def test_without_an_extract_it_says_so(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "tiles_path", str(tmp_path / "none"))
    r = await admin_client.get("/api/v1/map/places", params={"q": "kelp"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "tiles_missing"


async def test_the_query_is_bounded(admin_client, extract):
    assert (await admin_client.get("/api/v1/map/places", params={"q": "k"})).status_code == 422
    r = await admin_client.get("/api/v1/map/places", params={"q": "kelp", "lat": "95"})
    assert r.status_code == 422


async def test_the_endpoint_needs_a_session(client, extract):
    assert (await client.get("/api/v1/map/places", params={"q": "kelp"})).status_code == 401


def test_a_malformed_tile_is_an_error_not_a_hang():
    with pytest.raises(ValueError):
        mvt.points(b"\x1a\xff\xff\xff\xff\x0f", {"pois"})
    with pytest.raises(ValueError):
        mvt.points(b"\x1a\x05\x0a\x03", {"pois"})
