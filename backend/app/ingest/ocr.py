"""OCR adapters. Tried in the order given by ``OCR_ADAPTERS``.

Interface: an object with ``name``, ``version`` and ``async run(document,
image_path) -> str``. An adapter that cannot serve a document (no client text,
no binary, an unsupported format) raises :class:`OcrUnavailable` and the next
one is tried; any other exception is a stage failure.

The ``"vision"`` adapter (04, 2O) transcribes the receipt image with a local
vision model through :mod:`app.ingest.llm`. It is off unless ``OCR_ADAPTERS``
names it. A model it cannot use, a timeout or an empty transcript passes to the
next adapter; a model server that cannot be reached is the job's to wait for,
as it is for the text model.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingest import formats, raster
from app.ingest.errors import ModelMissing, ModelTimeout, OcrUnavailable, StageFailure
from app.ingest.llm import LlmClient
from app.models import ReceiptDocument

log = get_logger(__name__)

TESSERACT_TIMEOUT_SECONDS = 120.0
# Rasterising is bounded separately, because TESSERACT_TIMEOUT_SECONDS only
# starts once tesseract runs. The megapixel ceiling in app.ingest.raster caps
# what comes out; this caps how long getting there may take, so one pathological
# page cannot hold the worker -- which processes jobs one at a time -- and delay
# every receipt behind it. 60s is far above a real page: measured, a 200 dpi
# till receipt renders in well under a second.
RASTER_TIMEOUT_SECONDS = 60.0

# How Tesseract reads a receipt: --psm 6, one uniform block of text, on the page
# rendered at app.ingest.raster.PDF_RENDER_DPI (200). Measured against the
# alternatives #65 proposed with scripts/ocr_benchmark.py, on eight real phone
# document-scan receipts with their printed totals known, as header reads (two
# per receipt per run) that returned exactly the printed total, since the total
# is what every line is checked against:
#   current (200 dpi)     35/48 over three runs
#   300 dpi               38/48, but its worst miss dropped the leading digit of a
#                         total; the current setting's is a steady one-dollar misread
#   --psm 4               24/32 over two runs, and 32% of prices on synthetic scans
#   Sauvola thresholding  20/32 over two runs, though 48% of prices on synthetic
#                         scans against 42% for the current setting
# Otsu first and a 1.5x upscale read fewer prices even on synthetic scans. The
# spread is within the model's run-to-run noise on eight receipts, so nothing is
# changed; 300 dpi is the candidate to measure again with more receipts.
TESSERACT_CONFIG = ("--psm", "6")

# How the vision transcriber is asked (04, 2O). glm-ocr's own prompt: the JSON
# extraction prompt gave nothing usable. It transcribes the receipt and then
# repeats lines until its output cap (measured: 22 distinct lines in 112), and
# repeat and presence penalties did not stop it, so the cap is what a 100-line
# receipt needs and the loop is cut off afterwards. The reading benchmark scores
# exactly this.
OCR_TASK = "Text Recognition:"
OCR_NUM_PREDICT = 4096
REPEATED_RUN = 3


def drop_repeated_tail(transcript: str, run: int = REPEATED_RUN) -> str:
    """The transcript up to the first run of ``run`` lines it has already printed.

    A receipt can print one line twice (two of the same item), but not the same
    three lines in the same order twice; that is the OCR model looping.
    """
    lines = transcript.splitlines()
    seen: set[tuple[str, ...]] = set()
    for i in range(len(lines) - run + 1):
        key = tuple(line.strip() for line in lines[i : i + run])
        if not all(key):
            continue
        if key in seen:
            return "\n".join(lines[:i])
        seen.add(key)
    return transcript


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
    """Server-side fallback: the ``tesseract`` binary in the worker image.

    Reads every format :mod:`app.ingest.formats` accepts. Tesseract itself only
    understands the image formats; HEIC and PDF are rendered to PNG first, which
    is why the accepted-format list can be the list of formats that work.
    """

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
        fmt = formats.BY_MIME.get(document.mime)
        if fmt is None:
            # Unreachable through the upload endpoint, which rejects anything not
            # in FORMATS, and so a real inconsistency rather than a bad document.
            raise OcrUnavailable("unsupported_for_tesseract")
        if fmt.converter is None:
            return await self._recognize(image_path)
        with tempfile.TemporaryDirectory(prefix="kerp-ocr-") as tmp:
            # Rendering is CPU-bound and would otherwise stall the worker's loop
            # for the length of a page.
            try:
                png = await asyncio.wait_for(
                    asyncio.to_thread(
                        raster.to_png, fmt.converter, image_path, Path(tmp) / "page.png"
                    ),
                    timeout=RASTER_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                # The thread is abandoned rather than killed -- there is no way to
                # interrupt a native decode -- so this bounds the job's wait, not
                # the work. That is the part that matters here: the stage stops and
                # the queue moves on instead of one receipt blocking the rest.
                raise StageFailure(
                    code="raster_timeout",
                    detail=f"{fmt.converter} after {RASTER_TIMEOUT_SECONDS:g}s",
                ) from None
            return await self._recognize(png)

    async def _recognize(self, image_path: Path) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command,
                str(image_path),
                "stdout",
                "-l",
                "eng",
                *TESSERACT_CONFIG,
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


class VisionOcrAdapter:
    """The receipt image transcribed by a local vision model (04, 2O).

    Page 1, upright, in colour, its long side capped (the render the reading
    benchmark measured). The transcript is untrusted OCR text like any other.
    ``record`` holds what the call cost and how it ended, for the stage output.
    """

    name = "vision"
    REASON_BY_MISSING = {
        "not_found": "vision_model_missing",
        "not_multimodal": "vision_model_text_only",
    }

    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.ocr_vision_model
        self.timeout_seconds = settings.ocr_vision_timeout_seconds
        self.record: dict[str, object] = {}

    @property
    def version(self) -> str:
        return f"{self.model}/1"

    async def run(self, document: ReceiptDocument, image_path: Path) -> str:
        if not self.model:
            raise OcrUnavailable("no_vision_model")
        fmt = formats.BY_MIME.get(document.mime)
        try:
            image = await asyncio.wait_for(
                asyncio.to_thread(
                    raster.vision_png,
                    image_path,
                    None if fmt is None else fmt.converter,
                    raster.VISION_LONG_SIDE,
                ),
                timeout=RASTER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            raise OcrUnavailable("vision_render_timeout") from None
        except StageFailure as exc:
            # The next adapter renders the same document and says what is wrong
            # with it in its own words; this one only steps aside.
            log.info("vision render failed", extra={"code": exc.code})
            raise OcrUnavailable("vision_render_failed") from None
        client = LlmClient(model=self.model, role="ocr")
        self.record = {"model": self.model, "pages_truncated": image.pages_truncated}
        try:
            transcript = await client.transcribe(
                [image.png],
                OCR_TASK,
                timeout_seconds=self.timeout_seconds,
                num_predict=OCR_NUM_PREDICT,
            )
        except ModelMissing as exc:
            self._note(client)
            raise OcrUnavailable(
                self.REASON_BY_MISSING.get(exc.reason, "vision_model_missing")
            ) from None
        except ModelTimeout:
            self._note(client)
            raise OcrUnavailable("vision_timeout") from None
        # ModelUnavailable (a server that cannot be reached, or in trouble) is not
        # caught: the job waits and retries, exactly as for the text model.
        self._note(client)
        transcript = drop_repeated_tail(transcript)
        if not transcript.strip():
            raise OcrUnavailable("empty_transcript")
        return transcript

    def _note(self, client: LlmClient) -> None:
        call = client.ledger.calls[-1] if client.ledger.calls else None
        if call is not None:
            self.record.update(
                outcome=call.outcome,
                seconds=round(call.seconds, 3),
                load_seconds=call.load_seconds,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
            )


# Registered adapters by name. Tests replace entries with spies.
ADAPTERS: dict[str, Callable[[], OcrAdapter]] = {
    "client": ClientOcrAdapter,
    "vision": VisionOcrAdapter,
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
    # Which adapters declined and why, so "no_ocr_text" on the Receipts page is
    # followed by the reason instead of leaving the operator to guess between a
    # missing tesseract, an unreadable file and an empty photograph.
    detail = ", ".join(f"{s['adapter']}: {s['reason']}" for s in skipped) or None
    raise StageFailure(code="no_ocr_text", detail=detail)
