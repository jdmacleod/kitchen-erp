"""The printed total, read from the text when the model finds none.

Found by /devex-review: a till that labels its total BALANCE left the model
reporting no total, so the receipt was flagged "total unread" and its lines
were never checked against what was paid. All text here is invented.
"""

from decimal import Decimal

import pytest

from app.ingest.header import amount_in_text, printed_total_from_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BREAD 2.49 F\nTAX 0.00\nae BALANCE 12.34", Decimal("12.34")),
        ("** BALANCE DUE ** 7.10", Decimal("7.10")),
        ("TOTAL: 45.00", Decimal("45.00")),
        ("Amount due 3,99", Decimal("3.99")),
        # A subtotal and a savings line are not the total; the last total wins.
        ("SUBTOTAL 10.00\nTOTAL 10.80\nYou saved 1.20\nTOTAL SAVINGS 1.20", Decimal("10.80")),
        ("TOTAL 5.00\nCASH 10.00\nTOTAL 5.25", Decimal("5.25")),
    ],
)
def test_a_labelled_total_line_is_read(text, expected):
    assert printed_total_from_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "BREAD 2.49\nMILK 3.10",
        "SUBTOTAL 10.00",
        "TOTAL ITEMS 12",
        "TOTAL SAVINGS 1.20",
        "TOTAL see reverse",
    ],
)
def test_nothing_is_guessed_without_a_labelled_amount(text):
    assert printed_total_from_text(text) is None


@pytest.mark.parametrize(
    ("amount", "text", "found"),
    [
        (Decimal("60.80"), "TOTAL $60.80", True),
        (Decimal("60.8"), "Total: USD$ 60,80", True),
        (Decimal("4.54"), "TOTAL $60.80\nVISA $60.80", False),
        # Not a fragment of a longer number.
        (Decimal("0.80"), "TOTAL $60.80", False),
        (Decimal("60.80"), "TOTAL $160.80", False),
    ],
)
def test_amount_in_text(amount, text, found):
    assert amount_in_text(amount, text) is found
