"""The stage machine: run one stage of one job and record the attempt.

``run_stage(db, job)`` is the unit of work the worker and the tests share. It
appends an ``ingest_stage_result`` for every attempt, successful or not,
advances the job on success, and on failure schedules a retry with exponential
backoff (``INGEST_BACKOFF_BASE_SECONDS`` × ``INGEST_BACKOFF_FACTOR`` ^ (attempts−1),
capped at ``INGEST_BACKOFF_MAX_SECONDS``) until ``INGEST_MAX_ATTEMPTS``, then
marks the job ``failed``. An unreachable model server is the exception: the
job keeps waiting in ``pending`` with capped backoff and never fails for it,
so the deployment works without a model and resumes when one appears. Which
errors get that treatment is decided by :class:`app.ingest.errors.RetryableError`,
not here — a condition this deployment cannot fix by trying again, such as a
request that exceeds the model timeout, is not one of them.

Stages are idempotent. The latest result per stage is the effective one;
each stage reads its inputs from the latest results of the stages before it.
``last_error``, ``last_error_detail`` and every log line carry error codes and
short condition names only, never receipt text.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger, job_id_var
from app.ingest import assess, draft, parsers, readers
from app.ingest import header as header_stage
from app.ingest import lines as lines_stage
from app.ingest.errors import IngestError, RetryableError
from app.ingest.llm import CLIENT_VERSION, LlmClient
from app.ingest.ocr import run_ocr, second_transcript
from app.ingest.paths import document_path
from app.ingest.schemas import ReceiptLines
from app.models import IngestJob, IngestStageResult, ReceiptDocument
from app.services import resolution

log = get_logger(__name__)

PIPELINE_VERSION = "1"
STAGE_ORDER = ("captured", "ocr", "header", "lines", "resolve", "review", "committed")
RUNNABLE_STAGES = ("captured", "ocr", "header", "lines", "resolve")


def next_stage(stage: str) -> str:
    return STAGE_ORDER[STAGE_ORDER.index(stage) + 1]


def backoff_seconds(attempts: int) -> float:
    settings = get_settings()
    raw = settings.ingest_backoff_base_seconds * settings.ingest_backoff_factor ** max(
        attempts - 1, 0
    )
    return min(raw, settings.ingest_backoff_max_seconds)


@dataclass
class StageOutcome:
    adapter: str
    adapter_version: str
    output: dict[str, Any]
    next_stage: str
    next_status: str = "pending"


@dataclass
class StageContext:
    db: AsyncSession
    job: IngestJob
    document: ReceiptDocument
    llm: LlmClient
    results: dict[str, IngestStageResult] = field(default_factory=dict)
    ocr_adapters: list[str] | None = None

    def latest_output(self, stage: str) -> dict[str, Any] | None:
        result = self.results.get(stage)
        return None if result is None else result.output

    def receipt_text(self) -> str:
        output = self.latest_output("ocr") or {}
        text = output.get("text")
        if not isinstance(text, str) or not text.strip():
            raise IngestError(code="no_ocr_text")
        return text


async def latest_results(db: AsyncSession, job_id: uuid.UUID) -> dict[str, IngestStageResult]:
    """The most recent result per stage for a job."""
    rows = await db.execute(
        select(IngestStageResult)
        .where(IngestStageResult.job_id == job_id)
        .order_by(IngestStageResult.created_at.desc(), IngestStageResult.id.desc())
    )
    latest: dict[str, IngestStageResult] = {}
    for result in rows.scalars():
        latest.setdefault(result.stage, result)
    return latest


# --- stages --------------------------------------------------------------------


async def stage_captured(ctx: StageContext) -> StageOutcome:
    """Confirm the stored image is present and hand over to OCR."""
    path = document_path(ctx.document)
    if not path.is_file():
        raise IngestError(code="image_missing")
    return StageOutcome(
        adapter="capture",
        adapter_version=PIPELINE_VERSION,
        output={
            "sha256": ctx.document.sha256,
            "mime": ctx.document.mime,
            "bytes": ctx.document.bytes,
            "has_client_ocr_text": bool(ctx.document.client_ocr_text),
        },
        next_stage="ocr",
    )


async def stage_ocr(ctx: StageContext) -> StageOutcome:
    adapter, text, skipped = await run_ocr(
        ctx.document, document_path(ctx.document), ctx.ocr_adapters
    )
    output: dict[str, Any] = {"text": text, "chars": len(text), "skipped": skipped}
    record = getattr(adapter, "record", None)
    if record:
        output["vision"] = record  # what the transcription cost and how it ended (2O)
    if any(s["adapter"] == "vision" for s in skipped):
        # The transcriber was asked and could not read it; the text is another
        # adapter's. Review says so (ocr_fallback).
        output["fallback"] = True
    second = await second_transcript(
        ctx.document, document_path(ctx.document), adapter.name, ctx.ocr_adapters
    )
    if second is not None:
        output["second_text"] = second  # for the header's phone and store number (#488)
    return StageOutcome(
        adapter=adapter.name,
        adapter_version=adapter.version,
        output=output,
        next_stage="header",
    )


async def stage_header(ctx: StageContext) -> StageOutcome:
    settings = get_settings()
    text = ctx.receipt_text()
    header, attempts = await readers.read_header(
        ctx.llm, text, deadline_at=readers.stage_deadline_at()
    )
    flags: list[str] = []
    purchased_at = None
    if header is not None:
        purchased_at = header_stage.parse_local_datetime(
            header.purchased_at_local, settings.household_timezone, settings.receipt_date_order
        )
        purchased_at, when_flags = header_stage.datetime_from_text(
            header.purchased_at_local,
            purchased_at,
            text,
            settings.household_timezone,
            settings.receipt_date_order,
            # When it was photographed or first uploaded: the purchase came before.
            reference=ctx.document.captured_at or ctx.job.created_at,
        )
        flags.extend(when_flags)
        if header.purchased_at_local and purchased_at is None:
            flags.append("purchased_at_unparsed")
        header, total_flags = readers.apply_printed_total(header, text)
        flags.extend(total_flags)
    second = (ctx.latest_output("ocr") or {}).get("second_text") or {}
    match = await header_stage.match_location(
        ctx.db,
        document_id=ctx.document.id,
        receipt_text=text,
        merchant_name=None if header is None else header.merchant_name,
        store_identifier=None if header is None else header.store_identifier,
        phone_text=None if header is None else header.phone_text,
        address_text=None if header is None else header.address_text,
        second_text=second.get("text"),
    )
    output = header_stage.header_output(
        header, model_attempts=attempts, purchased_at=purchased_at, match=match, flags=flags
    )
    return StageOutcome(
        adapter="llm",
        adapter_version=f"{ctx.llm.model}/{CLIENT_VERSION}",
        output=output,
        next_stage="lines",
    )


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))


@dataclass
class LinesRead:
    """What the reader made of a receipt's text, before any pass."""

    result: ReceiptLines | None
    parser: str
    parser_version: str
    attempts: int = 0
    reason: str | None = None
    unread_parts: list[int] = field(default_factory=list)
    part_count: int = 1


