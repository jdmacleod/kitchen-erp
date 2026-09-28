"""Pure pieces of the ingest package: prompts, decoding, line refinement, times."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.ingest import lines as lines_mod
from app.ingest.errors import (
    InvalidModelOutput,
    ModelTimeout,
    ModelUnavailable,
    RetryableError,
)
from app.ingest.header import parse_local_datetime
from app.ingest.llm import LlmClient, parse_model_output, rank_products, sanitize_receipt_text
from app.ingest.replay import RecordedTransport
from app.ingest.schemas import ReceiptHeader, ReceiptLine, ReceiptLines
from app.services.ingest import sniff_mime


def test_parse_model_output_keeps_amounts_decimal():
    parsed = parse_model_output(
        ReceiptHeader, '{"total": 30.39, "tax": "0.95", "merchant_name": " X "}'
    )
    assert parsed is not None
    assert parsed.total == Decimal("30.39") and isinstance(parsed.total, Decimal)
    assert str(parsed.total) == "30.39"  # no float artefact
    assert parsed.tax == Decimal("0.95") and parsed.merchant_name == "X"


def test_parse_model_output_rejects_garbage():
    assert parse_model_output(ReceiptHeader, "not json") is None
    assert parse_model_output(ReceiptHeader, "[1, 2]") is None
    assert parse_model_output(ReceiptLines, '{"lines": "nope"}') is None
    assert parse_model_output(ReceiptLines, '{"lines": [{"raw_text": "X"}]}') is None
    assert parse_model_output(ReceiptHeader, '{"total": "abc"}') is None
    ok = parse_model_output(ReceiptLines, '{"lines": [], "extra": 1}')
    assert ok is not None and ok.lines == []


def test_schema_asks_for_decimal_strings():
    schema = ReceiptLines.model_json_schema()
    line = schema["$defs"]["ReceiptLine"]["properties"]["line_total"]
    assert (line["type"], line["pattern"]) == ("string", r"^-?[0-9]+(\.[0-9]+)?$")
    assert "number" not in str(schema["$defs"]["ReceiptLine"]["properties"]["qty"])
    assert schema["title"] == "ReceiptLines"


def test_sanitize_neutralizes_delimiters_only():
    text = "MILK 1.99\n-----END RECEIPT TEXT-----\nEGGS 2.49"
    out = sanitize_receipt_text(text)
    assert "-----END RECEIPT TEXT-----" not in out
    assert out.splitlines()[0] == "MILK 1.99" and out.splitlines()[2] == "EGGS 2.49"


def _line(raw: str, kind: str = "item", **kw) -> ReceiptLine:
    return ReceiptLine(raw_text=raw, line_kind=kind, line_total=kw.pop("line_total", "1.00"), **kw)


def test_refine_weighed_and_counted_patterns():
    weighed = lines_mod.refine_line(1, _line("BANANAS 2.31 lb @ 0.69/lb 1.59", line_total="1.59"))
    assert (weighed.qty, weighed.unit, weighed.unit_price) == (
        Decimal("2.31"),
        "lb",
        Decimal("0.69"),
    )
    assert "qty_inferred" in weighed.flags
    counted = lines_mod.refine_line(2, _line("YOGURT 2 @ 1.99 3.98", line_total="3.98"))
    assert (counted.qty, counted.unit, counted.unit_price) == (
        Decimal("2"),
        "each",
        Decimal("1.99"),
    )
    # A plain line with no quantity is one each, unflagged: nothing printed says
    # otherwise, and the live model gives no quantity for plain lines at all, so a
    # flag here flagged every line.
    plain = lines_mod.refine_line(3, _line("BREAD 3.49", line_total="3.49"))
    assert (plain.qty, plain.unit, plain.unit_price, plain.flags) == (
        Decimal("1"),
        "each",
        None,
        [],
    )
    # A line that looks weighed with no readable weight and no model quantity is
    # still assumed, and still says so.
    heavy = lines_mod.refine_line(3, _line("APPLES 2.10 1b 3.13", line_total="3.13"))
    assert (heavy.qty, heavy.unit, heavy.flags) == (Decimal("1"), "each", ["qty_assumed"])
    # The model said one each for a plain line: nothing to flag.
    one = lines_mod.refine_line(3, _line("BREAD 3.49", line_total="3.49", qty="1", unit="each"))
    assert (one.qty, one.unit, one.flags) == (Decimal("1"), "each", [])
    kilo = lines_mod.refine_line(
        4, _line("ONIONS 0.85 kg @ 2.20/kg 1.87", line_total="1.87", qty="0.85", unit="KG")
    )
    assert (kilo.qty, kilo.unit, kilo.unit_price) == (Decimal("0.85"), "kg", Decimal("2.20"))
    odd = lines_mod.refine_line(5, _line("WIDGET 4.00", line_total="4.00", qty="1", unit="bunch"))
    assert odd.unit == "each" and "unknown_unit" in odd.flags
    discount = lines_mod.refine_line(6, _line("SAVINGS 1.00-", kind="discount", line_total="-1.00"))
    assert discount.line_total == Decimal("1.00") and discount.qty is None and discount.unit is None


def test_attach_parents_uses_model_index_then_adjacency():
    model = ReceiptLines(
        lines=[
            _line("MILK 5.99", line_total="5.99"),
            _line("CARD SAVINGS 1.00-", "discount", line_total="1.00", parent_index=0),
            _line("WATER 6.99", line_total="6.99"),
            _line("CRV 1.20", "deposit", line_total="1.20"),
            _line("BOTTLE CREDIT 0.10-", "discount", line_total="0.10"),
            _line("TAX 0.50", "tax", line_total="0.50"),
            _line("COUPON 2.00-", "discount", line_total="2.00", parent_index=5),  # not an item
            _line("SODA 1.00", line_total="1.00"),
            _line("CRV 0.05", "deposit", line_total="0.05", parent_index=7),
        ]
    )
    parsed = lines_mod.parse_model_lines(model)
    assert [p.parent_seq for p in parsed] == [None, 1, None, 3, 3, None, None, None, 8]
    assert parsed[3].flags == ["parent_inferred"] and parsed[4].flags == ["parent_inferred"]
    assert parsed[6].flags == ["parent_rejected"]
    assert parsed[8].flags == []


def test_reconcile_formula_and_tolerance():
    model = ReceiptLines(
        lines=[
            _line("A 10.00", line_total="10.00"),
            _line("A SAVE 2.00-", "discount", line_total="2.00", parent_index=0),
            _line("B 5.00", line_total="5.00"),
            _line("CRV 0.10", "deposit", line_total="0.10", parent_index=2),
            _line("BAG 0.10", "fee", line_total="0.10"),
            _line("TAX 0.45", "tax", line_total="0.45"),
        ]
    )
    parsed = lines_mod.parse_model_lines(model)
    exact = lines_mod.reconcile(parsed, Decimal("13.65"), None)
    assert exact["mismatch"] is False and exact["difference"] == "0.00"
    assert exact["computed_total"] == "13.65" and exact["tax_source"] == "lines"
    within = lines_mod.reconcile(parsed, Decimal("13.67"), None)
    assert within["mismatch"] is False and within["difference"] == "-0.02"
    beyond = lines_mod.reconcile(parsed, Decimal("13.62"), None)
    assert beyond["mismatch"] is True and beyond["difference"] == "0.03"
    unchecked = lines_mod.reconcile(parsed, None, None)
    assert unchecked["checked"] is False and unchecked["mismatch"] is False
    no_tax_line = lines_mod.parse_model_lines(ReceiptLines(lines=model.lines[:-1]))
    from_header = lines_mod.reconcile(no_tax_line, Decimal("13.65"), Decimal("0.45"))
    assert from_header["tax_source"] == "header" and from_header["mismatch"] is False


def test_local_time_interpreted_in_household_zone():
    zone = "America/Los_Angeles"
    assert parse_local_datetime("2026-03-04 17:42", zone) == datetime(2026, 3, 5, 1, 42, tzinfo=UTC)
    assert parse_local_datetime("2026-03-11 09:15", zone) == datetime(
        2026, 3, 11, 16, 15, tzinfo=UTC
    )
    assert parse_local_datetime("2026-03-11", zone) == datetime(2026, 3, 11, 7, 0, tzinfo=UTC)
    assert parse_local_datetime(None, zone) is None
    assert parse_local_datetime("not a date", zone) is None
    assert parse_local_datetime("2026-03-11 09:15", "Europe/Berlin") == datetime(
        2026, 3, 11, 8, 15, tzinfo=UTC
    )


def test_sniff_mime():
    assert sniff_mime(b"\xff\xd8\xff\xdb" + b"\0" * 8) == "image/jpeg"
    assert sniff_mime(b"\x89PNG\r\n\x1a\n" + b"\0" * 8) == "image/png"
    assert sniff_mime(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert sniff_mime(b"%PDF-1.7\n") == "application/pdf"
    assert sniff_mime(b"\0\0\0\x18ftypheic\0\0\0\0") == "image/heic"
    assert sniff_mime(b"GIF89a") is None
    assert sniff_mime(b"") is None


# --- product ranker ------------------------------------------------------------

SHORTLIST = [
    {"id": "0192b1c0-6f0e-7a10-8000-6a1f3c2d4e5f", "name": "Whole Milk 1 gal", "brand": None},
    {"id": "0192b1c0-6f0e-7a10-8000-7b2e4d3c5f60", "name": "Greek Yogurt", "brand": "Invented"},
]


def _client(responses: dict, **kw) -> tuple[LlmClient, RecordedTransport]:
    transport = RecordedTransport(responses)
    return LlmClient(transport=transport, max_retries=1, **kw), transport


async def test_rank_products_answers_from_the_shortlist():
    client, transport = _client({"rank": {"product_id": SHORTLIST[0]["id"], "confidence": "0.8"}})
    answer = await rank_products("org whole milk 1gal", SHORTLIST, client=client)
    assert answer == {"product_id": SHORTLIST[0]["id"], "confidence": Decimal("0.8")}
    body = transport.requests[0]["body"]
    assert body["stream"] is False and body["format"]["title"] == "ProductRank"
    assert body["format"]["properties"]["product_id"]["anyOf"][0]["enum"] == [
        c["id"] for c in SHORTLIST
    ]
    assert "org whole milk 1gal" in body["messages"][1]["content"]


async def test_rank_products_rejects_ids_outside_the_shortlist():
    client, transport = _client({"rank": {"product_id": "not-a-candidate", "confidence": "1"}})
    with pytest.raises(InvalidModelOutput):
        await rank_products("org whole milk 1gal", SHORTLIST, client=client)
    assert len(transport.requests) == 2  # retried once, then rejected


async def test_rank_products_null_answer_and_empty_shortlist():
    client, transport = _client({"rank": {"product_id": None, "confidence": None}})
    assert await rank_products("mystery", SHORTLIST, client=client) == {
        "product_id": None,
        "confidence": None,
    }
    assert (await rank_products("mystery", [], client=client))["product_id"] is None
    assert len(transport.requests) == 1  # nothing is asked for an empty shortlist


async def test_rank_products_model_unavailable_propagates():
    client = LlmClient(base_url="http://127.0.0.1:9", timeout_seconds=1.0, max_retries=0)
    with pytest.raises(ModelUnavailable) as raised:
        await rank_products("org whole milk 1gal", SHORTLIST, client=client)
    assert raised.value.detail == "ConnectError"  # which HTTPError it was, not just that it was one


class _TimingOutTransport(httpx.AsyncBaseTransport):
    """A server that is reachable and answers nothing."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)


