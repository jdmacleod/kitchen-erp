"""The named points in a Mapbox Vector Tile, and nothing else.

Place search (#62) needs the name, kind and position of the points in two
layers of the basemap extract. A full MVT library brings shapely and numpy for
geometry this never touches, so this reads the protobuf directly: layers,
their key and value tables, and each point feature's tags and first MoveTo.
Lines and polygons are skipped. The tile comes from the deployment's own
extract, but it is parsed defensively all the same: a truncated or malformed
tile raises ValueError rather than looping or reading out of bounds.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

_POINT = 1


@dataclass(frozen=True)
class TilePoint:
    layer: str
    x: int  # position within the tile, 0..extent
    y: int
    extent: int
    properties: dict[str, str | int | float | bool]


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(field number, wire type, value) for every field in a message."""
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
            yield field, wire, value
        elif wire == 2:
            length, pos = _varint(buf, pos)
            if pos + length > len(buf):
                raise ValueError("truncated field")
            yield field, wire, buf[pos : pos + length]
            pos += length
        elif wire == 1:
            if pos + 8 > len(buf):
                raise ValueError("truncated field")
            yield field, wire, buf[pos : pos + 8]
            pos += 8
        elif wire == 5:
            if pos + 4 > len(buf):
                raise ValueError("truncated field")
            yield field, wire, buf[pos : pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")


def _packed(buf: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(buf):
        value, pos = _varint(buf, pos)
        out.append(value)
    return out


def _zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _value(buf: bytes) -> str | int | float | bool | None:
    for field, _wire, raw in _fields(buf):
        if field == 1 and isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        if field == 2 and isinstance(raw, bytes):
            return struct.unpack("<f", raw)[0]
        if field == 3 and isinstance(raw, bytes):
            return struct.unpack("<d", raw)[0]
        if field in (4, 5) and isinstance(raw, int):
            return raw
        if field == 6 and isinstance(raw, int):
            return _zigzag(raw)
        if field == 7 and isinstance(raw, int):
            return bool(raw)
    return None


def points(tile: bytes, layers: set[str]) -> list[TilePoint]:
    """Every point feature in the named layers of an uncompressed tile."""
    found: list[TilePoint] = []
    for field, _wire, layer_buf in _fields(tile):
        if field != 3 or not isinstance(layer_buf, bytes):
            continue
        name, extent = "", 4096
        keys: list[str] = []
        values: list[str | int | float | bool | None] = []
        features: list[bytes] = []
        for lf, _lw, raw in _fields(layer_buf):
            if lf == 1 and isinstance(raw, bytes):
                name = raw.decode("utf-8", "replace")
            elif lf == 2 and isinstance(raw, bytes):
                features.append(raw)
            elif lf == 3 and isinstance(raw, bytes):
                keys.append(raw.decode("utf-8", "replace"))
            elif lf == 4 and isinstance(raw, bytes):
                values.append(_value(raw))
            elif lf == 5 and isinstance(raw, int):
                extent = raw
        if name not in layers:
            continue
        for feature in features:
            tags: list[int] = []
            geometry: list[int] = []
            kind = 0
            for ff, _fw, raw in _fields(feature):
                if ff == 2 and isinstance(raw, bytes):
                    tags = _packed(raw)
                elif ff == 3 and isinstance(raw, int):
                    kind = raw
                elif ff == 4 and isinstance(raw, bytes):
                    geometry = _packed(raw)
            if kind != _POINT or len(geometry) < 3 or geometry[0] & 7 != 1:
                continue
            props: dict[str, str | int | float | bool] = {}
            for i in range(0, len(tags) - 1, 2):
                k, v = tags[i], tags[i + 1]
                if k < len(keys) and v < len(values) and values[v] is not None:
                    props[keys[k]] = values[v]  # type: ignore[assignment]
            found.append(TilePoint(name, _zigzag(geometry[1]), _zigzag(geometry[2]), extent, props))
    return found
