"""Reading one receipt for ``kerp reading-benchmark`` (spec 04, 2J): one
configuration's arm (OCR text, vision as OCR, or a vision reader) and the lines
stage's own post-passes, before scoring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ingest import header as header_stage
from app.ingest import lines as lines_stage
from app.ingest import llm, raster, readers, structure
from app.ingest.errors import InvalidModelOutput, ModelMissing, ModelTimeout, StageFailure
from app.ingest.formats import BY_MIME
from app.ingest.llm import VISION_RETRY, CallLedger, LlmClient, RetryPolicy
from app.ingest.ocr import (  # the transcription the vision adapter ships (2O)
    OCR_NUM_PREDICT,
    OCR_TASK,
    drop_repeated_tail,
    run_ocr,
)
from app.ingest.schemas import ReceiptHeader, VisionReceiptLines, valid_box
from app.models import ReceiptDocument
from app.services.reading_bench_inputs import (
    LONG_SIDE,
    VISION_CALL_TIMEOUT_SECONDS,
    VISION_LINES_TASK,
    Config,
    Receipt,
)

# --- the grid --------------------------------------------------------------------

# Arm (c)'s screening grid (MS4, 2026-10-01): two quality-tier models and four
# that fit on the household model server's GPU, in three families.


# --- reading one receipt -----------------------------------------------------------


class ConfigSkipped(Exception):
    """The configuration cannot run at all: its model is missing or text-only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Reading:
    """What one configuration made of one receipt, before scoring."""

    lines: list[lines_stage.ParsedLine] | None = None
    header: ReceiptHeader | None = None
    error: str | None = None
    boxes_valid: int = 0
    boxes_total: int = 0
    pages_truncated: bool = False
    ledger: CallLedger = field(default_factory=CallLedger)


def _path(receipt: Receipt) -> Path:
    return Path(get_settings().receipts_path) / receipt.image_path


def _converter(receipt: Receipt) -> str | None:
    fmt = BY_MIME.get(receipt.mime)
    return None if fmt is None else fmt.converter


async def ocr_text(receipt: Receipt) -> str | None:
    """Today's OCR for the receipt: client text when present, else Tesseract."""
    document = ReceiptDocument(
        image_path=receipt.image_path,
        mime=receipt.mime,
        client_ocr_text=receipt.client_ocr_text,
    )
    try:
        _, found, _ = await run_ocr(document, _path(receipt))
    except StageFailure:
        return None
    return found


def _post_passes(
    result: Any, receipt_text: str = "", header: ReceiptHeader | None = None
) -> list[lines_stage.ParsedLine]:
    """The lines stage's deterministic passes, exactly as the pipeline runs them."""
    return structure.post_passes(
        result,
        receipt_text,
        None if header is None else header.total,
        None if header is None else header.tax,
    ).lines


async def _read_text(client: LlmClient, receipt_text: str, reading: Reading) -> None:
    """Arm (a)'s reading of a receipt text: today's readers and post-passes."""
    header, _ = await readers.read_header(
        client, receipt_text, deadline_at=readers.stage_deadline_at()
    )
    if header is not None:
        header, _ = readers.apply_printed_total(header, receipt_text)
    reading.header = header
    lines = await readers.read_lines(client, receipt_text, deadline_at=readers.stage_deadline_at())
    if lines.lines is None:
        reading.error = lines.reason or "lines_unparsed"
        return
    reading.lines = _post_passes(lines.lines, receipt_text, header)
    if lines.unread_parts:
        reading.error = "lines_partial"


async def _read_vision(client: LlmClient, image: bytes, reading: Reading) -> None:
    """Arm (c): header and lines from the image, one call each (the "separate" variant)."""
    common: dict[str, Any] = {
        "images": [image],
        "think": False,
        "timeout_seconds": VISION_CALL_TIMEOUT_SECONDS,
    }
    try:
        reading.header, _ = await client.extract(
            ReceiptHeader,
            header_stage.HEADER_TASK,
            "",
            deadline_seconds=lines_stage.lines_deadline_seconds(),
            retry=VISION_RETRY,
            **common,
        )
    except InvalidModelOutput:
        reading.header = None
    deadline = lines_stage.lines_deadline_seconds()
    started = time.monotonic()
    answer, _ = await client.extract(
        VisionReceiptLines,
        VISION_LINES_TASK,
        "",
        deadline_seconds=deadline,
        retry=VISION_RETRY,
        **common,
    )
    if not any(line.line_kind == "item" for line in answer.lines):
        # A valid reply with no items is no reading (GAP-2). Ask once more a
        # little differently, as for an empty text part (#60).
        answer, _ = await client.extract(
            VisionReceiptLines,
            VISION_LINES_TASK,
            "",
            deadline_seconds=max(deadline - (time.monotonic() - started), 0.001),
            temperature=llm.EMPTY_PART_RETRY_TEMPERATURE,
            retry=RetryPolicy(retries=0),
            **common,
        )
        if not any(line.line_kind == "item" for line in answer.lines):
            reading.error = "empty_reading"
            return
    reading.boxes_total = len(answer.lines)
    reading.boxes_valid = sum(1 for line in answer.lines if valid_box(line.box))
    reading.lines = _post_passes(answer.without_boxes(), header=reading.header)


async def read_receipt(
    config: Config, receipt: Receipt, ocr: str | None, *, base_url: str | None = None
) -> Reading:
    """One configuration's reading of one receipt. Model failures become ``error``.

    Raises :class:`ConfigSkipped` for a model that is not there or cannot take
    images, and :class:`ModelUnavailable` when the server itself is down: both
    stop more than this one reading.
    """
    reading = Reading()

    def client(model: str, role: str) -> LlmClient:
        return LlmClient(base_url=base_url, model=model, ledger=reading.ledger, role=role)

    try:
        if config.arm == "a":
            if not ocr:
                reading.error = "no_ocr_text"
                return reading
            await _read_text(client(config.model, "text"), ocr, reading)
        else:
            image = raster.vision_png(
                _path(receipt), _converter(receipt), config.long_side or LONG_SIDE
            )
            reading.pages_truncated = image.pages_truncated
            if config.arm == "b":
                transcript = await client(config.model, "ocr").transcribe(
                    [image.png],
                    OCR_TASK,
                    timeout_seconds=VISION_CALL_TIMEOUT_SECONDS,
                    num_predict=OCR_NUM_PREDICT,
                )
                transcript = drop_repeated_tail(transcript)
                if not transcript.strip():
                    reading.error = "empty_transcript"
                    return reading
                await _read_text(
                    client(config.text_model or config.model, "text"), transcript, reading
                )
            else:
                await _read_vision(client(config.model, "primary"), image.png, reading)
    except ModelMissing as exc:
        raise ConfigSkipped(exc.reason) from None
    except StageFailure as exc:
        reading.error = exc.code
    except InvalidModelOutput as exc:
        reading.error = exc.code
    except ModelTimeout:
        reading.error = ModelTimeout.code
    return reading