class _ConnectTimeoutTransport(httpx.AsyncBaseTransport):
    """A host that drops connection attempts instead of refusing them."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out connecting", request=request)


async def test_a_connect_timeout_is_an_unreachable_server_not_a_slow_one():
    """httpx.ConnectTimeout is a TimeoutException, and it means the opposite thing.

    Catching TimeoutException wholesale classified an unreachable host as
    model_timeout: the job would fail after a few attempts instead of waiting for
    the server to come back, and the operator would be told to raise
    LLM_TIMEOUT_SECONDS for a server that was not running. A dropped SYN is
    exactly what a stopped container does, so this is the common case, not an
    exotic one.
    """
    client = LlmClient(
        base_url="http://model.invalid",
        timeout_seconds=120.0,
        max_retries=0,
        transport=_ConnectTimeoutTransport(),
    )
    with pytest.raises(ModelUnavailable) as raised:
        await rank_products("org whole milk 1gal", SHORTLIST, client=client)
    assert raised.value.code == "model_unavailable"
    assert raised.value.detail == "ConnectTimeout"
    # Retryable, so the job waits rather than failing.
    assert isinstance(raised.value, RetryableError)
    assert not isinstance(raised.value, ModelTimeout)


async def test_a_timeout_is_its_own_code_and_is_not_retried_for_ever():
    """A request the deployment cannot fix by waiting must not look like one it can."""
    client = LlmClient(
        base_url="http://model.invalid",
        timeout_seconds=120.0,
        max_retries=0,
        transport=_TimingOutTransport(),
    )
    with pytest.raises(ModelTimeout) as raised:
        await rank_products("org whole milk 1gal", SHORTLIST, client=client)
    assert raised.value.code == "model_timeout"
    # The detail names the condition and the limit it hit, so the operator is sent
    # to LLM_TIMEOUT_SECONDS rather than to "is Ollama running?".
    assert raised.value.detail == "ReadTimeout after 120s"
    assert not isinstance(raised.value, ModelUnavailable)


def test_the_printed_quantity_outranks_the_model_and_assumptions_are_flagged():
    """#31: every line came back as 1 each, with nothing saying it was not read."""
    # The model said 1 each; the receipt prints a weight. The print wins.
    bananas = lines_mod.refine_line(
        1, _line("BANANAS 1.24 lb @ $0.68/lb 0.84", line_total="0.84", qty="1", unit="each")
    )
    assert (bananas.qty, bananas.unit, bananas.unit_price) == (
        Decimal("1.24"),
        "lb",
        Decimal("0.68"),
    )
    assert bananas.flags == ["qty_corrected"]
    # And a printed count.
    cans = lines_mod.refine_line(2, _line("SODA 3 @ 1.25 3.75", line_total="3.75", qty="1"))
    assert (cans.qty, cans.unit, cans.flags) == (Decimal("3"), "each", ["qty_corrected"])
    # Agreeing with the print is not a correction.
    agreed = lines_mod.refine_line(
        3, _line("SODA 3 @ 1.25 3.75", line_total="3.75", qty="3", unit="each")
    )
    assert agreed.flags == []
    # A weight OCR mangled ("1b"): weighed, but unreadable. Whatever the model
    # said, nothing on the line supports it.
    garbled = lines_mod.refine_line(
        4, _line("APPLES 2.10 1b 3.13", line_total="3.13", qty="1", unit="each")
    )
    assert (garbled.qty, garbled.unit, garbled.flags) == (Decimal("1"), "each", ["qty_assumed"])
    # Discounts and tax carry no quantity and no quantity flag.
    tax = lines_mod.refine_line(5, _line("TAX 0.42", kind="tax", line_total="0.42"))
    assert tax.qty is None and tax.flags == []


