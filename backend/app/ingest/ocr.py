"""OCR adapters. Tried in the order given by ``OCR_ADAPTERS``.

Interface: an object with ``name``, ``version`` and ``async run(document,
image_path) -> str``. An adapter that cannot serve a document (no client text,
no binary, an unsupported format) raises :class:`OcrUnavailable` and the next
one is tried; any other exception is a stage failure.

Vision-model seam: a future adapter (``"vision"``) would implement the same
interface, send the image bytes to a multimodal model through
:mod:`app.ingest.llm`, and be registered in :data:`ADAPTERS`; the stage
machine, settings, and stage output need no change. It is deliberately not
built in Phase 2.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingest.errors import OcrUnavailable, StageFailure
from app.models import ReceiptDocument

log = get_logger(__name__)

TESSERACT_TIMEOUT_SECONDS = 120.0
TESSERACT_MIMES = {"image/jpeg", "image/png", "image/webp"}


class OcrAdapter(Protocol):
    name: str
    version: str

    async def run(self, document: ReceiptDocument, image_path: Path) -> str: ...


class ClientOcrAdapter:
    """Text recognised on the capturing device and sent with the upload."""

    name = "client"
    version = "1"

    async def run(self, document: ReceiptDocument, image_path: Path) -> str:
        text = document.client_ocr_text
        if text is None or not text.strip():
            raise OcrUnavailable("no_client_text")
        return text


class TesseractAdapter:
    """Server-side fallback: the ``tesseract`` binary in the worker image."""

    name = "tesseract"
    _version: str | None = None

    def __init__(self) -> None:
        self.command = get_settings().tesseract_command

    @property
    def version(self) -> str:
        if self._version is None:
            self._version = "unknown"
            if shutil.which(self.command):
                try:
                    import subprocess

                    out = subprocess.run(
                        [self.command, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    first = (out.stdout or out.stderr).strip().splitlines()[0]
                    self._version = first.split()[-1] if first else "unknown"
                except (OSError, subprocess.SubprocessError, IndexError) as exc:
                    # Not fatal: the version is provenance recorded alongside the
                    # OCR text, not something the pipeline depends on, and it is
                    # already "unknown" here. Swallowing it silently was hiding a
                    # broken tesseract install behind a stage that then failed
                    # somewhere less obvious, so it gets logged.
                    log.debug("tesseract version probe failed: %s", exc)
        return self._version

    async def run(self, document: ReceiptDocument, image_path: Path) -> str:
        if shutil.which(self.command) is None:
            raise OcrUnavailable("tesseract_missing")
        if document.mime not in TESSERACT_MIMES:
            raise OcrUnavailable("unsupported_for_tesseract")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command,
                str(image_path),
                "stdout",
                "-l",
                "eng",
                "--psm",
                "6",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=TESSERACT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            raise StageFailure(code="tesseract_timeout") from None
        except OSError:
            raise StageFailure(code="tesseract_spawn_failed") from None
        if proc.returncode != 0:
            raise StageFailure(code="tesseract_failed")
        return stdout.decode("utf-8", errors="replace")


# Registered adapters by name. Tests replace entries with spies.
ADAPTERS: dict[str, Callable[[], OcrAdapter]] = {
    "client": ClientOcrAdapter,
    "tesseract": TesseractAdapter,
}


async def run_ocr(
    document: ReceiptDocument, image_path: Path, adapter_names: list[str] | None = None
) -> tuple[OcrAdapter, str, list[dict[str, str]]]:
    """Try adapters in order; return (adapter, text, skipped) or raise StageFailure."""
    names = adapter_names if adapter_names is not None else get_settings().ocr_adapters
    skipped: list[dict[str, str]] = []
    for name in names:
        factory = ADAPTERS.get(name)
        if factory is None:
            skipped.append({"adapter": name, "reason": "unknown_adapter"})
            continue
        adapter = factory()
        try:
            text = await adapter.run(document, image_path)
        except OcrUnavailable as exc:
            skipped.append({"adapter": name, "reason": str(exc)})
            continue
        if text.strip():
            return adapter, text, skipped
        skipped.append({"adapter": name, "reason": "empty_text"})
    raise StageFailure(code="no_ocr_text")
