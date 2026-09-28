"""Find a place by name in the deployment's own basemap extract (#62).

With the map unlabelled and Nominatim off by default, putting a real shop on
the map meant finding it in another map application and copying coordinates.
This reads the PMTiles extract the map already draws, so nothing leaves the
deployment (non-negotiable 9):

- towns and cities from the extract's low-zoom tiles, across the whole extract,
  read once and kept;
- neighbourhoods from z12 tiles within NEIGHBOURHOOD_RADIUS_M of the map centre;
- shops and other points of interest from the most detailed tiles within
  POI_RADIUS_M of the map centre.

The names are OpenStreetMap data, public and already on the map. The query is
untrusted text and is only ever compared with names, never interpreted.
"""

from __future__ import annotations

import gzip
import math
import os
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import Compression

from app.core.errors import ApiError
from app.core.logging import get_logger
from app.services import mvt

log = get_logger(__name__)

TILES_FILE = "basemap.pmtiles"
TOWN_ZOOM = 8
NEIGHBOURHOOD_ZOOM = 12
NEIGHBOURHOOD_RADIUS_M = 10_000
POI_RADIUS_M = 3_000
MAX_RESULTS = 10
TILE_CACHE = 512
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class PlaceHit:
    name: str
    kind: str  # "town", "neighbourhood" or "poi"
    detail: str | None  # the extract's kind_detail, e.g. "supermarket"
    lat: float
    lon: float
    distance_m: float | None


def _fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _tile_of(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat = max(min(lat, 85.0511), -85.0511)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _lonlat(z: int, x: int, y: int, point: mvt.TilePoint) -> tuple[float, float]:
    n = 2**z
    fx = (x + point.x / point.extent) / n
    fy = (y + point.y / point.extent) / n
    lon = fx * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * fy))))
    return lat, lon


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _tiles_around(lat: float, lon: float, radius_m: float, z: int) -> list[tuple[int, int]]:
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    x0, y1 = _tile_of(lat - dlat, lon - dlon, z)
    x1, y0 = _tile_of(lat + dlat, lon + dlon, z)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


class Extract:
    """One opened PMTiles file, with its decoded tiles cached."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = path.open("rb")
        self.reader = Reader(MmapSource(self._file))
        header = self.reader.header()
        self.max_zoom = int(header["max_zoom"])
        self.gzipped = header["tile_compression"] == Compression.GZIP
        self.bounds = (
            header["min_lat_e7"] / 1e7,
            header["min_lon_e7"] / 1e7,
            header["max_lat_e7"] / 1e7,
            header["max_lon_e7"] / 1e7,
        )
        self._tiles: OrderedDict[tuple[int, int, int], list[mvt.TilePoint]] = OrderedDict()
        self._towns: list[PlaceHit] | None = None

    def points(self, z: int, x: int, y: int) -> list[mvt.TilePoint]:
        key = (z, x, y)
        if key in self._tiles:
            self._tiles.move_to_end(key)
            return self._tiles[key]
        found: list[mvt.TilePoint] = []
        data = self.reader.get(z, x, y)
        if data:
            try:
                raw = gzip.decompress(data) if self.gzipped else data
                found = mvt.points(raw, {"places", "pois"})
            except (OSError, ValueError) as exc:  # one bad tile is skipped, not fatal
                log.warning(
                    "unreadable map tile", extra={"tile": f"{z}/{x}/{y}", "error": str(exc)}
                )
        self._tiles[key] = found
        if len(self._tiles) > TILE_CACHE:
            self._tiles.popitem(last=False)
        return found

    def towns(self) -> list[PlaceHit]:
        """Every town and city in the extract, read once from its low-zoom tiles."""
        if self._towns is None:
            min_lat, min_lon, max_lat, max_lon = self.bounds
            z = min(TOWN_ZOOM, self.max_zoom)
            x0, y1 = _tile_of(min_lat, min_lon, z)
            x1, y0 = _tile_of(max_lat, max_lon, z)
            hits = []
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    hits.extend(self._hits(z, x, y, {"locality"}, "town", None))
            self._towns = hits
        return self._towns

    def _hits(
        self, z: int, x: int, y: int, kinds: set[str] | None, as_kind: str, layer: str | None
    ) -> list[PlaceHit]:
        hits = []
        for point in self.points(z, x, y):
            if layer is not None and point.layer != layer:
                continue
            props = point.properties
            if kinds is not None and (point.layer != "places" or props.get("kind") not in kinds):
                continue
            name = props.get("name:en") or props.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            lat, lon = _lonlat(z, x, y, point)
            detail = props.get("kind_detail") or props.get("kind")
            hits.append(
                PlaceHit(
                    name.strip(),
                    as_kind,
                    detail if isinstance(detail, str) else None,
                    lat,
                    lon,
                    None,
                )
            )
        return hits

    def near(self, lat: float, lon: float) -> list[PlaceHit]:
        hits = []
        zn = min(NEIGHBOURHOOD_ZOOM, self.max_zoom)
        for x, y in _tiles_around(lat, lon, NEIGHBOURHOOD_RADIUS_M, zn):
            hits.extend(self._hits(zn, x, y, {"neighbourhood", "macrohood"}, "neighbourhood", None))
        zp = self.max_zoom
        for x, y in _tiles_around(lat, lon, POI_RADIUS_M, zp):
            hits.extend(self._hits(zp, x, y, None, "poi", "pois"))
        return hits


_open: dict[str, tuple[float, Extract]] = {}
# Searches run in a thread pool and share one extract and its tile cache.
_lock = threading.Lock()


def _extract(tiles_path: str) -> Extract:
    path = Path(tiles_path) / TILES_FILE
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        raise ApiError(
            409,
            "tiles_missing",
            "No map extract is installed, so there is nothing to search; see docs/tiles.md.",
        ) from None
    cached = _open.get(str(path))
    if cached is None or cached[0] != mtime:
        cached = (mtime, Extract(path))
        _open[str(path)] = cached
    return cached[1]


def _rank(hit: PlaceHit, query: str) -> tuple[Any, ...]:
    # A name where the query starts a word beats one that only contains it; then
    # the nearest first, since the shop being looked for is usually close by.
    word = f" {query}" in f" {_fold(hit.name)}"
    return (not word, hit.distance_m if hit.distance_m is not None else 0.0, len(hit.name))


def search(tiles_path: str, query: str, lat: float | None, lon: float | None) -> list[PlaceHit]:
    """Places whose name contains every word of the query, best first."""
    with _lock:
        return _search(tiles_path, query, lat, lon)


def _search(tiles_path: str, query: str, lat: float | None, lon: float | None) -> list[PlaceHit]:
    extract = _extract(tiles_path)
    words = _fold(query).split()
    if not words:
        return []
    candidates = list(extract.towns())
    if lat is not None and lon is not None:
        candidates.extend(extract.near(lat, lon))
    seen: set[tuple[str, str, float, float]] = set()
    found: list[PlaceHit] = []
    for hit in candidates:
        folded = _fold(hit.name)
        if not all(w in folded for w in words):
            continue
        key = (
            folded,
            hit.kind,
            round(hit.lat, 3),
            round(hit.lon, 3),
        )  # a label repeated in two tiles
        if key in seen:
            continue
        seen.add(key)
        distance = (
            _distance_m((lat, lon), (hit.lat, hit.lon))
            if lat is not None and lon is not None
            else None
        )
        found.append(PlaceHit(hit.name, hit.kind, hit.detail, hit.lat, hit.lon, distance))
    phrase = " ".join(words)
    found.sort(key=lambda h: _rank(h, phrase))
    return found[:MAX_RESULTS]
