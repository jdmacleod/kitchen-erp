"""The receipt formats this deployment accepts, and how each one reaches OCR.

One table, in one module, because the two halves of this contract used to live
apart and drifted: the upload endpoint decided what it accepted and the OCR
adapter decided what it could read, and a format in the first list but not the
second was accepted at the door and then failed on a retry loop with a code the
operator could not act on (issue #13). Everything that needs to know about a
format — the stored extension, the message the 415 carries, whether Tesseract
can read the bytes as they arrived — reads it from here.

Adding a format is one row plus, if it is not directly readable, a converter in
:mod:`app.ingest.raster`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceiptFormat:
    mime: str
    extension: str
    label: str
    """What a person calls it, for the message on a rejected upload."""
    converter: str | None = None
    """None when Tesseract reads the stored file as-is; otherwise the name of a
    converter in :mod:`app.ingest.raster` that renders it to PNG first."""


FORMATS: tuple[ReceiptFormat, ...] = (
    ReceiptFormat("image/jpeg", "jpg", "JPEG"),
    ReceiptFormat("image/png", "png", "PNG"),
    ReceiptFormat("image/webp", "webp", "WebP"),
    # The iPhone default, and photographing a receipt with a phone is the capture
    # path this feature exists for, so it is not optional.
    ReceiptFormat("image/heic", "heic", "HEIC", converter="heif"),
    # An emailed or downloaded receipt is ordinarily a PDF. Only the first page
    # is rendered; a till receipt does not run to two.
    ReceiptFormat("application/pdf", "pdf", "PDF", converter="pdf"),
)

BY_MIME: dict[str, ReceiptFormat] = {fmt.mime: fmt for fmt in FORMATS}
EXTENSIONS: dict[str, str] = {fmt.mime: fmt.extension for fmt in FORMATS}


def accepted_labels() -> str:
    """The human list for a rejection message: "JPEG, PNG, WebP, HEIC and PDF"."""
    labels = [fmt.label for fmt in FORMATS]
    return f"{', '.join(labels[:-1])} and {labels[-1]}" if len(labels) > 1 else labels[0]