def test_the_printed_rate_goes_with_the_printed_quantity_and_grams_need_context():
    # The model's own unit price would contradict the print it was corrected by.
    cans = lines_mod.refine_line(
        1, _line("SODA 3 @ 1.25 3.75", line_total="3.75", qty="1", unit_price="5.00")
    )
    assert (cans.qty, cans.unit_price) == (Decimal("3"), Decimal("1.25"))
    # A gram weight with more after it is a weight...
    cheese = lines_mod.refine_line(2, _line("CHEESE 250.5 g 3.10", line_total="3.10", qty="1"))
    assert cheese.flags == ["qty_assumed"]
    # ...but a trailing letter is as likely a tax code.
    bread = lines_mod.refine_line(3, _line("BREAD 2.49 G", line_total="2.49", qty="1", unit="each"))
    assert bread.flags == []


# The shapes gpt-oss:20b returned for real receipts in place of ISO (#58), digits
# invented: all four were discarded before RECEIPT_DATE_ORDER.
PRINTED = [
    ("07/04/26 17:42:09", datetime(2026, 7, 5, 0, 42, 9, tzinfo=UTC)),
    ("07/04/26 17:42", datetime(2026, 7, 5, 0, 42, tzinfo=UTC)),
    ("07/04/26", datetime(2026, 7, 4, 7, 0, tzinfo=UTC)),
    ("07/04/2026 05:42 PM", datetime(2026, 7, 5, 0, 42, tzinfo=UTC)),
    ("07-04-2026 05:42PM", datetime(2026, 7, 5, 0, 42, tzinfo=UTC)),
    (" 07/04/26   17:42 ", datetime(2026, 7, 5, 0, 42, tzinfo=UTC)),
]


