"""Render a stored receipt: grayscale for OCR, colour for a model or a person.

HEIC and PDF are the two formats people actually have — the iPhone camera
default and the emailed receipt — and neither is bytes Tesseract understands.
Converting them here means the accepted-format list in
:mod:`app.ingest.formats` can stay the list of formats that work, rather than
the list of formats that upload.

Tesseract gets grayscale PNGs from the converters below. A vision model and the
review screen get :func:`render_page`, which keeps colour (EV10) and turns the
page upright with :func:`orient`, the one orientation step they share, so a box
a model draws on its image lands on the same text in the image a person sees.

The result is a temporary file or bytes. It is a derivation, rebuildable from the stored
document at any time (non-negotiable 5), so it never goes into the receipts
store beside the immutable original.

Both inputs are untrusted (non-negotiable 7): they are decoded by a library,
never executed, and the work is bounded — one page, a capped pixel count — so a
crafted file cannot turn a stage into an unbounded render.

The decoders are imported inside their functions. Both pull in native libraries
of some size, and only the worker ever rasterises; the API process imports this
module through the OCR adapters and should not pay for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.ingest.errors import StageFailure

if TYPE_CHECKING:
    from PIL import Image

log = get_logger(__name__)

# 200 dpi is what a scanned receipt needs for Tesseract to read 8pt monospace
# reliably; below ~150 the decimal points start to go. pdfium works in scale
# factors against PDF's 72 dpi user space.
PDF_RENDER_DPI = 200
PDF_RENDER_SCALE = PDF_RENDER_DPI / 72

# A ceiling on what one receipt may cost to rasterise. A till receipt at 200 dpi
# is a few megapixels; 80 is far above any real one and far below a decompression
# bomb. Checked before rendering, from the declared page size, not after.
MAX_MEGAPIXELS = 80


def _guard_megapixels(width: float, height: float, *, code: str) -> None:
    if width * height > MAX_MEGAPIXELS * 1_000_000:
        raise StageFailure(code=code, detail=f"{int(width)}x{int(height)}")


def pdf_to_png(source: Path, target: Path) -> Path:
    """Render page 1 of a PDF. A till receipt does not run to two pages."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(source)
    except pypdfium2.PdfiumError as exc:
        raise StageFailure(code="pdf_unreadable", detail=type(exc).__name__) from None
    try:
        if len(document) == 0:
            raise StageFailure(code="pdf_unreadable", detail="no_pages")
        page = document[0]
        _guard_megapixels(
            page.get_width() * PDF_RENDER_SCALE,
            page.get_height() * PDF_RENDER_SCALE,
            code="pdf_too_large",
        )
        page.render(scale=PDF_RENDER_SCALE).to_pil().convert("L").save(target, format="PNG")
    finally:
        document.close()
    return target


def heif_to_png(source: Path, target: Path) -> Path:
    """Transcode a HEIC/HEIF photograph."""
    import pillow_heif
    from PIL import Image, UnidentifiedImageError

    pillow_heif.register_heif_opener()
    try:
        with Image.open(source) as image:
            _guard_megapixels(image.width, image.height, code="heif_too_large")
            image.convert("L").save(target, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise StageFailure(code="heif_unreadable", detail=type(exc).__name__) from None
    return target


CONVERTERS = {"pdf": pdf_to_png, "heif": heif_to_png}


def to_png(converter: str, source: Path, target: Path) -> Path:
    """Run the named converter. Unknown names are a programming error, not input."""
    convert = CONVERTERS[converter]
    result = convert(source, target)
    log.info(
        "rasterised for OCR",
        extra={"converter": converter, "bytes": result.stat().st_size},
    )
    return result


# --- colour renders for a vision model and for review -------------------------------

# The long side a vision model is shown, until Phase 0 of the reading benchmark
# picks one. A 2000-pixel receipt costs about 1,600 prompt tokens on qwen3-vl.
VISION_LONG_SIDE = 2000


def orient(image: Image.Image) -> Image.Image:
    """The image turned the way its EXIF orientation says it is seen.

    A phone writes the sensor's pixels and a tag saying how to turn them. HEIC
    arrives already turned (pillow-heif applies the rotation and resets the
    tag), so this is a no-op there and the step can be applied to every format.
    """
    from PIL import ImageOps

    return ImageOps.exif_transpose(image)


@dataclass
class RenderedPage:
    """Page 1 of a receipt, upright and in colour. ``pages_truncated`` says a
    PDF had more pages, which were not rendered (EV7)."""

    image: Image.Image
    pages_truncated: bool = False


def render_page(source: Path, converter: str | None) -> RenderedPage:
    """Page 1 of a stored receipt as an upright RGB image.

    ``converter`` is the format's converter name (``pdf``, ``heif``) or None for
    an image a browser shows as it is. Every path holds the same pixel ceiling
    as the grayscale converters.
    """
    if converter == "pdf":
        return _render_pdf_page(source)
    from PIL import Image, UnidentifiedImageError

    if converter == "heif":
        import pillow_heif

        pillow_heif.register_heif_opener()
    code = "heif" if converter == "heif" else "image"
    try:
        with Image.open(source) as opened:
            # Checked from the header before any pixels are decoded: a small
            # file can declare an enormous image.
            _guard_megapixels(opened.width, opened.height, code=f"{code}_too_large")
            image = orient(opened).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise StageFailure(code=f"{code}_unreadable", detail=type(exc).__name__) from None
    return RenderedPage(image)


def _render_pdf_page(source: Path) -> RenderedPage:
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(source)
    except pypdfium2.PdfiumError as exc:
        raise StageFailure(code="pdf_unreadable", detail=type(exc).__name__) from None
    try:
        if len(document) == 0:
            raise StageFailure(code="pdf_unreadable", detail="no_pages")
        page = document[0]
        _guard_megapixels(
            page.get_width() * PDF_RENDER_SCALE,
            page.get_height() * PDF_RENDER_SCALE,
            code="pdf_too_large",
        )
        image = page.render(scale=PDF_RENDER_SCALE).to_pil().convert("RGB")
        return RenderedPage(image, pages_truncated=len(document) > 1)
    finally:
        document.close()


@dataclass(frozen=True)
class VisionImage:
    """What a vision model is shown: PNG bytes and the pixel size they encode."""

    png: bytes
    width: int
    height: int
    pages_truncated: bool


def vision_png(
    source: Path, converter: str | None, long_side: int = VISION_LONG_SIDE
) -> VisionImage:
    """Page 1, upright, in colour, its long side capped at ``long_side``."""
    import io

    from PIL import Image

    page = render_page(source, converter)
    image = page.image
    scale = long_side / max(image.size)
    if scale < 1:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return VisionImage(out.getvalue(), image.width, image.height, page.pages_truncated)
