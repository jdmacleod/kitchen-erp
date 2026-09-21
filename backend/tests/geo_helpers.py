"""Shared fixtures and helpers for the Phase 1D geo tests.

Every coordinate here lies in the synthetic grid from SECURITY.md (latitude 33.0
to 34.0, longitude -121.0 to -120.0): open ocean, no home, no shop. Every vendor
is invented. Test modules import the fixtures they need by name.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import asyncpg
import httpx
import pytest

from app.core.config import get_settings
from app.services import osm
from tests.conftest import _dsn

# Synthetic grid points (SECURITY.md). pii-scan: allow synthetic ocean coordinates
HOME_A = ("33.500000", "-120.500000")
HOME_B = ("33.900000", "-120.100000")
NEAR_A = ("33.510000", "-120.490000")
NEAR_B = ("33.890000", "-120.110000")
MID = ("33.700000", "-120.300000")
FAR = ("33.050000", "-120.950000")

GEO_TABLES = ("vendor_location", "home_base", "vendor", "place")
_LOOPBACK = {"127.0.0.1", "::1", "localhost", None, ""}


@pytest.fixture(autouse=True)
async def clean_geo() -> AsyncIterator[None]:
    """conftest truncates app_user only; geo tables have no FK to it."""
    yield
    owner = await asyncpg.connect(_dsn(os.environ["MIGRATION_DATABASE_URL"]))
    try:
        await owner.execute(f"TRUNCATE {', '.join(GEO_TABLES)} CASCADE")
    finally:
        await owner.close()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail the test on any attempt to resolve or connect beyond loopback.

    The database on 127.0.0.1 stays reachable; anything else raises at the
    socket layer, below httpx and asyncio, so no client library can slip past.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if self.family == socket.AF_UNIX or host in _LOOPBACK:
            return real_connect(self, address)
        raise AssertionError(f"outbound network access attempted (connect): {host!r}")

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host in _LOOPBACK or (isinstance(host, bytes) and host.decode() in _LOOPBACK):
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError(f"outbound network access attempted (dns): {host!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield


@dataclass
class FakeOverpass:
    """Elements the mocked Overpass API returns, and a log of what it was asked."""

    elements: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    status_code: int = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode()).get("data", [""])[0]
        self.requests.append(
            {
                "url": str(request.url),
                "user_agent": request.headers.get("user-agent"),
                "query": query,
            }
        )
        if self.status_code != 200:
            return httpx.Response(self.status_code, text="busy")
        if "around:" in query:
            elements = self.elements
        else:
            elements = [
                e
                for e in self.elements
                if f"{e['type']}({e['id']})" in query  # the by-id query names the object
            ]
        return httpx.Response(200, json={"elements": elements})


def osm_node(
    osm_id: int,
    name: str | None,
    lat: str,
    lon: str,
    *,
    shop: str | None = "supermarket",
    amenity: str | None = None,
    opening_hours: str | None = None,
    address: dict[str, str] | None = None,
    osm_type: str = "node",
) -> dict[str, Any]:
    tags: dict[str, str] = {}
    if name is not None:
        tags["name"] = name
    if shop:
        tags["shop"] = shop
    if amenity:
        tags["amenity"] = amenity
    if opening_hours is not None:
        tags["opening_hours"] = opening_hours
    tags.update(address or {})
    element: dict[str, Any] = {"type": osm_type, "id": osm_id, "tags": tags}
    coords = {"lat": float(lat), "lon": float(lon)}  # Overpass emits JSON numbers
    if osm_type == "node":
        element.update(coords)
    else:
        element["center"] = coords
    return element


@pytest.fixture
def overpass(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[FakeOverpass]:
    """Enable the integration against a mocked transport, zero rate-limit wait, temp cache."""
    fake = FakeOverpass()
    monkeypatch.setattr(get_settings(), "enable_overpass", True)
    monkeypatch.setattr(osm, "http_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(osm, "cache_dir", lambda: tmp_path / "osm-cache")
    monkeypatch.setattr(osm, "limiter", osm.RateLimiter(0))
    osm.cache.clear()
    yield fake
    osm.cache.clear()


# --- API helpers ---------------------------------------------------------------


async def make_home_base(
    client: httpx.AsyncClient, name: str, coords: tuple[str, str], **extra: Any
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/home-bases", json={"name": name, "lat": coords[0], "lon": coords[1], **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def make_vendor(
    client: httpx.AsyncClient, name: str, kind: str = "independent", **extra: Any
) -> dict[str, Any]:
    r = await client.post("/api/v1/vendors", json={"name": name, "kind": kind, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def make_location(
    client: httpx.AsyncClient, name: str, coords: tuple[str, str], **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "lat": coords[0], "lon": coords[1], **extra}
    if "vendor_id" not in body and "vendor" not in body:
        body["vendor"] = {"name": name, "kind": "stand"}
    r = await client.post("/api/v1/vendor-locations", json=body)
    assert r.status_code == 201, r.text
    return r.json()
