"""Render a stored receipt to a PNG that an image OCR adapter can read.

HEIC and PDF are the two formats people actually have — the iPhone camera
default and the emailed receipt — and neither is bytes Tesseract understands.
Converting them here means the accepted-format list in
:mod:`app.ingest.formats` can stay the list of formats that work, rather than
the list of formats that upload.

The result is a temporary file. It is a derivation, rebuildable from the stored
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

from pathlib import Path

from app.core.logging import get_logger
from app.ingest.errors import StageFailure

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
