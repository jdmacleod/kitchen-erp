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


# --- A card slip's total misread, under a BALANCE the tender repeats ----------

_SLIP_MISREAD = "\n".join(
    [
        "FENWICK OATS              6.40",
        "CINDER PLUMS             20.45",
        "**** BALANCE             26.85",
        "Visa Credit",
        "TOTAI AMOUNT: $28.85",  # the slip's own total, one digit misread
        "Visa                     26.85",
        "CHANGE                    0.00",
    ]
)


def test_a_balance_the_tender_repeats_beats_a_total_printed_only_on_the_slip():
    fixed, flags = readers.apply_printed_total(ReceiptHeader(total=Decimal("28.85")), _SLIP_MISREAD)
    assert fixed.total == Decimal("26.85") and flags == ["total_from_balance"]


def test_a_balance_printed_once_does_not_replace_a_slip_total():
    text = _SLIP_MISREAD.replace("Visa                     26.85", "Visa")
    fixed, flags = readers.apply_printed_total(ReceiptHeader(total=Decimal("28.85")), text)
    assert fixed.total == Decimal("28.85") and flags == []


def test_a_model_total_printed_outside_the_slip_stands_over_a_bare_balance():
    text = "FENWICK OATS 6.10\nBALANCE 3.00\nVisa 3.00"
    fixed, flags = readers.apply_printed_total(ReceiptHeader(total=Decimal("6.10")), text)
    assert fixed.total == Decimal("6.10") and flags == []


# --- One date printed twice, its year misread differently --------------------

_UPLOADED = datetime(2025, 4, 2, 18, 0, tzinfo=UTC)


def test_years_misread_on_both_printed_dates_come_from_the_upload():
    text = "03/22/19 11:05am 4 7 120\nFENWICK OATS 2.00\n03/22/71 11:05am"
    at, flags = header_stage.datetime_from_text(None, None, text, "UTC", reference=_UPLOADED)
    assert at == datetime(2025, 3, 22, 11, 5, tzinfo=UTC)
    assert flags == ["year_from_upload", "time_from_text", "date_from_text"]


def test_a_month_and_day_after_the_upload_date_fall_in_the_year_before():
    text = "11/30/19 9:10 PM\n11/30/61 9:10 PM"
    uploaded = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
    at, flags = header_stage.datetime_from_text(None, None, text, "UTC", reference=uploaded)
    assert at == datetime(2024, 11, 30, 21, 10, tzinfo=UTC) and "year_from_upload" in flags


def test_the_one_plausible_year_is_taken():
    text = "03/22/25 11:05am\n03/22/75 11:05am"
    at, flags = header_stage.datetime_from_text(None, None, text, "UTC", reference=_UPLOADED)
    assert at == datetime(2025, 3, 22, 11, 5, tzinfo=UTC) and "year_from_upload" in flags


def test_a_garbled_am_pm_on_one_copy_still_matches_the_other():
    # OCR reads "pm" as "pn" or "prn" on one copy, or loses it altogether.
    for second in ("03/22/71 7:15pn", "03/22/71 7:15prn", "03/22/71 7:15"):
        text = f"03/22/19 7:15pm 2 9\nFENWICK OATS 2.00\n{second}"
        at, flags = header_stage.datetime_from_text(None, None, text, "UTC", reference=_UPLOADED)
        assert at == datetime(2025, 3, 22, 19, 15, tzinfo=UTC), second
        assert "year_from_upload" in flags


def test_a_garbled_meridiem_alone_reads_as_the_meridiem():
    text = "03/22/25 7:15pn"
    at, flags = header_stage.datetime_from_text(None, None, text, "UTC")
    assert at == datetime(2025, 3, 22, 19, 15, tzinfo=UTC) and "time_from_text" in flags


def test_dates_that_differ_in_more_than_the_year_stay_with_the_model():
    for text in ("03/22/19 11:05am\n03/23/71 11:05am", "03/22/19 11:05am\n03/22/71 4:30pm"):
        assert header_stage.datetime_from_text(None, None, text, "UTC", reference=_UPLOADED) == (
            None,
            [],
        )
    # Without a reference nothing changes.
    text = "03/22/19 11:05am\n03/22/71 11:05am"
    assert header_stage.datetime_from_text(None, None, text, "UTC") == (None, [])