async def read_receipt_lines(
    llm: LlmClient, text: str, vendor_id: uuid.UUID | None, vendor_name: str | None
) -> LinesRead:
    """The vendor's parser when one reads the text, otherwise the model.

    The lines stage and Read again (04, 2S) both read through here. An
    unreachable model server raises, as it does in the stage, so the caller
    waits and tries again.
    """
    vendor_parser = parsers.find(vendor_id, vendor_name)
    if vendor_parser is not None:
        result = vendor_parser.parse(text)
        if result is not None:
            return LinesRead(result, vendor_parser.name, vendor_parser.version)
    reading = await readers.read_lines(llm, text, deadline_at=readers.stage_deadline_at())
    return LinesRead(
        reading.lines,
        lines_stage.GENERIC_PARSER,
        lines_stage.GENERIC_PARSER_VERSION,
        reading.attempts,
        reading.reason,
        list(reading.unread_parts),
        reading.part_count,
    )


def _has_items(result: ReceiptLines | None) -> bool:
    return result is not None and any(line.line_kind == "item" for line in result.lines)


@dataclass
class LinesSource:
    """A reading, and the text it was read from."""

    read: LinesRead
    text: str
    fallback: bool = False


async def read_lines_with_fallback(
    llm: LlmClient,
    text: str,
    second_text: str | None,
    vendor_id: uuid.UUID | None,
    vendor_name: str | None,
) -> LinesSource:
    """The lines from the OCR text, or from the second transcript when that gave none.

    On a till that prints two price columns the vision transcriber can lose the
    amounts, and the reader then finds no items (#490). When the OCR result
    kept a second transcript (2O), the lines are read again from it, and that
    reading is used if it found items. Every check on the lines then reads the
    same text the lines came from.
    """
    first = await read_receipt_lines(llm, text, vendor_id, vendor_name)
    if _has_items(first.result) or not (second_text and second_text.strip()):
        return LinesSource(first, text)
    again = await read_receipt_lines(llm, second_text, vendor_id, vendor_name)
    if _has_items(again.result):
        return LinesSource(again, second_text, fallback=True)
    return LinesSource(first, text)


def second_text_of(ocr: dict[str, Any] | None) -> str | None:
    text = ((ocr or {}).get("second_text") or {}).get("text")
    return text if isinstance(text, str) else None


