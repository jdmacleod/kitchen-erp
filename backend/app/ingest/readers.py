"""Readers: turn receipt text into a header and lines, apart from the stages.

The ``header`` and ``lines`` stages call these, and the reading benchmark
(spec 04, 2J) calls the same functions, so the benchmark's score for today's
pipeline is the pipeline's own. Nothing here touches the database or records a
stage result; the stages do that with what a reader returns.

Every model read takes an absolute ``deadline_at`` on the ``time.monotonic()``
clock from its caller, not a length of time of its own, so one clock can bound
everything a stage does.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.ingest import header as header_stage
from app.ingest import lines as lines_stage
from app.ingest.errors import InvalidModelOutput, ModelTimeout
from app.ingest.llm import EMPTY_PART_RETRY_TEMPERATURE, LlmClient
from app.ingest.schemas import ReceiptHeader, ReceiptLine, ReceiptLines

# The least time handed to extract() as a deadline, which must be positive. A
# read that reaches it gets a request that times out at once, as before #34.
_MIN_REMAINING_SECONDS = 0.001


def stage_deadline_at() -> float:
    """The monotonic time by which a stage's model reads must be done.

    Under the job's lock: a stage still working when the lock lapses could be
    claimed by a second worker and read twice.
    """
    return time.monotonic() + lines_stage.lines_deadline_seconds()


def _remaining(deadline_at: float) -> float:
    return deadline_at - time.monotonic()


@dataclass(frozen=True)
class LinesReading:
    """What reading the lines produced.

    ``lines`` is None when no part could be read. ``reason`` is the code of the
    last failure, ``unread_parts`` the 1-based numbers of the parts that could
    not be read, and ``part_count`` how many parts the receipt was split into.
    """

    lines: ReceiptLines | None
    attempts: int
    reason: str | None = None
    unread_parts: list[int] = field(default_factory=list)
    part_count: int = 1


async def read_header(
    llm: LlmClient, text: str, *, deadline_at: float
) -> tuple[ReceiptHeader | None, int]:
    """Ask the model for the header. Returns (header or None, attempts).

    A reply that never validates gives None, so the receipt still reaches
    review with what location matching can find on its own.
    """
    try:
        return await llm.extract(
            ReceiptHeader,
            header_stage.HEADER_TASK,
            text,
            deadline_seconds=max(_remaining(deadline_at), _MIN_REMAINING_SECONDS),
        )
    except InvalidModelOutput:
        return None, 1 + max(llm.max_retries, 0)


def apply_printed_total(header: ReceiptHeader, text: str) -> tuple[ReceiptHeader, list[str]]:
    """Prefer the total printed on the receipt to the model's. Returns (header, flags).

    The model's total is untrusted like the rest of its output. A line labelled
    TOTAL (or AMOUNT/BALANCE DUE) wins over a different model total, which may
    be an item price that is printed too; a bare BALANCE line only fills in a
    total the model missed or invented. A model total that is a savings
    summary's total is dropped, flagged ``savings_total_rejected``.
    """
    flags: list[str] = []
    if header.total is not None and header.total in header_stage.savings_block_totals(text):
        # What a savings summary says was saved, never what was paid (#121).
        header = header.model_copy(update={"total": None})
        flags.append("savings_total_rejected")
    printed = header_stage.printed_total_line(text)
    if printed is None:
        return header, flags
    amount, strong = printed
    missing = header.total is None or not header_stage.amount_in_text(header.total, text)
    if amount != header.total and (strong or missing):
        return header.model_copy(update={"total": amount}), [*flags, "total_from_text"]
    return header, flags


async def read_lines(llm: LlmClient, text: str, *, deadline_at: float) -> LinesReading:
    """Ask the model for the lines of each part of the receipt, and join them.

    Every part shares the one deadline. A parent_index points within its own
    part, so it is offset into the joined list; one that points outside its
    part is dropped and the item printed above is used instead, as for any
    unattached discount.
    """
    parts = lines_stage.split_receipt(text)
    joined: list[ReceiptLine] = []
    attempts, reason, unread = 0, None, []
    for index, part in enumerate(parts, start=1):
        remaining = _remaining(deadline_at)
        if remaining <= 0 and joined:
            # Out of time with parts already read: keep them, mark the rest.
            unread.extend(range(index, len(parts) + 1))
            reason = "model_timeout"
            break
        try:
            answer, used = await llm.extract(
                ReceiptLines,
                lines_stage.part_task(index, len(parts)),
                part,
                timeout_seconds=lines_stage.lines_budget_seconds(part),
                deadline_seconds=max(remaining, _MIN_REMAINING_SECONDS),
            )
        except InvalidModelOutput as exc:
            attempts += exc.attempts or 1 + max(llm.max_retries, 0)
            reason = exc.code
            unread.append(index)
            continue
        except ModelTimeout:
            if not joined:
                raise  # nothing read yet: the job's own retry policy applies
            # Parts already read are kept; the one that timed out and the rest are
            # unread. A server that is not there (ModelUnavailable) still raises,
            # so the job waits and reads the whole receipt when it is back.
            unread.extend(range(index, len(parts) + 1))
            reason = ModelTimeout.code
            break
        attempts += used
        if (
            not any(line.line_kind == "item" for line in answer.lines)
            and lines_stage.item_like_rows(part) >= lines_stage.EMPTY_PART_MIN_ROWS
        ):
            # A valid answer with no items for a part that plainly has them. At
            # temperature 0 the same request would say the same, so ask once
            # more a little differently; if that is empty too, the part is unread.
            try:
                answer, used = await llm.extract(
                    ReceiptLines,
                    lines_stage.part_task(index, len(parts)),
                    part,
                    timeout_seconds=lines_stage.lines_budget_seconds(part),
                    deadline_seconds=max(_remaining(deadline_at), _MIN_REMAINING_SECONDS),
                    temperature=EMPTY_PART_RETRY_TEMPERATURE,
                )
                attempts += used
            except InvalidModelOutput as exc:
                attempts += exc.attempts or 1 + max(llm.max_retries, 0)
                answer = None
            if answer is None or not any(line.line_kind == "item" for line in answer.lines):
                unread.append(index)
                reason = "empty_part"
                continue
        offset = len(joined)
        for line in answer.lines:
            parent = line.parent_index
            if parent is not None:
                parent = parent + offset if parent < len(answer.lines) else None
            joined.append(line.model_copy(update={"parent_index": parent}))
    if not joined:
        return LinesReading(None, attempts, reason, unread, len(parts))
    return LinesReading(
        ReceiptLines.model_construct(lines=joined), attempts, reason, unread, len(parts)
    )
