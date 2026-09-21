"""Optional OpenStreetMap adoption through the public Overpass API.

Off by default (``ENABLE_OVERPASS``). Every entry point calls ``ensure_enabled``
before touching the cache or the network, so a disabled integration never opens
a socket. When enabled: one request at most every ``MIN_INTERVAL_SECONDS``, a
``User-Agent`` naming the application, and a memory plus on-disk cache with a
24 hour TTL so browsing candidates does not repeat the query. Only public place
data (a centre point and a radius) is ever sent. OSM data is ODbL; see
docs/licensing.md for the attribution obligation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.services.opening_hours import validate_hours

log = get_logger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "kitchen-erp/0.1 (self-hosted household kitchen system; Overpass client)"
MIN_INTERVAL_SECONDS = 2.0
CACHE_TTL = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 30.0

SHOP_VALUES = (
    "supermarket",
    "greengrocer",
    "butcher",
    "seafood",
    "bakery",
    "deli",
    "cheese",
    "farm",
)
KIND_GUESS = {
    "supermarket": "chain",
    "greengrocer": "independent",
    "butcher": "independent",
    "seafood": "independent",
    "bakery": "independent",
    "deli": "independent",
    "cheese": "independent",
    "farm": "stand",
    "marketplace": "market",
}
_ADDRESS_KEYS = ("addr:housenumber", "addr:street", "addr:city", "addr:postcode")


def ensure_enabled() -> None:
    if not get_settings().enable_overpass:
        raise ApiError(
            409,
            "integration_disabled",
            "OpenStreetMap adoption is disabled. Set ENABLE_OVERPASS=true to use it.",
        )


@dataclass(frozen=True)
class OsmCandidate:
    osm_type: str
    osm_id: int
    name: str | None
    kind_guess: str
    lat: Decimal
    lon: Decimal
    address: str | None
    opening_hours: str | None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["lat"] = str(self.lat)
        data["lon"] = str(self.lon)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OsmCandidate:
        return cls(**{**data, "lat": Decimal(data["lat"]), "lon": Decimal(data["lon"])})


# --- rate limiting -----------------------------------------------------------


class RateLimiter:
    """At most one request per ``min_interval`` seconds, process-wide."""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock: asyncio.Lock | None = None

    async def wait(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._last is not None:
                delay = self._last + self._min_interval - self._clock()
                if delay > 0:
                    await self._sleep(delay)
            self._last = self._clock()


limiter = RateLimiter(MIN_INTERVAL_SECONDS)


# --- cache -------------------------------------------------------------------


def cache_dir() -> Path:
    return Path(get_settings().receipts_path).parent / "osm-cache"


class CandidateCache:
    """Memory first, then a JSON file per key. Disk failures degrade to memory only."""

    def __init__(self) -> None:
        self._memory: dict[str, tuple[datetime, list[OsmCandidate]]] = {}
        self._disk_warned = False

    def clear(self) -> None:
        self._memory.clear()

    def _path(self, key: str) -> Path:
        return cache_dir() / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str, now: datetime | None = None) -> list[OsmCandidate] | None:
        now = now or datetime.now(UTC)
        hit = self._memory.get(key)
        if hit is None:
            hit = self._read_disk(key)
            if hit is not None:
                self._memory[key] = hit
        if hit is None:
            return None
        fetched_at, items = hit
        if now - fetched_at > CACHE_TTL:
            self._memory.pop(key, None)
            return None
        return items

    def put(self, key: str, items: list[OsmCandidate], now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        self._memory[key] = (now, items)
        payload = {"fetched_at": now.isoformat(), "items": [c.to_json() for c in items]}
        try:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        except OSError as exc:
            if not self._disk_warned:
                self._disk_warned = True
                log.warning("osm cache directory unwritable", extra={"error": type(exc).__name__})

    def _read_disk(self, key: str) -> tuple[datetime, list[OsmCandidate]] | None:
        try:
            raw = json.loads(self._path(key).read_text())
            return (
                datetime.fromisoformat(raw["fetched_at"]),
                [OsmCandidate.from_json(c) for c in raw["items"]],
            )
        except (OSError, ValueError, KeyError, TypeError, InvalidOperation):
            return None


cache = CandidateCache()


# --- Overpass client -----------------------------------------------------------


# Tests set this to an httpx.MockTransport; production leaves it None (real sockets).
http_transport: httpx.AsyncBaseTransport | None = None


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        transport=http_transport,
    )


def _address(tags: dict[str, Any]) -> str | None:
    number, street, city, postcode = (tags.get(k) for k in _ADDRESS_KEYS)
    line1 = " ".join(p for p in (number, street) if p)
    line2 = " ".join(p for p in (city, postcode) if p)
    joined = ", ".join(p for p in (line1, line2) if p)
    return joined or None


def _candidate(element: dict[str, Any]) -> OsmCandidate | None:
    osm_type = element.get("type")
    osm_id = element.get("id")
    if osm_type not in ("node", "way", "relation") or not isinstance(osm_id, int):
        return None
    coords = element if osm_type == "node" else element.get("center") or {}
    try:
        lat = Decimal(str(coords["lat"]))
        lon = Decimal(str(coords["lon"]))
    except (KeyError, TypeError, InvalidOperation):
        return None
    tags = element.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}
    shop = tags.get("shop")
    amenity = tags.get("amenity")
    kind_guess = KIND_GUESS.get(str(shop), KIND_GUESS.get(str(amenity), "independent"))
    hours = tags.get("opening_hours")
    if not isinstance(hours, str) or validate_hours(hours) is not None:
        hours = None  # OSM data is untrusted text; keep only what parses
    name = tags.get("name")
    address = _address({k: v for k, v in tags.items() if isinstance(v, str)})
    return OsmCandidate(
        osm_type=osm_type,
        osm_id=osm_id,
        name=name.strip()[:200] if isinstance(name, str) and name.strip() else None,
        kind_guess=kind_guess,
        lat=lat,
        lon=lon,
        address=address[:500] if address else None,
        opening_hours=hours,
    )


async def _query(ql: str) -> list[dict[str, Any]]:
    """POST an Overpass QL query; parse elements. Numbers are parsed as Decimal."""
    ensure_enabled()
    await limiter.wait()
    try:
        async with http_client() as client:
            response = await client.post(OVERPASS_URL, data={"data": ql})
    except httpx.HTTPError as exc:
        raise ApiError(
            502, "overpass_unavailable", "The Overpass API could not be reached."
        ) from exc
    if response.status_code != 200:
        raise ApiError(
            502,
            "overpass_unavailable",
            "The Overpass API rejected the request.",
            {"status": response.status_code},
        )
    try:
        body = response.json(parse_float=Decimal)
    except ValueError as exc:
        raise ApiError(
            502, "overpass_invalid_response", "The Overpass API returned unreadable data."
        ) from exc
    elements = body.get("elements") if isinstance(body, dict) else None
    if not isinstance(elements, list):
        raise ApiError(502, "overpass_invalid_response", "The Overpass API returned no elements.")
    return [e for e in elements if isinstance(e, dict)]


def around_query(lat: Decimal, lon: Decimal, radius_m: int) -> str:
    shops = "|".join(SHOP_VALUES)
    around = f"around:{int(radius_m)},{lat},{lon}"
    return (
        "[out:json][timeout:25];("
        f'nwr({around})["shop"~"^({shops})$"];'
        f'nwr({around})["amenity"="marketplace"];'
        ");out center tags;"
    )


def by_id_query(osm_type: str, osm_id: int) -> str:
    if osm_type not in ("node", "way", "relation"):
        raise ValueError("osm_type must be node, way, or relation")
    return f"[out:json][timeout:25];{osm_type}({int(osm_id)});out center tags;"


def cache_key(home_base_id: Any, lat: Decimal, lon: Decimal, radius_m: int) -> str:
    return f"{home_base_id}:{lat}:{lon}:{int(radius_m)}"


async def candidates_around(
    home_base_id: Any, lat: Decimal, lon: Decimal, radius_m: int
) -> list[OsmCandidate]:
    """Cached candidate list for a home base and radius."""
    ensure_enabled()
    key = cache_key(home_base_id, lat, lon, radius_m)
    cached = cache.get(key)
    if cached is not None:
        return cached
    elements = await _query(around_query(lat, lon, radius_m))
    items = [c for c in (_candidate(e) for e in elements) if c is not None]
    items.sort(key=lambda c: (c.name is None, c.name or "", c.osm_type, c.osm_id))
    cache.put(key, items)
    return items


async def fetch_by_id(osm_type: str, osm_id: int) -> OsmCandidate | None:
    """Re-fetch one object's tags. Never cached: a refresh wants current data."""
    ensure_enabled()
    elements = await _query(by_id_query(osm_type, osm_id))
    for element in elements:
        candidate = _candidate(element)
        if candidate and candidate.osm_type == osm_type and candidate.osm_id == osm_id:
            return candidate
    return None