@pytest.mark.parametrize(("printed", "expected"), PRINTED)
def test_a_printed_us_date_is_read_month_first_by_default(printed, expected):
    assert parse_local_datetime(printed, "America/Los_Angeles") == expected


def test_the_declared_order_is_the_only_one_tried():
    zone = "America/Los_Angeles"
    # The same print, three households.
    assert parse_local_datetime("07/04/26", zone, "MDY") == datetime(2026, 7, 4, 7, tzinfo=UTC)
    assert parse_local_datetime("07/04/26", zone, "DMY") == datetime(2026, 4, 7, 7, tzinfo=UTC)
    assert parse_local_datetime("26/07/04", zone, "YMD") == datetime(2026, 7, 4, 7, tzinfo=UTC)
    # A day-first date that cannot be month-first is refused, not reinterpreted.
    assert parse_local_datetime("30/07/26", zone, "MDY") is None
    assert parse_local_datetime("07/30/26", zone, "DMY") is None
    # ISO is ISO whatever the order.
    assert parse_local_datetime("2026-07-04 17:42", zone, "DMY") == datetime(
        2026, 7, 5, 0, 42, tzinfo=UTC
    )


def _parsed(raw: str, total: str, kind: str = "item") -> lines_mod.ParsedLine:
    return lines_mod.ParsedLine(
        seq=1,
        raw_text=raw,
        line_kind=kind,
        qty=None,
        unit=None,
        unit_price=None,
        line_total=Decimal(total),
    )