def lines_text(ocr: dict[str, Any] | None, lines: dict[str, Any] | None) -> str | None:
    """The text a reading's lines were read from: the OCR text, or the second
    transcript when the lines fell back to it (#490)."""
    if (lines or {}).get("text_source") == "second_text":
        second = second_text_of(ocr)
        if second is not None:
            return second
    text = (ocr or {}).get("text")
    return text if isinstance(text, str) else None


def receipt_vendor(header: dict[str, Any]) -> tuple[uuid.UUID | None, str | None]:
    """The vendor the header stage matched, and its name, for choosing a parser."""
    location = header.get("location") or {}
    vendor_id = _uuid(location.get("vendor_id"))
    for candidate in location.get("candidates") or []:
        if candidate.get("vendor_id") == location.get("vendor_id"):
            return vendor_id, candidate.get("vendor_name")
    return vendor_id, None


def _source_flags(ctx: StageContext, header: dict[str, Any], source: LinesSource) -> list[str]:
    """What review says about where the text and the header came from."""
    flags: list[str] = []
    if (ctx.latest_output("ocr") or {}).get("fallback"):
        flags.append("ocr_fallback")
    if not header.get("parsed", False):
        flags.append("header_unparsed")
    if source.fallback:
        flags.append("read_fallback")
    return flags


async def stage_lines(ctx: StageContext) -> StageOutcome:
    text = ctx.receipt_text()
    header = ctx.latest_output("header") or {}
    location = header.get("location") or {}
    vendor_location_id = _uuid(location.get("vendor_location_id"))
    vendor_id, vendor_name = receipt_vendor(header)

    source = await read_lines_with_fallback(
        ctx.llm, text, second_text_of(ctx.latest_output("ocr")), vendor_id, vendor_name
    )
    lines_read, text = source.read, source.text
    result, reason = lines_read.result, lines_read.reason
    parser_name, parser_version = lines_read.parser, lines_read.parser_version
    attempts, unread_parts, part_count = (
        lines_read.attempts,
        lines_read.unread_parts,
        lines_read.part_count,
    )
    printed_total = _decimal(header.get("total"))
    header_tax = _decimal(header.get("tax"))

    purchase_flags = _source_flags(ctx, header, source)
    assessed = assess.assess(result, text, printed_total, header_tax, unread_parts=unread_parts)
    passes, parsed, reconciliation = assessed.passes, assessed.lines, assessed.reconciliation
    merged_rows, informational = passes.merged_rows, passes.dropped_rows
    purchase_flags.extend(assessed.flags)

    purchased_at = None
    if header.get("purchased_at"):
        purchased_at = datetime.fromisoformat(header["purchased_at"])
    if purchased_at is None:
        purchased_at = ctx.document.captured_at or ctx.document.created_at
        purchase_flags.append("purchased_at_missing")
    total = printed_total if printed_total is not None else lines_stage.computed_total(parsed)
    if printed_total is None:
        purchase_flags.append("total_missing")

    purchase = await draft.upsert_draft_purchase(
        ctx.db,
        ctx.job,
        ctx.document,
        purchased_at=purchased_at,
        subtotal=_decimal(header.get("subtotal")),
        tax=header_tax,
        total=total,
        vendor_location_id=vendor_location_id,
        flags=purchase_flags,
        lines=parsed,
        printed_checks=passes.checks,
    )
    output: dict[str, Any] = {
        "parsed": result is not None,
        "parser": parser_name,
        "parser_version": parser_version,
        "model_attempts": attempts,
        "purchase_id": str(purchase.id),
        "line_count": len(parsed),
        "lines": [line.as_json() for line in parsed],
        "reconciliation": reconciliation,
        "printed_checks": passes.checks,
        "purchase_flags": purchase_flags,
        "rules_version": assess.LINE_RULES_VERSION,
    }
    if reason is not None:
        output["reason"] = reason
    if source.fallback:
        output["text_source"] = "second_text"  # what a re-check reads them against
    if result is not None:
        # The reader's own answer, before any pass, so the passes can be run again
        # over it without asking the model (04, 2S).
        output["read_lines"] = result.model_dump(mode="json")
    if informational:
        # Shelf-price rows taken out of the lines, kept here for the record.
        output["dropped_rows"] = informational
    if merged_rows:
        # Weight and count rows joined to their items, kept here for the record.
        output["merged_rows"] = merged_rows
    if passes.unread_rows:
        output["unread_rows"] = passes.unread_rows
    if part_count > 1:
        output["parts"] = part_count
        output["unread_parts"] = unread_parts
    return StageOutcome(
        adapter=parser_name,
        adapter_version=(
            f"{ctx.llm.model}/{CLIENT_VERSION}"
            if parser_name == lines_stage.GENERIC_PARSER
            else parser_version
        ),
        output=output,
        next_stage="resolve",
    )


