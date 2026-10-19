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
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.services import phone as phones
from app.services.opening_hours import validate_hours

log = get_logger(__name__)

USER_AGENT = "kitchen-erp/0.1 (self-hosted household kitchen system; Overpass client)"
MIN_INTERVAL_SECONDS = 2.0
CACHE_TTL = timedelta(hours=24)
# A public server queues a query before running it, and its [timeout:25] counts only
# the run: answers took 31-33 s on 2026-10-19, so 30 s gave up on answers on their way.
REQUEST_TIMEOUT_SECONDS = 75.0
# Everything one search may spend across retries and servers, inside the web
# proxy's 120 s; an attempt never starts with less than MIN_ATTEMPT_SECONDS left.
QUERY_BUDGET_SECONDS = 100.0
MIN_ATTEMPT_SECONDS = 10.0
# An unreachable server says so in seconds, not after a whole answer's budget.
CONNECT_TIMEOUT_SECONDS = 5.0
# A public Overpass server that is at capacity answers at once (429, or a 502/503/504
# "all query workers are busy"); it is asked once more after a pause, then the next.
BUSY_STATUSES = frozenset({429, 502, 503, 504})
BUSY_RETRIES = 1
BUSY_RETRY_SECONDS = 5.0

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
    # Defaults keep cache files written before these were captured readable.
    phone: str | None = None
    website: str | None = None
    brand: str | None = None
    brand_wikidata: str | None = None

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


def http_client(read_seconds: float = REQUEST_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(read_seconds, connect=CONNECT_TIMEOUT_SECONDS),
        transport=http_transport,
    )


def _address(tags: dict[str, Any]) -> str | None:
    number, street, city, postcode = (tags.get(k) for k in _ADDRESS_KEYS)
    line1 = " ".join(p for p in (number, street) if p)
    line2 = " ".join(p for p in (city, postcode) if p)
    joined = ", ".join(p for p in (line1, line2) if p)
    return joined or None


def _website(tags: dict[str, Any]) -> str | None:
    for key in ("website", "contact:website"):
        value = tags.get(key)
        if isinstance(value, str):
            value = value.strip()
            parts = urlsplit(value)
            if parts.scheme.lower() in ("http", "https") and parts.hostname and len(value) <= 500:
                return value
    return None


def _brand(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:200]


def _wikidata(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"Q[0-9]+", value.strip()):
        return value.strip()
    return None


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
        phone=phones.from_tag(tags.get("phone")) or phones.from_tag(tags.get("contact:phone")),
        website=_website(tags),
        brand=_brand(tags.get("brand")),
        brand_wikidata=_wikidata(tags.get("brand:wikidata")),
    )


def endpoints() -> list[str]:
    """The Overpass servers to ask, in order (``OVERPASS_URLS``)."""
    return [u.strip() for u in get_settings().overpass_urls.split(",") if u.strip()]


# Tests set this to a no-op; production waits between asking a busy server twice.
pause: Callable[[float], Awaitable[None]] = asyncio.sleep


async def _post(url: str, ql: str, read_seconds: float) -> httpx.Response | str:
    """The server's response, or why there was none: "busy" (no answer in time)
    or "unreachable" (no connection). Failures are logged with their cause."""
    started = time.monotonic()
    try:
        async with http_client(read_seconds) as client:
            return await client.post(url, data={"data": ql})
    except httpx.HTTPError as exc:
        # A connection that opened but never answered is a busy server.
        why = "busy" if isinstance(exc, httpx.ReadTimeout | httpx.PoolTimeout) else "unreachable"
        log.warning(
            "overpass request failed",
            extra={
                "host": urlsplit(url).hostname,
                "error": type(exc).__name__,
                "seconds": round(time.monotonic() - started, 1),
            },
        )
        return why


async def _query(ql: str) -> list[dict[str, Any]]:
    """POST an Overpass QL query to each server in turn; parse elements as Decimal."""
    ensure_enabled()
    reasons: list[str] = []
    deadline = time.monotonic() + QUERY_BUDGET_SECONDS
    for url in endpoints():
        for attempt in range(BUSY_RETRIES + 1):
            await limiter.wait()
            left = deadline - time.monotonic()
            if left < MIN_ATTEMPT_SECONDS:
                break
            got = await _post(url, ql, min(REQUEST_TIMEOUT_SECONDS, left))
            if isinstance(got, str):
                reasons.append(got)
                break  # a silent or unreachable server is not asked again
            if got.status_code == 200:
                return _elements(got)
            log.warning(
                "overpass refused the request",
                extra={"host": urlsplit(url).hostname, "status": got.status_code},
            )
            if got.status_code not in BUSY_STATUSES:
                raise ApiError(
                    502,
                    "overpass_rejected",
                    f"OpenStreetMap's place search refused the request (HTTP {got.status_code}).",
                    {"status": got.status_code},
                )
            reasons.append("busy")
            if attempt < BUSY_RETRIES:
                await pause(BUSY_RETRY_SECONDS)
    if "busy" in reasons:
        raise ApiError(
            503,
            "overpass_busy",
            "OpenStreetMap's place search is busy right now. Try again in a minute.",
            {"servers": len(endpoints())},
        )
    raise ApiError(
        502,
        "overpass_unavailable",
        "This server could not reach OpenStreetMap's place search. Check its internet connection.",
    )


def _elements(response: httpx.Response) -> list[dict[str, Any]]:
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


def cache_key(anchor_id: Any, lat: Decimal, lon: Decimal, radius_m: int) -> str:
    return f"{anchor_id}:{lat}:{lon}:{int(radius_m)}"


async def candidates_around(
    anchor_id: Any, lat: Decimal, lon: Decimal, radius_m: int
) -> list[OsmCandidate]:
    """Cached candidate list around a kitchen or a location's pin, for a radius."""
    ensure_enabled()
    key = cache_key(anchor_id, lat, lon, radius_m)
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