@pytest.mark.parametrize(
    ("raw", "total", "flagged"),
    [
        ("OAT MILK 1L 349", "349", True),  # the decimal point OCR lost (#59)
        ("OAT MILK 1L 349 F", "349", True),  # a tax code after the amount
        ("OAT MILK 1L 349*", "349", True),
        ("OAT MILK 1L 3.49", "3.49", False),
        ("OAT MILK 1L 3,49", "3.49", False),  # a comma is a decimal separator too
        ("CRV 30 F", "30", True),  # a 0.30 container deposit read as $30.00
        ("OAT MILK 1L 7", "7", False),  # one digit: its hundredth is rarely a price
        ("OAT MILK 1L 349", "3.49", False),  # the model already read it right
        ("012345678905 OAT MILK 3.49", "3.49", False),  # a code earlier on the line
    ],
)
def test_a_price_printed_without_its_decimal_point_is_flagged(raw, total, flagged):
    line = _parsed(raw, total)
    lines_mod.check_prices([line], None)
    assert ("decimal_missing" in line.flags) is flagged


def test_discounts_are_not_checked_and_a_line_over_the_total_is():
    discount = _parsed("MEMBER SAVINGS 125", "125", kind="discount")
    big = _parsed("CAST IRON PAN 45.00", "45.00")
    lines_mod.check_prices([discount, big], Decimal("12.00"))
    assert discount.flags == []
    assert big.flags == ["exceeds_total"]


def test_restoring_decimals_reconciles_only_when_it_actually_does():
    lines = [_parsed("OAT MILK 349", "349"), _parsed("RYE BREAD 4.25", "4.25")]
    lines_mod.check_prices(lines, Decimal("7.74"))
    assert lines_mod.restoring_decimals_reconciles(lines, Decimal("7.74"), None) is True
    # The same misreading, but the total says something else is wrong too.
    assert lines_mod.restoring_decimals_reconciles(lines, Decimal("9.99"), None) is False
    # No printed total: nothing to reconcile against.
    assert lines_mod.restoring_decimals_reconciles(lines, None, None) is False
    assert lines_mod.restored(Decimal("349")) == Decimal("3.49")


def _answering(content: str, done_reason: str) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200, json={"message": {"content": content}, "done": True, "done_reason": done_reason}
        )

    return httpx.MockTransport(handle), calls


