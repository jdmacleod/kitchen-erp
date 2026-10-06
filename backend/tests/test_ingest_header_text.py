"""What the receipt text says about the header, over the model (#121, #181).

Every receipt here is invented: names, amounts and dates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.ingest import header as header_stage
from app.ingest import readers
from app.ingest.schemas import ReceiptHeader

# --- #121. The total of a savings summary -----------------------------------

_SAVINGS_FOOTER = "\n".join(
    [
        "FENWICK OATS              6.40",
        "CINDER PLUMS             16.70",
        "SUBTOTAL                 23.10",
        "TOTAL                    23.10",
        "VISA                     23.10",
        "YOUR SAVINGS TODAY",
        "Card Savings              3.15",
        "Coupons                   1.00",
        "Total                     4.15",
    ]
)


def test_a_savings_summary_total_is_never_the_receipt_total():
    assert header_stage.printed_total_line(_SAVINGS_FOOTER) == (Decimal("23.10"), True)
    header = ReceiptHeader(total=Decimal("4.15"))
    fixed, flags = readers.apply_printed_total(header, _SAVINGS_FOOTER)
    assert fixed.total == Decimal("23.10")
    assert flags == ["savings_total_rejected", "total_from_text"]


def test_a_savings_summary_total_alone_leaves_the_total_unknown():
    text = "\n".join(_SAVINGS_FOOTER.splitlines()[:2] + _SAVINGS_FOOTER.splitlines()[5:])
    assert header_stage.printed_total_line(text) is None
    fixed, flags = readers.apply_printed_total(ReceiptHeader(total=Decimal("4.15")), text)
    assert fixed.total is None and flags == ["savings_total_rejected"]


def test_a_saving_on_an_item_does_not_hide_the_balance_below_it():
    text = "\n".join(
        [
            "FENWICK OATS              5.50",
            "Card Savings              0.30-",
            "**** BALANCE DUE          5.20",
        ]
    )
    assert header_stage.printed_total_line(text) == (Decimal("5.20"), True)


# --- 8. The printed date and time -------------------------------------------


def test_a_time_printed_beside_the_date_fills_the_one_the_model_left_out():
    text = "WREN MARKET\n11/14/2023   7:42 PM   REG 3\nFENWICK OATS 2.00"
    model_at = header_stage.parse_local_datetime("2023-11-14", "UTC")
    at, flags = header_stage.datetime_from_text("2023-11-14", model_at, text, "UTC")
    assert at == datetime(2023, 11, 14, 19, 42, tzinfo=UTC) and flags == ["time_from_text"]


def test_a_date_the_model_misread_gives_way_to_the_printed_one():
    text = "11-14-2023 14:05\nFENWICK OATS 2.00"
    model_at = header_stage.parse_local_datetime("2025-11-14 14:05", "UTC")
    at, flags = header_stage.datetime_from_text("2025-11-14 14:05", model_at, text, "UTC")
    assert at == datetime(2023, 11, 14, 14, 5, tzinfo=UTC) and flags == ["date_from_text"]


def test_the_household_zone_applies_to_a_printed_time():
    text = "03/02/24 08:15"
    at, flags = header_stage.datetime_from_text(None, None, text, "America/New_York")
    assert at == datetime(2024, 3, 2, 13, 15, tzinfo=UTC)
    assert flags == ["time_from_text", "date_from_text"]


def test_two_printed_dates_are_left_to_the_model():
    text = "11/14/2023 7:42 PM\nRETURN BY 12/14/2023"
    model_at = header_stage.parse_local_datetime("2023-11-14", "UTC")
    assert header_stage.datetime_from_text("2023-11-14", model_at, text, "UTC") == (model_at, [])


def test_a_model_reading_that_agrees_with_the_print_is_kept():
    text = "11/14/2023 7:42 PM"
    model_at = header_stage.parse_local_datetime("2023-11-14 19:42", "UTC")
    assert header_stage.datetime_from_text("2023-11-14 19:42", model_at, text, "UTC") == (
        model_at,
        [],
    )
