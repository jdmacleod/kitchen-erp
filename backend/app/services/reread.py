"""Re-reading a receipt purchase's lines, as a proposal a person applies (04, 2S).

``request`` makes a re-read. Re-check (``check``) answers at once from the
reader's answer the lines stage kept; Read again (``read``) waits for the worker.
Transcribe again (``transcribe``, #491) also waits for the worker, and reads the
stored receipt image again first: a new transcript, header and lines. Its
transcript is kept in the proposal and recorded only when it is applied, and its
header fills only what the purchase is missing.
Either way the result is a proposal (``app.ingest.reread.propose``), and nothing
changes until ``apply``: on a draft it goes through Correct the lines; on a
committed purchase it reopens it first, so its prices move only on recommit.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.ingest import assess
from app.ingest import reread as proposal_of
from app.ingest.errors import IngestError, RetryableError
from app.ingest.llm import LlmClient
from app.ingest.paths import document_path
from app.ingest.schemas import ReceiptLines
from app.ingest.stages import (
    backoff_seconds,
    header_version,
    latest_results,
    lines_text,
    read_header_output,
    read_lines_with_fallback,
    read_scan,
    receipt_vendor,
    second_text_of,
)
from app.models import (
    AppUser,
    IngestJob,
    IngestStageResult,
    LineReread,
    Purchase,
    PurchaseLine,
    ReceiptDocument,
)
from app.models.purchases import REREAD_OPEN
from app.services import recipe_cost_triggers
from app.services.purchases import ensure_not_voided, get_purchase
from app.services.review import ReadRow, apply_rows

log = get_logger(__name__)

ADAPTER = "reread"
ADAPTER_VERSION = "1"


def fingerprint(purchase: Purchase) -> str:
    """The purchase's lines as a digest: ids, last changes and removals."""
    parts = sorted(
        f"{line.id}:{line.updated_at.isoformat() if line.updated_at else ''}:"
        f"{line.removed_at.isoformat() if line.removed_at else ''}"
        for line in purchase.lines
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _present(purchase: Purchase) -> list[proposal_of.Line]:
    lines = sorted((x for x in purchase.lines if x.removed_at is None), key=lambda x: x.seq)
    return [
        proposal_of.Line(
            raw_text=x.raw_text,
            line_kind=x.line_kind,
            qty=x.qty,
            unit=x.unit,
            unit_price=x.unit_price,
            line_total=x.line_total,
            flags=list(x.flags),
            id=x.id,
            parent_id=x.parent_line_id,
            seq=x.seq,
            printed_text=x.printed_text,
            in_price_amount=x.in_price_amount,
        )
        for x in lines
    ]


async def _job(db: AsyncSession, purchase: Purchase) -> IngestJob:
    job = (
        await db.execute(select(IngestJob).where(IngestJob.purchase_id == purchase.id))
    ).scalar_one_or_none()
    if purchase.source != "receipt" or job is None:
        raise ApiError(409, "not_a_receipt", "Only a purchase read from a receipt can be re-read.")
    return job


def _totals(purchase: Purchase) -> tuple[Decimal | None, Decimal | None]:
    """The total and tax the re-read checks against: the purchase's own, as corrected."""
    printed = None if "total_missing" in purchase.flags else purchase.total
    return printed, purchase.tax


def make_proposal(
    purchase: Purchase,
    lines_output: dict[str, Any] | None,
    receipt_text: str,
    read: ReceiptLines,
    *,
    unread_parts: list[int] | None = None,
    read_fallback: bool = False,
    fill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The proposal for a reading of ``purchase``'s receipt. Pure.

    ``read_fallback``: the reading came from the second transcript (#490), and
    ``receipt_text`` is that transcript. ``fill``: the header values Transcribe
    again would give a purchase missing them (#491); a total or tax there is the
    one the lines are checked against.
    """
    printed, tax = _totals(purchase)
    if fill and fill.get("total") is not None:
        printed = Decimal(fill["total"])
    if fill and fill.get("tax") is not None:
        tax = Decimal(fill["tax"])
    assessed = assess.assess(read, receipt_text, printed, tax, unread_parts=unread_parts)
    reading_flags = [*assessed.flags, *(["read_fallback"] if read_fallback else [])]
    proposal = proposal_of.propose(
        baseline=proposal_of.baseline_lines(lines_output),
        present=_present(purchase),
        reading=assessed.lines,
        printed_total=printed,
        header_tax=tax,
        present_flags=list(purchase.flags),
        reading_flags=reading_flags,
    )
    proposal["read_lines"] = read.model_dump(mode="json")
    proposal["printed_checks"] = assessed.passes.checks
    proposal["rules_version"] = assess.LINE_RULES_VERSION
    if unread_parts:
        proposal["unread_parts"] = list(unread_parts)
    if read_fallback:
        proposal["text_source"] = "second_text"
    return proposal


def header_fill(purchase: Purchase, header: dict[str, Any]) -> dict[str, Any]:
    """What a new header reading gives that the purchase is missing (#491).

    A value someone set, or the first reading found, is never replaced: only a
    location not chosen, a date or total the reader could not find, and a tax
    left blank.
    """
    fill: dict[str, Any] = {}
    location = header.get("location") or {}
    if purchase.vendor_location_id is None and location.get("vendor_location_id"):
        fill["vendor_location_id"] = location["vendor_location_id"]
        for candidate in location.get("candidates") or []:
            if candidate.get("vendor_location_id") == location["vendor_location_id"]:
                vendor, branch = candidate.get("vendor_name"), candidate.get("location_name")
                # A branch named as its vendor is named once.
                named = vendor if branch in (None, vendor) else f"{vendor}, {branch}"
                fill["location_name"] = named
    if "purchased_at_missing" in purchase.flags and header.get("purchased_at"):
        fill["purchased_at"] = header["purchased_at"]
    if "total_missing" in purchase.flags and header.get("total") is not None:
        fill["total"] = header["total"]
    if purchase.tax is None and header.get("tax") is not None:
        fill["tax"] = header["tax"]
    return fill


def _apply_fill(purchase: Purchase, fill: dict[str, Any]) -> None:
    if fill.get("vendor_location_id"):
        purchase.vendor_location_id = uuid.UUID(fill["vendor_location_id"])
    if fill.get("purchased_at"):
        purchase.purchased_at = datetime.fromisoformat(fill["purchased_at"])
    if fill.get("total") is not None:
        purchase.total = Decimal(fill["total"])
    if fill.get("tax") is not None:
        purchase.tax = Decimal(fill["tax"])
    answered = {
        flag
        for field, flag in (("purchased_at", "purchased_at_missing"), ("total", "total_missing"))
        if fill.get(field) is not None
    }
    purchase.flags = [f for f in purchase.flags if f not in answered]


async def state(db: AsyncSession, purchase: Purchase) -> dict[str, Any] | None:
    """What Re-read lines can offer: Re-check when the answer was kept, and any open one."""
    if purchase.source != "receipt" or purchase.status == "voided":
        return None
    kept = (
        await db.execute(
            text(
                "SELECT r.output ? 'read_lines' FROM ingest_stage_result r "
                "JOIN ingest_job j ON j.id = r.job_id "
                "WHERE j.purchase_id = :purchase AND r.stage = 'lines' "
                "ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
            ),
            {"purchase": purchase.id},
        )
    ).scalar_one_or_none()
    if kept is None:
        return None  # never read: nothing to re-read
    found = await _open(db, purchase.id)
    return {
        "can_recheck": bool(kept),
        "open": None if found is None else {"id": found.id, "status": found.status},
    }


async def _open(db: AsyncSession, purchase_id: uuid.UUID) -> LineReread | None:
    return (
        await db.execute(
            select(LineReread).where(
                LineReread.purchase_id == purchase_id, LineReread.status.in_(REREAD_OPEN)
            )
        )
    ).scalar_one_or_none()


async def request(db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, mode: str) -> LineReread:
    purchase = await get_purchase(db, purchase_id, lock=True)
    ensure_not_voided(purchase)
    job = await _job(db, purchase)
    if await _open(db, purchase.id) is not None:
        raise ApiError(409, "reread_open", "This receipt already has a re-read open.")
    results = await latest_results(db, job.id)
    reread = LineReread(
        purchase_id=purchase.id,
        mode=mode,
        status="pending",
        purchase_status=purchase.status,
        lines_fingerprint=fingerprint(purchase),
        requested_by=user.id,
    )
    if mode == "check":
        lines_output = results["lines"].output if "lines" in results else None
        stored = (lines_output or {}).get("read_lines")
        if stored is None:
            raise ApiError(
                409,
                "not_stored",
                "This receipt was read before its reader's answer was kept: read it again.",
            )
        ocr_output = results["ocr"].output if "ocr" in results else None
        text = lines_text(ocr_output, lines_output) or ""
        try:
            read = ReceiptLines.model_validate(stored)
        except ValidationError as exc:
            raise ApiError(409, "not_stored", "The kept answer no longer reads.") from exc
        proposal = make_proposal(
            purchase,
            lines_output,
            text,
            read,
            unread_parts=lines_output.get("unread_parts"),
            read_fallback=lines_output.get("text_source") == "second_text",
        )
        reread.proposal = proposal
        reread.status = "ready" if proposal_of.has_changes(proposal) else "no_changes"
    db.add(reread)
    try:
        await db.flush()
    except IntegrityError as exc:  # another request opened one first
        await db.rollback()
        raise ApiError(409, "reread_open", "This receipt already has a re-read open.") from exc
    await db.commit()
    return reread


async def get(db: AsyncSession, reread_id: uuid.UUID) -> LineReread:
    reread = await db.get(LineReread, reread_id)
    if reread is None:
        raise ApiError(404, "not_found", "No such re-read.")
    return reread


async def latest(db: AsyncSession, purchase_id: uuid.UUID) -> LineReread:
    reread = (
        await db.execute(
            select(LineReread)
            .where(LineReread.purchase_id == purchase_id)
            .order_by(LineReread.created_at.desc(), LineReread.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if reread is None:
        raise ApiError(404, "not_found", "This purchase has no re-read.")
    return reread


def _decided(reread: LineReread, user: AppUser, status: str) -> None:
    reread.status = status
    reread.decided_by = user.id
    reread.decided_at = datetime.now(UTC)


async def dismiss(db: AsyncSession, user: AppUser, reread_id: uuid.UUID) -> LineReread:
    reread = await get(db, reread_id)
    if reread.status not in REREAD_OPEN:
        raise ApiError(409, "reread_closed", "This re-read is already closed.")
    _decided(reread, user, "dismissed")
    await db.commit()
    return reread


def _rows(
    proposal: dict[str, Any], purchase: Purchase, written: dict[uuid.UUID, proposal_of.Line]
) -> list[ReadRow]:
    """The proposal's rows as Correct the lines takes them.

    A reader's row carries its printed rate and flags. A flag the last reading
    raised on the line is replaced by the new reading's; any other flag (a
    resolution's, a person's choice of no price) stays.
    """
    saved = {line.id: line for line in purchase.lines}
    rows: list[ReadRow] = []
    for row in proposal["rows"]:
        line_id = uuid.UUID(row["line_id"]) if row["line_id"] else None
        line = saved.get(line_id) if line_id else None
        fields: dict[str, Any] = {
            "id": line_id,
            "raw_text": row["raw_text"],
            "line_kind": row["line_kind"],
            "qty": None if row["qty"] is None else Decimal(row["qty"]),
            "unit": row["unit"],
            "line_total": Decimal(row["line_total"]),
            "attach_to": row["attach_to"],
        }
        if row["owner"] == "reader":
            before = set(written[line_id].flags) if line_id in written else set()
            others = [f for f in (line.flags if line else []) if f not in before]
            fields["unit_price"] = None if row["unit_price"] is None else Decimal(row["unit_price"])
            fields["flags"] = list(dict.fromkeys([*row["flags"], *others]))
            fields["printed_text"] = row.get("printed_text")
            in_price = row.get("in_price_amount")
            fields["in_price_amount"] = None if in_price is None else Decimal(in_price)
        elif line is not None:
            fields["unit_price"] = line.unit_price
            fields["flags"] = list(line.flags)
            fields["printed_text"] = line.printed_text
            fields["in_price_amount"] = line.in_price_amount
        rows.append(ReadRow(**fields))
    return rows


def _applied_output(
    reread: LineReread,
    rows: list[ReadRow],
    ordered: list[PurchaseLine],
    written: dict[uuid.UUID, proposal_of.Line],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """The ``lines`` stage result an applied re-read appends: what the reader wrote.

    The reader's rows are written as read. A person's line the last reading also
    wrote keeps that reading's values, so it is still the person's next time; a
    line only a person added is not the reader's and is left out.
    """
    seq_of = {line.id: at for at, line in enumerate(ordered, start=1)}
    out: list[dict[str, Any]] = []
    for at, (row, line, spec) in enumerate(zip(rows, ordered, proposal["rows"], strict=True), 1):
        if spec["owner"] == "reader":
            parent = None if row.attach_to is None else row.attach_to + 1
            out.append(
                {
                    "seq": at,
                    "purchase_line_id": str(line.id),
                    "raw_text": spec["raw_text"],
                    "line_kind": spec["line_kind"],
                    "qty": spec["qty"],
                    "unit": spec["unit"],
                    "unit_price": spec["unit_price"],
                    "line_total": spec["line_total"],
                    "parent_seq": parent,
                    "flags": list(spec["flags"]),
                    "printed_row": spec.get("printed_text"),
                    "in_price_amount": spec.get("in_price_amount"),
                }
            )
        elif line.id in written:
            old = written[line.id]
            out.append(
                {
                    "seq": at,
                    "purchase_line_id": str(line.id),
                    "raw_text": old.raw_text,
                    "line_kind": old.line_kind,
                    "qty": None if old.qty is None else str(old.qty),
                    "unit": old.unit,
                    "unit_price": None if old.unit_price is None else str(old.unit_price),
                    "line_total": str(old.line_total),
                    "parent_seq": seq_of.get(old.parent_id) if old.parent_id else None,
                    "flags": list(old.flags),
                    "printed_row": old.printed_text,
                    "in_price_amount": None
                    if old.in_price_amount is None
                    else str(old.in_price_amount),
                }
            )
    output: dict[str, Any] = {
        "parsed": True,
        "reread_id": str(reread.id),
        "mode": reread.mode,
        "line_count": len(out),
        "lines": out,
        "reconciliation": proposal["after"]["reconciliation"],
        "purchase_flags": proposal["after"]["flags"],
        "read_lines": proposal["read_lines"],
    }
    if "rules_version" in proposal:  # proposals made before issue 382 carry none
        output["rules_version"] = proposal["rules_version"]
    if proposal.get("unread_parts"):
        output["unread_parts"] = proposal["unread_parts"]
    if proposal.get("text_source"):
        output["text_source"] = proposal["text_source"]
    return output


async def apply(db: AsyncSession, user: AppUser, reread_id: uuid.UUID) -> LineReread:
    started = time.perf_counter()
    reread = await get(db, reread_id)
    purchase = await get_purchase(db, reread.purchase_id, lock=True)
    await db.refresh(reread, with_for_update=True)
    if reread.status != "ready":
        raise ApiError(409, "reread_closed", "This re-read has nothing to apply.")
    ensure_not_voided(purchase)
    if (
        purchase.status != reread.purchase_status
        or fingerprint(purchase) != reread.lines_fingerprint
    ):
        raise ApiError(
            409,
            "reread_stale",
            "The lines changed since this re-read. Re-check or read again.",
        )
    job = await _job(db, purchase)
    results = await latest_results(db, job.id)
    transcript = (reread.proposal or {}).get("transcript")
    if transcript is not None and transcript.get("based_on") != (
        str(results["ocr"].id) if "ocr" in results else None
    ):
        raise ApiError(
            409,
            "reread_stale",
            "The receipt was transcribed again since this re-read. Re-read it again.",
        )
    written = {
        line.id: line
        for line in proposal_of.baseline_lines(
            results["lines"].output if "lines" in results else None
        )
    }
    proposal = reread.proposal or {}

    if purchase.status == "committed":
        # As Reopen does: its prices stop counting until a person commits again.
        purchase.status = "reviewed"
        await db.flush()
        await recipe_cost_triggers.after_purchase_changed(db, purchase.id)

    reading_flags = set(assess.LINE_READING_FLAGS) | {"total_mismatch"}
    purchase.flags = [
        *[f for f in purchase.flags if f not in reading_flags],
        *proposal["after"]["flags"],
    ]
    if "printed_checks" in proposal:  # proposals made before 0057 carry none
        purchase.printed_checks = proposal["printed_checks"]
    if transcript is not None:
        _record_transcript(db, job, purchase, transcript)
    rows = _rows(proposal, purchase, written)
    ordered = await apply_rows(db, user, purchase, rows)
    db.add(
        IngestStageResult(
            job_id=job.id,
            stage="lines",
            adapter=ADAPTER,
            adapter_version=ADAPTER_VERSION,
            output=_applied_output(reread, rows, ordered, written, proposal),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    _decided(reread, user, "applied")
    await db.commit()
    return reread


def _record_transcript(
    db: AsyncSession, job: IngestJob, purchase: Purchase, transcript: dict[str, Any]
) -> None:
    """Record Transcribe again's OCR and header results and fill the blanks (#491).

    Appended before the lines result, so the newest of each stage is this
    reading's; the earlier results stay, as stage results always do. Row boxes
    found against the old text stop applying and are found again.
    """
    for stage in ("ocr", "header"):
        db.add(
            IngestStageResult(
                job_id=job.id,
                stage=stage,
                adapter=transcript[stage]["adapter"],
                adapter_version=transcript[stage]["adapter_version"],
                output=transcript[stage]["output"],
                duration_ms=transcript[stage]["duration_ms"],
            )
        )
    ocr_output, header = transcript["ocr"]["output"], transcript["header"]["output"]
    source_flags = {
        "ocr_fallback": bool(ocr_output.get("fallback")),
        "header_unparsed": not header.get("parsed", False),
    }
    purchase.flags = [
        *[f for f in purchase.flags if f not in source_flags],
        *[f for f, on in source_flags.items() if on],
    ]
    _apply_fill(purchase, transcript.get("fill") or {})


async def request_many(
    db: AsyncSession, user: AppUser, mode: str, *, since: datetime | None = None
) -> dict[str, int]:
    """A re-read for every receipt purchase not voided and with none open, oldest first.

    ``kerp receipts reread``. Re-checks with nothing to change close as
    ``no_changes``, so only receipts that would change reach the inbox.
    """
    stmt = (
        select(Purchase.id)
        .join(IngestJob, IngestJob.purchase_id == Purchase.id)
        .where(Purchase.source == "receipt", Purchase.status != "voided")
        .order_by(Purchase.purchased_at, Purchase.id)
    )
    if since is not None:
        stmt = stmt.where(Purchase.purchased_at >= since)
    ids = list((await db.execute(stmt)).scalars())
    counts = {"ready": 0, "no_changes": 0, "pending": 0, "not_stored": 0, "already_open": 0}
    for purchase_id in ids:
        try:
            made = await request(db, user, purchase_id, mode)
        except ApiError as exc:
            await db.rollback()
            if exc.code == "not_stored":
                counts["not_stored"] += 1
            elif exc.code == "reread_open":
                counts["already_open"] += 1
            else:
                raise
            continue
        counts[made.status] += 1
    return counts


# --- Read again, in the worker ------------------------------------------------------


async def claim_read(db: AsyncSession, locked_by: str) -> LineReread | None:
    """Lock and mark one Read again as running; None when none is due.

    As a receipt job is claimed: pending and due, or running past the lock
    timeout (a worker that stopped mid-read).
    """
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=get_settings().ingest_lock_timeout_seconds)
    reread = (
        await db.execute(
            select(LineReread)
            .where(
                LineReread.mode.in_(("read", "transcribe")),
                or_(
                    (LineReread.status == "pending")
                    & or_(LineReread.next_attempt_at.is_(None), LineReread.next_attempt_at <= now),
                    (LineReread.status == "running") & (LineReread.locked_at < stale),
                ),
            )
            .order_by(LineReread.next_attempt_at.nulls_first(), LineReread.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if reread is None:
        await db.rollback()
        return None
    reread.status = "running"
    reread.locked_at = now
    reread.locked_by = locked_by
    await db.commit()
    return reread


def _unlock(reread: LineReread) -> None:
    reread.locked_at = None
    reread.locked_by = None


async def _hold(db: AsyncSession, reread: LineReread) -> None:
    """Renew the re-read's lock between steps.

    Transcribe again runs the OCR, header and lines reads under one claim, where
    the ingest job takes one claim for each; each read keeps within the lock
    timeout, and renewing it between them keeps a second worker from claiming
    the re-read mid-way.
    """
    reread.locked_at = datetime.now(UTC)
    await db.commit()


async def _transcribe(
    db: AsyncSession,
    llm: LlmClient,
    reread: LineReread,
    purchase: Purchase,
    job: IngestJob,
    results: dict[str, IngestStageResult],
) -> dict[str, Any]:
    """Transcribe the stored receipt again and read its header (#491).

    The OCR adapters run as configured, without the client's text, which is the
    upload's and would only give the same transcript back.
    """
    document = await db.get(ReceiptDocument, job.receipt_document_id)
    if document is None or not document_path(document).is_file():
        raise IngestError(code="image_missing")
    names = [name for name in get_settings().ocr_adapters if name != "client"]
    started = time.perf_counter()
    adapter, ocr_output = await read_scan(document, names)
    read_at = time.perf_counter()
    await _hold(db, reread)
    header = await read_header_output(
        db, llm, document, ocr_output, document.captured_at or job.created_at
    )
    done = time.perf_counter()
    await _hold(db, reread)
    return {
        # The OCR result this was read beside: applying after another is stale.
        "based_on": str(results["ocr"].id) if "ocr" in results else None,
        "ocr": {
            "adapter": adapter.name,
            "adapter_version": adapter.version,
            "output": ocr_output,
            "duration_ms": int((read_at - started) * 1000),
        },
        "header": {
            "adapter": "llm",
            "adapter_version": header_version(llm),
            "output": header,
            "duration_ms": int((done - read_at) * 1000),
        },
        "fill": header_fill(purchase, header),
    }


async def _read(db: AsyncSession, reread: LineReread, llm: LlmClient) -> None:
    """Read the receipt again and make the proposal, or say why not.

    Read again reads the stored text; Transcribe again reads the image first.
    """
    purchase = await get_purchase(db, reread.purchase_id)
    if purchase.status == "voided":
        raise IngestError(code="voided")
    job = await _job(db, purchase)
    results = await latest_results(db, job.id)
    transcript = None
    if reread.mode == "transcribe":
        transcript = await _transcribe(db, llm, reread, purchase, job, results)
        ocr_output, header = transcript["ocr"]["output"], transcript["header"]["output"]
    else:
        ocr_output = (results["ocr"].output if "ocr" in results else None) or {}
        header = (results["header"].output if "header" in results else None) or {}
    text = ocr_output.get("text")
    if not isinstance(text, str) or not text.strip():
        raise IngestError(code="no_ocr_text")
    vendor_id, vendor_name = receipt_vendor(header)
    source = await read_lines_with_fallback(
        llm, text, second_text_of(ocr_output), vendor_id, vendor_name
    )
    lines_read, text = source.read, source.text
    if lines_read.result is None:
        # The reader already tried as often as the stage would: say why, and stop.
        reread.status = "failed"
        reread.reason = lines_read.reason or "invalid_model_output"
        reread.attempts += 1
        return
    lines_output = results["lines"].output if "lines" in results else None
    fill = None if transcript is None else transcript["fill"]
    proposal = make_proposal(
        purchase,
        lines_output,
        text,
        lines_read.result,
        unread_parts=lines_read.unread_parts,
        read_fallback=source.fallback,
        fill=fill,
    )
    proposal["reader"] = {
        "parser": lines_read.parser,
        "parser_version": lines_read.parser_version,
        "model_attempts": lines_read.attempts,
    }
    if transcript is not None:
        proposal["transcript"] = transcript
    # The proposal is made against the lines as they are now, so that is what a
    # later change is measured from.
    reread.proposal = proposal
    reread.purchase_status = purchase.status
    reread.lines_fingerprint = fingerprint(purchase)
    # A reading from the other text is a change even with the same lines: applying
    # it moves the fallback note and the text later checks read (#490). So is a
    # header value Transcribe again fills (#491).
    moved = source.fallback != ((lines_output or {}).get("text_source") == "second_text")
    changed = proposal_of.has_changes(proposal) or moved or bool(fill)
    reread.status = "ready" if changed else "no_changes"
    reread.reason = None


async def run_read_once(db: AsyncSession, *, locked_by: str, llm: LlmClient | None = None) -> bool:
    """Claim and answer one Read again. False when none was due.

    An unreachable model server leaves it pending, retried with the receipt
    jobs' backoff and never failed for it (2O). Any other failure is retried up
    to INGEST_MAX_ATTEMPTS, then the re-read ends ``failed`` with its code.
    """
    reread = await claim_read(db, locked_by)
    if reread is None:
        return False
    reread_id = reread.id
    settings = get_settings()
    try:
        await _read(db, reread, llm or LlmClient())
    except Exception as exc:
        await db.rollback()
        reread = await db.get(LineReread, reread_id, populate_existing=True)
        assert reread is not None  # noqa: S101
        code = exc.code if isinstance(exc, IngestError) else "unexpected_error"
        retryable = isinstance(exc, RetryableError)
        reread.attempts += 1
        _unlock(reread)
        if not retryable and (reread.attempts >= settings.ingest_max_attempts or code == "voided"):
            reread.status = "failed"
            reread.reason = code
            reread.next_attempt_at = None
        else:
            reread.status = "pending"
            reread.reason = code
            reread.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=backoff_seconds(reread.attempts)
            )
        await db.commit()
        log.warning(
            "reread attempt failed",
            extra={"error": code, "attempts": reread.attempts, "status": reread.status},
        )
        return True
    _unlock(reread)
    await db.commit()
    log.info("reread answered", extra={"status": reread.status, "reason": reread.reason})
    return True