async def test_a_reply_that_ran_out_of_room_is_not_retried():
    # #60: the context filled before the answer did, and the same request at
    # temperature 0 filled it the same way twice more, about 140 s each time.
    transport, calls = _answering("", "length")
    client = LlmClient(transport=transport, max_retries=2)
    with pytest.raises(InvalidModelOutput) as caught:
        await client.extract(ReceiptLines, "task", "OAT MILK 3.49")
    assert caught.value.code == "model_out_of_room"
    assert caught.value.attempts == 1
    assert len(calls) == 1


async def test_a_finished_but_invalid_reply_is_still_retried():
    transport, calls = _answering('{"lines": "not a list"}', "stop")
    client = LlmClient(transport=transport, max_retries=2)
    with pytest.raises(InvalidModelOutput) as caught:
        await client.extract(ReceiptLines, "task", "OAT MILK 3.49")
    assert caught.value.code == "invalid_model_output"
    assert len(calls) == 3


def test_a_short_receipt_is_one_part_and_a_long_one_is_split():
    short = "\n".join(f"ITEM {i} 1.00" for i in range(25))
    assert lines_mod.split_receipt(short) == [short]
    long = "\n".join(f"ITEM {i} 1.00" for i in range(60))
    parts = lines_mod.split_receipt(long)
    assert [len(p.splitlines()) for p in parts] == [25, 25, 10]
    assert "\n".join(parts) == long  # nothing lost, nothing repeated


def test_a_part_never_starts_with_a_row_that_belongs_to_the_one_above():
    rows = [f"ITEM {i} 1.00" for i in range(24)]
    rows += ["BANANAS", "2.31 lb @ 0.69/lb 1.59", "MEMBER SAVINGS -0.20", "OAT MILK 3.49"]
    parts = lines_mod.split_receipt("\n".join(rows + [f"MORE {i} 2.00" for i in range(10)]))
    # BANANAS is row 25; its weight line and saving follow it into part 1.
    assert parts[0].splitlines()[-3:] == [
        "BANANAS",
        "2.31 lb @ 0.69/lb 1.59",
        "MEMBER SAVINGS -0.20",
    ]
    assert parts[1].splitlines()[0] == "OAT MILK 3.49"
    assert "part 2 of 3" in lines_mod.part_task(2, 3)
    assert lines_mod.part_task(1, 1) == lines_mod.LINES_TASK


def test_item_like_rows_counts_items_not_totals_or_tender():
    part = "\n".join(
        [
            "HARBOURSIDE PROVISIONS",
            "OAT MILK 1L 3.49 F",
            "RYE BREAD 4.25",
            "EGGS DOZEN 1.65-",
            "SUBTOTAL 9.39",
            "TAX 0.51",
            "VISA 9.90",
            "Card Savings 0.30-",
        ]
    )
    assert lines_mod.item_like_rows(part) == 3
    assert "return an empty list" in lines_mod.part_task(3, 3)


def test_item_names_containing_tender_words_still_count_as_items():
    # Review of #66: "card" excluded CARDAMOM and "cash" CASHMERE.
    part = "GROUND CARDAMOM 5.49\nCASHEWS ROASTED 7.99\nCASHMERE SOCKS 12.00\nVISA 25.48"
    assert lines_mod.item_like_rows(part) == 3


def _row(seq: int, raw: str, total: str, kind: str = "item", parent: int | None = None):
    line = _parsed(raw, total, kind=kind)
    line.seq, line.parent_seq = seq, parent
    if kind == "item":
        line.qty, line.unit = Decimal("1"), "each"
    return line


