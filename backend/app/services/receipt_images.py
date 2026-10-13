"""The receipt as a person sees it: the stored original, or an image rendered from it.

A browser shows JPEG, PNG and WebP as they are, so those are served untouched. A
PDF, and a HEIC photograph outside Safari, it cannot show, so the review screen
was blank for them (#30). Those are rendered in colour, page 1 only,
with the render a vision model is shown (``raster.render_page``), so both see
the page the same way up.

A rendering is a derivation, rebuildable from the immutable original at any
time (non-negotiable 5), so it is made on request and never stored. A thumbnail
(`width`) is the same, scaled down, for telling receipts apart in a list (#28).
Every rendering is turned upright first; a scaled photo taken sideways used to
come out sideways, because the browser's own EXIF handling never saw it.

A rendering of a PDF page is PNG, which keeps printed text sharp. Anything else
is a photograph and is sent as JPEG: as PNG, a receipt photo scaled to the
review's 1,024 pixels was about 4 MB, and each list thumbnail about 450 KB
(QA 2026-10-12).

The stored bytes are untrusted input (non-negotiable 7): they are decoded by a
library, never executed, and the converters cap the pixel count before they
render.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.core.errors import ApiError
from app.ingest.errors import StageFailure
from app.ingest.formats import BY_MIME
from app.ingest.raster import render_page

THUMB_MIN = 32
THUMB_MAX = 1024


JPEG_QUALITY = 85


@dataclass(frozen=True)
class Displayable:
    """Either a file to send as it is, or rendered image bytes."""

    path: Path | None
    content: bytes | None
    media_type: str


def media_type_for(converter: str | None) -> str:
    """PNG for a rendered PDF page, JPEG for a photograph."""
    return "image/png" if converter == "pdf" else "image/jpeg"


def _render(converter: str | None, source: Path, width: int | None) -> bytes:
    """Image bytes for `source`: upright, in colour, scaled to `width` if given."""
    from PIL import Image

    image = render_page(source, converter).image
    if width is not None and image.width > width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if media_type_for(converter) == "image/png":
        image.save(out, format="PNG")
    else:
        if image.mode != "RGB":
            # JPEG has no transparency: a see-through pixel shows as the paper.
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.getchannel("A") if "A" in image.getbands() else None)
            image = flat
        image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


async def displayable(source: Path, mime: str, width: int | None = None) -> Displayable:
    """What to send for a stored receipt, so every accepted format can be seen."""
    fmt = BY_MIME.get(mime)
    if fmt is None:
        raise ApiError(415, "unsupported_media", "This receipt's format cannot be shown.")
    if fmt.converter is None and width is None:
        return Displayable(path=source, content=None, media_type=mime)
    try:
        # Decoding is CPU-bound; keep it off the event loop.
        content = await anyio.to_thread.run_sync(_render, fmt.converter, source, width)
    except StageFailure as exc:
        raise ApiError(
            422,
            "image_unreadable",
            "The stored receipt could not be rendered.",
            {"reason": exc.code},
        ) from None
    return Displayable(path=None, content=content, media_type=media_type_for(fmt.converter))
