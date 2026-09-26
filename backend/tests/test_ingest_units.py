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
    # The model gave no quantity: one each is the default, and it says so (#31).
    plain = lines_mod.refine_line(3, _line("BREAD 3.49", line_total="3.49"))
    assert (plain.qty, plain.unit, plain.unit_price, plain.flags) == (
        Decimal("1"),
        "each",
        None,
        ["qty_assumed"],
    )
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
    assert parse_local_datetime("03/11/2026", zone) is None
    assert parse_local_datetime(None, zone) is None
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
