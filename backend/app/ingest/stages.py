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
from app.ingest import header as header_stage
from app.ingest import lines as lines_stage
from app.ingest import parsers
from app.ingest.errors import IngestError, InvalidModelOutput, RetryableError
from app.ingest.llm import CLIENT_VERSION, LlmClient
from app.ingest.ocr import run_ocr
from app.ingest.paths import document_path
from app.ingest.schemas import ReceiptHeader, ReceiptLines
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
    return StageOutcome(
        adapter=adapter.name,
        adapter_version=adapter.version,
        output={"text": text, "chars": len(text), "skipped": skipped},
        next_stage="header",
    )


async def stage_header(ctx: StageContext) -> StageOutcome:
    settings = get_settings()
    text = ctx.receipt_text()
    header: ReceiptHeader | None
    try:
        header, attempts = await ctx.llm.extract(ReceiptHeader, header_stage.HEADER_TASK, text)
    except InvalidModelOutput:
        header, attempts = None, 1 + max(ctx.llm.max_retries, 0)
    flags: list[str] = []
    purchased_at = None
    if header is not None:
        purchased_at = header_stage.parse_local_datetime(
            header.purchased_at_local, settings.household_timezone
        )
        if header.purchased_at_local and purchased_at is None:
            flags.append("purchased_at_unparsed")
    match = await header_stage.match_location(
        ctx.db,
        document_id=ctx.document.id,
        receipt_text=text,
        merchant_name=None if header is None else header.merchant_name,
        store_identifier=None if header is None else header.store_identifier,
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


async def stage_lines(ctx: StageContext) -> StageOutcome:
    text = ctx.receipt_text()
    header = ctx.latest_output("header") or {}
    location = header.get("location") or {}
    vendor_location_id = _uuid(location.get("vendor_location_id"))
    vendor_id = _uuid(location.get("vendor_id"))
    vendor_name = None
    for candidate in location.get("candidates") or []:
        if candidate.get("vendor_id") == location.get("vendor_id"):
            vendor_name = candidate.get("vendor_name")
            break

    parser_name, parser_version, attempts = (
        lines_stage.GENERIC_PARSER,
        lines_stage.GENERIC_PARSER_VERSION,
        0,
    )
    result: ReceiptLines | None = None
    reason: str | None = None
    vendor_parser = parsers.find(vendor_id, vendor_name)
    if vendor_parser is not None:
        result = vendor_parser.parse(text)
        if result is not None:
            parser_name, parser_version = vendor_parser.name, vendor_parser.version
    if result is None:
        try:
            result, attempts = await ctx.llm.extract(
                ReceiptLines,
                lines_stage.LINES_TASK,
                text,
                timeout_seconds=lines_stage.lines_budget_seconds(text),
                deadline_seconds=lines_stage.lines_deadline_seconds(),
            )
        except InvalidModelOutput:
            attempts, reason = 1 + max(ctx.llm.max_retries, 0), InvalidModelOutput.code
    parsed = [] if result is None else lines_stage.parse_model_lines(result)

    purchase_flags: list[str] = []
    if not header.get("parsed", False):
        purchase_flags.append("header_unparsed")
    if result is None:
        purchase_flags.append("lines_unparsed")
    printed_total = _decimal(header.get("total"))
    header_tax = _decimal(header.get("tax"))
    reconciliation = lines_stage.reconcile(parsed, printed_total, header_tax)
    if reconciliation["mismatch"]:
        purchase_flags.append("reconcile_mismatch")

    purchased_at = None
    if header.get("purchased_at"):
        purchased_at = datetime.fromisoformat(header["purchased_at"])
    if purchased_at is None:
        purchased_at = ctx.document.captured_at or ctx.document.created_at
        purchase_flags.append("purchased_at_missing")
    total = printed_total if printed_total is not None else lines_stage.computed_total(parsed)
    if printed_total is None:
        purchase_flags.append("total_missing")

    purchase = await lines_stage.upsert_draft_purchase(
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
        "purchase_flags": purchase_flags,
    }
    if reason is not None:
        output["reason"] = reason
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


async def run_stage(
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
            assert job is not None
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
