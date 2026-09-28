"""A synthetic basemap extract for place-search tests (#62).

Builds a small PMTiles file at test time, with point features in the `places`
and `pois` layers the way Protomaps basemaps store them. Every name is
invented and every position is in the synthetic box from SECURITY.md (latitude
33 to 34, longitude -121 to -120: open ocean).
"""

from __future__ import annotations

import gzip
import math
from collections import defaultdict
from pathlib import Path

from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

EXTENT = 4096


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, wire: int, payload: bytes | int) -> bytes:
    key = _varint((number << 3) | wire)
    if wire == 0:
        return key + _varint(payload)  # type: ignore[arg-type]
    return key + _varint(len(payload)) + payload  # type: ignore[arg-type]


def _zigzag(n: int) -> int:
    return (n << 1) ^ (n >> 31)


def _layer(name: str, features: list[tuple[int, int, dict[str, str]]]) -> bytes:
    keys: list[str] = []
    values: list[str] = []
    body = _field(15, 0, 2) + _field(1, 2, name.encode())
    for x, y, props in features:
        tags: list[int] = []
        for k, v in props.items():
            if k not in keys:
                keys.append(k)
            if v not in values:
                values.append(v)
            tags += [keys.index(k), values.index(v)]
        geometry = [(1 & 7) | (1 << 3), _zigzag(x), _zigzag(y)]
        feature = (
            _field(2, 2, b"".join(_varint(t) for t in tags))
            + _field(3, 0, 1)
            + _field(4, 2, b"".join(_varint(g) for g in geometry))
        )
        body += _field(2, 2, feature)
    for k in keys:
        body += _field(3, 2, k.encode())
    for v in values:
        body += _field(4, 2, _field(1, 2, v.encode()))
    return body + _field(5, 0, EXTENT)


def _position(lat: float, lon: float, z: int) -> tuple[int, int, int, int]:
    n = 2**z
    fx = (lon + 180.0) / 360.0 * n
    fy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    x, y = int(fx), int(fy)
    return x, y, int((fx - x) * EXTENT), int((fy - y) * EXTENT)


def write_extract(
    path: Path, features: list[tuple[str, float, float, dict[str, str], tuple[int, ...]]]
) -> Path:
    """features: (layer, lat, lon, properties, zooms it appears at)."""
    tiles: dict[tuple[int, int, int], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for layer, lat, lon, props, zooms in features:
        for z in zooms:
            x, y, px, py = _position(lat, lon, z)
            tiles[(z, x, y)][layer].append((px, py, props))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        writer = Writer(f)
        for (z, x, y), layers in sorted(tiles.items(), key=lambda t: zxy_to_tileid(*t[0])):
            tile = b"".join(_field(3, 2, _layer(name, feats)) for name, feats in layers.items())
            writer.write_tile(zxy_to_tileid(z, x, y), gzip.compress(tile))
        writer.finalize(
            {
                "tile_type": TileType.MVT,
                "tile_compression": Compression.GZIP,
                "min_lon_e7": int(-121.0 * 1e7),
                "min_lat_e7": int(33.0 * 1e7),
                "max_lon_e7": int(-120.0 * 1e7),
                "max_lat_e7": int(34.0 * 1e7),
                "center_zoom": 8,
                "center_lon_e7": int(-120.5 * 1e7),
                "center_lat_e7": int(33.5 * 1e7),
            },
            {"vector_layers": [{"id": "places"}, {"id": "pois"}]},
        )
    return path
