"""Remove what a receipt photo says about where and when it was taken (#221).

A phone photo carries EXIF (camera, time, often GPS), XMP and sometimes IPTC or
text chunks. A receipt photographed at home would keep the home's coordinates.
This removes all of it before the photo is stored.

It works on the file's structure, never on its pixels, so the stored photo is the
same picture, bit for bit, as the one uploaded: the reading sees exactly what it
saw before. The one thing kept is the orientation, written back as a minimal
EXIF block holding that single tag, so a photo taken sideways still turns upright
in every viewer and in the reading (`raster.orient`).

- JPEG: drops APP1 (EXIF, XMP), APP13 (IPTC), COM and every other APPn except
  JFIF (APP0), an ICC colour profile (APP2) and Adobe's colour marker (APP14),
  and anything after the main image's end (multi-picture extras and gain maps).
- PNG: drops eXIf, tEXt, zTXt, iTXt and tIME, and anything after IEND.
- WebP: drops the EXIF and XMP chunks and clears their flags in VP8X.
- HEIC: overwrites the Exif and XMP items' bytes in place (Exif with an empty
  EXIF block, XMP with spaces). Orientation in HEIC lives in the irot and imir
  properties, which are kept.
- PDF: stored as uploaded. Removing a PDF's document information safely needs a
  PDF writer the project does not have; a phone scan's PDF carries no GPS.

When a file cannot be taken apart as expected, or still shows metadata after
stripping, it is re-encoded as a high-quality JPEG with the orientation applied
to the pixels: the one lossy path, chosen over storing the metadata. A file that
cannot even be decoded is stored as it came only when it shows no sign of
metadata; otherwise it is refused (`Unreadable`).
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)

ORIENTATION = 0x0112
GPS_IFD = 0x8825
EXIF_IFD = 0x8769
FALLBACK_QUALITY = 95


@dataclass(frozen=True)
class Stripped:
    data: bytes
    mime: str
    # True when the file had to be re-encoded rather than taken apart.
    reencoded: bool = False


class _Malformed(Exception):
    """The file is not laid out the way its format says."""


class Unreadable(Exception):
    """A photo that can be neither taken apart nor decoded, and that carries
    metadata: it is refused rather than stored with it."""


# Byte patterns that only metadata carries: an EXIF block, an XMP packet, IPTC.
_METADATA_SIGNS = (b"Exif\x00\x00", b"<x:xmpmeta", b"http://ns.adobe.com/xap/", b"Photoshop 3.0")


def _tiff(orientation: int) -> bytes:
    """A little-endian TIFF header with one IFD holding only the orientation."""
    if orientation == 1:
        return b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 0) + struct.pack("<I", 0)
    entry = struct.pack("<HHIHH", ORIENTATION, 3, 1, orientation, 0)
    return b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1) + entry + struct.pack("<I", 0)


def _orientation(data: bytes) -> int:
    from app.services import media

    try:
        with media._open(data) as image:
            value = image.getexif().get(ORIENTATION, 1)
    except (OSError, ValueError, SyntaxError):
        return 1
    return value if isinstance(value, int) and 1 <= value <= 8 else 1


# --- JPEG ---------------------------------------------------------------------

_KEEP_APP = {0xE0, 0xEE}  # JFIF, Adobe
_STANDALONE = {0x01, *range(0xD0, 0xD8)}


def _jpeg(data: bytes, orientation: int) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise _Malformed("no SOI")
    out = bytearray(b"\xff\xd8")
    if orientation != 1:
        exif = b"Exif\x00\x00" + _tiff(orientation)
        out += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    i, n = 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise _Malformed("expected a marker")
        while i < n and data[i] == 0xFF:  # fill bytes
            i += 1
        if i >= n:
            raise _Malformed("truncated marker")
        marker = data[i]
        i += 1
        if marker == 0xD9:  # EOI: the main image ends; extras after it are dropped
            out += b"\xff\xd9"
            return bytes(out)
        if marker in _STANDALONE:
            out += bytes((0xFF, marker))
            continue
        if i + 2 > n:
            raise _Malformed("truncated length")
        length = struct.unpack(">H", data[i : i + 2])[0]
        end = i + length
        if length < 2 or end > n:
            raise _Malformed("bad segment length")
        segment = data[i - 2 : end]
        body = data[i + 2 : end]
        i = end
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            keep = marker in _KEEP_APP or (marker == 0xE2 and body.startswith(b"ICC_PROFILE\x00"))
            if keep:
                out += segment
            continue
        out += segment
        if marker == 0xDA:  # SOS: entropy-coded data up to the next real marker
            j = i
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= n:
                    raise _Malformed("no end of scan")
                nxt = data[j + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
                    j += 1 if nxt == 0xFF else 2
                    continue
                break
            out += data[i:j]
            i = j
    raise _Malformed("no EOI")


# --- PNG ----------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def _png(data: bytes, orientation: int) -> bytes:
    if not data.startswith(_PNG_SIG):
        raise _Malformed("no PNG signature")
    out = bytearray(_PNG_SIG)
    i, n = len(_PNG_SIG), len(data)
    placed = orientation == 1
    while i < n:
        if i + 8 > n:
            raise _Malformed("truncated chunk")
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind = data[i + 4 : i + 8]
        end = i + 12 + length
        if end > n:
            raise _Malformed("bad chunk length")
        chunk = data[i:end]
        i = end
        if kind == b"IDAT" and not placed:
            out += _png_chunk(b"eXIf", _tiff(orientation))
            placed = True
        if kind in _PNG_DROP:
            continue
        out += chunk
        if kind == b"IEND":
            return bytes(out)
    raise _Malformed("no IEND")


# --- WebP ---------------------------------------------------------------------


def _webp(data: bytes, orientation: int) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise _Malformed("not RIFF WEBP")
    chunks: list[tuple[bytes, bytes]] = []
    i, n = 12, min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    while i < n:
        if i + 8 > n:
            raise _Malformed("truncated chunk")
        kind = data[i : i + 4]
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        end = i + 8 + size
        if end > n:
            raise _Malformed("bad chunk size")
        chunks.append((kind, data[i + 8 : end]))
        i = end + (size & 1)
    kept = [(k, b) for k, b in chunks if k not in (b"EXIF", b"XMP ")]
    has_vp8x = any(k == b"VP8X" for k, _ in kept)
    if has_vp8x:
        exif_flag = 0x08 if orientation != 1 else 0
        kept = [
            (k, bytes(((b[0] & ~0x0C) | exif_flag,)) + b[1:]) if k == b"VP8X" else (k, b)
            for k, b in kept
        ]
        if orientation != 1:
            kept.append((b"EXIF", _tiff(orientation)))
    body = bytearray(b"WEBP")
    for kind, chunk in kept:
        body += kind + struct.pack("<I", len(chunk)) + chunk
        if len(chunk) & 1:
            body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)


# --- HEIC ---------------------------------------------------------------------


def _boxes(data: bytes, start: int, end: int) -> list[tuple[bytes, int, int]]:
    """(type, payload start, payload end) for each box in data[start:end]."""
    out = []
    i = start
    while i < end:
        if i + 8 > end:
            raise _Malformed("truncated box")
        size = struct.unpack(">I", data[i : i + 4])[0]
        kind = data[i + 4 : i + 8]
        header = 8
        if size == 1:
            if i + 16 > end:
                raise _Malformed("truncated large box")
            size = struct.unpack(">Q", data[i + 8 : i + 16])[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header or i + size > end:
            raise _Malformed("bad box size")
        out.append((kind, i + header, i + size))
        i += size
    return out


def _uint(data: bytes, pos: int, size: int) -> int:
    if size == 0:
        return 0
    return int.from_bytes(data[pos : pos + size], "big")


def _metadata_items(data: bytes, meta_start: int, meta_end: int) -> dict[int, str]:
    """item_ID -> "exif" or "xmp" for the meta box's metadata items."""
    found: dict[int, str] = {}
    for kind, start, end in _boxes(data, meta_start, meta_end):
        if kind != b"iinf":
            continue
        version = data[start]
        pos = start + 4
        count_size = 2 if version == 0 else 4
        pos += count_size
        for entry, e_start, e_end in _boxes(data, pos, end):
            if entry != b"infe":
                continue
            e_version = data[e_start]
            if e_version < 2:
                continue
            p = e_start + 4
            id_size = 2 if e_version == 2 else 4
            item_id = _uint(data, p, id_size)
            p += id_size + 2  # protection index
            item_type = data[p : p + 4]
            p += 4
            name_end = data.index(b"\x00", p, e_end)
            p = name_end + 1
            if item_type == b"Exif":
                found[item_id] = "exif"
            elif item_type == b"mime":
                content_type = data[p : data.index(b"\x00", p, e_end)]
                if b"xml" in content_type.lower() or b"rdf" in content_type.lower():
                    found[item_id] = "xmp"
    return found


