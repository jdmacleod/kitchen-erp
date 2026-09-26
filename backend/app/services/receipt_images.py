"""The receipt as a person sees it: the stored original, or a PNG rendered from it.

A browser shows JPEG, PNG and WebP as they are, so those are served untouched. A
PDF, and a HEIC photograph outside Safari, it cannot show, so the review screen
was blank for them (#30). Those are rendered to PNG with the converters the OCR
stage already uses, page 1 only and bounded the same way.

A rendering is a derivation, rebuildable from the immutable original at any
time (non-negotiable 5), so it is made on request and never stored. A thumbnail
(`width`) is the same, scaled down, for telling receipts apart in a list (#28).

The stored bytes are untrusted input (non-negotiable 7): they are decoded by a
library, never executed, and the converters cap the pixel count before they
render.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.core.errors import ApiError
from app.ingest.errors import StageFailure
from app.ingest.formats import BY_MIME
from app.ingest.raster import to_png

THUMB_MIN = 32
THUMB_MAX = 1024


@dataclass(frozen=True)
class Displayable:
    """Either a file to send as it is, or rendered PNG bytes."""

    path: Path | None
    content: bytes | None
    media_type: str


def _render(converter: str | None, source: Path, width: int | None) -> bytes:
    """PNG bytes for `source`, converted if it needs it and scaled to `width` if given."""
    from PIL import Image, UnidentifiedImageError

    with tempfile.TemporaryDirectory(prefix="kerp-display-") as tmp:
        path = to_png(converter, source, Path(tmp) / "page.png") if converter else source
        try:
            with Image.open(path) as opened:
                shown = opened
                if width is not None and opened.width > width:
                    height = max(1, round(opened.height * width / opened.width))
                    shown = opened.resize((width, height))
                out = io.BytesIO()
                shown.save(out, format="PNG")
                return out.getvalue()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise StageFailure(code="image_unreadable", detail=type(exc).__name__) from None


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
    return Displayable(path=None, content=content, media_type="image/png")
