"""The readers the stages and the reading benchmark share (spec 04, 2J).

Each read takes an absolute deadline from its caller, so one clock can bound a
whole stage. These drive the readers with a stub client and a fake clock; the
stage outputs themselves are pinned by ``test_ingest_golden``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.ingest import lines as lines_stage
from app.ingest import readers
from app.ingest.errors import InvalidModelOutput
from app.ingest.schemas import ReceiptHeader, ReceiptLine, ReceiptLines
from tests.ingest_helpers import load_fixture


class StubClient:
    """Answers every read after a fixed delay on the fake clock."""

    def __init__(self, clock: list[float], answer: Any, seconds: float = 0) -> None:
        self.clock = clock
        self.answer = answer
        self.seconds = seconds
        self.max_retries = 2
        self.calls: list[dict[str, Any]] = []

    async def extract(self, model_cls, task, receipt_text, **kwargs):
        self.calls.append(kwargs)
        self.clock[0] += self.seconds
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer, 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(readers.time, "monotonic", lambda: now[0])
    return now


def _one_item() -> ReceiptLines:
    return ReceiptLines(
        lines=[ReceiptLine(raw_text="WIDGET ROLLS 2.50", line_kind="item", line_total="2.50")]
    )


async def test_every_part_shares_the_callers_deadline(clock: list[float]):
    text = load_fixture("long_till_receipt").ocr_text
    assert len(lines_stage.split_receipt(text)) == 3
    client = StubClient(clock, _one_item(), seconds=200)

    reading = await readers.read_lines(client, text, deadline_at=clock[0] + 250)

    # The first part gets the whole 250 s, the second what is left; the third
    # finds the deadline gone and is marked unread rather than asked.
    assert [call["deadline_seconds"] for call in client.calls] == [250, 50]
    assert reading.lines is not None and len(reading.lines.lines) == 2
    assert (reading.unread_parts, reading.reason, reading.part_count) == ([3], "model_timeout", 3)


async def test_the_deadline_is_absolute_not_counted_from_the_read(clock: list[float]):
    deadline_at = clock[0] + 300
    clock[0] += 100  # time the caller spent before reading
    client = StubClient(clock, ReceiptHeader(total="9.99"))

    await readers.read_header(client, "TOTAL 9.99", deadline_at=deadline_at)

    assert client.calls[0]["deadline_seconds"] == 200


async def test_a_header_that_never_validates_reads_as_none(clock: list[float]):
    client = StubClient(clock, InvalidModelOutput())

    header, attempts = await readers.read_header(client, "TOTAL 9.99", deadline_at=clock[0] + 60)

    assert header is None
    assert attempts == 1 + client.max_retries


def test_stage_deadline_is_under_the_job_lock(clock: list[float]):
    assert readers.stage_deadline_at() == clock[0] + lines_stage.lines_deadline_seconds()


@pytest.mark.parametrize(
    ("model_total", "text", "expected_total", "flags"),
    [
        # A labelled TOTAL wins over a different model total.
        ("4.25", "WIDGET 4.25\nTOTAL 7.40", "7.40", ["total_from_text"]),
        # The model agreeing with the printed total changes nothing.
        ("7.40", "WIDGET 4.25\nTOTAL 7.40", "7.40", []),
        # A bare BALANCE fills in a total the model missed...
        (None, "WIDGET 7.40\nBALANCE 7.40", "7.40", ["total_from_text"]),
        # ...but never replaces one printed on the receipt.
        ("6.10", "WIDGET 6.10\nBALANCE 3.00", "6.10", []),
        # With no total line, the model's total stands.
        ("6.10", "WIDGET 6.10", "6.10", []),
    ],
)
def test_printed_total_rule(
    model_total: str | None, text: str, expected_total: str, flags: list[str]
):
    header, got_flags = readers.apply_printed_total(ReceiptHeader(total=model_total), text)
    assert header.total == Decimal(expected_total)
    assert got_flags == flags