def _extents(
    data: bytes, meta_start: int, meta_end: int, wanted: dict[int, str]
) -> list[tuple[int, int, str]]:
    """(absolute offset, length, kind) of each wanted item's bytes."""
    idat = next(((s, e) for k, s, e in _boxes(data, meta_start, meta_end) if k == b"idat"), None)
    out = []
    for kind, start, _end in _boxes(data, meta_start, meta_end):
        if kind != b"iloc":
            continue
        version = data[start]
        p = start + 4
        offset_size, length_size = data[p] >> 4, data[p] & 0x0F
        base_size = data[p + 1] >> 4
        index_size = data[p + 1] & 0x0F if version in (1, 2) else 0
        p += 2
        count_size = 2 if version < 2 else 4
        count = _uint(data, p, count_size)
        p += count_size
        for _ in range(count):
            id_size = 2 if version < 2 else 4
            item_id = _uint(data, p, id_size)
            p += id_size
            method = 0
            if version in (1, 2):
                method = _uint(data, p, 2) & 0x0F
                p += 2
            p += 2  # data reference index
            base = _uint(data, p, base_size)
            p += base_size
            extent_count = _uint(data, p, 2)
            p += 2
            for _ in range(extent_count):
                p += index_size
                offset = _uint(data, p, offset_size)
                p += offset_size
                length = _uint(data, p, length_size)
                p += length_size
                if item_id not in wanted:
                    continue
                if method == 0:
                    absolute = base + offset
                elif method == 1 and idat is not None:
                    absolute = idat[0] + base + offset
                else:
                    raise _Malformed("unsupported item construction")
                if length == 0 or absolute + length > len(data):
                    raise _Malformed("item outside the file")
                out.append((absolute, length, wanted[item_id]))
    return out