async def stage_resolve(ctx: StageContext) -> StageOutcome:
    if ctx.job.purchase_id is None:
        raise IngestError(code="no_purchase")
    output = await resolution.resolve_purchase(ctx.db, ctx.job.purchase_id)
    return StageOutcome(
        adapter="resolution",
        adapter_version=resolution.RESOLVE_VERSION,
        output={"purchase_id": str(ctx.job.purchase_id), **output},
        next_stage="review",
        next_status="needs_review",
    )


STAGE_FUNCTIONS = {
    "captured": stage_captured,
    "ocr": stage_ocr,
    "header": stage_header,
    "lines": stage_lines,
    "resolve": stage_resolve,
}


# --- runner --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def run_stage(  # noqa: PLR0915
    db: AsyncSession,
    job: IngestJob,
    *,
    llm: LlmClient | None = None,
    ocr_adapters: list[str] | None = None,
) -> IngestStageResult | None:
    """Run the job's current stage once and persist the attempt. Commits.

    Returns the stage result appended, or None when the job is at a stage a
    person owns (``review``, ``committed``).
    """
    if job.stage not in RUNNABLE_STAGES:
        return None
    job_id = job.id
    stage = job.stage
    token = job_id_var.set(str(job_id))
    settings = get_settings()
    started = time.perf_counter()
    try:
        document = await db.get(ReceiptDocument, job.receipt_document_id)
        if document is None:
            raise IngestError(code="document_missing")
        ctx = StageContext(
            db=db,
            job=job,
            document=document,
            llm=llm or LlmClient(),
            results=await latest_results(db, job_id),
            ocr_adapters=ocr_adapters,
        )
        try:
            outcome = await STAGE_FUNCTIONS[stage](ctx)
        except Exception as exc:
            await db.rollback()
            job = await db.get(IngestJob, job_id, populate_existing=True)
            assert job is not None  # noqa: S101
            duration_ms = int((time.perf_counter() - started) * 1000)
            code = exc.code if isinstance(exc, IngestError) else "unexpected_error"
            detail = exc.detail if isinstance(exc, IngestError) else None
            retryable = isinstance(exc, RetryableError)
            job.attempts += 1
            job.last_error = code
            job.last_error_detail = detail
            job.locked_at = None
            job.locked_by = None
            if not retryable and job.attempts >= settings.ingest_max_attempts:
                job.status = "failed"
                job.next_attempt_at = None
            else:
                job.status = "pending"
                job.next_attempt_at = _now() + timedelta(seconds=backoff_seconds(job.attempts))
            result = IngestStageResult(
                job_id=job_id,
                stage=stage,
                adapter="pipeline",
                adapter_version=PIPELINE_VERSION,
                # detail is omitted rather than null when there is none: the
                # output is an append-only record, and an absent key reads as
                # "the code said everything" without inventing a value.
                output={"ok": False, "error": code, "retryable": retryable}
                | ({"detail": detail} if detail is not None else {}),
                duration_ms=duration_ms,
            )
            db.add(result)
            await db.commit()
            log.warning(
                "stage attempt failed",
                extra={
                    "stage": stage,
                    "error": code,
                    "detail": detail,
                    "exc_type": type(exc).__name__,
                    "attempts": job.attempts,
                    "status": job.status,
                    "duration_ms": duration_ms,
                },
            )
            return result

        duration_ms = int((time.perf_counter() - started) * 1000)
        result = IngestStageResult(
            job_id=job_id,
            stage=stage,
            adapter=outcome.adapter,
            adapter_version=outcome.adapter_version,
            output=outcome.output,
            duration_ms=duration_ms,
        )
        db.add(result)
        job.stage = outcome.next_stage
        job.status = outcome.next_status
        job.attempts = 0
        job.last_error = None
        job.last_error_detail = None
        job.next_attempt_at = None
        job.locked_at = None
        job.locked_by = None
        await db.commit()
        log.info(
            "stage completed",
            extra={
                "stage": stage,
                "next_stage": outcome.next_stage,
                "adapter": outcome.adapter,
                "duration_ms": duration_ms,
            },
        )
        return result
    finally:
        job_id_var.reset(token)


async def run_pending(db: AsyncSession, job: IngestJob, **kwargs: Any) -> list[IngestStageResult]:
    """Drive a job through every stage it can run right now (tests and the CLI)."""
    results: list[IngestStageResult] = []
    while (
        job.stage in RUNNABLE_STAGES
        and job.status == "pending"
        and (job.next_attempt_at is None or job.next_attempt_at <= _now())
    ):
        result = await run_stage(db, job, **kwargs)
        if result is None:
            break
        results.append(result)
        job = await db.get(IngestJob, job.id, populate_existing=True)  # type: ignore[assignment]
    return results