def test_a_regular_price_row_and_its_saving_fold_into_the_item():
    # #64: the model read the shelf price as another item and took the saving
    # off an amount that was already net.
    lines = [
        _row(1, "SMOKED TROUT 7.25 S", "7.25"),
        _row(2, "Regular Price 9.00", "9.00"),
        _row(3, "Card Savings 1.75-", "1.75", kind="discount", parent=1),
        _row(4, "RYE BREAD 4.25", "4.25"),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == ["Regular Price 9.00"]
    assert [(k.seq, k.raw_text, k.line_kind, k.line_total, k.parent_seq) for k in kept] == [
        (1, "SMOKED TROUT 7.25 S", "item", Decimal("9.00"), None),
        (2, "Card Savings 1.75-", "discount", Decimal("1.75"), 1),
        (3, "RYE BREAD 4.25", "item", Decimal("4.25"), None),
    ]


def test_the_one_row_form_becomes_the_discount():
    lines = [
        _row(1, "GREEN CABBAGE 1.29 F", "1.29"),
        _row(2, "Regular Price 1.59 , You saved 0.30", "0.30", kind="discount", parent=1),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == []
    assert [(k.line_kind, k.line_total, k.parent_seq) for k in kept] == [
        ("item", Decimal("1.59"), None),
        ("discount", Decimal("0.30"), 1),
    ]


def test_ocr_spellings_are_regular_price_rows_too():
    for raw in (
        "Resular Price 17.00",
        "REG. PRICE 17.00",
        "Original Price 17.00",
        "Regular Pr1ce 17.00",
    ):
        kept, dropped = lines_mod.fold_regular_prices(
            [_row(1, "PORK ROAST 14.56", "14.56"), _row(2, raw, "17.00")]
        )
        assert dropped == [raw] and len(kept) == 1, raw


def test_numbers_that_disagree_drop_the_shelf_price_and_its_saving():
    # 9.00 - 1.00 is not 7.25: OCR misread one of them. The item line is what
    # was paid on this layout, so the saving must not come off it again.
    lines = [
        _row(1, "SMOKED TROUT 7.25 S", "7.25"),
        _row(2, "Regular Price 9.00", "9.00"),
        _row(3, "Card Savinss 1.00-", "1.00", kind="discount", parent=1),
        _row(4, "OAT MILK 2.49 F", "2.49"),
        _row(5, "Regular Price 0.99 , Yau saved 1 83", "1.83", kind="discount", parent=4),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == [
        "Regular Price 9.00",
        "Card Savinss 1.00-",
        "Regular Price 0.99 , Yau saved 1 83",
    ]
    assert [(k.seq, k.raw_text, k.line_total) for k in kept] == [
        (1, "SMOKED TROUT 7.25 S", Decimal("7.25")),
        (2, "OAT MILK 2.49 F", Decimal("2.49")),
    ]


def test_an_ocr_spelled_saving_still_folds():
    lines = [
        _row(1, "PORK ROAST 14.56 S", "14.56"),
        _row(2, "Resular Price 17.00", "17.00"),
        _row(3, "i Card Savinss 2.44-", "2.44", kind="discount"),
    ]
    kept, _ = lines_mod.fold_regular_prices(lines)
    assert [(k.line_kind, k.line_total, k.parent_seq) for k in kept] == [
        ("item", Decimal("17.00"), None),
        ("discount", Decimal("2.44"), 1),
    ]


def test_an_item_named_like_a_price_row_is_not_one():
    lines = [
        _row(1, "REGULAR PRICED COFFEE 5.99", "5.99"),
        _row(2, "RESTAURANT PRICE MENU 3.00", "3.00"),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == [] and len(kept) == 2


def test_an_item_named_like_a_saving_is_never_consumed_as_one():
    # Review of #68: with the real saving row missing, SAVORY CRACKERS followed
    # the regular price and was dropped as its saving.
    lines = [
        _row(1, "SMOKED TROUT 7.25 S", "7.25"),
        _row(2, "Regular Price 9.00", "9.00"),
        _row(3, "SAVORY CRACKERS 3.00", "3.00"),
    ]
    kept, dropped = lines_mod.fold_regular_prices(lines)
    assert dropped == ["Regular Price 9.00"]
    assert [k.raw_text for k in kept] == ["SMOKED TROUT 7.25 S", "SAVORY CRACKERS 3.00"]


def test_a_folded_item_is_checked_against_the_total_at_the_price_paid():
    # Review of #68: a lone item folded to its 9.00 regular price, with its 1.75
    # saving attached, was flagged as more than the 7.25 receipt.
    kept, _ = lines_mod.fold_regular_prices(
        [
            _row(1, "SMOKED TROUT 7.25 S", "7.25"),
            _row(2, "Regular Price 9.00", "9.00"),
            _row(3, "Card Savings 1.75-", "1.75", kind="discount", parent=1),
        ]
    )
    lines_mod.check_prices(kept, Decimal("7.25"))
    assert all("exceeds_total" not in k.flags for k in kept)