def _heic(data: bytes) -> bytes:
    top = _boxes(data, 0, len(data))
    meta = next(((s, e) for k, s, e in top if k == b"meta"), None)
    if meta is None:
        raise _Malformed("no meta box")
    meta_start = meta[0] + 4  # full box: version and flags
    wanted = _metadata_items(data, meta_start, meta[1])
    out = bytearray(data)
    for offset, length, kind in _extents(data, meta_start, meta[1], wanted):
        if kind == "xmp":
            out[offset : offset + length] = b" " * length
        else:
            # An Exif item is a 4-byte offset to the TIFF header, then the EXIF data.
            empty = struct.pack(">I", 0) + _tiff(1)
            out[offset : offset + length] = empty[:length].ljust(length, b"\x00")
    return bytes(out)


# --- checking and the fallback ------------------------------------------------


def _leaks(data: bytes) -> bool:
    """True when the photo still carries anything but its orientation."""
    from app.services import media

    try:
        with media._open(data) as image:
            exif = image.getexif()
            if set(exif.keys()) - {ORIENTATION}:
                return True
            for key, value in image.info.items():
                if key.lower() not in {"xmp", "xml:com.adobe.xmp", "comment", "photoshop", "iptc"}:
                    continue
                # A blanked HEIC XMP item still reads back, as spaces.
                raw = value.encode() if isinstance(value, str) else value
                if isinstance(raw, bytes) and not raw.strip(b" \x00"):
                    continue
                if value:
                    return True
            text = getattr(image, "text", None)
            if text:
                return True
    except (OSError, ValueError, SyntaxError):
        return True
    return False


def _reencode(data: bytes) -> bytes:
    from PIL import ImageOps

    from app.services import media

    with media._open(data) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    clean = media._clean(image)
    out = io.BytesIO()
    clean.save(out, format="JPEG", quality=FALLBACK_QUALITY, subsampling=0)
    return out.getvalue()


_STRIPPERS = {"image/jpeg": _jpeg, "image/png": _png, "image/webp": _webp}


def strip(data: bytes, mime: str) -> Stripped:
    """The photo without its metadata; a PDF, or any other type, as it came."""
    if mime == "application/pdf" or mime not in (*_STRIPPERS, "image/heic"):
        return Stripped(data, mime)
    try:
        heic = mime == "image/heic"
        result = _heic(data) if heic else _STRIPPERS[mime](data, _orientation(data))
        if not _leaks(result):
            return Stripped(result, mime)
        reason = "metadata left"
    except (_Malformed, ValueError, IndexError, struct.error) as exc:
        reason = type(exc).__name__
    try:
        reencoded = _reencode(data)
    except (OSError, ValueError, SyntaxError):
        if any(sign in data for sign in _METADATA_SIGNS):
            raise Unreadable from None
        # Undecodable and carrying nothing to remove: the reading will say so.
        return Stripped(data, mime)
    log.warning("receipt photo re-encoded to remove its metadata", extra={"reason": reason})
    return Stripped(reencoded, "image/jpeg", reencoded=True)
